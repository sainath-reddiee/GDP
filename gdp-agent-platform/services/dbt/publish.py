"""CORE.PUBLISH_DBT_PR: push the latest generated dbt code to a new GitHub branch and open a pull request.

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
from services.common.platform_schema import PLATFORM_SCHEMA
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.standard import conventions_for, fill, run_pipeline, run_standard
from services.dbt import github
from services.dbt.workspace import safe_fqn
from services.git import roundtrip

SECRET_ALIAS = "github_token"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")


def procedure_sql(database: str, services_import: str, eai: str, secret: str) -> str:
    """DDL for the publisher; shared by the setup endpoint and the deploy script."""
    for value in (database, eai, secret):
        assert NAME.match(value or ""), f"unsafe identifier: {value}"
    assert re.fullmatch(r"@[A-Za-z0-9_./$-]+\.zip", services_import or ""), f"unsafe import: {services_import}"
    return (
        f"CREATE OR REPLACE PROCEDURE {database}.CORE.PUBLISH_DBT_PR(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)\n"
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
        f"CREATE NETWORK RULE IF NOT EXISTS {database}.CORE.GITHUB_API_EGRESS MODE = EGRESS TYPE = HOST_PORT "
        "VALUE_LIST = ('api.github.com')",
    ]
    if create_secret:
        statements.append(f"CREATE OR REPLACE SECRET {secret} TYPE = GENERIC_STRING SECRET_STRING = '<github token>'")
    statements.append(
        f"CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION {eai} "
        f"ALLOWED_NETWORK_RULES = ({database}.CORE.GITHUB_API_EGRESS) "
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
    stored = rows(session, "SELECT CONTENT_JSON FROM CORE.DOMAIN_KNOWLEDGE WHERE IS_CURRENT AND SOURCE_REFERENCE = ?",
                  [f"dbt.branch.{run_id}"])
    plan = dict(variant(stored[0]["CONTENT_JSON"]) or {}) if stored else {}
    # where to push is decided at generation (the configured repository); a publish call may not redirect it
    for key in ("base_branch", "cut_branch", "title", "draft", "create_project"):
        if payload.get(key) not in (None, ""):
            plan[key] = payload[key]
    return plan


def _record(session, run_id: str, generation_id: Optional[str], plan: Dict[str, Any], result: Dict[str, Any]) -> None:
    pr = result.get("pull_request") or {}
    insert_rows(session, "CORE.GIT_PUBLICATION",
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
    file, its new content and the repository are read here from CORE.CASE_ARTIFACT and CORE.REPO, and the artifact
    must be an ACCEPTED, publishable DBT_PATCH, so the procedure never pushes content a person did not accept."""
    artifact_id, case_id = str(request.get("artifact_id") or ""), str(request.get("case_id") or "")
    head = github.check_branch(str(request.get("head") or ""))
    assert head.startswith("fix/"), "a case fix branch starts with fix/"
    found = rows(session, "SELECT TYPE, STATUS, CONTENT FROM CORE.CASE_ARTIFACT WHERE ARTIFACT_ID = ? AND CASE_ID = ?",
                 [artifact_id, case_id])
    assert found, "case artifact not found"
    content = variant(found[0]["CONTENT"]) or {}
    assert found[0]["TYPE"] == "DBT_PATCH" and found[0]["STATUS"] == "ACCEPTED", "only an accepted dbt patch is published"
    assert content.get("publishable") and content.get("new_content") and content.get("path"), "this patch cannot be published"
    repo = rows(session, "SELECT GIT_URL, BRANCH, PROVIDER FROM CORE.REPO WHERE REPO_ID = ? AND ENABLED", [content.get("repo_id")])
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
    Only ids and the branch come from the caller: the files are read here from CORE.MODEL_DESIGNS (a FINALIZED or
    PUBLISH_FAILED version) and the repository from CORE.REPO, so the procedure never pushes content nobody finalized."""
    design_id, version = str(request.get("design_id") or ""), int(request.get("version") or 0)
    head = github.check_branch(str(request.get("head") or ""))
    assert head.startswith("feature/model-"), "a model branch starts with feature/model-"
    found = rows(session, """SELECT DOMAIN_NAME, MODE, STATUS, ARTIFACTS FROM CORE.MODEL_DESIGNS
                              WHERE DESIGN_ID = ? AND VERSION = ?""", [design_id, version])
    assert found, "model design not found"
    assert found[0]["STATUS"] in ("FINALIZED", "PUBLISH_FAILED"), "only a finalized model design is published"
    files = variant(found[0]["ARTIFACTS"]) or {}
    assert isinstance(files, dict) and files, "the model design has no rendered files"
    repo = rows(session, "SELECT GIT_URL, BRANCH, PROVIDER FROM CORE.REPO WHERE REPO_ID = ? AND ENABLED",
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
    session.sql("""UPDATE CORE.MODEL_DESIGNS SET STATUS = ?, REPO_ID = ?, BRANCH = ?, PR_URL = NULLIF(?, ''),
                          PUBLISH_RESULT = PARSE_JSON(?), UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE DESIGN_ID = ? AND VERSION = ?""",
                params=["PUBLISHED" if ok else "PUBLISH_FAILED", str(request.get("repo_id") or ""), head,
                        (result.get("pull_request") or {}).get("url") or "", json.dumps(result, default=str),
                        design_id, version]).collect()
    return result


def _repo(session, repo_id: str) -> Dict[str, Any]:
    repo = rows(session, "SELECT GIT_URL, BRANCH, PROVIDER FROM CORE.REPO WHERE REPO_ID = ? AND ENABLED", [repo_id])
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
    found = rows(session, """SELECT D.STATUS, D.SPEC, M.DOMAIN_ID, M.DOMAIN_NAME FROM CORE.MODEL_DESIGNS D
                              JOIN CORE.DOMAIN_MODEL M ON M.MODEL_ID = D.MODEL_ID
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
    state, pr = roundtrip.outcome(result)
    status = "PUBLISHED" if state == "NO_CHANGES" else "APPROVED"  # the repository already holds exactly this model
    session.sql("""UPDATE CORE.MODEL_DESIGNS SET STATUS = ?, REPO_ID = ?, BRANCH = ?, PR_URL = NULLIF(?, ''),
                          PR_NUMBER = NULLIF(?, '')::NUMBER, PR_STATE = ?, PUBLISH_RESULT = PARSE_JSON(?),
                          PR_SYNCED_AT = CURRENT_TIMESTAMP(), UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE MODEL_ID = ? AND VERSION = ?""",
                params=[status, repo_id, head, pr.get("url") or "", str(pr.get("number") or ""), state,
                        json.dumps(result, default=str), model_id, version]).collect()
    roundtrip.record(session, "MODEL", model_id, version, result, asset_name=domain, domain_id=found[0]["DOMAIN_ID"],
                     repo_id=repo_id, branch=head, title=title, files=list(files))
    return result


def sync_model_prs(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """Read the state of open domain model pull requests from GitHub: merged marks the version PUBLISHED, closed
    without merging leaves it APPROVED with PR_STATE CLOSED (to publish again). Versions synced in the last minute are
    skipped unless `force`. Run on demand from the Publish tab and by the CORE.SYNC_MODEL_PRS_TASK task."""
    model_id = str(request.get("model_id") or "")
    where = "D.PR_NUMBER IS NOT NULL AND D.PR_STATE IN ('OPEN', 'DRAFT') AND D.REPO_ID IS NOT NULL"
    params: List[Any] = []
    if model_id:
        where += " AND D.MODEL_ID = ?"
        params.append(model_id)
    if not request.get("force"):
        where += " AND (D.PR_SYNCED_AT IS NULL OR D.PR_SYNCED_AT < DATEADD('second', -60, CURRENT_TIMESTAMP()))"
    found = rows(session, f"""SELECT D.MODEL_ID, D.VERSION, D.PR_NUMBER, R.GIT_URL FROM CORE.MODEL_DESIGNS D
                               JOIN CORE.REPO R ON R.REPO_ID = D.REPO_ID WHERE {where} LIMIT 200""", params)
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
        session.sql(f"""UPDATE CORE.MODEL_DESIGNS SET PR_STATE = ?, MERGED_SHA = NULLIF(?, ''),
                               PR_SYNCED_AT = CURRENT_TIMESTAMP(){status_sql}
                         WHERE MODEL_ID = ? AND VERSION = ? AND STATUS IN ('APPROVED', 'PUBLISHED')""",
                    params=[pr["state"], pr.get("merge_commit_sha") or "", r["MODEL_ID"], int(r["VERSION"])]).collect()
        roundtrip.mirror(session, "MODEL", r["MODEL_ID"], int(r["VERSION"]), pr["state"], pr.get("merge_commit_sha"))
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
                              FROM CORE.DQ_SUITE_VERSION V JOIN CORE.DQ_SUITE S ON S.SUITE_ID = V.SUITE_ID
                              LEFT JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = S.DOMAIN_ID
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
    state, pr = roundtrip.outcome(result)
    status = "PUBLISHED" if state == "NO_CHANGES" else "APPROVED"
    session.sql("""UPDATE CORE.DQ_SUITE_VERSION SET STATUS = ?, REPO_ID = ?, BRANCH = ?, PR_URL = NULLIF(?, ''),
                          PR_NUMBER = NULLIF(?, '')::NUMBER, PR_STATE = ?, PUBLISH_RESULT = PARSE_JSON(?),
                          PR_SYNCED_AT = CURRENT_TIMESTAMP(), UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE SUITE_ID = ? AND VERSION = ?""",
                params=[status, repo_id, head, pr.get("url") or "", str(pr.get("number") or ""), state,
                        json.dumps(result, default=str), suite_id, version]).collect()
    roundtrip.record(session, "DQ_SUITE", suite_id, version, result, asset_name=table, domain_id=found[0].get("DOMAIN_ID"),
                     repo_id=repo_id, branch=head, title=title, files=list(files))
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
        found = rows(session, f"""SELECT V.SUITE_ID, V.VERSION, V.PR_NUMBER, R.GIT_URL FROM CORE.DQ_SUITE_VERSION V
                                   JOIN CORE.REPO R ON R.REPO_ID = V.REPO_ID WHERE {where} LIMIT 200""", params)
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
        session.sql(f"""UPDATE CORE.DQ_SUITE_VERSION SET PR_STATE = ?, MERGED_SHA = NULLIF(?, ''),
                               PR_SYNCED_AT = CURRENT_TIMESTAMP(){status_sql}
                         WHERE SUITE_ID = ? AND VERSION = ? AND STATUS IN ('APPROVED', 'PUBLISHED')""",
                    params=[pr["state"], pr.get("merge_commit_sha") or "", r["SUITE_ID"], int(r["VERSION"])]).collect()
        roundtrip.mirror(session, "DQ_SUITE", r["SUITE_ID"], int(r["VERSION"]), pr["state"], pr.get("merge_commit_sha"))
        if pr["state"] in ("MERGED", "CLOSED"):
            out[pr["state"].lower()].append({"suite_id": r["SUITE_ID"], "version": int(r["VERSION"])})
    return out


def publish_qa_pack(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """An APPROVED QA pack version as its repository file (domain/<domain>/qa/<pack>.sql, or the file it was seeded
    from) on a branch feature/qa-<pack>-v<version> with a pull request. The file is rendered here from the approved
    queries (services/qa/pack_files.render), the format the deploy seeds from."""
    from services.qa import pack_git, packs
    from services.qa.pack_files import render

    pack_id, version = str(request.get("pack_id") or ""), int(request.get("version") or 0)
    head = github.check_branch(str(request.get("head") or ""))
    assert head.startswith("feature/qa-"), "a QA pack branch starts with feature/qa-"
    detail = packs.get_pack(session, pack_id, version)
    pack = detail["pack"]
    assert pack["status"] == "APPROVED", "only an approved QA pack version is published"
    domain = rows(session, "SELECT DOMAIN_NAME FROM CORE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [pack["domain_id"]])
    path = pack.get("repo_path") or pack_git.pack_path(domain[0]["DOMAIN_NAME"] if domain else "domain", pack["name"])
    text = render({"name": pack["name"], "description": pack.get("description"), "tags": pack.get("tags") or [],
                   "queries": detail["queries"]})
    repo_id = str(request.get("repo_id") or "")
    repo = _repo(session, repo_id)
    origin, base_branch = repo["GIT_URL"], str(request.get("base") or repo["BRANCH"] or "main")
    title = clip(request.get("title") or f"QA pack {pack['name']} v{version} (approved)", 200)
    body = clip(f"QA pack {pack['name']} v{version}, approved in the QA Store.\n\nFile: {path}", 6000)
    with tool_call(session, None, "publish_qa_pack", {"pack_id": pack_id, "version": version, "head": head}) as call:
        try:
            result = github.publish(github.urllib_request(_token()), origin, base_branch, head, {path: text}, title=title,
                                    body=body, message=title, draft=False)
            call.summary = f"{result['status']} {head}: PR {(result.get('pull_request') or {}).get('url') or 'none'}"
        except Exception as exc:
            result = {**github.explain(exc, origin), "base_branch": base_branch, "head_branch": head}
            result["detail"] = clip(result.get("detail"), 1000)
            call.status, call.error = "FAILED", clip(exc)
    state, pr = roundtrip.outcome(result)
    session.sql("""UPDATE CORE.DOMAIN_QA_PACKS SET REPO_ID = ?, BRANCH = ?, PR_URL = NULLIF(?, ''),
                          PR_NUMBER = NULLIF(?, '')::NUMBER, PR_STATE = ?, PUBLISH_RESULT = PARSE_JSON(?),
                          REPO_PATH = COALESCE(REPO_PATH, ?), PR_SYNCED_AT = CURRENT_TIMESTAMP()
                    WHERE PACK_ID = ? AND VERSION = ?""",
                params=[repo_id, head, pr.get("url") or "", str(pr.get("number") or ""), state,
                        json.dumps(result, default=str), path, pack_id, version]).collect()
    roundtrip.record(session, "QA_PACK", pack_id, version, result, asset_name=pack["name"], domain_id=pack.get("domain_id"),
                     repo_id=repo_id, branch=head, title=title, files=[path])
    return {**result, "path": path}


def sync_qa_prs(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """QA pack pull requests: merged or closed is recorded in PR_STATE (the pack version stays APPROVED; once the
    repository is deployed the pack is in step with its file)."""
    pack_id = str(request.get("pack_id") or "")
    where = "P.PR_NUMBER IS NOT NULL AND P.PR_STATE IN ('OPEN', 'DRAFT') AND P.REPO_ID IS NOT NULL"
    params: List[Any] = []
    if pack_id:
        where += " AND P.PACK_ID = ?"
        params.append(pack_id)
    if not request.get("force"):
        where += " AND (P.PR_SYNCED_AT IS NULL OR P.PR_SYNCED_AT < DATEADD('second', -60, CURRENT_TIMESTAMP()))"
    try:
        found = rows(session, f"""SELECT P.PACK_ID, P.VERSION, P.PR_NUMBER, R.GIT_URL FROM CORE.DOMAIN_QA_PACKS P
                                   JOIN CORE.REPO R ON R.REPO_ID = P.REPO_ID WHERE {where} LIMIT 200""", params)
    except Exception as exc:  # before V042
        return {"checked": 0, "merged": [], "closed": [], "errors": [{"error": clip(exc, 300)}]}
    request_fn = github.urllib_request(_token()) if found else None
    out: Dict[str, Any] = {"checked": 0, "merged": [], "closed": [], "errors": []}
    for r in found:
        try:
            pr = github.pull_status(request_fn, r["GIT_URL"], int(r["PR_NUMBER"]))
        except Exception as exc:
            out["errors"].append({"pack_id": r["PACK_ID"], "version": int(r["VERSION"]), "error": clip(exc, 300)})
            continue
        out["checked"] += 1
        session.sql("""UPDATE CORE.DOMAIN_QA_PACKS SET PR_STATE = ?, MERGED_SHA = NULLIF(?, ''),
                              PR_SYNCED_AT = CURRENT_TIMESTAMP() WHERE PACK_ID = ? AND VERSION = ?""",
                    params=[pr["state"], pr.get("merge_commit_sha") or "", r["PACK_ID"], int(r["VERSION"])]).collect()
        roundtrip.mirror(session, "QA_PACK", r["PACK_ID"], int(r["VERSION"]), pr["state"], pr.get("merge_commit_sha"))
        if pr["state"] in ("MERGED", "CLOSED"):
            out[pr["state"].lower()].append({"pack_id": r["PACK_ID"], "version": int(r["VERSION"])})
    return out


STTM_COLUMNS = ("target_column", "target_datatype", "mapping_type", "source_system", "source_table", "source_column",
                "source_datatype", "transformation", "filter_rule", "business_rule", "business_definition", "join_logic")


def sttm_files(entry: Dict[str, Any]) -> Dict[str, str]:
    """An STTM catalog version as repository files: the mapping as sttm/<domain>/<target>.csv (the STTM Store's CSV
    export) and its table design (grain, keys, dedup, SCD, strategy, row filters, join plan) beside it."""
    from services.sttm.refine import render_csv

    domain = re.sub(r"[^a-z0-9_]+", "_", str(entry.get("domain_name") or "domain").lower()).strip("_") or "domain"
    target = re.sub(r"[^a-z0-9_]+", "_", str(entry.get("target_entity") or "target").split(".")[-1].lower()).strip("_") or "target"
    lines = sorted(entry.get("lines") or [], key=lambda l: str(l.get("target_column") or ""))
    design = {"target_entity": entry.get("target_entity"), "source_system": entry.get("source_system"),
              "catalog_id": entry.get("catalog_id"), "version": entry.get("version"),
              "table_design": entry.get("table_design") or {}}
    return {f"sttm/{domain}/{target}.csv": render_csv([{f: l.get(f) for f in STTM_COLUMNS} for l in lines]),
            f"sttm/{domain}/{target}.design.json": json.dumps(design, indent=2, sort_keys=True, default=str) + "\n"}


def publish_sttm(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """An APPROVED STTM catalog version (STTM Store) as its repository files on a branch feature/sttm-<target>-v<version>
    with a pull request. Rendered here from the approved version; the pull request lives in CORE.GIT_PR only."""
    catalog_id, version = str(request.get("catalog_id") or ""), int(request.get("version") or 0)
    head = github.check_branch(str(request.get("head") or ""))
    assert head.startswith("feature/sttm-"), "an STTM branch starts with feature/sttm-"
    found = rows(session, """SELECT CATALOG_ID, VERSION, DOMAIN_ID, DOMAIN_NAME, SOURCE_SYSTEM, TARGET_ENTITY, STATUS,
                                    TABLE_DESIGN, LINES FROM CORE.STTM_CATALOG WHERE CATALOG_ID = ? AND VERSION = ?""",
                 [catalog_id, version])
    assert found, "mapping version not found"
    assert str(found[0]["STATUS"]).upper() == "APPROVED", "only an approved mapping version is published"
    entry = {str(k).lower(): v for k, v in found[0].items()}
    entry["lines"] = [{str(k).lower(): v for k, v in l.items()} for l in (variant(entry.get("lines")) or [])]
    entry["table_design"] = variant(entry.get("table_design")) or {}
    files = sttm_files(entry)
    repo_id = str(request.get("repo_id") or "")
    repo = _repo(session, repo_id)
    origin, base_branch = repo["GIT_URL"], str(request.get("base") or repo["BRANCH"] or "main")
    target = entry.get("target_entity") or catalog_id
    title = clip(request.get("title") or f"STTM {target} v{version} (approved)", 200)
    body = clip(f"Source-to-target mapping of {target} v{version}, approved in the STTM Store.\n\nFiles:\n"
                + "\n".join(f"- {p}" for p in sorted(files)), 6000)
    with tool_call(session, None, "publish_sttm", {"catalog_id": catalog_id, "version": version, "head": head}) as call:
        try:
            result = github.publish(github.urllib_request(_token()), origin, base_branch, head, files, title=title,
                                    body=body, message=title, draft=False)
            call.summary = f"{result['status']} {head}: PR {(result.get('pull_request') or {}).get('url') or 'none'}"
        except Exception as exc:
            result = {**github.explain(exc, origin), "base_branch": base_branch, "head_branch": head}
            result["detail"] = clip(result.get("detail"), 1000)
            call.status, call.error = "FAILED", clip(exc)
    result.setdefault("base_branch", base_branch)
    recorded = roundtrip.record(session, "STTM", catalog_id, version, result, asset_name=target,
                                domain_id=entry.get("domain_id"), repo_id=repo_id, branch=head, title=title, files=list(files))
    return {**result, "files": sorted(files), "recorded": recorded}


def publish_table_model(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """A table model version (Model Store) as its DDL and dbt files on a branch feature/model-<table>-v<version> with a
    pull request. The files are rendered here from the stored version, so only what was saved in the app is pushed;
    the version becomes PUBLISHED when the pull request is merged (CORE.GIT_PR sync)."""
    from services.modeling import table_model

    model_id, version = str(request.get("table_model_id") or ""), int(request.get("version") or 0)
    head = github.check_branch(str(request.get("head") or ""))
    assert head.startswith("feature/model-"), "a model branch starts with feature/model-"
    header = table_model._header(session, model_id)
    spec = table_model._spec(session, model_id, version, header)
    problems = [i for i in table_model.validate(spec) if i.get("severity") == "ERROR"]
    assert not problems, f"fix {len(problems)} problem(s) before publishing: {problems[0]['message']}"
    files = table_model.files(spec, header.get("model_layer") or "SILVER")
    repo_id = str(request.get("repo_id") or "")
    repo = _repo(session, repo_id)
    origin, base_branch = repo["GIT_URL"], str(request.get("base") or repo["BRANCH"] or "main")
    name = f"{header['source_name']}.{header['table_name']}"
    title = clip(request.get("title") or f"Table model {name} v{version}", 200)
    body = clip(f"Table model {name} v{version} ({header.get('model_layer')}, {header.get('table_type')}), saved in the "
                f"Model Store (table model {model_id}).\n\nFiles:\n" + "\n".join(f"- {p}" for p in sorted(files)), 6000)
    with tool_call(session, None, "publish_table_model", {"table_model_id": model_id, "version": version, "head": head}) as call:
        try:
            result = github.publish(github.urllib_request(_token()), origin, base_branch, head, files, title=title,
                                    body=body, message=title, draft=False)
            call.summary = f"{result['status']} {head}: PR {(result.get('pull_request') or {}).get('url') or 'none'}"
        except Exception as exc:
            result = {**github.explain(exc, origin), "base_branch": base_branch, "head_branch": head}
            result["detail"] = clip(result.get("detail"), 1000)
            call.status, call.error = "FAILED", clip(exc)
    result.setdefault("base_branch", base_branch)
    state, pr = roundtrip.outcome(result)
    session.sql(f"""UPDATE {table_model.VERSIONS} SET STATUS = IFF(? = 'NO_CHANGES', 'PUBLISHED', STATUS), REPO_ID = ?,
                           BRANCH = ?, PR_URL = NULLIF(?, ''), PR_NUMBER = NULLIF(?, '')::NUMBER, PR_STATE = ?,
                           PR_SYNCED_AT = CURRENT_TIMESTAMP()
                     WHERE TABLE_MODEL_ID = ? AND VERSION = ?""",
                params=[state or "", repo_id, head, pr.get("url") or "", str(pr.get("number") or ""), state,
                        model_id, version]).collect()
    recorded = roundtrip.record(session, "TABLE_MODEL", model_id, version, result, asset_name=name,
                                domain_id=header.get("domain_id"), repo_id=repo_id, branch=head, title=title,
                                files=list(files))
    return {**result, "files": sorted(files), "recorded": recorded}


def sync_git_prs(session, request: Dict[str, Any]) -> Dict[str, Any]:
    """Every governed pull request (the scheduled task and the Source control page): with V043 one pass over
    CORE.GIT_PR, which also updates each asset's own version table; before it, each asset's own sync."""
    if roundtrip.deployed(session):
        request_fn: Dict[str, Any] = {}

        def status(git_url: str, number: int) -> Dict[str, Any]:
            if "fn" not in request_fn:
                request_fn["fn"] = github.urllib_request(_token())
            return github.pull_status(request_fn["fn"], git_url, number)

        return {"source_control": True, **roundtrip.sync(session, request, status)}
    return {"models": sync_model_prs(session, request), "dq_suites": sync_dq_prs(session, request),
            "qa_packs": sync_qa_prs(session, request)}


def sync_task_sql(database: str, warehouse: str) -> List[str]:
    """A task that syncs every governed pull request (domain models, data quality suites, QA packs, STTM mappings)
    every 30 minutes (created with the publisher)."""
    for value in (database, warehouse):
        assert NAME.match(value or ""), f"unsafe identifier: {value}"
    return [f"CREATE OR REPLACE TASK {database}.CORE.SYNC_MODEL_PRS_TASK WAREHOUSE = {warehouse} "
            f"SCHEDULE = '30 MINUTE' COMMENT = 'Read the state of every governed pull request (CORE.GIT_PR)' "
            f"AS CALL {database}.CORE.PUBLISH_DBT_PR('', '{{\"sync_git_prs\": {{}}}}')",
            f"ALTER TASK {database}.CORE.SYNC_MODEL_PRS_TASK RESUME"]


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
    if not run_id and isinstance(payload.get("qa_pack"), dict):
        return publish_qa_pack(session, payload["qa_pack"])
    if not run_id and isinstance(payload.get("sync_qa_prs"), dict):
        return sync_qa_prs(session, payload["sync_qa_prs"])
    if not run_id and isinstance(payload.get("sttm"), dict):
        return publish_sttm(session, payload["sttm"])
    if not run_id and isinstance(payload.get("table_model"), dict):
        return publish_table_model(session, payload["table_model"])
    if not run_id and isinstance(payload.get("sync_git_prs"), dict):
        return sync_git_prs(session, payload["sync_git_prs"])
    if run_id and payload.get("star_schema"):
        return publish_star_schema(session, run_id, payload)
    plan = _plan(session, run_id, payload) if run_id else dict(payload)
    if payload.get("check_only"):
        try:
            return {"status": "OK", **github.preflight(github.urllib_request(_token()), plan.get("origin") or "")}
        except Exception as exc:
            return {**github.explain(exc, plan.get("origin") or ""), "check": True}
    run = rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    conv = conventions_for(lambda sql, params: rows(session, sql, params), run_standard(run[0] if run else {}),
                           run_pipeline(run[0] if run else {}), (run[0] if run else {}).get("DOMAIN_ID"))
    assert run, f"run not found: {run_id}"
    gen = rows(session, """SELECT GENERATION_ID, GENERATION_VERSION, STTM_ID FROM CORE.DBT_GENERATION_REGISTRY
                           WHERE RUN_ID = ? ORDER BY GENERATION_VERSION DESC LIMIT 1""", [run_id])
    generation_id = gen[0]["GENERATION_ID"] if gen else None
    result: Dict[str, Any]
    with tool_call(session, run_id, "publish_dbt_pr", {"cut_branch": plan.get("cut_branch")}) as call:
        try:
            assert gen, "generate dbt before publishing"
            files = {r["FILE_PATH"]: r["CONTENT"] for r in rows(
                session, "SELECT FILE_PATH, CONTENT FROM CORE.GENERATED_ARTIFACT WHERE GENERATION_ID = ?",
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
                    f"{scalar(session, 'SELECT CURRENT_DATABASE()')}.{PLATFORM_SCHEMA}.{conv['codegen_prefix']}"
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


def publish_star_schema(session, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """A Gold run's star schema as one branch and one pull request: the latest generation of every table run, merged
    (services/gold/star.merge_files). Pushed to the repository the table runs were generated for."""
    from services.dbt.procedures import branch_slug
    from services.gold.star import merge_files

    run = rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    assert run, f"run not found: {run_id}"
    children = rows(session, """SELECT R.RUN_ID, R.MODEL_ENTITY,
                                       (SELECT G.GENERATION_ID FROM CORE.DBT_GENERATION_REGISTRY G WHERE G.RUN_ID = R.RUN_ID
                                         AND G.GENERATION_STATUS IN ('GENERATED', 'APPROVED')
                                         ORDER BY G.GENERATION_VERSION DESC LIMIT 1) AS GENERATION_ID
                                  FROM CORE.WORKFLOW_RUN R WHERE R.PARENT_RUN_ID = ? AND R.DELETED_AT IS NULL
                                 ORDER BY R.CREATED_AT""", [run_id])
    assert children, "build the star schema first: this Gold run has no table runs"
    missing = [c["MODEL_ENTITY"] for c in children if not c["GENERATION_ID"]]
    assert not missing, "generate the code of every table first: " + ", ".join(str(m) for m in missing)
    conv = conventions_for(lambda sql, params: rows(session, sql, params), run_standard(run[0]), run_pipeline(run[0]),
                           run[0].get("DOMAIN_ID"))
    parts = []
    for c in children:
        files = {r["FILE_PATH"]: r["CONTENT"] for r in rows(
            session, "SELECT FILE_PATH, CONTENT FROM CORE.GENERATED_ARTIFACT WHERE GENERATION_ID = ?", [c["GENERATION_ID"]])}
        parts.append({"entity": c["MODEL_ENTITY"], "files": files})
    merged = merge_files(parts)
    plan = _plan(session, children[0]["RUN_ID"], {})   # the repository the table runs were generated for
    name = run[0]["RUN_NAME"] or run_id
    plan["cut_branch"] = payload.get("cut_branch") or f"{conv['branch_prefix']}{branch_slug(name, run_id)}"
    for key in ("base_branch", "title", "draft"):
        if payload.get(key) not in (None, ""):
            plan[key] = payload[key]
    tables = ", ".join(str(c["MODEL_ENTITY"]) for c in children)
    with tool_call(session, run_id, "publish_star_schema", {"cut_branch": plan.get("cut_branch"), "tables": tables}) as call:
        try:
            assert plan.get("origin") or plan.get("git_repository"), "the table runs were generated without a repository"
            result = github.publish(
                github.urllib_request(_token()),
                plan.get("origin") or "", plan.get("base_branch") or "main", plan["cut_branch"], merged["files"],
                title=plan.get("title") or f"{fill(conv['pr_title'], name=name, version=len(children))}: star schema",
                body=github.pr_body(name, run_id, sorted(merged["files"]), None)
                     + f"\n\nStar schema: {tables}."
                     + (f"\nDiffering copies kept once: {', '.join(merged['conflicts'])}." if merged["conflicts"] else ""),
                message=f"{name}: gold star schema ({tables})",
                draft=bool(plan.get("draft")),
                root=plan.get("project_dir") or "",
            )
            result["tables"] = [c["MODEL_ENTITY"] for c in children]
            result["conflicts"] = merged["conflicts"]
            call.summary = f"{result['status']} {plan.get('cut_branch')}: PR {(result.get('pull_request') or {}).get('url') or 'none'}"
        except Exception as exc:
            result = {**github.explain(exc, plan.get("origin") or ""), "base_branch": plan.get("base_branch"),
                      "head_branch": plan.get("cut_branch")}
            result["detail"] = clip(result.get("detail"), 1000)
            call.status, call.error = "FAILED", clip(exc)
    _record(session, run_id, None, plan, result)
    return result

