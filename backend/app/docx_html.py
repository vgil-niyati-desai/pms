"""A read-only HTML view of a write-up (.docx or .rtf), for the proposal library's
"view before downloading". No third-party dependency.

Every piece of text is escaped and only tags built here are emitted, so the
result is safe to show; the page also shows it in a sandboxed frame. Covered:
paragraphs, headings, bold / italic / underline, line breaks and tabs, bulleted
and numbered lists, and tables. Header/footer content and pictures are not
shown (in the sampled write-ups the pictures are header logos); the stored file
and its download are never changed. An .rtf is shown as plain paragraphs because
its formatting is not kept.
"""

import re
import zipfile
from html import escape
from io import BytesIO
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from . import docx_text

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_OFF = {"0", "false", "none"}


def _flag(properties, name: str) -> bool:
    node = properties.find(_W + name) if properties is not None else None
    return node is not None and node.get(_W + "val", "1").lower() not in _OFF


def _numbering(archive: zipfile.ZipFile) -> Dict[str, Dict[str, str]]:
    """{numId: {level: numFmt}} from word/numbering.xml (empty when absent)."""
    try:
        root = ET.fromstring(archive.read("word/numbering.xml"))
    except (KeyError, ET.ParseError):
        return {}
    abstract: Dict[str, Dict[str, str]] = {}
    for node in root.findall(_W + "abstractNum"):
        levels = {}
        for level in node.findall(_W + "lvl"):
            fmt = level.find(_W + "numFmt")
            levels[level.get(_W + "ilvl", "0")] = fmt.get(_W + "val", "bullet") if fmt is not None else "bullet"
        abstract[node.get(_W + "abstractNumId", "")] = levels
    result = {}
    for num in root.findall(_W + "num"):
        link = num.find(_W + "abstractNumId")
        if link is not None:
            result[num.get(_W + "numId", "")] = abstract.get(link.get(_W + "val", ""), {})
    return result


def _runs(paragraph) -> str:
    out: List[str] = []
    for run in paragraph.iter(_W + "r"):
        properties = run.find(_W + "rPr")
        pieces = []
        for child in run:
            if child.tag == _W + "t":
                pieces.append(escape(child.text or ""))
            elif child.tag == _W + "tab":
                pieces.append("&emsp;")
            elif child.tag == _W + "br":
                pieces.append("<br>")
        text = "".join(pieces)
        if not text:
            continue
        if _flag(properties, "b"):
            text = "<strong>%s</strong>" % text
        if _flag(properties, "i"):
            text = "<em>%s</em>" % text
        if properties is not None and (properties.find(_W + "u") is not None) and _flag(properties, "u"):
            text = "<u>%s</u>" % text
        out.append(text)
    html = "".join(out)
    for tag in ("strong", "em", "u"):
        html = html.replace("</%s><%s>" % (tag, tag), "")
    return html


def _paragraph(paragraph, numbering) -> Optional[tuple]:
    """(kind, html) where kind is 'ul', 'ol' or None for a plain block."""
    inner = _runs(paragraph).strip()
    if not re.sub(r"<[^>]+>|&emsp;|\s", "", inner):
        return None
    properties = paragraph.find(_W + "pPr")
    style = properties.find(_W + "pStyle") if properties is not None else None
    style_name = (style.get(_W + "val", "") if style is not None else "").lower()
    number = properties.find(_W + "numPr") if properties is not None else None
    if number is not None:
        num_id = number.find(_W + "numId")
        level = number.find(_W + "ilvl")
        fmt = numbering.get(num_id.get(_W + "val", "") if num_id is not None else "", {}).get(
            level.get(_W + "val", "0") if level is not None else "0", "bullet")
        if num_id is not None and num_id.get(_W + "val") != "0":
            return ("ul" if fmt == "bullet" else "ol", "<li>%s</li>" % inner)
    heading = re.match(r"heading(\d)", style_name)
    if heading or style_name == "title":
        return (None, ("<h4>%s</h4>" if heading else "<h3>%s</h3>") % inner)
    align = properties.find(_W + "jc") if properties is not None else None
    side = align.get(_W + "val") if align is not None else None
    css = {"center": "center", "right": "right", "both": "justify"}.get(side or "")
    return (None, '<p style="text-align:%s">%s</p>' % (css, inner) if css else "<p>%s</p>" % inner)


def _blocks(parent, numbering) -> str:
    out: List[str] = []
    open_list: Optional[str] = None

    def close():
        nonlocal open_list
        if open_list:
            out.append("</%s>" % open_list)
            open_list = None

    for node in parent:
        if node.tag == _W + "p":
            block = _paragraph(node, numbering)
            if block is None:
                continue
            kind, html = block
            if kind != open_list:
                close()
                if kind:
                    out.append("<%s>" % kind)
                    open_list = kind
            out.append(html)
        elif node.tag == _W + "tbl":
            close()
            rows = []
            for row in node.findall(_W + "tr"):
                cells = "".join("<td>%s</td>" % _blocks(cell, numbering) for cell in row.findall(_W + "tc"))
                rows.append("<tr>%s</tr>" % cells)
            out.append("<table>%s</table>" % "".join(rows))
        elif node.tag == _W + "sdt":
            content = node.find(_W + "sdtContent")
            if content is not None:
                close()
                out.append(_blocks(content, numbering))
    close()
    return "".join(out)


def render_docx(contents: bytes) -> str:
    try:
        archive = zipfile.ZipFile(BytesIO(contents))
        root = ET.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise docx_text.UnreadableDocument("not a valid .docx file") from error
    body = root.find(_W + "body")
    return _blocks(body, _numbering(archive)) if body is not None else ""


def render(contents: bytes, extension: str) -> Dict:
    """{'html': fragment, 'fidelity': 'formatted' | 'text'}."""
    extension = extension.lower().lstrip(".")
    if extension == "docx":
        return {"html": render_docx(contents), "fidelity": "formatted"}
    if extension == "rtf":
        paragraphs = docx_text.extract_rtf(contents)["paragraphs"]
        return {"html": "".join("<p>%s</p>" % escape(p) for p in paragraphs), "fidelity": "text"}
    raise docx_text.UnreadableDocument("unsupported file type: " + extension)
