"""0002 — standardise the contract document type on "Contract".

Two documents were imported typed "Contract (Consultant Services)". The
evidence strip, the has-document filter and the type dropdown have always
spelled it "Contract", so those two satisfied nothing: the projects holding
them showed Contract as missing and could never read as fully evidenced, and
the type had no colour of its own because no rule matches its slug.

This renames that one type to "Contract". It is a rename of a field value and
nothing else:

  * no document is created, deleted, merged or re-id'd, and no file is touched
  * no other document type is altered. "Agreement", "Acceptance of
    Tender-cum-Order", "Acceptance of Quotation-cum-Order" and the CV and
    certificate types are left exactly as they are -- whether any of those
    should also fold into an evidence type is a question about the business
    vocabulary, not something to decide inside a rename
  * the project <-> document relationship is not read or written here. A
    document keeps its project_id and stays in the same project's
    document_ids; only what it calls itself changes
  * more than one document of the same type stays valid, so two documents
    both becoming "Contract" on the same project is a correct outcome, not a
    collision
"""

from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId

FROM_TYPE = "Contract (Consultant Services)"
TO_TYPE = "Contract"

VERSION = "0002_standardise_contract_type"
DESCRIPTION = 'Rename document_type "%s" to "%s".' % (FROM_TYPE, TO_TYPE)

# Reported but deliberately untouched, so the run says what it is not doing.
# Anything whose name mentions a contract but is not the one type this
# migration is about.
def _other_contract_like(counts):
    return sorted(
        name
        for name in counts
        if name
        and name not in (FROM_TYPE, TO_TYPE)
        and ("contract" in name.lower() or "agreement" in name.lower())
    )


def analyse(db):
    """Read-only. What the rename would touch, and what it would leave."""
    documents = db["documents"]
    projects = db["projects"]

    counts = {}
    for doc in documents.find({}, {"document_type": 1}):
        name = doc.get("document_type")
        counts[name] = counts.get(name, 0) + 1

    affected = list(
        documents.find({"document_type": FROM_TYPE}, {"_id": 1, "project_id": 1})
    )
    # Which projects gain a Contract mark once this runs -- the visible effect,
    # worth naming so the run can be checked against the screen afterwards.
    gaining = []
    for doc in affected:
        project_id = doc.get("project_id")
        if not project_id:
            continue
        try:
            project = projects.find_one({"_id": ObjectId(project_id)}, {"title": 1})
        except (InvalidId, TypeError):
            project = None
        if project is not None:
            gaining.append({"id": project_id, "title": project.get("title") or ""})

    return {
        "version": VERSION,
        "documents_total": sum(counts.values()),
        "to_rename": len(affected),
        "already_named": counts.get(TO_TYPE, 0),
        "projects_affected": gaining,
        "left_alone": _other_contract_like(counts),
        "distinct_types": len([name for name in counts if name]),
    }


def apply(db):
    """Rename the type. Idempotent -- a second run matches nothing."""
    report = analyse(db)
    result = db["documents"].update_many(
        {"document_type": FROM_TYPE}, {"$set": {"document_type": TO_TYPE}}
    )
    report["renamed"] = result.modified_count
    # Proof the rename did not lose anything: nothing should answer to the old
    # name afterwards, and the new one should have gained exactly that many.
    report["remaining_old"] = db["documents"].count_documents({"document_type": FROM_TYPE})
    report["now_named"] = db["documents"].count_documents({"document_type": TO_TYPE})
    return report


def report_lines(report):
    lines = [
        "documents in log           : %d" % report["documents_total"],
        "distinct document types    : %d" % report["distinct_types"],
        'typed "%s" : %d' % (FROM_TYPE, report["to_rename"]),
        'already typed "%s"   : %d' % (TO_TYPE, report["already_named"]),
    ]
    if report["projects_affected"]:
        lines.append("")
        lines.append("projects that gain a Contract mark:")
        for project in report["projects_affected"]:
            lines.append("   %s  %s" % (project["id"], project["title"][:52]))
    if report["left_alone"]:
        lines.append("")
        lines.append("other contract-like types, deliberately NOT renamed:")
        for name in report["left_alone"]:
            lines.append("   %s" % name)
    if "renamed" in report:
        lines.append("")
        lines.append("renamed                    : %d" % report["renamed"])
        lines.append('still typed "%s" : %d' % (FROM_TYPE, report["remaining_old"]))
        lines.append('now typed "%s"       : %d' % (TO_TYPE, report["now_named"]))
    return lines


def record(db, report):
    """Mark this migration as applied, keeping the first run's numbers."""
    now = datetime.now(timezone.utc)
    existing = db["migrations"].find_one({"_id": VERSION}) or {}
    db["migrations"].replace_one(
        {"_id": VERSION},
        {
            "_id": VERSION,
            "description": DESCRIPTION,
            "first_applied_at": existing.get("first_applied_at") or now,
            "first_renamed": existing.get("first_renamed", report.get("renamed", 0)),
            "last_run_at": now,
            "last_renamed": report.get("renamed", 0),
        },
        upsert=True,
    )
