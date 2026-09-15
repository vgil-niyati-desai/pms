"""Show what the detection pass actually reads, and what it makes of it.

For the question this exists to answer: *why was that amount not suggested?*
There are only three possible reasons, and this tells you which one it is:

  1. the text never arrived -- the page is a scan and OCR misread it, or the
     line is split in a way that separates a figure from its label;
  2. the text arrived but no rule matched it -- the phrasing is one the rules
     do not know yet; or
  3. a rule matched and the box was dropped -- too small, or merged away.

Nothing is uploaded and nothing is modified: it opens the file read-only,
prints what it found, and exits.

Usage, from the backend folder:

    .\\venv\\Scripts\\python.exe diagnose_detection.py "C:\\path\\to\\tender.pdf"
    .\\venv\\Scripts\\python.exe diagnose_detection.py tender.pdf --page 3
    .\\venv\\Scripts\\python.exe diagnose_detection.py tender.pdf --all-lines

By default it prints only the lines that look like they *should* have produced
something -- lines holding digits, a currency marker, or a written number --
plus what each one produced. `--all-lines` prints every line instead.

If a line shows under MISSED, paste that line to whoever maintains the rules:
it is the exact string a rule needs to cover, and it is usually enough on its
own. Redact the digits first if the figure is confidential; the *shape* of the
line is what matters, not the value.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import detection  # noqa: E402
from app import redaction  # noqa: E402

# A line worth reporting on: it holds a figure, a currency marker, or a number
# written out in words. Anything else was never a candidate.
INTERESTING = re.compile(
    r"\d|{cur}|\b(?:{words})\b".format(cur=detection._CURRENCY, words=detection._W_ANY),
    re.IGNORECASE,
)


def _lines_for(page, pymupdf):
    """The page's lines, however they have to be obtained, plus which it was."""
    lines = detection._lines_from_text_layer(page, pymupdf)
    how = "text layer"
    if detection._looks_scanned(page, lines):
        if not detection.ocr_available():
            return lines, "text layer (NO OCR: " + detection.OCR_MISSING_HINT + ")"
        ocr_lines = detection._lines_from_ocr(page, pymupdf)
        if ocr_lines is None:
            return lines, "text layer (OCR failed on this page)"
        lines = lines + ocr_lines
        how = "OCR" if not lines[: len(lines) - len(ocr_lines)] else "text layer + OCR"
    return lines, how


def _matches(line):
    """Every (rule, matched text) one line produces."""
    out = []
    for rule in detection._RULES:
        for match in rule.pattern.finditer(line.text):
            if rule.validate and not rule.validate(match):
                continue
            out.append((rule.name, match.group("amount").strip()))
    return out


def report(path, only_page=None, all_lines=False):
    pymupdf = redaction._pymupdf()
    print("file      :", path)
    print("OCR       :", detection.tessdata_dir() or "NOT INSTALLED")

    is_pdf = path.lower().endswith(".pdf")
    if is_pdf:
        doc = redaction._open_pdf(path)
        pages = list(enumerate(doc))
    else:
        pixmap = redaction._open_image_pixmap(path, want_alpha=False)
        doc = pymupdf.open()
        page = doc.new_page(width=pixmap.width, height=pixmap.height)
        page.insert_image(page.rect, pixmap=pixmap)
        pages = [(0, page)]

    try:
        for index, page in pages:
            if only_page is not None and index != only_page:
                continue
            lines, how = _lines_for(page, pymupdf)
            print()
            print("=" * 78)
            print("PAGE %d   rotation=%s   read via: %s   (%d lines)"
                  % (index + 1, page.rotation, how, len(lines)))
            print("=" * 78)

            missed = []
            for line in lines:
                hits = _matches(line)
                interesting = bool(INTERESTING.search(line.text))
                if not (all_lines or interesting):
                    continue
                if hits:
                    print("  OK   %r" % line.text[:110])
                    for name, text in hits:
                        print("         -> %-22s %r" % (name, text))
                elif interesting:
                    missed.append(line.text)
                elif all_lines:
                    print("  --   %r" % line.text[:110])

            if missed:
                print()
                print("  MISSED -- these lines hold a figure or a money word but")
                print("            produced no suggestion. This is what to report:")
                for text in missed:
                    print("     %r" % text[:130])
    finally:
        doc.close()

    print()
    print("=" * 78)
    print("WHAT THE APP WOULD ACTUALLY SUGGEST (after merging and dropping)")
    print("=" * 78)
    payload = (
        detection._scan_pdf(path) if is_pdf else detection._scan_image(path)
    )
    print("  engine=%s  text_pages=%s  ocr_pages=%s  unread=%s"
          % (payload["engine"], payload["text_pages"], payload["ocr_pages"],
             payload["pages_without_text"]))
    if payload.get("message"):
        print("  message: %s" % payload["message"])
    if not payload["detections"]:
        print("  (nothing suggested)")
    for det in payload["detections"]:
        print("  p%-2d %-18s %-7s %-14s %r"
              % (det["page"] + 1, det["label"], det["confidence"], det["rule"],
                 det["text"]))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    target = args[0]
    if not os.path.exists(target):
        print("No such file: %s" % target)
        sys.exit(1)
    page_arg = None
    if "--page" in sys.argv:
        page_arg = int(sys.argv[sys.argv.index("--page") + 1]) - 1
    report(target, page_arg, "--all-lines" in sys.argv)
