"""Import CBS write-ups (or any folder of section-numbered write-ups) into the
proposal library. Safe to run repeatedly; nothing is deleted.

    cd backend
    python import_proposal_library.py SOURCE_DIR --confirm-db NAME [--dry-run]

SOURCE_DIR is the folder whose sub-folders are the sections, e.g.
"CBS Write-ups" containing "3.1.1 General Application Features" and so on.
A sub-folder inside a section (for example "a) Loan account creation") is
kept as the write-up's `subsection`. Copy the folder to the server as it is;
no OCR or other tool is needed there.

Like import_company_documents.py it refuses to run unless --confirm-db names
the database in use, and needs the unique source_key index from migration
0014. Per file, keyed by SHA-256:

  inserted  new write-up: file stored, text extracted, record created.
  skipped   the same bytes are already in the library.
  failed    the file is not what its extension says (for example a ".docx"
            that is not a Word document); nothing is written for it.

Screenshots inside a .docx are read by local OCR into `image_text` (kept apart from
the write-up's own text); --no-ocr turns that off. Files already in the library are
never touched, so they get no OCR text from a later run.

--limit N processes only the first N files in sorted order (a pilot batch).
Files other than .docx/.rtf are ignored and counted. A report is written to
SOURCE_DIR's parent under import_reports/.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app import docx_text, mongodb, proposal_library, schemas, storage


def _now():
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _gaps(record):
    """Metadata the file and its folder did not supply; reported, never filled in."""
    gaps = []
    for field in ("section_number", "section_title", "point_number", "requirement_text", "response_text"):
        if record.get(field) in (None, ""):
            gaps.append("no " + field)
    if record["word_count"] == 0:
        gaps.append("no text extracted")
    return gaps


def import_folder(source_dir, items, dry_run=False, limit=None, ocr=True):
    """limit: stop after this many .docx/.rtf files, taken in the order the
    sections and files sort (folder path, then file name). Files already in the
    library count towards it, so the same limit always means the same files."""
    results, ignored, seen = [], 0, 0
    source_dir = os.path.abspath(source_dir)
    for folder, _, names in sorted(os.walk(source_dir)):
        relative = os.path.relpath(folder, source_dir).replace("\\", "/")
        if relative == ".":
            continue
        parts = relative.split("/")
        section_number, section_title = docx_text.parse_section_folder(parts[0])
        subsection = " / ".join(parts[1:]) or None
        for name in sorted(names):
            extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if extension not in schemas.PROPOSAL_FILE_TYPES:
                ignored += 1
                continue
            if limit is not None and seen >= limit:
                continue
            seen += 1
            path = os.path.join(folder, name)
            source_path = relative + "/" + name

            def log(status, detail="", gaps=()):
                results.append({"status": status, "file": source_path, "detail": detail, "missing": list(gaps)})

            with open(path, "rb") as handle:
                contents = handle.read()
            try:
                record = proposal_library.build_record(
                    contents, name,
                    section_number=section_number, section_title=section_title,
                    subsection=subsection, point_number=docx_text.parse_point_number(name),
                    source_path=source_path, created_at=_now(), ocr=ocr,
                )
            except docx_text.UnreadableDocument as error:
                log("failed", "could not be read: %s" % error)
                continue
            gaps = _gaps(record)
            if items.find_one({"source_key": record["source_key"]}):
                log("skipped", "already in the library", gaps)
                continue
            if dry_run:
                log("inserted", "dry run: would insert", gaps)
                continue
            record["stored_file_name"] = storage.write_upload(contents, name)
            try:
                items.insert_one(record)
            except DuplicateKeyError:
                storage.remove_stored_file(record["stored_file_name"])
                log("skipped", "inserted concurrently by another process")
                continue
            except Exception as error:
                storage.remove_stored_file(record["stored_file_name"])
                log("failed", "%s: %s" % (type(error).__name__, error))
                continue
            log("inserted", "", gaps)
    return results, ignored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--confirm-db", required=True, help="must equal the database in use")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-ocr", action="store_true", help="do not read text out of embedded screenshots")
    parser.add_argument("--limit", type=int, help="process only the first N .docx/.rtf files (sorted by folder, then name)")
    args = parser.parse_args()
    if args.confirm_db != mongodb.MONGO_DB_NAME:
        sys.exit("Refusing to run: --confirm-db is %r but MONGO_DB_NAME is %r."
                 % (args.confirm_db, mongodb.MONGO_DB_NAME))
    reachable, error = mongodb.ping()
    if not reachable:
        sys.exit("Cannot reach MongoDB at %s: %s" % (mongodb.safe_uri(), error))
    items = mongodb.get_proposal_library_collection()
    names = {ix["name"] for ix in items.list_indexes()} if mongodb.MONGO_PROPOSAL_LIBRARY_COLLECTION_NAME in mongodb.get_database().list_collection_names() else set()
    if "ux_proposal_library_source_key" not in names:
        sys.exit("The unique source_key index is missing. Apply migration 0014_proposal_library "
                 "(python migrate.py), then run this again.")
    print("database: %s / %s%s" % (mongodb.safe_uri(), mongodb.MONGO_DB_NAME, "  (DRY RUN)" if args.dry_run else ""))
    print("uploads : %s" % storage.UPLOAD_DIR)
    results, ignored = import_folder(args.source, items, args.dry_run, args.limit, ocr=not args.no_ocr)
    counts = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        if row["status"] != "inserted" and row["status"] != "skipped":
            print("  %-8s %s -- %s" % (row["status"], row["file"], row["detail"]))
    print("summary:", ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "nothing found",
          "| ignored (not .docx/.rtf): %d" % ignored)
    if not args.dry_run:
        folder = os.path.join(os.path.dirname(os.path.abspath(args.source)), "import_reports")
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with open(os.path.join(folder, "proposal_library_%s.json" % stamp), "w", encoding="utf-8") as handle:
            json.dump({"database": mongodb.MONGO_DB_NAME, "run_at": stamp, "counts": counts,
                       "ignored": ignored, "results": results}, handle, indent=1)
    gaps = [row for row in results if row["missing"]]
    if gaps:
        print("metadata not supplied by the files for %d of %d files (left empty):" % (len(gaps), len(results)))
        for row in gaps[:20]:
            print("  %s -- %s" % (row["file"], ", ".join(row["missing"])))
    sys.exit(1 if counts.get("failed") else 0)


if __name__ == "__main__":
    main()
