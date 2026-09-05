"""Renames already-split PDFs to descriptive names, from their run report.

The splitter names files from the index's metadata columns, and when those
are blank it falls back to the document type — which is how a folder ends up
with "Work Order (3).pdf". This tool runs afterwards, over the _split_report
CSV sitting next to the files, and rebuilds each name from the best metadata
the report actually holds:

    {type}_{date}_REF-{reference}_{title}.pdf

with any part that has no value simply dropped. Concretely:

  * type       document_type, underscored. A parenthesised acronym is used
               alone — "Letter of Intent (LOI)" becomes "LOI".
  * date       document_date normalised to ISO (2021-05-20). The column holds
               whatever the source document said — "20/05/2021", "06-May-2020",
               "June 2016" — so the first recognisable date wins, at whatever
               precision it has (a bare year stays a year). Day-first, because
               that is how these documents are dated.
  * reference  reference_number when the column is filled; otherwise a
               "Ref .../ Contract No. ... / Tender No. ..." pattern lifted
               from the notes column, where this metadata actually lives in
               practice. Slashes, spaces and dots become dashes.
  * title      project_title, underscored.

A row with none of that keeps its original name. Whatever the source, names
are made unique with a _2/_3 suffix, so two undated "Educational Certificate"
rows still get two files.

The report is rewritten with the new file_name values; the old name of every
renamed row is kept in a previous_file_name column, and the original report
is copied to *.before_rename.csv once, so the change is fully reversible.

Only file names change. Page ranges, document contents, classification and
every other report column are left exactly as they were.

Usage (any Python 3.8+; no third-party packages needed):

    python -m pdf_splitter.rename output\\PMC_split_documents\\_split_report.csv --dry-run
    python -m pdf_splitter.rename output\\PMC_split_documents\\_split_report.csv
"""

import argparse
import csv
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .config import DEFAULT_MAX_NAME_LENGTH
from .naming import sanitise
from .splitter import STATUS_WRITTEN

BACKUP_SUFFIX = ".before_rename.csv"

_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"),
            ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
            ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
            ("nov", "november"), ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))

# Tried in order; the first pattern that yields a plausible date wins. ISO and
# numeric day-first forms outrank looser ones so "17/11/2014 (quotation ref.)"
# is read from its date, not its parenthetical.
_DATE_PATTERNS = [
    ("iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("dmy", re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")),
    ("d_mon_y", re.compile(r"\b(\d{1,2})[ .-]*(" + _MONTH_PATTERN + r")\w*[ .,-]*(\d{4})\b", re.I)),
    ("mon_y", re.compile(r"\b(" + _MONTH_PATTERN + r")\w*[ .,-]*(\d{4})\b", re.I)),
    ("year", re.compile(r"\b((?:19|20)\d{2})\b")),
]

# Where a reference lives when the reference_number column is blank: a
# labelled pattern inside the notes. Case-sensitive on purpose — "Ref" and
# "Tender No." are labels, "references" and "the final tender submission" are
# prose. The value itself stops at a comma or semicolon, which in these notes
# separates the reference from commentary.
_NOTES_REF = re.compile(
    r"\b(?:"
    r"Ref(?:erence)?\b\.?\s*(?:No\b\.?)?\s*(?:quote\b)?"
    r"|(?:Contract|Tender|Dispatch|Order|Letter)\s+No\b\.?"
    r")\s*:?\s*"
    r"([A-Za-z0-9][A-Za-z0-9/\\. \-]*)"
)


def normalise_date(text: str) -> str:
    """The first recognisable date in `text` as ISO, at its own precision.

    "20/05/2021" -> "2021-05-20"; "June 2016" -> "2016-06"; "2019" -> "2019";
    "-" and "(same as pages 13-39)" -> "". Numeric forms are day-first; a
    value that only works month-first ("05/25/2021") is read that way rather
    than thrown away.
    """
    text = (text or "").strip()
    if not text:
        return ""

    for kind, pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if kind == "iso":
            year, month, day = (int(g) for g in match.groups())
        elif kind == "dmy":
            day, month, year = (int(g) for g in match.groups())
            if month > 12 and day <= 12:
                day, month = month, day
        elif kind == "d_mon_y":
            day = int(match.group(1))
            month = _MONTHS[match.group(2).lower()[:3] if match.group(2).lower()[:3] in _MONTHS else match.group(2).lower()]
            year = int(match.group(3))
        elif kind == "mon_y":
            name = match.group(1).lower()
            month = _MONTHS.get(name, _MONTHS.get(name[:3], 0))
            return "{y}-{m:02d}".format(y=int(match.group(2)), m=month)
        else:  # bare year
            return match.group(1)

        if 1 <= month <= 12 and 1 <= day <= 31:
            return "{y}-{m:02d}-{d:02d}".format(y=year, m=month, d=day)
    return ""


def _clean_reference(text: str) -> str:
    """A captured reference as a filename token: separators become dashes.

    "MSAMB/ERP AMC/LoI/.../2024" -> "MSAMB-ERP-AMC-LoI-2024".
    """
    cleaned = re.sub(r"[\s/\\.]+", "-", (text or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    # A "reference" with neither a digit nor more than one part is prose the
    # pattern happened to catch, not a document number.
    if not re.search(r"\d", cleaned) and "-" not in cleaned:
        return ""
    return cleaned


def extract_reference(row: Dict[str, str]) -> str:
    """The row's reference: the dedicated column first, then the notes."""
    direct = _clean_reference(row.get("reference_number", ""))
    if direct:
        return direct
    match = _NOTES_REF.search(row.get("notes", "") or "")
    return _clean_reference(match.group(1)) if match else ""


def _type_part(text: str) -> str:
    """document_type as a name token.

    A parenthesised acronym ("Letter of Intent (LOI)") replaces the whole
    phrase; any other parenthetical is kept as words. Slashes are separators
    in these labels ("Resume / CV"), not meaning.
    """
    text = (text or "").strip()
    acronym = re.search(r"\(([A-Z][A-Z&]{1,7})\)", text)
    if acronym:
        return acronym.group(1)
    return re.sub(r"[()/]", " ", text).strip()


def _underscored(text: str, max_length: int) -> str:
    """A sanitised token with underscores where spaces were."""
    safe = sanitise(text, max_length)
    return re.sub(r"_{2,}", "_", safe.replace(" ", "_")).strip("_")


def build_base_name(row: Dict[str, str], max_length: int) -> str:
    """The descriptive stem for one report row, or "" when the metadata gives
    nothing to work with (the caller then keeps the original name)."""
    parts = []
    doc_type = _type_part(row.get("document_type", ""))
    if doc_type:
        parts.append(doc_type)
    date = normalise_date(row.get("document_date", ""))
    if date:
        parts.append(date)
    reference = extract_reference(row)
    if reference:
        parts.append("REF-" + reference)
    title = (row.get("project_title", "") or "").strip()
    if title:
        parts.append(title)

    if not parts:
        return ""
    return _underscored(" ".join(_underscored(p, max_length) for p in parts), max_length)


def _unique(base: str, taken: Set[str], max_length: int) -> str:
    """base.pdf, or base_2.pdf, base_3.pdf... — whichever is free.

    Case-insensitive, like naming._unique, because Windows would treat two
    names differing only in case as the same file.
    """
    candidate = base + ".pdf"
    counter = 1
    while candidate.lower() in taken:
        counter += 1
        suffix = "_{n}".format(n=counter)
        trimmed = base
        if len(base) + len(suffix) > max_length:
            trimmed = base[: max_length - len(suffix)].rstrip("_ .")
        candidate = trimmed + suffix + ".pdf"
    taken.add(candidate.lower())
    return candidate


def plan_renames(
    rows: List[Dict[str, str]], directory: Path, max_length: int
) -> List[Tuple[Dict[str, str], str, str]]:
    """(row, old_name, new_name) for every written row, in report order.

    Every written row gets a plan entry — unchanged names included — so
    uniqueness is decided across the whole set at once. Names claimed by
    files this tool does not manage (the report itself, anything not in the
    report) are reserved up front so a new name can never collide with them.
    """
    managed = {
        (row.get("file_name") or "").lower()
        for row in rows
        if row.get("status") == STATUS_WRITTEN and row.get("file_name")
    }
    taken: Set[str] = {
        entry.name.lower()
        for entry in directory.iterdir()
        if entry.name.lower() not in managed
    }

    plans = []
    for row in rows:
        if row.get("status") != STATUS_WRITTEN or not row.get("file_name"):
            continue
        old_name = row["file_name"]
        base = build_base_name(row, max_length)
        if not base:
            # Nothing usable: the original name, minus any old " (n)" marker,
            # re-uniqued in the new style.
            base = _underscored(re.sub(r"\s*\(\d+\)$", "", Path(old_name).stem), max_length)
        if not base:
            base = _underscored(Path(old_name).stem, max_length) or "document"
        plans.append((row, old_name, _unique(base, taken, max_length)))
    return plans


def apply_renames(directory: Path, plans: List[Tuple[Dict[str, str], str, str]]) -> int:
    """Execute the plan; returns how many files actually moved.

    Two phases through temporary names, so the plan's order can never make
    one row's target collide with another row's not-yet-vacated source (or
    with its own, on a case-only change).
    """
    leftovers = list(directory.glob("__rename_tmp_*.pdf"))
    if leftovers:
        raise RuntimeError(
            "Temporary files from an interrupted rename are still here "
            "({n} of them, e.g. {e}). Sort those out first.".format(
                n=len(leftovers), e=leftovers[0].name
            )
        )

    moving = [(old, new) for _, old, new in plans if old != new]
    staged: List[Tuple[Path, str]] = []
    for index, (old, new) in enumerate(moving):
        source = directory / old
        temporary = directory / "__rename_tmp_{n}.pdf".format(n=index)
        source.rename(temporary)
        staged.append((temporary, new))
    for temporary, new in staged:
        temporary.rename(directory / new)
    return len(moving)


def rewrite_report(
    report_path: Path, rows: List[Dict[str, str]], fieldnames: List[str]
) -> None:
    """Write the report back with updated names, keeping the column order and
    the utf-8-sig/newline conventions of report.write_report."""
    if "previous_file_name" not in fieldnames:
        fieldnames = fieldnames + ["previous_file_name"]
    with open(report_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pdf_splitter.rename",
        description=(
            "Rename already-split PDFs to descriptive names built from the "
            "metadata in their _split_report CSV, and update the report to "
            "match."
        ),
    )
    parser.add_argument("report", type=Path, help="the _split_report.csv of a finished run")
    parser.add_argument(
        "--max-name-length", type=int, default=DEFAULT_MAX_NAME_LENGTH, metavar="N",
        help="longest allowed name before .pdf (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print every planned rename without touching anything",
    )
    args = parser.parse_args(argv)

    report_path = args.report.resolve()
    if not report_path.is_file():
        print("Report not found: {p}".format(p=report_path), file=sys.stderr)
        return 1
    directory = report_path.parent

    with open(report_path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if "file_name" not in fieldnames:
        print("This does not look like a split report: no file_name column.", file=sys.stderr)
        return 1

    plans = plan_renames(rows, directory, args.max_name_length)

    # Refuse to start unless every file the report claims exists actually
    # does — renaming half a folder leaves report and disk disagreeing, which
    # is worse than either problem alone.
    missing = [old for _, old, _ in plans if not (directory / old).is_file()]
    if missing:
        print("Aborting: {n} file(s) named in the report are not in {d}:".format(
            n=len(missing), d=directory), file=sys.stderr)
        for name in missing:
            print("  " + name, file=sys.stderr)
        return 1

    changing = [(row, old, new) for row, old, new in plans if old != new]
    for row, old, new in plans:
        marker = "->" if old != new else "== (already descriptive)"
        print("  {old}\n      {m} {new}".format(old=old, m=marker, new=new))
    print()
    print("{n} of {t} file(s) would be renamed.".format(n=len(changing), t=len(plans))
          if args.dry_run else
          "{n} of {t} file(s) to rename.".format(n=len(changing), t=len(plans)))

    if args.dry_run:
        print("Dry run - nothing was changed. Re-run without --dry-run to apply.")
        return 0
    if not changing:
        print("Nothing to do.")
        return 0

    backup = report_path.with_suffix(BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(report_path, backup)
        print("Original report kept as {b}".format(b=backup.name))

    renamed = apply_renames(directory, plans)

    for row, old, new in plans:
        if old != new:
            row["file_name"] = new
            # First rename wins: after that, "previous" means the name the
            # splitter gave the file, not last run's intermediate.
            if not row.get("previous_file_name"):
                row["previous_file_name"] = old
    rewrite_report(report_path, rows, fieldnames)

    print("Renamed {n} file(s); report updated.".format(n=renamed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
