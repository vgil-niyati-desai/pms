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


# ---------------------------------------------------------------------------
# Automatic detection of sensitive figures
#
# The suggestion pass. What is proved here is not "a regex matched" but the
# claims the feature actually rests on:
#
#   * the boxes it suggests are in the *manual* coordinate format, and land on
#     the figures -- posted to /redacted-copy with no special casing they
#     remove exactly those amounts and leave the rest of the page alone;
#   * they land there on a rotated page too, measured against the rendered
#     page image the reviewer is actually looking at; and
#   * looking at a document changes nothing about it.
#
# Plus the boring-but-important ones: it does not fire on part numbers, page
# numbers or years, and a file it cannot read says so rather than coming back
# empty and looking clean.
# ---------------------------------------------------------------------------

DETECT_LINES = [
    "Project: Riverside Bridge Rehabilitation",          # no figures at all
    "Contract Value: Rs. 4,50,00,000",
    "EMD 2.5 lakh",
    "Total 450000",
    "Unit rate 8,912 per sqm",
    "Quoted amount USD 1,200.50",
    "Tender reference PO-4500-B dated 12 March 2024",     # must NOT be flagged
    "Page 3 of 12",                                       # must NOT be flagged
]

# The figures that must come out of the copy, and the text that must survive.
DETECT_SECRETS = ("4,50,00,000", "2.5 lakh", "450000", "8,912", "1,200.50")
DETECT_KEEP = ("Riverside Bridge Rehabilitation", "PO-4500-B", "Page 3 of 12")

# The figure the rotation checks follow through the coordinate conversion.
DETECT_ROTATION_PROBE = "4,50,00,000"


def _detectable_pdf(rotation=0):
    """A one-page PDF of known lines, optionally rotated."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 100
    for line in DETECT_LINES:
        page.insert_text((72, y), line, fontsize=12)
        y += 40
    if rotation:
        page.set_rotation(rotation)
    content = doc.tobytes()
    doc.close()
    return content


def _post_detect_pdf(client, name, content, client_name):
    return client.post(
        "/documents/",
        data={"document_type": "Cost Sheet", "category": "Tendering",
              "client_name": client_name, "submitted_by": "vansh"},
        files={"document_file": (name, io.BytesIO(content), "application/pdf")},
    )


def _darkest_in(pixmap, x0, y0, x1, y1):
    """The darkest channel sample inside a box of a rendered page image.

    Near 255 is blank paper; a low value means glyphs. This is how "the box is
    on the figure" gets answered without trusting the code that placed it --
    it is read off the same image the browser draws the box over.
    """
    darkest = 255
    for y in range(max(0, y0), min(pixmap.height, y1)):
        row = y * pixmap.stride
        for x in range(max(0, x0), min(pixmap.width, x1)):
            darkest = min(darkest, pixmap.samples[row + x * pixmap.n])
    return darkest


def _check_rotation(client, collection, rotation):
    """A rotated page's suggestions land on the figure, as rendered.

    PyMuPDF reports extracted text in the *unrotated* page, while the page
    images the browser draws on are rendered from the rotated `page.rect`.
    Skip the conversion between them and every suggestion on a /Rotate 90 page
    is offered somewhere the figure is not -- so both halves are checked: the
    converted box is on ink, and the unconverted one is on blank paper.
    """
    import pymupdf

    from bson import ObjectId

    from app import storage

    res = _post_detect_pdf(client, f"rotated{rotation}.pdf",
                           _detectable_pdf(rotation), f"Rotation Check {rotation}")
    if not check(f"POST a /Rotate {rotation} PDF -> 201", res.status_code == 201, res.text):
        return
    entry = res.json()
    res = client.get(f"/documents/{entry['id']}/redaction/suggestions")
    spots = (res.json().get("detections") or []) if res.status_code == 200 else []
    check(f"the scan reads a /Rotate {rotation} page", len(spots) >= 5, res.text)

    path = storage.upload_path(
        collection.find_one({"_id": ObjectId(entry["id"])})["stored_file_name"])
    doc = pymupdf.open(path)
    page = doc[0]
    bounds = page.rect
    # Rendered exactly the way redaction.render_page_image renders it, which
    # is the image the fractions are fractions of.
    shot = page.get_pixmap()

    blank = 0
    for spot in spots:
        if _darkest_in(
            shot,
            int(spot["x"] * shot.width), int(spot["y"] * shot.height),
            int(round((spot["x"] + spot["width"]) * shot.width)),
            int(round((spot["y"] + spot["height"]) * shot.height)),
        ) > 200:
            blank += 1
    check(f"every /Rotate {rotation} suggestion sits on inked pixels of the rendered page",
          bool(spots) and blank == 0,
          f"{blank} of {len(spots)} landed on blank paper")

    # The same figure, normalised *without* the rotation conversion -- what a
    # naive implementation would have offered. It has to land on blank paper,
    # or the check above would pass on a page busy enough to hit ink anywhere.
    hit = page.search_for(DETECT_ROTATION_PROBE)
    if check(f"the probe figure is findable on the /Rotate {rotation} page", bool(hit)):
        raw = hit[0]
        check(f"an unconverted /Rotate {rotation} box would miss the figure",
              _darkest_in(
                  shot,
                  int(raw.x0 / bounds.width * shot.width),
                  int(raw.y0 / bounds.height * shot.height),
                  int(round(raw.x1 / bounds.width * shot.width)),
                  int(round(raw.y1 / bounds.height * shot.height)),
              ) > 200,
              "the unconverted box still landed on ink, so this proves nothing")

    doc.close()
    client.delete(f"/documents/{entry['id']}")


def run_detection_suite(client, collection, id_without_file):
    """Drive the suggestion endpoint, and prove the boxes land on the money."""
    import hashlib

    from bson import ObjectId

    from app import detection, storage

    try:
        import pymupdf
    except ImportError:
        check("PyMuPDF is installed for detection", False,
              "pip install -r requirements.txt")
        return

    pdf_bytes = _detectable_pdf()
    res = _post_detect_pdf(client, "quotation.pdf", pdf_bytes, "Detection Check Ltd")
    if not check("POST a readable PDF for detection -> 201", res.status_code == 201, res.text):
        return
    entry = res.json()
    stored = collection.find_one({"_id": ObjectId(entry["id"])})["stored_file_name"]
    path = storage.upload_path(stored)
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()

    # --- the scan ----------------------------------------------------------
    res = client.get(f"/documents/{entry['id']}/redaction/suggestions")
    if not check("GET /redaction/suggestions -> 200", res.status_code == 200, res.text):
        client.delete(f"/documents/{entry['id']}")
        return
    scan = res.json()
    # A born-digital PDF must take the text path whether or not OCR exists on
    # the machine: OCR is for the pages that need it, not a replacement for
    # reading a content stream that is already there.
    check("the scan reports which engine read the document",
          scan.get("engine") == "text"
          and scan.get("ocr_available") is detection.ocr_available(), res.text)
    check("the scan reports the page it read",
          scan.get("pages_scanned") == 1 and scan.get("pages_without_text") == [], res.text)

    found = scan.get("detections") or []
    check("the scan found the financial figures", len(found) >= 5, f"{len(found)} found")
    matched = " | ".join(d["text"] for d in found)
    for secret in DETECT_SECRETS:
        check(f"the scan found {secret!r}", secret in matched, matched)
    check("every suggestion says what it matched and how sure it is",
          all(d.get("label") and d.get("text") and d.get("id")
              and d.get("confidence") in ("high", "medium")
              and d.get("category") == "financial" for d in found), matched)

    # A suggestion is exactly a RedactionArea plus review material: same four
    # normalised numbers, same bounds, same zero-based page. If this ever
    # drifts, an accepted box stops being sendable through the manual endpoint.
    check("suggestions are in the manual redaction coordinate format",
          all(d["page"] == 0 and 0 <= d["x"] < 1 and 0 <= d["y"] < 1
              and 0 < d["width"] <= 1 and 0 < d["height"] <= 1
              and d["x"] + d["width"] <= 1.0001 and d["y"] + d["height"] <= 1.0001
              for d in found),
          str([(d["x"], d["y"], d["width"], d["height"]) for d in found]))

    # --- the part that matters: the boxes are on the money ------------------
    # Posted through the *existing* endpoint with no special casing, which is
    # the whole design: an accepted suggestion is an ordinary redaction area.
    areas = [{"page": d["page"], "x": d["x"], "y": d["y"],
              "width": d["width"], "height": d["height"]} for d in found]
    res = client.post(f"/documents/{entry['id']}/redacted-copy", json={"areas": areas})
    if check("accepted suggestions POST to /redacted-copy unchanged -> 200",
             res.status_code == 200, res.text):
        copied = pymupdf.open(stream=res.content, filetype="pdf")
        text = copied[0].get_text()
        streams = copied[0].read_contents().decode("latin-1")
        copied.close()
        for secret in DETECT_SECRETS:
            check(f"accepting the suggestions removes {secret!r}",
                  secret not in text and secret not in streams, repr(text[:80]))
        for kept in DETECT_KEEP:
            check(f"the suggestions leave {kept!r} alone", kept in text, repr(text[:120]))

    # --- what must not be suggested ----------------------------------------
    # A part number, a page number and a year are digits on a page, not money.
    # Without these the feature is a box over every number in the document.
    check("a part number is not suggested", "4500-B" not in matched, matched)
    check("a page number is not suggested",
          not any(d["text"].strip() in ("3", "12", "Page 3", "3 of 12") for d in found), matched)
    check("a bare year is not suggested", "2024" not in matched, matched)

    # --- rotation -----------------------------------------------------------
    for rotation in (90, 270):
        _check_rotation(client, collection, rotation)

    # --- a document with no text ------------------------------------------
    res = client.post(
        "/documents/",
        data={"document_type": "Site Photo", "category": "Roads",
              "client_name": "Detection Image Ltd", "submitted_by": "vansh"},
        files={"document_file": ("scan.png", io.BytesIO(_flat_png(210)), "image/png")},
    )
    if check("POST a PNG for detection -> 201", res.status_code == 201, res.text):
        image_entry = res.json()
        res = client.get(f"/documents/{image_entry['id']}/redaction/suggestions")
        payload = res.json() if res.status_code == 200 else {}
        check("an image scan -> 200 with nothing found",
              res.status_code == 200 and payload.get("detections") == [], res.text)
        # A blank image is the case that separates "read it, there was nothing
        # on it" from "could not read it". Both come back with no detections,
        # and only the second one leaves the page still needing a human --
        # so a page OCR looked at must not be reported as unread.
        if detection.ocr_available():
            check("a blank image is reported as read, not as unreadable",
                  payload.get("engine") == "ocr"
                  and payload.get("ocr_pages") == [0]
                  and payload.get("pages_without_text") == [], res.text)
            check("a blank image says nothing was found rather than that it could not look",
                  "No financial figures were found" in (payload.get("message") or "")
                  and "cannot" not in (payload.get("message") or ""), res.text)
        else:
            # Without language data the honest answer is the opposite one, and
            # it still must not read as a clean page. The message has to say
            # how to fix it, which is the hint the module publishes.
            check("without OCR an image says it could not be read",
                  payload.get("engine") == "none"
                  and payload.get("pages_without_text") == [0]
                  and detection.OCR_MISSING_HINT in (payload.get("message") or ""),
                  res.text)
        client.delete(f"/documents/{image_entry['id']}")

    # --- reading a document changes nothing --------------------------------
    after = hashlib.sha256(open(path, "rb").read()).hexdigest()
    check("scanning leaves the stored original byte-for-byte unchanged", before == after)
    check("scanning created no new entry",
          collection.count_documents({"client_name": "Detection Check Ltd"}) == 1)
    check("scanning an entry with no file -> 404",
          client.get(f"/documents/{id_without_file}/redaction/suggestions").status_code == 404)
    check("scanning a missing entry -> 404",
          client.get("/documents/0123456789abcdef01234567/redaction/suggestions")
          .status_code == 404)

    client.delete(f"/documents/{entry['id']}")


def _scanned_page_pixmap(dpi=200):
    """A picture of the detection lines: what a scanned page really is."""
    import pymupdf

    src = pymupdf.open()
    page = src.new_page(width=595, height=842)
    y = 100
    for line in DETECT_LINES:
        page.insert_text((72, y), line, fontsize=12)
        y += 40
    pix = page.get_pixmap(dpi=dpi)
    src.close()
    return pix


def _build_pdf(pages):
    """A PDF from a recipe of ("text"|"scan", rotation) pages.

    One builder for every shape the scan has to cope with: born-digital,
    scanned, and the mixture of the two that a real tender submission is.
    """
    import pymupdf

    doc = pymupdf.open()
    pix = None
    for kind, rotation in pages:
        page = doc.new_page(width=595, height=842)
        if kind == "text":
            y = 100
            for line in DETECT_LINES:
                page.insert_text((72, y), line, fontsize=12)
                y += 40
        else:
            pix = pix or _scanned_page_pixmap()
            page.insert_image(page.rect, pixmap=pix)
        if rotation:
            page.set_rotation(rotation)
    content = doc.tobytes()
    doc.close()
    return content


def _image_bytes(ext):
    """A JPG or PNG of the same lines, for the image path."""
    pix = _scanned_page_pixmap(dpi=150)
    return pix.tobytes("jpg", jpg_quality=95) if ext == "jpg" else pix.tobytes("png")


def _scan_document(client, collection, name, content, media_type, client_name):
    """Upload something, scan it, and hand back (entry, scan payload)."""
    res = client.post(
        "/documents/",
        data={"document_type": "Cost Sheet", "category": "Tendering",
              "client_name": client_name, "submitted_by": "vansh"},
        files={"document_file": (name, io.BytesIO(content), media_type)},
    )
    if not check(f"POST {name} -> 201", res.status_code == 201, res.text):
        return None, {}
    entry = res.json()
    res = client.get(f"/documents/{entry['id']}/redaction/suggestions")
    if not check(f"scan {name} -> 200", res.status_code == 200, res.text):
        return entry, {}
    return entry, res.json()


def _figures_in(scan):
    return " | ".join(d["text"] for d in (scan.get("detections") or []))


def run_ocr_suite(client, collection):
    """Every supported format reaches the same rules and the same boxes.

    Six shapes, one set of claims: the financial figures are found, the boxes
    are in the manual coordinate format, and feeding them to the existing
    /redacted-copy endpoint actually covers those figures and nothing else.

    Text PDFs are proved by extracting the copy's text. Scans and images have
    no text to extract, so they are proved at the pixel level instead -- the
    suggested area is dark before the redaction and white after it, which is
    the same claim made the only way the format allows.
    """
    import hashlib

    from bson import ObjectId

    from app import detection, storage

    try:
        import pymupdf
    except ImportError:
        check("PyMuPDF is installed for OCR detection", False,
              "pip install -r requirements.txt")
        return

    # OCR is a *system* install, so this is a real precondition rather than a
    # formality. Everything below it is skipped rather than reported as broken
    # on a machine that simply does not have Tesseract.
    if not check("Tesseract OCR is available to the backend", detection.ocr_available(),
                 detection.OCR_MISSING_HINT):
        return
    check("the scan reports OCR as available",
          detection.tessdata_dir() is not None, str(detection.tessdata_dir()))

    def darkest(pixmap, spot):
        """The darkest pixel inside a normalised box of a rendered page."""
        x0, y0 = int(spot["x"] * pixmap.width), int(spot["y"] * pixmap.height)
        x1 = int(round((spot["x"] + spot["width"]) * pixmap.width))
        y1 = int(round((spot["y"] + spot["height"]) * pixmap.height))
        value = 255
        for y in range(max(0, y0), min(pixmap.height, y1)):
            row = y * pixmap.stride
            for x in range(max(0, x0), min(pixmap.width, x1)):
                value = min(value, pixmap.samples[row + x * pixmap.n])
        return value

    # ---- 1. a born-digital PDF still takes the text path -------------------
    entry, scan = _scan_document(client, collection, "text.pdf", _build_pdf([("text", 0)]),
                                 "application/pdf", "OCR Text Ltd")
    if entry:
        check("a text PDF is read from its text layer, not OCR'd",
              scan.get("engine") == "text" and scan.get("ocr_pages") == []
              and scan.get("text_pages") == [0], str(scan.get("engine")))
        check("every suggestion on a text PDF is marked as text",
              all(d.get("source") == "text" for d in scan.get("detections") or []),
              _figures_in(scan))
        client.delete(f"/documents/{entry['id']}")

    # ---- 2. a scanned PDF goes through OCR ---------------------------------
    entry, scan = _scan_document(client, collection, "scanned.pdf", _build_pdf([("scan", 0)]),
                                 "application/pdf", "OCR Scan Ltd")
    if entry:
        found = scan.get("detections") or []
        check("a scanned PDF is read by OCR",
              scan.get("engine") == "ocr" and scan.get("ocr_pages") == [0]
              and scan.get("pages_without_text") == [], str(scan.get("engine")))
        matched = _figures_in(scan)
        for secret in DETECT_SECRETS:
            check(f"OCR found {secret!r} on a scanned page", secret in matched, matched)
        check("every suggestion on a scanned page is marked as OCR",
              bool(found) and all(d.get("source") == "ocr" for d in found), matched)
        check("a scanned page's suggestions are in the manual coordinate format",
              all(d["page"] == 0 and 0 <= d["x"] < 1 and 0 <= d["y"] < 1
                  and 0 < d["width"] <= 1 and 0 < d["height"] <= 1 for d in found),
              str([(d["x"], d["y"]) for d in found]))

        # A scan has no text to extract, so the proof is in the pixels: dark
        # before, white after, and an unselected line untouched either way.
        path = storage.upload_path(
            collection.find_one({"_id": ObjectId(entry["id"])})["stored_file_name"])
        areas = [{"page": d["page"], "x": d["x"], "y": d["y"],
                  "width": d["width"], "height": d["height"]} for d in found]
        res = client.post(f"/documents/{entry['id']}/redacted-copy", json={"areas": areas})
        if check("a scanned page's suggestions POST to /redacted-copy -> 200",
                 res.status_code == 200, res.text):
            before_doc = pymupdf.open(path)
            after_doc = pymupdf.open(stream=res.content, filetype="pdf")
            before_shot = before_doc[0].get_pixmap()
            after_shot = after_doc[0].get_pixmap()
            dark_before = all(darkest(before_shot, d) < 128 for d in found)
            white_after = all(darkest(after_shot, d) > 245 for d in found)
            check("every suggested area on the scan had ink in it", dark_before)
            check("redacting a scanned page blanks those pixels", white_after)
            # The control: a line nobody selected has to survive, or "it all
            # went white" would pass for a page that was simply wiped.
            control = {"x": 0.10, "y": 0.105, "width": 0.55, "height": 0.025}
            check("an unselected line on the scan is untouched",
                  darkest(before_shot, control) < 128
                  and darkest(after_shot, control) < 128,
                  f"before {darkest(before_shot, control)} after {darkest(after_shot, control)}")
            before_doc.close()
            after_doc.close()
        check("OCR left the stored original unchanged",
              hashlib.sha256(open(path, "rb").read()).hexdigest()
              == hashlib.sha256(client.get(f"/documents/{entry['id']}/file").content).hexdigest())
        client.delete(f"/documents/{entry['id']}")

    # ---- 3. a mixed PDF routes per page ------------------------------------
    entry, scan = _scan_document(
        client, collection, "mixed.pdf",
        _build_pdf([("text", 0), ("scan", 0), ("text", 0), ("scan", 0)]),
        "application/pdf", "OCR Mixed Ltd")
    if entry:
        found = scan.get("detections") or []
        check("a mixed PDF reports itself as mixed", scan.get("engine") == "mixed",
              str(scan.get("engine")))
        check("a mixed PDF routes per page, not per document",
              scan.get("text_pages") == [0, 2] and scan.get("ocr_pages") == [1, 3],
              f"text {scan.get('text_pages')} ocr {scan.get('ocr_pages')}")
        check("figures are found on every page of a mixed PDF",
              sorted({d["page"] for d in found}) == [0, 1, 2, 3],
              str(sorted({d["page"] for d in found})))
        # The point of the per-detection source: the same figure on a text
        # page and a scanned page is labelled differently, because only one of
        # them was guessed at.
        by_source = {page: {d["source"] for d in found if d["page"] == page}
                     for page in (0, 1, 2, 3)}
        check("each page's suggestions carry the source that read them",
              by_source == {0: {"text"}, 1: {"ocr"}, 2: {"text"}, 3: {"ocr"}},
              str(by_source))
        areas = [{"page": d["page"], "x": d["x"], "y": d["y"],
                  "width": d["width"], "height": d["height"]} for d in found]
        res = client.post(f"/documents/{entry['id']}/redacted-copy", json={"areas": areas})
        if check("a mixed PDF's suggestions POST to /redacted-copy -> 200",
                 res.status_code == 200, res.text):
            copy = pymupdf.open(stream=res.content, filetype="pdf")
            text = copy[0].get_text() + copy[2].get_text()
            scanned_clean = all(
                darkest(copy[page].get_pixmap(), d) > 245
                for page in (1, 3) for d in found if d["page"] == page)
            copy.close()
            for secret in DETECT_SECRETS:
                check(f"the mixed PDF's text pages lose {secret!r}", secret not in text,
                      repr(text[:80]))
            check("the mixed PDF's scanned pages are blanked where suggested", scanned_clean)
            check("the mixed PDF's text pages keep their unselected text",
                  "Riverside Bridge Rehabilitation" in text, repr(text[:120]))
        client.delete(f"/documents/{entry['id']}")

    # ---- 4. rotated scanned pages ------------------------------------------
    # OCR renders the page to read it, and a rotated page renders sideways --
    # which Tesseract returns noise for. The scan sets the rotation aside
    # while it reads, so this is the check that it does.
    for rotation in (90, 270):
        entry, scan = _scan_document(
            client, collection, f"scan_rot{rotation}.pdf",
            _build_pdf([("scan", rotation)]), "application/pdf",
            f"OCR Rotation {rotation}")
        if not entry:
            continue
        found = scan.get("detections") or []
        matched = _figures_in(scan)
        check(f"OCR reads a /Rotate {rotation} scanned page instead of noise",
              all(secret in matched for secret in DETECT_SECRETS), matched)
        path = storage.upload_path(
            collection.find_one({"_id": ObjectId(entry["id"])})["stored_file_name"])
        doc = pymupdf.open(path)
        shot = doc[0].get_pixmap()
        blank = sum(1 for d in found if darkest(shot, d) > 200)
        doc.close()
        check(f"every /Rotate {rotation} OCR box sits on ink of the rendered page",
              bool(found) and blank == 0, f"{blank} of {len(found)} on blank paper")
        client.delete(f"/documents/{entry['id']}")

    # ---- 5 & 6. JPG and PNG ------------------------------------------------
    for ext, media_type in (("png", "image/png"), ("jpg", "image/jpeg")):
        entry, scan = _scan_document(client, collection, f"quote.{ext}",
                                     _image_bytes(ext), media_type,
                                     f"OCR Image {ext.upper()}")
        if not entry:
            continue
        found = scan.get("detections") or []
        matched = _figures_in(scan)
        check(f"a {ext.upper()} is read by OCR",
              scan.get("kind") == "image" and scan.get("engine") == "ocr",
              f"{scan.get('kind')}/{scan.get('engine')}")
        for secret in DETECT_SECRETS:
            check(f"OCR found {secret!r} in a {ext.upper()}", secret in matched, matched)

        # An image's fractions resolve against the image itself, which is what
        # redaction._redact_image does -- so the same box has to be on ink
        # there too, and white in the copy.
        path = storage.upload_path(
            collection.find_one({"_id": ObjectId(entry["id"])})["stored_file_name"])
        source_pix = pymupdf.Pixmap(path)
        if source_pix.colorspace is None or source_pix.colorspace.n != 3:
            source_pix = pymupdf.Pixmap(pymupdf.csRGB, source_pix)
        if source_pix.alpha:
            source_pix = pymupdf.Pixmap(source_pix, 0)
        check(f"every {ext.upper()} suggestion sits on ink of the image",
              bool(found) and all(darkest(source_pix, d) < 128 for d in found))

        areas = [{"page": 0, "x": d["x"], "y": d["y"],
                  "width": d["width"], "height": d["height"]} for d in found]
        res = client.post(f"/documents/{entry['id']}/redacted-copy", json={"areas": areas})
        if check(f"a {ext.upper()}'s suggestions POST to /redacted-copy -> 200",
                 res.status_code == 200, res.text):
            out = pymupdf.Pixmap(io.BytesIO(res.content))
            if out.colorspace is None or out.colorspace.n != 3:
                out = pymupdf.Pixmap(pymupdf.csRGB, out)
            if out.alpha:
                out = pymupdf.Pixmap(out, 0)
            # JPEG is lossy, so "white" is a threshold rather than exactly 255.
            check(f"redacting the {ext.upper()} blanks those pixels",
                  all(darkest(out, d) > 240 for d in found),
                  str([darkest(out, d) for d in found]))
            control = {"x": 0.10, "y": 0.105, "width": 0.55, "height": 0.025}
            check(f"an unselected line of the {ext.upper()} is untouched",
                  darkest(out, control) < 128, str(darkest(out, control)))
        check(f"scanning a {ext.upper()} left the stored original unchanged",
              hashlib.sha256(open(path, "rb").read()).hexdigest()
              == hashlib.sha256(client.get(f"/documents/{entry['id']}/file").content).hexdigest())
        client.delete(f"/documents/{entry['id']}")


# ---------------------------------------------------------------------------
# The figures a line-by-line numeric pass misses
#
# Every case here came from the feature being used on a real scanned tender,
# and each one is a way the redaction was worth nothing:
#
#   * an amount written out in brackets after the numeral. Covering
#     "Rs.45,00,000/-" and leaving "(Rupees Forty Five Lakh Only)" beside it
#     redacts the punctuation and nothing else -- the amount is still there in
#     plain English.
#   * "Rs.45,00,000" with no space after the dot. The guard that stops a
#     figure matching inside a part number ("PO-4500-B") also rejected a digit
#     preceded by a dot, and "Rs." ends in one -- so one of the commonest ways
#     to write an amount was silently unmatchable.
#   * a rate in a table, whose only label is the column heading a row above.
#
# The document below states the same amount in several of these shapes, so a
# regression in any one of them leaves a figure on the page.
# ---------------------------------------------------------------------------

WORDS_DOC = [
    "TENDER FOR RIVERSIDE BRIDGE REHABILITATION",
    "The contractor shall work as per drawing PO-4500-B dated 12 March 2024.",
    "Contact: Phone 9876543210, Account No. 45000123",
    "Regd. Office: 1st Floor, IT Park, Nagpur, Maharashtra 440010",
    "Tel: 0712-2345678   Mobile +91 98765 43210",
    "Advance paid Rs.8,50,000 (Rupees Eight Lakh Fifty Thousand Only)",
    "The total contract value is Rs.45,00,000/- (Rupees Forty Five Lakh Only)",
    "EMD of Rs. 90,000 (Rupees Ninety Thousand Only) must accompany the bid.",
    "Item                          Qty          Rate",
    "Deck slab concrete            250        8,912.50",
    "Approach road works           180          450000",
    "Amount in words: Indian Rupees Forty Five Lakh Only",
    "Page 3 of 12",
]

# Every one of these must be gone from the copy.
WORDS_MUST_GO = (
    "45,00,000", "90,000", "8,912.50", "450000",
    "Forty Five Lakh Only", "Ninety Thousand Only",
    "8,50,000", "Eight Lakh Fifty Thousand Only",
)
# ...and every one of these must survive it. The phone number and the account
# number are the point of the identifier guard: digits with a label in front
# of them that makes them a reference, not money.
WORDS_MUST_STAY = (
    "RIVERSIDE BRIDGE", "PO-4500-B", "Deck slab concrete",
    "9876543210", "45000123", "Page 3 of 12",
    # An address and a phone number are not money, and covering them makes the
    # copy useless for the person who receives it.
    "440010", "2345678", "98765",
)


def _words_pdf():
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for line in WORDS_DOC:
        page.insert_text((60, y), line, fontsize=10.5)
        y += 34
    content = doc.tobytes()
    doc.close()
    return content


def run_written_amount_suite(client, collection):
    """An amount stated in words, or under a column heading, is still covered."""
    from bson import ObjectId

    from app import storage

    try:
        import pymupdf
    except ImportError:
        check("PyMuPDF is installed for written-amount detection", False,
              "pip install -r requirements.txt")
        return

    res = client.post(
        "/documents/",
        data={"document_type": "Cost Sheet", "category": "Tendering",
              "client_name": "Written Amount Ltd", "submitted_by": "vansh"},
        files={"document_file": ("tender.pdf", io.BytesIO(_words_pdf()),
                                 "application/pdf")},
    )
    if not check("POST a tender page stating amounts in words -> 201",
                 res.status_code == 201, res.text):
        return
    entry = res.json()

    res = client.get(f"/documents/{entry['id']}/redaction/suggestions")
    if not check("scan the tender page -> 200", res.status_code == 200, res.text):
        client.delete(f"/documents/{entry['id']}")
        return
    found = res.json().get("detections") or []
    matched = " | ".join(d["text"] for d in found)

    check("an amount in words after the numeral is suggested",
          "Forty Five Lakh Only" in matched, matched)
    check("a second amount in words on another line is suggested",
          "Ninety Thousand Only" in matched, matched)
    check("an 'Amount in words:' line is suggested",
          matched.count("Forty Five Lakh Only") >= 2, matched)
    check("the amounts in words are labelled as such",
          any(d["label"] == "Amount in words" for d in found),
          str(sorted({d["label"] for d in found})))
    check("Rs.45,00,000 with no space after the dot is suggested",
          "45,00,000" in matched, matched)
    check("a rate labelled only by its column heading is suggested",
          "450000" in matched and "8,912.50" in matched, matched)
    check("the column heading is what labelled it",
          any(d["rule"] == "column_figure" for d in found),
          str(sorted({d["rule"] for d in found})))
    # The box must be the figure, not the figure plus the quantity beside it.
    # Reported from real use: a PIN code in an address and a phone number in a
    # footer were both being suggested, and the phone number at *high*
    # confidence because the word "Value" in a sentence elsewhere on the page
    # was read as a table heading above it. Neither is money; both made the
    # review list untrustworthy.
    check("a PIN code in an address is not suggested",
          "440010" not in matched, matched)
    check("a phone number is not suggested, whole or in parts",
          not any(part in matched for part in ("9876543210", "98765", "43210")),
          matched)
    check("a landline number is not suggested", "2345678" not in matched, matched)
    check("a sentence containing 'Value' is not read as a column heading",
          all("98765" not in d["text"] and "440010" not in d["text"]
              for d in found if d["rule"] == "column_figure"),
          str([d["text"] for d in found if d["rule"] == "column_figure"]))
    # Reported from real use: Rs. with no space matched only because a keyword
    # shared the line, and grouped_number boxed a fragment of the number.
    check("Rs.8,50,000 is matched whole, not as a fragment",
          any(d["text"].endswith("8,50,000") or "Rs.8,50,000" in d["text"]
              for d in found), matched)

    check("a column figure's box does not swallow the neighbouring column",
          all("250" not in d["text"] and "180" not in d["text"]
              for d in found if d["rule"] == "column_figure"),
          str([d["text"] for d in found if d["rule"] == "column_figure"]))

    # --- and the whole point: apply them and read what is left --------------
    areas = [{"page": d["page"], "x": d["x"], "y": d["y"],
              "width": d["width"], "height": d["height"]} for d in found]
    res = client.post(f"/documents/{entry['id']}/redacted-copy", json={"areas": areas})
    if check("the suggestions POST to /redacted-copy -> 200", res.status_code == 200,
             res.text):
        copy = pymupdf.open(stream=res.content, filetype="pdf")
        text = copy[0].get_text()
        streams = copy[0].read_contents().decode("latin-1")
        copy.close()
        for secret in WORDS_MUST_GO:
            check(f"accepting the suggestions removes {secret!r}",
                  secret not in text and secret not in streams, repr(text[:100]))
        for kept in WORDS_MUST_STAY:
            check(f"the suggestions leave {kept!r} alone", kept in text,
                  repr(text[:200]))

    client.delete(f"/documents/{entry['id']}")


# ---------------------------------------------------------------------------
# A photograph embedded in a PDF
#
# The case this feature is actually used on, and the one that was silently
# broken: a phone photo of a signed work order, dropped into a PDF page. It
# failed for a reason that is invisible from the outside -- the OCR pass
# rasterised the *page* at a fixed 300 dpi, which resamples the photograph.
# Upscaling a 200-dpi capture interpolates its glyph edges and amplifies its
# JPEG artefacts, and Tesseract answers with confident speckle ('f', 'j', '|',
# 'SS ean') rather than failing. Where the photo sat on the page decided
# whether it worked:
#
#     full page            read correctly
#     inset with a margin  read as noise
#     in a corner          read as nothing at all
#
# So the fix was to stop rendering the page: the image is OCR'd at its own
# pixels and the word boxes are mapped back onto the page. These checks pin
# that down at the placements that used to fail, and pin down the two things
# that make the result worth anything -- BOTH amounts found however they are
# spaced, and the words beside each of them found too.
# ---------------------------------------------------------------------------

PHOTO_LINES = [
    "VIRTUAL GALAXY INFOTECH LTD.",
    "Regd. Office: IT Park, Nagpur, Maharashtra 440010",
    "Phone: +91 98765 43210    GSTIN: 27AABCU9603R1ZM",
    "WORK ORDER   Ref: PO-4500-B   Dated: 12 March 2024",
    "1.  Contract Value: Rs.8,50,000",
    "    (Rupees Eight Lakh Fifty Thousand Only)",
    "2.  Earnest Money Deposit: Rs. 8,50,000",
    "    (Rupees Eight Lakh Fifty Thousand Only)",
    "Terms: Payment within 30 days of invoice.",
]

# Two amounts, written with different spacing after "Rs.", and the words
# beside each. All four have to be found, every time.
PHOTO_AMOUNT = "8,50,000"
PHOTO_WORDS = "Eight Lakh Fifty Thousand"
# A phone number, a PIN code, a GSTIN, a reference and a year share the page.
# None of them is money.
PHOTO_NOT_MONEY = ("440010", "9876543210", "98765", "43210", "27AABCU", "4500-B")


def _photograph(seed=7, dpi=200, quality=60):
    """A page of text turned into something a camera might have produced.

    Not a clean render: an uneven light gradient, sensor noise and JPEG
    artefacts, because those are what break OCR and a pristine bitmap would
    let a broken pipeline pass.
    """
    import random

    import pymupdf

    random.seed(seed)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for line in PHOTO_LINES:
        page.insert_text((55, y), line, fontsize=11)
        y += 34
    pixmap = page.get_pixmap(dpi=dpi)
    doc.close()

    samples = bytearray(pixmap.samples)
    width, height, n, stride = pixmap.width, pixmap.height, pixmap.n, pixmap.stride
    for row_index in range(height):
        shade = 0.82 + 0.18 * (row_index / height)
        row = row_index * stride
        for column in range(0, width * n, n):
            i = row + column
            for channel in range(min(3, n)):
                value = samples[i + channel] * shade + random.randint(-5, 5)
                samples[i + channel] = max(0, min(255, int(value)))
    raw = pymupdf.Pixmap(pymupdf.csRGB, width, height, bytes(samples), 0)
    return raw.tobytes("jpg", jpg_quality=quality)


def _photo_pdf(photo, rect=None, rotation=0, header=False):
    """The photograph embedded in a PDF page, placed where asked."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    if header:
        # A typed heading over the photograph: the page then HAS a text layer,
        # which used to be enough to stop it ever being OCR'd.
        page.insert_text((50, 30), "CONFIDENTIAL - Annexure B to the tender", fontsize=11)
        page.insert_text((50, 46), "Scanned copy of the signed work order follows.", fontsize=9)
    page.insert_image(rect or page.rect, stream=photo)
    if rotation:
        page.set_rotation(rotation)
    content = doc.tobytes()
    doc.close()
    return content


def run_photo_suite(client, collection):
    """A photograph inside a PDF is read wherever it sits on the page."""
    from bson import ObjectId

    from app import detection, storage

    try:
        import pymupdf
    except ImportError:
        check("PyMuPDF is installed for the photo suite", False,
              "pip install -r requirements.txt")
        return
    if not detection.ocr_available():
        check("Tesseract is available for the photo suite", False,
              detection.OCR_MISSING_HINT)
        return

    photo = _photograph()

    # Each of these placements resamples the photograph differently under a
    # fixed-dpi page render, and all but the first used to come back as noise.
    placements = [
        ("filling the page", None, 0, False),
        ("inset with a margin", pymupdf.Rect(60, 60, 535, 782), 0, False),
        ("inset deeply", pymupdf.Rect(120, 120, 475, 722), 0, False),
        ("in a corner", pymupdf.Rect(400, 600, 560, 820), 0, False),
        ("under a typed heading", pymupdf.Rect(60, 70, 535, 740), 0, True),
        ("on a /Rotate 90 page", None, 90, False),
        ("on a /Rotate 270 page", pymupdf.Rect(60, 60, 535, 782), 270, False),
    ]

    for label, rect, rotation, header in placements:
        res = client.post(
            "/documents/",
            data={"document_type": "Work Order", "category": "Tendering",
                  "client_name": f"Photo {label}", "submitted_by": "vansh"},
            files={"document_file": (f"photo.pdf",
                                     io.BytesIO(_photo_pdf(photo, rect, rotation, header)),
                                     "application/pdf")},
        )
        if not check(f"POST a photo {label} -> 201", res.status_code == 201, res.text):
            continue
        entry = res.json()
        res = client.get(f"/documents/{entry['id']}/redaction/suggestions")
        if not check(f"scan a photo {label} -> 200", res.status_code == 200, res.text):
            client.delete(f"/documents/{entry['id']}")
            continue
        found = res.json().get("detections") or []
        matched = " | ".join(d["text"] for d in found)

        # Both amounts, not just the first, and whatever the spacing after Rs.
        check(f"a photo {label}: both amounts are found",
              matched.count(PHOTO_AMOUNT) >= 2,
              f"{matched.count(PHOTO_AMOUNT)} found in: {matched}")
        check(f"a photo {label}: both amounts-in-words are found",
              matched.count(PHOTO_WORDS) >= 2,
              f"{matched.count(PHOTO_WORDS)} found in: {matched}")
        check(f"a photo {label}: no phone, PIN, GSTIN or reference is suggested",
              not any(noise in matched for noise in PHOTO_NOT_MONEY), matched)
        check(f"a photo {label}: it is reported as read by OCR",
              all(d["source"] == "ocr" for d in found if PHOTO_AMOUNT in d["text"]),
              str(sorted({d["source"] for d in found})))

        # The boxes have to be on the figures as *rendered*, which is the only
        # thing the reviewer can check them against.
        path = storage.upload_path(
            collection.find_one({"_id": ObjectId(entry["id"])})["stored_file_name"])
        doc = pymupdf.open(path)
        shot = doc[0].get_pixmap(dpi=150)
        blank = 0
        for spot in found:
            darkest = 255
            x0, y0 = int(spot["x"] * shot.width), int(spot["y"] * shot.height)
            x1 = int(round((spot["x"] + spot["width"]) * shot.width))
            y1 = int(round((spot["y"] + spot["height"]) * shot.height))
            for y in range(max(0, y0), min(shot.height, y1)):
                row = y * shot.stride
                for x in range(max(0, x0), min(shot.width, x1)):
                    darkest = min(darkest, shot.samples[row + x * shot.n])
            if darkest > 170:
                blank += 1
        doc.close()
        check(f"a photo {label}: every box sits on ink of the rendered page",
              bool(found) and blank == 0,
              f"{blank} of {len(found)} landed on blank paper")

        client.delete(f"/documents/{entry['id']}")

    # --- the box has to cover the WHOLE amount ------------------------------
    # Read back what survives the redaction, with OCR, which is the only way
    # to ask the question of a scan. A box that clipped the "Rs." or the last
    # digits would leave them legible here.
    res = client.post(
        "/documents/",
        data={"document_type": "Work Order", "category": "Tendering",
              "client_name": "Photo Coverage", "submitted_by": "vansh"},
        files={"document_file": ("photo.pdf",
                                 io.BytesIO(_photo_pdf(photo, pymupdf.Rect(60, 60, 535, 782))),
                                 "application/pdf")},
    )
    if check("POST a photo for the coverage check -> 201", res.status_code == 201, res.text):
        entry = res.json()
        found = client.get(
            f"/documents/{entry['id']}/redaction/suggestions").json()["detections"]
        areas = [{"page": d["page"], "x": d["x"], "y": d["y"],
                  "width": d["width"], "height": d["height"]} for d in found]
        res = client.post(f"/documents/{entry['id']}/redacted-copy", json={"areas": areas})
        if check("the photo's suggestions POST to /redacted-copy -> 200",
                 res.status_code == 200, res.text):
            copy = pymupdf.open(stream=res.content, filetype="pdf")
            pymupdf_module = detection.redaction._pymupdf()
            lines = detection._lines_from_ocr(copy, copy[0], pymupdf_module) or []
            readable = " ".join(line.text for line in lines)
            copy.close()
            check("OCR of the redacted photo cannot read the amount back",
                  PHOTO_AMOUNT not in readable, repr(readable[:200]))
            check("OCR of the redacted photo cannot read the words back",
                  PHOTO_WORDS not in readable, repr(readable[:200]))
            check("the box covered the Rs. as well as the digits",
                  "Rs." not in readable and "Rs " not in readable, repr(readable[:200]))
            check("the rest of the photographed page is still legible",
                  "VIRTUAL" in readable or "GALAXY" in readable, repr(readable[:200]))
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

        # --- automatic detection of sensitive figures ----------------------
        run_detection_suite(client, collection, plain["id"])

        # --- detection across every supported format ------------------------
        run_ocr_suite(client, collection)

        # --- amounts in words, and figures labelled by a column -------------
        run_written_amount_suite(client, collection)

        # --- a photograph embedded in a PDF ---------------------------------
        run_photo_suite(client, collection)

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
