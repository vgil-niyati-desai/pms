"""0015 - remove the retired review-status fields.

Prepared, NOT yet applied anywhere. The review workflow (a "needs review"
flag and an "expiry unverified" flag) was removed from Company Documents and
the Proposal Library. The application no longer reads, writes or shows these
fields, so any value still stored is inert; this migration deletes them.

It touches only:
  documents        where scope = "company"   -> needs_review, expiry_unverified
  proposal_library (every record)             -> needs_review

No other field and no other record is changed, and no file is touched.
Dates are not altered: an expiry_date stays exactly as stored.
"""

from datetime import datetime, timezone

VERSION = "0015_remove_review_flags"
DESCRIPTION = "Remove the retired needs_review and expiry_unverified fields."
TARGETS = (
    ("documents", {"scope": "company"}, ("needs_review", "expiry_unverified")),
    ("proposal_library", {}, ("needs_review",)),
)


def _count(db, collection, base, fields):
    if collection not in db.list_collection_names():
        return 0
    return db[collection].count_documents(
        {**base, "$or": [{field: {"$exists": True}} for field in fields]}
    )


def analyse(db):
    return {
        "version": VERSION,
        "to_clean": {collection: _count(db, collection, base, fields) for collection, base, fields in TARGETS},
    }


def apply(db):
    report = analyse(db)
    cleaned = {}
    for collection, base, fields in TARGETS:
        if collection not in db.list_collection_names():
            cleaned[collection] = 0
            continue
        result = db[collection].update_many(
            {**base, "$or": [{field: {"$exists": True}} for field in fields]},
            {"$unset": {field: "" for field in fields}},
        )
        cleaned[collection] = result.modified_count
    report["cleaned"] = cleaned
    return report


def report_lines(report):
    if "cleaned" in report:
        return ["records cleaned in %s : %d" % (name, count) for name, count in report["cleaned"].items()]
    return ["records to clean in %s : %d" % (name, count) for name, count in report["to_clean"].items()]


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
            "cleaned": report.get("cleaned", {}),
        },
        upsert=True,
    )
