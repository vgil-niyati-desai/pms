"""0011 - initialize the employee source_identity field.

The migration does not infer identities for legacy employees. Their company
and authoritative source identifier are not known reliably.
"""

from datetime import datetime, timezone

COLLECTION = "employees"
VERSION = "0011_employee_source_identity"
DESCRIPTION = "Initialize missing employee source identities as null."


def _inspect(db):
    missing = 0
    invalid = 0
    for employee in db[COLLECTION].find({}, {"source_identity": 1}):
        identity = employee.get("source_identity")
        if identity is None or (isinstance(identity, str) and not identity.strip()):
            missing += 1
        elif not isinstance(identity, str):
            invalid += 1
    return missing, invalid


def analyse(db):
    missing, invalid = _inspect(db)
    return {
        "version": VERSION,
        "missing": missing,
        "invalid": invalid,
    }


def apply(db):
    report = analyse(db)
    if report["invalid"]:
        raise RuntimeError(
            "%s refused to run: %d employee source identity value(s) are "
            "not strings; nothing was written."
            % (VERSION, report["invalid"])
        )

    employees = db[COLLECTION]
    updated = 0
    for employee in employees.find({}, {"source_identity": 1}):
        identity = employee.get("source_identity")
        if identity is None or not identity.strip():
            result = employees.update_one(
                {"_id": employee["_id"]},
                {"$set": {"source_identity": None}},
            )
            updated += result.modified_count

    report["updated"] = updated
    return report


def report_lines(report):
    lines = [
        "employees missing source identity : %d" % report["missing"],
        "invalid source identity values : %d" % report["invalid"],
    ]
    if "updated" in report:
        lines.append("source identity fields initialized : %d" % report["updated"])
    else:
        lines.append("source identity fields to initialize : %d" % report["missing"])
    return lines


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
            "updated": report.get("updated", 0),
        },
        upsert=True,
    )
