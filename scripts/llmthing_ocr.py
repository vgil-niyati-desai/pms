"""Optional OCR helper: send scanned pages to LLMThing and cache the text.

Operator script, run locally (the server never needs it). Claude/Anthropic is
untouched; LLMThing is only used as a separate OCR service.

    python -I scripts/llmthing_ocr.py --cache DIR FILE [FILE ...]

* Token: read from the file named by the LLMTHING_TOKEN_FILE environment
  variable. It is never printed, logged or written anywhere.
* Plain PDF text is used when a page has a real text layer; only pages
  without one are sent for OCR.
* Results are cached in DIR/<sha256>.json, so a file already processed (same
  bytes) is never sent again. Delete the cache entry to force a re-run.
* Output: a transcription per page, in page order. Nothing is interpreted
  here -- metadata is derived from the text afterwards and reviewed.
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

import pymupdf

BASE_URL = os.environ.get("LLMTHING_BASE_URL", "https://llmthing.vanshraja.me/v1")
MODEL = os.environ.get("LLMTHING_MODEL", "gemma-4-26b-a4b")
MIN_TEXT_CHARS = 40  # a page with fewer characters is treated as a scan
MAX_SIDE = 2000
PROMPT = (
    "Transcribe all text visible in this document page exactly as written, in reading "
    "order. Keep line breaks, numbers, dates and identifiers character for character. "
    "Do not summarise, translate, correct or add anything. If a part is illegible, "
    "write [illegible] in its place. Output only the transcription."
)


def read_token() -> str:
    path = os.environ.get("LLMTHING_TOKEN_FILE")
    if not path:
        sys.exit("LLMTHING_TOKEN_FILE is not set.")
    with open(path, "r", encoding="utf-8") as handle:
        token = handle.read().strip()
    if not token:
        sys.exit("The token file is empty.")
    return token


def page_image(pixmap: "pymupdf.Pixmap") -> bytes:
    scale = min(1.0, MAX_SIDE / max(pixmap.width, pixmap.height))
    if scale < 1.0:
        pixmap = pymupdf.Pixmap(pixmap, 0) if pixmap.alpha else pixmap
    return pixmap.tobytes("png")


def render(page: "pymupdf.Page") -> bytes:
    rect = page.rect
    zoom = min(3.0, MAX_SIDE / max(rect.width, rect.height))
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return pixmap.tobytes("jpeg", jpg_quality=90)


def ocr_image(jpeg: bytes, token: str) -> dict:
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 4000,
        "think": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()},
                    },
                ],
            }
        ],
    }
    body = json.dumps(payload).encode()
    for attempt in range(3):
        request = urllib.request.Request(
            BASE_URL + "/chat/completions",
            data=body,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                # Cloudflare in front of the gateway rejects urllib's default agent (1010).
                "User-Agent": "pms-ocr/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.load(response)
            return {
                "status": response.status,
                "text": data["choices"][0]["message"]["content"] or "",
                "finish_reason": data["choices"][0].get("finish_reason"),
            }
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            if error.code == 503 and attempt < 2:  # model loading: honour Retry-After
                time.sleep(min(int(error.headers.get("Retry-After", "30")), 120))
                continue
            raise SystemExit("LLMThing returned HTTP %d: %s" % (error.code, detail))
        except urllib.error.URLError as error:
            raise SystemExit("Could not reach LLMThing: %s" % error.reason)
    raise SystemExit("LLMThing stayed unavailable (503) after 3 attempts.")


def process(path: str, cache_dir: str, token_loader) -> dict:
    with open(path, "rb") as handle:
        raw = handle.read()
    digest = hashlib.sha256(raw).hexdigest()
    cache_path = os.path.join(cache_dir, digest + ".json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as handle:
            cached = json.load(handle)
        cached["from_cache"] = True
        return cached

    suffix = path.rsplit(".", 1)[-1].lower()
    document = pymupdf.open(stream=raw, filetype="pdf" if suffix == "pdf" else suffix)
    pages, token = [], None
    for number, page in enumerate(document, start=1):
        layer = page.get_text().strip()
        if len(layer) >= MIN_TEXT_CHARS:
            pages.append({"page": number, "method": "text_extraction", "text": layer})
            continue
        token = token or token_loader()
        result = ocr_image(render(page), token)
        pages.append(
            {
                "page": number,
                "method": "ocr",
                "model": MODEL,
                "text": result["text"].strip(),
                "finish_reason": result["finish_reason"],
            }
        )
    record = {
        "sha256": digest,
        "source_name": os.path.basename(path),
        "page_count": len(pages),
        "pages": pages,
        "from_cache": False,
    }
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=1)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", required=True)
    parser.add_argument("--quiet", action="store_true", help="do not print the text")
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    for path in args.files:
        record = process(path, args.cache, read_token)
        methods = sorted({p["method"] for p in record["pages"]})
        print("== %s  sha256=%s  pages=%d  methods=%s  cached=%s" % (
            record["source_name"], record["sha256"][:12], record["page_count"], methods, record["from_cache"]))
        if not args.quiet:
            for page in record["pages"]:
                print("--- page %d (%s)" % (page["page"], page["method"]))
                print(page["text"])


if __name__ == "__main__":
    main()
