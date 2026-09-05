from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from . import mongodb
from .routers import documents_mongo, employees_mongo, projects_mongo, tenders_mongo

load_dotenv()

app = FastAPI(title="Project Document Collection System")

# Allow the local React dev server to call this API. Vite prefers 5173 but
# falls back to the next free port when it is taken (a second `npm run dev`,
# or something else on 5173), and a fallback port used to be silently blocked
# here — the app loaded but every request died as "Failed to fetch". The
# regex admits localhost on 5170-5179, which covers Vite's fallback walk
# while still refusing anything that is not this machine.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):517\d$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents_mongo.router)
app.include_router(projects_mongo.router)
app.include_router(employees_mongo.router)
app.include_router(tenders_mongo.router)

MONGO_UNREACHABLE_HINT = (
    "Cannot reach MongoDB at {uri}. Check that the MongoDB server is "
    "installed and running, and that MONGO_* in backend/.env points at "
    "it. See SETUP.md, section 1b."
)


@app.exception_handler(ServerSelectionTimeoutError)
@app.exception_handler(ConnectionFailure)
def _mongo_unreachable(request: Request, exc: Exception):
    """Turn a dead connection into a clear 503 instead of a 500 traceback.

    Requests are not pre-flighted against the server, so this is where an
    unreachable MongoDB actually surfaces.
    """
    return JSONResponse(
        status_code=503,
        content={"detail": MONGO_UNREACHABLE_HINT.format(uri=mongodb.safe_uri())},
    )


@app.on_event("startup")
def on_startup():
    # Collections in MongoDB are created implicitly on first insert, so there
    # is no schema to create here. What does need creating is the indexes,
    # and create_index() is idempotent.
    reachable, error = mongodb.ping()
    if not reachable:
        # Deliberately not fatal: the app should still start (and /docs
        # should still load) on a machine where MongoDB isn't up yet.
        print(f"WARNING: MongoDB at {mongodb.safe_uri()} is not reachable: {error}")
        print("         The API will return 503 on document requests until it is.")
        print("         See SETUP.md, section 1b, for installing/starting MongoDB.")
        return

    mongodb.ensure_indexes()
    mongodb.ensure_project_indexes()
    mongodb.ensure_employee_indexes()
    mongodb.ensure_tender_indexes()

    # Hash any files that were uploaded before duplicate detection
    # existed, otherwise they'd never be recognised as duplicates.
    filled = documents_mongo.backfill_file_hashes(mongodb.get_collection())
    if filled:
        print(f"Backfilled content hashes for {filled} existing document(s).")

    print(f"Connected to MongoDB: {mongodb.safe_uri()} / db '{mongodb.MONGO_DB_NAME}'")


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "Project Document Collection System",
        "database": "mongodb",
    }


@app.get("/health")
def health():
    """Is MongoDB actually reachable right now?

    Useful on its own for confirming a fresh MongoDB install is wired up
    correctly, without having to save an entry through the UI first.
    """
    reachable, error = mongodb.ping()
    return {
        "database": "mongodb",
        "target": mongodb.safe_uri(),
        "db_name": mongodb.MONGO_DB_NAME,
        "collection": mongodb.MONGO_COLLECTION_NAME,
        "projects_collection": mongodb.MONGO_PROJECTS_COLLECTION_NAME,
        "employees_collection": mongodb.MONGO_EMPLOYEES_COLLECTION_NAME,
        "tenders_collection": mongodb.MONGO_TENDERS_COLLECTION_NAME,
        "connected": reachable,
        "error": error,
    }
