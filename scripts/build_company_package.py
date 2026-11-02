"""Build the transferable company-documents package (run locally).

    python -I scripts/build_company_package.py --source DIR --metadata FILE \
        --ocr-cache DIR --out DIR

* --source    folder holding the original files (read only; never modified)
* --metadata  curated metadata, a JSON list; each entry names its file by
              `path` (relative to --source) and carries only values that were
              verified from the document. Anything unsupported is left out.
* --ocr-cache the llmthing_ocr.py cache; the extracted text of each file is
              attached by hash, so the server never needs OCR.
* --out       the package: manifest.json + files/. Rebuilding into the same
              folder is safe; files are keyed by hash and never duplicated.

The server runs backend/import_company_documents.py on the package. A later
batch is a new package; hashes already imported are recognised and skipped.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

FIELDS = (
    "title", "document_type", "category", "subcategory", "issuing_organisation",
    "certificate_number", "issue_date", "expiry_date", "notes",
    "metadata_source",
)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--ocr-cache", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.metadata, "r", encoding="utf-8") as handle:
        entries = json.load(handle)
    files_dir = os.path.join(args.out, "files")
    os.makedirs(files_dir, exist_ok=True)

    manifest, problems, seen = [], [], {}
    for entry in entries:
        source = os.path.join(args.source, *entry["path"].split("/"))
        if not os.path.isfile(source):
            problems.append("missing source file: " + entry["path"])
            continue
        digest = sha256_of(source)
        if digest in seen:
            problems.append("same bytes listed twice: %s and %s" % (seen[digest], entry["path"]))
            continue
        seen[digest] = entry["path"]
        extension = source.rsplit(".", 1)[-1].lower()
        packaged = "%s.%s" % (digest, extension)
        target = os.path.join(files_dir, packaged)
        if not os.path.exists(target) or sha256_of(target) != digest:
            shutil.copyfile(source, target)

        text = None
        cache = os.path.join(args.ocr_cache, digest + ".json")
        if os.path.exists(cache):
            with open(cache, "r", encoding="utf-8") as handle:
                record = json.load(handle)
            methods = sorted({p["method"] for p in record["pages"]})
            text = {
                "methods": methods,
                "model": next((p["model"] for p in record["pages"] if p.get("model")), None),
                "pages": [{"page": p["page"], "method": p["method"], "text": p["text"]} for p in record["pages"]],
            }
        item = {
            "source_key": "sha256:" + digest,
            "sha256": digest,
            "size": os.path.getsize(source),
            "file": "files/" + packaged,
            "original_name": os.path.basename(source),
            "source_path": entry["path"],
            "extraction": text,
        }
        for field in FIELDS:
            if entry.get(field) not in (None, ""):
                item[field] = entry[field]
        item.setdefault("metadata_source", "manual")
        manifest.append(item)

    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump({"kind": "company_documents", "version": 1, "documents": manifest}, handle,
                  ensure_ascii=False, indent=1)
    print("packaged %d document(s) into %s" % (len(manifest), args.out))
    for problem in problems:
        print("PROBLEM:", problem)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
