"""External sources: register non-Snowflake systems and land file sources into Snowflake.

Snowflake cannot read an external system in place, so an external source is landed first: files from cloud
storage or an upload go through a stage, INFER_SCHEMA and COPY INTO a schema of their own
(<platform db>.EXT_<SOURCE>). From then on the landed tables are profiled and modeled like any Snowflake
table. Database, SaaS and API connectors are registered with their connection metadata and land through a
Snowflake connector the account administrator enables; this module never stores credentials, only the name
of a Snowflake SECRET or STORAGE INTEGRATION that holds them.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from services.common.sql import insert_rows, rows, variant
from services.source.identifiers import SOURCE_SYSTEM_NAME, fqn

OBJECT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")
SAFE_PATTERN = re.compile(r"^[A-Za-z0-9_.*+?\\/\-\[\]()|^$]{1,200}$")
HOST = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")
FILE_FORMATS = {
    "CSV": "TYPE = CSV PARSE_HEADER = TRUE FIELD_OPTIONALLY_ENCLOSED_BY = '\"' "
           "ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE EMPTY_FIELD_AS_NULL = TRUE TRIM_SPACE = TRUE",
    "PARQUET": "TYPE = PARQUET",
    "JSON": "TYPE = JSON STRIP_OUTER_ARRAY = TRUE",
}
MAX_FILES = 200

CONNECTORS: Dict[str, Dict[str, Any]] = {
    "s3": {"label": "Amazon S3", "kind": "FILE", "landable": True, "url_prefix": "s3://",
           "fields": ["url", "storage_integration", "file_format", "pattern"]},
    "azure": {"label": "Azure Blob / ADLS Gen2", "kind": "FILE", "landable": True, "url_prefix": "azure://",
              "fields": ["url", "storage_integration", "file_format", "pattern"]},
    "gcs": {"label": "Google Cloud Storage", "kind": "FILE", "landable": True, "url_prefix": "gcs://",
            "fields": ["url", "storage_integration", "file_format", "pattern"]},
    "upload": {"label": "File upload (CSV, Parquet, JSON)", "kind": "FILE", "landable": True,
               "fields": ["file_format"]},
    "postgres": {"label": "PostgreSQL", "kind": "DATABASE", "landable": False,
                 "fields": ["host", "port", "database", "schema", "secret"]},
    "sqlserver": {"label": "SQL Server", "kind": "DATABASE", "landable": False,
                  "fields": ["host", "port", "database", "schema", "secret"]},
    "oracle": {"label": "Oracle", "kind": "DATABASE", "landable": False,
               "fields": ["host", "port", "database", "schema", "secret"]},
    "mysql": {"label": "MySQL", "kind": "DATABASE", "landable": False,
              "fields": ["host", "port", "database", "schema", "secret"]},
    "salesforce": {"label": "Salesforce", "kind": "SAAS", "landable": False, "fields": ["instance_url", "secret"]},
    "rest_api": {"label": "REST API", "kind": "API", "landable": False, "fields": ["base_url", "secret"]},
}
LANDING_GUIDANCE = {
    "DATABASE": "Land it with the Snowflake connector for this database (Openflow) or a CDC tool into a Snowflake "
                "schema, then profile that schema in Sources.",
    "SAAS": "Land it with the Snowflake native connector or Openflow into a Snowflake schema, then profile it in Sources.",
    "API": "Land it with an Openflow flow or an external access function into a Snowflake schema, then profile it.",
}


def connector_catalog() -> List[Dict[str, Any]]:
    return [{"id": k, **{f: v[f] for f in ("label", "kind", "landable", "fields")},
             "guidance": None if v["landable"] else LANDING_GUIDANCE[v["kind"]]} for k, v in CONNECTORS.items()]


def source_type_for(connector: str) -> str:
    return f"EXTERNAL_{CONNECTORS[connector]['kind']}"


def is_external(source_type: Optional[str]) -> bool:
    return str(source_type or "").upper().startswith("EXTERNAL_")


def landing_schema(source_name: str) -> str:
    return f"EXT_{source_name.upper()}"[:255]


def validate_config(connector: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Non-secret settings only. Credentials live in a Snowflake SECRET or STORAGE INTEGRATION named here."""
    assert connector in CONNECTORS, f"connector must be one of {sorted(CONNECTORS)}"
    spec = CONNECTORS[connector]
    unknown = set(config) - set(spec["fields"])
    assert not unknown, f"unknown fields for {connector}: {sorted(unknown)}"
    clean: Dict[str, Any] = {}
    for key, value in config.items():
        if value in (None, ""):
            continue
        value = str(value).strip()
        if key == "url":
            assert value.lower().startswith(spec["url_prefix"]), f"url must start with {spec['url_prefix']}"
            assert "'" not in value and " " not in value and len(value) <= 1024, "invalid url"
        elif key in ("storage_integration", "secret"):
            assert OBJECT_NAME.match(value), f"{key} must be a Snowflake object name, not a credential"
        elif key == "file_format":
            value = value.upper()
            assert value in FILE_FORMATS, f"file_format must be one of {sorted(FILE_FORMATS)}"
        elif key == "pattern":
            assert SAFE_PATTERN.match(value), "pattern may use letters, digits and regex characters only"
        elif key == "host":
            assert HOST.match(value), "host must be a host name or IP address"
        elif key == "port":
            assert value.isdigit() and 0 < int(value) < 65536, "port must be 1-65535"
        elif key in ("instance_url", "base_url"):
            assert value.lower().startswith("https://") and "'" not in value, f"{key} must be an https URL"
        elif key in ("database", "schema"):
            assert re.match(r"^[A-Za-z0-9_$\-]{1,255}$", value), f"{key} has invalid characters"
        clean[key] = value
    if connector in ("s3", "azure", "gcs"):
        assert clean.get("url"), "url is required"
        assert clean.get("storage_integration"), "storage_integration is required: an admin creates it once"
    if spec["kind"] == "FILE":
        clean.setdefault("file_format", "CSV")
    return clean


def table_name_for(file_path: str) -> str:
    """orders/2024/Orders-Jan.csv.gz -> ORDERS_JAN."""
    stem = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    stem = re.sub(r"(\.(gz|bz2|zst|snappy))$", "", stem, flags=re.I)
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", stem)
    name = re.sub(r"[^A-Z0-9_]", "_", stem.upper()).strip("_") or "FILE"
    return name if re.match(r"^[A-Z_]", name) else f"T_{name}"


def stage_name(database: str, schema: str) -> str:
    return fqn(database, schema, "FILES")


def format_name(database: str, schema: str, file_format: str) -> str:
    return fqn(database, schema, f"FF_{file_format}")


def setup_sql(database: str, schema: str, connector: str, config: Dict[str, Any]) -> List[str]:
    """Schema, file format and stage that hold the source's files. Idempotent."""
    file_format = config.get("file_format", "CSV")
    stage = stage_name(database, schema)
    statements = [
        f"CREATE SCHEMA IF NOT EXISTS {fqn(database, schema)} COMMENT = 'Landed external source {schema}'",
        f"CREATE FILE FORMAT IF NOT EXISTS {format_name(database, schema, file_format)} {FILE_FORMATS[file_format]}",
    ]
    if connector == "upload":
        statements.append(f"CREATE STAGE IF NOT EXISTS {stage} DIRECTORY = (ENABLE = TRUE) "
                          "ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')")
    else:
        statements.append(f"CREATE STAGE IF NOT EXISTS {stage} URL = '{config['url']}' "
                          f"STORAGE_INTEGRATION = {config['storage_integration']}")
    return statements


def staged_file(name: str, stage_url_or_prefix: str) -> str:
    """LIST returns full URLs (s3://bucket/path/file) or stage paths (files/path); keep the path inside the stage."""
    path = name.replace("\\", "/")
    for prefix in (stage_url_or_prefix.rstrip("/") + "/", "files/"):
        if prefix and path.lower().startswith(prefix.lower()):
            return path[len(prefix):]
    return path.split("/", 3)[-1] if "://" in path else path


def land_sql(database: str, schema: str, file_format: str, table: str, files: List[str]) -> List[str]:
    """Replace the table with the files' inferred shape and load them by column name."""
    stage = stage_name(database, schema)
    fmt = format_name(database, schema, file_format)
    target = fqn(database, schema, table)
    file_list = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
    first = files[0].replace("'", "''")
    return [
        # Header names become plain upper-case identifiers so downstream SQL and dbt never need quoting;
        # COPY matches them back to the file headers case-insensitively.
        f"CREATE OR REPLACE TABLE {target} USING TEMPLATE (SELECT ARRAY_AGG(OBJECT_CONSTRUCT("
        f"'COLUMN_NAME', UPPER(REGEXP_REPLACE(COLUMN_NAME, '[^A-Za-z0-9_]', '_')), 'TYPE', TYPE, "
        f"'NULLABLE', NULLABLE)) WITHIN GROUP (ORDER BY ORDER_ID) FROM TABLE(INFER_SCHEMA("
        f"LOCATION => '@{stage}/{first}', FILE_FORMAT => '{fmt}')))",
        f"COPY INTO {target} FROM @{stage} FILES = ({file_list}) FILE_FORMAT = (FORMAT_NAME = '{fmt}') "
        "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = ABORT_STATEMENT",
    ]


def group_files(files: List[str], table: Optional[str] = None) -> Dict[str, List[str]]:
    """One table per file by default; an explicit table name loads every file into that table."""
    groups: Dict[str, List[str]] = {}
    for f in files:
        groups.setdefault(table or table_name_for(f), []).append(f)
    return groups


# ---------------------------------------------------------------- Snowpark handlers (caller's rights)


def _platform_database(session) -> str:
    db = rows(session, "SELECT CURRENT_DATABASE() AS D")[0]["D"]
    assert db, "no current database"
    return db


def register_external_source(session, payload_json: str) -> Dict[str, Any]:
    payload = json.loads(payload_json or "{}")
    connector = str(payload.get("connector") or "")
    assert connector in CONNECTORS, f"connector must be one of {sorted(CONNECTORS)}"
    name = str(payload.get("source_system_name") or "").strip()
    assert SOURCE_SYSTEM_NAME.match(name), "name must be a letter followed by up to 63 letters, digits or _"
    name = name.upper()
    config = validate_config(connector, payload.get("config") or {})
    existing = rows(session, "SELECT SOURCE_SYSTEM_ID FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_NAME = ? "
                             "AND ACTIVE_FLAG", [name])
    if existing:
        raise ValueError(f"SOURCE_NAME_CONFLICT: {name} is already registered")
    database = _platform_database(session)
    location = {"database": database, "schema": landing_schema(name)}
    stored = {**config, **location, "connector": connector, "landed_tables": [], "last_landed_at": None}
    reference = config.get("secret") or config.get("storage_integration") or ""
    source_id = str(uuid.uuid4())
    insert_rows(session, "SOURCE.SOURCE_REGISTRY",
                ["SOURCE_SYSTEM_ID", "SOURCE_SYSTEM_NAME", "SOURCE_TYPE", "OWNER", "CONNECTION_TYPE",
                 "SECURITY_CLASSIFICATION", "CONFIGURATION_REFERENCE", "CONFIGURATION_JSON", "CREATED_BY"],
                ["?", "?", "?", "NULLIF(?, '')", "?", "NULLIF(?, '')", "NULLIF(?, '')", "PARSE_JSON(?)",
                 "CURRENT_USER()"],
                [[source_id, name, source_type_for(connector), payload.get("owner"), connector,
                  payload.get("security_classification"), reference, json.dumps(stored)]])
    if CONNECTORS[connector]["landable"]:
        for sql in setup_sql(database, location["schema"], connector, config):
            session.sql(sql).collect()
    return {"source_system_id": source_id, "source_system_name": name, "source_type": source_type_for(connector),
            "landing": location, "landable": CONNECTORS[connector]["landable"],
            "guidance": None if CONNECTORS[connector]["landable"] else LANDING_GUIDANCE[CONNECTORS[connector]["kind"]]}


def _external_source(session, source_id: str) -> Dict[str, Any]:
    found = rows(session, "SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, SOURCE_TYPE, CONNECTION_TYPE, "
                          "CONFIGURATION_JSON FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = ? AND ACTIVE_FLAG",
                 [source_id])
    assert found, f"source {source_id} not found"
    src = found[0]
    assert is_external(src["SOURCE_TYPE"]), f"{src['SOURCE_SYSTEM_NAME']} is not an external source"
    src["CONFIG"] = variant(src["CONFIGURATION_JSON"]) or {}
    return src


def list_external_files(session, source_id: str) -> Dict[str, Any]:
    src = _external_source(session, source_id)
    cfg = src["CONFIG"]
    if not CONNECTORS[src["CONNECTION_TYPE"]]["landable"]:
        return {"files": [], "landable": False}
    stage = stage_name(cfg["database"], cfg["schema"])
    pattern = cfg.get("pattern")
    listed = rows(session, f"LIST @{stage}" + (f" PATTERN = '{pattern}'" if pattern else ""))
    prefix = cfg.get("url") or ""
    files = [{"path": staged_file(str(r.get("name")), prefix), "size": r.get("size"),
              "last_modified": str(r.get("last_modified") or "")} for r in listed][:MAX_FILES]
    return {"files": files, "landable": True}


def land_external_files(session, source_id: str, payload_json: str) -> Dict[str, Any]:
    """Load the chosen staged files (default: all) into tables of the source's landing schema."""
    payload = json.loads(payload_json or "{}")
    src = _external_source(session, source_id)
    cfg = src["CONFIG"]
    assert CONNECTORS[src["CONNECTION_TYPE"]]["landable"], LANDING_GUIDANCE.get(
        CONNECTORS[src["CONNECTION_TYPE"]]["kind"], "this source is landed outside the platform")
    available = {f["path"] for f in list_external_files(session, source_id)["files"]}
    files = payload.get("files") or sorted(available)
    assert files, "no files to land; upload or add files to the source location first"
    missing = [f for f in files if f not in available]
    assert not missing, f"not found on the stage: {missing[:5]}"
    table = payload.get("table")
    if table:
        assert re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$", table), "table must be a plain identifier"
        table = table.upper()
    results = []
    for name, group in group_files(sorted(files), table).items():
        out: Dict[str, Any] = {"table": name, "files": group, "status": "FAILED", "rows_loaded": 0, "error": None}
        try:
            statements = land_sql(cfg["database"], cfg["schema"], cfg.get("file_format", "CSV"), name, group)
            session.sql(statements[0]).collect()
            loaded = [r.as_dict() for r in session.sql(statements[1]).collect()]
            out["rows_loaded"] = sum(int(r.get("rows_loaded") or r.get("ROWS_LOADED") or 0) for r in loaded)
            out["status"] = "LOADED"
        except Exception as exc:
            out["error"] = str(exc)[:600]
        results.append(out)
    landed = sorted({r["table"] for r in results if r["status"] == "LOADED"} | set(cfg.get("landed_tables") or []))
    session.sql(
        "UPDATE SOURCE.SOURCE_REGISTRY SET CONFIGURATION_JSON = PARSE_JSON(?), UPDATED_AT = CURRENT_TIMESTAMP() "
        "WHERE SOURCE_SYSTEM_ID = ?",
        params=[json.dumps({**cfg, "landed_tables": landed,
                            "last_landed_at": rows(session, "SELECT CURRENT_TIMESTAMP()::VARCHAR AS T")[0]["T"]}),
                source_id],
    ).collect()
    return {"source_system_id": source_id, "database": cfg["database"], "schema": cfg["schema"], "tables": results}

