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


# ---------------------------------------------------------------- conventions per standard
# Everything the dbt generator, publisher and workspace used to hardcode. GDP reproduces today's output exactly;
# GENERIC is a plain model any company can adopt. An organisation overrides any key through PLATFORM_CONFIG
# (key MODELING_STANDARD.<GDP|GENERIC>) without a code change.

PRESETS: Dict[str, Dict[str, Any]] = {
    GDP: {
        "prefix": "GDP",
        "default_domain": "gdp",
        "model_dir": "models/silver/{domain}",
        "source_dir": "models/bronze",
        "layer_tag": "silver",
        "staging_materialization": "ephemeral",
        "scd_type": 1,
        "key_strategy": "sequence",            # sequence | hash | none
        "hub_active_filter": "{pl}_is_active",
        "hub_latest_order": "{pl}_inserted_ts desc",
        "watermark_table": "DEV_{P}_UTIL_DB.CONFIG.{P}_DBT_WATERMARK_TBL",
        "dbt_profile": "gdp_platform",
        "project_name": "gdp_{domain}",
        "branch_prefix": "feat/gdp-",
        "codegen_prefix": "GDP_",
        "pr_title": "GDP: onboard {name} (dbt v{version})",
        "commit_message": "GDP run {name}: dbt v{version} from the approved STTM",
        "skill": "GDP-DBT-ONBOARD-SOURCE",
        "convention": "bronze -> ephemeral silver staging -> silver hub (SCD1)",
    },
    GENERIC: {
        "prefix": "",
        "default_domain": "general",
        "model_dir": "models/{domain}",
        "source_dir": "models/sources",
        "layer_tag": "model",
        "staging_materialization": "view",     # snapshots cannot read ephemeral models
        "scd_type": 2,
        "key_strategy": "hash",
        "hub_active_filter": "dbt_valid_to is null",
        "hub_latest_order": "dbt_valid_from desc",
        "watermark_table": "",
        "dbt_profile": "default",
        "project_name": "{domain}",
        "branch_prefix": "feat/onboard-",
        "codegen_prefix": "ONBOARD_",
        "pr_title": "Onboard {name} (dbt v{version})",
        "commit_message": "Onboard {name}: dbt v{version} from the approved STTM",
        "skill": "",
        "convention": "sources -> staging view -> snapshot (SCD2) -> current-rows view",
    },
}


def conventions(standard: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The standard's preset with the organisation's overrides (unknown keys ignored)."""
    preset = dict(PRESETS.get(standard) or PRESETS[GDP])
    for key, value in (overrides or {}).items():
        if key in preset and value is not None and isinstance(value, type(preset[key])):
            preset[key] = value
    preset["standard"] = standard if standard in PRESETS else GDP
    return preset


def conventions_for(query, standard: str) -> Dict[str, Any]:
    """`query(sql, params)` -> rows; reads PLATFORM_CONFIG overrides for this standard (missing -> preset)."""
    import json

    try:
        found = query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = ? AND IS_CURRENT",
                      [f"MODELING_STANDARD.{standard}"])
        value = (found[0].get("CONFIG_VALUE") if "CONFIG_VALUE" in found[0] else found[0].get("config_value")) \
            if found else None
        overrides = json.loads(value) if isinstance(value, str) else value
    except Exception:
        overrides = None
    return conventions(standard, overrides if isinstance(overrides, dict) else None)


def fill(template: str, **values: Any) -> str:
    """Placeholders in convention strings: {domain}, {P} (prefix), {pl} (prefix lower), {name}, {version}."""
    out = str(template or "")
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out
