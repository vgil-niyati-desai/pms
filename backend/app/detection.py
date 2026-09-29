"""Automatic detection of sensitive financial figures in a stored document.

This is a *suggestion* pass, and nothing more. It reads a stored file, finds
the places that look like money, and hands back rectangles in exactly the
format `redaction.Area` already uses. It never writes anything, never touches
the original, and never redacts on its own: what comes out of here is a list
for a person to accept or reject, and an accepted box then goes through the
existing manual redaction endpoint unchanged.

Three things shape the code.

**The coordinate format is the manual one, deliberately.** A suggested box and
a hand-drawn box are the same four numbers -- x, y, width and height as
fractions of the page, origin top-left -- so the review UI can draw both on
the same surface and the backend cannot tell them apart by the time they
reach `redaction.redact`. That is the whole point: automatic detection adds a
source of areas, not a second redaction path.

**Rotation is converted, not ignored.** PyMuPDF reports extracted text in the
page's *unrotated* space, while `page.rect` -- what the page images are
rendered from, and what `redaction._redact_pdf` resolves fractions against --
is the *rotated* space. On a /Rotate 90 page those two disagree completely, so
every text rectangle is pushed through `page.rotation_matrix` before it is
normalised. Skip that and every suggestion on a rotated page lands somewhere
else entirely; there is a check for exactly this in verify_mongo.py.

**Every format reaches the same rules.** Everything below works off a sequence
of lines, each carrying its text and the boxes of the words in it. Two things
produce that shape and nothing else has to care which:

    text layer  ->  _lines_from_text_layer   (a normal, born-digital PDF)
    OCR         ->  _lines_from_ocr          (a scan, or a JPG/PNG)

So a text PDF, a scanned PDF, a mixed PDF and a photograph all run through the
same financial patterns, the same span-to-box mapping and the same rotation
conversion, and all come back in the same normalised coordinates. Routing is
per *page*, not per document, which is what makes a mixed PDF work: each page
is asked whether its text layer is worth anything, and only the ones that need
it pay for OCR.

**OCR reads the page upright.** Tesseract is given a rendered page, and a
/Rotate 90 page renders with its text running down the side -- which comes
back as unusable noise. So the page's rotation is set to 0 *in memory* for the
duration of the OCR call and put straight back afterwards (the document is
opened read-only and never saved, so the file on disk is untouched either
way). The boxes then arrive in the same unrotated space the text layer uses,
and the one rotation conversion at the bottom serves both.
"""

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import redaction
from .redaction import Area

# A suggestion is offered for review, so a near-miss costs a click and a
# missed figure costs a leak. The patterns below lean towards offering it, and
# every rule carries a confidence the UI shows, so the reviewer knows which
# ones deserve a second look.
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"

# The categories a detection can belong to. Only one today; named rather than
# assumed so a later pass (names, addresses, bank details) slots in beside it
# without the response or the UI having to change shape.
CATEGORY_FINANCIAL = "financial"

# How the text under the boxes was obtained. Reported per document, and the
# per-page lists below say which pages went which way.
ENGINE_TEXT = "text"     # every page read from the PDF's own text layer
ENGINE_OCR = "ocr"       # every page read by OCR (a scan, or an image)
ENGINE_MIXED = "mixed"   # some of each, which is what a mixed PDF looks like
ENGINE_NONE = "none"     # nothing could be read at all

# ---------------------------------------------------------------------------
# OCR
#
# Tesseract, through PyMuPDF's own binding to it. It is a *system* install,
# not a Python package, so it can be absent on a machine the rest of the app
# runs on perfectly well -- every entry point below treats that as "this page
# could not be read" and says so, rather than failing the request or, worse,
# returning an empty list that reads as "nothing sensitive here".
# ---------------------------------------------------------------------------

OCR_LANGUAGE = "eng"

# ---- how the pixels reach Tesseract ---------------------------------------
#
# This is the part that decides whether a scanned page is read or misread, and
# it is not the obvious knob. Rendering the *page* at a fixed DPI is wrong for
# the commonest real case -- a photograph embedded in a PDF -- because the
# photograph has a resolution of its own and the page render resamples it:
#
#   photo placed at        page render        what Tesseract got
#   -------------------    ---------------    --------------------------
#   full page              300 dpi            fine
#   inset with a margin    300 dpi            nonsense: 'f', 'j', '|', 'SS ean'
#   a corner of the page   300 dpi            nothing at all
#
# Upsampling a 200-dpi photograph to 300 interpolates the glyph edges and
# amplifies its JPEG artefacts, and Tesseract returns confident speckle rather
# than failing. Worse, a big mostly-blank page with a small photograph on it
# defeats its page segmentation entirely.
#
# So the image is OCR'd *as an image*, at its own pixels, and the word boxes
# are mapped back onto the page afterwards. Tesseract then always gets what it
# is tuned for: a tightly cropped document at the resolution it was captured
# at, whatever size it happens to sit at on the page. Rendering the page stays
# as the fallback for pages that are not one image -- a vector-drawn scan, or
# an image placed under a rotation this cannot map.

# Never upsample: 1:1 is a scratch page whose *points* are the image's pixels,
# which is exactly 72 dpi.
OCR_NATIVE_DPI = 72

# The fallback page render. 200 rather than 300 deliberately -- measured, on
# the same photograph, 200 read every placement correctly and 300 read three
# of four as noise.
OCR_PAGE_RENDER_DPI = 200

# Cost bounds on one image. Above the cap it is scaled down, which OCR
# tolerates far better than being scaled up; below the floor it is scaled up,
# because a thumbnail has nothing to read either way and Tesseract does better
# with something than with 40 pixels of text.
OCR_MAX_PIXELS = 4000
OCR_MIN_PIXELS = 1000

# Below this share of tokens looking like words, a read is speckle rather than
# text, and the other strategy is worth trying. Measured: a good read of the
# test page scores 0.72, and every failing render scored 0.00-0.39.
OCR_QUALITY_FLOOR = 0.45

# OCR is seconds per page, not milliseconds, and this endpoint is called
# synchronously when the redactor opens. A 400-page scan would hang it, so
# only this many pages are OCR'd and the rest are reported as unread -- an
# honest "I did not look at these" beats a request that never returns.
OCR_PAGE_BUDGET = 40

# A page whose text layer yields fewer words than this is treated as having
# nothing useful on it. Set low: the aim is to catch a scan carrying a stray
# text watermark, not to second-guess a genuinely sparse title page.
MIN_TEXT_WORDS = 6

# What makes an image worth OCRing: its own pixel count, NOT how much of the
# page it covers.
#
# Coverage was the obvious test and it is the wrong one. A photograph of a
# work order pasted into a corner of an A4 page covers 7% of it and holds
# every figure in the document; a company logo across the top of a letterhead
# covers more and holds nothing. What separates them is resolution — a
# document capture is a thousand pixels or more on its long side whatever size
# it is printed at, and a logo or a signature strip is a few hundred.
#
# Getting this wrong in the safe-looking direction is what left a corner-
# mounted photograph unread.
OCR_MIN_IMAGE_LONG_SIDE = 600
OCR_MIN_IMAGE_PIXELS = 240_000

# ---- where the language data comes from -----------------------------------
#
# Worth knowing before changing any of this: **there is no Tesseract process.**
# PyMuPDF's wheels carry Tesseract and Leptonica linked into MuPDF's own shared
# library, so OCR happens inside this process. Nothing here shells out, nothing
# needs `tesseract` on PATH, and `get_textpage_ocr(tessdata=...)` returns an
# explicit path verbatim without reading or writing TESSDATA_PREFIX.
#
# That is what makes the install self-contained: the only thing OCR needs from
# outside the process is a directory holding `eng.traineddata`, and the one
# below ships with the project. Nothing is installed system-wide, no global
# environment variable is set, and nothing else on a shared machine is touched
# or depended on.
#
# The project's own copy, `backend/ocr/tessdata/`. Resolved from this file
# rather than the working directory, so it is found however the API is started.
PROJECT_TESSDATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ocr", "tessdata"
)

# The one setting: point this at a tessdata directory to override the bundled
# one (a server that keeps language data on a shared volume, or an extra
# language). Deliberately its own name rather than TESSDATA_PREFIX, which is
# global to Tesseract everywhere and is exactly what this avoids needing.
TESSDATA_ENV_VAR = "PMS_TESSDATA_DIR"

# A system-wide install, used only if the project's own copy is missing. These
# keep an existing developer machine working unchanged; no deployment relies
# on them.
_TESSDATA_FALLBACKS = (
    r"C:\Program Files\Tesseract-OCR\tessdata",
    r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
    "/opt/homebrew/share/tessdata",
)

OCR_MISSING_HINT = (
    "OCR language data is missing, so scanned pages and images cannot be "
    "read. Put eng.traineddata in backend/ocr/tessdata/ (or set "
    f"{TESSDATA_ENV_VAR} to a folder holding it) and restart the API, or mark "
    "those areas by hand."
)


def _usable_tessdata(path: Optional[str]) -> Optional[str]:
    """A tessdata directory is only usable if the language file is in it."""
    if not path:
        return None
    if os.path.isfile(os.path.join(path, f"{OCR_LANGUAGE}.traineddata")):
        return path
    return None


@lru_cache(maxsize=1)
def tessdata_dir() -> Optional[str]:
    """Where the OCR language data is, or None if there is none to be had.

    The order is the point. The project's own copy is preferred over anything
    installed on the machine, so a shared server running several projects gives
    this one the data it shipped with rather than whatever another project or
    the distribution happens to have put in /usr/share -- and so an install
    here cannot change behaviour there.

    Cached, because this is filesystem probing on a path that does not change
    while the process is up, and it is asked once per page otherwise.
    """
    candidates = (
        # An explicit instruction wins over everything.
        os.environ.get(TESSDATA_ENV_VAR),
        # The copy that ships with the project: the normal answer, and the one
        # that needs nothing installed.
        PROJECT_TESSDATA,
        # The standard variable, for a process that has deliberately set it.
        os.environ.get("TESSDATA_PREFIX"),
    )
    for candidate in candidates:
        found = _usable_tessdata(candidate)
        if found:
            return found

    for candidate in _TESSDATA_FALLBACKS:
        found = _usable_tessdata(candidate)
        if found:
            return found

    # Last resort, and the only thing in this module that touches the wider
    # machine: PyMuPDF looks for a Tesseract installation, which runs
    # `tesseract --list-langs` in a shell. Left in place so a machine that was
    # working before keeps working, but nothing is expected to reach it -- the
    # project's own copy is found several steps earlier.
    try:
        return _usable_tessdata(redaction._pymupdf().get_tessdata())
    except Exception:
        return None


def ocr_available() -> bool:
    """Can this machine read a scanned page at all?"""
    return tessdata_dir() is not None

# The same ceiling `schemas.RedactionRequest` puts on a redaction: there is no
# point suggesting more boxes than could be sent back.
MAX_DETECTIONS = 500

# Glyph boxes are tight to the ink. A hair of padding means a suggestion
# covers the whole figure rather than clipping the top of a "5", and matches
# the small margin a hand-drawn box naturally has. Page units (points).
PAD_X = 1.5
PAD_Y = 1.0

# Two suggestions covering essentially the same figure -- the keyword rule and
# the currency rule both firing on "Total: Rs. 4,50,000" -- are one figure, so
# the weaker of the pair is dropped once this much of the smaller box lies
# inside the larger.
MERGE_OVERLAP = 0.6


# ---------------------------------------------------------------------------
# What money looks like in this corpus
# ---------------------------------------------------------------------------

# Indian and Western digit grouping in one pattern: 4,50,00,000 and 4,500,000
# both parse, as do a plain 450000 and a 1234.50.
# The optional spaces around the separator are for OCR, not for typography:
# Tesseract routinely returns "1 ,200.50" or "4,50 ,000" for a figure that is
# printed without them, and a pattern that insists on clean grouping quietly
# misses exactly the amounts on a scan that it catches on a text PDF.
_NUMBER = r"\d{1,3}(?:\s?,\s?\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"

# Written scales. Single-letter forms ("k", "cr") are left out on purpose:
# alone they are too easy to hit inside a part number, and in practice they
# arrive next to a currency marker, which the currency rule already catches.
_SCALE = r"(?:lakhs?|lacs?|crores?|millions?|billions?|thousands?)"

# The lettered codes must stand as a word of their own. Matched case-blind
# and unguarded, "Rs" is the tail of "years", "hours" and "members", so
# "years 2019" came back as a sure currency amount -- and "SAR" / "USD" hit
# the front of longer words the same way. Only letters are ruled out on
# either side: "Rs.50,000", "(INR 45 lakh)" and "500Rs" all still read as
# money. The symbols need no guard; nothing ordinary ends in "₹".
_CURRENCY = (
    "(?:₹|\\$|€|£|(?<![A-Za-z])(?:Rs\\.?|INR|USD|EUR|GBP|AED|SAR)(?![A-Za-z]))"
)

# A trailing "/-" or "only", the way an amount is written out on an Indian
# invoice, is part of the figure and belongs inside the box.
_TAIL = r"(?:\s*/\s*-|\s+only\b)?"

# The words that make a number on the same line a money number. Longest first,
# so "contract value" wins over "value" and the label reads correctly.
_KEYWORDS = (
    "earnest money deposit", "earnest money", "liquidated damages",
    "performance guarantee", "performance security", "security deposit",
    "bank guarantee", "contract value", "contract price", "contract amount",
    "estimated cost", "estimated value", "quoted price", "quoted amount",
    "quoted rate", "tender value", "tender cost", "invoice value",
    "invoice amount", "total amount", "total value", "total cost",
    "grand total", "sub total", "subtotal", "net total", "unit price",
    "unit rate", "bid value", "bid price", "lump sum", "lumpsum",
    "service tax", "bg amount", "amount payable", "total", "emd",
    "price", "prices", "cost", "costs", "charges", "charge", "fees", "fee",
    "rate", "rates", "amount", "value", "budget", "quotation", "quote",
    "turnover", "revenue", "profit", "margin", "discount", "rebate",
    "payment", "advance", "retention", "penalty", "deposit",
    "gst", "igst", "cgst", "sgst", "vat", "tds", "tax", "taxes",
    "salary", "ctc", "remuneration", "wages",
)
_KEYWORD = "|".join(re.escape(word) for word in _KEYWORDS)

# Anything but a digit, bounded, so a keyword reaches across ": Rs. " or
# " payable is " to its figure without reaching across half a paragraph to an
# unrelated one. Non-greedy: the nearest number wins.
_GAP_LONG = r"[^\d\n]{0,30}?"
_GAP_SHORT = r"[^\d\n]{0,12}?"

# A figure must not be a slice of a longer token: the "4500" inside
# "PO-4500-B" is a part number, not a price.
#
# `_LEFT` is deliberately NOT applied after a currency marker. It rejects a
# digit preceded by a dot, and "Rs." ends in one -- so guarding the number in
# "Rs.50,000/-" with it threw away one of the most common ways an amount is
# written on an Indian invoice. The currency marker is itself the proof that
# the digits after it start a figure, so nothing is lost by dropping the
# guard there and a great deal was lost by keeping it.
_LEFT = r"(?<![\w.])"
_RIGHT = r"(?![\w.]*\d)"

_MONEYISH = (
    # Rs.50,000 / Rs. 4,50,000 / INR 45 lakh / 900 USD
    "(?:{cur}\\s*(?:{num})(?:\\s*{scale})?"
    "|{left}(?:{num})(?:\\s*{scale})?\\s*{cur}"
    # 4,50,000 / 2.5 crore -- grouped or scaled, so a bare "3" never qualifies
    "|{left}(?:\\d{{1,3}}(?:\\s?,\\s?\\d{{2,3}})+(?:\\.\\d+)?)"
    "|{left}(?:{num})\\s*{scale})"
).format(cur=_CURRENCY, num=_NUMBER, scale=_SCALE, left=_LEFT)

# ---------------------------------------------------------------------------
# Amounts written out in words
#
# "Rs. 4,50,000 (Rupees Four Lakh Fifty Thousand Only)" is the standard way an
# Indian tender states a figure, and covering the digits while leaving the
# words next to them redacts nothing at all -- the amount is still sitting
# there in plain English. So the words are a figure in their own right here,
# boxed separately from the numeral beside them.
# ---------------------------------------------------------------------------

_W_ONES = (
    "nineteen|eighteen|seventeen|sixteen|fifteen|fourteen|thirteen|twelve|"
    "eleven|ten|nine|eight|seven|six|five|four|three|two|one|zero"
)
_W_TENS = "twenty|thirty|forty|fourty|fifty|sixty|seventy|eighty|ninety"
_W_SCALE_WORD = (
    "hundreds?|thousands?|lakhs?|lacs?|crores?|millions?|billions?|arabs?"
)
# "Rs." and "INR" lead a written amount as often as "Rupees" does --
# "Rs. Eight Lakh Fifty Thousand Only" is written exactly that way on a
# cheque line -- so the short forms belong here too, not only in _CURRENCY.
_W_CURRENCY = (
    "rupees|rupee|rs\\.?|inr|dollars|dollar|usd|euros|euro|pounds|pound|"
    "taka|dirhams|riyals"
)
# The tail end of a written amount: the fractional unit and the "only" that
# closes it off.
_W_MINOR = "paise|paisa|cents|cent|pence"

# A word that can appear anywhere inside a written amount.
_W_ANY = "(?:{ones}|{tens}|{scale}|{cur}|{minor}|and|point|only)".format(
    ones=_W_ONES, tens=_W_TENS, scale=_W_SCALE_WORD, cur=_W_CURRENCY, minor=_W_MINOR
)
# ...and the subset that makes it an amount rather than a stray word.
_W_NUMBER = "(?:{ones}|{tens}|{scale})".format(
    ones=_W_ONES, tens=_W_TENS, scale=_W_SCALE_WORD
)
# What separates two words of it. Includes the punctuation OCR sprinkles in.
_W_JOIN = r"[\s,.\-]+"

_W_NUMBER_RE = re.compile(r"\b(?:%s)\b" % _W_NUMBER, re.IGNORECASE)


def _has_written_number(match) -> bool:
    """Reject "Rupees only" and "the only copy"; keep "Rupees Four Lakh Only".

    The patterns are kept simple and this does the discriminating, because
    demanding a number word *inside* the repetition turns the expression into
    something that backtracks badly on a long line.
    """
    return bool(_W_NUMBER_RE.search(match.group("amount")))


# Words that turn the number after them into an identifier rather than an
# amount: a phone number, an account, a reference. Anchored to the end of the
# text *before* the match, so only an immediately preceding label counts.
#
# "invoice no" is listed while "invoice value" is a keyword above, and the two
# have to stay on their own sides of that line -- hence matching the specific
# phrases rather than just "invoice".
_IDENTIFIER_BEFORE = re.compile(
    r"\b(?:phone|mobile|tel|telephone|fax|contact|account|a/c|acc|ifsc|pan|"
    r"gstin|tin|pin|pincode|zip|ref|reference|challan|serial|sr|id|code|"
    r"registration|licence|license|no|nos|number|num)\b\s*[.:#\-/]*\s*$",
    re.IGNORECASE,
)


def _not_an_identifier(match) -> bool:
    """Is this number an amount, or the reference number of something?

    Applied only to the rules loose enough to need it -- the ones that fire on
    a bare number with no currency marker and no financial keyword of its own.
    The stricter rules do not want this: "Account ... Rs. 4,50,000" is an
    amount whatever sits in front of it.
    """
    return not _IDENTIFIER_BEFORE.search(match.string[: match.start("amount")])


def _substantial_figure(match) -> bool:
    """Is a lone number on a line big enough to be money rather than a label?

    A line holding nothing but a number is almost always a value cell, with
    its heading in a row above that this line-by-line pass cannot see. But
    "12" and "2024" are also lines holding nothing but a number, so the bar is
    a figure that would be an odd page number or year: five digits, or four
    with grouping or decimals, or any number with a currency marker on it.
    """
    amount = match.group("amount")
    digits = re.sub(r"\D", "", amount)
    if not digits:
        return False
    if not _not_an_identifier(match):
        return False
    if re.search(_CURRENCY, amount, re.IGNORECASE):
        return True
    if len(digits) >= 5:
        return True
    return len(digits) >= 4 and ("," in amount or "." in amount)


@dataclass(frozen=True)
class _Rule:
    """One way of recognising a figure, and what to call it when it fires."""

    name: str
    pattern: "re.Pattern"
    confidence: str
    # None means "take the label from the keyword the rule matched".
    label: Optional[str] = None
    # An optional second opinion on a match, for the rules where expressing
    # the condition in the pattern itself would make it slow or unreadable.
    # Returning False drops the match as if the pattern had not fired.
    validate: Optional[Any] = None


# Order is for readability only: every rule is run over every line, and
# overlapping hits are merged afterwards.
_RULES: Tuple[_Rule, ...] = (
    _Rule(
        # "Contract value: Rs. 4,50,000", "EMD 2.5 lakh", "Total - INR 9,900"
        name="keyword_money",
        pattern=re.compile(
            "(?P<keyword>\\b(?:{kw})\\b){gap}(?P<amount>{money}{tail}){right}".format(
                kw=_KEYWORD, gap=_GAP_LONG, money=_MONEYISH, tail=_TAIL, right=_RIGHT
            ),
            re.IGNORECASE,
        ),
        confidence=CONFIDENCE_HIGH,
    ),
    _Rule(
        # "Total 450000" -- a plain figure is money only when a keyword is
        # sitting right next to it, hence the shorter gap and the 3-digit floor.
        name="keyword_plain",
        pattern=re.compile(
            "(?P<keyword>\\b(?:{kw})\\b){gap}"
            "(?P<amount>{left}\\d{{3,}}(?:\\.\\d+)?{tail}){right}".format(
                kw=_KEYWORD, gap=_GAP_SHORT, left=_LEFT, tail=_TAIL, right=_RIGHT
            ),
            re.IGNORECASE,
        ),
        confidence=CONFIDENCE_HIGH,
    ),
    _Rule(
        # "Interest rate 7.5%", "GST @ 18 %"
        name="keyword_percent",
        pattern=re.compile(
            "(?P<keyword>\\b(?:{kw})\\b){gap}(?P<amount>{left}\\d+(?:\\.\\d+)?\\s*%)".format(
                kw=_KEYWORD, gap=_GAP_LONG, left=_LEFT
            ),
            re.IGNORECASE,
        ),
        confidence=CONFIDENCE_HIGH,
    ),
    _Rule(
        # A currency marker is money whether or not anything labels it.
        name="currency_amount",
        pattern=re.compile(
            # No {left} after the currency marker -- see the note on _LEFT.
            # With it, "Rs.8,50,000" matched only when a keyword happened to
            # share the line; a bare one in a table cell was missed entirely,
            # and grouped_number boxed just the "50,000" out of it, leaving
            # "Rs.8," sitting on the page under a box that looked complete.
            "(?P<amount>{cur}\\s*(?:{num})(?:\\s*{scale})?{tail}"
            "|{left}(?:{num})(?:\\s*{scale})?\\s*{cur}){right}".format(
                cur=_CURRENCY, num=_NUMBER, scale=_SCALE, left=_LEFT,
                tail=_TAIL, right=_RIGHT
            ),
            re.IGNORECASE,
        ),
        confidence=CONFIDENCE_HIGH,
        label="Currency amount",
    ),
    _Rule(
        name="scale_amount",
        pattern=re.compile(
            "(?P<amount>{left}(?:{num})\\s*{scale}){right}".format(
                num=_NUMBER, scale=_SCALE, left=_LEFT, right=_RIGHT
            ),
            re.IGNORECASE,
        ),
        confidence=CONFIDENCE_HIGH,
        label="Amount",
    ),
    _Rule(
        # A grouped figure in a bare table cell, with no label and no symbol
        # anywhere near it. Often a price; sometimes a quantity, which is why
        # this is the one rule that does not claim to be sure.
        name="grouped_number",
        pattern=re.compile(
            "(?P<amount>{left}\\d{{1,3}}(?:\\s?,\\s?\\d{{2,3}})+(?:\\.\\d+)?){right}".format(
                left=_LEFT, right=_RIGHT
            ),
        ),
        confidence=CONFIDENCE_MEDIUM,
        label="Figure",
    ),
    _Rule(
        # "(Rupees Four Lakh Fifty Thousand Only)" -- led by the currency word.
        # Covering the numeral and leaving this is not a redaction: the amount
        # is still legible, just spelled out.
        name="words_after_currency",
        pattern=re.compile(
            "(?P<amount>\\b(?:indian\\s+|us\\s+)?(?:{cur})\\b(?:{join}{any}\\b){{1,20}})".format(
                cur=_W_CURRENCY, join=_W_JOIN, any=_W_ANY
            ),
            re.IGNORECASE,
        ),
        confidence=CONFIDENCE_HIGH,
        label="Amount in words",
        validate=_has_written_number,
    ),
    _Rule(
        # The same thing without the currency word in front of it, closed off
        # by "only" -- "Twenty Five Lakhs Only". The "only" is what keeps this
        # from firing on ordinary prose containing a number word.
        name="words_before_only",
        pattern=re.compile(
            "(?P<amount>\\b(?:{num})\\b(?:{join}{any}\\b){{0,20}}{join}only\\b)".format(
                num=_W_NUMBER, join=_W_JOIN, any=_W_ANY
            ),
            re.IGNORECASE,
        ),
        confidence=CONFIDENCE_HIGH,
        label="Amount in words",
        validate=_has_written_number,
    ),
    # There is deliberately NO rule here for a bare ungrouped integer sitting
    # loose in a line. There was one, and it was a mistake: five-to-eight
    # digits with no currency marker, no grouping and no keyword describes a
    # PIN code ("Nagpur, Maharashtra 440010"), half a phone number ("+91 98765
    # 43210" splits into two), and a landline ("0712-2345678") just as well as
    # it describes money. The label guard in front of it caught "Phone: 98765"
    # and nothing else, because an address or a footer states those numbers
    # with no label at all.
    #
    # The case it was added for -- an ungrouped "450000" in a rate column -- is
    # the column pass's job, and the column pass knows it is money because of
    # the heading above it rather than because of how many digits it has.
    _Rule(
        # A line that is nothing but a number: a value cell whose heading sits
        # in a row this line-by-line pass cannot see. Medium, because the same
        # shape is how a quantity column looks.
        name="standalone_figure",
        pattern=re.compile(
            "^[\\s:=|\\-]*(?P<amount>(?:{cur}\\s*)?(?:{num}))"
            "(?:\\s*/\\s*-)?[\\s|]*$".format(cur=_CURRENCY, num=_NUMBER),
            re.IGNORECASE,
        ),
        confidence=CONFIDENCE_MEDIUM,
        label="Figure",
        validate=_substantial_figure,
    ),
)


def _label_for(rule: _Rule, match) -> str:
    """What the review panel calls this suggestion."""
    if rule.label:
        return rule.label
    keyword = (match.groupdict().get("keyword") or "").strip()
    if not keyword:
        return "Amount"
    return keyword[:1].upper() + keyword[1:].lower()


# ---------------------------------------------------------------------------
# Lines of text, with a box per word
#
# Everything downstream works off this shape and nothing else, which is what
# makes the OCR pass a matter of producing it from a different source.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    """One line of a page: its text, and where each word of it sits.

    `spans` are (start, end, rect) against `text`, in the coordinate space the
    words were extracted in -- for a PDF text layer that is the *unrotated*
    page, which is what `_area_for` corrects.
    """

    text: str
    spans: Sequence[Tuple[int, int, Any]]
    # "text" or "ocr". Carried through to each detection, because a figure
    # Tesseract read off a scan deserves a closer look than one lifted from a
    # content stream, and the reviewer can only apply that judgement if the
    # suggestion says which it was.
    source: str = ENGINE_TEXT


def _lines_from_words(words, pymupdf, source: str) -> List[_Line]:
    """Group PyMuPDF's word tuples into lines.

    `get_text("words")` comes back in reading order and tagged with the block
    and line each word belongs to, so rebuilding a line is a matter of joining
    its words with the single space that separated them on the page.

    Shared by both sources on purpose: OCR hands back the same eight-field
    tuples the text layer does, so a figure is recognised the same way whether
    it was read off the content stream or off the pixels.
    """
    grouped: Dict[Tuple[int, int], List[Tuple[Any, str]]] = {}
    for x0, y0, x1, y1, word, block_no, line_no, _ in words:
        if not word.strip():
            continue
        grouped.setdefault((block_no, line_no), []).append(
            (pymupdf.Rect(x0, y0, x1, y1), word)
        )

    lines: List[_Line] = []
    for line_words in grouped.values():
        parts: List[str] = []
        spans: List[Tuple[int, int, Any]] = []
        cursor = 0
        for rect, word in line_words:
            if parts:
                cursor += 1  # the joining space
            spans.append((cursor, cursor + len(word), rect))
            cursor += len(word)
            parts.append(word)
        lines.append(_Line(text=" ".join(parts), spans=spans, source=source))
    return lines


def _lines_from_text_layer(page, pymupdf) -> List[_Line]:
    """The page's own text: what a born-digital PDF carries."""
    return _lines_from_words(page.get_text("words"), pymupdf, ENGINE_TEXT)


_WORDY = re.compile(r"[A-Za-z]{3,}")


def _read_quality(words) -> float:
    """How much of a read looks like language rather than speckle.

    OCR on a badly scaled image does not fail loudly. It returns a confident
    page of one- and two-character fragments -- 'f', 'j', '|', 'SS ean' -- and
    nothing downstream can tell that apart from a page that genuinely has
    little text on it. The share of tokens holding a run of three or more
    letters separates the two cheaply, and is what lets the second strategy be
    tried only when the first has clearly gone wrong.
    """
    if not words:
        return 0.0
    text = " ".join(word[4] for word in words)
    tokens = text.split()
    if not tokens:
        return 0.0
    return len(_WORDY.findall(text)) / len(tokens)


def _ocr_ready_pixmap(pixmap, pymupdf):
    """An image in the shape Tesseract wants: RGB, opaque, sensibly sized.

    Scaling down a very large scan costs accuracy far less than scaling up
    costs it, so the cap is generous and the floor is small.
    """
    if pixmap.colorspace is None or pixmap.colorspace.n != 3:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    longest = max(pixmap.width, pixmap.height)
    if longest <= 0:
        return pixmap
    if longest > OCR_MAX_PIXELS:
        scale = OCR_MAX_PIXELS / longest
    elif longest < OCR_MIN_PIXELS:
        # Capped, so a 20-pixel sliver does not become a 1000-pixel blur.
        scale = min(OCR_MIN_PIXELS / longest, 4.0)
    else:
        return pixmap
    try:
        return _scaled(pixmap, scale, pymupdf)
    except Exception:
        return pixmap


def _scaled(pixmap, scale: float, pymupdf):
    """A resized copy, via a one-page PDF because Pixmap has no resize."""
    width = max(1, int(pixmap.width * scale))
    height = max(1, int(pixmap.height * scale))
    scratch = pymupdf.open()
    page = scratch.new_page(width=width, height=height)
    page.insert_image(page.rect, pixmap=pixmap)
    out = page.get_pixmap(alpha=False)
    scratch.close()
    return out


def _ocr_pixmap(pixmap, pymupdf, tessdata):
    """Recognise a bitmap at 1:1, returning words in its own pixel space.

    The scratch page is sized in *points* to the image's pixel count, and
    rendered at 72 dpi, so one point is one pixel and nothing is resampled on
    the way in. That is the whole trick: Tesseract sees the photograph exactly
    as it was captured.
    """
    scratch = pymupdf.open()
    try:
        page = scratch.new_page(width=pixmap.width, height=pixmap.height)
        page.insert_image(page.rect, pixmap=pixmap)
        textpage = page.get_textpage_ocr(
            flags=0,
            language=OCR_LANGUAGE,
            dpi=OCR_NATIVE_DPI,
            full=True,
            tessdata=tessdata,
        )
        # Read while `page` is still the object the textpage was built from:
        # PyMuPDF holds its parent weakly, and a fresh handle raises.
        return page.get_text("words", textpage=textpage)
    finally:
        scratch.close()


def _axis_aligned(transform) -> bool:
    """Is this image placed square on the page, or rotated/sheared?

    Only the square case can have its word boxes mapped back by scaling a
    bounding box. Anything else is handed to the page renderer, which applies
    the transform itself.
    """
    if not transform:
        return True
    b, c = transform[1], transform[2]
    return abs(b) < 1e-6 and abs(c) < 1e-6


def _ocr_words_from_images(doc, page, pymupdf, tessdata):
    """Words from every embedded image, in the page's own coordinates.

    This is the strategy that makes a photograph inside a PDF readable no
    matter how small it sits on the page, because the image never passes
    through a page render.
    """
    words = []
    try:
        placements = page.get_image_info(xrefs=True)
    except Exception:
        return words

    for info in placements:
        xref = info.get("xref")
        box = info.get("bbox")
        if not xref or not box:
            continue
        rect = pymupdf.Rect(box)
        if rect.is_empty or not _axis_aligned(info.get("transform")):
            continue
        # Skip logos, rules and signature strips: they hold no document text
        # and each one costs a full OCR pass. Judged on the image's own
        # pixels, so a document photographed at full resolution still counts
        # when it is placed small.
        if not _could_hold_text(info.get("width"), info.get("height")):
            continue
        try:
            raw = doc.extract_image(xref)
            pixmap = _ocr_ready_pixmap(pymupdf.Pixmap(raw["image"]), pymupdf)
            found = _ocr_pixmap(pixmap, pymupdf, tessdata)
        except Exception:
            continue
        if not found or not pixmap.width or not pixmap.height:
            continue
        # Image pixels -> page points. The placement box is the whole of the
        # mapping, which is why only axis-aligned images come down this path.
        scale_x = rect.width / pixmap.width
        scale_y = rect.height / pixmap.height
        for x0, y0, x1, y1, text, block, line, index in found:
            words.append(
                (
                    rect.x0 + x0 * scale_x,
                    rect.y0 + y0 * scale_y,
                    rect.x0 + x1 * scale_x,
                    rect.y0 + y1 * scale_y,
                    text,
                    block,
                    line,
                    index,
                )
            )
    return words


def _page_render_dpi(page) -> int:
    """What to rasterise a whole page at, when it has to be rasterised.

    Matched to the biggest image on it so the pixels it already has are
    reproduced rather than interpolated, and clamped so a postage stamp does
    not ask for a render nothing can hold.
    """
    best = None
    for info in page.get_image_info():
        box = info.get("bbox")
        if not box:
            continue
        width_pt = box[2] - box[0]
        if width_pt <= 1:
            continue
        dpi = info["width"] / (width_pt / 72.0)
        area = width_pt * (box[3] - box[1])
        if best is None or area > best[1]:
            best = (dpi, area)
    if best is None:
        return OCR_PAGE_RENDER_DPI
    return int(max(72, min(600, best[0])))


def _ocr_words_from_render(page, pymupdf, tessdata):
    """Words from a rasterised page: the fallback, and the vector-scan case."""
    try:
        textpage = page.get_textpage_ocr(
            flags=0,
            language=OCR_LANGUAGE,
            dpi=_page_render_dpi(page),
            full=True,
            tessdata=tessdata,
        )
        return page.get_text("words", textpage=textpage)
    except Exception:
        return []


def _lines_from_ocr(doc, page, pymupdf) -> Optional[List[_Line]]:
    """The page as Tesseract reads it: what a scan or an image carries.

    Two strategies, better one wins. The embedded image is tried first because
    it is right far more often; the page render is tried when that found
    nothing, could not be used (a rotated placement, a vector-drawn scan), or
    came back looking like speckle.

    Everything is recognised **upright**. A /Rotate 90 page renders with its
    text running down the side, and Tesseract returns noise for it -- measured,
    not assumed: the same page comes back as 63 words of gibberish rotated and
    22 correct words upright. The rotation is set to 0 for the duration and put
    straight back, which costs nothing because:

      * the document is opened read-only and never saved, so the file on disk
        cannot be affected by it;
      * `get_image_info` then reports placements in the unrotated space too, so
        the mapping above stays consistent; and
      * the boxes arrive in the unrotated space the text layer already uses, so
        `_area_for` handles both with one conversion.

    The rotation goes back before anything reads `page.rect` or
    `page.rotation_matrix`, which is what that conversion depends on.

    Returns None when OCR could not run at all, and a list -- possibly empty --
    when it did. The distinction is the whole difference between "this page was
    never looked at" and "this page was read and has no text on it", which is a
    photograph of a bridge, a blank sheet, or a page of drawings. Collapsing
    the two would report a perfectly readable image as unreadable.
    """
    tessdata = tessdata_dir()
    if not tessdata:
        return None

    rotation = page.rotation
    try:
        if rotation:
            page.set_rotation(0)
        words = _ocr_words_from_images(doc, page, pymupdf, tessdata)
        if _read_quality(words) < OCR_QUALITY_FLOOR:
            rendered = _ocr_words_from_render(page, pymupdf, tessdata)
            if _read_quality(rendered) > _read_quality(words):
                words = rendered
    except Exception:
        return None
    finally:
        if rotation:
            page.set_rotation(rotation)
    return _lines_from_words(words, pymupdf, ENGINE_OCR)


def _could_hold_text(width, height) -> bool:
    """Is this image big enough, in its own pixels, to be a document?"""
    if not width or not height:
        return False
    return (
        max(width, height) >= OCR_MIN_IMAGE_LONG_SIDE
        and width * height >= OCR_MIN_IMAGE_PIXELS
    )


def _has_document_image(page) -> bool:
    """Does this page carry a picture that could be a document?"""
    try:
        images = page.get_image_info()
    except Exception:
        return False
    return any(_could_hold_text(im.get("width"), im.get("height")) for im in images)


def _looks_scanned(page, lines: List[_Line]) -> bool:
    """Is there anything on this page the text layer cannot tell us about?

    Two shapes count, and the second one is the one that was getting missed.

    A page with no text at all is the plain case: a scan, and nothing to read
    without OCR.

    A page that *does* have text but also carries a substantial image is the
    other, and it is common -- a photograph of a signed page dropped into an
    otherwise ordinary PDF, under a typed heading. The text layer is real and
    tells you nothing about the picture, which is where the amounts are. This
    used to demand that the text be almost absent (fewer than MIN_TEXT_WORDS)
    *and* the image nearly fill the page, so a photograph inset with margins
    under a heading was passed over in silence.

    Now any image big enough to be a document earns the page an OCR pass,
    whatever its text layer says, and the two readings are merged. A page whose
    picture turns out to be a photograph of a bridge costs a second and yields
    nothing; a page skipped because of a heading costs an amount left in a
    document that was meant to be redacted.
    """
    if not lines:
        return True
    if _has_document_image(page):
        return True
    # No real image, and some text: a sparse page, not a scan.
    return sum(len(line.spans) for line in lines) < MIN_TEXT_WORDS


def _rect_for_span(line: _Line, start: int, end: int):
    """The box around every word a match touches.

    Whole words, even when the match covers only part of one: an amount
    written "Rs.45,000/-" arrives as a single word, and a box that stopped
    mid-word would leave half the figure showing. Rounding outwards is the
    safe direction for a redaction.
    """
    box = None
    for word_start, word_end, rect in line.spans:
        if word_end <= start or word_start >= end:
            continue
        box = rect if box is None else box | rect
    return box


# ---------------------------------------------------------------------------
# From a text rectangle to a redaction Area
# ---------------------------------------------------------------------------


def _area_for(page, rect, page_index: int, pymupdf) -> Optional[Area]:
    """Normalise one text rectangle into the manual redaction format.

    Two conversions, in this order:

    1. **Rotation.** Extracted text is in the unrotated page; `page.rect` is
       the rotated one the page image is rendered from, and the one
       `redaction._redact_pdf` resolves fractions against. `rotation_matrix`
       is the map between them. It is the identity on an unrotated page, so
       this costs nothing in the common case and is the whole job in the
       /Rotate 90 one.
    2. **Normalisation.** Against `page.rect` including its origin, because a
       cropped page's rect does not start at (0, 0) and the redaction pass
       adds that origin back on.

    The target space is deliberately the one the *page image* is rendered in,
    because that is what the normalised format means: `render_page_image`
    renders from `page.rect`, the browser measures its box, and a hand-drawn
    rectangle is a fraction of that. A suggestion therefore lands on exactly
    the pixels a person would have dragged over, which is the alignment this
    whole module is judged on.

    Worth knowing, because it is easy to mistake for a bug here: PyMuPDF's
    *drawing* side (`draw_rect`, `add_redact_annot`) takes rectangles in the
    unrotated page and applies /Rotate itself, so on a rotated page it wants
    different numbers than `get_pixmap` produced. `redaction._redact_pdf`
    turns every box back with `derotation_matrix` before redacting, the
    inverse of the step below, so automatic and manual boxes stay the same
    thing and both land where they were drawn.
    """
    box = (rect * page.rotation_matrix).normalize()
    box = pymupdf.Rect(box.x0 - PAD_X, box.y0 - PAD_Y, box.x1 + PAD_X, box.y1 + PAD_Y)
    bounds = page.rect
    if not bounds.width or not bounds.height:
        return None
    box = box & bounds
    if box.is_empty:
        return None

    area = Area(
        page=page_index,
        x=(box.x0 - bounds.x0) / bounds.width,
        y=(box.y0 - bounds.y0) / bounds.height,
        width=box.width / bounds.width,
        height=box.height / bounds.height,
    )
    # Anything under this is dropped by the redaction pass anyway, so
    # suggesting it would offer a box that quietly does nothing.
    if area.width < redaction.MIN_AREA_SIZE or area.height < redaction.MIN_AREA_SIZE:
        return None
    return area


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def _scan_line(line: _Line, page, page_index: int, pymupdf) -> List[Dict[str, Any]]:
    """Every figure one line of text gives up."""
    found: List[Dict[str, Any]] = []
    for rule in _RULES:
        for match in rule.pattern.finditer(line.text):
            start, end = match.span("amount")
            if end <= start:
                continue
            if rule.validate and not rule.validate(match):
                continue
            rect = _rect_for_span(line, start, end)
            if rect is None:
                continue
            area = _area_for(page, rect, page_index, pymupdf)
            if area is None:
                continue
            found.append(
                {
                    "page": area.page,
                    "x": area.x,
                    "y": area.y,
                    "width": area.width,
                    "height": area.height,
                    "category": CATEGORY_FINANCIAL,
                    "label": _label_for(rule, match),
                    # The whole match, not just the figure, so the reviewer
                    # reads "Contract value: Rs. 4,50,000" and can tell at a
                    # glance whether the box belongs on the page.
                    "text": match.group(0).strip()[:120],
                    "confidence": rule.confidence,
                    "rule": rule.name,
                    "source": line.source,
                }
            )
    return found


# ---------------------------------------------------------------------------
# Columns
#
# The rules above read one line at a time, which is blind to the single most
# common way a tender states a price:
#
#     Item                    Qty        Rate
#     Deck slab concrete      250     8,912.50
#     Approach road works     180       450000
#
# "450000" shares its line with "Approach road works" and "180", and there is
# no financial keyword anywhere on it -- the keyword is the column *heading*,
# a row further up. A line-by-line pass cannot see that, so a schedule of
# rates gives up its grouped figures and quietly keeps its ungrouped ones.
#
# This pass reads down instead of across: find the headings that name a money
# column, then claim the numbers sitting underneath them.
# ---------------------------------------------------------------------------

# Headings that make a column a money column. Narrower than the keyword list
# used per line: a column headed "Qty" or "Item" is not money, and a word like
# "deposit" heads a paragraph far more often than a column.
_COLUMN_HEADINGS = frozenset((
    "rate", "rates", "amount", "amounts", "cost", "costs", "price", "prices",
    "value", "total", "charges", "fees", "emd", "gst", "tax", "figures",
    "quoted", "quotation", "estimate", "estimated",
))

# How far a number may sit from its heading's column and still belong to it.
# Page units. Generous, because a column of right-aligned figures under a
# left-aligned heading barely overlaps it at all.
_COLUMN_SLACK = 36.0

# One number, and only one. The space allowance inside `_NUMBER` is a single
# optional space beside a separator (an OCR artefact), deliberately not a run
# of them: a pattern that let spaces accumulate would read the gap between two
# table columns as part of the figure and box the quantity along with the
# rate.
_NUMBER_TOKEN_RE = re.compile(
    r"(?<![\w.])(?:{cur}\s*)?(?:{num})".format(cur=_CURRENCY, num=_NUMBER),
    re.IGNORECASE,
)


# A heading row labels columns; it does not state values and it is not prose.
# Without these two tests the word "Value" in the sentence "Contract Value:
# Rs. 8,50,000" was read as a column heading, and every number lower down the
# page that happened to share its x -- a phone number, in the case that found
# this -- was claimed as a figure in that column, at high confidence.
_MAX_HEADING_WORDS = 8


def _is_heading_row(line: _Line) -> bool:
    """Could this line be the header row of a table?

    Two cheap tests that between them exclude prose and data rows: a header
    row carries no figures of its own, and it is short. "Item Qty Rate" passes;
    "The total contract value shall be paid on completion" does not, and
    neither does "Contract Value: Rs. 8,50,000".
    """
    if any(character.isdigit() for character in line.text):
        return False
    return len(line.spans) <= _MAX_HEADING_WORDS


def _column_headings(lines: Sequence[_Line]) -> List[Tuple[float, float, float, str]]:
    """(x0, x1, y1, label) for every word on the page that heads a money column."""
    headings: List[Tuple[float, float, float, str]] = []
    for line in lines:
        if not _is_heading_row(line):
            continue
        for start, end, rect in line.spans:
            word = line.text[start:end].strip(" :|-.()").lower()
            if word not in _COLUMN_HEADINGS:
                continue
            # "Total:" introduces a value on the same line; "Total" over a
            # column of figures heads it. The colon is what tells them apart.
            if line.text[end:end + 1] == ":":
                continue
            headings.append((rect.x0, rect.x1, rect.y1, word))
    return headings


def _under_heading(rect, headings) -> Optional[str]:
    """The money column this rectangle sits in, if any.

    Two tests, both needed. Vertically it has to be *below* the heading, or a
    figure in the header band itself would be claimed by a heading beside it.
    Horizontally it has to share the heading's column, with enough slack that
    right-aligned figures under a left-aligned heading still count.
    """
    for x0, x1, y1, label in headings:
        if rect.y0 < y1:
            continue
        if rect.x1 < x0 - _COLUMN_SLACK or rect.x0 > x1 + _COLUMN_SLACK:
            continue
        return label
    return None


def _scan_columns(
    lines: Sequence[_Line], page, page_index: int, pymupdf
) -> List[Dict[str, Any]]:
    """Numbers sitting under a heading that names them as money.

    Confidence is high: an unlabelled number is a guess, but a number in a
    column headed "Rate" is as well identified as one on a line reading
    "Rate: ...". The difference is only which direction the label sits in.
    """
    headings = _column_headings(lines)
    if not headings:
        return []

    found: List[Dict[str, Any]] = []
    for line in lines:
        for match in _NUMBER_TOKEN_RE.finditer(line.text):
            token = match.group(0).strip()
            # Same bar as a lone number on its own line: enough digits that it
            # is a figure rather than a serial number or a quantity.
            digits = re.sub(r"\D", "", token)
            if len(digits) < 4:
                continue
            if len(digits) == 4 and not ("," in token or "." in token):
                continue
            # A reference number in a money column is still a reference number.
            if _IDENTIFIER_BEFORE.search(line.text[: match.start()]):
                continue
            rect = _rect_for_span(line, match.start(), match.end())
            if rect is None:
                continue
            label = _under_heading(rect, headings)
            if not label:
                continue
            area = _area_for(page, rect, page_index, pymupdf)
            if area is None:
                continue
            found.append(
                {
                    "page": area.page,
                    "x": area.x,
                    "y": area.y,
                    "width": area.width,
                    "height": area.height,
                    "category": CATEGORY_FINANCIAL,
                    "label": label[:1].upper() + label[1:] + " column",
                    "text": token,
                    "confidence": CONFIDENCE_HIGH,
                    "rule": "column_figure",
                    "source": line.source,
                }
            )
    return found


def _overlap_fraction(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """How much of the smaller box lies inside the larger one."""
    left = max(a["x"], b["x"])
    top = max(a["y"], b["y"])
    right = min(a["x"] + a["width"], b["x"] + b["width"])
    bottom = min(a["y"] + a["height"], b["y"] + b["height"])
    if right <= left or bottom <= top:
        return 0.0
    shared = (right - left) * (bottom - top)
    smaller = min(a["width"] * a["height"], b["width"] * b["height"])
    return shared / smaller if smaller else 0.0


_CONFIDENCE_ORDER = {CONFIDENCE_HIGH: 1, CONFIDENCE_MEDIUM: 0}


def _merge(found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One box per figure.

    Several rules firing on the same amount is normal and wanted -- it is how
    a figure gets caught whether or not it is labelled -- but the reviewer
    should see it once. The survivor of an overlapping pair is the more
    confident one, and then the larger, so the kept box still covers
    everything the dropped one did.
    """
    ranked = sorted(
        found,
        key=lambda d: (
            -_CONFIDENCE_ORDER.get(d["confidence"], 0),
            -(d["width"] * d["height"]),
        ),
    )
    kept: List[Dict[str, Any]] = []
    for candidate in ranked:
        if any(
            other["page"] == candidate["page"]
            and _overlap_fraction(other, candidate) >= MERGE_OVERLAP
            for other in kept
        ):
            continue
        kept.append(candidate)
    # Reading order, so the review list runs down the page.
    kept.sort(key=lambda d: (d["page"], round(d["y"], 4), round(d["x"], 4)))
    for index, detection in enumerate(kept):
        detection["id"] = "auto-{0}-{1}".format(detection["page"], index)
    return kept


@dataclass
class _Tally:
    """Which pages were read how, so the answer can say so.

    Kept as a running record rather than inferred at the end, because "this
    page was OCR'd", "this page had a text layer" and "this page could not be
    read at all" are three different things to a reviewer, and only the last
    means the page still needs looking at by hand.
    """

    text_pages: List[int]
    ocr_pages: List[int]
    unread_pages: List[int]
    skipped_pages: List[int]

    @classmethod
    def empty(cls) -> "_Tally":
        return cls([], [], [], [])

    def engine(self) -> str:
        if self.text_pages and self.ocr_pages:
            return ENGINE_MIXED
        if self.ocr_pages:
            return ENGINE_OCR
        if self.text_pages:
            return ENGINE_TEXT
        return ENGINE_NONE


def _scan_pages(doc, pages, pymupdf) -> Tuple[List[Dict[str, Any]], _Tally]:
    """Read every page the best way it can be read, and scan what comes back.

    The routing is per page, which is the whole of "mixed PDFs": a document
    can be twenty text pages and three scanned inserts, and each is handled on
    its own terms. A page that looks scanned but still has a little real text
    gets *both* -- the text layer's words and the OCR's -- because the header
    a scan carries is as worth reading as the body, and `_merge` collapses
    anything the two agree on.
    """
    found: List[Dict[str, Any]] = []
    tally = _Tally.empty()
    ocr_budget = OCR_PAGE_BUDGET

    for page_index, page in pages:
        lines = _lines_from_text_layer(page, pymupdf)
        had_text = bool(lines)

        if _looks_scanned(page, lines):
            if not ocr_available():
                # Nothing more to try. Recorded as unread rather than passed
                # over in silence: a scan that comes back empty otherwise
                # reads exactly like a page with nothing sensitive on it.
                if not had_text:
                    tally.unread_pages.append(page_index)
            elif ocr_budget <= 0:
                tally.skipped_pages.append(page_index)
            else:
                ocr_budget -= 1
                ocr_lines = _lines_from_ocr(doc, page, pymupdf)
                if ocr_lines is None:
                    # OCR could not run on this page at all.
                    if not had_text:
                        tally.unread_pages.append(page_index)
                else:
                    # It ran. An empty result means the page genuinely has no
                    # text on it -- a photograph, a drawing, a blank sheet --
                    # which is a page that was read, not one that was missed.
                    lines = lines + ocr_lines
                    tally.ocr_pages.append(page_index)

        if had_text:
            tally.text_pages.append(page_index)
        for line in lines:
            found.extend(_scan_line(line, page, page_index, pymupdf))
        # Read down the page as well as across it, for the figures that are
        # labelled by a column heading rather than by anything on their own
        # line. Overlapping hits collapse in _merge.
        found.extend(_scan_columns(lines, page, page_index, pymupdf))

    return found, tally


def _result(kind: str, found, tally: _Tally, page_count: int) -> Dict[str, Any]:
    detections = _merge(found)
    truncated = len(detections) > MAX_DETECTIONS
    return {
        "kind": kind,
        "engine": tally.engine(),
        "detections": detections[:MAX_DETECTIONS],
        "pages_scanned": page_count,
        "text_pages": tally.text_pages,
        "ocr_pages": tally.ocr_pages,
        # The name predates OCR and is kept: to everything downstream it has
        # always meant "pages whose contents were not read", which is still
        # exactly what it holds.
        "pages_without_text": tally.unread_pages,
        "pages_skipped": tally.skipped_pages,
        "truncated": truncated,
        "message": _message(tally, len(detections), truncated),
    }


def _scan_pdf(path: str) -> Dict[str, Any]:
    pymupdf = redaction._pymupdf()
    doc = redaction._open_pdf(path)
    try:
        # Held as a list of (index, page): the page object has to stay alive
        # while its textpage is read, and `doc[i]` hands back a new one.
        found, tally = _scan_pages(doc, list(enumerate(doc)), pymupdf)
        page_count = doc.page_count
    finally:
        doc.close()
    return _result("pdf", found, tally, page_count)


def _page_list(pages: List[int]) -> str:
    """"3", or "3, 4 and 7", or "3, 4, 5... " once it stops being readable."""
    shown = [str(index + 1) for index in pages[:6]]
    if len(pages) > 6:
        return ", ".join(shown) + " and {0} more".format(len(pages) - 6)
    if len(shown) == 1:
        return shown[0]
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def _message(tally: _Tally, total: int, truncated: bool) -> Optional[str]:
    """The one sentence the review panel shows above the list.

    It exists mainly to keep "found nothing" and "could not look" apart: an
    unread page reads as a clean one otherwise, which is the worst way for
    this feature to fail. Everything else it says is secondary to that.
    """
    if truncated:
        return (
            "Found {0} possible figures and is showing the first {1}. "
            "Review these, then scan again.".format(total, MAX_DETECTIONS)
        )

    notes: List[str] = []
    if tally.unread_pages:
        if ocr_available():
            notes.append(
                "Page{0} {1} could not be read, even by OCR.".format(
                    "s" if len(tally.unread_pages) > 1 else "",
                    _page_list(tally.unread_pages),
                )
            )
        else:
            notes.append(
                "Page{0} {1} {2} scanned images with no text layer, and {3}".format(
                    "s" if len(tally.unread_pages) > 1 else "",
                    _page_list(tally.unread_pages),
                    "are" if len(tally.unread_pages) > 1 else "is",
                    OCR_MISSING_HINT[0].lower() + OCR_MISSING_HINT[1:],
                )
            )
    if tally.skipped_pages:
        notes.append(
            "Page{0} {1} {2} not OCR'd: this document has more scanned pages "
            "than one pass reads ({3}). Mark those by hand.".format(
                "s" if len(tally.skipped_pages) > 1 else "",
                _page_list(tally.skipped_pages),
                "were" if len(tally.skipped_pages) > 1 else "was",
                OCR_PAGE_BUDGET,
            )
        )
    if tally.ocr_pages and not notes:
        notes.append(
            "Page{0} {1} {2} read by OCR, so check these suggestions against "
            "the page.".format(
                "s" if len(tally.ocr_pages) > 1 else "",
                _page_list(tally.ocr_pages),
                "were" if len(tally.ocr_pages) > 1 else "was",
            )
        )
    if not total:
        notes.append("No financial figures were found.")
    return " ".join(notes) if notes else None


def _scan_image(path: str) -> Dict[str, Any]:
    """A JPG or PNG, read by OCR.

    The image is laid onto a one-page document whose size in points equals its
    size in *pixels*. That is not cosmetic: it makes a fraction of the page
    identical to a fraction of the image, which is what `redaction._redact_image`
    resolves its areas against. Suggestion and hand-drawn box therefore mean
    the same thing on an image, exactly as they do on a PDF.
    """
    pymupdf = redaction._pymupdf()
    pixmap = redaction._open_image_pixmap(path, want_alpha=False)
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=pixmap.width, height=pixmap.height)
        page.insert_image(page.rect, pixmap=pixmap)
        found, tally = _scan_pages(doc, [(0, page)], pymupdf)
    finally:
        doc.close()
    # An image has no text layer by definition, so a page counted as "text"
    # here would only ever be an artefact of how it was wrapped.
    tally.text_pages = []
    return _result("image", found, tally, 1)


def analyse(stored_file_name: Optional[str]) -> Dict[str, Any]:
    """Suggested redaction areas for a stored file, whatever format it is in.

    Read-only from end to end: the file is opened, scanned in memory and
    closed. Nothing is written, nothing is cached on disk, and no redaction
    happens here -- the caller gets rectangles, and only the areas a person
    accepts ever reach `redaction.redact`.
    """
    path = redaction.existing_path(stored_file_name)
    payload = (
        _scan_pdf(path) if redaction.is_pdf(stored_file_name) else _scan_image(path)
    )
    payload["ocr_available"] = ocr_available()
    return payload
