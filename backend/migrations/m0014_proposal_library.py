"""0014 - proposal library: indexes for the new `proposal_library` collection.

Prepared, NOT yet applied anywhere. The collection holds reusable proposal
write-ups (CBS feature write-ups). It is new, so there is no data to migrate
and no existing collection is touched; MongoDB creates the collection with
its first index. Indexes:

  ix_proposal_library_section   section order and the section filter
  ux_proposal_library_source_key  unique (partial) stable import identity, so a
                                  re-run of the import can never insert a
                                  write-up twice
  ix_proposal_library_file_type   the file-type filter
"""

from datetime import datetime, timezone

from pymongo import ASCENDING

COLLECTION = "proposal_library"
VERSION = "0014_proposal_library"
DESCRIPTION = "Create the indexes for the proposal_library collection."
INDEXES = {
    "ix_proposal_library_section": (
        [("section_key", ASCENDING), ("subsection", ASCENDING), ("point_number", ASCENDING)],
        {},
    ),
    "ux_proposal_library_source_key": (
        [("source_key", ASCENDING)],
        {"unique": True, "partialFilterExpression": {"source_key": {"$type": "string"}}},
    ),
    "ix_proposal_library_file_type": ([("file_type", ASCENDING)], {}),
}


def _existing(db):
    if COLLECTION not in db.list_collection_names():
        return {}
    return {index["name"]: index for index in db[COLLECTION].list_indexes()}


def analyse(db):
    existing = _existing(db)
    return {
        "version": VERSION,
        "collection_exists": COLLECTION in db.list_collection_names(),
        "missing": [name for name in INDEXES if name not in existing],
    }


def apply(db):
    report = analyse(db)
    for name in report["missing"]:
        key, options = INDEXES[name]
        db[COLLECTION].create_index(key, name=name, **options)
    report["created"] = list(report["missing"])
    return report


def report_lines(report):
    created = report.get("created")
    return [
        "collection exists : %s" % ("yes" if report["collection_exists"] else "no (created with the first index)"),
        ("indexes created : %s" % (", ".join(created) or "none (already present)"))
        if created is not None
        else ("indexes to create : %s" % (", ".join(report["missing"]) or "none")),
    ]


def record(db, report):
    now = datetime.now(timezone.utc)
    existing = db["migrations"].find_one({"_id": VERSION}) or {}
    db["migrations"].replace_one(
        {"_id": VERSION},
        {
            "_id": VERSION,
            "description": DESCRIPTION,
            "first_applied_at": existing.get("first_applied_at") or now,
            "last_run_at": now,
            "indexes": list(INDEXES),
        },
        upsert=True,
    )
