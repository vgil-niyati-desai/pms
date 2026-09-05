"""Builds output filenames from whatever metadata the index actually provides.

Names are derived only from real cell values: a placeholder whose column is
missing or blank drops out of the name entirely, together with its separator,
rather than leaving "Unknown" or an empty gap behind. A row with nothing usable
to name it is reported instead of being given a made-up name.
"""

from pathlib import Path
from typing import Dict, List, Set, Tuple
import re

from .config import NAMING_FIELDS
from .validation import PlannedDocument, RejectedRow

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

# Characters Windows forbids in a filename, plus control characters. Replaced
# with a space so words do not run together, then collapsed.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Reserved DOS device names. A file called "CON.pdf" cannot be created on
# Windows, so these get a leading underscore.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *("COM{n}".format(n=i) for i in range(1, 10)),
    *("LPT{n}".format(n=i) for i in range(1, 10)),
}


def sanitise(name: str, max_length: int) -> str:
    """Make a name safe to write on Windows, or return "" if nothing survives."""
    cleaned = _ILLEGAL.sub(" ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Trailing dots and spaces are silently dropped by Windows, which would
    # make two different names collide; remove them up front instead.
    cleaned = cleaned.strip(" .")
    if not cleaned:
        return ""
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip(" .")
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def render_template(template: str, values: Dict[str, str]) -> str:
    """Fill a name template, dropping empty placeholders and their separators.

    "{document_type} - {client_name} - {reference_number}" with no reference
    number becomes "Work Order - Acme", not "Work Order - Acme - ".
    """
    def substitute(match: "re.Match[str]") -> str:
        return values.get(match.group(1), "").strip()

    rendered = _PLACEHOLDER.sub(substitute, template)
    # Collapse separator runs left behind by empty placeholders, e.g.
    # " -  - Acme" -> "Acme".
    rendered = re.sub(r"\s*[-_,]\s*(?=[-_,])", "", rendered)
    rendered = re.sub(r"^[\s\-_,]+|[\s\-_,]+$", "", rendered)
    return re.sub(r"\s+", " ", rendered).strip()


def validate_template(template: str) -> None:
    """Reject a template referring to fields that can never be filled."""
    unknown = sorted({f for f in _PLACEHOLDER.findall(template) if f not in NAMING_FIELDS})
    if unknown:
        raise ValueError(
            "Unknown field(s) in --name-template: {bad}. Available: {ok}".format(
                bad=", ".join(unknown), ok=", ".join(NAMING_FIELDS)
            )
        )


def _unique(base: str, taken: Set[str], max_length: int) -> Tuple[str, bool]:
    """Return a filename not already used, and whether it had to be altered.

    Comparison is case-insensitive because Windows treats "Loi.pdf" and
    "loi.pdf" as the same file — without this, a second row would silently
    overwrite the first.
    """
    candidate = "{b}.pdf".format(b=base)
    if candidate.lower() not in taken:
        taken.add(candidate.lower())
        return candidate, False

    counter = 2
    while True:
        suffix = " ({n})".format(n=counter)
        trimmed = base[: max_length - len(suffix)].strip(" .") if len(base) + len(suffix) > max_length else base
        candidate = "{b}{s}.pdf".format(b=trimmed, s=suffix)
        if candidate.lower() not in taken:
            taken.add(candidate.lower())
            return candidate, True
        counter += 1


def assign_file_names(
    planned: List[PlannedDocument],
    template: str,
    max_length: int,
    number_prefix: bool = False,
    fallback_to_pages: bool = False,
) -> Tuple[List[PlannedDocument], List[RejectedRow]]:
    """Give every planned document a unique, safe filename.

    Rows whose metadata renders to nothing are rejected rather than named
    after their position in the file, unless --fallback-name-pages is passed —
    a file called "row 14" tells nobody what is inside it.
    """
    named: List[PlannedDocument] = []
    rejected: List[RejectedRow] = []
    taken: Set[str] = set()
    width = len(str(len(planned))) if planned else 1

    for order, doc in enumerate(planned, start=1):
        base = sanitise(render_template(template, doc.row.values), max_length)

        if not base:
            page_base = "pages {p}".format(p=doc.page_label)
            if not fallback_to_pages:
                rejected.append(RejectedRow(
                    doc.row,
                    "no metadata to build a file name from",
                    "every field used by the name template is blank; fix the "
                    "row, change --name-template, or pass --fallback-name-pages "
                    "to name it '{b}'".format(b=page_base),
                ))
                continue
            base = page_base
            doc.notes.append("named by page range (no usable metadata)")

        if number_prefix:
            base = "{n:0{w}d} {b}".format(n=order, w=width, b=base)
            base = sanitise(base, max_length)

        doc.file_name, altered = _unique(base, taken, max_length)
        if altered:
            doc.notes.append("name already used by another row; numbered suffix added")
        named.append(doc)

    return named, rejected


def ensure_output_dir(path: Path) -> None:
    """Create the output folder, failing clearly if the path is not usable."""
    if path.exists() and not path.is_dir():
        raise NotADirectoryError(
            "Output path exists but is not a folder: {p}".format(p=path)
        )
    path.mkdir(parents=True, exist_ok=True)
