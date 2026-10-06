"""CODEGEN.PUBLISH_DBT_PR: push the latest generated dbt code to a new GitHub branch and open a pull request.

Runs inside Snowflake with an external access integration to api.github.com and a SECRET holding a GitHub
token, so the token never leaves Snowflake. After the push, the branch is FETCHed into the Snowflake git
repository clone (when one is set) and a compile-only DBT PROJECT is created from that branch.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, Optional

from services.common.audit import tool_call
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.dbt import github
from services.dbt.workspace import safe_fqn

SECRET_ALIAS = "github_token"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")


def procedure_sql(database: str, services_import: str, eai: str, secret: str) -> str:
    """DDL for the publisher; shared by the setup endpoint and the deploy script."""
    for value in (database, eai, secret):
        assert NAME.match(value or ""), f"unsafe identifier: {value}"
    assert re.fullmatch(r"@[A-Za-z0-9_./$-]+\.zip", services_import or ""), f"unsafe import: {services_import}"
    return (
        f"CREATE OR REPLACE PROCEDURE {database}.CODEGEN.PUBLISH_DBT_PR(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)\n"
        "  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'\n"
        "  PACKAGES = ('snowflake-snowpark-python')\n"
        f"  IMPORTS = ('{services_import}')\n"
        "  HANDLER = 'services.dbt.publish.publish_dbt_pr'\n"
        f"  EXTERNAL_ACCESS_INTEGRATIONS = ({eai})\n"
        f"  SECRETS = ('{SECRET_ALIAS}' = {secret})\n"
        "  COMMENT = 'Push generated dbt code to a new GitHub branch and open a pull request'\n"
        "  EXECUTE AS OWNER"
    )


def setup_sql(database: str, eai: str, secret: str, create_secret: bool) -> list[str]:
    """Network rule + (optional) secret + external access integration. The token is bound separately."""
    for value in (database, eai, secret):
        assert NAME.match(value or ""), f"unsafe identifier: {value}"
    statements = [
        f"CREATE NETWORK RULE IF NOT EXISTS {database}.CODEGEN.GITHUB_API_EGRESS MODE = EGRESS TYPE = HOST_PORT "
        "VALUE_LIST = ('api.github.com')",
    ]
    if create_secret:
        statements.append(f"CREATE OR REPLACE SECRET {secret} TYPE = GENERIC_STRING SECRET_STRING = '<github token>'")
    statements.append(
        f"CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION {eai} "
        f"ALLOWED_NETWORK_RULES = ({database}.CODEGEN.GITHUB_API_EGRESS) "
        f"ALLOWED_AUTHENTICATION_SECRETS = ({secret}) ENABLED = TRUE "
        "COMMENT = 'GDP: push generated dbt branches and open pull requests'"
    )
    return statements


def _token() -> str:
    import _snowflake  # available inside Snowflake procedures only

    try:
        value = _snowflake.get_generic_secret_string(SECRET_ALIAS)
        if value:
            return value
    except Exception:
        pass
    return _snowflake.get_username_password(SECRET_ALIAS).password


def _plan(session, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    stored = rows(session, "SELECT CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE IS_CURRENT AND SOURCE_REFERENCE = ?",
                  [f"dbt.branch.{run_id}"])
    plan = dict(variant(stored[0]["CONTENT_JSON"]) or {}) if stored else {}
    for key in ("origin", "base_branch", "cut_branch", "git_repository", "title", "draft", "create_project"):
        if payload.get(key) not in (None, ""):
            plan[key] = payload[key]
    return plan


def _record(session, run_id: str, generation_id: Optional[str], plan: Dict[str, Any], result: Dict[str, Any]) -> None:
    pr = result.get("pull_request") or {}
    insert_rows(session, "CODEGEN.GIT_PUBLICATION",
                ["PUBLICATION_ID", "RUN_ID", "GENERATION_ID", "PROVIDER", "ORIGIN", "BASE_BRANCH", "HEAD_BRANCH",
                 "COMMIT_SHA", "FILES_PUSHED", "PR_NUMBER", "PR_URL", "DBT_PROJECT", "STATUS", "DETAIL", "CREATED_BY"],
                ["?", "?", "NULLIF(?, '')", "'GITHUB'", "?", "?", "?", "NULLIF(?, '')", "NULLIF(?, '')::NUMBER",
                 "NULLIF(?, '')::NUMBER", "NULLIF(?, '')", "NULLIF(?, '')", "?", "NULLIF(?, '')", "CURRENT_USER()"],
                [[str(uuid.uuid4()), run_id, generation_id, plan.get("origin") or "", plan.get("base_branch") or "",
                  plan.get("cut_branch") or "", result.get("commit_sha"), result.get("files_pushed"), pr.get("number"),
                  pr.get("url"), (result.get("dbt_project") or {}).get("dbt_project"), result.get("status") or "FAILED",
                  clip(result.get("detail") or (result.get("dbt_project") or {}).get("detail"), 4000)]])


def project_from_branch(session, repo_fqn: str, branch: str, project_fqn: str, comment: str) -> Dict[str, Any]:
    """FETCH the pushed branch into the Snowflake clone and point a compile-only DBT PROJECT at it."""
    repo, name = safe_fqn(repo_fqn), safe_fqn(project_fqn)
    head = github.check_branch(branch)
    session.sql(f"ALTER GIT REPOSITORY {repo} FETCH").collect()
    source = f"@{repo}/branches/{head}"
    session.sql(
        f"CREATE OR REPLACE DBT PROJECT {name} FROM '{source}' "
        f"AUTO_COMPILE = FALSE DEFAULT_WRITEBACK = FALSE COMMENT = '{comment.replace(chr(39), '')[:200]}'"
    ).collect()
    return {"status": "CREATED", "dbt_project": name, "from": source}


def publish_dbt_pr(session, run_id: str, payload_json: str = "{}") -> Dict[str, Any]:
    payload = json.loads(payload_json or "{}")
    plan = _plan(session, run_id, payload) if run_id else dict(payload)
    if payload.get("check_only"):
        try:
            return {"status": "OK", **github.preflight(github.urllib_request(_token()), plan.get("origin") or "")}
        except Exception as exc:
            return {**github.explain(exc, plan.get("origin") or ""), "check": True}
    run = rows(session, "SELECT RUN_ID, RUN_NAME FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    assert run, f"run not found: {run_id}"
    gen = rows(session, """SELECT GENERATION_ID, GENERATION_VERSION, STTM_ID FROM CODEGEN.DBT_GENERATION_REGISTRY
                           WHERE RUN_ID = ? ORDER BY GENERATION_VERSION DESC LIMIT 1""", [run_id])
    generation_id = gen[0]["GENERATION_ID"] if gen else None
    result: Dict[str, Any]
    with tool_call(session, run_id, "publish_dbt_pr", {"cut_branch": plan.get("cut_branch")}) as call:
        try:
            assert gen, "generate dbt before publishing"
            files = {r["FILE_PATH"]: r["CONTENT"] for r in rows(
                session, "SELECT FILE_PATH, CONTENT FROM CODEGEN.GENERATED_ARTIFACT WHERE GENERATION_ID = ?",
                [generation_id]) if r["CONTENT"] is not None}
            name = run[0]["RUN_NAME"] or run_id
            version = gen[0]["GENERATION_VERSION"]
            result = github.publish(
                github.urllib_request(_token()),
                plan.get("origin") or "", plan.get("base_branch") or "main", plan.get("cut_branch") or "",
                files,
                title=plan.get("title") or f"GDP: onboard {name} (dbt v{version})",
                body=github.pr_body(name, run_id, sorted(files), gen[0]["STTM_ID"]),
                message=f"GDP run {name}: dbt v{version} from the approved STTM",
                draft=bool(plan.get("draft")),
            )
            if plan.get("git_repository") and plan.get("create_project", True) and result["status"] != "FAILED":
                project = plan.get("dbt_project") or (
                    f"{scalar(session, 'SELECT CURRENT_DATABASE()')}.CODEGEN.GDP_{run_id.replace('-', '')[:18]}_BRANCH")
                try:
                    result["dbt_project"] = project_from_branch(
                        session, plan["git_repository"], plan["cut_branch"], project,
                        f"GDP run {run_id} branch {plan['cut_branch']} compile-only")
                except Exception as exc:
                    result["dbt_project"] = {"status": "SKIPPED", "detail": clip(exc, 400)}
            call.summary = f"{result['status']} {result.get('repository')} {plan.get('cut_branch')}: " \
                           f"PR {(result.get('pull_request') or {}).get('url') or 'none'}"
        except Exception as exc:
            result = {**github.explain(exc, plan.get("origin") or ""),
                      "base_branch": plan.get("base_branch"), "head_branch": plan.get("cut_branch")}
            result["detail"] = clip(result.get("detail"), 1000)
            call.status, call.error = "FAILED", clip(exc)
    _record(session, run_id, generation_id, plan, result)
    return result
