"""Snowpark handlers for source onboarding (deployed in SOURCE, EXECUTE AS OWNER).

REGISTER_SOURCE         CREATED -> SOURCE_REGISTERED, records the source and discovered objects
VALIDATE_SOURCE_ACCESS  SOURCE_REGISTERED -> ACCESS_VALIDATION -> ACCESS_APPROVED | SOURCE_REGISTERED
EXECUTE_LANDING         ACCESS_APPROVED -> LANDING_PENDING -> LANDING_RUNNING -> LANDING_COMPLETE | FAILED

Landing copies each selected object as-is (CTAS) into the run's target landing schema (default
<platform db>.LANDING, managed tables) and reconciles row counts.
Every state change goes through the workflow state machine; nothing here can skip a stage.

The source connection (SOURCE_REGISTRY) is registered once and reused by name; the landing target
is a property of the run, so inspecting a source never needs the landing definition and vice versa.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from services.common.sql import config_value
from services.common.sql import insert_rows as _insert
from services.source.adapters import (
    FAILED,
    PASSED,
    AccessCheck,
    SourceAdapter,
    TargetSpec,
    adapter_for,
    checks_passed,
)
from services.source.identifiers import SOURCE_SYSTEM_NAME, fqn, format_data_type, landing_table_name, quote
from services.workflow.procedures import (
    MAX_TEXT,
    _apply_transition,
    _get_run,
    _is_closed,
    _load_graph,
    _parse_details,
    _record_run_event,
    _rows,
    _state_payload,
    _text,
)

TARGET_EDITABLE_STATES = {"CREATED", "SOURCE_REGISTERED", "ACCESS_APPROVED", "LANDING_PENDING"}
TARGET_EDITABLE_FAILURES = {"ACCESS_VALIDATION", "LANDING_RUNNING"}

REGISTER_FIELDS = {"SOURCE_SYSTEM_NAME", "SOURCE_TYPE", "DATABASE", "SCHEMA", "OWNER",
                   "SECURITY_CLASSIFICATION", "DOMAIN_ID", "LANDING_DATABASE", "LANDING_SCHEMA", "STORAGE_TYPE"}
TARGET_FIELDS = ("LANDING_DATABASE", "LANDING_SCHEMA", "STORAGE_TYPE")


def _runner(session):
    return lambda sql, params: _rows(session, sql, params)


def _platform_database(session) -> str:
    database = _rows(session, "SELECT CURRENT_DATABASE() AS D")[0]["D"]
    assert database, "no current database in procedure context"
    return database


def _target_spec(session, run: Dict[str, Any], raw: Optional[Dict[str, Any]] = None) -> TargetSpec:
    """The run's landing target; runs created before V006 have no columns and get the default."""
    raw = raw if raw is not None else {
        "landing_database": run.get("LANDING_DATABASE"), "landing_schema": run.get("LANDING_SCHEMA"),
        "storage_type": run.get("STORAGE_TYPE"),
    }
    volume = None
    if str(raw.get("storage_type") or "").upper() == "ICEBERG":
        volume = config_value(session, "LANDING_EXTERNAL_VOLUME")
        volume = volume.get("name") if isinstance(volume, dict) else volume
    return TargetSpec.parse(raw, _platform_database(session), volume)


def _landing_check(session, spec: TargetSpec) -> AccessCheck:
    if not spec.copies:
        return AccessCheck("LANDING_TARGET", PASSED, "read in place: no landing copy is created")
    try:
        found = _rows(session, f"SELECT SCHEMA_NAME FROM {quote(spec.landing_database)}.INFORMATION_SCHEMA.SCHEMATA "
                               "WHERE SCHEMA_NAME = ?", [spec.landing_schema])
    except Exception as exc:
        return AccessCheck("LANDING_TARGET", FAILED, f"{spec.landing_database} is not accessible: {exc}",
                           f"Grant USAGE on {spec.landing_database} to the role running the onboarding")
    if not found:
        return AccessCheck("LANDING_TARGET", FAILED,
                           f"{spec.landing_database}.{spec.landing_schema} not found",
                           "Create the schema or grant USAGE and CREATE TABLE on it")
    return AccessCheck("LANDING_TARGET", PASSED,
                       f"{spec.landing_database}.{spec.landing_schema} ({spec.storage_type.lower()} tables)")


def fetch_schema_catalog(session, connection_id: str, database: Optional[str] = None,
                         schema: Optional[str] = None) -> Dict[str, Any]:
    """Inspect a registered connection without touching any run: objects, row counts, columns."""
    connection_id = _text(connection_id, "CONNECTION_ID", required=True)
    found = _rows(session, "SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, SOURCE_TYPE, CONFIGURATION_JSON "
                           "FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = ? AND ACTIVE_FLAG", [connection_id])
    assert found, f"connection {connection_id} not found"
    source = found[0]
    config = json.loads(source["CONFIGURATION_JSON"]) if isinstance(source["CONFIGURATION_JSON"], str) \
        else (source["CONFIGURATION_JSON"] or {})
    adapter = adapter_for(source["SOURCE_TYPE"], database or config["database"], schema or config["schema"])
    try:
        objects = adapter.fetch_schema_catalog(_runner(session))
    except Exception as exc:
        raise ValueError(f"SOURCE_NOT_ACCESSIBLE: {adapter.database}.{adapter.schema}: {exc}") from exc
    return {"connection_id": source["SOURCE_SYSTEM_ID"], "source_system_name": source["SOURCE_SYSTEM_NAME"],
            "source_type": source["SOURCE_TYPE"], "database": adapter.database, "schema": adapter.schema,
            "objects": objects}


def _clip(value: Optional[str]) -> str:
    return "" if value is None else str(value)[:MAX_TEXT]


def _require_state(run: Dict[str, Any], *states: str) -> None:
    if run["CURRENT_STATE"] not in states:
        raise ValueError(f"TRANSITION_REJECTED: run is in {run['CURRENT_STATE']}; "
                         f"this step needs {' or '.join(states)}")


def _source_for_run(session, run: Dict[str, Any]) -> tuple[Dict[str, Any], SourceAdapter]:
    assert run["SOURCE_SYSTEM_ID"], "run has no registered source"
    rows = _rows(session, "SELECT * FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = ?", [run["SOURCE_SYSTEM_ID"]])
    assert rows, f"source {run['SOURCE_SYSTEM_ID']} not found"
    source = rows[0]
    config = json.loads(source["CONFIGURATION_JSON"])
    return source, adapter_for(source["SOURCE_TYPE"], config["database"], config["schema"])


def register_source(session, run_id: str, payload_json: str) -> Dict[str, Any]:
    run_id = _text(run_id, "RUN_ID", required=True)
    payload = {k.upper(): v for k, v in _parse_details(payload_json).items()}
    unknown = set(payload) - REGISTER_FIELDS
    assert not unknown, f"unknown fields: {sorted(unknown)}"
    name = _text(payload.get("SOURCE_SYSTEM_NAME"), "SOURCE_SYSTEM_NAME", required=True)
    assert SOURCE_SYSTEM_NAME.match(name), "SOURCE_SYSTEM_NAME must be a letter followed by up to 63 letters, digits or _"
    name = name.upper()
    source_type = _text(payload.get("SOURCE_TYPE"), "SOURCE_TYPE", required=True).upper()
    adapter = adapter_for(source_type, _text(payload.get("DATABASE"), "DATABASE", required=True),
                          _text(payload.get("SCHEMA"), "SCHEMA", required=True))

    graph = _load_graph(session)
    run = _get_run(session, run_id)
    _require_state(run, "CREATED")
    target = None
    if any(payload.get(k) for k in TARGET_FIELDS):
        target = _target_spec(session, run, {k.lower(): payload.get(k) for k in TARGET_FIELDS})
    try:
        objects = adapter.discover_objects(_runner(session))
    except Exception as exc:
        raise ValueError(f"SOURCE_NOT_ACCESSIBLE: {adapter.database}.{adapter.schema}: {exc}") from exc
    if not objects:
        raise ValueError(f"SOURCE_NOT_ACCESSIBLE: no tables or views visible in {adapter.database}.{adapter.schema}")

    config = {"database": adapter.database, "schema": adapter.schema}
    existing = _rows(session, "SELECT SOURCE_SYSTEM_ID, SOURCE_TYPE, CONFIGURATION_JSON FROM SOURCE.SOURCE_REGISTRY "
                              "WHERE SOURCE_SYSTEM_NAME = ? AND ACTIVE_FLAG", [name])
    if existing:
        prior = existing[0]
        if prior["SOURCE_TYPE"] != source_type or json.loads(prior["CONFIGURATION_JSON"]) != config:
            raise ValueError(f"SOURCE_NAME_CONFLICT: {name} is already registered as {prior['SOURCE_TYPE']} "
                             f"{json.loads(prior['CONFIGURATION_JSON'])}")
        source_id = prior["SOURCE_SYSTEM_ID"]
    else:
        source_id = str(uuid.uuid4())

    def write(_event_id: str) -> None:
        if not existing:
            _insert(session, "SOURCE.SOURCE_REGISTRY",
                    ["SOURCE_SYSTEM_ID", "SOURCE_SYSTEM_NAME", "SOURCE_TYPE", "DOMAIN_ID", "OWNER", "CONNECTION_TYPE",
                     "SECURITY_CLASSIFICATION", "CONFIGURATION_REFERENCE", "CONFIGURATION_JSON", "CREATED_BY"],
                    ["?", "?", "?", "NULLIF(?, '')", "NULLIF(?, '')", "?", "NULLIF(?, '')", "?", "PARSE_JSON(?)",
                     "CURRENT_USER()"],
                    [[source_id, name, source_type, payload.get("DOMAIN_ID"), payload.get("OWNER"), "SNOWFLAKE",
                      payload.get("SECURITY_CLASSIFICATION"), adapter.database, json.dumps(config)]])
        _insert(session, "SOURCE.SOURCE_OBJECT",
                ["SOURCE_OBJECT_ID", "RUN_ID", "SOURCE_SYSTEM_ID", "SOURCE_DATABASE", "SOURCE_SCHEMA", "OBJECT_NAME",
                 "OBJECT_TYPE", "ROW_COUNT_ESTIMATE", "BYTES", "LAST_ALTERED", "SELECTED_FLAG"],
                ["?", "?", "?", "?", "?", "?", "?", "NULLIF(?, '')::NUMBER", "NULLIF(?, '')::NUMBER",
                 "NULLIF(?, '')::TIMESTAMP_LTZ", "FALSE"],
                [[str(uuid.uuid4()), run_id, source_id, o.database, o.schema, o.name, o.object_type,
                  o.row_count, o.bytes, o.last_altered] for o in objects])
        session.sql("UPDATE CORE.WORKFLOW_RUN SET SOURCE_SYSTEM_ID = ?, SOURCE_DATABASE = ?, SOURCE_SCHEMA = ? "
                    "WHERE RUN_ID = ?", params=[source_id, adapter.database, adapter.schema, run_id]).collect()
        if target is not None:
            session.sql("UPDATE CORE.WORKFLOW_RUN SET LANDING_DATABASE = ?, LANDING_SCHEMA = ?, STORAGE_TYPE = ? "
                        "WHERE RUN_ID = ?", params=[target.landing_database, target.landing_schema,
                                                    target.storage_type, run_id]).collect()

    _apply_transition(session, graph, run, "SOURCE_REGISTERED", "SYSTEM", f"source {name} registered",
                      {"source_system_id": source_id, "source_type": source_type, **config,
                       "objects_discovered": len(objects),
                       **({"target": target.as_dict()} if target else {})}, in_transaction=write)
    return {"source_system_id": source_id, "objects_discovered": len(objects),
            "state": _state_payload(graph, _get_run(session, run_id))}


def validate_source_access(session, run_id: str, selected_json: str) -> Dict[str, Any]:
    run_id = _text(run_id, "RUN_ID", required=True)
    selected = json.loads(selected_json or "[]")
    assert isinstance(selected, list) and all(isinstance(s, str) for s in selected), \
        "SELECTED_OBJECTS_JSON must be a JSON array of object names"
    selected = sorted(set(selected))

    graph = _load_graph(session)
    run = _get_run(session, run_id)
    _require_state(run, "SOURCE_REGISTERED")
    source, adapter = _source_for_run(session, run)

    def mark_selection(_event_id: str) -> None:
        session.sql("UPDATE SOURCE.SOURCE_OBJECT SET SELECTED_FLAG = ARRAY_CONTAINS(OBJECT_NAME::VARIANT, PARSE_JSON(?)::ARRAY) "
                    "WHERE RUN_ID = ?", params=[json.dumps(selected), run_id]).collect()

    _apply_transition(session, graph, run, "ACCESS_VALIDATION", "SYSTEM", "access validation started",
                      {"selected_objects": selected}, in_transaction=mark_selection)

    try:
        checks = adapter.validate_access(_runner(session), selected)
        if checks_passed(checks):
            try:
                checks.append(_landing_check(session, _target_spec(session, run)))
            except AssertionError as exc:
                checks.append(AccessCheck("LANDING_TARGET", FAILED, str(exc)))
    except Exception as exc:
        _apply_transition(session, graph, _get_run(session, run_id), "FAILED", "SYSTEM",
                          _clip(f"access validation error: {exc}"), {})
        return {"passed": False, "checks": [], "state": _state_payload(graph, _get_run(session, run_id))}

    passed = checks_passed(checks)
    failed = [c for c in checks if c.status == "FAILED"]
    reason = "all access checks passed" if passed else _clip("; ".join(f"{c.name}: {c.detail}" for c in failed))

    def record_checks(_event_id: str) -> None:
        _insert(session, "SOURCE.SOURCE_ACCESS_CHECK",
                ["CHECK_ID", "RUN_ID", "SOURCE_SYSTEM_ID", "CHECK_NAME", "STATUS", "DETAIL", "REMEDIATION"],
                ["?", "?", "?", "?", "?", "NULLIF(?, '')", "NULLIF(?, '')"],
                [[str(uuid.uuid4()), run_id, source["SOURCE_SYSTEM_ID"], c.name, c.status, _clip(c.detail),
                  _clip(c.remediation)] for c in checks])

    _apply_transition(session, graph, _get_run(session, run_id), "ACCESS_APPROVED" if passed else "SOURCE_REGISTERED",
                      "SYSTEM", reason, {"checks": [c.as_dict() for c in checks]}, in_transaction=record_checks)
    return {"passed": passed, "checks": [c.as_dict() for c in checks],
            "state": _state_payload(graph, _get_run(session, run_id))}


def _land_object(session, run_id: str, source: Dict[str, Any], adapter: SourceAdapter, spec: TargetSpec,
                 object_name: str, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    if spec.copies:
        landing_name = landing_table_name(source["SOURCE_SYSTEM_NAME"], object_name)
        database, schema = spec.landing_database, spec.landing_schema
    else:
        landing_name, database, schema = object_name, adapter.database, adapter.schema
    target = fqn(database, schema, landing_name)
    result: Dict[str, Any] = {"object": object_name, "landing_table": f"{database}.{schema}.{landing_name}",
                              "status": "FAILED", "source_rows": None, "landed_rows": None, "query_id": None,
                              "error": None}
    try:
        if not spec.copies:
            # Zero-copy: the registry points at the source table itself; row counts come from metadata.
            estimate = _rows(session, "SELECT ROW_COUNT_ESTIMATE AS N FROM SOURCE.SOURCE_OBJECT WHERE RUN_ID = ? "
                                      "AND OBJECT_NAME = ? ORDER BY DISCOVERED_AT DESC LIMIT 1", [run_id, object_name])
            rows_known = estimate[0]["N"] if estimate else None
            result.update(source_rows=rows_known, landed_rows=rows_known, status="COMPLETE")
        else:
            result["source_rows"] = _rows(session, adapter.count_sql(object_name))[0]["N"]
            comment = f"As-is landing of {adapter.database}.{adapter.schema}.{object_name} by run {run_id}"
            session.sql(spec.create_sql(landing_name, adapter.select_sql(object_name), comment)).collect()
            result["query_id"] = _rows(session, "SELECT LAST_QUERY_ID() AS Q")[0]["Q"]
            result["landed_rows"] = _rows(session, f"SELECT COUNT(*) AS N FROM {target}")[0]["N"]
            if result["landed_rows"] == result["source_rows"]:
                result["status"] = "COMPLETE"
            else:
                result["error"] = f"row count mismatch: source {result['source_rows']}, landed {result['landed_rows']}"
    except Exception as exc:
        result["error"] = _clip(str(exc))

    landing_id = str(uuid.uuid4())
    _insert(session, "SOURCE.LANDING_TABLE_REGISTRY",
            ["LANDING_ID", "RUN_ID", "SOURCE_SYSTEM_ID", "SOURCE_DATABASE", "SOURCE_SCHEMA", "SOURCE_TABLE",
             "LANDING_DATABASE", "LANDING_SCHEMA", "LANDING_TABLE", "INGESTION_METHOD", "INGESTION_STATUS",
             "ROW_COUNT", "SOURCE_ROW_COUNT", "INGESTED_AT", "QUERY_ID", "CREATED_BY", "ERROR_MESSAGE"],
            ["?", "?", "?", "?", "?", "?", "?", "?", "?", "?", "?", "NULLIF(?, '')::NUMBER",
             "NULLIF(?, '')::NUMBER", "IFF(? = 'COMPLETE', CURRENT_TIMESTAMP(), NULL)", "NULLIF(?, '')",
             "CURRENT_USER()", "NULLIF(?, '')"],
            [[landing_id, run_id, source["SOURCE_SYSTEM_ID"], adapter.database, adapter.schema, object_name,
              database, schema, landing_name, "CTAS" if spec.copies else "IN_PLACE", result["status"],
              result["landed_rows"], result["source_rows"],
              result["status"], result["query_id"], result["error"]]])
    if result["status"] == "COMPLETE" and columns:
        _insert(session, "SOURCE.LANDING_COLUMN_REGISTRY",
                ["LANDING_COLUMN_ID", "LANDING_ID", "COLUMN_NAME", "DATA_TYPE", "ORDINAL_POSITION", "NULLABLE",
                 "SOURCE_COMMENT"],
                ["?", "?", "?", "?", "?::NUMBER", "? = 'YES'", "NULLIF(?, '')"],
                [[str(uuid.uuid4()), landing_id, c["COLUMN_NAME"],
                  format_data_type(c["DATA_TYPE"], c["CHARACTER_MAXIMUM_LENGTH"], c["NUMERIC_PRECISION"],
                                   c["NUMERIC_SCALE"]),
                  c["ORDINAL_POSITION"], c["IS_NULLABLE"], c["COMMENT"]] for c in columns])
    result["landing_id"] = landing_id
    return result


def execute_landing(session, run_id: str) -> Dict[str, Any]:
    run_id = _text(run_id, "RUN_ID", required=True)
    graph = _load_graph(session)
    run = _get_run(session, run_id)
    _require_state(run, "ACCESS_APPROVED", "LANDING_PENDING")
    source, adapter = _source_for_run(session, run)
    selected = [r["OBJECT_NAME"] for r in _rows(
        session, "SELECT OBJECT_NAME FROM SOURCE.SOURCE_OBJECT WHERE RUN_ID = ? AND SELECTED_FLAG ORDER BY OBJECT_NAME",
        [run_id])]
    assert selected, "no selected objects for this run"

    if run["CURRENT_STATE"] == "ACCESS_APPROVED":
        _apply_transition(session, graph, run, "LANDING_PENDING", "SYSTEM", "landing queued", {})
    _apply_transition(session, graph, _get_run(session, run_id), "LANDING_RUNNING", "SYSTEM", "landing started",
                      {"objects": selected})

    results: List[Dict[str, Any]] = []
    try:
        spec = _target_spec(session, run)
        columns_by_table: Dict[str, List[Dict[str, Any]]] = {}
        for row in _rows(session, adapter.columns_sql(), [adapter.schema]):
            columns_by_table.setdefault(row["TABLE_NAME"], []).append(row)
        for name in selected:
            results.append(_land_object(session, run_id, source, adapter, spec, name,
                                        columns_by_table.get(name, [])))
    except Exception as exc:
        _apply_transition(session, graph, _get_run(session, run_id), "FAILED", "SYSTEM",
                          _clip(f"landing error: {exc}"), {"tables": results})
        return {"tables": results, "state": _state_payload(graph, _get_run(session, run_id))}

    failed = [r for r in results if r["status"] != "COMPLETE"]
    if failed:
        reason = _clip("; ".join(f"{r['object']}: {r['error']}" for r in failed))
        _apply_transition(session, graph, _get_run(session, run_id), "FAILED", "SYSTEM", reason, {"tables": results})
    else:
        _apply_transition(session, graph, _get_run(session, run_id), "LANDING_COMPLETE", "SYSTEM",
                          f"{len(results)} objects landed, row counts reconciled", {"tables": results})
    return {"tables": results, "state": _state_payload(graph, _get_run(session, run_id))}


def set_landing_target(session, run_id: str, payload_json: str) -> Dict[str, Any]:
    """Choose where the run lands (database, schema, MANAGED or ICEBERG) any time before landing completes."""
    run_id = _text(run_id, "RUN_ID", required=True)
    raw = {k.lower(): v for k, v in _parse_details(payload_json).items()}
    unknown = set(raw) - {f.lower() for f in TARGET_FIELDS}
    assert not unknown, f"unknown fields: {sorted(unknown)}"
    graph = _load_graph(session)
    run = _get_run(session, run_id)
    assert not _is_closed(run), "run is archived or deleted; restore it first"
    state = run["CURRENT_STATE"]
    if state not in TARGET_EDITABLE_STATES and not (state == "FAILED" and run["FAILED_FROM_STATE"] in TARGET_EDITABLE_FAILURES):
        raise ValueError(f"TRANSITION_REJECTED: the landing target is fixed once landing has run; run is in {state}")
    spec = _target_spec(session, run, raw)
    check = _landing_check(session, spec)
    if check.status == FAILED:
        raise ValueError(f"LANDING_TARGET_INVALID: {check.detail}. {check.remediation or ''}".strip())

    session.sql("BEGIN TRANSACTION").collect()
    try:
        updated = session.sql(
            "UPDATE CORE.WORKFLOW_RUN SET LANDING_DATABASE = ?, LANDING_SCHEMA = ?, STORAGE_TYPE = ?, "
            "STATE_VERSION = STATE_VERSION + 1, UPDATED_AT = CURRENT_TIMESTAMP() "
            "WHERE RUN_ID = ? AND STATE_VERSION = ?",
            params=[spec.landing_database, spec.landing_schema, spec.storage_type, run_id, run["STATE_VERSION"]],
        ).collect()
        if updated[0][0] != 1:
            raise ValueError("CONCURRENT_UPDATE: run state changed since it was read; reload and retry")
        _record_run_event(session, run, "HUMAN", "landing target set", spec.as_dict())
        session.sql("COMMIT").collect()
    except Exception:
        session.sql("ROLLBACK").collect()
        raise
    return {"target": spec.as_dict(), "check": check.as_dict(), "state": _state_payload(graph, _get_run(session, run_id))}


def register_connection(session, payload_json: str) -> Dict[str, Any]:
    """Register a source connection once, without a run, so it can be profiled and reused by any run."""
    payload = {k.upper(): v for k, v in _parse_details(payload_json).items()}
    unknown = set(payload) - {"SOURCE_SYSTEM_NAME", "SOURCE_TYPE", "DATABASE", "SCHEMA", "OWNER",
                              "SECURITY_CLASSIFICATION", "DOMAIN_ID"}
    assert not unknown, f"unknown fields: {sorted(unknown)}"
    name = _text(payload.get("SOURCE_SYSTEM_NAME"), "SOURCE_SYSTEM_NAME", required=True)
    assert SOURCE_SYSTEM_NAME.match(name), "SOURCE_SYSTEM_NAME must be a letter followed by up to 63 letters, digits or _"
    name = name.upper()
    source_type = (_text(payload.get("SOURCE_TYPE"), "SOURCE_TYPE") or "SNOWFLAKE_DATABASE").upper()
    adapter = adapter_for(source_type, _text(payload.get("DATABASE"), "DATABASE", required=True),
                          _text(payload.get("SCHEMA"), "SCHEMA", required=True))
    try:
        objects = adapter.discover_objects(_runner(session))
    except Exception as exc:
        raise ValueError(f"SOURCE_NOT_ACCESSIBLE: {adapter.database}.{adapter.schema}: {exc}") from exc
    config = {"database": adapter.database, "schema": adapter.schema}
    existing = _rows(session, "SELECT SOURCE_SYSTEM_ID, SOURCE_TYPE, CONFIGURATION_JSON FROM SOURCE.SOURCE_REGISTRY "
                              "WHERE SOURCE_SYSTEM_NAME = ? AND ACTIVE_FLAG", [name])
    if existing:
        prior = existing[0]
        if prior["SOURCE_TYPE"] != source_type or json.loads(prior["CONFIGURATION_JSON"]) != config:
            raise ValueError(f"SOURCE_NAME_CONFLICT: {name} is already registered as {prior['SOURCE_TYPE']} "
                             f"{json.loads(prior['CONFIGURATION_JSON'])}")
        return {"source_system_id": prior["SOURCE_SYSTEM_ID"], "created": False, "objects_discovered": len(objects)}
    source_id = str(uuid.uuid4())
    _insert(session, "SOURCE.SOURCE_REGISTRY",
            ["SOURCE_SYSTEM_ID", "SOURCE_SYSTEM_NAME", "SOURCE_TYPE", "DOMAIN_ID", "OWNER", "CONNECTION_TYPE",
             "SECURITY_CLASSIFICATION", "CONFIGURATION_REFERENCE", "CONFIGURATION_JSON", "CREATED_BY"],
            ["?", "?", "?", "NULLIF(?, '')", "NULLIF(?, '')", "?", "NULLIF(?, '')", "?", "PARSE_JSON(?)",
             "CURRENT_USER()"],
            [[source_id, name, source_type, payload.get("DOMAIN_ID"), payload.get("OWNER"), "SNOWFLAKE",
              payload.get("SECURITY_CLASSIFICATION"), adapter.database, json.dumps(config)]])
    return {"source_system_id": source_id, "created": True, "objects_discovered": len(objects)}
