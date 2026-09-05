"""Self-checks for the splitter, focused on the rows it must refuse to guess at.

Run from the project root with the tool's own interpreter:

    scripts\pdf_splitter\.venv\Scripts\python.exe -m unittest discover -s scripts\pdf_splitter\tests

Uses only the standard library's unittest, so no test framework has to be
installed alongside the tool.
"""

import csv
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pdf_splitter.config import DEFAULT_NAME_TEMPLATE, SplitOptions  # noqa: E402
from pdf_splitter.index_reader import IndexReadError, IndexRow, read_index  # noqa: E402
from pdf_splitter.naming import assign_file_names, render_template, sanitise  # noqa: E402
from pdf_splitter.splitter import open_pdf                            # noqa: E402
from pdf_splitter.validation import validate_rows                    # noqa: E402
from pdf_splitter.cli import run                                     # noqa: E402
from pdf_splitter.tests.make_sample import write_sample              # noqa: E402


def row(number: int, **values: str) -> IndexRow:
    return IndexRow(row_number=number, values={k: str(v) for k, v in values.items()})


class PageRangeTests(unittest.TestCase):
    """A range is only accepted when it can be read exactly one way."""

    def plan(self, rows, total_pages=20, **kwargs):
        return validate_rows(rows, total_pages=total_pages, **kwargs)

    def test_explicit_start_and_end_accepted(self):
        result = self.plan([row(2, start_page="3", end_page="5")])
        self.assertEqual(len(result.planned), 1)
        self.assertEqual((result.planned[0].start_page, result.planned[0].end_page), (3, 5))

    def test_combined_range_forms_accepted(self):
        for text in ["3-5", "3 – 5", "3 to 5", "3..5", "p3-p5", "Page 3 - Page 5"]:
            with self.subTest(text=text):
                result = self.plan([row(2, pages=text)])
                self.assertEqual(len(result.planned), 1, text)
                self.assertEqual(result.planned[0].end_page, 5)

    def test_single_page_document(self):
        result = self.plan([row(2, pages="7")])
        self.assertEqual((result.planned[0].start_page, result.planned[0].end_page), (7, 7))

    def test_non_numeric_page_rejected_not_coerced(self):
        result = self.plan([row(2, start_page="12A", end_page="14")])
        self.assertEqual(result.planned, [])
        self.assertIn("not a whole number", result.rejected[0].detail)

    def test_multiple_ranges_in_one_cell_rejected(self):
        result = self.plan([row(2, pages="3, 7, 9")])
        self.assertEqual(result.planned, [])
        self.assertIn("more than one range", result.rejected[0].detail)

    def test_conflicting_page_columns_rejected(self):
        result = self.plan([row(2, start_page="3", end_page="5", pages="4-9")])
        self.assertEqual(result.planned, [])
        self.assertIn("disagree", result.rejected[0].detail)

    def test_blank_page_columns_rejected(self):
        result = self.plan([row(2, start_page="", end_page="", client_name="Acme")])
        self.assertEqual(result.rejected[0].reason, "no page range given")

    def test_reversed_range_rejected(self):
        result = self.plan([row(2, start_page="9", end_page="4")])
        self.assertEqual(result.rejected[0].reason, "end page before start page")

    def test_range_past_end_of_pdf_rejected(self):
        result = self.plan([row(2, start_page="18", end_page="40")], total_pages=20)
        self.assertEqual(result.rejected[0].reason, "end page out of range")

    def test_page_zero_rejected(self):
        result = self.plan([row(2, start_page="0", end_page="3")])
        self.assertEqual(result.rejected[0].reason, "start page out of range")

    def test_missing_end_page_rejected_by_default(self):
        result = self.plan([row(2, start_page="4"), row(3, start_page="9", end_page="11")])
        self.assertEqual(len(result.planned), 1)
        self.assertEqual(result.rejected[0].reason, "end page missing")

    def test_missing_end_page_inferred_only_when_asked(self):
        result = self.plan(
            [row(2, start_page="4"), row(3, start_page="9", end_page="11")],
            infer_end_page=True,
        )
        inferred = [d for d in result.planned if d.row.row_number == 2][0]
        self.assertEqual(inferred.end_page, 8)
        self.assertTrue(any("inferred" in n for n in inferred.notes))

    def test_last_row_infers_to_end_of_pdf(self):
        result = self.plan([row(2, start_page="16")], total_pages=20, infer_end_page=True)
        self.assertEqual(result.planned[0].end_page, 20)

    def test_inference_refused_when_two_rows_share_a_start(self):
        result = self.plan(
            [row(2, start_page="4"), row(3, start_page="4", end_page="6")],
            infer_end_page=True,
        )
        self.assertTrue(any("could not be inferred" in r.reason for r in result.rejected))


class OverlapTests(unittest.TestCase):
    def test_overlapping_rows_both_rejected(self):
        result = validate_rows(
            [row(2, start_page="1", end_page="5"), row(3, start_page="4", end_page="8")],
            total_pages=20,
        )
        self.assertEqual(result.planned, [])
        self.assertEqual(len(result.rejected), 2)
        self.assertTrue(all("overlaps" in r.reason for r in result.rejected))

    def test_identical_ranges_both_rejected(self):
        result = validate_rows(
            [row(2, start_page="1", end_page="5"), row(3, start_page="1", end_page="5")],
            total_pages=20,
        )
        self.assertEqual(len(result.rejected), 2)

    def test_overlaps_kept_when_explicitly_allowed(self):
        result = validate_rows(
            [row(2, start_page="1", end_page="5"), row(3, start_page="4", end_page="8")],
            total_pages=20,
            allow_overlaps=True,
        )
        self.assertEqual(len(result.planned), 2)

    def test_touching_ranges_do_not_overlap(self):
        result = validate_rows(
            [row(2, start_page="1", end_page="5"), row(3, start_page="6", end_page="8")],
            total_pages=20,
        )
        self.assertEqual(len(result.planned), 2)

    def test_gaps_are_reported_not_rejected(self):
        result = validate_rows([row(2, start_page="5", end_page="6")], total_pages=10)
        self.assertEqual(len(result.planned), 1)
        self.assertEqual(result.uncovered_pages, [(1, 4), (7, 10)])


class ColumnMappingTests(unittest.TestCase):
    """Headers the alias table does not know must be recoverable, not fatal."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pdf_splitter_cols_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _workbook(self, header, *data_rows) -> Path:
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(list(header))
        for data_row in data_rows:
            sheet.append(list(data_row))
        path = self.tmp / "index.xlsx"
        workbook.save(str(path))
        return path

    def test_two_columns_claiming_one_field_is_an_error(self):
        path = self._workbook(
            ["Start Page", "From Page", "End Page", "Client"], [1, 1, 4, "Acme"]
        )
        with self.assertRaises(IndexReadError) as caught:
            read_index(path)
        self.assertIn("both map to start_page", str(caught.exception))

    def test_unrecognised_headers_are_reported_not_silently_dropped(self):
        path = self._workbook(
            ["Start Page", "End Page", "Client", "Box File"], [1, 4, "Acme", "B-12"]
        )
        self.assertEqual(read_index(path).unmapped_headers, ["Box File"])

    def test_column_map_makes_wholly_non_standard_headers_usable(self):
        path = self._workbook(
            ["Pg Range", "Nature of Doc", "Name of Client / Employer"],
            ["1-4", "LOI", "Metro Transit"],
        )
        with self.assertRaises(IndexReadError):
            read_index(path)                      # nothing recognisable on its own

        index = read_index(path, column_map={
            "Pg Range": "pages",
            "Nature of Doc": "document_type",
            "Name of Client / Employer": "client_name",
        })
        self.assertEqual(index.header_row, 1)     # found via the map
        self.assertEqual(index.rows[0].get("pages"), "1-4")


class NamingTests(unittest.TestCase):
    def plan_and_name(self, rows, **kwargs):
        planned = validate_rows(rows, total_pages=50).planned
        return assign_file_names(
            planned,
            template=kwargs.pop("template", DEFAULT_NAME_TEMPLATE),
            max_length=kwargs.pop("max_length", 120),
            **kwargs,
        )

    def test_blank_fields_drop_out_with_their_separator(self):
        rendered = render_template(
            "{document_type} - {client_name} - {reference_number}",
            {"document_type": "LOI", "client_name": "Acme", "reference_number": ""},
        )
        self.assertEqual(rendered, "LOI - Acme")

    def test_illegal_characters_replaced(self):
        self.assertEqual(sanitise('WO/2024:118 <draft>', 120), "WO 2024 118 draft")

    def test_reserved_windows_name_escaped(self):
        self.assertTrue(sanitise("CON", 120).startswith("_"))

    def test_name_truncated_to_limit(self):
        self.assertEqual(len(sanitise("x" * 300, 40)), 40)

    def test_duplicate_names_get_suffix_not_overwritten(self):
        named, rejected = self.plan_and_name([
            row(2, start_page="1", end_page="2", document_type="LOI", client_name="Acme"),
            row(3, start_page="3", end_page="4", document_type="LOI", client_name="Acme"),
        ])
        self.assertEqual(rejected, [])
        self.assertEqual(len({d.file_name for d in named}), 2)
        self.assertTrue(named[1].file_name.endswith("(2).pdf"))

    def test_case_only_difference_still_counts_as_duplicate(self):
        named, _ = self.plan_and_name([
            row(2, start_page="1", end_page="2", document_type="LOI", client_name="acme"),
            row(3, start_page="3", end_page="4", document_type="loi", client_name="ACME"),
        ])
        self.assertNotEqual(named[0].file_name.lower(), named[1].file_name.lower())

    def test_row_with_no_usable_metadata_rejected(self):
        named, rejected = self.plan_and_name([row(2, start_page="1", end_page="2")])
        self.assertEqual(named, [])
        self.assertEqual(rejected[0].reason, "no metadata to build a file name from")

    def test_page_fallback_only_when_asked(self):
        named, rejected = self.plan_and_name(
            [row(2, start_page="1", end_page="2")], fallback_to_pages=True
        )
        self.assertEqual(rejected, [])
        self.assertEqual(named[0].file_name, "pages 1-2.pdf")

    def test_number_prefix_keeps_file_order(self):
        named, _ = self.plan_and_name([
            row(2, start_page="1", end_page="2", client_name="Zeta"),
            row(3, start_page="3", end_page="4", client_name="Alpha"),
        ], number_prefix=True)
        self.assertEqual([n.file_name[:1] for n in named], ["1", "2"])


class EndToEndTests(unittest.TestCase):
    """Runs the whole tool against the generated sample."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="pdf_splitter_test_"))
        cls.sample = cls.tmp / "sample"
        write_sample(cls.sample, page_count=24)
        cls.pdf = cls.sample / "combined_documents.pdf"
        cls.index = cls.sample / "document_index.xlsx"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def options(self, name: str, **kwargs) -> SplitOptions:
        return SplitOptions(
            pdf_path=self.pdf,
            index_path=self.index,
            output_dir=self.tmp / name,
            **kwargs,
        )

    def test_header_row_detected_beneath_a_title_block(self):
        index = read_index(self.index)
        self.assertEqual(index.header_row, 3)
        self.assertEqual(index.columns["start_page"], "Start Page")

    def test_dry_run_writes_nothing(self):
        options = self.options("dry", dry_run=True)
        self.assertEqual(run(options, quiet=True), 2)      # sample has bad rows
        self.assertFalse(options.output_dir.exists())

    def test_written_pdfs_contain_the_right_pages(self):
        options = self.options("real")
        self.assertEqual(run(options, quiet=True), 2)

        written = sorted(p.name for p in options.output_dir.glob("*.pdf"))
        self.assertEqual(len(written), 4)                  # 5 of 9 rows are faulty

        first = options.output_dir / "LOI - Northern Rail - LOI 2024 118 - Signalling upgrade, Phase 2.pdf"
        reader = open_pdf(first)
        self.assertEqual(len(reader.pages), 3)
        self.assertIn("Page 1", reader.pages[0].extract_text())
        self.assertIn("Page 3", reader.pages[2].extract_text())

    def test_report_accounts_for_every_index_row(self):
        options = self.options("report")
        run(options, quiet=True)
        report = options.output_dir / "_split_report.csv"
        self.assertTrue(report.exists())
        lines = report.read_text(encoding="utf-8-sig").strip().splitlines()
        self.assertEqual(len(lines), 1 + 9)                # header + every row

    def test_report_columns_are_unique_and_keep_run_notes(self):
        """The index may have its own Notes column; it must not collide."""
        options = self.options("notes", infer_end_page=True)
        run(options, quiet=True)
        with open(options.output_dir / "_split_report.csv", encoding="utf-8-sig",
                  newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(len(reader.fieldnames), len(set(reader.fieldnames)))
            rows = {r["sheet_row"]: r for r in reader}
        self.assertIn("inferred", rows["8"]["run_notes"])
        self.assertIn("pages 16-14", rows["9"]["run_notes"])

    def test_rerun_does_not_overwrite_without_the_flag(self):
        options = self.options("rerun")
        run(options, quiet=True)
        target = next(options.output_dir.glob("*.pdf"))
        target.write_bytes(b"placeholder")

        run(self.options("rerun"), quiet=True)
        self.assertEqual(target.read_bytes(), b"placeholder")

        run(self.options("rerun", overwrite=True), quiet=True)
        self.assertNotEqual(target.read_bytes(), b"placeholder")

    def test_inference_recovers_the_row_with_a_blank_end_page(self):
        without = run(self.options("no_infer", dry_run=True), quiet=True)
        self.assertEqual(without, 2)

        options = self.options("infer", infer_end_page=True)
        run(options, quiet=True)
        recovered = [p for p in options.output_dir.glob("*.pdf") if "WO 2024 612" in p.name]
        self.assertEqual(len(recovered), 1)
        self.assertEqual(len(open_pdf(recovered[0]).pages), 3)   # pages 13-15


if __name__ == "__main__":
    unittest.main(verbosity=2)
