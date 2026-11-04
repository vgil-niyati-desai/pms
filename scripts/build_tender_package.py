"""Build a tender-documents package (run locally) for backend/import_tender_documents.py.

    python -I scripts/build_tender_package.py --source ROOT --bids FILE \
        --ocr-cache DIR [--ocr-log FILE] --out DIR

* --source     the folder whose sub-folders are the bids ("Country/Client"); read
               only, never modified.
* --bids       curated JSON, one entry per bid:
                 {"bids": [{"folder": "Botswana/NDB GRC",
                            "fields": {tender fields: only what the documents state},
                            "documents": [{"file": "<file name or unique part of it>",
                                           "kind": "client" | "vgil" | "other",
                                           "document_type": "...",
                                           "what": "short factual description",
                                           "document_date": "YYYY-MM-DD" (only if stated),
                                           "reference_number": (defaults to the tender's)}]}]}
               `kind` says who wrote the document, so a client-issued tender pack
               is never mistaken for a VGIL submission; it prefixes the stored notes.
* --ocr-cache  llmthing_ocr.py's cache. A scanned file's OCR text is attached by
               hash, so the server never needs OCR.
* --ocr-log    llmthing_ocr.py's --log file, to carry OCR failures into the log.
* --out        the package: manifest.json, files/<bid>/<original name>, and
               processing_log.json -- one line per file saying whether it was
               packaged and anything incomplete about it (no OCR result, over the
               upload limits, no reference or date, image-only pages not read).

Files in a bid folder that the curated list does not mention are logged as
`unclassified` and left out, never guessed at. Rebuilding into the same folder
is safe: files are keyed by name and checked by hash.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile

import pymupdf

LIMIT = 20 * 1024 * 1024
NATIVE_EXT = {"pdf", "png", "jpg", "jpeg"}
PREFIX = {"client": "Client-issued", "vgil": "VGIL-submitted", "other": ""}


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def inspect(path, ext):
    """Facts about the file: pages, how many have no text layer, sheets."""
    info = {}
    try:
        if ext == "pdf":
            doc = pymupdf.open(path)
            info["pages"] = len(doc)
            info["textless_pages"] = sum(1 for page in doc if len(page.get_text().strip()) < 40)
            # A file needs OCR when it has no usable text where it opens, or almost
            # no text anywhere. A text document with some image-only pages (an
            # embedded certificate, say) is not a scan: its text layer is read
            # directly and the image-only pages are only counted.
            info["scanned"] = info["textless_pages"] >= info["pages"] * 0.8 or (
                sum(len(doc[i].get_text().strip()) for i in range(min(5, len(doc)))) < 100
            )
        elif ext in ("png", "jpg", "jpeg"):
            info["pages"], info["scanned"], info["textless_pages"] = 1, True, 1
        elif ext == "xlsx":
            names = zipfile.ZipFile(path).namelist()
            info["sheets"] = len([n for n in names if n.startswith("xl/worksheets/sheet")])
    except Exception as error:  # an unreadable file is reported, not hidden
        info["unreadable"] = "%s: %s" % (type(error).__name__, error)
    return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--bids", required=True)
    parser.add_argument("--ocr-cache", required=True)
    parser.add_argument("--ocr-log")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.bids, "r", encoding="utf-8") as handle:
        bids = json.load(handle)["bids"]
    ocr_failures = {}
    if args.ocr_log and os.path.exists(args.ocr_log):
        with open(args.ocr_log, "r", encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                if event.get("status") == "failed":
                    ocr_failures[event["file"]] = event.get("error")
                elif event.get("status") == "ok":
                    ocr_failures.pop(event["file"], None)  # a later success supersedes

    os.makedirs(os.path.join(args.out, "files"), exist_ok=True)
    manifest, log = {"tenders": []}, []

    def record(bid, name, status, **extra):
        log.append({"bid": bid, "file": name, "status": status, **extra})

    for bid in bids:
        folder = os.path.join(args.source, *bid["folder"].split("/"))
        tender = {"source": "RFP Responses/" + bid["folder"], "fields": bid["fields"], "documents": []}
        default_reference = bid["fields"].get("reference_number")
        on_disk = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
        used = set()
        for spec in bid["documents"]:
            hits = [n for n in on_disk if n == spec["file"]] or [n for n in on_disk if spec["file"] in n]
            if len(hits) != 1:
                record(bid["folder"], spec["file"], "failed",
                       reason="no file matches" if not hits else "matches %d files: %s" % (len(hits), hits))
                continue
            name = hits[0]
            used.add(name)
            source = os.path.join(folder, name)
            ext = name.rsplit(".", 1)[-1].lower()
            digest = sha256_of(source)
            target_rel = "files/%s/%s" % (slug(bid["folder"]), name)
            target = os.path.join(args.out, *target_rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if not os.path.exists(target) or sha256_of(target) != digest:
                shutil.copyfile(source, target)
            info = inspect(source, ext)
            warnings = []
            if "unreadable" in info:
                record(bid["folder"], name, "failed", reason="unreadable: " + info["unreadable"])
                continue

            extraction = None
            if info.get("scanned"):
                cache = os.path.join(args.ocr_cache, digest + ".json")
                cached = json.load(open(cache, encoding="utf-8")) if os.path.exists(cache) else None
                if cached and cached.get("complete", True):
                    extraction = {"methods": sorted({p["method"] for p in cached["pages"]}),
                                  "model": next((p["model"] for p in cached["pages"] if p.get("model")), None),
                                  "pages": [{"page": p["page"], "method": p["method"], "text": p["text"]}
                                            for p in cached["pages"]]}
                    warnings.append("scanned: text read with LLMThing OCR (%d pages)" % len(cached["pages"]))
                else:
                    reason = ocr_failures.get(name) or "no OCR result in the cache"
                    warnings.append("INCOMPLETE: scanned file without OCR text (%s)" % reason)
            elif info.get("textless_pages"):
                warnings.append("%d of %d pages are image-only (no text layer) and were not OCR'd"
                                % (info["textless_pages"], info["pages"]))
            size = os.path.getsize(source)
            if size > LIMIT:
                warnings.append("over the 20 MB upload limit: needs --max-mb (%.0f MB)" % (size / 1048576))
            if ext not in NATIVE_EXT:
                warnings.append("file type .%s: needs --allow-ext" % ext)
            if not spec.get("document_date"):
                warnings.append("no document date stated")
            if not (spec.get("reference_number") or default_reference):
                warnings.append("no reference number for this bid")

            prefix = PREFIX[spec.get("kind", "other")]
            count = " (%d pages)" % info["pages"] if "pages" in info and ext == "pdf" else (
                " (%d sheets)" % info["sheets"] if "sheets" in info else "")
            notes = ("%s %s%s." % (prefix, spec["what"], count)).strip()
            if spec.get("notes"):
                notes += " " + spec["notes"]
            if extraction:
                notes += " Scanned document; its text was read with LLMThing OCR."
            entry = {"file": target_rel, "original_name": name, "sha256": digest,
                     "document_type": spec["document_type"], "notes": notes}
            if spec.get("document_date"):
                entry["document_date"] = spec["document_date"]
            if spec.get("reference_number") or default_reference:
                entry["reference_number"] = spec.get("reference_number") or default_reference
            if spec.get("category"):
                entry["category"] = spec["category"]
            if extraction:
                entry["extraction"] = extraction
            tender["documents"].append(entry)
            record(bid["folder"], name, "packaged", sha256=digest[:16], size=size, warnings=warnings)

        for name in on_disk:
            if name not in used and os.path.isfile(os.path.join(folder, name)):
                record(bid["folder"], name, "unclassified", reason="in the folder but not in the curated list")
        manifest["tenders"].append(tender)

    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "processing_log.json"), "w", encoding="utf-8") as handle:
        json.dump(log, handle, ensure_ascii=False, indent=1)

    counts = {}
    for row in log:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("bids: %d | files: %s" % (len(bids), ", ".join("%s=%d" % kv for kv in sorted(counts.items()))))
    flagged = [r for r in log if r.get("warnings")]
    print("files with something incomplete or needing an opt-in flag: %d" % len(flagged))
    sys.exit(1 if counts.get("failed") or counts.get("unclassified") else 0)


if __name__ == "__main__":
    main()
