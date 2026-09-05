"""Writes the run report and prints the console summary.

Every index row appears in the report exactly once — written, skipped, or
rejected — so the report is a complete account of the run, and the rejected
rows are a to-do list for fixing the spreadsheet.
"""

import csv
import sys
from pathlib import Path
from typing import Optional, Sequence, Tuple

from .config import NAMING_FIELDS
from .splitter import STATUS_FAILED, STATUS_WRITTEN, SplitOutcome
from .validation import RejectedRow

REPORT_FILE_NAME = "_split_report.csv"

# "run_notes" rather than "notes": the index itself may have a Notes/Remarks
# column, and a duplicate header would silently overwrite one with the other.
_BASE_COLUMNS = [
    "sheet_row", "status", "reason", "file_name",
    "start_page", "end_page", "page_count", "run_notes",
]


def write_report(
    path: Path,
    outcomes: Sequence[SplitOutcome],
    rejected: Sequence[RejectedRow],
) -> None:
    """Write one CSV row per index row, ordered by position in the sheet.

    The metadata columns are carried through so this file can double as a
    checklist when the split documents are later logged in the app.
    """
    rows = []
    for outcome in outcomes:
        doc = outcome.document
        row = {
            "sheet_row": doc.row.row_number,
            "status": outcome.status,
            "reason": outcome.message,
            "file_name": doc.file_name,
            "start_page": doc.start_page,
            "end_page": doc.end_page,
            "page_count": doc.page_count,
            "run_notes": "; ".join(doc.notes),
        }
        row.update({f: doc.row.get(f) for f in NAMING_FIELDS})
        rows.append(row)

    for item in rejected:
        row = {
            "sheet_row": item.row.row_number,
            "status": "rejected",
            "reason": (
                "{r} - {d}".format(r=item.reason, d=item.detail)
                if item.detail else item.reason
            ),
            "file_name": "",
            "start_page": "",
            "end_page": "",
            "page_count": "",
            "run_notes": "index says pages {p}".format(p=item.page_label) if item.page_label else "",
        }
        row.update({f: item.row.get(f) for f in NAMING_FIELDS})
        rows.append(row)

    rows.sort(key=lambda r: r["sheet_row"])

    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" is required on Windows, otherwise csv writes blank lines.
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_BASE_COLUMNS + NAMING_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _print(line: str = "") -> None:
    print(line, file=sys.stdout)


def print_summary(
    outcomes: Sequence[SplitOutcome],
    rejected: Sequence[RejectedRow],
    gaps: Sequence[Tuple[int, int]],
    total_pages: int,
    output_dir: Path,
    report_path: Optional[Path] = None,
    dry_run: bool = False,
) -> None:
    """Print what happened, leading with the rows that need attention."""
    counts: dict = {}
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1

    if rejected:
        _print()
        _print("Rows not split ({n}) - nothing was guessed for these:".format(n=len(rejected)))
        for item in rejected:
            pages = " [{p}]".format(p=item.page_label) if item.page_label else ""
            _print("  row {r}{pages}: {reason}".format(
                r=item.row.row_number, pages=pages, reason=item.reason))
            if item.detail:
                _print("      {d}".format(d=item.detail))

    failures = [o for o in outcomes if o.status == STATUS_FAILED]
    if failures:
        _print()
        _print("Failed to write ({n}):".format(n=len(failures)))
        for outcome in failures:
            _print("  row {r}: {name} - {msg}".format(
                r=outcome.document.row.row_number,
                name=outcome.document.file_name,
                msg=outcome.message,
            ))

    notable = [o for o in outcomes if o.document.notes and o.status != STATUS_FAILED]
    if notable:
        _print()
        _print("Written with assumptions recorded ({n}):".format(n=len(notable)))
        for outcome in notable:
            _print("  row {r}: {name} - {notes}".format(
                r=outcome.document.row.row_number,
                name=outcome.document.file_name,
                notes="; ".join(outcome.document.notes),
            ))

    if gaps:
        shown = ", ".join(
            str(start) if start == end else "{s}-{e}".format(s=start, e=end)
            for start, end in gaps[:12]
        )
        more = " (+{n} more)".format(n=len(gaps) - 12) if len(gaps) > 12 else ""
        _print()
        _print("Source pages not covered by any document: {shown}{more}".format(
            shown=shown, more=more))

    _print()
    _print("Summary")
    _print("  source pages      {n}".format(n=total_pages))
    _print("  index rows        {n}".format(n=len(outcomes) + len(rejected)))
    for status in sorted(counts):
        _print("  {s:<17} {n}".format(s=status, n=counts[status]))
    _print("  rejected          {n}".format(n=len(rejected)))
    _print("  output folder     {p}".format(p=output_dir))
    if report_path:
        _print("  report            {p}".format(p=report_path))
    if dry_run:
        _print()
        _print("Dry run - no files were written. Re-run without --dry-run to create them.")
    elif counts.get(STATUS_WRITTEN):
        _print()
        _print("Done. {n} document(s) written.".format(n=counts[STATUS_WRITTEN]))
