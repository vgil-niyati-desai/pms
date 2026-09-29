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


# One valid payload per criterion kind, with the category each belongs to.
CRITERIA_SAMPLES = [
    ({"kind": "similar_projects", "clause_ref": " Sec 3.2(a) ",
      "source_text": "Three similar works of at least Rs 2 Cr each in the last 7 years.",
      "params": {"count": 3, "work_description": " Water supply pipelines ",
                 "min_value_each": 20000000, "completed_within_years": 7,
                 "must_be_completed": True}}, "technical"),
    ({"kind": "project_value", "mandatory": False,
      "params": {"min_value": 50000000, "basis": "single", "within_years": 5}}, "technical"),
    ({"kind": "personnel",
      "params": {"role": "Project Manager", "count": 1, "min_experience_years": 15,
                 "experience_basis": "total", "qualification": "B.E. Civil",
                 "required_certifications": ["PMP", " pmp ", "", "PRINCE2"]}}, "personnel"),
    ({"kind": "certification",
      "params": {"name": "ISO 9001:2015", "issuing_body": "BSI", "valid_on": "2026-09-15"}},
     "documents"),
    ({"kind": "document",
      "params": {"document_type": "Completion Certificate", "count": 3,
                 "description": "For each similar work cited"}}, "documents"),
    ({"kind": "financial",
      "params": {"metric": "average_annual_turnover", "min_amount": 100000000,
                 "period_years": 3}}, "financial"),
    ({"kind": "other", "notes": "Check with legal",
      "params": {"description": "Bidder must not be blacklisted by any government body."}},
     "other"),
]

# Each of these must be refused with a 422, and must leave nothing behind.
CRITERIA_INVALID = [
    ("an unknown kind", {"kind": "client_type", "params": {"types": ["Govt"]}}),
    ("a missing kind", {"params": {"description": "x"}}),
    ("missing params", {"kind": "other"}),
    ("similar_projects without a count", {"kind": "similar_projects", "params": {}}),
    ("similar_projects with a count of 0", {"kind": "similar_projects", "params": {"count": 0}}),
    ("a negative project value", {"kind": "project_value", "params": {"min_value": -5}}),
    ("a zero project value", {"kind": "project_value", "params": {"min_value": 0}}),
    ("an unknown value basis", {"kind": "project_value", "params": {"min_value": 5, "basis": "median"}}),
    ("personnel with a blank role", {"kind": "personnel", "params": {"role": "   "}}),
    ("personnel experience over 60 years",
     {"kind": "personnel", "params": {"role": "PM", "min_experience_years": 61}}),
    ("an unknown experience basis",
     {"kind": "personnel", "params": {"role": "PM", "experience_basis": "lifetime"}}),
    ("certification without a name", {"kind": "certification", "params": {"name": ""}}),
    ("certification with a malformed date",
     {"kind": "certification", "params": {"name": "ISO", "valid_on": "15/09/2026"}}),
    ("document without a type", {"kind": "document", "params": {"description": "x"}}),
    ("an unknown financial metric",
     {"kind": "financial", "params": {"metric": "profit", "min_amount": 1}}),
    ("a negative financial amount",
     {"kind": "financial", "params": {"metric": "net_worth", "min_amount": -1}}),
    ("a financial 'other' with no description",
     {"kind": "financial", "params": {"metric": "other", "min_amount": 1}}),
    ("other without a description", {"kind": "other", "params": {"description": "  "}}),
    ("an unknown field inside params",
     {"kind": "other", "params": {"description": "x", "weight": 10}}),
    ("params belonging to another kind",
     {"kind": "document", "params": {"role": "PM"}}),
    ("a client-supplied id", {"kind": "other", "id": "abc", "params": {"description": "x"}}),
    ("a client-supplied category",
     {"kind": "other", "category": "financial", "params": {"description": "x"}}),
    ("an unknown top-level field",
     {"kind": "other", "marks": 5, "params": {"description": "x"}}),
    ("source wording over 2000 characters",
     {"kind": "other", "source_text": "x" * 2001, "params": {"description": "x"}}),
]


def _check_criteria(client, tenders):
    """Qualification criteria: all seven kinds, their validation, the cap,
    and that nothing else on the tender is disturbed by them."""
    from bson import ObjectId

    # A tender stored before criteria existed has no such field at all.
    legacy_oid = tenders.insert_one({
        "title": "Legacy tender", "tags": [], "cost_items": [], "certificates": [],
        "document_ids": [], "cited_project_ids": [],
        "created_at": __import__("datetime").datetime(2026, 1, 1),
        "updated_at": __import__("datetime").datetime(2026, 1, 1),
    }).inserted_id
    legacy_id = str(legacy_oid)
    res = client.get(f"/tenders/{legacy_id}")
    check("a tender saved before criteria existed reads as criteria: []",
          res.status_code == 200 and res.json()["criteria"] == [], res.text)
    check("so does its row in the tender list",
          any(t["id"] == legacy_id and t["criteria"] == []
              for t in client.get("/tenders/?view=all&page_size=200").json()["items"]))
    res = client.post(f"/tenders/{legacy_id}/criteria", json=CRITERIA_SAMPLES[6][0])
    check("a criterion can be added to such a tender",
          res.status_code == 201 and len(res.json()["criteria"]) == 1, res.text)
    client.delete(f"/tenders/{legacy_id}")

    # A tender carrying one of everything else, to prove criteria leave it be.
    res = client.post("/tenders/", json={"title": "Criteria check", "estimated_value": "90,00,000",
                                         "status": "Preparing", "tags": ["Water"]})
    tender = res.json()
    tid = tender["id"]
    check("a new tender starts with criteria: []", tender["criteria"] == [], str(tender))
    client.post(f"/tenders/{tid}/cost-items", json={"cost_type": "EMD", "amount": "90000"})
    client.post(f"/tenders/{tid}/certificates", json={"name": "Solvency certificate"})
    tenders.update_one({"_id": ObjectId(tid)},
                       {"$set": {"cited_project_ids": ["0123456789abcdef01234567"],
                                 "document_ids": ["0123456789abcdef01234568"]}})
    before = client.get(f"/tenders/{tid}").json()

    # --- all seven kinds --------------------------------------------------
    added = []
    for payload, category in CRITERIA_SAMPLES:
        res = client.post(f"/tenders/{tid}/criteria", json=payload)
        if not check(f"POST a {payload['kind']} criterion -> 201", res.status_code == 201, res.text):
            continue
        item = res.json()["criteria"][-1]
        added.append(item)
        check(f"{payload['kind']}: category is {category}", item["category"] == category, str(item))
        check(f"{payload['kind']}: has a server id and both timestamps",
              len(item["id"]) == 24 and item["created_at"] and item["updated_at"], str(item))
        check(f"{payload['kind']}: mandatory defaults to true unless given",
              item["mandatory"] is payload.get("mandatory", True), str(item))
    check("every criterion id is unique", len({c["id"] for c in added}) == len(added) == 7)

    by_kind = {c["kind"]: c for c in added}
    sp = by_kind.get("similar_projects", {})
    check("text is trimmed (clause reference, work description)",
          sp.get("clause_ref") == "Sec 3.2(a)"
          and sp.get("params", {}).get("work_description") == "Water supply pipelines", str(sp))
    check("amounts are stored as numbers",
          sp.get("params", {}).get("min_value_each") == 20000000
          and by_kind.get("financial", {}).get("params", {}).get("min_amount") == 100000000)
    check("required certifications are trimmed and de-duplicated",
          by_kind.get("personnel", {}).get("params", {}).get("required_certifications")
          == ["PMP", "PRINCE2"], str(by_kind.get("personnel")))
    check("optional params come back with their defaults",
          by_kind.get("project_value", {}).get("params", {}).get("basis") == "single"
          and by_kind.get("personnel", {}).get("params", {}).get("experience_basis") == "total")

    # --- validation ---------------------------------------------------------
    count_before = len(client.get(f"/tenders/{tid}").json()["criteria"])
    for label, payload in CRITERIA_INVALID:
        res = client.post(f"/tenders/{tid}/criteria", json=payload)
        check(f"refused: {label} -> 422", res.status_code == 422, f"{res.status_code} {res.text[:200]}")
    check("refused criteria leave nothing behind",
          len(client.get(f"/tenders/{tid}").json()["criteria"]) == count_before)
    check("POST to a tender that does not exist -> 404",
          client.post("/tenders/0123456789abcdef01234567/criteria",
                      json=CRITERIA_SAMPLES[6][0]).status_code == 404)
    check("POST to a malformed tender id -> 404",
          client.post("/tenders/not-an-id/criteria", json=CRITERIA_SAMPLES[6][0]).status_code == 404)

    # --- update -------------------------------------------------------------
    target = by_kind["personnel"]
    changed = {"kind": "personnel", "mandatory": False, "clause_ref": "Annex B",
               "params": {"role": "Site Engineer", "count": 2, "min_experience_years": 5}}
    res = client.put(f"/tenders/{tid}/criteria/{target['id']}", json=changed)
    if check("PUT a criterion -> 200", res.status_code == 200, res.text):
        after = next(c for c in res.json()["criteria"] if c["id"] == target["id"])
        check("the update replaced its contents",
              after["params"]["role"] == "Site Engineer" and after["params"]["count"] == 2
              and after["mandatory"] is False and after["clause_ref"] == "Annex B"
              and after["params"]["required_certifications"] == []
              and after["params"]["qualification"] is None, str(after))
        check("its id and created_at are kept",
              after["id"] == target["id"] and after["created_at"] == target["created_at"])
        check("its updated_at moved on", after["updated_at"] >= target["updated_at"])
        check("its position in the list is kept",
              [c["id"] for c in res.json()["criteria"]] == [c["id"] for c in added])
        others = [c for c in res.json()["criteria"] if c["id"] != target["id"]]
        check("the other criteria are untouched",
              others == [c for c in added if c["id"] != target["id"]])
    res = client.put(f"/tenders/{tid}/criteria/{target['id']}",
                     json={"kind": "other", "params": {"description": "x"}})
    check("changing a criterion's kind -> 400", res.status_code == 400, res.text)
    res = client.put(f"/tenders/{tid}/criteria/{target['id']}",
                     json={"kind": "personnel", "params": {"role": ""}})
    check("an invalid update -> 422", res.status_code == 422, res.text)
    check("PUT a criterion that does not exist -> 404",
          client.put(f"/tenders/{tid}/criteria/nope", json=CRITERIA_SAMPLES[6][0]).status_code == 404)

    # --- the tender form cannot touch criteria ------------------------------
    stored = client.get(f"/tenders/{tid}").json()["criteria"]
    # The whole form, as the tender form sends it, plus a criteria list that
    # must be ignored.
    form = {key: before[key] for key in (
        "title", "issuing_authority", "reference_number", "tender_type", "estimated_value",
        "emd_amount", "tender_fee", "published_date", "submission_deadline", "status",
        "submission_mode", "notes", "tags")}
    form.update({"title": "Criteria check (renamed)", "criteria": []})
    res = client.put(f"/tenders/{tid}", json=form)
    check("saving the tender form -> 200", res.status_code == 200, res.text)
    check("saving the tender form does not wipe criteria",
          res.json()["criteria"] == stored and res.json()["title"] == "Criteria check (renamed)")

    # --- delete -------------------------------------------------------------
    victim = by_kind["other"]["id"]
    res = client.delete(f"/tenders/{tid}/criteria/{victim}")
    check("DELETE a criterion -> 200 and it is gone",
          res.status_code == 200 and victim not in [c["id"] for c in res.json()["criteria"]]
          and len(res.json()["criteria"]) == len(stored) - 1, res.text)
    check("deleting it again -> 404",
          client.delete(f"/tenders/{tid}/criteria/{victim}").status_code == 404)

    # --- the rest of the tender survived all of it --------------------------
    after_all = client.get(f"/tenders/{tid}").json()
    check("cost items, certificates, documents and citations are unchanged",
          after_all["cost_items"] == before["cost_items"]
          and after_all["certificates"] == before["certificates"]
          and after_all["document_ids"] == before["document_ids"]
          and after_all["cited_project_ids"] == before["cited_project_ids"]
          and after_all["estimated_value"] == "90,00,000" and after_all["tags"] == ["Water"],
          str(after_all))

    # --- the cap --------------------------------------------------------------
    room = 100 - len(after_all["criteria"])
    statuses = {client.post(f"/tenders/{tid}/criteria",
                            json={"kind": "other", "params": {"description": f"Clause {n}"}}).status_code
                for n in range(room)}
    full = client.get(f"/tenders/{tid}").json()["criteria"]
    check("criteria can be added up to 100", statuses == {201} and len(full) == 100,
          f"{statuses} {len(full)}")
    res = client.post(f"/tenders/{tid}/criteria", json=CRITERIA_SAMPLES[6][0])
    check("the 101st criterion -> 409", res.status_code == 409, res.text)
    check("and the list stays at 100", len(client.get(f"/tenders/{tid}").json()["criteria"]) == 100)
    check("all 100 ids are unique", len({c["id"] for c in full}) == 100)
    res = client.put(f"/tenders/{tid}/criteria/{full[-1]['id']}",
                     json={"kind": "other", "params": {"description": "Edited at the cap"}})
    check("a full list can still be edited", res.status_code == 200, res.text)
    client.delete(f"/tenders/{tid}/criteria/{full[0]['id']}")
    check("after a delete there is room again",
          client.post(f"/tenders/{tid}/criteria", json=CRITERIA_SAMPLES[6][0]).status_code == 201)

    client.delete(f"/tenders/{tid}")


def _check_criteria_validation(client):
    """The four validation rules added after review: no Infinity or NaN,
    strict types, at most 50 required certifications, and errors that name
    the field they are about."""
    tid = client.post("/tenders/", json={"title": "Criteria validation check"}).json()["id"]
    url = f"/tenders/{tid}/criteria"

    def messages(res):
        detail = res.json().get("detail") if res.status_code == 422 else None
        return [d["msg"] for d in detail] if isinstance(detail, list) else []

    def raw(body):
        # Infinity and NaN are sent as raw JSON text: they are what a client
        # that does not guard against them actually puts on the wire.
        return client.post(url, content=body.encode(), headers={"Content-Type": "application/json"})

    # --- 1. Infinity and NaN ---------------------------------------------------
    for label, body, expected in [
        ("Infinity", '{"kind":"project_value","params":{"min_value":Infinity}}',
         "Minimum value: must be a finite number, not Infinity or NaN"),
        ("-Infinity", '{"kind":"financial","params":{"metric":"net_worth","min_amount":-Infinity}}',
         "Minimum amount: must be a finite number, not Infinity or NaN"),
        ("NaN", '{"kind":"financial","params":{"metric":"net_worth","min_amount":NaN}}',
         "Minimum amount: must be a finite number, not Infinity or NaN"),
        ("1e999 (overflows to Infinity)", '{"kind":"project_value","params":{"min_value":1e999}}',
         "Minimum value: must be a finite number, not Infinity or NaN"),
        ("Infinity in an optional amount",
         '{"kind":"similar_projects","params":{"count":1,"min_value_each":Infinity}}',
         "Minimum value of each: must be a finite number, not Infinity or NaN"),
        ("NaN in a years field",
         '{"kind":"personnel","params":{"role":"PM","min_experience_years":NaN}}',
         "Minimum experience (years): must be a finite number, not Infinity or NaN"),
        ('the string "inf"', '{"kind":"project_value","params":{"min_value":"inf"}}',
         "Minimum value: must be a number"),
    ]:
        res = raw(body)
        check(f"{label} is refused -> 422, naming the field",
              res.status_code == 422 and messages(res) == [expected], f"{res.status_code} {res.text[:200]}")

    # --- 2. strict types ---------------------------------------------------------
    for label, payload, expected in [
        ('a count sent as "3"', {"kind": "similar_projects", "params": {"count": "3"}},
         "Number of similar projects: must be a whole number"),
        ("a count sent as true", {"kind": "similar_projects", "params": {"count": True}},
         "Number of similar projects: must be a whole number"),
        ("a count sent as 3.5", {"kind": "similar_projects", "params": {"count": 3.5}},
         "Number of similar projects: must be a whole number"),
        ("financial years sent as 3.0", {"kind": "financial",
                                         "params": {"metric": "net_worth", "min_amount": 1, "period_years": 3.0}},
         "Over the last (financial years): must be a whole number"),
        ('an amount sent as "5000"', {"kind": "project_value", "params": {"min_value": "5000"}},
         "Minimum value: must be a number"),
        ("an amount sent as true", {"kind": "project_value", "params": {"min_value": True}},
         "Minimum value: must be a number"),
        ('mandatory sent as "yes"', {"kind": "other", "mandatory": "yes", "params": {"description": "x"}},
         "Requirement (mandatory or desirable): must be true or false"),
        ("mandatory sent as 1", {"kind": "other", "mandatory": 1, "params": {"description": "x"}},
         "Requirement (mandatory or desirable): must be true or false"),
        ('a checkbox sent as "true"',
         {"kind": "similar_projects", "params": {"count": 1, "must_be_completed": "true"}},
         "Must be completed, not ongoing: must be true or false"),
        ("a clause reference sent as a number", {"kind": "other", "clause_ref": 12, "params": {"description": "x"}},
         "Clause reference: must be text"),
    ]:
        res = client.post(url, json=payload)
        check(f"strict: {label} is refused -> 422, naming the field",
              res.status_code == 422 and messages(res) == [expected], f"{res.status_code} {res.text[:200]}")

    res = client.post(url, json={"kind": "similar_projects", "mandatory": False,
                                 "params": {"count": 2, "min_value_each": 5000000,
                                            "completed_within_years": 7, "must_be_completed": True}})
    stored = res.json()["criteria"][-1]["params"] if res.status_code == 201 else {}
    check("strict: a whole number is still accepted where a decimal is expected",
          res.status_code == 201 and stored.get("min_value_each") == 5000000
          and stored.get("completed_within_years") == 7, res.text[:200])

    # --- 3. at most 50 required certifications -----------------------------------
    def personnel(certifications):
        return {"kind": "personnel", "params": {"role": "PM", "required_certifications": certifications}}

    res = client.post(url, json=personnel([f"Cert {n}" for n in range(50)]))
    check("50 required certifications are accepted",
          res.status_code == 201 and len(res.json()["criteria"][-1]["params"]["required_certifications"]) == 50,
          res.text[:200])
    res = client.post(url, json=personnel([f"Cert {n}" for n in range(51)]))
    check("51 required certifications are refused -> 422, naming the field",
          res.status_code == 422 and messages(res) == ["Certifications held: must list at most 50 entries"],
          f"{res.status_code} {res.text[:200]}")
    res = client.post(url, json=personnel([f"Cert {n}" for n in range(50)] + ["cert 1", " ", "CERT 2", ""]))
    check("the limit counts what would be stored: 50 plus repeats and blanks is accepted",
          res.status_code == 201 and len(res.json()["criteria"][-1]["params"]["required_certifications"]) == 50,
          res.text[:200])
    target = res.json()["criteria"][-1]["id"] if res.status_code == 201 else "missing"
    res = client.put(f"{url}/{target}", json=personnel([f"Cert {n}" for n in range(60)]))
    check("the limit applies to edits too -> 422",
          res.status_code == 422 and messages(res) == ["Certifications held: must list at most 50 entries"],
          f"{res.status_code} {res.text[:200]}")

    # --- 4. errors name the field -----------------------------------------------------
    for label, payload, expected in [
        ("a missing kind", {"params": {"description": "x"}}, ["Type: is required"]),
        ("an unknown kind", {"kind": "client_type", "params": {}},
         ["Type: must be one of certification, document, financial, other, personnel, project_value, similar_projects"]),
        ("a blank role", {"kind": "personnel", "params": {"role": "   "}}, ["Role / designation: is required"]),
        ("a missing role", {"kind": "personnel", "params": {}}, ["Role / designation: is required"]),
        ("a count below 1, by kind", {"kind": "document", "params": {"document_type": "X", "count": 0}},
         ["How many: must be at least 1"]),
        ("a zero project value", {"kind": "project_value", "params": {"min_value": 0}},
         ["Minimum value: must be more than 0"]),
        ("years over 60", {"kind": "personnel", "params": {"role": "PM", "min_experience_years": 61}},
         ["Minimum experience (years): must be at most 60"]),
        ("an unknown basis", {"kind": "project_value", "params": {"min_value": 5, "basis": "median"}},
         ["Measured as: must be 'single', 'total' or 'average'"]),
        ("a malformed date", {"kind": "certification", "params": {"name": "ISO", "valid_on": "2026-02-30"}},
         ["Must be valid on: must be a date in YYYY-MM-DD form"]),
        ("financial Other with no description",
         {"kind": "financial", "params": {"metric": "other", "min_amount": 1}},
         ["Describe the requirement: required when the requirement is Other"]),
        ("an over-long clause reference",
         {"kind": "other", "clause_ref": "x" * 201, "params": {"description": "x"}},
         ["Clause reference: must be at most 200 characters"]),
        ("an over-long certification entry",
         personnel(["fine", "x" * 501]), ["Certifications held: entry 2 must be at most 500 characters"]),
        ("an unknown field", {"kind": "other", "params": {"description": "x", "weight": 10}},
         ["'weight': is not an accepted field"]),
        ("several problems at once",
         {"kind": "similar_projects", "mandatory": "no", "params": {"count": 0, "completed_within_years": 90}},
         ["Requirement (mandatory or desirable): must be true or false",
          "Number of similar projects: must be at least 1",
          "Within the last (years): must be at most 60"]),
    ]:
        res = client.post(url, json=payload)
        got = messages(res)
        check(f"errors name the field: {label}",
              res.status_code == 422 and sorted(got) == sorted(expected), f"{res.status_code} {got}")
    res = client.post(url, json={"kind": "personnel", "params": {"role": ""}})
    detail = res.json().get("detail", [{}])[0] if res.status_code == 422 else {}
    check("the 422 keeps FastAPI's shape: loc from the body, a type, and the message",
          detail.get("loc", [None])[0] == "body" and "role" in detail.get("loc", [])
          and bool(detail.get("type")), str(detail))
    res = client.put(f"{url}/{target}", json={"kind": "personnel", "params": {"role": " "}})
    check("an edit reports its errors the same way",
          res.status_code == 422 and messages(res) == ["Role / designation: is required"], res.text[:200])

    count = len(client.get(f"/tenders/{tid}").json()["criteria"])
    check("only the valid criteria were stored", count == 3, str(count))
    client.delete(f"/tenders/{tid}")


def _check_candidates(client):
    """Matching V1: the candidates endpoint answers each criterion kind from
    recorded data only, says what it cannot decide, and writes nothing."""
    created = {"projects": [], "employees": [], "documents": [], "tenders": []}

    def project(**fields):
        record = client.post("/projects/", json={"client_name": "Match Client", **fields}).json()
        created["projects"].append(record["id"])
        return record

    def document(document_type, **fields):
        data = {"document_type": document_type, "category": "Matching", "client_name": "Match Client",
                "submitted_by": "verify"}
        data.update(fields)
        record = client.post("/documents/", data=data).json()
        created["documents"].append(record["id"])
        return record

    def employee(name, designation=None, certifications=()):
        record = client.post("/employees/", json={"full_name": name, "designation": designation}).json()
        created["employees"].append(record["id"])
        for certification in certifications:
            client.post(f"/employees/{record['id']}/certifications", json=certification)
        return record

    # Projects: each shape the rules tell apart.
    completed = project(title="Match A completed", status="Completed")
    document("Work Order", project_id=completed["id"])
    certified_old = project(title="Match B certified, ended 2010", end_date="2010-01-01")
    old_certificate = document("Completion Certificate", project_id=certified_old["id"])
    ongoing = project(title="Match C ongoing", status="Ongoing")
    document("LOI", project_id=ongoing["id"])
    no_evidence = project(title="Match D completed, no documents", status="Completed")
    certified_recent = project(title="Match E certified, ended 2024", end_date="2024-06-01")
    recent_certificate = document("Completion Certificate", project_id=certified_recent["id"])

    # Employees.
    senior = employee("Asha Senior", "Senior Project Manager", [{"name": "PMP"}])
    expired = employee("Ravi Expired", "project manager", [{"name": "pmp", "expiry_date": "2020-01-01"}])
    lead = employee("Tara Lead", "Team Leader", [{"name": "PMP"}])
    employee("Nobody Unassigned")
    analytics = employee("Dev Analytics", "Data & Analytics Lead")

    # Documents for the certification criterion: one the company's, one a person's.
    company_iso = document("Certificate", project_title="ISO 9001:2015 certificate")
    personal_iso = document("Certification", project_title="ISO 9001 personal auditor")
    client.post(f"/employees/{lead['id']}/certifications",
                json={"name": "ISO 9001 auditor", "document_id": personal_iso["id"]})

    tender = client.post("/tenders/", json={"title": "Matching check"}).json()
    tid = tender["id"]
    created["tenders"].append(tid)
    client.post(f"/tenders/{tid}/documents/{recent_certificate['id']}")

    def criterion(payload):
        res = client.post(f"/tenders/{tid}/criteria", json=payload)
        return res.json()["criteria"][-1]["id"]

    def candidates(criterion_id):
        res = client.get(f"/tenders/{tid}/criteria/{criterion_id}/candidates")
        return res.status_code, (res.json() if res.status_code == 200 else res.text)

    def ids(result):
        return [c["id"] for c in result["candidates"]]

    def one(result, record_id):
        return next((c for c in result["candidates"] if c["id"] == record_id), {})

    # --- similar projects -----------------------------------------------------------
    completed_only = criterion({"kind": "similar_projects", "params": {
        "count": 2, "must_be_completed": True, "work_description": "water pipelines", "min_value_each": 1000}})
    status, result = candidates(completed_only)
    check("similar projects -> 200, rules applied", status == 200 and result["mode"] == "matched", str(result)[:200])
    check("completion is read from a recorded status or a Completion Certificate",
          sorted(ids(result)) == sorted([completed["id"], certified_old["id"], certified_recent["id"]]),
          str(ids(result)))
    check("an ongoing project, and one with no evidence documents, are not candidates",
          ongoing["id"] not in ids(result) and no_evidence["id"] not in ids(result))
    check("each project says why: recorded status or the certificate, and the evidence held",
          one(result, completed["id"]).get("reasons") == ["Recorded as Completed", "Evidence held: Work Order"]
          and one(result, certified_old["id"]).get("reasons")
          == ["Holds a Completion Certificate", "Evidence held: Completion Certificate"],
          str(result["candidates"]))
    check("similarity is never claimed: the note says it cannot be determined",
          any("similar to “water pipelines” cannot be determined" in n for n in result["notes"]), str(result["notes"]))
    check("a minimum value per project is reported as not recorded",
          any("project value not recorded" in n for n in result["notes"]), str(result["notes"]))
    check("the summary counts candidates against the number asked for",
          result["summary"] == "3 projects found with evidence of completion; the criterion asks for at least 2.",
          result["summary"])

    any_status = criterion({"kind": "similar_projects", "params": {"count": 1}})
    status, result = candidates(any_status)
    check("without must-be-completed, every project with evidence is a candidate",
          sorted(ids(result)) == sorted([completed["id"], certified_old["id"], ongoing["id"], certified_recent["id"]]),
          str(ids(result)))
    check("a recorded status other than Completed is shown as it is",
          one(result, ongoing["id"]).get("reasons") == ["Status: Ongoing", "Evidence held: LOI"],
          str(one(result, ongoing["id"])))

    within = criterion({"kind": "similar_projects", "params": {
        "count": 1, "must_be_completed": True, "completed_within_years": 7}})
    status, result = candidates(within)
    check("a recorded end date outside the window excludes the project",
          certified_old["id"] not in ids(result), str(ids(result)))
    check("a recorded end date inside the window is a reason",
          "Ended 2024-06-01, within the last 7 years" in one(result, certified_recent["id"]).get("reasons", []),
          str(one(result, certified_recent["id"])))
    check("no end date recorded is reported as not checkable, not as a pass or a fail",
          one(result, completed["id"]).get("missing")
          == ["End date not recorded — cannot check the last 7 years"], str(one(result, completed["id"])))

    client.post(f"/tenders/{tid}/projects/{completed['id']}")
    status, result = candidates(completed_only)
    check("a project the tender already cites is marked as cited",
          one(result, completed["id"]).get("cited") is True
          and one(result, certified_old["id"]).get("cited") is False)

    # --- project value ----------------------------------------------------------------
    value = criterion({"kind": "project_value", "params": {"min_value": 50000000}})
    status, result = candidates(value)
    check("project value is not matched: manual, with the not-recorded note and no candidates",
          status == 200 and result["mode"] == "manual" and result["candidates"] == []
          and result["notes"] == ["Cannot determine — project value not recorded."], str(result))

    # --- personnel ----------------------------------------------------------------------
    manager = criterion({"kind": "personnel", "params": {
        "role": "Project Manager", "count": 2, "min_experience_years": 10, "qualification": "B.E. Civil",
        "required_certifications": ["PMP", "PRINCE2"]}})
    status, result = candidates(manager)
    check("personnel: designations containing the role, case-blind",
          ids(result) == [senior["id"], expired["id"]], str(ids(result)))
    check("personnel: someone with another designation is not a candidate, whatever they hold",
          lead["id"] not in ids(result))
    check("personnel: each required certification is reported held or not",
          one(result, senior["id"]).get("reasons") == ["Designation: Senior Project Manager", "Holds PMP"]
          and one(result, senior["id"]).get("missing") == ["No PRINCE2 certification recorded"],
          str(one(result, senior["id"])))
    check("personnel: an expired certification does not count as held",
          one(result, expired["id"]).get("missing")
          == ["pmp expired on 2020-01-01", "No PRINCE2 certification recorded"], str(one(result, expired["id"])))
    check("personnel: experience and qualification are said not to be checked, and nothing is saved",
          any("Experience is not checked" in n for n in result["notes"])
          and any("Qualification “B.E. Civil” is not checked" in n for n in result["notes"])
          and any("Suggestions only" in n for n in result["notes"]), str(result["notes"]))
    analytics_role = criterion({"kind": "personnel", "params": {"role": "data and analytics lead"}})
    status, result = candidates(analytics_role)
    check("personnel: '&' and 'and' read as the same word",
          ids(result) == [analytics["id"]], str(ids(result)))

    # --- certification (company) ----------------------------------------------------------
    iso = criterion({"kind": "certification", "params": {"name": "ISO 9001"}})
    status, result = candidates(iso)
    check("certification: documents whose details mention it are leads",
          ids(result) == [company_iso["id"]]
          and one(result, company_iso["id"]).get("reasons") == ["Mentions “ISO 9001” in its title"],
          str(result["candidates"]))
    check("certification: a certificate held by an individual employee is not offered as the company's",
          personal_iso["id"] not in ids(result))
    check("certification: the note says company certifications are not recorded",
          any("does not record certifications the company holds" in n for n in result["notes"]))

    # --- document -----------------------------------------------------------------------------
    completion = criterion({"kind": "document", "params": {
        "document_type": "completion certificate", "count": 2, "validity_note": "issued in the last year"}})
    status, result = candidates(completion)
    check("document: every document of the type, matched case-blind",
          sorted(ids(result)) == sorted([recent_certificate["id"], old_certificate["id"]]), str(ids(result)))
    recent = one(result, recent_certificate["id"])
    check("document: its owner is named and linked",
          recent.get("owner_type") == "project" and recent.get("owner_id") == certified_recent["id"]
          and recent.get("owner_label") == "Match E certified, ended 2024", str(recent))
    check("document: one already attached to this tender says so",
          recent.get("attached") is True and "Already attached to this tender" in recent.get("reasons", []),
          str(recent))
    check("document: validity is said not to be checked",
          any("is not checked — documents have no validity dates recorded" in n for n in result["notes"]))

    # --- manual kinds -----------------------------------------------------------------------------
    for payload in ({"kind": "financial", "params": {"metric": "net_worth", "min_amount": 1}},
                    {"kind": "other", "params": {"description": "Not blacklisted"}}):
        status, result = candidates(criterion(payload))
        check(f"{payload['kind']}: manual, no candidates, with a note saying why",
              status == 200 and result["mode"] == "manual" and result["candidates"] == [] and result["notes"],
              str(result))

    # --- errors, and nothing written --------------------------------------------------------------
    check("an unknown criterion -> 404",
          client.get(f"/tenders/{tid}/criteria/nope/candidates").status_code == 404)
    check("an unknown tender -> 404",
          client.get(f"/tenders/0123456789abcdef01234567/criteria/{value}/candidates").status_code == 404)
    before = (client.get(f"/tenders/{tid}").json(),
              client.get("/projects/?page_size=200").json(),
              client.get("/employees/?page_size=200").json(),
              client.get("/documents/?page_size=200").json())
    for criterion_id in (completed_only, manager, iso, completion):
        candidates(criterion_id)
    after = (client.get(f"/tenders/{tid}").json(),
             client.get("/projects/?page_size=200").json(),
             client.get("/employees/?page_size=200").json(),
             client.get("/documents/?page_size=200").json())
    check("finding candidates writes nothing to tenders, projects, employees or documents", before == after)

    for kind in ("tenders", "documents", "projects", "employees"):
        for record_id in created[kind]:
            client.delete(f"/{kind}/{record_id}")


def _check_existing_document_links(client, documents):
    """Linking an existing document to a tender: the tender lists it, nothing
    is copied, and a document a project or person holds keeps its owner."""
    import io
    from bson import ObjectId

    created = {"tenders": [], "documents": [], "projects": [], "employees": []}
    project = client.post("/projects/", json={"title": "Link check project", "client_name": "C"}).json()
    created["projects"].append(project["id"])
    pdf = b"%PDF-1.4\n% link check " + ObjectId().binary + b"\n%%EOF\n"
    res = client.post("/documents/", data={"document_type": "Completion Certificate", "category": "Evidence",
                                           "client_name": "C", "submitted_by": "verify",
                                           "project_id": project["id"]},
                      files={"document_file": ("completion.pdf", io.BytesIO(pdf), "application/pdf")})
    project_doc = res.json()
    created["documents"].append(project_doc["id"])
    person = client.post("/employees/", json={"full_name": "Link check person"}).json()
    created["employees"].append(person["id"])
    person_doc = client.post("/documents/", data={"document_type": "Certification", "category": "Personnel",
                                                  "client_name": "C", "submitted_by": "verify"}).json()
    created["documents"].append(person_doc["id"])
    client.post(f"/employees/{person['id']}/certifications",
                json={"name": "ISO 9001 lead auditor", "document_id": person_doc["id"]})
    free_doc = client.post("/documents/", data={"document_type": "Solvency Certificate", "category": "Finance",
                                                "client_name": "C", "submitted_by": "verify"}).json()
    created["documents"].append(free_doc["id"])
    tender = client.post("/tenders/", json={"title": "Link check tender"}).json()
    tid = tender["id"]
    created["tenders"].append(tid)

    stored_before = documents.find_one({"_id": ObjectId(project_doc["id"])})
    total_before = client.get("/documents/?page_size=1").json()["total"]

    res = client.post(f"/tenders/{tid}/documents/{project_doc['id']}")
    check("linking a project's existing document -> 200", res.status_code == 200, res.text[:200])
    check("the tender lists it", project_doc["id"] in res.json()["document_ids"])
    listed = [d["id"] for d in client.get(f"/tenders/{tid}/documents").json()]
    check("it appears on the tender's documents endpoint (the Documents tab)", project_doc["id"] in listed, str(listed))
    stored = documents.find_one({"_id": ObjectId(project_doc["id"])})
    check("the project keeps it: project_id unchanged, and no tender_id written",
          stored.get("project_id") == project["id"] and stored.get("tender_id") is None, str(stored))
    check("the project still lists it",
          project_doc["id"] in [d["id"] for d in client.get(f"/projects/{project['id']}/documents").json()])
    check("the same record and the same file: nothing copied",
          stored.get("stored_file_name") == stored_before.get("stored_file_name")
          and stored.get("file_hash") == stored_before.get("file_hash")
          and client.get("/documents/?page_size=1").json()["total"] == total_before)

    res = client.post(f"/tenders/{tid}/documents/{project_doc['id']}")
    check("linking it again is safe: still one entry, no duplicate link",
          res.status_code == 200 and res.json()["document_ids"].count(project_doc["id"]) == 1, res.text[:200])

    client.post(f"/tenders/{tid}/documents/{person_doc['id']}")
    stored = documents.find_one({"_id": ObjectId(person_doc["id"])})
    check("a person's certificate can be linked and stays theirs",
          stored.get("employee_id") == person["id"] and stored.get("tender_id") is None
          and person_doc["id"] in [d["id"] for d in client.get(f"/tenders/{tid}/documents").json()], str(stored))

    client.post(f"/tenders/{tid}/documents/{free_doc['id']}")
    check("a document nobody holds becomes the tender's (as before)",
          documents.find_one({"_id": ObjectId(free_doc["id"])}).get("tender_id") == tid)

    res = client.post("/documents/", data={"document_type": "Completion Certificate", "category": "Evidence",
                                           "client_name": "C", "submitted_by": "verify"},
                      files={"document_file": ("again.pdf", io.BytesIO(pdf), "application/pdf")})
    check("re-uploading the same file is still refused as a duplicate", res.status_code == 409, res.text[:200])

    res = client.delete(f"/tenders/{tid}/documents/{project_doc['id']}")
    stored = documents.find_one({"_id": ObjectId(project_doc["id"])})
    check("unlinking removes it from the tender only: the document and its project are untouched",
          res.status_code == 200 and project_doc["id"] not in res.json()["document_ids"]
          and stored is not None and stored.get("project_id") == project["id"], res.text[:200])
    check("unlinking does not affect the other linked documents",
          sorted(res.json()["document_ids"]) == sorted([person_doc["id"], free_doc["id"]]), str(res.json()["document_ids"]))
    check("linking an unknown document -> 404",
          client.post(f"/tenders/{tid}/documents/0123456789abcdef01234567").status_code == 404)

    for kind in ("tenders", "documents", "projects", "employees"):
        for record_id in created[kind]:
            client.delete(f"/{kind}/{record_id}")


def run_suite(tenders, documents, offline=False):
    from bson import ObjectId
    from fastapi.testclient import TestClient

    from app import main, mongodb

    main.app.dependency_overrides[mongodb.get_tender_records] = lambda: tenders
    main.app.dependency_overrides[mongodb.get_documents] = lambda: documents
    # A tender now cites projects, so the tender endpoints read and the
    # citation checks write to the projects collection. Pointed at this
    # suite's own database, so neither reaches the one named in .env.
    main.app.dependency_overrides[mongodb.get_project_records] = (
        lambda: documents.database["projects"]
    )
    # Finding a criterion's candidates reads employees too; the suite's own
    # database, so the live run never reaches the one named in .env.
    main.app.dependency_overrides[mongodb.get_employee_records] = (
        lambda: documents.database["employees"]
    )
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

        # --- qualification criteria --------------------------------------
        _check_criteria(client, tenders)
        _check_criteria_validation(client)
        _check_candidates(client)
        _check_existing_document_links(client, documents)

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

        # A receipt deleted from the document log must not leave the cost item
        # pointing at nothing. The cost item keeps its amount and instrument
        # number -- what it cost is still true whether or not the scan of the
        # receipt survives -- and only the pointer and the stale file name go.
        receipt = client.post("/documents/", data={
            "document_type": "Payment Receipt", "category": "Tendering",
            "client_name": "MSAMB", "submitted_by": "vansh",
        }).json()
        paid = client.post(f"/tenders/{erp_id}/cost-items", json={
            "cost_type": "EMD", "amount": "50000", "instrument_number": "DD-4471",
            "document_id": receipt["id"], "file_name": "emd-receipt.pdf",
        }).json()["cost_items"][0]
        check("a cost item can hold a receipt", paid["document_id"] == receipt["id"])
        check("a receipt is not one of the tender's documents",
              client.get(f"/tenders/{erp_id}").json()["document_ids"] == []
              and client.get(f"/documents/{receipt['id']}").json()["tender_id"] is None)
        check("and it does not show on the tender's documents endpoint",
              [d["id"] for d in client.get(f"/tenders/{erp_id}/documents").json()]
              == [])

        client.delete(f"/documents/{receipt['id']}")
        after = client.get(f"/tenders/{erp_id}").json()["cost_items"][0]
        check("deleting the receipt keeps the cost item", after["id"] == paid["id"])
        check("deleting the receipt clears its document_id",
              after["document_id"] is None, str(after["document_id"]))
        check("deleting the receipt clears the stale file_name",
              after["file_name"] is None, str(after["file_name"]))
        check("the cost item keeps what it cost",
              after["amount"] == "50000" and after["instrument_number"] == "DD-4471")
        client.delete(f"/tenders/{erp_id}/cost-items/{paid['id']}")

        # The same for a certificate's scan.
        scan = client.post("/documents/", data={
            "document_type": "Tender Certificate", "category": "Tendering",
            "client_name": "MSAMB", "submitted_by": "vansh",
        }).json()
        held = client.post(f"/tenders/{erp_id}/certificates", json={
            "name": "ISO 9001", "document_id": scan["id"], "file_name": "iso.pdf",
        }).json()["certificates"][0]
        client.delete(f"/documents/{scan['id']}")
        after = client.get(f"/tenders/{erp_id}").json()["certificates"][0]
        check("deleting a certificate's scan keeps the certificate",
              after["id"] == held["id"] and after["name"] == "ISO 9001")
        check("deleting a certificate's scan clears its pointer",
              after["document_id"] is None and after["file_name"] is None,
              str((after["document_id"], after["file_name"])))
        client.delete(f"/tenders/{erp_id}/certificates/{held['id']}")

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

        check("a new document starts with no tender",
              client.get(f"/documents/{doc_id}").json()["tender_id"] is None)

        res = client.post(f"/tenders/{erp_id}/documents/{doc_id}")
        check("POST /tenders/{id}/documents/{id} attaches -> 200",
              res.status_code == 200 and res.json()["document_ids"] == [doc_id], res.text)
        check("attaching stamps tender_id on the document",
              client.get(f"/documents/{doc_id}").json()["tender_id"] == erp_id)
        again = client.post(f"/tenders/{erp_id}/documents/{doc_id}")
        check("attaching the same document twice is a no-op",
              again.json()["document_ids"] == [doc_id], str(again.json()["document_ids"]))
        check("re-attaching leaves tender_id alone",
              client.get(f"/documents/{doc_id}").json()["tender_id"] == erp_id)
        check("attaching an unknown document -> 404",
              client.post(f"/tenders/{erp_id}/documents/000000000000000000000000").status_code == 404)

        # --- GET /tenders/{id}/documents ---------------------------------
        res = client.get(f"/tenders/{erp_id}/documents")
        check("GET /tenders/{id}/documents -> 200 with a list",
              res.status_code == 200 and isinstance(res.json(), list), res.text)
        check("it returns the documents attached to this tender",
              [d["id"] for d in res.json()] == [doc_id], res.text)
        check("the rows are ordinary documents, unchanged in shape",
              res.json()[0]["document_type"] == "Tender Notice"
              and res.json()[0]["tender_id"] == erp_id
              and "file_name" in res.json()[0], res.text)

        empty = client.post("/tenders/", json={
            "title": "No documents yet", "issuing_authority": "Nobody",
            "status": "Identified",
        }).json()
        res = client.get(f"/tenders/{empty['id']}/documents")
        check("a tender with no documents returns an empty list",
              res.status_code == 200 and res.json() == [], res.text)
        check("it does not fall back to the whole document log",
              client.get("/documents/").json()["total"] > 0 and res.json() == [])

        # A second tender listing the same document still sees it, although
        # tender_id stayed with the first: attaching is open to more than one
        # tender, and the list is the only thing that knows about the second.
        second = client.post("/tenders/", json={
            "title": "Second bid, same notice", "issuing_authority": "MSAMB",
            "status": "Identified",
        }).json()
        client.post(f"/tenders/{second['id']}/documents/{doc_id}")
        check("a document a second tender lists appears under that tender too",
              [d["id"] for d in client.get(f"/tenders/{second['id']}/documents").json()]
              == [doc_id])
        check("the first tender keeps the tender_id",
              client.get(f"/documents/{doc_id}").json()["tender_id"] == erp_id)
        client.delete(f"/tenders/{second['id']}/documents/{doc_id}")
        client.delete(f"/tenders/{second['id']}")
        client.delete(f"/tenders/{empty['id']}")

        check("GET documents of an unknown tender -> 404",
              client.get("/tenders/000000000000000000000000/documents").status_code == 404)
        check("GET documents of a malformed tender id -> 404",
              client.get("/tenders/not-an-id/documents").status_code == 404)
        check("a 404 there says so the way every other tender endpoint does",
              client.get("/tenders/000000000000000000000000/documents").json()["detail"]
              == "Tender not found")

        # Detaching clears the pointer, and only where it names this tender.
        res = client.delete(f"/tenders/{erp_id}/documents/{doc_id}")
        check("DELETE /tenders/{id}/documents/{id} detaches -> 200",
              res.status_code == 200 and res.json()["document_ids"] == [], res.text)
        check("detaching clears tender_id",
              client.get(f"/documents/{doc_id}").json()["tender_id"] is None)
        check("the document itself survives being detached",
              client.get(f"/documents/{doc_id}").status_code == 200)

        # A document another tender holds is not this tender's to unclaim.
        other = client.post("/tenders/", json={
            "title": "Unrelated bid", "issuing_authority": "Someone else",
            "status": "Identified",
        }).json()
        client.post(f"/tenders/{other['id']}/documents/{doc_id}")
        client.delete(f"/tenders/{erp_id}/documents/{doc_id}")
        check("detaching from a tender that does not hold it leaves the owner alone",
              client.get(f"/documents/{doc_id}").json()["tender_id"] == other["id"])
        client.delete(f"/tenders/{other['id']}/documents/{doc_id}")

        # Deleting an attached document clears the tender's own list.
        client.post(f"/tenders/{erp_id}/documents/{doc_id}")
        client.delete(f"/documents/{doc_id}")
        check("deleting a document removes it from the tender's document_ids",
              client.get(f"/tenders/{erp_id}").json()["document_ids"] == [],
              str(client.get(f"/tenders/{erp_id}").json()["document_ids"]))
        check("deleting an attached document leaves the tender itself",
              client.get(f"/tenders/{erp_id}").status_code == 200)
        client.delete(f"/tenders/{other['id']}")

        res = client.delete(f"/tenders/{erp_id}/documents/{doc_id}")
        check("detaching an already-deleted document still works",
              res.status_code == 200 and res.json()["document_ids"] == [], res.text)

        # --- cited projects (evidence) -----------------------------------
        #
        # The past work a bid puts forward. A citation points at a project and
        # changes nothing about it: not its fields, not its documents.
        check("a new tender cites nothing",
              client.get(f"/tenders/{erp_id}/projects").json() == [],
              client.get(f"/tenders/{erp_id}/projects").text)

        past = client.post("/projects/", json={
            "title": "Nashik APMC rollout", "client_name": "Nashik APMC",
            "status": "Completed",
        })
        check("a project exists to cite", past.status_code == 201, past.text)
        past_id = past.json()["id"]

        res = client.post(f"/tenders/{erp_id}/projects/{past_id}")
        check("POST /tenders/{id}/projects/{id} cites -> 200",
              res.status_code == 200 and res.json()["cited_project_ids"] == [past_id],
              res.text)
        again = client.post(f"/tenders/{erp_id}/projects/{past_id}")
        check("citing the same project twice does not duplicate it",
              again.json()["cited_project_ids"] == [past_id],
              str(again.json()["cited_project_ids"]))

        res = client.get(f"/tenders/{erp_id}/projects")
        check("GET /tenders/{id}/projects returns the cited project",
              res.status_code == 200 and [p["id"] for p in res.json()] == [past_id],
              res.text)
        check("the rows are ordinary projects, unchanged in shape",
              res.json()[0]["title"] == "Nashik APMC rollout"
              and "document_types" in res.json()[0]
              and "document_ids" in res.json()[0], res.text)

        check("citing an unknown project -> 404",
              client.post(f"/tenders/{erp_id}/projects/000000000000000000000000")
              .status_code == 404)
        check("citing a malformed project id -> 404",
              client.post(f"/tenders/{erp_id}/projects/not-an-id").status_code == 404)
        check("citing on an unknown tender -> 404",
              client.post(f"/tenders/000000000000000000000000/projects/{past_id}")
              .status_code == 404)
        check("GET projects of an unknown tender -> 404",
              client.get("/tenders/000000000000000000000000/projects").status_code == 404)

        # Evidence is not exclusive: the same completed work is put forward
        # bid after bid, which is the whole point of citing it.
        sibling = client.post("/tenders/", json={
            "title": "Another bid, same experience", "issuing_authority": "MSAMB",
            "status": "Identified",
        }).json()
        res = client.post(f"/tenders/{sibling['id']}/projects/{past_id}")
        check("a second tender may cite the same project",
              res.status_code == 200 and res.json()["cited_project_ids"] == [past_id],
              res.text)
        check("the first tender still cites it too",
              client.get(f"/tenders/{erp_id}").json()["cited_project_ids"] == [past_id])

        # Citing must not reach into the project's documents.
        project_after = client.get(f"/projects/{past_id}").json()
        check("citing leaves the project's own document_ids alone",
              project_after["document_ids"] == [], str(project_after["document_ids"]))
        check("citing does not add the project's documents to the tender",
              client.get(f"/tenders/{erp_id}").json()["document_ids"]
              == client.get(f"/tenders/{sibling['id']}").json()["document_ids"])

        res = client.delete(f"/tenders/{sibling['id']}/projects/{past_id}")
        check("DELETE /tenders/{id}/projects/{id} uncites -> 200",
              res.status_code == 200 and res.json()["cited_project_ids"] == [], res.text)
        check("uncited on one tender leaves the other citation standing",
              client.get(f"/tenders/{erp_id}").json()["cited_project_ids"] == [past_id])
        check("uncited on a tender that never cited it is a no-op",
              client.delete(f"/tenders/{sibling['id']}/projects/{past_id}").status_code == 200)
        check("uncited leaves the project itself in place",
              client.get(f"/projects/{past_id}").status_code == 200)
        client.delete(f"/tenders/{sibling['id']}")

        # Deleting the project clears the citation (the projects router does
        # this; checked here because this is where the citation lives).
        client.delete(f"/projects/{past_id}")
        check("deleting a cited project clears the citation",
              client.get(f"/tenders/{erp_id}").json()["cited_project_ids"] == [],
              str(client.get(f"/tenders/{erp_id}").json()["cited_project_ids"]))
        check("and the tender itself is untouched",
              client.get(f"/tenders/{erp_id}").status_code == 200)

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

        client.post(f"/tenders/{erp_id}/documents/{surviving_doc}")
        check("the document is this tender's before the tender goes",
              client.get(f"/documents/{surviving_doc}").json()["tender_id"] == erp_id)

        res = client.delete(f"/tenders/{erp_id}")
        check("DELETE /tenders/{id} -> 204", res.status_code == 204, res.text)
        check("the tender is gone", client.get(f"/tenders/{erp_id}").status_code == 404)
        check("uploaded files stay in the document log, as the dialog promises",
              client.get(f"/documents/{surviving_doc}").status_code == 200)
        check("deleting the tender clears tender_id on its documents",
              client.get(f"/documents/{surviving_doc}").json()["tender_id"] is None)
        check("DELETE of an already-deleted tender -> 404",
              client.delete(f"/tenders/{erp_id}").status_code == 404)
        check("the remaining tenders are untouched",
              client.get("/tenders/").json()["total"] == 3)

        # --- migration 0004: the tender_id backfill ----------------------
        #
        # Seeded straight into the collections so the pointers exist without
        # the attach endpoint ever having run, which is what pre-migration
        # data is. Runs against this suite's own scratch database.
        from migrations import m0004_document_tender_id as m0004

        database = documents.database
        seeded = {}
        for label in ("plain", "shared", "conflicted", "project_owned"):
            res = client.post("/documents/", data={
                "document_type": "Tender Notice", "category": "Tendering",
                "client_name": label, "submitted_by": "vansh",
            })
            seeded[label] = res.json()["id"]
        gone = "ffffffffffffffffffffffff"

        first = client.post("/tenders/", json={
            "title": "Backfill A", "issuing_authority": "X", "status": "Identified",
        }).json()["id"]
        second = client.post("/tenders/", json={
            "title": "Backfill B", "issuing_authority": "Y", "status": "Identified",
        }).json()["id"]

        documents.update_many({}, {"$unset": {"tender_id": ""}})
        documents.update_one({"_id": ObjectId(seeded["conflicted"])},
                             {"$set": {"tender_id": second}})
        documents.update_one({"_id": ObjectId(seeded["project_owned"])},
                             {"$set": {"project_id": "someproject"}})
        tenders.update_one({"_id": ObjectId(first)}, {"$set": {"document_ids": [
            seeded["plain"], seeded["shared"], seeded["conflicted"],
            seeded["project_owned"], gone,
        ]}})
        tenders.update_one({"_id": ObjectId(second)},
                           {"$set": {"document_ids": [seeded["shared"]]}})

        before = m0004.analyse(database)
        check("migration: analyse finds the cleanly-mapped document",
              seeded["plain"] in before["_to_write"])
        check("migration: analyse reports the multi-tender document",
              seeded["shared"] in before["multi_tender"])
        check("migration: analyse reports the dangling reference",
              gone in before["dangling"])
        check("migration: analyse reports the conflicting document",
              seeded["conflicted"] in before["conflicting"])
        check("migration: analyse reports a document already owned elsewhere",
              seeded["project_owned"] in before["owned_elsewhere"])
        check("migration: analyse writes nothing",
              documents.find_one({"_id": ObjectId(seeded["plain"])}).get("tender_id") is None)

        run_one = m0004.apply(database)
        check("migration: the clean mapping is written",
              documents.find_one({"_id": ObjectId(seeded["plain"])})["tender_id"] == first)
        check("migration: a document two tenders list is left alone",
              documents.find_one({"_id": ObjectId(seeded["shared"])})["tender_id"] is None)
        check("migration: a conflicting tender_id is not overwritten",
              documents.find_one({"_id": ObjectId(seeded["conflicted"])})["tender_id"] == second)
        check("migration: a project's document is not claimed",
              documents.find_one({"_id": ObjectId(seeded["project_owned"])})["tender_id"] is None)
        check("migration: every document now carries the field",
              documents.count_documents({"tender_id": {"$exists": False}}) == 0)
        check("migration: the skipped count is reported", run_one["skipped"] == 2,
              str(run_one["skipped"]))
        check("migration: report_lines renders", len(m0004.report_lines(run_one)) > 0)

        run_two = m0004.apply(database)
        check("migration: a second run writes nothing", run_two["written"] == 0,
              str(run_two["written"]))
        check("migration: a second run fills no nulls", run_two["null_filled"] == 0,
              str(run_two["null_filled"]))
        check("migration: a second run keeps the clean mapping",
              documents.find_one({"_id": ObjectId(seeded["plain"])})["tender_id"] == first)

        if offline:
            print("  [SKIP] the tender_id index (needs a real server; run without --offline)")
        else:
            check("migration: the tender_id index exists",
                  m0004.INDEX_NAME in documents.index_information())
            mongodb.ensure_indexes(documents)
            check("ensure_indexes() creates the same tender_id index",
                  m0004.INDEX_NAME in documents.index_information())

        m0004.record(database, run_two)
        check("migration: it records itself as applied",
              database["migrations"].find_one({"_id": m0004.VERSION}) is not None)

        for tender_id in (first, second):
            client.delete(f"/tenders/{tender_id}")
        for document_id in seeded.values():
            client.delete(f"/documents/{document_id}")

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
    from verify_offline import install

    # The app's own client becomes this in-memory one, so startup and any
    # collection not overridden below cannot reach the database in .env.
    database = install(mongomock.MongoClient())["verify"]
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
