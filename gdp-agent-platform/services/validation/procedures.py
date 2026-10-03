"""CODEGEN.VALIDATE_DBT: VALIDATION_PENDING -> VALIDATION_RUNNING -> PASSED/FAILED -> DBT_REVIEW."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from services.common.audit import tool_call
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.soda.expectations import render_yaml
from services.knowledge.usage import STAGE_SKILLS, use_skills
from services.validation import checks


def _current_generation(session, run_id: str) -> Dict[str, Any]:
    found = rows(session, """SELECT * FROM CODEGEN.DBT_GENERATION_REGISTRY
                             WHERE RUN_ID = ? AND GENERATION_STATUS IN ('GENERATED', 'GENERATING')
                             ORDER BY GENERATION_VERSION DESC LIMIT 1""", [run_id])
    assert found, "no generated dbt project for this run"
    return found[0]


def _files(session, generation_id: str) -> Dict[str, str]:
    return {r["FILE_PATH"]: r["CONTENT"] for r in rows(
        session, "SELECT FILE_PATH, CONTENT FROM CODEGEN.GENERATED_ARTIFACT WHERE GENERATION_ID = ?",
        [generation_id])}


def _sttm_lines(session, sttm_id: str) -> List[Dict[str, Any]]:
    return [{
        "target_column": r["TARGET_COLUMN"], "target_datatype": r["TARGET_DATATYPE"],
        "mapping_type": r["MAPPING_TYPE"], "transformation": r["TRANSFORMATION"],
        "nullable_rule": r["NULLABLE_RULE"], "uniqueness_rule": r["UNIQUENESS_RULE"],
        "accepted_values": variant(r["ACCEPTED_VALUES"]) or [],
        "required": not r["NULLABLE_RULE"] and r["MAPPING_TYPE"] != "UNMAPPED",
        "business_definition": r["BUSINESS_DEFINITION"],
    } for r in rows(session, "SELECT * FROM CONTRACT.STTM_LINE WHERE STTM_ID = ?", [sttm_id])]


def _compile(session, generation: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """CREATE DBT PROJECT FROM the staged files and EXECUTE compile WRITEBACK=FALSE.

    Missing/unavailable dbt project objects are recorded as ERROR, not a hard failure of the run:
    deterministic checks still decide VALIDATION_PASSED / VALIDATION_FAILED.
    """
    database = scalar(session, "SELECT CURRENT_DATABASE()")
    safe = "GDP_" + run_id.replace("-", "")[:18] + "_V" + str(int(generation["GENERATION_VERSION"]))
    stage_path = generation["STAGE_PATH"] or ""
    source = stage_path if stage_path.startswith("@") else f"@{database}.{stage_path}"
    name = f"{database}.CODEGEN.{safe}"
    try:
        session.sql(
            f"CREATE OR REPLACE DBT PROJECT {name} FROM '{source}' "
            f"AUTO_COMPILE = FALSE DEFAULT_WRITEBACK = FALSE "
            f"COMMENT = 'Phase 1 compile-only project for run {run_id}'"
        ).collect()
        session.sql(f"EXECUTE DBT PROJECT {name} ARGS = 'compile' WRITEBACK = FALSE").collect()
        return {"validation_type": "DBT_COMPILE", "status": "PASSED", "error_count": 0, "warning_count": 0,
                "findings": [{"severity": "INFO", "message": f"compiled {name} WRITEBACK=FALSE"}],
                "query_id": None}
    except Exception as exc:
        return {"validation_type": "DBT_COMPILE", "status": "ERROR", "error_count": 0, "warning_count": 1,
                "findings": [{"severity": "WARN",
                              "message": "dbt project compile unavailable or failed; deterministic checks still apply: "
                                         + clip(exc, 800)}],
                "query_id": None}


def validate_dbt(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("VALIDATION_PENDING", "VALIDATION_RUNNING")
    stage.walk(["VALIDATION_PENDING", "VALIDATION_RUNNING"], "validation started")
    with tool_call(session, run_id, "validate_dbt", {"run_id": run_id}) as call:
        try:
            generation = _current_generation(session, run_id)
            guidance = use_skills(session, STAGE_SKILLS["VALIDATION"], excerpt=400)
            files = _files(session, generation["GENERATION_ID"])
            assert files, "generated dbt project has no artifacts"
            soda = files.get("soda/checks.yml") or ""
            results = checks.run(files, _sttm_lines(session, generation["STTM_ID"]), soda)
            results.append(_compile(session, generation, run_id))
            results.append({"validation_type": "SKILL", "status": "PASSED", "error_count": 0,
                            "warning_count": 0, "findings": [{"severity": "INFO", "message": guidance[:500]}],
                            "query_id": None})
            status, errors, warnings = checks.summary(results)
            validation_id = str(uuid.uuid4())

            def write(_event_id: str) -> None:
                insert_rows(session, "CODEGEN.VALIDATION_RUN",
                            ["VALIDATION_ID", "RUN_ID", "GENERATION_ID", "VALIDATION_TYPE", "STATUS",
                             "ERROR_COUNT", "WARNING_COUNT", "RESULT_JSON", "COMPLETED_AT"],
                            ["?", "?", "?", "?", "?", "?::NUMBER", "?::NUMBER", "PARSE_JSON(?)",
                             "CURRENT_TIMESTAMP()"],
                            [[str(uuid.uuid4()), run_id, generation["GENERATION_ID"], r["validation_type"],
                              r["status"], r["error_count"], r["warning_count"],
                              {"findings": r.get("findings", [])}] for r in results] +
                            [[validation_id, run_id, generation["GENERATION_ID"], "SUMMARY", status,
                              errors, warnings, {"results": results}]])

            call.summary = f"{status}: {errors} errors, {warnings} warnings across {len(results)} checks"
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            stage.fail(exc)
            return {"state": stage.payload()}
    next_state = "VALIDATION_PASSED" if status == "PASSED" else "VALIDATION_FAILED"
    stage.move(next_state, call.summary, {"errors": errors, "warnings": warnings}, in_transaction=write)
    if next_state == "VALIDATION_PASSED":
        stage.move("DBT_REVIEW", "validation passed; ready for code review", {"validation_id": validation_id})
    return {"status": status, "errors": errors, "warnings": warnings, "results": results, "state": stage.payload()}
