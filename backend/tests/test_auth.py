import unittest
from datetime import timedelta
from unittest.mock import patch

import mongomock
from bson import ObjectId
from fastapi import Response
from fastapi.testclient import TestClient

from app import auth, main, mongodb, storage
from app.routers import documents_mongo


class AuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_client = mongodb._client
        cls.previous_ping = mongodb.ping
        cls.previous_cookie_secure = auth.COOKIE_SECURE
        mongodb._client = mongomock.MongoClient(tz_aware=True)
        mongodb.ping = lambda: (True, None)
        auth.COOKIE_SECURE = False

        cls.database = mongodb.get_database()
        cls.users = cls.database[mongodb.MONGO_USERS_COLLECTION_NAME]
        cls.sessions = cls.database[mongodb.MONGO_SESSIONS_COLLECTION_NAME]
        main.app.dependency_overrides[mongodb.get_users_collection] = lambda: cls.users
        main.app.dependency_overrides[mongodb.get_sessions_collection] = lambda: cls.sessions
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        main.app.dependency_overrides.clear()
        mongodb._client = cls.previous_client
        mongodb.ping = cls.previous_ping
        auth.COOKIE_SECURE = cls.previous_cookie_secure

    def setUp(self):
        self.client.cookies.clear()
        self.users.delete_many({})
        self.sessions.delete_many({})
        self.database[mongodb.MONGO_COLLECTION_NAME].delete_many({})
        mongodb.get_project_records().delete_many({})
        mongodb.get_employee_records().delete_many({})
        mongodb.get_tender_records().delete_many({})
        # Login attempts in other tests must not count against this test's
        # rate-limit budget -- see test_login_rate_limit.py for the dedicated
        # throttling tests.
        self.database[mongodb.MONGO_LOGIN_THROTTLE_COLLECTION_NAME].delete_many({})
        auth.create_user("admin@example.test", "correct horse battery staple", self.users)

    def login(self, username="admin@example.test", password="correct horse battery staple"):
        return self.client.post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"Origin": "http://localhost:5173"},
        )

    def test_login_returns_public_user_and_httponly_session_cookie(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["username"], "admin@example.test")
        self.assertEqual(response.json()["user"]["role"], "Admin")
        self.assertNotIn("password_hash", response.json())
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        stored_user = self.users.find_one({"username": "admin@example.test"})
        self.assertNotEqual(stored_user["password_hash"], "correct horse battery staple")
        self.assertTrue(stored_user["password_hash"].startswith("pbkdf2_sha256$"))
        raw_cookie = response.cookies.get(auth.COOKIE_NAME)
        stored_session = next(self.sessions.find({}))
        self.assertNotEqual(stored_session["token_hash"], raw_cookie)
        self.assertNotIn("token", stored_session)
        self.assertEqual(response.json()["csrf_token"], self.client.get("/auth/me").json()["csrf_token"])

    def test_invalid_and_unknown_credentials_have_same_response(self):
        wrong_password = self.login(password="not the password")
        unknown_user = self.login(username="missing@example.test")
        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(wrong_password.json(), unknown_user.json())

    def test_disabled_account_cannot_login(self):
        self.users.update_one(
            {"username": "admin@example.test"}, {"$set": {"is_active": False}}
        )
        response = self.login()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], auth.GENERIC_LOGIN_ERROR)

    def test_https_session_cookie_is_secure(self):
        secure_client = TestClient(main.app, base_url="https://testserver")
        previous_secure = auth.COOKIE_SECURE
        auth.COOKIE_SECURE = True
        try:
            response = secure_client.post(
                "/auth/login",
                json={
                    "username": "admin@example.test",
                    "password": "correct horse battery staple",
                },
                headers={"Origin": "https://testserver"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("secure", response.headers["set-cookie"].lower())
            self.assertEqual(secure_client.get("/auth/me").status_code, 200)
        finally:
            secure_client.close()
            auth.COOKIE_SECURE = previous_secure

    def test_application_apis_require_auth_and_accept_authenticated_requests(self):
        paths = ("/documents/", "/projects/", "/employees/", "/tenders/")
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

        for method, path in (
            ("post", "/documents/"),
            ("get", "/documents/not-a-real-id/file"),
            ("delete", "/documents/not-a-real-id"),
            ("post", "/projects/"),
            ("post", "/employees/"),
            ("post", "/tenders/"),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 401)

        self.assertEqual(self.login().status_code, 200)
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        missing_csrf = self.client.post("/projects/", json={})
        self.assertEqual(missing_csrf.status_code, 403)
        invalid_csrf = self.client.post(
            "/projects/", json={}, headers={"X-CSRF-Token": "invalid"}
        )
        self.assertEqual(invalid_csrf.status_code, 403)
        csrf_token = self.client.get("/auth/me").json()["csrf_token"]
        accepted_csrf = self.client.post(
            "/projects/",
            json={},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(accepted_csrf.status_code, 422)

    def test_role_matrix_and_direct_api_access(self):
        auth.create_user("hod@example.test", "correct horse battery staple", self.users, role="HOD")
        auth.create_user(
            "normal@example.test",
            "correct horse battery staple",
            self.users,
            role="Normal User",
        )
        csrf = self.login().json()["csrf_token"]

        # Admin retains access to all four application modules.
        for path in ("/documents/", "/projects/", "/employees/", "/tenders/"):
            with self.subTest(role="Admin", path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        # HOD can use document operations but cannot read the other modules.
        self.client.cookies.clear()
        self.assertEqual(self.login("hod@example.test").status_code, 200)
        hod_csrf = self.client.get("/auth/me").json()["csrf_token"]
        for path in ("/documents/", "/documents/types", "/documents/categories"):
            with self.subTest(role="HOD", path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        for path in ("/projects/", "/employees/", "/tenders/"):
            with self.subTest(role="HOD", path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        for path in (
            "/projects/",
            "/employees/",
            "/tenders/",
            f"/employees/{ObjectId()}/cvs",
            f"/tenders/{ObjectId()}/documents/{ObjectId()}",
        ):
            with self.subTest(role="HOD", write_path=path):
                self.assertEqual(
                    self.client.post(
                        path,
                        json={},
                        headers={"X-CSRF-Token": hod_csrf},
                    ).status_code,
                    403,
                )

        # Normal User can only upload and cannot enumerate or retrieve files.
        self.client.cookies.clear()
        self.assertEqual(self.login("normal@example.test").status_code, 200)
        normal_csrf = self.client.get("/auth/me").json()["csrf_token"]
        for path in (
            "/documents/",
            "/documents/types",
            "/documents/categories",
            "/projects/",
            "/employees/",
            "/tenders/",
        ):
            with self.subTest(role="Normal User", path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        for path in (
            "/projects/",
            "/employees/",
            "/tenders/",
            f"/employees/{ObjectId()}/cvs",
            f"/tenders/{ObjectId()}/documents/{ObjectId()}",
        ):
            with self.subTest(role="Normal User", write_path=path):
                self.assertEqual(
                    self.client.post(
                        path,
                        json={},
                        headers={"X-CSRF-Token": normal_csrf},
                    ).status_code,
                    403,
                )

        upload = self.client.post(
            "/documents/",
            data={"document_type": "Other", "category": "General"},
            headers={"X-CSRF-Token": normal_csrf},
        )
        self.assertEqual(upload.status_code, 201)
        document_id = upload.json()["id"]
        for method, path in (
            ("get", f"/documents/{document_id}"),
            ("get", f"/documents/{document_id}/file"),
            ("get", f"/documents/{document_id}/redaction/source"),
            ("post", f"/documents/{document_id}/redacted-copy"),
            ("put", f"/documents/{document_id}"),
            ("delete", f"/documents/{document_id}"),
        ):
            with self.subTest(role="Normal User", method=method, path=path):
                response = getattr(self.client, method)(
                    path, headers={"X-CSRF-Token": normal_csrf}
                )
                self.assertEqual(response.status_code, 403)

        # A document-upload endpoint cannot be used as an indirect project edit.
        indirect = self.client.post(
            "/documents/",
            data={"document_type": "Other", "category": "General", "project_id": str(ObjectId())},
            headers={"X-CSRF-Token": normal_csrf},
        )
        self.assertEqual(indirect.status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.login().status_code, 200)
        self.assertTrue(csrf)

    def test_hod_metadata_edit_file_replacement_redaction_and_delete_rules(self):
        csrf = self.login().json()["csrf_token"]
        original = self.client.post(
            "/documents/",
            data={"document_type": "Other", "category": "General", "client_name": "Before"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(original.status_code, 201)
        document_id = original.json()["id"]

        auth.create_user("hod@example.test", "correct horse battery staple", self.users, role="HOD")
        self.client.cookies.clear()
        self.assertEqual(self.login("hod@example.test").status_code, 200)
        hod_csrf = self.client.get("/auth/me").json()["csrf_token"]

        metadata = self.client.put(
            f"/documents/{document_id}",
            data={"document_type": "Other", "category": "General", "client_name": "After"},
            headers={"X-CSRF-Token": hod_csrf},
        )
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(metadata.json()["client_name"], "After")

        replacement = self.client.put(
            f"/documents/{document_id}",
            data={"document_type": "Other", "category": "General"},
            files={"document_file": ("replacement.pdf", b"%PDF-test", "application/pdf")},
            headers={"X-CSRF-Token": hod_csrf},
        )
        self.assertEqual(replacement.status_code, 403)

        with patch(
            "app.routers.documents_mongo._redaction_source",
            return_value={"_id": ObjectId(document_id), "stored_file_name": "source.pdf"},
        ), patch(
            "app.detection.analyse",
            return_value={
                "kind": "pdf",
                "engine": "text",
                "ocr_available": False,
                "detections": [],
                "pages_scanned": 1,
                "text_pages": [0],
                "ocr_pages": [],
                "pages_without_text": [],
                "pages_skipped": [],
                "truncated": False,
                "message": "No figures found.",
            },
        ), patch(
            "app.redaction.redacted_copy_response",
            return_value=Response(content=b"redacted"),
        ), patch(
            "app.redaction.redact",
            return_value=(b"redacted-pdf", "application/pdf"),
        ), patch(
            "app.redaction.redacted_download_name",
            return_value="source_redacted.pdf",
        ), patch.object(
            storage,
            "write_upload",
            return_value="stored_redacted.pdf",
        ), patch.object(
            storage,
            "remove_stored_file",
        ):
            suggestions = self.client.get(f"/documents/{document_id}/redaction/suggestions")
            generated = self.client.post(
                f"/documents/{document_id}/redacted-copy",
                json={"areas": [{"page": 0, "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}]},
                headers={"X-CSRF-Token": hod_csrf},
            )
            saved = self.client.post(
                f"/documents/{document_id}/redacted-copy/save",
                json={"areas": [{"page": 0, "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}]},
                headers={"X-CSRF-Token": hod_csrf},
            )
        self.assertEqual(suggestions.status_code, 200)
        self.assertEqual(generated.status_code, 200)
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(saved.json()["redacted_from_id"], document_id)
        redacted_id = saved.json()["id"]

        # Referenced deletion fails even when the caller omits owner context.
        project_id = ObjectId()
        projects = mongodb.get_project_records()
        projects.insert_one({"_id": project_id, "title": "Linked", "document_ids": [document_id]})
        referenced = self.client.delete(
            f"/documents/{document_id}",
            headers={"X-CSRF-Token": hod_csrf},
        )
        self.assertEqual(referenced.status_code, 409)
        self.assertIsNotNone(self.database[mongodb.MONGO_COLLECTION_NAME].find_one({"_id": ObjectId(document_id)}))

        projects.delete_one({"_id": project_id})
        with patch.object(storage, "remove_stored_file"), patch(
            "app.routers.documents_mongo._set_nested_pointer_fields"
        ):
            blocked_by_redacted_copy = self.client.delete(
                f"/documents/{document_id}",
                headers={"X-CSRF-Token": hod_csrf},
            )
            self.assertEqual(blocked_by_redacted_copy.status_code, 409)
            delete_copy = self.client.delete(
                f"/documents/{redacted_id}",
                headers={"X-CSRF-Token": hod_csrf},
            )
            self.assertEqual(delete_copy.status_code, 204)
            deleted = self.client.delete(
                f"/documents/{document_id}",
                headers={"X-CSRF-Token": hod_csrf},
            )
        self.assertEqual(deleted.status_code, 204)

    def test_upload_cannot_link_admin_only_records_for_hod(self):
        auth.create_user("hod@example.test", "correct horse battery staple", self.users, role="HOD")
        self.login("hod@example.test")
        csrf = self.client.get("/auth/me").json()["csrf_token"]
        response = self.client.post(
            "/documents/",
            data={"document_type": "Other", "category": "General", "project_id": str(ObjectId())},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.database[mongodb.MONGO_COLLECTION_NAME].count_documents({}), 0)

    def test_hod_cannot_delete_tender_or_employee_referenced_documents(self):
        csrf = self.login().json()["csrf_token"]
        document_ids = {}
        for case in (
            "tender_attachment",
            "tender_cost_receipt",
            "tender_certificate",
            "employee_cv",
            "employee_certification",
            "tender_owner_pointer",
        ):
            response = self.client.post(
                "/documents/",
                data={"document_type": "Other", "category": "General", "client_name": case},
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(response.status_code, 201)
            document_ids[case] = response.json()["id"]

        tenders = mongodb.get_tender_records()
        employees = mongodb.get_employee_records()
        tenders.insert_one({
            "_id": ObjectId(),
            "title": "Referencing tender",
            "document_ids": [document_ids["tender_attachment"]],
            "cost_items": [{"id": str(ObjectId()), "document_id": document_ids["tender_cost_receipt"]}],
            "certificates": [{"id": str(ObjectId()), "document_id": document_ids["tender_certificate"]}],
        })
        employees.insert_one({
            "_id": ObjectId(),
            "full_name": "Referencing employee",
            "cvs": [{"id": str(ObjectId()), "document_id": document_ids["employee_cv"]}],
            "certifications": [{"id": str(ObjectId()), "document_id": document_ids["employee_certification"]}],
        })
        # Linked only from the document's side: the tender does not list it.
        owner_tender_id = ObjectId()
        tenders.insert_one({"_id": owner_tender_id, "title": "Owner tender", "document_ids": []})
        self.database[mongodb.MONGO_COLLECTION_NAME].update_one(
            {"_id": ObjectId(document_ids["tender_owner_pointer"])},
            {"$set": {"tender_id": str(owner_tender_id)}},
        )

        auth.create_user("hod@example.test", "correct horse battery staple", self.users, role="HOD")
        self.client.cookies.clear()
        self.assertEqual(self.login("hod@example.test").status_code, 200)
        hod_csrf = self.client.get("/auth/me").json()["csrf_token"]

        documents = self.database[mongodb.MONGO_COLLECTION_NAME]
        for case, document_id in document_ids.items():
            with self.subTest(case=case):
                response = self.client.delete(
                    f"/documents/{document_id}",
                    headers={"X-CSRF-Token": hod_csrf},
                )
                self.assertEqual(response.status_code, 409)
                self.assertIsNotNone(documents.find_one({"_id": ObjectId(document_id)}))

    def test_hod_cannot_create_or_modify_users(self):
        hod_id = auth.create_user(
            "hod@example.test",
            "correct horse battery staple",
            self.users,
            role="HOD",
        )
        self.login("hod@example.test")
        headers = {"X-CSRF-Token": self.client.get("/auth/me").json()["csrf_token"]}

        created = self.client.post(
            "/auth/users",
            json={
                "username": "new@example.test",
                "password": "initial-password-123",
                "role": "Admin",
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 403)
        self.assertIsNone(self.users.find_one({"username": "new@example.test"}))

        promoted = self.client.patch(
            f"/auth/users/{hod_id}",
            json={"role": "Admin"},
            headers=headers,
        )
        self.assertEqual(promoted.status_code, 403)
        self.assertEqual(self.users.find_one({"_id": hod_id})["role"], "HOD")

    def test_hod_can_read_single_document_and_usage(self):
        auth.create_user("hod@example.test", "correct horse battery staple", self.users, role="HOD")
        self.login("hod@example.test")
        csrf = self.client.get("/auth/me").json()["csrf_token"]

        upload = self.client.post(
            "/documents/",
            data={"document_type": "Other", "category": "General", "client_name": "HOD upload"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(upload.status_code, 201)
        document_id = upload.json()["id"]

        single = self.client.get(f"/documents/{document_id}")
        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.json()["client_name"], "HOD upload")

        usage = self.client.get(f"/documents/{document_id}/usage")
        self.assertEqual(usage.status_code, 200)
        self.assertEqual(usage.json(), [])

    def test_normal_user_blocked_from_remaining_document_and_user_routes(self):
        csrf = self.login().json()["csrf_token"]
        original = self.client.post(
            "/documents/",
            data={"document_type": "Other", "category": "General", "client_name": "Before"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(original.status_code, 201)
        document_id = original.json()["id"]
        admin = self.users.find_one({"username": "admin@example.test"})
        admin_sessions = self.sessions.count_documents({"user_id": admin["_id"]})

        normal_id = auth.create_user(
            "normal@example.test",
            "correct horse battery staple",
            self.users,
            role="Normal User",
        )
        self.client.cookies.clear()
        self.assertEqual(self.login("normal@example.test").status_code, 200)
        headers = {"X-CSRF-Token": self.client.get("/auth/me").json()["csrf_token"]}
        areas = {"areas": [{"page": 0, "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}]}

        with patch.object(storage, "write_upload") as write_upload:
            for method, path, kwargs in (
                ("get", f"/documents/{document_id}/redaction/pages/0", {}),
                ("get", f"/documents/{document_id}/redaction/suggestions", {}),
                ("post", f"/documents/{document_id}/redacted-copy/save", {"json": areas}),
                ("get", f"/documents/{document_id}/usage", {}),
                (
                    "put",
                    f"/documents/{document_id}",
                    {
                        "data": {"document_type": "Other", "category": "General", "client_name": "After"},
                        "files": {"document_file": ("replacement.pdf", b"%PDF-test", "application/pdf")},
                    },
                ),
            ):
                with self.subTest(method=method, path=path):
                    response = getattr(self.client, method)(path, headers=headers, **kwargs)
                    self.assertEqual(response.status_code, 403)
            write_upload.assert_not_called()
        documents = self.database[mongodb.MONGO_COLLECTION_NAME]
        self.assertEqual(documents.count_documents({}), 1)
        self.assertEqual(documents.find_one({"_id": ObjectId(document_id)})["client_name"], "Before")

        self.assertEqual(self.client.get("/auth/users").status_code, 403)
        created = self.client.post(
            "/auth/users",
            json={
                "username": "new@example.test",
                "password": "initial-password-123",
                "role": "Admin",
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 403)
        self.assertIsNone(self.users.find_one({"username": "new@example.test"}))

        promoted = self.client.patch(
            f"/auth/users/{normal_id}",
            json={"role": "Admin"},
            headers=headers,
        )
        self.assertEqual(promoted.status_code, 403)
        self.assertEqual(self.users.find_one({"_id": normal_id})["role"], "Normal User")

        revoked = self.client.delete(f"/auth/users/{admin['_id']}/sessions", headers=headers)
        self.assertEqual(revoked.status_code, 403)
        self.assertEqual(self.sessions.count_documents({"user_id": admin["_id"]}), admin_sessions)

    def test_normal_user_upload_cannot_set_link_fields(self):
        auth.create_user(
            "normal@example.test",
            "correct horse battery staple",
            self.users,
            role="Normal User",
        )
        self.login("normal@example.test")
        headers = {"X-CSRF-Token": self.client.get("/auth/me").json()["csrf_token"]}
        documents = self.database[mongodb.MONGO_COLLECTION_NAME]

        upload = self.client.post(
            "/documents/",
            data={
                "document_type": "Other",
                "category": "General",
                "tender_id": str(ObjectId()),
                "employee_id": str(ObjectId()),
                "redacted_from_id": str(ObjectId()),
            },
            headers=headers,
        )
        self.assertEqual(upload.status_code, 201)
        stored = documents.find_one({"_id": ObjectId(upload.json()["id"])})
        for field in ("project_id", "tender_id", "employee_id", "redacted_from_id"):
            with self.subTest(field=field):
                self.assertIsNone(stored[field])

        project_id = ObjectId()
        mongodb.get_project_records().insert_one(
            {"_id": project_id, "title": "Restricted", "document_ids": []}
        )
        linked = self.client.post(
            "/documents/",
            data={"document_type": "Other", "category": "General", "project_id": str(project_id)},
            headers=headers,
        )
        self.assertEqual(linked.status_code, 403)
        self.assertEqual(documents.count_documents({}), 1)
        self.assertEqual(
            mongodb.get_project_records().find_one({"_id": project_id})["document_ids"], []
        )

    def test_disabled_or_invalid_session_rejected(self):
        self.assertEqual(self.client.get("/auth/users").status_code, 401)

        normal_id = auth.create_user(
            "normal@example.test",
            "correct horse battery staple",
            self.users,
            role="Normal User",
        )
        self.assertEqual(self.login("normal@example.test").status_code, 200)
        self.assertEqual(self.client.get("/auth/me").status_code, 200)
        # Disabled directly, not through the API, so no session is revoked for it.
        self.users.update_one({"_id": normal_id}, {"$set": {"is_active": False}})
        self.assertEqual(self.client.get("/documents/").status_code, 401)
        self.assertEqual(self.sessions.count_documents({"user_id": normal_id}), 0)

        self.client.cookies.clear()
        self.client.cookies.set(auth.COOKIE_NAME, "forged-session-token")
        for path in ("/auth/me", "/documents/", "/auth/users"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_duplicate_upload_message_hides_existing_record_metadata(self):
        contents = b"%PDF-duplicate-test"
        secrets = ("confidential-name.pdf", "Secret Contract", "Secret Client", "Secret Project")
        csrf = self.login().json()["csrf_token"]
        with patch.object(storage, "write_upload", return_value="stored_original.pdf"):
            original = self.client.post(
                "/documents/",
                data={
                    "document_type": "Secret Contract",
                    "category": "General",
                    "client_name": "Secret Client",
                    "project_title": "Secret Project",
                },
                files={"document_file": ("confidential-name.pdf", contents, "application/pdf")},
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(original.status_code, 201)
        other = self.client.post(
            "/documents/",
            data={"document_type": "Other", "category": "General"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(other.status_code, 201)
        documents = self.database[mongodb.MONGO_COLLECTION_NAME]

        auth.create_user("hod@example.test", "correct horse battery staple", self.users, role="HOD")
        auth.create_user(
            "normal@example.test",
            "correct horse battery staple",
            self.users,
            role="Normal User",
        )
        for username in ("admin@example.test", "hod@example.test", "normal@example.test"):
            self.client.cookies.clear()
            self.assertEqual(self.login(username).status_code, 200)
            headers = {"X-CSRF-Token": self.client.get("/auth/me").json()["csrf_token"]}
            with patch.object(storage, "write_upload") as write_upload:
                duplicate = self.client.post(
                    "/documents/",
                    data={"document_type": "Other", "category": "General"},
                    files={"document_file": ("other-name.pdf", contents, "application/pdf")},
                    headers=headers,
                )
                write_upload.assert_not_called()
            with self.subTest(username=username):
                self.assertEqual(duplicate.status_code, 409)
                self.assertEqual(duplicate.json()["detail"], documents_mongo.DUPLICATE_UPLOAD_MESSAGE)
                for secret in secrets:
                    self.assertNotIn(secret, duplicate.text)
        self.assertEqual(documents.count_documents({}), 2)

        # A replacement with the same file is refused the same way.
        self.client.cookies.clear()
        self.login()
        headers = {"X-CSRF-Token": self.client.get("/auth/me").json()["csrf_token"]}
        with patch.object(storage, "write_upload") as write_upload:
            replacement = self.client.put(
                f"/documents/{other.json()['id']}",
                data={"document_type": "Other", "category": "General"},
                files={"document_file": ("other-name.pdf", contents, "application/pdf")},
                headers=headers,
            )
            write_upload.assert_not_called()
        self.assertEqual(replacement.status_code, 409)
        self.assertEqual(replacement.json()["detail"], documents_mongo.DUPLICATE_UPLOAD_MESSAGE)
        for secret in secrets:
            self.assertNotIn(secret, replacement.text)
        self.assertIsNone(documents.find_one({"_id": ObjectId(other.json()["id"])})["file_hash"])

    def test_login_rejects_missing_or_foreign_origin(self):
        credentials = {"username": "admin@example.test", "password": "correct horse battery staple"}
        missing = self.client.post("/auth/login", json=credentials)
        self.assertEqual(missing.status_code, 403)
        foreign = self.client.post(
            "/auth/login",
            json=credentials,
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(foreign.status_code, 403)
        self.assertNotIn(auth.COOKIE_NAME, foreign.cookies)
        self.assertEqual(self.sessions.count_documents({}), 0)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_session_cookie_attributes_and_logout_clears_cookie(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn(f"{auth.COOKIE_NAME}=", cookie)
        self.assertIn(f"samesite={auth.COOKIE_SAMESITE}", cookie)
        self.assertIn(f"max-age={auth.SESSION_HOURS * 60 * 60}", cookie)
        self.assertIn("path=/", cookie)
        self.assertIn("httponly", cookie)
        csrf = response.json()["csrf_token"]

        without_csrf = self.client.post("/auth/logout")
        self.assertEqual(without_csrf.status_code, 403)
        self.assertEqual(self.sessions.count_documents({}), 1)
        self.assertEqual(self.client.get("/auth/me").status_code, 200)

        logout = self.client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(logout.status_code, 204)
        cleared = logout.headers["set-cookie"].lower()
        self.assertIn(f"{auth.COOKIE_NAME}=", cleared)
        self.assertIn("max-age=0", cleared)
        self.assertEqual(self.sessions.count_documents({}), 0)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_role_change_revokes_existing_sessions(self):
        target_id = auth.create_user(
            "hod@example.test",
            "correct horse battery staple",
            self.users,
            role="HOD",
        )
        target_token = self.login("hod@example.test").cookies.get(auth.COOKIE_NAME)
        self.assertTrue(target_token)
        self.client.cookies.clear()
        self.client.cookies.set(auth.COOKIE_NAME, target_token)
        self.assertEqual(self.client.get("/auth/me").status_code, 200)

        self.client.cookies.clear()
        csrf = self.login().json()["csrf_token"]
        changed = self.client.patch(
            f"/auth/users/{target_id}",
            json={"role": "Normal User"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["role"], "Normal User")
        self.assertTrue(changed.json()["is_active"])
        self.assertEqual(changed.json()["revoked_sessions"], 1)
        self.assertEqual(self.sessions.count_documents({"user_id": target_id}), 0)

        self.client.cookies.clear()
        self.client.cookies.set(auth.COOKIE_NAME, target_token)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_account_creation_rejects_short_password(self):
        csrf = self.login().json()["csrf_token"]
        short = self.client.post(
            "/auth/users",
            json={"username": "short@example.test", "password": "a" * 11, "role": "Normal User"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(short.status_code, 422)
        self.assertIsNone(self.users.find_one({"username": "short@example.test"}))

        minimum = self.client.post(
            "/auth/users",
            json={"username": "minimum@example.test", "password": "a" * 12, "role": "Normal User"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(minimum.status_code, 201)
        self.assertIsNotNone(self.users.find_one({"username": "minimum@example.test"}))

    def test_admin_account_management_role_validation_and_last_admin_safety(self):
        self.login()
        csrf = self.client.get("/auth/me").json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}

        created = self.client.post(
            "/auth/users",
            json={
                "username": "hod@example.test",
                "password": "initial-password-123",
                "role": "HOD",
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 201)
        self.assertNotIn("password_hash", created.json())
        self.assertNotIn("password_hash", self.client.get("/auth/users").text)

        invalid = self.client.post(
            "/auth/users",
            json={
                "username": "bad-role@example.test",
                "password": "initial-password-123",
                "role": "Owner",
            },
            headers=headers,
        )
        self.assertEqual(invalid.status_code, 422)

        admin = self.users.find_one({"username": "admin@example.test"})
        for changes in ({"is_active": False}, {"role": "HOD"}):
            response = self.client.patch(
                f"/auth/users/{admin['_id']}",
                json=changes,
                headers=headers,
            )
            self.assertEqual(response.status_code, 409)

        target_id = ObjectId(created.json()["id"])
        self.sessions.insert_one({"user_id": target_id, "token_hash": "test-session"})
        disabled = self.client.patch(
            f"/auth/users/{target_id}",
            json={"is_active": False},
            headers=headers,
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.json()["is_active"])
        self.assertEqual(self.sessions.count_documents({"user_id": target_id}), 0)

        enabled = self.client.patch(
            f"/auth/users/{target_id}",
            json={"is_active": True, "role": "Normal User"},
            headers=headers,
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertEqual(enabled.json()["role"], "Normal User")

    def test_admin_session_revocation_and_management_access_denials(self):
        target_id = auth.create_user(
            "hod@example.test",
            "correct horse battery staple",
            self.users,
            role="HOD",
        )
        self.sessions.insert_one({"user_id": target_id, "token_hash": "test-session"})
        self.login("hod@example.test")
        csrf = self.client.get("/auth/me").json()["csrf_token"]
        self.assertEqual(self.client.get("/auth/users").status_code, 403)
        self.assertEqual(
            self.client.delete(
                f"/auth/users/{target_id}/sessions",
                headers={"X-CSRF-Token": csrf},
            ).status_code,
            403,
        )

        self.client.cookies.clear()
        self.login()
        csrf = self.client.get("/auth/me").json()["csrf_token"]
        response = self.client.delete(
            f"/auth/users/{target_id}/sessions",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.sessions.count_documents({"user_id": target_id}), 0)

    def test_logout_revokes_session_and_expiry_is_enforced(self):
        response = self.login()
        csrf_token = response.json()["csrf_token"]
        logout = self.client.post(
            "/auth/logout", headers={"X-CSRF-Token": csrf_token}
        )
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

        response = self.login()
        token_hash = next(self.sessions.find({}))["token_hash"]
        self.sessions.update_one(
            {"token_hash": token_hash},
            {"$set": {"expires_at": auth._now() - timedelta(seconds=1)}},
        )
        self.assertEqual(self.client.get("/auth/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
