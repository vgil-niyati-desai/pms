"""Who a tender's documents belong to, and who may change that.

A tender holds documents in two different ways, and the code treats them
differently on purpose:

* Attached documents (`tenders.document_ids`, mirrored by
  `documents.tender_id`). The first tender to attach a free document claims
  it; a second tender may still list it but does not take the pointer; and a
  document a project or an employee holds is listed without being claimed.
* Cost-item receipts and submitted-certificate scans (`document_id` on the
  nested record). These are attachments of the record, not of the tender:
  they never write `tender_id`, and any number of records, in any number of
  tenders, may point at one file. Deleting such a file is what is guarded
  (the usage check), not pointing at it.

test_nested_document_ids.py covers whether a pointer resolves at all. This
covers the ownership around it, and that only an Admin can change any of it.

Runs against mongomock with UPLOAD_DIR in a temporary directory, as the other
document tests do, and fingerprints the real uploads folder.
"""

import gc
import io
import tempfile
import unittest
from datetime import datetime, timezone

import mongomock
import pymupdf
from bson import ObjectId
from fastapi.testclient import TestClient

from app import auth, main, mongodb, storage

try:  # run as tests.test_x from backend/, or discovered with tests/ as the top
    from tests.test_document_deletion import _fingerprint_real_uploads
    from tests.test_nested_document_ids import _PositionalProjection
except ImportError:
    from test_document_deletion import _fingerprint_real_uploads
    from test_nested_document_ids import _PositionalProjection



def _pdf(text: str) -> bytes:
    """A readable PDF whose bytes depend on `text`: an identical file is
    refused as a duplicate, and these tests need several different ones."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


PASSWORD = "correct horse battery staple"
ORIGIN = {"Origin": "http://localhost:5173"}


class TenderDocumentOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uploads = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.previous_upload_dir = storage.UPLOAD_DIR
        cls.real_uploads_before = _fingerprint_real_uploads()
        storage.UPLOAD_DIR = cls.uploads.name

        cls.previous_client = mongodb._client
        cls.previous_ping = mongodb.ping
        cls.previous_cookie_secure = auth.COOKIE_SECURE
        mongodb._client = mongomock.MongoClient(tz_aware=True)
        mongodb.ping = lambda: (True, None)
        auth.COOKIE_SECURE = False

        cls.database = mongodb.get_database()
        cls.documents = cls.database[mongodb.MONGO_COLLECTION_NAME]
        cls.users = cls.database[mongodb.MONGO_USERS_COLLECTION_NAME]
        cls.tenders = _PositionalProjection(mongodb.get_tender_records())
        cls.employees = _PositionalProjection(mongodb.get_employee_records())
        main.app.dependency_overrides[mongodb.get_tender_records] = lambda: cls.tenders
        main.app.dependency_overrides[mongodb.get_employee_records] = lambda: cls.employees
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        main.app.dependency_overrides.clear()
        mongodb._client = cls.previous_client
        mongodb.ping = cls.previous_ping
        auth.COOKIE_SECURE = cls.previous_cookie_secure
        storage.UPLOAD_DIR = cls.previous_upload_dir
        assert _fingerprint_real_uploads() == cls.real_uploads_before, (
            "the real uploads folder changed while these tests ran"
        )
        gc.collect()
        cls.uploads.cleanup()

    def setUp(self):
        self.client.cookies.clear()
        for collection in (self.users, self.documents, self.tenders, self.employees):
            collection.delete_many({})
        self.database[mongodb.MONGO_SESSIONS_COLLECTION_NAME].delete_many({})
        self.database[mongodb.MONGO_LOGIN_THROTTLE_COLLECTION_NAME].delete_many({})
        auth.create_user("admin@example.test", PASSWORD, self.users)
        self.csrf = self.login("admin@example.test")

    def login(self, username, role=None):
        if role:
            auth.create_user(username, PASSWORD, self.users, role=role)
        self.client.cookies.clear()
        response = self.client.post(
            "/auth/login", json={"username": username, "password": PASSWORD}, headers=ORIGIN
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf_token"]

    # --- fixtures ---------------------------------------------------------

    def upload(self, name="receipt.pdf", text="CONTRACT VALUE 1,250,000"):
        response = self.client.post(
            "/documents/",
            data={"document_type": "Cost Sheet", "submitted_by": "tests"},
            files={"document_file": (name, io.BytesIO(_pdf(text)), "application/pdf")},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def add_tender(self, title="Metro bid"):
        now = datetime.now(timezone.utc)
        return str(
            self.tenders.insert_one(
                {"title": title, "created_at": now, "updated_at": now}
            ).inserted_id
        )

    def post(self, path, json=None, csrf=None):
        return self.client.post(
            path, json=json or {}, headers={"X-CSRF-Token": csrf or self.csrf}
        )

    def document(self, document_id):
        return self.documents.find_one({"_id": ObjectId(document_id)})

    def tender(self, tender_id):
        return self.tenders.find_one({"_id": ObjectId(tender_id)})

    def add_cost_item(self, tender_id, document_id):
        return self.post(
            f"/tenders/{tender_id}/cost-items",
            {"cost_type": "EMD", "amount": "10", "document_id": document_id},
        )

    def add_certificate(self, tender_id, document_id):
        return self.post(
            f"/tenders/{tender_id}/certificates",
            {"name": "ISO 9001", "document_id": document_id},
        )

    # --- nested records do not claim the document -------------------------

    def test_a_cost_receipt_and_a_certificate_scan_do_not_claim_their_document(self):
        tender_id = self.add_tender()
        receipt, scan = self.upload("receipt.pdf", "RECEIPT"), self.upload("scan.pdf", "SCAN")
        self.assertEqual(self.add_cost_item(tender_id, receipt).status_code, 201)
        self.assertEqual(self.add_certificate(tender_id, scan).status_code, 201)
        for document_id in (receipt, scan):
            self.assertIsNone(self.document(document_id).get("tender_id"))
        self.assertEqual(self.tender(tender_id).get("document_ids") or [], [])

    def test_one_document_may_back_records_in_two_tenders_and_both_are_reported(self):
        first, second = self.add_tender("First"), self.add_tender("Second")
        document_id = self.upload()
        self.assertEqual(self.add_cost_item(first, document_id).status_code, 201)
        self.assertEqual(self.add_certificate(second, document_id).status_code, 201)

        uses = self.client.get(f"/documents/{document_id}/usage").json()
        by_tender = {use["id"]: use["roles"] for use in uses if use["kind"] == "tender"}
        self.assertEqual(by_tender, {first: ["cost receipt"], second: ["submitted certificate"]})

    def test_a_document_shared_by_two_tenders_cannot_be_deleted_from_one_of_them(self):
        first, second = self.add_tender("First"), self.add_tender("Second")
        document_id = self.upload()
        self.add_cost_item(first, document_id)
        self.add_cost_item(second, document_id)

        refused = self.client.delete(
            f"/documents/{document_id}?from_tender={first}",
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIsNotNone(self.document(document_id))

    def test_saving_the_tender_form_cannot_touch_documents_or_nested_records(self):
        tender_id = self.add_tender()
        document_id = self.upload()
        self.add_cost_item(tender_id, document_id)
        self.post(f"/tenders/{tender_id}/documents/{document_id}")

        response = self.client.put(
            f"/tenders/{tender_id}",
            json={
                "title": "Renamed",
                "document_ids": [str(ObjectId())],
                "cost_items": [],
                "certificates": [{"id": "x", "document_id": str(ObjectId())}],
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        stored = self.tender(tender_id)
        self.assertEqual(stored["title"], "Renamed")
        self.assertEqual(stored["document_ids"], [document_id])
        self.assertEqual(len(stored["cost_items"]), 1)
        self.assertEqual(stored.get("certificates") or [], [])

    def test_a_record_for_a_tender_that_is_not_there_changes_nothing(self):
        document_id = self.upload()
        missing = str(ObjectId())
        for response in (
            self.add_cost_item(missing, document_id),
            self.add_certificate(missing, document_id),
        ):
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json()["detail"], "Tender not found")
        self.assertIsNone(self.document(document_id).get("tender_id"))

    # --- attaching a document to a tender ---------------------------------

    def test_the_first_tender_to_attach_a_free_document_claims_it(self):
        tender_id = self.add_tender()
        document_id = self.upload()
        response = self.post(f"/tenders/{tender_id}/documents/{document_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["document_ids"], [document_id])
        self.assertEqual(self.document(document_id)["tender_id"], tender_id)

    def test_a_second_tender_lists_the_document_without_taking_the_claim(self):
        first, second = self.add_tender("First"), self.add_tender("Second")
        document_id = self.upload()
        self.post(f"/tenders/{first}/documents/{document_id}")
        response = self.post(f"/tenders/{second}/documents/{document_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.tender(second)["document_ids"], [document_id])
        self.assertEqual(self.document(document_id)["tender_id"], first)

        listed = self.client.get(f"/tenders/{second}/documents").json()
        self.assertEqual([entry["id"] for entry in listed], [document_id])

    def test_attaching_twice_is_idempotent(self):
        tender_id = self.add_tender()
        document_id = self.upload()
        self.post(f"/tenders/{tender_id}/documents/{document_id}")
        self.post(f"/tenders/{tender_id}/documents/{document_id}")
        self.assertEqual(self.tender(tender_id)["document_ids"], [document_id])

    def test_a_document_a_project_or_an_employee_holds_is_listed_but_not_claimed(self):
        for owner_field in ("project_id", "employee_id"):
            with self.subTest(owner=owner_field):
                self.setUp()
                tender_id = self.add_tender()
                document_id = self.upload()
                owner = str(ObjectId())
                self.documents.update_one(
                    {"_id": ObjectId(document_id)}, {"$set": {owner_field: owner}}
                )
                response = self.post(f"/tenders/{tender_id}/documents/{document_id}")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.tender(tender_id)["document_ids"], [document_id])
                stored = self.document(document_id)
                self.assertIsNone(stored.get("tender_id"))
                self.assertEqual(stored[owner_field], owner)

    def test_attaching_a_missing_document_or_to_a_missing_tender_writes_nothing(self):
        tender_id = self.add_tender()
        document_id = self.upload()
        for path, detail in (
            (f"/tenders/{tender_id}/documents/{ObjectId()}", "Document not found"),
            (f"/tenders/{tender_id}/documents/not-an-id", "Document not found"),
            (f"/tenders/{ObjectId()}/documents/{document_id}", "Tender not found"),
        ):
            with self.subTest(path=path):
                response = self.post(path)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()["detail"], detail)
        self.assertEqual(self.tender(tender_id).get("document_ids") or [], [])
        self.assertIsNone(self.document(document_id).get("tender_id"))

    def test_detaching_clears_only_this_tenders_own_claim(self):
        first, second = self.add_tender("First"), self.add_tender("Second")
        document_id = self.upload()
        self.post(f"/tenders/{first}/documents/{document_id}")
        self.post(f"/tenders/{second}/documents/{document_id}")

        def unlink(tender_id):
            return self.client.delete(
                f"/tenders/{tender_id}/documents/{document_id}",
                headers={"X-CSRF-Token": self.csrf},
            )

        self.assertEqual(unlink(second).status_code, 200)
        self.assertEqual(self.tender(second)["document_ids"], [])
        self.assertEqual(self.document(document_id)["tender_id"], first)

        self.assertEqual(unlink(first).status_code, 200)
        self.assertIsNone(self.document(document_id)["tender_id"])
        self.assertIsNotNone(self.document(document_id))

    # --- only an Admin may change any of it -------------------------------

    def tender_with_records(self):
        tender_id = self.add_tender()
        document_id = self.upload()
        self.add_cost_item(tender_id, document_id)
        self.add_certificate(tender_id, document_id)
        stored = self.tender(tender_id)
        return tender_id, document_id, stored["cost_items"][0]["id"], stored["certificates"][0]["id"]

    def writes(self, tender_id, document_id, cost_item_id, certificate_id):
        item = {"cost_type": "EMD", "document_id": document_id}
        cert = {"name": "ISO 9001", "document_id": document_id}
        base = f"/tenders/{tender_id}"
        return (
            ("post", f"{base}/cost-items", item),
            ("put", f"{base}/cost-items/{cost_item_id}", item),
            ("delete", f"{base}/cost-items/{cost_item_id}", None),
            ("post", f"{base}/certificates", cert),
            ("put", f"{base}/certificates/{certificate_id}", cert),
            ("delete", f"{base}/certificates/{certificate_id}", None),
            ("post", f"{base}/documents/{document_id}", None),
            ("delete", f"{base}/documents/{document_id}", None),
        )

    def test_hod_and_normal_users_cannot_change_a_tenders_documents(self):
        tender_id, document_id, cost_item_id, certificate_id = self.tender_with_records()
        before = self.tender(tender_id)
        for username, role in (("hod@example.test", "HOD"), ("user@example.test", "Normal User")):
            csrf = self.login(username, role)
            for method, path, body in self.writes(tender_id, document_id, cost_item_id, certificate_id):
                with self.subTest(role=role, method=method, path=path):
                    kwargs = {"headers": {"X-CSRF-Token": csrf}}
                    if body is not None:
                        kwargs["json"] = body
                    self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 403)
        self.assertEqual(self.tender(tender_id), before)

    def test_an_unauthenticated_request_cannot_change_a_tenders_documents(self):
        tender_id, document_id, cost_item_id, certificate_id = self.tender_with_records()
        before = self.tender(tender_id)
        self.client.cookies.clear()
        for method, path, body in self.writes(tender_id, document_id, cost_item_id, certificate_id):
            with self.subTest(method=method, path=path):
                kwargs = {} if body is None else {"json": body}
                self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 401)
        self.assertEqual(self.tender(tender_id), before)

    def test_an_admin_request_without_the_csrf_token_is_refused(self):
        tender_id, document_id, cost_item_id, certificate_id = self.tender_with_records()
        before = self.tender(tender_id)
        for method, path, body in self.writes(tender_id, document_id, cost_item_id, certificate_id):
            with self.subTest(method=method, path=path):
                kwargs = {} if body is None else {"json": body}
                self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 403)
        self.assertEqual(self.tender(tender_id), before)

    def test_hod_and_normal_users_cannot_read_a_tenders_documents(self):
        tender_id = self.add_tender()
        for username, role in (("hod@example.test", "HOD"), ("user@example.test", "Normal User")):
            self.login(username, role)
            with self.subTest(role=role):
                self.assertEqual(self.client.get(f"/tenders/{tender_id}/documents").status_code, 403)
                self.assertEqual(self.client.get(f"/tenders/{tender_id}").status_code, 403)


if __name__ == "__main__":
    unittest.main()
