"""What a client-supplied file name may do once it is in the system.

The name a person sees (`file_name`) comes straight from the upload, and was
stored, copied into employee and tender records, repeated in error messages
and put into Content-Disposition headers exactly as sent. These tests pin the
cleaning in storage.safe_display_name and the one header builder every
download now goes through.

Everything runs against mongomock with UPLOAD_DIR pointed at a temporary
directory, and the real uploads folder is fingerprinted before and after.
"""

import gc
import io
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

import mongomock
import pymupdf
from bson import ObjectId
from fastapi.testclient import TestClient

from app import auth, main, mongodb, redaction, schemas, storage
from test_document_deletion import _ArrayFilterCollection

AREA = {"page": 0, "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.3}
RLO = "\u202e"


def _pdf(text="CONTRACT VALUE 1,250,000") -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


def _fingerprint_real_uploads() -> list:
    folder = os.path.join(os.path.dirname(os.path.abspath(storage.__file__)), "uploads")
    if not os.path.isdir(folder):
        return []
    return sorted(
        (name, os.path.getsize(os.path.join(folder, name))) for name in os.listdir(folder)
    )


def _only_safe_header_characters(value: str) -> bool:
    return all(" " <= char <= "~" for char in value)


class SafeDisplayNameTests(unittest.TestCase):
    def test_an_ordinary_name_is_unchanged(self):
        for name in ("cost sheet.pdf", "Q3_report-v2.PNG", "a.b.c.jpeg"):
            self.assertEqual(storage.safe_display_name(name), name)

    def test_path_segments_are_dropped_whichever_way_they_are_written(self):
        cases = {
            "../../etc/passwd.pdf": "passwd.pdf",
            "..\\..\\windows\\evil.pdf": "evil.pdf",
            "C:\\Users\\me\\scan.png": "scan.png",
            "/var/tmp/a.pdf": "a.pdf",
            "mixed/..\\seg/file.jpg": "file.jpg",
            "folder/": "folder",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(storage.safe_display_name(raw), expected)

    def test_control_characters_are_removed(self):
        self.assertEqual(storage.safe_display_name("a\r\nX-Evil: 1.pdf"), "aX-Evil: 1.pdf")
        self.assertEqual(storage.safe_display_name("t\tab\x00\x07\x7f.pdf"), "tab.pdf")
        self.assertEqual(storage.safe_display_name("x\u2028y\u2029z.pdf"), "xyz.pdf")

    def test_unicode_formatting_characters_are_removed(self):
        self.assertEqual(storage.safe_display_name(f"invoice{RLO}fdp.exe"), "invoicefdp.exe")
        self.assertEqual(storage.safe_display_name("a\u200bb\u200d.pdf"), "ab.pdf")
        self.assertEqual(storage.safe_display_name("\ufeffreport.pdf"), "report.pdf")

    def test_legitimate_unicode_is_kept(self):
        for name in ("résumé.pdf", "報告書.pdf", "отчёт 2025.jpg", "बिल.png", "📄 scan.png"):
            with self.subTest(name=name):
                self.assertEqual(storage.safe_display_name(name), name)

    def test_unicode_is_normalised_so_the_same_name_is_one_name(self):
        decomposed = "re\u0301sume\u0301.pdf"
        self.assertEqual(storage.safe_display_name(decomposed), "r\u00e9sum\u00e9.pdf")

    def test_whitespace_and_trailing_dots_are_trimmed(self):
        self.assertEqual(storage.safe_display_name("  scan.pdf  "), "scan.pdf")
        self.assertEqual(storage.safe_display_name("scan.pdf. . "), "scan.pdf")

    def test_nothing_usable_becomes_the_fallback(self):
        for raw in (None, "", "   ", "/", "\\\\", "..", ".", "\x00\x01", RLO, "a/.."):
            with self.subTest(raw=raw):
                self.assertEqual(storage.safe_display_name(raw), "document")
        self.assertEqual(storage.safe_display_name("", default="x.pdf"), "x.pdf")

    def test_a_long_name_is_capped_and_keeps_its_extension(self):
        result = storage.safe_display_name("a" * 400 + ".pdf")
        self.assertEqual(len(result), storage.MAX_DISPLAY_NAME_LENGTH)
        self.assertTrue(result.endswith(".pdf"))
        self.assertTrue(storage.allowed_file(result))

    def test_a_long_name_without_a_real_extension_is_just_cut(self):
        result = storage.safe_display_name("b" * 300)
        self.assertEqual(len(result), storage.MAX_DISPLAY_NAME_LENGTH)
        result = storage.safe_display_name("c" * 100 + "." + "d" * 100)
        self.assertLessEqual(len(result), storage.MAX_DISPLAY_NAME_LENGTH)

    def test_a_name_at_the_limit_is_untouched(self):
        name = "e" * (storage.MAX_DISPLAY_NAME_LENGTH - 4) + ".pdf"
        self.assertEqual(storage.safe_display_name(name), name)


class AllowedFileTests(unittest.TestCase):
    def test_the_four_types_are_accepted_in_any_case(self):
        for name in ("a.pdf", "a.PDF", "a.png", "a.jpg", "a.JPeG", ".pdf", "a.b.pdf"):
            with self.subTest(name=name):
                self.assertTrue(storage.allowed_file(name))

    def test_everything_else_is_refused(self):
        for name in (None, "", "pdf", "a.exe", "a.pdf.exe", "a.gif", "a.", "a.pdf ", "a.pdf\n"):
            with self.subTest(name=name):
                self.assertFalse(storage.allowed_file(name))


class ContentDispositionHeaderTests(unittest.TestCase):
    def header(self, name, disposition="attachment"):
        return redaction.content_disposition_header(name, disposition)

    def test_a_plain_name_has_both_forms(self):
        self.assertEqual(
            self.header("cost sheet.pdf"),
            "attachment; filename=\"cost sheet.pdf\"; filename*=UTF-8''cost%20sheet.pdf",
        )

    def test_inline_is_honoured_and_anything_else_is_an_attachment(self):
        self.assertTrue(self.header("a.pdf", "inline").startswith("inline;"))
        self.assertTrue(self.header("a.pdf", "attachment").startswith("attachment;"))
        self.assertTrue(self.header("a.pdf", "x; filename=evil").startswith("attachment;"))

    def test_non_ascii_names_keep_the_real_name_and_get_an_ascii_fallback(self):
        header = self.header("résumé.pdf")
        self.assertIn('filename="resume.pdf"', header)
        self.assertIn("filename*=UTF-8''r%C3%A9sum%C3%A9.pdf", header)

    def test_a_name_with_no_ascii_alphanumerics_falls_back_to_download(self):
        header = self.header("報告書")
        self.assertIn('filename="download"', header)
        self.assertIn(f"filename*=UTF-8''{'%E5%A0%B1%E5%91%8A%E6%9B%B8'}", header)

    def test_hostile_names_cannot_inject_into_the_header(self):
        hostile = [
            'q"uote.pdf',
            "a\r\nX-Evil: 1.pdf",
            "a\x00b\x08c\x7f.pdf",
            "../../x.pdf",
            "..\\..\\x.pdf",
            "semi;colon=1.pdf",
            "100%25.pdf",
            f"fdp{RLO}exe.pdf",
            "x" * 5000 + ".pdf",
        ]
        for name in hostile:
            with self.subTest(name=name[:30]):
                header = self.header(name)
                self.assertTrue(_only_safe_header_characters(header), repr(header))
                self.assertNotIn("\r", header)
                self.assertNotIn("\n", header)
                fallback = header.split('filename="', 1)[1].split('"; filename*=', 1)[0]
                for forbidden in ('"', "\\", "/", ";", "%"):
                    self.assertNotIn(forbidden, fallback)
                self.assertNotIn("/", header.split("filename*=UTF-8''", 1)[1])
                self.assertLessEqual(len(header), 700)

    def test_the_encoded_name_decodes_to_the_cleaned_name(self):
        encoded = self.header("dir/résumé 2025.pdf").split("filename*=UTF-8''", 1)[1]
        self.assertEqual(unquote(encoded), "résumé 2025.pdf")

    def test_a_missing_name_still_gives_a_usable_header(self):
        self.assertIn('filename="download"', self.header(None))
        self.assertIn('filename="download"', self.header(""))


class RedactedDownloadNameTests(unittest.TestCase):
    def test_the_suffix_goes_before_the_extension(self):
        self.assertEqual(redaction.redacted_download_name("invoice.pdf", "s.pdf"), "invoice_redacted.pdf")

    def test_an_unclean_stored_name_is_cleaned_first(self):
        self.assertEqual(
            redaction.redacted_download_name(f"..\\x\\inv{RLO}oice.pdf", "s.pdf"),
            "invoice_redacted.pdf",
        )

    def test_the_whole_name_stays_within_the_length_limit(self):
        limit = storage.MAX_DISPLAY_NAME_LENGTH
        for raw in ("a" * 400 + ".pdf", "a" * limit + ".pdf", "a" * (limit - 4) + ".pdf", "b" * 400):
            with self.subTest(length=len(raw)):
                result = redaction.redacted_download_name(raw, "s.pdf")
                self.assertLessEqual(len(result), limit)
                self.assertIn("_redacted", result)
                if raw.endswith(".pdf"):
                    self.assertTrue(result.endswith("_redacted.pdf"))

    def test_a_name_that_fits_is_not_shortened(self):
        short = "d" * 100 + ".pdf"
        self.assertEqual(redaction.redacted_download_name(short, "s.pdf"), "d" * 100 + "_redacted.pdf")

    def test_an_unusually_long_tail_is_cut_not_kept(self):
        result = redaction.redacted_download_name("e" * 10 + "." + "f" * 140, "s.pdf")
        self.assertLessEqual(len(result), storage.MAX_DISPLAY_NAME_LENGTH)
        self.assertIn("_redacted", result)

    def test_a_missing_name_uses_the_stored_one(self):
        self.assertEqual(redaction.redacted_download_name(None, "abc123.pdf"), "abc123_redacted.pdf")


class NestedRecordFileNameTests(unittest.TestCase):
    """The copies of file_name that CVs, certifications, cost items and
    submitted certificates take in their own JSON bodies."""

    # (schema, fields it cannot be built without)
    SCHEMAS = (
        (schemas.CvIn, {}),
        (schemas.CertificationIn, {"name": "PMP"}),
        (schemas.CostItemIn, {}),
        (schemas.TenderCertificateIn, {"name": "ISO 9001"}),
    )

    def build(self, schema, required, **fields):
        return schema(**required, **fields)

    def test_every_schema_cleans_the_name(self):
        cases = {
            "..\\..\\evil/inv‮oice.pdf": "invoice.pdf",
            "a\r\nX-Evil: 1.pdf": "aX-Evil: 1.pdf",
            "bell\x07\x00.png": "bell.png",
            "  spaced.jpg. ": "spaced.jpg",
        }
        for schema, required in self.SCHEMAS:
            for raw, expected in cases.items():
                with self.subTest(schema=schema.__name__, raw=raw):
                    self.assertEqual(self.build(schema, required, file_name=raw).file_name, expected)

    def test_every_schema_caps_a_long_name_and_keeps_the_extension(self):
        for schema, required in self.SCHEMAS:
            with self.subTest(schema=schema.__name__):
                name = self.build(schema, required, file_name="n" * 500 + ".pdf").file_name
                self.assertEqual(len(name), storage.MAX_DISPLAY_NAME_LENGTH)
                self.assertTrue(name.endswith(".pdf"))

    def test_every_schema_keeps_valid_unicode(self):
        for schema, required in self.SCHEMAS:
            for name in ("résumé 2025.pdf", "報告書.pdf", "отчёт.jpg"):
                with self.subTest(schema=schema.__name__, name=name):
                    self.assertEqual(self.build(schema, required, file_name=name).file_name, name)

    def test_absent_and_empty_values_are_left_alone(self):
        for schema, required in self.SCHEMAS:
            with self.subTest(schema=schema.__name__):
                self.assertIsNone(self.build(schema, required).file_name)
                self.assertIsNone(self.build(schema, required, file_name=None).file_name)
                self.assertEqual(self.build(schema, required, file_name="").file_name, "")

    def test_a_name_with_nothing_usable_becomes_none_not_the_placeholder(self):
        for schema, required in self.SCHEMAS:
            for raw in ("../..", "\x00\x07", f"/{RLO}/", "   . "):
                with self.subTest(schema=schema.__name__, raw=raw):
                    self.assertIsNone(self.build(schema, required, file_name=raw).file_name)

    def test_the_output_schemas_clean_a_name_stored_before_this_existed(self):
        for schema in (
            schemas.CvOut,
            schemas.CertificationOut,
            schemas.CostItemOut,
            schemas.TenderCertificateOut,
        ):
            with self.subTest(schema=schema.__name__):
                record = schema.model_validate(
                    {
                        "id": "1",
                        "created_at": "2026-01-01T00:00:00Z",
                        "name": "x",
                        "file_name": "..\\old/stored.pdf",
                    }
                )
                self.assertEqual(record.file_name, "stored.pdf")


class FilenameEndpointTests(unittest.TestCase):
    """Names over HTTP. The test client percent-encodes control characters
    (CR, LF, BEL...) in a multipart filename, as a browser does, so the server
    receives them as harmless printable text; the formatting characters used
    here (zero-width space, right-to-left override) are sent as they are. The
    raw control-character cases are covered above, on the helpers, and by the
    stored-before-cleaning tests below."""

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
        for leftover in os.listdir(self.uploads.name):
            try:
                os.remove(os.path.join(self.uploads.name, leftover))
            except OSError:
                pass  # MuPDF may still map it on Windows; the temp dir is ours anyway
        self.users.delete_many({})
        self.documents.delete_many({})
        self.employees.delete_many({})
        self.tenders.delete_many({})
        self.database[mongodb.MONGO_SESSIONS_COLLECTION_NAME].delete_many({})
        self.database[mongodb.MONGO_LOGIN_THROTTLE_COLLECTION_NAME].delete_many({})
        auth.create_user("admin@example.test", "correct horse battery staple", self.users)
        self.client.cookies.clear()
        response = self.client.post(
            "/auth/login",
            json={"username": "admin@example.test", "password": "correct horse battery staple"},
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.csrf = response.json()["csrf_token"]

    def post_upload(self, file_name, content=None, content_type="application/pdf"):
        return self.client.post(
            "/documents/",
            data={"document_type": "Cost Sheet", "submitted_by": "tests"},
            files={
                "document_file": (
                    file_name,
                    io.BytesIO(_pdf() if content is None else content),
                    content_type,
                )
            },
            headers={"X-CSRF-Token": self.csrf},
        )

    def upload(self, file_name):
        response = self.post_upload(file_name)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def stored(self, document_id):
        return self.documents.find_one({"_id": ObjectId(document_id)})

    # --- upload -----------------------------------------------------------

    def test_the_stored_name_is_cleaned_and_the_disk_name_is_a_uuid(self):
        document_id = self.upload("..\\..\\evil/" + "dir/" + f"re\u0301sume\u0301{RLO}\u200b.pdf")
        record = self.stored(document_id)
        self.assertEqual(record["file_name"], "r\u00e9sum\u00e9.pdf")
        self.assertRegex(record["stored_file_name"], r"^[0-9a-f]{32}\.pdf$")
        self.assertEqual(os.listdir(self.uploads.name), [record["stored_file_name"]])
        self.assertEqual(self.client.get(f"/documents/{document_id}").json()["file_name"], "r\u00e9sum\u00e9.pdf")

    def test_unicode_names_survive_an_upload(self):
        document_id = self.upload("報告書 2025.pdf")
        self.assertEqual(self.stored(document_id)["file_name"], "報告書 2025.pdf")

    def test_a_very_long_name_is_capped_on_upload(self):
        document_id = self.upload("n" * 600 + ".pdf")
        name = self.stored(document_id)["file_name"]
        self.assertEqual(len(name), storage.MAX_DISPLAY_NAME_LENGTH)
        self.assertTrue(name.endswith(".pdf"))

    def test_the_extension_is_still_validated(self):
        for name in ("evil.exe", "evil.pdf.exe", "noextension", "   .  ", "x.pdf.exe/"):
            with self.subTest(name=name):
                response = self.post_upload(name)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "File must be a PDF, PNG, or JPG.")
        self.assertEqual(os.listdir(self.uploads.name), [])
        self.assertEqual(self.documents.count_documents({}), 0)

    def test_a_trailing_control_character_does_not_turn_a_good_name_into_a_bad_one(self):
        document_id = self.upload("scan.pdf​")
        self.assertEqual(self.stored(document_id)["file_name"], "scan.pdf")

    def test_error_messages_repeat_only_the_cleaned_name(self):
        response = self.post_upload(f"..\\secret/{RLO}empty​.pdf", content=b"")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["detail"],
            '"empty.pdf" is empty (0 bytes). Please choose the actual file.',
        )

        with patch.object(storage, "MAX_UPLOAD_BYTES", 10):
            response = self.post_upload("../big​file.pdf", content=b"x" * 11)
        self.assertEqual(response.status_code, 413, response.text)
        self.assertIn('"bigfile.pdf" is larger than the', response.json()["detail"])
        self.assertEqual(os.listdir(self.uploads.name), [])

    # --- replace and nested copies ---------------------------------------

    def test_a_replacement_is_cleaned_and_written_through_to_nested_copies(self):
        document_id = self.upload("first.pdf")
        employee_id = self.employees.insert_one(
            {
                "full_name": "Asha Patil",
                "cvs": [{"document_id": document_id, "file_name": "first.pdf"}],
                "certifications": [{"document_id": document_id, "file_name": "first.pdf"}],
            }
        ).inserted_id
        tender_id = self.tenders.insert_one(
            {
                "title": "Metro bid",
                "cost_items": [{"document_id": document_id, "file_name": "first.pdf"}],
                "certificates": [{"document_id": document_id, "file_name": "first.pdf"}],
            }
        ).inserted_id

        response = self.client.put(
            f"/documents/{document_id}",
            data={"document_type": "Cost Sheet"},
            files={
                "document_file": (
                    "..\\new/dir\\second​.pdf",
                    io.BytesIO(_pdf("A DIFFERENT PAGE")),
                    "application/pdf",
                )
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.stored(document_id)["file_name"], "second.pdf")

        employee = self.employees.find_one({"_id": employee_id})
        tender = self.tenders.find_one({"_id": tender_id})
        for nested in (
            employee["cvs"][0],
            employee["certifications"][0],
            tender["cost_items"][0],
            tender["certificates"][0],
        ):
            self.assertEqual(nested["file_name"], "second.pdf")

    # --- downloads --------------------------------------------------------

    def test_the_download_is_an_attachment_with_a_safe_header(self):
        document_id = self.upload("résumé 2025.pdf")
        response = self.client.get(f"/documents/{document_id}/file")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(
            response.headers["content-disposition"],
            "attachment; filename=\"resume 2025.pdf\"; "
            "filename*=UTF-8''r%C3%A9sum%C3%A9%202025.pdf",
        )
        self.assertEqual(response.content[:5], b"%PDF-")

    def test_inline_preview_uses_the_same_header_builder(self):
        document_id = self.upload("résumé 2025.pdf")
        response = self.client.get(f"/documents/{document_id}/file?disposition=inline")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["content-disposition"],
            "inline; filename=\"resume 2025.pdf\"; "
            "filename*=UTF-8''r%C3%A9sum%C3%A9%202025.pdf",
        )

    def test_an_unknown_disposition_is_still_refused(self):
        document_id = self.upload("a.pdf")
        response = self.client.get(f"/documents/{document_id}/file?disposition=x%3Bevil")
        self.assertEqual(response.status_code, 422)

    def test_a_record_stored_before_cleaning_existed_is_still_sent_safely(self):
        document_id = self.upload("clean.pdf")
        self.documents.update_one(
            {"_id": ObjectId(document_id)},
            {"$set": {"file_name": 'a/../b"c;\x07\u202e.pdf'}},
        )
        for query in ("", "?disposition=inline"):
            with self.subTest(query=query):
                header = self.client.get(f"/documents/{document_id}/file{query}").headers[
                    "content-disposition"
                ]
                self.assertTrue(_only_safe_header_characters(header), repr(header))
                self.assertIn('filename="b_c_.pdf"', header)
                self.assertIn("filename*=UTF-8''b%22c%3B.pdf", header)

    def test_a_record_with_no_name_downloads_under_its_stored_name(self):
        document_id = self.upload("clean.pdf")
        self.documents.update_one({"_id": ObjectId(document_id)}, {"$set": {"file_name": None}})
        stored = self.stored(document_id)["stored_file_name"]
        header = self.client.get(f"/documents/{document_id}/file").headers["content-disposition"]
        self.assertIn(f'filename="{stored}"', header)

    def test_the_redacted_download_header_is_safe_and_keeps_unicode(self):
        document_id = self.upload("résumé.pdf")
        self.documents.update_one(
            {"_id": ObjectId(document_id)},
            {"$set": {"file_name": "..\\x/résumé\r\nX-Evil: 1.pdf"}},
        )
        response = self.client.post(
            f"/documents/{document_id}/redacted-copy",
            json={"areas": [AREA]},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        header = response.headers["content-disposition"]
        self.assertTrue(_only_safe_header_characters(header), repr(header))
        self.assertTrue(header.startswith("attachment;"))
        self.assertEqual(
            unquote(header.split("filename*=UTF-8''", 1)[1]), "résuméX-Evil: 1_redacted.pdf"
        )
        self.assertNotIn("x-evil", response.headers)

    def test_a_saved_redacted_copy_and_its_notes_use_the_cleaned_name(self):
        document_id = self.upload("clean.pdf")
        self.documents.update_one(
            {"_id": ObjectId(document_id)},
            {"$set": {"file_name": "..\\x/inv\u202eoice\r\n.pdf"}},
        )
        response = self.client.post(
            f"/documents/{document_id}/redacted-copy/save",
            json={"areas": [AREA]},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        copy = self.stored(response.json()["id"])
        self.assertEqual(copy["file_name"], "invoice_redacted.pdf")
        self.assertTrue(copy["notes"].startswith("Redacted copy of invoice.pdf."))


if __name__ == "__main__":
    unittest.main()
