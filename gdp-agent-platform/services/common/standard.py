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


SILVER, GOLD = "SILVER", "GOLD"


def run_pipeline(run: Dict[str, Any]) -> str:
    """SILVER (source to silver, every run before V054) or GOLD (silver, read in place, to gold)."""
    value = str(run.get("PIPELINE") or run.get("pipeline") or "").strip().upper()
    return GOLD if value == GOLD else SILVER


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


# The Gold Pipeline's own conventions, laid over the standard's (GDP gold: star schema in the domain's gold
# database, Iceberg, sequences and HKEY; the gold-model skill). Overridden through PLATFORM_CONFIG key
# MODELING_STANDARD.<STANDARD>.GOLD.
PIPELINE_PRESETS: Dict[str, Dict[str, Dict[str, Any]]] = {
    GDP: {
        GOLD: {
            "model_dir": "models/gold/{domain}",
            "layer_tag": "gold",
            "project_name": "gdp_gold_{domain}",
            "branch_prefix": "feat/gdp-gold-",
            "codegen_prefix": "GDP_GOLD_",
            "pr_title": "GDP Gold: {name} (dbt v{version})",
            "commit_message": "GDP Gold run {name}: dbt v{version} from the approved STTM and gold model",
            "skill": "GOLD-MODEL",
            "convention": "post-MDM silver -> gold star schema (facts, dimensions, bridges; SCD1, sequences, HKEY, Iceberg)",
        },
    },
    GENERIC: {
        GOLD: {
            "model_dir": "models/marts/{domain}",
            "layer_tag": "gold",
            "branch_prefix": "feat/gold-",
            "codegen_prefix": "GOLD_",
            "pr_title": "Gold: {name} (dbt v{version})",
            "commit_message": "Gold {name}: dbt v{version} from the approved STTM",
            "convention": "silver -> marts star schema (facts, dimensions, bridges)",
        },
    },
}


def _overlay(preset: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> None:
    for key, value in (overrides or {}).items():
        if key in preset and value is not None and isinstance(value, type(preset[key])):
            preset[key] = value


def conventions(standard: str, overrides: Optional[Dict[str, Any]] = None, pipeline: str = SILVER,
                pipeline_overrides: Optional[Dict[str, Any]] = None,
                domain_overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The standard's preset with the organisation's overrides (unknown keys ignored); for the Gold Pipeline, the gold
    preset and its overrides on top; then the domain's own overrides for this layer (CONFIG.conventions for Silver,
    CONFIG.conventions_gold for Gold), so one domain can build its code differently without changing the others."""
    std = standard if standard in PRESETS else GDP
    preset = dict(PRESETS[std])
    _overlay(preset, overrides)
    if str(pipeline or "").upper() == GOLD:
        preset.update(PIPELINE_PRESETS.get(std, {}).get(GOLD, {}))
        _overlay(preset, pipeline_overrides)
    _overlay(preset, domain_overrides)
    preset["standard"] = std
    preset["pipeline"] = GOLD if str(pipeline or "").upper() == GOLD else SILVER
    return preset


def _config(query, key: str) -> Optional[Dict[str, Any]]:
    import json

    try:
        found = query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = ? AND IS_CURRENT", [key])
        value = (found[0].get("CONFIG_VALUE") if "CONFIG_VALUE" in found[0] else found[0].get("config_value")) \
            if found else None
        value = json.loads(value) if isinstance(value, str) else value
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def domain_conventions(query, domain_id: Optional[str], pipeline: str = SILVER) -> Optional[Dict[str, Any]]:
    """A domain's own convention overrides for a layer (DOMAIN_REGISTRY.CONFIG.conventions / conventions_gold)."""
    import json

    if not domain_id:
        return None
    key = "conventions_gold" if str(pipeline or "").upper() == GOLD else "conventions"
    try:
        found = query(f"SELECT CONFIG:{key} AS V FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [domain_id])
        value = (found[0].get("V") if "V" in found[0] else found[0].get("v")) if found else None
        value = json.loads(value) if isinstance(value, str) else value
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def reference_conventions(query, domain_id: Optional[str], pipeline: str = SILVER) -> Dict[str, Any]:
    """What the domain's own dbt project (a DBT_CODE reference input for the layer, V057) shows, as overrides."""
    if not domain_id:
        return {}
    from services.reference import code_conventions, store

    ref = store.latest(query, domain_id, GOLD if str(pipeline or "").upper() == GOLD else SILVER, "DBT_CODE")
    learned = ((ref or {}).get("parsed") or {}).get("conventions") or {}
    return code_conventions.to_overrides(learned, pipeline) if learned else {}


def conventions_for(query, standard: str, pipeline: str = SILVER, domain_id: Optional[str] = None) -> Dict[str, Any]:
    """`query(sql, params)` -> rows; reads PLATFORM_CONFIG overrides for this standard, for the Gold Pipeline its gold
    overrides, then what the domain's own dbt project shows (a reference input), then the domain's own overrides for
    the layer, which win (missing -> preset)."""
    gold = str(pipeline or "").upper() == GOLD
    learned = reference_conventions(query, domain_id, pipeline)
    own = domain_conventions(query, domain_id, pipeline)
    out = conventions(standard, _config(query, f"MODELING_STANDARD.{standard}"), pipeline,
                      _config(query, f"MODELING_STANDARD.{standard}.GOLD") if gold else None,
                      {**learned, **(own or {})} if learned else own)
    if learned:
        out["learned_from_project"] = sorted(k for k in learned if k not in (own or {}))
    return out


def fill(template: str, **values: Any) -> str:
    """Placeholders in convention strings: {domain}, {P} (prefix), {pl} (prefix lower), {name}, {version}."""
    out = str(template or "")
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out
