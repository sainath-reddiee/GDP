"""SnapLogic config tables (pure): what one onboarding writes to the two tables SnapLogic triggers from.

    <prefix>SNAPLOGIC_CONFIG_MASTER   one row per source system   (MASTER_CONFIG_ID autoincrement)
    <prefix>SNAPLOGIC_CONFIG_DETAIL   one row per table           (CHILD_CONFIG_ID autoincrement, MASTER_CONFIG_ID links)

The prefix comes from the SNAPLOGIC_SYNC setting (default DEV_GDP_UTIL_DB.CONFIG.AODS_) and never names the live GDP_*
tables. Rows follow the live GDP_* conventions (the defaults below were profiled from them): the cron lives on the master
row, JOB_TRIGGER_TIME stays NULL (SnapLogic sets it when it runs), CONFIG_TABLE names the detail table itself, the child
pipeline follows the source type, and TARGET_PREVIOUS_LOAD_TIME, LAST_RUN_TIMESTAMP and COMPLETION_FLAG are starting
values SnapLogic updates after each run. CORE keeps its own conventions (upper-case names); only these rows use the
live ones. The tables belong to SnapLogic's owners, so ids are never invented: a source's master row is reused when it
exists, and new rows carry an exact creation time that the writer reads their ids back by, inside its transaction.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

KEY = "SNAPLOGIC_SYNC"
MASTER = "SNAPLOGIC_CONFIG_MASTER"
DETAIL = "SNAPLOGIC_CONFIG_DETAIL"
PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z0-9_$]*$")

# the live tables' columns, in their DDL order (the identity columns are filled by Snowflake)
MASTER_COLUMNS = ("SOURCE_SYSTEM", "SOURCE_TYPE", "ACTIVE_FLAG", "CREATED_AT", "SCHEDULE_CRON", "SOURCE_SYSTEM_DESC",
                  "DOMAIN", "AIRFLOW_SOURCE_SYSTEM", "SCHEDULE_FREQUENCY", "SILVER_COMMAND_NAME")
DETAIL_COLUMNS = (
    "MASTER_CONFIG_ID", "PARENT_PIPELINE_NAME", "CHILD_PIPELINE_NAME", "SNAPLOGIC_PROJECT_NAME", "SOURCE_ACCOUNT",
    "SOURCE_DATABASE_PATH", "SOURCE_SCHEMA_FILE", "SOURCE_TABLE_SQL", "SOURCE_FILE_TYPE", "SOURCE_DELTA_KEY",
    "SOURCE_PRIMARY_KEYS", "SOURCE_WHERE_CLAUSE", "TARGET_ACCOUNT", "LANDING_DATABASE", "LANDING_SCHEMA", "LANDING_TABLE",
    "BRONZE_DATABASE", "BRONZE_SCHEMA", "BRONZE_TABLE", "TARGET_PREVIOUS_LOAD_TIME", "LOAD_TYPE", "LOAD_FREQUENCY",
    "JOB_TRIGGER_TIME", "API_URL", "API_KEY", "EXTERNAL_STAGE", "LAST_RUN_TIMESTAMP", "IS_ACTIVE", "COMPLETION_FLAG",
    "LANDING_TO_BRONZE_FLAG", "CREATED_BY", "CREATED_AT", "UPDATED_AT", "COMMENTS", "SOURCE_AUDIT_COLUMNS",
    "BRONZE_LOAD_TYPE", "SOURCE_DELETE_ACTION", "SOURCE_DELETE_KEY", "SOURCE_DELETE_KEY_VALUE", "SOURCE_DELETE_QUERY",
    "SOURCE_SHEET_NAME", "CONFIG_TABLE", "EXTERNAL_VOLUME", "BASE_LOCATION")
# what an edit of a CORE control row carries over to its detail row (identity, source and target stay)
EDIT_COLUMNS = ("SOURCE_TABLE_SQL", "SOURCE_DELTA_KEY", "SOURCE_PRIMARY_KEYS", "SOURCE_WHERE_CLAUSE", "SOURCE_AUDIT_COLUMNS",
                "BRONZE_TABLE", "LOAD_TYPE", "BRONZE_LOAD_TYPE", "LOAD_FREQUENCY", "LANDING_TO_BRONZE_FLAG",
                "TARGET_PREVIOUS_LOAD_TIME", "LAST_RUN_TIMESTAMP", "COMPLETION_FLAG", "SOURCE_DELETE_ACTION",
                "SOURCE_DELETE_KEY", "SOURCE_DELETE_KEY_VALUE", "SOURCE_DELETE_QUERY", "API_URL", "API_KEY",
                "EXTERNAL_STAGE", "COMMENTS", "IS_ACTIVE")

CHILD_PIPELINE_BY_TYPE: Dict[str, str] = {
    "SNOWFLAKE_SHARE": "z_reusable_SnowflakeShare_Snowflake_load", "SNOWFLAKE": "z_reusable_SnowflakeShare_Snowflake_load",
    "ORACLE": "z_reusable_SnowflakeShare_Snowflake_load", "REST_API": "z_reusable_SnowflakeShare_Snowflake_load",
    "S3_DEALIQ": "z_reusable_SnowflakeShare_Snowflake_load", "SHAREPOINT": "z_reusable_Sharepoint_Snowflake_load",
    "S3": "z_reusable_s3_snowflake_load", "POSTGRESQL": "z_reusable_postgres_snowflake_load", "DNB": "z_dnb_load",
}
# CORE spellings that SnapLogic knows by another name
SOURCE_TYPE_ALIASES = {"POSTGRES": "POSTGRESQL"}

DEFAULT_SETTING: Dict[str, Any] = {
    "enabled": False,
    "table_prefix": "DEV_GDP_UTIL_DB.CONFIG.AODS_",
    "defaults": {
        "parent_pipeline_name": "GDP_config_driven_framework_parent",
        "snaplogic_project_name": "GLOBAL_DATA_PLATFORM/Global_Data_Ingestion",
        "target_account": "GDP_Integration/Snowflake_GDP_Dev",
        "external_volume": "gdp_sf_dev_bronze",
        "base_location": "gdp_sf_dev_bronze/",
        "bronze_database": None,
        "load_type": "APPEND",
        "bronze_load_type": "SCD2",
        "load_frequency": "DAILY",
    },
    "child_pipeline_by_type": CHILD_PIPELINE_BY_TYPE,
}
DEFAULT_KEYS = tuple(DEFAULT_SETTING["defaults"])


# ---------------------------------------------------------------- the setting


def validate_setting(value: Any) -> Tuple[Dict[str, Any], List[str]]:
    """(cleaned SNAPLOGIC_SYNC setting, problems). The prefix must name DATABASE.SCHEMA.<table prefix> and never the
    live GDP_ tables."""
    problems: List[str] = []
    raw = value if isinstance(value, dict) else {}
    prefix = str(raw.get("table_prefix", DEFAULT_SETTING["table_prefix"]) or "").strip()
    if not PREFIX.match(prefix):
        problems.append("table_prefix must look like DATABASE.SCHEMA.PREFIX_ (for example DEV_GDP_UTIL_DB.CONFIG.AODS_)")
    elif prefix.split(".")[-1].upper() in ("", "GDP_"):
        problems.append("table_prefix must not name the live GDP_* tables (or no prefix at all)")
    defaults = {**DEFAULT_SETTING["defaults"], **{k: v for k, v in dict(raw.get("defaults") or {}).items() if k in DEFAULT_KEYS}}
    unknown = sorted(set(dict(raw.get("defaults") or {})) - set(DEFAULT_KEYS))
    if unknown:
        problems.append(f"unknown defaults: {', '.join(unknown)}")
    children = {str(k).upper(): str(v) for k, v in dict(raw.get("child_pipeline_by_type") or CHILD_PIPELINE_BY_TYPE).items() if v}
    return {"enabled": raw.get("enabled") is True, "table_prefix": prefix, "defaults": defaults,
            "child_pipeline_by_type": children}, problems


def tables(setting: Dict[str, Any]) -> Dict[str, str]:
    prefix = setting.get("table_prefix") or DEFAULT_SETTING["table_prefix"]
    return {"master": f"{prefix}{MASTER}", "detail": f"{prefix}{DETAIL}"}


def child_pipeline(source_type: str, setting: Dict[str, Any], given: Optional[str] = None) -> Optional[str]:
    """The child pipeline for a source type, unless the onboarding named one."""
    if given:
        return given
    kind = SOURCE_TYPE_ALIASES.get(source_type.upper(), source_type.upper())
    return (setting.get("child_pipeline_by_type") or CHILD_PIPELINE_BY_TYPE).get(kind)


def domain_label(name: Optional[str]) -> Optional[str]:
    """The live tables name a domain like a title ("Company"), not like a platform id ("COMPANY")."""
    if not name:
        return None
    return " ".join(w.capitalize() for w in re.split(r"[_\s]+", str(name).strip()) if w)


def created_at(now: Optional[datetime] = None) -> str:
    """The exact creation time new rows carry (microseconds, UTC): the writer reads their ids back by it."""
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S.%f")


# ---------------------------------------------------------------- rows


def to_master(header: Dict[str, Any], setting: Dict[str, Any], stamp: str) -> Dict[str, Any]:
    """The master row for a source: header is services.source.ingestion_control.validate's header."""
    source_type = SOURCE_TYPE_ALIASES.get(header["source_type"], header["source_type"])
    return {
        "SOURCE_SYSTEM": header["source_name"], "SOURCE_TYPE": source_type, "ACTIVE_FLAG": bool(header.get("is_active")),
        "CREATED_AT": stamp, "SCHEDULE_CRON": header.get("schedule_cron") or None,
        "SOURCE_SYSTEM_DESC": header.get("source_system_desc") or None,
        "DOMAIN": header.get("domain_label") or domain_label(header.get("domain_name")),
        "AIRFLOW_SOURCE_SYSTEM": header.get("airflow_source_system") or None,
        "SCHEDULE_FREQUENCY": header.get("schedule_frequency") or header.get("load_frequency") or None,
        "SILVER_COMMAND_NAME": header.get("silver_command_name") or None,
    }


def _text(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value)


def to_detail(row: Dict[str, Any], header: Dict[str, Any], master_config_id: int, setting: Dict[str, Any], user: str,
              stamp: str) -> Dict[str, Any]:
    """The detail row for one table: row is a CORE control row (ingestion_control.validate's rows)."""
    d = setting.get("defaults") or DEFAULT_SETTING["defaults"]
    source_type = SOURCE_TYPE_ALIASES.get(header["source_type"], header["source_type"])
    return {
        "MASTER_CONFIG_ID": int(master_config_id),
        "PARENT_PIPELINE_NAME": row.get("parent_pipeline_name") or d["parent_pipeline_name"],
        "CHILD_PIPELINE_NAME": child_pipeline(source_type, setting, row.get("child_pipeline_name")),
        "SNAPLOGIC_PROJECT_NAME": row.get("snaplogic_project_name") or d["snaplogic_project_name"],
        "SOURCE_ACCOUNT": row.get("source_account"), "SOURCE_DATABASE_PATH": row.get("source_database_path"),
        "SOURCE_SCHEMA_FILE": row.get("source_schema_file"), "SOURCE_TABLE_SQL": row.get("source_table_sql"),
        "SOURCE_FILE_TYPE": row.get("source_file_type") or "TABLE", "SOURCE_DELTA_KEY": row.get("source_delta_key"),
        "SOURCE_PRIMARY_KEYS": row.get("source_primary_keys"), "SOURCE_WHERE_CLAUSE": row.get("source_where_clause"),
        "TARGET_ACCOUNT": row.get("target_account") or d["target_account"],
        "LANDING_DATABASE": row.get("landing_database"), "LANDING_SCHEMA": row.get("landing_schema"),
        "LANDING_TABLE": row.get("landing_table"),
        "BRONZE_DATABASE": row.get("bronze_database") or header.get("bronze_database") or d.get("bronze_database"),
        "BRONZE_SCHEMA": row.get("bronze_schema"), "BRONZE_TABLE": row.get("bronze_table"),
        "TARGET_PREVIOUS_LOAD_TIME": _text(row.get("target_previous_load_time")),
        "LOAD_TYPE": row.get("load_type") or d["load_type"], "LOAD_FREQUENCY": row.get("load_frequency") or d["load_frequency"],
        "JOB_TRIGGER_TIME": None,                     # SnapLogic sets it when it runs; the cron is on the master row
        "API_URL": row.get("api_url"), "API_KEY": row.get("api_key"), "EXTERNAL_STAGE": row.get("external_stage"),
        "LAST_RUN_TIMESTAMP": _text(row.get("last_run_timestamp")), "IS_ACTIVE": bool(row.get("is_active")),
        "COMPLETION_FLAG": bool(row.get("completion_flag")),
        "LANDING_TO_BRONZE_FLAG": row.get("landing_to_bronze_flag") is not False,
        "CREATED_BY": user, "CREATED_AT": stamp, "UPDATED_AT": stamp, "COMMENTS": row.get("comments") or header.get("comments"),
        "SOURCE_AUDIT_COLUMNS": row.get("source_audit_columns"),
        "BRONZE_LOAD_TYPE": row.get("bronze_load_type") or d["bronze_load_type"],
        "SOURCE_DELETE_ACTION": row.get("source_delete_action") or "NOT_REQUIRED",
        "SOURCE_DELETE_KEY": row.get("source_delete_key"), "SOURCE_DELETE_KEY_VALUE": row.get("source_delete_key_value"),
        "SOURCE_DELETE_QUERY": row.get("source_delete_query"), "SOURCE_SHEET_NAME": row.get("source_sheet_name"),
        "CONFIG_TABLE": tables(setting)["detail"],
        "EXTERNAL_VOLUME": row.get("external_volume") or d["external_volume"],
        "BASE_LOCATION": row.get("base_location") or d["base_location"],
    }


def edit_values(row: Dict[str, Any]) -> Dict[str, Any]:
    """The detail columns an edit of a CORE control row changes."""
    out = {}
    for col in EDIT_COLUMNS:
        value = row.get(col.lower())
        if col in ("TARGET_PREVIOUS_LOAD_TIME", "LAST_RUN_TIMESTAMP"):
            value = _text(value)
        out[col] = value
    return out


# ---------------------------------------------------------------- SQL


def _select(columns: Tuple[str, ...]) -> str:
    """One SELECT of binds; timestamps cast so TIMESTAMP_NTZ columns take the exact creation time."""
    parts = []
    for c in columns:
        parts.append("%s::TIMESTAMP_NTZ" if c in ("CREATED_AT", "UPDATED_AT", "LAST_RUN_TIMESTAMP") else "%s")
    return "SELECT " + ", ".join(parts)


def insert_master_sql(table: str) -> str:
    return f"INSERT INTO {table} ({', '.join(MASTER_COLUMNS)}) {_select(MASTER_COLUMNS)}"


def insert_detail_sql(table: str, rows: int) -> str:
    assert rows >= 1
    return f"INSERT INTO {table} ({', '.join(DETAIL_COLUMNS)}) " + " UNION ALL ".join([_select(DETAIL_COLUMNS)] * rows)


def master_params(master: Dict[str, Any]) -> List[Any]:
    return [master[c] for c in MASTER_COLUMNS]


def detail_params(details: List[Dict[str, Any]]) -> List[Any]:
    out: List[Any] = []
    for d in details:
        out += [d[c] for c in DETAIL_COLUMNS]
    return out


def update_detail_sql(table: str, columns: List[str]) -> str:
    sets = ", ".join(f"{c} = %s::TIMESTAMP_NTZ" if c == "LAST_RUN_TIMESTAMP" else f"{c} = %s" for c in columns)
    return f"UPDATE {table} SET {sets}, UPDATED_AT = CURRENT_TIMESTAMP()::TIMESTAMP_NTZ WHERE CHILD_CONFIG_ID = %s"
