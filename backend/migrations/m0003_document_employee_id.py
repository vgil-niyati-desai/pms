"""0003 - give each employee's document an explicit employee_id.

The counterpart of migration 0001, for the CVs area. Until now the only
record of whose a CV or a certificate scan was lived inside the employee, in
the `document_id` that the nested record carries. Answering "which documents
belong to this person" therefore meant reading the employee and following
each pointer, and answering "whose is this document" meant scanning every
employee's two arrays.

This migration writes the answer onto the document itself, as `employee_id`
holding the employee's existing id. It does not remove or alter the nested
pointers: both representations are kept in step for the transition, and the
code that maintains them writes to both.

Only `employee_id` is written. Which *CV version* or *certification* a
document belongs to is already answered from the other side, by the record
that names it, so a cv_id here would be a second copy of a fact that is not
in doubt.

What it deliberately does NOT do:

  * It does not create, delete, merge or re-id any document, does not touch a
    single stored file, and does not modify any employee. The only field it
    writes is documents.employee_id.
  * It does not assign an employee to a document that two employees both
    claim. Choosing one would be a guess, and the wrong guess is invisible
    once made, so those are reported and left exactly as they are.
  * It does not overwrite a document that already carries a *different*
    employee_id. That disagreement is a decision, not a value to correct.
  * It does not claim a document that already belongs to a project. A file
    referenced by a CV *and* stamped with a project_id is ambiguous in a way
    this migration cannot settle, so it is reported and left alone.
  * It does not touch documents no employee references. A project's evidence
    or a tender receipt lives in the same log and has no employee, and null
    is the right answer for it.
  * It does not touch project_id, document_ids, or any collection other than
    `documents` (plus its own record in `migrations`).
"""

from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING

VERSION = "0003_document_employee_id"
DESCRIPTION = (
    "Populate documents.employee_id from employees' cvs[] and certifications[], "
    "and index it."
)

# The index this migration is responsible for. Named so that ensure_indexes()
# in app/mongodb.py creates the identical one on startup -- create_index is
# idempotent, so whichever runs first makes it and the other is a no-op.
INDEX_NAME = "ix_documents_employee_id"

# The two embedded arrays whose records can point at a document.
NESTED_FIELDS = ("cvs", "certifications")


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
    employees = db["employees"]

    existing_ids = {str(d["_id"]) for d in documents.find({}, {"_id": 1})}
    stored = {
        str(d["_id"]): (d.get("employee_id"), d.get("project_id"))
        for d in documents.find({}, {"_id": 1, "employee_id": 1, "project_id": 1})
    }

    # document id -> every employee claiming it, once each however many of
    # their records point at it. Two CV versions of the same file is one
    # owner, not two.
    claims = {}
    total_refs = 0
    for employee in employees.find(
        {}, {"_id": 1, "full_name": 1, "cvs": 1, "certifications": 1}
    ):
        employee_id = str(employee["_id"])
        seen_here = {}
        for field in NESTED_FIELDS:
            for item in employee.get(field) or []:
                document_id = item.get("document_id")
                if not document_id:
                    continue
                total_refs += 1
                seen_here.setdefault(document_id, set()).add(field)
        for document_id, fields in seen_here.items():
            claims.setdefault(document_id, []).append(
                {
                    "id": employee_id,
                    "name": employee.get("full_name") or "",
                    "via": ", ".join(sorted(fields)),
                }
            )

    clean, multi, dangling = {}, {}, {}
    for document_id, owners in claims.items():
        if document_id not in existing_ids:
            dangling[document_id] = owners
        elif len(owners) > 1:
            multi[document_id] = owners
        else:
            clean[document_id] = owners[0]["id"]

    # A clean document already carrying a *different* employee's id. Not
    # something this migration creates, but it would be silently overwritten
    # if it were not called out, so it is reported and skipped.
    conflicting = {
        document_id: {
            "stored": stored.get(document_id, (None, None))[0],
            "referenced_by": employee_id,
        }
        for document_id, employee_id in clean.items()
        if stored.get(document_id, (None, None))[0] not in (None, "", employee_id)
    }

    # A document a CV points at that is also stamped as a project's. Two
    # areas both claiming one file is a question about the data, not
    # something to resolve by writing over one of the answers.
    project_claimed = {
        document_id: {
            "project_id": stored.get(document_id, (None, None))[1],
            "referenced_by": employee_id,
        }
        for document_id, employee_id in clean.items()
        if stored.get(document_id, (None, None))[1]
    }

    skipped = set(conflicting) | set(project_claimed)

    # Of the clean ones, which actually need writing.
    to_write = {
        document_id: employee_id
        for document_id, employee_id in clean.items()
        if document_id not in skipped
        and stored.get(document_id, (None, None))[0] != employee_id
    }

    return {
        "version": VERSION,
        "documents_total": len(existing_ids),
        "employees_total": employees.count_documents({}),
        "reference_entries": total_refs,
        "documents_referenced": len(claims),
        "mapped_cleanly": len(clean),
        "needing_write": len(to_write),
        "already_correct": len(clean) - len(to_write) - len(skipped),
        "multi_employee": multi,
        "dangling": dangling,
        "conflicting": conflicting,
        "project_claimed": project_claimed,
        "unreferenced_documents": len(existing_ids) - len(clean) - len(multi),
        "_to_write": to_write,
    }


def apply(db):
    """Write employee_id for every cleanly-mapped document, and index it.

    Safe to run repeatedly: a document already holding the right employee_id
    is not in `_to_write`, so a second run writes nothing at all.
    """
    report = analyse(db)
    documents = db["documents"]

    written = 0
    for document_id, employee_id in report["_to_write"].items():
        oid = _object_id(document_id)
        if oid is None:
            continue
        # The filter repeats what analyse() established. Between the read and
        # this write the document could have been claimed, and the migration
        # must not take it off whoever claimed it.
        result = documents.update_one(
            {
                "_id": oid,
                "$or": [
                    {"employee_id": None},
                    {"employee_id": {"$exists": False}},
                    {"employee_id": employee_id},
                ],
            },
            {"$set": {"employee_id": employee_id}},
        )
        written += result.modified_count

    # Every document that is not an employee's gets the field too, set to
    # None, so "has no employee" is recorded rather than merely absent -- the
    # two read the same in a query, but only one of them says the question
    # was asked. This is what migration 0001 did for project_id.
    filled_null = documents.update_many(
        {"employee_id": {"$exists": False}}, {"$set": {"employee_id": None}}
    ).modified_count

    documents.create_index([("employee_id", ASCENDING)], name=INDEX_NAME)

    report["written"] = written
    report["skipped"] = len(report["conflicting"]) + len(report["project_claimed"])
    report["null_filled"] = filled_null
    report["index"] = INDEX_NAME
    report.pop("_to_write", None)
    return report


def report_lines(report):
    """The run's own account of itself, for the runner to print.

    The refusals are as much the report as the writes are: a document two
    employees claim is named here rather than folded into a count, because it
    is the only place a person will be told to go and settle it.
    """
    lines = [
        "documents in log            : %d" % report["documents_total"],
        "employees                   : %d" % report["employees_total"],
        "reference entries           : %d" % report["reference_entries"],
        "documents referenced        : %d" % report["documents_referenced"],
        "map cleanly to one employee : %d" % report["mapped_cleanly"],
        "  already correct           : %d" % report["already_correct"],
        "  needing employee_id       : %d" % report["needing_write"],
        "documents with no employee  : %d" % report["unreferenced_documents"],
    ]

    for label, key in (
        ("REFERENCED BY MULTIPLE EMPLOYEES", "multi_employee"),
        ("DANGLING (a record points at a missing document)", "dangling"),
    ):
        entries = report.get(key) or {}
        if entries:
            lines.append("")
            lines.append("!! %d %s -- left untouched:" % (len(entries), label))
            for document_id, owners in entries.items():
                lines.append("   document %s" % document_id)
                for owner in owners:
                    lines.append(
                        "       employee %s  %s  (via %s)"
                        % (owner["id"], owner["name"][:40], owner["via"])
                    )

    conflicting = report.get("conflicting") or {}
    if conflicting:
        lines.append("")
        lines.append(
            "!! %d document(s) already hold a DIFFERENT employee_id "
            "-- left untouched:" % len(conflicting)
        )
        for document_id, detail in conflicting.items():
            lines.append(
                "   document %s: stored=%s referenced by=%s"
                % (document_id, detail["stored"], detail["referenced_by"])
            )

    project_claimed = report.get("project_claimed") or {}
    if project_claimed:
        lines.append("")
        lines.append(
            "!! %d document(s) referenced by an employee are ALSO a project's "
            "-- left untouched:" % len(project_claimed)
        )
        for document_id, detail in project_claimed.items():
            lines.append(
                "   document %s: project=%s referenced by employee=%s"
                % (document_id, detail["project_id"], detail["referenced_by"])
            )

    if "written" in report:
        lines.append("")
        lines.append("employee_id written         : %d" % report["written"])
        lines.append("employee_id set to null     : %d" % report["null_filled"])
        lines.append("skipped (needs a decision)  : %d" % report["skipped"])
        lines.append("index ensured               : %s" % report["index"])
    return lines


def record(db, report):
    """Mark this migration as applied, with what it did.

    A re-run (--force) records its own numbers, but the first application's
    are kept beside them -- otherwise re-running overwrites "wrote 12" with
    "wrote 0", which is true of the second run and useless as a record of
    what the migration did to this database.
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
