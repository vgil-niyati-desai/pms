"""import_tender_documents.py: tenders and documents, idempotence and safety.

mongomock and a temporary uploads folder only; the real uploads folder is
fingerprinted before and after.
"""

import hashlib
import json
import os
import tempfile
import unittest

import mongomock
import pymupdf

import import_tender_documents as importer
from app import storage


def pdf_bytes(text):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), text)
    data = doc.tobytes()
    doc.close()
    return data


def fingerprint():
    folder = os.path.join(os.path.dirname(os.path.abspath(storage.__file__)), "uploads")
    return sorted((n, os.path.getsize(os.path.join(folder, n))) for n in os.listdir(folder)) if os.path.isdir(folder) else []


class TenderDocumentImportTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.uploads.cleanup)
        self.package = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.package.cleanup)
        self.prev_dir, self.before = storage.UPLOAD_DIR, fingerprint()
        storage.UPLOAD_DIR = self.uploads.name
        db = mongomock.MongoClient(tz_aware=True)["tender_import_test"]
        self.tenders, self.documents = db["tenders"], db["documents"]

    def tearDown(self):
        storage.UPLOAD_DIR = self.prev_dir
        self.assertEqual(fingerprint(), self.before, "real uploads changed")

    def package_with(self, *bids):
        manifest = {"tenders": []}
        for number, (fields, docs) in enumerate(bids):
            entry = {"source": "test", "fields": fields, "documents": []}
            for index, (name, text, extra) in enumerate(docs):
                data = pdf_bytes(text)
                rel = "files/%d/%s" % (number, name)
                os.makedirs(os.path.dirname(os.path.join(self.package.name, rel)), exist_ok=True)
                with open(os.path.join(self.package.name, rel), "wb") as handle:
                    handle.write(data)
                entry["documents"].append({"file": rel, "original_name": name,
                                           "sha256": hashlib.sha256(data).hexdigest(), **extra})
            manifest["tenders"].append(entry)
        with open(os.path.join(self.package.name, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)

    def run_import(self, dry_run=False):
        results = importer.import_manifest(self.package.name, self.tenders, self.documents, dry_run)
        counts = {}
        for row in results:
            key = "%s %s" % (row["kind"], row["status"])
            counts[key] = counts.get(key, 0) + 1
        return results, counts

    FIELDS = {"title": "Core Banking RFP", "issuing_authority": "Test Bank", "reference_number": "TB/1/2026",
              "tender_type": "RFP", "submission_deadline": "2026-03-01", "tags": ["Testland"], "estimated_value": ""}
    DOC = ("Original Name.pdf", "client rfp", {"document_type": "Tender Document", "reference_number": "TB/1/2026",
                                               "document_date": "", "notes": "Client-issued."})

    def test_creates_tender_and_attaches_document_in_the_apps_shape(self):
        self.package_with((self.FIELDS, [self.DOC]))
        _, counts = self.run_import()
        self.assertEqual(counts, {"tender created": 1, "document inserted": 1})
        tender = self.tenders.find_one({})
        document = self.documents.find_one({})
        self.assertEqual(tender["document_ids"], [str(document["_id"])])
        self.assertEqual(document["tender_id"], str(tender["_id"]))
        self.assertEqual((document["client_name"], document["project_title"]), ("Test Bank", "Core Banking RFP"))
        self.assertEqual(document["file_name"], "Original Name.pdf")  # original name kept
        self.assertIsNone(document["document_date"])                  # unavailable stays empty
        self.assertIsNone(tender["estimated_value"])
        self.assertIsNone(document["project_id"])
        self.assertTrue(os.path.exists(os.path.join(self.uploads.name, document["stored_file_name"])))
        self.assertEqual(tender["cost_items"] + tender["certificates"] + tender["criteria"], [])

    def test_rerun_creates_nothing_and_changes_nothing(self):
        self.package_with((self.FIELDS, [self.DOC]))
        self.run_import()
        snapshot = (list(self.tenders.find({})), list(self.documents.find({})), sorted(os.listdir(self.uploads.name)))
        _, counts = self.run_import()
        self.assertEqual(counts, {"tender existing": 1, "document skipped": 1})
        self.assertEqual((list(self.tenders.find({})), list(self.documents.find({})),
                          sorted(os.listdir(self.uploads.name))), snapshot)

    def test_dry_run_writes_nothing(self):
        self.package_with((self.FIELDS, [self.DOC]))
        _, counts = self.run_import(dry_run=True)
        self.assertEqual(counts, {"tender created": 1, "document inserted": 1})
        self.assertEqual((self.tenders.count_documents({}), self.documents.count_documents({}),
                          os.listdir(self.uploads.name)), (0, 0, []))

    def test_an_existing_tender_is_never_modified(self):
        self.tenders.insert_one({"title": "Edited by a person", "issuing_authority": "Test Bank",
                                 "reference_number": "TB/1/2026", "document_ids": [], "notes": "mine"})
        self.package_with((self.FIELDS, [self.DOC]))
        self.run_import()
        tender = self.tenders.find_one({})
        self.assertEqual((self.tenders.count_documents({}), tender["title"], tender["notes"]), (1, "Edited by a person", "mine"))
        self.assertEqual(len(tender["document_ids"]), 1)  # the document is still attached to it

    def test_a_file_owned_by_something_else_is_left_alone_and_a_free_one_is_linked(self):
        self.package_with((self.FIELDS, [self.DOC]))
        with open(os.path.join(self.package.name, "files/0/Original Name.pdf"), "rb") as handle:
            data = handle.read()  # the packaged bytes (PyMuPDF output differs on every call)
        owned = self.documents.insert_one({"file_hash": hashlib.sha256(data).hexdigest(), "project_id": "p1",
                                           "tender_id": None, "document_type": "Evidence"}).inserted_id
        results, counts = self.run_import()
        self.assertEqual(counts, {"tender created": 1, "document skipped": 1})
        self.assertIn("conflict", results[-1]["detail"])
        self.assertIsNone(self.documents.find_one({"_id": owned}).get("tender_id"))

        self.documents.update_one({"_id": owned}, {"$set": {"project_id": None}})
        _, counts = self.run_import()
        self.assertEqual(counts, {"tender existing": 1, "document linked": 1})
        self.assertEqual(self.documents.find_one({"_id": owned})["tender_id"], str(self.tenders.find_one({})["_id"]))
        self.assertEqual(self.documents.count_documents({}), 1)  # no second record for the same file

    def test_a_corrupt_file_or_invalid_tender_fails_without_writing_a_document(self):
        self.package_with((self.FIELDS, [self.DOC]))
        with open(os.path.join(self.package.name, "files/0/Original Name.pdf"), "ab") as handle:
            handle.write(b"tampered")
        _, counts = self.run_import()
        self.assertEqual(counts, {"tender created": 1, "document failed": 1})
        self.assertEqual((self.documents.count_documents({}), os.listdir(self.uploads.name)), (0, []))

        bad = dict(self.FIELDS, reference_number="TB/2/2026", published_date="05/01/2026")
        self.package_with((bad, [self.DOC]))
        _, counts = self.run_import()
        self.assertEqual(counts, {"tender failed": 1})  # the app's own date rule applies
        self.assertEqual(self.tenders.count_documents({}), 1)

    def test_a_bid_without_a_reference_is_matched_on_authority_and_title(self):
        fields = {"title": "Proposal for Test Bank", "issuing_authority": "Test Bank", "tags": ["Testland"]}
        self.package_with((fields, [("a.pdf", "first", {"document_type": "Submitted Bid"})]))
        _, counts = self.run_import()
        self.assertEqual(counts, {"tender created": 1, "document inserted": 1})
        # a later batch adds a second file to the same bid (appended to the package, so a.pdf is unchanged)
        extra = pdf_bytes("second")
        with open(os.path.join(self.package.name, "files/0/b.pdf"), "wb") as handle:
            handle.write(extra)
        manifest_path = os.path.join(self.package.name, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["tenders"][0]["documents"].append({
            "file": "files/0/b.pdf", "original_name": "b.pdf", "sha256": hashlib.sha256(extra).hexdigest(),
            "document_type": "Financial Proposal"})
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        _, counts = self.run_import()
        self.assertEqual(counts, {"tender existing": 1, "document skipped": 1, "document inserted": 1})
        self.assertEqual(self.tenders.count_documents({}), 1)  # not a second tender
        self.assertEqual(len(self.tenders.find_one({})["document_ids"]), 2)  # new file joined the same one

    def test_size_and_type_limits_default_to_the_apps_and_are_opt_in(self):
        self.package_with((self.FIELDS, [("Sheet.xlsx", "x", {"document_type": "Other"})]))
        os.replace(os.path.join(self.package.name, "files/0/Sheet.xlsx"), os.path.join(self.package.name, "files/0/Sheet.xlsx"))
        results, counts = self.run_import()
        self.assertEqual(counts, {"tender created": 1, "document failed": 1})
        self.assertIn("not accepted", results[-1]["detail"])
        # opt in to the type: accepted, stored under its original name
        results = importer.import_manifest(self.package.name, self.tenders, self.documents, extra_ext={"xlsx"})
        self.assertEqual([r["status"] for r in results if r["kind"] == "document"], ["inserted"])
        # a file over the run's limit is refused with the limit named
        self.documents.delete_many({})
        self.tenders.delete_many({})
        results = importer.import_manifest(self.package.name, self.tenders, self.documents, max_bytes=10, extra_ext={"xlsx"})
        self.assertIn("upload limit for this run", [r for r in results if r["kind"] == "document"][0]["detail"])

    def test_ocr_text_travels_with_a_newly_inserted_document(self):
        self.package_with((self.FIELDS, [("Scan.pdf", "scan", {"document_type": "Tender Notice"})]))
        manifest_path = os.path.join(self.package.name, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["tenders"][0]["documents"][0]["extraction"] = {
            "methods": ["ocr"], "pages": [{"page": 1, "method": "ocr", "text": "Closing date 6 August"}]}
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        self.run_import()
        document = self.documents.find_one({})
        self.assertEqual(document["extraction_methods"], ["ocr"])
        self.assertIn("Closing date 6 August", document["extracted_text"])

    def _enrich(self, **extra_fields):
        """Re-write the package manifest with richer metadata for the same files."""
        path = os.path.join(self.package.name, "manifest.json")
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        entry = manifest["tenders"][0]
        entry["fields"].update(extra_fields)
        entry["documents"][0].update({"category": "Core Banking System", "document_date": "2026-01-16",
                                      "notes": "Client-issued RFP, richer description."})
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)

    def test_update_fills_and_corrects_but_never_clears_and_is_idempotent(self):
        self.package_with((self.FIELDS, [self.DOC]))
        self.run_import()
        self._enrich(tender_fee="560", status="Submitted", tags=["Testland", "Core Banking"], submission_deadline="")
        # without --update nothing is touched
        _, counts = self.run_import()
        self.assertEqual(counts, {"tender existing": 1, "document skipped": 1})
        self.assertIsNone(self.tenders.find_one({})["tender_fee"])

        results = importer.import_manifest(self.package.name, self.tenders, self.documents, update=True)
        statuses = sorted((r["kind"], r["status"]) for r in results)
        self.assertEqual(statuses, [("document", "updated"), ("tender", "updated")])
        tender, document = self.tenders.find_one({}), self.documents.find_one({})
        self.assertEqual((tender["tender_fee"], tender["status"], tender["tags"]),
                         ("560", "Submitted", ["Testland", "Core Banking"]))
        self.assertEqual(tender["submission_deadline"], "2026-03-01")  # an empty manifest value never clears
        self.assertEqual((document["category"], document["document_date"]), ("Core Banking System", "2026-01-16"))
        self.assertEqual(len(tender["document_ids"]), 1)  # no new records, nothing re-attached
        self.assertEqual((self.tenders.count_documents({}), self.documents.count_documents({})), (1, 1))

        again = importer.import_manifest(self.package.name, self.tenders, self.documents, update=True)
        self.assertEqual(sorted((r["kind"], r["status"]) for r in again), [("document", "skipped"), ("tender", "existing")])

    def test_update_does_not_touch_a_document_this_tool_did_not_import(self):
        self.package_with((self.FIELDS, [self.DOC]))
        self.run_import()
        self.documents.update_one({}, {"$set": {"submitted_by": "A. Person", "notes": "written by hand"}})
        self._enrich()
        results = importer.import_manifest(self.package.name, self.tenders, self.documents, update=True)
        self.assertEqual([r["status"] for r in results if r["kind"] == "document"], ["skipped"])
        self.assertEqual(self.documents.find_one({})["notes"], "written by hand")

    def test_update_dry_run_writes_nothing(self):
        self.package_with((self.FIELDS, [self.DOC]))
        self.run_import()
        self._enrich(tender_fee="560")
        results = importer.import_manifest(self.package.name, self.tenders, self.documents, dry_run=True, update=True)
        self.assertEqual(sorted(r["status"] for r in results), ["updated", "updated"])
        self.assertIsNone(self.tenders.find_one({})["tender_fee"])


if __name__ == "__main__":
    unittest.main()
