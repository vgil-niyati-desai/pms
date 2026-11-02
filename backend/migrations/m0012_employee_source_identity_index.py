"""0012 - enforce uniqueness for imported employee source identities."""

from datetime import datetime, timezone

from pymongo import ASCENDING

COLLECTION = "employees"
VERSION = "0012_employee_source_identity_index"
DESCRIPTION = "Create a unique partial index on employee source_identity."
INDEX_NAME = "ux_employees_source_identity"
INDEX_KEY = [("source_identity", ASCENDING)]
INDEX_OPTIONS = {
    "unique": True,
    "partialFilterExpression": {"source_identity": {"$type": "string"}},
}
_NON_SPEC_FIELDS = {"v", "key", "name", "ns", "background"}


class EmployeeIdentityIndexConflict(RuntimeError):
    """Employee data or an existing index prevents the identity index."""


def _index_state(collection):
    for index in collection.list_indexes():
        key = list(index["key"].items())
        options = {name: value for name, value in index.items() if name not in _NON_SPEC_FIELDS}
        if index["name"] == INDEX_NAME:
            if key == INDEX_KEY and options == INDEX_OPTIONS:
                return "present", None
            return "conflict", (
                "%s.%s already exists with a different definition"
                % (collection.name, INDEX_NAME)
            )
        if key == INDEX_KEY:
            return "conflict", (
                "%s already has an index on source_identity under another name"
                % collection.name
            )
    return "missing", None


def _duplicate_counts(collection):
    counts = {}
    for employee in collection.find(
        {"source_identity": {"$type": "string"}}, {"source_identity": 1}
    ):
        identity = employee["source_identity"]
        counts[identity] = counts.get(identity, 0) + 1
    duplicate_values = sum(1 for count in counts.values() if count > 1)
    duplicate_employees = sum(count for count in counts.values() if count > 1)
    return duplicate_values, duplicate_employees


def analyse(db):
    employees = db[COLLECTION]
    state, index_conflict = _index_state(employees)
    duplicate_values, duplicate_employees = _duplicate_counts(employees)
    conflicts = []
    if index_conflict:
        conflicts.append(index_conflict)
    if duplicate_values:
        conflicts.append(
            "%d source identity value(s) are shared by %d employees"
            % (duplicate_values, duplicate_employees)
        )
    return {
        "version": VERSION,
        "index_state": state,
        "duplicate_values": duplicate_values,
        "duplicate_employees": duplicate_employees,
        "conflicts": conflicts,
    }


def apply(db):
    report = analyse(db)
    if report["conflicts"]:
        raise EmployeeIdentityIndexConflict(
            "%s refused to run; nothing was written and it stays pending:\n  - %s"
            % (VERSION, "\n  - ".join(report["conflicts"]))
        )

    if report["index_state"] == "missing":
        db[COLLECTION].create_index(
            INDEX_KEY,
            name=INDEX_NAME,
            **INDEX_OPTIONS,
        )
        report["created"] = True
    else:
        report["created"] = False
    return report


def report_lines(report):
    if "created" in report:
        state = "created" if report["created"] else "already present -- left as is"
    else:
        state = (
            "conflict -- migration would refuse to run"
            if report["conflicts"]
            else "missing -- would be created"
            if report["index_state"] == "missing"
            else "already present -- nothing to create"
        )
    return [
        "duplicate source identity values : %d" % report["duplicate_values"],
        "index %s : %s" % (INDEX_NAME, state),
    ]


def record(db, report):
    now = datetime.now(timezone.utc)
    existing = db["migrations"].find_one({"_id": VERSION}) or {}
    db["migrations"].replace_one(
        {"_id": VERSION},
        {
            "_id": VERSION,
            "description": DESCRIPTION,
            "first_applied_at": existing.get("first_applied_at") or now,
            "last_run_at": now,
            "index": INDEX_NAME,
            "index_created": report.get("created", False),
        },
        upsert=True,
    )
