"""Building a proposal-library record from a write-up file.

Shared by the API (an upload) and the bulk import script, so both produce
the same record from the same file.
"""

import hashlib
import re
from typing import Any, Dict, Optional

from . import docx_text, storage


def sort_key(section_number: Optional[str]) -> str:
    """'3.10.1' -> '003.010.001', so sections order numerically ('3.2' before
    '3.10'), not as text. Text after the first number group ('5.1.1&5.1.2')
    is ignored for ordering only."""
    groups = re.match(r"\d+(?:\.\d+)*", section_number or "")
    if not groups:
        return "~"
    return ".".join(part.zfill(3) for part in groups.group(0).split("."))


def build_record(
    contents: bytes,
    file_name: str,
    *,
    section_number: Optional[str] = None,
    section_title: Optional[str] = None,
    subsection: Optional[str] = None,
    point_number: Optional[int] = None,
    source_path: Optional[str] = None,
    created_at=None,
) -> Dict[str, Any]:
    """The record (without stored_file_name) for one write-up file.

    Raises docx_text.UnreadableDocument when the file is not what its
    extension says. The first paragraph is taken as the requirement and the
    rest as the response -- the pattern every sampled file follows -- and
    the record says so in `structure` so a person can correct it.
    """
    extension = file_name.rsplit(".", 1)[-1].lower()
    extracted = docx_text.extract(contents, extension)
    paragraphs = extracted["paragraphs"]
    requirement = paragraphs[0] if paragraphs else None
    response = "\n\n".join(paragraphs[1:]) or None
    digest = hashlib.sha256(contents).hexdigest()
    words = sum(len(p.split()) for p in paragraphs)
    return {
        "section_number": section_number,
        "section_title": section_title,
        "section_key": sort_key(section_number),
        "subsection": subsection,
        "point_number": point_number,
        "title": (requirement or file_name)[:300],
        "requirement_text": requirement,
        "response_text": response,
        "structure": "first paragraph = requirement, remainder = response",
        "word_count": words,
        "image_count": extracted["images"],
        "extraction_method": extracted["method"],
        "file_name": storage.safe_display_name(file_name),
        "file_type": extension,
        "file_hash": digest,
        "source_key": "sha256:" + digest,
        "source_path": source_path,
        "created_at": created_at,
    }
