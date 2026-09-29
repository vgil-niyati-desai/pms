"""0004 - give each tender's attached document an explicit tender_id.

The third and last of the ownership backfills, after 0001 (projects) and
0003 (employees). Until now the only record of which tender a document was
attached to lived in the tender's own `document_ids` array, so answering
"whose is this document" meant scanning every tender, and deleting a
document left its id behind in that array pointing at nothing.

This migration writes the answer onto the document itself, as `tender_id`
holding the tender's existing id. It does not remove `document_ids`: both
representations are kept in step for the transition, and the code that
maintains them writes to both.

Scope is deliberately `tenders.document_ids[]` and nothing else. A cost
item's payment receipt and a certificate's scan also carry a document_id,
but those are attachments of a nested record rather than documents attached
to the tender -- they are not in document_ids, and the tender's Documents
tab does not list them. Claiming them here would assert an ownership the
rest of the system does not.

What it deliberately does NOT do:

  * It does not create, delete, merge or re-id any document, does not touch
    a single stored file, and does not modify any tender. The only field it
    writes is documents.tender_id.
  * It does not assign a tender to a document that two tenders both list.
    Attaching is open to more than one tender, so this is legitimate data
    rather than corruption -- but a single field cannot name two owners, so
    those are reported and left alone rather than resolved by a guess.
  * It does not overwrite a document that already carries a *different*
    tender_id.
  * It does not claim a document that already belongs to a project or an
    employee. Two areas claiming one file is a question about the data, not
    something to settle by writing over one of the answers.
  * It does not touch project_id, employee_id, document_ids, or any
    collection other than `documents` (plus its own record in `migrations`).
"""

from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING

VERSION = "0004_document_tender_id"
DESCRIPTION = "Populate documents.tender_id from tenders.document_ids, and index it."

# The index this migration is responsible for. Named so that ensure_indexes()
# in app/mongodb.py creates the identical one on startup -- create_index is
# idempotent, so whichever runs first makes it and the other is a no-op.
INDEX_NAME = "ix_documents_tender_id"


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
    tenders = db["tenders"]

    existing_ids = {str(d["_id"]) for d in documents.find({}, {"_id": 1})}
    stored = {
        str(d["_id"]): (
            d.get("tender_id"),
            d.get("project_id"),
            d.get("employee_id"),
        )
        for d in documents.find(
            {}, {"_id": 1, "tender_id": 1, "project_id": 1, "employee_id": 1}
        )
    }

    # document id -> every tender listing it
    claims = {}
    total_refs = 0
    for tender in tenders.find({}, {"_id": 1, "title": 1, "document_ids": 1}):
        for document_id in tender.get("document_ids") or []:
            total_refs += 1
            claims.setdefault(document_id, []).append(
                {"id": str(tender["_id"]), "title": tender.get("title") or ""}
            )

    clean, multi, dangling = {}, {}, {}
    for document_id, owners in claims.items():
        if document_id not in existing_ids:
            dangling[document_id] = owners
        elif len({owner["id"] for owner in owners}) > 1:
            multi[document_id] = owners
        else:
            clean[document_id] = owners[0]["id"]

    conflicting = {
        document_id: {
            "stored": stored.get(document_id, (None, None, None))[0],
            "referenced_by": tender_id,
        }
        for document_id, tender_id in clean.items()
        if stored.get(document_id, (None, None, None))[0] not in (None, "", tender_id)
    }

    # Already another area's. Reported under whichever field holds it, so the
    # run says what the other claim actually is.
    owned_elsewhere = {}
    for document_id, tender_id in clean.items():
        _, project_id, employee_id = stored.get(document_id, (None, None, None))
        if project_id or employee_id:
            owned_elsewhere[document_id] = {
                "project_id": project_id,
                "employee_id": employee_id,
                "referenced_by": tender_id,
            }

    skipped = set(conflicting) | set(owned_elsewhere)

    to_write = {
        document_id: tender_id
        for document_id, tender_id in clean.items()
        if document_id not in skipped
        and stored.get(document_id, (None, None, None))[0] != tender_id
    }

    return {
        "version": VERSION,
        "documents_total": len(existing_ids),
        "tenders_total": tenders.count_documents({}),
        "reference_entries": total_refs,
        "documents_referenced": len(claims),
        "mapped_cleanly": len(clean),
        "needing_write": len(to_write),
        "already_correct": len(clean) - len(to_write) - len(skipped),
        "multi_tender": multi,
        "dangling": dangling,
        "conflicting": conflicting,
        "owned_elsewhere": owned_elsewhere,
        "unreferenced_documents": len(existing_ids) - len(clean) - len(multi),
        "_to_write": to_write,
    }


def apply(db):
    """Write tender_id for every cleanly-mapped document, and index it.

    Safe to run repeatedly: a document already holding the right tender_id is
    not in `_to_write`, so a second run writes nothing at all.
    """
    report = analyse(db)
    documents = db["documents"]

    written = 0
    for document_id, tender_id in report["_to_write"].items():
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
                    {"tender_id": None},
                    {"tender_id": {"$exists": False}},
                    {"tender_id": tender_id},
                ],
            },
            {"$set": {"tender_id": tender_id}},
        )
        written += result.modified_count

    # Every document that is no tender's gets the field too, set to None, so
    # "has no tender" is recorded rather than merely absent -- the two read
    # the same in a query, but only one of them says the question was asked.
    filled_null = documents.update_many(
        {"tender_id": {"$exists": False}}, {"$set": {"tender_id": None}}
    ).modified_count

    documents.create_index([("tender_id", ASCENDING)], name=INDEX_NAME)

    report["written"] = written
    report["skipped"] = len(report["conflicting"]) + len(report["owned_elsewhere"])
    report["null_filled"] = filled_null
    report["index"] = INDEX_NAME
    report.pop("_to_write", None)
    return report


def report_lines(report):
    """The run's own account of itself, for the runner to print.

    The refusals are as much the report as the writes are: a document two
    tenders list is named here rather than folded into a count, because it is
    the only place a person will be told to go and settle it.
    """
    lines = [
        "documents in log          : %d" % report["documents_total"],
        "tenders                   : %d" % report["tenders_total"],
        "reference entries         : %d" % report["reference_entries"],
        "documents referenced      : %d" % report["documents_referenced"],
        "map cleanly to one tender : %d" % report["mapped_cleanly"],
        "  already correct         : %d" % report["already_correct"],
        "  needing tender_id       : %d" % report["needing_write"],
        "documents with no tender  : %d" % report["unreferenced_documents"],
    ]

    for label, key in (
        ("LISTED BY MULTIPLE TENDERS", "multi_tender"),
        ("DANGLING (a tender points at a missing document)", "dangling"),
    ):
        entries = report.get(key) or {}
        if entries:
            lines.append("")
            lines.append("!! %d %s -- left untouched:" % (len(entries), label))
            for document_id, owners in entries.items():
                lines.append("   document %s" % document_id)
                for owner in owners:
                    lines.append(
                        "       tender %s  %s" % (owner["id"], owner["title"][:48])
                    )

    conflicting = report.get("conflicting") or {}
    if conflicting:
        lines.append("")
        lines.append(
            "!! %d document(s) already hold a DIFFERENT tender_id "
            "-- left untouched:" % len(conflicting)
        )
        for document_id, detail in conflicting.items():
            lines.append(
                "   document %s: stored=%s referenced by=%s"
                % (document_id, detail["stored"], detail["referenced_by"])
            )

    owned_elsewhere = report.get("owned_elsewhere") or {}
    if owned_elsewhere:
        lines.append("")
        lines.append(
            "!! %d document(s) a tender lists are ALREADY a project's or an "
            "employee's -- left untouched:" % len(owned_elsewhere)
        )
        for document_id, detail in owned_elsewhere.items():
            lines.append(
                "   document %s: project=%s employee=%s listed by tender=%s"
                % (
                    document_id,
                    detail["project_id"],
                    detail["employee_id"],
                    detail["referenced_by"],
                )
            )

    if "written" in report:
        lines.append("")
        lines.append("tender_id written         : %d" % report["written"])
        lines.append("tender_id set to null     : %d" % report["null_filled"])
        lines.append("skipped (needs a decision): %d" % report["skipped"])
        lines.append("index ensured             : %s" % report["index"])
    return lines


def record(db, report):
    """Mark this migration as applied, with what it did.

    A re-run (--force) records its own numbers, but the first application's
    are kept beside them -- otherwise re-running overwrites "wrote 7" with
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
