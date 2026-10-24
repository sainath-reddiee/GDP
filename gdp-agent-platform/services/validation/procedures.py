"""CORE.VALIDATE_DBT: VALIDATION_PENDING -> VALIDATION_RUNNING -> PASSED/FAILED -> DBT_REVIEW."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from services.common.audit import tool_call
from services.common.platform_schema import fqn, resolve
from services.common.rules import rule
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.common.standard import run_pipeline, run_standard
from services.soda.expectations import render_yaml
from services.knowledge.usage import use_stage
from services.validation import checks, standards


def _current_generation(session, run_id: str) -> Dict[str, Any]:
    found = rows(session, """SELECT * FROM CORE.DBT_GENERATION_REGISTRY
                             WHERE RUN_ID = ? AND GENERATION_STATUS IN ('GENERATED', 'GENERATING')
                             ORDER BY GENERATION_VERSION DESC LIMIT 1""", [run_id])
    assert found, "no generated dbt project for this run"
    return found[0]


def _files(session, generation_id: str) -> Dict[str, str]:
    return {r["FILE_PATH"]: r["CONTENT"] for r in rows(
        session, "SELECT FILE_PATH, CONTENT FROM CORE.GENERATED_ARTIFACT WHERE GENERATION_ID = ?",
        [generation_id])}


def _sttm_lines(session, sttm_id: str) -> List[Dict[str, Any]]:
    return [{
        "target_column": r["TARGET_COLUMN"], "target_datatype": r["TARGET_DATATYPE"],
        "source_datatype": r.get("SOURCE_DATATYPE"), "mapping_type": r["MAPPING_TYPE"], "transformation": r["TRANSFORMATION"],
        "nullable_rule": r["NULLABLE_RULE"], "uniqueness_rule": r["UNIQUENESS_RULE"],
        "accepted_values": variant(r["ACCEPTED_VALUES"]) or [],
        "required": not r["NULLABLE_RULE"] and r["MAPPING_TYPE"] != "UNMAPPED",
        "business_definition": r["BUSINESS_DEFINITION"],
    } for r in rows(session, "SELECT * FROM CORE.STTM_LINE WHERE STTM_ID = ?", [sttm_id])]


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
    stage_path = resolve(generation["STAGE_PATH"] or "", "stage") or ""  # names before V061 (CODEGEN.DBT_STAGE/...) still resolve
    source = stage_path if stage_path.startswith("@") else f"@{database}.{stage_path}"
    name = fqn(database, safe)
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


MISSING_NODE = ("depends on a node named", "which was not found", "source named")


def gold_compile(result: Dict[str, Any], pipeline: str, files: Dict[str, str]) -> Dict[str, Any]:
    """A Gold run's models ref() the silver hubs and conformed dimensions of the gdp-dbt project. Without the
    repository skeleton they are not in the staged project, so a compile that fails only on a missing model or source
    is a warning that says how to compile for real, not a failure of the generated code (pure)."""
    if pipeline != "GOLD" or result.get("status") != "FAILED" or ("dbt_project.yml" in files and any(
            p.startswith("models/silver") for p in files)):
        return result
    text = " ".join(f.get("message", "") for f in result.get("findings") or [])
    if not any(m in text for m in MISSING_NODE):
        return result
    return {**result, "status": "SKIPPED", "error_count": 0, "warning_count": 1,
            "findings": [{"severity": "WARN", "message": "The gold models reference silver hubs and conformed dimensions "
                          "that live in the gdp-dbt project, so they cannot compile on their own. Set the dbt repository "
                          "on this step so the branch skeleton is fetched, then validate again. " + text[:500]}]}


POST_STTM_VALIDATE = (
    "VALIDATION_PENDING", "VALIDATION_RUNNING", "VALIDATION_FAILED",
    "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING",
)


def fixed_findings(summary_json: Any) -> List[Dict[str, Any]]:
    """The findings of a stored SUMMARY validation (RESULT_JSON {results}), each with its check type. Pure."""
    from services.common.sql import variant

    out = []
    for r in (variant(summary_json) or {}).get("results") or []:
        for f in r.get("findings") or []:
            out.append({**f, "validation_type": r.get("validation_type")})
    return out


def _remember_fixed(session, run: Dict[str, Any], run_id: str) -> None:
    """A pass after a failed validation of the same run: what failed was fixed, so keep it as DBT_PATTERN knowledge
    for this target's next generation and review. Never fails the validation."""
    if not run.get("DOMAIN_ID"):
        return
    try:
        found = rows(session, """SELECT RESULT_JSON FROM CORE.VALIDATION_RUN
                                  WHERE RUN_ID = ? AND VALIDATION_TYPE = 'SUMMARY' AND STATUS = 'FAILED'
                                  ORDER BY COMPLETED_AT DESC LIMIT 1""", [run_id])
        if not found:
            return
        from services.knowledge.signals import keep, validation_fixed

        keep(session, run["DOMAIN_ID"], validation_fixed((run.get("TARGET_MODEL") or "").split(".")[-1] or None,
                                                         fixed_findings(found[0]["RESULT_JSON"])), "DBT", run_id)
    except Exception:
        pass


def validate_dbt(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require(*POST_STTM_VALIDATE)
    walked = False
    # a re-validation after VALIDATION_FAILED runs again through PENDING -> RUNNING, so a pass moves the run on
    if stage.state in ("VALIDATION_FAILED", "VALIDATION_PENDING", "VALIDATION_RUNNING"):
        stage.walk(["VALIDATION_FAILED", "VALIDATION_PENDING", "VALIDATION_RUNNING"], "validation started")
        walked = True
    elif stage.state in ("SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING"):
        # the code was generated while the run waited at the Data Quality gate: once the checks are approved, the
        # same generation goes on to validation instead of being generated again
        _current_generation(session, run_id)
        stage.walk(["SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING"],
                   "validation started on the generated code")
        walked = True
    with tool_call(session, run_id, "validate_dbt", {"run_id": run_id}) as call:
        try:
            generation = _current_generation(session, run_id)
            guidance = use_stage(session, "VALIDATION", run_standard(stage.run), run_id=run_id, excerpt=400)
            files = _files(session, generation["GENERATION_ID"])
            assert files, "generated dbt project has no artifacts"
            soda = files.get("soda/checks.yml") or ""
            results = checks.run(files, _sttm_lines(session, generation["STTM_ID"]), soda)
            results.append(gold_compile(_compile(session, generation, run_id), run_pipeline(stage.run), files))
            if rule("validation.standards_review"):
                # the code standards agent judges the project, one check per area (it replaces the skill note)
                results.extend(_review_standards(session, stage.run, run_id, files, generation)["checks"])
            else:
                results.append({"validation_type": "SKILL", "status": "PASSED", "error_count": 0,
                                "warning_count": 0, "findings": [{"severity": "INFO", "message": guidance[:500]}],
                                "query_id": None})
            # the gate (rules validation.*, active for this run's domain and pipeline since Stage loaded them)
            verdict = checks.gate(results, float(rule("validation.min_score")), bool(rule("validation.block_on_errors")))
            status, errors, warnings = verdict["status"], verdict["errors"], verdict["warnings"]
            validation_id = str(uuid.uuid4())

            def write(_event_id: str) -> None:
                insert_rows(session, "CORE.VALIDATION_RUN",
                            ["VALIDATION_ID", "RUN_ID", "GENERATION_ID", "VALIDATION_TYPE", "STATUS",
                             "ERROR_COUNT", "WARNING_COUNT", "RESULT_JSON", "COMPLETED_AT"],
                            ["?", "?", "?", "?", "?", "?::NUMBER", "?::NUMBER", "PARSE_JSON(?)",
                             "CURRENT_TIMESTAMP()"],
                            [[str(uuid.uuid4()), run_id, generation["GENERATION_ID"], r["validation_type"],
                              r["status"], r["error_count"], r["warning_count"],
                              {"findings": r.get("findings", [])}] for r in results] +
                            [[validation_id, run_id, generation["GENERATION_ID"], "SUMMARY", status,
                              errors, warnings, {"results": results, "gate": verdict}]])

            scored = "no score" if verdict["score"] is None else f"score {verdict['score']:g}% (minimum {verdict['min_score']:g}%)"
            call.summary = (f"{status}: {scored}, {errors} errors, {warnings} warnings across {len(results)} checks"
                            + (f"; {'; '.join(verdict['reasons'])}" if verdict["reasons"] else ""))
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            stage.fail(exc)
            return {"state": stage.payload()}
    next_state = "VALIDATION_PASSED" if status == "PASSED" else "VALIDATION_FAILED"
    if status == "PASSED":
        _remember_fixed(session, stage.run, run_id)
    if walked:
        stage.move(next_state, call.summary, {"errors": errors, "warnings": warnings, "score": verdict["score"],
                                              "min_score": verdict["min_score"], "reasons": verdict["reasons"]},
                   in_transaction=write)
        if next_state == "VALIDATION_PASSED":
            stage.move("DBT_REVIEW", "validation passed; ready for code review", {"validation_id": validation_id})
    else:
        write("")
    return {"status": status, "errors": errors, "warnings": warnings, "score": verdict["score"],
            "min_score": verdict["min_score"], "reasons": verdict["reasons"], "results": results,
            "state": stage.payload()}


def _load(session, names: List[str], run_id: str) -> List[Dict[str, Any]]:
    """Each skill the run would load (its override, else production), skipping one that is not registered."""
    from services.knowledge.procedures import load_skill_for

    out = []
    for name in names:
        try:
            out.append(load_skill_for(session, name, run_id))
        except AssertionError:
            continue
    return out


def _review_standards(session, run: Dict[str, Any], run_id: str, files: Dict[str, str],
                      generation: Dict[str, Any]) -> Dict[str, Any]:
    """The code standards agent on one generation (services/validation/standards.py): its own skill and any
    standards bound to the STANDARDS stage, against the generation playbooks bound to the run's DBT stage. Records
    its tool call and Cortex cost; never raises (a failed review is one SKIPPED check with the reason)."""
    from services.common.audit import record_cost
    from services.common.llm import complete_json
    from services.knowledge.usage import bound_skills, prompt_excerpt

    standard = run_standard(run)
    with tool_call(session, run_id, "review_standards", {"run_id": run_id, "generation_id": generation["GENERATION_ID"]}) as call:
        bound = _load(session, bound_skills(session, standards.STAGE, standard, run_id), run_id)
        own = [s for s in bound if s["skill_name"] == standards.SKILL]
        extra = [s for s in bound if s["skill_name"] != standards.SKILL]
        playbooks = _load(session, bound_skills(session, "DBT", standard, run_id), run_id) + extra
        skill = prompt_excerpt(own[0].get("content") or "", 8000) if own else "(the CODE-STANDARDS-REVIEW skill is not registered)"
        result = standards.review(
            lambda text, schema, max_tokens: complete_json(session, text, schema, max_tokens=max_tokens, stage="DBT"),
            files, skill, standards.standards_text(playbooks, prompt_excerpt), _sttm_lines(session, generation["STTM_ID"]),
            run_pipeline(run), int(rule("validation.standards_max_files")), bool(rule("validation.standards_block")))
        result["skills"] = [f"{s['skill_name']}@{s['version']}" for s in (own + playbooks)]
        call.summary = standards.gate_note(result)
    if result.get("model"):
        record_cost(session, run_id, "VALIDATION", result["model"], result["usage"], result["duration_ms"],
                    agent="CODE_STANDARDS")
    return result


def review_standards(session, run_id: str) -> Dict[str, Any]:
    """CORE.REVIEW_STANDARDS: the code standards agent alone on the run's current generation. It reports and records
    its tool call and cost, but writes no validation and never moves the run (Validate again does that)."""
    stage = Stage(session, run_id)  # activates the rules of the run's domain and pipeline
    generation = _current_generation(session, run_id)
    files = _files(session, generation["GENERATION_ID"])
    result = _review_standards(session, stage.run, run_id, files, generation)
    exact = checks.score(result["checks"])
    return {"run_id": run_id, "generation_id": generation["GENERATION_ID"],
            "generation_version": generation.get("GENERATION_VERSION"), "summary": result["summary"],
            "score": None if exact is None else round(exact, 1), "checks": result["checks"],
            "reviewed": result["reviewed"], "left_out": result["left_out"], "skills": result.get("skills", []),
            "model": result.get("model"), "block": bool(rule("validation.standards_block"))}
