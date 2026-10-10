"""Code repositories: connect a Git repository through Snowflake's Git integration, index it (CODE.INDEX_REPO), refresh
it on demand or on a schedule (a Snowflake task), switch its branch, rotate its credentials, and search, read and trace
its code. Credentials never pass through this API's storage: a token given here goes straight into a Snowflake SECRET.

Repositories are configured once, here, and used everywhere: prompt context for dbt, QA, Soda, STTM and the copilot,
and the dbt workspace (clone, origin and base branch) for generating and publishing models.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import DATABASE, WAREHOUSE, Db
from app.main import _json, _snowflake_error, current_db
from services.code import context as code_ctx
from services.code.indexer import LOCK_MINUTES, SAFE_BRANCH, branch_segment, safe_path

router = APIRouter()
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,62}$")
URL = re.compile(r"^https://[A-Za-z0-9.\-]+(:\d+)?/[A-Za-z0-9_.\-/~%]+$")
# five cron fields, then a time zone such as UTC, Europe/London, America/Argentina/Buenos_Aires or Etc/GMT+5
CRON = re.compile(r"^([\w*/,\-]+ ){5}[A-Za-z_]+(/[A-Za-z0-9_+\-]+)*$")
PROVIDERS = {"github.com": "GITHUB", "gitlab.com": "GITLAB", "bitbucket.org": "BITBUCKET", "dev.azure.com": "AZURE_DEVOPS",
             "visualstudio.com": "AZURE_DEVOPS"}
_refreshing: set[str] = set()
_again: set[str] = set()


def _rows(db: Db):
    return lambda sql, params: db.query(sql.replace("?", "%s"), tuple(params))


def _search(db: Db):
    def run(request: dict) -> list:
        found = db.query("SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(%s, %s) AS R",
                         (f"{DATABASE}.{code_ctx.SERVICE}", json.dumps(request)))
        return (_json(found[0]["r"]) or {}).get("results", []) if found else []
    return run


def provider_of(url: str) -> str:
    host = url.split("/")[2].lower()
    return next((v for k, v in PROVIDERS.items() if host == k or host.endswith("." + k)), "OTHER")


def same_origin(a: str, b: str) -> bool:
    norm = lambda u: (u or "").strip().lower().removesuffix("/").removesuffix(".git")  # noqa: E731
    return norm(a) == norm(b)


def _repo(db: Db, repo_id: str) -> dict:
    found = db.query("SELECT * FROM CODE.REPO WHERE REPO_ID = %s", (repo_id,))
    if not found:
        raise HTTPException(404, "repository not found")
    r = found[0]
    for k in ("domain_ids", "include_globs", "exclude_globs", "stats", "created_objects"):
        r[k] = _json(r.get(k)) or ([] if k != "stats" else {})
    return r


def _task_name(repo_id: str) -> str:
    return f"{DATABASE}.CODE.REFRESH_{repo_id.replace('-', '')[:12].upper()}"


def _platform_repo_name(name: str) -> str:
    return f"{DATABASE}.CODE.{name}"


def _platform_secret_name(name: str) -> str:
    return f"{DATABASE}.CODE.{name}_GIT_TOKEN"


def _owns_git_repo(repo: dict) -> bool:
    """Whether the platform created this repository's GIT REPOSITORY object (a reused one is never altered or dropped)."""
    target = str(repo.get("git_repository") or "").upper()
    if any(o.get("kind") == "GIT REPOSITORY" and str(o.get("name", "")).upper() == target for o in repo.get("created_objects") or []):
        return True
    return target == _platform_repo_name(str(repo.get("name") or "")).upper()  # rows from before CREATED_OBJECTS existed


def _branches(db: Db, fqn: str, fetch: bool = True) -> tuple[list[dict], str]:
    """(branches as last fetched, fetch error). A failed FETCH still lists what the clone already has."""
    error = ""
    if fetch:
        try:
            db.execute(f"ALTER GIT REPOSITORY {fqn} FETCH")
        except Exception as exc:
            error = str(exc)[:600]
    try:
        found = db.query(f"SHOW GIT BRANCHES IN GIT REPOSITORY {fqn}")
    except Exception as exc:
        return [], error or str(exc)[:600]
    return sorted(({"name": str(r.get("name") or "").strip("/"), "commit": str(r.get("commit_hash") or "")} for r in found),
                  key=lambda b: b["name"]), error


COMMON_BRANCHES = ("main", "master", "develop", "dev")


def _missing_branch(branch: str, names: list[str], where: str) -> HTTPException:
    ordered = [n for n in COMMON_BRANCHES if n in names] + [n for n in names if n not in COMMON_BRANCHES]
    return HTTPException(400, f"Branch '{branch}' is not in {where}. Available: {', '.join(ordered[:20]) or 'none'}")


def _privilege_hint(db: Db, text: str, integration: str) -> Optional[HTTPException]:
    if "Insufficient privileges" in text and "Integration" in text:
        from services.dbt.workspace import grant_sql

        role = (db.query("SELECT CURRENT_ROLE() AS R") or [{}])[0].get("r") or "<role>"
        return HTTPException(403, f"This role can see {integration} but cannot use it. An admin can run: "
                                  f"{grant_sql(integration, role)}")
    return None


def _mask(text: str, token: Optional[str]) -> str:
    token = (token or "").strip()
    return text.replace(token, "***") if token else text


@router.get("/api/code/setup")
def setup(db: Db = Depends(current_db)):
    """Git integrations this role can use and the Git repository objects already in the account."""
    from services.dbt.workspace import discover

    data = discover(lambda sql: db.query(sql))
    secrets = []
    try:
        secrets = [{"name": f"{r['database_name']}.{r['schema_name']}.{r['name']}", "type": r.get("secret_type")}
                   for r in db.query("SHOW SECRETS IN ACCOUNT") if str(r.get("secret_type") or "").upper() == "PASSWORD"]
    except Exception:
        pass
    return {"integrations": data.get("integrations", []), "repositories": data.get("git_repositories", []),
            "secrets": secrets, "warnings": data.get("warnings", [])}


@router.get("/api/code/repos")
def list_repos(db: Db = Depends(current_db)):
    try:
        repos = db.query(f"""WITH LAST AS (
                                 SELECT REPO_ID, OBJECT_CONSTRUCT('status', STATUS, 'started_at', STARTED_AT::VARCHAR,
                                                                  'duration_ms', DURATION_MS, 'files_changed', FILES_CHANGED,
                                                                  'error', ERROR) AS LAST_RUN
                                   FROM CODE.INDEX_RUN QUALIFY ROW_NUMBER() OVER (PARTITION BY REPO_ID ORDER BY STARTED_AT DESC) = 1)
                               SELECT R.*, R.LAST_INDEXED_AT::VARCHAR AS LAST_INDEXED, R.CREATED_AT::VARCHAR AS CREATED, L.LAST_RUN,
                                      R.LOCK_RUN_ID IS NOT NULL AND R.LOCKED_AT >= DATEADD(minute, -{LOCK_MINUTES}, CURRENT_TIMESTAMP())
                                        AS LOCK_LIVE
                                 FROM CODE.REPO R LEFT JOIN LAST L ON L.REPO_ID = R.REPO_ID ORDER BY R.NAME""")
    except Exception as exc:
        if "does not exist" not in str(exc) and "invalid identifier" not in str(exc):
            raise _snowflake_error(exc) from exc
        return {"repos": [], "ready": False}
    for r in repos:
        for k in ("domain_ids", "include_globs", "exclude_globs", "stats", "last_run", "created_objects"):
            r[k] = _json(r.get(k)) or ([] if k in ("domain_ids", "include_globs", "exclude_globs", "created_objects") else {})
        r["refreshing"] = r["repo_id"] in _refreshing
        if r.get("status") == "INDEXING" and not r.get("lock_live"):
            # the run was killed (timeout, cancel) before it could record its end
            r["status"] = "FAILED"
            r["error"] = r.get("error") or "The last index run stopped without finishing (timed out or was cancelled). Refresh to run it again."
        r["owns_git_repository"] = _owns_git_repo(r)
        for k in ("lock_run_id", "locked_at", "lock_live"):
            r.pop(k, None)
    return {"repos": repos, "ready": True}


class RepoIn(BaseModel):
    name: str = Field(min_length=2, max_length=63)
    git_url: str = Field(min_length=10, max_length=1024)
    branch: str = Field(default="main", min_length=1, max_length=200)
    api_integration: str = Field(min_length=1, max_length=256)
    existing_git_repository: Optional[str] = Field(default=None, max_length=512)
    secret_name: Optional[str] = Field(default=None, max_length=512)
    username: Optional[str] = Field(default=None, max_length=256)
    token: Optional[str] = Field(default=None, max_length=4096)
    domain_ids: list[str] = Field(default_factory=list)
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    kind: Literal["DBT", "SQL", "PYTHON", "MIXED"] = "MIXED"
    index_now: bool = True


def _put_secret(db: Db, name: str, username: Optional[str], token: str) -> str:
    """Create or replace the platform's secret for a repository; the token goes straight into Snowflake."""
    secret = _platform_secret_name(name)
    db.execute(f"CREATE OR REPLACE SECRET {secret} TYPE = PASSWORD USERNAME = %s PASSWORD = %s "
               "COMMENT = 'Git credentials for code context'", ((username or "token").strip() or "token", token.strip()))
    return secret


@router.post("/api/code/repos")
def create_repo(body: RepoIn, db: Db = Depends(current_db)):
    from services.dbt.workspace import quote_exact, safe_fqn

    name = body.name.strip().upper()
    if not NAME.match(name):
        raise HTTPException(400, "Name: letters, digits and underscores, starting with a letter")
    url = body.git_url.strip().removesuffix("/")
    if not URL.match(url):
        raise HTTPException(400, "Give the repository's https URL")
    branch = body.branch.strip().strip("/")
    if not SAFE_BRANCH.fullmatch(branch):
        raise HTTPException(400, "Branch name has unexpected characters")
    if db.query("SELECT 1 FROM CODE.REPO WHERE NAME = %s", (name,)):
        raise HTTPException(409, f"A repository named {name} is already connected")
    secret = (body.secret_name or "").strip() or None
    created: list[dict] = []
    try:
        if body.token and body.token.strip():
            secret = _put_secret(db, name, body.username, body.token)
            created.append({"kind": "SECRET", "name": secret})
        if body.existing_git_repository:
            git_repo = safe_fqn(body.existing_git_repository)
            described = db.query(f"DESCRIBE GIT REPOSITORY {git_repo}")
            origin = str((described or [{}])[0].get("origin") or "")
            if origin and not same_origin(origin, url):
                raise HTTPException(409, f"{git_repo} points to {origin}, not {url}.")
            url = origin.removesuffix("/") or url
        else:
            git_repo = _platform_repo_name(name)
            try:
                described = db.query(f"DESCRIBE GIT REPOSITORY {git_repo}")
            except Exception:
                described = []
            if described:
                # left behind by an earlier connect or disconnect of the same name: reuse it only for the same origin
                origin = str(described[0].get("origin") or "")
                if not same_origin(origin, url):
                    raise HTTPException(409, f"A Snowflake Git repository {git_repo} already points to {origin}. "
                                             "Use another name, or disconnect with 'also drop Snowflake objects' first.")
                if secret:
                    db.execute(f"ALTER GIT REPOSITORY {git_repo} SET GIT_CREDENTIALS = {safe_fqn(secret)}")
            else:
                creds = f" GIT_CREDENTIALS = {safe_fqn(secret)}" if secret else ""
                db.execute(f"CREATE GIT REPOSITORY {git_repo} API_INTEGRATION = {quote_exact(body.api_integration)} "
                           f"ORIGIN = '{url}'{creds} COMMENT = 'Code context repository'")
            created.append({"kind": "GIT REPOSITORY", "name": git_repo})
        remote, fetch_error = _branches(db, git_repo)
        if fetch_error:
            raise RuntimeError(fetch_error)
    except HTTPException:
        raise
    except Exception as exc:
        text = _mask(str(exc), body.token)
        raise (_privilege_hint(db, text, body.api_integration) or _snowflake_error(Exception(text))) from None
    names = [b["name"] for b in remote]
    if branch not in names:
        raise _missing_branch(branch, names, url)
    repo_id = str(uuid.uuid4())
    db.execute("""INSERT INTO CODE.REPO (REPO_ID, NAME, GIT_URL, PROVIDER, BRANCH, GIT_REPOSITORY, API_INTEGRATION, SECRET_NAME,
                         DOMAIN_IDS, INCLUDE_GLOBS, EXCLUDE_GLOBS, KIND, CREATED_OBJECTS)
                  SELECT %s, %s, %s, %s, %s, %s, %s, NULLIF(%s, ''), PARSE_JSON(%s), PARSE_JSON(%s), PARSE_JSON(%s), %s, PARSE_JSON(%s)""",
               (repo_id, name, url, provider_of(url), branch, git_repo, body.api_integration, secret or "",
                json.dumps(body.domain_ids), json.dumps(body.include_globs), json.dumps(body.exclude_globs), body.kind,
                json.dumps(created)))
    if body.index_now:
        _start_refresh(db, repo_id)
    return _repo(db, repo_id)


@router.get("/api/code/repos/{repo_id}/branches")
def repo_branches(repo_id: str, fetch: bool = True, db: Db = Depends(current_db)):
    """The repository's branches (after a FETCH), with the configured one marked."""
    from services.dbt.workspace import safe_fqn

    repo = _repo(db, repo_id)
    found, error = _branches(db, safe_fqn(repo["git_repository"]), fetch)
    for b in found:
        b["current"] = b["name"] == repo["branch"]
    return {"branches": found, "current": repo["branch"], "fetched": fetch and not error, "error": error or None,
            "current_exists": any(b["current"] for b in found)}


class RepoUpdate(BaseModel):
    branch: Optional[str] = Field(default=None, max_length=200)
    domain_ids: Optional[list[str]] = None
    include_globs: Optional[list[str]] = None
    exclude_globs: Optional[list[str]] = None
    kind: Optional[Literal["DBT", "SQL", "PYTHON", "MIXED"]] = None
    enabled: Optional[bool] = None
    # dbt workspace settings (used by every run whose domain this repository serves)
    use_for_dbt: Optional[bool] = None
    dbt_project_dir: Optional[str] = Field(default=None, max_length=512)
    open_pr: Optional[bool] = None
    draft_pr: Optional[bool] = None


def project_roots(repo: dict) -> list[str]:
    """dbt project folders found by the last index ('' is the repository root)."""
    return [str(p.get("root") or "") for p in (repo.get("stats") or {}).get("dbt_project_roots") or []]


def _flag(value: Optional[bool], current: Any, default: bool) -> bool:
    if value is not None:
        return value
    return default if current is None else bool(current)


def _reset_index(db: Db, repo_id: str) -> None:
    for table in ("CODE_CHUNK", "CODE_EDGE", "CODE_FILE", "REPO_SUMMARY"):
        db.execute(f"DELETE FROM CODE.{table} WHERE REPO_ID = %s", (repo_id,))
    db.execute("""UPDATE CODE.REPO SET STATS = NULL, LAST_COMMIT = NULL, LAST_INDEXED_AT = NULL, ERROR = NULL,
                         STATUS = IFF(LOCK_RUN_ID IS NULL, 'NEW', STATUS) WHERE REPO_ID = %s""", (repo_id,))


@router.put("/api/code/repos/{repo_id}")
def update_repo(repo_id: str, body: RepoUpdate, db: Db = Depends(current_db)):
    """Change a repository. A different branch or path filter is a different file set: the index is rebuilt at once
    (a run in progress on the old branch notices and stops without writing). Domains, kind and enabled apply as is."""
    from services.dbt.workspace import safe_fqn

    repo = _repo(db, repo_id)
    branch = (body.branch or repo["branch"]).strip().strip("/")
    if not SAFE_BRANCH.fullmatch(branch):
        raise HTTPException(400, "Branch name has unexpected characters")
    clean = lambda xs: [x.strip() for x in xs if x and x.strip()]  # noqa: E731
    include = clean(body.include_globs) if body.include_globs is not None else repo["include_globs"]
    exclude = clean(body.exclude_globs) if body.exclude_globs is not None else repo["exclude_globs"]
    new_files = branch != repo["branch"] or include != repo["include_globs"] or exclude != repo["exclude_globs"]
    if branch != repo["branch"]:
        found, error = _branches(db, safe_fqn(repo["git_repository"]))
        if branch not in [b["name"] for b in found]:
            if error and not found:
                raise _snowflake_error(Exception(error))
            raise _missing_branch(branch, [b["name"] for b in found], repo["git_url"])
    db.execute("""UPDATE CODE.REPO SET BRANCH = %s, DOMAIN_IDS = PARSE_JSON(%s), INCLUDE_GLOBS = PARSE_JSON(%s),
                         EXCLUDE_GLOBS = PARSE_JSON(%s), KIND = %s, ENABLED = %s WHERE REPO_ID = %s""",
               (branch, json.dumps(body.domain_ids if body.domain_ids is not None else repo["domain_ids"]),
                json.dumps(include), json.dumps(exclude), body.kind or repo["kind"],
                repo["enabled"] if body.enabled is None else body.enabled, repo_id))
    if any(v is not None for v in (body.use_for_dbt, body.dbt_project_dir, body.open_pr, body.draft_pr)):
        folder = repo.get("dbt_project_dir") or ""
        if body.dbt_project_dir is not None:
            folder = body.dbt_project_dir.replace("\\", "/").strip().strip("/")
            if folder and not safe_path(folder):
                raise HTTPException(400, "The project folder has unexpected characters")
            roots = project_roots(repo)
            if roots and folder not in roots:
                raise HTTPException(400, f"No dbt_project.yml in '{folder or '(root)'}'. Found: {', '.join(r or '(root)' for r in roots)}")
        db.execute("""UPDATE CODE.REPO SET USE_FOR_DBT = %s, DBT_PROJECT_DIR = %s, OPEN_PR = %s, DRAFT_PR = %s WHERE REPO_ID = %s""",
                   (_flag(body.use_for_dbt, repo.get("use_for_dbt"), True), folder, _flag(body.open_pr, repo.get("open_pr"), True),
                    _flag(body.draft_pr, repo.get("draft_pr"), False), repo_id))
    if new_files:
        _reset_index(db, repo_id)
        _start_refresh(db, repo_id)
    out = _repo(db, repo_id)
    out["reindexing"] = new_files
    return out


class CredentialsIn(BaseModel):
    mode: Literal["token", "secret", "public"]
    username: Optional[str] = Field(default=None, max_length=256)
    token: Optional[str] = Field(default=None, max_length=4096)
    secret_name: Optional[str] = Field(default=None, max_length=512)


@router.put("/api/code/repos/{repo_id}/credentials")
def set_credentials(repo_id: str, body: CredentialsIn, db: Db = Depends(current_db)):
    """Rotate or change how Snowflake signs in to the repository, then test it with a FETCH."""
    from services.dbt.workspace import safe_fqn

    repo = _repo(db, repo_id)
    if not _owns_git_repo(repo):
        raise HTTPException(400, f"This repository reuses the Snowflake Git repository {repo['git_repository']}, which the platform "
                                 "did not create. Change its GIT_CREDENTIALS in Snowflake, or connect again with a new clone.")
    fqn = safe_fqn(repo["git_repository"])
    created = list(repo.get("created_objects") or [])
    try:
        if body.mode == "token":
            if not (body.token or "").strip():
                raise HTTPException(400, "Paste the new token")
            secret: Optional[str] = _put_secret(db, repo["name"], body.username, body.token or "")
            if not any(o.get("kind") == "SECRET" and o.get("name") == secret for o in created):
                created.append({"kind": "SECRET", "name": secret})
        elif body.mode == "secret":
            if not (body.secret_name or "").strip():
                raise HTTPException(400, "Choose a secret")
            secret = safe_fqn(body.secret_name or "")
        else:
            secret = None
        db.execute(f"ALTER GIT REPOSITORY {fqn} SET GIT_CREDENTIALS = {safe_fqn(secret)}" if secret
                   else f"ALTER GIT REPOSITORY {fqn} UNSET GIT_CREDENTIALS")
        db.execute(f"ALTER GIT REPOSITORY {fqn} FETCH")
    except HTTPException:
        raise
    except Exception as exc:
        text = _mask(str(exc), body.token)
        raise (_privilege_hint(db, text, repo.get("api_integration") or "") or _snowflake_error(Exception(text))) from None
    db.execute("UPDATE CODE.REPO SET SECRET_NAME = NULLIF(%s, ''), CREATED_OBJECTS = PARSE_JSON(%s), ERROR = NULL WHERE REPO_ID = %s",
               (secret or "", json.dumps(created), repo_id))
    return _repo(db, repo_id)


@router.delete("/api/code/repos/{repo_id}")
def remove_repo(repo_id: str, drop_objects: bool = False, db: Db = Depends(current_db)):
    """Disconnect: stop the schedule and drop the index, history and usage. With drop_objects, also drop the Snowflake
    Git repository and secret the platform created for it (never reused objects, nor ones another repository uses)."""
    repo = _repo(db, repo_id)
    try:
        db.execute(f"DROP TASK IF EXISTS {_task_name(repo_id)}")
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    # deleting the row first makes a run in progress stop without writing (it checks the row before each write)
    db.execute("DELETE FROM CODE.REPO WHERE REPO_ID = %s", (repo_id,))
    for table in ("CODE_CHUNK", "CODE_EDGE", "CODE_FILE", "REPO_SUMMARY", "INDEX_RUN", "CODE_USAGE"):
        try:
            db.execute(f"DELETE FROM CODE.{table} WHERE REPO_ID = %s", (repo_id,))
        except Exception:
            pass
    dropped, kept = [], []
    if drop_objects:
        others = {str(r["git_repository"]).upper() for r in db.query("SELECT GIT_REPOSITORY FROM CODE.REPO")}
        others_secrets = {str(r["secret_name"]).upper() for r in db.query("SELECT SECRET_NAME FROM CODE.REPO WHERE SECRET_NAME IS NOT NULL")}
        owned = list(repo.get("created_objects") or [])
        if not owned and _owns_git_repo(repo):
            owned = [{"kind": "GIT REPOSITORY", "name": repo["git_repository"]}]
        for obj in sorted(owned, key=lambda o: o.get("kind") != "GIT REPOSITORY"):  # the clone before its secret
            name = str(obj.get("name") or "")
            in_use = name.upper() in (others if obj.get("kind") == "GIT REPOSITORY" else others_secrets)
            if in_use or obj.get("kind") not in ("GIT REPOSITORY", "SECRET"):
                kept.append(name)
                continue
            try:
                db.execute(f"DROP {obj['kind']} IF EXISTS {name}")
                dropped.append(name)
            except Exception as exc:
                kept.append(f"{name} ({str(exc)[:120]})")
    _refreshing.discard(repo_id)
    return {"removed": repo_id, "dropped": dropped, "kept": kept}


def _start_refresh(db: Db, repo_id: str) -> None:
    """Index in the background. When another run holds the repository (the old branch, a scheduled run), wait for it
    to stop and then run, so a branch switch always ends with the new branch indexed."""
    if repo_id in _refreshing:
        _again.add(repo_id)  # asked again while running (say, a second branch switch): run once more afterwards
        return
    _refreshing.add(repo_id)

    def run() -> None:
        try:
            for _ in range(60):
                _again.discard(repo_id)
                try:
                    result = db.call("CALL CODE.INDEX_REPO(%s)", (repo_id,))
                except Exception:
                    result = None  # the procedure records the failure on the repository and its index run
                result = _json(result) if isinstance(result, str) else result
                reason = str(result.get("reason") or "") if isinstance(result, dict) else ""
                if "in progress" in reason:
                    time.sleep(15)  # another run holds the repository; it stops or finishes, then this one runs
                    continue
                if repo_id in _again or "branch changed" in reason:
                    continue  # the configuration changed during the run: index the current one
                break
        finally:
            _refreshing.discard(repo_id)
            _again.discard(repo_id)

    threading.Thread(target=run, name=f"code-index-{repo_id[:8]}", daemon=True).start()


@router.post("/api/code/repos/{repo_id}/refresh")
def refresh(repo_id: str, wait: bool = False, db: Db = Depends(current_db)):
    _repo(db, repo_id)
    if wait:
        try:
            return {"result": db.call("CALL CODE.INDEX_REPO(%s)", (repo_id,))}
        except Exception as exc:
            raise _snowflake_error(exc) from exc
    _start_refresh(db, repo_id)
    return {"started": True, "repo_id": repo_id}


class Schedule(BaseModel):
    cron: str = Field(min_length=9, max_length=120)


@router.put("/api/code/repos/{repo_id}/schedule")
def set_schedule(repo_id: str, body: Schedule, db: Db = Depends(current_db)):
    _repo(db, repo_id)
    cron = " ".join(body.cron.split())
    if not CRON.match(cron):
        raise HTTPException(400, "Use five cron fields and a time zone, for example '0 6 * * * UTC' or '0 6 * * MON-FRI Europe/London'")
    task = _task_name(repo_id)
    try:
        db.execute(f"CREATE OR REPLACE TASK {task} WAREHOUSE = {WAREHOUSE} SCHEDULE = 'USING CRON {cron}' "
                   f"COMMENT = 'Code context: scheduled repository refresh' AS CALL {DATABASE}.CODE.INDEX_REPO('{repo_id}')")
        db.execute(f"ALTER TASK {task} RESUME")
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    db.execute("UPDATE CODE.REPO SET SCHEDULE_CRON = %s WHERE REPO_ID = %s", (cron, repo_id))
    return {"schedule": cron, "task": task}


@router.delete("/api/code/repos/{repo_id}/schedule")
def clear_schedule(repo_id: str, db: Db = Depends(current_db)):
    _repo(db, repo_id)
    try:
        db.execute(f"DROP TASK IF EXISTS {_task_name(repo_id)}")
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    db.execute("UPDATE CODE.REPO SET SCHEDULE_CRON = NULL WHERE REPO_ID = %s", (repo_id,))
    return {"schedule": None}


@router.get("/api/code/repos/{repo_id}/runs")
def index_runs(repo_id: str, db: Db = Depends(current_db)):
    return {"runs": db.query("""SELECT INDEX_RUN_ID, STATUS, STARTED_AT::VARCHAR AS STARTED_AT, FINISHED_AT::VARCHAR AS FINISHED_AT,
                                       COMMIT_SHA, FILES_SEEN, FILES_CHANGED, FILES_REMOVED, CHUNKS, EDGES, DURATION_MS,
                                       TRIGGERED_BY, ERROR
                                  FROM CODE.INDEX_RUN WHERE REPO_ID = %s ORDER BY STARTED_AT DESC LIMIT 30""", (repo_id,))}


def like_pattern(q: str) -> str:
    """A LIKE pattern that matches q literally (ESCAPE '!')."""
    return "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


@router.get("/api/code/search")
def search(q: str, repo_id: Optional[str] = None, kind: Optional[str] = None, limit: int = 20,
           db: Db = Depends(current_db)):
    """Name matches first, then Cortex Search hits, across connected repositories."""
    q = q.strip()
    if not q:
        return {"hits": []}
    repos = [r for r in db.query("SELECT REPO_ID, NAME FROM CODE.REPO WHERE ENABLED") if not repo_id or r["repo_id"] == repo_id]
    if not repos:
        return {"hits": []}
    names = {r["repo_id"]: r["name"] for r in repos}
    ids = list(names)
    pattern = like_pattern(q)
    exact = db.query("""SELECT CHUNK_ID, REPO_ID, PATH, START_LINE, END_LINE, KIND, NAME, LEFT(TEXT, 600) AS TEXT
                          FROM CODE.CODE_CHUNK
                         WHERE ARRAY_CONTAINS(REPO_ID::VARIANT, PARSE_JSON(%s)) AND (%s IS NULL OR KIND = %s)
                           AND (UPPER(NAME) LIKE UPPER(%s) ESCAPE '!' OR UPPER(PATH) LIKE UPPER(%s) ESCAPE '!')
                         ORDER BY IFF(UPPER(NAME) = UPPER(%s), 0, 1), LENGTH(PATH) LIMIT 15""",
                     (json.dumps(ids), kind, kind, pattern, pattern, q))
    hits = {h["chunk_id"]: {**h, "match": "name"} for h in exact}
    try:
        flt: list[Any] = [{"@or": [{"@eq": {"REPO_ID": i}} for i in ids]} if len(ids) > 1 else {"@eq": {"REPO_ID": ids[0]}}]
        if kind:
            flt.append({"@eq": {"KIND": kind}})
        found = _search(db)({"query": q[:2000], "columns": code_ctx.SEARCH_COLUMNS, "limit": max(1, min(limit, 30)),
                             "filter": {"@and": flt} if len(flt) > 1 else flt[0]})
        for h in found:
            h = {str(k).lower(): v for k, v in h.items()}
            hits.setdefault(h["chunk_id"], {**h, "text": str(h.get("text") or "")[:600], "match": "search"})
    except Exception:
        pass
    out = list(hits.values())[: max(1, min(limit, 50))]
    for h in out:
        h["repo_name"] = names.get(h.get("repo_id"))
    return {"hits": out}


def _read_text(db: Db, fqn: str, ref: str, path: str) -> str:
    """Whole file at a branch or commit reference such as branches/main or commits/<sha>."""
    stage = f"@{fqn}/{ref}/{path}"
    read = lambda sql: [list(r.values())[0] for r in db.query(sql)]  # noqa: E731
    try:
        chunks = read(f"SELECT $1 FROM {stage} (FILE_FORMAT => '{DATABASE}.CODEGEN.RAW_TEXT_FORMAT')")
    except Exception:
        chunks = read(f"SELECT $1 FROM {stage}")
    return "\n".join(str(c) for c in chunks if c is not None)


@router.get("/api/code/file")
def file(repo_id: str, path: str, db: Db = Depends(current_db)):
    """A file as it is in the indexed commit (so highlights and line numbers match the index), with its chunks."""
    from services.code.dbt_parse import scrub
    from services.dbt.workspace import safe_fqn

    repo = _repo(db, repo_id)
    if not safe_path(path):
        raise HTTPException(400, "invalid path")
    known = db.query("SELECT SKIPPED_REASON FROM CODE.CODE_FILE WHERE REPO_ID = %s AND PATH = %s", (repo_id, path))
    if not known:
        raise HTTPException(404, "This file is not in the index (it may be excluded or not a text file)")
    if known[0].get("skipped_reason"):
        raise HTTPException(422, f"This file could not be indexed: {known[0]['skipped_reason']}")
    fqn = safe_fqn(repo["git_repository"])
    commit = str(repo.get("last_commit") or "")
    text, at = None, "branch"
    if re.fullmatch(r"[0-9a-f]{7,64}", commit):
        try:
            text, at = _read_text(db, fqn, f"commits/{commit}", path), "commit"
        except Exception:
            text = None
    if text is None:
        try:
            text = _read_text(db, fqn, f"branches/{branch_segment(repo['branch'])}", path)
        except Exception as exc:
            raise _snowflake_error(exc) from exc
    chunks = db.query("""SELECT CHUNK_ID, START_LINE, END_LINE, KIND, NAME, REFS, SOURCES, COLUMNS, TESTS
                           FROM CODE.CODE_CHUNK WHERE REPO_ID = %s AND PATH = %s ORDER BY START_LINE""", (repo_id, path))
    for c in chunks:
        for k in ("refs", "sources", "columns", "tests"):
            c[k] = _json(c.get(k)) or []
    return {"repo": {"repo_id": repo_id, "name": repo["name"], "git_url": repo["git_url"], "branch": repo["branch"],
                     "commit": commit or None}, "path": path, "text": scrub(text), "chunks": chunks, "read_at": at}


@router.get("/api/code/lineage")
def lineage(name: str, repo_id: Optional[str] = None, db: Db = Depends(current_db)):
    """What a model, source or macro uses and what uses it (two hops each way), in enabled repositories."""
    key = name.strip().upper()
    scope = "AND REPO_ID = %s" if repo_id else "AND REPO_ID IN (SELECT REPO_ID FROM CODE.REPO WHERE ENABLED)"
    args = (repo_id,) if repo_id else ()
    up, down, frontier_up, frontier_down = [], [], {key}, {key}
    for _ in range(2):
        if frontier_up:
            rows = db.query(f"""SELECT FROM_NAME, TO_NAME, KIND, PATH, REPO_ID, ORIGIN FROM CODE.CODE_EDGE
                                 WHERE ARRAY_CONTAINS(UPPER(FROM_NAME)::VARIANT, PARSE_JSON(%s)) {scope} LIMIT 300""",
                            (json.dumps(sorted(frontier_up)), *args))
            up += rows
            frontier_up = {str(r["to_name"]).split(".")[-1].upper() for r in rows if r["kind"] == "REF"}
        if frontier_down:
            rows = db.query(f"""SELECT FROM_NAME, TO_NAME, KIND, PATH, REPO_ID, ORIGIN FROM CODE.CODE_EDGE
                                 WHERE ARRAY_CONTAINS(UPPER(SPLIT_PART(TO_NAME, '.', -1))::VARIANT, PARSE_JSON(%s)) {scope}
                                 LIMIT 300""", (json.dumps(sorted(frontier_down)), *args))
            down += rows
            frontier_down = {str(r["from_name"]).upper() for r in rows}
    return {"name": name, "upstream": up, "downstream": down}


def _graph(db: Db, repo_id: Optional[str]):
    from services.code import graph as code_graph

    ids = [repo_id] if repo_id else [r["repo_id"] for r in db.query("SELECT REPO_ID FROM CODE.REPO WHERE ENABLED")]
    return code_graph.load(_rows(db), ids)


@router.get("/api/code/impact")
def impact(name: str, repo_id: Optional[str] = None, depth: int = 3, db: Db = Depends(current_db)):
    """What `name` depends on and everything that depends on it, transitively: what may break when it changes."""
    g = _graph(db, repo_id)
    found = g.impact(name, depth)
    return {"name": name, "known": g.knows(name), "uses": g.uses(name, 1), "impact": found,
            "direct": sum(1 for i in found if i["depth"] == 1), "total": len(found)}


@router.get("/api/code/path")
def dependency_path(source: str, target: str, repo_id: Optional[str] = None, db: Db = Depends(current_db)):
    """How `source` reaches `target` through the code graph (refs, macro calls, function calls, table reads)."""
    return {"source": source, "target": target, "steps": _graph(db, repo_id).path(source, target)}


MATERIALIZED = re.compile(r"materialized\s*=\s*['\"](\w+)", re.I)


def build_catalog(chunks: list[dict], graph: Any) -> dict:
    """dbt models (and snapshots), sources and macros of a repository with test and documentation coverage, from its
    indexed chunks and code graph. Counts are exact: one test per test definition in schema.yml."""
    def arr(v: Any) -> list:
        v = _json(v) if isinstance(v, str) else v
        return list(v or [])

    schema = {str(c["name"]).upper(): c for c in chunks if c["kind"] == "DBT_SCHEMA_YML" and c.get("name")}
    models, sources, macros = [], [], []
    for c in chunks:
        name = c.get("name") or ""
        if c["kind"] in ("DBT_MODEL", "DBT_SNAPSHOT") and name:
            entry = schema.get(name.upper())
            tests = arr(entry["tests"]) if entry else []
            columns = arr(c.get("columns"))
            documented = arr(entry["columns"]) if entry else []
            uses, impact = graph.uses(name, 1), graph.impact(name, 6)
            models.append({
                "name": name, "kind": "snapshot" if c["kind"] == "DBT_SNAPSHOT" else "model", "path": c["path"],
                "line": c["start_line"], "folder": c["path"].rsplit("/", 1)[0] if "/" in c["path"] else "",
                "materialized": (MATERIALIZED.search(c.get("text") or "") or [None, None])[1],
                "columns": len(columns), "documented_columns": len([d for d in documented if d in set(columns)]) if columns else len(documented),
                "schema_path": entry["path"] if entry else None, "schema_line": entry["start_line"] if entry else None,
                "tests": len(tests), "test_list": tests[:40],
                "refs": len(arr(c.get("refs"))), "sources": len(arr(c.get("sources"))),
                "macros": sorted({u["name"] for u in uses if u["via"] == "MACRO_USE"}),
                "hard_coded": sorted({u["name"] for u in uses if u["via"] == "READS"}),
                "upstream": len(uses), "downstream": sum(1 for i in impact if i["depth"] == 1), "reach": len(impact),
            })
        elif c["kind"] == "DBT_SOURCE" and name:
            tests = arr(c.get("tests"))
            for table in arr(c.get("sources")):
                short = table.split(".")[-1].upper()
                sources.append({"source": name, "table": table, "path": c["path"], "line": c["start_line"],
                                "tests": sum(1 for t in tests if ":" in t and t.split(":", 1)[1].split(".")[0] == short),
                                "used_by": sum(1 for i in graph.impact(table, 1))})
        elif c["kind"] == "DBT_MACRO" and name:
            macros.append({"name": name, "path": c["path"], "line": c["start_line"],
                           "used_by": sum(1 for i in graph.impact(name, 1))})
    real = [m for m in models if m["kind"] == "model"]
    return {
        "models": sorted(models, key=lambda m: (m["folder"], m["name"])),
        "sources": sorted(sources, key=lambda x: x["table"]),
        "macros": sorted(macros, key=lambda m: (-m["used_by"], m["name"])),
        "totals": {
            "models": len(real), "snapshots": len(models) - len(real), "source_tables": len(sources), "macros": len(macros),
            "tests": sum(m["tests"] for m in models) + sum(x["tests"] for x in sources),
            "tested_models": sum(1 for m in real if m["tests"]), "documented_models": sum(1 for m in real if m["schema_path"]),
            "hard_coded_models": sum(1 for m in models if m["hard_coded"]),
            "unused_macros": sum(1 for m in macros if not m["used_by"]),
        },
    }


@router.get("/api/code/repos/{repo_id}/catalog")
def repo_catalog(repo_id: str, db: Db = Depends(current_db)):
    """The repository's dbt catalog: models with materialization, columns, tests, documentation and lineage counts,
    source tables and macros with usage, and coverage totals."""
    _repo(db, repo_id)
    chunks = db.query("""SELECT KIND, NAME, PATH, START_LINE, COLUMNS, TESTS, REFS, SOURCES,
                                IFF(KIND IN ('DBT_MODEL', 'DBT_SNAPSHOT'), LEFT(TEXT, 2000), NULL) AS TEXT
                           FROM CODE.CODE_CHUNK
                          WHERE REPO_ID = %s AND KIND IN ('DBT_MODEL', 'DBT_SNAPSHOT', 'DBT_SCHEMA_YML', 'DBT_SOURCE', 'DBT_MACRO')
                          ORDER BY PATH, START_LINE""", (repo_id,))
    seen, unique = set(), []
    for c in chunks:  # a long model spans several windows: keep its first
        k = (c["kind"], c.get("name"), c["path"])
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return build_catalog(unique, _graph(db, repo_id))


@router.get("/api/code/repos/{repo_id}/files")
def repo_files(repo_id: str, db: Db = Depends(current_db)):
    """Indexed files with language, size, chunks and, for files that could not be read, the reason."""
    _repo(db, repo_id)
    files = db.query("""SELECT F.PATH, F.LANG, F.SIZE, F.SKIPPED_REASON, F.INDEXED_AT::VARCHAR AS INDEXED_AT,
                               COUNT(C.CHUNK_ID) AS CHUNKS, ARRAY_UNIQUE_AGG(C.KIND) AS KINDS
                          FROM CODE.CODE_FILE F
                          LEFT JOIN CODE.CODE_CHUNK C ON C.REPO_ID = F.REPO_ID AND C.PATH = F.PATH
                         WHERE F.REPO_ID = %s GROUP BY 1, 2, 3, 4, 5 ORDER BY F.PATH""", (repo_id,))
    for f in files:
        f["kinds"] = _json(f.get("kinds")) or []
    return {"files": files}


@router.get("/api/code/usage")
def code_usage(repo_id: Optional[str] = None, days: int = 30, db: Db = Depends(current_db)):
    """Where AI steps used the code: citations per stage and run, and the most cited chunks."""
    days = max(1, min(days, 365))
    scope = "AND U.REPO_ID = %s" if repo_id else ""
    args: tuple = (days, *((repo_id,) if repo_id else ()))
    recent = db.query(f"""SELECT U.RUN_ID, MAX(R.RUN_NAME) AS RUN_NAME, U.STAGE, COUNT(*) AS CITATIONS,
                                 MAX(U.USED_AT)::VARCHAR AS LAST_USED, ARRAY_UNIQUE_AGG(C.NAME) AS NAMES
                            FROM CODE.CODE_USAGE U
                            LEFT JOIN CODE.CODE_CHUNK C ON C.CHUNK_ID = U.CHUNK_ID
                            LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = U.RUN_ID
                           WHERE U.USED_AT >= DATEADD(DAY, -%s, CURRENT_TIMESTAMP()) {scope}
                           GROUP BY U.RUN_ID, U.STAGE ORDER BY LAST_USED DESC LIMIT 60""", args)
    top = db.query(f"""SELECT C.NAME, C.KIND, C.PATH, C.START_LINE, U.REPO_ID, COUNT(*) AS CITATIONS
                         FROM CODE.CODE_USAGE U JOIN CODE.CODE_CHUNK C ON C.CHUNK_ID = U.CHUNK_ID
                        WHERE U.USED_AT >= DATEADD(DAY, -%s, CURRENT_TIMESTAMP()) {scope}
                        GROUP BY 1, 2, 3, 4, 5 ORDER BY CITATIONS DESC LIMIT 15""", args)
    for r in recent:
        r["names"] = [n for n in (_json(r.get("names")) or []) if n][:8]
    stages: dict = {}
    for r in recent:
        s = stages.setdefault(r["stage"], {"citations": 0, "runs": set()})
        s["citations"] += int(r["citations"])
        if r.get("run_id"):
            s["runs"].add(r["run_id"])
    return {"days": days, "recent": recent, "top": top,
            "stages": {k: {"citations": v["citations"], "runs": len(v["runs"])} for k, v in stages.items()},
            "citations": sum(int(r["citations"]) for r in recent), "runs": len({r["run_id"] for r in recent if r.get("run_id")})}


@router.get("/api/code/neighborhood")
def neighborhood(name: str, repo_id: Optional[str] = None, depth: int = 2, db: Db = Depends(current_db)):
    """A node with what it depends on and what depends on it, `depth` hops each way, as nodes and edges for a
    lineage diagram (upstream on the left, downstream on the right)."""
    g = _graph(db, repo_id)
    depth = max(1, min(depth, 4))
    up, down = g.uses(name, depth), g.impact(name, depth)
    nodes = [{"id": name.split(".")[-1].upper(), "name": name, "level": 0, "path": (g.where.get(name.split(".")[-1].upper()) or {}).get("path")}]
    edges = []
    for side, items in ((-1, up), (1, down)):
        for n in items:
            nid = n["name"].split(".")[-1].upper()
            if any(x["id"] == nid for x in nodes):
                continue
            nodes.append({"id": nid, "name": n["name"], "level": side * n["depth"], "path": n.get("path"), "via": n["via"]})
            parent = n["from"].split(".")[-1].upper()
            edges.append({"from": nid, "to": parent, "via": n["via"]} if side < 0 else {"from": parent, "to": nid, "via": n["via"]})
    return {"name": name, "known": g.knows(name), "nodes": nodes, "edges": edges}


@router.get("/api/code/summary")
def summary(db: Db = Depends(current_db)):
    """Per repository: dbt shape and most-used macros, its architecture from the code graph, and usage by stage in the
    last 30 days."""
    out: dict = {}
    for r in db.query("SELECT REPO_ID, KIND, SUMMARY FROM CODE.REPO_SUMMARY WHERE KIND IN ('DBT', 'ARCHITECTURE')"):
        out.setdefault(r["repo_id"], {})["dbt" if r["kind"] == "DBT" else "architecture"] = _json(r["summary"])
    try:
        for r in db.query("""SELECT REPO_ID, STAGE, COUNT(*) AS N, COUNT(DISTINCT RUN_ID) AS RUNS FROM CODE.CODE_USAGE
                              WHERE USED_AT >= DATEADD(DAY, -30, CURRENT_TIMESTAMP()) GROUP BY 1, 2"""):
            out.setdefault(r["repo_id"], {}).setdefault("usage", {})[r["stage"]] = {"uses": int(r["n"]), "runs": int(r["runs"])}
    except Exception:
        pass
    return {"repos": out}


def run_code_context(db: Db, run_id: str, stage: str, question: Optional[str] = None) -> dict:
    """Code context for an API-side stage (dbt review and enhance): the run's domain, target, sources and columns."""
    try:
        run = db.query("SELECT DOMAIN_ID, TARGET_MODEL FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
        if not run:
            return {"text": "", "citations": []}
        target = str(run[0].get("target_model") or "").split(".")[-1] or None
        sources = [r["object_name"] for r in db.query("""SELECT DISTINCT OBJECT_NAME FROM SOURCE.SOURCE_OBJECT
                                                         WHERE RUN_ID = %s AND SELECTED_FLAG LIMIT 30""", (run_id,))]
        columns = [r["target_column"] for r in db.query("""SELECT L.TARGET_COLUMN FROM CONTRACT.STTM_LINE L
                                                           JOIN CONTRACT.STTM_REGISTRY S ON S.STTM_ID = L.STTM_ID
                                                          WHERE S.RUN_ID = %s QUALIFY DENSE_RANK() OVER (ORDER BY S.STTM_VERSION DESC) = 1
                                                          LIMIT 200""", (run_id,))]
        out = code_ctx.code_context(_rows(db), _search(db), stage=stage, domain_id=run[0].get("domain_id"), target=target,
                                    sources=sources, columns=columns, question=question)
        code_ctx.record_usage(lambda sql, params: db.execute(sql.replace("?", "%s"), tuple(params)), run_id, stage,
                              out["citations"])
        return out
    except Exception:
        return {"text": "", "citations": []}
