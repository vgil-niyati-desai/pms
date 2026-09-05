"""Document endpoints backed by MongoDB.

`id` is a 24-character ObjectId hex string, which the React app treats as
opaque — it only ever compares ids and puts them in URLs.
"""

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pymongo import DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from .. import redaction, schemas, storage
from ..mongodb import get_documents

router = APIRouter(prefix="/documents", tags=["documents"])


def _now_utc_ms() -> datetime:
    """Current UTC time at BSON's precision (milliseconds)."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _object_id(document_id: str) -> ObjectId:
    """Parse a path id, or 404.

    A malformed id is a request for something that cannot exist, so it gets
    the same answer as a well-formed id that isn't there, not a 422.
    """
    try:
        return ObjectId(document_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Document not found")


def _serialise(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Mongo document -> the shape schemas.DocumentMongoOut expects."""
    out = {key: value for key, value in doc.items() if key != "_id"}
    out["id"] = str(doc["_id"])
    return out


def backfill_file_hashes(collection: Collection) -> int:
    """Fill in file_hash for entries stored before hashing existed.

    Without this, duplicate detection would silently ignore any file already
    in the system. Entries whose file is missing from disk are skipped and
    stay null, which just means they can't be matched against.
    """
    pending = list(
        collection.find(
            {"stored_file_name": {"$ne": None}, "file_hash": None},
            {"stored_file_name": 1},
        )
    )
    updated = 0
    for record in pending:
        digest = storage.hash_file_on_disk(record["stored_file_name"])
        if digest:
            collection.update_one({"_id": record["_id"]}, {"$set": {"file_hash": digest}})
            updated += 1
    return updated


def _describe_duplicate(existing: Dict[str, Any]) -> str:
    """Human-readable message pointing at the entry that already has this file."""
    parts = [existing.get("document_type") or "another entry"]
    if existing.get("client_name"):
        parts.append("for " + str(existing["client_name"]))
    if existing.get("project_title"):
        parts.append("(" + str(existing["project_title"]) + ")")
    where = " ".join(parts)
    return (
        'This exact file has already been uploaded as "'
        + str(existing.get("file_name"))
        + '" under '
        + where
        + ". Upload a different file, or edit that entry instead."
    )


def _duplicate_error(collection: Collection, file_hash: Optional[str]) -> HTTPException:
    """Turn a unique-index violation into the same 409 the pre-check raises."""
    existing = collection.find_one({"file_hash": file_hash}) if file_hash else None
    detail = (
        _describe_duplicate(existing)
        if existing
        else "This exact file has already been uploaded under another entry."
    )
    return HTTPException(status_code=409, detail=detail)


def _check_duplicate(
    collection: Collection, file_hash: str, exclude_id: Optional[ObjectId] = None
) -> None:
    """Reject the upload if another entry already holds a file with this hash."""
    query: Dict[str, Any] = {"file_hash": file_hash}
    if exclude_id is not None:
        # Re-uploading the same file to the entry that already has it is a
        # no-op replace, not a duplicate.
        query["_id"] = {"$ne": exclude_id}
    existing = collection.find_one(query)
    if existing:
        raise HTTPException(status_code=409, detail=_describe_duplicate(existing))


def _save_upload(
    document_file: UploadFile,
    collection: Collection,
    exclude_id: Optional[ObjectId] = None,
) -> Tuple[str, str]:
    """Validate, de-duplicate, and write an uploaded file.

    Returns (stored_file_name, file_hash). The duplicate check runs *before*
    anything is written to disk, so a rejected upload leaves no file behind
    and no database record is created.
    """
    if not storage.allowed_file(document_file.filename):
        raise HTTPException(status_code=400, detail="File must be a PDF, PNG, or JPG.")

    contents = document_file.file.read()
    file_hash = hashlib.sha256(contents).hexdigest()
    _check_duplicate(collection, file_hash, exclude_id=exclude_id)

    stored_file_name = storage.write_upload(contents, document_file.filename)
    return stored_file_name, file_hash


@router.post("/", response_model=schemas.DocumentMongoOut, status_code=201)
def create_document(
    document_type: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    client_name: Optional[str] = Form(None),
    reference_number: Optional[str] = Form(None),
    project_title: Optional[str] = Form(None),
    contract_value: Optional[str] = Form(None),
    document_date: Optional[str] = Form(None),
    department: Optional[str] = Form(None),
    submitted_by: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    document_file: Optional[UploadFile] = File(None),
    documents: Collection = Depends(get_documents),
):
    stored_file_name = None
    original_file_name = None
    file_hash = None

    if document_file and document_file.filename:
        original_file_name = document_file.filename
        # Raises 409 on a duplicate, before any file is written or any
        # document is inserted, so a rejected upload changes nothing.
        stored_file_name, file_hash = _save_upload(document_file, documents)

    record = {
        "document_type": document_type,
        "category": category,
        "client_name": client_name,
        "reference_number": reference_number,
        "project_title": project_title,
        "contract_value": contract_value,
        "document_date": document_date,
        "department": department,
        "submitted_by": submitted_by,
        "notes": notes,
        "file_name": original_file_name,
        "stored_file_name": stored_file_name,
        "file_hash": file_hash,
        # Supplied by the application rather than the server, always in
        # UTC. Rounded to milliseconds because that is all BSON stores --
        # without this the timestamp in the create response would not match
        # the one every later read returns.
        "created_at": _now_utc_ms(),
    }

    try:
        result = documents.insert_one(record)
    except DuplicateKeyError:
        # The unique index caught a duplicate that slipped past the check
        # above (two simultaneous uploads of the same file). Clean up the
        # file this request wrote and report it like any other duplicate.
        storage.remove_stored_file(stored_file_name)
        raise _duplicate_error(documents, file_hash)

    record["_id"] = result.inserted_id
    return _serialise(record)


@router.get("/", response_model=List[schemas.DocumentMongoOut])
def list_documents(
    document_type: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    documents: Collection = Depends(get_documents),
):
    query: Dict[str, Any] = {}

    if document_type:
        query["document_type"] = document_type
    if category:
        query["category"] = category
    if q:
        # The MongoDB spelling of ILIKE '%q%': a case-insensitive substring
        # match. re.escape matters here, because without it a search for
        # "(2)" or "a+b" would be read as a regex and match nonsense.
        pattern = re.compile(re.escape(q), re.IGNORECASE)
        query["$or"] = [
            {"client_name": pattern},
            {"project_title": pattern},
            {"reference_number": pattern},
        ]

    # _id breaks ties: ObjectIds are time-ordered, so two entries saved in
    # the same instant still come back newest-first, not in arbitrary order.
    cursor = documents.find(query).sort([("created_at", DESCENDING), ("_id", DESCENDING)])
    return [_serialise(doc) for doc in cursor]


@router.get("/{document_id}", response_model=schemas.DocumentMongoOut)
def get_document(document_id: str, documents: Collection = Depends(get_documents)):
    record = documents.find_one({"_id": _object_id(document_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")
    return _serialise(record)


@router.get("/{document_id}/file")
def download_document_file(
    document_id: str,
    disposition: str = Query(
        "attachment",
        pattern="^(attachment|inline)$",
        description="'attachment' downloads the file; 'inline' serves it for display in the app.",
    ),
    documents: Collection = Depends(get_documents),
):
    """Serve a stored file, either as a download or for in-app preview.

    The default stays `attachment` so every existing caller keeps downloading
    exactly as before. `?disposition=inline` is what the preview asks for:
    without it a PDF loaded into an iframe is downloaded by the browser
    instead of rendered, because Content-Disposition wins over the frame.
    """
    record = documents.find_one({"_id": _object_id(document_id)})
    if not record or not record.get("stored_file_name"):
        raise HTTPException(status_code=404, detail="No file for this document")
    file_path = storage.upload_path(record["stored_file_name"])
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File missing on server")
    # Stored names keep the original extension, so FileResponse still guesses
    # the right Content-Type (application/pdf, image/png, image/jpeg) — which
    # is what an inline response needs to render rather than offer a save.
    return FileResponse(
        file_path,
        filename=record.get("file_name"),
        content_disposition_type=disposition,
    )


# ---------------------------------------------------------------------------
# Manual redaction
#
# All three of these read the stored file and nothing else. None of them
# writes to the uploads folder or changes the document record, so the
# original stays exactly as uploaded and its preview and download are
# unaffected -- the redacted copy is generated per request and streamed
# straight to the browser.
# ---------------------------------------------------------------------------


def _redaction_source(document_id: str, documents: Collection) -> Dict[str, Any]:
    """The record behind a redaction request, or the same 404 /file gives."""
    record = documents.find_one({"_id": _object_id(document_id)})
    if not record or not record.get("stored_file_name"):
        raise HTTPException(status_code=404, detail="No file for this document")
    return record


@router.get("/{document_id}/redaction/source", response_model=schemas.RedactionSource)
def get_redaction_source(document_id: str, documents: Collection = Depends(get_documents)):
    """How many pages the document has, and how big each one is.

    The selection view needs this before it can lay a page out, because a
    rectangle is only meaningful once it knows the shape it was drawn on.
    """
    record = _redaction_source(document_id, documents)
    return redaction.source_payload(record["stored_file_name"], record.get("file_name"))


@router.get("/{document_id}/redaction/pages/{page_index}")
def get_redaction_page_image(
    document_id: str,
    page_index: int,
    width: int = Query(
        redaction.DEFAULT_PAGE_IMAGE_WIDTH,
        ge=redaction.MIN_PAGE_IMAGE_WIDTH,
        le=redaction.MAX_PAGE_IMAGE_WIDTH,
        description="Width in pixels to render the page at.",
    ),
    documents: Collection = Depends(get_documents),
):
    """One page as a PNG, for the browser to draw rectangles over.

    The normal preview shows a PDF in an iframe, which is opaque: nothing
    outside it can tell where on the page a click landed. Redaction needs
    that, so it draws over a rendered page instead.
    """
    record = _redaction_source(document_id, documents)
    return redaction.page_image_response(record["stored_file_name"], page_index, width)


@router.post("/{document_id}/redacted-copy")
def create_redacted_copy(
    document_id: str,
    request: schemas.RedactionRequest,
    documents: Collection = Depends(get_documents),
):
    """Generate and return a permanently redacted copy of the stored file.

    The copy is not saved anywhere and gets no document record: it is a
    download, built fresh from the untouched original each time it is asked
    for. Under every rectangle the text is deleted rather than covered, so
    the black box in the copy has nothing behind it.
    """
    record = _redaction_source(document_id, documents)
    areas = [
        redaction.Area(page=a.page, x=a.x, y=a.y, width=a.width, height=a.height)
        for a in request.areas
    ]
    return redaction.redacted_copy_response(
        record["stored_file_name"], record.get("file_name"), areas
    )


@router.put("/{document_id}", response_model=schemas.DocumentMongoOut)
def update_document(
    document_id: str,
    document_type: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    client_name: Optional[str] = Form(None),
    reference_number: Optional[str] = Form(None),
    project_title: Optional[str] = Form(None),
    contract_value: Optional[str] = Form(None),
    document_date: Optional[str] = Form(None),
    department: Optional[str] = Form(None),
    submitted_by: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    document_file: Optional[UploadFile] = File(None),
    documents: Collection = Depends(get_documents),
):
    """Update metadata, and optionally replace the stored file.

    Sending no `document_file` keeps whatever file the record already has.
    Sending one replaces it: the new file is written first, and the old one
    is only deleted once the database write has succeeded, so a failure at
    any point leaves the record and its file still matching each other.
    """
    oid = _object_id(document_id)
    record = documents.find_one({"_id": oid})
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")

    replacing = bool(document_file and document_file.filename)
    previous_stored_file_name = record.get("stored_file_name")
    new_stored_file_name = None
    new_file_hash = None
    if replacing:
        # Rejects with 409 if another entry already has this file. The
        # record's own current file is excluded, so re-uploading it is fine.
        new_stored_file_name, new_file_hash = _save_upload(
            document_file, documents, exclude_id=oid
        )

    changes: Dict[str, Any] = {
        "document_type": document_type,
        "category": category,
        "client_name": client_name,
        "reference_number": reference_number,
        "project_title": project_title,
        "contract_value": contract_value,
        "document_date": document_date,
        "department": department,
        "submitted_by": submitted_by,
        "notes": notes,
    }
    if replacing:
        changes["file_name"] = document_file.filename
        changes["stored_file_name"] = new_stored_file_name
        changes["file_hash"] = new_file_hash

    try:
        updated = documents.find_one_and_update(
            {"_id": oid}, {"$set": changes}, return_document=ReturnDocument.AFTER
        )
    except DuplicateKeyError:
        storage.remove_stored_file(new_stored_file_name)
        raise _duplicate_error(documents, new_file_hash)
    except Exception:
        # Don't leave the just-written replacement orphaned on disk.
        storage.remove_stored_file(new_stored_file_name)
        raise

    if updated is None:
        # Deleted by someone else between the read above and this write.
        storage.remove_stored_file(new_stored_file_name)
        raise HTTPException(status_code=404, detail="Document not found")

    if replacing and previous_stored_file_name != new_stored_file_name:
        storage.remove_stored_file(previous_stored_file_name)

    return _serialise(updated)


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: str, documents: Collection = Depends(get_documents)):
    """Delete the record, then its stored file.

    The database document goes first so a failed file delete can never leave
    a live record pointing at a file that is no longer there.
    """
    record = documents.find_one_and_delete({"_id": _object_id(document_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")

    storage.remove_stored_file(record.get("stored_file_name"))
    return Response(status_code=204)
