"""0010 - initialize the employee education array without replacing data."""

from datetime import datetime, timezone

COLLECTION = "employees"
VERSION = "0010_employee_education"
DESCRIPTION = "Initialize missing employee education arrays."


def _inspect(db):
    missing = 0
    invalid = 0
    for employee in db[COLLECTION].find({}, {"education": 1}):
        if "education" not in employee or employee["education"] is None:
            missing += 1
        elif not isinstance(employee["education"], list) or not all(
            isinstance(item, str) for item in employee["education"]
        ):
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
            "%s refused to run: %d employee education value(s) are not "
            "string arrays; nothing was written."
            % (VERSION, report["invalid"])
        )

    result = db[COLLECTION].update_many(
        {
            "$or": [
                {"education": {"$exists": False}},
                {"education": None},
            ]
        },
        {"$set": {"education": []}},
    )
    report["updated"] = result.modified_count
    return report


def report_lines(report):
    lines = [
        "employees missing education array : %d" % report["missing"],
        "invalid education values : %d" % report["invalid"],
    ]
    if "updated" in report:
        lines.append("education arrays initialized : %d" % report["updated"])
    else:
        lines.append("education arrays to initialize : %d" % report["missing"])
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
