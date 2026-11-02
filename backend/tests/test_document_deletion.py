"""Deleting a document: what it takes with it, and what it refuses to take.

Deleting a document is the one irreversible thing the app does, and it
reaches into four other collections to do it. Until now that was checked
only by the verify_*.py scripts, which need a real MongoDB and a person to
run them; the employee side of it -- a CV's and a certification's pointer --
was not checked anywhere at all.

Everything here runs against mongomock with UPLOAD_DIR pointed at a
temporary directory, and the real uploads folder is fingerprinted before and
after so a test that writes into it is a failure rather than a surprise.

The owning records are written straight into the collections. What is under
test is the documents router, not the project, tender and employee ones, and
inserting the shape it reads keeps each test to the references it is about.
"""

import gc
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import mongomock
import pymupdf
from bson import ObjectId
from fastapi.testclient import TestClient

from app import auth, main, mongodb, storage

AREA = {"page": 0, "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.3}


class _ArrayFilterCollection:
    """A mongomock collection that understands one `array_filters` update.

    mongomock raises NotImplementedError for array_filters, which is why
    `_set_nested_pointer_fields` -- the write that clears a CV's, a
    certification's, a cost item's and a submitted certificate's pointer --
    has never been covered by an automated test. The alternative to this
    wrapper is a real MongoDB, which these tests must not need.

    It handles exactly the shape documents_mongo.py sends and raises on
    anything else, so a change to that write cannot quietly start being
    emulated as something it is not. Real-MongoDB behaviour is still checked
    by verify_tenders.py.
    """

    def __init__(self, collection):
        self._collection = collection

    def __getattr__(self, name):
        return getattr(self._collection, name)

    def update_many(self, filter, update, array_filters=None, **kwargs):
        if array_filters is None:
            return self._collection.update_many(filter, update, **kwargs)

        if set(update) != {"$set"}:
            raise AssertionError(f"unsupported update operators: {sorted(update)}")
        if len(array_filters) != 1:
            raise AssertionError("expected exactly one array filter")
        (condition,) = array_filters
        if len(condition) != 1:
            raise AssertionError(f"unsupported array filter: {condition}")
        (filter_key, wanted), = condition.items()
        identifier, _, filter_field = filter_key.partition(".")
        if not filter_field:
            raise AssertionError(f"unsupported array filter: {condition}")

        marker = f".$[{identifier}]."
        matched = 0
        for record in list(self._collection.find(filter)):
            # Which elements the filter picks is decided once, against the
            # record as it arrived. Re-testing it per field would miss every
            # field after the first: clearing document_id is exactly what
            # the filter matches on.
            chosen = {
                field: [
                    entry
                    for entry in record.get(field) or []
                    if entry.get(filter_field) == wanted
                ]
                for field in {
                    key.partition(marker)[0] for key in update["$set"] if marker in key
                }
            }
            for key, value in update["$set"].items():
                if marker not in key:
                    record[key] = value
                    continue
                field, _, attribute = key.partition(marker)
                for entry in chosen[field]:
                    entry[attribute] = value
            self._collection.replace_one({"_id": record["_id"]}, record)
            matched += 1
        return matched


def _pdf() -> bytes:
    """A small readable PDF, so the redaction endpoints have something real."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "CONTRACT VALUE 1,250,000", fontsize=14)
    page.insert_text((72, 600), "Public footer", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _fingerprint_real_uploads() -> list:
    """Names and sizes of the files in the app's own uploads folder."""
    folder = os.path.join(os.path.dirname(os.path.abspath(storage.__file__)), "uploads")
    if not os.path.isdir(folder):
        return []
    return sorted(
        (name, os.path.getsize(os.path.join(folder, name)))
        for name in os.listdir(folder)
    )


class DocumentDeletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # ignore_cleanup_errors because MuPDF maps a file it has opened and
        # Windows will not unlink it until the mapping is released.
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
        cls.projects = mongodb.get_project_records()
        cls.employees = _ArrayFilterCollection(mongodb.get_employee_records())
        cls.tenders = _ArrayFilterCollection(mongodb.get_tender_records())
        main.app.dependency_overrides[mongodb.get_employee_records] = lambda: cls.employees
        main.app.dependency_overrides[mongodb.get_tender_records] = lambda: cls.tenders

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
        self.users.delete_many({})
        self.documents.delete_many({})
        self.projects.delete_many({})
        self.employees.delete_many({})
        self.tenders.delete_many({})
        self.database[mongodb.MONGO_SESSIONS_COLLECTION_NAME].delete_many({})
        self.database[mongodb.MONGO_LOGIN_THROTTLE_COLLECTION_NAME].delete_many({})
        auth.create_user("admin@example.test", "correct horse battery staple", self.users)
        self.csrf = self.login("admin@example.test")

    def login(self, username, role=None):
        if role:
            auth.create_user(username, "correct horse battery staple", self.users, role=role)
        self.client.cookies.clear()
        response = self.client.post(
            "/auth/login",
            json={"username": username, "password": "correct horse battery staple"},
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf_token"]

    # --- fixtures ---------------------------------------------------------

    def upload(self, file_name="cost sheet.pdf", **fields):
        """An entry with a real file, as the log's own upload makes it."""
        response = self.client.post(
            "/documents/",
            data={
                "document_type": "Cost Sheet",
                "category": "Tendering",
                "client_name": "Deletion Check Ltd",
                "submitted_by": "tests",
                **fields,
            },
            files={"document_file": (file_name, io.BytesIO(_pdf()), "application/pdf")},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        document_id = response.json()["id"]
        stored = self.documents.find_one({"_id": ObjectId(document_id)})["stored_file_name"]
        return document_id, stored

    def delete(self, document_id, **owner):
        query = "&".join(f"{key}={value}" for key, value in owner.items())
        return self.client.delete(
            f"/documents/{document_id}{'?' + query if query else ''}",
            headers={"X-CSRF-Token": self.csrf},
        )

    def usage(self, document_id, **owner):
        query = "&".join(f"{key}={value}" for key, value in owner.items())
        response = self.client.get(
            f"/documents/{document_id}/usage{'?' + query if query else ''}"
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def add_project(self, document_id, title="Riverside Bridge"):
        return str(
            self.projects.insert_one(
                {"title": title, "document_ids": [document_id]}
            ).inserted_id
        )

    def add_tender(self, title="Metro bid", **lists):
        return str(self.tenders.insert_one({"title": title, **lists}).inserted_id)

    def add_employee(self, full_name="Asha Patil", **lists):
        return str(self.employees.insert_one({"full_name": full_name, **lists}).inserted_id)

    def save_redacted_copy(self, document_id):
        response = self.client.post(
            f"/documents/{document_id}/redacted-copy/save",
            json={"areas": [AREA]},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    # --- what /usage reports ---------------------------------------------

    def test_usage_is_empty_for_a_document_nothing_references(self):
        document_id, _ = self.upload()
        self.assertEqual(self.usage(document_id), [])

    def test_usage_reports_every_kind_of_reference(self):
        document_id, _ = self.upload()
        project_id = self.add_project(document_id)
        tender_id = self.add_tender(
            document_ids=[document_id],
            cost_items=[{"id": "cost-a", "document_id": document_id}],
            certificates=[{"id": "cert-a", "document_id": document_id}],
        )
        employee_id = self.add_employee(
            cvs=[{"id": "cv-a", "document_id": document_id}],
            certifications=[{"id": "qual-a", "document_id": document_id}],
        )
        copy_id = self.save_redacted_copy(document_id)

        uses = {use["kind"]: use for use in self.usage(document_id)}
        self.assertEqual(set(uses), {"project", "tender", "employee", "document"})
        self.assertEqual(uses["project"]["id"], project_id)
        self.assertEqual(uses["project"]["label"], "Riverside Bridge")
        self.assertEqual(uses["tender"]["id"], tender_id)
        self.assertEqual(
            sorted(uses["tender"]["roles"]),
            ["attached document", "cost receipt", "submitted certificate"],
        )
        self.assertEqual(uses["employee"]["id"], employee_id)
        self.assertEqual(sorted(uses["employee"]["roles"]), ["CV", "certification"])
        self.assertEqual(uses["document"]["id"], copy_id)
        self.assertEqual(uses["document"]["roles"], ["redacted copy"])

    def test_usage_named_from_an_owner_leaves_that_owner_out(self):
        document_id, _ = self.upload()
        project_id = self.add_project(document_id)
        tender_id = self.add_tender(document_ids=[document_id])

        from_project = self.usage(document_id, from_project=project_id)
        self.assertEqual([use["kind"] for use in from_project], ["tender"])
        from_tender = self.usage(document_id, from_tender=tender_id)
        self.assertEqual([use["kind"] for use in from_tender], ["project"])

    def test_usage_for_a_missing_document_is_404(self):
        self.assertEqual(
            self.client.get(f"/documents/{ObjectId()}/usage").status_code, 404
        )

    # --- the delete itself ------------------------------------------------

    def test_delete_removes_the_record_and_its_file_from_disk(self):
        document_id, stored = self.upload()
        path = storage.upload_path(stored)
        self.assertTrue(os.path.exists(path))

        self.assertEqual(self.delete(document_id).status_code, 204)
        self.assertIsNone(self.documents.find_one({"_id": ObjectId(document_id)}))
        self.assertFalse(os.path.exists(path), "the stored file is still on disk")
        self.assertEqual(self.delete(document_id).status_code, 404)

    def test_a_file_that_cannot_be_removed_still_deletes_the_record_and_is_logged(self):
        """The record is the source of truth, so the delete still succeeds.

        What changes is that the file left behind is now named in the log
        rather than disappearing quietly -- see tests/test_storage.py.
        """
        document_id, stored = self.upload()
        with patch.object(
            storage.os, "remove", side_effect=PermissionError(13, "used by another process")
        ):
            with self.assertLogs("uvicorn.error", level="WARNING") as logged:
                response = self.delete(document_id)

        self.assertEqual(response.status_code, 204)
        self.assertIsNone(self.documents.find_one({"_id": ObjectId(document_id)}))
        self.assertIn(stored, "\n".join(logged.output))
        self.assertTrue(os.path.exists(storage.upload_path(stored)))

    def _delete_with_failing_clear(self, document_id, dependency, collection):
        """Delete while every update_many on `collection` raises.

        Returns the response and the log it wrote. The app's catch-all turns
        the original exception into a 500, so the original error is checked in
        the log (where its traceback goes) and the 500 is checked as the
        answer -- the caller is never told the delete worked.
        """

        class Failing:
            def __getattr__(self, name):
                return getattr(collection, name)

            def update_many(self, *args, **kwargs):
                raise RuntimeError("simulated database failure")

        main.app.dependency_overrides[dependency] = lambda: Failing()
        try:
            with self.assertLogs("uvicorn.error", level="WARNING") as logged:
                response = self.delete(document_id)
        finally:
            main.app.dependency_overrides.pop(dependency, None)
            if dependency is mongodb.get_employee_records:
                main.app.dependency_overrides[dependency] = lambda: self.employees
            elif dependency is mongodb.get_tender_records:
                main.app.dependency_overrides[dependency] = lambda: self.tenders
        return response, "\n".join(logged.output)

    def test_a_failed_reference_clear_still_removes_the_file_and_is_logged(self):
        for label, dependency, collection in (
            ("projects", mongodb.get_project_records, self.projects),
            ("tenders", mongodb.get_tender_records, self.tenders),
            ("employees", mongodb.get_employee_records, self.employees),
        ):
            with self.subTest(failing=label):
                self.setUp()
                document_id, stored = self.upload()
                project_id = self.add_project(document_id)
                employee_id = self.add_employee(
                    cvs=[{"id": "cv-a", "document_id": document_id}]
                )
                # The nested pointer write is the one that reaches employees;
                # tenders is reached by both the list pull and that write.
                response, log = self._delete_with_failing_clear(
                    document_id, dependency, collection
                )

                self.assertEqual(response.status_code, 500)
                self.assertIn(document_id, log)
                self.assertIn(stored, log)
                self.assertIn("may still", log)
                self.assertIn("simulated database failure", log)  # traceback kept
                self.assertIsNone(
                    self.documents.find_one({"_id": ObjectId(document_id)})
                )
                self.assertFalse(
                    os.path.exists(storage.upload_path(stored)),
                    "file cleanup was skipped after the clear failed",
                )
                # Nothing claims the references were repaired: the ones the
                # failure stopped before reaching are still there.
                if label == "projects":
                    self.assertEqual(
                        self.projects.find_one({"_id": ObjectId(project_id)})[
                            "document_ids"
                        ],
                        [document_id],
                    )
                if label == "employees":
                    self.assertEqual(
                        self.employees.find_one({"_id": ObjectId(employee_id)})["cvs"][0][
                            "document_id"
                        ],
                        document_id,
                    )

    def test_a_failed_reference_clear_with_a_stuck_file_still_raises_the_original(self):
        document_id, stored = self.upload()
        with patch.object(
            storage.os, "remove", side_effect=PermissionError(13, "in use")
        ):
            response, log = self._delete_with_failing_clear(
                document_id, mongodb.get_project_records, self.projects
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn("simulated database failure", log)
        self.assertIn("Could not delete the stored file", log)
        self.assertTrue(os.path.exists(storage.upload_path(stored)))

    def test_delete_clears_the_project_and_tender_document_lists(self):
        document_id, _ = self.upload()
        other_id, _ = self.upload("other.pdf", client_name="Someone Else")
        project_id = self.add_project(document_id)
        self.projects.update_one(
            {"_id": ObjectId(project_id)}, {"$push": {"document_ids": other_id}}
        )
        tender_id = self.add_tender(document_ids=[document_id, other_id])

        self.assertEqual(self.delete(document_id).status_code, 204)
        project = self.projects.find_one({"_id": ObjectId(project_id)})
        tender = self.tenders.find_one({"_id": ObjectId(tender_id)})
        self.assertEqual(project["document_ids"], [other_id])
        self.assertEqual(tender["document_ids"], [other_id])
        self.assertIn("updated_at", project)

    def test_delete_clears_cv_and_certification_pointers(self):
        """The gap the verify scripts never covered.

        A CV and a certification keep their own history -- the version
        label, the issuing body, the dates -- whether or not the file behind
        them still exists. What must go is the pointer, and the copy of the
        file name they carry, which would otherwise show as a file that
        cannot be opened.
        """
        document_id, _ = self.upload()
        other_id, _ = self.upload("other.pdf", client_name="Someone Else")
        employee_id = self.add_employee(
            cvs=[
                # Two versions pointing at the same file: both have to change.
                {"id": "cv-a", "version_label": "v1", "document_id": document_id,
                 "file_name": "cost sheet.pdf"},
                {"id": "cv-b", "version_label": "v2", "document_id": document_id,
                 "file_name": "cost sheet.pdf"},
                {"id": "cv-c", "version_label": "other", "document_id": other_id,
                 "file_name": "other.pdf"},
            ],
            certifications=[
                {"id": "qual-a", "name": "PMP", "issuing_body": "PMI",
                 "document_id": document_id, "file_name": "cost sheet.pdf"},
            ],
        )

        self.assertEqual(self.delete(document_id).status_code, 204)
        employee = self.employees.find_one({"_id": ObjectId(employee_id)})
        self.assertEqual(
            [(cv["document_id"], cv["file_name"]) for cv in employee["cvs"]],
            [(None, None), (None, None), (other_id, "other.pdf")],
        )
        self.assertEqual(
            [cv["version_label"] for cv in employee["cvs"]], ["v1", "v2", "other"]
        )
        certification = employee["certifications"][0]
        self.assertEqual(certification["document_id"], None)
        self.assertEqual(certification["file_name"], None)
        self.assertEqual(certification["name"], "PMP")
        self.assertEqual(certification["issuing_body"], "PMI")

    def test_delete_clears_cost_item_and_submitted_certificate_pointers(self):
        document_id, _ = self.upload()
        tender_id = self.add_tender(
            cost_items=[
                {"id": "cost-a", "cost_type": "EMD", "amount": "50000",
                 "document_id": document_id, "file_name": "cost sheet.pdf"},
            ],
            certificates=[
                {"id": "cert-a", "name": "ISO 9001", "document_id": document_id,
                 "file_name": "cost sheet.pdf"},
            ],
        )

        self.assertEqual(self.delete(document_id).status_code, 204)
        tender = self.tenders.find_one({"_id": ObjectId(tender_id)})
        cost_item = tender["cost_items"][0]
        self.assertEqual((cost_item["document_id"], cost_item["file_name"]), (None, None))
        self.assertEqual(cost_item["amount"], "50000", "the cost item lost its own data")
        certificate = tender["certificates"][0]
        self.assertEqual((certificate["document_id"], certificate["file_name"]), (None, None))
        self.assertEqual(certificate["name"], "ISO 9001")

    def test_an_admin_is_not_blocked_by_references(self):
        """The warning the log shows an Admin is a warning, not a refusal.

        The same document, referenced the same way, is refused to an HOD --
        see test_auth.py. The two together are what says the restriction is
        on the role and not on the state of the document.
        """
        document_id, _ = self.upload()
        self.add_project(document_id)
        self.assertTrue(self.usage(document_id))
        self.assertEqual(self.delete(document_id).status_code, 204)

    # --- redacted copies --------------------------------------------------

    def test_a_saved_redacted_copy_outlives_the_original(self):
        document_id, _ = self.upload()
        copy_id = self.save_redacted_copy(document_id)
        copy_stored = self.documents.find_one({"_id": ObjectId(copy_id)})["stored_file_name"]
        copy_path = storage.upload_path(copy_stored)

        self.assertEqual(self.delete(document_id).status_code, 204)
        copy = self.documents.find_one({"_id": ObjectId(copy_id)})
        self.assertIsNotNone(copy, "the copy was deleted with its original")
        self.assertTrue(os.path.exists(copy_path), "the copy's own file was removed")
        # The link is left as it was: the copy still says what it is, which
        # is the one thing about it worth knowing once the original is gone.
        self.assertEqual(copy["redacted_from_id"], document_id)

    def test_deleting_a_redacted_copy_leaves_the_original_alone(self):
        document_id, stored = self.upload()
        copy_id = self.save_redacted_copy(document_id)

        self.assertEqual(self.delete(copy_id).status_code, 204)
        self.assertIsNotNone(self.documents.find_one({"_id": ObjectId(document_id)}))
        self.assertTrue(os.path.exists(storage.upload_path(stored)))
        self.assertEqual(self.usage(document_id), [])

    # --- deleting from an owner -------------------------------------------

    def test_owner_scoped_delete_is_refused_when_anything_else_uses_it(self):
        document_id, stored = self.upload()
        project_id = self.add_project(document_id)
        self.add_tender(document_ids=[document_id])

        refused = self.delete(document_id, from_project=project_id)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Metro bid", refused.json()["detail"])
        self.assertIn("Remove it from this project instead.", refused.json()["detail"])
        # Refused before anything is written.
        self.assertIsNotNone(self.documents.find_one({"_id": ObjectId(document_id)}))
        self.assertTrue(os.path.exists(storage.upload_path(stored)))
        self.assertEqual(
            self.projects.find_one({"_id": ObjectId(project_id)})["document_ids"],
            [document_id],
        )

    def test_owner_scoped_delete_goes_ahead_when_the_owner_is_the_only_user(self):
        document_id, stored = self.upload()
        project_id = self.add_project(document_id)

        self.assertEqual(self.delete(document_id, from_project=project_id).status_code, 204)
        self.assertIsNone(self.documents.find_one({"_id": ObjectId(document_id)}))
        self.assertFalse(os.path.exists(storage.upload_path(stored)))
        self.assertEqual(
            self.projects.find_one({"_id": ObjectId(project_id)})["document_ids"], []
        )

    def test_a_nested_record_is_refused_while_a_sibling_points_at_the_same_file(self):
        document_id, _ = self.upload()
        employee_id = self.add_employee(
            cvs=[
                {"id": "cv-a", "document_id": document_id},
                {"id": "cv-b", "document_id": document_id},
            ],
        )

        refused = self.delete(document_id, from_employee=employee_id, from_item="cv-a")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Asha Patil", refused.json()["detail"])
        self.assertIsNotNone(self.documents.find_one({"_id": ObjectId(document_id)}))

        # With the sibling gone it is the only user, and the delete goes ahead.
        self.employees.update_one(
            {"_id": ObjectId(employee_id)}, {"$pull": {"cvs": {"id": "cv-b"}}}
        )
        self.assertEqual(
            self.delete(document_id, from_employee=employee_id, from_item="cv-a").status_code,
            204,
        )

    def test_a_redacted_copy_refuses_an_owner_scoped_delete_of_its_original(self):
        document_id, _ = self.upload()
        project_id = self.add_project(document_id)
        self.save_redacted_copy(document_id)

        refused = self.delete(document_id, from_project=project_id)
        self.assertEqual(refused.status_code, 409)
        # The refusal names the copy by its file name. It does not say the
        # word "copy" -- _describe_uses prints a use's kind and label, not
        # its roles -- so the name is what it is matched on here.
        self.assertIn('the document "cost sheet_redacted.pdf"', refused.json()["detail"])

    def test_naming_an_owner_badly_is_refused_before_anything_is_read(self):
        document_id, _ = self.upload()
        for owner, expected in (
            ({"from_item": "cv-a"}, "from_item needs from_tender or from_employee."),
            (
                {"from_project": str(ObjectId()), "from_tender": str(ObjectId())},
                "Name one of from_project, from_tender or from_employee, not several.",
            ),
            (
                {"from_project": str(ObjectId()), "from_item": "cv-a"},
                "from_item cannot be used with from_project.",
            ),
            ({"from_employee": str(ObjectId())}, "from_employee needs from_item."),
        ):
            with self.subTest(owner=owner):
                response = self.delete(document_id, **owner)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"], expected)
        self.assertIsNotNone(self.documents.find_one({"_id": ObjectId(document_id)}))


if __name__ == "__main__":
    unittest.main()
