"""MongoDB connection setup.

Reads connection details from environment variables (see ../.env.example)
and hands out a collection handle. Nothing here connects at import time — MongoClient dials lazily on
first use — so the app still starts, and /docs still loads, on a machine
where MongoDB is not installed or not running yet.
"""

import os
from typing import Optional, Tuple
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import ConnectionFailure, OperationFailure, ServerSelectionTimeoutError

load_dotenv()

MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = os.getenv("MONGO_PORT", "27017")
MONGO_USER = os.getenv("MONGO_USER", "")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "")
MONGO_AUTH_SOURCE = os.getenv("MONGO_AUTH_SOURCE", "admin")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "doc_collection_dev")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION", "documents")
MONGO_PROJECTS_COLLECTION_NAME = os.getenv("MONGO_PROJECTS_COLLECTION", "projects")
MONGO_EMPLOYEES_COLLECTION_NAME = os.getenv("MONGO_EMPLOYEES_COLLECTION", "employees")
MONGO_TENDERS_COLLECTION_NAME = os.getenv("MONGO_TENDERS_COLLECTION", "tenders")

# Short by default: a local MongoDB either answers immediately or isn't there.
# Waiting pymongo's 30s default just makes an unreachable server look like a
# hung request instead of a clear error.
SERVER_SELECTION_TIMEOUT_MS = int(os.getenv("MONGO_TIMEOUT_MS", "3000"))


def _build_uri() -> str:
    """MONGO_URI wins if set (Atlas, replica sets, custom options).

    Otherwise a plain single-server URI is assembled from the host/port/
    credential parts, so a local install needs no URI knowledge at all.
    Credentials are percent-encoded — a password containing @ or / would
    otherwise corrupt the URI.
    """
    explicit = os.getenv("MONGO_URI", "").strip()
    if explicit:
        return explicit

    if MONGO_USER:
        credentials = f"{quote_plus(MONGO_USER)}:{quote_plus(MONGO_PASSWORD)}@"
        auth = f"?authSource={quote_plus(MONGO_AUTH_SOURCE)}"
    else:
        # A default local mongod has no authentication enabled.
        credentials = ""
        auth = ""
    return f"mongodb://{credentials}{MONGO_HOST}:{MONGO_PORT}/{auth}"


MONGO_URI = _build_uri()

_client: Optional[MongoClient] = None


def safe_uri() -> str:
    """MONGO_URI with any password blanked out, for logging."""
    if "@" not in MONGO_URI:
        return MONGO_URI
    scheme, _, rest = MONGO_URI.partition("://")
    creds, _, host = rest.rpartition("@")
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


def get_client() -> MongoClient:
    """Process-wide client. Pymongo pools connections internally, so this is
    created once and shared rather than opened per request."""
    global _client
    if _client is None:
        _client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
            tz_aware=True,
        )
    return _client


def get_database() -> Database:
    return get_client()[MONGO_DB_NAME]


def get_collection() -> Collection:
    return get_database()[MONGO_COLLECTION_NAME]


def get_projects_collection() -> Collection:
    """Projects live in their own collection, alongside documents.

    A project points at its documents by id rather than embedding them: the
    same document log is shared with the areas that are not projects, and a
    document has to keep working when the project referencing it is gone.
    """
    return get_database()[MONGO_PROJECTS_COLLECTION_NAME]


def get_documents() -> Collection:
    """FastAPI dependency, the MongoDB counterpart of get_db().

    Deliberately does not ping: a round trip on every request would be
    wasted work. An unreachable server surfaces when the query runs, and
    main.py turns that into a 503 with setup instructions.
    """
    return get_collection()


def get_project_records() -> Collection:
    """FastAPI dependency for the projects collection.

    Like get_documents(), this does not ping — an unreachable server surfaces
    when the query runs and main.py turns that into a 503.
    """
    return get_projects_collection()


def get_employees_collection() -> Collection:
    """Employees, each carrying its CVs and certifications inside itself.

    Unlike a project's documents, a CV version or a certification has no life
    of its own outside the person it belongs to — so they are embedded rather
    than given a collection, which is also how the frontend has always shaped
    them. Only the uploaded files live elsewhere: in the shared document log,
    pointed at by document_id.
    """
    return get_database()[MONGO_EMPLOYEES_COLLECTION_NAME]


def get_employee_records() -> Collection:
    """FastAPI dependency for the employees collection. See get_project_records."""
    return get_employees_collection()


def get_tenders_collection() -> Collection:
    """Tenders, each carrying its cost items and certificates inside itself,
    the way an employee carries its CVs — an EMD payment or a submitted
    certificate has no life apart from its tender. Attached documents are
    referenced by id, the way a project references its evidence, because the
    files live in the shared document log.
    """
    return get_database()[MONGO_TENDERS_COLLECTION_NAME]


def get_tender_records() -> Collection:
    """FastAPI dependency for the tenders collection. See get_project_records."""
    return get_tenders_collection()


def ping() -> Tuple[bool, Optional[str]]:
    """(reachable, error message). Never raises — callers use it to report
    status rather than to fail."""
    try:
        get_client().admin.command("ping")
        return True, None
    except (ServerSelectionTimeoutError, ConnectionFailure, OperationFailure) as exc:
        return False, str(exc).split("\n")[0]
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"{type(exc).__name__}: {exc}"


def ensure_indexes(collection: Optional[Collection] = None) -> None:
    """Create the indexes the document queries rely on.

    Idempotent — create_index() on an existing index is a no-op, so this is
    safe to run on every startup, which is where it is called from.
    """
    coll = collection if collection is not None else get_collection()

    # Duplicate detection looks this up on every upload. Unique + partial so
    # that the many records with no file (file_hash: null) don't collide with
    # each other, while two records can never hold the same file's bytes.
    coll.create_index(
        [("file_hash", ASCENDING)],
        name="ux_documents_file_hash",
        unique=True,
        partialFilterExpression={"file_hash": {"$type": "string"}},
    )
    # The entries table is always sorted newest-first.
    coll.create_index([("created_at", DESCENDING)], name="ix_documents_created_at")
    # The two dropdown filters on the list screen.
    coll.create_index([("document_type", ASCENDING)], name="ix_documents_document_type")
    coll.create_index([("category", ASCENDING)], name="ix_documents_category")


def ensure_project_indexes(collection: Optional[Collection] = None) -> None:
    """Create the indexes the project queries rely on.

    Kept separate from ensure_indexes() because the two collections are
    indexed for different questions, and because verify_mongo.py calls that
    one with a documents collection of its own. Idempotent, like its sibling.
    """
    coll = collection if collection is not None else get_projects_collection()

    # The list screen's default order, and the tiebreak every sort falls back
    # to.
    coll.create_index([("updated_at", DESCENDING)], name="ix_projects_updated_at")
    # The client dropdown and the status filter.
    coll.create_index([("client_name", ASCENDING)], name="ix_projects_client_name")
    coll.create_index([("status", ASCENDING)], name="ix_projects_status")
    # Multikey indexes: one entry per array element, which is what the tag
    # filter and the has-document filter both match against.
    coll.create_index([("tags", ASCENDING)], name="ix_projects_tags")
    coll.create_index([("document_ids", ASCENDING)], name="ix_projects_document_ids")


def ensure_employee_indexes(collection: Optional[Collection] = None) -> None:
    """Create the indexes the employee queries rely on.

    A separate function per collection, like its two siblings above, and
    idempotent for the same reason.
    """
    coll = collection if collection is not None else get_employees_collection()

    # The list screen's default order.
    coll.create_index([("updated_at", DESCENDING)], name="ix_employees_updated_at")
    # The two dropdown filters.
    coll.create_index([("designation", ASCENDING)], name="ix_employees_designation")
    coll.create_index([("department", ASCENDING)], name="ix_employees_department")
    # Multikey over the embedded certifications: the holds-certificate filter
    # and the flattened certification index both start from this.
    coll.create_index([("certifications.name", ASCENDING)], name="ix_employees_certification_names")


def ensure_tender_indexes(collection: Optional[Collection] = None) -> None:
    """Create the indexes the tender queries rely on. Idempotent, like the
    three siblings above."""
    coll = collection if collection is not None else get_tenders_collection()

    # The default list order, and what the closed view sorts by.
    coll.create_index([("updated_at", DESCENDING)], name="ix_tenders_updated_at")
    # What the open view sorts and windows by.
    coll.create_index([("submission_deadline", ASCENDING)], name="ix_tenders_deadline")
    # The status filter, the open/closed split, and the authority dropdown.
    coll.create_index([("status", ASCENDING)], name="ix_tenders_status")
    coll.create_index([("issuing_authority", ASCENDING)], name="ix_tenders_authority")
    # Multikey, for the tag filter.
    coll.create_index([("tags", ASCENDING)], name="ix_tenders_tags")
