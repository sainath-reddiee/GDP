"""Modeling standard of a run: GDP (hub/spoke with GDP conventions) or GENERIC (any company).

Every run answers "is this GDP or not?". The answer decides which domain packs compete, the dbt prefix and which
target columns are filled by the platform instead of mapped from a source. Runs created before the choice existed
keep the GDP behaviour they were built with.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

GDP, GENERIC = "GDP", "GENERIC"
STANDARDS = (GDP, GENERIC)

# Semantic types of target columns the platform fills itself (never mapped from a source column).
SYSTEM_DERIVED = {"SURROGATE_KEY", "RECORD_SOURCE", "AUDIT_TIMESTAMP", "DERIVED_KEY"}
GDP_AUDIT_SUFFIXES = ("INSERTED_TS", "UPDATED_TS", "IS_ACTIVE", "ROW_HASH", "CREATED_TS", "CREATED_BY",
                      "UPDATED_BY", "INSERTED_BY", "DELETED_TS", "LOAD_TS")
TIME_DEFAULTS = ("CURRENT_TIMESTAMP", "SYSDATE", "GETDATE", "LOCALTIMESTAMP", "CURRENT_DATE", "SYSTIMESTAMP")


def normalize_standard(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    return text if text in STANDARDS else None


def run_standard(run: Dict[str, Any]) -> str:
    """The run's standard; runs from before the choice existed keep GDP behaviour."""
    return normalize_standard(run.get("MODELING_STANDARD") or run.get("modeling_standard")) or GDP


def default_prefix(standard: str) -> str:
    """Column/object prefix the dbt generator uses when the run does not set one."""
    return "GDP" if standard == GDP else ""


def technical_semantic(column_name: str, standard: str, target_table: str = "", prefix: str = "GDP",
                       column_default: Optional[str] = None, is_identity: bool = False,
                       hub_fk: Optional[str] = None) -> Optional[str]:
    """Semantic type for a target column the platform fills, or None when a source should map it.

    The table definition decides first, for any company: identity/sequence columns are surrogate keys and
    columns defaulting to the current time are audit columns. GDP naming conventions apply only to GDP runs."""
    default = str(column_default or "").upper()
    if is_identity or "NEXTVAL" in default:
        return "SURROGATE_KEY"
    if any(marker in default for marker in TIME_DEFAULTS):
        return "AUDIT_TIMESTAMP"
    if standard != GDP:
        return None
    name, p, table = str(column_name).upper(), (prefix or "GDP").upper(), str(target_table).upper()
    if name.endswith("_HKEY") or (table and name == f"{table}_SKEY"):
        return "SURROGATE_KEY"
    if name == f"REF_{p}_SOURCE_SYSTEM_SKEY":
        return "RECORD_SOURCE"
    if name.startswith(f"{p}_") and name[len(p) + 1:] in GDP_AUDIT_SUFFIXES:
        return "AUDIT_TIMESTAMP"
    if hub_fk and name == str(hub_fk).upper():
        return "DERIVED_KEY"  # a spoke's own hub reference, resolved from the hub in dbt (other hubs are mapped)
    return None
