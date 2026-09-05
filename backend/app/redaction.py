"""Permanent redaction of a stored file into a separate, new copy.

Two rules shape everything in here:

1. **The original is read-only.** No function writes to UPLOAD_DIR, edits a
   database record, or replaces a stored file. Each one opens a stored file,
   works on an in-memory copy, and hands back fresh bytes. The caller streams
   those bytes to the browser; nothing about the original entry changes.
2. **The redaction is real, not a picture of one.** For a PDF the covered
   text is deleted from the content stream by MuPDF's redaction pass, not
   painted over -- the white box is what is left after the glyphs are gone,
   so there is nothing underneath to select, copy, or pull back out with a
   text extractor. Images inside the box lose the covered pixels the same
   way.

Areas arrive in *normalised* page coordinates: x, y, width and height as
fractions of the page, with the origin at its top-left corner. The browser
draws over a rendered page whose pixel size it chose, and page images are
rendered from the same `page.rect` these fractions are resolved against, so
neither side has to know the other's zoom level or DPI.

PyMuPDF is imported lazily. It is the only dependency the rest of the app
does not need, and a missing install must degrade to a clear message on the
redaction endpoints rather than stop the whole API from starting.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import HTTPException, Response

from . import storage

_IMAGE_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}

# Rendered page images are always PNG; a redacted copy keeps the original's
# own format.
PAGE_IMAGE_MEDIA_TYPE = "image/png"

# What a page image may be rendered at. The default is comfortable to draw on
# at a normal window size; the cap stops a hand-written query string from
# asking for a render big enough to exhaust memory.
DEFAULT_PAGE_IMAGE_WIDTH = 1200
MIN_PAGE_IMAGE_WIDTH = 200
MAX_PAGE_IMAGE_WIDTH = 2600

# An area smaller than this covers no meaningful content and is dropped, so a
# stray click that registers as a one-pixel drag never becomes a redaction.
MIN_AREA_SIZE = 0.002

# What a redacted area is filled with once its content has been removed. Solid
# white, so the copy reads as blanked-out paper rather than struck-through.
# Nothing survives underneath either way -- the fill is cosmetic, and the
# removal above it is what makes the redaction permanent.
#
# The two spellings are the two APIs: PyMuPDF takes page colours as floats
# 0-1, and Pixmap.set_rect takes one integer 0-255 per channel.
PDF_FILL = (1, 1, 1)
IMAGE_FILL_CHANNEL = 255

PYMUPDF_MISSING_DETAIL = (
    "Redaction needs PyMuPDF, which is not installed. Run "
    "`pip install -r backend/requirements.txt` in the backend virtual "
    "environment and restart the API."
)


@dataclass(frozen=True)
class Area:
    """One rectangle to redact, in fractions of its page (origin top-left)."""

    page: int
    x: float
    y: float
    width: float
    height: float


def _pymupdf():
    """The PyMuPDF module, or a 503 explaining how to get it."""
    try:
        import pymupdf
    except ImportError:
        raise HTTPException(status_code=503, detail=PYMUPDF_MISSING_DETAIL)
    return pymupdf


def _extension(stored_file_name: str) -> str:
    return stored_file_name.rsplit(".", 1)[-1].lower() if "." in stored_file_name else ""


def is_pdf(stored_file_name: str) -> bool:
    return _extension(stored_file_name) == "pdf"


def is_image(stored_file_name: str) -> bool:
    return _extension(stored_file_name) in _IMAGE_MEDIA_TYPES


def supports(stored_file_name: Optional[str]) -> bool:
    """Can this stored file be redacted at all? Used to decide whether the
    preview offers a Redact button."""
    if not stored_file_name:
        return False
    return is_pdf(stored_file_name) or is_image(stored_file_name)


def _existing_path(stored_file_name: Optional[str]) -> str:
    """The on-disk path of a stored file, or the 404 the file routes give."""
    if not stored_file_name:
        raise HTTPException(status_code=404, detail="No file for this document")
    path = storage.upload_path(stored_file_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File missing on server")
    if not supports(stored_file_name):
        raise HTTPException(
            status_code=400, detail="Only PDF, PNG and JPG files can be redacted."
        )
    return path


def _open_pdf(path: str):
    """Open a PDF for redaction, refusing the cases we cannot handle."""
    pymupdf = _pymupdf()
    try:
        doc = pymupdf.open(path)
    except Exception:
        raise HTTPException(status_code=400, detail="This PDF could not be opened.")
    if doc.needs_pass:
        doc.close()
        raise HTTPException(
            status_code=400,
            detail="This PDF is password-protected and cannot be redacted.",
        )
    if doc.page_count == 0:
        doc.close()
        raise HTTPException(status_code=400, detail="This PDF has no pages.")
    return doc


def _open_image_pixmap(path: str, want_alpha: bool):
    """Load an image as an RGB pixmap, whatever colour space it arrived in.

    A CMYK or greyscale JPEG would otherwise need its own fill value -- in
    CMYK, all-zero is white and all-max is black, the exact opposite of RGB --
    so converting first is what makes one fill constant correct everywhere.
    """
    pymupdf = _pymupdf()
    try:
        pix = pymupdf.Pixmap(path)
    except Exception:
        raise HTTPException(status_code=400, detail="This image could not be opened.")
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.alpha and not want_alpha:
        # JPEG has no alpha channel to write, so it has to go before encoding.
        pix = pymupdf.Pixmap(pix, 0)
    return pix


def describe(stored_file_name: Optional[str]) -> Dict[str, object]:
    """The page list the redaction UI needs before it can draw anything.

    Sizes are the page's own units -- points for a PDF, pixels for an image --
    and are used only for the aspect ratio the browser lays the page out at.
    """
    path = _existing_path(stored_file_name)

    if is_pdf(stored_file_name):
        doc = _open_pdf(path)
        try:
            pages = [
                {"index": index, "width": page.rect.width, "height": page.rect.height}
                for index, page in enumerate(doc)
            ]
        finally:
            doc.close()
        return {"kind": "pdf", "pages": pages}

    pix = _open_image_pixmap(path, want_alpha=True)
    return {
        "kind": "image",
        "pages": [{"index": 0, "width": float(pix.width), "height": float(pix.height)}],
    }


def render_page_image(
    stored_file_name: Optional[str],
    page_index: int,
    width: int = DEFAULT_PAGE_IMAGE_WIDTH,
) -> bytes:
    """A PNG of one page, for the browser to draw redaction rectangles over.

    PDFs are rendered here rather than shown in the browser's own PDF viewer
    for one reason: an <iframe> is opaque. Nothing outside it can tell where
    a click landed on the page, which is exactly what selecting an area needs.
    """
    path = _existing_path(stored_file_name)
    width = max(MIN_PAGE_IMAGE_WIDTH, min(MAX_PAGE_IMAGE_WIDTH, int(width)))

    if not is_pdf(stored_file_name):
        if page_index != 0:
            raise HTTPException(status_code=404, detail="Page not found")
        return _open_image_pixmap(path, want_alpha=True).tobytes("png")

    pymupdf = _pymupdf()
    doc = _open_pdf(path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise HTTPException(status_code=404, detail="Page not found")
        page = doc[page_index]
        # page.rect already carries the page's /Rotate, and get_pixmap renders
        # in that same space, so the image the user draws on and the rectangle
        # the redaction is applied to share one coordinate system.
        zoom = width / page.rect.width if page.rect.width else 1.0
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pixmap.tobytes("png")
    finally:
        doc.close()


def _clean_areas(areas: List[Area], page_count: int) -> Dict[int, List[Area]]:
    """Drop unusable areas, clamp the rest to the page, and group by page."""
    grouped: Dict[int, List[Area]] = {}
    for area in areas:
        if area.page < 0 or area.page >= page_count:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"A redaction area refers to page {area.page + 1}, "
                    "which this document does not have."
                ),
            )
        x0 = min(max(area.x, 0.0), 1.0)
        y0 = min(max(area.y, 0.0), 1.0)
        x1 = min(max(area.x + area.width, 0.0), 1.0)
        y1 = min(max(area.y + area.height, 0.0), 1.0)
        if x1 - x0 < MIN_AREA_SIZE or y1 - y0 < MIN_AREA_SIZE:
            continue
        grouped.setdefault(area.page, []).append(
            Area(page=area.page, x=x0, y=y0, width=x1 - x0, height=y1 - y0)
        )
    if not grouped:
        raise HTTPException(
            status_code=400,
            detail="Select at least one area to redact before generating a copy.",
        )
    return grouped


def _redact_pdf(path: str, areas: List[Area]) -> bytes:
    pymupdf = _pymupdf()
    doc = _open_pdf(path)
    try:
        grouped = _clean_areas(areas, doc.page_count)
        for page_index, page_areas in grouped.items():
            page = doc[page_index]
            bounds = page.rect
            rects = []
            for area in page_areas:
                rect = (
                    pymupdf.Rect(
                        bounds.x0 + area.x * bounds.width,
                        bounds.y0 + area.y * bounds.height,
                        bounds.x0 + (area.x + area.width) * bounds.width,
                        bounds.y0 + (area.y + area.height) * bounds.height,
                    )
                    & bounds
                )
                if rect.is_empty:
                    continue
                rects.append(rect)
                page.add_redact_annot(rect, fill=PDF_FILL)
            if not rects:
                continue
            # The defaults here are the strict ones, and they are what makes
            # this a real redaction: text under the rectangle is removed from
            # the content stream, covered image pixels are blanked inside the
            # image itself, and vector art the rectangle touches is dropped.
            page.apply_redactions()
            # apply_redactions() has already painted the fill. Drawing it
            # again costs almost nothing and guarantees a clean, uniform area
            # on a page whose annotation appearance was discarded rather than
            # rendered.
            for rect in rects:
                page.draw_rect(rect, color=PDF_FILL, fill=PDF_FILL, width=0)

        # A full rewrite, never an incremental save: incremental would append
        # the changes and leave the original, un-redacted objects sitting in
        # the file. garbage=4 drops the now-unreferenced ones outright.
        return doc.tobytes(garbage=4, deflate=True, clean=True)
    finally:
        doc.close()


def _redact_image(path: str, stored_file_name: str, areas: List[Area]) -> bytes:
    as_jpeg = _extension(stored_file_name) in ("jpg", "jpeg")
    pix = _open_image_pixmap(path, want_alpha=not as_jpeg)
    grouped = _clean_areas(areas, 1)

    pymupdf = _pymupdf()
    # set_rect wants one value per colour channel, alpha excluded.
    fill = (IMAGE_FILL_CHANNEL,) * (pix.n - pix.alpha)
    page_box = pymupdf.IRect(0, 0, pix.width, pix.height)
    for area in grouped[0]:
        box = (
            pymupdf.IRect(
                int(area.x * pix.width),
                int(area.y * pix.height),
                int(round((area.x + area.width) * pix.width)),
                int(round((area.y + area.height) * pix.height)),
            )
            & page_box
        )
        if box.is_empty:
            continue
        # The pixels are overwritten in the decoded bitmap, so the encode
        # below writes the fill where the content was, not a layer over it.
        pix.set_rect(box, fill)

    return pix.tobytes("jpg", jpg_quality=95) if as_jpeg else pix.tobytes("png")


def redact(stored_file_name: Optional[str], areas: List[Area]) -> Tuple[bytes, str]:
    """Redacted bytes for a stored file, plus the media type to serve them as.

    The stored file is only ever opened for reading, so whatever happens in
    here it stays byte-for-byte what it was.
    """
    path = _existing_path(stored_file_name)
    if is_pdf(stored_file_name):
        return _redact_pdf(path, areas), "application/pdf"
    return (
        _redact_image(path, stored_file_name, areas),
        _IMAGE_MEDIA_TYPES[_extension(stored_file_name)],
    )


def redacted_download_name(file_name: Optional[str], stored_file_name: str) -> str:
    """`invoice.pdf` -> `invoice_redacted.pdf`, so the copy is never mistaken
    for the original in a downloads folder."""
    name = file_name or stored_file_name
    stem, dot, extension = name.rpartition(".")
    if not dot:
        return f"{name}_redacted"
    return f"{stem}_redacted.{extension}"


# ---------------------------------------------------------------------------
# The three answers the redaction endpoints give.
#
# They live here, rather than in a router, because both persistence backends
# expose the same endpoints and neither of them has anything to add: once a
# record has been looked up, the only inputs are its stored file name and the
# name to download the copy under.
# ---------------------------------------------------------------------------


def source_payload(
    stored_file_name: Optional[str], file_name: Optional[str]
) -> Dict[str, Any]:
    """The page list, plus the original file name for the UI to show."""
    payload = describe(stored_file_name)
    payload["file_name"] = file_name
    return payload


def page_image_response(
    stored_file_name: Optional[str], page_index: int, width: int
) -> Response:
    """One rendered page, cached by the browser for this session.

    The bytes are a pure function of a file that is never modified in place,
    so re-rendering the same page on every scroll back and forth would be
    wasted work on both sides.
    """
    image = render_page_image(stored_file_name, page_index, width)
    return Response(
        content=image,
        media_type=PAGE_IMAGE_MEDIA_TYPE,
        headers={"Cache-Control": "private, max-age=3600"},
    )


def redacted_copy_response(
    stored_file_name: Optional[str], file_name: Optional[str], areas: List[Area]
) -> Response:
    """The generated copy, as a download. Nothing is written to disk."""
    content, media_type = redact(stored_file_name, areas)
    download_name = redacted_download_name(file_name, stored_file_name or "document")
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": _attachment_header(download_name),
            # The copy exists only for this response; caching it would serve a
            # stale set of rectangles the next time a different one is drawn.
            "Cache-Control": "no-store",
            # fetch() cannot read a header it has not been given access to,
            # and this is where the browser learns the file's name.
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


def _attachment_header(name: str) -> str:
    """A Content-Disposition that survives a non-ASCII or quoted file name.

    The bare `filename=` is the ASCII fallback every browser understands;
    `filename*` carries the real name for the ones that read RFC 5987, which
    is all of them in practice.
    """
    ascii_name = name.encode("ascii", "ignore").decode("ascii").replace('"', "")
    ascii_name = ascii_name.strip() or "redacted-copy"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}'
