"""Text out of proposal write-ups (.docx, and best-effort .rtf).

No third-party dependency: a .docx is a zip of XML, so the paragraphs and the
number of embedded pictures are read straight from it. An .rtf is stripped of
its control words, which is enough to search it but not to reproduce it, so
its result is marked as basic.

Nothing here is interpreted. The caller decides what the first paragraph or
the rest mean; this only returns what the file contains, in order.
"""

import re
import zipfile
from io import BytesIO
from typing import Dict, List

_PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_TEXT = re.compile(r"<w:t[^>]*>([^<]*)</w:t>|<w:tab/>|<w:br/>")
_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&"))


def _unescape(text: str) -> str:
    for entity, char in _ENTITIES:
        text = text.replace(entity, char)
    return text


class UnreadableDocument(ValueError):
    """The file is not the format its name claims."""


def extract_docx(contents: bytes) -> Dict:
    """{'paragraphs': [...], 'images': n, 'method': 'docx'} or UnreadableDocument."""
    try:
        archive = zipfile.ZipFile(BytesIO(contents))
        xml = archive.read("word/document.xml").decode("utf-8", "ignore")
    except (zipfile.BadZipFile, KeyError) as error:
        raise UnreadableDocument("not a valid .docx file") from error
    paragraphs: List[str] = []
    for paragraph in _PARAGRAPH.findall(xml):
        parts = []
        for match in _TEXT.finditer(paragraph):
            parts.append(match.group(1) if match.group(1) is not None else " ")
        text = _unescape("".join(parts)).strip()
        if text:
            paragraphs.append(re.sub(r"[ \t]+", " ", text))
    images = len([name for name in archive.namelist() if name.startswith("word/media/")])
    return {"paragraphs": paragraphs, "images": images, "method": "docx"}


def extract_rtf(contents: bytes) -> Dict:
    """Best-effort plain text from RTF. Pictures are counted, not extracted."""
    if not contents.lstrip().startswith(b"{\\rtf"):
        raise UnreadableDocument("not a valid .rtf file")
    text = contents.decode("latin-1", "ignore")
    images = len(re.findall(r"\\pict\b", text))
    text = re.sub(r"\{\\\*[^{}]*\}", "", text)  # ignorable destinations
    text = re.sub(r"\{\\pict.*?\}", "", text, flags=re.S)
    text = re.sub(r"\\par[d]?\b", "\n", text)
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", "ignore"), text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = re.sub(r"[{}]", "", text)
    paragraphs = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return {"paragraphs": [p for p in paragraphs if p], "images": images, "method": "rtf-basic"}


def extract(contents: bytes, extension: str) -> Dict:
    extension = extension.lower().lstrip(".")
    if extension == "docx":
        return extract_docx(contents)
    if extension == "rtf":
        return extract_rtf(contents)
    raise UnreadableDocument("unsupported file type: " + extension)


# --- naming conventions of the source folders -------------------------------

_SECTION = re.compile(r"^\s*(\d+(?:\.\d+)*[\d.&,\s]*?)\s*[.\s]*(?=[A-Za-z(])(.*)$")


def parse_section_folder(name: str):
    """('3.3.7', 'Loan account management') from a folder such as
    '3.3.7 Loan account management'. Folders written without a space after
    the number ('5.1.6Installation') and ones naming several numbers
    ('5.1.1&5.1.2,5.1.3 Project Plan') are handled; the number part is kept
    exactly as written, tidied of stray spaces and trailing dots."""
    match = _SECTION.match(name)
    if not match:
        return None, name.strip()
    number = re.sub(r"\s+", "", match.group(1)).strip(".")
    return number or None, match.group(2).strip()


def parse_point_number(file_name: str):
    """The write-up's point number from 'X Point No. 7.docx', 'X 12.docx' or
    'X12.docx'; None when the name carries no trailing number."""
    stem = file_name.rsplit(".", 1)[0]
    match = re.search(r"(\d+)\s*$", stem)
    return int(match.group(1)) if match else None
