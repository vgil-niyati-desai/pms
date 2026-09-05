"""Column vocabulary, naming defaults, and run options.

Real-world index spreadsheets never agree on header wording, so each canonical
field carries a list of aliases. Anything the aliases don't cover can be mapped
explicitly with a --column-map JSON file rather than by editing this module.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import json

# Canonical field name -> header spellings seen in the wild. Matching is done
# on a normalised form (lowercased, punctuation and spacing stripped), so
# "Client Name", "client_name" and "CLIENT-NAME" all collapse to the same key.
FIELD_ALIASES: Dict[str, List[str]] = {
    # --- page range ---------------------------------------------------------
    "start_page": [
        "start page", "startpage", "from page", "page from", "start",
        "begin page", "beginning page", "first page", "page start",
        "start pg", "from pg", "pg from",
    ],
    "end_page": [
        "end page", "endpage", "to page", "page to", "end", "last page",
        "finish page", "page end", "end pg", "to pg", "pg to",
    ],
    # A single column holding the whole range, e.g. "12-18", "12 to 18", "12".
    "pages": [
        "pages", "page range", "page nos", "page numbers", "page no",
        "page number", "page", "pg", "pgs", "page(s)",
    ],
    # --- metadata used for naming ------------------------------------------
    "document_type": [
        "document type", "doc type", "type", "type of document",
        "document category", "doc kind",
    ],
    "category": ["category", "project category", "sector", "segment", "discipline"],
    "client_name": [
        "client", "client name", "customer", "customer name", "owner",
        "employer", "company", "organisation", "organization", "party",
    ],
    "reference_number": [
        "reference number", "reference no", "reference", "ref no", "ref",
        "ref number", "letter no", "letter number", "loi no", "wo no",
        "work order no", "document no", "doc no", "document number",
        "certificate no", "order no",
    ],
    "project_title": [
        "project title", "project", "title", "name of work", "work",
        "description", "subject", "project name", "scope",
    ],
    "contract_value": [
        "contract value", "value", "amount", "contract amount", "order value",
        "cost", "price",
    ],
    "document_date": [
        "document date", "date", "dated", "issue date", "letter date",
        "loi date", "date of issue",
    ],
    "department": ["department", "dept", "division", "unit", "section"],
    "notes": ["notes", "remarks", "comments", "note", "remark"],
}

# Fields that may take part in the generated filename, in the order they are
# offered to the user. Page columns are deliberately excluded.
NAMING_FIELDS: List[str] = [
    "document_type", "category", "client_name", "reference_number",
    "project_title", "contract_value", "document_date", "department", "notes",
]

# Placeholders resolve against whatever the index actually provides; any that
# come back empty are dropped along with their separator, so a row missing a
# reference number simply yields a shorter name instead of a gap.
DEFAULT_NAME_TEMPLATE = "{document_type} - {client_name} - {reference_number} - {project_title}"

# Windows caps a full path at 260 characters by default. The name is capped
# well below that so a deep output folder still leaves room for the extension
# and any de-duplication suffix.
DEFAULT_MAX_NAME_LENGTH = 120


@dataclass
class SplitOptions:
    """Everything the run needs, resolved from CLI arguments."""

    pdf_path: Path
    index_path: Path
    output_dir: Path

    sheet: Optional[str] = None
    header_row: Optional[int] = None          # 1-based; None means auto-detect
    column_map: Dict[str, str] = field(default_factory=dict)

    name_template: str = DEFAULT_NAME_TEMPLATE
    max_name_length: int = DEFAULT_MAX_NAME_LENGTH
    number_prefix: bool = False               # prefix "001_" to keep file order

    infer_end_page: bool = False              # opt-in; off means never guess
    allow_overlaps: bool = False
    fallback_name_pages: bool = False         # name unnamable rows by page range

    overwrite: bool = False
    dry_run: bool = False
    report_path: Optional[Path] = None


def load_column_map(path: Path) -> Dict[str, str]:
    """Load an explicit {"Header text in sheet": "canonical_field"} mapping.

    Used when a workbook's headers are too unusual for the alias table, or when
    two columns are close enough that auto-matching would have to pick one.
    Validated up front so a typo fails immediately instead of silently
    dropping a column.
    """
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)

    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a JSON object of header -> field")

    known = set(FIELD_ALIASES)
    mapping: Dict[str, str] = {}
    for header, canonical in raw.items():
        # JSON has no comments, so keys starting with "_" are treated as notes.
        if str(header).startswith("_"):
            continue
        if canonical not in known:
            raise ValueError(
                f"{path}: '{canonical}' is not a known field. "
                f"Valid fields: {', '.join(sorted(known))}"
            )
        mapping[str(header)] = canonical
    return mapping
