"""Document endpoints backed by MongoDB.

`id` is a 24-character ObjectId hex string, which the React app treats as
opaque â€” it only ever compares ids and puts them in URLs.
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
# The furthest page a list may ask for. Far past any real list, and far short
# of the skip MongoDB can no longer represent -- which a page like 10**20
# reached, as a 500. Beyond the last page is still a normal, empty page.
MAX_PAGE = 100_000


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


def _check_document_date(document_date: Optional[str]) -> None:
    """422 for a document date that is not blank or YYYY-MM-DD -- the form's
    date input only produces those. Checked before any upload is written, so
    a refused request leaves nothing behind. Reported in the same shape as
    FastAPI's own validation errors."""
    try:
        schemas.check_iso_date(document_date, "Document date")
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=[{"type": "value_error", "loc": ["body", "document_date"], "msg": str(exc)}],
        )


def _maybe_object_id(value: Optional[str]) -> Optional[ObjectId]:
    try:
        return ObjectId(value) if value else None
    except (InvalidId, TypeError):
        return None


def _document_usage(
    document_id: str,
    record: Dict[str, Any],
    projects: Collection,
    tenders: Collection,
    employees: Collection,
) -> List[Dict[str, Any]]:
    """Every project, tender and employee that uses this document.

    Read from both sides of each relationship -- the owner pointer on the
    document and the lists and nested records that point back at it -- so a
    reference only one side still carries is counted too. These are exactly
    the references delete_document clears, which is what makes this the
    answer to "what would a permanent delete take away from someone?".
    """
    uses: List[Dict[str, Any]] = []

    def owned_by(field: str) -> List[Dict[str, Any]]:
        oid = _maybe_object_id(record.get(field))
        return [{"_id": oid}] if oid else []

    for project in projects.find(
        {"$or": [{"document_ids": document_id}, *owned_by("project_id")]}, {"title": 1}
    ):
        uses.append({
            "kind": "project",
            "id": str(project["_id"]),
            "label": (project.get("title") or "").strip() or "Untitled project",
            "roles": ["attached document"],
        })

    for tender in tenders.find(
        {
            "$or": [
                {"document_ids": document_id},
                {"cost_items.document_id": document_id},
                {"certificates.document_id": document_id},
                *owned_by("tender_id"),
            ]
        },
        {"title": 1, "document_ids": 1, "cost_items.document_id": 1, "certificates.document_id": 1},
    ):
        tender_id = str(tender["_id"])
        roles = []
        if document_id in (tender.get("document_ids") or []) or record.get("tender_id") == tender_id:
            roles.append("attached document")
        if any(i.get("document_id") == document_id for i in tender.get("cost_items") or []):
            roles.append("cost receipt")
        if any(i.get("document_id") == document_id for i in tender.get("certificates") or []):
            roles.append("submitted certificate")
        uses.append({
            "kind": "tender",
            "id": tender_id,
            "label": (tender.get("title") or "").strip() or "Untitled tender",
            "roles": roles,
        })

    for employee in employees.find(
        {
            "$or": [
                {"cvs.document_id": document_id},
                {"certifications.document_id": document_id},
                *owned_by("employee_id"),
            ]
        },
        {"full_name": 1, "cvs.document_id": 1, "certifications.document_id": 1},
    ):
        roles = []
        if any(i.get("document_id") == document_id for i in employee.get("cvs") or []):
            roles.append("CV")
        if any(i.get("document_id") == document_id for i in employee.get("certifications") or []):
            roles.append("certification")
        uses.append({
            "kind": "employee",
            "id": str(employee["_id"]),
            "label": (employee.get("full_name") or "").strip() or "Unnamed employee",
            "roles": roles or ["document"],
        })

    return uses


# The record a delete is asked for from -- the "this" in "used by anything
# other than this?". A project or a tender's own document list is named by
# the record alone; a CV, a certification, a cost receipt or a submitted
# certificate is one nested record inside an employee or a tender, so it is
# named by that record plus `from_item`, the nested record's id.
_OWNER_NOUN = {"project": "this project", "tender": "this tender"}


def _owner_context(
    from_project: Optional[str],
    from_tender: Optional[str],
    from_employee: Optional[str],
    from_item: Optional[str],
) -> Optional[Dict[str, Optional[str]]]:
    """The owner a request names, or None for the document log's own call.

    At most one owner may be named. `from_item` only makes sense inside a
    tender or an employee, and an employee always needs it: every reference
    an employee holds is a CV or a certification.
    """
    named = [
        (kind, value)
        for kind, value in (
            ("project", from_project),
            ("tender", from_tender),
            ("employee", from_employee),
        )
        if value
    ]
    if not named:
        if from_item:
            raise HTTPException(
                status_code=422, detail="from_item needs from_tender or from_employee."
            )
        return None
    if len(named) > 1:
        raise HTTPException(
            status_code=422,
            detail="Name one of from_project, from_tender or from_employee, not several.",
        )
    kind, owner_id = named[0]
    if kind == "project" and from_item:
        raise HTTPException(status_code=422, detail="from_item cannot be used with from_project.")
    if kind == "employee" and not from_item:
        raise HTTPException(status_code=422, detail="from_employee needs from_item.")
    return {"kind": kind, "id": owner_id, "item": from_item}


def _other_uses(
    document_id: str,
    record: Dict[str, Any],
    context: Dict[str, Optional[str]],
    projects: Collection,
    tenders: Collection,
    employees: Collection,
) -> List[Dict[str, Any]]:
    """Every use of the document except the one `context` names.

    Built on _document_usage, then with the owner's own reference taken out:

    * a project: the whole project -- its list entry and its project_id.
    * a tender's document list (no item): the tender's "attached document"
      use. Its cost receipts and submitted certificates are separate records
      and still count.
    * one nested record: that record alone. Another CV, certification, cost
      item or certificate in the same employee or tender pointing at the
      same file still counts, as does the tender's own document list.
      An employee's employee_id is not a use of its own: it is stamped and
      released by the CVs and certifications, so it goes with them.
    """
    uses = _document_usage(document_id, record, projects, tenders, employees)
    kind, owner_id, item_id = context["kind"], context["id"], context["item"]
    if kind == "project":
        return [use for use in uses if not (use["kind"] == "project" and use["id"] == owner_id)]

    # For one nested record: which nested roles the owner still has once that
    # record is set aside. None for a tender's document list, whose nested
    # records are all still there.
    remaining_roles: Optional[List[str]] = None
    if item_id:
        collection, fields = (
            (tenders, (("cost_items", "cost receipt"), ("certificates", "submitted certificate")))
            if kind == "tender"
            else (employees, (("cvs", "CV"), ("certifications", "certification")))
        )
        owner_oid = _maybe_object_id(owner_id)
        holder = (
            collection.find_one(
                {"_id": owner_oid},
                {f"{field}.{key}": 1 for field, _ in fields for key in ("id", "document_id")},
            )
            if owner_oid
            else None
        ) or {}
        remaining_roles = [
            role
            for field, role in fields
            if any(
                entry.get("document_id") == document_id and entry.get("id") != item_id
                for entry in holder.get(field) or []
            )
        ]

    others = []
    for use in uses:
        if use["kind"] != kind or use["id"] != owner_id:
            others.append(use)
            continue
        if remaining_roles is None:
            roles = [role for role in use["roles"] if role != "attached document"]
        else:
            # The tender's document list is not a nested record, so it is
            # kept as it was; everything nested is what was just counted.
            listed = [role for role in use["roles"] if kind == "tender" and role == "attached document"]
            roles = listed + remaining_roles
        if roles:
            others.append({**use, "roles": roles})
    return others


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


def _redacted_copy_notes(record: Dict[str, Any]) -> str:
    """The copy's notes: a line saying what it is, then the original's own.

    The file name ends `_redacted` and `redacted_from_id` names the source, so
    this is not what identifies the copy -- it is what makes it obvious in a
    plain read of the collection, where neither of those is being looked at.
    """
    source_name = record.get("file_name") or record.get("stored_file_name") or "a document"
    marker = f"Redacted copy of {source_name}."
    existing = (record.get("notes") or "").strip()
    if not existing:
        return marker
    return marker + "\n\n" + existing


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

    Returns (stored_file_name, file_hash). Every check -- type, empty, size,
    duplicate -- runs *before* anything is written to disk, so a rejected
    upload leaves no file behind and no database record is created.
    """
    if not storage.allowed_file(document_file.filename):
        raise HTTPException(status_code=400, detail="File must be a PDF, PNG, or JPG.")

    # One byte past the limit is enough to know it is over, without holding
    # the rest of an oversized file in memory.
    contents = document_file.file.read(storage.MAX_UPLOAD_BYTES + 1)
    if not contents:
        raise HTTPException(
            status_code=400,
            detail=f'"{document_file.filename}" is empty (0 bytes). Please choose the actual file.',
        )
    if len(contents) > storage.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f'"{document_file.filename}" is larger than the {storage.MAX_UPLOAD_LABEL} '
                "limit. Please upload a smaller or compressed copy."
            ),
        )

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
    _check_document_date(document_date)
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
        # Only a redacted copy carries this, and only the save-redacted-copy
        # endpoint sets it. An ordinary upload is nothing's copy, recorded
        # here rather than left absent for the same reason as the two above.
        "redacted_from_id": None,
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
    except Exception:
        # Any other failed insert leaves no record to point at the file, so
        # the file goes too -- as the update path already does.
        storage.remove_stored_file(stored_file_name)
        raise

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


@router.get("/categories", response_model=List[str])
def list_document_categories(documents: Collection = Depends(get_documents)):
    """Every category in use, for the log's category filter and the forms.

    Read from the data, like the types above, and for a stronger reason: a
    category is free text that every form asks for, so the only list that can
    be right is the one already written. A fixed list would be wrong the first
    time anyone captured something it did not anticipate.

    This is what keeps the vocabulary from drifting. The filter matches a
    category exactly, so "ERP Implementation" and "ERP implementation" are two
    different filters over what people meant as one heading; offering what has
    been used already is how the second one stops being typed. Nothing is
    renamed or rejected here -- every value stays exactly as stored.
    """
    values = documents.distinct("category")
    return sorted({v for v in values if isinstance(v, str) and v.strip()}, key=str.lower)


@router.get("/", response_model=schemas.DocumentPage)
def list_documents(
    document_type: Optional[str] = None,
    category: Optional[str] = None,
    project_id: Optional[str] = None,
    q: Optional[str] = None,
    sort: str = DEFAULT_SORT,
    page: int = Query(default=1, ge=1, le=MAX_PAGE),
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
    if project_id:
        query["project_id"] = project_id
    if q:
        # The MongoDB spelling of ILIKE '%q%': a case-insensitive substring
        # match. re.escape matters here, because without it a search for
        # "(2)" or "a+b" would be read as a regex and match nonsense.
        #
        # Covers every free-text field worth finding an entry by: who it is
        # for and what it references (client/project/reference, the original
        # three), plus what kind of document it is and any note someone left
        # on it. Not contract_value or the dates -- those are compared as
        # amounts and days elsewhere, not read as text -- and not file_name,
        # which is what the owner fields and the type/category already answer
        # "what is this" for.
        pattern = re.compile(re.escape(q), re.IGNORECASE)
        query["$or"] = [
            {"client_name": pattern},
            {"project_title": pattern},
            {"reference_number": pattern},
            {"document_type": pattern},
            {"category": pattern},
            {"notes": pattern},
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
    # the right Content-Type (application/pdf, image/png, image/jpeg) â€” which
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


@router.post(
    "/{document_id}/redacted-copy/save",
    response_model=schemas.DocumentMongoOut,
    status_code=201,
)
def save_redacted_copy(
    document_id: str,
    request: schemas.RedactionRequest,
    documents: Collection = Depends(get_documents),
):
    """Log the redacted copy as its own entry, beside the untouched original.

    The one endpoint in this section that writes anything -- and what it
    writes is new. The original record is only read: its stored file is opened
    for reading by redaction.redact(), and no update_one, no replace and no
    storage call ever names it. Afterwards it previews, downloads and hashes
    exactly as it did before, because not one byte of it was touched.

    What is created is a second document: a new file under a new UUID, and a
    new row carrying the original's metadata, a file name ending `_redacted`,
    and `redacted_from_id` pointing back at the original.

    Order matters, and it is the order every upload in this module already
    uses: generate, hash, check for a duplicate, *then* write the file, then
    insert. A rejected copy leaves nothing on disk, and a failed insert takes
    the file it wrote back out -- so there is no state in which a copy exists
    without an entry for it, and the caller is never told it was saved unless
    both landed.

    The copy is not attached to the original's project, employee or tender.
    Doing that would mean writing to the owner's own document_ids -- modifying
    a record this operation has no business modifying -- and would put a
    second file into an evidence tab nobody asked to change. The copy lands in
    the log, and attaching it anywhere stays a decision someone makes.
    """
    record = _redaction_source(document_id, documents)
    areas = [
        redaction.Area(page=a.page, x=a.x, y=a.y, width=a.width, height=a.height)
        for a in request.areas
    ]

    source_stored_name = record["stored_file_name"]
    # Reads the original and returns fresh bytes. Nothing in redaction.py
    # writes to the uploads folder; this is the same call the download endpoint
    # above makes, and it is as read-only here as it is there.
    contents, _media_type = redaction.redact(source_stored_name, areas)

    copy_file_name = redaction.redacted_download_name(
        record.get("file_name"), source_stored_name
    )
    file_hash = hashlib.sha256(contents).hexdigest()
    # Before anything is written, as _save_upload does for an ordinary upload.
    # Saving the same selection twice is the duplicate it looks like, and is
    # refused with the message that names the entry already holding it.
    _check_duplicate(documents, file_hash)

    # The extension comes from the *stored* name, which is always one of the
    # four allowed ones. The display name cannot be trusted for it: a record
    # whose file_name has no extension would make `_redacted` the whole name.
    stored_file_name = storage.write_upload(contents, source_stored_name)

    copy = {
        # The original's own description of what this document is. Copied so
        # the entry stands on its own in the log, exactly as an upload does.
        "document_type": record.get("document_type"),
        "category": record.get("category"),
        "client_name": record.get("client_name"),
        "reference_number": record.get("reference_number"),
        "project_title": record.get("project_title"),
        "contract_value": record.get("contract_value"),
        "document_date": record.get("document_date"),
        "department": record.get("department"),
        "submitted_by": record.get("submitted_by"),
        "notes": _redacted_copy_notes(record),
        "file_name": copy_file_name,
        "stored_file_name": stored_file_name,
        "file_hash": file_hash,
        # Nobody's: see the docstring. The original keeps whichever of these
        # it had, and is not read for them here.
        "project_id": None,
        "employee_id": None,
        "tender_id": None,
        "redacted_from_id": str(record["_id"]),
        "created_at": _now_utc_ms(),
    }

    try:
        result = documents.insert_one(copy)
    except DuplicateKeyError:
        # Two saves of the same selection racing each other. Same handling as
        # a duplicate upload: take this request's file back off disk and
        # report the entry that already holds it.
        storage.remove_stored_file(stored_file_name)
        raise _duplicate_error(documents, file_hash)
    except Exception:
        # Anything else -- the database unreachable mid-request, a write
        # refused. The file this request wrote has no entry pointing at it and
        # never will, so it goes; the error travels on untouched, and the
        # caller is told the save failed rather than that it worked.
        storage.remove_stored_file(stored_file_name)
        raise

    copy["_id"] = result.inserted_id
    return _serialise(copy)


def _set_nested_pointer_fields(
    document_id: str,
    values: Dict[str, Any],
    employees: Collection,
    tenders: Collection,
) -> None:
    """Write `values` into every nested record that points at this document.

    A CV, a certification, a cost item and a submitted certificate each carry
    their own copy of the document's file_name, written when the record is
    saved and read back by every screen that shows it -- the document itself
    is never looked up for it. So anything that changes what the document is
    called has to be written through to each copy, or the employee and tender
    screens go on showing a name the log no longer has. A delete clears the
    copies (document_id and file_name both to None); replacing the file
    renames them.

    The positional filter updates every matching element, not just the first:
    one employee can hold two CV versions of the same file only by pointing
    both at it, and both have to change together. update_many because more
    than one record may reference the document.
    """
    for collection, fields in (
        (tenders, ("cost_items", "certificates")),
        (employees, ("cvs", "certifications")),
    ):
        for field in fields:
            changes = {f"{field}.$[item].{key}": value for key, value in values.items()}
            changes["updated_at"] = _now_utc_ms()
            collection.update_many(
                {f"{field}.document_id": document_id},
                {"$set": changes},
                array_filters=[{"item.document_id": document_id}],
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
    employees: Collection = Depends(get_employee_records),
    tenders: Collection = Depends(get_tender_records),
):
    """Update metadata, and optionally replace the stored file.

    Sending no `document_file` keeps whatever file the record already has.
    Sending one replaces it: the new file is written first, and the old one
    is only deleted once the database write has succeeded, so a failure at
    any point leaves the record and its file still matching each other.

    A replace also renames the copies of file_name that CVs, certifications,
    cost items and submitted certificates hold (see
    _set_nested_pointer_fields). The drawers that upload through those
    records already write the new name back themselves; this covers a
    replace made from the Documents log or an attached-documents drawer,
    which used to leave the employee and tender screens showing the old
    name -- and, because the preview picks PDF-or-image from that name,
    showing a broken image for a receipt whose type changed.
    """
    oid = _object_id(document_id)
    record = documents.find_one({"_id": oid})
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")
    _check_document_date(document_date)

    replacing =bool(document_file and document_file.filename)
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

    if replacing:
        # After the document's own write has landed, so the copies can never
        # name a file the document itself does not have.
        _set_nested_pointer_fields(
            document_id, {"file_name": document_file.filename}, employees, tenders
        )

    if replacing and previous_stored_file_name != new_stored_file_name:
        storage.remove_stored_file(previous_stored_file_name)

    return _serialise(updated)


_FROM_PROJECT = Query(
    None,
    description=(
        "The project the request is made from. On a delete, it is refused (409) "
        "if anything other than that project uses the document."
    ),
)
_FROM_TENDER = Query(
    None,
    description=(
        "The tender the request is made from: its document list, or with "
        "from_item one of its cost items or submitted certificates."
    ),
)
_FROM_EMPLOYEE = Query(
    None, description="The employee the request is made from; needs from_item."
)
_FROM_ITEM = Query(
    None,
    description=(
        "The id of the CV, certification, cost item or submitted certificate "
        "inside from_employee or from_tender."
    ),
)


@router.get("/{document_id}/usage", response_model=List[schemas.DocumentUsage])
def get_document_usage(
    document_id: str,
    from_project: Optional[str] = _FROM_PROJECT,
    from_tender: Optional[str] = _FROM_TENDER,
    from_employee: Optional[str] = _FROM_EMPLOYEE,
    from_item: Optional[str] = _FROM_ITEM,
    documents: Collection = Depends(get_documents),
    projects: Collection = Depends(get_project_records),
    employees: Collection = Depends(get_employee_records),
    tenders: Collection = Depends(get_tender_records),
):
    """Which projects, tenders and employees use this document -- what a
    permanent delete would take it away from. Read-only.

    Naming the record asking (from_project, from_tender, from_employee,
    from_item) leaves that record's own use out, so the answer is exactly
    what a delete from there would be refused over.
    """
    context = _owner_context(from_project, from_tender, from_employee, from_item)
    record = documents.find_one({"_id": _object_id(document_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")
    if context:
        return _other_uses(document_id, record, context, projects, tenders, employees)
    return _document_usage(document_id, record, projects, tenders, employees)


def _describe_uses(uses: List[Dict[str, Any]]) -> str:
    """'the tender "X" and the employee "Y"' -- for a refusal message."""
    names = [f'the {use["kind"]} "{use["label"]}"' for use in uses[:3]]
    if len(uses) > 3:
        names.append(f"{len(uses) - 3} more")
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    from_project: Optional[str] = _FROM_PROJECT,
    from_tender: Optional[str] = _FROM_TENDER,
    from_employee: Optional[str] = _FROM_EMPLOYEE,
    from_item: Optional[str] = _FROM_ITEM,
    documents: Collection = Depends(get_documents),
    projects: Collection = Depends(get_project_records),
    employees: Collection = Depends(get_employee_records),
    tenders: Collection = Depends(get_tender_records),
):
    """Delete the record, its references, then its stored file.

    Without an owner -- the document log's own delete -- this removes the
    document from everything that uses it, as it always has. Named from a
    project, a tender, or a CV, certification, cost item or submitted
    certificate (see _other_uses), the delete only goes ahead when that is
    the document's sole user; otherwise it is refused with a 409 before
    anything is written, and the caller should drop only its own reference.
    The check is made here, at the moment of the delete, whatever the screen
    asking was shown earlier.

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
    context = _owner_context(from_project, from_tender, from_employee, from_item)
    if context:
        existing = documents.find_one({"_id": _object_id(document_id)})
        if not existing:
            raise HTTPException(status_code=404, detail="Document not found")
        others = _other_uses(document_id, existing, context, projects, tenders, employees)
        if others:
            if context["item"]:
                advice = (
                    "here. Remove only this record instead; the file stays "
                    "available to them."
                )
            else:
                where = _OWNER_NOUN[context["kind"]]
                advice = f"from {where}. Remove it from {where} instead."
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This document is also used by {_describe_uses(others)}, so it "
                    f"cannot be deleted {advice}"
                ),
            )

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
    # so they are reached from here and not through document_ids. The same
    # write a replace makes to rename the copies, here clearing them.
    _set_nested_pointer_fields(
        document_id, {"document_id": None, "file_name": None}, employees, tenders
    )

    storage.remove_stored_file(record.get("stored_file_name"))
    return Response(status_code=204)

