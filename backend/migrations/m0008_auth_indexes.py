"""0008 - create the users and sessions indexes.

Authentication (see backend/app/auth.py) relies on three indexes that, until
now, only app startup and app/create_admin.py created, through
ensure_auth_indexes() in app/mongodb.py:

  * users.ux_users_username        unique -- one account per username
  * sessions.ux_sessions_token_hash unique -- one session per cookie token
  * sessions.ix_sessions_expires_at TTL (expireAfterSeconds=0) -- expired
                                    sessions are removed by MongoDB itself

This migration creates the same three, with the same names, keys and
options, so a database can be brought up to date before the application is
started against it. create_index() is idempotent: whichever of this migration
or startup runs first creates an index, and the other is a no-op.

Unlike 0007, two of these indexes are unique, and a unique index cannot be
built over data that already breaks it. So before creating anything, analyse()
checks, read-only:

  * usernames held by more than one account (a missing username counts as
    null, and two nulls collide too);
  * session token hashes held by more than one session;
  * sessions with no token hash. The unique index is not sparse, so a missing
    token_hash indexes as null and at most one document may lack it. Exactly
    one normally does: the account-change lock record auth.py keeps in this
    collection (_id "admin-account-mutation-lock").
  * existing indexes that would clash: one with the same name but a different
    key or options, or one on the same key under a different name. MongoDB
    refuses to create the index in either case.

If any of these is found, apply() raises AuthIndexConflict before writing
anything -- no index is created and the migration is not recorded, so it stays
pending until a person has resolved the data or index by hand. That departs
from the "apply what is certain, report the rest, record anyway" pattern of
0001-0006 on purpose: recording a migration whose index was never built would
make --status claim protection the database does not have.

What it deliberately does NOT do:

  * It does not read a password hash or a session token, and does not create,
    modify, merge or delete any account or session.
  * It does not drop or rebuild an existing index, conflicting or not.
  * It does not touch login_throttle (that is 0007) or any collection other
    than users and sessions (plus its own record in `migrations`).

Collection names are the defaults, as in every other migration; a deployment
that overrides MONGO_USERS_COLLECTION or MONGO_SESSIONS_COLLECTION is not
covered.
"""

from datetime import datetime, timezone

from pymongo import ASCENDING

VERSION = "0008_auth_indexes"
DESCRIPTION = (
    "Create the users/sessions indexes: unique username, unique session "
    "token hash, and the session expiry TTL."
)

USERS = "users"
SESSIONS = "sessions"

# Identical to ensure_auth_indexes() in app/mongodb.py -- same collections,
# names, keys and options -- so whichever runs first wins and the other is a
# no-op. tests/test_auth_index_migrations.py checks the two stay identical.
INDEXES = (
    {
        "collection": USERS,
        "name": "ux_users_username",
        "key": [("username", ASCENDING)],
        "options": {"unique": True},
    },
    {
        "collection": SESSIONS,
        "name": "ux_sessions_token_hash",
        "key": [("token_hash", ASCENDING)],
        "options": {"unique": True},
    },
    {
        "collection": SESSIONS,
        "name": "ix_sessions_expires_at",
        "key": [("expires_at", ASCENDING)],
        "options": {"expireAfterSeconds": 0},
    },
)

# Fields list_indexes() returns that describe the index record rather than the
# index itself. `background` is a build-time hint some older servers echo
# back; it does not make two indexes different.
_NON_SPEC_FIELDS = {"v", "key", "name", "ns", "background"}


class AuthIndexConflict(RuntimeError):
    """Existing data or indexes would make the auth indexes fail to build."""


def _index_state(collection, spec):
    """("present" | "missing" | "conflict", reason or None) for one index."""
    for index in collection.list_indexes():
        key = list(index["key"].items())
        options = {k: v for k, v in index.items() if k not in _NON_SPEC_FIELDS}
        same_definition = key == spec["key"] and options == spec["options"]
        if index["name"] == spec["name"]:
            if same_definition:
                return "present", None
            return "conflict", (
                "%s.%s already exists with a different definition "
                "(key %s, options %s; expected key %s, options %s)"
                % (collection.name, spec["name"], key, options, spec["key"], spec["options"])
            )
        if key == spec["key"]:
            return "conflict", (
                "%s already has index %s on %s; %s cannot be created beside it"
                % (collection.name, index["name"], key, spec["name"])
            )
    return "missing", None


def _shared_values(collection, field, match=None):
    """(values held by more than one document, documents holding them)."""
    pipeline = [{"$match": match}] if match else []
    pipeline += [
        {"$group": {"_id": "$" + field, "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
    ]
    groups = list(collection.aggregate(pipeline))
    return len(groups), sum(group["count"] for group in groups)


def analyse(db):
    """Read-only. Which indexes exist, and whether anything would stop them.

    apply() runs this first and acts on exactly what it reports, so --dry-run
    and the real run can never disagree.
    """
    users = db[USERS]
    sessions = db[SESSIONS]
    conflicts = []

    indexes = []
    for spec in INDEXES:
        state, reason = _index_state(db[spec["collection"]], spec)
        indexes.append({"collection": spec["collection"], "name": spec["name"], "state": state})
        if reason:
            conflicts.append(reason)

    username_values, username_accounts = _shared_values(users, "username")
    if username_values:
        conflicts.append(
            "%d username value(s) are shared by %d accounts; ux_users_username "
            "cannot be unique until they are distinct"
            % (username_values, username_accounts)
        )

    token_values, token_sessions = _shared_values(
        sessions, "token_hash", {"token_hash": {"$ne": None}}
    )
    if token_values:
        conflicts.append(
            "%d session token hash(es) are shared by %d sessions; "
            "ux_sessions_token_hash cannot be unique until they are distinct"
            % (token_values, token_sessions)
        )

    without_token = sessions.count_documents({"token_hash": None})
    if without_token > 1:
        conflicts.append(
            "%d sessions documents have no token_hash; ux_sessions_token_hash "
            "allows at most one (the account-change lock record)" % without_token
        )

    return {
        "version": VERSION,
        "indexes": indexes,
        "shared_usernames": username_values,
        "shared_token_hashes": token_values,
        "sessions_without_token_hash": without_token,
        "conflicts": conflicts,
    }


def apply(db):
    """Create whichever of the three indexes is missing.

    Refuses -- raising before any write -- if analyse() found a conflict.
    Safe to run repeatedly: create_index on an index that already exists with
    the same definition is a no-op, and a second run creates nothing.
    """
    report = analyse(db)
    if report["conflicts"]:
        raise AuthIndexConflict(
            "%s refused to run; nothing was written and it stays pending:\n  - %s\n"
            "Resolve these by hand, then run migrate.py again."
            % (VERSION, "\n  - ".join(report["conflicts"]))
        )

    for spec in INDEXES:
        db[spec["collection"]].create_index(spec["key"], name=spec["name"], **spec["options"])

    report["created"] = [
        index["name"] for index in report["indexes"] if index["state"] == "missing"
    ]
    return report


def report_lines(report):
    """The run's own account of itself, for the runner to print.

    "created" is only in the report once apply() has run, so a --dry-run
    report says what would happen and never that anything was created.
    """
    applied = "created" in report
    states = {
        "present": "already present -- left as is" if applied else "already present -- nothing to create",
        "missing": "created" if applied else "missing -- would be created",
        "conflict": "CONFLICT -- see below",
    }
    lines = [
        "%-35s: %s" % ("%s.%s" % (index["collection"], index["name"]), states[index["state"]])
        for index in report["indexes"]
    ]
    lines += [
        "",
        "usernames shared by >1 account     : %d" % report["shared_usernames"],
        "token hashes shared by >1 session  : %d" % report["shared_token_hashes"],
        "sessions without a token hash      : %d (at most 1 allowed)"
        % report["sessions_without_token_hash"],
    ]
    if report["conflicts"]:
        lines.append("")
        lines.append(
            "   applying would refuse and write nothing until these are resolved by hand:"
        )
        lines += ["   - %s" % conflict for conflict in report["conflicts"]]
    return lines


def record(db, report):
    """Mark this migration as applied.

    A re-run (--force) records its own result, but what the first application
    created is kept beside it, as in its siblings.
    """
    now = datetime.now(timezone.utc)
    existing = db["migrations"].find_one({"_id": VERSION}) or {}

    entry = {
        "_id": VERSION,
        "description": DESCRIPTION,
        "first_applied_at": existing.get("first_applied_at") or now,
        "first_created": existing.get("first_created", report.get("created", [])),
        "last_run_at": now,
        "last_created": report.get("created", []),
        "indexes": [spec["name"] for spec in INDEXES],
    }
    db["migrations"].replace_one({"_id": VERSION}, entry, upsert=True)
