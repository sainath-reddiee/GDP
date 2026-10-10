"""Code repositories: connect a Git repository through Snowflake's Git integration, index it (CODE.INDEX_REPO), refresh
it on demand or on a schedule (a Snowflake task), and search, read and trace its code. Credentials never pass through
this API's storage: a token given here goes straight into a Snowflake SECRET.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import DATABASE, WAREHOUSE, Db
from app.main import _json, _snowflake_error, current_db
from services.code import context as code_ctx

router = APIRouter()
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,62}$")
URL = re.compile(r"^https://[A-Za-z0-9.\-]+(:\d+)?/[A-Za-z0-9_.\-/~%]+$")
CRON = re.compile(r"^[\d*/,\- ]+ [A-Za-z_]+(/[A-Za-z_]+)?$")
PROVIDERS = {"github.com": "GITHUB", "gitlab.com": "GITLAB", "bitbucket.org": "BITBUCKET", "dev.azure.com": "AZURE_DEVOPS",
             "visualstudio.com": "AZURE_DEVOPS"}
_refreshing: set[str] = set()


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


def _repo(db: Db, repo_id: str) -> dict:
    found = db.query("SELECT * FROM CODE.REPO WHERE REPO_ID = %s", (repo_id,))
    if not found:
        raise HTTPException(404, "repository not found")
    r = found[0]
    for k in ("domain_ids", "include_globs", "exclude_globs", "stats"):
        r[k] = _json(r.get(k)) or ([] if k != "stats" else {})
    return r


def _task_name(repo_id: str) -> str:
    return f"{DATABASE}.CODE.REFRESH_{repo_id.replace('-', '')[:12].upper()}"


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
        repos = db.query("""WITH LAST AS (
                                SELECT REPO_ID, OBJECT_CONSTRUCT('status', STATUS, 'started_at', STARTED_AT::VARCHAR,
                                                                 'duration_ms', DURATION_MS, 'files_changed', FILES_CHANGED,
                                                                 'error', ERROR) AS LAST_RUN
                                  FROM CODE.INDEX_RUN QUALIFY ROW_NUMBER() OVER (PARTITION BY REPO_ID ORDER BY STARTED_AT DESC) = 1)
                              SELECT R.*, R.LAST_INDEXED_AT::VARCHAR AS LAST_INDEXED, R.CREATED_AT::VARCHAR AS CREATED, L.LAST_RUN
                                FROM CODE.REPO R LEFT JOIN LAST L ON L.REPO_ID = R.REPO_ID ORDER BY R.NAME""")
    except Exception as exc:
        if "does not exist" not in str(exc):
            raise _snowflake_error(exc) from exc
        return {"repos": [], "ready": False}
    for r in repos:
        for k in ("domain_ids", "include_globs", "exclude_globs", "stats", "last_run"):
            r[k] = _json(r.get(k)) or ([] if k in ("domain_ids", "include_globs", "exclude_globs") else {})
        r["refreshing"] = r["repo_id"] in _refreshing
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


def _mask(text: str, token: Optional[str]) -> str:
    return text.replace(token, "***") if token else text


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
    if not re.fullmatch(r"[A-Za-z0-9._/\-]+", branch):
        raise HTTPException(400, "Branch name has unexpected characters")
    if db.query("SELECT 1 FROM CODE.REPO WHERE NAME = %s", (name,)):
        raise HTTPException(409, f"A repository named {name} is already connected")
    secret = body.secret_name
    try:
        if body.token:
            # the token goes straight into a Snowflake secret; it is never stored or logged by the platform
            secret = f"{DATABASE}.CODE.{name}_GIT_TOKEN"
            db.execute(f"CREATE SECRET IF NOT EXISTS {secret} TYPE = PASSWORD USERNAME = %s PASSWORD = %s "
                       "COMMENT = 'Git credentials for code context'",
                       ((body.username or "token").strip(), body.token.strip()))
        if body.existing_git_repository:
            git_repo = safe_fqn(body.existing_git_repository)
        else:
            git_repo = f"{DATABASE}.CODE.{name}"
            creds = f" GIT_CREDENTIALS = {safe_fqn(secret)}" if secret else ""
            db.execute(f"CREATE GIT REPOSITORY IF NOT EXISTS {git_repo} API_INTEGRATION = {quote_exact(body.api_integration)} "
                       f"ORIGIN = '{url}'{creds} COMMENT = 'Code context repository'")
        db.execute(f"ALTER GIT REPOSITORY {git_repo} FETCH")
    except HTTPException:
        raise
    except Exception as exc:
        text = _mask(str(exc), body.token)
        if "Insufficient privileges" in text and "Integration" in text:
            from services.dbt.workspace import grant_sql

            role = (db.query("SELECT CURRENT_ROLE() AS R") or [{}])[0].get("r") or "<role>"
            raise HTTPException(403, f"This role can see {body.api_integration} but cannot use it. An admin can run: "
                                     f"{grant_sql(body.api_integration, role)}") from None
        raise _snowflake_error(Exception(text)) from None
    repo_id = str(uuid.uuid4())
    db.execute("""INSERT INTO CODE.REPO (REPO_ID, NAME, GIT_URL, PROVIDER, BRANCH, GIT_REPOSITORY, API_INTEGRATION, SECRET_NAME,
                         DOMAIN_IDS, INCLUDE_GLOBS, EXCLUDE_GLOBS, KIND)
                  SELECT %s, %s, %s, %s, %s, %s, %s, NULLIF(%s, ''), PARSE_JSON(%s), PARSE_JSON(%s), PARSE_JSON(%s), %s""",
               (repo_id, name, url, provider_of(url), branch, git_repo, body.api_integration, secret or "",
                json.dumps(body.domain_ids), json.dumps(body.include_globs), json.dumps(body.exclude_globs), body.kind))
    if body.index_now:
        _start_refresh(db, repo_id)
    return _repo(db, repo_id)


class RepoUpdate(BaseModel):
    branch: Optional[str] = Field(default=None, max_length=200)
    domain_ids: Optional[list[str]] = None
    include_globs: Optional[list[str]] = None
    exclude_globs: Optional[list[str]] = None
    kind: Optional[Literal["DBT", "SQL", "PYTHON", "MIXED"]] = None
    enabled: Optional[bool] = None


@router.put("/api/code/repos/{repo_id}")
def update_repo(repo_id: str, body: RepoUpdate, db: Db = Depends(current_db)):
    repo = _repo(db, repo_id)
    branch = (body.branch or repo["branch"]).strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9._/\-]+", branch):
        raise HTTPException(400, "Branch name has unexpected characters")
    db.execute("""UPDATE CODE.REPO SET BRANCH = %s, DOMAIN_IDS = PARSE_JSON(%s), INCLUDE_GLOBS = PARSE_JSON(%s),
                         EXCLUDE_GLOBS = PARSE_JSON(%s), KIND = %s, ENABLED = %s WHERE REPO_ID = %s""",
               (branch, json.dumps(body.domain_ids if body.domain_ids is not None else repo["domain_ids"]),
                json.dumps(body.include_globs if body.include_globs is not None else repo["include_globs"]),
                json.dumps(body.exclude_globs if body.exclude_globs is not None else repo["exclude_globs"]),
                body.kind or repo["kind"], repo["enabled"] if body.enabled is None else body.enabled, repo_id))
    if branch != repo["branch"] or body.include_globs is not None or body.exclude_globs is not None:
        # a different file set: start from a clean index
        for table in ("CODE_CHUNK", "CODE_EDGE", "CODE_FILE"):
            db.execute(f"DELETE FROM CODE.{table} WHERE REPO_ID = %s", (repo_id,))
    return _repo(db, repo_id)


@router.delete("/api/code/repos/{repo_id}")
def remove_repo(repo_id: str, db: Db = Depends(current_db)):
    """Disconnect: stop the schedule and drop the index. The Snowflake Git repository object and secret are kept."""
    _repo(db, repo_id)
    try:
        db.execute(f"DROP TASK IF EXISTS {_task_name(repo_id)}")
    except Exception:
        pass
    for table in ("CODE_CHUNK", "CODE_EDGE", "CODE_FILE", "REPO_SUMMARY"):
        db.execute(f"DELETE FROM CODE.{table} WHERE REPO_ID = %s", (repo_id,))
    db.execute("DELETE FROM CODE.REPO WHERE REPO_ID = %s", (repo_id,))
    return {"removed": repo_id}


def _start_refresh(db: Db, repo_id: str) -> None:
    if repo_id in _refreshing:
        return
    _refreshing.add(repo_id)

    def run() -> None:
        try:
            db.call("CALL CODE.INDEX_REPO(%s)", (repo_id,))
        except Exception:
            pass  # the procedure records the failure on the repository and its index run
        finally:
            _refreshing.discard(repo_id)

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
    cron = body.cron.strip()
    if not CRON.match(cron) or len(cron.split()) != 6:
        raise HTTPException(400, "Use a cron expression with a time zone, for example '0 6 * * * UTC'")
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
    exact = db.query("""SELECT CHUNK_ID, REPO_ID, PATH, START_LINE, END_LINE, KIND, NAME, LEFT(TEXT, 600) AS TEXT
                          FROM CODE.CODE_CHUNK
                         WHERE ARRAY_CONTAINS(REPO_ID::VARIANT, PARSE_JSON(%s)) AND (%s IS NULL OR KIND = %s)
                           AND (UPPER(NAME) LIKE UPPER(%s) OR UPPER(PATH) LIKE UPPER(%s))
                         ORDER BY IFF(UPPER(NAME) = UPPER(%s), 0, 1), LENGTH(PATH) LIMIT 15""",
                     (json.dumps(ids), kind, kind, f"%{q}%", f"%{q}%", q))
    hits = {h["chunk_id"]: {**h, "match": "name"} for h in exact}
    try:
        flt = [{"@or": [{"@eq": {"REPO_ID": i}} for i in ids]} if len(ids) > 1 else {"@eq": {"REPO_ID": ids[0]}}]
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


@router.get("/api/code/file")
def file(repo_id: str, path: str, db: Db = Depends(current_db)):
    """A file as it is in the indexed commit, with its chunks (for highlights) and its lineage."""
    from services.code.indexer import branch_segment
    from services.dbt.workspace import read_repo_text, safe_fqn

    repo = _repo(db, repo_id)
    if ".." in path or path.startswith("/"):
        raise HTTPException(400, "invalid path")
    known = db.query("SELECT 1 FROM CODE.CODE_FILE WHERE REPO_ID = %s AND PATH = %s", (repo_id, path))
    if not known:
        raise HTTPException(404, "This file is not in the index (it may be excluded or not a text file)")
    try:
        text = read_repo_text(lambda sql: [list(r.values())[0] for r in db.query(sql)], safe_fqn(repo["git_repository"]),
                              branch_segment(repo["branch"]), path)
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    from services.code.dbt_parse import scrub

    chunks = db.query("""SELECT CHUNK_ID, START_LINE, END_LINE, KIND, NAME, REFS, SOURCES, COLUMNS, TESTS
                           FROM CODE.CODE_CHUNK WHERE REPO_ID = %s AND PATH = %s ORDER BY START_LINE""", (repo_id, path))
    for c in chunks:
        for k in ("refs", "sources", "columns", "tests"):
            c[k] = _json(c.get(k)) or []
    return {"repo": {"repo_id": repo_id, "name": repo["name"], "git_url": repo["git_url"], "branch": repo["branch"],
                     "commit": repo.get("last_commit")}, "path": path, "text": scrub(text), "chunks": chunks}


@router.get("/api/code/lineage")
def lineage(name: str, repo_id: Optional[str] = None, db: Db = Depends(current_db)):
    """What a model, source or macro uses and what uses it (two hops each way)."""
    key = name.strip().upper()
    scope = "AND REPO_ID = %s" if repo_id else ""
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


@router.get("/api/code/summary")
def summary(db: Db = Depends(current_db)):
    """Per repository: dbt shape and most-used macros, plus usage by stage in the last 30 days."""
    out = {r["repo_id"]: {"dbt": _json(r["summary"])} for r in db.query("SELECT REPO_ID, SUMMARY FROM CODE.REPO_SUMMARY WHERE KIND = 'DBT'")}
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
