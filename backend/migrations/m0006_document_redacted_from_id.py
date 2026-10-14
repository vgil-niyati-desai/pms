"""0006 - give every document a redacted_from_id, and index it.

A redacted copy is now saved into the document log as an entry of its own,
beside the original it was made from. The copy records where it came from, as
`redacted_from_id` holding the original document's id; an ordinary entry holds
None, because it is nothing's copy.

Like 0005 and unlike 0001, 0003 and 0004, this migration derives nothing.
Those three backfilled a pointer from a relationship the data already recorded
somewhere else. There has never been any record of which entry is a redacted
copy of which -- redaction used to stream the copy to the browser and keep no
trace of it -- so there is nothing to read it from and nothing to guess at.
What it does instead is make the field real on every existing document, so
"this is not a redacted copy" is recorded rather than merely absent, and
create the index the lookup relies on.

The field also makes the relationship one-directional on purpose. The original
is not told about its copies and gains no field of its own: the copies of a
document are found by querying this one. That is what keeps saving a copy from
being a write to the original in any form.

What it deliberately does NOT do:

  * It does not identify any existing entry as a redacted copy. Entries whose
    file name happens to end in `_redacted` are not claimed: a name is not
    provenance, the file may have arrived redacted from outside, and inventing
    a link to an original that may not be in the log would assert something
    this migration cannot know. They are counted and reported so a person can
    look, and left exactly as they are.
  * It does not create, delete, re-id or modify any document beyond adding
    this one field where it is absent, and it does not touch a single stored
    file.
  * It does not touch project_id, employee_id, tender_id, file_name,
    stored_file_name, file_hash, notes, or any collection other than
    `documents` (plus its own record in `migrations`).
"""

from datetime import datetime, timezone

from pymongo import ASCENDING

VERSION = "0006_document_redacted_from_id"
DESCRIPTION = "Add redacted_from_id to every document, and index it."

# The index this migration is responsible for. Named so that ensure_indexes()
# in app/mongodb.py creates the identical one on startup -- create_index is
# idempotent, so whichever runs first makes it and the other is a no-op.
INDEX_NAME = "ix_documents_redacted_from_id"

FIELD = "redacted_from_id"


def analyse(db):
    """Read-only. What the migration would do, and what it refuses to do.

    Runs the same counts `apply` does, so --dry-run and the real run can never
    disagree.
    """
    documents = db["documents"]

    total = documents.count_documents({})
    missing = documents.count_documents({FIELD: {"$exists": False}})

    # Already linked: only the save-redacted-copy endpoint writes these, so on
    # a database that has not run it yet this is zero. Counted rather than
    # assumed, because a re-run has to report what it found.
    linked = documents.count_documents({FIELD: {"$nin": [None, ""]}})

    # Entries that *look* like copies by name alone. Reported, never claimed --
    # see the module docstring.
    named_redacted = [
        {"id": str(d["_id"]), "file_name": d.get("file_name") or ""}
        for d in documents.find(
            {
                "file_name": {"$regex": r"_redacted(\.[^.]+)?$", "$options": "i"},
                FIELD: {"$in": [None, ""]},
            },
            {"_id": 1, "file_name": 1},
        )
    ]

    return {
        "version": VERSION,
        "documents_total": total,
        "needing_field": missing,
        "already_have_field": total - missing,
        "already_linked": linked,
        "named_redacted": named_redacted,
    }


def apply(db):
    """Give every document the field, and create the index.

    Safe to run repeatedly: a document that already has the field is not
    matched, so a second run writes nothing at all. The filter is on absence
    only, so an entry that already holds a link keeps it.
    """
    report = analyse(db)
    documents = db["documents"]

    filled = documents.update_many(
        {FIELD: {"$exists": False}}, {"$set": {FIELD: None}}
    ).modified_count

    documents.create_index([(FIELD, ASCENDING)], name=INDEX_NAME)

    report["filled"] = filled
    report["index"] = INDEX_NAME
    return report


def report_lines(report):
    """The run's own account of itself, for the runner to print."""
    lines = [
        "documents in log            : %d" % report["documents_total"],
        "already have the field      : %d" % report["already_have_field"],
        "needing the field           : %d" % report["needing_field"],
        "already linked to an original: %d" % report["already_linked"],
    ]

    named = report.get("named_redacted") or []
    if named:
        lines.append("")
        lines.append(
            "   %d entry/entries are named like a redacted copy but hold no "
            "link -- left untouched, a name is not provenance:" % len(named)
        )
        for entry in named:
            lines.append("   document %s  %s" % (entry["id"], entry["file_name"][:48]))

    if "filled" in report:
        lines.append("")
        lines.append("redacted_from_id added      : %d" % report["filled"])
        lines.append("index ensured               : %s" % report["index"])
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
