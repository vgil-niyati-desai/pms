"""What a redacted copy must not carry out of the building.

The redaction pass itself is already covered by verify_mongo.py: it proves
that text under a box is gone from the copy's content stream. These tests
cover everything a content stream is not -- metadata, the XMP packet,
bookmarks, attachments, annotations and form fields -- each of which was
confirmed to hand the original's words straight to whoever opens the copy
before app/redaction.py learned to clear them.

Nothing here touches the real uploads folder or a real database. UPLOAD_DIR
is pointed at a temporary directory for the duration, the collections are
mongomock's, and the real folder is fingerprinted before and after so a test
that writes into it by accident is a failure rather than a surprise.
"""

import gc
import hashlib
import io
import os
import tempfile
import unittest

import mongomock
import pymupdf
from fastapi.testclient import TestClient

from app import auth, main, mongodb, redaction, storage

# Words planted in the fixture, each in a place a redaction pass does not
# reach on its own. A test asserts the exact string is gone, so they are
# deliberately distinctive.
SECRET_TEXT = "CONTRACT VALUE 4,750,000 INR"
SECRET_HIDDEN = "OCR LAYER SECRET 8,912"
SECRET_INFO = "Bid ACME 4,750,000"
SECRET_XMP = "XMP SECRET 4,750,000"
SECRET_ATTACHMENT = b"ATTACHED PRICING SECRET 4,750,000"
SECRET_ANNOT_FILE = b"ANNOT ATTACHMENT SECRET 4,750,000"
SECRET_STICKY = "STICKY NOTE SECRET 4,750,000"
SECRET_WIDGET = "WIDGET SECRET 4,750,000"
SECRET_BOOKMARK = "Bookmark 4,750,000"
SECRET_EXIF = b"GPS 12.9716,77.5946 SECRET"

# Planted outside every box, or on a page with no box at all. These must
# survive: a targeted removal that takes content the user did not mark is as
# wrong as one that leaves content they did.
PUBLIC_TEXT = "Project: Riverside Bridge Rehabilitation"
PUBLIC_HIDDEN = "OCR LAYER PUBLIC riverside"
PUBLIC_STICKY = "STICKY NOTE PUBLIC"
PUBLIC_WIDGET = "WIDGET PUBLIC"
PUBLIC_WIDGET_PAGE_TWO = "WIDGET PAGE TWO"

# The band the tests redact: the top third of page 1, in the normalised
# coordinates the browser sends.
AREA = {"page": 0, "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.33}


def _leaky_pdf() -> bytes:
    """A two-page PDF carrying a secret in every place that leaks.

    Page 1 holds the planted material, split either side of the band in
    AREA: everything sensitive above it, everything public below. Page 2 is
    never redacted and exists to prove an untouched page stays untouched.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    height = page.rect.height
    inside = height * 0.33

    # --- inside the band ---
    page.insert_text((72, inside * 0.3), SECRET_TEXT, fontsize=14)
    # render_mode=3 is invisible text: what an OCR pass puts under a scan.
    page.insert_text((72, inside * 0.5), SECRET_HIDDEN, fontsize=14, render_mode=3)
    sticky = page.add_text_annot(pymupdf.Point(72, inside * 0.6), SECRET_STICKY)
    sticky.update()
    attachment = page.add_file_annot(
        pymupdf.Point(200, inside * 0.6), SECRET_ANNOT_FILE, "pricing.txt"
    )
    attachment.update()
    secret_field = pymupdf.Widget()
    secret_field.field_name = "contract_value"
    secret_field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    secret_field.rect = pymupdf.Rect(72, inside * 0.7, 340, inside * 0.85)
    secret_field.field_value = SECRET_WIDGET
    page.add_widget(secret_field)

    # --- below the band, and so untouched ---
    page.insert_text((72, height * 0.6), PUBLIC_TEXT, fontsize=14)
    page.insert_text((72, height * 0.65), PUBLIC_HIDDEN, fontsize=14, render_mode=3)
    public_sticky = page.add_text_annot(pymupdf.Point(72, height * 0.7), PUBLIC_STICKY)
    public_sticky.update()
    public_field = pymupdf.Widget()
    public_field.field_name = "project_title"
    public_field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    public_field.rect = pymupdf.Rect(72, height * 0.75, 340, height * 0.78)
    public_field.field_value = PUBLIC_WIDGET
    page.add_widget(public_field)

    # --- a second page, never redacted ---
    page_two = doc.new_page()
    page_two.insert_text((72, 120), "Page two body text", fontsize=14)
    page_two_field = pymupdf.Widget()
    page_two_field.field_name = "page_two"
    page_two_field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    page_two_field.rect = pymupdf.Rect(72, 150, 340, 170)
    page_two_field.field_value = PUBLIC_WIDGET_PAGE_TWO
    page_two.add_widget(page_two_field)

    # --- everything that lives outside a page ---
    doc.set_metadata(
        {
            "title": SECRET_INFO,
            "author": "procurement@example.test",
            "subject": SECRET_INFO,
            "keywords": SECRET_INFO,
            "creator": "PMS",
            "producer": "PMS",
        }
    )
    doc.set_xml_metadata(
        "<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>"
        "<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF "
        "xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
        "<rdf:Description rdf:about='' "
        "xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title><rdf:Alt>"
        f"<rdf:li xml:lang='x-default'>{SECRET_XMP}</rdf:li>"
        "</rdf:Alt></dc:title></rdf:Description></rdf:RDF></x:xmpmeta>"
        "<?xpacket end='w'?>"
    )
    doc.embfile_add("pricing.txt", SECRET_ATTACHMENT, filename="pricing.txt")
    doc.set_toc([[1, SECRET_BOOKMARK, 1]])

    data = doc.tobytes()
    doc.close()
    return data


def _jpeg_with_exif() -> bytes:
    """A JPEG carrying an APP1 Exif segment with a location in it.

    Built by hand rather than loaded from a fixture file: the point is one
    byte string the test can look for, and a real camera file would make the
    assertion depend on what a decoder happens to keep.
    """
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 120), False)
    pix.clear_with(90)
    baseline = pix.tobytes("jpg", jpg_quality=95)

    # A valid-but-empty TIFF header (little-endian, IFD at offset 8, zero
    # entries), with the planted bytes after it. Decoders read the header,
    # find no tags, and copy the segment through untouched.
    payload = (
        b"Exif\x00\x00"
        + b"II*\x00"
        + (8).to_bytes(4, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + SECRET_EXIF
    )
    segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    return baseline[:2] + segment + baseline[2:]


def _redacted_document(stored_file_name: str, areas=(AREA,)) -> pymupdf.Document:
    """The copy app/redaction.py produces, opened for inspection."""
    content, media_type = redaction.redact(
        stored_file_name, [redaction.Area(**area) for area in areas]
    )
    assert media_type == "application/pdf"
    return pymupdf.open("pdf", content)


def _recoverable(pdf: bytes) -> bytes:
    """Everything a determined reader could pull out of a PDF, as one blob.

    The raw bytes alone are not enough: an embedded file's stream is
    deflated, so a planted attachment would not appear in them whether it
    survived or not. This adds every object's source and every object's
    *decompressed* stream, so the search covers what the file holds rather
    than what happens to be legible in it.
    """
    doc = pymupdf.open("pdf", pdf)
    try:
        parts = [pdf]
        for xref in range(1, doc.xref_length()):
            try:
                parts.append(doc.xref_object(xref, compressed=False).encode("latin-1"))
            except Exception:
                pass
            if doc.xref_is_stream(xref):
                try:
                    parts.append(doc.xref_stream(xref))
                except Exception:
                    pass
        return b"\n".join(parts)
    finally:
        doc.close()


def _leaks(blob: bytes, needle: bytes) -> bool:
    """Is `needle` recoverable from `blob`, however a PDF chose to write it?

    PyMuPDF writes page text as a hex string -- `<434f4e...>` rather than
    `(CONTRACT...)` -- and a string object may be UTF-16BE. Searching for
    the plain bytes alone would pass on a file that still holds every word
    of the original, so each form is searched for.
    """
    return any(
        form in blob
        for form in (
            needle,
            needle.hex().encode(),
            needle.hex().upper().encode(),
            needle.decode("latin-1").encode("utf-16-be"),
        )
    )


def _annot_contents(page) -> list:
    return [annot.info.get("content") for annot in page.annots()]


def _widget_values(page) -> list:
    return [widget.field_value for widget in page.widgets()]


class RedactionSanitisationTests(unittest.TestCase):
    """The copy, inspected for everything the original planted in it."""

    @classmethod
    def setUpClass(cls):
        # ignore_cleanup_errors because MuPDF maps a file it has opened and
        # Windows will not unlink it until the mapping is released, which can
        # outlive the Document object. A locked scratch file is noise; the
        # fingerprint below is the assertion that matters.
        cls.uploads = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.previous_upload_dir = storage.UPLOAD_DIR
        cls.real_uploads_before = _fingerprint_real_uploads()
        storage.UPLOAD_DIR = cls.uploads.name

    @classmethod
    def tearDownClass(cls):
        storage.UPLOAD_DIR = cls.previous_upload_dir
        assert _fingerprint_real_uploads() == cls.real_uploads_before, (
            "the real uploads folder changed while these tests ran"
        )
        gc.collect()
        cls.uploads.cleanup()

    def setUp(self):
        self.pdf_bytes = _leaky_pdf()
        self.stored_name = storage.write_upload(self.pdf_bytes, "leaky.pdf")
        self.addCleanup(storage.remove_stored_file, self.stored_name)

    # --- what the copy must not carry ------------------------------------

    def test_document_metadata_and_xmp_are_cleared(self):
        copy = _redacted_document(self.stored_name)
        self.addCleanup(copy.close)
        carried = {
            key: value
            for key, value in copy.metadata.items()
            if value and key not in ("format", "encryption")
        }
        self.assertEqual(carried, {}, f"metadata survived: {carried}")
        self.assertFalse(copy.get_xml_metadata())

    def test_bookmarks_and_embedded_files_are_cleared(self):
        copy = _redacted_document(self.stored_name)
        self.addCleanup(copy.close)
        self.assertEqual(copy.get_toc(), [])
        self.assertEqual(copy.embfile_names(), [])

    def test_annotations_and_fields_under_a_box_are_removed(self):
        copy = _redacted_document(self.stored_name)
        self.addCleanup(copy.close)
        page = copy[0]
        self.assertNotIn(SECRET_STICKY, _annot_contents(page))
        self.assertNotIn(SECRET_WIDGET, _widget_values(page))
        self.assertEqual(
            [a for a in page.annots() if a.type[1] == "FileAttachment"],
            [],
            "the attachment annotation under the box survived",
        )

    def test_no_planted_secret_survives_anywhere_in_the_file(self):
        """The whole-file check, and the one that matters most.

        Reading the objects back can only find what PyMuPDF chooses to
        expose. This reads the bytes and every decompressed stream, so an
        object left behind in a corner of the file nothing asked about still
        fails the test.
        """
        content, _ = redaction.redact(self.stored_name, [redaction.Area(**AREA)])
        content = _recoverable(content)
        needles = [
            SECRET_TEXT.encode(),
            SECRET_HIDDEN.encode(),
            SECRET_INFO.encode(),
            SECRET_XMP.encode(),
            SECRET_STICKY.encode(),
            SECRET_WIDGET.encode(),
            SECRET_BOOKMARK.encode(),
            SECRET_ATTACHMENT,
            SECRET_ANNOT_FILE,
        ]
        survivors = [needle for needle in needles if _leaks(content, needle)]
        self.assertEqual(survivors, [], f"recoverable from the copy: {survivors}")
        # Every one of them is recoverable from the original, so the check
        # above fails if the sanitising pass is removed.
        original = _recoverable(self.pdf_bytes)
        missing = [n for n in needles if not _leaks(original, n)]
        self.assertEqual(missing, [], f"never planted in the fixture: {missing}")

    def test_covered_text_is_gone_from_the_page_and_its_content_stream(self):
        copy = _redacted_document(self.stored_name)
        self.addCleanup(copy.close)
        page_text = copy[0].get_text()
        self.assertNotIn(SECRET_TEXT, page_text)
        self.assertNotIn(SECRET_HIDDEN, page_text)
        self.assertNotIn(SECRET_WIDGET, page_text)
        # Not merely invisible: not in the page's own instructions either,
        # which is where a box drawn over live text leaves that text sitting.
        stream = copy[0].read_contents()
        self.assertFalse(_leaks(stream, SECRET_TEXT.encode()))
        self.assertFalse(_leaks(stream, SECRET_HIDDEN.encode()))
        self.assertTrue(
            _leaks(stream, PUBLIC_TEXT.encode()),
            "nothing is in the content stream, so this check cannot fail",
        )

    def test_the_redacted_band_renders_blank(self):
        """Nothing is drawn over the fill.

        A form field's appearance is painted after the page content, so a
        value left behind shows through the white box however thoroughly the
        text underneath was deleted.
        """
        copy = _redacted_document(self.stored_name)
        self.addCleanup(copy.close)
        bounds = copy[0].rect
        clip = pymupdf.Rect(
            bounds.x0, bounds.y0, bounds.x1, bounds.y0 + bounds.height * AREA["height"]
        ) + (2, 2, -2, -2)
        shot = copy[0].get_pixmap(clip=clip)
        self.assertGreater(
            min(shot.samples), 245, f"darkest sample {min(shot.samples)}"
        )
        original = pymupdf.open(storage.upload_path(self.stored_name))
        self.addCleanup(original.close)
        before = original[0].get_pixmap(clip=clip)
        self.assertLess(min(before.samples), 245, "the band was already blank")

    # --- what the copy must keep -----------------------------------------

    def test_content_outside_the_boxes_survives(self):
        copy = _redacted_document(self.stored_name)
        self.addCleanup(copy.close)
        page = copy[0]
        self.assertIn(PUBLIC_TEXT, page.get_text())
        self.assertIn(PUBLIC_HIDDEN, page.get_text())
        self.assertIn(PUBLIC_STICKY, _annot_contents(page))
        self.assertIn(PUBLIC_WIDGET, _widget_values(page))

    def test_a_page_with_no_box_is_left_alone(self):
        copy = _redacted_document(self.stored_name)
        self.addCleanup(copy.close)
        self.assertEqual(copy.page_count, 2)
        self.assertIn("Page two body text", copy[1].get_text())
        self.assertIn(PUBLIC_WIDGET_PAGE_TWO, _widget_values(copy[1]))

    def test_the_original_is_byte_for_byte_unchanged(self):
        path = storage.upload_path(self.stored_name)
        before = _digest(path)
        _redacted_document(self.stored_name).close()
        after = _digest(path)
        self.assertEqual(before, after)
        self.assertEqual(before, hashlib.sha256(self.pdf_bytes).hexdigest())

    # --- the image path ---------------------------------------------------

    def test_image_redaction_drops_embedded_image_metadata(self):
        """No fix was needed here, and this is what keeps it that way.

        Redacting an image decodes it to a pixmap and re-encodes from raw
        pixels, so an Exif block cannot survive. A future change to a
        passthrough encoder would carry the camera's location into every
        redacted photograph, and would fail here.
        """
        source = _jpeg_with_exif()
        self.assertIn(SECRET_EXIF, source)
        stored = storage.write_upload(source, "photo.jpg")
        self.addCleanup(storage.remove_stored_file, stored)

        content, media_type = redaction.redact(
            stored, [redaction.Area(page=0, x=0.0, y=0.0, width=1.0, height=0.5)]
        )
        self.assertEqual(media_type, "image/jpeg")
        self.assertNotIn(SECRET_EXIF, content)
        self.assertNotIn(b"Exif", content)

        redacted = pymupdf.Pixmap(io.BytesIO(content))
        self.assertEqual(redacted.pixel(10, 10), (255, 255, 255))
        self.assertNotEqual(redacted.pixel(10, redacted.height - 10), (255, 255, 255))


class SavedRedactedCopyTests(unittest.TestCase):
    """The same assertions, against the file the save endpoint leaves on disk.

    The download and the saved copy are the same bytes from the same call, so
    this is the check that they stay the same bytes -- a sanitising pass added
    to one path and not the other would pass every test above.
    """

    @classmethod
    def setUpClass(cls):
        # ignore_cleanup_errors because MuPDF maps a file it has opened and
        # Windows will not unlink it until the mapping is released, which can
        # outlive the Document object. A locked scratch file is noise; the
        # fingerprint below is the assertion that matters.
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
        self.database[mongodb.MONGO_SESSIONS_COLLECTION_NAME].delete_many({})
        self.database[mongodb.MONGO_LOGIN_THROTTLE_COLLECTION_NAME].delete_many({})
        auth.create_user("admin@example.test", "correct horse battery staple", self.users)
        response = self.client.post(
            "/auth/login",
            json={
                "username": "admin@example.test",
                "password": "correct horse battery staple",
            },
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(response.status_code, 200)
        self.csrf = response.json()["csrf_token"]

    def test_saved_copy_on_disk_is_sanitised_and_leaves_the_original_alone(self):
        pdf_bytes = _leaky_pdf()
        created = self.client.post(
            "/documents/",
            data={
                "document_type": "Cost Sheet",
                "category": "Tendering",
                "client_name": "Redaction Check Ltd",
                "submitted_by": "tests",
            },
            files={"document_file": ("cost sheet.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(created.status_code, 201, created.text)
        document_id = created.json()["id"]
        source_path = storage.upload_path(
            self.documents.find_one({"file_name": "cost sheet.pdf"})["stored_file_name"]
        )
        before = _digest(source_path)

        saved = self.client.post(
            f"/documents/{document_id}/redacted-copy/save",
            json={"areas": [AREA]},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(saved.status_code, 201, saved.text)
        copy_record = self.documents.find_one({"redacted_from_id": document_id})
        self.assertIsNotNone(copy_record)

        with open(storage.upload_path(copy_record["stored_file_name"]), "rb") as f:
            on_disk = f.read()
        recoverable = _recoverable(on_disk)
        for needle in (
            SECRET_TEXT.encode(),
            SECRET_HIDDEN.encode(),
            SECRET_INFO.encode(),
            SECRET_XMP.encode(),
            SECRET_STICKY.encode(),
            SECRET_WIDGET.encode(),
            SECRET_BOOKMARK.encode(),
            SECRET_ATTACHMENT,
            SECRET_ANNOT_FILE,
        ):
            self.assertFalse(
                _leaks(recoverable, needle),
                f"{needle!r} survived into the saved copy",
            )

        copy = pymupdf.open("pdf", on_disk)
        self.addCleanup(copy.close)
        self.assertEqual(copy.embfile_names(), [])
        self.assertEqual(copy.get_toc(), [])
        self.assertIn(PUBLIC_TEXT, copy[0].get_text())
        self.assertIn(PUBLIC_WIDGET, _widget_values(copy[0]))

        # The streamed download and the saved file are the same bytes.
        streamed = self.client.post(
            f"/documents/{document_id}/redacted-copy",
            json={"areas": [AREA]},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(streamed.status_code, 200, streamed.text)
        self.assertEqual(
            _comparable(streamed.content), _comparable(on_disk),
            "the download and the saved copy are not the same document",
        )

        after = _digest(source_path)
        self.assertEqual(before, after, "the original changed")
        self.assertEqual(
            self.client.get(f"/documents/{document_id}/file").content, pdf_bytes
        )


def _comparable(pdf: bytes) -> list:
    """A PDF reduced to what two runs of the same redaction should share.

    The raw bytes differ run to run -- a PDF carries its own /ID, and MuPDF
    generates a fresh one on every save -- so the comparison is of the pages
    and of the places this module clears, not of the file.
    """
    doc = pymupdf.open("pdf", pdf)
    try:
        return [
            [page.get_text() for page in doc],
            [[w.field_name, w.field_value] for page in doc for w in page.widgets()],
            [a.info.get("content") for page in doc for a in page.annots()],
            doc.get_toc(),
            doc.embfile_names(),
            {k: v for k, v in doc.metadata.items() if v and k != "format"},
        ]
    finally:
        doc.close()


def _digest(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _fingerprint_real_uploads() -> list:
    """Names and sizes of the files in the app's own uploads folder.

    Compared before and after: these tests must not create, change or delete
    anything in there, and a fingerprint is how that is proved rather than
    assumed.
    """
    folder = os.path.join(os.path.dirname(os.path.abspath(storage.__file__)), "uploads")
    if not os.path.isdir(folder):
        return []
    return sorted(
        (name, os.path.getsize(os.path.join(folder, name)))
        for name in os.listdir(folder)
    )


if __name__ == "__main__":
    unittest.main()
