"""Import tender documents into the existing Tenders structure. Safe to repeat.

    cd backend
    python import_tender_documents.py PACKAGE_DIR --confirm-db NAME [--dry-run]

PACKAGE_DIR holds a manifest.json and the files it names. The manifest is a
list of tenders, each with its own fields and documents:

    {"tenders": [{
        "source": "free text: where this bid came from (folder)",
        "fields":    {title, issuing_authority, reference_number, tender_type,
                      published_date, submission_deadline, submission_mode,
                      tender_fee, estimated_value, emd_amount, status, notes, tags},
        "documents": [{file, original_name, sha256, document_type, category,
                       reference_number, document_date, notes}]
    }]}

Only values the source documents support belong in `fields` and the document
entries; anything left out is stored empty, never guessed.

It writes the same shapes the application itself writes:

* a tender, as POST /tenders/ does (validated by schemas.TenderIn, so the
  app's own rules on dates, amounts and status apply);
* a document, as the tender's "add document" form does -- the client is the
  tender's issuing authority and the project title is the tender's title --
  with `tender_id` set and the document listed in the tender's `document_ids`.

Per tender it reports *created* or *existing* (matched on issuing authority
and reference number -- or, for a bid with no reference number, on issuing
authority and title; an existing tender is never changed). Per document it
reports *inserted*, *linked* (the file was already in the log, free of any
other owner, and is now attached), *skipped* (already attached to this
tender, or already owned by something else, which is left as it is) or
*failed* (missing file, hash mismatch, unsupported type, over the upload
limit). Nothing is deleted or overwritten. A report is written to
PACKAGE_DIR/reports/.

The application's own upload rules (PDF/PNG/JPG, 20 MB) are the default here
too. A bulk load of bid files that legitimately break them is opt-in, per run:

    --max-mb 250           raise the size limit for this run
    --allow-ext docx,xlsx  also accept these types (only docx and xlsx)

    --update               refresh records this tool created from the manifest

--update fills in and corrects what the manifest states: on a matched tender it
sets every non-empty manifest field that differs (and adds missing tags); on a
matched document it does the same for type, category, reference, date, notes,
client, title and value -- but only for documents this tool imported
(submitted_by "import"). A value the manifest leaves out is never cleared. Use
it before people have edited the records, because it overwrites their values
for the fields the manifest names. Every change is listed in the report.

A document entry may carry `extraction` ({methods, pages:[{page, method, text}]})
-- text read from a scan -- which is stored with a newly inserted record as
`extracted_text`, so an OCR result travels with the document it came from.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app import mongodb, schemas, storage

EXTRA_EXTENSIONS_ALLOWED = {"docx", "xlsx"}


def _now():
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean(value):
    """Empty strings and None both mean 'not available' and are stored as None."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _extraction_text(extraction):
    if not extraction:
        return None
    return "\n\n".join(
        "[page %d]\n%s" % (p["page"], p["text"]) for p in extraction.get("pages", []) if p.get("text")
    ) or None


def _tender_changes(existing, payload):
    """Fields a manifest sets that the stored tender lacks or has differently."""
    wanted = payload.model_dump()
    changes = {k: wanted[k] for k in schemas.TenderFields.model_fields
               if k != "tags" and wanted.get(k) not in (None, "") and existing.get(k) != wanted[k]}
    tags = list(existing.get("tags") or [])
    added = [t for t in wanted.get("tags") or [] if t not in tags]
    if added:
        changes["tags"] = tags + added
    return changes


def _document_changes(record, wanted):
    return {k: v for k, v in wanted.items() if v not in (None, "") and record.get(k) != v}


def import_manifest(package_dir, tenders, documents, dry_run=False, max_bytes=None, extra_ext=(), update=False):
    with open(os.path.join(package_dir, "manifest.json"), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    results = []

    def log(kind, status, label, detail=""):
        results.append({"kind": kind, "status": status, "item": label, "detail": detail})

    for entry in manifest["tenders"]:
        fields = {k: v for k, v in entry["fields"].items() if _clean(v) is not None or k == "tags"}
        try:
            payload = schemas.TenderIn.model_validate(fields)
        except ValidationError as error:
            log("tender", "failed", fields.get("title"), "; ".join(e["msg"] for e in error.errors()))
            continue
        label = "%s (%s)" % (payload.title, payload.reference_number or "no reference")

        existing = None
        if payload.reference_number and payload.issuing_authority:
            existing = tenders.find_one(
                {"reference_number": payload.reference_number, "issuing_authority": payload.issuing_authority}
            )
        elif payload.issuing_authority and payload.title:
            # No reference number to match on: the same authority and title, and
            # no reference recorded on the existing tender either.
            existing = tenders.find_one(
                {"issuing_authority": payload.issuing_authority, "title": payload.title,
                 "$or": [{"reference_number": None}, {"reference_number": ""}]}
            )
        if existing is not None:
            tender_id, tender = existing["_id"], existing
            changes = _tender_changes(existing, payload) if update else {}
            if changes:
                if not dry_run:
                    tenders.update_one({"_id": tender_id}, {"$set": {**changes, "updated_at": _now()}})
                tender = {**existing, **changes}
                log("tender", "updated", label, "fields: " + ", ".join(sorted(changes)))
            else:
                log("tender", "existing", label, "nothing to update" if update else "left unchanged")
        else:
            now = _now()
            tender = payload.model_dump()
            tender.update(cost_items=[], certificates=[], document_ids=[], cited_project_ids=[],
                          criteria=[], created_at=now, updated_at=now)
            if dry_run:
                tender_id = None
                log("tender", "created", label, "dry run: would create")
            else:
                tender_id = tenders.insert_one(tender).inserted_id
                tender["_id"] = tender_id
                log("tender", "created", label)

        for item in entry.get("documents", []):
            name = item.get("original_name")
            path = os.path.join(package_dir, *item["file"].split("/"))
            if not os.path.isfile(path):
                log("document", "failed", name, "packaged file is missing")
                continue
            digest = _sha256(path)
            if digest != item["sha256"]:
                log("document", "failed", name, "packaged file does not match its recorded hash")
                continue
            tid = str(tender_id) if tender_id is not None else None

            found = documents.find_one({"file_hash": digest})
            if found is not None:
                fid = str(found["_id"])
                if tender_id is not None and fid in (tender.get("document_ids") or []):
                    wanted = {"document_type": _clean(item.get("document_type")), "category": _clean(item.get("category")),
                              "reference_number": _clean(item.get("reference_number")),
                              "document_date": _clean(item.get("document_date")), "notes": _clean(item.get("notes")),
                              "client_name": tender.get("issuing_authority"), "project_title": tender.get("title"),
                              "contract_value": tender.get("estimated_value")}
                    changes = _document_changes(found, wanted) if update and found.get("submitted_by") == "import" else {}
                    if changes:
                        try:
                            schemas.check_iso_date(changes.get("document_date"), "Document date")
                        except ValueError as error:
                            log("document", "failed", name, str(error))
                            continue
                        if not dry_run:
                            documents.update_one({"_id": found["_id"]}, {"$set": changes})
                        log("document", "updated", name, "fields: " + ", ".join(sorted(changes)))
                    else:
                        log("document", "skipped", name, "already attached to this tender")
                    continue
                owned_elsewhere = any(found.get(k) for k in ("project_id", "employee_id")) or (
                    found.get("tender_id") and found.get("tender_id") != tid
                )
                if owned_elsewhere:
                    log("document", "skipped", name, "conflict: this file already belongs to another "
                        "record; left untouched")
                    continue
                if not dry_run:
                    tenders.update_one({"_id": tender_id}, {"$addToSet": {"document_ids": fid},
                                                            "$set": {"updated_at": _now()}})
                    documents.update_one({"_id": found["_id"]}, {"$set": {"tender_id": tid}})
                log("document", "linked", name, "file was already in the document log")
                continue

            if dry_run:
                log("document", "inserted", name, "dry run: would insert")
                continue
            display = storage.safe_display_name(name)
            extension = display.rsplit(".", 1)[-1].lower() if "." in display else ""
            if not (storage.allowed_file(display) or extension in extra_ext):
                log("document", "failed", name, "file type is not accepted by the PMS")
                continue
            limit = max_bytes or storage.MAX_UPLOAD_BYTES
            if os.path.getsize(path) > limit:
                log("document", "failed", name, "larger than the upload limit for this run (%.0f MB)" % (limit / 1048576))
                continue
            with open(path, "rb") as handle:
                stored = storage.write_upload(handle.read(), display)
            record = {
                "document_type": _clean(item.get("document_type")),
                "category": _clean(item.get("category")),
                "client_name": tender.get("issuing_authority"),
                "reference_number": _clean(item.get("reference_number")),
                "project_title": tender.get("title"),
                "contract_value": tender.get("estimated_value"),
                "document_date": _clean(item.get("document_date")),
                "department": None,
                "submitted_by": "import",
                "notes": _clean(item.get("notes")),
                "file_name": display,
                "stored_file_name": stored,
                "file_hash": digest,
                "project_id": None,
                "employee_id": None,
                "tender_id": tid,
                "redacted_from_id": None,
                "created_at": _now(),
            }
            text = _extraction_text(item.get("extraction"))
            if text:
                record["extracted_text"] = text
                record["extraction_methods"] = (item.get("extraction") or {}).get("methods")
            try:
                schemas.check_iso_date(record["document_date"], "Document date")
                new_id = documents.insert_one(record).inserted_id
            except (DuplicateKeyError, ValueError) as error:
                storage.remove_stored_file(stored)
                log("document", "failed", name, "%s: %s" % (type(error).__name__, error))
                continue
            except Exception as error:  # leave no orphan file behind
                storage.remove_stored_file(stored)
                log("document", "failed", name, "%s: %s" % (type(error).__name__, error))
                continue
            tenders.update_one({"_id": tender_id}, {"$addToSet": {"document_ids": str(new_id)},
                                                    "$set": {"updated_at": _now()}})
            tender.setdefault("document_ids", []).append(str(new_id))
            log("document", "inserted", name)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    parser.add_argument("--confirm-db", required=True, help="must equal the database in use")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-mb", type=float, help="raise the per-file size limit for this run")
    parser.add_argument("--update", action="store_true", help="refresh records this tool created from the manifest")
    parser.add_argument("--allow-ext", default="", help="comma list of extra types to accept (docx, xlsx)")
    args = parser.parse_args()
    extra_ext = {e.strip().lower().lstrip(".") for e in args.allow_ext.split(",") if e.strip()}
    if extra_ext - EXTRA_EXTENSIONS_ALLOWED:
        sys.exit("--allow-ext accepts only: %s" % ", ".join(sorted(EXTRA_EXTENSIONS_ALLOWED)))
    max_bytes = int(args.max_mb * 1048576) if args.max_mb else None

    if args.confirm_db != mongodb.MONGO_DB_NAME:
        sys.exit("Refusing to run: --confirm-db is %r but MONGO_DB_NAME is %r."
                 % (args.confirm_db, mongodb.MONGO_DB_NAME))
    reachable, error = mongodb.ping()
    if not reachable:
        sys.exit("Cannot reach MongoDB at %s: %s" % (mongodb.safe_uri(), error))
    print("database: %s / %s%s" % (mongodb.safe_uri(), mongodb.MONGO_DB_NAME, "  (DRY RUN)" if args.dry_run else ""))
    print("uploads : %s" % storage.UPLOAD_DIR)
    results = import_manifest(args.package, mongodb.get_tenders_collection(), mongodb.get_collection(),
                              args.dry_run, max_bytes, extra_ext, args.update)

    counts = {}
    for row in results:
        key = "%s %s" % (row["kind"], row["status"])
        counts[key] = counts.get(key, 0) + 1
        print("  %-9s %-9s %s%s" % (row["kind"], row["status"], row["item"], "  -- " + row["detail"] if row["detail"] else ""))
    print("summary:", ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "nothing in manifest")

    if not args.dry_run:
        folder = os.path.join(args.package, "reports")
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with open(os.path.join(folder, "tender_import_%s.json" % stamp), "w", encoding="utf-8") as handle:
            json.dump({"database": mongodb.MONGO_DB_NAME, "run_at": stamp, "counts": counts, "results": results},
                      handle, indent=1, default=str)
    sys.exit(1 if any(r["status"] == "failed" for r in results) else 0)


if __name__ == "__main__":
    main()
