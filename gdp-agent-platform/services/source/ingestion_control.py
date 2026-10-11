"""SnapLogic ingestion control (V044, PLATFORM_CONFIG.INGESTION_CONTROL_CONFIG): the external onboarding wizard's
answers, validated and turned into one control row per table (pure; the API writes them).

One onboarding is one source: a source block, a target block, a load block, a SnapLogic block and one or more
tables. Every table becomes a row sharing the source's MASTER_ID (taken once from SEQ_INGESTION_MASTER_ID), each with
its own CHILD_ID. The platform only writes the configuration; SnapLogic reads it and sets TARGET_PREVIOUS_LOAD_TIME,
LAST_RUN_TIMESTAMP and COMPLETION_FLAG after each run; the wizard asks for their starting values (a table's previous
load time is the watermark its first delta load starts from). API_KEY is a secret reference (the name of
the secret in the vault), never the token: a value that looks like a token is refused.

Errors are keyed by the answer's path ("source.source_sheet_name", "tables.1.source_table_sql") so the wizard can
show each one on its step and field.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from services.source.identifiers import landing_table_name

TABLE = "PLATFORM_CONFIG.INGESTION_CONTROL_CONFIG"
HISTORY = "PLATFORM_CONFIG.INGESTION_CONTROL_HISTORY"
SOURCE_TYPES = ("ORACLE", "SQLSERVER", "POSTGRES", "MYSQL", "SALESFORCE", "REST_API", "S3", "SFTP", "OTHER")
FILE_TYPES = ("TABLE", "CSV", "JSON", "PARQUET", "XLSX")
LOAD_TYPES = ("APPEND", "TRUNCATE", "SCD2")
BRONZE_LOAD_TYPES = ("TRUNCATE", "SCD2", "MERGE")
FREQUENCIES = ("HOURLY", "DAILY", "WEEKLY", "MONTHLY", "ON_DEMAND")
DELETE_ACTIONS = ("REQUIRED", "NOT_REQUIRED")
ACTIONS = ("CREATED", "UPDATED", "ACTIVATED", "DEACTIVATED")
MAX_TABLES = 200

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,127}$")
OBJECT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")
STAGE = re.compile(r"^@?[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}(/[^\s;']*)?$")
PIPELINE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-/]{0,255}$")
ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,255}$")
CRON = re.compile(r"^\s*(\S+\s+){4}\S+(\s+[A-Za-z_]+(?:/[A-Za-z_]+)*)?\s*$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
SECRET_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/\-]{0,255}$")
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
WRITES = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|CALL|EXECUTE|COPY|PUT|REMOVE)\b", re.I)


def _text(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _upper(value: Any, limit: int = 128) -> str:
    return _text(value, limit).upper()


def looks_like_token(value: str) -> bool:
    """A long run of letters and digits with no separators, or a known token prefix: someone pasted a secret."""
    v = value.strip()
    if re.match(r"^(ghp_|gho_|github_pat_|xox[abp]-|sk-|AKIA|eyJ)", v):
        return True
    return len(v) >= 32 and re.fullmatch(r"[A-Za-z0-9+/=_\-]+", v) is not None and not re.search(r"[./:]", v) \
        and sum(c.isdigit() for c in v) >= 4 and sum(c.isalpha() for c in v) >= 8


def one_statement(sql: str) -> bool:
    """No ';' outside string literals (a single trailing ';' is allowed and stripped)."""
    bare = STRING_LITERAL.sub("''", sql.strip())
    return ";" not in bare.rstrip().rstrip(";")


def _timestamp(value: Any) -> Tuple[Optional[str], bool]:
    """'YYYY-MM-DD[ HH:MM[:SS]]' normalised to 'YYYY-MM-DD HH:MM:SS'; (value or None, valid)."""
    v = str(value or "").strip().replace("T", " ")
    if not v:
        return None, True
    if not TIMESTAMP.match(v):
        return None, False
    if len(v) == 10:
        v += " 00:00"
    return (v + ":00" if len(v) == 16 else v), True


def _keys(value: Any) -> Tuple[str, bool]:
    """A comma list of column names, normalised; (text, valid)."""
    parts = [p.strip() for p in str(value or "").split(",") if p.strip()]
    return ", ".join(p.upper() for p in parts), all(IDENT.match(p) for p in parts)


def _object_name(sql: str, file_type: str, schema_file: str) -> str:
    """The table or file a row reads, for its default landing table name."""
    plain = sql.strip().rstrip(";").strip()
    if OBJECT.match(plain):
        return plain.split(".")[-1]
    m = re.search(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_$.\"]*)", STRING_LITERAL.sub("''", plain), re.I)
    if m:
        return m.group(1).replace('"', "").split(".")[-1]
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", schema_file.split("/")[-1]) if file_type != "TABLE" else ""
    return stem or "DATA"


def validate(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, str]]:
    """(header, rows, errors). The header holds the source-level answers (for the catalog row); each row is the
    control table's columns for one table, without MASTER_ID, CHILD_ID or the audit columns."""
    errors: Dict[str, str] = {}
    src = dict(payload.get("source") or {})
    tgt = dict(payload.get("target") or {})
    load = dict(payload.get("load") or {})
    snap = dict(payload.get("snaplogic") or {})
    tables = list(payload.get("tables") or [])

    def need(path: str, value: str, label: str) -> None:
        if not value:
            errors[path] = f"{label} is required"

    # source
    source_name = _upper(src.get("source_name"), 64)
    need("source.source_name", source_name, "Source name")
    if source_name and not NAME.match(source_name):
        errors["source.source_name"] = "Letters, digits and _; starts with a letter"
    source_type = _upper(src.get("source_type"), 32) or "OTHER"
    if source_type not in SOURCE_TYPES:
        errors["source.source_type"] = f"Choose one of {', '.join(SOURCE_TYPES)}"
    file_type = _upper(src.get("source_file_type"), 64) or "TABLE"
    if file_type not in FILE_TYPES:
        errors["source.source_file_type"] = f"Choose one of {', '.join(FILE_TYPES)}"
    sheet = _text(src.get("source_sheet_name"), 128)
    if file_type == "XLSX" and not sheet:
        errors["source.source_sheet_name"] = "Sheet name is required for an Excel workbook"
    if file_type != "XLSX":
        sheet = ""
    source_account = _text(src.get("source_account"), 256)
    if source_account and not ACCOUNT.match(source_account):
        errors["source.source_account"] = "Letters, digits, spaces, _ . and -"
    schema_file = _text(src.get("source_schema_file"), 256)
    db_path = _text(src.get("source_database_path"), 512)

    # target
    landing_db = _upper(tgt.get("landing_database"))
    landing_schema = _upper(tgt.get("landing_schema"))
    bronze_schema = _upper(tgt.get("bronze_schema"))
    for path, value, label in (("target.landing_database", landing_db, "Landing database"),
                               ("target.landing_schema", landing_schema, "Landing schema"),
                               ("target.bronze_schema", bronze_schema, "Bronze schema")):
        need(path, value, label)
        if value and not IDENT.match(value):
            errors[path] = f"{label} must be a plain Snowflake name"
    stage = _text(tgt.get("external_stage"), 256)
    if stage and not STAGE.match(stage):
        errors["target.external_stage"] = "A stage name such as @LANDING.EXT_STAGE/path"
    iceberg = bool(tgt.get("iceberg"))
    volume = _upper(tgt.get("external_volume"), 256) if iceberg else ""
    base_location = _text(tgt.get("base_location"), 512) if iceberg else ""
    if iceberg and not volume:
        errors["target.external_volume"] = "An Iceberg table needs an external volume"
    if volume and not IDENT.match(volume):
        errors["target.external_volume"] = "A plain Snowflake name"
    if base_location and (" " in base_location or "'" in base_location):
        errors["target.base_location"] = "A storage path without spaces or quotes"
    config_table = _upper(tgt.get("config_table"), 256)
    if config_table and not OBJECT.match(config_table):
        errors["target.config_table"] = "A table name such as DB.SCHEMA.TABLE"
    target_account = _text(tgt.get("target_account"), 256)
    if target_account and not ACCOUNT.match(target_account):
        errors["target.target_account"] = "Letters, digits, spaces, _ . and -"

    # load
    load_type = _upper(load.get("load_type"), 32)
    if load_type not in LOAD_TYPES:
        errors["load.load_type"] = f"Choose one of {', '.join(LOAD_TYPES)}"
    bronze_load = _upper(load.get("bronze_load_type"), 32)
    if bronze_load not in BRONZE_LOAD_TYPES:
        errors["load.bronze_load_type"] = f"Choose one of {', '.join(BRONZE_LOAD_TYPES)}"
    frequency = _upper(load.get("load_frequency"), 64)
    if frequency not in FREQUENCIES:
        errors["load.load_frequency"] = f"Choose one of {', '.join(FREQUENCIES)}"
    trigger = _text(load.get("job_trigger_time"), 64)
    if frequency == "ON_DEMAND":
        trigger = ""
    elif not trigger:
        errors["load.job_trigger_time"] = "A cron expression, such as 0 2 * * * UTC"
    elif not CRON.match(trigger):
        errors["load.job_trigger_time"] = "Five cron fields and an optional time zone, such as 0 2 * * * UTC"
    landing_to_bronze = load.get("landing_to_bronze_flag", True) is not False
    is_active = load.get("is_active", True) is not False
    completion = load.get("completion_flag", False) is True
    last_run, ok = _timestamp(load.get("last_run_timestamp"))
    if not ok:
        errors["load.last_run_timestamp"] = "A date and time, such as 2026-10-01 02:00"

    # SnapLogic
    project = _text(snap.get("snaplogic_project_name"), 256)
    parent = _text(snap.get("parent_pipeline_name"), 256)
    child = _text(snap.get("child_pipeline_name"), 256)
    for path, value, label in (("snaplogic.snaplogic_project_name", project, "SnapLogic project"),
                               ("snaplogic.parent_pipeline_name", parent, "Parent pipeline"),
                               ("snaplogic.child_pipeline_name", child, "Child pipeline")):
        need(path, value, label)
        if value and not PIPELINE.match(value):
            errors[path] = f"{label}: letters, digits, spaces, _ . - and /"
    api_url = _text(snap.get("api_url"), 1024)
    if api_url and not re.match(r"^https://[A-Za-z0-9.\-]+(:\d+)?(/[^\s'\"]*)?$", api_url):
        errors["snaplogic.api_url"] = "An https:// URL"
    api_key = _text(snap.get("api_key"), 512)
    if api_key and looks_like_token(api_key):
        errors["snaplogic.api_key"] = "This looks like a token. Enter the name of the secret in your vault, never the token itself"
    elif api_key and not SECRET_REF.match(api_key):
        errors["snaplogic.api_key"] = "A secret name or path, such as vault:snaplogic/ingestion"

    # tables
    if not tables:
        errors["tables"] = "Add at least one table"
    if len(tables) > MAX_TABLES:
        errors["tables"] = f"At most {MAX_TABLES} tables in one onboarding"
    rows: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for i, t in enumerate(tables[:MAX_TABLES]):
        p = f"tables.{i}"
        sql = _text(t.get("source_table_sql"), 100_000).rstrip().rstrip(";").rstrip()
        if not sql:
            errors[f"{p}.source_table_sql"] = "The source table or query is required"
        elif not one_statement(sql):
            errors[f"{p}.source_table_sql"] = "One table name or one query, without ';'"
        elif not OBJECT.match(sql) and (not re.match(r"^\s*(SELECT|WITH)\b", sql, re.I) or WRITES.search(STRING_LITERAL.sub("''", sql))):
            errors[f"{p}.source_table_sql"] = "A table name or a read-only SELECT query"
        where = _text(t.get("source_where_clause"), 100_000)
        where = re.sub(r"^\s*WHERE\s+", "", where, flags=re.I)
        if where and (not one_statement(where) or WRITES.search(STRING_LITERAL.sub("''", where))):
            errors[f"{p}.source_where_clause"] = "One condition, without ';' or statements"
        delta = _upper(t.get("source_delta_key"))
        if delta and not IDENT.match(delta):
            errors[f"{p}.source_delta_key"] = "A column name"
        pks, ok = _keys(t.get("source_primary_keys"))
        if not ok:
            errors[f"{p}.source_primary_keys"] = "Column names separated by commas"
        audit, ok = _keys(t.get("source_audit_columns"))
        if not ok:
            errors[f"{p}.source_audit_columns"] = "Column names separated by commas"
        if load_type == "SCD2" or bronze_load in ("SCD2", "MERGE"):
            if not pks:
                errors[f"{p}.source_primary_keys"] = f"{'SCD2' if 'SCD2' in (load_type, bronze_load) else 'MERGE'} needs the primary key columns"
        obj = _object_name(sql, file_type, schema_file) if sql else "DATA"
        landing = _upper(t.get("landing_table")) or (landing_table_name(source_name, obj)[:128] if source_name else "")
        bronze = _upper(t.get("bronze_table")) or landing
        for path, value, label in ((f"{p}.landing_table", landing, "Landing table"), (f"{p}.bronze_table", bronze, "Bronze table")):
            if value and not IDENT.match(value):
                errors[path] = f"{label} must be a plain Snowflake name"
        if landing:
            if landing in seen:
                errors[f"{p}.landing_table"] = f"Table {seen[landing] + 1} already loads {landing}"
            seen.setdefault(landing, i)
        watermark, ok = _timestamp(t.get("target_previous_load_time"))
        if not ok:
            errors[f"{p}.target_previous_load_time"] = "A date and time, such as 2026-10-01 00:00"
        deletes = bool(t.get("deletes")) or _upper(t.get("source_delete_action"), 32) == "REQUIRED"
        dkey = _upper(t.get("source_delete_key")) if deletes else ""
        dvalue = _text(t.get("source_delete_key_value"), 256) if deletes else ""
        dquery = _text(t.get("source_delete_query"), 100_000).rstrip().rstrip(";").rstrip() if deletes else ""
        if deletes:
            if not dkey:
                errors[f"{p}.source_delete_key"] = "The column that marks a deleted row"
            elif not IDENT.match(dkey):
                errors[f"{p}.source_delete_key"] = "A column name"
            if not dvalue and not dquery:
                errors[f"{p}.source_delete_key_value"] = "The value that marks a deleted row, or a delete query"
            if dquery and not one_statement(dquery):
                errors[f"{p}.source_delete_query"] = "One query, without ';'"
        rows.append({
            "parent_pipeline_name": parent, "child_pipeline_name": child, "snaplogic_project_name": project,
            "source_account": source_account or None, "source_database_path": db_path or None,
            "source_schema_file": schema_file or None, "source_table_sql": sql, "source_file_type": file_type,
            "source_sheet_name": sheet or None, "source_delta_key": delta or None, "source_primary_keys": pks or None,
            "source_where_clause": where or None, "source_audit_columns": audit or None,
            "target_account": target_account or None, "landing_database": landing_db, "landing_schema": landing_schema,
            "landing_table": landing, "bronze_schema": bronze_schema, "bronze_table": bronze,
            "external_stage": stage or None, "external_volume": volume or None, "base_location": base_location or None,
            "config_table": config_table or None, "load_type": load_type, "bronze_load_type": bronze_load,
            "load_frequency": frequency, "job_trigger_time": trigger or None, "target_previous_load_time": watermark,
            "last_run_timestamp": last_run, "landing_to_bronze_flag": landing_to_bronze, "is_active": is_active,
            "completion_flag": completion, "source_delete_action": "REQUIRED" if deletes else "NOT_REQUIRED",
            "source_delete_key": dkey or None, "source_delete_key_value": dvalue or None,
            "source_delete_query": dquery or None, "api_url": api_url or None, "api_key": api_key or None,
        })
    header = {"source_name": source_name, "source_type": source_type, "domain_id": _text(src.get("domain_id"), 36) or None,
              "comments": _text(payload.get("comments"), 4000) or None, "parent_pipeline_name": parent,
              "landing_database": landing_db, "landing_schema": landing_schema, "load_frequency": frequency,
              "job_trigger_time": trigger or None, "is_active": is_active}
    return header, rows, errors


COLUMNS = ("parent_pipeline_name", "child_pipeline_name", "snaplogic_project_name", "source_account", "source_database_path",
           "source_schema_file", "source_table_sql", "source_file_type", "source_sheet_name", "source_delta_key",
           "source_primary_keys", "source_where_clause", "source_audit_columns", "target_account", "landing_database",
           "landing_schema", "landing_table", "bronze_schema", "bronze_table", "external_stage", "external_volume",
           "base_location", "config_table", "load_type", "bronze_load_type", "load_frequency", "job_trigger_time",
           "target_previous_load_time", "last_run_timestamp", "landing_to_bronze_flag", "is_active", "completion_flag",
           "source_delete_action", "source_delete_key", "source_delete_key_value",
           "source_delete_query", "api_url", "api_key")
# fields a person may edit afterwards (the pipeline names, source and target keep their identity)
EDITABLE = ("source_table_sql", "source_delta_key", "source_primary_keys", "source_where_clause", "source_audit_columns",
            "bronze_table", "load_type", "bronze_load_type", "load_frequency", "job_trigger_time", "landing_to_bronze_flag",
            "target_previous_load_time", "last_run_timestamp", "completion_flag",
            "source_delete_action", "source_delete_key", "source_delete_key_value", "source_delete_query", "api_url",
            "api_key", "external_stage", "comments")


def insert_sql(rows: int) -> str:
    """INSERT of `rows` control rows (%s binds): MASTER_ID bound, CHILD_ID from its sequence default."""
    cols = ("MASTER_ID", "SOURCE_ID", "DOMAIN_ID", *(c.upper() for c in COLUMNS), "CREATED_BY", "UPDATED_BY", "COMMENTS")
    one = "(" + ", ".join(["%s"] * len(cols)) + ")"
    return f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES " + ", ".join([one] * rows)


def insert_params(master_id: int, source_id: Optional[str], header: Dict[str, Any], rows: List[Dict[str, Any]],
                  user: str) -> List[Any]:
    out: List[Any] = []
    for r in rows:
        out += [int(master_id), source_id, header.get("domain_id"), *(r[c] for c in COLUMNS), user[:128], user[:128],
                header.get("comments")]
    return out


def edit_row(current: Dict[str, Any], changes: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """A control row with `changes` applied, validated as the wizard would: (new row, errors)."""
    unknown = sorted(set(changes) - set(EDITABLE) - {"is_active"})
    if unknown:
        return current, {k: "This field cannot be changed after onboarding" for k in unknown}
    merged = {k: current.get(k) for k in COLUMNS}
    merged.update({k: v for k, v in changes.items() if k in COLUMNS})
    payload = {
        "source": {"source_name": "X", "source_type": "OTHER", "source_file_type": merged["source_file_type"],
                   "source_sheet_name": merged["source_sheet_name"], "source_account": merged["source_account"],
                   "source_schema_file": merged["source_schema_file"], "source_database_path": merged["source_database_path"]},
        "target": {"landing_database": merged["landing_database"], "landing_schema": merged["landing_schema"],
                   "bronze_schema": merged["bronze_schema"], "external_stage": merged["external_stage"],
                   "iceberg": bool(merged["external_volume"]), "external_volume": merged["external_volume"],
                   "base_location": merged["base_location"], "config_table": merged["config_table"],
                   "target_account": merged["target_account"]},
        "load": {"load_type": merged["load_type"], "bronze_load_type": merged["bronze_load_type"],
                 "load_frequency": merged["load_frequency"], "job_trigger_time": merged["job_trigger_time"],
                 "landing_to_bronze_flag": merged["landing_to_bronze_flag"], "is_active": merged["is_active"],
                 "completion_flag": bool(merged["completion_flag"]),
                 "last_run_timestamp": str(merged["last_run_timestamp"])[:19] if merged["last_run_timestamp"] else None},
        "snaplogic": {"snaplogic_project_name": merged["snaplogic_project_name"],
                      "parent_pipeline_name": merged["parent_pipeline_name"],
                      "child_pipeline_name": merged["child_pipeline_name"], "api_url": merged["api_url"],
                      "api_key": merged["api_key"]},
        "tables": [{k: merged[k] for k in ("source_table_sql", "source_delta_key", "source_primary_keys", "source_where_clause",
                                           "source_audit_columns", "landing_table", "bronze_table", "source_delete_key",
                                           "source_delete_key_value", "source_delete_query")}
                   | {"target_previous_load_time": str(merged["target_previous_load_time"])[:19] if merged["target_previous_load_time"] else None}
                   | {"deletes": merged["source_delete_action"] == "REQUIRED"}],
    }
    _, rows, errors = validate(payload)
    errors = {k.replace("tables.0.", "").split(".")[-1]: v for k, v in errors.items()}
    row = dict(rows[0]) if rows else merged
    row["comments"] = changes.get("comments", current.get("comments"))
    if "is_active" in changes:
        row["is_active"] = changes["is_active"] is not False
    return row, errors


def action_for(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    if bool(before.get("is_active")) != bool(after.get("is_active")):
        return "ACTIVATED" if after.get("is_active") else "DEACTIVATED"
    return "UPDATED"
