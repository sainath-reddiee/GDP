"""Snowpark handlers for source onboarding (deployed in SOURCE, EXECUTE AS OWNER).

REGISTER_SOURCE         CREATED -> SOURCE_REGISTERED, records the source and discovered objects
VALIDATE_SOURCE_ACCESS  SOURCE_REGISTERED -> ACCESS_VALIDATION -> ACCESS_APPROVED | SOURCE_REGISTERED
EXECUTE_LANDING         ACCESS_APPROVED -> LANDING_PENDING -> LANDING_RUNNING -> LANDING_COMPLETE | FAILED

Landing copies each selected object as-is (CTAS) into <platform db>.LANDING and reconciles row counts.
Every state change goes through the workflow state machine; nothing here can skip a stage.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from services.common.sql import insert_rows as _insert
from services.source.adapters import SourceAdapter, adapter_for, checks_passed
from services.source.identifiers import SOURCE_SYSTEM_NAME, fqn, format_data_type, landing_table_name
from services.workflow.procedures import (
    MAX_TEXT,
    _apply_transition,
    _get_run,
    _load_graph,
    _parse_details,
    _rows,
    _state_payload,
    _text,
)

REGISTER_FIELDS = {"SOURCE_SYSTEM_NAME", "SOURCE_TYPE", "DATABASE", "SCHEMA", "OWNER",
                   "SECURITY_CLASSIFICATION", "DOMAIN_ID"}
LANDING_SCHEMA = "LANDING"


def _runner(session):
    return lambda sql, params: _rows(session, sql, params)


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

    _apply_transition(session, graph, run, "SOURCE_REGISTERED", "SYSTEM", f"source {name} registered",
                      {"source_system_id": source_id, "source_type": source_type, **config,
                       "objects_discovered": len(objects)}, in_transaction=write)
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


def _escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _land_object(session, run_id: str, source: Dict[str, Any], adapter: SourceAdapter, database: str,
                 object_name: str, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    landing_name = landing_table_name(source["SOURCE_SYSTEM_NAME"], object_name)
    target = fqn(database, LANDING_SCHEMA, landing_name)
    result: Dict[str, Any] = {"object": object_name, "landing_table": f"{database}.{LANDING_SCHEMA}.{landing_name}",
                              "status": "FAILED", "source_rows": None, "landed_rows": None, "query_id": None,
                              "error": None}
    try:
        result["source_rows"] = _rows(session, adapter.count_sql(object_name))[0]["N"]
        comment = _escape_literal(f"As-is landing of {adapter.database}.{adapter.schema}.{object_name} by run {run_id}")
        session.sql(f"CREATE OR REPLACE TABLE {target} COMMENT = '{comment}' AS {adapter.select_sql(object_name)}").collect()
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
            ["?", "?", "?", "?", "?", "?", "?", "?", "?", "'CTAS'", "?", "NULLIF(?, '')::NUMBER",
             "NULLIF(?, '')::NUMBER", "IFF(? = 'COMPLETE', CURRENT_TIMESTAMP(), NULL)", "NULLIF(?, '')",
             "CURRENT_USER()", "NULLIF(?, '')"],
            [[landing_id, run_id, source["SOURCE_SYSTEM_ID"], adapter.database, adapter.schema, object_name,
              database, LANDING_SCHEMA, landing_name, result["status"], result["landed_rows"], result["source_rows"],
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
        database = _rows(session, "SELECT CURRENT_DATABASE() AS D")[0]["D"]
        assert database, "no current database in procedure context"
        columns_by_table: Dict[str, List[Dict[str, Any]]] = {}
        for row in _rows(session, adapter.columns_sql(), [adapter.schema]):
            columns_by_table.setdefault(row["TABLE_NAME"], []).append(row)
        for name in selected:
            results.append(_land_object(session, run_id, source, adapter, database, name,
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
