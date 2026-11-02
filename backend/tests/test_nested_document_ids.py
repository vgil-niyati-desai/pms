"""The document a CV, certification, cost item or submitted certificate names.

All four carry a document_id pointing into the document log. It used to be
stored as typed: text that is not an id, or an id naming nothing, was saved
and only showed up later as a file that cannot be opened. A save now refuses
either before it writes either side.

What stays allowed is a record that already points at a deleted document being
saved with that same pointer, so its other fields can still be edited.

Runs against mongomock with UPLOAD_DIR in a temporary directory, as
test_document_deletion.py does, and fingerprints the real uploads folder.
"""

import gc
from datetime import datetime, timezone
import io
import tempfile
import unittest

import mongomock
from bson import ObjectId
from fastapi.testclient import TestClient

from app import auth, main, mongodb, storage
try:  # run as tests.test_x from backend/, or discovered with tests/ as the top
    from tests.test_document_deletion import (
        _ArrayFilterCollection, _fingerprint_real_uploads, _pdf,
    )
except ImportError:
    from test_document_deletion import (
        _ArrayFilterCollection, _fingerprint_real_uploads, _pdf,
    )



class _PositionalProjection(_ArrayFilterCollection):
    """Adds the one find_one projection mongomock raises on: `{"field.$": 1}`.

    The routers read the element a query matched this way, to learn what a
    nested record pointed at before an edit. This does the same by fetching
    the record whole and keeping only the element whose id the filter names.
    Any other projection is passed straight through.
    """

    def find_one(self, filter=None, projection=None, *args, **kwargs):
        positional = [k for k in (projection or {}) if k.endswith(".$")]
        if not positional:
            return self._collection.find_one(filter, projection, *args, **kwargs)
        (key,) = positional
        field = key[:-2]
        record = self._collection.find_one(filter)
        if record is None:
            return None
        wanted = filter[f"{field}.id"]
        return {
            "_id": record["_id"],
            field: [item for item in record.get(field) or [] if item.get("id") == wanted][:1],
        }


PASSWORD = "correct horse battery staple"
STALE = str(ObjectId())  # a well-formed id that names no document

# kind -> (collection attribute, owner, URL segment, array field, extra fields)
KINDS = {
    "cv": ("employees", "employees", "cvs", "cvs", {"version_label": "v1"}),
    "certification": (
        "employees", "employees", "certifications", "certifications", {"name": "PMP"},
    ),
    "cost item": (
        "tenders", "tenders", "cost-items", "cost_items", {"cost_type": "EMD", "amount": "10"},
    ),
    "certificate": (
        "tenders", "tenders", "certificates", "certificates", {"name": "ISO 9001"},
    ),
}


class NestedDocumentIdTests(unittest.TestCase):
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

        database = mongodb.get_database()
        cls.documents = database[mongodb.MONGO_COLLECTION_NAME]
        cls.users = database[mongodb.MONGO_USERS_COLLECTION_NAME]
        cls.database = database
        cls.collections = {
            "employees": _PositionalProjection(mongodb.get_employee_records()),
            "tenders": _PositionalProjection(mongodb.get_tender_records()),
        }
        main.app.dependency_overrides[mongodb.get_employee_records] = (
            lambda: cls.collections["employees"]
        )
        main.app.dependency_overrides[mongodb.get_tender_records] = (
            lambda: cls.collections["tenders"]
        )
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
        for collection in (self.users, self.documents, *self.collections.values()):
            collection.delete_many({})
        self.database[mongodb.MONGO_SESSIONS_COLLECTION_NAME].delete_many({})
        self.database[mongodb.MONGO_LOGIN_THROTTLE_COLLECTION_NAME].delete_many({})
        auth.create_user("admin@example.test", PASSWORD, self.users)
        response = self.client.post(
            "/auth/login",
            json={"username": "admin@example.test", "password": PASSWORD},
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.csrf = response.json()["csrf_token"]

    # --- fixtures ---------------------------------------------------------

    def upload(self, file_name="receipt.pdf"):
        response = self.client.post(
            "/documents/",
            data={"document_type": "Cost Sheet", "category": "Tendering",
                  "client_name": "Ref Check Ltd", "submitted_by": "tests"},
            files={"document_file": (file_name, io.BytesIO(_pdf()), "application/pdf")},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def make_owner(self, kind):
        collection_name, _, _, _, _ = KINDS[kind]
        collection = self.collections[collection_name]
        # created_at because the response models require it.
        fields = {"full_name": "Asha Patil"} if collection_name == "employees" else {"title": "Metro bid"}
        now = datetime.now(timezone.utc)
        return str(
            collection.insert_one({**fields, "created_at": now, "updated_at": now}).inserted_id
        )

    def url(self, kind, owner_id, item_id=None):
        _, owner, segment, _, _ = KINDS[kind]
        base = f"/{owner}/{owner_id}/{segment}"
        return f"{base}/{item_id}" if item_id else base

    def body(self, kind, document_id, **more):
        return {**KINDS[kind][4], "document_id": document_id, **more}

    def add(self, kind, owner_id, document_id):
        return self.client.post(
            self.url(kind, owner_id),
            json=self.body(kind, document_id),
            headers={"X-CSRF-Token": self.csrf},
        )

    def edit(self, kind, owner_id, item_id, document_id, **more):
        return self.client.put(
            self.url(kind, owner_id, item_id),
            json=self.body(kind, document_id, **more),
            headers={"X-CSRF-Token": self.csrf},
        )

    def stored_items(self, kind, owner_id):
        collection_name, _, _, field, _ = KINDS[kind]
        record = self.collections[collection_name].find_one({"_id": ObjectId(owner_id)})
        return record.get(field) or []

    def seed_item(self, kind, owner_id, document_id):
        """A record written straight in, so it can hold a pointer to nothing."""
        collection_name, _, _, field, _ = KINDS[kind]
        item = {
            "id": "item-1",
            "created_at": datetime.now(timezone.utc),
            **self.body(kind, document_id),
        }
        self.collections[collection_name].update_one(
            {"_id": ObjectId(owner_id)}, {"$push": {field: item}}
        )
        return "item-1"

    # --- adding -----------------------------------------------------------

    def test_adding_with_an_existing_document_is_accepted(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                document_id = self.upload()
                response = self.add(kind, owner_id, document_id)
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(self.stored_items(kind, owner_id)[0]["document_id"], document_id)

    def test_adding_with_no_document_is_accepted(self):
        for kind in KINDS:
            for empty in (None, ""):
                with self.subTest(kind=kind, value=empty):
                    self.setUp()
                    owner_id = self.make_owner(kind)
                    response = self.add(kind, owner_id, empty)
                    self.assertEqual(response.status_code, 201, response.text)
                    self.assertFalse(self.stored_items(kind, owner_id)[0]["document_id"])

    def test_adding_with_a_malformed_id_is_refused_and_writes_nothing(self):
        for kind in KINDS:
            for bad in ("not-an-id", "123", "z" * 24, " "):
                with self.subTest(kind=kind, value=bad):
                    self.setUp()
                    owner_id = self.make_owner(kind)
                    response = self.add(kind, owner_id, bad)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(self.stored_items(kind, owner_id), [])

    def test_adding_with_a_nonexistent_document_is_refused_and_writes_nothing(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                response = self.add(kind, owner_id, STALE)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()["detail"], "Document not found")
                self.assertEqual(self.stored_items(kind, owner_id), [])

    def test_a_refused_employee_add_stamps_no_document(self):
        owner_id = self.make_owner("cv")
        document_id = self.upload()
        self.add("cv", owner_id, "not-an-id")
        self.assertIsNone(
            self.documents.find_one({"_id": ObjectId(document_id)}).get("employee_id")
        )

    # --- editing ----------------------------------------------------------

    def test_editing_to_an_existing_document_is_accepted(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                item_id = self.seed_item(kind, owner_id, None)
                document_id = self.upload()
                response = self.edit(kind, owner_id, item_id, document_id)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.stored_items(kind, owner_id)[0]["document_id"], document_id)

    def test_editing_to_no_document_is_accepted(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                item_id = self.seed_item(kind, owner_id, self.upload())
                response = self.edit(kind, owner_id, item_id, None)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIsNone(self.stored_items(kind, owner_id)[0]["document_id"])

    def test_editing_to_a_bad_document_is_refused_and_leaves_the_record_alone(self):
        for kind in KINDS:
            for bad, status in (("not-an-id", 422), (STALE, 404)):
                with self.subTest(kind=kind, value=bad):
                    self.setUp()
                    owner_id = self.make_owner(kind)
                    original = self.upload()
                    item_id = self.seed_item(kind, owner_id, original)
                    response = self.edit(kind, owner_id, item_id, bad)
                    self.assertEqual(response.status_code, status, response.text)
                    self.assertEqual(
                        self.stored_items(kind, owner_id)[0]["document_id"], original
                    )

    def test_an_unchanged_stale_pointer_can_still_be_saved(self):
        """Its document was deleted elsewhere; the record's other fields are
        still the person's to edit."""
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                item_id = self.seed_item(kind, owner_id, STALE)
                label = {"cv": "version_label"}.get(kind, "name")
                if kind == "cost item":
                    label = "cost_type"
                response = self.edit(kind, owner_id, item_id, STALE, **{label: "Renamed"})
                self.assertEqual(response.status_code, 200, response.text)
                item = self.stored_items(kind, owner_id)[0]
                self.assertEqual(item["document_id"], STALE)
                self.assertEqual(item[label], "Renamed")

    def test_changing_a_stale_pointer_to_another_missing_document_is_refused(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                item_id = self.seed_item(kind, owner_id, STALE)
                response = self.edit(kind, owner_id, item_id, str(ObjectId()))
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(self.stored_items(kind, owner_id)[0]["document_id"], STALE)

    def test_a_stale_pointer_can_be_replaced_with_a_real_document(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                item_id = self.seed_item(kind, owner_id, STALE)
                document_id = self.upload()
                response = self.edit(kind, owner_id, item_id, document_id)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.stored_items(kind, owner_id)[0]["document_id"], document_id)


if __name__ == "__main__":
    unittest.main()
