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
from pymongo import ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from .. import detection, redaction, schemas, storage
from ..mongodb import (
    get_documents,
    get_employee_records,
    get_project_records,
    get_tender_records,
)

router = APIRouter(prefix="/documents", tags=["documents"])

# What the document log can be ordered by. Every one except created_at is a
# string, stored as typed (dates as ISO text, which sorts correctly as text).
SORTABLE = {"created_at", "document_date", "document_type", "client_name", "project_title"}
DEFAULT_SORT = "-created_at"

# The same page size ceiling the other lists use, so a hand-edited URL cannot
# ask for the whole log in one response.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200


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


# The projects router serves a project's own documents and needs the same
# shape back. Exported rather than duplicated, so the two can never drift.
serialise_document = _serialise


def _require_project(projects: Collection, project_id: str) -> ObjectId:
    """Parse and check a project id supplied with an upload, or 404.

    Checked before anything is written, so a bad project id cannot leave a
    file on disk or a document record pointing at a project that is not
    there.
    """
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Project not found")
    if not projects.find_one({"_id": oid}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Project not found")
    return oid


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
    project_id: Optional[str] = Form(None),
    document_file: Optional[UploadFile] = File(None),
    documents: Collection = Depends(get_documents),
    projects: Collection = Depends(get_project_records),
):
    """Log a document, optionally as one belonging to a project.

    Supplying project_id both stamps the document and adds it to that
    project's document_ids, so one request leaves the two representations
    agreeing. Omitting it is the standalone case -- the document log is
    shared with the CV and tender screens, whose uploads belong to no
    project at all.
    """
    project_oid = _require_project(projects, project_id) if project_id else None

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
        "project_id": project_id or None,
        # Set from the employee side, by the CV and certification endpoints
        # in routers/employees_mongo.py. Present from the start so "belongs
        # to no employee" is recorded rather than merely absent.
        "employee_id": None,
        # Set from the tender side, by the attach endpoint in
        # routers/tenders_mongo.py. Present from the start for the same
        # reason as employee_id above.
        "tender_id": None,
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

    if project_oid is not None:
        # $addToSet, so this staying in step with an explicit link call
        # from the client is a no-op rather than a duplicate entry.
        projects.update_one(
            {"_id": project_oid},
            {
                "$addToSet": {"document_ids": str(result.inserted_id)},
                "$set": {"updated_at": _now_utc_ms()},
            },
        )

    return _serialise(record)


def _sort_stages(sort: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The (stages before the sort, sort spec) for one sort option.

    The rules the projects list sorts by: blanks last whichever way the arrow
    points, text compared case-insensitively, and `_id` breaking every tie so
    paging never shows a row twice or skips one. An unknown key falls back to
    newest first rather than failing the request.
    """
    descending = sort.startswith("-")
    key = sort.lstrip("-")
    if key not in SORTABLE:
        descending, key = True, "created_at"
    direction = -1 if descending else 1

    if key == "created_at":
        return [], {"created_at": direction, "_id": direction}

    value = {"$ifNull": [f"${key}", ""]}
    stages = [
        {
            "$addFields": {
                "_sort_blank": {"$cond": [{"$eq": [value, ""]}, 1, 0]},
                "_sort_key": {"$toLower": value},
            }
        }
    ]
    return stages, {"_sort_blank": 1, "_sort_key": direction, "_id": direction}


@router.get("/types", response_model=List[str])
def list_document_types(documents: Collection = Depends(get_documents)):
    """Every document type in use, for the log's type filter and form.

    Read from the data rather than a fixed list, because the log holds the
    types every area writes -- CVs, receipts, tender papers, imported
    entries -- not only the ones the Documents form offers.
    """
    values = documents.distinct("document_type")
    return sorted({v for v in values if isinstance(v, str) and v.strip()}, key=str.lower)


@router.get("/", response_model=schemas.DocumentPage)
def list_documents(
    document_type: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    sort: str = DEFAULT_SORT,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    documents: Collection = Depends(get_documents),
):
    """One page of the document log, filtered and sorted.

    The count is taken against the same filter as the page, so the pager and
    the rows can never disagree about how many results there are.
    """
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

    key_stages, sort_spec = _sort_stages(sort)
    pipeline: List[Dict[str, Any]] = [{"$match": query}, *key_stages]
    pipeline.append({"$sort": sort_spec})
    pipeline.append({"$skip": (page - 1) * page_size})
    pipeline.append({"$limit": page_size})
    # The computed sort keys are working state, not part of the document.
    pipeline.append({"$project": {"_sort_blank": 0, "_sort_key": 0}})

    records = list(documents.aggregate(pipeline))
    return {
        "items": [_serialise(doc) for doc in records],
        "total": documents.count_documents(query),
        "page": page,
        "page_size": page_size,
    }


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


@router.get(
    "/{document_id}/redaction/suggestions", response_model=schemas.SensitiveScan
)
def get_sensitive_suggestions(
    document_id: str, documents: Collection = Depends(get_documents)
):
    """Areas that look like financial information, for the user to review.

    A suggestion and nothing more. This reads the stored file, finds figures
    in its text layer and returns rectangles in the same normalised format a
    hand-drawn box uses -- it does not redact, does not save the boxes, and
    does not change the entry or its file. Accepting one in the UI simply
    adds it to the areas posted to /redacted-copy below, which is still the
    only thing that redacts anything.
    """
    record = _redaction_source(document_id, documents)
    return detection.analyse(record["stored_file_name"])


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

    # project_id is deliberately absent from this dict. An edit changes what
    # a document says, not whose it is; attaching and detaching are their own
    # endpoints. Listing it here would also mean a form that does not send it
    # -- which is every form the app has -- silently detaching the document
    # on every save while the project's document_ids still pointed at it.
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
def delete_document(
    document_id: str,
    documents: Collection = Depends(get_documents),
    projects: Collection = Depends(get_project_records),
    employees: Collection = Depends(get_employee_records),
    tenders: Collection = Depends(get_tender_records),
):
    """Delete the record, its references, then its stored file.

    The database document goes first so a failed file delete can never leave
    a live record pointing at a file that is no longer there.

    Any project listing the document is then cleared of it. Without that a
    delete from anywhere other than a project's own drawer -- the document
    log screen, say -- left the id behind in document_ids, pointing at
    nothing. update_many rather than update_one because the field is an
    array on every project and more than one may reference it.

    The employee side is cleared the same way but not by the same means. A
    project references a document from a list, so the id is pulled out of it;
    a CV version or a certification *is* a record, which keeps its dates, its
    label and its place in the person's history whether or not the file
    behind it still exists. So the record stays and only the pointer is
    cleared -- along with file_name, which is a copy of a name that no longer
    resolves and would otherwise show as a file that cannot be opened.
    """
    record = documents.find_one_and_delete({"_id": _object_id(document_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")

    projects.update_many(
        {"document_ids": document_id},
        {
            "$pull": {"document_ids": document_id},
            "$set": {"updated_at": _now_utc_ms()},
        },
    )

    # A tender holds its documents in a list, exactly as a project does, so
    # it is cleared the same way. Without this a document deleted from the
    # log left its id behind in document_ids, pointing at nothing -- which is
    # what the tender screen then tried to show.
    tenders.update_many(
        {"document_ids": document_id},
        {
            "$pull": {"document_ids": document_id},
            "$set": {"updated_at": _now_utc_ms()},
        },
    )

    # A tender's nested records point at a document the way a CV does, and
    # are cleared the way a CV is: the cost item keeps its amount, its
    # payment mode and its instrument number whether or not the receipt
    # behind it still exists, so the record stays and only the pointer goes.
    # These files are deliberately not the tender's *documents* -- the
    # Documents tab holds what the bid was filed with, not what it cost --
    # so they are reached from here and not through document_ids.
    for field in ("cost_items", "certificates"):
        tenders.update_many(
            {f"{field}.document_id": document_id},
            {
                "$set": {
                    f"{field}.$[item].document_id": None,
                    f"{field}.$[item].file_name": None,
                    "updated_at": _now_utc_ms(),
                }
            },
            array_filters=[{"item.document_id": document_id}],
        )

    # Both nested arrays, because either kind of record can point at this
    # document. The positional filter updates every matching element, not
    # just the first -- one employee can hold two CV versions of the same
    # file only by pointing both at it, and both have to be cleared.
    for field in ("cvs", "certifications"):
        employees.update_many(
            {f"{field}.document_id": document_id},
            {
                "$set": {
                    f"{field}.$[item].document_id": None,
                    f"{field}.$[item].file_name": None,
                    "updated_at": _now_utc_ms(),
                }
            },
            array_filters=[{"item.document_id": document_id}],
        )

    storage.remove_stored_file(record.get("stored_file_name"))
    return Response(status_code=204)
