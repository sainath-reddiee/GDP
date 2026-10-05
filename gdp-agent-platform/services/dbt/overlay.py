"""API-side dbt write that does not walk the workflow graph.

Snowflake GENERATE_DBT still require()s early post-STTM states. After Data Quality
moves the run to VALIDATION_PENDING, regenerate/push must run here instead.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

from services.dbt.procedures import _artifact_type, merge_branch_plan
from services.dbt.project import build
from services.dbt.workspace import merge_skeleton, origin_allowed, safe_fqn
from services.soda.expectations import render_yaml

TEXT_SUFFIXES = (".sql", ".yml", ".yaml", ".md", ".json", ".csv", ".txt", ".toml")


def _upper(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).upper(): v for k, v in row.items()}


def _json(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def _put_files(db, stage_root: str, files: Dict[str, str]) -> None:
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
            uri = (Path(tmp) / rel).resolve().as_posix()
            if not uri.startswith("/"):
                uri = f"/{uri}"
            db.execute(f"PUT 'file://{uri}' {remote} AUTO_COMPRESS = FALSE OVERWRITE = TRUE")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fetch_skeleton(db, repo_fqn: str, branch: str, limit: int = 80) -> Dict[str, str]:
    repo = safe_fqn(repo_fqn)
    branch_name = (branch or "main").strip().strip("/")
    try:
        db.execute(f"ALTER GIT REPOSITORY {repo} FETCH")
    except Exception:
        pass
    listed = db.query(f"LIST @{repo}/branches/{branch_name}/")
    files: Dict[str, str] = {}
    for row in listed:
        name = str(row.get("name") or "")
        rel = name.split(f"/branches/{branch_name}/", 1)[-1].lstrip("/")
        if not rel or not rel.lower().endswith(TEXT_SUFFIXES):
            continue
        if len(files) >= limit:
            break
        try:
            chunks = db.query(f"SELECT $1 AS TXT FROM @{repo}/branches/{branch_name}/{rel}")
            text = "\n".join(str(c.get("txt")) for c in chunks if c.get("txt") is not None)
            if text:
                files[rel] = text
        except Exception:
            continue
    return files


def _create_project(db, project_fqn: str, stage_path: str, comment: str) -> Dict[str, Any]:
    name = safe_fqn(project_fqn)
    source = stage_path if stage_path.startswith("@") else f"@{stage_path}"
    db.execute(
        f"CREATE OR REPLACE DBT PROJECT {name} FROM '{source}' "
        f"AUTO_COMPILE = FALSE DEFAULT_WRITEBACK = FALSE "
        f"COMMENT = '{comment.replace(chr(39), '')[:200]}'"
    )
    return {"dbt_project": name, "from": source, "status": "CREATED"}


def _push_branch(db, repo_fqn: str, cut_branch: str, stage_path: str) -> Dict[str, Any]:
    repo = safe_fqn(repo_fqn)
    branch = (cut_branch or "").strip().strip("/")
    source = stage_path if stage_path.startswith("@") else f"@{stage_path}"
    try:
        db.execute(f"ALTER GIT REPOSITORY {repo} FETCH")
    except Exception as exc:
        return {"status": "FETCH_FAILED", "detail": str(exc)[:400]}
    try:
        db.execute(f"COPY FILES INTO @{repo}/branches/{branch}/ FROM {source} OVERWRITE = TRUE")
        return {
            "status": "PUSHED",
            "repo": repo,
            "branch": branch,
            "from": source,
            "method": f"COPY FILES INTO @{repo}/branches/{branch}/",
            "pull_request": False,
        }
    except Exception as exc:
        return {"status": "COPY_FAILED", "detail": str(exc)[:400], "repo": repo, "branch": branch}


def generate_via_db(db, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Regenerate models, optional CREATE DBT PROJECT, optional COPY FILES. No state walk."""
    run_rows = db.query(
        "SELECT RUN_ID, RUN_NAME, DOMAIN_ID, TARGET_MODEL, CURRENT_STATE "
        "FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s",
        (run_id,),
    )
    assert run_rows, f"run not found: {run_id}"
    run = _upper(run_rows[0])
    sttm_rows = db.query(
        """
        SELECT * FROM CONTRACT.STTM_REGISTRY
         WHERE RUN_ID = %s AND STATUS IN ('REVIEW', 'APPROVED')
         ORDER BY STTM_VERSION DESC LIMIT 1
        """,
        (run_id,),
    )
    assert sttm_rows, "no current STTM for this run"
    sttm = _upper(sttm_rows[0])
    design = _json(sttm.get("TABLE_DESIGN")) or {}
    line_rows = db.query(
        "SELECT * FROM CONTRACT.STTM_LINE WHERE STTM_ID = %s ORDER BY TARGET_COLUMN",
        (sttm["STTM_ID"],),
    )
    lines = [{
        "target_column": r.get("target_column"), "target_datatype": r.get("target_datatype"),
        "source_column": r.get("source_column"), "source_table": r.get("source_table"),
        "mapping_type": r.get("mapping_type"), "transformation": r.get("transformation"),
        "nullable_rule": r.get("nullable_rule"), "uniqueness_rule": r.get("uniqueness_rule"),
        "accepted_values": _json(r.get("accepted_values")) or [],
        "business_definition": r.get("business_definition"),
    } for r in line_rows]
    soda_rows = db.query(
        """
        SELECT TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION,
               SEVERITY, ORIGIN, CLIENT_REQUIREMENT
          FROM CONTRACT.SODA_EXPECTATION_REGISTRY
         WHERE RUN_ID = %s AND IS_CURRENT AND STATUS IN ('PROPOSED', 'APPROVED')
        """,
        (run_id,),
    )
    checks = [{
        "target_table": r.get("target_table"), "target_column": r.get("target_column"),
        "check_type": r.get("check_type"), "definition": _json(r.get("check_definition")) or {},
        "severity": r.get("severity"), "origin": r.get("origin"),
        "requirement": r.get("client_requirement"),
    } for r in soda_rows]
    soda_yaml = render_yaml((design.get("target_table") or "dim_customer").lower(), checks)
    source_rows = db.query(
        """
        SELECT S.SOURCE_SYSTEM_NAME FROM SOURCE.SOURCE_REGISTRY S
          JOIN CORE.WORKFLOW_RUN R ON R.SOURCE_SYSTEM_ID = S.SOURCE_SYSTEM_ID
         WHERE R.RUN_ID = %s
        """,
        (run_id,),
    )
    source_name = (source_rows[0].get("source_system_name") if source_rows else None) or "SOURCE"
    landing = [{
        "source_table": r.get("source_table"), "landing_table": r.get("landing_table"),
        "database": r.get("landing_database"), "schema": r.get("landing_schema"),
    } for r in db.query(
        """
        SELECT SOURCE_TABLE, LANDING_TABLE, LANDING_DATABASE, LANDING_SCHEMA
          FROM SOURCE.LANDING_TABLE_REGISTRY
         WHERE RUN_ID = %s AND INGESTION_STATUS = 'COMPLETE'
        QUALIFY ROW_NUMBER() OVER (PARTITION BY SOURCE_TABLE ORDER BY CREATED_AT DESC) = 1
        """,
        (run_id,),
    )]
    files = build(
        {"sttm_id": sttm["STTM_ID"], "table_design": design, "lines": lines},
        [], soda_yaml, landing, source_name,
    )
    prior_rows = db.query(
        "SELECT CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE "
        "WHERE IS_CURRENT AND SOURCE_REFERENCE = %s",
        (f"dbt.branch.{run_id}",),
    )
    prior = _json(prior_rows[0].get("content_json")) if prior_rows else {}
    if not isinstance(prior, dict):
        prior = {}
    plan = merge_branch_plan(payload or {}, prior, run.get("RUN_NAME") or "", run_id)
    skeleton: Dict[str, str] = {}
    if plan.get("fetch_skeleton") and plan.get("git_repository"):
        try:
            skeleton = _fetch_skeleton(db, plan["git_repository"], plan["base_branch"])
        except Exception as exc:
            plan["skeleton_error"] = str(exc)[:400]
    if skeleton:
        files = merge_skeleton(skeleton, files)
        plan["skeleton_files"] = len(skeleton)
    prefixes = payload.get("allowed_prefixes") or []
    if plan.get("origin") and prefixes and not origin_allowed(plan["origin"], prefixes):
        raise AssertionError(f"origin {plan['origin']} is not in the API integration allowed prefixes")
    instruction = (
        f"Cut `{plan['cut_branch']}` from `{plan['base_branch']}`"
        + (f" in {plan.get('origin') or plan['repo']}" if (plan.get("origin") or plan["repo"]) else "")
        + " using DBT-ONBOARD-SOURCE on the approved STTM. "
        + "Review the models, then push the cut branch into the Snowflake git repository."
    )
    files["release/branch.json"] = json.dumps({**plan, "instruction": instruction}, indent=2)
    files["release/skills.json"] = json.dumps({
        "applied": [],
        "domain_id": run.get("DOMAIN_ID"),
        "source": "DBT-ONBOARD-SOURCE + STTM (API overlay, no workflow walk)",
    }, indent=2)
    version_rows = db.query(
        "SELECT COALESCE(MAX(GENERATION_VERSION), 0) + 1 AS V "
        "FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE RUN_ID = %s",
        (run_id,),
    )
    version = version_rows[0]["v"] if version_rows else 1
    generation_id = str(uuid.uuid4())
    stage_path = f"CODEGEN.DBT_STAGE/{run_id}/v{version}"
    _put_files(db, stage_path, files)
    workspace: Dict[str, Any] = {
        "stage_path": f"@{stage_path}",
        "overlay": True,
        "pull_request": {
            "created": False,
            "reason": (
                "GDP copies files onto the Snowflake git branch with COPY FILES. "
                "It does not open a GitHub or GitLab pull request."
            ),
        },
    }
    project_name = plan.get("dbt_project") or (
        f"{_current_database(db)}.CODEGEN.GDP_{run_id.replace('-', '')[:18]}_V{version}"
    )
    try:
        workspace["dbt_project"] = _create_project(db, project_name, f"@{stage_path}", f"GDP run {run_id} compile-only")
    except Exception as exc:
        workspace["dbt_project"] = {"status": "SKIPPED", "detail": str(exc)[:400]}
    if plan.get("push") and plan.get("git_repository"):
        workspace["push"] = _push_branch(db, plan["git_repository"], plan["cut_branch"], f"@{stage_path}")
    else:
        workspace["push"] = {"status": "NOT_REQUESTED"}
    workspace["lineage"] = {
        "sttm_id": sttm["STTM_ID"],
        "skills": ["DBT-ONBOARD-SOURCE"],
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
    _put_files(db, stage_path, {"release/workspace.json": files["release/workspace.json"]})
    plan["workspace"] = workspace
    db.execute(
        "UPDATE CODEGEN.DBT_GENERATION_REGISTRY SET GENERATION_STATUS = 'SUPERSEDED' "
        "WHERE RUN_ID = %s AND GENERATION_STATUS IN ('GENERATING', 'GENERATED')",
        (run_id,),
    )
    domain = "GDP"
    if run.get("DOMAIN_ID"):
        domain_rows = db.query(
            "SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s",
            (run["DOMAIN_ID"],),
        )
        if domain_rows:
            domain = domain_rows[0].get("domain_name") or domain
    db.execute(
        """
        INSERT INTO CODEGEN.DBT_GENERATION_REGISTRY
          (GENERATION_ID, RUN_ID, DOMAIN, TARGET_MODEL, STTM_ID, STTM_VERSION,
           FILES_GENERATED, SKILL_VERSION, KNOWLEDGE_VERSION, MODEL_VERSION,
           GENERATION_VERSION, GENERATION_STATUS, STAGE_PATH, CREATED_BY)
        SELECT %s, %s, %s, %s, %s, %s, %s, 'GDP_DOMAIN_SKILL:1.0.0',
               NULL, 'dbt-deterministic-v1', %s, 'GENERATED', %s, CURRENT_USER()
        """,
        (
            generation_id, run_id, domain,
            run.get("TARGET_MODEL") or design.get("target_table"),
            sttm["STTM_ID"], sttm.get("STTM_VERSION"), len(files), version, f"@{stage_path}",
        ),
    )
    for path, content in files.items():
        db.execute(
            """
            INSERT INTO CODEGEN.GENERATED_ARTIFACT
              (ARTIFACT_ID, GENERATION_ID, RUN_ID, ARTIFACT_TYPE, FILE_PATH, CONTENT, CONTENT_SHA256)
            SELECT %s, %s, %s, %s, %s, %s, %s
            """,
            (
                str(uuid.uuid4()), generation_id, run_id, _artifact_type(path), path, content,
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
            ),
        )
    return {
        "generation_id": generation_id,
        "version": version,
        "files": list(files),
        "branch": plan,
        "workspace": workspace,
        "overlay": True,
        "state": {"current_state": run.get("CURRENT_STATE")},
    }


def _current_database(db) -> str:
    rows = db.query("SELECT CURRENT_DATABASE() AS DB")
    return (rows[0].get("db") if rows else None) or "DEV_AI_PLATFORM"
