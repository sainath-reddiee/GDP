"""Hide seed/POC catalog leftovers and product-name labels from task UIs."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

HIDDEN_TARGET_TABLES = {"COMPLETE_EMPLOYEE_DETAILS"}
HIDDEN_TARGET_DATABASES = {"ALATION_POC"}
HIDDEN_TARGET_SCHEMAS = {"GDP_SILVER"}
HIDDEN_TARGET_IDS = {"00000000-0000-4000-a000-000000000002"}
HIDDEN_DOMAIN_NAMES = {"GDP"}


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def is_hidden_domain(name: Any) -> bool:
    return _upper(name) in HIDDEN_DOMAIN_NAMES


def display_domain_name(name: Any) -> Optional[str]:
    if is_hidden_domain(name) or not str(name or "").strip():
        return None
    return str(name).strip()


def is_hidden_target(row: Dict[str, Any]) -> bool:
    table = _upper(row.get("target_table") or row.get("TARGET_TABLE"))
    database = _upper(row.get("target_database") or row.get("TARGET_DATABASE"))
    schema = _upper(row.get("target_schema") or row.get("TARGET_SCHEMA"))
    ident = str(row.get("target_table_id") or row.get("TARGET_TABLE_ID") or "").strip()
    if ident in HIDDEN_TARGET_IDS:
        return True
    if table in HIDDEN_TARGET_TABLES:
        return True
    if database in HIDDEN_TARGET_DATABASES:
        return True
    if schema in HIDDEN_TARGET_SCHEMAS:
        return True
    return False


def catalog_related(target: Dict[str, Any], database: Optional[str], schema: Optional[str]) -> bool:
    tdb = _upper(target.get("target_database") or target.get("TARGET_DATABASE"))
    tsch = _upper(target.get("target_schema") or target.get("TARGET_SCHEMA"))
    db = _upper(database)
    sch = _upper(schema)
    if db and tdb and db == tdb:
        return True
    if sch and tsch and (sch in tsch or tsch in sch):
        return True
    return False


def workspace_targets(
    targets: Iterable[Dict[str, Any]],
    database: Optional[str] = None,
    schema: Optional[str] = None,
) -> List[Dict[str, Any]]:
    visible = [row for row in targets if not is_hidden_target(row)]
    if not database and not schema:
        return []
    return [row for row in visible if catalog_related(row, database, schema)]


def _safe_ident(raw: str) -> str:
    ident = re.sub(r"[^A-Za-z0-9_]", "", raw)
    if ident and ident[0].isdigit():
        ident = f"S_{ident}"
    return (ident or "SOURCE")[:64]


def source_system_name(
    database: Optional[str] = None,
    schema: Optional[str] = None,
    explicit: Optional[str] = None,
) -> str:
    if explicit:
        cleaned = re.sub(r"(?i)_?GDP_?", "_", explicit)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        if cleaned and not is_hidden_domain(cleaned):
            return _safe_ident(cleaned)
    raw = schema or database or "SOURCE"
    cleaned = re.sub(r"(?i)_?GDP_?", "_", raw)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return _safe_ident(cleaned)
