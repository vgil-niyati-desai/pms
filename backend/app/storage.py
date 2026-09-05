"""Uploaded-file handling, shared by every router that stores a file.

Nothing in here touches a database. The document, employee and tender routes
all use these helpers so that file validation, path safety, and on-disk
cleanup behave identically wherever an upload comes in.
"""

import hashlib
import os
import uuid
from typing import Optional

from fastapi import HTTPException

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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


def remove_stored_file(stored_file_name: Optional[str]) -> None:
    """Best-effort delete of a stored file; a missing file is not an error."""
    if not stored_file_name:
        return
    try:
        os.remove(upload_path(stored_file_name))
    except (FileNotFoundError, OSError, HTTPException):
        # The database record is the source of truth. If the file is already
        # gone (or unreadable), leaving it behind must not fail the request.
        pass


def write_upload(contents: bytes, original_filename: str) -> str:
    """Write uploaded bytes under a fresh UUID name and return that name."""
    ext = original_filename.rsplit(".", 1)[1].lower()
    stored_file_name = f"{uuid.uuid4().hex}.{ext}"
    with open(upload_path(stored_file_name), "wb") as f:
        f.write(contents)
    return stored_file_name
