"""Create, read, update and delete of the records nested in an employee or a tender.

test_nested_document_ids.py checks that a document_id being saved resolves,
and test_tender_document_ownership.py checks who owns a tender's documents.
Neither follows a nested record through its life: what an add returns, what an
edit leaves alone, what a delete takes with it, which bodies are refused, and
-- for an employee's CVs and certifications, which stamp and release
`documents.employee_id` -- who holds the file at each step. This does, for all
four kinds, plus the role restrictions on the employee routes (the tender
routes' are in the ownership file).

Runs against mongomock with UPLOAD_DIR in a temporary directory, and
fingerprints the real uploads folder, as the other document tests do.
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
    from tests.test_nested_document_ids import KINDS, PASSWORD, STALE, _PositionalProjection
except ImportError:
    from test_document_deletion import _fingerprint_real_uploads
    from test_nested_document_ids import KINDS, PASSWORD, STALE, _PositionalProjection

ORIGIN = {"Origin": "http://localhost:5173"}
OLD = datetime(2000, 1, 1, tzinfo=timezone.utc)

# kind -> (field that identifies the record in a response, a body field to edit,
#          a body the schema refuses)
FIELDS = {
    "cv": ("version_label", "version_label", {"cv_date": "31/12/2024"}),
    "certification": ("name", "issuing_body", {"name": ""}),
    "cost item": ("cost_type", "cost_type", {"amount": "-5"}),
    "certificate": ("name", "issuing_body", {"name": ""}),
}
EMPLOYEE_KINDS = ("cv", "certification")


def _pdf(text: str) -> bytes:
    """A readable PDF whose bytes depend on `text` (an identical file is
    refused as a duplicate, and these tests need several different ones)."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


class NestedRecordCrudTests(unittest.TestCase):
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
        self.csrf = self.login("admin@example.test")
        self.uploaded = 0

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

    def upload(self):
        self.uploaded += 1
        response = self.client.post(
            "/documents/",
            data={"document_type": "Cost Sheet", "submitted_by": "tests"},
            files={
                "document_file": (
                    f"file {self.uploaded}.pdf",
                    io.BytesIO(_pdf(f"DOCUMENT NUMBER {self.uploaded}")),
                    "application/pdf",
                )
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def make_owner(self, kind):
        collection_name = KINDS[kind][0]
        fields = (
            {"full_name": "Asha Patil"} if collection_name == "employees" else {"title": "Metro bid"}
        )
        return str(
            self.collections[collection_name]
            .insert_one({**fields, "created_at": OLD, "updated_at": OLD})
            .inserted_id
        )

    def url(self, kind, owner_id, item_id=None):
        _, owner, segment, _, _ = KINDS[kind]
        base = f"/{owner}/{owner_id}/{segment}"
        return f"{base}/{item_id}" if item_id else base

    def body(self, kind, document_id=None, **more):
        return {**KINDS[kind][4], "document_id": document_id, **more}

    def send(self, method, url, body=None, csrf=None):
        kwargs = {"headers": {"X-CSRF-Token": csrf or self.csrf}}
        if body is not None:
            kwargs["json"] = body
        return getattr(self.client, method)(url, **kwargs)

    def add(self, kind, owner_id, document_id=None, **more):
        return self.send("post", self.url(kind, owner_id), self.body(kind, document_id, **more))

    def edit(self, kind, owner_id, item_id, document_id=None, **more):
        return self.send(
            "put", self.url(kind, owner_id, item_id), self.body(kind, document_id, **more)
        )

    def remove(self, kind, owner_id, item_id):
        return self.send("delete", self.url(kind, owner_id, item_id))

    def owner_record(self, kind, owner_id):
        return self.collections[KINDS[kind][0]].find_one({"_id": ObjectId(owner_id)})

    def items(self, kind, owner_id):
        return self.owner_record(kind, owner_id).get(KINDS[kind][3]) or []

    def document(self, document_id):
        return self.documents.find_one({"_id": ObjectId(document_id)})

    def holder(self, document_id):
        return self.document(document_id).get("employee_id")

    # --- create and read --------------------------------------------------

    def test_an_add_returns_the_owner_with_the_new_record_and_keeps_the_others(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                first_doc = self.upload()
                first = self.add(kind, owner_id, first_doc, **{FIELDS[kind][1]: "first"})
                self.assertEqual(first.status_code, 201, first.text)
                second = self.add(kind, owner_id, None, **{FIELDS[kind][1]: "second"})
                self.assertEqual(second.status_code, 201, second.text)

                returned = second.json()[KINDS[kind][3]]
                self.assertEqual([r[FIELDS[kind][1]] for r in returned], ["first", "second"])
                self.assertEqual(returned[0]["document_id"], first_doc)
                ids = [r["id"] for r in returned]
                self.assertTrue(all(ids) and len(set(ids)) == 2)
                self.assertTrue(all(r["created_at"] for r in returned))
                self.assertGreater(
                    self.owner_record(kind, owner_id)["updated_at"].replace(tzinfo=timezone.utc),
                    OLD,
                )

    def test_the_nested_records_are_part_of_reading_the_owner(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                document_id = self.upload()
                self.add(kind, owner_id, document_id)
                response = self.client.get(f"/{KINDS[kind][1]}/{owner_id}")
                self.assertEqual(response.status_code, 200, response.text)
                (record,) = response.json()[KINDS[kind][3]]
                self.assertEqual(record["document_id"], document_id)

    def test_employee_reads_link_owned_certificate_files_without_writing_records(self):
        employee_id = self.make_owner("certification")
        certifications = [
            "Certificate Course for Women Directors",
            "Training on Prevention of Sexual Harassment (POSH)",
            "Crash Course on A to Z of Initial Public Offer (IPO)",
            "Online Proficiency Self Assessment Test for Independent Director's Databank",
            "Commercial Contract Management",
            "IT Tools for professionals",
        ]
        file_names = [
            "ANJALI PADHYE - Certificate Course for Women Directors.pdf",
            "ANJALI PADHYE - Certificate of Completion - POSH.pdf",
            "ANJALI PADHYE - Crash course on A to Z of IPO Certificate.pdf",
            "ANJALI PADHYE - Online Proficiency Self Assessment Test for Independent Director's Databank.pdf",
            "ANJALI_PADHYE - Commercial Contract Management.pdf",
            "ANJALI_PADHYE - IT Tools for professionals.pdf",
        ]
        employee = self.collections["employees"].find_one({"_id": ObjectId(employee_id)})
        employee["full_name"] = "ANJALI VINAY PADHYE"
        employee["employee_code"] = "301"
        employee["certifications"] = [
            {
                "id": f"certification-{index}",
                "name": name,
                "document_id": None,
                "file_name": None,
                "created_at": OLD,
            }
            for index, name in enumerate(certifications)
        ]
        self.collections["employees"].replace_one({"_id": ObjectId(employee_id)}, employee)

        document_ids = []
        for file_name in file_names:
            document_id = ObjectId()
            document_ids.append(str(document_id))
            self.documents.insert_one(
                {
                    "_id": document_id,
                    "employee_id": employee_id,
                    "document_type": "Certification",
                    "file_name": file_name,
                    "created_at": OLD,
                }
            )

        response = self.client.get(f"/employees/{employee_id}")
        self.assertEqual(response.status_code, 200, response.text)
        returned = response.json()["certifications"]
        self.assertEqual(
            [(item["document_id"], item["file_name"]) for item in returned],
            list(zip(document_ids, file_names)),
        )

        stored = self.collections["employees"].find_one({"_id": ObjectId(employee_id)})
        self.assertTrue(
            all(item["document_id"] is None for item in stored["certifications"]),
            "read-time document links must not change employee records",
        )
        self.assertEqual(self.documents.count_documents({"employee_id": employee_id}), 6)

        singleton_cases = [
            ("34", "SACHIN BURGHATE", "Certified Srum Master", "Sachin Burghate.png"),
            ("995", "TEJAS KALE", "SQL Course", "TEJAS KALE SQL Course.png"),
        ]
        singleton_expected = []
        for employee_code, full_name, name, file_name in singleton_cases:
            singleton_employee_id = self.make_owner("certification")
            singleton_employee = self.collections["employees"].find_one(
                {"_id": ObjectId(singleton_employee_id)}
            )
            singleton_employee.update(
                {
                    "full_name": full_name,
                    "employee_code": employee_code,
                    "certifications": [
                        {
                            "id": f"certification-{employee_code}",
                            "name": name,
                            "document_id": None,
                            "file_name": None,
                            "created_at": OLD,
                        }
                    ],
                }
            )
            self.collections["employees"].replace_one(
                {"_id": ObjectId(singleton_employee_id)}, singleton_employee
            )
            document_id = ObjectId()
            self.documents.insert_one(
                {
                    "_id": document_id,
                    "employee_id": singleton_employee_id,
                    "document_type": "Certification",
                    "file_name": file_name,
                    "created_at": OLD,
                }
            )
            singleton_expected.append((full_name, name, str(document_id), file_name))
            response = self.client.get(f"/employees/{singleton_employee_id}")
            self.assertEqual(response.status_code, 200, response.text)
            (linked,) = response.json()["certifications"]
            self.assertEqual(
                (linked["document_id"], linked["file_name"]),
                (str(document_id), file_name),
            )

        self.assertEqual(self.documents.count_documents({"document_type": "Certification"}), 8)

        index = self.client.get("/employees/certifications?page_size=10")
        self.assertEqual(index.status_code, 200, index.text)
        indexed = {
            (item["employee_name"], item["name"]): item for item in index.json()["items"]
        }
        imported_certificate_rows = [
            *zip(
                ["ANJALI VINAY PADHYE"] * len(certifications),
                certifications,
                document_ids,
                file_names,
            ),
            *singleton_expected,
        ]
        for employee_name, name, document_id, file_name in imported_certificate_rows:
            self.assertEqual(
                (
                    indexed[(employee_name, name)]["document_id"],
                    indexed[(employee_name, name)]["file_name"],
                ),
                (document_id, file_name),
            )

    def test_an_add_to_an_owner_that_is_not_there_is_404_and_writes_nothing(self):
        for kind in KINDS:
            for missing in (str(ObjectId()), "not-an-id"):
                with self.subTest(kind=kind, owner=missing):
                    self.setUp()
                    document_id = self.upload()
                    response = self.add(kind, missing, document_id)
                    self.assertEqual(response.status_code, 404, response.text)
                    self.assertIsNone(self.holder(document_id))

    # --- update -----------------------------------------------------------

    def test_an_edit_changes_only_its_record_and_keeps_its_identity(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                doc_a, doc_b = self.upload(), self.upload()
                self.add(kind, owner_id, doc_a, **{FIELDS[kind][1]: "A"})
                self.add(kind, owner_id, doc_b, **{FIELDS[kind][1]: "B"})
                before_a, before_b = self.items(kind, owner_id)

                response = self.edit(kind, owner_id, before_a["id"], doc_a, **{FIELDS[kind][1]: "A2"})
                self.assertEqual(response.status_code, 200, response.text)
                after_a, after_b = self.items(kind, owner_id)

                self.assertEqual(after_a[FIELDS[kind][1]], "A2")
                self.assertEqual((after_a["id"], after_a["created_at"]), (before_a["id"], before_a["created_at"]))
                self.assertEqual(after_a["document_id"], doc_a)
                self.assertEqual(after_b, before_b)

    def test_an_edit_can_move_a_record_to_another_document_or_clear_it(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                doc_a, doc_b = self.upload(), self.upload()
                self.add(kind, owner_id, doc_a)
                item_id = self.items(kind, owner_id)[0]["id"]

                self.assertEqual(self.edit(kind, owner_id, item_id, doc_b).status_code, 200)
                self.assertEqual(self.items(kind, owner_id)[0]["document_id"], doc_b)
                self.assertEqual(self.edit(kind, owner_id, item_id, None).status_code, 200)
                self.assertIsNone(self.items(kind, owner_id)[0]["document_id"])
                # The files stay in the log either way.
                self.assertIsNotNone(self.document(doc_a))
                self.assertIsNotNone(self.document(doc_b))

    def test_an_edit_of_a_record_or_owner_that_is_not_there_is_404_and_writes_nothing(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                document_id = self.upload()
                self.add(kind, owner_id, None)
                before = self.items(kind, owner_id)

                for owner, item in ((owner_id, "no-such-record"), (str(ObjectId()), "x"), ("not-an-id", "x")):
                    response = self.edit(kind, owner, item, document_id)
                    self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(self.items(kind, owner_id), before)
                self.assertIsNone(self.holder(document_id))

    # --- delete -----------------------------------------------------------

    def test_a_delete_takes_only_its_record_and_leaves_every_file_in_the_log(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                doc_a, doc_b = self.upload(), self.upload()
                self.add(kind, owner_id, doc_a, **{FIELDS[kind][1]: "A"})
                self.add(kind, owner_id, doc_b, **{FIELDS[kind][1]: "B"})
                first, second = self.items(kind, owner_id)

                response = self.remove(kind, owner_id, first["id"])
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([r["id"] for r in response.json()[KINDS[kind][3]]], [second["id"]])
                self.assertEqual(self.items(kind, owner_id), [second])
                self.assertEqual(self.items(kind, owner_id)[0]["document_id"], doc_b)
                self.assertIsNotNone(self.document(doc_a))
                self.assertIsNotNone(self.document(doc_b))

    def test_deleting_twice_or_from_an_owner_that_is_not_there_is_404(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                self.add(kind, owner_id, None)
                item_id = self.items(kind, owner_id)[0]["id"]
                self.assertEqual(self.remove(kind, owner_id, item_id).status_code, 200)
                for owner, item in ((owner_id, item_id), (str(ObjectId()), item_id), ("not-an-id", item_id)):
                    response = self.remove(kind, owner, item)
                    self.assertEqual(response.status_code, 404, response.text)

    def test_saving_the_owners_own_form_cannot_replace_its_nested_records(self):
        for kind, body, path_key in (
            ("cv", {"full_name": "Asha Patil", "designation": "Engineer"}, "employees"),
            ("cost item", {"title": "Metro bid"}, "tenders"),
        ):
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                document_id = self.upload()
                self.add(kind, owner_id, document_id)
                before = self.items(kind, owner_id)

                response = self.send(
                    "put",
                    f"/{path_key}/{owner_id}",
                    {**body, KINDS[kind][3]: [], "document_ids": [STALE]},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.items(kind, owner_id), before)

    # --- validation -------------------------------------------------------

    def test_a_body_the_schema_refuses_is_422_and_writes_nothing(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                document_id = self.upload()
                refused = FIELDS[kind][2]

                response = self.send("post", self.url(kind, owner_id), {**self.body(kind, document_id), **refused})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.items(kind, owner_id), [])
                self.assertIsNone(self.holder(document_id))

                self.add(kind, owner_id, None)
                before = self.items(kind, owner_id)
                response = self.send(
                    "put",
                    self.url(kind, owner_id, before[0]["id"]),
                    {**self.body(kind, document_id), **refused},
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.items(kind, owner_id), before)
                self.assertIsNone(self.holder(document_id))

    def test_a_missing_required_name_is_422(self):
        for kind in ("certification", "certificate"):
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                response = self.send("post", self.url(kind, owner_id), {"document_id": None})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.items(kind, owner_id), [])

    # --- who holds an employee's file -------------------------------------

    def test_adding_a_cv_or_certification_makes_the_employee_hold_the_file(self):
        for kind in EMPLOYEE_KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                document_id = self.upload()
                self.assertIsNone(self.holder(document_id))
                self.assertEqual(self.add(kind, owner_id, document_id).status_code, 201)
                self.assertEqual(self.holder(document_id), owner_id)

    def test_an_edit_hands_the_hold_from_the_old_file_to_the_new_one(self):
        for kind in EMPLOYEE_KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                old, new = self.upload(), self.upload()
                self.add(kind, owner_id, old)
                item_id = self.items(kind, owner_id)[0]["id"]

                self.assertEqual(self.edit(kind, owner_id, item_id, old, **{FIELDS[kind][1]: "x"}).status_code, 200)
                self.assertEqual(self.holder(old), owner_id)

                self.assertEqual(self.edit(kind, owner_id, item_id, new).status_code, 200)
                self.assertIsNone(self.holder(old))
                self.assertEqual(self.holder(new), owner_id)

                self.assertEqual(self.edit(kind, owner_id, item_id, None).status_code, 200)
                self.assertIsNone(self.holder(new))

    def test_deleting_a_cv_or_certification_releases_its_file_but_keeps_it(self):
        for kind in EMPLOYEE_KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                kept, other = self.upload(), self.upload()
                self.add(kind, owner_id, kept)
                self.add(kind, owner_id, other)
                item_id = self.items(kind, owner_id)[0]["id"]

                self.assertEqual(self.remove(kind, owner_id, item_id).status_code, 200)
                self.assertIsNone(self.holder(kept))
                self.assertIsNotNone(self.document(kept))
                self.assertEqual(self.holder(other), owner_id)

    def test_a_file_another_employee_holds_is_refused_and_stays_theirs(self):
        for kind in EMPLOYEE_KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                first, second = self.make_owner(kind), self.make_owner(kind)
                document_id = self.upload()
                self.assertEqual(self.add(kind, first, document_id).status_code, 201)

                refused = self.add(kind, second, document_id)
                self.assertEqual(refused.status_code, 409, refused.text)
                self.assertEqual(self.items(kind, second), [])

                self.add(kind, second, None)
                mine = self.items(kind, second)[0]
                refused = self.edit(kind, second, mine["id"], document_id)
                self.assertEqual(refused.status_code, 409, refused.text)
                self.assertEqual(self.items(kind, second)[0], mine)
                self.assertEqual(self.holder(document_id), first)

    # --- one file shared by two of an employee's records ------------------
    # Two CVs, or a CV and a certification, may name the same file. The hold
    # is released only when the last of them stops naming it.

    SHARING = (("cv", "cv"), ("certification", "certification"), ("cv", "certification"))

    def shared_file(self, kinds):
        first_kind, second_kind = kinds
        owner_id = self.make_owner("cv")
        document_id = self.upload()
        self.assertEqual(self.add(first_kind, owner_id, document_id).status_code, 201)
        self.assertEqual(self.add(second_kind, owner_id, document_id).status_code, 201)
        first = self.items(first_kind, owner_id)[0]["id"]
        second = self.items(second_kind, owner_id)[-1]["id"]
        return owner_id, document_id, first, second

    def test_deleting_one_of_two_records_sharing_a_file_keeps_the_hold(self):
        for kinds in self.SHARING:
            with self.subTest(kinds=kinds):
                self.setUp()
                owner_id, document_id, first, second = self.shared_file(kinds)
                self.assertEqual(self.remove(kinds[0], owner_id, first).status_code, 200)
                self.assertEqual(self.holder(document_id), owner_id)

    def test_deleting_the_last_record_naming_a_file_releases_it(self):
        for kinds in self.SHARING:
            with self.subTest(kinds=kinds):
                self.setUp()
                owner_id, document_id, first, second = self.shared_file(kinds)
                self.remove(kinds[0], owner_id, first)
                self.assertEqual(self.remove(kinds[1], owner_id, second).status_code, 200)
                self.assertIsNone(self.holder(document_id))
                self.assertIsNotNone(self.document(document_id))

    def test_repointing_one_of_two_records_sharing_a_file_keeps_the_hold(self):
        for kinds in self.SHARING:
            with self.subTest(kinds=kinds):
                self.setUp()
                owner_id, document_id, first, second = self.shared_file(kinds)
                other = self.upload()
                self.assertEqual(self.edit(kinds[0], owner_id, first, other).status_code, 200)
                self.assertEqual(self.holder(document_id), owner_id)
                self.assertEqual(self.holder(other), owner_id)

    def test_repointing_or_clearing_the_last_record_naming_a_file_releases_it(self):
        # Always the first element of its array that is edited: mongomock's
        # positional `$` writes to the first element whichever one the query
        # matched (seen with two CVs; real MongoDB does not), so a second
        # element is brought down to first place by deleting the one before it.
        for kinds in self.SHARING:
            for target in ("other", None):
                with self.subTest(kinds=kinds, to=target):
                    self.setUp()
                    owner_id, document_id, first, second = self.shared_file(kinds)
                    moved = self.upload() if target else None

                    self.assertEqual(self.remove(kinds[1], owner_id, second).status_code, 200)
                    self.assertEqual(self.holder(document_id), owner_id)  # `first` still names it
                    self.assertEqual(self.edit(kinds[0], owner_id, first, moved).status_code, 200)
                    self.assertIsNone(self.holder(document_id))
                    if moved:
                        self.assertEqual(self.holder(moved), owner_id)

    def test_saving_a_shared_record_back_unchanged_keeps_the_hold(self):
        for kinds in self.SHARING:
            with self.subTest(kinds=kinds):
                self.setUp()
                owner_id, document_id, first, second = self.shared_file(kinds)
                self.edit(kinds[0], owner_id, first, document_id, **{FIELDS[kinds[0]][1]: "renamed"})
                self.assertEqual(self.holder(document_id), owner_id)

    def test_another_employee_cannot_claim_a_file_one_record_still_names(self):
        for kinds in self.SHARING:
            with self.subTest(kinds=kinds):
                self.setUp()
                owner_id, document_id, first, second = self.shared_file(kinds)
                self.remove(kinds[0], owner_id, first)
                rival = self.make_owner("cv")
                refused = self.add("cv", rival, document_id)
                self.assertEqual(refused.status_code, 409, refused.text)
                self.assertEqual(self.items("cv", rival), [])
                self.assertEqual(self.holder(document_id), owner_id)

    def test_a_file_becomes_claimable_again_once_its_last_record_is_gone(self):
        self.setUp()
        owner_id, document_id, first, second = self.shared_file(("cv", "certification"))
        self.remove("cv", owner_id, first)
        self.remove("certification", owner_id, second)
        rival = self.make_owner("cv")
        self.assertEqual(self.add("cv", rival, document_id).status_code, 201)
        self.assertEqual(self.holder(document_id), rival)

    def test_a_stale_pointer_saved_back_unchanged_does_not_disturb_any_hold(self):
        for kind in EMPLOYEE_KINDS:
            with self.subTest(kind=kind):
                self.setUp()
                owner_id = self.make_owner(kind)
                self.collections["employees"].update_one(
                    {"_id": ObjectId(owner_id)},
                    {"$push": {KINDS[kind][3]: {
                        "id": "item-1", "created_at": OLD, **self.body(kind, STALE)}}},
                )
                response = self.edit(kind, owner_id, "item-1", STALE, **{FIELDS[kind][1]: "kept"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.items(kind, owner_id)[0]["document_id"], STALE)
                self.assertEqual(self.remove(kind, owner_id, "item-1").status_code, 200)
                self.assertEqual(self.items(kind, owner_id), [])

    # --- roles on the employee routes -------------------------------------

    def employee_writes(self, employee_id, cv_id, certification_id):
        cv, cert = {"version_label": "v"}, {"name": "PMP"}
        base = f"/employees/{employee_id}"
        return (
            ("post", f"{base}/cvs", cv),
            ("put", f"{base}/cvs/{cv_id}", cv),
            ("delete", f"{base}/cvs/{cv_id}", None),
            ("post", f"{base}/certifications", cert),
            ("put", f"{base}/certifications/{certification_id}", cert),
            ("delete", f"{base}/certifications/{certification_id}", None),
        )

    def seeded_employee(self):
        owner_id = self.make_owner("cv")
        document_id = self.upload()
        self.add("cv", owner_id, document_id)
        self.add("certification", owner_id, None)
        record = self.owner_record("cv", owner_id)
        return owner_id, record["cvs"][0]["id"], record["certifications"][0]["id"]

    def test_hod_and_normal_users_cannot_change_an_employees_records(self):
        owner_id, cv_id, certification_id = self.seeded_employee()
        before = self.owner_record("cv", owner_id)
        for username, role in (("hod@example.test", "HOD"), ("user@example.test", "Normal User")):
            csrf = self.login(username, role)
            for method, path, body in self.employee_writes(owner_id, cv_id, certification_id):
                with self.subTest(role=role, method=method, path=path):
                    self.assertEqual(self.send(method, path, body, csrf=csrf).status_code, 403)
            with self.subTest(role=role, read=True):
                self.assertEqual(self.client.get(f"/employees/{owner_id}").status_code, 403)
        self.assertEqual(self.owner_record("cv", owner_id), before)

    def test_unauthenticated_and_csrf_less_requests_cannot_change_an_employees_records(self):
        owner_id, cv_id, certification_id = self.seeded_employee()
        before = self.owner_record("cv", owner_id)
        writes = self.employee_writes(owner_id, cv_id, certification_id)

        for method, path, body in writes:  # signed in as Admin, no token
            with self.subTest(case="no csrf", method=method, path=path):
                kwargs = {} if body is None else {"json": body}
                self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 403)

        self.client.cookies.clear()
        for method, path, body in writes:
            with self.subTest(case="signed out", method=method, path=path):
                kwargs = {} if body is None else {"json": body}
                self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 401)
        self.assertEqual(self.owner_record("cv", owner_id), before)


if __name__ == "__main__":
    unittest.main()
