"""0005 - give every tender a cited_project_ids list, and index it.

A tender can now cite the past projects it puts forward as experience. The
citations live on the tender, as `cited_project_ids`, because a project is
evidence in its own right: it outlives the bid and may be cited by any number
of bids at once, so nothing is written to the project side.

Unlike migrations 0001, 0003 and 0004, this one has nothing to derive. Those
three backfilled a reverse pointer from a relationship the data already
recorded somewhere else; there has never been any record of which projects a
tender cites, so there is nothing to read it from and nothing to guess at.
What it does instead is make the field real on every existing tender -- an
absent list and an empty one read the same in a query, but only one of them
says the question was asked -- and create the index the lookups rely on.

What it deliberately does NOT do:

  * It does not create, delete or modify any project, and it does not touch
    projects.document_ids, documents.project_id, or any other ownership
    field. A citation is not a claim on the project or on its documents.
  * It does not invent a citation for any tender. A tender that cites
    nothing gets an empty list, which is the truth about it.
  * It does not remove a citation that points at a project which is no
    longer there. Those are reported so a person can look; clearing them
    silently would destroy the only remaining record that the bid ever cited
    anything. Deleting a project clears its citations going forward.
  * It does not touch any collection other than `tenders` (plus its own
    record in `migrations`).
"""

from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING

VERSION = "0005_tender_cited_project_ids"
DESCRIPTION = "Add cited_project_ids to every tender, and index it."

# The index this migration is responsible for. Named so that
# ensure_tender_indexes() in app/mongodb.py creates the identical one on
# startup -- create_index is idempotent, so whichever runs first makes it and
# the other is a no-op.
INDEX_NAME = "ix_tenders_cited_project_ids"

FIELD = "cited_project_ids"


def _object_id(value):
    """A project id string as an ObjectId, or None if it is not one."""
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def analyse(db):
    """Read-only. What the migration would do, and what it refuses to do.

    Runs the same classification `apply` does, so --dry-run and the real run
    can never disagree.
    """
    tenders = db["tenders"]
    projects = db["projects"]

    total = tenders.count_documents({})
    missing = tenders.count_documents({FIELD: {"$exists": False}})

    existing_projects = {str(p["_id"]) for p in projects.find({}, {"_id": 1})}

    citing = 0
    citations = 0
    dangling = {}
    for tender in tenders.find(
        {FIELD: {"$exists": True, "$ne": []}}, {"_id": 1, "title": 1, FIELD: 1}
    ):
        cited = tender.get(FIELD) or []
        citing += 1
        citations += len(cited)
        gone = [pid for pid in cited if pid not in existing_projects]
        if gone:
            dangling[str(tender["_id"])] = {
                "title": tender.get("title") or "",
                "project_ids": gone,
            }

    return {
        "version": VERSION,
        "tenders_total": total,
        "projects_total": projects.count_documents({}),
        "needing_field": missing,
        "already_have_field": total - missing,
        "tenders_citing": citing,
        "citations": citations,
        "dangling": dangling,
    }


def apply(db):
    """Give every tender the field, and create the index.

    Safe to run repeatedly: a tender that already has the field is not
    matched, so a second run writes nothing at all.
    """
    report = analyse(db)
    tenders = db["tenders"]

    filled = tenders.update_many(
        {FIELD: {"$exists": False}}, {"$set": {FIELD: []}}
    ).modified_count

    tenders.create_index([(FIELD, ASCENDING)], name=INDEX_NAME)

    report["filled"] = filled
    report["index"] = INDEX_NAME
    return report


def report_lines(report):
    """The run's own account of itself, for the runner to print."""
    lines = [
        "tenders                    : %d" % report["tenders_total"],
        "projects                   : %d" % report["projects_total"],
        "already have the field     : %d" % report["already_have_field"],
        "needing the field          : %d" % report["needing_field"],
        "tenders citing a project   : %d" % report["tenders_citing"],
        "citations in total         : %d" % report["citations"],
    ]

    dangling = report.get("dangling") or {}
    if dangling:
        lines.append("")
        lines.append(
            "!! %d tender(s) cite a project that is no longer there "
            "-- left untouched:" % len(dangling)
        )
        for tender_id, detail in dangling.items():
            lines.append("   tender %s  %s" % (tender_id, detail["title"][:48]))
            for project_id in detail["project_ids"]:
                lines.append("       project %s" % project_id)

    if "filled" in report:
        lines.append("")
        lines.append("cited_project_ids added    : %d" % report["filled"])
        lines.append("index ensured              : %s" % report["index"])
    return lines


def record(db, report):
    """Mark this migration as applied, with what it did.

    A re-run (--force) records its own numbers, but the first application's
    are kept beside them, as in its siblings.
    """
    now = datetime.now(timezone.utc)
    existing = db["migrations"].find_one({"_id": VERSION}) or {}

    entry = {
        "_id": VERSION,
        "description": DESCRIPTION,
        "first_applied_at": existing.get("first_applied_at") or now,
        "first_filled": existing.get("first_filled", report.get("filled", 0)),
        "last_run_at": now,
        "last_filled": report.get("filled", 0),
        "index": report.get("index"),
    }
    db["migrations"].replace_one({"_id": VERSION}, entry, upsert=True)
