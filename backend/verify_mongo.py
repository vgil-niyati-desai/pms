"""Verify the MongoDB backend end to end, without touching the React app.

Two modes:

    python verify_mongo.py
        Connect to the MongoDB configured in .env. If it's reachable, run the
        full endpoint suite against a scratch database (<MONGO_DB_NAME>_verify)
        which is dropped afterwards, so the real database is never written to.

    python verify_mongo.py --offline
        Run the same suite against an in-memory MongoDB stand-in (mongomock).
        Proves the application code is correct on a machine where MongoDB is
        not installed yet. Requires: pip install -r requirements-dev.txt

Each check drives the real FastAPI app through its HTTP layer, so what is
being tested is exactly what the React frontend calls.
"""

import io
import os
import sys

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not condition:
        line += f"\n         {detail}"
    print(line)
    return bool(condition)


def _pdf(marker: bytes) -> bytes:
    """Minimal distinct byte blob; content only has to be stable and unique."""
    return b"%PDF-1.4\n" + marker + b"\n%%EOF\n"


# The redaction checks need a PDF that is genuinely readable, not the stub
# above: the point of them is that real text goes in and is not there
# afterwards. Same for the image case.
REDACT_SECRET = "CONTRACT VALUE 4,750,000 INR"
REDACT_SECRET_P2 = "UNIT RATE 8,912 PER SQM"
REDACT_PUBLIC = "Project: Riverside Bridge Rehabilitation"


def _redact_band():
    """The dark band drawn behind page 2's secret. See _readable_pdf."""
    import pymupdf

    return pymupdf.Rect(60, 132, 400, 158)


def _readable_pdf():
    """A two-page PDF with known text, plus where that text sits on the page.

    Returns (bytes, areas) with the areas already normalised the way the
    browser sends them, so the suite redacts exactly the words it then looks
    for the absence of.
    """
    import pymupdf

    doc = pymupdf.open()
    page1 = doc.new_page(width=595, height=842)
    page1.insert_text((72, 120), REDACT_PUBLIC, fontsize=14)
    page1.insert_text((72, 200), REDACT_SECRET, fontsize=14)
    page2 = doc.new_page(width=595, height=842)
    # A dark band behind page 2's secret. The redaction fill is white, and on
    # white paper "the area came out white" would pass even if nothing had
    # been drawn at all -- against this band it only passes if the fill was
    # really applied, and it fails if the fill ever goes back to black.
    page2.draw_rect(_redact_band(), color=None, fill=(0.15, 0.15, 0.15))
    page2.insert_text((72, 150), REDACT_SECRET_P2, fontsize=14, color=(1, 1, 1))

    areas = []
    for index, text in ((0, REDACT_SECRET), (1, REDACT_SECRET_P2)):
        page = doc[index]
        hit = page.search_for(text)[0]
        bounds = page.rect
        areas.append({
            "page": index,
            # A little margin, exactly as a hand-drawn box would have.
            "x": (hit.x0 - 2) / bounds.width,
            "y": (hit.y0 - 2) / bounds.height,
            "width": (hit.width + 4) / bounds.width,
            "height": (hit.height + 4) / bounds.height,
        })

    content = doc.tobytes()
    doc.close()
    return content, areas


def _flat_png(value: int) -> bytes:
    """A plain grey PNG. Grey, not white, so "the copy's pixels came out
    white" is a claim about the fill rather than about the source."""
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
    pix.clear_with(value)
    return pix.tobytes("png")


def run_redaction_suite(client, collection, id_without_file):
    """Drive the redaction endpoints, and prove what they promise.

    Two promises, and both are checked here rather than assumed:

      * the copy is *permanently* redacted — the selected words are not in
        the extracted text of the copy, and not in its decompressed page
        content either, so there is nothing to select, copy, or recover from
        under the white box; and
      * the original is *untouched* — same bytes on disk, same bytes from
        the download endpoint, still containing the text it always did.

    Everything this creates is deleted again before it returns, so the
    checks that follow still see the two entries they expect.
    """
    import hashlib

    from bson import ObjectId

    from app import storage

    try:
        import pymupdf  # noqa: F401 - presence is what is being tested
    except ImportError:
        check("PyMuPDF is installed for redaction", False,
              "pip install -r requirements.txt")
        return

    pdf_bytes, areas = _readable_pdf()
    res = client.post(
        "/documents/",
        data={"document_type": "Cost Sheet", "category": "Tendering",
              "client_name": "Redaction Check Ltd", "submitted_by": "vansh"},
        files={"document_file": ("cost sheet.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    if not check("POST a readable PDF for redaction -> 201", res.status_code == 201, res.text):
        return
    entry = res.json()
    stored = collection.find_one({"_id": ObjectId(entry["id"])})["stored_file_name"]
    path = storage.upload_path(stored)
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()

    # --- what the selection view asks for first ---------------------------
    res = client.get(f"/documents/{entry['id']}/redaction/source")
    check("GET /redaction/source -> 200", res.status_code == 200, res.text)
    source = res.json() if res.status_code == 200 else {}
    check("source reports a PDF with both pages",
          source.get("kind") == "pdf" and len(source.get("pages") or []) == 2, res.text)
    check("source reports each page's size",
          bool(source.get("pages")) and source["pages"][0]["width"] == 595, res.text)

    res = client.get(f"/documents/{entry['id']}/redaction/pages/0")
    check("GET /redaction/pages/0 renders a PNG",
          res.status_code == 200 and res.content[:8] == b"\x89PNG\r\n\x1a\n", res.status_code)
    check("a page that does not exist -> 404",
          client.get(f"/documents/{entry['id']}/redaction/pages/7").status_code == 404)
    check("redaction endpoints for an entry with no file -> 404",
          client.get(f"/documents/{id_without_file}/redaction/source").status_code == 404)

    # --- generate the copy -------------------------------------------------
    # Sent with an Origin, because the React app is on a different port and
    # reads the copy's file name out of Content-Disposition. A cross-origin
    # fetch cannot see a header it was not handed, so if the CORS middleware
    # ever stops exposing it every download silently loses its name.
    res = client.post(
        f"/documents/{entry['id']}/redacted-copy",
        json={"areas": areas},
        headers={"Origin": "http://localhost:5173"},
    )
    if not check("POST /redacted-copy -> 200", res.status_code == 200, res.text):
        client.delete(f"/documents/{entry['id']}")
        return
    check("copy is served as a PDF download",
          res.headers.get("content-type") == "application/pdf"
          and "attachment" in res.headers.get("content-disposition", ""),
          res.headers.get("content-disposition"))
    check("copy is named to distinguish it from the original",
          "cost sheet_redacted.pdf" in res.headers.get("content-disposition", ""),
          res.headers.get("content-disposition"))
    check("the browser is allowed to read that name cross-origin",
          "Content-Disposition" in res.headers.get("access-control-expose-headers", ""),
          res.headers.get("access-control-expose-headers"))

    copy_bytes = res.content
    copied = pymupdf.open(stream=copy_bytes, filetype="pdf")
    page0, page1 = copied[0].get_text(), copied[1].get_text()
    check("the copy keeps every page", copied.page_count == 2, str(copied.page_count))
    check("selected text is gone from page 1", REDACT_SECRET not in page0, repr(page0[:60]))
    check("selected text is gone from page 2 (multiple areas)",
          REDACT_SECRET_P2 not in page1, repr(page1[:60]))
    check("unselected text on the same page survives", REDACT_PUBLIC in page0, repr(page0[:60]))
    # The whole point: not merely invisible, but not in the file at all. This
    # reads the *decompressed* page content, which is where a box drawn over
    # live text would leave that text sitting untouched underneath. Searching
    # the raw file bytes instead would prove nothing -- page streams are
    # deflated, so the words would not appear there either way.
    streams = (copied[0].read_contents() + copied[1].read_contents()).decode("latin-1")
    check("selected text is gone from the copy's page content streams",
          REDACT_SECRET not in streams and REDACT_SECRET_P2 not in streams)
    check("unselected text is still in the content stream, so the check can fail",
          REDACT_PUBLIC in streams)
    # Page 2's secret sat on a dark band (see _readable_pdf), so this is the
    # page where the fill has to prove itself. The clip is the whole redacted
    # rectangle, and the assertion is that *every* pixel in it came out white:
    # a black fill would make this 0, and no fill at all would leave the
    # band's own 38 behind.
    marked = next(a for a in areas if a["page"] == 1)
    bounds = copied[1].rect
    clip = pymupdf.Rect(
        marked["x"] * bounds.width,
        marked["y"] * bounds.height,
        (marked["x"] + marked["width"]) * bounds.width,
        (marked["y"] + marked["height"]) * bounds.height,
    # Inset by two points. The band is only removed *inside* the rectangle,
    # which is what should happen, so the boundary pixels are an antialiased
    # blend of the white fill and the dark band still sitting outside it.
    ) + (2, 2, -2, -2)
    shot = copied[1].get_pixmap(clip=clip)
    check("the redacted area is filled solid white", min(shot.samples) > 245,
          f"darkest sample {min(shot.samples)}")
    original = pymupdf.open(path)
    before_shot = original[1].get_pixmap(clip=clip)
    original.close()
    check("that area was not white to begin with, so the check can fail",
          min(before_shot.samples) < 80, f"darkest sample {min(before_shot.samples)}")
    copied.close()

    # --- the original is exactly as it was --------------------------------
    after = hashlib.sha256(open(path, "rb").read()).hexdigest()
    check("the stored original is byte-for-byte unchanged", before == after)
    check("the original still downloads unchanged",
          client.get(f"/documents/{entry['id']}/file").content == pdf_bytes)
    original = pymupdf.open(path)
    check("the original still contains the redacted text",
          REDACT_SECRET in original[0].get_text())
    original.close()
    check("generating a copy created no new entry",
          collection.count_documents({"client_name": "Redaction Check Ltd"}) == 1)

    # --- bad requests ------------------------------------------------------
    check("no areas -> 422",
          client.post(f"/documents/{entry['id']}/redacted-copy",
                      json={"areas": []}).status_code == 422)
    res = client.post(f"/documents/{entry['id']}/redacted-copy",
                      json={"areas": [{"page": 9, "x": 0.1, "y": 0.1,
                                       "width": 0.2, "height": 0.1}]})
    check("an area on a page that does not exist -> 400", res.status_code == 400, res.text)

    # --- PNG ---------------------------------------------------------------
    res = client.post(
        "/documents/",
        data={"document_type": "Site Photo", "category": "Roads",
              "client_name": "Redaction Image Ltd", "submitted_by": "vansh"},
        files={"document_file": ("site.png", io.BytesIO(_flat_png(200)), "image/png")},
    )
    if check("POST a PNG for redaction -> 201", res.status_code == 201, res.text):
        image_entry = res.json()
        res = client.get(f"/documents/{image_entry['id']}/redaction/source")
        check("an image reports as a single page",
              res.status_code == 200 and res.json()["kind"] == "image"
              and len(res.json()["pages"]) == 1, res.text)
        res = client.post(
            f"/documents/{image_entry['id']}/redacted-copy",
            json={"areas": [{"page": 0, "x": 0.1, "y": 0.1, "width": 0.3, "height": 0.3}]},
        )
        check("POST /redacted-copy for a PNG -> 200", res.status_code == 200, res.text)
        if res.status_code == 200:
            check("the PNG copy is served as a PNG",
                  res.headers.get("content-type") == "image/png",
                  res.headers.get("content-type"))
            out = pymupdf.Pixmap(io.BytesIO(res.content))
            check("selected pixels are white in the copy", out.pixel(80, 80) == (255, 255, 255),
                  str(out.pixel(80, 80)))
            check("unselected pixels are untouched", out.pixel(300, 250) == (200, 200, 200),
                  str(out.pixel(300, 250)))
        client.delete(f"/documents/{image_entry['id']}")

    client.delete(f"/documents/{entry['id']}")


def run_suite(collection):
    """Drive every documents endpoint against `collection`."""
    from fastapi.testclient import TestClient

    from app import main, mongodb
    from app.routers import documents_mongo

    # Point both the request path and the startup path at this collection.
    main.app.dependency_overrides[mongodb.get_documents] = lambda: collection
    mongodb.get_collection = lambda: collection
    mongodb.ping = lambda: (True, None)

    try:
        mongodb.ensure_indexes(collection)
        check("startup: indexes created", True)
    except Exception as exc:
        # mongomock doesn't implement every index option; a real server does.
        check("startup: indexes created", False, f"{type(exc).__name__}: {exc}")

    with TestClient(main.app) as client:
        check("GET / reports the mongodb backend",
              client.get("/").json().get("database") == "mongodb")

        # --- open-ended fields ------------------------------------------
        # No document field is enforced any more: a row renders and stays
        # editable with everything blank, and which fields must be captured
        # is unsettled business policy. The entry form still asks for type,
        # category, client and submitted-by; the API does not.
        res = client.post("/documents/", data={"notes": "bare row"})
        ok = check("POST /documents/ with no required fields -> 201",
                   res.status_code == 201, res.text)
        if ok:
            bare = res.json()
            check("the blanks come back as blanks",
                  bare["document_type"] is None and bare["category"] is None
                  and bare["client_name"] is None and bare["submitted_by"] is None)
            check("a bare row can be deleted like any other",
                  client.delete("/documents/{i}".format(i=bare["id"])).status_code == 204)

        # --- create, without a file -------------------------------------
        res = client.post("/documents/", data={
            "document_type": "LOI",
            "category": "Water Supply",
            "client_name": "Pune Municipal Corporation",
            "reference_number": "REF/2026/001",
            "project_title": "Pipeline Upgrade Phase II",
            "contract_value": "5,00,000",
            "document_date": "2026-01-15",
            "department": "Civil",
            "submitted_by": "vansh",
            "notes": "no file attached",
        })
        ok = check("POST /documents/ (no file) -> 201", res.status_code == 201, res.text)
        if not ok:
            return
        plain = res.json()
        check("created id is an ObjectId string",
              isinstance(plain["id"], str) and len(plain["id"]) == 24, repr(plain.get("id")))
        check("created_at was set", bool(plain.get("created_at")))
        check("all metadata fields round-tripped",
              plain["reference_number"] == "REF/2026/001"
              and plain["project_title"] == "Pipeline Upgrade Phase II"
              and plain["contract_value"] == "5,00,000"
              and plain["document_date"] == "2026-01-15"
              and plain["department"] == "Civil"
              and plain["notes"] == "no file attached",
              str(plain))

        # --- create, with a file ----------------------------------------
        res = client.post(
            "/documents/",
            data={
                "document_type": "Work Order",
                "category": "Roads",
                "client_name": "Acme Infra Ltd",
                "project_title": "Ring Road Package 3",
                "reference_number": "WO-77",
                "submitted_by": "vansh",
            },
            files={"document_file": ("work_order.pdf", io.BytesIO(_pdf(b"A")), "application/pdf")},
        )
        ok = check("POST /documents/ (with file) -> 201", res.status_code == 201, res.text)
        if not ok:
            return
        withfile = res.json()
        check("original file name kept", withfile["file_name"] == "work_order.pdf")
        stored = collection.find_one({"_id": __import__("bson").ObjectId(withfile["id"])})
        check("stored_file_name + file_hash written to MongoDB",
              bool(stored.get("stored_file_name")) and len(stored.get("file_hash") or "") == 64)

        # --- rejected file type -----------------------------------------
        res = client.post(
            "/documents/",
            data={"document_type": "LOI", "category": "X",
                  "client_name": "Y", "submitted_by": "vansh"},
            files={"document_file": ("notes.txt", io.BytesIO(b"nope"), "text/plain")},
        )
        check("non-PDF/PNG/JPG upload -> 400", res.status_code == 400, res.text)

        # --- duplicate detection ----------------------------------------
        res = client.post(
            "/documents/",
            data={"document_type": "LOI", "category": "Roads",
                  "client_name": "Someone Else", "submitted_by": "vansh"},
            files={"document_file": ("copy.pdf", io.BytesIO(_pdf(b"A")), "application/pdf")},
        )
        check("re-uploading identical bytes -> 409", res.status_code == 409, res.text)
        check("409 names the entry that already has the file",
              "Acme Infra Ltd" in res.json().get("detail", ""), res.text)
        check("rejected duplicate created no entry",
              collection.count_documents({"client_name": "Someone Else"}) == 0)

        # --- download ----------------------------------------------------
        res = client.get(f"/documents/{withfile['id']}/file")
        check("GET /documents/{id}/file returns the bytes",
              res.status_code == 200 and res.content == _pdf(b"A"), res.status_code)
        res = client.get(f"/documents/{plain['id']}/file")
        check("file download for an entry with no file -> 404", res.status_code == 404)

        # --- manual redaction ---------------------------------------------
        run_redaction_suite(client, collection, plain["id"])

        # --- read --------------------------------------------------------
        res = client.get(f"/documents/{plain['id']}")
        check("GET /documents/{id} -> 200", res.status_code == 200, res.text)
        check("GET /documents/{id} with a malformed id -> 404",
              client.get("/documents/not-an-object-id").status_code == 404)
        check("GET /documents/{id} for a missing id -> 404",
              client.get("/documents/0123456789abcdef01234567").status_code == 404)

        # --- list, filter, search ---------------------------------------
        rows = client.get("/documents/").json()
        check("GET /documents/ lists both entries", len(rows) == 2, str(len(rows)))
        check("list is newest-first",
              rows[0]["id"] == withfile["id"], [r["id"] for r in rows])

        check("filter by document_type",
              [r["id"] for r in client.get("/documents/?document_type=LOI").json()] == [plain["id"]])
        check("filter by category",
              [r["id"] for r in client.get("/documents/?category=Roads").json()] == [withfile["id"]])
        check("search matches client_name, case-insensitively",
              [r["id"] for r in client.get("/documents/?q=acme").json()] == [withfile["id"]])
        check("search matches project_title",
              [r["id"] for r in client.get("/documents/?q=Pipeline").json()] == [plain["id"]])
        check("search matches reference_number",
              [r["id"] for r in client.get("/documents/?q=WO-77").json()] == [withfile["id"]])
        check("search + filter combine",
              client.get("/documents/?q=acme&document_type=LOI").json() == [])
        check("regex metacharacters in search are literal, not patterns",
              client.get("/documents/?q=.*").json() == [])
        check("search with no match returns empty",
              client.get("/documents/?q=zzzznothing").json() == [])

        # --- update, metadata only --------------------------------------
        res = client.put(f"/documents/{plain['id']}", data={
            "document_type": "Completion Certificate",
            "category": "Water Supply",
            "client_name": "Pune Municipal Corporation",
            "reference_number": "REF/2026/001-A",
            "project_title": "Pipeline Upgrade Phase II",
            "contract_value": "6,00,000",
            "document_date": "2026-02-01",
            "department": "Civil",
            "submitted_by": "vansh",
            "notes": "revised",
        })
        ok = check("PUT /documents/{id} (metadata only) -> 200", res.status_code == 200, res.text)
        if ok:
            body = res.json()
            check("edited fields were saved",
                  body["document_type"] == "Completion Certificate"
                  and body["contract_value"] == "6,00,000"
                  and body["notes"] == "revised", str(body))
            check("id is unchanged by an edit", body["id"] == plain["id"])
            check("created_at is unchanged by an edit", body["created_at"] == plain["created_at"])

        # --- update, replacing the file ---------------------------------
        old_stored = collection.find_one(
            {"_id": __import__("bson").ObjectId(withfile["id"])})["stored_file_name"]
        res = client.put(
            f"/documents/{withfile['id']}",
            data={"document_type": "Work Order", "category": "Roads",
                  "client_name": "Acme Infra Ltd", "project_title": "Ring Road Package 3",
                  "reference_number": "WO-77", "submitted_by": "vansh"},
            files={"document_file": ("revised.pdf", io.BytesIO(_pdf(b"B")), "application/pdf")},
        )
        ok = check("PUT /documents/{id} (replacing the file) -> 200", res.status_code == 200, res.text)
        if ok:
            check("new file name is shown", res.json()["file_name"] == "revised.pdf")
            check("replacement bytes are what downloads now",
                  client.get(f"/documents/{withfile['id']}/file").content == _pdf(b"B"))
            from app import storage
            check("the replaced file was deleted from disk",
                  not os.path.exists(storage.upload_path(old_stored)))

        # --- update, re-uploading the entry's own file is not a duplicate -
        res = client.put(
            f"/documents/{withfile['id']}",
            data={"document_type": "Work Order", "category": "Roads",
                  "client_name": "Acme Infra Ltd", "submitted_by": "vansh"},
            files={"document_file": ("revised.pdf", io.BytesIO(_pdf(b"B")), "application/pdf")},
        )
        check("re-uploading an entry's own file -> 200, not 409", res.status_code == 200, res.text)

        # --- update, uploading a file another entry already has ----------
        res = client.put(
            f"/documents/{plain['id']}",
            data={"document_type": "LOI", "category": "Water Supply",
                  "client_name": "Pune Municipal Corporation", "submitted_by": "vansh"},
            files={"document_file": ("steal.pdf", io.BytesIO(_pdf(b"B")), "application/pdf")},
        )
        check("editing in a file another entry holds -> 409", res.status_code == 409, res.text)

        check("PUT to a missing id -> 404",
              client.put("/documents/0123456789abcdef01234567",
                         data={"document_type": "LOI", "category": "X",
                               "client_name": "Y", "submitted_by": "v"}).status_code == 404)

        # --- backfill ----------------------------------------------------
        collection.update_one({"_id": __import__("bson").ObjectId(withfile["id"])},
                              {"$set": {"file_hash": None}})
        filled = documents_mongo.backfill_file_hashes(collection)
        check("backfill_file_hashes re-hashes an entry with a null hash", filled == 1, str(filled))

        # --- delete ------------------------------------------------------
        current_stored = collection.find_one(
            {"_id": __import__("bson").ObjectId(withfile["id"])})["stored_file_name"]
        from app import storage
        res = client.delete(f"/documents/{withfile['id']}")
        check("DELETE /documents/{id} -> 204", res.status_code == 204, res.text)
        check("the entry is gone from MongoDB",
              collection.count_documents({"_id": __import__("bson").ObjectId(withfile["id"])}) == 0)
        check("its file is gone from disk",
              not os.path.exists(storage.upload_path(current_stored)))
        check("deleting the same id again -> 404",
              client.delete(f"/documents/{withfile['id']}").status_code == 404)
        check("the other entry survived the delete",
              len(client.get("/documents/").json()) == 1)

        # --- cleanup ------------------------------------------------------
        client.delete(f"/documents/{plain['id']}")

    main.app.dependency_overrides.clear()


def main_offline():
    try:
        import mongomock
    except ImportError:
        print("mongomock is not installed. Run:")
        print("    .\\venv\\Scripts\\python.exe -m pip install -r requirements-dev.txt")
        return 2
    print("Mode: offline (in-memory MongoDB stand-in; no server required)\n")
    # tz_aware mirrors how app/mongodb.py builds the real client, so datetimes
    # come back as UTC-aware here too.
    collection = mongomock.MongoClient(tz_aware=True)["doc_collection_verify"]["documents"]
    run_suite(collection)
    return 0


def main_live():
    from app import mongodb

    print(f"Mode: live — {mongodb.safe_uri()}\n")
    reachable, error = mongodb.ping()
    if not check("MongoDB is reachable", reachable, error or ""):
        print("\nMongoDB is not running (or not installed) on this machine.")
        print("Install and start it — SETUP.md, section 1b — then run this again.")
        print("To verify the application code without a server:")
        print("    .\\venv\\Scripts\\python.exe verify_mongo.py --offline")
        return 1

    scratch_name = mongodb.MONGO_DB_NAME + "_verify"
    client = mongodb.get_client()
    print(f"  (using throwaway database '{scratch_name}'; "
          f"'{mongodb.MONGO_DB_NAME}' is not touched)\n")
    try:
        run_suite(client[scratch_name]["documents"])
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
