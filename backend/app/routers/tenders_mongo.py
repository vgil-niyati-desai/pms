"""Tender endpoints backed by MongoDB.

The third area to move off its browser-local store, built to the same
pattern as routers/projects_mongo.py and routers/employees_mongo.py — and a
hybrid of the two shapes they established:

  * Like an employee, a tender embeds its nested records: the cost items
    paid to pursue it (EMD, fees) and the certificates submitted with it,
    each with its own id and sub-endpoints.
  * Like a project, it references attached documents by id, because the
    files live in the shared document log and must outlive the tender.

The list endpoint does everything the browser store did in JavaScript —
search, the status/authority/tag filters, the deadline window, the value
range, the open-only view, sort, and paging — because a screen that receives
one page cannot reorder what it was not sent.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pymongo import DESCENDING, ReturnDocument
from pymongo.collection import Collection

from .. import matching, schemas
from ..mongodb import (
    get_documents,
    get_employee_records,
    get_project_records,
    get_tender_records,
)
from .documents_mongo import serialise_document
from .projects_mongo import serialise_project, types_by_project

router = APIRouter(prefix="/tenders", tags=["tenders"])

# The statuses the "Open" view shows: tenders still being worked on. Must
# match OPEN_STATUSES in the frontend's api/tenders.js.
OPEN_STATUSES = ["Identified", "Preparing"]

SORTABLE = {
    "title",
    "issuing_authority",
    "reference_number",
    "tender_type",
    "estimated_value",
    "published_date",
    "submission_deadline",
    "status",
    "created_at",
    "updated_at",
}
DEFAULT_SORT = "-updated_at"
DATE_FIELDS = {"created_at", "updated_at"}

SEARCH_FIELDS = ["title", "reference_number", "issuing_authority"]

MAX_PAGE_SIZE = 200


def _now_utc_ms() -> datetime:
    """Current UTC time at BSON's precision (milliseconds), so a create
    response matches every later read."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _object_id(tender_id: str) -> ObjectId:
    """Parse a path id, or 404 — a malformed id gets the same answer as a
    well-formed one that isn't there."""
    try:
        return ObjectId(tender_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Tender not found")


def _serialise(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Mongo document -> the shape schemas.TenderOut expects."""
    out = {key: value for key, value in doc.items() if not key.startswith("_")}
    out["id"] = str(doc["_id"])
    out.setdefault("tags", [])
    out.setdefault("cost_items", [])
    out.setdefault("certificates", [])
    out.setdefault("document_ids", [])
    out.setdefault("cited_project_ids", [])
    # Tenders saved before criteria existed have no such field; they read as
    # having none recorded, with no migration needed.
    out.setdefault("criteria", [])
    return out


def _exact_ci(value: str) -> re.Pattern:
    """Anchored case-insensitive match for one whole tag, escaped."""
    return re.compile(f"^{re.escape(value)}$", re.IGNORECASE)


def _number_key(field: str) -> Dict[str, Any]:
    """A string money field as a double, or null where it doesn't parse —
    commas and spaces stripped first, as everywhere else."""
    digits = {
        "$replaceAll": {
            "input": {"$toString": {"$ifNull": [f"${field}", ""]}},
            "find": ",",
            "replacement": "",
        }
    }
    digits = {"$replaceAll": {"input": digits, "find": " ", "replacement": ""}}
    return {"$convert": {"input": digits, "to": "double", "onError": None, "onNull": None}}


def _parse_amount(value: Optional[str]) -> Optional[float]:
    """A querystring bound as a number, or None when absent or unusable."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _sort_stages(sort: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The (stages before the sort, sort spec) for one sort option — the
    same three rules as the other two list endpoints: blanks last in both
    directions, estimated_value compared as a number with unparseable values
    after the real ones, text compared case-insensitively, `_id` breaking
    every tie so paging never repeats or skips a row."""
    descending = sort.startswith("-")
    key = sort[1:] if descending else sort
    if key not in SORTABLE:
        key, descending = "updated_at", True
    direction = -1 if descending else 1

    if key in DATE_FIELDS:
        return [], {key: direction, "_id": DESCENDING}

    value = {"$ifNull": [f"${key}", ""]}
    stages: List[Dict[str, Any]] = [
        {
            "$addFields": {
                "_blank": {"$cond": [{"$eq": [value, ""]}, 1, 0]},
                "_text": {"$toLower": {"$toString": value}},
            }
        }
    ]
    spec: Dict[str, int] = {"_blank": 1}

    if key == "estimated_value":
        stages.append({"$addFields": {"_number": _number_key(key)}})
        # Separate stage: fields added in one $addFields cannot see each other.
        stages.append(
            {"$addFields": {"_unparsed": {"$cond": [{"$eq": ["$_number", None]}, 1, 0]}}}
        )
        spec["_unparsed"] = 1
        spec["_number"] = direction

    spec["_text"] = direction
    spec["_id"] = DESCENDING
    return stages, spec


# --------------------------------------------------------------------------
# Vocabulary endpoints — declared before /{tender_id}, which would otherwise
# swallow their path segment.
# --------------------------------------------------------------------------


@router.get("/authorities", response_model=List[str])
def list_authorities(tenders: Collection = Depends(get_tender_records)):
    """Distinct issuing authorities, for the list screen's dropdown."""
    return sorted((a for a in tenders.distinct("issuing_authority") if a), key=str.lower)


@router.get("/tags", response_model=List[str])
def list_tags(tenders: Collection = Depends(get_tender_records)):
    """The tag vocabulary, built from the tags already in use."""
    return sorted((t for t in tenders.distinct("tags") if t), key=str.lower)


# --------------------------------------------------------------------------
# Tenders
# --------------------------------------------------------------------------


@router.get("/", response_model=schemas.TenderPage)
def list_tenders(
    q: Optional[str] = None,
    statuses: List[str] = Query(default=[]),
    authority: Optional[str] = None,
    tags: List[str] = Query(default=[]),
    tag_mode: str = "any",
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    min_value: Optional[str] = None,
    max_value: Optional[str] = None,
    open_only: bool = False,
    sort: str = DEFAULT_SORT,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=MAX_PAGE_SIZE),
    tenders: Collection = Depends(get_tender_records),
):
    """One page of tenders, filtered and sorted."""
    query: Dict[str, Any] = {}
    clauses: List[Dict[str, Any]] = []

    if q and q.strip():
        pattern = re.compile(re.escape(q.strip()), re.IGNORECASE)
        clauses.append({"$or": [{field: pattern} for field in SEARCH_FIELDS]})

    if open_only:
        clauses.append({"status": {"$in": OPEN_STATUSES}})
    if statuses:
        clauses.append({"status": {"$in": statuses}})
    if authority:
        query["issuing_authority"] = authority

    if tags:
        patterns = [_exact_ci(tag) for tag in tags]
        if tag_mode == "all":
            clauses.extend({"tags": pattern} for pattern in patterns)
        else:
            clauses.append({"tags": {"$in": patterns}})

    window: Dict[str, Any] = {}
    if deadline_from and deadline_from.strip():
        window["$gte"] = deadline_from.strip()
    if deadline_to and deadline_to.strip():
        window["$lte"] = deadline_to.strip()
        # An empty string is "less than" any date, so a to-only window would
        # sweep in every tender with no deadline recorded. It mustn't: no
        # deadline cannot fall inside a window. $gt "" shuts that door (a
        # missing or null field already fails a string range on its own).
        window.setdefault("$gt", "")
    if window:
        query["submission_deadline"] = window

    if clauses:
        query["$and"] = clauses

    pipeline: List[Dict[str, Any]] = [{"$match": query}]

    minimum = _parse_amount(min_value)
    maximum = _parse_amount(max_value)
    if minimum is not None or maximum is not None:
        # Convert-then-match, like the experience range on employees: a value
        # that doesn't parse becomes null, and null never satisfies a numeric
        # range — a tender with no estimated value cannot satisfy one.
        bounds: Dict[str, Any] = {}
        if minimum is not None:
            bounds["$gte"] = minimum
        if maximum is not None:
            bounds["$lte"] = maximum
        pipeline.append({"$addFields": {"_value": _number_key("estimated_value")}})
        pipeline.append({"$match": {"_value": bounds}})

    totals = list(tenders.aggregate(pipeline + [{"$count": "n"}]))
    total = totals[0]["n"] if totals else 0

    key_stages, sort_spec = _sort_stages(sort)
    pipeline.extend(key_stages)
    pipeline.append({"$sort": sort_spec})
    pipeline.append({"$skip": (page - 1) * page_size})
    pipeline.append({"$limit": page_size})

    items = [_serialise(doc) for doc in tenders.aggregate(pipeline)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{tender_id}", response_model=schemas.TenderOut)
def get_tender(tender_id: str, tenders: Collection = Depends(get_tender_records)):
    record = tenders.find_one({"_id": _object_id(tender_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Tender not found")
    return _serialise(record)


@router.post("/", response_model=schemas.TenderOut, status_code=201)
def create_tender(
    payload: schemas.TenderIn,
    tenders: Collection = Depends(get_tender_records),
):
    now = _now_utc_ms()
    record = payload.model_dump()
    record["cost_items"] = []
    record["certificates"] = []
    record["document_ids"] = []
    record["cited_project_ids"] = []
    record["criteria"] = []
    record["created_at"] = now
    record["updated_at"] = now

    result = tenders.insert_one(record)
    record["_id"] = result.inserted_id
    return _serialise(record)


@router.put("/{tender_id}", response_model=schemas.TenderOut)
def update_tender(
    tender_id: str,
    payload: schemas.TenderIn,
    tenders: Collection = Depends(get_tender_records),
):
    """Replace the editable fields.

    Cost items, certificates, criteria and document links are not among
    them: each changes through its own endpoint, so a form submitted from a
    stale page cannot silently drop a payment someone recorded in the
    meantime.
    """
    changes = payload.model_dump()
    changes["updated_at"] = _now_utc_ms()

    updated = tenders.find_one_and_update(
        {"_id": _object_id(tender_id)},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    return _serialise(updated)


@router.delete("/{tender_id}", status_code=204)
def delete_tender(
    tender_id: str,
    tenders: Collection = Depends(get_tender_records),
    documents: Collection = Depends(get_documents),
):
    """Delete the tender, embedded cost items, certificates and criteria
    included.

    Uploaded files and attached documents stay in the document log — the
    confirmation dialog says so, and a receipt may still matter to accounts
    long after the bid is closed.

    What does not stay is the claim on them. A document still naming this
    tender would point at a record that cannot be opened, which is what
    deleting a project clears from its documents and deleting an employee
    clears from theirs.
    """
    record = tenders.find_one_and_delete({"_id": _object_id(tender_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Tender not found")

    documents.update_many({"tender_id": tender_id}, {"$set": {"tender_id": None}})
    return Response(status_code=204)


# --------------------------------------------------------------------------
# Nested records: cost items and certificates. Same mechanics as an
# employee's CVs — positional $set for edits, existence required on delete,
# every write bumping the tender's updated_at.
# --------------------------------------------------------------------------


def _add_nested(
    tenders: Collection, tender_id: str, field: str, values: Dict[str, Any]
) -> Dict[str, Any]:
    item = dict(values)
    item["id"] = str(ObjectId())
    item["created_at"] = _now_utc_ms()

    updated = tenders.find_one_and_update(
        {"_id": _object_id(tender_id)},
        {"$push": {field: item}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    return _serialise(updated)


def _update_nested(
    tenders: Collection, tender_id: str, field: str, item_id: str, values: Dict[str, Any]
) -> Dict[str, Any]:
    # Positional $ writes into the element the query matched, field by field,
    # preserving the item's id and created_at.
    changes = {f"{field}.$.{key}": value for key, value in values.items()}
    changes["updated_at"] = _now_utc_ms()

    updated = tenders.find_one_and_update(
        {"_id": _object_id(tender_id), f"{field}.id": item_id},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return _serialise(updated)


def _remove_nested(
    tenders: Collection, tender_id: str, field: str, item_id: str
) -> Dict[str, Any]:
    updated = tenders.find_one_and_update(
        {"_id": _object_id(tender_id), f"{field}.id": item_id},
        {"$pull": {field: {"id": item_id}}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return _serialise(updated)


@router.post("/{tender_id}/cost-items", response_model=schemas.TenderOut, status_code=201)
def add_cost_item(
    tender_id: str,
    payload: schemas.CostItemIn,
    tenders: Collection = Depends(get_tender_records),
):
    return _add_nested(tenders, tender_id, "cost_items", payload.model_dump())


@router.put("/{tender_id}/cost-items/{cost_item_id}", response_model=schemas.TenderOut)
def update_cost_item(
    tender_id: str,
    cost_item_id: str,
    payload: schemas.CostItemIn,
    tenders: Collection = Depends(get_tender_records),
):
    return _update_nested(tenders, tender_id, "cost_items", cost_item_id, payload.model_dump())


@router.delete("/{tender_id}/cost-items/{cost_item_id}", response_model=schemas.TenderOut)
def delete_cost_item(
    tender_id: str,
    cost_item_id: str,
    tenders: Collection = Depends(get_tender_records),
):
    """Remove one cost item. Its receipt is the caller's to delete through
    the documents endpoint first, which is what the drawer does."""
    return _remove_nested(tenders, tender_id, "cost_items", cost_item_id)


@router.post("/{tender_id}/certificates", response_model=schemas.TenderOut, status_code=201)
def add_certificate(
    tender_id: str,
    payload: schemas.TenderCertificateIn,
    tenders: Collection = Depends(get_tender_records),
):
    return _add_nested(tenders, tender_id, "certificates", payload.model_dump())


@router.put("/{tender_id}/certificates/{certificate_id}", response_model=schemas.TenderOut)
def update_certificate(
    tender_id: str,
    certificate_id: str,
    payload: schemas.TenderCertificateIn,
    tenders: Collection = Depends(get_tender_records),
):
    return _update_nested(tenders, tender_id, "certificates", certificate_id, payload.model_dump())


@router.delete("/{tender_id}/certificates/{certificate_id}", response_model=schemas.TenderOut)
def delete_certificate(
    tender_id: str,
    certificate_id: str,
    tenders: Collection = Depends(get_tender_records),
):
    return _remove_nested(tenders, tender_id, "certificates", certificate_id)


# --------------------------------------------------------------------------
# Qualification criteria — embedded like cost items, but each carries its own
# updated_at, and a criterion's kind is fixed once added: its params were
# validated for that kind, so a different kind is a different criterion.
# --------------------------------------------------------------------------


def _validated_criterion(body: Any) -> Any:
    """The body as a validated criterion, or a 422 in FastAPI's usual shape
    whose every message starts with the label of the field it is about."""
    try:
        return schemas.validate_criterion(body)
    except schemas.CriterionValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors)


def _criterion_values(payload: Any, now: datetime) -> Dict[str, Any]:
    """The stored form of a validated criterion. `category` is derived from
    the kind here, never taken from the client."""
    values = payload.model_dump()
    values["category"] = schemas.CRITERION_CATEGORIES[values["kind"]]
    values["updated_at"] = now
    return values


@router.post("/{tender_id}/criteria", response_model=schemas.TenderOut, status_code=201)
def add_criterion(
    tender_id: str,
    body: Any = Body(...),
    tenders: Collection = Depends(get_tender_records),
):
    """Record one qualification criterion.

    The cap is enforced inside the same write: the update only matches while
    the list has room, so two requests racing for the last slot cannot both
    land.
    """
    oid = _object_id(tender_id)
    payload = _validated_criterion(body)
    now = _now_utc_ms()
    item = _criterion_values(payload, now)
    item["id"] = str(ObjectId())
    item["created_at"] = now

    updated = tenders.find_one_and_update(
        {"_id": oid, f"criteria.{schemas.MAX_CRITERIA - 1}": {"$exists": False}},
        {"$push": {"criteria": item}, "$set": {"updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        if tenders.count_documents({"_id": oid}, limit=1) == 0:
            raise HTTPException(status_code=404, detail="Tender not found")
        raise HTTPException(
            status_code=409,
            detail=f"A tender can hold at most {schemas.MAX_CRITERIA} criteria.",
        )
    return _serialise(updated)


@router.put("/{tender_id}/criteria/{criterion_id}", response_model=schemas.TenderOut)
def update_criterion(
    tender_id: str,
    criterion_id: str,
    body: Any = Body(...),
    tenders: Collection = Depends(get_tender_records),
):
    """Replace one criterion's contents, keeping its id and created_at."""
    oid = _object_id(tender_id)
    payload = _validated_criterion(body)
    record = tenders.find_one({"_id": oid}, {"criteria": 1})
    if record is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    criteria = record.get("criteria") or []
    index = next(
        (i for i, item in enumerate(criteria) if item.get("id") == criterion_id), None
    )
    if index is None:
        raise HTTPException(status_code=404, detail="Criterion not found")
    current = criteria[index]
    if current.get("kind") != payload.kind:
        raise HTTPException(
            status_code=400,
            detail="A criterion's type cannot be changed. Delete it and add a new one.",
        )

    # Written by position, with the filter re-checking that the element at
    # that position is still this criterion: if another request removed or
    # moved it since the read, nothing matches and the answer is a 404 rather
    # than an edit landing on its neighbour.
    now = _now_utc_ms()
    prefix = f"criteria.{index}"
    changes = {f"{prefix}.{key}": value for key, value in _criterion_values(payload, now).items()}
    changes["updated_at"] = now
    updated = tenders.find_one_and_update(
        {"_id": oid, f"{prefix}.id": criterion_id},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Criterion not found")
    return _serialise(updated)


@router.delete("/{tender_id}/criteria/{criterion_id}", response_model=schemas.TenderOut)
def delete_criterion(
    tender_id: str,
    criterion_id: str,
    tenders: Collection = Depends(get_tender_records),
):
    return _remove_nested(tenders, tender_id, "criteria", criterion_id)


@router.get(
    "/{tender_id}/criteria/{criterion_id}/candidates",
    response_model=schemas.CandidateResults,
)
def criterion_candidates(
    tender_id: str,
    criterion_id: str,
    tenders: Collection = Depends(get_tender_records),
    projects: Collection = Depends(get_project_records),
    employees: Collection = Depends(get_employee_records),
    documents: Collection = Depends(get_documents),
):
    """The projects, employees or documents that meet one criterion, by the
    rules in app/matching.py. Read-only: nothing is written, and nothing
    about the result is stored."""
    record = tenders.find_one({"_id": _object_id(tender_id)})
    if record is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    tender = _serialise(record)
    criterion = next((c for c in tender["criteria"] if c.get("id") == criterion_id), None)
    if criterion is None:
        raise HTTPException(status_code=404, detail="Criterion not found")
    return matching.candidates_for(
        criterion,
        tender,
        projects=projects,
        employees=employees,
        documents=documents,
        tenders=tenders,
    )


# --------------------------------------------------------------------------
# Attached documents — same contract as a project's: attach validates the
# document exists, detach deliberately does not, because deleting an attached
# document detaches it as a second step, by which point it is already gone.
# --------------------------------------------------------------------------


def _document_oids(document_ids: List[str]) -> List[ObjectId]:
    """The ids that are parseable, as ObjectIds.

    An unparseable entry is skipped rather than raising: it can only be junk
    left by something outside this API, and one bad string must not make a
    tender unreadable.
    """
    oids = []
    for document_id in document_ids or []:
        try:
            oids.append(ObjectId(document_id))
        except (InvalidId, TypeError):
            continue
    return oids


@router.get("/{tender_id}/documents", response_model=List[schemas.DocumentMongoOut])
def list_tender_documents(
    tender_id: str,
    tenders: Collection = Depends(get_tender_records),
    documents: Collection = Depends(get_documents),
):
    """The documents attached to one tender, newest first.

    This is what the Documents tab reads. It used to fetch the whole document
    log and filter it in the browser, which meant every tender screen paid
    for every CV, project evidence file and unattached entry in the system.

    Both representations are accepted, as a project's own endpoint accepts
    both of its: a document counts as this tender's if it carries the
    tender_id *or* if the tender lists it. Attaching is deliberately open to
    more than one tender while tender_id can only name one, so the list alone
    is the only thing that knows about the second tender's attachment -- and
    a document the migration left unassigned, one two tenders both list, is
    known by nothing else at all. Reading only tender_id would drop both from
    a tab that shows them today.

    Ordering matches the document log's own: newest first, with _id breaking
    ties. The tab groups by type on top of that, so documents of the same
    type keep their newest-first order inside their group.
    """
    record = tenders.find_one({"_id": _object_id(tender_id)}, {"document_ids": 1})
    if not record:
        raise HTTPException(status_code=404, detail="Tender not found")

    query = {
        "$or": [
            {"tender_id": tender_id},
            {"_id": {"$in": _document_oids(record.get("document_ids") or [])}},
        ]
    }
    cursor = documents.find(query).sort([("created_at", DESCENDING), ("_id", DESCENDING)])
    return [serialise_document(doc) for doc in cursor]


# --------------------------------------------------------------------------
# Cited projects
#
# The past work a bid puts forward as its experience. A tender points at
# projects and does not own them: a project is evidence in its own right,
# outlives the bid, and may be cited by any number of bids at once -- so
# nothing here writes to the project, and a citation is not a claim.
#
# It is emphatically NOT the project a won tender produced. Citing carries no
# outcome, no assignment and no status, and it does not reach the project's
# documents: a tender's own Documents tab and the projects it cites are two
# separate lists, and `projects.document_ids` and `documents.project_id` are
# neither read nor written here.
# --------------------------------------------------------------------------

# Ids are stored as strings on both sides; this is the same parse the
# document list needs, under the name that reads correctly here.
_project_oids = _document_oids


@router.get("/{tender_id}/projects", response_model=List[schemas.ProjectOut])
def list_tender_projects(
    tender_id: str,
    tenders: Collection = Depends(get_tender_records),
    projects: Collection = Depends(get_project_records),
    documents: Collection = Depends(get_documents),
):
    """The projects this tender cites, most recently updated first.

    Resolved here rather than in the browser, the way a tender's documents
    are: the evidence tab receives the projects it needs instead of the whole
    project list to filter down.

    Each row is an ordinary project, carrying the `document_types` the
    projects list already resolves, so the evidence strip reads the same on
    this tab as it does on the Projects screen. Reading those types touches
    the document log but writes nothing to it -- citing a project does not
    give the tender its documents.

    A citation of a project that has since been deleted simply matches
    nothing; deleting a project clears the citation, so this is only ever the
    projects that are really there.
    """
    record = tenders.find_one({"_id": _object_id(tender_id)}, {"cited_project_ids": 1})
    if not record:
        raise HTTPException(status_code=404, detail="Tender not found")

    oids = _project_oids(record.get("cited_project_ids") or [])
    cursor = projects.find({"_id": {"$in": oids}}).sort(
        [("updated_at", DESCENDING), ("_id", DESCENDING)]
    )
    records = list(cursor)
    types = types_by_project(documents, records)
    return [
        serialise_project(project, types.get(str(project["_id"]))) for project in records
    ]


@router.post("/{tender_id}/projects/{project_id}", response_model=schemas.TenderOut)
def cite_project(
    tender_id: str,
    project_id: str,
    tenders: Collection = Depends(get_tender_records),
    projects: Collection = Depends(get_project_records),
):
    """Cite an existing project as evidence for this tender.

    Idempotent: $addToSet rather than $push, so citing the same project twice
    is a no-op instead of a duplicate entry.

    Unlike attaching a document, this is not exclusive and never could be.
    The whole point of past evidence is that the same completed project is
    put forward for bid after bid, so a project already cited elsewhere is
    cited here as well, and neither citation is disturbed.

    The project is checked before anything is written, so citing one that is
    not there changes nothing.
    """
    oid = _object_id(tender_id)
    try:
        project_oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Project not found")
    if not projects.find_one({"_id": project_oid}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Project not found")

    updated = tenders.find_one_and_update(
        {"_id": oid},
        {
            "$addToSet": {"cited_project_ids": project_id},
            "$set": {"updated_at": _now_utc_ms()},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    return _serialise(updated)


@router.delete("/{tender_id}/projects/{project_id}", response_model=schemas.TenderOut)
def uncite_project(
    tender_id: str,
    project_id: str,
    tenders: Collection = Depends(get_tender_records),
):
    """Stop citing a project. The project itself is untouched.

    The project is deliberately not looked up first, on the same reasoning as
    detaching a document: deleting a project clears the citation as a second
    step, and a citation left pointing at a project that is gone is exactly
    what this call exists to clear.
    """
    updated = tenders.find_one_and_update(
        {"_id": _object_id(tender_id)},
        {
            "$pull": {"cited_project_ids": project_id},
            "$set": {"updated_at": _now_utc_ms()},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    return _serialise(updated)


@router.post("/{tender_id}/documents/{document_id}", response_model=schemas.TenderOut)
def link_document(
    tender_id: str,
    document_id: str,
    tenders: Collection = Depends(get_tender_records),
    documents: Collection = Depends(get_documents),
):
    oid = _object_id(tender_id)
    try:
        document_oid = ObjectId(document_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Document not found")
    if not documents.find_one({"_id": document_oid}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Document not found")

    updated = tenders.find_one_and_update(
        {"_id": oid},
        {"$addToSet": {"document_ids": document_id}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Tender not found")

    # The other half of the link. Written only where the document is free or
    # already this tender's: attaching is deliberately still open to more
    # than one tender (unlike a project's, which refuses), and a second
    # tender taking the pointer off the first would make it say something
    # untrue about a tender that is still showing the document. First claim
    # keeps it; the later tender still lists the document in its own
    # document_ids, which is what the screens read.
    #
    # Nor is it written on a document a project or an employee holds. Linking
    # an existing project evidence document or a person's certificate to a
    # bid puts it forward; it does not make it the tender's. The tender lists
    # it through document_ids, and the document keeps its one owner -- the
    # rule migration 0004 applied to the data already there.
    documents.update_one(
        {
            "_id": document_oid,
            "$and": [
                {"$or": [
                    {"tender_id": None},
                    {"tender_id": {"$exists": False}},
                    {"tender_id": tender_id},
                ]},
                {"$or": [{"project_id": None}, {"project_id": {"$exists": False}}]},
                {"$or": [{"employee_id": None}, {"employee_id": {"$exists": False}}]},
            ],
        },
        {"$set": {"tender_id": tender_id}},
    )
    return _serialise(updated)


@router.delete("/{tender_id}/documents/{document_id}", response_model=schemas.TenderOut)
def unlink_document(
    tender_id: str,
    document_id: str,
    tenders: Collection = Depends(get_tender_records),
    documents: Collection = Depends(get_documents),
):
    """Detach a document from this tender. The document itself is kept.

    The document is deliberately not looked up first, for the reason given
    above: deleting an attached document detaches it as a second step, by
    which point it is already gone, and a link left pointing at a deleted
    document is exactly what this call exists to clear.

    tender_id is cleared only where it names *this* tender. A document
    another tender claimed is not this call's to unclaim, and one that is
    already gone simply matches nothing.
    """
    updated = tenders.find_one_and_update(
        {"_id": _object_id(tender_id)},
        {"$pull": {"document_ids": document_id}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Tender not found")

    try:
        document_oid = ObjectId(document_id)
    except (InvalidId, TypeError):
        document_oid = None
    if document_oid is not None:
        documents.update_one(
            {"_id": document_oid, "tender_id": tender_id},
            {"$set": {"tender_id": None}},
        )
    return _serialise(updated)
