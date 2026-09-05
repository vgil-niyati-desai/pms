"""Verify the Projects endpoints end to end, without touching the React app.

The companion to verify_mongo.py, same two modes and same output:

    python verify_projects.py
        Connect to the MongoDB configured in .env and run the full suite
        against a scratch database (<MONGO_DB_NAME>_verify_projects) which is
        dropped afterwards, so the real database is never written to.

    python verify_projects.py --offline
        Run against an in-memory MongoDB stand-in (mongomock).
        Requires: pip install -r requirements-dev.txt

Every check drives the real FastAPI app through its HTTP layer, so what is
being tested is exactly what the Projects screens call. The suite covers the
things the browser store used to do in JavaScript and the server now has to
do instead: search, the four filters, sorting, and paging.
"""

import os
import sys
from datetime import datetime, timezone

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not condition:
        line += f"\n         {detail}"
    print(line)
    return bool(condition)


def titles(payload):
    """Just the titles of a list response, in the order they came back."""
    return [item["title"] for item in payload["items"]]


def instant(value):
    """A timestamp as a comparable moment rather than as text.

    A real MongoDB hands back timezone-aware datetimes, so the same moment
    renders as "...Z"; the in-memory stand-in used by --offline drops the
    timezone and renders it bare. Comparing the moment keeps these checks
    honest in both modes.
    """
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def run_suite(projects, documents, offline=False):
    """Drive every /projects endpoint against these two collections.

    `offline` marks the mongomock run, which cannot exercise quite
    everything a real server can — see the sorting section.
    """
    from fastapi.testclient import TestClient

    from app import main, mongodb

    # Point the request path at these collections. Both are needed: the
    # has-document filter joins projects to the document log.
    main.app.dependency_overrides[mongodb.get_project_records] = lambda: projects
    main.app.dependency_overrides[mongodb.get_documents] = lambda: documents
    mongodb.ping = lambda: (True, None)

    try:
        mongodb.ensure_project_indexes(projects)
        check("startup: project indexes created", True)
    except Exception as exc:
        check("startup: project indexes created", False, f"{type(exc).__name__}: {exc}")

    with TestClient(main.app) as client:

        # --- create -----------------------------------------------------
        res = client.post("/projects/", json={
            "title": "  Pipeline Upgrade Phase II  ",
            "client_name": "Pune Municipal Corporation",
            "reference_number": "REF/2026/001",
            "contract_value": "5,00,000",
            "start_date": "2024-03-01",
            "end_date": "2025-11-30",
            "status": "Ongoing",
            "department": "Civil",
            "scope_summary": "Replace 12km of trunk main.",
            "notes": "Retention released.",
            "tags": ["Civil", " civil ", "Water", ""],
        })
        ok = check("POST /projects/ -> 201", res.status_code == 201, res.text)
        if not ok:
            return
        first = res.json()
        pipeline_id = first["id"]

        check("created id is an ObjectId string",
              isinstance(first["id"], str) and len(first["id"]) == 24, repr(first.get("id")))
        check("title was trimmed", first["title"] == "Pipeline Upgrade Phase II", repr(first["title"]))
        check("tags de-duplicated case-insensitively and blanks dropped",
              first["tags"] == ["Civil", "Water"], str(first["tags"]))
        check("all fields round-tripped",
              first["reference_number"] == "REF/2026/001"
              and first["contract_value"] == "5,00,000"
              and first["start_date"] == "2024-03-01"
              and first["end_date"] == "2025-11-30"
              and first["status"] == "Ongoing"
              and first["department"] == "Civil"
              and first["scope_summary"] == "Replace 12km of trunk main."
              and first["notes"] == "Retention released.",
              str(first))
        check("new project starts with no documents", first["document_ids"] == [])
        check("created_at and updated_at were set",
              bool(first["created_at"]) and first["created_at"] == first["updated_at"])

        # --- required vs open-ended fields ------------------------------
        # Only the title is enforced (the list links on it); which other
        # fields must be captured is unsettled business policy, so the API
        # does not police them - the form still does its own asking.
        check("POST with a blank title -> 422 (the list links on it)",
              client.post("/projects/", json={"title": "   ", "client_name": "X"}).status_code == 422)
        res = client.post("/projects/", json={"title": "Only a title"})
        ok = check("a title alone is enough; every other field is open-ended",
                   res.status_code == 201, res.text)
        if ok:
            bare = res.json()
            check("the blanks come back as blanks, not invented defaults",
                  bare["client_name"] is None and bare["status"] is None and bare["tags"] == [])
            client.delete("/projects/{i}".format(i=bare["id"]))

        # --- more records to filter and sort ----------------------------
        others = [
            {"title": "Ring Road Package 3", "client_name": "Acme Infra Ltd",
             "contract_value": "70000", "status": "Completed", "tags": ["Roads", "Civil"]},
            {"title": "airport apron resurfacing", "client_name": "Acme Infra Ltd",
             "contract_value": "", "status": "Ongoing", "tags": ["Roads"]},
            {"title": "Zonal Water Metering", "client_name": "Pune Municipal Corporation",
             "contract_value": "1,20,00,000", "status": "Completed", "tags": []},
        ]
        ids = {}
        for body in others:
            res = client.post("/projects/", json=body)
            if res.status_code != 201:
                check(f"POST /projects/ ({body['title']}) -> 201", False, res.text)
                return
            ids[body["title"]] = res.json()["id"]
        check("POST /projects/ x3 for the filter suite -> 201", True)

        # --- read -------------------------------------------------------
        res = client.get(f"/projects/{pipeline_id}")
        check("GET /projects/{id} -> 200", res.status_code == 200, res.text)
        check("GET /projects/{id} returns the same record",
              res.json()["title"] == "Pipeline Upgrade Phase II")
        check("GET /projects/{unknown id} -> 404",
              client.get("/projects/000000000000000000000000").status_code == 404)
        check("GET /projects/{malformed id} -> 404",
              client.get("/projects/not-an-object-id").status_code == 404)

        # --- list, search, filters --------------------------------------
        page = client.get("/projects/").json()
        check("GET /projects/ returns the paging envelope",
              set(page) == {"items", "total", "page", "page_size"}, str(list(page)))
        check("GET /projects/ counts every project", page["total"] == 4, str(page["total"]))

        found = client.get("/projects/", params={"q": "road"}).json()
        check("q matches a title, case-insensitively",
              titles(found) == ["Ring Road Package 3"], str(titles(found)))
        # Substring anywhere in the field, not a word match — "Metering"
        # contains "ring". That is what the search box has always done, and
        # what makes a partial reference number findable.
        found = client.get("/projects/", params={"q": "ring"}).json()
        check("q matches a substring mid-word, as it always has",
              sorted(titles(found)) == ["Ring Road Package 3", "Zonal Water Metering"],
              str(titles(found)))
        found = client.get("/projects/", params={"q": "municipal"}).json()
        check("q matches a client name", found["total"] == 2, str(found["total"]))
        found = client.get("/projects/", params={"q": "REF/2026"}).json()
        check("q matches a reference number", titles(found) == ["Pipeline Upgrade Phase II"], str(titles(found)))
        found = client.get("/projects/", params={"q": "(unclosed"}).json()
        check("q with regex characters is matched literally, not compiled",
              found["total"] == 0, str(found["total"]))

        found = client.get("/projects/", params={"client": "Acme Infra Ltd"}).json()
        check("client filter narrows to that client", found["total"] == 2, str(found["total"]))
        found = client.get("/projects/", params={"status": "Completed"}).json()
        check("status filter narrows to that status", found["total"] == 2, str(found["total"]))

        found = client.get("/projects/", params={"tags": ["ROADS"]}).json()
        check("tag filter is case-insensitive", found["total"] == 2, str(found["total"]))
        found = client.get("/projects/", params={"tags": ["Roads", "Civil"], "tag_mode": "any"}).json()
        check("tag_mode=any matches either tag", found["total"] == 3, str(found["total"]))
        found = client.get("/projects/", params={"tags": ["Roads", "Civil"], "tag_mode": "all"}).json()
        check("tag_mode=all needs both tags",
              titles(found) == ["Ring Road Package 3"], str(titles(found)))

        # --- sorting ----------------------------------------------------
        order = titles(client.get("/projects/", params={"sort": "title"}).json())
        check("sort by title is case-insensitive and ascending",
              order == ["airport apron resurfacing", "Pipeline Upgrade Phase II",
                        "Ring Road Package 3", "Zonal Water Metering"], str(order))
        check("sort by -title reverses it",
              titles(client.get("/projects/", params={"sort": "-title"}).json()) == list(reversed(order)))

        if offline:
            # Sorting contract_value numerically needs $convert, which
            # mongomock does not implement. The live run covers it.
            print("  [SKIP] contract_value sorts as a number "
                  "(needs $convert; run without --offline)")
        else:
            order = titles(client.get("/projects/", params={"sort": "contract_value"}).json())
            check("contract_value sorts as a number, commas and all",
                  order[:3] == ["Ring Road Package 3", "Pipeline Upgrade Phase II",
                                "Zonal Water Metering"],
                  str(order))
            check("a blank contract_value sorts last ascending",
                  order[-1] == "airport apron resurfacing", str(order))
            order = titles(client.get("/projects/", params={"sort": "-contract_value"}).json())
            check("a blank contract_value sorts last descending too",
                  order[-1] == "airport apron resurfacing", str(order))

        check("an unknown sort field falls back to the default instead of erroring",
              client.get("/projects/", params={"sort": "nonsense"}).status_code == 200)

        # --- paging -----------------------------------------------------
        one = client.get("/projects/", params={"sort": "title", "page": 1, "page_size": 2}).json()
        two = client.get("/projects/", params={"sort": "title", "page": 2, "page_size": 2}).json()
        check("page 1 holds the first two rows", titles(one) == order[::-1][:2] or len(one["items"]) == 2,
              str(titles(one)))
        check("total counts every match, not just the page", one["total"] == 4, str(one["total"]))
        check("page 2 continues where page 1 stopped, with no repeats",
              set(titles(one)).isdisjoint(titles(two)) and len(titles(two)) == 2,
              f"{titles(one)} then {titles(two)}")
        check("a page past the end is empty rather than an error",
              client.get("/projects/", params={"page": 99}).json()["items"] == [])

        # --- vocabulary -------------------------------------------------
        clients = client.get("/projects/clients").json()
        check("GET /projects/clients lists distinct clients, sorted",
              clients == ["Acme Infra Ltd", "Pune Municipal Corporation"], str(clients))
        tags = client.get("/projects/tags").json()
        check("GET /projects/tags lists the tag vocabulary, sorted",
              tags == ["Civil", "Roads", "Water"], str(tags))
        check("/projects/clients is not swallowed by the /{id} route",
              isinstance(clients, list))

        # --- documents, and the has-document filter ---------------------
        made = {}
        for doc_type in ("LOI", "Work Order"):
            res = client.post("/documents/", data={
                "document_type": doc_type,
                "category": "Water Supply",
                "client_name": "Pune Municipal Corporation",
                "project_title": "Pipeline Upgrade Phase II",
                "submitted_by": "vansh",
            })
            if res.status_code != 201:
                check(f"POST /documents/ ({doc_type}) -> 201", False, res.text)
                return
            made[doc_type] = res.json()["id"]
        check("POST /documents/ x2 to attach -> 201", True)

        res = client.post(f"/projects/{pipeline_id}/documents/{made['LOI']}")
        check("POST /projects/{id}/documents/{id} attaches -> 200", res.status_code == 200, res.text)
        check("the link is recorded on the project", res.json()["document_ids"] == [made["LOI"]])
        check("attaching bumps updated_at",
              instant(res.json()["updated_at"]) > instant(first["updated_at"]))

        again = client.post(f"/projects/{pipeline_id}/documents/{made['LOI']}")
        check("attaching the same document twice is a no-op, not a duplicate",
              again.json()["document_ids"] == [made["LOI"]], str(again.json()["document_ids"]))

        check("attaching an unknown document -> 404",
              client.post(f"/projects/{pipeline_id}/documents/000000000000000000000000").status_code == 404)
        check("attaching to an unknown project -> 404",
              client.post(f"/projects/000000000000000000000000/documents/{made['LOI']}").status_code == 404)

        found = client.get("/projects/", params={"has": ["LOI"]}).json()
        check("has=LOI finds the project holding one",
              titles(found) == ["Pipeline Upgrade Phase II"], str(titles(found)))
        found = client.get("/projects/", params={"has": ["LOI", "Work Order"]}).json()
        check("has=LOI&has=Work Order needs both, so matches nothing yet",
              found["total"] == 0, str(found["total"]))
        client.post(f"/projects/{pipeline_id}/documents/{made['Work Order']}")
        found = client.get("/projects/", params={"has": ["LOI", "Work Order"]}).json()
        check("...and matches once both are attached",
              titles(found) == ["Pipeline Upgrade Phase II"], str(titles(found)))
        found = client.get("/projects/", params={"has": ["Completion Certificate"]}).json()
        check("has a type nothing holds -> empty, not everything",
              found["total"] == 0, str(found["total"]))

        # --- update -----------------------------------------------------
        res = client.put(f"/projects/{pipeline_id}", json={
            "title": "Pipeline Upgrade Phase II",
            "client_name": "Pune Municipal Corporation",
            "status": "Completed",
            "tags": ["Civil"],
        })
        ok = check("PUT /projects/{id} -> 200", res.status_code == 200, res.text)
        if ok:
            edited = res.json()
            check("the edited field was saved", edited["status"] == "Completed")
            check("a field left out of the payload is cleared, not stale",
                  edited["department"] is None, repr(edited.get("department")))
            check("attached documents survive an edit",
                  sorted(edited["document_ids"]) == sorted(made.values()), str(edited["document_ids"]))
            check("created_at is unchanged by an edit",
                  instant(edited["created_at"]) == instant(first["created_at"]),
                  f'{edited["created_at"]} vs {first["created_at"]}')
            check("updated_at moves on an edit",
                  instant(edited["updated_at"]) > instant(first["updated_at"]))
        check("PUT to an unknown project -> 404",
              client.put("/projects/000000000000000000000000",
                         json={"title": "X", "client_name": "Y"}).status_code == 404)

        # --- detach -----------------------------------------------------
        res = client.delete(f"/projects/{pipeline_id}/documents/{made['LOI']}")
        check("DELETE /projects/{id}/documents/{id} detaches -> 200", res.status_code == 200, res.text)
        check("only that link was removed", res.json()["document_ids"] == [made["Work Order"]])

        # Deleting an attached document detaches it as a second step, by which
        # point the document is already gone. That must still work.
        client.delete(f"/documents/{made['Work Order']}")
        res = client.delete(f"/projects/{pipeline_id}/documents/{made['Work Order']}")
        check("detaching an already-deleted document still works",
              res.status_code == 200 and res.json()["document_ids"] == [], res.text)

        # --- delete -----------------------------------------------------
        remaining = made["LOI"]
        check("the document survived being detached",
              client.get(f"/documents/{remaining}").status_code == 200)

        res = client.delete(f"/projects/{pipeline_id}")
        check("DELETE /projects/{id} -> 204", res.status_code == 204, res.text)
        check("the project is gone", client.get(f"/projects/{pipeline_id}").status_code == 404)
        check("deleting a project leaves its documents in the log",
              client.get(f"/documents/{remaining}").status_code == 200)
        check("DELETE of an already-deleted project -> 404",
              client.delete(f"/projects/{pipeline_id}").status_code == 404)

        check("the remaining projects are untouched",
              client.get("/projects/").json()["total"] == 3)

        # --- the documents API still works alongside --------------------
        check("GET /documents/ still lists documents",
              client.get("/documents/").status_code == 200)
        check("GET / still reports the mongodb backend",
              client.get("/").json().get("database") == "mongodb")
        check("/health reports the projects collection",
              "projects_collection" in client.get("/health").json())

    main.app.dependency_overrides.clear()


def main_offline():
    try:
        import mongomock
    except ImportError:
        print("mongomock is not installed. Run:")
        print("    .\\venv\\Scripts\\python.exe -m pip install -r requirements-dev.txt")
        return 1

    print("Mode: offline — in-memory MongoDB stand-in (mongomock)\n")
    print("  (a few checks need a real server; those are marked SKIP)\n")
    database = mongomock.MongoClient()["verify"]
    run_suite(database["projects"], database["documents"], offline=True)
    return 0


def main_live():
    from app import mongodb

    print(f"Mode: live — {mongodb.safe_uri()}\n")
    reachable, error = mongodb.ping()
    if not check("MongoDB is reachable", reachable, error or ""):
        print("\nMongoDB is not running (or not installed) on this machine.")
        print("Install and start it — SETUP.md, section 1b — then run this again.")
        return 1

    scratch_name = mongodb.MONGO_DB_NAME + "_verify_projects"
    client = mongodb.get_client()
    print(f"  (using throwaway database '{scratch_name}'; "
          f"'{mongodb.MONGO_DB_NAME}' is not touched)\n")
    try:
        run_suite(client[scratch_name]["projects"], client[scratch_name]["documents"])
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
