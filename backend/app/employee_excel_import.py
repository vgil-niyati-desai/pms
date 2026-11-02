"""Safe, explicit employee imports from the certification workbooks."""

import argparse
from difflib import SequenceMatcher
import json
import posixpath
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import xml.etree.ElementTree as ET

from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.uri_parser import parse_uri

from .employee_identity import (
    fintech_source_identity,
    normalize_employee_name,
    unknown_source_identity,
    vgil_source_identity,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_WORKBOOK = (
    REPOSITORY_ROOT
    / "data_import"
    / "employee_certifications"
    / "CERTIFICATION OF EMPLOYEE.xlsx"
)
DEFAULT_AI_WORKBOOK = (
    REPOSITORY_ROOT
    / "data_import"
    / "employee_certifications"
    / "AI CERTIFICATION SHEET.xlsx"
)
SCRATCH_ONLY_DATABASE_SUFFIXES = ("_scratch", "_test", "_local")
EXCLUDED_VGIL_IDENTITIES = {"vgil:611"}
EXCLUDED_REASON = "Conflicting VGIL ID 611 source records are explicitly excluded."

_XLSX_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "relationships": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


class EmployeeWorkbookError(ValueError):
    """The workbook does not satisfy the importer's source-data contract."""


class EmployeeImportTargetError(ValueError):
    """The MongoDB target is not an explicitly allowed local test database."""


def _label_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _cell_column_index(reference: str) -> int:
    match = re.match(r"[A-Z]+", reference)
    if not match:
        raise EmployeeWorkbookError("Invalid worksheet cell reference: %s" % reference)
    index = 0
    for character in match.group(0):
        index = index * 26 + ord(character) - ord("A") + 1
    return index - 1


def _read_xlsx(path: Path) -> Dict[str, List[Dict[int, str]]]:
    """Read plain cell values from XLSX without a third-party dependency."""
    try:
        with zipfile.ZipFile(path) as workbook:
            workbook_xml = ET.fromstring(workbook.read("xl/workbook.xml"))
            relationships_xml = ET.fromstring(
                workbook.read("xl/_rels/workbook.xml.rels")
            )
            relationships = {
                relation.attrib["Id"]: relation.attrib["Target"]
                for relation in relationships_xml
            }
            shared_strings = []
            if "xl/sharedStrings.xml" in workbook.namelist():
                shared_xml = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
                shared_strings = [
                    "".join(node.text or "" for node in item.findall(".//main:t", _XLSX_NS))
                    for item in shared_xml.findall("main:si", _XLSX_NS)
                ]

            sheets = {}
            for sheet in workbook_xml.findall("main:sheets/main:sheet", _XLSX_NS):
                target = relationships[sheet.attrib["{%(relationships)s}id" % _XLSX_NS]]
                sheet_path = posixpath.normpath(
                    posixpath.join("xl", target.lstrip("/"))
                )
                if sheet_path not in workbook.namelist():
                    raise EmployeeWorkbookError(
                        "Worksheet part is missing: %s" % sheet_path
                    )
                sheet_xml = ET.fromstring(workbook.read(sheet_path))
                rows = []
                for row in sheet_xml.findall(".//main:sheetData/main:row", _XLSX_NS):
                    values = {}
                    for cell in row.findall("main:c", _XLSX_NS):
                        value = cell.find("main:v", _XLSX_NS)
                        cell_value = value.text if value is not None else ""
                        if cell.attrib.get("t") == "s" and cell_value:
                            cell_value = shared_strings[int(cell_value)]
                        elif cell.attrib.get("t") == "inlineStr":
                            cell_value = "".join(
                                node.text or ""
                                for node in cell.findall(".//main:t", _XLSX_NS)
                            )
                        values[_cell_column_index(cell.attrib["r"])] = cell_value or ""
                    if any(
                        value.strip() for index, value in values.items() if index >= 0
                    ):
                        values[-1] = row.attrib.get("r", "")
                        rows.append(values)
                sheets[sheet.attrib["name"].strip().casefold()] = rows
            return sheets
    except (OSError, KeyError, IndexError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise EmployeeWorkbookError(
            "Could not read workbook %s: %s" % (path, exc)
        ) from exc


def _required_sheet(
    sheets: Dict[str, List[Dict[int, str]]], name: str, path: Path
) -> List[Dict[int, str]]:
    try:
        return sheets[name.casefold()]
    except KeyError as exc:
        raise EmployeeWorkbookError(
            "%s is missing the required %r worksheet." % (path, name)
        ) from exc


def _header_columns(
    rows: List[Dict[int, str]], required: Dict[str, Iterable[str]], path: Path, sheet: str
) -> Dict[str, int]:
    if not rows:
        raise EmployeeWorkbookError("%s worksheet %r is empty." % (path, sheet))
    headers = {
        _label_key(value): index for index, value in rows[0].items() if value.strip()
    }
    columns = {}
    for field, names in required.items():
        match = next(
            (headers[_label_key(name)] for name in names if _label_key(name) in headers),
            None,
        )
        if match is None:
            raise EmployeeWorkbookError(
                "%s worksheet %r is missing a required %s header."
                % (path, sheet, field)
            )
        columns[field] = match
    return columns


def _value(row: Dict[int, str], column: Optional[int]) -> str:
    return (row.get(column, "") if column is not None else "").strip()


def split_certification_names(value: str) -> List[str]:
    """Split numbered and line-separated workbook certification names."""
    text = unicodedata.normalize("NFKC", value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    numbered_items = list(re.finditer(r"(?<!\S)\d+\.\s*", text))
    if numbered_items:
        segments = [
            text[item.end() : numbered_items[index + 1].start()]
            if index + 1 < len(numbered_items)
            else text[item.end() :]
            for index, item in enumerate(numbered_items)
        ]
    else:
        segments = text.splitlines()
    output = []
    seen = set()
    for line in segments:
        name = re.sub(r"^[\s•●▪◦‣⁃�]+", "", line).strip()
        name = re.sub(r"\s+", " ", name)
        key = _label_key(name)
        if name and key not in seen:
            output.append(name)
            seen.add(key)
    return output


def _split_education(value: str) -> List[str]:
    output = []
    seen = set()
    for item in re.split(r"[,;\r\n]+", unicodedata.normalize("NFKC", value or "")):
        text = re.sub(r"^[\s•●▪◦‣⁃�]+", "", item).strip()
        text = re.sub(r"\s+", " ", text)
        key = _label_key(text)
        if text and key not in seen:
            output.append(text)
            seen.add(key)
    return output


def _merge_unique(target: List[str], values: Iterable[str]) -> None:
    known = {_label_key(value) for value in target}
    for value in values:
        key = _label_key(value)
        if key and key not in known:
            target.append(value)
            known.add(key)


def _row_provenance(workbook: Path, sheet: str, row_number: int) -> Dict[str, object]:
    return {
        "source_workbook": Path(workbook).name,
        "sheet": sheet,
        "row": row_number,
    }


def _possible_name_matches(name: str, candidates: Iterable[Dict[str, object]]) -> List[Dict[str, str]]:
    source_key = normalize_employee_name(name)
    source_tokens = source_key.split()
    possible = []
    seen = set()
    for candidate in candidates:
        candidate_name = candidate.get("full_name") or candidate.get("name")
        if not isinstance(candidate_name, str) or not candidate_name.strip():
            continue
        candidate_key = normalize_employee_name(candidate_name)
        candidate_identity = str(candidate.get("source_identity") or "")
        candidate_ref = (candidate_key, candidate_identity)
        if candidate_key == source_key or candidate_ref in seen:
            continue
        candidate_tokens = candidate_key.split()
        if len(source_tokens) < 2 or len(candidate_tokens) < 2:
            continue
        overlap = len(set(source_tokens) & set(candidate_tokens))
        similarity = SequenceMatcher(None, source_key, candidate_key).ratio()
        is_middle_initial_variant = (
            len(source_tokens) == 3
            and len(candidate_tokens) == 2
            and source_tokens[0] == candidate_tokens[0]
            and source_tokens[-1] == candidate_tokens[-1]
            and len(source_tokens[1]) == 1
        ) or (
            len(candidate_tokens) == 3
            and len(source_tokens) == 2
            and candidate_tokens[0] == source_tokens[0]
            and candidate_tokens[-1] == source_tokens[-1]
            and len(candidate_tokens[1]) == 1
        )
        if is_middle_initial_variant or (overlap >= 2 and similarity >= 0.84):
            possible.append(
                {
                    "name": candidate_name,
                    "source_identity": candidate_identity,
                }
            )
            seen.add(candidate_ref)
    return possible


def _manual_review(
    provenance: Dict[str, object],
    name: str,
    reason: str,
    possible_matches: Optional[List[Dict[str, str]]] = None,
    source_identity: str = "",
) -> Dict[str, object]:
    return {
        "action": "review",
        "review_type": "manual_name_review",
        "source_identity": source_identity,
        "name": name,
        "source_workbook": provenance.get("source_workbook", ""),
        "sheet": provenance.get("sheet", ""),
        "row": provenance.get("row", ""),
        "possible_matches": possible_matches or [],
        "reason": reason,
    }


def _review_source(
    source: Dict[str, object],
    reason: str,
    possible_matches: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, object]:
    provenance = source.get("provenance", [{}])[0]
    return _manual_review(
        provenance,
        str(source.get("full_name", "")),
        reason,
        possible_matches,
        str(source.get("source_identity", "")),
    )


def load_employee_workbooks(
    source_path: Path = DEFAULT_SOURCE_WORKBOOK,
    ai_path: Path = DEFAULT_AI_WORKBOOK,
    source_identities: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
) -> Tuple[List[Dict[str, object]], List[Dict[str, str]], Dict[str, int]]:
    """Combine authoritative employee rows with name-matched education/AI data."""
    source_sheets = _read_xlsx(Path(source_path))
    ai_sheets = _read_xlsx(Path(ai_path))
    vgil_rows = _required_sheet(source_sheets, "VGIL", Path(source_path))
    fintech_rows = _required_sheet(source_sheets, "Fintech", Path(source_path))
    ai_rows = _required_sheet(ai_sheets, "AI Certificates", Path(ai_path))
    education_rows = _required_sheet(ai_sheets, "Education", Path(ai_path))

    vgil_columns = _header_columns(
        vgil_rows,
        {
            "employee ID in Column A": ("EMPLOYEE. ID", "EMPLOYEE ID"),
            "employee name": ("EMPNAME", "EMPLOYEE NAME"),
            "department": ("DEPARTMENT",),
            "designation": ("DESIGNATION",),
            "certification": ("CERTIFICATION",),
        },
        Path(source_path),
        "VGIL",
    )
    if vgil_columns["employee ID in Column A"] != 0:
        raise EmployeeWorkbookError(
            "VGIL employee identity must come from Column A; the workbook layout changed."
        )
    fintech_columns = _header_columns(
        fintech_rows,
        {
            "employee name": ("EMPNAME", "EMPLOYEE NAME"),
            "department": ("DEPARTMENT",),
            "designation": ("DESIGNATION",),
            "certification": ("CERTIFICATION",),
        },
        Path(source_path),
        "Fintech",
    )
    ai_columns = _header_columns(
        ai_rows,
        {
            "employee name": ("NAME", "EMPNAME", "EMPLOYEE NAME"),
            "certifications": ("CERTIFICATIONS NAMES", "CERTIFICATION NAMES"),
        },
        Path(ai_path),
        "AI Certificates",
    )
    education_columns = _header_columns(
        education_rows,
        {"employee name": ("NAME",), "education": ("QUALIFICATION", "EDUCATION")},
        Path(ai_path),
        "Education",
    )

    records: Dict[str, Dict[str, object]] = {}
    manual_name_review = []
    excluded = []

    def add_source_row(
        row: Dict[int, str],
        row_number: int,
        company: str,
        columns: Dict[str, int],
        workbook_path: Path,
        sheet_name: str,
    ) -> None:
        name = _value(row, columns["employee name"])
        provenance = _row_provenance(workbook_path, sheet_name, row_number)
        identity = ""
        employee_code = ""
        if company == "VGIL":
            # Deliberately never inspect the second "Employee id" column.
            employee_id = _value(row, 0)
            if employee_id:
                identity = vgil_source_identity(employee_id)
                employee_code = identity.removeprefix("vgil:")
        elif name:
            identity = fintech_source_identity(name)
        if not name:
            manual_name_review.append(
                _manual_review(
                    provenance,
                    "",
                    "%s row has no employee name." % company,
                )
            )
            return
        if company == "VGIL":
            if not identity:
                manual_name_review.append(
                    _manual_review(
                        provenance,
                        name,
                        "VGIL row has no authoritative Column A ID.",
                    )
                )
                return
        else:
            if not identity:
                manual_name_review.append(
                    _manual_review(
                        provenance, name, "Fintech row has no usable employee identity."
                    )
                )
                return

        if identity in EXCLUDED_VGIL_IDENTITIES:
            excluded.append(
                {
                    "action": "skip",
                    "source_identity": identity,
                    "name": name,
                    "reason": EXCLUDED_REASON,
                    "provenance": [provenance],
                }
            )
            return

        incoming = {
            "source_identity": identity,
            "full_name": name,
            "company": company,
            "employee_code": employee_code,
            "department": _value(row, columns["department"]),
            "designation": _value(row, columns["designation"]),
            "education": [],
            "certifications": split_certification_names(
                _value(row, columns["certification"])
            ),
            "provenance": [provenance],
            "source_conflicts": [],
        }
        existing = records.get(identity)
        if existing is None:
            records[identity] = incoming
            return
        if normalize_employee_name(str(existing["full_name"])) != normalize_employee_name(
            name
        ):
            existing["source_conflicts"].append(
                "The same source identity appears with a different name."
            )
            manual_name_review.append(
                _manual_review(
                    provenance,
                    name,
                    "The same source identity appears with a different name.",
                    [
                        {
                            "name": str(existing["full_name"]),
                            "source_identity": identity,
                        }
                    ],
                )
            )
            return
        for field in ("department", "designation"):
            old_value = existing[field]
            new_value = incoming[field]
            if old_value and new_value and _label_key(str(old_value)) != _label_key(
                str(new_value)
            ):
                existing["source_conflicts"].append(
                    "Conflicting %s values in repeated source rows." % field
                )
            elif not old_value:
                existing[field] = new_value
        _merge_unique(existing["certifications"], incoming["certifications"])
        existing["provenance"].extend(incoming["provenance"])

    for fallback_row, row in enumerate(vgil_rows[1:], start=2):
        row_number = int(row.get(-1) or fallback_row)
        add_source_row(
            row, row_number, "VGIL", vgil_columns, Path(source_path), "VGIL"
        )
    for fallback_row, row in enumerate(fintech_rows[1:], start=2):
        row_number = int(row.get(-1) or fallback_row)
        add_source_row(
            row_number=row_number,
            row=row,
            company="Fintech",
            columns=fintech_columns,
            workbook_path=Path(source_path),
            sheet_name="Fintech",
        )

    by_normalized_name: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for record in records.values():
        by_normalized_name[normalize_employee_name(str(record["full_name"]))].append(
            record
        )
    for matches in by_normalized_name.values():
        if len(matches) < 2:
            continue
        possible = [
            {
                "name": str(record["full_name"]),
                "source_identity": str(record["source_identity"]),
            }
            for record in matches
        ]
        for record in matches:
            record["source_conflicts"].append(
                "The normalized name appears under multiple source identities."
            )
            for provenance in record["provenance"]:
                manual_name_review.append(
                    _manual_review(
                        provenance,
                        str(record["full_name"]),
                        "The normalized name appears under multiple source identities.",
                        possible,
                        str(record["source_identity"]),
                    )
                )

    auxiliary_counts = {
        "ai_rows_matched": 0,
        "ai_rows_new": 0,
        "ai_rows_review": 0,
        "education_rows_matched": 0,
        "education_rows_new": 0,
        "education_rows_review": 0,
    }

    def add_auxiliary_row(
        row: Dict[int, str],
        row_number: int,
        sheet_name: str,
        workbook_path: Path,
        name_column: int,
        content_column: int,
        content_field: str,
    ) -> None:
        name = _value(row, name_column)
        provenance = _row_provenance(workbook_path, sheet_name, row_number)
        count_key = "ai_rows" if sheet_name == "AI Certificates" else "education_rows"
        if not name:
            auxiliary_counts[count_key + "_review"] += 1
            manual_name_review.append(
                _manual_review(provenance, "", "The auxiliary row has no employee name.")
            )
            return

        excluded_exact = [
            item
            for item in excluded
            if normalize_employee_name(str(item["name"]))
            == normalize_employee_name(name)
        ]
        excluded_possible = _possible_name_matches(name, excluded)
        if excluded_exact or excluded_possible:
            auxiliary_counts[count_key + "_review"] += 1
            possible = [
                {
                    "name": str(item["name"]),
                    "source_identity": str(item["source_identity"]),
                }
                for item in excluded_exact
            ]
            possible.extend(excluded_possible)
            manual_name_review.append(
                _manual_review(
                    provenance,
                    name,
                    "Possible match to a conflicting VGIL ID 611 record; excluded pending manual review.",
                    possible,
                    unknown_source_identity(name),
                )
            )
            return

        candidates = by_normalized_name.get(normalize_employee_name(name), [])
        if len(candidates) > 1:
            auxiliary_counts[count_key + "_review"] += 1
            possible = [
                {
                    "name": str(candidate["full_name"]),
                    "source_identity": str(candidate["source_identity"]),
                }
                for candidate in candidates
            ]
            manual_name_review.append(
                _manual_review(
                    provenance,
                    name,
                    "The normalized name matches multiple workbook employees.",
                    possible,
                    unknown_source_identity(name),
                )
            )
            return

        if not candidates:
            possible = _possible_name_matches(name, records.values())
            if possible:
                auxiliary_counts[count_key + "_review"] += 1
                manual_name_review.append(
                    _manual_review(
                        provenance,
                        name,
                        "A similar workbook name exists; confirm the identity before merging or creating.",
                        possible,
                        unknown_source_identity(name),
                    )
                )
                return
            identity = unknown_source_identity(name)
            record = {
                "source_identity": identity,
                "full_name": name,
                "company": "Unknown",
                "employee_code": "",
                "department": "",
                "designation": "",
                "education": [],
                "certifications": [],
                "provenance": [],
                "source_conflicts": [],
            }
            records[identity] = record
            by_normalized_name[normalize_employee_name(name)].append(record)
            candidates = [record]
            auxiliary_counts[count_key + "_new"] += 1
        else:
            auxiliary_counts[count_key + "_matched"] += 1

        target = candidates[0]
        content = (
            split_certification_names(_value(row, content_column))
            if content_field == "certifications"
            else _split_education(_value(row, content_column))
        )
        _merge_unique(target[content_field], content)
        target["provenance"].append(provenance)

    for fallback_row, row in enumerate(ai_rows[1:], start=2):
        row_number = int(row.get(-1) or fallback_row)
        add_auxiliary_row(
            row,
            row_number,
            "AI Certificates",
            Path(ai_path),
            ai_columns["employee name"],
            ai_columns["certifications"],
            "certifications",
        )

    for fallback_row, row in enumerate(education_rows[1:], start=2):
        row_number = int(row.get(-1) or fallback_row)
        add_auxiliary_row(
            row,
            row_number,
            "Education",
            Path(ai_path),
            education_columns["employee name"],
            education_columns["education"],
            "education",
        )

    requested = set(source_identities or [])
    if requested:
        review_identities = {
            item.get("source_identity")
            for item in manual_name_review
            if item.get("source_identity")
        }
        unknown = (
            requested
            - records.keys()
            - EXCLUDED_VGIL_IDENTITIES
            - review_identities
        )
        if unknown:
            raise EmployeeWorkbookError(
                "Requested source identity(ies) not found in the workbooks: %s"
                % ", ".join(sorted(unknown))
            )
        selected = [
            record for identity, record in records.items() if identity in requested
        ]
        selected.extend(
            record for record in excluded if record["source_identity"] in requested
        )
        manual_name_review = [
            item
            for item in manual_name_review
            if item.get("source_identity") in requested
            or any(
                match.get("source_identity") in requested
                for match in item.get("possible_matches", [])
            )
        ]
    else:
        selected = list(records.values()) + excluded
    if limit is not None:
        if limit < 1:
            raise EmployeeWorkbookError("--limit must be at least 1.")
        selected = selected[:limit]
    return selected, manual_name_review, auxiliary_counts


def _employee_name_key(employee: Dict[str, object]) -> Optional[str]:
    name = employee.get("full_name")
    if not isinstance(name, str) or not name.strip():
        return None
    return normalize_employee_name(name)


def _plan_one(source: Dict[str, object], employees: List[Dict[str, object]]) -> Dict[str, object]:
    if source.get("action") in ("skip", "review"):
        return source
    identity = str(source["source_identity"])
    name = str(source["full_name"])
    name_key = normalize_employee_name(name)
    identity_matches = [
        employee
        for employee in employees
        if employee.get("source_identity") == identity
    ]
    name_matches = [
        employee for employee in employees if _employee_name_key(employee) == name_key
    ]
    if source.get("source_conflicts"):
        return _review_source(source, "; ".join(source["source_conflicts"]))
    if len(identity_matches) > 1:
        return _review_source(
            source,
            "Multiple existing employees have this source identity.",
            [
                {
                    "name": str(employee.get("full_name") or ""),
                    "source_identity": str(employee.get("source_identity") or ""),
                }
                for employee in identity_matches
            ],
        )
    employee = identity_matches[0] if identity_matches else None
    if employee is not None and _employee_name_key(employee) != name_key:
        return _review_source(
            source,
            "Source identity matches an employee with a different name.",
            [
                {
                    "name": str(employee.get("full_name") or ""),
                    "source_identity": str(employee.get("source_identity") or ""),
                }
            ],
        )
    if employee is None:
        if len(name_matches) > 1:
            return _review_source(
                source,
                "Multiple existing employees have this normalized name.",
                [
                    {
                        "name": str(candidate.get("full_name") or ""),
                        "source_identity": str(candidate.get("source_identity") or ""),
                    }
                    for candidate in name_matches
                ],
            )
        if len(name_matches) == 1:
            employee = name_matches[0]
            existing_identity = employee.get("source_identity")
            if (
                source["company"] != "Unknown"
                and isinstance(existing_identity, str)
                and existing_identity.strip()
            ):
                return _review_source(
                    source,
                    "Exact name match already has a different source identity.",
                    [
                        {
                            "name": str(employee.get("full_name") or ""),
                            "source_identity": str(existing_identity),
                        }
                    ],
                )
    if employee is None:
        possible_matches = _possible_name_matches(name, employees)
        if possible_matches:
            provenance = source.get("provenance", [{}])[0]
            return _manual_review(
                provenance,
                name,
                "A similar existing employee name exists; confirm the identity before creating or merging.",
                possible_matches,
                identity,
            )

    if employee is None:
        return {
            "action": "create",
            "source_identity": identity,
            "name": name,
            "changes": [
                "create employee",
                "set company to %s" % source["company"],
                "add %d education entr(y/ies)" % len(source["education"]),
                "add %d certification name(s)" % len(source["certifications"]),
            ],
            "employee_id": None,
        }

    current_company = employee.get("company")
    if source["company"] != "Unknown" and current_company not in (
        None,
        "",
        "Unknown",
        source["company"],
    ):
        return _review_source(
            source,
            "Existing company %r conflicts with source company %r."
            % (current_company, source["company"]),
            [
                {
                    "name": str(employee.get("full_name") or ""),
                    "source_identity": str(employee.get("source_identity") or ""),
                }
            ],
        )
    if source["company"] == "VGIL":
        current_code = employee.get("employee_code")
        if current_code not in (None, "") and str(current_code).strip() != str(
            source["employee_code"]
        ):
            return _review_source(
                source,
                "Existing employee_code conflicts with authoritative VGIL Column A ID.",
                [
                    {
                        "name": str(employee.get("full_name") or ""),
                        "source_identity": str(employee.get("source_identity") or ""),
                    }
                ],
            )
    education = employee.get("education", [])
    certifications = employee.get("certifications", [])
    if not isinstance(education, list):
        return _review_source(source, "Existing education field is not an array.")
    if not isinstance(certifications, list):
        return _review_source(source, "Existing certifications field is not an array.")

    existing_education = {
        _label_key(value) for value in education if isinstance(value, str)
    }
    education_additions = [
        value
        for value in source["education"]
        if _label_key(value) not in existing_education
    ]
    existing_certifications = {
        _label_key(item["name"])
        for item in certifications
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    certification_additions = [
        value
        for value in source["certifications"]
        if _label_key(value) not in existing_certifications
    ]
    changes = []
    set_fields = {}
    existing_identity = employee.get("source_identity")
    if (
        not isinstance(existing_identity, str) or not existing_identity.strip()
    ) and not (
        source["company"] == "Unknown"
        and isinstance(existing_identity, str)
        and existing_identity.strip()
    ):
        set_fields["source_identity"] = identity
        changes.append("set source identity")
    if source["company"] != "Unknown" and source["company"] != current_company:
        set_fields["company"] = source["company"]
        changes.append("set company to %s" % source["company"])
    if source["company"] == "VGIL" and employee.get("employee_code") in (None, ""):
        set_fields["employee_code"] = source["employee_code"]
        changes.append("set employee code from Column A")
    if education_additions:
        changes.append("add %d education entr(y/ies)" % len(education_additions))
    if certification_additions:
        changes.append("add %d certification name(s)" % len(certification_additions))
    return {
        "action": "update" if changes else "skip",
        "source_identity": identity,
        "name": name,
        "employee_id": employee["_id"],
        "changes": changes,
        "set_fields": set_fields,
        "education_additions": education_additions,
        "certification_additions": certification_additions,
    }


def plan_employee_import(
    sources: List[Dict[str, object]],
    employees: List[Dict[str, object]],
    source_conflicts: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, object]:
    """Build a report and a write plan without changing the database."""
    actions = list(source_conflicts or [])
    actions.extend(_plan_one(source, employees) for source in sources)
    return _report_sections(actions)


def _report_sections(actions: List[Dict[str, object]]) -> Dict[str, object]:
    unique_actions = []
    seen_reviews = set()
    for action in actions:
        if action["action"] == "review":
            key = (
                action.get("source_workbook"),
                action.get("sheet"),
                action.get("row"),
                action.get("source_identity"),
                action.get("name"),
                action.get("reason"),
            )
            if key in seen_reviews:
                continue
            seen_reviews.add(key)
        unique_actions.append(action)
    actions = unique_actions
    counts = {name: 0 for name in ("create", "update", "skip", "review")}
    for action in actions:
        counts[action["action"]] += 1
    return {
        "counts": counts,
        "actions": actions,
        "employees_to_create": [
            action for action in actions if action["action"] == "create"
        ],
        "existing_employees_to_update": [
            action for action in actions if action["action"] == "update"
        ],
        "skipped_records": [
            action for action in actions if action["action"] == "skip"
        ],
        "manual_name_review": [
            action
            for action in actions
            if action["action"] == "review"
        ],
    }


def _new_certification(name: str, now: datetime) -> Dict[str, object]:
    return {
        "id": str(ObjectId()),
        "name": name,
        "document_id": None,
        "file_name": None,
        "created_at": now,
    }


def apply_employee_import(
    collection, sources: List[Dict[str, object]], source_conflicts: Optional[List[Dict[str, str]]] = None
) -> Dict[str, object]:
    """Apply a freshly planned bounded sample; untouched fields stay untouched."""
    report = plan_employee_import(
        sources,
        list(collection.find({})),
        source_conflicts=source_conflicts,
    )
    applied_actions = []
    for planned_action in report["actions"]:
        if planned_action["action"] in ("skip", "review"):
            applied_actions.append(planned_action)
            continue
        source = next(
            item
            for item in sources
            if item["source_identity"] == planned_action["source_identity"]
        )
        action = _plan_one(source, list(collection.find({})))
        if action["action"] in ("skip", "review"):
            applied_actions.append(action)
            continue
        if action["action"] == "create":
            now = datetime.now(timezone.utc)
            record = {
                "full_name": source["full_name"],
                "employee_code": source["employee_code"] or None,
                "company": source["company"],
                "designation": source["designation"] or None,
                "department": source["department"] or None,
                "source_identity": source["source_identity"],
                "education": list(source["education"]),
                "cvs": [],
                "certifications": [
                    _new_certification(name, now) for name in source["certifications"]
                ],
                "created_at": now,
                "updated_at": now,
            }
            try:
                collection.insert_one(record)
            except DuplicateKeyError as exc:
                raise RuntimeError(
                    "Import stopped at %s: the source identity became non-unique; "
                    "no automatic match was made. %d earlier action(s) may already "
                    "have been applied; review the database before retrying."
                    % (action["source_identity"], len(applied_actions))
                ) from exc
            except PyMongoError as exc:
                raise RuntimeError(
                    "Import stopped at %s after a database error: %s. %d earlier "
                    "action(s) may already have been applied; review the database "
                    "before retrying."
                    % (action["source_identity"], exc, len(applied_actions))
                ) from exc
            applied_actions.append({**action, "action": "create", "result": "created"})
            continue

        current = collection.find_one({"_id": action["employee_id"]})
        if current is None:
            raise RuntimeError(
                "Import stopped at %s: the matched employee disappeared during import. "
                "%d earlier action(s) may already have been applied; review the "
                "database before retrying."
                % (action["source_identity"], len(applied_actions))
            )
        now = datetime.now(timezone.utc)
        set_fields = action.get("set_fields", {})
        set_fields["updated_at"] = now
        update = {"$set": set_fields}
        additions = action.get("education_additions", [])
        if additions:
            update["$addToSet"] = {"education": {"$each": additions}}
        cert_additions = action.get("certification_additions", [])
        if cert_additions:
            update["$push"] = {
                "certifications": {
                    "$each": [_new_certification(name, now) for name in cert_additions]
                }
            }
        guard = {"_id": action["employee_id"]}
        for field in ("source_identity", "company", "employee_code"):
            guard[field] = (
                current[field] if field in current else {"$exists": False}
            )
        try:
            result = collection.update_one(guard, update)
        except DuplicateKeyError as exc:
            raise RuntimeError(
                "Import stopped at %s: the source identity became non-unique; "
                "no automatic match was made. %d earlier action(s) may already "
                "have been applied; review the database before retrying."
                % (action["source_identity"], len(applied_actions))
            ) from exc
        except PyMongoError as exc:
            raise RuntimeError(
                "Import stopped at %s after a database error: %s. %d earlier "
                "action(s) may already have been applied; review the database "
                "before retrying."
                % (action["source_identity"], exc, len(applied_actions))
            ) from exc
        if result.matched_count != 1:
            raise RuntimeError(
                "Import stopped at %s: the matched employee changed during import. "
                "%d earlier action(s) may already have been applied; review the "
                "database before retrying."
                % (action["source_identity"], len(applied_actions))
            )
        applied_actions.append({**action, "result": "updated"})

    return _report_sections(applied_actions)


def _validate_local_target(
    mongo_uri: str,
    database_name: str,
    allow_doc_collection_dev: bool = False,
) -> None:
    is_doc_collection_dev = database_name.casefold() == "doc_collection_dev"
    if is_doc_collection_dev and not allow_doc_collection_dev:
        raise EmployeeImportTargetError("doc_collection_dev is never an import target.")
    if database_name.casefold() in {"admin", "local", "config"}:
        raise EmployeeImportTargetError(
            "MongoDB system databases are not employee import targets."
        )
    if not is_doc_collection_dev and not database_name.casefold().endswith(
        SCRATCH_ONLY_DATABASE_SUFFIXES
    ):
        raise EmployeeImportTargetError(
            "The importer only allows local databases ending in _scratch, _test, or _local."
        )
    try:
        parsed = parse_uri(mongo_uri)
    except (TypeError, ValueError) as exc:
        raise EmployeeImportTargetError("Invalid MongoDB URI: %s" % exc) from exc
    hosts = {host.casefold().strip("[]") for host, _port in parsed["nodelist"]}
    if not hosts or not hosts.issubset({"localhost", "127.0.0.1", "::1"}):
        raise EmployeeImportTargetError(
            "The importer refuses remote MongoDB hosts; use an isolated loopback database."
        )


def _print_report(report: Dict[str, object], mode: str, database_name: str) -> None:
    print("mode: %s" % mode)
    print("database: %s" % database_name)
    print("counts: %s" % json.dumps(report["counts"], sort_keys=True))
    sections = (
        ("Employees to create", report["employees_to_create"]),
        ("Existing employees to update", report["existing_employees_to_update"]),
        ("Skipped records", report["skipped_records"]),
    )
    for title, items in sections:
        print("%s (%d):" % (title, len(items)))
        for item in items:
            print(
                "  %s | %s | %s"
                % (
                    item.get("source_identity", "(no identity)"),
                    item.get("name", "(no name)"),
                    item.get("reason")
                    or "; ".join(item.get("changes", []))
                    or item.get("result", "no changes"),
                )
            )
    print("Manual Name Review (%d):" % len(report["manual_name_review"]))
    for item in report["manual_name_review"]:
        matches = ", ".join(
            "%s [%s]" % (match["name"], match["source_identity"])
            for match in item.get("possible_matches", [])
        ) or "(none found)"
        print(
            "  %s | %s | row %s | %s | identity %s | possible matches: %s | %s"
            % (
                item.get("source_workbook", "(unknown workbook)"),
                item.get("sheet", "(unknown sheet)"),
                item.get("row", "(unknown row)"),
                item.get("name", "(no name)"),
                item.get("source_identity", ""),
                matches,
                item.get("reason", ""),
            )
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview or safely import a selected employee workbook sample."
    )
    parser.add_argument("--mongo-uri", required=True, help="Explicit local MongoDB URI.")
    parser.add_argument("--database", required=True, help="Explicit local database.")
    parser.add_argument("--source-workbook", type=Path, default=DEFAULT_SOURCE_WORKBOOK)
    parser.add_argument("--ai-workbook", type=Path, default=DEFAULT_AI_WORKBOOK)
    parser.add_argument(
        "--source-identity",
        action="append",
        dest="source_identities",
        help="Limit to an explicit identity; may be supplied more than once.",
    )
    parser.add_argument("--limit", type=int, help="Limit the selected sample size.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the previewed changes (requires an explicit <=10 identity allowlist).",
    )
    parser.add_argument(
        "--allow-doc-collection-dev",
        action="store_true",
        help="Explicitly permit doc_collection_dev for this invocation; loopback is still required.",
    )
    args = parser.parse_args(argv)
    try:
        _validate_local_target(
            args.mongo_uri,
            args.database,
            allow_doc_collection_dev=args.allow_doc_collection_dev,
        )
        if args.apply:
            if not args.source_identities:
                raise EmployeeImportTargetError(
                    "--apply requires one or more explicit --source-identity values."
                )
            if len(args.source_identities) > 10:
                raise EmployeeImportTargetError(
                    "--apply is limited to 10 explicitly selected identities per run."
                )
            if args.limit is not None and args.limit > 10:
                raise EmployeeImportTargetError("--apply is limited to --limit 10.")
        sources, source_conflicts, auxiliary_counts = load_employee_workbooks(
            args.source_workbook,
            args.ai_workbook,
            args.source_identities,
            args.limit,
        )
        with MongoClient(
            args.mongo_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
        ) as client:
            database = client[args.database]
            database.command("ping")
            collection = database["employees"]
            if args.apply:
                report = apply_employee_import(
                    collection, sources, source_conflicts=source_conflicts
                )
                mode = "applied"
            else:
                report = plan_employee_import(
                    sources,
                    list(collection.find({})),
                    source_conflicts=source_conflicts,
                )
                mode = "dry-run"
        report["auxiliary_rows"] = auxiliary_counts
        _print_report(report, mode, args.database)
        print("auxiliary rows: %s" % json.dumps(auxiliary_counts, sort_keys=True))
        return 0
    except (
        EmployeeWorkbookError,
        EmployeeImportTargetError,
        RuntimeError,
        PyMongoError,
    ) as exc:
        print("employee import failed: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
