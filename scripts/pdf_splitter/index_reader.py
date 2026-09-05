"""Reads the Excel index and turns it into canonical, string-normalised rows.

Two things are deliberately strict here: which row is the header, and which
sheet column feeds which canonical field. If either is unclear the read stops
with an explanation instead of picking a likely-looking option, because a wrong
column mapping silently mis-names or mis-cuts every document in the run.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re

from openpyxl import load_workbook

from .config import FIELD_ALIASES

# How far down the sheet to look for a header row before giving up. Indexes
# often carry a title block or a blank line or two above the real headers.
HEADER_SEARCH_DEPTH = 20

_ALIAS_LOOKUP: Dict[str, str] = {}
for _canonical, _aliases in FIELD_ALIASES.items():
    for _alias in [_canonical] + _aliases:
        _ALIAS_LOOKUP[re.sub(r"[^a-z0-9]", "", _alias.lower())] = _canonical


class IndexReadError(Exception):
    """Raised when the index cannot be read unambiguously."""


@dataclass
class IndexRow:
    """One spreadsheet row, mapped to canonical fields."""

    row_number: int                                        # 1-based row in the sheet
    values: Dict[str, str] = field(default_factory=dict)   # canonical -> text

    def get(self, field_name: str) -> str:
        return self.values.get(field_name, "")

    def is_blank(self) -> bool:
        return not any(v for v in self.values.values())


@dataclass
class IndexReadResult:
    sheet_name: str
    header_row: int
    columns: Dict[str, str]          # canonical field -> the header text used
    unmapped_headers: List[str]      # headers nothing was recognised for
    rows: List[IndexRow]


def _normalise_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower()) if value is not None else ""


def _cell_to_text(value: object) -> str:
    """Normalise a cell to trimmed text without inventing information.

    Dates become ISO strings (matching how the app stores `document_date`), and
    whole-number floats lose the Excel-added ".0" so a reference number typed
    as a number does not become "1234.0" in a filename.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        # Midnight almost always means a plain date cell, not a timestamp.
        if value.time() == time(0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\s+", " ", str(value)).strip()


def _score_header_row(
    cells: Tuple[object, ...],
    explicit_by_norm: Dict[str, str],
) -> Tuple[int, bool]:
    """Return (recognised header count, whether a page column was recognised).

    Headers named in --column-map count as recognised, so a sheet whose column
    titles are all non-standard can still have its header row found.
    """
    matches = set()
    for key in (_normalise_header(c) for c in cells):
        if not key:
            continue
        canonical = explicit_by_norm.get(key) or _ALIAS_LOOKUP.get(key)
        if canonical:
            matches.add(canonical)
    has_pages = bool(matches & {"start_page", "end_page", "pages"})
    return len(matches), has_pages


def _find_header_row(
    grid: List[Tuple[object, ...]],
    explicit_by_norm: Dict[str, str],
) -> int:
    """Pick the header row, or explain why it could not be identified.

    A row only qualifies if it names at least one page column and one other
    known field. That combination is what the splitter actually needs, and it
    keeps a stray title line from being mistaken for headers.
    """
    best_row = None
    best_score = 0
    for offset, cells in enumerate(grid[:HEADER_SEARCH_DEPTH], start=1):
        score, has_pages = _score_header_row(cells, explicit_by_norm)
        if has_pages and score >= 2 and score > best_score:
            best_row, best_score = offset, score

    if best_row is None:
        raise IndexReadError(
            "Could not identify the header row automatically. No row in the "
            "first {depth} rows contained both a recognisable page column "
            "(e.g. 'Start Page' / 'Pages') and another known column. Pass "
            "--header-row <n>, and --column-map if the headers are "
            "non-standard.".format(depth=HEADER_SEARCH_DEPTH)
        )
    return best_row


def _map_columns(
    header_cells: Tuple[object, ...],
    explicit_map: Dict[str, str],
) -> Tuple[Dict[int, str], Dict[str, str], List[str]]:
    """Map sheet column indexes to canonical fields.

    Explicit --column-map entries win over the alias table. Two columns
    claiming the same field is treated as an error rather than a first-wins
    guess, since only the operator knows which one is authoritative.
    """
    explicit_by_norm = {
        _normalise_header(header): canonical
        for header, canonical in explicit_map.items()
    }

    by_index: Dict[int, str] = {}
    claimed: Dict[str, str] = {}          # canonical -> header text that claimed it
    conflicts: List[str] = []
    unmapped: List[str] = []

    for index, cell in enumerate(header_cells):
        text = _cell_to_text(cell)
        if not text:
            continue
        norm = _normalise_header(cell)
        canonical = explicit_by_norm.get(norm) or _ALIAS_LOOKUP.get(norm)
        if canonical is None:
            unmapped.append(text)
            continue
        if canonical in claimed:
            conflicts.append(
                "'{a}' and '{b}' both map to {field}".format(
                    a=claimed[canonical], b=text, field=canonical
                )
            )
            continue
        claimed[canonical] = text
        by_index[index] = canonical

    if conflicts:
        raise IndexReadError(
            "Ambiguous columns in the index: "
            + "; ".join(conflicts)
            + ". Use --column-map to say explicitly which column to use."
        )
    return by_index, claimed, unmapped


def read_index(
    path: Path,
    sheet: Optional[str] = None,
    header_row: Optional[int] = None,
    column_map: Optional[Dict[str, str]] = None,
) -> IndexReadResult:
    """Read the index workbook into canonical rows.

    `data_only=True` returns the cached results of formulas rather than the
    formula text. A workbook that has never been opened and saved in Excel has
    no cached values, which surfaces here as empty cells — and therefore as
    reported skipped rows, not as silently valid ones.
    """
    if not path.exists():
        raise IndexReadError("Index file not found: {p}".format(p=path))

    try:
        workbook = load_workbook(filename=str(path), data_only=True, read_only=True)
    except Exception as exc:                      # openpyxl raises a wide range
        raise IndexReadError(
            "Could not open '{p}' as an Excel workbook: {exc}. "
            ".xls (pre-2007) files are not supported - re-save as .xlsx."
            .format(p=path, exc=exc)
        ) from exc

    try:
        if sheet is not None:
            if sheet not in workbook.sheetnames:
                raise IndexReadError(
                    "Sheet '{s}' not found. Available sheets: {names}".format(
                        s=sheet, names=", ".join(workbook.sheetnames)
                    )
                )
            worksheet = workbook[sheet]
        else:
            worksheet = workbook[workbook.sheetnames[0]]

        sheet_title = worksheet.title
        grid = [tuple(row) for row in worksheet.iter_rows(values_only=True)]
    finally:
        workbook.close()

    if not grid:
        raise IndexReadError("Sheet '{s}' is empty.".format(s=sheet_title))

    explicit_by_norm = {
        _normalise_header(header): canonical
        for header, canonical in (column_map or {}).items()
    }
    resolved_header = (
        header_row if header_row is not None
        else _find_header_row(grid, explicit_by_norm)
    )
    if resolved_header < 1 or resolved_header > len(grid):
        raise IndexReadError(
            "--header-row {n} is outside the sheet (it has {total} rows).".format(
                n=resolved_header, total=len(grid)
            )
        )

    by_index, columns, unmapped = _map_columns(grid[resolved_header - 1], column_map or {})
    if not by_index:
        raise IndexReadError(
            "No known columns were recognised on row {n} of sheet '{s}'. "
            "Check --header-row, or supply --column-map.".format(
                n=resolved_header, s=sheet_title
            )
        )
    if not (set(columns) & {"start_page", "end_page", "pages"}):
        raise IndexReadError(
            "The index has no page column. At least one of a start/end page "
            "pair or a combined page-range column is required to work out "
            "where each document begins and ends."
        )

    rows: List[IndexRow] = []
    for offset, cells in enumerate(grid[resolved_header:], start=resolved_header + 1):
        values = {
            canonical: _cell_to_text(cells[index])
            for index, canonical in by_index.items()
            if index < len(cells)
        }
        row = IndexRow(row_number=offset, values=values)
        if row.is_blank():
            # Trailing or separating blank lines are layout, not data, so they
            # are dropped rather than reported as rejected rows.
            continue
        rows.append(row)

    return IndexReadResult(
        sheet_name=sheet_title,
        header_row=resolved_header,
        columns=columns,
        unmapped_headers=unmapped,
        rows=rows,
    )
