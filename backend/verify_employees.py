"""Verify the Employees/CV endpoints end to end, without touching the React app.

The third of the suite, alongside verify_mongo.py (documents) and
verify_projects.py, with the same two modes:

    python verify_employees.py
        Connect to the MongoDB configured in .env and run against a scratch
        database (<MONGO_DB_NAME>_verify_employees), dropped afterwards.

    python verify_employees.py --offline
        Run against an in-memory MongoDB stand-in (mongomock).
        Requires: pip install -r requirements-dev.txt

Every check drives the real FastAPI app through its HTTP layer. The suite
covers the employee CRUD, the nested CV and certification records, the
flattened certification index with its computed validity, and everything the
browser store used to do in JavaScript that the server now does: search, the
filters, sorting, and paging.
"""

import os
import sys
from datetime import datetime, timezone

RESULTS = []

TODAY = "2026-08-29"  # fixed, so the validity checks don't rot


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not condition:
        line += f"\n         {detail}"
    print(line)
    return bool(condition)


def names(payload):
    """The employee names of a list response, in the order they came back."""
    return [item["full_name"] for item in payload["items"]]


def instant(value):
    """A timestamp as a comparable moment rather than as text — mongomock
    drops the timezone a real MongoDB keeps, so strings differ across modes
    for the same moment."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# Experience is derived from dates, so the fixtures are built relative to
# today rather than pinned to a calendar year. A record created with a start
# date "14 years ago" still reads as 14 years when this suite runs in 2030 --
# which is the property being tested, so the fixtures have to have it too.
DAYS_PER_YEAR = 365.2425


def years_ago(years):
    """The ISO date `years` before today, the way the API computes it back."""
    from datetime import date, timedelta

    return (date.today() - timedelta(days=years * DAYS_PER_YEAR)).isoformat()


def run_suite(employees, documents, offline=False):
    from fastapi.testclient import TestClient

    from app import main, mongodb

    main.app.dependency_overrides[mongodb.get_employee_records] = lambda: employees
    main.app.dependency_overrides[mongodb.get_documents] = lambda: documents
    mongodb.ping = lambda: (True, None)

    try:
        mongodb.ensure_employee_indexes(employees)
        check("startup: employee indexes created", True)
    except Exception as exc:
        check("startup: employee indexes created", False, f"{type(exc).__name__}: {exc}")

    with TestClient(main.app) as client:

        # --- create -----------------------------------------------------
        res = client.post("/employees/", json={
            "full_name": "  Asha Rao  ",
            "employee_code": "EMP-014",
            "designation": "Project Manager",
            "department": "Civil",
            # 14 years of career, the last 10 of them at VGIL. The two
            # differ on purpose: conflating them is the bug this replaced.
            "career_start_date": years_ago(14),
            "date_of_joining": years_ago(10),
            "highest_qualification": "B.E. Civil",
            "key_skills": "planning, contracts, QA",
            "email": "asha@example.com",
            "phone": "98220 00000",
            "notes": "Lead PM on the metering rollout.",
        })
        ok = check("POST /employees/ -> 201", res.status_code == 201, res.text)
        if not ok:
            return
        asha = res.json()
        asha_id = asha["id"]

        check("created id is an ObjectId string",
              isinstance(asha["id"], str) and len(asha["id"]) == 24, repr(asha.get("id")))
        check("full name was trimmed", asha["full_name"] == "Asha Rao", repr(asha["full_name"]))
        check("all profile fields round-tripped",
              asha["employee_code"] == "EMP-014"
              and asha["designation"] == "Project Manager"
              and asha["department"] == "Civil"
              and asha["date_of_joining"] == years_ago(10)
              and asha["career_start_date"] == years_ago(14)
              and asha["highest_qualification"] == "B.E. Civil"
              and asha["key_skills"] == "planning, contracts, QA"
              and asha["email"] == "asha@example.com"
              and asha["phone"] == "98220 00000"
              and asha["notes"] == "Lead PM on the metering rollout.",
              str(asha))
        # The whole point of the two dates: the figures are worked out on read,
        # they differ from each other, and neither is stored anywhere.
        check("Total Professional Experience is computed from career_start_date",
              asha["total_experience_years"] == 14.0, str(asha.get("total_experience_years")))
        check("Experience with VGIL is computed from date_of_joining",
              asha["vgil_experience_years"] == 10.0, str(asha.get("vgil_experience_years")))
        check("the two experience figures are not the same number",
              asha["total_experience_years"] != asha["vgil_experience_years"])
        from bson import ObjectId

        stored = employees.find_one({"_id": ObjectId(asha_id)})
        check("neither figure is stored on the record, only derived on read",
              "total_experience_years" not in stored
              and "vgil_experience_years" not in stored,
              "a computed figure was written to the database")

        check("new employee starts with no CVs and no certifications",
              asha["cvs"] == [] and asha["certifications"] == [])
        check("created_at and updated_at were set",
              bool(asha["created_at"]) and asha["created_at"] == asha["updated_at"])

        # --- required vs open-ended fields ------------------------------
        # Only full_name is enforced (the list links on it). Designation,
        # a CV's uploaded-by and a certification's name are back to
        # open-ended - the forms still ask, the API does not insist.
        check("POST with a blank name -> 422 (the list links on it)",
              client.post("/employees/", json={"full_name": " ", "designation": "X"}).status_code == 422)
        # The dates drive a calculation now, so an unparseable one has to be
        # refused rather than silently producing no experience at all.
        check("POST with a malformed career start date -> 422",
              client.post("/employees/", json={"full_name": "X", "designation": "Y",
                                               "career_start_date": "15/08/2024"}).status_code == 422)
        check("POST with a malformed joining date -> 422",
              client.post("/employees/", json={"full_name": "X", "designation": "Y",
                                               "date_of_joining": "not a date"}).status_code == 422)
        # A joining date can legitimately be set ahead for someone starting
        # next month. That reads as no experience yet, never as a negative.
        # Removed again straight away: the counts and sort orders below expect
        # exactly the four records this suite creates on purpose.
        res = client.post("/employees/", json={"full_name": "Future Starter",
                                               "date_of_joining": years_ago(-1)})
        check("a future joining date is allowed and reads as no experience yet",
              res.status_code == 201 and res.json()["vgil_experience_years"] == 0.0,
              res.text)
        if res.status_code == 201:
            client.delete(f"/employees/{res.json()['id']}")
        res = client.post("/employees/", json={"full_name": "Scratch Row"})
        ok = check("a name alone is enough; every other field is open-ended",
                   res.status_code == 201, res.text)
        if ok:
            scratch_id = res.json()["id"]
            check("the blanks come back as blanks", res.json()["designation"] is None)
            check("a CV with no fields at all is accepted",
                  client.post(f"/employees/{scratch_id}/cvs", json={}).status_code == 201)
            check("a certification without a name is accepted",
                  client.post(f"/employees/{scratch_id}/certifications", json={}).status_code == 201)
            client.delete(f"/employees/{scratch_id}")

        # --- more records to filter and sort ----------------------------
        others = [
            {"full_name": "Vikram Iyer", "designation": "Site Engineer",
             "department": "Civil", "key_skills": "surveying",
             "career_start_date": years_ago(6), "date_of_joining": years_ago(6)},
            # No dates at all: the record that must read as "not recorded"
            # rather than as nought years.
            {"full_name": "meera Pillai", "designation": "Electrical Engineer",
             "department": "MEP"},
            # 9 years of career, only 2 of them here. The case the old single
            # number got wrong: filtering on total must find him, and the two
            # columns must not agree.
            {"full_name": "Rahul Deshpande", "designation": "Project Manager",
             "department": "IT", "employee_code": "EMP-021",
             "career_start_date": years_ago(9), "date_of_joining": years_ago(2)},
        ]
        ids = {}
        for body in others:
            res = client.post("/employees/", json=body)
            if res.status_code != 201:
                check(f"POST /employees/ ({body['full_name']}) -> 201", False, res.text)
                return
            ids[body["full_name"]] = res.json()["id"]
        check("POST /employees/ x3 for the filter suite -> 201", True)

        # --- read -------------------------------------------------------
        check("GET /employees/{id} -> 200",
              client.get(f"/employees/{asha_id}").status_code == 200)
        check("GET /employees/{unknown id} -> 404",
              client.get("/employees/000000000000000000000000").status_code == 404)
        check("GET /employees/{malformed id} -> 404",
              client.get("/employees/not-an-object-id").status_code == 404)

        # --- list, search, filters --------------------------------------
        page = client.get("/employees/").json()
        check("GET /employees/ returns the paging envelope",
              set(page) == {"items", "total", "page", "page_size"}, str(list(page)))
        check("GET /employees/ counts every employee", page["total"] == 4, str(page["total"]))

        found = client.get("/employees/", params={"q": "asha"}).json()
        check("q matches a name, case-insensitively", names(found) == ["Asha Rao"], str(names(found)))
        found = client.get("/employees/", params={"q": "EMP-0"}).json()
        check("q matches an employee code", found["total"] == 2, str(found["total"]))
        found = client.get("/employees/", params={"q": "surveying"}).json()
        check("q matches key skills", names(found) == ["Vikram Iyer"], str(names(found)))

        found = client.get("/employees/", params={"designation": "Project Manager"}).json()
        check("designation filter narrows to that designation",
              found["total"] == 2, str(found["total"]))
        found = client.get("/employees/", params={"department": "MEP"}).json()
        check("department filter narrows to that department",
              names(found) == ["meera Pillai"], str(names(found)))

        # --- the computed figures across the list ------------------------
        rows = {row["full_name"]: row for row in client.get("/employees/").json()["items"]}
        check("every row carries both experience figures",
              all("total_experience_years" in row and "vgil_experience_years" in row
                  for row in rows.values()))
        check("a record with no dates reports no experience, not zero",
              rows["meera Pillai"]["total_experience_years"] is None
              and rows["meera Pillai"]["vgil_experience_years"] is None,
              str(rows["meera Pillai"].get("total_experience_years")))
        check("total and VGIL differ where the person worked elsewhere first",
              rows["Rahul Deshpande"]["total_experience_years"] == 9.0
              and rows["Rahul Deshpande"]["vgil_experience_years"] == 2.0,
              str(rows["Rahul Deshpande"]))
        check("they agree where the person has only ever worked here",
              rows["Vikram Iyer"]["total_experience_years"]
              == rows["Vikram Iyer"]["vgil_experience_years"] == 6.0,
              str(rows["Vikram Iyer"]))

        # --- experience range -------------------------------------------
        # These used to be skipped offline: the old filter converted a string
        # field with $convert, which mongomock does not implement. Bounding a
        # stored date instead is a plain comparison, so it runs everywhere --
        # and it is the same comparison a real MongoDB will make.
        found = client.get("/employees/", params={"min_experience": "8"}).json()
        check("min experience keeps 8y and up, counted from the career start",
              sorted(names(found)) == ["Asha Rao", "Rahul Deshpande"], str(names(found)))
        found = client.get("/employees/",
                           params={"min_experience": "1", "max_experience": "7"}).json()
        check("a range keeps only those inside it",
              names(found) == ["Vikram Iyer"], str(names(found)))
        found = client.get("/employees/", params={"min_experience": "0"}).json()
        check("no recorded experience cannot satisfy a range, even one from zero",
              "meera Pillai" not in names(found), str(names(found)))
        # Rahul has 2 years with VGIL and 9 in total. The filter bounds the
        # total, so a 5-year floor has to keep him -- bounding VGIL tenure by
        # mistake would drop him, which is the failure this whole change is
        # about.
        found = client.get("/employees/", params={"min_experience": "5"}).json()
        check("the range bounds total experience, not time at VGIL",
              "Rahul Deshpande" in names(found), str(names(found)))

        # --- sorting ----------------------------------------------------
        order = names(client.get("/employees/", params={"sort": "full_name"}).json())
        check("sort by name is case-insensitive and ascending",
              order == ["Asha Rao", "meera Pillai", "Rahul Deshpande", "Vikram Iyer"], str(order))
        check("sort by -full_name reverses it",
              names(client.get("/employees/", params={"sort": "-full_name"}).json())
              == list(reversed(order)))
        # Sorting an experience column sorts the date behind it, backwards --
        # so this also checks that the inversion was not forgotten.
        order = names(client.get("/employees/",
                                 params={"sort": "total_experience_years"}).json())
        check("total experience sorts ascending, 6 before 9 before 14",
              order[:3] == ["Vikram Iyer", "Rahul Deshpande", "Asha Rao"], str(order))
        check("no recorded experience sorts last", order[-1] == "meera Pillai", str(order))
        order = names(client.get("/employees/",
                                 params={"sort": "-total_experience_years"}).json())
        check("-total experience puts the most experienced first",
              order[:3] == ["Asha Rao", "Rahul Deshpande", "Vikram Iyer"], str(order))
        check("no recorded experience still sorts last, both ways",
              order[-1] == "meera Pillai", str(order))
        # And the two columns order people differently, which is the point of
        # having both: Rahul has more career than Vikram but less time here.
        order = names(client.get("/employees/",
                                 params={"sort": "-vgil_experience_years"}).json())
        check("VGIL experience sorts on its own date, not the career one",
              order[:3] == ["Asha Rao", "Vikram Iyer", "Rahul Deshpande"], str(order))
        check("an unknown sort field falls back to the default instead of erroring",
              client.get("/employees/", params={"sort": "nonsense"}).status_code == 200)

        # --- paging -----------------------------------------------------
        one = client.get("/employees/", params={"sort": "full_name", "page": 1, "page_size": 2}).json()
        two = client.get("/employees/", params={"sort": "full_name", "page": 2, "page_size": 2}).json()
        check("total counts every match, not just the page", one["total"] == 4, str(one["total"]))
        check("page 2 continues where page 1 stopped, with no repeats",
              set(names(one)).isdisjoint(names(two)) and len(names(two)) == 2,
              f"{names(one)} then {names(two)}")

        # --- nested CVs -------------------------------------------------
        res = client.post(f"/employees/{asha_id}/cvs", json={
            "version_label": "2026 general", "purpose": "",
            "cv_date": "2026-02-01", "uploaded_by": "vansh",
        })
        ok = check("POST /employees/{id}/cvs -> 201", res.status_code == 201, res.text)
        if not ok:
            return
        after_cv = res.json()
        cv = after_cv["cvs"][0]
        check("the CV got its own id and created_at",
              isinstance(cv["id"], str) and len(cv["id"]) == 24 and bool(cv["created_at"]))
        check("adding a CV bumps the employee's updated_at",
              instant(after_cv["updated_at"]) > instant(asha["updated_at"]))

        res = client.put(f"/employees/{asha_id}/cvs/{cv['id']}", json={
            "version_label": "2026 general, revised", "purpose": "MSAMB tender",
            "cv_date": "2026-02-01", "uploaded_by": "vansh",
            "document_id": "abc123abc123abc123abc123", "file_name": "asha_cv.pdf",
        })
        ok = check("PUT /employees/{id}/cvs/{cv_id} -> 200", res.status_code == 200, res.text)
        if ok:
            edited = res.json()["cvs"][0]
            check("the CV edit was saved",
                  edited["version_label"] == "2026 general, revised"
                  and edited["document_id"] == "abc123abc123abc123abc123"
                  and edited["file_name"] == "asha_cv.pdf", str(edited))
            check("the CV kept its id and created_at through the edit",
                  edited["id"] == cv["id"] and edited["created_at"] == cv["created_at"])
        check("editing a CV that does not exist -> 404",
              client.put(f"/employees/{asha_id}/cvs/000000000000000000000000",
                         json={"uploaded_by": "vansh"}).status_code == 404)

        res = client.delete(f"/employees/{asha_id}/cvs/{cv['id']}")
        check("DELETE /employees/{id}/cvs/{cv_id} -> 200 with the updated employee",
              res.status_code == 200 and res.json()["cvs"] == [], res.text)
        check("deleting the same CV again -> 404",
              client.delete(f"/employees/{asha_id}/cvs/{cv['id']}").status_code == 404)

        # --- nested certifications --------------------------------------
        certs = [
            {"name": "PMP", "issuing_body": "PMI", "expiry_date": "2024-01-01"},        # expired
            {"name": "First Aid", "issuing_body": "Red Cross", "expiry_date": "2030-01-01"},  # valid
            {"name": "AutoCAD", "issuing_body": ""},                                     # no expiry
        ]
        for body in certs:
            res = client.post(f"/employees/{asha_id}/certifications", json=body)
            if res.status_code != 201:
                check(f"POST certification ({body['name']}) -> 201", False, res.text)
                return
        check("POST /employees/{id}/certifications x3 -> 201", True)
        client.post(f"/employees/{ids['Vikram Iyer']}/certifications",
                    json={"name": "First Aid", "issuing_body": "St John"})

        # --- the holds-certificate filter -------------------------------
        found = client.get("/employees/", params={"certifications": ["First Aid"]}).json()
        check("holds First Aid finds both holders",
              sorted(names(found)) == ["Asha Rao", "Vikram Iyer"], str(names(found)))
        found = client.get("/employees/", params={"certifications": ["First Aid", "PMP"]}).json()
        check("two named certificates mean ALL of them",
              names(found) == ["Asha Rao"], str(names(found)))
        found = client.get("/employees/", params={"certifications": ["Nothing Held"]}).json()
        check("a certificate nobody holds -> empty, not everything",
              found["total"] == 0, str(found["total"]))

        # --- the flattened certification index --------------------------
        rows = client.get("/employees/certifications", params={"today": TODAY}).json()
        check("GET /employees/certifications flattens every certification",
              rows["total"] == 4, str(rows["total"]))
        by_name = {(r["name"], r["employee_name"]): r for r in rows["items"]}
        check("each row carries who holds it",
              ("First Aid", "Asha Rao") in by_name and ("First Aid", "Vikram Iyer") in by_name,
              str(list(by_name)))
        check("validity is computed against `today`",
              by_name[("PMP", "Asha Rao")]["status"] == "Expired"
              and by_name[("First Aid", "Asha Rao")]["status"] == "Valid"
              and by_name[("AutoCAD", "Asha Rao")]["status"] == "No expiry",
              str({k: v["status"] for k, v in by_name.items()}))
        check("each row links back to its employee",
              by_name[("PMP", "Asha Rao")]["employee_id"] == asha_id)

        found = client.get("/employees/certifications",
                           params={"status": "Expired", "today": TODAY}).json()
        check("the status filter uses the computed value",
              [r["name"] for r in found["items"]] == ["PMP"], str(found["items"]))
        found = client.get("/employees/certifications",
                           params={"name": "First Aid", "today": TODAY}).json()
        check("the certificate filter narrows to that certificate",
              found["total"] == 2, str(found["total"]))
        found = client.get("/employees/certifications",
                           params={"issuing_body": "PMI", "today": TODAY}).json()
        check("the issuing-body filter narrows to that body",
              [r["name"] for r in found["items"]] == ["PMP"], str(found["total"]))
        found = client.get("/employees/certifications",
                           params={"q": "vikram", "today": TODAY}).json()
        check("index search covers the holder's name",
              found["total"] == 1, str(found["total"]))
        order = [r["name"] for r in client.get("/employees/certifications",
                                               params={"today": TODAY}).json()["items"]]
        check("the index defaults to certificate name, ascending",
              order == ["AutoCAD", "First Aid", "First Aid", "PMP"], str(order))

        # --- vocabularies ------------------------------------------------
        check("GET /employees/designations",
              client.get("/employees/designations").json()
              == ["Electrical Engineer", "Project Manager", "Site Engineer"])
        check("GET /employees/departments",
              client.get("/employees/departments").json() == ["Civil", "IT", "MEP"])
        check("GET /employees/certification-names",
              client.get("/employees/certification-names").json()
              == ["AutoCAD", "First Aid", "PMP"])
        check("GET /employees/issuing-bodies drops blanks",
              client.get("/employees/issuing-bodies").json()
              == ["PMI", "Red Cross", "St John"])
        check("/employees/certifications is not swallowed by the /{id} route",
              isinstance(client.get("/employees/designations").json(), list))

        # --- update ------------------------------------------------------
        res = client.put(f"/employees/{asha_id}", json={
            "full_name": "Asha Rao", "designation": "Senior Project Manager",
        })
        ok = check("PUT /employees/{id} -> 200", res.status_code == 200, res.text)
        if ok:
            edited = res.json()
            check("the edited field was saved", edited["designation"] == "Senior Project Manager")
            check("a field left out of the payload is cleared, not stale",
                  edited["department"] is None, repr(edited.get("department")))
            check("CVs and certifications survive a profile edit",
                  len(edited["certifications"]) == 3, str(len(edited["certifications"])))
            check("created_at is unchanged by an edit",
                  instant(edited["created_at"]) == instant(asha["created_at"]))
        check("PUT to an unknown employee -> 404",
              client.put("/employees/000000000000000000000000",
                         json={"full_name": "X", "designation": "Y"}).status_code == 404)

        # --- delete ------------------------------------------------------
        res = client.post("/documents/", data={
            "document_type": "Resume CV", "category": "Personnel",
            "client_name": "Asha Rao", "project_title": "2026 general",
            "submitted_by": "vansh",
        })
        surviving_doc = res.json()["id"] if res.status_code == 201 else None
        check("a Personnel document exists in the log", surviving_doc is not None, res.text)

        res = client.delete(f"/employees/{asha_id}")
        check("DELETE /employees/{id} -> 204", res.status_code == 204, res.text)
        check("the employee is gone",
              client.get(f"/employees/{asha_id}").status_code == 404)
        check("their rows left the certification index with them",
              client.get("/employees/certifications",
                         params={"today": TODAY}).json()["total"] == 1)
        if surviving_doc:
            check("uploaded files stay in the document log, as the dialog promises",
                  client.get(f"/documents/{surviving_doc}").status_code == 200)
        check("DELETE of an already-deleted employee -> 404",
              client.delete(f"/employees/{asha_id}").status_code == 404)
        check("the remaining employees are untouched",
              client.get("/employees/").json()["total"] == 3)

        # --- the neighbours still work alongside ------------------------
        check("GET /documents/ still lists documents",
              client.get("/documents/").status_code == 200)
        check("GET /projects/ still answers",
              client.get("/projects/").status_code == 200)
        check("/health reports the employees collection",
              "employees_collection" in client.get("/health").json())

    main.app.dependency_overrides.clear()


def main_offline():
    try:
        import mongomock
    except ImportError:
        print("mongomock is not installed. Run:")
        print("    .\\venv\\Scripts\\python.exe -m pip install -r requirements-dev.txt")
        return 1

    print("Mode: offline — in-memory MongoDB stand-in (mongomock)")
    print("  (a few checks need a real server; those are marked SKIP)\n")
    database = mongomock.MongoClient()["verify"]
    run_suite(database["employees"], database["documents"], offline=True)
    return 0


def main_live():
    from app import mongodb

    print(f"Mode: live — {mongodb.safe_uri()}\n")
    reachable, error = mongodb.ping()
    if not check("MongoDB is reachable", reachable, error or ""):
        print("\nMongoDB is not running (or not installed) on this machine.")
        print("Install and start it — SETUP.md, section 1b — then run this again.")
        return 1

    scratch_name = mongodb.MONGO_DB_NAME + "_verify_employees"
    client = mongodb.get_client()
    print(f"  (using throwaway database '{scratch_name}'; "
          f"'{mongodb.MONGO_DB_NAME}' is not touched)\n")
    try:
        run_suite(client[scratch_name]["employees"], client[scratch_name]["documents"])
    finally:
        client.drop_database(scratch_name)
        print(f"\n  Dropped throwaway database '{scratch_name}'.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    offline = "--offline" in sys.argv
    code = main_offline() if offline else main_live()

    if RESULTS:
        passed = sum(1 for _, ok, _ in RESULTS if ok)
        print(f"\n{passed}/{len(RESULTS)} checks passed.")
        failed = [name for name, ok, _ in RESULTS if not ok]
        if failed:
            print("Failed: " + ", ".join(failed))
            code = 1
    sys.exit(code)
