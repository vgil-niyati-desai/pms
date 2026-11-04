"""Export, verify and restore a PMS database snapshot, and check uploaded files.

For the one-time move of a PMS database and its uploads to another machine.
It needs only pymongo (already a backend dependency): no mongodump or
mongorestore, which are installed on neither side.

    export        read a database (read-only) into a bundle directory
    verify        check a bundle is intact, and optionally equals the source
    restore       load a bundle into a database that does not exist yet
    verify-files  check a directory of uploads against the bundle's manifest
                  (standard library only; runs with any python3)

The connection string is read from an environment variable named by
--uri-env (default PMS_BUNDLE_URI), never from the command line, so a password
does not appear in a process list or shell history.

What a bundle holds, per collection:
    <name>.bson            the documents, byte for byte as MongoDB stores them
                           (RawBSONDocument), so _id values, types and field
                           order survive exactly
    <name>.indexes.json    every index definition except _id
    manifest.json          source db, server version, counts, sizes, SHA-256
                           of every file and a digest of each collection's
                           documents in _id order
    uploads_manifest.json  name, size and SHA-256 of every uploaded file
    references_report.json which stored file names the records mention, which
                           are missing from disk, which files nothing mentions

Nothing here deletes or overwrites anything. `export` refuses a non-empty
output directory; `restore` refuses a database that already holds any
collection, which is the "stop and ask" case.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

BUNDLE_VERSION = 1
FILE_KEY = "stored_file_name"


# --------------------------------------------------------------------- utils

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def connect(uri_env):
    from pymongo import MongoClient

    uri = os.environ.get(uri_env, "").strip()
    if not uri:
        sys.exit("Set %s to the MongoDB connection string first." % uri_env)
    return MongoClient(uri, serverSelectionTimeoutMS=8000)


def raw_collection(db, name):
    from bson.codec_options import CodecOptions
    from bson.raw_bson import RawBSONDocument

    return db.get_collection(name, codec_options=CodecOptions(document_class=RawBSONDocument))


def collection_digest(db, name):
    """SHA-256 over the raw bytes of every document, in _id order, and the count."""
    digest = hashlib.sha256()
    count = 0
    for doc in raw_collection(db, name).find({}).sort("_id", 1):
        digest.update(doc.raw)
        count += 1
    return digest.hexdigest(), count


def index_specs(db, name):
    from bson import json_util

    specs = []
    for index in db[name].list_indexes():
        spec = dict(index)
        if spec.get("name") == "_id_":
            continue
        for volatile in ("v", "ns"):
            spec.pop(volatile, None)
        spec["key"] = list(spec["key"].items())
        specs.append(json.loads(json_util.dumps(spec)))
    return specs


def find_file_references(value, found):
    """Every value stored under a `stored_file_name` key, at any depth."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == FILE_KEY and isinstance(inner, str):
                found.append(inner)
            else:
                find_file_references(inner, found)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            find_file_references(inner, found)


# -------------------------------------------------------------------- export

def file_listing(uploads_dir):
    entries = []
    for name in sorted(os.listdir(uploads_dir)):
        path = os.path.join(uploads_dir, name)
        if name == ".gitkeep" or not os.path.isfile(path):
            continue
        entries.append({"name": name, "size": os.path.getsize(path), "sha256": sha256_file(path)})
    return entries


def cmd_export(args):
    client = connect(args.uri_env)
    db = client[args.db]
    out = os.path.abspath(args.out)
    uploads = os.path.abspath(args.uploads)
    if os.path.exists(out) and os.listdir(out):
        sys.exit("Output directory %s is not empty; choose a new one." % out)
    if os.path.commonpath([out, uploads]) == uploads:
        sys.exit("Output directory must not be inside the uploads folder.")
    os.makedirs(out, exist_ok=True)

    names = sorted(db.list_collection_names())
    if not names:
        sys.exit("Database %r has no collections; refusing to export nothing." % args.db)

    info = client.server_info()
    fcv = client.admin.command("getParameter", 1, featureCompatibilityVersion=1)
    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_db": args.db,
        "source_server_version": info["version"],
        "source_feature_compatibility": fcv["featureCompatibilityVersion"]["version"],
        "collections": {},
    }

    references = []
    for name in names:
        bson_path = os.path.join(out, name + ".bson")
        digest = hashlib.sha256()
        count = 0
        with open(bson_path, "wb") as handle:
            for doc in raw_collection(db, name).find({}).sort("_id", 1):
                handle.write(doc.raw)
                digest.update(doc.raw)
                count += 1
        for doc in db[name].find({}):
            found = []
            find_file_references(doc, found)
            references.extend((name, ref) for ref in found)
        indexes_path = os.path.join(out, name + ".indexes.json")
        write_json(indexes_path, index_specs(db, name))
        manifest["collections"][name] = {
            "count": count,
            "documents_sha256": digest.hexdigest(),
            "bson_file": name + ".bson",
            "bson_bytes": os.path.getsize(bson_path),
            "bson_file_sha256": sha256_file(bson_path),
            "indexes_file": name + ".indexes.json",
            "indexes_file_sha256": sha256_file(indexes_path),
        }
        print("exported %-18s %6d documents" % (name, count))

    listing = file_listing(uploads)
    on_disk = {entry["name"] for entry in listing}
    referenced = {ref for _, ref in references}
    by_collection = {}
    for collection, _ in references:
        by_collection[collection] = by_collection.get(collection, 0) + 1
    report = {
        "files_on_disk": len(on_disk),
        "distinct_references": len(referenced),
        "references_by_collection": by_collection,
        "referenced_but_missing_on_disk": sorted(referenced - on_disk),
        "on_disk_but_unreferenced": sorted(on_disk - referenced),
        "absolute_looking_references": sorted(
            r for r in referenced if r.startswith(("/", "\\")) or (len(r) > 1 and r[1] == ":")
        ),
    }
    uploads_manifest = {
        "file_count": len(listing),
        "total_bytes": sum(entry["size"] for entry in listing),
        "files": listing,
    }
    write_json(os.path.join(out, "uploads_manifest.json"), uploads_manifest)
    write_json(os.path.join(out, "references_report.json"), report)
    manifest["uploads"] = {
        "file_count": uploads_manifest["file_count"],
        "total_bytes": uploads_manifest["total_bytes"],
        "manifest_sha256": sha256_file(os.path.join(out, "uploads_manifest.json")),
    }
    write_json(os.path.join(out, "manifest.json"), manifest)
    print("uploads: %d files, %d bytes; unreferenced: %d; missing: %d" % (
        uploads_manifest["file_count"], uploads_manifest["total_bytes"],
        len(report["on_disk_but_unreferenced"]), len(report["referenced_but_missing_on_disk"]),
    ))
    print("bundle written to", out)


# ------------------------------------------------------------ verify bundle

def check_bundle_files(bundle):
    """Every file in the manifest exists with the recorded hash and the .bson parses."""
    from bson import decode_all

    manifest = read_json(os.path.join(bundle, "manifest.json"))
    problems = []
    for name, entry in manifest["collections"].items():
        for key, hash_key in (("bson_file", "bson_file_sha256"), ("indexes_file", "indexes_file_sha256")):
            path = os.path.join(bundle, entry[key])
            if not os.path.isfile(path):
                problems.append("missing file %s" % entry[key])
            elif sha256_file(path) != entry[hash_key]:
                problems.append("checksum mismatch %s" % entry[key])
        path = os.path.join(bundle, entry["bson_file"])
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                data = handle.read()
            docs = decode_all(data)
            if len(docs) != entry["count"]:
                problems.append("%s: %d documents, manifest says %d" % (name, len(docs), entry["count"]))
            if len({repr(doc["_id"]) for doc in docs}) != len(docs):
                problems.append("%s: duplicate _id values" % name)
            if hashlib.sha256(data).hexdigest() != entry["documents_sha256"]:
                problems.append("%s: documents digest differs from manifest" % name)
    uploads_path = os.path.join(bundle, "uploads_manifest.json")
    if sha256_file(uploads_path) != manifest["uploads"]["manifest_sha256"]:
        problems.append("uploads_manifest.json checksum mismatch")
    return manifest, problems


def cmd_verify(args):
    bundle = os.path.abspath(args.bundle)
    manifest, problems = check_bundle_files(bundle)
    if args.against_source:
        client = connect(args.uri_env)
        db = client[manifest["source_db"] if not args.db else args.db]
        live = sorted(db.list_collection_names())
        if live != sorted(manifest["collections"]):
            problems.append("collections differ: source %s, bundle %s" % (live, sorted(manifest["collections"])))
        for name, entry in manifest["collections"].items():
            digest, count = collection_digest(db, name)
            if (digest, count) != (entry["documents_sha256"], entry["count"]):
                problems.append("%s: source now differs from the bundle (%d vs %d documents)" % (name, count, entry["count"]))
    for problem in problems:
        print("PROBLEM:", problem)
    print("bundle OK" if not problems else "bundle has %d problem(s)" % len(problems))
    sys.exit(1 if problems else 0)


# ------------------------------------------------------------------- restore

def cmd_restore(args):
    from bson import decode_all

    bundle = os.path.abspath(args.bundle)
    manifest, problems = check_bundle_files(bundle)
    if problems:
        sys.exit("Bundle failed verification, nothing restored:\n  " + "\n  ".join(problems))

    client = connect(args.uri_env)
    existing = [n for n in client.list_database_names() if n == args.db]
    if existing or client[args.db].list_collection_names():
        sys.exit("Database %r already exists on this server. Not touching it: ask first." % args.db)

    db = client[args.db]
    for name, entry in sorted(manifest["collections"].items()):
        with open(os.path.join(bundle, entry["bson_file"]), "rb") as handle:
            docs = decode_all(handle.read(), codec_options=_raw_options())
        db.create_collection(name)
        for start in range(0, len(docs), 500):
            db.get_collection(name, codec_options=_raw_options()).insert_many(
                docs[start:start + 500], ordered=True
            )
        for spec in read_json(os.path.join(bundle, entry["indexes_file"])):
            _create_index(db[name], spec)
        print("restored %-18s %6d documents" % (name, len(docs)))

    bad = []
    for name, entry in manifest["collections"].items():
        digest, count = collection_digest(db, name)
        if (digest, count) != (entry["documents_sha256"], entry["count"]):
            bad.append(name)
    if bad:
        sys.exit("RESTORED DATA DIFFERS from the bundle in: %s. Database %r left in place for inspection." % (bad, args.db))
    print("restore verified: every collection matches the bundle byte for byte")


def _raw_options():
    from bson.codec_options import CodecOptions
    from bson.raw_bson import RawBSONDocument

    return CodecOptions(document_class=RawBSONDocument)


def _create_index(collection, spec):
    from bson import json_util

    spec = json_util.loads(json.dumps(spec))
    keys = [(field, direction) for field, direction in spec.pop("key")]
    name = spec.pop("name")
    collection.create_index(keys, name=name, **spec)


# ------------------------------------------------------------- verify files

def cmd_verify_files(args):
    bundle = os.path.abspath(args.bundle)
    manifest = read_json(os.path.join(bundle, "uploads_manifest.json"))
    directory = os.path.abspath(args.dir)
    expected = {entry["name"]: entry for entry in manifest["files"]}
    present = {
        n for n in os.listdir(directory)
        if n != ".gitkeep" and os.path.isfile(os.path.join(directory, n))
    }
    problems = []
    for name in sorted(set(expected) - present):
        problems.append("missing: %s" % name)
    for name in sorted(present - set(expected)):
        problems.append("not in manifest: %s" % name)
    for name in sorted(set(expected) & present):
        path = os.path.join(directory, name)
        if os.path.getsize(path) != expected[name]["size"] or sha256_file(path) != expected[name]["sha256"]:
            problems.append("size/checksum mismatch: %s" % name)
    for problem in problems:
        print("PROBLEM:", problem)
    print("%d of %d files verified" % (len(expected) - len([p for p in problems if not p.startswith("not in")]), len(expected)))
    sys.exit(1 if problems else 0)


# -------------------------------------------------------------------- delta
#
# After the baseline has been delivered, `delta` finds what was added since,
# using _id: a record whose _id is not in the baseline is new. Only new records
# (and the new files they point at) go into the delta. Records that exist in
# the baseline but have since changed are listed in the report and are NOT
# transferred. Collections that hold only runtime state are left out.

DELTA_VERSION = 1
RUNTIME_COLLECTIONS = {"sessions", "login_throttle"}


def _dotted(doc, path):
    value = doc
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def unique_specs(index_specs_list):
    """The unique indexes of a collection, as (name, [fields], string_only)."""
    result = []
    for spec in index_specs_list:
        if spec.get("unique"):
            keys = [field for field, _ in spec["key"]]
            string_only = "$type" in json.dumps(spec.get("partialFilterExpression", {}))
            result.append((spec["name"], keys, string_only))
    return result


def unique_values(doc, spec):
    _, keys, string_only = spec
    values = tuple(_dotted(doc, key) for key in keys)
    if any(value is None for value in values):
        return None
    if string_only and not all(isinstance(value, str) for value in values):
        return None
    return values


def load_raw_by_id(path):
    from bson import decode_all

    with open(path, "rb") as handle:
        docs = decode_all(handle.read(), codec_options=_raw_options())
    return {doc["_id"]: doc for doc in docs}


def cmd_delta(args):
    from bson import decode

    baseline = os.path.abspath(args.baseline)
    uploads = os.path.abspath(args.uploads)
    out = os.path.abspath(args.out)
    base_manifest, problems = check_bundle_files(baseline)
    if problems:
        sys.exit("Baseline bundle failed verification:\n  " + "\n  ".join(problems))
    if os.path.exists(out) and os.listdir(out):
        sys.exit("Output directory %s is not empty; choose a new one." % out)
    if os.path.commonpath([out, uploads]) == uploads:
        sys.exit("Output directory must not be inside the uploads folder.")

    client = connect(args.uri_env)
    db = client[args.db or base_manifest["source_db"]]
    base_files = {e["name"] for e in read_json(os.path.join(baseline, "uploads_manifest.json"))["files"]}
    os.makedirs(os.path.join(out, "uploads"), exist_ok=True)

    report = {"collections": {}, "problems": []}
    delta_manifest = {
        "delta_version": DELTA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_db": db.name,
        "baseline_manifest_sha256": sha256_file(os.path.join(baseline, "manifest.json")),
        "collections": {},
    }
    referenced_by_new = set()
    referenced_by_modified = set()

    for name in sorted(db.list_collection_names()):
        if name in RUNTIME_COLLECTIONS:
            report["collections"][name] = {"skipped": "runtime state, never transferred",
                                           "local_count": db[name].estimated_document_count()}
            continue
        in_baseline = name in base_manifest["collections"]
        base_docs = load_raw_by_id(os.path.join(baseline, name + ".bson")) if in_baseline else {}
        base_index = read_json(os.path.join(baseline, name + ".indexes.json")) if in_baseline else index_specs(db, name)
        specs = unique_specs(base_index)
        seen_unique = [{} for _ in specs]
        for doc in base_docs.values():
            plain = decode(doc.raw)
            for slot, spec in zip(seen_unique, specs):
                values = unique_values(plain, spec)
                if values is not None:
                    slot[values] = doc["_id"]

        new_ids, modified, duplicates, same = [], [], [], 0
        new_raw = []
        current_ids = set()
        for doc in raw_collection(db, name).find({}).sort("_id", 1):
            current_ids.add(doc["_id"])
            previous = base_docs.get(doc["_id"])
            if previous is not None:
                if previous.raw == doc.raw:
                    same += 1
                else:
                    modified.append(str(doc["_id"]))
                    refs = []
                    find_file_references(decode(doc.raw), refs)
                    referenced_by_modified.update(refs)
                continue
            plain = decode(doc.raw)
            clash = None
            for slot, spec in zip(seen_unique, specs):
                values = unique_values(plain, spec)
                if values is not None and values in slot:
                    clash = {"id": str(doc["_id"]), "same_as": str(slot[values]), "index": spec[0]}
                    break
            if clash:
                duplicates.append(clash)
                continue
            for slot, spec in zip(seen_unique, specs):
                values = unique_values(plain, spec)
                if values is not None:
                    slot[values] = doc["_id"]
            new_ids.append(str(doc["_id"]))
            new_raw.append(doc.raw)
            refs = []
            find_file_references(plain, refs)
            referenced_by_new.update(refs)

        removed = sorted(str(i) for i in set(base_docs) - current_ids)
        report["collections"][name] = {
            "new_collection": not in_baseline,
            "baseline_count": len(base_docs), "local_count": len(current_ids),
            "unchanged": same, "new": len(new_ids),
            "modified_locally_not_transferred": modified,
            "duplicates_not_transferred": duplicates,
            "in_baseline_but_gone_locally": removed,
            "new_ids": new_ids,
        }
        if new_raw:
            bson_path = os.path.join(out, name + ".bson")
            digest = hashlib.sha256()
            with open(bson_path, "wb") as handle:
                for raw in new_raw:
                    handle.write(raw)
                    digest.update(raw)
            entry = {"count": len(new_raw), "bson_file": name + ".bson",
                     "bson_file_sha256": sha256_file(bson_path), "documents_sha256": digest.hexdigest(),
                     "new_collection": not in_baseline}
            indexes_path = os.path.join(out, name + ".indexes.json")
            write_json(indexes_path, base_index)
            entry["indexes_file"] = name + ".indexes.json"
            entry["indexes_file_sha256"] = sha256_file(indexes_path)
            delta_manifest["collections"][name] = entry

    on_disk = {n for n in os.listdir(uploads) if n != ".gitkeep" and os.path.isfile(os.path.join(uploads, n))}
    wanted = sorted(referenced_by_new - base_files)
    missing = [n for n in wanted if n not in on_disk]
    if missing:
        report["problems"].append("new records point at files missing from disk: %s" % missing)
    files = []
    for fname in wanted:
        if fname in on_disk:
            target = os.path.join(out, "uploads", fname)
            with open(os.path.join(uploads, fname), "rb") as src, open(target, "wb") as dst:
                for block in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(block)
            files.append({"name": fname, "size": os.path.getsize(target), "sha256": sha256_file(target)})
    delta_manifest["files"] = files
    report["files"] = {
        "new_files_in_delta": len(files),
        "already_on_server_referenced_again": len(referenced_by_new & base_files),
        "new_on_disk_not_in_delta": sorted(on_disk - base_files - referenced_by_new),
        "new_files_used_only_by_modified_records": sorted((referenced_by_modified - base_files) - referenced_by_new),
    }
    write_json(os.path.join(out, "delta_manifest.json"), delta_manifest)
    write_json(os.path.join(out, "delta_report.json"), report)

    lines = ["PMS delta report  (baseline: %s)" % base_manifest["created_at"], ""]
    for name, info in sorted(report["collections"].items()):
        if "skipped" in info:
            lines.append("%-18s skipped (%s, %d local)" % (name, info["skipped"], info["local_count"]))
            continue
        lines.append("%-18s baseline %5d  local %5d  NEW %5d  unchanged %5d  modified %d  duplicates %d  gone-locally %d" % (
            name, info["baseline_count"], info["local_count"], info["new"], info["unchanged"],
            len(info["modified_locally_not_transferred"]), len(info["duplicates_not_transferred"]),
            len(info["in_baseline_but_gone_locally"])))
    lines += ["", "files to copy: %d (%d bytes)" % (len(files), sum(f["size"] for f in files))]
    lines.append("new files on disk that no new record uses (not copied): %d" % len(report["files"]["new_on_disk_not_in_delta"]))
    lines += ["PROBLEM: " + p for p in report["problems"]]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(out, "delta_report.txt"), "w", encoding="utf-8") as handle:
        handle.write(text)
    print(text)
    print("delta written to", out)


# ------------------------------------------------------- apply (files / db)

def load_delta(path):
    delta = os.path.abspath(path)
    manifest = read_json(os.path.join(delta, "delta_manifest.json"))
    problems = []
    for name, entry in manifest["collections"].items():
        for key, hash_key in (("bson_file", "bson_file_sha256"), ("indexes_file", "indexes_file_sha256")):
            target = os.path.join(delta, entry[key])
            if not os.path.isfile(target) or sha256_file(target) != entry[hash_key]:
                problems.append("bad or missing %s" % entry[key])
    for entry in manifest["files"]:
        target = os.path.join(delta, "uploads", entry["name"])
        if not os.path.isfile(target) or sha256_file(target) != entry["sha256"]:
            problems.append("bad or missing file %s" % entry["name"])
    return delta, manifest, problems


def cmd_apply_files(args):
    """Copy the delta's files into the uploads folder. Never overwrites."""
    import shutil

    delta, manifest, problems = load_delta(args.delta)
    if problems:
        sys.exit("Delta failed verification, nothing copied:\n  " + "\n  ".join(problems))
    dest = os.path.abspath(args.dest)
    if not os.path.isdir(dest):
        sys.exit("Destination %s does not exist." % dest)
    copied = skipped = 0
    conflicts = []
    for entry in manifest["files"]:
        target = os.path.join(dest, entry["name"])
        if os.path.exists(target):
            if sha256_file(target) == entry["sha256"]:
                skipped += 1
            else:
                conflicts.append(entry["name"])
            continue
        if args.execute:
            shutil.copyfile(os.path.join(delta, "uploads", entry["name"]), target)
            if sha256_file(target) != entry["sha256"]:
                sys.exit("Checksum mismatch after copying %s" % entry["name"])
        copied += 1
    for name in conflicts:
        print("CONFLICT (exists with different content, left alone):", name)
    print("%s: %d to copy, %d already present and identical, %d conflicts" % (
        "COPIED" if args.execute else "DRY RUN", copied, skipped, len(conflicts)))
    if not args.execute:
        print("Add --execute to copy.")
    sys.exit(1 if conflicts else 0)


def cmd_apply_db(args):
    """Insert the delta's new records into a database. Insert-only."""
    from bson import decode, decode_all
    from pymongo.errors import DuplicateKeyError

    delta, manifest, problems = load_delta(args.delta)
    if problems:
        sys.exit("Delta failed verification, nothing inserted:\n  " + "\n  ".join(problems))
    client = connect(args.uri_env)
    db = client[args.db]
    if not db.list_collection_names():
        sys.exit("Database %r is empty or missing. Restore the baseline first." % args.db)
    uploads = os.path.abspath(args.uploads) if args.uploads else None
    summary, failed = [], False
    for name, entry in sorted(manifest["collections"].items()):
        with open(os.path.join(delta, entry["bson_file"]), "rb") as handle:
            docs = decode_all(handle.read(), codec_options=_raw_options())
        coll = db.get_collection(name, codec_options=_raw_options())
        specs = unique_specs(read_json(os.path.join(delta, entry["indexes_file"])))
        will_insert, present, clashes, no_file = [], [], [], []
        for doc in docs:
            existing = coll.find_one({"_id": doc["_id"]})
            if existing is not None:
                present.append((doc["_id"], existing.raw == doc.raw))
                continue
            plain = decode(doc.raw)
            clash = None
            for spec in specs:
                values = unique_values(plain, spec)
                if values is not None and coll.find_one(dict(zip(spec[1], values))) is not None:
                    clash = (doc["_id"], spec[0])
                    break
            if clash:
                clashes.append(clash)
                continue
            if uploads:
                refs = []
                find_file_references(plain, refs)
                if any(not os.path.isfile(os.path.join(uploads, r)) for r in refs):
                    no_file.append(doc["_id"])
                    continue
            will_insert.append(doc)
        if args.execute:
            if name not in db.list_collection_names():
                db.create_collection(name)
                for spec in read_json(os.path.join(delta, entry["indexes_file"])):
                    _create_index(db[name], spec)
            for doc in will_insert:
                try:
                    coll.insert_one(doc)
                except DuplicateKeyError:
                    clashes.append((doc["_id"], "duplicate key at insert"))
                    continue
                if coll.find_one({"_id": doc["_id"]}).raw != doc.raw:
                    failed = True
                    print("PROBLEM: %s %s differs after insert" % (name, doc["_id"]))
        summary.append("%-16s in delta %4d  %s %4d  already there %d (identical %d, DIFFERENT %d)  unique-key clashes %d  file missing %d" % (
            name, len(docs), "INSERTED" if args.execute else "would insert", len(will_insert), len(present),
            sum(1 for _, same in present if same), sum(1 for _, same in present if not same),
            len(clashes), len(no_file)))
    print("\n".join(summary))
    if not args.execute:
        print("DRY RUN: nothing written. Add --execute to insert.")
    sys.exit(1 if failed else 0)


# ------------------------------------------------------------------ updates
#
# A separate, opt-in package for records that exist in the baseline and have
# since been changed at the source. It carries only the changed fields, and for
# each one the value the baseline had. Applying it touches a field only while
# the target still holds that baseline value, so a change made on the target
# in the meantime (through MCP, say) is never overwritten: it is reported.

_MISSING = {"$missing": True}


def _ejson(value):
    from bson import json_util

    return json.loads(json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS))


def _from_ejson(value):
    from bson import json_util

    return json_util.loads(json.dumps(value), json_options=json_util.CANONICAL_JSON_OPTIONS)


def _brief(value, limit=90):
    text = json.dumps(_ejson(value), ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + "..."


def cmd_updates(args):
    from bson import decode

    baseline = os.path.abspath(args.baseline)
    out = os.path.abspath(args.out)
    base_manifest, problems = check_bundle_files(baseline)
    if problems:
        sys.exit("Baseline bundle failed verification:\n  " + "\n  ".join(problems))
    if os.path.exists(out) and os.listdir(out):
        sys.exit("Output directory %s is not empty; choose a new one." % out)
    os.makedirs(out, exist_ok=True)
    client = connect(args.uri_env)
    db = client[args.db or base_manifest["source_db"]]

    changes, lines = [], []
    for name in sorted(base_manifest["collections"]):
        if name in RUNTIME_COLLECTIONS:
            continue
        base_docs = load_raw_by_id(os.path.join(baseline, name + ".bson"))
        for doc in raw_collection(db, name).find({"_id": {"$in": list(base_docs)}}).sort("_id", 1):
            old_raw = base_docs[doc["_id"]]
            if old_raw.raw == doc.raw:
                continue
            old, cur = decode(old_raw.raw), decode(doc.raw)
            set_fields, unset_fields, expected = {}, [], {}
            for key in sorted((set(old) | set(cur)) - {"_id"}):
                if key in old and key in cur and old[key] == cur[key]:
                    continue
                expected[key] = old[key] if key in old else _MISSING
                if key in cur:
                    set_fields[key] = cur[key]
                else:
                    unset_fields.append(key)
            changes.append({"collection": name, "_id": _ejson(doc["_id"]),
                            "set": _ejson(set_fields), "unset": unset_fields,
                            "expected": _ejson(expected)})
            label = cur.get("title") or cur.get("file_name") or cur.get("name") or ""
            lines.append("%s %s  %s" % (name, doc["_id"], _brief(label, 70)))
            for key in sorted(set(set_fields) | set(unset_fields)):
                before = old.get(key, "(absent)")
                after = cur.get(key, "(removed)")
                if isinstance(before, list) and isinstance(after, list):
                    added = [x for x in after if x not in before]
                    dropped = [x for x in before if x not in after]
                    lines.append("    %-18s list %d -> %d items; added %d, dropped %d" % (key, len(before), len(after), len(added), len(dropped)))
                else:
                    lines.append("    %-18s %s  ->  %s" % (key, _brief(before), _brief(after)))
    package = {
        "updates_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_db": db.name,
        "baseline_manifest_sha256": sha256_file(os.path.join(baseline, "manifest.json")),
        "changes": changes,
    }
    write_json(os.path.join(out, "updates.json"), package)
    summary = ["PMS update package: %d existing record(s) with changed fields" % len(changes),
               "Apply only AFTER the matching delta, and only with approval.", ""] + lines
    text = "\n".join(summary) + "\n"
    with open(os.path.join(out, "updates_summary.txt"), "w", encoding="utf-8") as handle:
        handle.write(text)
    write_json(os.path.join(out, "updates_manifest.json"),
               {"updates_sha256": sha256_file(os.path.join(out, "updates.json")), "count": len(changes)})
    print(text)
    print("update package written to", out)


def cmd_apply_updates(args):
    package_dir = os.path.abspath(args.package)
    manifest = read_json(os.path.join(package_dir, "updates_manifest.json"))
    if sha256_file(os.path.join(package_dir, "updates.json")) != manifest["updates_sha256"]:
        sys.exit("updates.json does not match its manifest; refusing.")
    package = read_json(os.path.join(package_dir, "updates.json"))
    client = connect(args.uri_env)
    db = client[args.db]
    counts = {"apply": 0, "already": 0, "conflict": 0, "missing": 0}
    for change in package["changes"]:
        coll = db[change["collection"]]
        _id = _from_ejson(change["_id"])
        current = coll.find_one({"_id": _id})
        if current is None:
            counts["missing"] += 1
            print("MISSING on target, skipped: %s %s" % (change["collection"], _id))
            continue
        sets, expected = _from_ejson(change["set"]), _from_ejson(change["expected"])
        unsets = change["unset"]
        fields = list(sets) + list(unsets)
        if all((current.get(f, _MISSING) == sets[f]) if f in sets else (f not in current) for f in fields):
            counts["already"] += 1
            continue
        bad = [f for f in fields if (current[f] if f in current else _MISSING) != expected[f]]
        if bad:
            counts["conflict"] += 1
            print("CONFLICT, target changed since baseline, left alone: %s %s fields %s" % (change["collection"], _id, bad))
            continue
        counts["apply"] += 1
        if args.execute:
            flt = {"_id": _id}
            for f in fields:
                flt[f] = {"$exists": False} if expected[f] == _MISSING else expected[f]
            update = {}
            if sets:
                update["$set"] = sets
            if unsets:
                update["$unset"] = {f: "" for f in unsets}
            result = coll.update_one(flt, update)
            if result.modified_count != 1:
                sys.exit("Update of %s %s did not apply (changed concurrently?); stopping." % (change["collection"], _id))
    print("%s: %d to update, %d already up to date, %d conflicts, %d missing" % (
        "APPLIED" if args.execute else "DRY RUN", counts["apply"], counts["already"], counts["conflict"], counts["missing"]))
    if not args.execute:
        print("Nothing written. Add --execute to apply.")
    sys.exit(1 if counts["conflict"] or counts["missing"] else 0)


# ---------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uri-env", default="PMS_BUNDLE_URI", help="environment variable holding the MongoDB URI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("export", help="read a database into a new bundle directory")
    p.add_argument("--db", required=True)
    p.add_argument("--uploads", required=True, help="the uploads folder to inventory")
    p.add_argument("--out", required=True, help="new, empty directory for the bundle")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("verify", help="check a bundle; with --against-source also compare to the live source")
    p.add_argument("--bundle", required=True)
    p.add_argument("--against-source", action="store_true")
    p.add_argument("--db", help="source database (defaults to the one recorded in the bundle)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("restore", help="load a bundle into a database that does not exist yet")
    p.add_argument("--bundle", required=True)
    p.add_argument("--db", required=True, help="target database; must not exist")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("delta", help="package only what is new since a baseline bundle (read-only on the source)")
    p.add_argument("--baseline", required=True, help="the bundle already delivered")
    p.add_argument("--uploads", required=True)
    p.add_argument("--out", required=True, help="new, empty directory for the delta")
    p.add_argument("--db", help="source database (defaults to the baseline's)")
    p.set_defaults(func=cmd_delta)

    p = sub.add_parser("apply-files", help="copy a delta's files into an uploads folder; never overwrites")
    p.add_argument("--delta", required=True)
    p.add_argument("--dest", required=True)
    p.add_argument("--execute", action="store_true", help="without this, only report")
    p.set_defaults(func=cmd_apply_files)

    p = sub.add_parser("apply-db", help="insert a delta's new records into a database; insert-only")
    p.add_argument("--delta", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--uploads", help="if given, skip records whose files are not in this folder")
    p.add_argument("--execute", action="store_true", help="without this, only report")
    p.set_defaults(func=cmd_apply_db)

    p = sub.add_parser("updates", help="package changed fields of records that already exist in the baseline")
    p.add_argument("--baseline", required=True)
    p.add_argument("--out", required=True, help="new, empty directory")
    p.add_argument("--db", help="source database (defaults to the baseline's)")
    p.set_defaults(func=cmd_updates)

    p = sub.add_parser("apply-updates", help="apply an update package; only where the target still has the baseline value")
    p.add_argument("--package", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--execute", action="store_true", help="without this, only report")
    p.set_defaults(func=cmd_apply_updates)

    p = sub.add_parser("verify-files", help="check an uploads directory against the bundle's manifest")
    p.add_argument("--bundle", required=True)
    p.add_argument("--dir", required=True)
    p.set_defaults(func=cmd_verify_files)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
