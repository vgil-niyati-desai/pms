"""Verify the Tenders endpoints end to end, without touching the React app.

The fourth suite, alongside verify_mongo.py (documents), verify_projects.py
and verify_employees.py, with the same two modes:

    python verify_tenders.py
        Connect to the MongoDB configured in .env and run against a scratch
        database (<MONGO_DB_NAME>_verify_tenders), dropped afterwards.

    python verify_tenders.py --offline
        Run against an in-memory MongoDB stand-in (mongomock).
        Requires: pip install -r requirements-dev.txt

Every check drives the real FastAPI app through its HTTP layer. The suite
covers the tender CRUD, the nested cost items and certificates, document
attach/detach, and everything the browser store used to do in JavaScript:
search, the open view, the status/authority/tag filters, the deadline
window, the value range, sorting, and paging.
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
    """The tender titles of a list response, in the order they came back."""
    return [item["title"] for item in payload["items"]]


def instant(value):
    """A timestamp as a comparable moment rather than as text — mongomock
    drops the timezone a real MongoDB keeps."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def run_suite(tenders, documents, offline=False):
    from fastapi.testclient import TestClient

    from app import main, mongodb

    main.app.dependency_overrides[mongodb.get_tender_records] = lambda: tenders
    main.app.dependency_overrides[mongodb.get_documents] = lambda: documents
    mongodb.ping = lambda: (True, None)

    try:
        mongodb.ensure_tender_indexes(tenders)
        check("startup: tender indexes created", True)
    except Exception as exc:
        check("startup: tender indexes created", False, f"{type(exc).__name__}: {exc}")

    with TestClient(main.app) as client:

        # --- create -----------------------------------------------------
        res = client.post("/tenders/", json={
            "title": "  MSAMB ERP rollout  ",
            "issuing_authority": "MSAMB",
            "reference_number": "MSAMB/IT/2026/07",
            "tender_type": "Open",
            "estimated_value": "50,00,000",
            "emd_amount": "50000",
            "tender_fee": "5000",
            "published_date": "2026-08-01",
            "submission_deadline": "2026-09-15",
            "status": "Preparing",
            "submission_mode": "Online",
            "notes": "Pre-bid meeting attended.",
            "tags": ["IT", " it ", "ERP", ""],
        })
        ok = check("POST /tenders/ -> 201", res.status_code == 201, res.text)
        if not ok:
            return
        erp = res.json()
        erp_id = erp["id"]

        check("created id is an ObjectId string",
              isinstance(erp["id"], str) and len(erp["id"]) == 24, repr(erp.get("id")))
        check("title was trimmed", erp["title"] == "MSAMB ERP rollout", repr(erp["title"]))
        check("tags de-duplicated case-insensitively and blanks dropped",
              erp["tags"] == ["IT", "ERP"], str(erp["tags"]))
        check("all fields round-tripped",
              erp["reference_number"] == "MSAMB/IT/2026/07"
              and erp["tender_type"] == "Open"
              and erp["estimated_value"] == "50,00,000"
              and erp["emd_amount"] == "50000"
              and erp["tender_fee"] == "5000"
              and erp["published_date"] == "2026-08-01"
              and erp["submission_deadline"] == "2026-09-15"
              and erp["status"] == "Preparing"
              and erp["submission_mode"] == "Online"
              and erp["notes"] == "Pre-bid meeting attended.",
              str(erp))
        check("new tender starts with no nested records and no documents",
              erp["cost_items"] == [] and erp["certificates"] == [] and erp["document_ids"] == [])
        check("created_at and updated_at were set",
              bool(erp["created_at"]) and erp["created_at"] == erp["updated_at"])

        # --- required vs open-ended fields ------------------------------
        # Only the title is enforced (the list links on it). Authority and
        # status are open-ended: a status-less tender simply never matches
        # the Open view. The form still asks for all three.
        check("POST with a blank title -> 422 (the list links on it)",
              client.post("/tenders/", json={"title": " ", "issuing_authority": "X",
                                             "status": "Won"}).status_code == 422)
        check("POST with a negative estimated value -> 422 (invalid, not merely missing)",
              client.post("/tenders/", json={"title": "X", "issuing_authority": "Y",
                                             "status": "Won",
                                             "estimated_value": "-1"}).status_code == 422)
        res = client.post("/tenders/", json={"title": "Only a title"})
        ok = check("a title alone is enough; every other field is open-ended",
                   res.status_code == 201, res.text)
        if ok:
            scratch = res.json()
            check("the blanks come back as blanks",
                  scratch["issuing_authority"] is None and scratch["status"] is None)
            check("a status-less tender never matches the Open view",
                  "Only a title" not in titles(client.get(
                      "/tenders/", params={"open_only": "true"}).json()))
            check("a cost with no fields at all is accepted",
                  client.post(f"/tenders/{scratch['id']}/cost-items", json={}).status_code == 201)
            check("a negative amount is still rejected",
                  client.post(f"/tenders/{scratch['id']}/cost-items",
                              json={"amount": "-1"}).status_code == 422)
            check("a certificate without a name is accepted",
                  client.post(f"/tenders/{scratch['id']}/certificates", json={}).status_code == 201)
            client.delete(f"/tenders/{scratch['id']}")

        # --- more records to filter and sort ----------------------------
        others = [
            {"title": "Ring road widening", "issuing_authority": "NHAI",
             "estimated_value": "900000", "status": "Won",
             "submission_deadline": "2026-01-10", "tags": ["Roads"]},
            {"title": "apron resurfacing", "issuing_authority": "Airports Authority",
             "estimated_value": "", "status": "Identified",
             "submission_deadline": "", "tags": ["Roads", "Aviation"]},
            {"title": "Zonal metering AMC", "issuing_authority": "MSAMB",
             "estimated_value": "1,20,00,000", "status": "Lost",
             "submission_deadline": "2025-11-30", "tags": []},
        ]
        for body in others:
            res = client.post("/tenders/", json=body)
            if res.status_code != 201:
                check(f"POST /tenders/ ({body['title']}) -> 201", False, res.text)
                return
        check("POST /tenders/ x3 for the filter suite -> 201", True)

        # --- read -------------------------------------------------------
        check("GET /tenders/{id} -> 200", client.get(f"/tenders/{erp_id}").status_code == 200)
        check("GET /tenders/{unknown id} -> 404",
              client.get("/tenders/000000000000000000000000").status_code == 404)
        check("GET /tenders/{malformed id} -> 404",
              client.get("/tenders/not-an-object-id").status_code == 404)

        # --- list, search, filters --------------------------------------
        page = client.get("/tenders/").json()
        check("GET /tenders/ returns the paging envelope",
              set(page) == {"items", "total", "page", "page_size"}, str(list(page)))
        check("GET /tenders/ counts every tender", page["total"] == 4, str(page["total"]))

        found = client.get("/tenders/", params={"q": "erp"}).json()
        check("q matches a title, case-insensitively",
              titles(found) == ["MSAMB ERP rollout"], str(titles(found)))
        found = client.get("/tenders/", params={"q": "MSAMB/IT"}).json()
        check("q matches a reference number", found["total"] == 1, str(found["total"]))
        found = client.get("/tenders/", params={"q": "nhai"}).json()
        check("q matches the issuing authority", titles(found) == ["Ring road widening"],
              str(titles(found)))

        found = client.get("/tenders/", params={"open_only": "true"}).json()
        check("the open view keeps only Identified and Preparing",
              sorted(titles(found)) == ["MSAMB ERP rollout", "apron resurfacing"],
              str(titles(found)))
        found = client.get("/tenders/", params={"statuses": ["Won", "Lost"]}).json()
        check("the status filter takes several at once",
              sorted(titles(found)) == ["Ring road widening", "Zonal metering AMC"],
              str(titles(found)))
        found = client.get("/tenders/", params={"authority": "MSAMB"}).json()
        check("the authority filter narrows to that authority",
              found["total"] == 2, str(found["total"]))

        found = client.get("/tenders/", params={"tags": ["roads"]}).json()
        check("tag filter is case-insensitive", found["total"] == 2, str(found["total"]))
        found = client.get("/tenders/", params={"tags": ["Roads", "Aviation"],
                                                "tag_mode": "all"}).json()
        check("tag_mode=all needs both tags",
              titles(found) == ["apron resurfacing"], str(titles(found)))

        # --- the deadline window ----------------------------------------
        found = client.get("/tenders/", params={"deadline_from": "2026-01-01",
                                                "deadline_to": "2026-12-31"}).json()
        check("a deadline window keeps what falls inside it",
              sorted(titles(found)) == ["MSAMB ERP rollout", "Ring road widening"],
              str(titles(found)))
        found = client.get("/tenders/", params={"deadline_to": "2026-12-31"}).json()
        check("a to-only window still excludes tenders with no deadline",
              "apron resurfacing" not in titles(found), str(titles(found)))
        found = client.get("/tenders/", params={"deadline_from": "2026-06-01"}).json()
        check("a from-only window works",
              titles(found) == ["MSAMB ERP rollout"], str(titles(found)))

        # --- the value range --------------------------------------------
        if offline:
            print("  [SKIP] the value range and value sort (need $convert; run without --offline)")
        else:
            found = client.get("/tenders/", params={"min_value": "1000000"}).json()
            check("min value keeps 10 lakh and up, reading '50,00,000' as a number",
                  sorted(titles(found)) == ["MSAMB ERP rollout", "Zonal metering AMC"],
                  str(titles(found)))
            found = client.get("/tenders/", params={"min_value": "1", "max_value": "1000000"}).json()
            check("a range keeps only what falls inside it",
                  titles(found) == ["Ring road widening"], str(titles(found)))
            found = client.get("/tenders/", params={"min_value": "0"}).json()
            check("no estimated value cannot satisfy a range, even one from zero",
                  "apron resurfacing" not in titles(found), str(titles(found)))

            order = titles(client.get("/tenders/", params={"sort": "estimated_value"}).json())
            check("estimated value sorts as a number, commas and all",
                  order[:3] == ["Ring road widening", "MSAMB ERP rollout", "Zonal metering AMC"],
                  str(order))
            check("a blank value sorts last in both directions",
                  order[-1] == "apron resurfacing"
                  and titles(client.get("/tenders/",
                                        params={"sort": "-estimated_value"}).json())[-1]
                  == "apron resurfacing", str(order))

        # --- sorting and paging -----------------------------------------
        order = titles(client.get("/tenders/", params={"sort": "title"}).json())
        check("sort by title is case-insensitive and ascending",
              order == ["apron resurfacing", "MSAMB ERP rollout", "Ring road widening",
                        "Zonal metering AMC"], str(order))
        order = titles(client.get("/tenders/", params={"sort": "submission_deadline"}).json())
        check("sort by deadline puts the undated last",
              order[-1] == "apron resurfacing", str(order))
        check("an unknown sort field falls back to the default instead of erroring",
              client.get("/tenders/", params={"sort": "nonsense"}).status_code == 200)

        one = client.get("/tenders/", params={"sort": "title", "page": 1, "page_size": 2}).json()
        two = client.get("/tenders/", params={"sort": "title", "page": 2, "page_size": 2}).json()
        check("total counts every match, not just the page", one["total"] == 4, str(one["total"]))
        check("page 2 continues where page 1 stopped, with no repeats",
              set(titles(one)).isdisjoint(titles(two)) and len(titles(two)) == 2,
              f"{titles(one)} then {titles(two)}")

        # --- vocabularies ------------------------------------------------
        check("GET /tenders/authorities lists distinct authorities, sorted",
              client.get("/tenders/authorities").json()
              == ["Airports Authority", "MSAMB", "NHAI"])
        check("GET /tenders/tags lists the tag vocabulary, sorted",
              client.get("/tenders/tags").json() == ["Aviation", "ERP", "IT", "Roads"])
        check("/tenders/authorities is not swallowed by the /{id} route",
              isinstance(client.get("/tenders/authorities").json(), list))

        # --- nested cost items ------------------------------------------
        res = client.post(f"/tenders/{erp_id}/cost-items", json={
            "cost_type": "EMD", "amount": "50000", "payment_mode": "DD",
            "instrument_number": "DD-4411", "paid_on": "2026-08-20",
        })
        ok = check("POST /tenders/{id}/cost-items -> 201", res.status_code == 201, res.text)
        if not ok:
            return
        after_cost = res.json()
        cost = after_cost["cost_items"][0]
        check("the cost item got its own id and created_at",
              isinstance(cost["id"], str) and len(cost["id"]) == 24 and bool(cost["created_at"]))
        check("adding a cost bumps the tender's updated_at",
              instant(after_cost["updated_at"]) > instant(erp["updated_at"]))

        res = client.put(f"/tenders/{erp_id}/cost-items/{cost['id']}", json={
            "cost_type": "EMD", "amount": "60000", "payment_mode": "DD",
            "instrument_number": "DD-4411", "paid_on": "2026-08-20",
            "document_id": "abc123abc123abc123abc123", "file_name": "emd_receipt.pdf",
        })
        ok = check("PUT /tenders/{id}/cost-items/{id} -> 200", res.status_code == 200, res.text)
        if ok:
            edited = res.json()["cost_items"][0]
            check("the cost edit was saved",
                  edited["amount"] == "60000" and edited["file_name"] == "emd_receipt.pdf",
                  str(edited))
            check("the cost kept its id and created_at through the edit",
                  edited["id"] == cost["id"] and edited["created_at"] == cost["created_at"])
        check("editing a cost that does not exist -> 404",
              client.put(f"/tenders/{erp_id}/cost-items/000000000000000000000000",
                         json={"cost_type": "EMD", "amount": "1"}).status_code == 404)

        res = client.delete(f"/tenders/{erp_id}/cost-items/{cost['id']}")
        check("DELETE cost item -> 200 with the updated tender",
              res.status_code == 200 and res.json()["cost_items"] == [], res.text)
        check("deleting the same cost again -> 404",
              client.delete(f"/tenders/{erp_id}/cost-items/{cost['id']}").status_code == 404)

        # --- nested certificates ----------------------------------------
        res = client.post(f"/tenders/{erp_id}/certificates", json={
            "name": "GST Registration", "issuing_body": "GSTN",
            "valid_from": "2020-01-01", "valid_to": "",
        })
        ok = check("POST /tenders/{id}/certificates -> 201", res.status_code == 201, res.text)
        if not ok:
            return
        certificate = res.json()["certificates"][0]
        res = client.put(f"/tenders/{erp_id}/certificates/{certificate['id']}", json={
            "name": "GST Registration", "issuing_body": "GSTN",
            "valid_from": "2020-01-01", "valid_to": "2030-01-01",
        })
        check("PUT certificate -> 200 and saved",
              res.status_code == 200
              and res.json()["certificates"][0]["valid_to"] == "2030-01-01", res.text)
        res = client.delete(f"/tenders/{erp_id}/certificates/{certificate['id']}")
        check("DELETE certificate -> 200 with the updated tender",
              res.status_code == 200 and res.json()["certificates"] == [], res.text)

        # --- attached documents -----------------------------------------
        res = client.post("/documents/", data={
            "document_type": "Tender Notice", "category": "Tendering",
            "client_name": "MSAMB", "project_title": "MSAMB ERP rollout",
            "submitted_by": "vansh",
        })
        doc_id = res.json()["id"] if res.status_code == 201 else None
        check("a document exists in the log to attach", doc_id is not None, res.text)

        res = client.post(f"/tenders/{erp_id}/documents/{doc_id}")
        check("POST /tenders/{id}/documents/{id} attaches -> 200",
              res.status_code == 200 and res.json()["document_ids"] == [doc_id], res.text)
        again = client.post(f"/tenders/{erp_id}/documents/{doc_id}")
        check("attaching the same document twice is a no-op",
              again.json()["document_ids"] == [doc_id], str(again.json()["document_ids"]))
        check("attaching an unknown document -> 404",
              client.post(f"/tenders/{erp_id}/documents/000000000000000000000000").status_code == 404)

        client.delete(f"/documents/{doc_id}")
        res = client.delete(f"/tenders/{erp_id}/documents/{doc_id}")
        check("detaching an already-deleted document still works",
              res.status_code == 200 and res.json()["document_ids"] == [], res.text)

        # --- update ------------------------------------------------------
        client.post(f"/tenders/{erp_id}/cost-items", json={"cost_type": "Tender fee", "amount": "5000"})
        res = client.put(f"/tenders/{erp_id}", json={
            "title": "MSAMB ERP rollout", "issuing_authority": "MSAMB", "status": "Submitted",
        })
        ok = check("PUT /tenders/{id} -> 200", res.status_code == 200, res.text)
        if ok:
            edited = res.json()
            check("the edited field was saved", edited["status"] == "Submitted")
            check("a field left out of the payload is cleared, not stale",
                  edited["submission_mode"] is None, repr(edited.get("submission_mode")))
            check("cost items survive a form edit", len(edited["cost_items"]) == 1)
            check("created_at is unchanged by an edit",
                  instant(edited["created_at"]) == instant(erp["created_at"]))
            check("updated_at moves on an edit",
                  instant(edited["updated_at"]) > instant(erp["updated_at"]))
        check("PUT to an unknown tender -> 404",
              client.put("/tenders/000000000000000000000000",
                         json={"title": "X", "issuing_authority": "Y",
                               "status": "Won"}).status_code == 404)

        # --- delete ------------------------------------------------------
        res = client.post("/documents/", data={
            "document_type": "Payment Receipt", "category": "Tendering",
            "client_name": "MSAMB", "project_title": "EMD", "submitted_by": "vansh",
        })
        surviving_doc = res.json()["id"]

        res = client.delete(f"/tenders/{erp_id}")
        check("DELETE /tenders/{id} -> 204", res.status_code == 204, res.text)
        check("the tender is gone", client.get(f"/tenders/{erp_id}").status_code == 404)
        check("uploaded files stay in the document log, as the dialog promises",
              client.get(f"/documents/{surviving_doc}").status_code == 200)
        check("DELETE of an already-deleted tender -> 404",
              client.delete(f"/tenders/{erp_id}").status_code == 404)
        check("the remaining tenders are untouched",
              client.get("/tenders/").json()["total"] == 3)

        # --- the neighbours still work alongside ------------------------
        check("GET /documents/ still lists documents",
              client.get("/documents/").status_code == 200)
        check("GET /projects/ still answers", client.get("/projects/").status_code == 200)
        check("GET /employees/ still answers", client.get("/employees/").status_code == 200)
        check("/health reports the tenders collection",
              "tenders_collection" in client.get("/health").json())

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
    run_suite(database["tenders"], database["documents"], offline=True)
    return 0


def main_live():
    from app import mongodb

    print(f"Mode: live — {mongodb.safe_uri()}\n")
    reachable, error = mongodb.ping()
    if not check("MongoDB is reachable", reachable, error or ""):
        print("\nMongoDB is not running (or not installed) on this machine.")
        print("Install and start it — SETUP.md, section 1b — then run this again.")
        return 1

    scratch_name = mongodb.MONGO_DB_NAME + "_verify_tenders"
    client = mongodb.get_client()
    print(f"  (using throwaway database '{scratch_name}'; "
          f"'{mongodb.MONGO_DB_NAME}' is not touched)\n")
    try:
        run_suite(client[scratch_name]["tenders"], client[scratch_name]["documents"])
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
