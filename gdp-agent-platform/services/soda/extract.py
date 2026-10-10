"""Turn a client quality brief into structured Soda requirements (pure + Cortex)."""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Dict, List

EXTRACT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_column": {"type": ["string", "null"]},
                    "check_type": {"type": "string"},
                    "severity": {"type": "string"},
                    "requirement": {"type": "string"},
                    "threshold": {"type": ["string", "number", "null"]},
                    "valid_values": {"type": "array", "items": {"type": "string"}},
                    "valid_regex": {"type": ["string", "null"]},
                    "valid_format": {"type": ["string", "null"]},
                    "valid_min": {"type": ["number", "null"]},
                    "valid_max": {"type": ["number", "null"]},
                    "freshness": {"type": ["string", "null"]},
                    "missing_values": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
                "required": ["check_type", "severity", "requirement"],
            },
        }
    },
    "required": ["requirements"],
}

SODACL_HINT = """
Write checks using official SodaCL (Soda v3), not invented syntax.
Preferred metrics:
- row_count > N or row_count between A and B
- missing_count(column) = 0, optional missing values: [NA, n/a]
- duplicate_count(column) = 0
- invalid_count(column) = 0 with valid values, valid regex, valid format, valid min, valid max
- invalid_percent(column) < N%
- freshness(timestamp_column) < 1d   (only < is valid)
- schema fail when required column missing
- values in (fk) must exist in other_table (pk) for referential integrity
Built-in valid formats include email, phone number, uuid, credit card number, date iso 8601, ipv4.
Severity FAIL for grain, keys, required columns and accepted codes. WARN for format, freshness, and soft distributions.
Do not invent columns that are not on the STTM. Table-level checks leave target_column null.
"""


def parse_client_document(text: str, filename: str = "") -> Dict[str, Any]:
    """Accept JSON rows, CSV rows, or free-text. Returns {rows} and/or {brief}."""
    raw = (text or "").strip()
    assert raw, "upload or paste the client quality brief"
    name = (filename or "").lower()
    if raw.startswith("[") or raw.startswith("{") or name.endswith(".json"):
        payload = json.loads(raw)
        if isinstance(payload, list):
            return {"rows": payload}
        if isinstance(payload, dict) and (payload.get("rows") or payload.get("brief")):
            return {k: payload.get(k) for k in ("rows", "brief", "filename") if payload.get(k)}
        return {"brief": json.dumps(payload, indent=2)}
    if name.endswith(".csv") or ("," in raw.splitlines()[0] and _looks_csv(raw)):
        rows = list(csv.DictReader(io.StringIO(raw)))
        assert rows, "CSV has a header but no requirement rows"
        return {"rows": rows}
    return {"brief": raw, "filename": filename}


def _looks_csv(text: str) -> bool:
    header = text.splitlines()[0].lower()
    return bool(re.search(r"attribute|target_column|check_type|requirement", header))


def extract_prompt(brief: str, table: str, columns: List[str], knowledge: List[str]) -> str:
    known = ", ".join(columns) if columns else "(no STTM columns yet)"
    memory = "\n".join(f"- {k}" for k in knowledge[:8]) or "- none"
    return (
        f"You extract data-quality requirements for Soda checks on {table}.\n"
        f"STTM columns: {known}\n"
        f"Learned Soda patterns:\n{memory}\n"
        f"{SODACL_HINT}\n"
        f"Client brief:\n{brief[:12000]}\n"
        "Return only requirements the brief or domain patterns actually justify."
    )


def normalize_check_type(value: str) -> str:
    text = re.sub(r"[^A-Z0-9]+", "_", (value or "CUSTOM").upper()).strip("_")
    aliases = {
        "NOTNULL": "NOT_NULL", "MISSING": "NOT_NULL", "MISSING_COUNT": "NOT_NULL",
        "UNIQUENESS": "UNIQUE", "DUPLICATE": "UNIQUE", "DUPLICATE_COUNT": "UNIQUE",
        "VALID_VALUES": "ACCEPTED_VALUES", "ACCEPTED": "ACCEPTED_VALUES",
        "REGEX": "CUSTOM", "FORMAT": "CUSTOM", "VALIDITY": "CUSTOM", "EMAIL": "CUSTOM",
        "MIN_MAX": "RANGE", "BETWEEN": "RANGE",
        "REFERENTIAL_INTEGRITY": "REFERENTIAL_INTEGRITY", "REFERENCE": "REFERENTIAL_INTEGRITY",
        "ROWCOUNT": "ROW_COUNT",
    }
    return aliases.get(text, text or "CUSTOM")


def _as_list(value: Any) -> List[Any]:
    """CSV cells carry lists as text, e.g. "ACTIVE|INACTIVE" or "A;B,C"."""
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[|;,]", value) if v.strip()]
    return list(value or [])


def _as_number(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        number = float(value.strip())
    except ValueError:
        return value.strip()
    return int(number) if number.is_integer() else number


def requirement_from_row(target_table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    # Blank CSV cells mean "not given", not an empty value.
    row = {k: v for k, v in row.items() if not (v is None or (isinstance(v, str) and not v.strip()))}
    col = row.get("target_column") or row.get("attribute") or row.get("ATTRIBUTE") or row.get("column")
    kind = normalize_check_type(str(row.get("check_type") or row.get("CHECK_TYPE") or "CUSTOM"))
    definition: Dict[str, Any] = {"kind": _kind(kind, row)}
    if row.get("valid_values") or row.get("accepted_values"):
        definition["values"] = _as_list(row.get("valid_values") or row.get("accepted_values"))
        definition["kind"] = "accepted_values"
        kind = "ACCEPTED_VALUES"
    if row.get("valid_regex") or row.get("pattern"):
        definition["pattern"] = row.get("valid_regex") or row.get("pattern")
        definition["kind"] = "regex"
    if row.get("valid_format"):
        definition["format"] = row["valid_format"]
        definition["kind"] = "format"
    if row.get("valid_min") is not None or row.get("valid_max") is not None:
        definition["min"] = _as_number(row.get("valid_min"))
        definition["max"] = _as_number(row.get("valid_max"))
        definition["kind"] = "range"
        kind = "RANGE"
    if row.get("freshness"):
        definition["threshold"] = row["freshness"]
        definition["kind"] = "freshness"
        kind = "FRESHNESS"
    if row.get("missing_values"):
        definition["missing_values"] = _as_list(row["missing_values"])
    if row.get("threshold") and "threshold" not in definition:
        definition["threshold"] = row["threshold"]
    if row.get("reference_table"):
        definition["reference_table"] = row["reference_table"]
        definition["reference_column"] = row.get("reference_column")
        definition["kind"] = "reference"
        kind = "REFERENTIAL_INTEGRITY"
    return {
        "target_table": target_table,
        "target_column": (str(col).strip() if col else None) or None,
        "check_type": kind,
        "definition": definition,
        "severity": str(row.get("severity") or row.get("SEVERITY") or "WARN").upper(),
        "origin": row.get("origin") or "CLIENT",
        "requirement": row.get("requirement") or row.get("REQUIREMENT") or row.get("client_requirement")
        or row.get("rationale"),
    }


def _kind(check_type: str, row: Dict[str, Any]) -> str:
    return {
        "NOT_NULL": "not_null", "UNIQUE": "unique", "ACCEPTED_VALUES": "accepted_values",
        "RANGE": "range", "FRESHNESS": "freshness", "ROW_COUNT": "row_count",
        "SCHEMA": "schema", "REFERENTIAL_INTEGRITY": "reference", "CUSTOM": "custom",
    }.get(check_type, str(row.get("kind") or "custom"))
