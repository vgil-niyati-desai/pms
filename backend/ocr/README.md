# OCR language data

`tessdata/eng.traineddata` is the language model the automatic redaction scan
uses to read scanned pages, photographs and JPG/PNG uploads. It ships with the
project on purpose.

## Why it is here and not installed

There is **no Tesseract process**. PyMuPDF's wheels carry Tesseract and
Leptonica linked into MuPDF's own shared library, so OCR runs inside the API
process. Nothing shells out, `tesseract` does not need to be on PATH, and
`get_textpage_ocr(tessdata=...)` takes an explicit path without reading or
writing `TESSDATA_PREFIX`.

So the only thing OCR needs from outside the process is this folder. Keeping it
in the project means:

* the PMS can be deployed to a shared server with no root access;
* nothing is installed system-wide, and no global environment variable is set;
* other projects on the same machine are neither affected nor depended on —
  this one uses its own copy even when the machine has a Tesseract of its own.

`app/detection.py` looks here first. See `SETUP.md`, section 3f.

## Replacing or adding a language

The file is the standard `eng` model from the Tesseract project
(<https://github.com/tesseract-ocr/tessdata>, ~4 MB). Any build of
`eng.traineddata` works — `tessdata_fast` is smaller and quicker,
`tessdata_best` is slower and slightly more accurate.

To use a different set without editing the repository, set `PMS_TESSDATA_DIR`
in `backend/.env` to a folder holding the `.traineddata` files. To recognise a
language other than English, put its file here and change `OCR_LANGUAGE` in
`app/detection.py`.

`osd.traineddata` is **not** needed: it is for orientation detection, which the
scan does not use — page rotation is handled before Tesseract sees anything.
