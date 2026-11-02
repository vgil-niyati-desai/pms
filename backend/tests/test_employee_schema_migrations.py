import unittest

import mongomock
from bson import ObjectId
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app import mongodb, schemas
from app.employee_identity import (
    fintech_source_identity,
    normalize_employee_name,
    vgil_source_identity,
)
from app.routers import employees_mongo
from migrations import (
    MIGRATIONS,
    m0009_employee_company as m0009,
    m0010_employee_education as m0010,
    m0011_employee_source_identity as m0011,
    m0012_employee_source_identity_index as m0012,
)


class EmployeeSchemaTests(unittest.TestCase):
    def test_source_identities_use_authoritative_vgil_ids_and_normalized_fintech_names(self):
        self.assertEqual(vgil_source_identity("34.0"), "vgil:34")
        self.assertEqual(vgil_source_identity("0034"), "vgil:0034")
        self.assertEqual(
            fintech_source_identity("  Sujal   Thakur "),
            "fintech:sujal thakur",
        )
        self.assertEqual(normalize_employee_name("Sujal Thakur"), "sujal thakur")

    def test_source_identity_rejects_missing_authoritative_values(self):
        with self.assertRaises(ValueError):
            vgil_source_identity("")
        with self.assertRaises(ValueError):
            fintech_source_identity("  ")

    def test_company_and_education_are_separate_employee_fields(self):
        employee = schemas.EmployeeIn.model_validate(
            {
                "full_name": "Pilot Employee",
                "designation": "Analyst",
                "education": [" MCA ", "", "MCA", "MBA"],
            }
        )

        self.assertEqual(employee.company, "Unknown")
        self.assertEqual(employee.education, ["MCA", "MBA"])
        self.assertIsNone(employee.highest_qualification)

    def test_company_only_accepts_supported_affiliations(self):
        with self.assertRaises(ValidationError):
            schemas.EmployeeIn.model_validate(
                {
                    "full_name": "Pilot Employee",
                    "designation": "Analyst",
                    "company": "Other",
                }
            )

    def test_source_identity_is_returned_but_not_part_of_profile_input(self):
        employee = schemas.EmployeeIn.model_validate(
            {
                "full_name": "Pilot Employee",
                "designation": "Analyst",
                "source_identity": "vgil:34",
            }
        )
        self.assertNotIn("source_identity", employee.model_dump())

    def test_profile_update_without_new_fields_preserves_them(self):
        database = mongomock.MongoClient(tz_aware=True)["pms_employee_update_test"]
        employees = database["employees"]
        employee_id = ObjectId()
        employees.insert_one(
            {
                "_id": employee_id,
                "full_name": "Pilot Employee",
                "designation": "Analyst",
                "company": "VGIL",
                "education": ["MCA"],
                "cvs": [{"id": "cv-1"}],
                "certifications": [{"id": "cert-1", "name": "Existing certificate"}],
            }
        )
        payload = schemas.EmployeeIn.model_validate(
            {"full_name": "Pilot Employee", "designation": "Analyst"}
        )

        # update_employee also takes the documents collection (certification files).
        employees_mongo.update_employee(
            str(employee_id), payload, employees, database["documents"]
        )
        updated = employees.find_one({"_id": employee_id})

        self.assertEqual(updated["company"], "VGIL")
        self.assertEqual(updated["education"], ["MCA"])
        self.assertEqual(updated["cvs"], [{"id": "cv-1"}])
        self.assertEqual(
            updated["certifications"], [{"id": "cert-1", "name": "Existing certificate"}]
        )


class EmployeeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient(tz_aware=True)["pms_employee_migration_test"]
        self.employees = self.db["employees"]

    def test_migrations_0009_through_0012_are_registered_in_order(self):
        # Later migrations may follow; these four must stay consecutive and in order.
        versions = [migration.VERSION for migration in MIGRATIONS]
        start = versions.index(m0009.VERSION)
        self.assertEqual(
            versions[start : start + 4],
            [m0009.VERSION, m0010.VERSION, m0011.VERSION, m0012.VERSION],
        )

    def test_company_backfill_preserves_existing_employee_data(self):
        cv = {"id": "cv-1", "version_label": "Existing CV"}
        certification = {"id": "cert-1", "name": "Existing certification"}
        employee_id = self.employees.insert_one(
            {
                "full_name": "Legacy employee",
                "company": None,
                "cvs": [cv],
                "certifications": [certification],
                "notes": "keep this",
            }
        ).inserted_id
        self.employees.insert_one({"full_name": "Fintech employee", "company": "Fintech"})

        report = m0009.apply(self.db)
        updated = self.employees.find_one({"_id": employee_id})

        self.assertEqual(report["updated"], 1)
        self.assertEqual(updated["company"], "Unknown")
        self.assertEqual(updated["cvs"], [cv])
        self.assertEqual(updated["certifications"], [certification])
        self.assertEqual(updated["notes"], "keep this")
        self.assertEqual(m0009.apply(self.db)["updated"], 0)

    def test_company_migration_refuses_unknown_values_without_writing(self):
        employee_id = self.employees.insert_one(
            {"full_name": "Legacy employee", "company": ["Fintech"]}
        ).inserted_id
        before = self.employees.find_one({"_id": employee_id})

        with self.assertRaisesRegex(RuntimeError, "nothing was written"):
            m0009.apply(self.db)

        self.assertEqual(self.employees.find_one({"_id": employee_id}), before)

    def test_education_migration_initializes_only_absent_values(self):
        self.employees.insert_many(
            [
                {"full_name": "Missing education"},
                {"full_name": "Null education", "education": None},
                {"full_name": "Known education", "education": ["MCA"]},
            ]
        )

        report = m0010.apply(self.db)

        self.assertEqual(report["updated"], 2)
        self.assertEqual(
            [record["education"] for record in self.employees.find({}, {"education": 1})],
            [[], [], ["MCA"]],
        )

    def test_education_migration_refuses_invalid_values_without_partial_writes(self):
        self.employees.insert_many(
            [
                {"full_name": "Missing education"},
                {"full_name": "Invalid education", "education": "MCA"},
            ]
        )
        before = list(self.employees.find({}))

        with self.assertRaisesRegex(RuntimeError, "nothing was written"):
            m0010.apply(self.db)

        self.assertEqual(list(self.employees.find({})), before)

    def test_source_identity_migration_initializes_without_guessing_legacy_ids(self):
        self.employees.insert_many(
            [
                {"full_name": "Legacy employee"},
                {"full_name": "Blank identity", "source_identity": "  "},
                {"full_name": "Imported employee", "source_identity": "vgil:34"},
            ]
        )

        report = m0011.apply(self.db)
        records = list(self.employees.find({}, {"source_identity": 1}))

        self.assertEqual(report["updated"], 2)
        self.assertEqual([record["source_identity"] for record in records], [None, None, "vgil:34"])

    def test_source_identity_migration_refuses_non_string_values(self):
        employee_id = self.employees.insert_one(
            {"full_name": "Bad identity", "source_identity": 34}
        ).inserted_id
        before = self.employees.find_one({"_id": employee_id})

        with self.assertRaisesRegex(RuntimeError, "nothing was written"):
            m0011.apply(self.db)

        self.assertEqual(self.employees.find_one({"_id": employee_id}), before)

    def test_source_identity_index_enforces_unique_non_null_strings(self):
        self.employees.insert_many(
            [
                {"full_name": "Imported employee", "source_identity": "vgil:34"},
                {"full_name": "Legacy employee", "source_identity": None},
            ]
        )

        report = m0012.apply(self.db)

        self.assertTrue(report["created"])
        index = self.employees.index_information()[m0012.INDEX_NAME]
        self.assertEqual(index["key"], [("source_identity", 1)])
        self.assertTrue(index["unique"])
        self.assertEqual(
            index["partialFilterExpression"],
            {"source_identity": {"$type": "string"}},
        )
        self.assertFalse(m0012.apply(self.db)["created"])
        with self.assertRaises(DuplicateKeyError):
            self.employees.insert_one(
                {"full_name": "Duplicate identity", "source_identity": "vgil:34"}
            )
        self.employees.insert_one(
            {"full_name": "Another legacy employee", "source_identity": None}
        )

    def test_source_identity_index_migration_refuses_duplicates_without_writing(self):
        self.employees.insert_many(
            [
                {"full_name": "First", "source_identity": "fintech:alex smith"},
                {"full_name": "Second", "source_identity": "fintech:alex smith"},
            ]
        )
        before = list(self.employees.find({}))

        with self.assertRaisesRegex(m0012.EmployeeIdentityIndexConflict, "nothing was written"):
            m0012.apply(self.db)

        self.assertEqual(list(self.employees.find({})), before)
        self.assertNotIn(m0012.INDEX_NAME, self.employees.index_information())

    def test_migration_index_matches_employee_startup_index(self):
        startup = mongomock.MongoClient()["pms_employee_startup_test"]["employees"]
        mongodb.ensure_employee_indexes(startup)
        m0012.apply(self.db)

        self.assertEqual(
            self.employees.index_information()[m0012.INDEX_NAME],
            startup.index_information()[m0012.INDEX_NAME],
        )

    def test_all_migrations_leave_cvs_and_certifications_unchanged(self):
        cvs = [{"id": "cv-1", "file_name": "existing.pdf"}]
        certifications = [{"id": "cert-1", "name": "Existing certification"}]
        self.employees.insert_one(
            {
                "full_name": "Existing employee",
                "cvs": cvs,
                "certifications": certifications,
            }
        )

        for migration in (m0009, m0010, m0011, m0012):
            report = migration.apply(self.db)
            migration.record(self.db, report)

        employee = self.employees.find_one({"full_name": "Existing employee"})
        self.assertEqual(employee["cvs"], cvs)
        self.assertEqual(employee["certifications"], certifications)
        self.assertEqual(employee["company"], "Unknown")
        self.assertEqual(employee["education"], [])
        self.assertIsNone(employee["source_identity"])
        self.assertEqual(self.db["migrations"].count_documents({}), 4)


if __name__ == "__main__":
    unittest.main()
