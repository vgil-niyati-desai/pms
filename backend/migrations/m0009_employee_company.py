"""0009 - mark employees with no recorded company as Unknown.

Existing company values are left untouched. Values outside the supported
affiliations are reported and stop the migration before any write.
"""

from datetime import datetime, timezone

COLLECTION = "employees"
VERSION = "0009_employee_company"
DESCRIPTION = "Backfill missing employee company affiliations as Unknown."


def _inspect(db):
    missing = 0
    invalid = 0
    for employee in db[COLLECTION].find({}, {"company": 1}):
        company = employee.get("company")
        if company is None or company == "":
            missing += 1
        elif not isinstance(company, str) or company not in {"VGIL", "Fintech", "Unknown"}:
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
            "%s refused to run: %d employee company value(s) are outside "
            "VGIL, Fintech, or Unknown; nothing was written."
            % (VERSION, report["invalid"])
        )

    result = db[COLLECTION].update_many(
        {
            "$or": [
                {"company": {"$exists": False}},
                {"company": None},
                {"company": ""},
            ]
        },
        {"$set": {"company": "Unknown"}},
    )
    report["updated"] = result.modified_count
    return report


def report_lines(report):
    lines = [
        "employees missing company : %d" % report["missing"],
        "unsupported company values : %d" % report["invalid"],
    ]
    if "updated" in report:
        lines.append("employees marked Unknown : %d" % report["updated"])
    else:
        lines.append("employees to mark Unknown : %d" % report["missing"])
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
