"""Tender qualification criteria -> candidates, V1.

Read-only and rule-based: each criterion kind is answered from data the
system actually records, and says so plainly where the data needed to decide
is not recorded. Nothing here scores, ranks, guesses or stores anything --
a candidate is a record that passes the rules below, listed with the reasons
it passed and what could not be checked.

What each kind can be answered from today:

  similar_projects  projects holding evidence documents, filtered by
                    completion (recorded status or a Completion Certificate)
                    and by end date where one is recorded. Whether the work
                    is *similar* is never decided: projects have no scope or
                    category recorded.
  project_value     nothing -- project values are not recorded.
  personnel         employees whose designation contains the role, with each
                    required certification reported as held or not.
                    Experience and qualification are not checked.
  certification     documents (not an individual employee's) whose details
                    mention the certification. The system has no record of
                    certifications the company holds, so these are leads to
                    check, not a finding.
  document          documents of the required type, with their owner.
  financial, other  manual only.
"""

import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.collection import Collection

from .routers.projects_mongo import types_by_project

# A drawer, not a report: past this many, the list is not what should be
# read. The total always says how many passed.
MAX_CANDIDATES = 50

COMPLETION_CERTIFICATE = "Completion Certificate"
DAYS_PER_YEAR = 365.2425

MANUAL_MODE = "manual"
MATCHED_MODE = "matched"

VALUE_NOT_RECORDED = "Cannot determine — project value not recorded."


def _normalise(text: Optional[str]) -> str:
    """Case-blind, whitespace-collapsed, "&" read as "and" -- so "Data &
    Analytics Lead" and "data and analytics lead" are the same words."""
    text = (text or "").replace("&", " and ").lower()
    return re.sub(r"\s+", " ", text).strip()


def _iso(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def _years_before(today: date, years: float) -> date:
    return date.fromordinal(today.toordinal() - round(years * DAYS_PER_YEAR))


def _plural(n: Any, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _years_label(years: Any) -> str:
    value = int(years) if float(years).is_integer() else years
    return _plural(value, "year", "years")


def _result(criterion, mode, summary=None, notes=None, candidates=None) -> Dict[str, Any]:
    candidates = candidates or []
    return {
        "criterion_id": criterion["id"],
        "kind": criterion["kind"],
        "mode": mode,
        "summary": summary,
        "notes": notes or [],
        "candidates": candidates[:MAX_CANDIDATES],
        "total": len(candidates),
        "truncated": len(candidates) > MAX_CANDIDATES,
    }


def _oids(ids) -> List[ObjectId]:
    out = []
    for value in ids:
        try:
            out.append(ObjectId(value))
        except (InvalidId, TypeError):
            continue
    return out


# --- projects ---------------------------------------------------------------------


def _similar_projects(criterion, tender, projects: Collection, documents: Collection, today: date):
    params = criterion.get("params") or {}
    wanted = params.get("count") or 1
    description = params.get("work_description")

    notes = [
        (f"Whether a project's work is similar to “{description}” cannot be determined"
         if description else "Whether a project's work is similar cannot be determined")
        + " — projects have no scope or category recorded. Check each one."
    ]
    if params.get("min_value_each"):
        notes.append("Value of each project: " + VALUE_NOT_RECORDED)

    within = params.get("completed_within_years")
    cutoff = _years_before(today, within) if within else None
    cited = set(tender.get("cited_project_ids") or [])

    records = list(
        projects.find({"document_ids.0": {"$exists": True}}).sort([("updated_at", -1), ("_id", -1)])
    )
    held_by_project = types_by_project(documents, records)

    candidates = []
    for record in records:
        held = held_by_project.get(str(record["_id"])) or []
        if not held:
            # document_ids that point at nothing are not evidence.
            continue
        status = (record.get("status") or "").strip()
        recorded_completed = status.lower() == "completed"
        certified = COMPLETION_CERTIFICATE in held

        reasons, missing = [], []
        if params.get("must_be_completed"):
            if not (recorded_completed or certified):
                continue
            if recorded_completed and certified:
                reasons.append("Recorded as Completed, and holds a Completion Certificate")
            elif recorded_completed:
                reasons.append("Recorded as Completed")
            else:
                reasons.append("Holds a Completion Certificate")
        elif status:
            reasons.append(f"Status: {status}")
        reasons.append("Evidence held: " + ", ".join(held))

        if cutoff:
            ended = _iso(record.get("end_date"))
            if ended is None:
                missing.append(f"End date not recorded — cannot check the last {_years_label(within)}")
            elif ended < cutoff:
                continue
            else:
                reasons.append(f"Ended {ended.isoformat()}, within the last {_years_label(within)}")

        project_id = str(record["_id"])
        candidates.append({
            "type": "project",
            "id": project_id,
            "title": record.get("title") or "Untitled project",
            "subtitle": record.get("client_name"),
            "reasons": reasons,
            "missing": missing,
            "cited": project_id in cited,
        })

    summary = (
        f"{_plural(len(candidates), 'project', 'projects')} found with evidence"
        f"{' of completion' if params.get('must_be_completed') else ''}"
        f"; the criterion asks for at least {wanted}."
    )
    return _result(criterion, MATCHED_MODE, summary, notes, candidates)


# --- employees --------------------------------------------------------------------------


def _personnel(criterion, employees: Collection, today: date):
    params = criterion.get("params") or {}
    role = params.get("role") or ""
    wanted_role = _normalise(role)
    required = params.get("required_certifications") or []

    notes = ["Suggestions only — people are not added to the tender."]
    if params.get("min_experience_years") is not None:
        notes.append("Experience is not checked — employees' experience dates are not recorded.")
    if params.get("qualification"):
        notes.append(f"Qualification “{params['qualification']}” is not checked — check each CV.")

    candidates = []
    records = employees.find(
        {"designation": {"$nin": [None, ""]}},
        {"full_name": 1, "designation": 1, "certifications": 1},
    ).sort([("full_name", 1), ("_id", 1)])
    for record in records:
        designation = record.get("designation") or ""
        if not wanted_role or wanted_role not in _normalise(designation):
            continue
        reasons = [f"Designation: {designation}"]
        missing = []

        held = {_normalise(c.get("name")): c for c in record.get("certifications") or [] if c.get("name")}
        for name in required:
            certification = held.get(_normalise(name))
            if certification is None:
                missing.append(f"No {name} certification recorded")
                continue
            expiry = _iso(certification.get("expiry_date"))
            if expiry and expiry < today:
                missing.append(f"{certification['name']} expired on {expiry.isoformat()}")
            else:
                reasons.append(f"Holds {certification['name']}")

        candidates.append({
            "type": "employee",
            "id": str(record["_id"]),
            "title": record.get("full_name") or "Unnamed employee",
            "subtitle": designation,
            "reasons": reasons,
            "missing": missing,
        })

    summary = (
        f"{_plural(len(candidates), 'employee', 'employees')} with a designation matching “{role}”"
        f"; the criterion asks for {params.get('count') or 1}."
    )
    return _result(criterion, MATCHED_MODE, summary, notes, candidates)


# --- documents ------------------------------------------------------------------------------


def _owners(documents_found, projects, employees, tenders) -> Dict[str, Dict[str, str]]:
    """{document id: {type, id, label}} for the owner each document names."""
    wanted = {"project": set(), "employee": set(), "tender": set()}
    for document in documents_found:
        for kind in wanted:
            if document.get(f"{kind}_id"):
                wanted[kind].add(document[f"{kind}_id"])
    labels = {}
    for kind, collection, field in (
        ("project", projects, "title"),
        ("employee", employees, "full_name"),
        ("tender", tenders, "title"),
    ):
        for record in collection.find({"_id": {"$in": _oids(wanted[kind])}}, {field: 1}):
            labels[(kind, str(record["_id"]))] = record.get(field)
    owners = {}
    for document in documents_found:
        for kind in ("project", "employee", "tender"):
            owner_id = document.get(f"{kind}_id")
            if owner_id:
                owners[str(document["_id"])] = {
                    "type": kind,
                    "id": owner_id,
                    "label": labels.get((kind, owner_id)) or f"a {kind} that no longer exists",
                }
                break
    return owners


def _document_candidate(document, owner, reasons, tender) -> Dict[str, Any]:
    document_id = str(document["_id"])
    attached = document.get("tender_id") == tender.get("id") or document_id in (tender.get("document_ids") or [])
    if attached:
        reasons = reasons + ["Already attached to this tender"]
    return {
        "type": "document",
        "id": document_id,
        "title": document.get("project_title") or document.get("file_name") or document.get("document_type") or "Document",
        "subtitle": document.get("document_type"),
        "reasons": reasons,
        "missing": [],
        "attached": attached,
        "file_name": document.get("file_name"),
        "owner_type": owner["type"] if owner else None,
        "owner_id": owner["id"] if owner else None,
        "owner_label": owner["label"] if owner else None,
    }


def _document(criterion, tender, projects, employees, tenders, documents: Collection):
    params = criterion.get("params") or {}
    required_type = params.get("document_type") or ""
    wanted = _normalise(required_type)

    notes = []
    if params.get("validity_note"):
        notes.append(f"Validity (“{params['validity_note']}”) is not checked — documents have no validity dates recorded.")

    found = [
        document
        for document in documents.find({}).sort([("created_at", -1), ("_id", -1)])
        if _normalise(document.get("document_type")) == wanted
    ]
    owners = _owners(found, projects, employees, tenders)
    candidates = []
    for document in found:
        owner = owners.get(str(document["_id"]))
        # Who holds it travels in the owner fields; only its absence is a reason.
        reasons = [f"Type: {document.get('document_type')}"]
        if not owner:
            reasons.append("Not attached to any project, employee or tender")
        candidates.append(_document_candidate(document, owner, reasons, tender))

    needed = params.get("count")
    summary = (
        f"{_plural(len(candidates), 'document', 'documents')} of type “{required_type}”"
        + (f"; the criterion asks for {needed}." if needed else ".")
    )
    return _result(criterion, MATCHED_MODE, summary, notes, candidates)


# The details of a document that might name a certification, and how each is
# described in a reason.
_CERTIFICATION_FIELDS = (
    ("document_type", "type"),
    ("project_title", "title"),
    ("file_name", "file name"),
    ("reference_number", "reference number"),
    ("notes", "notes"),
)


def _certification(criterion, tender, projects, employees, tenders, documents: Collection):
    params = criterion.get("params") or {}
    name = params.get("name") or ""
    wanted = _normalise(name)

    notes = [
        "The system does not record certifications the company holds. These are documents whose "
        f"details mention “{name}” — check each one. Certificates held by individual employees are not included."
    ]

    found, reasons_by_id = [], {}
    # Not an individual employee's: a person's certificate is not the company's.
    for document in documents.find({"employee_id": {"$in": [None, ""]}}).sort([("created_at", -1), ("_id", -1)]):
        where = [label for field, label in _CERTIFICATION_FIELDS if wanted and wanted in _normalise(document.get(field))]
        if where:
            found.append(document)
            reasons_by_id[str(document["_id"])] = [f"Mentions “{name}” in its " + ", ".join(where)]

    owners = _owners(found, projects, employees, tenders)
    candidates = [
        _document_candidate(document, owners.get(str(document["_id"])), reasons_by_id[str(document["_id"])], tender)
        for document in found
    ]
    summary = f"{_plural(len(candidates), 'document', 'documents')} mentioning “{name}”."
    return _result(criterion, MATCHED_MODE, summary, notes, candidates)


# --- entry point -----------------------------------------------------------------------------


def candidates_for(
    criterion: Dict[str, Any],
    tender: Dict[str, Any],
    *,
    projects: Collection,
    employees: Collection,
    documents: Collection,
    tenders: Collection,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """The candidates for one criterion of one tender. Reads only."""
    today = today or datetime.now(timezone.utc).date()
    kind = criterion.get("kind")
    if kind == "similar_projects":
        return _similar_projects(criterion, tender, projects, documents, today)
    if kind == "project_value":
        return _result(criterion, MANUAL_MODE, notes=[VALUE_NOT_RECORDED])
    if kind == "personnel":
        return _personnel(criterion, employees, today)
    if kind == "certification":
        return _certification(criterion, tender, projects, employees, tenders, documents)
    if kind == "document":
        return _document(criterion, tender, projects, employees, tenders, documents)
    if kind == "financial":
        return _result(criterion, MANUAL_MODE, notes=[
            "Checked manually — the system records no financial figures to compare against."
        ])
    return _result(criterion, MANUAL_MODE, notes=["Checked manually — there is nothing recorded to match this against."])
