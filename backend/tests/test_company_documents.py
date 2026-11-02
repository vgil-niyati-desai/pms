"""Company documents API: scope separation, status, permissions, duplicates.

mongomock + a temporary UPLOAD_DIR; the real uploads folder is fingerprinted.
"""

import gc
import io
import os
import tempfile
import unittest
from datetime import date, timedelta

import mongomock
import pymupdf
from fastapi.testclient import TestClient

from app import auth, main, mongodb, schemas, storage


_PDF_CACHE = {}


def _pdf(text="certificate") -> bytes:
    """Cached per text: pymupdf embeds a random document id, so two calls would
    otherwise give different bytes and a 'same file' test would be a lie."""
    if text not in _PDF_CACHE:
        _PDF_CACHE[text] = _make_pdf(text)
    return _PDF_CACHE[text]


def _make_pdf(text) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), text)
    data = doc.tobytes()
    doc.close()
    return data


def _fingerprint():
    folder = os.path.join(os.path.dirname(os.path.abspath(storage.__file__)), "uploads")
    if not os.path.isdir(folder):
        return []
    return sorted((n, os.path.getsize(os.path.join(folder, n))) for n in os.listdir(folder))


class CompanyDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uploads = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.prev_dir = storage.UPLOAD_DIR
        cls.before = _fingerprint()
        storage.UPLOAD_DIR = cls.uploads.name
        cls.prev_client, cls.prev_ping, cls.prev_secure = (
            mongodb._client,
            mongodb.ping,
            auth.COOKIE_SECURE,
        )
        mongodb._client = mongomock.MongoClient(tz_aware=True)
        mongodb.ping = lambda: (True, None)
        auth.COOKIE_SECURE = False
        cls.db = mongodb.get_database()
        cls.documents = cls.db[mongodb.MONGO_COLLECTION_NAME]
        cls.users = cls.db[mongodb.MONGO_USERS_COLLECTION_NAME]
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        main.app.dependency_overrides.clear()
        mongodb._client, mongodb.ping, auth.COOKIE_SECURE = (
            cls.prev_client,
            cls.prev_ping,
            cls.prev_secure,
        )
        storage.UPLOAD_DIR = cls.prev_dir
        assert _fingerprint() == cls.before, "real uploads changed"
        gc.collect()
        cls.uploads.cleanup()

    def setUp(self):
        self.client.cookies.clear()
        for name in (
            mongodb.MONGO_USERS_COLLECTION_NAME,
            mongodb.MONGO_SESSIONS_COLLECTION_NAME,
            mongodb.MONGO_COLLECTION_NAME,
            mongodb.MONGO_LOGIN_THROTTLE_COLLECTION_NAME,
        ):
            self.db[name].delete_many({})
        for stale in os.listdir(self.uploads.name):
            os.remove(os.path.join(self.uploads.name, stale))
        self.csrf = self.login("admin@example.test", "Admin")

    def login(self, username, role):
        auth.create_user(username, "correct horse battery staple", self.users, role=role)
        self.client.cookies.clear()
        r = self.client.post(
            "/auth/login",
            json={"username": username, "password": "correct horse battery staple"},
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["csrf_token"]

    def add(self, title="ISO 9001", text="a", **fields):
        return self.client.post(
            "/company-documents/",
            data={"title": title, "category": "Certifications", **fields},
            files={"document_file": (f"{title}.pdf", io.BytesIO(_pdf(text)), "application/pdf")},
            headers={"X-CSRF-Token": self.csrf},
        )

    def test_status_is_computed_from_expiry(self):
        today = date.today()
        cases = [
            (None, "unknown"),
            ((today - timedelta(days=1)).isoformat(), "expired"),
            ((today + timedelta(days=30)).isoformat(), "expiring_soon"),
            ((today + timedelta(days=400)).isoformat(), "valid"),
        ]
        for index, (expiry, expected) in enumerate(cases):
            fields = {"expiry_date": expiry} if expiry else {}
            r = self.add(title=f"d{index}", text=str(index), **fields)
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["status"], expected)
            listed = self.client.get(f"/company-documents/?status={expected}").json()
            self.assertEqual([i["title"] for i in listed["items"]], [f"d{index}"])
        facets = self.client.get("/company-documents/facets").json()
        self.assertEqual(
            {s["value"]: s["count"] for s in facets["statuses"]},
            {"valid": 1, "expiring_soon": 1, "expired": 1, "unknown": 1},
        )

    def test_scope_is_separate_from_other_documents(self):
        self.documents.insert_one(
            {"document_type": "CV", "category": "x", "file_hash": None, "created_at": schemas.datetime.utcnow()}
        )
        self.assertEqual(self.add().status_code, 201)
        self.assertEqual(self.client.get("/company-documents/").json()["total"], 1)
        self.assertEqual(self.client.get("/documents/").json()["total"], 2)
        cv_id = str(self.documents.find_one({"document_type": "CV"})["_id"])
        self.assertEqual(self.client.get(f"/company-documents/{cv_id}").status_code, 404)

    def test_duplicate_file_is_refused_and_file_serves(self):
        first = self.add(text="same")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(self.add(title="again", text="same").status_code, 409)
        self.assertEqual(self.documents.count_documents({}), 1)
        file = self.client.get(f"/documents/{first.json()['id']}/file?disposition=inline")
        self.assertEqual(file.status_code, 200)
        self.assertEqual(file.content[:4], b"%PDF")

    def test_validation_leaves_nothing_behind(self):
        self.assertEqual(self.add(issue_date="2026-05-01", expiry_date="2026-01-01").status_code, 422)
        self.assertEqual(self.add(issue_date="01/05/2026").status_code, 422)
        self.assertEqual(self.documents.count_documents({}), 0)
        self.assertEqual(os.listdir(self.uploads.name), [])

    def test_search_sort_filter(self):
        self.add(title="Alpha", text="1", issuing_organisation="Org A", expiry_date="2099-01-01")
        self.add(title="beta", text="2", issuing_organisation="Org B")

        def get(query):
            items = self.client.get("/company-documents/?" + query).json()["items"]
            return [i["title"] for i in items]

        self.assertEqual(get("q=org%20b"), ["beta"])
        self.assertEqual(get("sort=title"), ["Alpha", "beta"])
        self.assertEqual(get("sort=-title"), ["beta", "Alpha"])
        self.assertEqual(get("sort=-expiry_date"), ["Alpha", "beta"])  # blank last
        self.assertEqual(get("sort=expiry_date"), ["Alpha", "beta"])
        self.assertEqual(get("organisation=Org%20A"), ["Alpha"])

    def test_hod_can_edit_but_not_replace(self):
        created = self.add(title="OCR", text="3").json()
        self.documents.update_one({"title": "OCR"}, {"$set": {"metadata_source": "ocr"}})
        csrf = self.login("hod@example.test", "HOD")
        form = {"title": "OCR fixed", "category": "Certifications"}
        r = self.client.put(f"/company-documents/{created['id']}", data=form, headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("needs_review", r.json())
        self.assertNotIn("expiry_unverified", r.json())
        self.assertEqual(r.json()["metadata_source"], "manual")
        r = self.client.put(
            f"/company-documents/{created['id']}",
            data=form,
            headers={"X-CSRF-Token": csrf},
            files={"document_file": ("n.pdf", io.BytesIO(_pdf("new")), "application/pdf")},
        )
        self.assertEqual(r.status_code, 403)

    def test_admin_replace_swaps_file(self):
        created = self.add(text="old").json()
        old = self.documents.find_one({"title": "ISO 9001"})["stored_file_name"]
        r = self.client.put(
            f"/company-documents/{created['id']}",
            data={"title": "ISO 9001"},
            headers={"X-CSRF-Token": self.csrf},
            files={"document_file": ("n.pdf", io.BytesIO(_pdf("new")), "application/pdf")},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(os.path.exists(os.path.join(self.uploads.name, old)))

    def test_normal_user_has_no_access(self):
        csrf = self.login("u@example.test", "Normal User")
        self.assertEqual(self.client.get("/company-documents/").status_code, 403)
        r = self.client.post(
            "/company-documents/",
            data={"title": "x"},
            headers={"X-CSRF-Token": csrf},
            files={"document_file": ("n.pdf", io.BytesIO(_pdf()), "application/pdf")},
        )
        self.assertEqual(r.status_code, 403)

    def test_no_expiry_date_is_never_expired_or_expiring(self):
        """Status comes only from an entered expiry date: a document without one
        is "unknown", whatever else its notes say."""
        r = self.add(title="no date", text="n1", notes="Surv. audit date 25 Oct 2026").json()
        self.assertEqual(r["status"], "unknown")
        for status in ("expired", "expiring_soon", "valid"):
            self.assertEqual(self.client.get(f"/company-documents/?status={status}").json()["total"], 0)
        self.assertEqual(self.client.get("/company-documents/?status=unknown").json()["total"], 1)

    def test_unauthenticated(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/company-documents/").status_code, 401)


if __name__ == "__main__":
    unittest.main()
