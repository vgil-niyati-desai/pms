"""Proposal library: parsing, API, permissions and the bulk import.

Everything is a fixture: generated .docx files, mongomock, and a temporary
uploads folder. The real uploads folder is fingerprinted before and after.
"""

import gc
import io
import os
import tempfile
import unittest
import zipfile

import mongomock
from fastapi.testclient import TestClient

import import_proposal_library as importer
from app import auth, docx_ocr, docx_text, main, mongodb, proposal_library, storage


def make_docx(paragraphs, images=0) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
        for i in range(images):
            z.writestr(f"word/media/image{i}.png", b"png")
    return buffer.getvalue()


def fingerprint():
    folder = os.path.join(os.path.dirname(os.path.abspath(storage.__file__)), "uploads")
    if not os.path.isdir(folder):
        return []
    return sorted((n, os.path.getsize(os.path.join(folder, n))) for n in os.listdir(folder))


class ParsingTests(unittest.TestCase):
    def test_section_folder_names(self):
        cases = {
            "3.1.1 General Application Features": ("3.1.1", "General Application Features"),
            "3.3.10. INTEGRATION": ("3.3.10", "INTEGRATION"),
            "5.1.6Installation": ("5.1.6", "Installation"),
            "5.1.1&5.1.2,5.1.3 Project Plan": ("5.1.1&5.1.2,5.1.3", "Project Plan"),
            "3.7.1  Employee Registration": ("3.7.1", "Employee Registration"),
            "D) INTEREST CALCULATION": (None, "D) INTEREST CALCULATION"),
        }
        for name, expected in cases.items():
            self.assertEqual(docx_text.parse_section_folder(name), expected, name)

    def test_point_numbers(self):
        self.assertEqual(docx_text.parse_point_number("Tests Point No. 7.docx"), 7)
        self.assertEqual(docx_text.parse_point_number("Payroll Processing18.docx"), 18)
        self.assertIsNone(docx_text.parse_point_number("Summary.docx"))
        self.assertEqual(docx_text.parse_point_number("BULLET PAYMENT - 12 REHABILITATION & RESTRUCTURING.docx"), 12)
        self.assertIsNone(docx_text.parse_point_number("Plan 2 of 3 phases.docx"))  # two numbers: not guessed

    def test_sections_sort_numerically(self):
        keys = {n: proposal_library.sort_key(n) for n in ("3.10.1", "3.2", "3.1.1", "5.1.1&5.1.2")}
        self.assertEqual(sorted(keys, key=keys.get), ["3.1.1", "3.2", "3.10.1", "5.1.1&5.1.2"])

    def test_extract_docx_and_rejects_non_docx(self):
        result = docx_text.extract_docx(make_docx(["Requirement &amp; scope", "Answer"], images=2))
        self.assertEqual(result["paragraphs"], ["Requirement & scope", "Answer"])
        self.assertEqual(result["images"], 2)
        with self.assertRaises(docx_text.UnreadableDocument):
            docx_text.extract_docx(b"\x11Surabhi not a zip")

    def test_rtf_is_parsed_structurally(self):
        rtf = (b"{\\rtf1\\ansi\\ansicpg1252\\uc1{\\fonttbl{\\f0 Times;}}{\\colortbl;\\red0\\green0\\blue0;}"
               b"{\\*\\generator Word;}{\\stylesheet{\\s0 Normal;}}{\\header \\pard Header text\\par}"
               b"\\pard First line:\\par {\\listtext\\f1\\'b7\\tab}Item one\\par "
               b"Caf\\'e9 \\u8211? and \\u8364\\'80 done\\par {\\*\\shppict{\\pict\\jpegblip ffd8}}"
               b"{\\nonshppict{\\pict\\jpegblip ffd8}}}")
        record = proposal_library.build_record(rtf, "x 1.rtf", section_number="3.6.6")
        self.assertEqual(record["extraction_method"], "rtf")
        self.assertEqual(record["image_count"], 1)
        self.assertEqual(record["requirement_text"], "First line:")  # list item has no bullet glyph here
        self.assertEqual(record["response_text"], "Item one\n\nCafé – and € done")
        self.assertNotIn("Header", record["response_text"])
        with self.assertRaises(docx_text.UnreadableDocument):
            docx_text.extract_rtf(b"plain text")


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uploads = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.prev_dir, cls.before = storage.UPLOAD_DIR, fingerprint()
        storage.UPLOAD_DIR = cls.uploads.name
        cls.prev = (mongodb._client, mongodb.ping, auth.COOKIE_SECURE)
        mongodb._client = mongomock.MongoClient(tz_aware=True)
        mongodb.ping = lambda: (True, None)
        auth.COOKIE_SECURE = False
        cls.db = mongodb.get_database()
        cls.items = mongodb.get_proposal_library_collection()
        cls.users = cls.db[mongodb.MONGO_USERS_COLLECTION_NAME]
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        main.app.dependency_overrides.clear()
        mongodb._client, mongodb.ping, auth.COOKIE_SECURE = cls.prev
        storage.UPLOAD_DIR = cls.prev_dir
        assert fingerprint() == cls.before, "real uploads changed"
        gc.collect()
        cls.uploads.cleanup()

    def setUp(self):
        self.client.cookies.clear()
        for name in (mongodb.MONGO_USERS_COLLECTION_NAME, mongodb.MONGO_SESSIONS_COLLECTION_NAME,
                     mongodb.MONGO_LOGIN_THROTTLE_COLLECTION_NAME, mongodb.MONGO_PROPOSAL_LIBRARY_COLLECTION_NAME):
            self.db[name].delete_many({})
        for stale in os.listdir(self.uploads.name):
            os.remove(os.path.join(self.uploads.name, stale))
        self.csrf = self.login("admin@example.test", "Admin")

    def login(self, username, role):
        auth.create_user(username, "correct horse battery staple", self.users, role=role)
        self.client.cookies.clear()
        r = self.client.post("/auth/login", json={"username": username, "password": "correct horse battery staple"},
                             headers={"Origin": "http://localhost:5173"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["csrf_token"]

    def add(self, section, title, paragraphs, point=None, name="w.docx", **fields):
        data = {"section_number": section, "section_title": title, **fields}
        if point is not None:
            data["point_number"] = point
        return self.client.post("/proposal-library/", data=data, headers={"X-CSRF-Token": self.csrf},
                                files={"document_file": (name, io.BytesIO(make_docx(paragraphs)), "application/octet-stream")})

    def test_upload_reads_text_and_serves_the_original(self):
        r = self.add("3.1.1", "General", ["The system is web based", "VGIL answer one", "More detail"], point=1)
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["requirement_text"], "The system is web based")
        self.assertEqual(body["response_text"], "VGIL answer one\n\nMore detail")
        self.assertEqual(body["file_type"], "docx")
        self.assertNotIn("needs_review", body)
        got = self.client.get(f"/proposal-library/{body['id']}/file")
        self.assertEqual(got.status_code, 200)
        self.assertIn("attachment", got.headers["content-disposition"])
        self.assertEqual(zipfile.ZipFile(io.BytesIO(got.content)).namelist()[0], "word/document.xml")

    def test_duplicates_wrong_types_and_unreadable_files_leave_nothing_behind(self):
        self.assertEqual(self.add("3.1", "A", ["x", "y"]).status_code, 201)
        self.assertEqual(self.add("3.2", "B", ["x", "y"], name="other.docx").status_code, 409)
        bad = self.client.post("/proposal-library/", data={"section_number": "3.1"}, headers={"X-CSRF-Token": self.csrf},
                               files={"document_file": ("x.pdf", io.BytesIO(b"%PDF"), "application/pdf")})
        self.assertEqual(bad.status_code, 400)
        broken = self.client.post("/proposal-library/", data={"section_number": "3.1"}, headers={"X-CSRF-Token": self.csrf},
                                  files={"document_file": ("x.docx", io.BytesIO(b"not a zip"), "application/octet-stream")})
        self.assertEqual(broken.status_code, 422)
        self.assertEqual(self.items.count_documents({}), 1)
        self.assertEqual(len(os.listdir(self.uploads.name)), 1)

    def test_list_sections_search_filters_and_sort(self):
        self.add("3.10.1", "Capture", ["Scan documents", "We scan"], point=2)
        self.add("3.2", "Clients", ["Register client", "Done quickly"], point=1)
        self.add("5.4.1", "Training", ["Train staff", "Classroom training"], point=1)
        self.add("3.2", "Clients", ["Edit client", "Also done"], point=2)
        rows = lambda q: [(i["section_number"], i["point_number"]) for i in self.client.get("/proposal-library/?" + q).json()["items"]]
        self.assertEqual(rows(""), [("3.2", 1), ("3.2", 2), ("3.10.1", 2), ("5.4.1", 1)])
        self.assertEqual(rows("part=3"), [("3.2", 1), ("3.2", 2), ("3.10.1", 2)])
        self.assertEqual(rows("section=3.2"), [("3.2", 1), ("3.2", 2)])
        self.assertEqual(rows("q=classroom"), [("5.4.1", 1)])
        self.assertEqual(rows("sort=-section")[0], ("5.4.1", 1))
        sections = self.client.get("/proposal-library/sections").json()
        self.assertEqual([(s["section_number"], s["count"]) for s in sections], [("3.2", 2), ("3.10.1", 1), ("5.4.1", 1)])
        self.assertNotIn("response_text", self.client.get("/proposal-library/").json()["items"][0])

    def test_edit_and_delete_removes_file(self):
        created = self.add("3.1", "A", ["Req", "Resp"]).json()
        r = self.client.put(f"/proposal-library/{created['id']}", headers={"X-CSRF-Token": self.csrf},
                            json={"section_number": "3.1", "section_title": "A", "response_text": "Better", "point_number": 4})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["response_text"], r.json()["point_number"]), ("Better", 4))
        self.assertEqual(self.client.delete(f"/proposal-library/{created['id']}", headers={"X-CSRF-Token": self.csrf}).status_code, 204)
        self.assertEqual(os.listdir(self.uploads.name), [])

    def test_permissions(self):
        self.add("3.1", "A", ["Req", "Resp"])
        csrf = self.login("hod@example.test", "HOD")
        self.assertEqual(self.client.get("/proposal-library/").status_code, 200)
        self.csrf = csrf
        self.assertEqual(self.add("3.3", "C", ["R3", "S3"]).status_code, 201)  # HOD may add
        self.login("user@example.test", "Normal User")
        for method, url in (("get", "/proposal-library/"), ("get", "/proposal-library/sections")):
            self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/proposal-library/").status_code, 401)

    def test_import_is_idempotent_and_reports_unreadable_files(self):
        with tempfile.TemporaryDirectory() as src:
            def put(rel, data):
                path = os.path.join(src, *rel.split("/")); os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").write(data)
            put("3.3.7 Loan account management/a) Creation/Creation 1.docx", make_docx(["R1", "A1"]))
            put("3.3.7 Loan account management/a) Creation/Creation 2.docx", make_docx(["R2", "A2"]))
            put("5.1.6Installation/Installation 1.docx", make_docx(["R3", "A3"], images=1))
            put("5.1.6Installation/BUDGET 1.docx", b"\x11Surabhi")
            put("5.1.6Installation/notes.txt", b"ignored")
            put("9.9 Dupe/Copy.docx", make_docx(["R1", "A1"]))  # same bytes as Creation 1
            results, ignored = importer.import_folder(src, self.items, dry_run=True)
            self.assertEqual(self.items.count_documents({}), 0)  # dry run writes nothing
            results, ignored = importer.import_folder(src, self.items)
            counts = lambda r: {s: sum(1 for x in r if x["status"] == s) for s in {x["status"] for x in r}}
            self.assertEqual(counts(results), {"inserted": 3, "failed": 1, "skipped": 1})
            self.assertEqual(ignored, 1)
            self.assertEqual(len(os.listdir(self.uploads.name)), 3)
            row = self.items.find_one({"title": "R1"})
            self.assertEqual((row["section_number"], row["subsection"], row["point_number"]), ("3.3.7", "a) Creation", 1))
            again, _ = importer.import_folder(src, self.items)
            self.assertEqual(counts(again), {"skipped": 4, "failed": 1})
            self.assertEqual(self.items.count_documents({}), 3)
            self.assertEqual(len(os.listdir(self.uploads.name)), 3)

    def test_import_stores_ocr_apart_and_skipped_records_are_untouched(self):
        shot = make_docx_with_pictures(["Req one", "Ans one"], [("s.png", screenshot(SHOT_A))])
        logo = make_docx_with_pictures(["Req two", "Ans two"], [("logo.png", plain_png(551, 166))])
        with tempfile.TemporaryDirectory() as src:
            for rel, data in (("3.1 A/A 1.docx", shot), ("3.1 A/A 2.docx", logo)):
                path = os.path.join(src, *rel.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").write(data)
            results, _ = importer.import_folder(src, self.items)
            self.assertEqual([r["status"] for r in results], ["inserted", "inserted"])
            one, two = self.items.find_one({"title": "Req one"}), self.items.find_one({"title": "Req two"})
            self.assertIn("customer id: 100234", squash(one["image_text"]))
            self.assertEqual((one["requirement_text"], one["response_text"]), ("Req one", "Ans one"))
            self.assertIsNone(two["image_text"])
            self.assertEqual(two["image_ocr"]["details"][0]["status"], "too_small")
            before = self.items.find_one({"_id": one["_id"]})
            again, _ = importer.import_folder(src, self.items)
            self.assertEqual({r["status"] for r in again}, {"skipped"})
            self.assertEqual(self.items.find_one({"_id": one["_id"]}), before)  # untouched
            self.assertEqual(self.items.count_documents({}), 2)
            # the API shows it on the detail view, and search finds screenshot text
            detail = self.client.get("/proposal-library/%s" % one["_id"]).json()
            self.assertIn("100234", detail["image_text"])
            self.assertEqual(detail["image_ocr"]["images_read"], 1)
            hits = self.client.get("/proposal-library/", params={"q": "LILONGWE"}).json()
            self.assertEqual([h["title"] for h in hits["items"]], ["Req one"])
            self.assertNotIn("image_text", hits["items"][0])  # list rows stay light

    RICH = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Title</w:t></w:r></w:p>'
        '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Bold</w:t></w:r><w:r><w:rPr><w:i/><w:u w:val="single"/></w:rPr>'
        '<w:t xml:space="preserve"> slanted &lt;script&gt;alert(1)&lt;/script&gt;</w:t></w:r><w:r><w:br/></w:r><w:r><w:t>next</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>one</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>two</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>step</w:t></w:r></w:p>'
        '<w:p><w:r><w:t></w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '</w:body></w:document>')
    NUMBERING = (
        '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
        '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>')

    def rich_docx(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("word/document.xml", self.RICH)
            z.writestr("word/numbering.xml", self.NUMBERING)
        return buffer.getvalue()

    def test_viewer_renders_formatting_lists_tables_and_escapes_text(self):
        from app import docx_html
        out = docx_html.render(self.rich_docx(), "docx")
        self.assertEqual(out["fidelity"], "formatted")
        html = out["html"]
        self.assertIn("<h4>Title</h4>", html)
        self.assertIn("<strong>Bold</strong>", html)
        self.assertIn("<u><em> slanted &lt;script&gt;alert(1)&lt;/script&gt;</em></u>", html)
        self.assertNotIn("<script", html)
        self.assertIn("<br>next", html)
        self.assertIn("<ul><li>one</li><li>two</li></ul><ol><li>step</li></ol>", html)
        self.assertIn("<table><tr><td><p>A</p></td><td><p>B</p></td></tr></table>", html)
        with self.assertRaises(docx_text.UnreadableDocument):
            docx_html.render(b"\x11not a zip", "docx")

    def test_preview_endpoint_for_docx_rtf_and_missing_file(self):
        post = lambda name, data: self.client.post(
            "/proposal-library/", data={"section_number": "3.1", "section_title": "A"}, headers={"X-CSRF-Token": self.csrf},
            files={"document_file": (name, io.BytesIO(data), "application/octet-stream")})
        docx_id = post("rich.docx", self.rich_docx()).json()["id"]
        rtf_id = post("plain.rtf", b"{\\rtf1\\ansi First\\par Second <b>\\par}").json()["id"]
        r = self.client.get("/proposal-library/%s/preview" % docx_id)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["fidelity"], "formatted")
        self.assertIn("<strong>Bold</strong>", r.json()["html"])
        r = self.client.get("/proposal-library/%s/preview" % rtf_id)
        self.assertEqual(r.json(), {"html": "<p>First</p><p>Second &lt;b&gt;</p>", "fidelity": "text"})
        self.items.update_one({"file_type": "docx"}, {"$set": {"stored_file_name": "missing.docx"}})
        self.assertEqual(self.client.get("/proposal-library/%s/preview" % docx_id).status_code, 404)
        self.assertEqual(self.client.get("/proposal-library/%s/preview" % "0" * 24).status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/proposal-library/%s/preview" % rtf_id).status_code, 401)

    def test_viewer_is_closed_to_normal_users(self):
        item_id = self.add("3.1", "A", ["R", "A"]).json()["id"]
        self.login("user@example.test", "Normal User")
        self.assertEqual(self.client.get("/proposal-library/%s/preview" % item_id).status_code, 403)

    def test_line_breaks_with_attributes_keep_words_apart(self):
        xml = ('<w:document xmlns:w="x"><w:body><w:p><w:r><w:t>Heading</w:t></w:r><w:r><w:br w:type="textWrapping"/></w:r>'
               '<w:r><w:t xml:space="preserve">Body</w:t></w:r><w:r><w:tab /></w:r><w:r><w:t>End</w:t></w:r></w:p></w:body></w:document>')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("word/document.xml", xml)
        self.assertEqual(docx_text.extract_docx(buffer.getvalue())["paragraphs"], ["Heading Body End"])

    def test_bullets_under_the_requirement_stay_with_it(self):
        record = proposal_library.build_record(
            make_docx(["The system captures:", "• Name", "• Phone", "VGIL answer", "• a bullet inside the answer"]), "Contacts 5.docx")
        self.assertEqual(record["requirement_text"], "The system captures:\n• Name\n• Phone")
        self.assertEqual(record["response_text"], "VGIL answer\n\n• a bullet inside the answer")
        self.assertEqual(record["title"], "The system captures:")

    def test_limit_takes_the_first_files_in_order_and_reports_gaps(self):
        with tempfile.TemporaryDirectory() as src:
            for rel, paragraphs in (("3.1 A/A 2.docx", ["R2", "A2"]), ("3.1 A/A 1.docx", ["R1", "A1"]),
                                    ("3.2 B/B 1.docx", ["R3", "A3"]), ("3.2 B/Summary.docx", ["R4"])):
                path = os.path.join(src, *rel.split("/")); os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").write(make_docx(paragraphs))
            results, _ = importer.import_folder(src, self.items, limit=3)
            self.assertEqual([r["file"] for r in results], ["3.1 A/A 1.docx", "3.1 A/A 2.docx", "3.2 B/B 1.docx"])
            self.assertEqual(self.items.count_documents({}), 3)
            rest, _ = importer.import_folder(src, self.items, dry_run=True)
            summary = {r["file"]: r for r in rest}
            self.assertEqual(summary["3.2 B/Summary.docx"]["missing"], ["no point_number", "no response_text"])
            self.assertEqual(summary["3.1 A/A 1.docx"]["status"], "skipped")


def screenshot(lines, size=(1000, 420)) -> bytes:
    """A PNG that looks like a screenshot: dark text on white (drawn with
    PyMuPDF, which the app already requires, so tests need no extra package)."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    for i, line in enumerate(lines):
        page.insert_text((30, 60 + i * 70), line, fontname="helv", fontsize=40)
    png = page.get_pixmap(dpi=72).tobytes("png")
    doc.close()
    return png


def plain_png(width, height) -> bytes:
    import pymupdf
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.clear_with(255)
    return pixmap.tobytes("png")


def make_docx_with_pictures(paragraphs, pictures, zip_order=None) -> bytes:
    """pictures: [(name, bytes)] in the order the body shows them. zip_order
    reorders how they are stored, to prove reading order is the body's."""
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    rels = ""
    for i, (name, _) in enumerate(pictures, start=1):
        rels += f'<Relationship Id="rId{i}" Type="image" Target="media/{name}"/>'
        body += f'<w:p><w:r><w:drawing><a:blip r:embed="rId{i}"/></w:drawing></w:r></w:p>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="x" xmlns:a="a" xmlns:r="r"><w:body>%s</w:body></w:document>' % body)
        z.writestr("word/_rels/document.xml.rels", "<Relationships>%s</Relationships>" % rels)
        for name, data in (zip_order(pictures) if zip_order else pictures):
            z.writestr("word/media/" + name, data)
    return buffer.getvalue()


SHOT_A = ["Create Loan Account", "Customer ID: 100234", "Branch: LILONGWE"]
SHOT_B = ["Repayment Schedule", "Instalment 12 due 2026-03-15", "Amount 4,500.00"]


def squash(text):
    return " ".join((text or "").split()).lower()


class ImageOcrTests(unittest.TestCase):
    def test_reads_screenshot_text_in_document_order_and_labels_it(self):
        pictures = [("image9.png", screenshot(SHOT_A)), ("image1.png", screenshot(SHOT_B))]
        data = make_docx_with_pictures(["Requirement", "Answer"], pictures, zip_order=lambda p: p[::-1])
        found = docx_ocr.extract_image_text(data)
        text = squash(found["text"])
        for expected in ("create loan account", "customer id: 100234", "branch: lilongwe",
                         "repayment schedule", "instalment 12 due 2026-03-15", "amount 4,500.00"):
            self.assertIn(expected, text)
        self.assertLess(text.index("create loan account"), text.index("repayment schedule"))
        self.assertIn("[Image 1 of 2]", found["text"])
        self.assertIn("[Image 2 of 2]", found["text"])
        self.assertEqual(found["ocr"]["images_read"], 2)
        self.assertEqual(found["ocr"]["images_with_text"], 2)
        self.assertNotIn("loan", str(found["ocr"]["details"]).lower())  # the report carries no text

    def test_logos_spacers_duplicates_and_bad_pictures_add_no_text(self):
        same = screenshot(SHOT_A)
        pictures = [("logo.png", plain_png(551, 166)), ("dot.png", plain_png(1, 1)),
                    ("a.png", same), ("a_again.png", same), ("broken.png", b"not a picture")]
        found = docx_ocr.extract_image_text(make_docx_with_pictures(["R", "A"], pictures))
        status = {d["file"]: d["status"] for d in found["ocr"]["details"]}
        self.assertEqual(status, {"logo.png": "too_small", "dot.png": "too_small", "a.png": "read",
                                  "a_again.png": "duplicate", "broken.png": "unreadable_image"})
        self.assertEqual(squash(found["text"]).count("create loan account"), 1)  # not duplicated
        self.assertEqual(found["ocr"]["details"][3]["same_as"], 3)

    def test_no_pictures_and_bad_files_never_raise(self):
        none = docx_ocr.extract_image_text(make_docx(["R", "A"]))
        self.assertEqual(none["ocr"]["status"], "no_images")
        self.assertIsNone(none["text"])
        self.assertEqual(docx_ocr.extract_image_text(b"\x11Surabhi")["ocr"]["status"], "unreadable_document")

    def test_picture_cap_is_reported_not_silent(self):
        pictures = [("p%d.png" % i, screenshot(["Page number %d" % i])) for i in range(docx_ocr.MAX_IMAGES + 2)]
        found = docx_ocr.extract_image_text(make_docx_with_pictures(["R", "A"], pictures))
        self.assertEqual(found["ocr"]["images_read"], docx_ocr.MAX_IMAGES)
        self.assertEqual([d["status"] for d in found["ocr"]["details"]].count("not_read_limit"), 2)

    def test_record_keeps_text_split_and_word_count_unchanged(self):
        data = make_docx_with_pictures(["The requirement.", "The answer, in two sentences. Here."],
                                       [("s.png", screenshot(SHOT_A))])
        plain = proposal_library.build_record(data, "X 1.docx", section_number="3.1", section_title="T")
        with_ocr = proposal_library.build_record(data, "X 1.docx", section_number="3.1", section_title="T", ocr=True)
        self.assertNotIn("image_text", plain)  # off by default: the record is exactly as before
        self.assertNotIn("image_ocr", plain)
        for key in plain:
            self.assertEqual(plain[key], with_ocr[key], key)  # nothing else differs
        self.assertEqual(with_ocr["requirement_text"], "The requirement.")
        self.assertEqual(with_ocr["response_text"], "The answer, in two sentences. Here.")
        self.assertEqual(with_ocr["word_count"], 8)  # OCR words are not counted
        self.assertIn("create loan account", squash(with_ocr["image_text"]))
        self.assertNotIn("create loan account", squash(with_ocr["response_text"]))

    def test_rtf_gets_no_ocr_fields(self):
        record = proposal_library.build_record(b"{\\rtf1\\ansi Requirement.\\par Answer.\\par}", "Y 1.rtf", ocr=True)
        self.assertNotIn("image_text", record)


if __name__ == "__main__":
    unittest.main()
