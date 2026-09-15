"""Employee, CV and certification endpoints backed by MongoDB.

The counterpart of routers/projects_mongo.py for the CVs area, built to the
same pattern: this replaces a browser-local store, so the list endpoints do
everything that store did in JavaScript — search, filter, sort, page —
because a screen that receives one page cannot reorder what it was not sent.

Two shapes live here:

  * The employee, with its CVs and certifications embedded. A CV version or
    a certification has no meaning apart from its person, so they are nested
    records with their own sub-endpoints rather than a collection of their
    own. Uploaded files are NOT here — they go through the documents
    endpoints and each nested record keeps a document_id pointer.
  * The flattened certification index: every certification held by anyone,
    one row each, because "who holds a PMP?" is a tender-qualification
    question that should be one filter, not a walk through every profile.
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo import DESCENDING, ReturnDocument
from pymongo.collection import Collection

from .. import schemas
from ..mongodb import get_employee_records

router = APIRouter(prefix="/employees", tags=["employees"])

# The columns the employees list can sort by; anything else falls back to the
# default rather than erroring, so a stale bookmark still loads.
SORTABLE = {
    "full_name",
    "employee_code",
    "designation",
    "department",
    "total_experience_years",
    "vgil_experience_years",
    "highest_qualification",
    "date_of_joining",
    "career_start_date",
    "created_at",
    "updated_at",
}
DEFAULT_SORT = "-updated_at"
DATE_FIELDS = {"created_at", "updated_at"}

# Sorting by an experience figure is sorting by the date behind it, backwards:
# the earlier the start date, the more experience. Doing it this way rather
# than computing years per document keeps the sort on a plain stored field --
# it can use an index, it needs no aggregation operator, and it cannot
# disagree with the number `_serialise` puts on the screen, because both read
# the same date.
EXPERIENCE_SORT_SOURCE = {
    "total_experience_years": "career_start_date",
    "vgil_experience_years": "date_of_joining",
}

# The flattened index sorts on its own row shape. `name` ascending is the
# default the screen opens with.
CERT_SORTABLE = {"name", "issuing_body", "employee_name", "issue_date", "expiry_date", "status"}
CERT_DEFAULT_SORT = "name"

SEARCH_FIELDS = ["full_name", "designation", "highest_qualification", "employee_code", "key_skills"]
CERT_SEARCH_FIELDS = ["name", "issuing_body", "certificate_number", "employee_name"]

MAX_PAGE_SIZE = 200


def _now_utc_ms() -> datetime:
    """Current UTC time at BSON's precision (milliseconds), like the other
    routers, so a create response matches every later read."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _object_id(employee_id: str) -> ObjectId:
    """Parse a path id, or 404 — a malformed id gets the same answer as a
    well-formed one that isn't there."""
    try:
        return ObjectId(employee_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Employee not found")


# --------------------------------------------------------------------------
# Experience
#
# Two quantities, both derived from a date and neither ever stored:
#
#     career_start_date -> Total Professional Experience
#     date_of_joining   -> Experience with VGIL
#
# Computed on read, like a certificate's Valid/Expired status further down, and
# for the same reason: a stored number is wrong the morning after it is typed,
# and nobody goes back to correct it. Tenders ask both questions separately
# ("N years' experience", "a regular employee of the bidder for N years"), so
# answering one with the other is how a qualified person gets ruled out.
#
# DAYS_PER_YEAR is the mean Gregorian year, which absorbs leap years without
# any calendar arithmetic. The result is displayed to one decimal at most, so
# the error this could introduce is far below what shows.
# --------------------------------------------------------------------------

DAYS_PER_YEAR = 365.2425


def _years_since(value: Optional[str], today: str) -> Optional[float]:
    """Whole years and tenths from an ISO date until `today`.

    None when there is no date or it does not parse — which the screens show
    as a dash. That is the honest answer: "not recorded" is a different thing
    from "no experience", and a 0.0 in a personnel table reads as the latter.

    A date in the future gives 0.0 rather than a negative: a joining date can
    legitimately be set ahead for someone starting next month, and they have
    no experience yet rather than a negative amount of it.
    """
    if not value or not str(value).strip():
        return None
    try:
        started = date.fromisoformat(str(value).strip())
        now = date.fromisoformat(today)
    except ValueError:
        return None
    days = (now - started).days
    if days <= 0:
        return 0.0
    return round(days / DAYS_PER_YEAR, 1)


def _serialise(doc: Dict[str, Any], today: Optional[str] = None) -> Dict[str, Any]:
    """Mongo document -> the shape schemas.EmployeeOut expects.

    This is where the two experience figures are worked out, because it is the
    one place every read passes through — the list, the detail page, and the
    response to a create or an update all come back through here, so none of
    them can drift from the others or go stale.
    """
    out = {key: value for key, value in doc.items() if not key.startswith("_")}
    out["id"] = str(doc["_id"])
    out.setdefault("cvs", [])
    out.setdefault("certifications", [])
    on = today or _today_utc()
    out["total_experience_years"] = _years_since(doc.get("career_start_date"), on)
    out["vgil_experience_years"] = _years_since(doc.get("date_of_joining"), on)
    return out


def _today_utc() -> str:
    """The default 'today' for certificate validity, as an ISO date."""
    return datetime.now(timezone.utc).date().isoformat()


def _date_years_ago(years: float, today: str) -> str:
    """The ISO date `years` before `today`.

    This is what turns an experience filter into a date comparison. "At least
    10 years' experience" is exactly "started on or before this date", so the
    bound is resolved once, here, and the query then compares stored ISO
    strings — no per-document arithmetic, and it works against an index.
    """
    return (date.fromisoformat(today) - timedelta(days=years * DAYS_PER_YEAR)).isoformat()


def _number_key(field: str) -> Dict[str, Any]:
    """An aggregation expression turning a string field into a double, or
    null where it doesn't parse. Commas and spaces are stripped first, the
    same treatment contract_value gets on projects."""
    digits = {
        "$replaceAll": {
            "input": {"$toString": {"$ifNull": [f"${field}", ""]}},
            "find": ",",
            "replacement": "",
        }
    }
    digits = {"$replaceAll": {"input": digits, "find": " ", "replacement": ""}}
    return {"$convert": {"input": digits, "to": "double", "onError": None, "onNull": None}}


def _sort_stages(
    sort: str, sortable: set, default_key: str, default_descending: bool, numeric_fields: set
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The (stages before the sort, sort spec) for one sort option.

    The same three rules as the projects list, carried over from the store
    both replaced: blanks last in both directions; a numeric column compared
    as numbers with unparseable values after the real ones; text compared
    case-insensitively. `_id` breaks every tie so paging never repeats or
    skips a row between requests.
    """
    descending = sort.startswith("-")
    key = sort[1:] if descending else sort
    if key not in sortable:
        key, descending = default_key, default_descending
    direction = -1 if descending else 1

    # An experience column is the date behind it, in the opposite direction:
    # "most experienced first" is "earliest start date first". Swapped here so
    # the rest of this function, and the blanks-last rule in particular, works
    # on the stored field exactly as it does for any other text column.
    if key in EXPERIENCE_SORT_SOURCE:
        key = EXPERIENCE_SORT_SOURCE[key]
        direction = -direction

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

    if key in numeric_fields:
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


def _search_clause(q: Optional[str], fields: List[str]) -> Optional[Dict[str, Any]]:
    """Case-insensitive substring match across `fields`, escaped so that a
    search for "(2)" is looked for literally rather than compiled."""
    if not q or not q.strip():
        return None
    pattern = re.compile(re.escape(q.strip()), re.IGNORECASE)
    return {"$or": [{field: pattern} for field in fields]}


def _parse_years(value: Optional[str]) -> Optional[float]:
    """A querystring experience bound as a number, or None when absent or
    unparseable — an unusable bound is ignored, not an error."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Vocabulary endpoints and the flattened certification index.
#
# All declared before /{employee_id}, which would otherwise swallow their
# path segment — same ordering rule as /projects/clients.
# --------------------------------------------------------------------------


@router.get("/designations", response_model=List[str])
def list_designations(employees: Collection = Depends(get_employee_records)):
    return sorted((d for d in employees.distinct("designation") if d), key=str.lower)


@router.get("/departments", response_model=List[str])
def list_departments(employees: Collection = Depends(get_employee_records)):
    return sorted((d for d in employees.distinct("department") if d), key=str.lower)


@router.get("/certification-names", response_model=List[str])
def list_certification_names(employees: Collection = Depends(get_employee_records)):
    """Every certificate name anyone holds — the vocabulary for the
    holds-certificate filter and the index's certificate dropdown."""
    return sorted((n for n in employees.distinct("certifications.name") if n), key=str.lower)


@router.get("/issuing-bodies", response_model=List[str])
def list_issuing_bodies(employees: Collection = Depends(get_employee_records)):
    return sorted((b for b in employees.distinct("certifications.issuing_body") if b), key=str.lower)


@router.get("/certifications", response_model=schemas.CertificationIndexPage)
def list_certification_index(
    q: Optional[str] = None,
    name: Optional[str] = None,
    issuing_body: Optional[str] = None,
    status: Optional[str] = None,
    today: Optional[str] = None,
    sort: str = CERT_DEFAULT_SORT,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=MAX_PAGE_SIZE),
    employees: Collection = Depends(get_employee_records),
):
    """Every certification held by anyone, one row each.

    Validity is computed against `today` rather than stored, so nothing goes
    stale at midnight. The caller may pass its own date (the browser's local
    day can differ from the server's); absent that, today in UTC.

    ISO dates compare correctly as strings, which is what makes the Expired
    test a plain string comparison.
    """
    on = today.strip() if today and today.strip() else _today_utc()

    # Paths here are relative to the unwound employee document, where the
    # certification still sits under its field name.
    expiry = {"$ifNull": ["$certifications.expiry_date", ""]}
    status_expr = {
        "$cond": [
            {"$eq": [expiry, ""]},
            "No expiry",
            {"$cond": [{"$lt": [expiry, on]}, "Expired", "Valid"]},
        ]
    }

    # One row per certification, rebuilt explicitly rather than merged, so
    # an employee field can never shadow a certification field.
    pipeline: List[Dict[str, Any]] = [
        {"$unwind": "$certifications"},
        {
            "$project": {
                "id": "$certifications.id",
                "name": "$certifications.name",
                "issuing_body": "$certifications.issuing_body",
                "certificate_number": "$certifications.certificate_number",
                "issue_date": "$certifications.issue_date",
                "expiry_date": "$certifications.expiry_date",
                "notes": "$certifications.notes",
                "document_id": "$certifications.document_id",
                "file_name": "$certifications.file_name",
                "created_at": "$certifications.created_at",
                "employee_name": "$full_name",
                "status": status_expr,
            }
        },
    ]

    match: Dict[str, Any] = {}
    search = _search_clause(q, CERT_SEARCH_FIELDS)
    if search:
        match["$and"] = [search]
    if name:
        match["name"] = name
    if issuing_body:
        match["issuing_body"] = issuing_body
    if status:
        match["status"] = status
    if match:
        pipeline.append({"$match": match})

    totals = list(employees.aggregate(pipeline + [{"$count": "n"}]))
    total = totals[0]["n"] if totals else 0

    key_stages, sort_spec = _sort_stages(
        sort, CERT_SORTABLE, CERT_DEFAULT_SORT, False, numeric_fields=set()
    )
    pipeline.extend(key_stages)
    pipeline.append({"$sort": sort_spec})
    pipeline.append({"$skip": (page - 1) * page_size})
    pipeline.append({"$limit": page_size})

    items = []
    for row in employees.aggregate(pipeline):
        # _id survived $project (it always does unless suppressed) — it is the
        # employee's, and becomes the profile link.
        row["employee_id"] = str(row.pop("_id"))
        items.append({k: v for k, v in row.items() if not k.startswith("_")})

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------
# Employees
# --------------------------------------------------------------------------


@router.get("/", response_model=schemas.EmployeePage)
def list_employees(
    q: Optional[str] = None,
    designation: Optional[str] = None,
    department: Optional[str] = None,
    certifications: List[str] = Query(default=[]),
    min_experience: Optional[str] = None,
    max_experience: Optional[str] = None,
    sort: str = DEFAULT_SORT,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=MAX_PAGE_SIZE),
    employees: Collection = Depends(get_employee_records),
):
    """One page of employees, filtered and sorted.

    The certifications filter answers "who holds ALL of these?" — the
    question a tender's personnel criteria asks — so it is one clause per
    name, not an $in.

    min_experience/max_experience bound *Total Professional Experience*, the
    figure a tender's personnel criteria quote. They are years, as before, and
    are resolved to start-date bounds below. Experience with VGIL is returned
    on every row and can be sorted on; it has no filter of its own yet.
    """
    query: Dict[str, Any] = {}
    clauses: List[Dict[str, Any]] = []

    search = _search_clause(q, SEARCH_FIELDS)
    if search:
        clauses.append(search)
    if designation:
        query["designation"] = designation
    if department:
        query["department"] = department
    clauses.extend({"certifications.name": held} for held in certifications)
    if clauses:
        query["$and"] = clauses

    pipeline: List[Dict[str, Any]] = [{"$match": query}]

    # Experience bounds become date bounds. "At least 10 years" is exactly
    # "started on or before the date 10 years ago", so the arithmetic happens
    # once here and the query is then a plain comparison of ISO strings —
    # which is the pattern the certification index already leans on, works
    # against an index, and cannot drift from the figure `_serialise` shows,
    # since both are reading the same stored date.
    #
    # The bounds invert: a *minimum* number of years is a *maximum* start
    # date. An employee with no date recorded matches neither, exactly as one
    # with no experience recorded did not satisfy a numeric range before.
    today = _today_utc()
    bounds: Dict[str, Any] = {}
    minimum = _parse_years(min_experience)
    maximum = _parse_years(max_experience)
    if minimum is not None:
        bounds["$lte"] = _date_years_ago(minimum, today)
    if maximum is not None:
        bounds["$gte"] = _date_years_ago(maximum, today)
    if bounds:
        pipeline.append(
            {"$match": {"career_start_date": {**bounds, "$nin": [None, ""]}}}
        )

    totals = list(employees.aggregate(pipeline + [{"$count": "n"}]))
    total = totals[0]["n"] if totals else 0

    key_stages, sort_spec = _sort_stages(
        sort, SORTABLE, "updated_at", True, numeric_fields=set()
    )
    pipeline.extend(key_stages)
    pipeline.append({"$sort": sort_spec})
    pipeline.append({"$skip": (page - 1) * page_size})
    pipeline.append({"$limit": page_size})

    # One `today` for the whole page, so two rows of the same list can never
    # be computed against different days.
    items = [_serialise(doc, today) for doc in employees.aggregate(pipeline)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{employee_id}", response_model=schemas.EmployeeOut)
def get_employee(employee_id: str, employees: Collection = Depends(get_employee_records)):
    record = employees.find_one({"_id": _object_id(employee_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Employee not found")
    return _serialise(record)


@router.post("/", response_model=schemas.EmployeeOut, status_code=201)
def create_employee(
    payload: schemas.EmployeeIn,
    employees: Collection = Depends(get_employee_records),
):
    now = _now_utc_ms()
    record = payload.model_dump()
    record["cvs"] = []
    record["certifications"] = []
    record["created_at"] = now
    record["updated_at"] = now

    result = employees.insert_one(record)
    record["_id"] = result.inserted_id
    return _serialise(record)


@router.put("/{employee_id}", response_model=schemas.EmployeeOut)
def update_employee(
    employee_id: str,
    payload: schemas.EmployeeIn,
    employees: Collection = Depends(get_employee_records),
):
    """Replace the profile fields.

    cvs and certifications are not among them: they change through their own
    endpoints below, so a profile form submitted from a stale page cannot
    silently drop a CV someone uploaded in the meantime — the same rule that
    keeps document_ids off a project's PUT.
    """
    changes = payload.model_dump()
    changes["updated_at"] = _now_utc_ms()

    updated = employees.find_one_and_update(
        {"_id": _object_id(employee_id)},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return _serialise(updated)


@router.delete("/{employee_id}", status_code=204)
def delete_employee(employee_id: str, employees: Collection = Depends(get_employee_records)):
    """Delete the employee, embedded CVs and certifications included.

    Uploaded files are deliberately left in the document log — that is what
    the confirmation dialog promises ("Files already uploaded to the server
    are not deleted"), and it matches how deleting a project behaves.
    """
    record = employees.find_one_and_delete({"_id": _object_id(employee_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Employee not found")


# --------------------------------------------------------------------------
# Nested records: CVs and certifications.
#
# One implementation each for add / edit / remove, specialised per field the
# way saveNested/removeNested were in the store this replaces. Every write
# also bumps the employee's updated_at: a new CV is a change to the person's
# record, and the list sorts by that.
# --------------------------------------------------------------------------


def _add_nested(
    employees: Collection, employee_id: str, field: str, values: Dict[str, Any]
) -> Dict[str, Any]:
    item = dict(values)
    item["id"] = str(ObjectId())  # time-ordered, like every other id here
    item["created_at"] = _now_utc_ms()

    updated = employees.find_one_and_update(
        {"_id": _object_id(employee_id)},
        {"$push": {field: item}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return _serialise(updated)


def _update_nested(
    employees: Collection, employee_id: str, field: str, item_id: str, values: Dict[str, Any]
) -> Dict[str, Any]:
    # The positional $ writes into the array element the query matched, field
    # by field, which is what preserves the item's id and created_at.
    changes = {f"{field}.$.{key}": value for key, value in values.items()}
    changes["updated_at"] = _now_utc_ms()

    updated = employees.find_one_and_update(
        {"_id": _object_id(employee_id), f"{field}.id": item_id},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        # The employee is missing, or the item is — either way the thing
        # being edited is not there.
        raise HTTPException(status_code=404, detail="Record not found")
    return _serialise(updated)


def _remove_nested(
    employees: Collection, employee_id: str, field: str, item_id: str
) -> Dict[str, Any]:
    # The query insists the item exists, so removing something already gone
    # is a 404 rather than a silent success — deleting twice should say so.
    updated = employees.find_one_and_update(
        {"_id": _object_id(employee_id), f"{field}.id": item_id},
        {"$pull": {field: {"id": item_id}}, "$set": {"updated_at": _now_utc_ms()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return _serialise(updated)


@router.post("/{employee_id}/cvs", response_model=schemas.EmployeeOut, status_code=201)
def add_cv(
    employee_id: str,
    payload: schemas.CvIn,
    employees: Collection = Depends(get_employee_records),
):
    return _add_nested(employees, employee_id, "cvs", payload.model_dump())


@router.put("/{employee_id}/cvs/{cv_id}", response_model=schemas.EmployeeOut)
def update_cv(
    employee_id: str,
    cv_id: str,
    payload: schemas.CvIn,
    employees: Collection = Depends(get_employee_records),
):
    return _update_nested(employees, employee_id, "cvs", cv_id, payload.model_dump())


@router.delete("/{employee_id}/cvs/{cv_id}", response_model=schemas.EmployeeOut)
def delete_cv(
    employee_id: str,
    cv_id: str,
    employees: Collection = Depends(get_employee_records),
):
    """Remove one CV version. Its uploaded file is the caller's to delete
    through the documents endpoint first, which is what the drawer does."""
    return _remove_nested(employees, employee_id, "cvs", cv_id)


@router.post("/{employee_id}/certifications", response_model=schemas.EmployeeOut, status_code=201)
def add_certification(
    employee_id: str,
    payload: schemas.CertificationIn,
    employees: Collection = Depends(get_employee_records),
):
    return _add_nested(employees, employee_id, "certifications", payload.model_dump())


@router.put("/{employee_id}/certifications/{certification_id}", response_model=schemas.EmployeeOut)
def update_certification(
    employee_id: str,
    certification_id: str,
    payload: schemas.CertificationIn,
    employees: Collection = Depends(get_employee_records),
):
    return _update_nested(
        employees, employee_id, "certifications", certification_id, payload.model_dump()
    )


@router.delete(
    "/{employee_id}/certifications/{certification_id}", response_model=schemas.EmployeeOut
)
def delete_certification(
    employee_id: str,
    certification_id: str,
    employees: Collection = Depends(get_employee_records),
):
    return _remove_nested(employees, employee_id, "certifications", certification_id)
