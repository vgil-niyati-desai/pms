"""Import a company-documents package into MongoDB. Safe to run repeatedly.

    cd backend
    python import_company_documents.py PACKAGE_DIR --confirm-db NAME [--dry-run]

Works against whatever MONGO_* in backend/.env points at, and refuses to run
unless --confirm-db names that database, so a script run against the wrong
environment stops instead of writing. Needs the unique source_key index
(migration 0013, or one start of the API).

Per document, keyed by the file's SHA-256 (source_key "sha256:<hash>"):

  inserted  new: the file is copied into the uploads folder under a fresh
            name and a company-document record is created.
  updated   already imported: fields that are still EMPTY on the record are
            filled from the package. A value is never overwritten, and a
            record a person has edited (metadata_source "manual") is never
            touched -- so a re-import cannot undo review work.
  skipped   already imported and nothing to add; or the same file is already
            held by a non-company record (an employee's document, say), which
            is left exactly as it is and reported as a conflict.
  failed    the packaged file is missing or its hash does not match; nothing
            is written for it.

Nothing is ever deleted. A report is written to PACKAGE_DIR/reports/.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app import mongodb, schemas, storage

FILL_FIELDS = (
    "title", "document_type", "category", "subcategory", "issuing_organisation",
    "certificate_number", "issue_date", "expiry_date", "notes",
)


def _now():
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _extraction_text(extraction):
    if not extraction:
        return None
    return "\n\n".join(
        "[page %d]\n%s" % (p["page"], p["text"]) for p in extraction["pages"] if p.get("text")
    ) or None


def import_package(package_dir, documents, dry_run=False):
    with open(os.path.join(package_dir, "manifest.json"), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    results = []

    def log(status, item, detail=""):
        results.append({"status": status, "file": item.get("original_name"),
                        "sha256": item.get("sha256"), "detail": detail})

    for item in manifest["documents"]:
        path = os.path.join(package_dir, *item["file"].split("/"))
        if not os.path.isfile(path):
            log("failed", item, "packaged file is missing")
            continue
        if _sha256(path) != item["sha256"]:
            log("failed", item, "packaged file does not match its recorded hash")
            continue

        existing = documents.find_one(
            {"$or": [{"source_key": item["source_key"]}, {"file_hash": item["sha256"]}]}
        )
        if existing is not None:
            if existing.get("scope") != schemas.COMPANY_SCOPE:
                log("skipped", item, "conflict: this file is already held by a non-company "
                    "record (%s); left untouched" % existing.get("document_type"))
                continue
            if existing.get("metadata_source") == "manual":
                log("skipped", item, "already imported and edited by a person; left untouched")
                continue
            fill = {f: item[f] for f in FILL_FIELDS if item.get(f) and not existing.get(f)}
            text = _extraction_text(item.get("extraction"))
            if text and not existing.get("extracted_text"):
                fill["extracted_text"] = text
                fill["extraction_methods"] = item["extraction"]["methods"]
            if not fill:
                log("skipped", item, "already imported; nothing new")
                continue
            if not dry_run:
                documents.update_one({"_id": existing["_id"]}, {"$set": fill})
            log("updated", item, "filled empty fields: " + ", ".join(sorted(fill)))
            continue

        if dry_run:
            log("inserted", item, "dry run: would insert")
            continue
        with open(path, "rb") as handle:
            contents = handle.read()
        original = storage.safe_display_name(item["original_name"])
        if not storage.allowed_file(original):
            log("failed", item, "file type is not accepted by the PMS")
            continue
        if len(contents) > storage.MAX_UPLOAD_BYTES:
            log("failed", item, "larger than the PMS upload limit (%s)" % storage.MAX_UPLOAD_LABEL)
            continue
        stored = storage.write_upload(contents, original)
        record = {
            "scope": schemas.COMPANY_SCOPE,
            **{f: item.get(f) for f in FILL_FIELDS},
            "file_name": original,
            "stored_file_name": stored,
            "file_hash": item["sha256"],
            "source_key": item["source_key"],
            "source_path": item.get("source_path"),
            "metadata_source": item.get("metadata_source", "manual"),
            "extracted_text": _extraction_text(item.get("extraction")),
            "extraction_methods": (item.get("extraction") or {}).get("methods"),
            "submitted_by": "import",
            "project_id": None, "employee_id": None, "tender_id": None, "redacted_from_id": None,
            "created_at": _now(),
        }
        try:
            documents.insert_one(record)
        except DuplicateKeyError:
            storage.remove_stored_file(stored)
            log("skipped", item, "inserted concurrently by another process")
            continue
        except Exception as error:  # leave no orphan file behind
            storage.remove_stored_file(stored)
            log("failed", item, "%s: %s" % (type(error).__name__, error))
            continue
        log("inserted", item)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    parser.add_argument("--confirm-db", required=True, help="must equal the database in use")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.confirm_db != mongodb.MONGO_DB_NAME:
        sys.exit("Refusing to run: --confirm-db is %r but MONGO_DB_NAME is %r."
                 % (args.confirm_db, mongodb.MONGO_DB_NAME))
    reachable, error = mongodb.ping()
    if not reachable:
        sys.exit("Cannot reach MongoDB at %s: %s" % (mongodb.safe_uri(), error))
    database = mongodb.get_database()
    documents_index_names = {ix["name"] for ix in database[mongodb.MONGO_COLLECTION_NAME].list_indexes()}
    if "ux_documents_source_key" not in documents_index_names:
        sys.exit("The unique source_key index is missing. Apply migration 0013_company_documents "
                 "(python migrate.py) or start the API once, then run this again.")

    documents = mongodb.get_collection()
    print("database: %s / %s%s" % (mongodb.safe_uri(), mongodb.MONGO_DB_NAME,
                                  "  (DRY RUN)" if args.dry_run else ""))
    print("uploads : %s" % storage.UPLOAD_DIR)
    results = import_package(args.package, documents, args.dry_run)

    counts = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        print("  %-8s %s%s" % (row["status"], row["file"], "  -- " + row["detail"] if row["detail"] else ""))
    print("summary:", ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "nothing in package")

    if not args.dry_run:
        folder = os.path.join(args.package, "reports")
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with open(os.path.join(folder, "import_%s.json" % stamp), "w", encoding="utf-8") as handle:
            json.dump({"database": mongodb.MONGO_DB_NAME, "run_at": stamp, "counts": counts,
                       "results": results}, handle, indent=1)
    sys.exit(1 if counts.get("failed") else 0)


if __name__ == "__main__":
    main()
