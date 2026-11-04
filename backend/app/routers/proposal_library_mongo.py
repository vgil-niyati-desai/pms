"""Proposal library: reusable write-ups that belong to no tender.

Admin and HOD may read, add, edit and delete, matching the document log
(Normal Users have no access to either). Files are stored with the shared
storage helpers, so they sit in the same uploads folder under UUID names, but
the records live in their own collection and only .docx and .rtf are accepted
here -- the document log's PDF/PNG/JPG-only rule is not widened for the rest
of the app.
"""

import os
import re
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pymongo import ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from .. import auth, docx_html, docx_text, proposal_library, redaction, schemas, storage
from ..mongodb import get_proposal_library
from . import documents_mongo as log

router = APIRouter(
    prefix="/proposal-library",
    tags=["proposal-library"],
    dependencies=[Depends(auth.require_roles("Admin", "HOD"))],
)

SORTABLE = {"section", "title", "created_at", "word_count"}
MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "rtf": "application/rtf",
}
_LONG_FIELDS = ("requirement_text", "response_text", "source_path", "image_text")


def _oid(item_id: str) -> ObjectId:
    try:
        return ObjectId(item_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Write-up not found")


def _row(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: doc.get(key) for key in schemas.ProposalItemRow.model_fields}
    out["id"] = str(doc["_id"])
    out["has_file"] = bool(doc.get("stored_file_name"))
    out["word_count"] = doc.get("word_count") or 0
    out["image_count"] = doc.get("image_count") or 0
    return out


def _full(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = _row(doc)
    for field in _LONG_FIELDS:
        out[field] = doc.get(field)
    out["image_ocr"] = doc.get("image_ocr")
    return out


def _clean(value: Optional[str], limit: int = 300) -> Optional[str]:
    if value is None:
        return None
    return " ".join(value.split())[:limit] or None


def _text(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value[:20000] or None


def _sort(sort: str) -> Dict[str, int]:
    descending = sort.startswith("-")
    key = sort.lstrip("-")
    if key not in SORTABLE:
        key, descending = "section", False
    direction = -1 if descending else 1
    if key == "section":
        return {"section_key": direction, "subsection": direction, "point_number": direction, "_id": direction}
    return {key: direction, "_id": direction}


@router.get("/", response_model=schemas.ProposalItemPage)
def list_items(
    q: Optional[str] = None,
    section: Optional[str] = Query(None, description="Exact section number, e.g. 3.1.1"),
    part: Optional[str] = Query(None, description="First number of the section, e.g. 3"),
    file_type: Optional[str] = None,
    has_images: Optional[bool] = None,
    sort: str = "section",
    page: int = Query(default=1, ge=1, le=log.MAX_PAGE),
    page_size: int = Query(default=25, ge=1, le=log.MAX_PAGE_SIZE),
    items: Collection = Depends(get_proposal_library),
):
    clauses: List[Dict[str, Any]] = []
    if section:
        clauses.append({"section_number": section})
    if part:
        clauses.append({"section_number": re.compile("^" + re.escape(part) + r"(\.|$|[^0-9])")})
    if file_type:
        clauses.append({"file_type": file_type.lower()})
    if has_images is True:
        clauses.append({"image_count": {"$gt": 0}})
    if has_images is False:
        clauses.append({"$or": [{"image_count": 0}, {"image_count": None}]})
    if q:
        pattern = re.compile(re.escape(q), re.IGNORECASE)
        clauses.append(
            {
                "$or": [
                    {field: pattern}
                    for field in (
                        "title", "requirement_text", "response_text", "image_text", "section_title",
                        "section_number", "subsection", "file_name",
                    )
                ]
            }
        )
    query = {"$and": clauses} if clauses else {}
    cursor = (
        items.find(query, {field: 0 for field in _LONG_FIELDS})
        .sort(list(_sort(sort).items()))
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [_row(doc) for doc in cursor],
        "total": items.count_documents(query),
        "page": page,
        "page_size": page_size,
    }


@router.get("/sections", response_model=List[schemas.ProposalSection])
def list_sections(items: Collection = Depends(get_proposal_library)):
    """Every section in use with its write-up count, in section order."""
    rows = items.aggregate(
        [
            {
                "$group": {
                    "_id": {"n": "$section_number", "t": "$section_title", "k": "$section_key"},
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id.k": 1, "_id.t": 1}},
        ]
    )
    return [
        {"section_number": r["_id"]["n"], "section_title": r["_id"]["t"], "count": r["count"]}
        for r in rows
    ]


@router.get("/{item_id}", response_model=schemas.ProposalItemOut)
def get_item(item_id: str, items: Collection = Depends(get_proposal_library)):
    doc = items.find_one({"_id": _oid(item_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Write-up not found")
    return _full(doc)


@router.get("/{item_id}/file")
def download_file(item_id: str, items: Collection = Depends(get_proposal_library)):
    doc = items.find_one({"_id": _oid(item_id)})
    if not doc or not doc.get("stored_file_name"):
        raise HTTPException(status_code=404, detail="No file for this write-up")
    path = storage.upload_path(doc["stored_file_name"])
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File missing on server")
    return FileResponse(
        path,
        media_type=MEDIA_TYPES.get(doc.get("file_type"), "application/octet-stream"),
        headers={
            "Content-Disposition": redaction.content_disposition_header(
                storage.safe_display_name(doc.get("file_name"), default=doc["stored_file_name"]),
                "attachment",
            )
        },
    )


@router.get("/{item_id}/preview")
def preview_file(item_id: str, items: Collection = Depends(get_proposal_library)):
    """The stored original as an HTML fragment (for the in-app viewer), so a
    write-up can be read before it is downloaded. Read-only; nothing is written."""
    doc = items.find_one({"_id": _oid(item_id)})
    if not doc or not doc.get("stored_file_name"):
        raise HTTPException(status_code=404, detail="No file for this write-up")
    path = storage.upload_path(doc["stored_file_name"])
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File missing on server")
    with open(path, "rb") as handle:
        contents = handle.read()
    try:
        return docx_html.render(contents, doc.get("file_type") or "")
    except docx_text.UnreadableDocument as error:
        raise HTTPException(status_code=422, detail="This file cannot be previewed: %s" % error)


@router.post("/", response_model=schemas.ProposalItemOut, status_code=201)
def create_item(
    section_number: str = Form(...),
    section_title: Optional[str] = Form(None),
    subsection: Optional[str] = Form(None),
    point_number: Optional[int] = Form(None),
    document_file: UploadFile = File(...),
    items: Collection = Depends(get_proposal_library),
):
    """Add one write-up. The text is read from the file; the requirement and
    response can be corrected afterwards. Duplicate files (same bytes) are
    refused with 409."""
    name = storage.safe_display_name(document_file.filename)
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in schemas.PROPOSAL_FILE_TYPES:
        raise HTTPException(status_code=400, detail="File must be a Word (.docx) or RTF (.rtf) document.")
    if not _clean(section_number, 40):
        raise HTTPException(status_code=422, detail="Section number is required.")
    contents = document_file.file.read(storage.MAX_UPLOAD_BYTES + 1)
    if not contents:
        raise HTTPException(status_code=400, detail=f'"{name}" is empty (0 bytes). Please choose the actual file.')
    if len(contents) > storage.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail=f'"{name}" is larger than the {storage.MAX_UPLOAD_LABEL} limit.'
        )
    try:
        record = proposal_library.build_record(
            contents, name,
            section_number=_clean(section_number, 40), section_title=_clean(section_title),
            subsection=_clean(subsection), point_number=point_number,
            created_at=log._now_utc_ms(),
        )
    except docx_text.UnreadableDocument as error:
        raise HTTPException(status_code=422, detail=f'"{name}" could not be read: {error}.')
    if items.find_one({"source_key": record["source_key"]}):
        raise log._duplicate_error()
    record["stored_file_name"] = storage.write_upload(contents, name)
    try:
        record["_id"] = items.insert_one(record).inserted_id
    except DuplicateKeyError:
        storage.remove_stored_file(record["stored_file_name"])
        raise log._duplicate_error()
    except Exception:
        storage.remove_stored_file(record["stored_file_name"])
        raise
    return _full(record)


@router.put("/{item_id}", response_model=schemas.ProposalItemOut)
def update_item(
    item_id: str,
    body: schemas.ProposalItemIn,
    items: Collection = Depends(get_proposal_library),
):
    if not _clean(body.section_number, 40):
        raise HTTPException(status_code=422, detail="Section number is required.")
    changes = {
        "section_number": _clean(body.section_number, 40),
        "section_title": _clean(body.section_title),
        "section_key": proposal_library.sort_key(_clean(body.section_number, 40)),
        "subsection": _clean(body.subsection),
        "point_number": body.point_number,
        "title": _clean(body.title) or _clean(body.requirement_text),
        "requirement_text": _text(body.requirement_text),
        "response_text": _text(body.response_text),
    }
    updated = items.find_one_and_update(
        {"_id": _oid(item_id)}, {"$set": changes}, return_document=ReturnDocument.AFTER
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Write-up not found")
    return _full(updated)


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: str, items: Collection = Depends(get_proposal_library)):
    doc = items.find_one_and_delete({"_id": _oid(item_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Write-up not found")
    storage.remove_stored_file(doc.get("stored_file_name"))
    return Response(status_code=204)
