"""CODEGEN.GENERATE_DBT: runs from the approved STTM in parallel with Soda."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

from services.common.audit import tool_call
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.dbt.project import build
from services.dbt.workspace import create_dbt_project, fetch_branch_files, merge_skeleton, origin_allowed, push_pending
from services.knowledge.procedures import current_knowledge_version, load_skill
from services.knowledge.usage import STAGE_SKILLS, use_skills
from services.soda.expectations import render_yaml
from services.soda.procedures import _current_sttm, _lines


def _landing_tables(session, run_id: str) -> List[Dict[str, str]]:
    return [{
        "source_table": r["SOURCE_TABLE"],
        "landing_table": r["LANDING_TABLE"],
        "database": r["LANDING_DATABASE"],
        "schema": r["LANDING_SCHEMA"],
    } for r in rows(session, """
        SELECT SOURCE_TABLE, LANDING_TABLE, LANDING_DATABASE, LANDING_SCHEMA
          FROM SOURCE.LANDING_TABLE_REGISTRY
         WHERE RUN_ID = ? AND INGESTION_STATUS = 'COMPLETE'
       QUALIFY ROW_NUMBER() OVER (PARTITION BY SOURCE_TABLE ORDER BY CREATED_AT DESC) = 1
    """, [run_id])]


def _macros(session) -> List[Dict[str, str]]:
    skill = load_skill(session, "GDP_DOMAIN_SKILL")
    config = skill.get("config") or {}
    return [{"name": m["name"], "sql": m["sql"]} for m in config.get("macros", []) if m.get("sql")]


def _put_files(session, stage_root: str, files: Dict[str, str]) -> None:
    tmp = tempfile.mkdtemp()
    try:
        for rel, content in files.items():
            local = Path(tmp) / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
        for rel in files:
            posix = rel.replace("\\", "/")
            parent = posix.rsplit("/", 1)[0] if "/" in posix else ""
            remote = f"@{stage_root}/{parent}" if parent else f"@{stage_root}"
            session.file.put((Path(tmp) / rel).as_posix(), remote, auto_compress=False, overwrite=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


POST_STTM = (
    "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
    "DBT_PENDING", "DBT_GENERATING",
    "VALIDATION_PENDING", "VALIDATION_RUNNING", "VALIDATION_PASSED", "VALIDATION_FAILED",
    "DBT_REVIEW",
)


def merge_branch_plan(payload: Dict[str, Any], prior: Dict[str, Any], run_name: str, run_id: str) -> Dict[str, Any]:
    """Merge the request body over the last stored plan. Empty/null fields do not wipe prior values."""
    def pick(*keys: str, default: str = "") -> str:
        for src in (payload, prior):
            for key in keys:
                value = src.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
        return default

    def flag(key: str, default: bool) -> bool:
        for src in (payload, prior):
            if key in src and src[key] is not None:
                return bool(src[key])
        return default

    slug = "".join(c.lower() if c.isalnum() else "-" for c in (run_name or run_id)[:40]).strip("-") or "run"
    origin = pick("origin", "repo")
    return {
        "base_branch": pick("base_branch", default="main") or "main",
        "cut_branch": pick("cut_branch", default=f"feat/gdp-{slug}"),
        "repo": pick("repo", "origin"),
        "origin": origin,
        "git_repository": pick("git_repository"),
        "api_integration": pick("api_integration"),
        "dbt_project": pick("dbt_project"),
        "push": flag("push", False),
        "fetch_skeleton": flag("fetch_skeleton", True),
        "allowed_prefixes": payload.get("allowed_prefixes") or prior.get("allowed_prefixes") or [],
    }


def _stored_plan(session, run_id: str) -> Dict[str, Any]:
    stored = rows(session, """SELECT CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                              WHERE IS_CURRENT AND SOURCE_REFERENCE = ?""", [f"dbt.branch.{run_id}"])
    return variant(stored[0]["CONTENT_JSON"]) if stored else {}


def _branch_plan(session, run_id: str, payload: Dict[str, Any], run_name: str) -> Dict[str, Any]:
    return merge_branch_plan(payload or {}, _stored_plan(session, run_id), run_name, run_id)


def _store_branch(session, run_id: str, domain_id: str, plan: Dict[str, str], instruction: str) -> None:
    if not domain_id:
        return
    ref = f"dbt.branch.{run_id}"
    session.sql("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, STATUS = 'RETIRED'
                   WHERE SOURCE_REFERENCE = ? AND IS_CURRENT""", params=[ref]).collect()
    version = (scalar(session, "SELECT MAX(VERSION) FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = ?",
                      [ref]) or 0) + 1
    insert_rows(session, "KNOWLEDGE.DOMAIN_KNOWLEDGE",
                ["KNOWLEDGE_ID", "DOMAIN_ID", "KNOWLEDGE_TYPE", "TITLE", "CONTENT", "CONTENT_JSON",
                 "TAGS", "SOURCE_REFERENCE", "STATUS", "VERSION", "IS_CURRENT", "CREATED_BY"],
                ["?", "?", "'TRANSFORMATION_RULE'", "?", "?", "PARSE_JSON(?)", "PARSE_JSON(?)", "?",
                 "'ACTIVE'", "?::NUMBER", "TRUE", "CURRENT_USER()"],
                [[str(uuid.uuid4()), domain_id, f"dbt branch plan {run_id}"[:500],
                  clip(instruction, 8000), {**plan, "run_id": run_id},
                  ["DBT", "BRANCH", "RELEASE"], ref, version]])


def generate_dbt_simple(session, run_id: str) -> Dict[str, Any]:
    """One-arg Snowflake signature: reuse the last persisted branch plan."""
    return generate_dbt(session, run_id, json.dumps(_stored_plan(session, run_id) or {}))


def generate_dbt(session, run_id: str, payload_json: str = "{}") -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require(*POST_STTM)
    payload = json.loads(payload_json or "{}")
    walked = False
    if stage.state in ("STTM_APPROVED", "DBT_PENDING", "SODA_APPROVED"):
        try:
            if stage.state == "STTM_APPROVED":
                stage.move("DBT_PENDING", "dbt generation started")
            if stage.state == "SODA_APPROVED":
                stage.move("DBT_PENDING", "dbt generation started")
            stage.walk(["DBT_PENDING", "DBT_GENERATING"], "dbt generation started")
            walked = True
        except Exception:
            walked = False
    with tool_call(session, run_id, "generate_dbt", {"run_id": run_id}) as call:
        try:
            use_skills(session, STAGE_SKILLS["DBT"])
            sttm = _current_sttm(session, run_id)
            design = variant(sttm["TABLE_DESIGN"]) or {}
            lines = [{
                "target_column": r["TARGET_COLUMN"], "target_datatype": r["TARGET_DATATYPE"],
                "source_column": r["SOURCE_COLUMN"], "source_table": r["SOURCE_TABLE"],
                "mapping_type": r["MAPPING_TYPE"], "transformation": r["TRANSFORMATION"],
                "nullable_rule": r["NULLABLE_RULE"], "uniqueness_rule": r["UNIQUENESS_RULE"],
                "accepted_values": variant(r["ACCEPTED_VALUES"]) or [],
                "business_definition": r["BUSINESS_DEFINITION"],
            } for r in rows(session, "SELECT * FROM CONTRACT.STTM_LINE WHERE STTM_ID = ? ORDER BY TARGET_COLUMN",
                            [sttm["STTM_ID"]])]
            soda_rows = rows(session, """SELECT TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION,
                                         SEVERITY, ORIGIN, CLIENT_REQUIREMENT
                                         FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                         WHERE RUN_ID = ? AND IS_CURRENT AND STATUS IN ('PROPOSED', 'APPROVED')""",
                             [run_id])
            checks = [{
                "target_table": r["TARGET_TABLE"], "target_column": r["TARGET_COLUMN"],
                "check_type": r["CHECK_TYPE"], "definition": variant(r["CHECK_DEFINITION"]) or {},
                "severity": r["SEVERITY"], "origin": r["ORIGIN"], "requirement": r["CLIENT_REQUIREMENT"],
            } for r in soda_rows]
            soda_yaml = render_yaml((design.get("target_table") or "dim_customer").lower(), checks)
            source_name = scalar(session, """SELECT S.SOURCE_SYSTEM_NAME FROM SOURCE.SOURCE_REGISTRY S
                                             JOIN CORE.WORKFLOW_RUN R ON R.SOURCE_SYSTEM_ID = S.SOURCE_SYSTEM_ID
                                             WHERE R.RUN_ID = ?""", [run_id]) or "SOURCE"
            files = build({"sttm_id": sttm["STTM_ID"], "table_design": design, "lines": lines}, _macros(session), soda_yaml,
                          _landing_tables(session, run_id), source_name)
            plan = _branch_plan(session, run_id, payload, stage.run.get("RUN_NAME") or "")
            skill_names = STAGE_SKILLS["DBT"]
            skill_meta = []
            for name in skill_names:
                try:
                    loaded = load_skill(session, name)
                    skill_meta.append({"name": loaded.get("skill_name") or name,
                                       "version": loaded.get("version"),
                                       "description": (loaded.get("description") or "")[:240]})
                except Exception:
                    skill_meta.append({"name": name, "version": None, "description": ""})
            skeleton: Dict[str, str] = {}
            if plan.get("fetch_skeleton") and plan.get("git_repository"):
                try:
                    skeleton = fetch_branch_files(session, plan["git_repository"], plan["base_branch"])
                except Exception as exc:
                    plan["skeleton_error"] = clip(exc, 400)
            if skeleton:
                files = merge_skeleton(skeleton, files)
                plan["skeleton_files"] = len(skeleton)
            prefixes = payload.get("allowed_prefixes") or []
            if plan.get("origin") and prefixes and not origin_allowed(plan["origin"], prefixes):
                raise AssertionError(
                    f"origin {plan['origin']} is not in the API integration allowed prefixes"
                )
            instruction = (
                f"Cut `{plan['cut_branch']}` from `{plan['base_branch']}`"
                + (f" in {plan.get('origin') or plan['repo']}" if (plan.get("origin") or plan["repo"]) else "")
                + " using DBT-ONBOARD-SOURCE on the approved STTM. "
                + "Review the models, then publish the branch and pull request to GitHub."
            )
            files["release/branch.json"] = json.dumps({**plan, "instruction": instruction}, indent=2)
            files["release/skills.json"] = json.dumps({
                "applied": skill_meta,
                "domain_id": stage.run.get("DOMAIN_ID"),
                "source": "DBT-ONBOARD-SOURCE + STTM + domain skill macros",
            }, indent=2)
            version = (scalar(session, "SELECT MAX(GENERATION_VERSION) FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE RUN_ID = ?",
                              [run_id]) or 0) + 1
            generation_id = str(uuid.uuid4())
            kv = current_knowledge_version(session, stage.run["DOMAIN_ID"])
            stage_path = f"CODEGEN.DBT_STAGE/{run_id}/v{version}"
            _put_files(session, stage_path, files)
            workspace: Dict[str, Any] = {
                "stage_path": f"@{stage_path}",
                "pull_request": {
                    "created": False,
                    "reason": "Opened by CODEGEN.PUBLISH_DBT_PR through the GitHub API after generation.",
                },
            }
            project_name = plan.get("dbt_project") or (
                f"{scalar(session, 'SELECT CURRENT_DATABASE()')}.CODEGEN.GDP_{run_id.replace('-', '')[:18]}_V{version}"
            )
            try:
                workspace["dbt_project"] = create_dbt_project(
                    session, project_name, f"@{stage_path}", f"GDP run {run_id} compile-only",
                )
            except Exception as exc:
                workspace["dbt_project"] = {"status": "SKIPPED", "detail": clip(exc, 400)}
            workspace["push"] = push_pending(plan, True)
            workspace["lineage"] = {
                "sttm_id": sttm["STTM_ID"],
                "skills": skill_names,
                "skeleton": {
                    "requested": bool(plan.get("fetch_skeleton")),
                    "files": plan.get("skeleton_files") or 0,
                    "branch": plan.get("base_branch"),
                    "error": plan.get("skeleton_error"),
                },
                "stage": f"@{stage_path}",
                "files": len(files),
                "dbt_project": workspace.get("dbt_project"),
                "git_copy": workspace.get("push"),
                "pull_request": workspace["pull_request"],
            }
            files["release/workspace.json"] = json.dumps(workspace, indent=2)
            _put_files(session, stage_path, {"release/workspace.json": files["release/workspace.json"]})
            plan["workspace"] = workspace

            def write(_event_id: str) -> None:
                session.sql("UPDATE CODEGEN.DBT_GENERATION_REGISTRY SET GENERATION_STATUS = 'SUPERSEDED' "
                            "WHERE RUN_ID = ? AND GENERATION_STATUS IN ('GENERATING', 'GENERATED')",
                            params=[run_id]).collect()
                insert_rows(session, "CODEGEN.DBT_GENERATION_REGISTRY",
                            ["GENERATION_ID", "RUN_ID", "DOMAIN", "TARGET_MODEL", "STTM_ID", "STTM_VERSION",
                             "FILES_GENERATED", "SKILL_VERSION", "KNOWLEDGE_VERSION", "MODEL_VERSION",
                             "GENERATION_VERSION", "GENERATION_STATUS", "STAGE_PATH", "CREATED_BY"],
                            ["?", "?", "?", "?", "?", "?::NUMBER", "?::NUMBER", "'GDP_DOMAIN_SKILL:1.0.0'",
                             "?", "'dbt-deterministic-v1'", "?::NUMBER", "'GENERATED'", "?", "CURRENT_USER()"],
                            [[generation_id, run_id,
                              scalar(session, "SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?",
                                     [stage.run["DOMAIN_ID"]]) or "GDP",
                              stage.run.get("TARGET_MODEL") or design.get("target_table"),
                              sttm["STTM_ID"], sttm["STTM_VERSION"], len(files), kv, version, f"@{stage_path}"]])
                insert_rows(session, "CODEGEN.GENERATED_ARTIFACT",
                            ["ARTIFACT_ID", "GENERATION_ID", "RUN_ID", "ARTIFACT_TYPE", "FILE_PATH", "CONTENT",
                             "CONTENT_SHA256"],
                            ["?", "?", "?", "?", "?", "?", "?"],
                            [[str(uuid.uuid4()), generation_id, run_id, _artifact_type(path), path, content,
                              hashlib.sha256(content.encode("utf-8")).hexdigest()]
                             for path, content in files.items()])
                _store_branch(session, run_id, stage.run.get("DOMAIN_ID") or sttm.get("DOMAIN_ID"), plan, instruction)

            call.summary = f"dbt v{version}: {len(files)} files on @{stage_path}; push={workspace['push'].get('status')}"
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            if walked:
                stage.fail(exc)
            return {"state": stage.payload(), "error": clip(exc)}
    if walked:
        stage.move("VALIDATION_PENDING", call.summary,
                   {"generation_id": generation_id, "files": list(files), "branch": plan}, in_transaction=write)
    else:
        write("")
    return {"generation_id": generation_id, "version": version, "files": list(files),
            "branch": plan, "workspace": plan.get("workspace"), "state": stage.payload()}


def _artifact_type(path: str) -> str:
    if path.endswith("dbt_project.yml"):
        return "DBT_PROJECT"
    if path.startswith("macros/"):
        return "DBT_MACRO"
    if path.endswith("_sources.yml"):
        return "DBT_SOURCES_YML"
    if path.endswith(".yml"):
        return "DBT_SCHEMA_YML"
    if path.startswith("soda/"):
        return "SODA_CHECKS"
    if path.startswith("mappings/"):
        return "STTM_EXPORT"
    if path.startswith("release/"):
        return "RELEASE_PLAN"
    return "DBT_MODEL"
