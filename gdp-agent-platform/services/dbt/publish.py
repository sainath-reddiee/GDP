"""CODEGEN.PUBLISH_DBT_PR: push the latest generated dbt code to a new GitHub branch and open a pull request.

Runs inside Snowflake with an external access integration to api.github.com and a SECRET holding a GitHub
token, so the token never leaves Snowflake. After the push, the branch is FETCHed into the Snowflake git
repository clone (when one is set) and a compile-only DBT PROJECT is created from that branch.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from services.common.audit import tool_call
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.standard import conventions_for, fill, run_standard
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
        "COMMENT = 'Agentic pipeline: push generated dbt branches and open pull requests'"
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
    # where to push is decided at generation (the configured repository); a publish call may not redirect it
    for key in ("base_branch", "cut_branch", "title", "draft", "create_project"):
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


def project_from_branch(session, repo_fqn: str, branch: str, project_fqn: str, comment: str,
                        project_dir: str = "") -> Dict[str, Any]:
    """FETCH the pushed branch into the Snowflake clone and point a compile-only DBT PROJECT at it."""
    from services.dbt.workspace import branch_segment

    repo, name = safe_fqn(repo_fqn), safe_fqn(project_fqn)
    head = github.check_branch(branch)
    folder = github.clean_root(project_dir)
    session.sql(f"ALTER GIT REPOSITORY {repo} FETCH").collect()
    source = f"@{repo}/branches/{branch_segment(head)}" + (f"/{folder}" if folder else "")
    session.sql(
        f"CREATE OR REPLACE DBT PROJECT {name} FROM '{source}' "
        f"AUTO_COMPILE = FALSE DEFAULT_WRITEBACK = FALSE COMMENT = '{comment.replace(chr(39), '')[:200]}'"
    ).collect()
    return {"status": "CREATED", "dbt_project": name, "from": source}


def publish_case_patch(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """A case's accepted DBT_PATCH as a draft pull request (PR Q2). Only ids and the branch come from the caller: the
    file, its new content and the repository are read here from CASES.CASE_ARTIFACT and CODE.REPO, and the artifact
    must be an ACCEPTED, publishable DBT_PATCH, so the procedure never pushes content a person did not accept."""
    artifact_id, case_id = str(request.get("artifact_id") or ""), str(request.get("case_id") or "")
    head = github.check_branch(str(request.get("head") or ""))
    assert head.startswith("fix/"), "a case fix branch starts with fix/"
    found = rows(session, "SELECT TYPE, STATUS, CONTENT FROM CASES.CASE_ARTIFACT WHERE ARTIFACT_ID = ? AND CASE_ID = ?",
                 [artifact_id, case_id])
    assert found, "case artifact not found"
    content = variant(found[0]["CONTENT"]) or {}
    assert found[0]["TYPE"] == "DBT_PATCH" and found[0]["STATUS"] == "ACCEPTED", "only an accepted dbt patch is published"
    assert content.get("publishable") and content.get("new_content") and content.get("path"), "this patch cannot be published"
    repo = rows(session, "SELECT GIT_URL, BRANCH, PROVIDER FROM CODE.REPO WHERE REPO_ID = ? AND ENABLED", [content.get("repo_id")])
    assert repo and str(repo[0]["PROVIDER"] or "").upper() == "GITHUB", "the patch's repository is not a connected GitHub repository"
    origin, base = repo[0]["GIT_URL"], str(repo[0]["BRANCH"] or "main")
    with tool_call(session, None, "publish_case_patch", {"case_id": case_id, "artifact_id": artifact_id, "head": head}) as call:
        try:
            result = github.publish(github.urllib_request(_token()), origin, base, head, {content["path"]: content["new_content"]},
                                    title=clip(request.get("title") or f"Case fix {case_id[:8]}", 200),
                                    body=clip(request.get("body") or "", 6000),
                                    message=clip(request.get("title") or "Case fix", 200), draft=True)
            call.summary = f"{result['status']} {head}: PR {(result.get('pull_request') or {}).get('url') or 'none'}"
        except Exception as exc:
            result = {**github.explain(exc, origin), "base_branch": base, "head_branch": head}
            result["detail"] = clip(result.get("detail"), 1000)
            call.status, call.error = "FAILED", clip(exc)
    return result


def publish_model_branch(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """A model finalized in the Modeling hub as a branch feature/model-<domain>-<timestamp> with a draft pull request.
    Only ids and the branch come from the caller: the files are read here from GOVERNANCE.MODEL_DESIGNS (a FINALIZED or
    PUBLISH_FAILED version) and the repository from CODE.REPO, so the procedure never pushes content nobody finalized."""
    design_id, version = str(request.get("design_id") or ""), int(request.get("version") or 0)
    head = github.check_branch(str(request.get("head") or ""))
    assert head.startswith("feature/model-"), "a model branch starts with feature/model-"
    found = rows(session, """SELECT DOMAIN_NAME, MODE, STATUS, ARTIFACTS FROM GOVERNANCE.MODEL_DESIGNS
                              WHERE DESIGN_ID = ? AND VERSION = ?""", [design_id, version])
    assert found, "model design not found"
    assert found[0]["STATUS"] in ("FINALIZED", "PUBLISH_FAILED"), "only a finalized model design is published"
    files = variant(found[0]["ARTIFACTS"]) or {}
    assert isinstance(files, dict) and files, "the model design has no rendered files"
    repo = rows(session, "SELECT GIT_URL, BRANCH, PROVIDER FROM CODE.REPO WHERE REPO_ID = ? AND ENABLED",
                [str(request.get("repo_id") or "")])
    assert repo and str(repo[0]["PROVIDER"] or "").upper() == "GITHUB", "the repository is not a connected GitHub repository"
    origin = repo[0]["GIT_URL"]
    base = str(request.get("base") or repo[0]["BRANCH"] or "main")
    domain = found[0]["DOMAIN_NAME"]
    title = clip(request.get("title") or f"Model {domain} ({found[0]['MODE'].lower()}) v{version}", 200)
    body = clip(request.get("body") or (f"Model design {design_id} v{version} for domain {domain}, finalized in the "
                                        f"Modeling hub.\n\nFiles:\n" + "\n".join(f"- {p}" for p in sorted(files))), 6000)
    with tool_call(session, None, "publish_model_branch", {"design_id": design_id, "version": version, "head": head}) as call:
        try:
            result = github.publish(github.urllib_request(_token()), origin, base, head, files, title=title, body=body,
                                    message=title, draft=True)
            call.summary = f"{result['status']} {head}: PR {(result.get('pull_request') or {}).get('url') or 'none'}"
        except Exception as exc:
            result = {**github.explain(exc, origin), "base_branch": base, "head_branch": head}
            result["detail"] = clip(result.get("detail"), 1000)
            call.status, call.error = "FAILED", clip(exc)
    ok = result.get("status") in ("PUBLISHED", "NO_CHANGES")
    session.sql("""UPDATE GOVERNANCE.MODEL_DESIGNS SET STATUS = ?, REPO_ID = ?, BRANCH = ?, PR_URL = NULLIF(?, ''),
                          PUBLISH_RESULT = PARSE_JSON(?), UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE DESIGN_ID = ? AND VERSION = ?""",
                params=["PUBLISHED" if ok else "PUBLISH_FAILED", str(request.get("repo_id") or ""), head,
                        (result.get("pull_request") or {}).get("url") or "", json.dumps(result, default=str),
                        design_id, version]).collect()
    return result


def _repo(session, repo_id: str) -> Dict[str, Any]:
    repo = rows(session, "SELECT GIT_URL, BRANCH, PROVIDER FROM CODE.REPO WHERE REPO_ID = ? AND ENABLED", [repo_id])
    assert repo and str(repo[0]["PROVIDER"] or "").upper() == "GITHUB", "the repository is not a connected GitHub repository"
    return repo[0]


def publish_domain_model(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """An APPROVED domain model version (Model Workbench) as a branch feature/model-<domain>-v<version> with a pull
    request for the repository's owners to merge. Only ids and the branch come from the caller: the files are rendered
    here from the approved specification, so nothing that was not approved in the app is ever pushed. The version
    stays APPROVED until the pull request is merged (sync_model_prs marks it PUBLISHED)."""
    from services.knowledge.packs import export_pack
    from services.modeling import render_v2, spec_v2

    model_id, version = str(request.get("model_id") or ""), int(request.get("version") or 0)
    head = github.check_branch(str(request.get("head") or ""))
    assert head.startswith("feature/model-"), "a model branch starts with feature/model-"
    found = rows(session, """SELECT D.STATUS, D.SPEC, M.DOMAIN_ID, M.DOMAIN_NAME FROM GOVERNANCE.MODEL_DESIGNS D
                              JOIN GOVERNANCE.DOMAIN_MODEL M ON M.MODEL_ID = D.MODEL_ID
                             WHERE D.MODEL_ID = ? AND D.VERSION = ?""", [model_id, version])
    assert found, "model version not found"
    assert found[0]["STATUS"] == "APPROVED", "only an approved model version is published"
    spec = spec_v2.normalize_v2(variant(found[0]["SPEC"]) or {}, model_id)
    try:  # keep the domain's knowledge items and settings in the pack; the targets come from the approved model
        base = export_pack(lambda sql, params: rows(session, sql.replace("%s", "?"), list(params)), found[0]["DOMAIN_ID"])
    except Exception:
        base = None
    files = render_v2.artifacts(spec, base)
    repo_id = str(request.get("repo_id") or "")
    repo = _repo(session, repo_id)
    origin, base_branch = repo["GIT_URL"], str(request.get("base") or repo["BRANCH"] or "main")
    domain = found[0]["DOMAIN_NAME"]
    title = clip(request.get("title") or f"Model {domain} v{version} (approved)", 200)
    body = clip(request.get("body") or (f"Domain model {domain} v{version}, approved in the Model Workbench "
                                        f"(model {model_id}).\n\nFiles:\n" + "\n".join(f"- {p}" for p in sorted(files))), 6000)
    with tool_call(session, None, "publish_domain_model", {"model_id": model_id, "version": version, "head": head}) as call:
        try:
            result = github.publish(github.urllib_request(_token()), origin, base_branch, head, files, title=title, body=body,
                                    message=title, draft=False)
            call.summary = f"{result['status']} {head}: PR {(result.get('pull_request') or {}).get('url') or 'none'}"
        except Exception as exc:
            result = {**github.explain(exc, origin), "base_branch": base_branch, "head_branch": head}
            result["detail"] = clip(result.get("detail"), 1000)
            call.status, call.error = "FAILED", clip(exc)
    pr = result.get("pull_request") or {}
    state = ("OPEN" if pr.get("url") else None) if result.get("status") == "PUBLISHED" else (
        "NO_CHANGES" if result.get("status") == "NO_CHANGES" else "FAILED")
    status = "PUBLISHED" if state == "NO_CHANGES" else "APPROVED"  # the repository already holds exactly this model
    session.sql("""UPDATE GOVERNANCE.MODEL_DESIGNS SET STATUS = ?, REPO_ID = ?, BRANCH = ?, PR_URL = NULLIF(?, ''),
                          PR_NUMBER = NULLIF(?, '')::NUMBER, PR_STATE = ?, PUBLISH_RESULT = PARSE_JSON(?),
                          PR_SYNCED_AT = CURRENT_TIMESTAMP(), UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE MODEL_ID = ? AND VERSION = ?""",
                params=[status, repo_id, head, pr.get("url") or "", str(pr.get("number") or ""), state,
                        json.dumps(result, default=str), model_id, version]).collect()
    return result


def sync_model_prs(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """Read the state of open domain model pull requests from GitHub: merged marks the version PUBLISHED, closed
    without merging leaves it APPROVED with PR_STATE CLOSED (to publish again). Versions synced in the last minute are
    skipped unless `force`. Run on demand from the Publish tab and by the CODEGEN.SYNC_MODEL_PRS_TASK task."""
    model_id = str(request.get("model_id") or "")
    where = "D.PR_NUMBER IS NOT NULL AND D.PR_STATE IN ('OPEN', 'DRAFT') AND D.REPO_ID IS NOT NULL"
    params: List[Any] = []
    if model_id:
        where += " AND D.MODEL_ID = ?"
        params.append(model_id)
    if not request.get("force"):
        where += " AND (D.PR_SYNCED_AT IS NULL OR D.PR_SYNCED_AT < DATEADD('second', -60, CURRENT_TIMESTAMP()))"
    found = rows(session, f"""SELECT D.MODEL_ID, D.VERSION, D.PR_NUMBER, R.GIT_URL FROM GOVERNANCE.MODEL_DESIGNS D
                               JOIN CODE.REPO R ON R.REPO_ID = D.REPO_ID WHERE {where} LIMIT 200""", params)
    request_fn = github.urllib_request(_token()) if found else None
    out: Dict[str, Any] = {"checked": 0, "merged": [], "closed": [], "errors": []}
    for r in found:
        try:
            pr = github.pull_status(request_fn, r["GIT_URL"], int(r["PR_NUMBER"]))
        except Exception as exc:
            out["errors"].append({"model_id": r["MODEL_ID"], "version": int(r["VERSION"]), "error": clip(exc, 300)})
            continue
        out["checked"] += 1
        status_sql = ", STATUS = 'PUBLISHED'" if pr["state"] == "MERGED" else ""
        session.sql(f"""UPDATE GOVERNANCE.MODEL_DESIGNS SET PR_STATE = ?, MERGED_SHA = NULLIF(?, ''),
                               PR_SYNCED_AT = CURRENT_TIMESTAMP(){status_sql}
                         WHERE MODEL_ID = ? AND VERSION = ? AND STATUS IN ('APPROVED', 'PUBLISHED')""",
                    params=[pr["state"], pr.get("merge_commit_sha") or "", r["MODEL_ID"], int(r["VERSION"])]).collect()
        if pr["state"] == "MERGED":
            out["merged"].append({"model_id": r["MODEL_ID"], "version": int(r["VERSION"])})
        elif pr["state"] == "CLOSED":
            out["closed"].append({"model_id": r["MODEL_ID"], "version": int(r["VERSION"])})
    return out


def publish_dq_suite(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """An APPROVED data quality suite version (one table's checks) as a branch feature/dq-<table>-v<version> with a pull
    request: the SodaCL file and the Great Expectations suite under dq/<domain>/. The files are rendered here from the
    approved checks; the version stays APPROVED until the pull request is merged (sync_dq_prs marks it PUBLISHED)."""
    from services.catalog import dq_suite

    suite_id, version = str(request.get("suite_id") or ""), int(request.get("version") or 0)
    head = github.check_branch(str(request.get("head") or ""))
    assert head.startswith("feature/dq-"), "a data quality branch starts with feature/dq-"
    found = rows(session, """SELECT V.STATUS, V.CHECKS, S.TABLE_NAME, S.DOMAIN_ID, D.DOMAIN_NAME
                              FROM GOVERNANCE.DQ_SUITE_VERSION V JOIN GOVERNANCE.DQ_SUITE S ON S.SUITE_ID = V.SUITE_ID
                              LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = S.DOMAIN_ID
                             WHERE V.SUITE_ID = ? AND V.VERSION = ?""", [suite_id, version])
    assert found, "suite version not found"
    assert found[0]["STATUS"] == "APPROVED", "only an approved data quality suite version is published"
    table = found[0]["TABLE_NAME"]
    files = dq_suite.artifacts(table, found[0].get("DOMAIN_NAME"), list(variant(found[0]["CHECKS"]) or []))
    repo_id = str(request.get("repo_id") or "")
    repo = _repo(session, repo_id)
    origin, base_branch = repo["GIT_URL"], str(request.get("base") or repo["BRANCH"] or "main")
    title = clip(request.get("title") or f"Data quality checks {table} v{version} (approved)", 200)
    body = clip(f"Data quality suite of {table} v{version}, approved in the Data Quality Store.\n\nFiles:\n"
                + "\n".join(f"- {p}" for p in sorted(files)), 6000)
    with tool_call(session, None, "publish_dq_suite", {"suite_id": suite_id, "version": version, "head": head}) as call:
        try:
            result = github.publish(github.urllib_request(_token()), origin, base_branch, head, files, title=title,
                                    body=body, message=title, draft=False)
            call.summary = f"{result['status']} {head}: PR {(result.get('pull_request') or {}).get('url') or 'none'}"
        except Exception as exc:
            result = {**github.explain(exc, origin), "base_branch": base_branch, "head_branch": head}
            result["detail"] = clip(result.get("detail"), 1000)
            call.status, call.error = "FAILED", clip(exc)
    pr = result.get("pull_request") or {}
    state = ("OPEN" if pr.get("url") else None) if result.get("status") == "PUBLISHED" else (
        "NO_CHANGES" if result.get("status") == "NO_CHANGES" else "FAILED")
    status = "PUBLISHED" if state == "NO_CHANGES" else "APPROVED"
    session.sql("""UPDATE GOVERNANCE.DQ_SUITE_VERSION SET STATUS = ?, REPO_ID = ?, BRANCH = ?, PR_URL = NULLIF(?, ''),
                          PR_NUMBER = NULLIF(?, '')::NUMBER, PR_STATE = ?, PUBLISH_RESULT = PARSE_JSON(?),
                          PR_SYNCED_AT = CURRENT_TIMESTAMP(), UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE SUITE_ID = ? AND VERSION = ?""",
                params=[status, repo_id, head, pr.get("url") or "", str(pr.get("number") or ""), state,
                        json.dumps(result, default=str), suite_id, version]).collect()
    return result


def sync_dq_prs(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """The data quality suite counterpart of sync_model_prs: merged marks the version PUBLISHED, closed leaves it
    APPROVED with PR_STATE CLOSED. Versions synced in the last minute are skipped unless `force`."""
    suite_id = str(request.get("suite_id") or "")
    where = "V.PR_NUMBER IS NOT NULL AND V.PR_STATE IN ('OPEN', 'DRAFT') AND V.REPO_ID IS NOT NULL"
    params: List[Any] = []
    if suite_id:
        where += " AND V.SUITE_ID = ?"
        params.append(suite_id)
    if not request.get("force"):
        where += " AND (V.PR_SYNCED_AT IS NULL OR V.PR_SYNCED_AT < DATEADD('second', -60, CURRENT_TIMESTAMP()))"
    try:
        found = rows(session, f"""SELECT V.SUITE_ID, V.VERSION, V.PR_NUMBER, R.GIT_URL FROM GOVERNANCE.DQ_SUITE_VERSION V
                                   JOIN CODE.REPO R ON R.REPO_ID = V.REPO_ID WHERE {where} LIMIT 200""", params)
    except Exception as exc:  # before V040
        return {"checked": 0, "merged": [], "closed": [], "errors": [{"error": clip(exc, 300)}]}
    request_fn = github.urllib_request(_token()) if found else None
    out: Dict[str, Any] = {"checked": 0, "merged": [], "closed": [], "errors": []}
    for r in found:
        try:
            pr = github.pull_status(request_fn, r["GIT_URL"], int(r["PR_NUMBER"]))
        except Exception as exc:
            out["errors"].append({"suite_id": r["SUITE_ID"], "version": int(r["VERSION"]), "error": clip(exc, 300)})
            continue
        out["checked"] += 1
        status_sql = ", STATUS = 'PUBLISHED'" if pr["state"] == "MERGED" else ""
        session.sql(f"""UPDATE GOVERNANCE.DQ_SUITE_VERSION SET PR_STATE = ?, MERGED_SHA = NULLIF(?, ''),
                               PR_SYNCED_AT = CURRENT_TIMESTAMP(){status_sql}
                         WHERE SUITE_ID = ? AND VERSION = ? AND STATUS IN ('APPROVED', 'PUBLISHED')""",
                    params=[pr["state"], pr.get("merge_commit_sha") or "", r["SUITE_ID"], int(r["VERSION"])]).collect()
        if pr["state"] in ("MERGED", "CLOSED"):
            out[pr["state"].lower()].append({"suite_id": r["SUITE_ID"], "version": int(r["VERSION"])})
    return out


def sync_git_prs(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """Every governed pull request: domain models and data quality suites (the scheduled task)."""
    return {"models": sync_model_prs(session, request), "dq_suites": sync_dq_prs(session, request)}


def sync_task_sql(database: str, warehouse: str) -> List[str]:
    """A task that syncs governed pull requests (domain models, data quality suites) every 30 minutes (created with the
    publisher)."""
    for value in (database, warehouse):
        assert NAME.match(value or ""), f"unsafe identifier: {value}"
    return [f"CREATE OR REPLACE TASK {database}.CODEGEN.SYNC_MODEL_PRS_TASK WAREHOUSE = {warehouse} "
            f"SCHEDULE = '30 MINUTE' COMMENT = 'Read the state of domain model and data quality suite pull requests' "
            f"AS CALL {database}.CODEGEN.PUBLISH_DBT_PR('', '{{\"sync_git_prs\": {{}}}}')",
            f"ALTER TASK {database}.CODEGEN.SYNC_MODEL_PRS_TASK RESUME"]


def publish_dbt_pr(session, run_id: str, payload_json: str = "{}") -> Dict[str, Any]:
    payload = json.loads(payload_json or "{}")
    if not run_id and isinstance(payload.get("case_patch"), dict):
        return publish_case_patch(session, payload["case_patch"])
    if not run_id and isinstance(payload.get("model_branch"), dict):
        return publish_model_branch(session, payload["model_branch"])
    if not run_id and isinstance(payload.get("domain_model"), dict):
        return publish_domain_model(session, payload["domain_model"])
    if not run_id and isinstance(payload.get("sync_model_prs"), dict):
        return sync_model_prs(session, payload["sync_model_prs"])
    if not run_id and isinstance(payload.get("dq_suite"), dict):
        return publish_dq_suite(session, payload["dq_suite"])
    if not run_id and isinstance(payload.get("sync_dq_prs"), dict):
        return sync_dq_prs(session, payload["sync_dq_prs"])
    if not run_id and isinstance(payload.get("sync_git_prs"), dict):
        return sync_git_prs(session, payload["sync_git_prs"])
    plan = _plan(session, run_id, payload) if run_id else dict(payload)
    if payload.get("check_only"):
        try:
            return {"status": "OK", **github.preflight(github.urllib_request(_token()), plan.get("origin") or "")}
        except Exception as exc:
            return {**github.explain(exc, plan.get("origin") or ""), "check": True}
    run = rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    conv = conventions_for(lambda sql, params: rows(session, sql, params), run_standard(run[0] if run else {}))
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
                title=plan.get("title") or fill(conv["pr_title"], name=name, version=version),
                body=github.pr_body(name, run_id, sorted(files), gen[0]["STTM_ID"]),
                message=fill(conv["commit_message"], name=name, version=version),
                draft=bool(plan.get("draft")),
                root=plan.get("project_dir") or "",
            )
            if plan.get("git_repository") and plan.get("create_project", True) and result["status"] != "FAILED":
                project = plan.get("dbt_project") or (
                    f"{scalar(session, 'SELECT CURRENT_DATABASE()')}.CODEGEN.{conv['codegen_prefix']}"
                    f"{run_id.replace('-', '')[:18]}_BRANCH")
                try:
                    result["dbt_project"] = project_from_branch(
                        session, plan["git_repository"], plan["cut_branch"], project,
                        f"Agentic pipeline run {run_id} branch {plan['cut_branch']} compile-only",
                        plan.get("project_dir") or "")
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
