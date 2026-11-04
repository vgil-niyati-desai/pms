"""Text out of proposal write-ups (.docx and .rtf).

No third-party dependency: a .docx is a zip of XML, so the paragraphs and the
number of embedded pictures are read straight from it. An .rtf is read with a
small parser of the RTF structure (see extract_rtf).

Nothing here is interpreted. The caller decides what the first paragraph or
the rest mean; this only returns what the file contains, in order.
"""

import re
import zipfile
from io import BytesIO
from typing import Dict, List

_PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>|<w:tab\s*/>|<w:br(?:\s[^>]*)?/>")
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


# Groups whose content is not document text (tables, styles, headers, pictures...).
_RTF_SKIP = {
    "fonttbl", "colortbl", "stylesheet", "info", "header", "headerl", "headerr", "headerf",
    "footer", "footerl", "footerr", "footerf", "pict", "nonshppict", "listtable",
    "listoverridetable", "rsidtbl", "generator", "themedata", "colorschememapping", "datastore",
    "latentstyles", "xmlnstbl", "fldinst", "listtext", "pntext", "pntxta", "pntxtb", "revtbl",
    "filetbl", "pgptbl", "mmathpr", "wgrffmtfilter", "ftnsep", "ftnsepc", "aftnsep", "aftnsepc",
    "private", "object", "objdata", "shpinst", "falt", "panose",
}
# control word + optional number | \'hh | control symbol | brace | plain text
_RTF_TOKEN = re.compile(
    r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(\r?\n|[^a-zA-Z])|([{}])|([^\\{}\r\n]+)")
_RTF_SYMBOLS = {"bullet": "\u2022", "endash": "\u2013", "emdash": "\u2014", "lquote": "\u2018",
                "rquote": "\u2019", "ldblquote": "\u201c", "rdblquote": "\u201d", "tab": " ",
                "line": " ", "emspace": " ", "enspace": " ", "qmspace": " "}


def extract_rtf(contents: bytes) -> Dict:
    """Plain text from an RTF, one entry per paragraph.

    Reads the RTF structure: font/colour/style tables, headers and footers,
    field instructions and picture data are skipped, \\'xx and \\uN escapes
    are decoded, and list bullets become a leading bullet as in a .docx.
    Pictures are counted, not extracted. Text formatting is not kept.
    """
    if not contents.lstrip().startswith(b"{\\rtf"):
        raise UnreadableDocument("not a valid .rtf file")
    text = contents.decode("latin-1")
    codepage = re.search(r"\\ansicpg(\d+)", text[:2000])
    codec = "cp" + codepage.group(1) if codepage else "cp1252"
    try:
        "x".encode(codec)
    except LookupError:
        codec = "cp1252"

    paragraphs: List[str] = []
    line: List[str] = []
    images = 0
    stack: List[tuple] = []  # (skipping, unicode fallback length) of the enclosing groups
    skipping, uc, to_skip = False, 1, 0
    group_start = star = False

    def flush():
        joined = re.sub(r"\s+", " ", "".join(line)).strip()
        if joined:
            paragraphs.append(joined)
        line.clear()

    def emit(chars: str):
        nonlocal to_skip
        if skipping or not chars:
            return
        if to_skip:
            drop = min(to_skip, len(chars))
            chars, to_skip = chars[drop:], to_skip - drop
        line.append(chars)

    for match in _RTF_TOKEN.finditer(text):
        word, number, hexa, symbol, brace, plain = match.groups()
        if brace == "{":
            stack.append((skipping, uc))
            group_start, star = True, False
            continue
        if brace == "}":
            if stack:
                skipping, uc = stack.pop()
            group_start = star = False
            continue
        if group_start:
            if symbol == "*":
                star = True
                continue
            group_start = False
            name = (word or "").lower()
            if (star and name != "shppict") or name in _RTF_SKIP:
                if name == "pict" and not skipping:
                    images += 1
                skipping = True
        if word:
            name = word.lower()
            if name in ("par", "cell", "sect", "page"):
                if not skipping:
                    flush()
            elif name == "uc":
                uc = int(number or 1)
            elif name == "u" and number is not None and not skipping:
                code = int(number)
                code += 65536 if code < 0 else 0
                emit(chr(code))
                to_skip = uc
            elif name in _RTF_SYMBOLS:
                emit(_RTF_SYMBOLS[name])
        elif hexa:
            if to_skip:
                to_skip -= 1
            elif not skipping:
                line.append(bytes([int(hexa, 16)]).decode(codec, "replace"))
        elif symbol:
            if symbol in "\\{}":
                emit(symbol)
            elif symbol in ("\n", "\r\n"):
                if not skipping:
                    flush()
            elif symbol == "~":
                emit(" ")
            elif symbol == "_":
                emit("-")
        elif plain:
            emit(plain)
    flush()
    bulleted = [re.sub(r"^[\u00b7\uf0b7]\s*", "\u2022 ", p) for p in paragraphs]
    return {"paragraphs": bulleted, "images": images, "method": "rtf"}


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
    if match:
        return int(match.group(1))
    # 'BULLET PAYMENT - 3 REHABILITATION': a number in the middle counts only
    # when it is the one standalone number in the name.
    standalone = re.findall(r"(?<![\w.])(\d+)(?![\w.])", stem)
    return int(standalone[0]) if len(standalone) == 1 else None
