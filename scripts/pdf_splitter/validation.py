"""Turns index rows into page ranges, rejecting anything unclear.

The guiding rule: a row is only split out when its range can be read one way
and one way only. Every other row is rejected with a reason and reported, so
the operator fixes the spreadsheet rather than the tool inventing an answer.
Guessing here would produce a plausible-looking PDF containing the wrong pages,
which is worse than producing nothing.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import re

from .index_reader import IndexRow

# Accepts "12", "12.0", "p12", "pg. 12", "page 12". Anything else is treated as
# unreadable rather than stripped down to its digits — "12A" and "12-13" must
# not quietly become 12.
_PAGE_NUMBER = re.compile(r"^(?:p|pg|page)?\s*\.?\s*(\d+)(?:\.0+)?$", re.IGNORECASE)

# "12-18", "12 – 18", "12 to 18", "12..18", "12 through 18".
_PAGE_RANGE = re.compile(
    r"^(?P<start>.+?)\s*(?:-|–|—|\.\.|to|through)\s*(?P<end>.+)$", re.IGNORECASE
)


@dataclass
class PlannedDocument:
    """A row that produced one unambiguous, in-bounds page range."""

    row: IndexRow
    start_page: int                                   # 1-based, inclusive
    end_page: int                                     # 1-based, inclusive
    notes: List[str] = field(default_factory=list)    # e.g. "end page inferred"
    file_name: str = ""                               # filled in by naming

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page + 1

    @property
    def page_label(self) -> str:
        if self.start_page == self.end_page:
            return str(self.start_page)
        return "{s}-{e}".format(s=self.start_page, e=self.end_page)


@dataclass
class RejectedRow:
    """A row that was not split out, and why."""

    row: IndexRow
    reason: str
    detail: str = ""

    @property
    def page_label(self) -> str:
        """Whatever the row actually said about its pages, blanks marked "?"."""
        start = self.row.get("start_page").strip()
        end = self.row.get("end_page").strip()
        if start or end:
            return "{s}-{e}".format(s=start or "?", e=end or "?")
        return self.row.get("pages").strip()


@dataclass
class ValidationResult:
    planned: List[PlannedDocument]
    rejected: List[RejectedRow]
    uncovered_pages: List[Tuple[int, int]]     # inclusive (start, end) gaps


def _parse_page_number(text: str) -> Optional[int]:
    match = _PAGE_NUMBER.match(text.strip())
    return int(match.group(1)) if match else None


def _parse_pages_cell(text: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Parse a combined page cell into (start, end, error).

    Non-contiguous lists ("3, 7, 9") are rejected: a single output PDF can only
    be cut from one continuous run of pages, and choosing which part the row
    meant would be a guess.
    """
    value = text.strip()
    if not value:
        return None, None, None

    if re.search(r"[,;&]|\band\b", value, re.IGNORECASE):
        return None, None, (
            "page cell '{v}' lists more than one range; only a single "
            "continuous range can be split".format(v=value)
        )

    range_match = _PAGE_RANGE.match(value)
    if range_match:
        start = _parse_page_number(range_match.group("start"))
        end = _parse_page_number(range_match.group("end"))
        if start is None or end is None:
            return None, None, "page cell '{v}' is not a readable range".format(v=value)
        return start, end, None

    single = _parse_page_number(value)
    if single is None:
        return None, None, "page cell '{v}' is not a readable page number".format(v=value)
    return single, single, None


def _resolve_row_pages(row: IndexRow) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Work out (start, end) for a row from whichever page columns it has.

    When both a combined `pages` column and explicit start/end columns are
    present they must agree; a disagreement is ambiguous, not a tie to break.
    """
    start_text = row.get("start_page").strip()
    end_text = row.get("end_page").strip()

    explicit_start = explicit_end = None
    if start_text:
        explicit_start = _parse_page_number(start_text)
        if explicit_start is None:
            return None, None, "start page '{v}' is not a whole number".format(v=start_text)
    if end_text:
        explicit_end = _parse_page_number(end_text)
        if explicit_end is None:
            return None, None, "end page '{v}' is not a whole number".format(v=end_text)

    combined_start, combined_end, error = _parse_pages_cell(row.get("pages"))
    if error:
        return None, None, error

    if combined_start is not None and explicit_start is not None:
        if combined_start != explicit_start or (
            explicit_end is not None and combined_end != explicit_end
        ):
            return None, None, (
                "page columns disagree: start/end say {a}, the page range "
                "column says {b}".format(
                    a="{s}-{e}".format(s=explicit_start, e=explicit_end),
                    b="{s}-{e}".format(s=combined_start, e=combined_end),
                )
            )

    start = explicit_start if explicit_start is not None else combined_start
    end = explicit_end if explicit_end is not None else combined_end
    return start, end, None


def _infer_missing_end_pages(
    pending: List[Tuple[IndexRow, int]],
    all_starts: List[int],
    total_pages: int,
) -> Tuple[List[PlannedDocument], List[RejectedRow]]:
    """Derive an end page from where the next document starts (opt-in only).

    This is an assumption — that documents are contiguous and in page order —
    so it is never applied unless --infer-end-page is passed, and each result
    is flagged in the report. Where the assumption cannot hold (no later start,
    or another row starting on the same page), the row is still rejected.
    """
    planned: List[PlannedDocument] = []
    rejected: List[RejectedRow] = []
    ordered_starts = sorted(set(all_starts))

    for row, start in pending:
        later = [s for s in ordered_starts if s > start]
        if all_starts.count(start) > 1:
            rejected.append(RejectedRow(
                row,
                "end page missing and could not be inferred",
                "another row also starts on page {s}".format(s=start),
            ))
            continue
        end = (later[0] - 1) if later else total_pages
        if end < start:
            rejected.append(RejectedRow(
                row,
                "end page missing and could not be inferred",
                "the next document starts on page {n}, leaving no pages for "
                "this one".format(n=later[0] if later else total_pages),
            ))
            continue
        planned.append(PlannedDocument(
            row, start, end,
            notes=["end page inferred as {e} (--infer-end-page)".format(e=end)],
        ))
    return planned, rejected


def _reject_overlaps(
    planned: List[PlannedDocument],
) -> Tuple[List[PlannedDocument], List[RejectedRow]]:
    """Drop every row involved in an overlapping range.

    Two rows claiming the same page means at least one of them is wrong, and
    there is nothing in the index that says which. Both are rejected so the
    conflict is visible; --allow-overlaps keeps them if the overlap is real
    (for example a covering letter counted inside its attachment).
    """
    ordered = sorted(range(len(planned)), key=lambda i: (planned[i].start_page, planned[i].end_page))
    conflicting: Dict[int, List[str]] = {}

    for position, index in enumerate(ordered):
        current = planned[index]
        for other_index in ordered[position + 1:]:
            other = planned[other_index]
            if other.start_page > current.end_page:
                break                       # sorted by start: nothing later overlaps
            conflicting.setdefault(index, []).append(
                "row {r} ({p})".format(r=other.row.row_number, p=other.page_label)
            )
            conflicting.setdefault(other_index, []).append(
                "row {r} ({p})".format(r=current.row.row_number, p=current.page_label)
            )

    kept = [doc for i, doc in enumerate(planned) if i not in conflicting]
    rejected = [
        RejectedRow(
            planned[i].row,
            "page range overlaps another row",
            "pages {p} also claimed by {others}".format(
                p=planned[i].page_label, others=", ".join(sorted(set(others)))
            ),
        )
        for i, others in sorted(conflicting.items())
    ]
    return kept, rejected


def _find_gaps(planned: List[PlannedDocument], total_pages: int) -> List[Tuple[int, int]]:
    """Pages of the source PDF that no planned document covers.

    Not an error — an index legitimately skips dividers and blank pages — but
    worth surfacing, because a large gap usually means rows were rejected or
    the index is incomplete.
    """
    gaps: List[Tuple[int, int]] = []
    cursor = 1
    for doc in sorted(planned, key=lambda d: d.start_page):
        if doc.start_page > cursor:
            gaps.append((cursor, doc.start_page - 1))
        cursor = max(cursor, doc.end_page + 1)
    if cursor <= total_pages:
        gaps.append((cursor, total_pages))
    return gaps


def validate_rows(
    rows: List[IndexRow],
    total_pages: int,
    infer_end_page: bool = False,
    allow_overlaps: bool = False,
) -> ValidationResult:
    """Validate every index row against the source PDF's real page count."""
    planned: List[PlannedDocument] = []
    rejected: List[RejectedRow] = []
    pending_inference: List[Tuple[IndexRow, int]] = []
    known_starts: List[int] = []

    for row in rows:
        start, end, error = _resolve_row_pages(row)

        if error:
            rejected.append(RejectedRow(row, "page range could not be read", error))
            continue
        if start is None and end is None:
            rejected.append(RejectedRow(row, "no page range given", "page columns are blank"))
            continue
        if start is None:
            rejected.append(RejectedRow(
                row, "start page missing",
                "an end page of {e} was given with no start page".format(e=end),
            ))
            continue
        if start < 1:
            rejected.append(RejectedRow(
                row, "start page out of range",
                "page numbering starts at 1, got {s}".format(s=start),
            ))
            continue
        if start > total_pages:
            rejected.append(RejectedRow(
                row, "start page out of range",
                "the PDF has {t} pages, row starts at {s}".format(t=total_pages, s=start),
            ))
            continue

        known_starts.append(start)

        if end is None:
            if infer_end_page:
                pending_inference.append((row, start))
            else:
                rejected.append(RejectedRow(
                    row, "end page missing",
                    "pass --infer-end-page to derive it from where the next "
                    "document starts",
                ))
            continue
        if end < start:
            rejected.append(RejectedRow(
                row, "end page before start page",
                "start {s}, end {e}".format(s=start, e=end),
            ))
            continue
        if end > total_pages:
            rejected.append(RejectedRow(
                row, "end page out of range",
                "the PDF has {t} pages, row ends at {e}".format(t=total_pages, e=end),
            ))
            continue

        planned.append(PlannedDocument(row, start, end))

    if pending_inference:
        inferred, inference_rejected = _infer_missing_end_pages(
            pending_inference, known_starts, total_pages
        )
        planned.extend(inferred)
        rejected.extend(inference_rejected)

    if not allow_overlaps:
        planned, overlap_rejected = _reject_overlaps(planned)
        rejected.extend(overlap_rejected)

    planned.sort(key=lambda d: (d.start_page, d.end_page, d.row.row_number))
    rejected.sort(key=lambda r: r.row.row_number)
    return ValidationResult(planned, rejected, _find_gaps(planned, total_pages))
