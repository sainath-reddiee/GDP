"""CONTRACT.GENERATE_SODA / IMPORT_CLIENT_EXPECTATIONS."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from services.common.audit import tool_call
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.soda.expectations import from_client, from_sttm, render_yaml


def _current_sttm(session, run_id: str) -> Dict[str, Any]:
    found = rows(session, """SELECT * FROM CONTRACT.STTM_REGISTRY
                             WHERE RUN_ID = ? AND STATUS IN ('REVIEW', 'APPROVED')
                             ORDER BY STTM_VERSION DESC LIMIT 1""", [run_id])
    assert found, "no current STTM for this run"
    return found[0]


def _lines(session, sttm_id: str) -> List[Dict[str, Any]]:
    return [{
        "target_column": r["TARGET_COLUMN"], "target_datatype": r["TARGET_DATATYPE"],
        "nullable_rule": r["NULLABLE_RULE"], "uniqueness_rule": r["UNIQUENESS_RULE"],
        "accepted_values": variant(r["ACCEPTED_VALUES"]) or [],
        "business_definition": r["BUSINESS_DEFINITION"],
    } for r in rows(session, "SELECT * FROM CONTRACT.STTM_LINE WHERE STTM_ID = ? ORDER BY TARGET_COLUMN", [sttm_id])]


def _store(session, run_id: str, sttm: Dict[str, Any], checks: List[Dict[str, Any]]) -> int:
    version = (scalar(session, "SELECT MAX(VERSION) FROM CONTRACT.SODA_EXPECTATION_REGISTRY WHERE RUN_ID = ?",
                      [run_id]) or 0) + 1
    session.sql("UPDATE CONTRACT.SODA_EXPECTATION_REGISTRY SET IS_CURRENT = FALSE, STATUS = 'SUPERSEDED' "
                "WHERE RUN_ID = ? AND IS_CURRENT", params=[run_id]).collect()
    insert_rows(session, "CONTRACT.SODA_EXPECTATION_REGISTRY",
                ["EXPECTATION_ID", "RUN_ID", "STTM_ID", "DOMAIN_ID", "TARGET_TABLE", "TARGET_COLUMN", "CHECK_TYPE",
                 "CHECK_DEFINITION", "SEVERITY", "ORIGIN", "CLIENT_REQUIREMENT", "STATUS", "VERSION", "IS_CURRENT",
                 "CREATED_BY"],
                ["?", "?", "?", "?", "?", "NULLIF(?, '')", "?", "PARSE_JSON(?)", "?", "?", "NULLIF(?, '')",
                 "'PROPOSED'", "?::NUMBER", "TRUE", "CURRENT_USER()"],
                [[str(uuid.uuid4()), run_id, sttm["STTM_ID"], sttm["DOMAIN_ID"], c["target_table"],
                  c.get("target_column"), c["check_type"], c["definition"], c["severity"], c["origin"],
                  clip(c.get("requirement")), version] for c in checks])
    return version


def generate_soda(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("STTM_APPROVED", "SODA_PENDING")
    stage.walk(["STTM_APPROVED", "SODA_PENDING"], "Soda generation started")
    version = 0
    with tool_call(session, run_id, "generate_soda", {"run_id": run_id}) as call:
        try:
            sttm = _current_sttm(session, run_id)
            design = variant(sttm["TABLE_DESIGN"]) or {}
            table = design.get("target_table") or "DIM_CUSTOMER"
            checks = from_sttm(table, _lines(session, sttm["STTM_ID"]), design.get("business_keys") or [])
            existing_client = rows(session, """SELECT TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION,
                                               SEVERITY, CLIENT_REQUIREMENT FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                               WHERE RUN_ID = ? AND ORIGIN = 'CLIENT' AND STATUS <> 'REJECTED'
                                               QUALIFY ROW_NUMBER() OVER (PARTITION BY CHECK_TYPE, TARGET_COLUMN
                                                                          ORDER BY VERSION DESC) = 1""", [run_id])
            for r in existing_client:
                checks.append({"target_table": r["TARGET_TABLE"], "target_column": r["TARGET_COLUMN"],
                               "check_type": r["CHECK_TYPE"], "definition": variant(r["CHECK_DEFINITION"]),
                               "severity": r["SEVERITY"], "origin": "CLIENT",
                               "requirement": r["CLIENT_REQUIREMENT"]})
            yaml_text = render_yaml(table.lower(), checks)

            def write(_event_id: str) -> None:
                nonlocal version
                version = _store(session, run_id, sttm, checks)

            call.summary = f"{len(checks)} Soda expectations"
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            stage.fail(exc)
            return {"state": stage.payload()}
    stage.move("SODA_REVIEW", call.summary, {"count": len(checks)}, in_transaction=write)
    return {"version": version, "count": len(checks), "yaml": yaml_text, "checks": checks, "state": stage.payload()}


def import_client_expectations(session, run_id: str, rows_json: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW")
    payload = json.loads(rows_json or "[]")
    assert isinstance(payload, list) and payload, "provide a JSON array of client expectation rows"
    sttm = _current_sttm(session, run_id)
    design = variant(sttm["TABLE_DESIGN"]) or {}
    table = design.get("target_table") or "DIM_CUSTOMER"
    imported = from_client(table, payload)
    version = (scalar(session, "SELECT MAX(VERSION) FROM CONTRACT.SODA_EXPECTATION_REGISTRY WHERE RUN_ID = ?",
                      [run_id]) or 0) + 1
    insert_rows(session, "CONTRACT.SODA_EXPECTATION_REGISTRY",
                ["EXPECTATION_ID", "RUN_ID", "STTM_ID", "DOMAIN_ID", "TARGET_TABLE", "TARGET_COLUMN", "CHECK_TYPE",
                 "CHECK_DEFINITION", "SEVERITY", "ORIGIN", "CLIENT_REQUIREMENT", "STATUS", "VERSION", "IS_CURRENT",
                 "CREATED_BY"],
                ["?", "?", "?", "?", "?", "NULLIF(?, '')", "?", "PARSE_JSON(?)", "?", "'CLIENT'", "NULLIF(?, '')",
                 "'PROPOSED'", "?::NUMBER", "TRUE", "CURRENT_USER()"],
                [[str(uuid.uuid4()), run_id, sttm["STTM_ID"], sttm["DOMAIN_ID"], c["target_table"],
                  c.get("target_column"), c["check_type"], c["definition"], c["severity"],
                  clip(c.get("requirement")), version] for c in imported])
    return {"imported": len(imported), "version": version}
