"""Uploaded-file handling, shared by every router that stores a file.

Nothing in here touches a database. The document, employee and tender routes
all use these helpers so that file validation, path safety, and on-disk
cleanup behave identically wherever an upload comes in.
"""

import hashlib
import logging
import os
import re
import unicodedata
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import HTTPException

# Same call mongodb.py makes, so a script that imports only this module still
# sees backend/.env. Real environment variables win over the file.
load_dotenv()

# uvicorn's own error logger, as main.py uses, so a warning from here lands
# in the same console in the same format as everything else the server says.
logger = logging.getLogger("uvicorn.error")

# Where uploaded files live. Unset (local development), it is app/uploads next
# to this file. A deployment sets PMS_UPLOAD_DIR to an absolute path on a
# persistent volume, so the code checkout and the documents can be backed up,
# replaced and permissioned separately. Records keep only the bare stored
# file name, so the same records work from either location.
_DEFAULT_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
_configured_upload_dir = os.getenv("PMS_UPLOAD_DIR", "").strip()
if _configured_upload_dir and not os.path.isabs(_configured_upload_dir):
    raise RuntimeError("PMS_UPLOAD_DIR must be an absolute path.")
UPLOAD_DIR = os.path.abspath(_configured_upload_dir) if _configured_upload_dir else _DEFAULT_UPLOAD_DIR
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}

# The largest file an upload may be. Generous for a scanned tender PDF -- the
# largest stored so far is under 3 MB -- while refusing something that is
# plainly not a document. Must match MAX_UPLOAD_BYTES in the frontend's
# components/FileUpload.jsx, which checks it before sending.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_LABEL = "20 MB"


MAX_DISPLAY_NAME_LENGTH = 150
# An extension longer than this is not one, so it gets no protection from the
# length cap and is cut with the rest of the name.
_MAX_KEPT_EXTENSION = 16

# Control, format (bidi overrides, zero-width joiners), surrogate, and line or
# paragraph separator characters. None belongs in a file name, and the bidi
# ones let `fdp.exe` display as `exe.pdf`.
_UNSAFE_CATEGORIES = {"Cc", "Cf", "Cs", "Zl", "Zp"}


def safe_display_name(name: Optional[str], default: str = "document") -> str:
    """A client-supplied file name, made safe to store, show and send in a header.

    This is the name people see (`file_name`); the file on disk is always a
    UUID. It keeps only the last path segment, drops control and Unicode
    formatting characters, normalises to NFC so the same name always compares
    equal, trims whitespace and trailing dots, and caps the length while
    keeping the extension. Legitimate Unicode is left alone. A name with
    nothing usable left becomes `default`.
    """
    if not name:
        return default
    name = unicodedata.normalize("NFC", name)
    segments = [segment for segment in re.split(r"[\\/]", name) if segment.strip()]
    if not segments:
        return default
    name = "".join(
        char for char in segments[-1] if unicodedata.category(char) not in _UNSAFE_CATEGORIES
    )
    name = name.strip().rstrip(". ")

    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        stem, dot, extension = name.rpartition(".")
        if dot and stem and len(extension) <= _MAX_KEPT_EXTENSION:
            keep = MAX_DISPLAY_NAME_LENGTH - len(extension) - 1
            name = f"{stem[:keep].rstrip()}.{extension}"
        else:
            name = name[:MAX_DISPLAY_NAME_LENGTH].rstrip()
    return name or default


def allowed_file(filename: Optional[str]) -> bool:
    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def upload_path(stored_file_name: str) -> str:
    """Resolve a stored file name to a path inside UPLOAD_DIR.

    Stored names are UUIDs we generate ourselves, but edit/delete actually
    remove files from disk, so the path is re-checked here to make sure a
    bad value in the database can never point outside the uploads folder.
    """
    path = os.path.abspath(os.path.join(UPLOAD_DIR, os.path.basename(stored_file_name)))
    if os.path.commonpath([os.path.abspath(UPLOAD_DIR), path]) != os.path.abspath(UPLOAD_DIR):
        raise HTTPException(status_code=400, detail="Invalid stored file name")
    return path


def hash_file_on_disk(stored_file_name: str) -> Optional[str]:
    """SHA-256 of an already-stored file, or None if it can't be read."""
    try:
        with open(upload_path(stored_file_name), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except (FileNotFoundError, OSError, HTTPException):
        return None


def remove_stored_file(stored_file_name: Optional[str]) -> bool:
    """Delete a stored file. True when nothing of it is left behind.

    A missing file is not a failure. The database record is the source of
    truth, and a file that is already gone is the state the caller asked
    for, so that answers True with nothing said about it.

    Anything else is: no permission, a file another process is holding open
    (which Windows does to one MuPDF has mapped for a preview or a
    redaction), a stored name that will not resolve inside UPLOAD_DIR. Each
    leaves a real file on disk that no record points at any more and nothing
    in the app will ever look at again -- so it is logged, with the name
    needed to find it, instead of disappearing silently.

    It is reported rather than raised, because every caller is a cleanup
    step on a path that has already decided what it is doing: a delete whose
    record is gone still answers 204, and an upload rolling its file back
    still reports the error that made it roll back. The return value is for
    a caller that wants to say more; none has to.
    """
    if not stored_file_name:
        return True
    try:
        os.remove(upload_path(stored_file_name))
    except FileNotFoundError:
        return True
    except (OSError, HTTPException) as error:
        logger.warning(
            "Could not delete the stored file %s: %s. It is left in the uploads "
            "folder with no record pointing at it.",
            stored_file_name,
            error,
        )
        return False
    return True


def write_upload(contents: bytes, original_filename: str) -> str:
    """Write uploaded bytes under a fresh UUID name and return that name."""
    ext = original_filename.rsplit(".", 1)[1].lower()
    stored_file_name = f"{uuid.uuid4().hex}.{ext}"
    try:
        with open(upload_path(stored_file_name), "wb") as f:
            f.write(contents)
    except Exception:
        # A write that failed part-way (disk full, say) must not leave a
        # truncated file behind that no record will ever point at.
        remove_stored_file(stored_file_name)
        raise
    return stored_file_name
