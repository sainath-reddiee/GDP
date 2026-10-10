"""CODEGEN.VALIDATE_DBT: VALIDATION_PENDING -> VALIDATION_RUNNING -> PASSED/FAILED -> DBT_REVIEW."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from services.common.audit import tool_call
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.common.standard import run_standard
from services.soda.expectations import render_yaml
from services.knowledge.usage import use_stage
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
        "source_datatype": r.get("SOURCE_DATATYPE"), "mapping_type": r["MAPPING_TYPE"], "transformation": r["TRANSFORMATION"],
        "nullable_rule": r["NULLABLE_RULE"], "uniqueness_rule": r["UNIQUENESS_RULE"],
        "accepted_values": variant(r["ACCEPTED_VALUES"]) or [],
        "required": not r["NULLABLE_RULE"] and r["MAPPING_TYPE"] != "UNMAPPED",
        "business_definition": r["BUSINESS_DEFINITION"],
    } for r in rows(session, "SELECT * FROM CONTRACT.STTM_LINE WHERE STTM_ID = ?", [sttm_id])]


UNAVAILABLE_MARKERS = ("unsupported feature", "not supported", "insufficient privileges",
                       "unexpected 'dbt'", "unknown object type", "feature is not enabled",
                       "unsupported statement type")  # dbt inside an owner's-rights procedure cannot run SHOW


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
        text = str(exc)
        unavailable = any(marker in text.lower() for marker in UNAVAILABLE_MARKERS)
        if unavailable:  # the account cannot compile dbt projects: deterministic checks decide
            return {"validation_type": "DBT_COMPILE", "status": "SKIPPED", "error_count": 0, "warning_count": 1,
                    "findings": [{"severity": "WARN",
                                  "message": "dbt projects are not available to this role/account, so compile was "
                                             "skipped; deterministic checks still apply: " + clip(exc, 600)}],
                    "query_id": None}
        # A real compile error (bad SQL, missing ref or source) fails validation.
        return {"validation_type": "DBT_COMPILE", "status": "FAILED", "error_count": 1, "warning_count": 0,
                "findings": [{"severity": "ERROR", "message": "dbt compile failed: " + clip(exc, 800)}],
                "query_id": None}


POST_STTM_VALIDATE = (
    "VALIDATION_PENDING", "VALIDATION_RUNNING", "VALIDATION_FAILED",
    "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING",
)


def validate_dbt(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require(*POST_STTM_VALIDATE)
    walked = False
    # a re-validation after VALIDATION_FAILED runs again through PENDING -> RUNNING, so a pass moves the run on
    if stage.state in ("VALIDATION_FAILED", "VALIDATION_PENDING", "VALIDATION_RUNNING"):
        stage.walk(["VALIDATION_FAILED", "VALIDATION_PENDING", "VALIDATION_RUNNING"], "validation started")
        walked = True
    with tool_call(session, run_id, "validate_dbt", {"run_id": run_id}) as call:
        try:
            generation = _current_generation(session, run_id)
            guidance = use_stage(session, "VALIDATION", run_standard(stage.run), run_id=run_id, excerpt=400)
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
    if walked:
        stage.move(next_state, call.summary, {"errors": errors, "warnings": warnings}, in_transaction=write)
        if next_state == "VALIDATION_PASSED":
            stage.move("DBT_REVIEW", "validation passed; ready for code review", {"validation_id": validation_id})
    else:
        write("")
    return {"status": status, "errors": errors, "warnings": warnings, "results": results, "state": stage.payload()}
