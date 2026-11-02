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
from app import auth, docx_text, main, mongodb, proposal_library, storage


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

    def test_sections_sort_numerically(self):
        keys = {n: proposal_library.sort_key(n) for n in ("3.10.1", "3.2", "3.1.1", "5.1.1&5.1.2")}
        self.assertEqual(sorted(keys, key=keys.get), ["3.1.1", "3.2", "3.10.1", "5.1.1&5.1.2"])

    def test_extract_docx_and_rejects_non_docx(self):
        result = docx_text.extract_docx(make_docx(["Requirement &amp; scope", "Answer"], images=2))
        self.assertEqual(result["paragraphs"], ["Requirement & scope", "Answer"])
        self.assertEqual(result["images"], 2)
        with self.assertRaises(docx_text.UnreadableDocument):
            docx_text.extract_docx(b"\x11Surabhi not a zip")

    def test_rtf_is_basic_and_flagged(self):
        rtf = b"{\\rtf1\\ansi{\\fonttbl x}\\pard First line\\par Second \\'e9 line\\par}"
        record = proposal_library.build_record(rtf, "x 1.rtf", section_number="3.6.6")
        self.assertEqual(record["extraction_method"], "rtf-basic")
        self.assertNotIn("needs_review", record)
        self.assertIn("Second", record["response_text"])


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


if __name__ == "__main__":
    unittest.main()
