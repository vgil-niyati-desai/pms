"""Deleting a stored file, and saying so when it cannot be done.

`remove_stored_file` is the last step of every delete and the rollback of
every failed upload. It cannot raise -- the caller has already decided what
it is doing -- so for as long as it also said nothing, a file it failed to
remove stayed in the uploads folder with no record pointing at it and
nothing to show it was ever there.

What is checked here is the difference between the two silences it used to
share: a file that is already gone, which is the state the caller wanted,
and a file that is still there, which is an orphan someone has to go and
find.

UPLOAD_DIR is pointed at a temporary directory throughout, and the real
uploads folder is fingerprinted before and after.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import storage


def _fingerprint_real_uploads() -> list:
    """Names and sizes of the files in the app's own uploads folder."""
    folder = os.path.join(os.path.dirname(os.path.abspath(storage.__file__)), "uploads")
    if not os.path.isdir(folder):
        return []
    return sorted(
        (name, os.path.getsize(os.path.join(folder, name)))
        for name in os.listdir(folder)
    )


class RemoveStoredFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        cls.uploads.cleanup()

    def stored_file(self) -> str:
        return storage.write_upload(b"%PDF-stored", "evidence.pdf")

    # --- the file goes ----------------------------------------------------

    def test_removing_a_stored_file_reports_success_and_deletes_it(self):
        stored = self.stored_file()
        path = storage.upload_path(stored)
        self.assertTrue(os.path.exists(path))

        self.assertIs(storage.remove_stored_file(stored), True)
        self.assertFalse(os.path.exists(path))

    def test_nothing_is_logged_for_an_ordinary_delete(self):
        """A warning has to mean something, so the quiet path stays quiet."""
        stored = self.stored_file()
        with self.assertNoLogs("uvicorn.error", level="WARNING"):
            self.assertIs(storage.remove_stored_file(stored), True)

    # --- there was nothing to remove --------------------------------------

    def test_a_file_that_is_already_gone_is_not_a_failure(self):
        stored = self.stored_file()
        os.remove(storage.upload_path(stored))

        with self.assertNoLogs("uvicorn.error", level="WARNING"):
            self.assertIs(storage.remove_stored_file(stored), True)

    def test_no_file_name_at_all_is_not_a_failure(self):
        for name in (None, ""):
            with self.subTest(name=name), self.assertNoLogs("uvicorn.error", level="WARNING"):
                self.assertIs(storage.remove_stored_file(name), True)

    # --- the file is still there ------------------------------------------

    def test_a_file_that_cannot_be_removed_is_reported_and_logged(self):
        """The real case this exists for: the file is held by something else.

        Windows refuses to unlink a file another handle has open, which is
        what a preview or a redaction does to one MuPDF has mapped. The
        error is mocked rather than staged, because staging it means a
        platform-specific lock and the handling is the same whichever errno
        arrives.
        """
        stored = self.stored_file()
        path = storage.upload_path(stored)

        with patch.object(
            storage.os, "remove", side_effect=PermissionError(13, "used by another process")
        ):
            with self.assertLogs("uvicorn.error", level="WARNING") as logged:
                self.assertIs(storage.remove_stored_file(stored), False)

        message = "\n".join(logged.output)
        self.assertIn(stored, message, "the warning does not name the orphan")
        self.assertIn("used by another process", message)
        self.assertTrue(os.path.exists(path), "the file was removed after all")
        storage.remove_stored_file(stored)

    def test_a_real_undeletable_entry_is_reported(self):
        """The same, staged on the filesystem rather than mocked.

        A directory where a file is expected cannot be unlinked on any
        platform -- IsADirectoryError on POSIX, PermissionError on Windows,
        both OSError -- so this covers the branch without a mock and without
        pinning the test to one operating system.
        """
        stored = "4f9c1b2a3d5e4f6a7b8c9d0e1f2a3b4c.pdf"
        os.mkdir(os.path.join(storage.UPLOAD_DIR, stored))
        self.addCleanup(os.rmdir, os.path.join(storage.UPLOAD_DIR, stored))

        with self.assertLogs("uvicorn.error", level="WARNING") as logged:
            self.assertIs(storage.remove_stored_file(stored), False)
        self.assertIn(stored, "\n".join(logged.output))

    def test_a_name_that_will_not_resolve_is_reported(self):
        """upload_path refuses some names outright, and that is a failure too.

        Whatever is on disk under such a name, this call did not remove it
        and cannot say it did.
        """
        with patch.object(
            storage,
            "upload_path",
            side_effect=HTTPException(status_code=400, detail="Invalid stored file name"),
        ):
            with self.assertLogs("uvicorn.error", level="WARNING") as logged:
                self.assertIs(storage.remove_stored_file("elsewhere.pdf"), False)
        self.assertIn("Invalid stored file name", "\n".join(logged.output))

    # --- the callers are unchanged ----------------------------------------

    def test_a_failed_rollback_still_raises_the_error_that_caused_it(self):
        """write_upload's own cleanup call must not swallow or change it."""
        with patch.object(storage.os, "remove", side_effect=PermissionError(13, "locked")):
            with patch("builtins.open", side_effect=OSError("disk full")):
                with self.assertLogs("uvicorn.error", level="WARNING"):
                    with self.assertRaises(OSError) as raised:
                        storage.write_upload(b"%PDF-x", "doomed.pdf")
        self.assertIn("disk full", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
