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
from ..mongodb import get_documents, get_project_records, get_tender_records
from .documents_mongo import serialise_document

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


def _serialise(
    doc: Dict[str, Any], document_types: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Mongo document -> the shape schemas.ProjectOut expects.

    Underscore-prefixed keys are dropped: _id becomes id, and the sort keys
    the list pipeline computes are scaffolding the client never sees.

    document_types is resolved by the caller, which knows whether it is
    answering for one project or a page of them and can do it in one query
    either way.
    """
    out = {key: value for key, value in doc.items() if not key.startswith("_")}
    out["id"] = str(doc["_id"])
    out.setdefault("tags", [])
    out.setdefault("document_ids", [])
    out["document_types"] = list(document_types or [])
    return out


def _document_oids(document_ids: List[str]) -> List[ObjectId]:
    """The ids that are parseable, as ObjectIds.

    An unparseable entry is skipped rather than raising: it can only be
    junk left by something outside this API, and one bad string must not
    make a project unreadable.
    """
    oids = []
    for document_id in document_ids or []:
        try:
            oids.append(ObjectId(document_id))
        except (InvalidId, TypeError):
            continue
    return oids


def _types_by_project(
    documents: Collection, records: List[Dict[str, Any]]
) -> Dict[str, List[str]]:
    """{project id: the distinct document types it holds}, in one query.

    The list screen draws an evidence pill per type, and used to download the
    entire document log to work that out for itself. This answers the same
    question for just the projects on the page.

    Resolved through document_ids rather than through documents.project_id:
    that array is what every writer maintains and what the migration treated
    as the source of truth, so it stays right even for a document the
    migration deliberately left unassigned.
    """
    wanted: List[str] = []
    for record in records:
        wanted.extend(record.get("document_ids") or [])
    if not wanted:
        return {}

    type_by_id = {
        str(doc["_id"]): doc.get("document_type")
        for doc in documents.find(
            {"_id": {"$in": _document_oids(list(set(wanted)))}},
            {"_id": 1, "document_type": 1},
        )
    }

    resolved: Dict[str, List[str]] = {}
    for record in records:
        seen: List[str] = []
        for document_id in record.get("document_ids") or []:
            document_type = type_by_id.get(document_id)
            # A type is listed once however many documents carry it: the strip
            # answers whether the project holds one, not how many. Holding
            # three Work Orders is normal -- they are issued separately.
            if document_type and document_type not in seen:
                seen.append(document_type)
        resolved[str(record["_id"])] = seen
    return resolved


# The tenders router lists the projects a tender cites and needs the same
# shape back. Exported rather than duplicated, so the two can never drift.
serialise_project = _serialise
types_by_project = _types_by_project


def _owner_project(
    projects: Collection, project_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """The project a document says it belongs to, if that project exists.

    None covers three cases that all mean the same thing here -- the
    document names no project, names an unparseable one, or names one that
    has since been deleted -- so a stale claim cannot make a document
    permanently unattachable.
    """
    if not project_id:
        return None
    try:
        oid = ObjectId(project_id)
    except (InvalidId, TypeError):
        return None
    return projects.find_one({"_id": oid}, {"_id": 1, "title": 1})


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

    # Not named `page`: that is the page *number* parameter above, and
    # shadowing it put a list of records into the response's page field.
    records = list(projects.aggregate(pipeline))
    # One extra query for the whole page, which is what lets the list screen
    # draw its evidence pills without downloading the document log.
    types = _types_by_project(documents, records)
    # _serialise drops the computed sort keys on the way out.
    items = [_serialise(doc, types.get(str(doc["_id"]))) for doc in records]
    return {
        "items": items,
        "total": projects.count_documents(query),
        "page": page,
        "page_size": page_size,
    }


@router.get("/{project_id}/documents", response_model=List[schemas.DocumentMongoOut])
def list_project_documents(
    project_id: str,
    projects: Collection = Depends(get_project_records),
    documents: Collection = Depends(get_documents),
):
    """The documents belonging to one project, newest first.

    This is what the Documents tab reads. It used to fetch the whole document
    log and filter it in the browser, which meant every project screen paid
    for every CV, tender receipt and unattached entry in the system.

    Both representations are accepted: a document counts as this project's if
    it carries the project_id *or* if the project lists it. During the
    transition either one alone is enough, so a document the migration left
    unassigned -- one two projects both claim, say -- still appears under the
    project that references it, and a document stamped by a create whose
    follow-up write failed still appears too.

    Ordering matches the document log's own: newest first, with _id breaking
    ties. The tab groups by type on top of that, so documents of the same
    type keep their newest-first order inside their group.
    """
    record = projects.find_one({"_id": _object_id(project_id)}, {"document_ids": 1})
    if not record:
        raise HTTPException(status_code=404, detail="Project not found")

    query = {
        "$or": [
            {"project_id": project_id},
            {"_id": {"$in": _document_oids(record.get("document_ids") or [])}},
        ]
    }
    cursor = documents.find(query).sort([("created_at", DESCENDING), ("_id", DESCENDING)])
    return [serialise_document(doc) for doc in cursor]


@router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(
    project_id: str,
    projects: Collection = Depends(get_project_records),
    documents: Collection = Depends(get_documents),
):
    record = projects.find_one({"_id": _object_id(project_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Project not found")
    return _serialise(record, _types_by_project(documents, [record]).get(project_id))


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
    # A new project holds nothing yet, so the derived list is empty.
    return _serialise(record)


@router.put("/{project_id}", response_model=schemas.ProjectOut)
def update_project(
    project_id: str,
    payload: schemas.ProjectIn,
    projects: Collection = Depends(get_project_records),
    documents: Collection = Depends(get_documents),
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
    return _serialise(updated, _types_by_project(documents, [updated]).get(project_id))


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    projects: Collection = Depends(get_project_records),
    documents: Collection = Depends(get_documents),
    tenders: Collection = Depends(get_tender_records),
):
    """Delete the project, leaving its documents in the document log.

    That is what the confirmation dialog promises, and it is the right way
    round: a document is evidence in its own right and may be cited by a
    tender that has nothing to do with this project.

    What does not survive is the pointer back: a document left carrying the
    id of a project that no longer exists would be claimed by nothing and
    findable under a project that cannot be opened. The documents stay, their
    files stay, and they simply belong to no project again.
    """
    record = projects.find_one_and_delete({"_id": _object_id(project_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Project not found")

    documents.update_many(
        {"project_id": project_id}, {"$set": {"project_id": None}}
    )

    # A tender citing this project as past experience is cleared of it too,
    # for the same reason: a citation of a project that cannot be opened is
    # evidence of nothing. Only the citation goes -- the tender itself, its
    # own documents and everything else about it are untouched.
    tenders.update_many(
        {"cited_project_ids": project_id},
        {
            "$pull": {"cited_project_ids": project_id},
            "$set": {"updated_at": _now_utc_ms()},
        },
    )
    return Response(status_code=204)


@router.post("/{project_id}/documents/{document_id}", response_model=schemas.ProjectOut)
def link_document(
    project_id: str,
    document_id: str,
    projects: Collection = Depends(get_project_records),
    documents: Collection = Depends(get_documents),
):
    """Attach an existing document to this project.

    Idempotent. $addToSet rather than $push, so attaching the same document
    twice is a no-op instead of a duplicate entry, and the project_id write
    below is a no-op once it already names this project. Note that this is
    about the *same document* twice: two different documents of the same
    type are a normal thing for a project to hold -- they are issued at
    different times -- and nothing here or in the indexes prevents it.

    A document another live project already holds is refused with a 409
    naming that project, rather than being quietly moved. Reassigning it
    would take the document out of a project that is still showing it, and
    nothing would say so. Detaching it there first is the way to move it;
    the alternative is to upload a copy, which is a different document.

    Nothing is written before that check, so a refused attach changes
    neither representation.
    """
    oid = _object_id(project_id)
    document_oid = _object_id(document_id)
    document = documents.find_one(
        {"_id": document_oid}, {"_id": 1, "project_id": 1}
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    # Before the ownership test below, so naming a project that does not exist
    # is answered "Project not found" rather than refused as a conflict with a
    # project the document was never going to join.
    if not projects.find_one({"_id": oid}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Project not found")

    # Claims this project may overwrite: unset, absent, or already its own.
    free_claims: List[Dict[str, Any]] = [
        {"project_id": None},
        {"project_id": {"$exists": False}},
        {"project_id": project_id},
    ]
    claimed_by = document.get("project_id")
    if claimed_by and claimed_by != project_id:
        owner = _owner_project(projects, claimed_by)
        if owner is not None:
            title = (owner.get("title") or "").strip() or "another project"
            raise HTTPException(
                status_code=409,
                detail=(
                    f'This document is already attached to "{title}". '
                    "Detach it there first, or upload a copy to this project."
                ),
            )
        # The project it names is gone, so nothing is really holding it.
        free_claims.append({"project_id": claimed_by})

    updated = projects.find_one_and_update(
        {"_id": oid},
        {"$addToSet": {"document_ids": document_id}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Conditional rather than a plain $set: between the read above and this
    # write another request could have claimed the document, and the filter
    # is what stops this one taking it anyway.
    documents.update_one(
        {"_id": document_oid, "$or": free_claims},
        {"$set": {"project_id": project_id}},
    )
    return _serialise(updated, _types_by_project(documents, [updated]).get(project_id))


@router.delete("/{project_id}/documents/{document_id}", response_model=schemas.ProjectOut)
def unlink_document(
    project_id: str,
    document_id: str,
    projects: Collection = Depends(get_project_records),
    documents: Collection = Depends(get_documents),
):
    """Detach a document from this project. The document itself is kept.

    The document is deliberately not looked up first. Deleting an attached
    document detaches it as a second step, by which point it is already gone
    — and a link left pointing at a deleted document is exactly what this
    call exists to clear.

    The document's project_id is cleared only where it names *this* project.
    A document another project holds is not this call's to unclaim, and a
    document that is already gone simply matches nothing.
    """
    updated = projects.find_one_and_update(
        {"_id": _object_id(project_id)},
        {"$pull": {"document_ids": document_id}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        document_oid = ObjectId(document_id)
    except (InvalidId, TypeError):
        document_oid = None
    if document_oid is not None:
        documents.update_one(
            {"_id": document_oid, "project_id": project_id},
            {"$set": {"project_id": None}},
        )
    return _serialise(updated, _types_by_project(documents, [updated]).get(project_id))
