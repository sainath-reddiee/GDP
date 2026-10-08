"""Oracle actions for a registered source, run the same way in either runtime.

  runtime "snowflake": the stored procedure <db>.EXT_<NAME>.ORACLE_RUN (EXECUTE AS OWNER, external access
                       integration + PASSWORD secret) calls `procedure_entry`; the password never leaves Snowflake.
                       The secret and the integration are either created for the source or chosen from existing ones.
  runtime "api_host":  the API calls `run` in its own process with a Snowpark session on the user's connection;
                       the password comes from the environment variable named in the source (`password_env`).

Either way the session uploads Parquet to the source's stage, lands with INFER_SCHEMA + COPY (or MERGE), verifies
the landed row count, stores profiles in @METADATA.PROFILES_STAGE and records loads on the source, so the rest of the
platform sees one behaviour. Scheduled loads are Snowflake tasks calling the same procedure.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from services.common.sql import clip, rows, variant

SECRET_ALIAS = "oracle_login"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")
CRON = re.compile(r"^[0-9*/,\-]+ [0-9*/,\-]+ [0-9*/,\-?LW#]+ [0-9*/,\-A-Z]+ [0-9*/,\-A-Z?L#]+ [A-Za-z_/+\-]+$")
ACTIONS = ("test", "diagnose", "catalog", "columns", "preview", "profile", "extract")
HISTORY = 25


# ---------------------------------------------------------------- Snowflake runtime: setup DDL


def object_names(database: str, schema: str) -> Dict[str, str]:
    return {"rule": f"{database}.{schema}.ORACLE_EGRESS", "secret": f"{database}.{schema}.ORACLE_LOGIN",
            "procedure": f"{database}.{schema}.ORACLE_RUN", "task": f"{database}.{schema}.ORACLE_SCHEDULED_LOAD"}


def _check_name(value: str, what: str = "identifier") -> str:
    assert NAME.match(value or ""), f"unsafe {what}: {value}"
    return value


def setup_sql(database: str, schema: str, eai: str, host: str, port: int, user: str, *,
              secret: Optional[str] = None, create_secret: bool = True, create_integration: bool = True,
              allowed_secrets: Optional[List[str]] = None) -> List[str]:
    """What to create for the Snowflake runtime: the network rule to host:port and the integration (unless an existing
    integration is used), and the PASSWORD secret (unless an existing one is used; the password is bound only when
    the statement runs). Returns statements in order."""
    names = object_names(database, schema)
    for value in (database, schema, eai):
        _check_name(value)
    secret = _check_name(secret or names["secret"], "secret name")
    assert re.match(r"^[A-Za-z0-9.\-]{1,253}$", host) and 0 < int(port) < 65536, "invalid host or port"
    assert re.match(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$", user), "invalid Oracle user"
    out: List[str] = []
    if create_secret:
        out.append(f"CREATE OR REPLACE SECRET {secret} TYPE = PASSWORD USERNAME = '{user}' "
                   "PASSWORD = '<oracle password>' COMMENT = 'Agentic pipeline: Oracle login'")
    if create_integration:
        secrets = sorted({secret, *[_check_name(s, "secret name") for s in (allowed_secrets or [])]})
        out += [
            f"CREATE OR REPLACE NETWORK RULE {names['rule']} MODE = EGRESS TYPE = HOST_PORT "
            f"VALUE_LIST = ('{host}:{int(port)}') COMMENT = 'Agentic pipeline: Oracle listener'",
            f"CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION {eai} ALLOWED_NETWORK_RULES = ({names['rule']}) "
            f"ALLOWED_AUTHENTICATION_SECRETS = ({', '.join(secrets)}) ENABLED = TRUE "
            f"COMMENT = 'Agentic pipeline: read Oracle source {schema}'",
        ]
    return out


def procedure_sql(database: str, schema: str, services_import: str, eai: str, secret: Optional[str] = None) -> str:
    names = object_names(database, schema)
    for value in (database, schema, eai):
        _check_name(value)
    secret = _check_name(secret or names["secret"], "secret name")
    assert re.fullmatch(r"@[A-Za-z0-9_./$-]+\.zip", services_import or ""), f"unsafe import: {services_import}"
    return (
        f"CREATE OR REPLACE PROCEDURE {names['procedure']}(SOURCE_ID VARCHAR, ACTION VARCHAR, PAYLOAD_JSON VARCHAR)\n"
        "  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'\n"
        "  PACKAGES = ('snowflake-snowpark-python', 'oracledb', 'pyarrow')\n"
        f"  IMPORTS = ('{services_import}')\n"
        "  HANDLER = 'services.source.oracle.procedures.procedure_entry'\n"
        f"  EXTERNAL_ACCESS_INTEGRATIONS = ({eai})\n"
        f"  SECRETS = ('{SECRET_ALIAS}' = {secret})\n"
        f"  COMMENT = 'Agentic pipeline: Oracle source {schema} (test, catalog, profile, extract and land)'\n"
        "  EXECUTE AS OWNER"
    )


def rotate_password_sql(secret: str) -> str:
    return f"ALTER SECRET {_check_name(secret, 'secret name')} SET PASSWORD = '<oracle password>'"


def network_rule_sql(database: str, schema: str, host: str, port: int) -> str:
    assert re.match(r"^[A-Za-z0-9.\-]{1,253}$", host) and 0 < int(port) < 65536, "invalid host or port"
    return (f"ALTER NETWORK RULE {object_names(database, schema)['rule']} SET VALUE_LIST = ('{host}:{int(port)}')")


def schedule_sql(database: str, schema: str, source_id: str, warehouse: str, cron: str,
                 payload: Dict[str, Any]) -> List[str]:
    """A Snowflake task that runs the source's procedure on a cron schedule (incremental loads)."""
    names = object_names(database, schema)
    _check_name(warehouse, "warehouse")
    assert CRON.match(cron.strip()), "schedule must be a cron expression with a time zone, e.g. '0 * * * * UTC'"
    assert re.match(r"^[0-9a-f\-]{36}$", source_id), "invalid source id"
    body = json.dumps(payload, separators=(",", ":")).replace("'", "''")
    return [
        f"CREATE OR REPLACE TASK {names['task']} WAREHOUSE = {warehouse} SCHEDULE = 'USING CRON {cron.strip()}' "
        f"COMMENT = 'Agentic pipeline: scheduled Oracle load' "
        f"AS CALL {names['procedure']}('{source_id}', 'extract', '{body}')",
        f"ALTER TASK {names['task']} RESUME",
    ]


def unschedule_sql(database: str, schema: str) -> List[str]:
    return [f"DROP TASK IF EXISTS {object_names(database, schema)['task']}"]


def cleanup_sql(config: Dict[str, Any]) -> List[str]:
    """Drop what the platform created for the source (never a secret or integration that was chosen, not made)."""
    if not config.get("database") or not config.get("schema"):
        return []
    names = object_names(config["database"], config["schema"])
    created = set(config.get("created_objects") or [])
    out = [f"DROP TASK IF EXISTS {names['task']}", f"DROP PROCEDURE IF EXISTS {names['procedure']}(VARCHAR, VARCHAR, VARCHAR)"]
    if "integration" in created and config.get("external_access_integration"):
        out.append(f"DROP EXTERNAL ACCESS INTEGRATION IF EXISTS {_check_name(config['external_access_integration'])}")
        out.append(f"DROP NETWORK RULE IF EXISTS {names['rule']}")
    if "secret" in created and config.get("secret"):
        out.append(f"DROP SECRET IF EXISTS {_check_name(config['secret'], 'secret name')}")
    return out


def procedure_entry(session, source_id: str, action: str, payload_json: str) -> Dict[str, Any]:
    password = ""
    try:
        import _snowflake  # available inside Snowflake procedures only

        password = _snowflake.get_username_password(SECRET_ALIAS).password
    except Exception as exc:
        if action not in ("test", "diagnose"):
            raise AssertionError(f"the Oracle secret could not be read: {exc}") from exc
    return run(session, source_id, action, payload_json, password)


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
    assert value, f"environment variable {var} is not set on the API host (set it and restart the API)"
    return value


def _config(cfg: Dict[str, Any]):
    from services.source.oracle.connect import OracleConfig

    return OracleConfig.from_dict(cfg)


def _connect(config: Dict[str, Any], password: str, retry: bool = True):
    from services.source.oracle.connect import connect

    return connect(_config(config), password, retry=retry)


def _update_config(session, source_id: str, change: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    """Read the source's latest configuration, apply `change`, write it back: concurrent loads of other tables (or a
    scheduled run) never lose each other's records."""
    found = rows(session, "SELECT CONFIGURATION_JSON FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = ?", [source_id])
    cfg = (variant(found[0]["CONFIGURATION_JSON"]) if found else None) or {}
    change(cfg)
    session.sql("UPDATE SOURCE.SOURCE_REGISTRY SET CONFIGURATION_JSON = PARSE_JSON(?), "
                "UPDATED_AT = CURRENT_TIMESTAMP() WHERE SOURCE_SYSTEM_ID = ?",
                params=[json.dumps(cfg, default=str), source_id]).collect()
    return cfg


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def run(session, source_id: str, action: str, payload_json: str, password: str,
        progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancelled: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    from services.source.oracle import catalog

    assert action in ACTIONS, f"action must be one of {ACTIONS}"
    payload = json.loads(payload_json or "{}")
    src = source(session, source_id)
    cfg = src["config"]
    if action in ("test", "diagnose"):
        result = catalog.diagnose(_config(cfg), password, lambda: _connect(cfg, password, retry=False))
        health = {"status": result["status"], "at": _now(), "headline": result["headline"],
                  "version": result["server"].get("version"), "latency_ms": result["server"].get("latency_ms"),
                  "password_expires_in_days": result["server"].get("password_expires_in_days")}
        _update_config(session, source_id, lambda c: c.update(health=health))
        return result
    conn = _connect(cfg, password)
    try:
        owner = cfg.get("schema_owner") or cfg["user"]
        if action == "catalog":
            return {"tables": catalog.fetch_catalog(conn, owner)}
        tables = [str(t) for t in payload.get("tables") or []]
        assert tables, "choose at least one table"
        columns = catalog.inspect_columns(conn, owner, tables)
        missing = [t for t in tables if not columns.get(t)]
        if action == "columns":
            return {"columns": columns, "missing": missing}
        if action == "preview":
            assert not missing, f"{tables[0]} was not found or has no visible columns"
            return catalog.preview(conn, owner, tables[0], columns[tables[0]], int(payload.get("limit") or 20))
        estimates = {t["table"]: t["estimated_rows"] for t in catalog.fetch_catalog(conn, owner)}
        results = []
        for t in tables:
            if t in missing:
                results.append({"table": t, "status": "FAILED",
                                "error": f"{owner}.{t} no longer exists or is no longer granted to {cfg['user']}"})
            elif action == "profile":
                results.append(_profile_one(session, src, conn, owner, t, columns[t], estimates.get(t)))
            else:
                results.append(_extract_one(session, src, conn, owner, t, columns[t], payload, progress, cancelled))
        if action == "extract" and payload.get("scheduled"):
            summary = {"at": _now(), "tables": len(results),
                       "failed": [r["table"] for r in results if r.get("status") != "LANDED"],
                       "rows": sum(int(r.get("rows_loaded") or 0) for r in results)}
            _update_config(session, source_id, lambda c: c.update(last_scheduled_run=summary))
        return {"tables": results}
    finally:
        conn.close()


def _profile_ref(src: Dict[str, Any], owner: str, table: str, columns: List[Dict[str, Any]],
                 row_count: Optional[int]):
    from services.profiling.procedures import TableRef

    cfg = src["config"]
    return TableRef(source_name=f"ORACLE_{src['name']}", database=(cfg.get("service_name") or cfg.get("sid") or "ORACLE").upper(),
                    schema=owner, table=table, landing_fqn="",
                    row_count=row_count,
                    columns=tuple((c["column_name"], c["oracle_type"]) for c in columns if not c.get("skip_reason")))


def _profile_one(session, src, conn, owner: str, table: str, columns: List[Dict[str, Any]],
                 estimated_rows: Optional[int]) -> Dict[str, Any]:
    """Profile in Oracle and store the document like any other profile (source ORACLE_<NAME>)."""
    from services.profiling import profiler
    from services.profiling.procedures import _ensure_index_rows, _set_status, _upsert_index, _write_document
    from services.source.oracle.errors import explain
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
        document["skipped_columns"] = [{"column": c["column_name"], "reason": c["skip_reason"]}
                                       for c in columns if c.get("skip_reason")]
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
        return {"table": table, "status": "FAILED", "error": clip(exc, 600), "explain": explain(exc)}


def landed_table_name(table: str) -> str:
    from services.source.oracle.extract import snowflake_names

    return snowflake_names([table])[table]


def _landed_columns(session, database: str, schema: str, table: str) -> Dict[str, str]:
    """Columns of the landed table (name -> data type); empty when it does not exist yet."""
    from services.source.identifiers import quote

    found = rows(session, f"SELECT COLUMN_NAME, DATA_TYPE FROM {quote(database)}.INFORMATION_SCHEMA.COLUMNS "
                          "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION", [schema, table])
    return {r["COLUMN_NAME"]: r["DATA_TYPE"] for r in found}


def _merge_keys(payload: Dict[str, Any], table: str, columns: List[Dict[str, Any]], names: Dict[str, str]) -> List[str]:
    """Landed names of the merge key: chosen by the user, else the table's primary key."""
    chosen = (payload.get("merge_keys") or {}).get(table)
    source_keys = chosen or [c["column_name"] for c in columns if "PK" in (c.get("constraints") or [])]
    assert source_keys, (f"{table} has no primary key; choose the key columns for merge, or load it with "
                         "replace or append")
    unknown = [k for k in source_keys if k not in names]
    assert not unknown, f"merge key columns not readable in {table}: {unknown}"
    return [names[k] for k in source_keys]


def _extract_one(session, src, conn, owner: str, table: str, columns: List[Dict[str, Any]],
                 payload: Dict[str, Any], progress, cancelled=None) -> Dict[str, Any]:
    """Oracle -> Parquet parts on the source stage -> landed table (created once, then replaced, appended or
    merged) -> verified row count -> load recorded on the source."""
    from services.source.external import drift_sql, land_parquet_sql, schema_drift, stage_name
    from services.source.oracle.errors import explain
    from services.source.oracle.extract import Cancelled, extract_table

    cfg = src["config"]
    database, schema = cfg["database"], cfg["schema"]
    target = landed_table_name(table)
    mode = str(payload.get("mode") or "replace").lower()
    assert mode in ("replace", "append", "merge"), "mode must be replace, append or merge"
    prior = dict((cfg.get("loads") or {}).get(target) or {})
    incremental = mode in ("append", "merge")
    wm_col = (payload.get("watermark_columns") or {}).get(table) or (prior.get("watermark_column") if incremental else None)
    lookback = int(payload.get("lookback_minutes") or 0) if mode == "merge" else 0
    batch = dt.datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
    prefix = f"{target}/{batch}"
    stage = stage_name(database, schema)
    started = time.time()
    out: Dict[str, Any] = {"table": table, "landed_as": f"{database}.{schema}.{target}", "status": "FAILED",
                           "batch_id": batch, "mode": mode}

    def put(local_path: str, name: str) -> None:
        session.file.put(local_path, f"@{stage}/{prefix}", auto_compress=False, overwrite=True)
        if progress:
            progress({"table": table, "phase": "UPLOADING", "file": name})

    def extract():
        return extract_table(conn, owner, table, columns, source_system=f"ORACLE:{src['name']}", batch_id=batch,
                             put=put, watermark_column=wm_col,
                             watermark_value=prior.get("watermark") if incremental and wm_col else None,
                             lookback_minutes=lookback, cancelled=cancelled,
                             progress=(lambda p: progress({"table": table, "phase": "EXTRACTING", **p}))
                             if progress else None)

    try:
        if progress:
            progress({"table": table, "phase": "EXTRACTING"})
        try:
            result = extract()
        except Exception as exc:  # snapshot too old on a long read: once more, from scratch
            if "ORA-01555" not in str(exc):
                raise
            session.sql(f"REMOVE @{stage}/{prefix}/").collect()
            out["retried"] = "ORA-01555"
            result = extract()
        out.update(rows_extracted=result["rows"], files=len(result["files"]), columns=result["columns"],
                   text_numbers=result["text_numbers"], notes=result["notes"], skipped=result["skipped"])
        if result["files"]:
            if progress:
                progress({"table": table, "phase": "LANDING"})
            existing = _landed_columns(session, database, schema, target)
            exists = bool(existing)
            if exists:
                drift = schema_drift(result["types"], existing)
                if drift["changed"]:
                    out["drift"] = drift
                    for sql in drift_sql(database, schema, target, drift["added"]):
                        session.sql(sql).collect()
            volume = None
            if str(payload.get("storage") or "MANAGED").upper() == "ICEBERG" and not exists:
                found = rows(session, "SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = "
                                      "'LANDING_EXTERNAL_VOLUME' AND IS_CURRENT ORDER BY VERSION DESC LIMIT 1")
                volume = variant(found[0]["CONFIG_VALUE"]) if found else None
                assert volume, "Iceberg landing needs LANDING_EXTERNAL_VOLUME configured by an admin"
            keys = None
            if mode == "merge":
                keys = _merge_keys(payload, table, columns, result["columns"])
                out["merge_keys"] = keys
            loaded = 0
            for sql in land_parquet_sql(database, schema, target, prefix, mode, exists, volume, keys=keys,
                                        columns=list(result["types"])):
                result_rows = [r.as_dict() for r in session.sql(sql).collect()]
                if sql.startswith("COPY"):
                    loaded = sum(int(r.get("rows_loaded") or r.get("ROWS_LOADED") or 0) for r in result_rows)
                if sql.startswith("MERGE"):
                    first = result_rows[0] if result_rows else {}
                    out["merged"] = {"inserted": int(first.get("number of rows inserted", 0) or 0),
                                     "updated": int(first.get("number of rows updated", 0) or 0)}
            out["rows_loaded"] = loaded
            session.sql(f"REMOVE @{stage}/{prefix}/").collect()  # landed; the rows keep the file name
            # verify: the rows of this batch now in the table match what was read from Oracle
            counted = rows(session, f"SELECT COUNT(*) AS N FROM {out['landed_as']} WHERE \"_BATCH_ID\" = ?", [batch])
            in_table = int(counted[0]["N"]) if counted else 0
            expected = result["rows"] if mode != "merge" else loaded
            out["verification"] = {"read_from_oracle": result["rows"], "copied": loaded, "in_table": in_table,
                                   "verified": in_table == expected and loaded == result["rows"]}
        else:
            out["rows_loaded"] = 0
            out["verification"] = {"read_from_oracle": 0, "copied": 0, "in_table": 0, "verified": True}
            out["note"] = "no new rows since the last load" if incremental else "the table is empty"
        out["status"] = "LANDED"
    except Cancelled as exc:
        out["status"], out["error"] = "CANCELLED", str(exc)
        try:
            session.sql(f"REMOVE @{stage}/{prefix}/").collect()
        except Exception:
            pass
    except Exception as exc:
        out["error"] = clip(exc, 800)
        out["explain"] = explain(exc)
    out["duration_s"] = round(time.time() - started, 1)

    entry = {"batch_id": batch, "mode": mode, "at": _now(), "status": out["status"],
             "rows": out.get("rows_loaded", 0), "read": out.get("rows_extracted"),
             "verified": (out.get("verification") or {}).get("verified"), "duration_s": out["duration_s"],
             "drift": bool(out.get("drift")), "error": out.get("error"), "scheduled": bool(payload.get("scheduled"))}

    def record(c: Dict[str, Any]) -> None:
        history = dict(c.get("load_history") or {})
        history[target] = ([entry] + list(history.get(target) or []))[:HISTORY]
        c["load_history"] = history
        if out["status"] != "LANDED":
            return
        loads = dict(c.get("loads") or {})
        loads[target] = {"oracle_table": f"{owner}.{table}", "batch_id": batch, "mode": mode, "at": _now(),
                         "rows": out["rows_loaded"], "watermark_column": wm_col,
                         "watermark": (result.get("watermark") if wm_col else None),
                         "columns": result["columns"], "verified": entry["verified"],
                         "merge_keys": out.get("merge_keys"), "skipped": result["skipped"]}
        c["loads"] = loads
        c["landed_tables"] = sorted(set(c.get("landed_tables") or []) | {target})
        c["last_landed_at"] = _now()

    _update_config(session, src["id"], record)
    return out
