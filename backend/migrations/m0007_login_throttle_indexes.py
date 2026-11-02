"""0007 - create the login_throttle TTL index.

Login rate limiting (see backend/app/rate_limit.py) keeps its counters in a
new `login_throttle` collection: one document per (dimension, hashed IP or
username, time window). Every document carries its own `window_expires_at`,
and the TTL index on that field is what makes an old window disappear on its
own once it is over, rather than accumulating forever.

Unlike 0001-0006, this migration backfills nothing -- `login_throttle` is a
brand-new collection with no prior documents to touch, and every document it
will ever hold already carries `window_expires_at` from the moment the
application writes it. The only thing this migration is responsible for is
making the index exist on a database that has not started the application
(and so never hit app/mongodb.py's own ensure_login_throttle_indexes()) yet.

create_index() is idempotent either way: whichever of this migration or the
app's own startup call runs first creates the index, and the other is a
no-op.

What it deliberately does NOT do:

  * It does not read, write, or delete a single document in login_throttle
    or any other collection.
  * It does not touch users, sessions, or any collection other than
    login_throttle (plus its own record in `migrations`).
"""

from datetime import datetime, timezone

from pymongo import ASCENDING

VERSION = "0007_login_throttle_indexes"
DESCRIPTION = "Create the login_throttle TTL index on window_expires_at."

COLLECTION = "login_throttle"

# Named so that ensure_login_throttle_indexes() in app/mongodb.py creates the
# identical index on startup -- create_index is idempotent, so whichever
# runs first wins and the other is a no-op.
INDEX_NAME = "ix_login_throttle_window_expires_at"


def analyse(db):
    """Read-only. Reports whether the index already exists.

    Runs the same check `apply` does, so --dry-run and the real run can
    never disagree.
    """
    throttle = db[COLLECTION]
    existing_indexes = {index["name"] for index in throttle.list_indexes()}
    return {
        "version": VERSION,
        "collection_exists": COLLECTION in db.list_collection_names(),
        "index_already_present": INDEX_NAME in existing_indexes,
        "index": INDEX_NAME,
    }


def apply(db):
    """Create the index. Safe to run repeatedly: create_index on an index
    that already exists with the same definition is a no-op.

    index_already_present keeps what analyse() found before the run, so the
    report can say whether this run created the index or found it there.
    """
    report = analyse(db)
    db[COLLECTION].create_index(
        [("window_expires_at", ASCENDING)], name=INDEX_NAME, expireAfterSeconds=0
    )
    report["index_created"] = not report["index_already_present"]
    return report


def report_lines(report):
    """The run's own account of itself, for the runner to print.

    "index_created" is only in the report once apply() has run, so a
    --dry-run report says what would happen and never that anything was
    created.
    """
    if "index_created" not in report:
        state = (
            "already present -- nothing to create"
            if report["index_already_present"]
            else "missing -- would be created"
        )
    elif report["index_created"]:
        state = "created"
    else:
        state = "already present -- left as is"
    return [
        "login_throttle collection present : %s" % report["collection_exists"],
        "index %s : %s" % (report["index"], state),
    ]


def record(db, report):
    """Mark this migration as applied."""
    now = datetime.now(timezone.utc)
    existing = db["migrations"].find_one({"_id": VERSION}) or {}

    entry = {
        "_id": VERSION,
        "description": DESCRIPTION,
        "first_applied_at": existing.get("first_applied_at") or now,
        "last_run_at": now,
        "index": report.get("index"),
    }
    db["migrations"].replace_one({"_id": VERSION}, entry, upsert=True)
