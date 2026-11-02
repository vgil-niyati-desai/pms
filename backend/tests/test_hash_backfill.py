"""The startup hash backfill, when legacy entries hold identical files.

The unique file_hash index is created before the backfill runs. Two entries
uploaded before duplicates were refused can hold the same bytes, and giving
both the hash used to raise DuplicateKeyError out of the startup handler.

Everything runs against a private mongomock client with UPLOAD_DIR pointed at
a temporary directory; the real uploads folder is fingerprinted before and
after, as in test_document_deletion.py.
"""

import os
import tempfile
import unittest

import mongomock
from pymongo.errors import DuplicateKeyError

from app import mongodb, storage
from app.routers import documents_mongo

SAME = b"identical legacy bytes"
OTHER = b"a different file"


def _fingerprint_real_uploads() -> list:
    folder = os.path.join(os.path.dirname(os.path.abspath(storage.__file__)), "uploads")
    if not os.path.isdir(folder):
        return []
    return sorted(
        (name, os.path.getsize(os.path.join(folder, name))) for name in os.listdir(folder)
    )


class HashBackfillTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.addCleanup(self.uploads.cleanup)
        self.previous_upload_dir = storage.UPLOAD_DIR
        self.real_before = _fingerprint_real_uploads()
        storage.UPLOAD_DIR = self.uploads.name

        self.collection = mongomock.MongoClient(tz_aware=True)["backfill_test"]["documents"]
        mongodb.ensure_indexes(self.collection)

    def tearDown(self):
        storage.UPLOAD_DIR = self.previous_upload_dir
        self.assertEqual(_fingerprint_real_uploads(), self.real_before)

    def legacy(self, name, contents, **fields):
        """An entry stored before hashing existed: a file, and no file_hash."""
        if contents is not None:
            with open(os.path.join(self.uploads.name, name), "wb") as f:
                f.write(contents)
        return self.collection.insert_one(
            {"stored_file_name": name, "file_hash": None, **fields}
        ).inserted_id

    def hash_of(self, oid):
        return self.collection.find_one({"_id": oid})["file_hash"]

    def test_the_index_rejects_duplicate_hashes_but_allows_many_nulls(self):
        # The premise of everything below, checked against mongomock itself:
        # a partial unique index, and an update that violates it raises.
        a = self.legacy("a.pdf", None)
        b = self.legacy("b.pdf", None)
        self.collection.insert_one({"stored_file_name": None})  # no field at all
        self.collection.insert_one({"stored_file_name": None, "file_hash": None})
        self.collection.update_one({"_id": a}, {"$set": {"file_hash": "h" * 64}})
        with self.assertRaises(DuplicateKeyError):
            self.collection.update_one({"_id": b}, {"$set": {"file_hash": "h" * 64}})
        with self.assertRaises(DuplicateKeyError):
            self.collection.insert_one({"file_hash": "h" * 64})
        self.assertIsNone(self.hash_of(b))

    def test_identical_legacy_files_do_not_crash_and_only_one_is_hashed(self):
        first = self.legacy("a.pdf", SAME)
        second = self.legacy("b.pdf", SAME)

        with self.assertLogs("uvicorn.error", level="WARNING") as logged:
            filled = documents_mongo.backfill_file_hashes(self.collection)

        self.assertEqual(filled, 1)
        self.assertEqual(self.collection.count_documents({}), 2)
        hashes = [self.hash_of(first), self.hash_of(second)]
        self.assertEqual(sum(h is not None for h in hashes), 1)
        loser = second if self.hash_of(first) else first
        self.assertIn(str(loser), "\n".join(logged.output))

    def test_a_duplicate_does_not_stop_later_unique_files_being_hashed(self):
        self.legacy("a.pdf", SAME)
        self.legacy("b.pdf", SAME)
        unique = self.legacy("c.pdf", OTHER)

        with self.assertLogs("uvicorn.error", level="WARNING"):
            filled = documents_mongo.backfill_file_hashes(self.collection)

        self.assertEqual(filled, 2)
        self.assertEqual(len(self.hash_of(unique)), 64)

    def test_a_match_for_an_already_hashed_entry_stays_unhashed_and_is_logged(self):
        holder = self.legacy("a.pdf", SAME)
        self.collection.update_one(
            {"_id": holder},
            {"$set": {"file_hash": storage.hash_file_on_disk("a.pdf")}},
        )
        twin = self.legacy("b.pdf", SAME)

        with self.assertLogs("uvicorn.error", level="WARNING") as logged:
            filled = documents_mongo.backfill_file_hashes(self.collection)

        self.assertEqual(filled, 0)
        self.assertIsNone(self.hash_of(twin))
        self.assertIsNotNone(self.hash_of(holder))
        output = "\n".join(logged.output)
        self.assertIn(str(twin), output)
        self.assertIn("b.pdf", output)

    def test_a_duplicate_upload_is_still_refused_after_the_backfill(self):
        self.legacy("a.pdf", SAME)
        self.legacy("b.pdf", SAME)
        with self.assertLogs("uvicorn.error", level="WARNING"):
            documents_mongo.backfill_file_hashes(self.collection)

        digest = storage.hash_file_on_disk("a.pdf")
        with self.assertRaises(documents_mongo.HTTPException) as raised:
            documents_mongo._check_duplicate(self.collection, digest)
        self.assertEqual(raised.exception.status_code, 409)

    def test_running_the_backfill_again_changes_nothing(self):
        self.legacy("a.pdf", SAME)
        self.legacy("b.pdf", SAME)
        self.legacy("c.pdf", OTHER)
        with self.assertLogs("uvicorn.error", level="WARNING"):
            documents_mongo.backfill_file_hashes(self.collection)
        before = list(self.collection.find().sort("_id"))

        with self.assertLogs("uvicorn.error", level="WARNING"):
            filled = documents_mongo.backfill_file_hashes(self.collection)

        self.assertEqual(filled, 0)
        self.assertEqual(list(self.collection.find().sort("_id")), before)


if __name__ == "__main__":
    unittest.main()
