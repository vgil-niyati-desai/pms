"""Stable identities for employee records sourced from certification sheets."""

import re
import unicodedata


def normalize_employee_name(full_name: str) -> str:
    """Normalize a full name for identity and exact-match comparisons."""
    if not isinstance(full_name, str):
        raise ValueError("Full name must be text.")
    normalized = " ".join(unicodedata.normalize("NFKC", full_name).split()).casefold()
    if not normalized:
        raise ValueError("Full name is required for employee identity.")
    return normalized


def vgil_source_identity(employee_id: str) -> str:
    """Build the VGIL identity from its authoritative employee identifier."""
    if employee_id is None:
        raise ValueError("VGIL employee ID is required.")
    identifier = str(employee_id).strip()
    if not identifier:
        raise ValueError("VGIL employee ID is required.")
    if re.fullmatch(r"[0-9]+\.0+", identifier):
        identifier = identifier.split(".", 1)[0]
    return f"vgil:{identifier}"


def fintech_source_identity(full_name: str) -> str:
    """Build the agreed stable identity for a Fintech employee without an ID."""
    return f"fintech:{normalize_employee_name(full_name)}"


def unknown_source_identity(full_name: str) -> str:
    """Build a stable name identity when the source does not verify affiliation."""
    return f"unknown:{normalize_employee_name(full_name)}"
