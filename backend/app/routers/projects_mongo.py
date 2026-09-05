"""Project endpoints backed by MongoDB.

Projects are the records a tender submission cites as past evidence: what the
work was, who it was for, and which documents prove it happened. The documents
themselves stay in the document log (routers/documents_mongo.py), shared with
every other area of the system; a project only holds their ids.

This replaces the browser-local store the Projects screens used while there
was no endpoint. The list endpoint therefore has to do everything that store
did in JavaScript — search, filter, sort, page — because the screen only ever
receives one page and cannot sort what it has not been sent.
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
from ..mongodb import get_documents, get_project_records

router = APIRouter(prefix="/projects", tags=["projects"])

# The columns the list screen can sort by. Anything else falls back to the
# default rather than erroring: a stale bookmark should still load the list.
SORTABLE = {
    "title",
    "client_name",
    "reference_number",
    "contract_value",
    "status",
    "department",
    "start_date",
    "end_date",
    "created_at",
    "updated_at",
}
DEFAULT_SORT = "-updated_at"

# Fields held as real BSON dates, which sort on their own without a computed
# key. Everything else in SORTABLE is a string.
DATE_FIELDS = {"created_at", "updated_at"}

# A page size ceiling, so a hand-edited URL cannot ask for the whole
# collection in one response.
MAX_PAGE_SIZE = 200


def _now_utc_ms() -> datetime:
    """Current UTC time at BSON's precision (milliseconds).

    Same rounding as the documents router, and for the same reason: without
    it the timestamp in a create response would not match the one every later
    read returns.
    """
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _object_id(project_id: str) -> ObjectId:
    """Parse a path id, or 404.

    A malformed id is a request for something that cannot exist, so it gets
    the same answer as a well-formed id that isn't there, not a 422.
    """
    try:
        return ObjectId(project_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Project not found")


def _serialise(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Mongo document -> the shape schemas.ProjectOut expects.

    Underscore-prefixed keys are dropped: _id becomes id, and the sort keys
    the list pipeline computes are scaffolding the client never sees.
    """
    out = {key: value for key, value in doc.items() if not key.startswith("_")}
    out["id"] = str(doc["_id"])
    out.setdefault("tags", [])
    out.setdefault("document_ids", [])
    return out


def _exact_ci(value: str) -> re.Pattern:
    """Anchored case-insensitive match for one whole value.

    re.escape matters: a client named "Smith & Co. (Pvt)" would otherwise be
    read as a regex and match nothing.
    """
    return re.compile(f"^{re.escape(value)}$", re.IGNORECASE)


def _held_document_clauses(documents: Collection, has: List[str]) -> List[Dict[str, Any]]:
    """One clause per document type the project must hold.

    Projects and documents are separate collections, so this join is done
    here rather than in the query: for each wanted type, collect the ids of
    the documents that have it, and require the project to reference at
    least one of them. All the requested types must be present, which is what
    "Has document: LOI, Work Order" means on the screen.

    An empty id list is left in place deliberately — `$in: []` matches
    nothing, which is the right answer when no document of that type exists
    anywhere.
    """
    clauses = []
    for wanted in has:
        ids = [
            str(doc["_id"])
            for doc in documents.find({"document_type": wanted}, {"_id": 1})
        ]
        clauses.append({"document_ids": {"$in": ids}})
    return clauses


def _build_query(
    documents: Collection,
    q: Optional[str],
    client: Optional[str],
    status: Optional[str],
    tags: List[str],
    tag_mode: str,
    has: List[str],
) -> Dict[str, Any]:
    """The filter half of the list endpoint."""
    query: Dict[str, Any] = {}
    clauses: List[Dict[str, Any]] = []

    if q and q.strip():
        # Case-insensitive substring match, over the same three fields the
        # search box has always covered. Escaped, so "(2)" is looked for
        # literally rather than compiled as a group.
        pattern = re.compile(re.escape(q.strip()), re.IGNORECASE)
        clauses.append(
            {
                "$or": [
                    {"title": pattern},
                    {"client_name": pattern},
                    {"reference_number": pattern},
                ]
            }
        )

    if client:
        query["client_name"] = client
    if status:
        query["status"] = status

    if tags:
        # Tags are stored with whatever capitalisation they were typed in, so
        # matching is case-insensitive on both sides.
        patterns = [_exact_ci(tag) for tag in tags]
        if tag_mode == "all":
            clauses.extend({"tags": pattern} for pattern in patterns)
        else:
            clauses.append({"tags": {"$in": patterns}})

    clauses.extend(_held_document_clauses(documents, has))

    if clauses:
        query["$and"] = clauses
    return query


def _sort_stages(sort: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The (stages before the sort, sort spec) for one sort option.

    Three rules, carried over from the store this replaces:

      * Blanks sort last whichever way the arrow points. That has to be
        decided before the direction is applied, or reversing the order
        floats the empty rows to the top.
      * A column of numbers sorts as numbers, or "12" lands before "7".
        Only contract_value is treated this way, and commas and spaces are
        stripped first, so "5,00,000" is read as 500000 — something the
        browser store could not do, because Number("5,00,000") is NaN.
      * Text sorts case-insensitively.

    `_id` breaks every tie. ObjectIds are time-ordered, so two projects saved
    in the same millisecond still come back in a stable order rather than an
    arbitrary one — which is what keeps paging from repeating or skipping a
    row between requests.
    """
    descending = sort.startswith("-")
    key = sort[1:] if descending else sort
    if key not in SORTABLE:
        descending, key = True, "updated_at"
    direction = -1 if descending else 1

    if key in DATE_FIELDS:
        # Always set, and already comparable.
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

    if key == "contract_value":
        digits = {"$replaceAll": {"input": {"$toString": value}, "find": ",", "replacement": ""}}
        digits = {"$replaceAll": {"input": digits, "find": " ", "replacement": ""}}
        # onError/onNull leave anything unparseable as null.
        stages.append(
            {
                "$addFields": {
                    "_number": {
                        "$convert": {
                            "input": digits,
                            "to": "double",
                            "onError": None,
                            "onNull": None,
                        }
                    }
                }
            }
        )
        # A separate stage, because fields added in one $addFields cannot
        # reference each other. Values that did not parse sort after the ones
        # that did, in both directions, the same way blanks do.
        stages.append({"$addFields": {"_unparsed": {"$cond": [{"$eq": ["$_number", None]}, 1, 0]}}})
        spec["_unparsed"] = 1
        spec["_number"] = direction

    spec["_text"] = direction
    spec["_id"] = DESCENDING
    return stages, spec


# --------------------------------------------------------------------------
# Vocabulary endpoints.
#
# Declared before /{project_id}: FastAPI matches routes in declaration order,
# and a path parameter would otherwise swallow "clients" and "tags".
# --------------------------------------------------------------------------


@router.get("/clients", response_model=List[str])
def list_clients(projects: Collection = Depends(get_project_records)):
    """Distinct client names, for the list screen's dropdown."""
    names = [name for name in projects.distinct("client_name") if name]
    return sorted(names, key=str.lower)


@router.get("/tags", response_model=List[str])
def list_tags(projects: Collection = Depends(get_project_records)):
    """The tag vocabulary, built from the tags already in use.

    There is no separate tag list to maintain: a tag exists exactly as long
    as some project still carries it.
    """
    tags = [tag for tag in projects.distinct("tags") if tag]
    return sorted(tags, key=str.lower)


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------


@router.get("/", response_model=schemas.ProjectPage)
def list_projects(
    q: Optional[str] = None,
    client: Optional[str] = None,
    status: Optional[str] = None,
    tags: List[str] = Query(default=[]),
    tag_mode: str = "any",
    has: List[str] = Query(default=[]),
    sort: str = DEFAULT_SORT,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=MAX_PAGE_SIZE),
    projects: Collection = Depends(get_project_records),
    documents: Collection = Depends(get_documents),
):
    """One page of projects, filtered and sorted.

    The count is taken against the same filter as the page, so the pager and
    the rows can never disagree about how many results there are.
    """
    query = _build_query(documents, q, client, status, tags, tag_mode, has)
    key_stages, sort_spec = _sort_stages(sort)

    pipeline: List[Dict[str, Any]] = [{"$match": query}, *key_stages]
    pipeline.append({"$sort": sort_spec})
    pipeline.append({"$skip": (page - 1) * page_size})
    pipeline.append({"$limit": page_size})

    # _serialise drops the computed keys on the way out.
    items = [_serialise(doc) for doc in projects.aggregate(pipeline)]
    return {
        "items": items,
        "total": projects.count_documents(query),
        "page": page,
        "page_size": page_size,
    }


@router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: str, projects: Collection = Depends(get_project_records)):
    record = projects.find_one({"_id": _object_id(project_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Project not found")
    return _serialise(record)


@router.post("/", response_model=schemas.ProjectOut, status_code=201)
def create_project(
    payload: schemas.ProjectIn,
    projects: Collection = Depends(get_project_records),
):
    now = _now_utc_ms()
    record = payload.model_dump()
    record["document_ids"] = []
    record["created_at"] = now
    record["updated_at"] = now

    result = projects.insert_one(record)
    record["_id"] = result.inserted_id
    return _serialise(record)


@router.put("/{project_id}", response_model=schemas.ProjectOut)
def update_project(
    project_id: str,
    payload: schemas.ProjectIn,
    projects: Collection = Depends(get_project_records),
):
    """Replace the editable fields.

    document_ids is not among them. Attaching and detaching happen through
    the two endpoints below, so a slow form submitted from a stale page
    cannot silently drop a document someone attached in the meantime.
    """
    changes = payload.model_dump()
    changes["updated_at"] = _now_utc_ms()

    updated = projects.find_one_and_update(
        {"_id": _object_id(project_id)},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _serialise(updated)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, projects: Collection = Depends(get_project_records)):
    """Delete the project, leaving its documents in the document log.

    That is what the confirmation dialog promises, and it is the right way
    round: a document is evidence in its own right and may be cited by a
    tender that has nothing to do with this project.
    """
    record = projects.find_one_and_delete({"_id": _object_id(project_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Project not found")
    return Response(status_code=204)


@router.post("/{project_id}/documents/{document_id}", response_model=schemas.ProjectOut)
def link_document(
    project_id: str,
    document_id: str,
    projects: Collection = Depends(get_project_records),
    documents: Collection = Depends(get_documents),
):
    """Attach an existing document to this project.

    $addToSet rather than $push, so attaching the same document twice is a
    no-op instead of a duplicate entry.
    """
    oid = _object_id(project_id)
    if not documents.find_one({"_id": _object_id(document_id)}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Document not found")

    updated = projects.find_one_and_update(
        {"_id": oid},
        {"$addToSet": {"document_ids": document_id}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _serialise(updated)


@router.delete("/{project_id}/documents/{document_id}", response_model=schemas.ProjectOut)
def unlink_document(
    project_id: str,
    document_id: str,
    projects: Collection = Depends(get_project_records),
):
    """Detach a document from this project.

    The document is deliberately not looked up. Deleting an attached document
    detaches it as a second step, by which point it is already gone — and a
    link left pointing at a deleted document is exactly what this call exists
    to clear.
    """
    updated = projects.find_one_and_update(
        {"_id": _object_id(project_id)},
        {"$pull": {"document_ids": document_id}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _serialise(updated)
