"""Company documents: certificates, registrations and financial statements
that belong to the company itself.

They are rows of the shared document log (the `documents` collection) marked
with scope="company", so storage, hashing, duplicate refusal, preview,
download (GET /documents/{id}/file) and delete (DELETE /documents/{id}) are
the log's own and are not repeated here. This router adds only what is
specific to them: a list that returns only company rows, the descriptive
fields (title, issuer, certificate number, issue/expiry dates), and a status
computed from the expiry date on every read.

Permissions follow the document log: Admin and HOD may list, read, add and
edit; replacing the stored file is Admin-only, as it is there.
"""

import re
from typing import Any, Dict, List, Optional

from bson.errors import InvalidId
from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pymongo import ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from .. import auth, schemas, storage
from ..mongodb import get_documents
from . import documents_mongo as log

router = APIRouter(
    prefix="/company-documents",
    tags=["company-documents"],
    dependencies=[Depends(auth.require_roles("Admin", "HOD"))],
)

SORTABLE = {"created_at", "title", "category", "document_type", "issue_date", "expiry_date"}
DEFAULT_SORT = "-created_at"
SCOPE_FILTER = {"scope": schemas.COMPANY_SCOPE}


def _oid(document_id: str) -> ObjectId:
    try:
        return ObjectId(document_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Company document not found")


def _serialise(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: doc.get(key) for key in schemas.CompanyDocumentFields.model_fields}
    out["id"] = str(doc["_id"])
    out["status"] = schemas.company_status(doc.get("expiry_date"))
    out["has_file"] = bool(doc.get("stored_file_name"))
    return out


def _clean(value: Optional[str], limit: int = 300) -> Optional[str]:
    if value is None:
        return None
    value = " ".join(value.split())
    return value[:limit] or None


def _clean_notes(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value[:2000] or None


def _check_dates(issue_date: Optional[str], expiry_date: Optional[str]) -> None:
    try:
        schemas.check_iso_date(issue_date, "Issue date")
        schemas.check_iso_date(expiry_date, "Expiry date")
        schemas._check_order(
            issue_date or None, expiry_date or None, "Expiry date cannot be before the issue date."
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=[{"type": "value_error", "loc": ["body", "dates"], "msg": str(exc)}],
        )


def _status_query(status: str) -> Dict[str, Any]:
    """The stored-field query that selects one computed status."""
    today = schemas.datetime.utcnow().date()
    from datetime import timedelta

    soon = (today + timedelta(days=schemas.EXPIRING_SOON_DAYS)).isoformat()
    today_s = today.isoformat()
    has = {"$type": "string", "$ne": ""}
    if status == "unknown":
        return {"$or": [{"expiry_date": None}, {"expiry_date": ""}, {"expiry_date": {"$exists": False}}]}
    if status == "expired":
        return {"expiry_date": {**has, "$lt": today_s}}
    if status == "expiring_soon":
        return {"expiry_date": {**has, "$gte": today_s, "$lte": soon}}
    if status == "valid":
        return {"expiry_date": {**has, "$gt": soon}}
    raise HTTPException(status_code=422, detail="Unknown status filter.")


def _build_query(q, category, subcategory, document_type, organisation, status) -> Dict[str, Any]:
    clauses: List[Dict[str, Any]] = [dict(SCOPE_FILTER)]
    if category:
        clauses.append({"category": category})
    if subcategory:
        clauses.append({"subcategory": subcategory})
    if document_type:
        clauses.append({"document_type": document_type})
    if organisation:
        clauses.append({"issuing_organisation": organisation})
    if status:
        clauses.append(_status_query(status))
    if q:
        pattern = re.compile(re.escape(q), re.IGNORECASE)
        clauses.append(
            {
                "$or": [
                    {"title": pattern},
                    {"document_type": pattern},
                    {"category": pattern},
                    {"subcategory": pattern},
                    {"issuing_organisation": pattern},
                    {"certificate_number": pattern},
                    {"file_name": pattern},
                    {"notes": pattern},
                ]
            }
        )
    return {"$and": clauses}


@router.get("/", response_model=schemas.CompanyDocumentPage)
def list_company_documents(
    q: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    document_type: Optional[str] = None,
    organisation: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = DEFAULT_SORT,
    page: int = Query(default=1, ge=1, le=log.MAX_PAGE),
    page_size: int = Query(default=25, ge=1, le=log.MAX_PAGE_SIZE),
    documents: Collection = Depends(get_documents),
):
    query = _build_query(q, category, subcategory, document_type, organisation, status)
    key_stages, sort_spec = log._sort_stages(sort, SORTABLE)
    pipeline: List[Dict[str, Any]] = [
        {"$match": query},
        *key_stages,
        {"$sort": sort_spec},
        {"$skip": (page - 1) * page_size},
        {"$limit": page_size},
        {"$project": {"_sort_blank": 0, "_sort_key": 0}},
    ]
    return {
        "items": [_serialise(doc) for doc in documents.aggregate(pipeline)],
        "total": documents.count_documents(query),
        "page": page,
        "page_size": page_size,
    }


def _facet(documents: Collection, field: str) -> List[Dict[str, Any]]:
    rows = documents.aggregate(
        [
            {"$match": {**SCOPE_FILTER, field: {"$type": "string", "$ne": ""}}},
            {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
        ]
    )
    items = [{"value": row["_id"], "count": row["count"]} for row in rows]
    return sorted(items, key=lambda item: item["value"].lower())


@router.get("/facets", response_model=schemas.CompanyDocumentFacets)
def company_document_facets(documents: Collection = Depends(get_documents)):
    """Every value in use, with counts, for the filters and the form's
    suggestions. Read from the data, like the log's own type and category
    lists, so it can never offer a value nothing holds."""
    statuses = []
    for name in schemas.COMPANY_STATUSES:
        count = documents.count_documents({"$and": [dict(SCOPE_FILTER), _status_query(name)]})
        statuses.append({"value": name, "count": count})
    return {
        "categories": _facet(documents, "category"),
        "subcategories": _facet(documents, "subcategory"),
        "document_types": _facet(documents, "document_type"),
        "organisations": _facet(documents, "issuing_organisation"),
        "statuses": statuses,
        "total": documents.count_documents(SCOPE_FILTER),
    }


@router.get("/{document_id}", response_model=schemas.CompanyDocumentOut)
def get_company_document(document_id: str, documents: Collection = Depends(get_documents)):
    record = documents.find_one({"_id": _oid(document_id), **SCOPE_FILTER})
    if not record:
        raise HTTPException(status_code=404, detail="Company document not found")
    return _serialise(record)


@router.post("/", response_model=schemas.CompanyDocumentOut, status_code=201)
def create_company_document(
    title: str = Form(...),
    document_type: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    subcategory: Optional[str] = Form(None),
    issuing_organisation: Optional[str] = Form(None),
    certificate_number: Optional[str] = Form(None),
    issue_date: Optional[str] = Form(None),
    expiry_date: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    document_file: UploadFile = File(...),
    current_user: Dict[str, str] = Depends(auth.require_user),
    documents: Collection = Depends(get_documents),
):
    """Add a company document. The file is required: a company document
    without its certificate is not evidence of anything. Duplicate files are
    refused (409) by the log's own hash check."""
    if not _clean(title):
        raise HTTPException(status_code=422, detail="Title is required.")
    issue_date, expiry_date = (issue_date or None), (expiry_date or None)
    _check_dates(issue_date, expiry_date)
    stored_file_name, file_hash = log._save_upload(document_file, documents)
    record = {
        "scope": schemas.COMPANY_SCOPE,
        "title": _clean(title),
        "document_type": _clean(document_type),
        "category": _clean(category),
        "subcategory": _clean(subcategory),
        "issuing_organisation": _clean(issuing_organisation),
        "certificate_number": _clean(certificate_number, 100),
        "issue_date": issue_date,
        "expiry_date": expiry_date,
        "notes": _clean_notes(notes),
        "file_name": storage.safe_display_name(document_file.filename),
        "stored_file_name": stored_file_name,
        "file_hash": file_hash,
        "source_key": f"sha256:{file_hash}",
        "metadata_source": "manual",
        "submitted_by": current_user["username"],
        "project_id": None,
        "employee_id": None,
        "tender_id": None,
        "redacted_from_id": None,
        "created_at": log._now_utc_ms(),
    }
    try:
        record["_id"] = documents.insert_one(record).inserted_id
    except DuplicateKeyError:
        storage.remove_stored_file(stored_file_name)
        raise log._duplicate_error()
    except Exception:
        storage.remove_stored_file(stored_file_name)
        raise
    return _serialise(record)


@router.put("/{document_id}", response_model=schemas.CompanyDocumentOut)
def update_company_document(
    document_id: str,
    title: str = Form(...),
    document_type: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    subcategory: Optional[str] = Form(None),
    issuing_organisation: Optional[str] = Form(None),
    certificate_number: Optional[str] = Form(None),
    issue_date: Optional[str] = Form(None),
    expiry_date: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    document_file: Optional[UploadFile] = File(None),
    current_user: Dict[str, str] = Depends(auth.require_user),
    documents: Collection = Depends(get_documents),
):
    """Edit metadata, and (Admin only) replace the file.

    Saving by a person makes the metadata source "manual", so a later import
    never overwrites what was saved.
    """
    oid = _oid(document_id)
    replacing = bool(document_file and document_file.filename)
    if replacing and current_user["role"] != "Admin":
        raise HTTPException(
            status_code=403, detail="HODs may edit document details but cannot replace files."
        )
    record = documents.find_one({"_id": oid, **SCOPE_FILTER})
    if not record:
        raise HTTPException(status_code=404, detail="Company document not found")
    if not _clean(title):
        raise HTTPException(status_code=422, detail="Title is required.")
    issue_date, expiry_date = (issue_date or None), (expiry_date or None)
    _check_dates(issue_date, expiry_date)

    changes: Dict[str, Any] = {
        "title": _clean(title),
        "document_type": _clean(document_type),
        "category": _clean(category),
        "subcategory": _clean(subcategory),
        "issuing_organisation": _clean(issuing_organisation),
        "certificate_number": _clean(certificate_number, 100),
        "issue_date": issue_date,
        "expiry_date": expiry_date,
        "notes": _clean_notes(notes),
        "metadata_source": "manual",
    }
    new_stored = None
    if replacing:
        new_stored, new_hash = log._save_upload(document_file, documents, exclude_id=oid)
        changes.update(
            file_name=storage.safe_display_name(document_file.filename),
            stored_file_name=new_stored,
            file_hash=new_hash,
            source_key=f"sha256:{new_hash}",
        )
    try:
        updated = documents.find_one_and_update(
            {"_id": oid, **SCOPE_FILTER}, {"$set": changes}, return_document=ReturnDocument.AFTER
        )
    except DuplicateKeyError:
        storage.remove_stored_file(new_stored)
        raise log._duplicate_error()
    except Exception:
        storage.remove_stored_file(new_stored)
        raise
    if updated is None:
        storage.remove_stored_file(new_stored)
        raise HTTPException(status_code=404, detail="Company document not found")
    if replacing and record.get("stored_file_name") != new_stored:
        storage.remove_stored_file(record.get("stored_file_name"))
    return _serialise(updated)
