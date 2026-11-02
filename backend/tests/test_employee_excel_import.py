import tempfile
import unittest
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import mongomock
from bson import ObjectId

from app.employee_excel_import import (
    EmployeeImportTargetError,
    _validate_local_target,
    apply_employee_import,
    load_employee_workbooks,
    plan_employee_import,
)


def _write_xlsx(path, worksheets):
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationships = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_relationships = "http://schemas.openxmlformats.org/package/2006/relationships"
    ET.register_namespace("", namespace)
    ET.register_namespace("r", relationships)
    workbook = ET.Element("{%s}workbook" % namespace)
    sheet_list = ET.SubElement(workbook, "{%s}sheets" % namespace)
    workbook_relationships = ET.Element("{%s}Relationships" % package_relationships)
    sheet_documents = {}
    for index, (name, rows) in enumerate(worksheets.items(), start=1):
        relation_id = "rId%d" % index
        ET.SubElement(
            sheet_list,
            "{%s}sheet" % namespace,
            {"name": name, "sheetId": str(index), "{%s}id" % relationships: relation_id},
        )
        ET.SubElement(
            workbook_relationships,
            "{%s}Relationship" % package_relationships,
            {
                "Id": relation_id,
                "Type": relationships + "/worksheet",
                "Target": "worksheets/sheet%d.xml" % index,
            },
        )
        sheet = ET.Element("{%s}worksheet" % namespace)
        sheet_data = ET.SubElement(sheet, "{%s}sheetData" % namespace)
        for row_number, values in enumerate(rows, start=1):
            row = ET.SubElement(
                sheet_data, "{%s}row" % namespace, {"r": str(row_number)}
            )
            for column_index, value in enumerate(values):
                if value is None:
                    continue
                column = chr(ord("A") + column_index)
                cell = ET.SubElement(
                    row,
                    "{%s}c" % namespace,
                    {"r": "%s%d" % (column, row_number), "t": "inlineStr"},
                )
                inline = ET.SubElement(cell, "{%s}is" % namespace)
                text = ET.SubElement(inline, "{%s}t" % namespace)
                text.text = str(value)
        sheet_documents["xl/worksheets/sheet%d.xml" % index] = ET.tostring(
            sheet, encoding="utf-8", xml_declaration=True
        )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as workbook_zip:
        workbook_zip.writestr(
            "xl/workbook.xml",
            ET.tostring(workbook, encoding="utf-8", xml_declaration=True),
        )
        workbook_zip.writestr(
            "xl/_rels/workbook.xml.rels",
            ET.tostring(
                workbook_relationships, encoding="utf-8", xml_declaration=True
            ),
        )
        for name, content in sheet_documents.items():
            workbook_zip.writestr(name, content)


class EmployeeExcelImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _workbooks(self):
        source = self.directory / "source.xlsx"
        ai = self.directory / "ai.xlsx"
        _write_xlsx(
            source,
            {
                "VGIL": [
                    [
                        "EMPLOYEE. ID",
                        "Sr No",
                        "DEPARTMENT",
                        "DESIGNATION",
                        "EMPNAME",
                        "Employee id",
                        "CERTIFICATION",
                    ],
                    ["34.0", "1", "Engineering", "Director", "Sachin Burghate", "611", "1. Certificate A 2. Certificate B"],
                    ["611.0", "2", "Sales", "VP", "Ashutosh Gholap", "34", "Should not import"],
                    ["611.0", "3", "Sales", "VP", "Ashutosh Vijay Gholap", "34", "Should not import"],
                    ["900.0", "4", "Sales", "Analyst", "Rahul Sharma", "611", ""],
                ],
                "Fintech": [
                    ["Sr No", "DEPARTMENT", "DESIGNATION", "EMPNAME", "Employee id", "CERTIFICATION"],
                    ["1", "Security", "Analyst", "Sujal Thakur", "34", "Fintech cert"],
                ],
            },
        )
        _write_xlsx(
            ai,
            {
                "AI Certificates": [
                    ["SR.NO", "EMP ID", "NAME", "AI CERTIFICATIONS", "CERTIFICATIONS NAMES"],
                    ["1", "34", "Sachin Burghate", "link", "• AI Certificate"],
                    ["2", "611", "Ashutosh Gholap", "link", "Excluded AI cert"],
                    ["3", "34", "AI New Person", "link", "AI-only certificate"],
                    ["4", "34", "Joint New", "link", "Shared-source certificate"],
                    ["5", "34", "Rahul K Sharma", "link", "Uncertain identity certificate"],
                ],
                "Education": [
                    ["Name", "Qualification"],
                    ["Sachin Burghate", "MCA, MBA"],
                    ["Education New Person", "BSc, MSc"],
                    ["Joint New", "BA"],
                ],
            },
        )
        return source, ai

    def test_real_workbooks_load_verified_pilot_and_use_column_a(self):
        from app.employee_excel_import import DEFAULT_AI_WORKBOOK, DEFAULT_SOURCE_WORKBOOK

        sources, conflicts, _counts = load_employee_workbooks(
            DEFAULT_SOURCE_WORKBOOK,
            DEFAULT_AI_WORKBOOK,
            ["vgil:34", "vgil:301", "vgil:995"],
        )

        self.assertEqual(conflicts, [])
        self.assertEqual(
            [source["source_identity"] for source in sources],
            ["vgil:34", "vgil:301", "vgil:995"],
        )
        self.assertEqual(
            {source["source_identity"]: source["full_name"] for source in sources},
            {
                "vgil:34": "SACHIN BURGHATE",
                "vgil:301": "ANJALI VINAY PADHYE",
                "vgil:995": "TEJAS KALE",
            },
        )
        self.assertTrue(all(source["company"] == "VGIL" for source in sources))
        self.assertEqual(sum(len(source["certifications"]) for source in sources), 8)
        self.assertEqual(sum(len(source["education"]) for source in sources), 9)

    def test_real_workbook_plan_includes_all_valid_vgil_ids_and_skips_both_611_rows(self):
        sources, _review, _counts = load_employee_workbooks()
        vgil = [
            item
            for item in sources
            if item.get("source_identity", "").startswith("vgil:")
            and item.get("action") != "skip"
        ]
        excluded = [
            item
            for item in sources
            if item.get("source_identity") == "vgil:611"
            and item.get("action") == "skip"
        ]

        self.assertEqual(len(vgil), 476)
        self.assertEqual(len(excluded), 2)
        self.assertEqual(
            {item["source_identity"] for item in excluded}, {"vgil:611"}
        )

    def test_column_a_is_authoritative_and_both_611_rows_are_excluded(self):
        source, ai = self._workbooks()
        records, _conflicts, _counts = load_employee_workbooks(
            source,
            ai,
            [
                "vgil:34",
                "vgil:611",
                "fintech:sujal thakur",
                "unknown:ai new person",
                "unknown:joint new",
                "unknown:rahul k sharma",
                "unknown:education new person",
            ],
        )

        vgil = next(item for item in records if item["source_identity"] == "vgil:34")
        fintech = next(
            item
            for item in records
            if item["source_identity"] == "fintech:sujal thakur"
        )
        ai_new = next(
            item
            for item in records
            if item["source_identity"] == "unknown:ai new person"
        )
        joint = next(
            item for item in records if item["source_identity"] == "unknown:joint new"
        )
        education_new = next(
            item
            for item in records
            if item["source_identity"] == "unknown:education new person"
        )
        excluded = [item for item in records if item["source_identity"] == "vgil:611"]
        manual_review = [
            item
            for item in _conflicts
            if item["name"] == "Rahul K Sharma"
        ]
        self.assertEqual(vgil["employee_code"], "34")
        self.assertEqual(vgil["certifications"], ["Certificate A", "Certificate B", "AI Certificate"])
        self.assertEqual(vgil["education"], ["MCA", "MBA"])
        self.assertEqual(fintech["company"], "Fintech")
        self.assertEqual(fintech["employee_code"], "")
        self.assertEqual(ai_new["company"], "Unknown")
        self.assertEqual(ai_new["source_identity"], "unknown:ai new person")
        self.assertEqual(ai_new["certifications"], ["AI-only certificate"])
        self.assertEqual(joint["certifications"], ["Shared-source certificate"])
        self.assertEqual(joint["education"], ["BA"])
        self.assertEqual(education_new["company"], "Unknown")
        self.assertEqual(education_new["education"], ["BSc", "MSc"])
        self.assertEqual(len(manual_review), 1)
        self.assertEqual(manual_review[0]["source_workbook"], "ai.xlsx")
        self.assertEqual(manual_review[0]["sheet"], "AI Certificates")
        self.assertEqual(manual_review[0]["row"], 6)
        self.assertEqual(manual_review[0]["possible_matches"][0]["name"], "Rahul Sharma")
        self.assertNotIn(
            "unknown:ashutosh gholap",
            {item["source_identity"] for item in records},
        )
        self.assertEqual(len(excluded), 2)
        self.assertTrue(all(item["action"] == "skip" for item in excluded))

    def test_dry_run_reports_create_without_writing(self):
        database = mongomock.MongoClient()["employee_import_test"]
        source = {
            "source_identity": "fintech:sujal thakur",
            "full_name": "Sujal Thakur",
            "company": "Fintech",
            "employee_code": "",
            "department": "Security",
            "designation": "Analyst",
            "education": [],
            "certifications": ["Security certificate"],
            "source_conflicts": [],
        }

        report = plan_employee_import([source], [])

        self.assertEqual(report["counts"]["create"], 1)
        self.assertEqual(database.employees.count_documents({}), 0)

    def test_create_apply_sets_separate_fields_without_certificate_files(self):
        employees = mongomock.MongoClient()["employee_import_test"]["employees"]
        source = {
            "source_identity": "fintech:sujal thakur",
            "full_name": "Sujal Thakur",
            "company": "Fintech",
            "employee_code": "",
            "department": "Security",
            "designation": "Analyst",
            "education": ["BSc", "MSc"],
            "certifications": ["Security certificate"],
            "source_conflicts": [],
        }

        report = apply_employee_import(employees, [source])
        inserted = employees.find_one({"source_identity": "fintech:sujal thakur"})

        self.assertEqual(report["counts"]["create"], 1)
        self.assertEqual(inserted["company"], "Fintech")
        self.assertEqual(inserted["education"], ["BSc", "MSc"])
        self.assertNotIn("highest_qualification", inserted)
        self.assertEqual(len(inserted["certifications"]), 1)
        self.assertIsNone(inserted["certifications"][0]["document_id"])
        self.assertIsNone(inserted["certifications"][0]["file_name"])
        self.assertEqual(inserted["cvs"], [])
        self.assertEqual(apply_employee_import(employees, [source])["counts"]["skip"], 1)

    def test_apply_is_idempotent_and_preserves_existing_employee_data(self):
        database = mongomock.MongoClient()["employee_import_test"]
        employees = database["employees"]
        employee_id = ObjectId()
        existing_cv = {"id": "existing-cv", "version_label": "Current CV"}
        existing_certification = {
            "id": "existing-cert",
            "name": "Already held",
            "document_id": "existing-document",
        }
        employees.insert_one(
            {
                "_id": employee_id,
                "full_name": "Pilot Employee",
                "employee_code": "34",
                "company": "VGIL",
                "source_identity": "vgil:34",
                "designation": "Keep designation",
                "department": "Keep department",
                "education": ["MCA"],
                "cvs": [existing_cv],
                "certifications": [existing_certification],
                "notes": "Keep details",
            }
        )
        source = {
            "source_identity": "vgil:34",
            "full_name": "Pilot Employee",
            "company": "VGIL",
            "employee_code": "34",
            "department": "Source department",
            "designation": "Source designation",
            "education": ["MCA", "MBA"],
            "certifications": ["Already held", "New certificate"],
            "source_conflicts": [],
        }

        first = apply_employee_import(employees, [source])
        second = apply_employee_import(employees, [source])
        updated = employees.find_one({"_id": employee_id})

        self.assertEqual(first["counts"]["update"], 1)
        self.assertEqual(second["counts"]["skip"], 1)
        self.assertEqual(updated["education"], ["MCA", "MBA"])
        self.assertEqual(
            [item["name"] for item in updated["certifications"]],
            ["Already held", "New certificate"],
        )
        self.assertEqual(updated["certifications"][0], existing_certification)
        self.assertEqual(updated["cvs"], [existing_cv])
        self.assertEqual(updated["designation"], "Keep designation")
        self.assertEqual(updated["department"], "Keep department")
        self.assertEqual(updated["notes"], "Keep details")
        self.assertIsNone(updated["certifications"][1]["document_id"])
        self.assertIsNone(updated["certifications"][1]["file_name"])

    def test_ambiguous_name_and_identity_conflict_are_flagged(self):
        source = {
            "source_identity": "vgil:34",
            "full_name": "Pilot Employee",
            "company": "VGIL",
            "employee_code": "34",
            "education": [],
            "certifications": [],
            "source_conflicts": [],
        }
        ambiguous = [
            {"_id": ObjectId(), "full_name": "Pilot Employee"},
            {"_id": ObjectId(), "full_name": "  PILOT   EMPLOYEE "},
        ]
        report = plan_employee_import([source], ambiguous)
        self.assertEqual(report["counts"]["review"], 1)
        self.assertIn("Multiple existing employees", report["actions"][0]["reason"])

        conflicting = [
            {
                "_id": ObjectId(),
                "full_name": "Different Employee",
                "source_identity": "vgil:34",
            }
        ]
        report = plan_employee_import([source], conflicting)
        self.assertEqual(report["counts"]["review"], 1)
        self.assertIn("different name", report["actions"][0]["reason"])

    def test_possible_existing_name_match_enters_manual_review(self):
        source = {
            "source_identity": "unknown:rahul k sharma",
            "full_name": "Rahul K Sharma",
            "company": "Unknown",
            "employee_code": "",
            "department": "",
            "designation": "",
            "education": ["MBA"],
            "certifications": ["AI certificate"],
            "provenance": [
                {
                    "source_workbook": "AI CERTIFICATION SHEET.xlsx",
                    "sheet": "AI Certificates",
                    "row": 27,
                }
            ],
            "source_conflicts": [],
        }
        existing = [
            {
                "_id": ObjectId(),
                "full_name": "Rahul Sharma",
                "source_identity": "vgil:6111",
                "company": "VGIL",
            }
        ]

        report = plan_employee_import([source], existing)

        self.assertEqual(report["counts"]["review"], 1)
        review = report["manual_name_review"][0]
        self.assertEqual(review["source_workbook"], "AI CERTIFICATION SHEET.xlsx")
        self.assertEqual(review["sheet"], "AI Certificates")
        self.assertEqual(review["row"], 27)
        self.assertEqual(review["possible_matches"][0]["name"], "Rahul Sharma")
        self.assertIn("confirm the identity", review["reason"])

    def test_unknown_affiliation_source_enriches_exact_name_without_reassigning_identity(self):
        source = {
            "source_identity": "unknown:pilot employee",
            "full_name": "Pilot Employee",
            "company": "Unknown",
            "employee_code": "",
            "department": "",
            "designation": "",
            "education": ["MBA"],
            "certifications": ["AI certificate"],
            "provenance": [
                {"source_workbook": "AI.xlsx", "sheet": "AI Certificates", "row": 4}
            ],
            "source_conflicts": [],
        }
        existing = [
            {
                "_id": ObjectId(),
                "full_name": "PILOT EMPLOYEE",
                "source_identity": "vgil:34",
                "company": "VGIL",
                "employee_code": "34",
                "education": ["MCA"],
                "certifications": [],
                "cvs": [{"id": "keep-cv"}],
                "designation": "Keep designation",
            }
        ]

        employees = mongomock.MongoClient()["employee_import_test"]["employees"]
        employees.insert_one(existing[0])

        report = apply_employee_import(employees, [source])
        updated = employees.find_one({"source_identity": "vgil:34"})

        self.assertEqual(report["counts"]["update"], 1)
        action = report["existing_employees_to_update"][0]
        self.assertEqual(action["employee_id"], existing[0]["_id"])
        self.assertNotIn("source identity", " ".join(action["changes"]))
        self.assertNotIn("company", " ".join(action["changes"]))
        self.assertEqual(updated["company"], "VGIL")
        self.assertEqual(updated["source_identity"], "vgil:34")
        self.assertEqual(updated["employee_code"], "34")
        self.assertEqual(updated["designation"], "Keep designation")
        self.assertEqual(updated["cvs"], [{"id": "keep-cv"}])
        self.assertEqual(updated["education"], ["MCA", "MBA"])
        self.assertEqual(
            [cert["name"] for cert in updated["certifications"]],
            ["AI certificate"],
        )

    def test_duplicate_primary_names_are_not_imported_automatically(self):
        source, ai = self._workbooks()
        # The synthetic fixture exercises duplicate normalized source names
        # through two VGIL identities rather than trusting a workbook ID.
        source_rows = [
            ["EMPLOYEE. ID", "Sr No", "DEPARTMENT", "DESIGNATION", "EMPNAME", "Employee id", "CERTIFICATION"],
            ["10", "1", "Sales", "Analyst", "Same Name", "999", ""],
            ["11", "2", "Sales", "Analyst", " SAME   NAME ", "10", ""],
        ]
        _write_xlsx(
            source,
            {
                "VGIL": source_rows,
                "Fintech": [
                    ["Sr No", "DEPARTMENT", "DESIGNATION", "EMPNAME", "Employee id", "CERTIFICATION"]
                ],
            },
        )
        _write_xlsx(
            ai,
            {
                "AI Certificates": [
                    ["SR.NO", "EMP ID", "NAME", "AI CERTIFICATIONS", "CERTIFICATIONS NAMES"]
                ],
                "Education": [["Name", "Qualification"]],
            },
        )

        records, review, _counts = load_employee_workbooks(source, ai)
        plan = plan_employee_import(records, [])

        self.assertEqual(len(records), 2)
        self.assertTrue(all(item["source_conflicts"] for item in records))
        self.assertEqual(len(review), 2)
        self.assertTrue(all(item["possible_matches"] for item in review))
        self.assertEqual(plan["counts"]["create"], 0)
        self.assertEqual(plan["counts"]["review"], 2)

    def test_import_target_is_restricted_to_explicit_local_scratch_databases(self):
        _validate_local_target(
            "mongodb://127.0.0.1:27184/",
            "pms_employee_certification_pilot_scratch",
        )
        with self.assertRaises(EmployeeImportTargetError):
            _validate_local_target(
                "mongodb://example.invalid:27017/",
                "pms_employee_certification_pilot_scratch",
            )
        with self.assertRaises(EmployeeImportTargetError):
            _validate_local_target(
                "mongodb://127.0.0.1:27184/",
                "doc_collection_dev",
            )
        _validate_local_target(
            "mongodb://127.0.0.1:27017/",
            "doc_collection_dev",
            allow_doc_collection_dev=True,
        )
        with self.assertRaises(EmployeeImportTargetError):
            _validate_local_target(
                "mongodb://example.invalid:27017/",
                "doc_collection_dev",
                allow_doc_collection_dev=True,
            )


if __name__ == "__main__":
    unittest.main()
