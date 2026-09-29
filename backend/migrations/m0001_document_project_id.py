"""0001 — give each project document an explicit project_id.

Until now the only record of which project a document belongs to was the
project's own `document_ids` array. Answering "which documents belong to this
project" therefore meant reading every document in the log and filtering it
against that array, and answering "which project does this document belong
to" meant scanning every project.

This migration writes the answer onto the document itself, as `project_id`
holding the project's existing id. It does not remove `document_ids`: both
representations are kept in step for the transition, and the code that
maintains them writes to both.

What it deliberately does NOT do:

  * It does not create, delete, merge or re-id any document, and it does not
    touch a single stored file. The only field it writes is `project_id`.
  * It does not assign a project to a document that two projects both claim.
    Choosing one would be a guess, and the wrong guess is invisible once
    made, so those are reported and left exactly as they are.
  * It does not touch documents no project references. A CV or a tender
    receipt lives in the same log and has no project, and null is the right
    answer for it -- not a project picked to fill the field in.
  * It does not touch any collection other than `documents` (plus its own
    record in `migrations`).
"""

from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING

VERSION = "0001_document_project_id"
DESCRIPTION = "Populate documents.project_id from project.document_ids, and index it."

# The index this migration is responsible for. Named so that ensure_indexes()
# in app/mongodb.py creates the identical one on startup -- create_index is
# idempotent, so whichever runs first wins and the other is a no-op.
INDEX_NAME = "ix_documents_project_id"


def _object_id(value):
    """A document id string as an ObjectId, or None if it is not one."""
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def analyse(db):
    """Read-only. What the migration would do, and what it refuses to do.

    Runs the same classification `apply` does, so --dry-run and the real run
    can never disagree about which documents are in which bucket.
    """
    documents = db["documents"]
    projects = db["projects"]

    existing_ids = {str(d["_id"]) for d in documents.find({}, {"_id": 1})}
    current_pid = {
        str(d["_id"]): d.get("project_id")
        for d in documents.find({}, {"_id": 1, "project_id": 1})
    }

    # document id -> every project claiming it
    claims = {}
    total_refs = 0
    for project in projects.find({}, {"_id": 1, "title": 1, "document_ids": 1}):
        for document_id in project.get("document_ids") or []:
            total_refs += 1
            claims.setdefault(document_id, []).append(
                {"id": str(project["_id"]), "title": project.get("title") or ""}
            )

    clean, multi, dangling = {}, {}, {}
    for document_id, owners in claims.items():
        if document_id not in existing_ids:
            dangling[document_id] = owners
        elif len(owners) > 1:
            multi[document_id] = owners
        else:
            clean[document_id] = owners[0]["id"]

    # Of the clean ones, which actually need writing.
    to_write = {
        document_id: project_id
        for document_id, project_id in clean.items()
        if current_pid.get(document_id) != project_id
    }
    # A clean document already carrying a *different* project's id. Not
    # something this migration creates, but it would be silently overwritten
    # if it were not called out, so it is reported separately.
    conflicting = {
        document_id: {"stored": current_pid.get(document_id), "referenced_by": project_id}
        for document_id, project_id in clean.items()
        if current_pid.get(document_id) not in (None, "", project_id)
    }

    return {
        "version": VERSION,
        "documents_total": len(existing_ids),
        "projects_total": projects.count_documents({}),
        "reference_entries": total_refs,
        "documents_referenced": len(claims),
        "mapped_cleanly": len(clean),
        "needing_write": len(to_write),
        "already_correct": len(clean) - len(to_write),
        "multi_project": multi,
        "dangling": dangling,
        "conflicting": conflicting,
        "unreferenced_documents": len(existing_ids) - len(claims),
        "_to_write": to_write,
    }


def apply(db):
    """Write project_id for every cleanly-mapped document, and index it.

    Safe to run repeatedly: a document already holding the right project_id
    is not in `_to_write`, so a second run writes nothing at all.
    """
    report = analyse(db)
    documents = db["documents"]

    # A document two projects both claim is left untouched, and so is one
    # whose stored project_id disagrees with the project referencing it.
    # Both are reported for a human to settle.
    skipped = set(report["multi_project"]) | set(report["conflicting"])

    written = 0
    for document_id, project_id in report["_to_write"].items():
        if document_id in skipped:
            continue
        oid = _object_id(document_id)
        if oid is None:
            continue
        result = documents.update_one({"_id": oid}, {"$set": {"project_id": project_id}})
        written += result.modified_count

    # Every document that is not a project document gets the field too, set
    # to None, so "has no project" is recorded rather than merely absent --
    # the two read the same in a query, but only one of them says the
    # question was asked.
    filled_null = documents.update_many(
        {"project_id": {"$exists": False}}, {"$set": {"project_id": None}}
    ).modified_count

    documents.create_index([("project_id", ASCENDING)], name=INDEX_NAME)

    report["written"] = written
    report["skipped"] = len(skipped)
    report["null_filled"] = filled_null
    report["index"] = INDEX_NAME
    report.pop("_to_write", None)
    return report


def report_lines(report):
    """The run's own account of itself, for the runner to print.

    The refusals are as much the report as the writes are: a document two
    projects claim is named here rather than folded into a count, because it
    is the only place a person will be told to go and settle it.
    """
    lines = [
        "documents in log           : %d" % report["documents_total"],
        "projects                   : %d" % report["projects_total"],
        "reference entries          : %d" % report["reference_entries"],
        "documents referenced       : %d" % report["documents_referenced"],
        "map cleanly to one project : %d" % report["mapped_cleanly"],
        "  already correct          : %d" % report["already_correct"],
        "  needing project_id       : %d" % report["needing_write"],
        "documents with no project  : %d" % report["unreferenced_documents"],
    ]

    for label, key in (
        ("REFERENCED BY MULTIPLE PROJECTS", "multi_project"),
        ("DANGLING (project points at a missing document)", "dangling"),
    ):
        entries = report.get(key) or {}
        if entries:
            lines.append("")
            lines.append("!! %d %s -- left untouched:" % (len(entries), label))
            for document_id, owners in entries.items():
                lines.append("   document %s" % document_id)
                for owner in owners:
                    lines.append("       project %s  %s" % (owner["id"], owner["title"][:48]))

    conflicting = report.get("conflicting") or {}
    if conflicting:
        lines.append("")
        lines.append("!! %d document(s) already hold a DIFFERENT project_id "
                     "-- left untouched:" % len(conflicting))
        for document_id, detail in conflicting.items():
            lines.append("   document %s: stored=%s referenced by=%s"
                         % (document_id, detail["stored"], detail["referenced_by"]))

    if "written" in report:
        lines.append("")
        lines.append("project_id written         : %d" % report["written"])
        lines.append("project_id set to null     : %d" % report["null_filled"])
        lines.append("skipped (needs a decision) : %d" % report["skipped"])
        lines.append("index ensured              : %s" % report["index"])
    return lines


def record(db, report):
    """Mark this migration as applied, with what it did.

    A re-run (--force) records its own numbers, but the first application's
    are kept beside them. Without that, re-running overwrote "wrote 36" with
    "wrote 0" -- true of the second run and useless as a record of what the
    migration actually did to this database.
    """
    now = datetime.now(timezone.utc)
    existing = db["migrations"].find_one({"_id": VERSION}) or {}

    entry = {
        "_id": VERSION,
        "description": DESCRIPTION,
        # Unchanged once set: when this database first had the migration run.
        "first_applied_at": existing.get("first_applied_at") or now,
        "first_written": existing.get("first_written", report.get("written", 0)),
        "first_null_filled": existing.get(
            "first_null_filled", report.get("null_filled", 0)
        ),
        # The most recent run, which for an already-migrated database writes 0.
        "last_run_at": now,
        "last_written": report.get("written", 0),
        "last_null_filled": report.get("null_filled", 0),
        "skipped": report.get("skipped", 0),
        "index": report.get("index"),
    }
    db["migrations"].replace_one({"_id": VERSION}, entry, upsert=True)
