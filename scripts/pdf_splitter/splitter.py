"""Opens the source PDF and writes one output file per planned page range."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from .validation import PlannedDocument

STATUS_WRITTEN = "written"
STATUS_PLANNED = "planned"          # dry run only: nothing was written
STATUS_EXISTS = "skipped (already exists)"
STATUS_FAILED = "failed"


class SplitError(Exception):
    """Raised when the source PDF cannot be used at all."""


@dataclass
class SplitOutcome:
    document: PlannedDocument
    status: str
    path: Optional[Path] = None
    message: str = ""


def open_pdf(path: Path) -> PdfReader:
    """Open the source PDF, or explain exactly why it cannot be read.

    Encrypted files are attempted with an empty password, which covers the
    common case of a PDF carrying only an owner (permissions) password. A file
    that genuinely needs a password stops the run — silently producing
    unreadable output PDFs would be worse.
    """
    if not path.exists():
        raise SplitError("PDF not found: {p}".format(p=path))
    if path.is_dir():
        raise SplitError("Expected a PDF file but got a folder: {p}".format(p=path))

    try:
        reader = PdfReader(str(path))
    except PdfReadError as exc:
        raise SplitError(
            "'{p}' could not be read as a PDF: {exc}".format(p=path, exc=exc)
        ) from exc
    except OSError as exc:
        raise SplitError("Could not open '{p}': {exc}".format(p=path, exc=exc)) from exc

    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception as exc:                  # pypdf surfaces several types
            raise SplitError(
                "'{p}' is encrypted and could not be opened: {exc}".format(p=path, exc=exc)
            ) from exc
        if not opened:
            raise SplitError(
                "'{p}' is password-protected. Remove the password (open it in "
                "a PDF reader and re-save) and run again.".format(p=path)
            )

    if len(reader.pages) == 0:
        raise SplitError("'{p}' has no pages.".format(p=path))
    return reader


def write_documents(
    reader: PdfReader,
    planned: List[PlannedDocument],
    output_dir: Path,
    overwrite: bool = False,
    dry_run: bool = False,
) -> List[SplitOutcome]:
    """Write each planned range to its own PDF under `output_dir`.

    An existing file is never overwritten unless --overwrite is passed, so a
    re-run after fixing a few index rows leaves the already-correct output
    alone. A failure on one document does not abandon the rest of the run.
    """
    outcomes: List[SplitOutcome] = []

    for doc in planned:
        target = output_dir / doc.file_name

        if dry_run:
            outcomes.append(SplitOutcome(doc, STATUS_PLANNED, target))
            continue

        if target.exists() and not overwrite:
            outcomes.append(SplitOutcome(
                doc, STATUS_EXISTS, target,
                "pass --overwrite to replace it",
            ))
            continue

        try:
            writer = PdfWriter()
            # Index page numbers are 1-based; pypdf is 0-based.
            for page_index in range(doc.start_page - 1, doc.end_page):
                writer.add_page(reader.pages[page_index])
            with open(target, "wb") as handle:
                writer.write(handle)
            outcomes.append(SplitOutcome(doc, STATUS_WRITTEN, target))
        except Exception as exc:                  # keep going; report per row
            outcomes.append(SplitOutcome(
                doc, STATUS_FAILED, target, "{t}: {exc}".format(t=type(exc).__name__, exc=exc)
            ))

    return outcomes
