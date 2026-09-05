"""Command-line entry point for the PDF splitter.

Usage lives in README.md; `--help` covers the flags. Exit codes are meaningful
so this can be chained in a script:

    0  every index row produced a document
    1  the run could not start (bad PDF, unreadable index, bad arguments)
    2  the run finished but some rows were rejected, skipped, or failed
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .config import (
    DEFAULT_MAX_NAME_LENGTH,
    DEFAULT_NAME_TEMPLATE,
    NAMING_FIELDS,
    SplitOptions,
    load_column_map,
)
from .index_reader import IndexReadError, read_index
from .naming import assign_file_names, ensure_output_dir, validate_template
from .report import REPORT_FILE_NAME, print_summary, write_report
from .splitter import (
    STATUS_FAILED,
    STATUS_PLANNED,
    STATUS_WRITTEN,
    SplitError,
    open_pdf,
    write_documents,
)
from .validation import validate_rows

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_PARTIAL = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf_splitter",
        description=(
            "Split a large combined PDF into individual documents using an "
            "Excel index of page ranges and metadata."
        ),
        epilog=(
            "Rows that are invalid or ambiguous are never guessed at: they are "
            "skipped and listed in the report so the index can be corrected."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("pdf", type=Path, help="the combined source PDF")
    parser.add_argument("index", type=Path, help="the Excel index (.xlsx)")
    parser.add_argument(
        "-o", "--out", type=Path, default=None, metavar="DIR",
        help="output folder (default: ./output/<pdf name>)",
    )

    sheet_group = parser.add_argument_group("reading the index")
    sheet_group.add_argument(
        "--sheet", default=None,
        help="worksheet name (default: the first sheet)",
    )
    sheet_group.add_argument(
        "--header-row", type=int, default=None, metavar="N",
        help="1-based row holding the column headers (default: auto-detect)",
    )
    sheet_group.add_argument(
        "--column-map", type=Path, default=None, metavar="FILE",
        help=(
            "JSON file mapping sheet headers to fields, e.g. "
            '{"Pg From": "start_page"}. Use when headers are non-standard '
            "or two columns are ambiguous."
        ),
    )

    name_group = parser.add_argument_group("naming")
    name_group.add_argument(
        "--name-template", default=DEFAULT_NAME_TEMPLATE, metavar="TEMPLATE",
        help="filename pattern; blank fields drop out. Fields: " + ", ".join(NAMING_FIELDS),
    )
    name_group.add_argument(
        "--max-name-length", type=int, default=DEFAULT_MAX_NAME_LENGTH, metavar="N",
        help="truncate names to this many characters (Windows path limit)",
    )
    name_group.add_argument(
        "--number-prefix", action="store_true",
        help="prefix each name with its order (001, 002, ...) to keep file order",
    )
    name_group.add_argument(
        "--fallback-name-pages", action="store_true",
        help="name rows with no usable metadata after their page range instead "
             "of rejecting them",
    )

    risk_group = parser.add_argument_group("assumptions (off by default)")
    risk_group.add_argument(
        "--infer-end-page", action="store_true",
        help="where an end page is blank, derive it from where the next "
             "document starts. Assumes documents are contiguous and in order.",
    )
    risk_group.add_argument(
        "--allow-overlaps", action="store_true",
        help="keep rows whose page ranges overlap instead of rejecting them",
    )

    output_group = parser.add_argument_group("output")
    output_group.add_argument(
        "--overwrite", action="store_true",
        help="replace output files that already exist",
    )
    output_group.add_argument(
        "-n", "--dry-run", action="store_true",
        help="show what would be produced without writing any PDF",
    )
    output_group.add_argument(
        "--report", type=Path, default=None, metavar="FILE",
        help="where to write the CSV report (default: <out>/" + REPORT_FILE_NAME + ")",
    )
    output_group.add_argument(
        "-q", "--quiet", action="store_true",
        help="only print the summary and anything needing attention",
    )
    return parser


def _resolve_options(args: argparse.Namespace) -> SplitOptions:
    output_dir = args.out or (Path("output") / args.pdf.stem)
    column_map = load_column_map(args.column_map) if args.column_map else {}
    return SplitOptions(
        pdf_path=args.pdf,
        index_path=args.index,
        output_dir=output_dir,
        sheet=args.sheet,
        header_row=args.header_row,
        column_map=column_map,
        name_template=args.name_template,
        max_name_length=args.max_name_length,
        number_prefix=args.number_prefix,
        infer_end_page=args.infer_end_page,
        allow_overlaps=args.allow_overlaps,
        fallback_name_pages=args.fallback_name_pages,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        report_path=args.report,
    )


def run(options: SplitOptions, quiet: bool = False) -> int:
    """Execute a split run and return the process exit code."""
    def note(line: str = "") -> None:
        if not quiet:
            print(line)

    reader = open_pdf(options.pdf_path)
    total_pages = len(reader.pages)

    index = read_index(
        options.index_path,
        sheet=options.sheet,
        header_row=options.header_row,
        column_map=options.column_map,
    )

    note("Source PDF   {p} ({n} pages)".format(p=options.pdf_path, n=total_pages))
    note("Index        {p} [sheet '{s}', headers on row {r}]".format(
        p=options.index_path, s=index.sheet_name, r=index.header_row))
    note("Columns      " + ", ".join(
        "{h} -> {f}".format(h=header, f=field)
        for field, header in sorted(index.columns.items(), key=lambda kv: kv[1])
    ))
    if index.unmapped_headers:
        # Surfaced rather than ignored: a column the tool did not recognise may
        # be the one the operator expected it to name files from.
        note("Ignored      " + ", ".join(index.unmapped_headers)
             + "  (use --column-map to include one of these)")
    note("Index rows   {n}".format(n=len(index.rows)))

    validated = validate_rows(
        index.rows,
        total_pages=total_pages,
        infer_end_page=options.infer_end_page,
        allow_overlaps=options.allow_overlaps,
    )

    named, unnamed = assign_file_names(
        validated.planned,
        template=options.name_template,
        max_length=options.max_name_length,
        number_prefix=options.number_prefix,
        fallback_to_pages=options.fallback_name_pages,
    )
    rejected = sorted(validated.rejected + unnamed, key=lambda r: r.row.row_number)

    if not options.dry_run and named:
        ensure_output_dir(options.output_dir)

    outcomes = write_documents(
        reader,
        named,
        output_dir=options.output_dir,
        overwrite=options.overwrite,
        dry_run=options.dry_run,
    )

    if not quiet and outcomes:
        note()
        note("Documents:")
        for outcome in outcomes:
            note("  p{pages:<11} {name}".format(
                pages=outcome.document.page_label, name=outcome.document.file_name))

    report_path: Optional[Path] = options.report_path
    if report_path is None and not options.dry_run:
        report_path = options.output_dir / REPORT_FILE_NAME
    if report_path is not None:
        # The report is the record of the run, so it is written even when every
        # row was rejected and no output folder would otherwise exist.
        ensure_output_dir(report_path.parent)
        write_report(report_path, outcomes, rejected)

    print_summary(
        outcomes,
        rejected,
        validated.uncovered_pages,
        total_pages=total_pages,
        output_dir=options.output_dir,
        report_path=report_path,
        dry_run=options.dry_run,
    )

    clean = all(o.status in (STATUS_WRITTEN, STATUS_PLANNED) for o in outcomes)
    if rejected or not clean or any(o.status == STATUS_FAILED for o in outcomes):
        return EXIT_PARTIAL
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        options = _resolve_options(args)
        validate_template(options.name_template)
        return run(options, quiet=args.quiet)
    except (SplitError, IndexReadError, ValueError, OSError) as exc:
        # Expected, explainable failures: show the message, not a traceback.
        print("Error: {exc}".format(exc=exc), file=sys.stderr)
        return EXIT_FATAL
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_FATAL


if __name__ == "__main__":
    raise SystemExit(main())
