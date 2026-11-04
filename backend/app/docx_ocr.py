"""Text out of the screenshots embedded in a .docx write-up.

Kept apart from docx_text.py on purpose: that module returns what the file
*says*; this one returns what a recogniser *reads off the pictures*, which is
less reliable and must never be mixed into the document text, the
requirement/response split or the word count. The result is stored in its own
fields (`image_text`, `image_ocr`) and labelled as OCR.

OCR runs in-process with the Tesseract that ships inside PyMuPDF and the
project's own tessdata (see detection.py); nothing is sent to another service.

What it does, per .docx:
  * finds the pictures in the order they appear in the document body (not
    their order in the zip), so "Image 2" is the second picture a reader sees;
  * skips pictures too small to hold readable text (logos, icons, bullets);
  * reads each distinct picture once, so a picture used twice contributes its
    text once;
  * never raises for a bad picture: it is reported with a status instead.
"""

import hashlib
import posixpath
import re
import zipfile
from io import BytesIO
from typing import Dict, List, Optional

from . import detection, redaction

ENGINE = "tesseract-eng (PyMuPDF, in-process)"
# Formats PyMuPDF can decode. EMF/WMF (vector clip-art) are reported, not read.
READABLE = {"png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff"}
MAX_IMAGES = 12            # per document; the rest are counted as "not read"
MAX_TEXT_CHARS = 20000     # per document, so one huge screenshot cannot bloat a record

_EMBED = re.compile(r'<(?:a:blip|v:imagedata)\b[^>]*?\b(?:r:embed|r:id)="([^"]+)"')
_REL = re.compile(r"<Relationship\b[^>]*>")
_ATTR = lambda name: re.compile(r'\b%s="([^"]*)"' % name)


def _ordered_media(archive: zipfile.ZipFile) -> List[str]:
    """Pictures under word/media/, in the order the body first uses them.
    Pictures the body never references (e.g. only in a header) come last, in
    name order, so every file is accounted for."""
    names = [n for n in archive.namelist() if n.startswith("word/media/")]
    ordered: List[str] = []
    try:
        document = archive.read("word/document.xml").decode("utf-8", "ignore")
        rels_xml = archive.read("word/_rels/document.xml.rels").decode("utf-8", "ignore")
        targets: Dict[str, str] = {}
        for tag in _REL.findall(rels_xml):
            rid, target = _ATTR("Id").search(tag), _ATTR("Target").search(tag)
            if rid and target:
                targets[rid.group(1)] = posixpath.normpath(posixpath.join("word", target.group(1)))
        for rid in _EMBED.findall(document):
            path = targets.get(rid)
            if path in names and path not in ordered:
                ordered.append(path)
    except KeyError:
        pass
    ordered += sorted(n for n in names if n not in ordered)
    return ordered


def _lines(words) -> str:
    """Tesseract words -> text lines (grouped by its block/line numbers)."""
    rows: Dict[tuple, list] = {}
    for x0, y0, x1, y1, text, block, line, index in words:
        rows.setdefault((block, line), []).append((index, text))
    return "\n".join(
        " ".join(t for _, t in sorted(parts)) for _, parts in sorted(rows.items())
    ).strip()


def read_picture(data: bytes) -> Optional[str]:
    """OCR text for one picture's bytes. None if the picture cannot be decoded
    or OCR is unavailable; '' if it was read and holds no text."""
    tessdata = detection.tessdata_dir()
    if not tessdata:
        return None
    pymupdf = redaction._pymupdf()
    try:
        pixmap = detection._ocr_ready_pixmap(pymupdf.Pixmap(data), pymupdf)
        words = detection._ocr_pixmap(pixmap, pymupdf, tessdata)
    except Exception:
        return None
    return _lines(words or [])


def extract_image_text(contents: bytes) -> Dict:
    """{'text': str|None, 'ocr': {...}} for a .docx; never raises.

    `text` is the readable pictures' text, each under an "[Image n of N]"
    label, or None when nothing was read. `ocr` says how every picture fared,
    without repeating the text.
    """
    report = {"engine": ENGINE, "images_total": 0, "images_read": 0,
              "images_with_text": 0, "details": []}
    try:
        archive = zipfile.ZipFile(BytesIO(contents))
        media = _ordered_media(archive)
    except (zipfile.BadZipFile, KeyError):
        report["status"] = "unreadable_document"
        return {"text": None, "ocr": report}
    report["images_total"] = len(media)
    if not media:
        report["status"] = "no_images"
        return {"text": None, "ocr": report}
    if not detection.tessdata_dir():
        report["status"] = "ocr_unavailable"
        return {"text": None, "ocr": report}

    seen: Dict[str, int] = {}
    sections: List[str] = []
    attempted = 0
    for number, name in enumerate(media, start=1):
        detail = {"index": number, "file": posixpath.basename(name)}
        report["details"].append(detail)
        extension = name.rsplit(".", 1)[-1].lower()
        data = archive.read(name)
        digest = hashlib.sha256(data).hexdigest()
        if extension not in READABLE:
            detail["status"] = "unsupported_format"
        elif digest in seen:
            detail.update(status="duplicate", same_as=seen[digest])
        elif attempted >= MAX_IMAGES:
            detail["status"] = "not_read_limit"
        else:
            seen[digest] = number
            attempted += 1
            try:
                pixmap = redaction._pymupdf().Pixmap(data)
                big_enough = detection._could_hold_text(pixmap.width, pixmap.height)
                detail["size"] = "%dx%d" % (pixmap.width, pixmap.height)
            except Exception:
                big_enough = None
            if big_enough is None:
                detail["status"] = "unreadable_image"
            elif not big_enough:
                detail["status"] = "too_small"
            else:
                text = read_picture(data)
                if text is None:
                    detail["status"] = "ocr_failed"
                else:
                    report["images_read"] += 1
                    detail["status"] = "read" if text else "no_text"
                    detail["characters"] = len(text)
                    if text:
                        report["images_with_text"] += 1
                        sections.append("[Image %d of %d]\n%s" % (number, len(media), text))
    text = "\n\n".join(sections)[:MAX_TEXT_CHARS] or None
    report["status"] = "ok" if report["images_read"] else "nothing_read"
    return {"text": text, "ocr": report}
