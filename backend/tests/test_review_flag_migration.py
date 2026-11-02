"""Migration 0015 removes only the retired review flags, and nothing else."""

import unittest

import mongomock

from migrations import MIGRATIONS, m0015_remove_review_flags as m0015


class RemoveReviewFlagsTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient()["review_flag_test"]

    def test_registered_last_and_not_applied_by_analyse(self):
        self.assertEqual(MIGRATIONS[-1].VERSION, m0015.VERSION)
        self.db["documents"].insert_one({"scope": "company", "needs_review": True})
        report = m0015.analyse(self.db)
        self.assertEqual(report["to_clean"], {"documents": 1, "proposal_library": 0})
        self.assertTrue(self.db["documents"].find_one({})["needs_review"])  # analyse writes nothing

    def test_removes_only_the_flags_and_keeps_every_other_field(self):
        company = {
            "scope": "company", "title": "ISO", "certificate_number": "IN1",
            "issue_date": "2025-11-01", "expiry_date": "2026-10-25",
            "needs_review": True, "expiry_unverified": True, "file_hash": "h", "notes": "n",
        }
        other = {"document_type": "CV", "needs_review": "unrelated field of another record"}
        item = {"section_number": "3.1", "needs_review": True, "response_text": "r"}
        self.db["documents"].insert_many([dict(company), dict(other)])
        self.db["proposal_library"].insert_one(dict(item))

        report = m0015.apply(self.db)
        m0015.record(self.db, report)

        self.assertEqual(report["cleaned"], {"documents": 1, "proposal_library": 1})
        kept = self.db["documents"].find_one({"scope": "company"}, {"_id": 0})
        self.assertEqual(kept, {k: v for k, v in company.items() if k not in ("needs_review", "expiry_unverified")})
        self.assertEqual(kept["expiry_date"], "2026-10-25")  # dates are never touched
        self.assertIn("needs_review", self.db["documents"].find_one({"document_type": "CV"}))  # not company
        self.assertNotIn("needs_review", self.db["proposal_library"].find_one({}))
        self.assertEqual(self.db["proposal_library"].find_one({})["response_text"], "r")
        self.assertEqual(m0015.apply(self.db)["cleaned"], {"documents": 0, "proposal_library": 0})  # re-run is a no-op

    def test_missing_proposal_library_collection_is_fine(self):
        self.assertEqual(m0015.apply(self.db)["cleaned"], {"documents": 0, "proposal_library": 0})


if __name__ == "__main__":
    unittest.main()
