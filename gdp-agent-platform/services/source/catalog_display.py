"""Hide seed/POC catalog leftovers and product-name labels from task UIs.

The lists are defaults; an installation replaces them through PLATFORM_CONFIG key CATALOG_DISPLAY
({"hidden_target_tables": [...], "hidden_target_databases": [...], "hidden_target_schemas": [...],
"hidden_target_ids": [...], "hidden_domain_names": [...], "strip_tokens": [...]}) with `configure()`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

# Installation-specific entries come from CATALOG_DISPLAY (seeded, editable in Admin), not from code.
HIDDEN_TARGET_TABLES: set = set()
HIDDEN_TARGET_DATABASES: set = set()
HIDDEN_TARGET_SCHEMAS: set = set()
HIDDEN_TARGET_IDS: set = set()
HIDDEN_DOMAIN_NAMES = {"GDP"}
STRIP_TOKENS = {"GDP"}  # product-name tokens removed from suggested source system names

_SETS = {"hidden_target_tables": HIDDEN_TARGET_TABLES, "hidden_target_databases": HIDDEN_TARGET_DATABASES,
         "hidden_target_schemas": HIDDEN_TARGET_SCHEMAS, "hidden_target_ids": HIDDEN_TARGET_IDS,
         "hidden_domain_names": HIDDEN_DOMAIN_NAMES, "strip_tokens": STRIP_TOKENS}


def configure(config: Optional[Dict[str, Any]]) -> None:
    """Replace any list from configuration (keys missing from `config` keep their defaults)."""
    for key, target in _SETS.items():
        values = (config or {}).get(key)
        if isinstance(values, list):
            target.clear()
            target.update(str(v).strip() if key == "hidden_target_ids" else str(v).strip().upper()
                          for v in values if str(v).strip())


def strip_tokens(raw: str) -> str:
    """Drop whole product-name tokens only: GDP_CRM -> CRM, but GDPR_EVENTS stays GDPR_EVENTS."""
    parts = [p for p in re.split(r"_+", str(raw or "")) if p and p.upper() not in STRIP_TOKENS]
    return "_".join(parts)


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
        cleaned = strip_tokens(explicit)
        if cleaned and not is_hidden_domain(cleaned):
            return _safe_ident(cleaned)
    return _safe_ident(strip_tokens(schema or database or "SOURCE"))
