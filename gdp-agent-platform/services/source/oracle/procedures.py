"""Oracle actions for a registered source, run the same way in either runtime.

  runtime "snowflake": the stored procedure <db>.EXT_<NAME>.ORACLE_RUN (EXECUTE AS OWNER, external access
                       integration + PASSWORD secret) calls `procedure_entry`; the password never leaves Snowflake.
  runtime "api_host":  the API calls `run` in its own process with a Snowpark session on the user's connection;
                       the password comes from the environment variable named in the source (`password_env`).

Either way the session uploads Parquet to the source's stage, lands with INFER_SCHEMA + COPY, stores profiles in
@METADATA.PROFILES_STAGE and records loads on the source, so the rest of the platform sees one behaviour.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from typing import Any, Callable, Dict, List, Optional

from services.common.sql import clip, rows, variant

SECRET_ALIAS = "oracle_login"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")
ACTIONS = ("test", "catalog", "columns", "profile", "extract")


# ---------------------------------------------------------------- Snowflake runtime: setup DDL


def object_names(database: str, schema: str) -> Dict[str, str]:
    return {"rule": f"{database}.{schema}.ORACLE_EGRESS", "secret": f"{database}.{schema}.ORACLE_LOGIN",
            "procedure": f"{database}.{schema}.ORACLE_RUN"}


def setup_sql(database: str, schema: str, eai: str, host: str, port: int, user: str) -> List[str]:
    """Network rule to host:port, the PASSWORD secret (password bound only at execution) and the integration."""
    names = object_names(database, schema)
    for value in (database, schema, eai):
        assert NAME.match(value or ""), f"unsafe identifier: {value}"
    assert re.match(r"^[A-Za-z0-9.\-]{1,253}$", host) and 0 < int(port) < 65536, "invalid host or port"
    assert re.match(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$", user), "invalid Oracle user"
    return [
        f"CREATE OR REPLACE NETWORK RULE {names['rule']} MODE = EGRESS TYPE = HOST_PORT "
        f"VALUE_LIST = ('{host}:{int(port)}')",
        f"CREATE OR REPLACE SECRET {names['secret']} TYPE = PASSWORD USERNAME = '{user}' PASSWORD = '<oracle password>'",
        f"CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION {eai} ALLOWED_NETWORK_RULES = ({names['rule']}) "
        f"ALLOWED_AUTHENTICATION_SECRETS = ({names['secret']}) ENABLED = TRUE "
        f"COMMENT = 'Agentic pipeline: read Oracle source {schema}'",
    ]


def procedure_sql(database: str, schema: str, services_import: str, eai: str) -> str:
    names = object_names(database, schema)
    for value in (database, schema, eai):
        assert NAME.match(value or ""), f"unsafe identifier: {value}"
    assert re.fullmatch(r"@[A-Za-z0-9_./$-]+\.zip", services_import or ""), f"unsafe import: {services_import}"
    return (
        f"CREATE OR REPLACE PROCEDURE {names['procedure']}(SOURCE_ID VARCHAR, ACTION VARCHAR, PAYLOAD_JSON VARCHAR)\n"
        "  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'\n"
        "  PACKAGES = ('snowflake-snowpark-python', 'oracledb', 'pyarrow')\n"
        f"  IMPORTS = ('{services_import}')\n"
        "  HANDLER = 'services.source.oracle.procedures.procedure_entry'\n"
        f"  EXTERNAL_ACCESS_INTEGRATIONS = ({eai})\n"
        f"  SECRETS = ('{SECRET_ALIAS}' = {names['secret']})\n"
        f"  COMMENT = 'Agentic pipeline: Oracle source {schema} (test, catalog, profile, extract and land)'\n"
        "  EXECUTE AS OWNER"
    )


def procedure_entry(session, source_id: str, action: str, payload_json: str) -> Dict[str, Any]:
    import _snowflake  # available inside Snowflake procedures only

    login = _snowflake.get_username_password(SECRET_ALIAS)
    return run(session, source_id, action, payload_json, login.password)


# ---------------------------------------------------------------- shared behaviour


def source(session, source_id: str) -> Dict[str, Any]:
    found = rows(session, "SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, CONNECTION_TYPE, CONFIGURATION_JSON "
                          "FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = ? AND ACTIVE_FLAG", [source_id])
    assert found, f"source {source_id} not found"
    assert found[0]["CONNECTION_TYPE"] == "oracle", f"{found[0]['SOURCE_SYSTEM_NAME']} is not an Oracle source"
    return {"id": source_id, "name": found[0]["SOURCE_SYSTEM_NAME"], "config": variant(found[0]["CONFIGURATION_JSON"]) or {}}


def api_host_password(config: Dict[str, Any]) -> str:
    var = config.get("password_env")
    assert var, "this source has no password_env; set the environment variable name on the source"
    value = os.environ.get(var)
    assert value, f"environment variable {var} is not set on the API host"
    return value


def _connect(config: Dict[str, Any], password: str):
    from services.source.oracle.connect import OracleConfig, connect

    return connect(OracleConfig.from_dict(config), password)


def _save_config(session, source_id: str, config: Dict[str, Any]) -> None:
    session.sql("UPDATE SOURCE.SOURCE_REGISTRY SET CONFIGURATION_JSON = PARSE_JSON(?), "
                "UPDATED_AT = CURRENT_TIMESTAMP() WHERE SOURCE_SYSTEM_ID = ?",
                params=[json.dumps(config, default=str), source_id]).collect()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def run(session, source_id: str, action: str, payload_json: str, password: str,
        progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    from services.source.oracle import catalog

    assert action in ACTIONS, f"action must be one of {ACTIONS}"
    payload = json.loads(payload_json or "{}")
    src = source(session, source_id)
    cfg = src["config"]
    conn = _connect(cfg, password)
    try:
        owner = cfg.get("schema_owner") or cfg["user"]
        if action == "test":
            return catalog.test_connection(conn, owner)
        if action == "catalog":
            return {"tables": catalog.fetch_catalog(conn, owner)}
        tables = [str(t) for t in payload.get("tables") or []]
        assert tables, "choose at least one table"
        columns = catalog.inspect_columns(conn, owner, tables)
        missing = [t for t in tables if not columns.get(t)]
        assert not missing, f"not found or no visible columns in {owner}: {missing[:5]}"
        if action == "columns":
            return {"columns": columns}
        estimates = {t["table"]: t["estimated_rows"] for t in catalog.fetch_catalog(conn, owner)}
        if action == "profile":
            return {"tables": [_profile_one(session, src, conn, owner, t, columns[t], estimates.get(t)) for t in tables]}
        return {"tables": [_extract_one(session, src, conn, owner, t, columns[t], payload, progress) for t in tables]}
    finally:
        conn.close()


def _profile_ref(src: Dict[str, Any], owner: str, table: str, columns: List[Dict[str, Any]],
                 row_count: Optional[int]):
    from services.profiling.procedures import TableRef

    cfg = src["config"]
    return TableRef(source_name=f"ORACLE_{src['name']}", database=(cfg.get("service_name") or cfg.get("sid") or "ORACLE").upper(),
                    schema=owner, table=table, landing_fqn="",
                    row_count=row_count, columns=tuple((c["column_name"], c["oracle_type"]) for c in columns))


def _profile_one(session, src, conn, owner: str, table: str, columns: List[Dict[str, Any]],
                 estimated_rows: Optional[int]) -> Dict[str, Any]:
    """Profile in Oracle and store the document like any other profile (source ORACLE_<NAME>)."""
    from services.profiling import profiler
    from services.profiling.procedures import _ensure_index_rows, _set_status, _upsert_index, _write_document
    from services.source.oracle.profile import profile_table

    ref = _profile_ref(src, owner, table, columns, estimated_rows)
    _ensure_index_rows(session, [ref])
    _set_status(session, [ref], "PROFILING")
    try:
        built, row_count, approximate = profile_table(conn, owner, table, columns, estimated_rows)
        fingerprint = profiler.source_fingerprint(ref.columns, row_count)
        document = profiler.profile_document(
            {"source_name": ref.source_name, "database": ref.database, "schema": owner, "table": table},
            row_count, built, fingerprint, approximate, None, _now())
        document["origin"] = "oracle_in_place"
        path = profiler.profile_stage_path(ref.source_name, ref.database, owner, table)
        _write_document(session, path, document)
        _upsert_index(session, ref, document, None)
        _set_status(session, [ref], "STAGED_READY_FOR_MODELING")
        return {"table": table, "status": "PROFILED", "row_count": row_count, "approximate": approximate,
                "columns": len(built), "stage_path": path,
                "pii_columns": [c["column_name"] for c in built if c.get("pii_classification", "NONE") != "NONE"],
                "key_candidates": [c["column_name"] for c in built if c.get("potential_key")]}
    except Exception as exc:
        _set_status(session, [ref], "FAILED", str(exc))
        return {"table": table, "status": "FAILED", "error": clip(exc, 600)}


def landed_table_name(table: str) -> str:
    from services.source.oracle.extract import snowflake_names

    return snowflake_names([table])[table]


def _table_exists(session, database: str, schema: str, table: str) -> bool:
    from services.source.identifiers import quote

    return bool(rows(session, f"SELECT 1 FROM {quote(database)}.INFORMATION_SCHEMA.TABLES "
                              "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? LIMIT 1", [schema, table]))


def _extract_one(session, src, conn, owner: str, table: str, columns: List[Dict[str, Any]],
                 payload: Dict[str, Any], progress) -> Dict[str, Any]:
    """Oracle -> Parquet parts on the source stage -> landed table (created once, then replaced or appended)."""
    from services.source.external import land_parquet_sql, stage_name
    from services.source.oracle.extract import extract_table

    cfg = src["config"]
    database, schema = cfg["database"], cfg["schema"]
    target = landed_table_name(table)
    mode = str(payload.get("mode") or "replace").lower()
    loads = dict(cfg.get("loads") or {})
    prior = dict(loads.get(target) or {})
    wm_col = payload.get("watermark_columns", {}).get(table) or (prior.get("watermark_column") if mode == "append" else None)
    batch = dt.datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
    prefix = f"{target}/{batch}"
    stage = stage_name(database, schema)
    out: Dict[str, Any] = {"table": table, "landed_as": f"{database}.{schema}.{target}", "status": "FAILED",
                           "batch_id": batch, "mode": mode}

    def put(local_path: str, name: str) -> None:
        session.file.put(local_path, f"@{stage}/{prefix}", auto_compress=False, overwrite=True)
        if progress:
            progress({"table": table, "phase": "UPLOADING", "file": name})

    try:
        if progress:
            progress({"table": table, "phase": "EXTRACTING"})
        result = extract_table(conn, owner, table, columns, source_system=f"ORACLE:{src['name']}", batch_id=batch,
                               put=put, watermark_column=wm_col,
                               watermark_value=prior.get("watermark") if mode == "append" and wm_col else None,
                               progress=(lambda p: progress({"table": table, "phase": "EXTRACTING", **p}))
                               if progress else None)
        out.update(rows_extracted=result["rows"], files=len(result["files"]), columns=result["columns"],
                   text_numbers=result["text_numbers"], notes=result["notes"])
        if result["files"]:
            if progress:
                progress({"table": table, "phase": "LANDING"})
            exists = _table_exists(session, database, schema, target)
            volume = None
            if str(payload.get("storage") or "MANAGED").upper() == "ICEBERG":
                found = rows(session, "SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = "
                                      "'LANDING_EXTERNAL_VOLUME' AND IS_CURRENT ORDER BY VERSION DESC LIMIT 1")
                volume = variant(found[0]["CONFIG_VALUE"]) if found else None
                assert volume, "Iceberg landing needs LANDING_EXTERNAL_VOLUME configured by an admin"
            loaded = 0
            for sql in land_parquet_sql(database, schema, target, prefix, mode, exists, volume):
                result_rows = [r.as_dict() for r in session.sql(sql).collect()]
                if sql.startswith("COPY"):
                    loaded = sum(int(r.get("rows_loaded") or r.get("ROWS_LOADED") or 0) for r in result_rows)
            out["rows_loaded"] = loaded
            session.sql(f"REMOVE @{stage}/{prefix}/").collect()  # landed; the rows keep the file name
        else:
            out["rows_loaded"] = 0
            out["note"] = "no new rows since the last load" if mode == "append" else "the table is empty"
        out["status"] = "LANDED"
        loads[target] = {"oracle_table": f"{owner}.{table}", "batch_id": batch, "mode": mode, "at": _now(),
                         "rows": out["rows_loaded"], "watermark_column": wm_col,
                         "watermark": result.get("watermark") if wm_col else None, "columns": result["columns"]}
        landed = sorted(set(cfg.get("landed_tables") or []) | {target})
        cfg.update(loads=loads, landed_tables=landed, last_landed_at=_now())
        _save_config(session, src["id"], cfg)
    except Exception as exc:
        out["error"] = clip(exc, 800)
    return out
