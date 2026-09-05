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
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pymongo import DESCENDING, ReturnDocument
from pymongo.collection import Collection

from .. import schemas
from ..mongodb import get_documents, get_tender_records

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

    Cost items, certificates and document links are not among them: each
    changes through its own endpoint, so a form submitted from a stale page
    cannot silently drop a payment someone recorded in the meantime.
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
def delete_tender(tender_id: str, tenders: Collection = Depends(get_tender_records)):
    """Delete the tender, embedded cost items and certificates included.

    Uploaded files and attached documents stay in the document log — the
    confirmation dialog says so, and a receipt may still matter to accounts
    long after the bid is closed.
    """
    record = tenders.find_one_and_delete({"_id": _object_id(tender_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Tender not found")
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
# Attached documents — same contract as a project's: attach validates the
# document exists, detach deliberately does not, because deleting an attached
# document detaches it as a second step, by which point it is already gone.
# --------------------------------------------------------------------------


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
    return _serialise(updated)


@router.delete("/{tender_id}/documents/{document_id}", response_model=schemas.TenderOut)
def unlink_document(
    tender_id: str,
    document_id: str,
    tenders: Collection = Depends(get_tender_records),
):
    updated = tenders.find_one_and_update(
        {"_id": _object_id(tender_id)},
        {"$pull": {"document_ids": document_id}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    return _serialise(updated)
