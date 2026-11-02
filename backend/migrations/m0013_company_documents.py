"""0013 - company documents: indexes for the new scope / source_key fields.

Company documents are rows of the existing `documents` collection carrying
scope="company" and a few descriptive fields (title, subcategory,
issuing_organisation, certificate_number, issue_date, expiry_date,
metadata_source, needs_review, source_key). MongoDB needs no schema change
for new optional fields, and existing rows are deliberately left alone: no
field is backfilled, so no existing document, CV or certification file is
modified. This migration only creates two indexes:

  ix_documents_scope_category   filters of the Company Documents section
  ux_documents_source_key       unique (partial) stable import identity, so an
                                import can never insert the same source twice
"""

from datetime import datetime, timezone

from pymongo import ASCENDING

COLLECTION = "documents"
VERSION = "0013_company_documents"
DESCRIPTION = "Create the scope/category and unique source_key indexes for company documents."
INDEXES = {
    "ix_documents_scope_category": ([("scope", ASCENDING), ("category", ASCENDING)], {}),
    "ux_documents_source_key": (
        [("source_key", ASCENDING)],
        {"unique": True, "partialFilterExpression": {"source_key": {"$type": "string"}}},
    ),
}


class CompanyDocumentIndexConflict(RuntimeError):
    pass


def _existing(collection):
    return {index["name"]: index for index in collection.list_indexes()}


def analyse(db):
    collection = db[COLLECTION]
    existing = _existing(collection)
    duplicates = list(
        collection.aggregate(
            [
                {"$match": {"source_key": {"$type": "string"}}},
                {"$group": {"_id": "$source_key", "n": {"$sum": 1}}},
                {"$match": {"n": {"$gt": 1}}},
            ]
        )
    )
    return {
        "version": VERSION,
        "missing": [name for name in INDEXES if name not in existing],
        "duplicate_source_keys": len(duplicates),
    }


def apply(db):
    report = analyse(db)
    if report["duplicate_source_keys"]:
        raise CompanyDocumentIndexConflict(
            "%s refused to run: %d source_key value(s) are shared by several documents; "
            "nothing was written." % (VERSION, report["duplicate_source_keys"])
        )
    for name in report["missing"]:
        key, options = INDEXES[name]
        db[COLLECTION].create_index(key, name=name, **options)
    report["created"] = list(report["missing"])
    return report


def report_lines(report):
    created = report.get("created")
    return [
        "duplicate source_key values : %d" % report["duplicate_source_keys"],
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
