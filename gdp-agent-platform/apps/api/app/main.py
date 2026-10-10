"""Backend for the onboarding console. Workflow authority stays in Snowflake procedures."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.agent import AGENT_NAME, stream_agent
from app.source_invoke import USE_CALLER, invoke_source
from app.db import (
    AUTH_MODE, DATABASE, WAREHOUSE, Db, SnowflakeSessionError,
    apply_work_role, close_session, dev_db, list_grantable_roles, lookup_session, open_pat_session, transaction,
)

app = FastAPI(title="Agentic pipeline API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000",
                   "http://localhost:3001", "http://127.0.0.1:3001"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_run_lock = threading.Lock()
_run_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}
_RUN_TTL = 3.0


def _cached_run(db: Db, run_id: str, loader):
    """Per caller (user and role see different rows) and per run; expired entries are pruned on write."""
    key = (getattr(db, "user", "") or "", getattr(db, "role", "") or "", run_id)
    now = time.time()
    with _run_lock:
        hit = _run_cache.get(key)
        if hit and now - hit[0] < _RUN_TTL:
            return hit[1]
    state = loader()
    with _run_lock:
        now = time.time()
        for stale in [k for k, (at, _) in _run_cache.items() if now - at >= _RUN_TTL]:
            _run_cache.pop(stale, None)
        _run_cache[key] = (now, state)
    return state


def _drop_run(run_id: str) -> None:
    with _run_lock:
        for key in [k for k in _run_cache if k[2] == run_id]:
            _run_cache.pop(key, None)


@app.middleware("http")
async def governance_guard(request: Request, call_next):
    """RBAC and approvals (app/governance.py). Registered first, so it runs after the cache middleware below."""
    from app.governance import middleware as governance_middleware

    return await governance_middleware(request, call_next)


@app.middleware("http")
async def bust_run_cache(request: Request, call_next):
    response = await call_next(request)
    if request.method in {"POST", "PUT", "PATCH"} and response.status_code < 400:
        parts = request.url.path.split("/")
        if "runs" in parts:
            i = parts.index("runs")
            if i + 1 < len(parts) and parts[i + 1]:
                _drop_run(parts[i + 1])
    return response


_CATALOG_DISPLAY_AT = {"at": 0.0}


def _load_catalog_display(db: Db) -> None:
    """CATALOG_DISPLAY (Admin-editable) applied to the catalog filters; cheap, refreshed once a minute."""
    if time.time() - _CATALOG_DISPLAY_AT["at"] < 60:
        return
    _CATALOG_DISPLAY_AT["at"] = time.time()
    try:
        from services.source.catalog_display import configure

        found = db.query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'CATALOG_DISPLAY' "
                         "AND IS_CURRENT ORDER BY VERSION DESC LIMIT 1")
        if found:
            value = found[0].get("config_value")
            configure(json.loads(value) if isinstance(value, str) else value)
    except Exception:
        pass


def current_db(
    x_aip_session: Optional[str] = Header(default=None),
    x_aip_role: Optional[str] = Header(default=None),
) -> Db:
    try:
        if AUTH_MODE == "dev":
            db = dev_db()
        else:
            db = lookup_session(x_aip_session)
            if db is None:
                raise HTTPException(401, "Sign in required")
        try:
            apply_work_role(db, x_aip_role)
        except SnowflakeSessionError as exc:
            raise HTTPException(403, str(exc)) from exc
        _load_catalog_display(db)
        return db
    except SnowflakeSessionError as exc:
        raise HTTPException(503, str(exc)) from exc


class Login(BaseModel):
    user: str = Field(min_length=1, max_length=256)
    token: str = Field(min_length=1)


class RolePick(BaseModel):
    role: str = Field(min_length=1, max_length=256)


class CreateRun(BaseModel):
    run_name: str = Field(min_length=1, max_length=256)
    target_model: Optional[str] = None
    domain_id: Optional[str] = None
    environment: str = "DEV"
    intent: Optional[dict] = None
    modeling_standard: Optional[Literal["GDP", "GENERIC"]] = None


class Transition(BaseModel):
    to_state: str
    reason: Optional[str] = None


class Review(BaseModel):
    to_state: str
    decision: str
    business_justification: Optional[str] = None
    comments: Optional[str] = None


class AgentMessage(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    run_id: Optional[str] = None


class RegisterSource(BaseModel):
    source_system_name: str = Field(min_length=1, max_length=64)
    source_type: str
    database: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(min_length=1, max_length=255, alias="schema")
    owner: Optional[str] = None
    security_classification: Optional[str] = None
    landing_database: Optional[str] = Field(default=None, max_length=255)
    landing_schema: Optional[str] = Field(default=None, max_length=255)
    storage_type: Optional[str] = Field(default=None, pattern=r"^(IN_PLACE|MANAGED|ICEBERG)$")


class AccessRequest(BaseModel):
    selected: list[str] = Field(min_length=1, max_length=500)


class MappingDecisions(BaseModel):
    decisions: list[dict]


class ClientExpectations(BaseModel):
    rows: Optional[list[dict]] = None
    brief: Optional[str] = None
    text: Optional[str] = None
    filename: Optional[str] = None


class SodaDecisions(BaseModel):
    decisions: list[dict]


class DbtPlan(BaseModel):
    # the repository comes from Admin, Integrations (CODE.REPO); origin, clone and integration are resolved server-side
    code_repo_id: Optional[str] = Field(default=None, max_length=36)
    base_branch: Optional[str] = Field(default=None, max_length=200)
    cut_branch: Optional[str] = Field(default=None, max_length=200)
    repo: Optional[str] = None
    origin: Optional[str] = None
    git_repository: Optional[str] = None
    api_integration: Optional[str] = None
    dbt_project: Optional[str] = None
    allowed_prefixes: Optional[list[str]] = None
    push: bool = False
    fetch_skeleton: bool = True
    prefix: Optional[str] = Field(default=None, max_length=16, pattern=r"^[A-Za-z0-9]*$")
    source_key: Optional[str] = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9_]*$")
    domain_folder: Optional[str] = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9_]*$")


class DbtEnhance(BaseModel):
    file_path: str = Field(min_length=1, max_length=400)
    prompt: Optional[str] = Field(default=None, max_length=2000)
    model: Optional[str] = Field(default=None, max_length=120)
    content: Optional[str] = None
    apply: bool = False


class TransformPrompt(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    sttm_line_id: Optional[str] = None
    target_column: Optional[str] = None
    source_column: Optional[str] = None
    source_table: Optional[str] = None
    source_datatype: Optional[str] = None
    target_datatype: Optional[str] = None
    current_transformation: Optional[str] = None
    business_definition: Optional[str] = None


class TransformApply(BaseModel):
    sttm_line_id: str
    transformation: str = Field(min_length=1, max_length=4000)
    prompt: Optional[str] = None
    rationale: Optional[str] = None
    dbt_notes: Optional[str] = None
    soda_checks: Optional[list[dict]] = None


class KnowledgeQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    domain: Optional[str] = None
    knowledge_type: Optional[str] = None
    limit: int = 8


# States entered only by the stage procedures (they record the artifacts that justify the move).
# The generic transition endpoint may still reach them when retrying from FAILED.
PROCEDURE_OWNED_STATES = {
    "SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED",
    "LANDING_PENDING", "LANDING_RUNNING", "LANDING_COMPLETE",
    "PROFILING_PENDING", "PROFILING_RUNNING", "PROFILING_COMPLETE",
    "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "MAPPING_REVIEW",
    "STTM_PENDING", "STTM_REVIEW", "SODA_PENDING", "SODA_REVIEW",
    "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING",
    "VALIDATION_PASSED", "VALIDATION_FAILED", "DBT_REVIEW", "COMPLETED",
}

PROC_ERROR = re.compile(r"(?:ValueError|AssertionError): (.+?)(?:\n|$)")


def _snowflake_error(exc: Exception) -> HTTPException:
    message = str(exc)
    found = PROC_ERROR.findall(message)
    if found:
        message = found[-1].strip()
    lower = message.lower()
    if "too many arguments" in lower or "expected 1, got 2" in lower:
        message = (
            "Snowflake still has the one-argument GENERATE_DBT. "
            "The API now saves the branch plan and retries that signature."
        )
    elif "unexpected 'null'" in lower or 'unexpected "null"' in lower:
        message = "Snowflake rejected a null argument. Choose a git repository or origin URL and generate again."
    status = 400
    if any(k in message for k in ("TRANSITION_REJECTED", "CONCURRENT_UPDATE", "SOURCE_NAME_CONFLICT",
                                  "archived or deleted")):
        status = 409
    elif "LANDING_TARGET_INVALID" in message:
        status = 422
    elif "BUSINESS_JUSTIFICATION is required" in message or "SOURCE_NOT_ACCESSIBLE" in message:
        status = 422
        if "SOURCE_NOT_ACCESSIBLE" in message:
            message = (
                f"{message} Catalog browse uses your sidebar Snowflake role; register/landing must "
                "use the same role (EXECUTE AS CALLER). Switch the role in the sidebar and retry."
            )
    return HTTPException(status, message)


@app.get("/api/health")
def health():
    return {"status": "ok", "database": DATABASE, "auth_mode": AUTH_MODE}


@app.post("/api/auth/login")
def login(body: Login):
    if AUTH_MODE != "pat":
        raise HTTPException(400, "Login is only used when AIP_AUTH=pat")
    try:
        session_id, db = open_pat_session(body.user, body.token)
    except Exception as exc:
        raise HTTPException(403, f"Snowflake rejected the token: {exc}") from exc
    return {"session_id": session_id, "user": db.user}


@app.post("/api/auth/logout")
def logout(x_aip_session: Optional[str] = Header(default=None)):
    close_session(x_aip_session)
    return {"ok": True}


@app.get("/api/auth/me")
def me(db: Db = Depends(current_db)):
    from app.governance import summary

    try:
        access = summary(db)
    except Exception:
        access = {"governance": False, "roles": [], "privileges": ["*"], "pending_for_me": 0}
    return {"user": db.user, "role": db.role, "auth_mode": AUTH_MODE, "agent": AGENT_NAME, **access}


@app.get("/api/auth/roles")
def auth_roles(db: Db = Depends(current_db)):
    roles = list_grantable_roles(db)
    return {"current": db.role, "roles": roles}


@app.put("/api/auth/role")
def auth_set_role(body: RolePick, db: Db = Depends(current_db)):
    try:
        apply_work_role(db, body.role.strip())
    except SnowflakeSessionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"role": db.role}


def _intent_table_counts(db: Db) -> dict[str, int]:
    try:
        found = db.query(
            """
            SELECT CONFIG_KEY, ARRAY_SIZE(CONFIG_VALUE:source:tables) AS N
              FROM CORE.PLATFORM_CONFIG
             WHERE IS_CURRENT AND CONFIG_KEY LIKE 'onboarding.intent.%'
            """
        )
    except Exception:
        return {}
    return {r["config_key"].rsplit(".", 1)[-1]: int(r["n"] or 0) for r in found}


@app.get("/api/runs")
def list_runs(include_test: bool = False, status: str = "all", limit: int = 200, offset: int = 0,
              q: Optional[str] = None, domain_id: Optional[str] = None, stage: Optional[str] = None,
              needs_review: bool = False, sort: str = "newest", tag: Optional[str] = None,
              db: Db = Depends(current_db)):
    """Runs with search, domain/stage/needs-review/tag filters, sorting and paging (`total` counts all matches)."""
    from services.common.tags import normalize_tag
    from services.workflow.listing import MAX_RUNS, SORTS, page, runs_where
    from services.workflow.state_machine import lifecycle_filter_sql, lifecycle_status

    try:
        predicate = lifecycle_filter_sql(status)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    offset, limit = page(offset, limit, max(MAX_RUNS, 500))
    where, params = runs_where(predicate, include_test, q, domain_id, stage, needs_review,
                               normalize_tag(tag) if tag else None)
    order = SORTS.get(sort, SORTS["newest"])
    total = None
    try:
        rows = db.query(
            f"""
            WITH SEL AS (SELECT RUN_ID, COUNT(*) AS N FROM SOURCE.SOURCE_OBJECT WHERE SELECTED_FLAG GROUP BY RUN_ID),
                 LAND AS (SELECT RUN_ID, COUNT(DISTINCT SOURCE_TABLE) AS N FROM SOURCE.LANDING_TABLE_REGISTRY
                           WHERE INGESTION_STATUS = 'COMPLETE' GROUP BY RUN_ID)
            SELECT R.RUN_ID, R.RUN_NAME, R.CURRENT_STATE, R.CURRENT_STAGE, R.STATUS, R.TARGET_MODEL, R.DOMAIN_ID,
                   R.CREATED_BY, R.CREATED_AT::VARCHAR AS CREATED_AT, R.UPDATED_AT::VARCHAR AS UPDATED_AT,
                   COALESCE(R.IS_ARCHIVED, FALSE) AS IS_ARCHIVED, R.SOURCE_DATABASE, R.SOURCE_SCHEMA,
                   S.SOURCE_SYSTEM_NAME, D.DOMAIN_NAME,
                   DATEDIFF('minute', R.CREATED_AT, CURRENT_TIMESTAMP()) AS AGE_MINUTES,
                   COALESCE(SEL.N, 0) AS SELECTED_TABLES, COALESCE(LAND.N, 0) AS LANDED_TABLES,
                   COUNT(*) OVER () AS TOTAL_MATCHES
              FROM CORE.WORKFLOW_RUN R
              LEFT JOIN SOURCE.SOURCE_REGISTRY S ON S.SOURCE_SYSTEM_ID = R.SOURCE_SYSTEM_ID
              LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
              LEFT JOIN SEL ON SEL.RUN_ID = R.RUN_ID
              LEFT JOIN LAND ON LAND.RUN_ID = R.RUN_ID
             WHERE {where}
             ORDER BY {order}
             LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        total = int(rows[0]["total_matches"]) if rows else 0
    except Exception as exc:
        # Before V006 is applied there are no lifecycle columns: show the plain list instead of failing.
        if "invalid identifier" not in str(exc).lower():
            raise _snowflake_error(exc) from exc
        if status.lower() == "archived":
            return {"runs": [], "status": "archived"}
        rows = db.query(
            """
            SELECT RUN_ID, RUN_NAME, CURRENT_STATE, CURRENT_STAGE, STATUS, TARGET_MODEL,
                   CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT, FALSE AS IS_ARCHIVED
              FROM CORE.WORKFLOW_RUN
             WHERE ENVIRONMENT <> 'TEST' OR %s
             ORDER BY CREATED_AT DESC
             LIMIT %s
            """,
            (include_test, limit),
        )
    intent_counts = _intent_table_counts(db)
    tags = _tags_for(db, "RUN", [r["run_id"] for r in rows])
    for r in rows:
        r["tags"] = tags.get(r["run_id"], [])
        r.pop("total_matches", None)
        r["lifecycle"] = lifecycle_status(r["current_state"], bool(r.get("is_archived")))
        r["table_count"] = (r.get("selected_tables") or r.get("landed_tables")
                            or intent_counts.get(r["run_id"]) or 0)
    return {"runs": rows, "status": status.lower(), "total": total if total is not None else len(rows),
            "offset": offset, "limit": limit}


class RunIds(BaseModel):
    run_ids: list[str] = Field(min_length=1, max_length=200)


class BatchArchive(RunIds):
    archived: bool = True


class BatchCleanup(RunIds):
    drop_landing_tables: bool = False
    delete_workspaces: bool = False
    delete_runs: bool = True


def _set_archived(db: Db, run_ids: list[str], archived: bool) -> dict:
    try:
        result = db.call("CALL CORE.SET_RUN_ARCHIVED(%s, %s)", (json.dumps(run_ids), archived))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    for run_id in run_ids:
        _drop_run(run_id)
    return result


@app.post("/api/runs/batch-archive")
def batch_archive(body: BatchArchive, db: Db = Depends(current_db)):
    return _set_archived(db, body.run_ids, body.archived)


@app.post("/api/runs/batch-cleanup")
def batch_cleanup(body: BatchCleanup, db: Db = Depends(current_db)):
    """Soft-delete runs (audit stays) and purge their sandbox. Staged table profiles are never removed."""
    from services.workflow.cleanup import cleanup_pipeline_runs

    try:
        result = _source_call(
            db,
            "CALL CORE.SP_CLEANUP_PIPELINE_RUNS(PARSE_JSON(%s)::ARRAY, %s, %s, %s)",
            cleanup_pipeline_runs,
            json.dumps(body.run_ids),
            body.drop_landing_tables,
            body.delete_workspaces,
            body.delete_runs,
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    for run_id in body.run_ids:
        _drop_run(run_id)
    return result


@app.post("/api/runs/{run_id}/archive")
def archive_run(run_id: str, db: Db = Depends(current_db)):
    return _set_archived(db, [run_id], True)


@app.post("/api/runs/{run_id}/restore")
def restore_run(run_id: str, db: Db = Depends(current_db)):
    return _set_archived(db, [run_id], False)


_POST_STTM = {
    "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
    "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING",
    "VALIDATION_PASSED", "VALIDATION_FAILED", "DBT_REVIEW", "DBT_APPROVED",
}


def _unlock_parallel_tracks(state: dict) -> dict:
    """Soda and dbt both open after STTM. Overlay in case the deployed rail is still linear."""
    current = str(state.get("current_state") or "").upper()
    if current not in _POST_STTM:
        return state
    for stage in state.get("stages") or []:
        name = stage.get("stage")
        if name in ("SODA", "DBT") and stage.get("status") == "LOCKED":
            stage["status"] = "ACTIVE"
        if name == "VALIDATION" and stage.get("status") == "LOCKED" and current not in {
            "STTM_APPROVED", "SODA_PENDING",
        }:
            stage["status"] = "ACTIVE"
    return state


def _qa_signoff(db: Db, run_id: str) -> Optional[dict]:
    """The latest sign-off for the run's current STTM version (an older version's sign-off does not count).
    The current version is the newest one in review or approved; a newer draft or rejected version does not count."""
    try:
        found = db.query(
            """SELECT Q.DECISION, Q.NOTE, Q.DECIDED_BY, Q.DECIDED_AT::VARCHAR AS DECIDED_AT, Q.STTM_ID
                 FROM CONTRACT.QA_SIGNOFF Q
                WHERE Q.RUN_ID = %s
                  AND Q.STTM_ID IS NOT DISTINCT FROM (SELECT STTM_ID FROM CONTRACT.STTM_REGISTRY WHERE RUN_ID = %s
                                                       AND STATUS IN ('APPROVED', 'REVIEW')
                                                     ORDER BY STTM_VERSION DESC LIMIT 1)
                ORDER BY Q.DECIDED_AT DESC LIMIT 1""", (run_id, run_id))
    except Exception:
        return None
    return found[0] if found else None


def _run_lanes(db: Db, run_id: str, current_state: Optional[str]) -> Optional[dict]:
    """Data Quality, QA and Validation lane status from the work done (see services.workflow.lanes)."""
    from services.workflow.lanes import POST_STTM, lanes

    if str(current_state or "").upper() not in POST_STTM:
        return None
    try:
        counts = {r["status"]: int(r["n"]) for r in db.query(
            """SELECT STATUS, COUNT(*) AS N FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                WHERE RUN_ID = %s AND IS_CURRENT GROUP BY STATUS""", (run_id,))}
    except Exception:
        counts = {}
    return lanes(current_state or "", counts, _qa_signoff(db, run_id), 0)


def _domain_id_for_target(db: Db, target_model: Optional[str], domain_id: Optional[str]) -> Optional[str]:
    if domain_id:
        return domain_id
    if not target_model:
        return None
    parts = target_model.split(".")
    if len(parts) == 3:
        found = db.query(
            """
            SELECT DOMAIN_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
             WHERE ACTIVE_FLAG AND UPPER(TARGET_DATABASE) = UPPER(%s) AND UPPER(TARGET_SCHEMA) = UPPER(%s)
               AND UPPER(TARGET_TABLE) = UPPER(%s)
             ORDER BY CREATED_AT LIMIT 1
            """,
            tuple(parts),
        )
        return found[0]["domain_id"] if found else None
    # A bare table name is ambiguous across domains (ADDRESS exists in several); only accept a unique match.
    found = db.query(
        "SELECT DISTINCT DOMAIN_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE ACTIVE_FLAG AND UPPER(TARGET_TABLE) = UPPER(%s)",
        (parts[-1],),
    )
    return found[0]["domain_id"] if len(found) == 1 else None


def _save_intent(db: Db, run_id: str, intent: dict) -> None:
    from services.source.intent import intent_key
    key = intent_key(run_id)
    payload = {**intent, "run_id": run_id}
    with transaction(db):  # supersede and insert together: never zero (or two) current intents
        db.execute(
            "UPDATE CORE.PLATFORM_CONFIG SET IS_CURRENT = FALSE WHERE CONFIG_KEY = %s AND IS_CURRENT",
            (key,),
        )
        version_rows = db.query(
            "SELECT COALESCE(MAX(VERSION), 0) + 1 AS V FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s",
            (key,),
        )
        version = version_rows[0]["v"] if version_rows else 1
        db.execute(
            """
            INSERT INTO CORE.PLATFORM_CONFIG
              (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION, VERSION, IS_CURRENT, CREATED_BY)
            SELECT %s, PARSE_JSON(%s), %s, %s, TRUE, CURRENT_USER()
            """,
            (key, json.dumps(payload), "Onboarding source/target intent", version),
        )


def _load_intent(db: Db, run_id: str) -> Optional[dict]:
    from services.source.intent import intent_key
    rows = db.query(
        "SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s AND IS_CURRENT "
        "ORDER BY VERSION DESC LIMIT 1",
        (intent_key(run_id),),
    )
    if not rows:
        return None
    value = rows[0].get("config_value")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


@app.post("/api/runs")
def create_run(body: CreateRun, db: Db = Depends(current_db)):
    origin = ((body.intent or {}).get("source") or {}).get("origin") or "snowflake"
    if origin != "snowflake":
        raise HTTPException(400, "External sources are not supported yet; land them into Snowflake first")
    payload = {
        "RUN_NAME": body.run_name,
        "TARGET_MODEL": body.target_model,
        "DOMAIN_ID": _domain_id_for_target(db, body.target_model, body.domain_id),
        "ENVIRONMENT": body.environment,
    }
    if body.modeling_standard:
        payload["MODELING_STANDARD"] = body.modeling_standard
    try:
        created = db.call("CALL CORE.CREATE_RUN(%s)", (json.dumps(payload),))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    run_id = (created or {}).get("run_id") or (created or {}).get("RUN_ID")
    if body.intent and run_id:
        try:
            _save_intent(db, run_id, body.intent)
        except Exception:
            pass
        created = _register_from_intent(db, run_id, body.intent, created)
    return created


def _register_from_intent(db: Db, run_id: str, intent: dict, created: dict) -> dict:
    """The wizard already chose the connection and target, so register now instead of asking again on the
    Source stage. A failure (grants, name conflict) leaves the run in CREATED with manual registration."""
    from services.source.procedures import register_source as register_source_handler

    source = intent.get("source") or {}
    target = intent.get("target") or {}
    if not (source.get("database") and source.get("schema") and source.get("source_system_name")):
        return created
    payload = {
        "SOURCE_SYSTEM_NAME": source.get("source_system_name"),
        "SOURCE_TYPE": source.get("source_type") or "SNOWFLAKE_DATABASE",
        "DATABASE": source.get("database"),
        "SCHEMA": source.get("schema"),
        "DOMAIN_ID": intent.get("domain_id"),
        "LANDING_DATABASE": target.get("landing_database"),
        "LANDING_SCHEMA": target.get("landing_schema"),
        "STORAGE_TYPE": target.get("storage_type"),
    }
    try:
        registered = _source_call(db, "CALL SOURCE.REGISTER_SOURCE(%s, %s)", register_source_handler, run_id,
                                  json.dumps({k: v for k, v in payload.items() if v}))
    except Exception as exc:
        return {**created, "registration_error": _snowflake_error(exc).detail}
    _drop_run(run_id)
    return {**created, **(registered.get("state") or {}), "source_system_id": registered.get("source_system_id")}


_GRAPH_TTL = 300.0
_graph_cache: dict = {"at": 0.0, "graph": None}


def _upper_keys(rows: list[dict]) -> list[dict]:
    return [{k.upper(): v for k, v in r.items()} for r in rows]


def _workflow_graph(db: Db):
    """The workflow graph changes only on deploy; read it once every few minutes instead of per request."""
    from services.workflow.graph import graph_from_rows

    now = time.time()
    if _graph_cache["graph"] is not None and now - _graph_cache["at"] < _GRAPH_TTL:
        return _graph_cache["graph"]
    states = _upper_keys(db.query("SELECT STATE, STAGE, KIND, ORDINAL, PHASE, ENABLED, RETRY_TO, GRAPH_VERSION "
                                  "FROM CORE.WORKFLOW_STATE"))
    versions = {r["GRAPH_VERSION"] for r in states}
    assert states and len(versions) == 1, "CORE.WORKFLOW_STATE must hold exactly one graph version"
    transitions = _upper_keys(db.query("SELECT FROM_STATE, TO_STATE, ACTOR, GUARD, ENABLED FROM CORE.WORKFLOW_TRANSITION"))
    graph = graph_from_rows(versions.pop(), states, transitions)
    _graph_cache.update(at=time.time(), graph=graph)
    return graph


def _workflow_state(db: Db, run_id: str) -> dict:
    """Same payload as CORE.GET_WORKFLOW_STATE, read with plain queries: the stored procedure pays a Python
    sandbox start on every call (several seconds) for what is a read-only lookup."""
    from services.workflow.procedures import _state_payload

    try:
        graph = _workflow_graph(db)
        rows = _upper_keys(db.query("SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,)))
    except AssertionError:
        return db.call("CALL CORE.GET_WORKFLOW_STATE(%s)", (run_id,))
    if not rows:
        raise HTTPException(404, f"run {run_id} not found")
    return _state_payload(graph, rows[0])


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, db: Db = Depends(current_db)):
    def load():
        try:
            state = _workflow_state(db, run_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise _snowflake_error(exc) from exc
        base = """
            SELECT R.RUN_NAME, R.TARGET_MODEL, R.SOURCE_SYSTEM_ID, R.SOURCE_DATABASE, R.SOURCE_SCHEMA,
                   R.ENVIRONMENT, R.CREATED_BY, R.CREATED_AT::VARCHAR AS CREATED_AT,
                   R.DOMAIN_ID, D.DOMAIN_NAME{extra}
              FROM CORE.WORKFLOW_RUN R
              LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
             WHERE R.RUN_ID = %s
            """
        try:
            state["run"] = db.query(base.format(extra=", R.LANDING_DATABASE, R.LANDING_SCHEMA, R.STORAGE_TYPE, "
                                                      "R.ARCHIVED_AT::VARCHAR AS ARCHIVED_AT, R.ARCHIVED_BY"),
                                    (run_id,))[0]
        except Exception:
            state["run"] = db.query(base.format(extra=""), (run_id,))[0]
        state = _unlock_parallel_tracks(state)
        lanes = _run_lanes(db, run_id, state.get("current_state"))
        if lanes:
            from services.workflow.lanes import apply

            state["stages"] = apply(state.get("stages") or [], state.get("current_state"), lanes)
            state["lanes"] = lanes
        return state

    return _cached_run(db, run_id, load)


@app.get("/api/runs/{run_id}/intent")
def get_run_intent(run_id: str, db: Db = Depends(current_db)):
    return {"intent": _load_intent(db, run_id)}


@app.get("/api/runs/{run_id}/model-graph")
def get_model_graph(run_id: str, db: Db = Depends(current_db)):
    from services.source.intent import suggest_models

    intent = _load_intent(db, run_id) or {}
    run_rows = db.query(
        "SELECT TARGET_MODEL, DOMAIN_ID, SOURCE_DATABASE, SOURCE_SCHEMA FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s",
        (run_id,),
    )
    run = run_rows[0] if run_rows else {}
    sources = db.query(
        """
        SELECT OBJECT_NAME, OBJECT_TYPE, ROW_COUNT_ESTIMATE, SELECTED_FLAG
          FROM SOURCE.SOURCE_OBJECT WHERE RUN_ID = %s
         ORDER BY OBJECT_NAME
        """,
        (run_id,),
    )
    if not sources:
        planned = (intent.get("source") or {}).get("tables") or []
        sources = [{"object_name": name, "object_type": "TABLE", "row_count_estimate": None, "selected_flag": True}
                   for name in planned]
    else:
        sources = [s for s in sources if s.get("selected_flag")] or sources
    profile = db.query(
        """
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, SEMANTIC_TYPE, POTENTIAL_KEY_FLAG
          FROM PROFILE.PROFILE_REGISTRY
         WHERE RUN_ID = %s AND IS_CURRENT
         ORDER BY TABLE_NAME, COLUMN_NAME
        """,
        (run_id,),
    )
    target_rows = db.query(
        """
        SELECT T.TARGET_TABLE_ID, D.DOMAIN_NAME, T.DOMAIN_ID, T.TARGET_DATABASE, T.TARGET_SCHEMA,
               T.TARGET_TABLE, T.TABLE_TYPE, T.GRAIN
          FROM KNOWLEDGE.TARGET_TABLE_REGISTRY T
          JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = T.DOMAIN_ID
         WHERE T.ACTIVE_FLAG
         ORDER BY T.TARGET_TABLE
        """
    )
    columns_by_table: dict[str, list[str]] = {}
    for col in db.query(
        """
        SELECT T.TARGET_TABLE, C.COLUMN_NAME
          FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY C
          JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = C.TARGET_TABLE_ID
         WHERE T.ACTIVE_FLAG
        """
    ):
        columns_by_table.setdefault(col["target_table"], []).append(col["column_name"])
    selected_fqns = {
        str(t.get("fqn") or t.get("target_table") or "").upper()
        for t in (intent.get("targets") or [])
    }
    if run.get("target_model"):
        selected_fqns.add(str(run["target_model"]).upper())
        selected_fqns.add(str(run["target_model"]).split(".")[-1].upper())
    from services.source.catalog_display import display_domain_name, is_hidden_target

    targets = []
    for row in target_rows:
        fqn = f"{row['target_database']}.{row['target_schema']}.{row['target_table']}"
        chosen = fqn.upper() in selected_fqns or str(row["target_table"]).upper() in selected_fqns
        if is_hidden_target(row) and not chosen:
            continue
        if intent.get("path") == "map_existing" and not chosen:
            if run.get("domain_id") and row.get("domain_id") != run.get("domain_id"):
                continue
        targets.append({
            **row,
            "fqn": fqn,
            "domain_name": display_domain_name(row.get("domain_name")),
            "columns": columns_by_table.get(row["target_table"], []),
            "selected": chosen,
        })
    if intent.get("path") == "map_existing":
        targets = [t for t in targets if t["selected"]] or targets
    try:
        mappings = db.query(
            """
            SELECT TBL.SOURCE_TABLE, TGT.TARGET_TABLE, COUNT(*) AS EDGES
              FROM MAPPING.MAPPING_DECISION D
              JOIN SOURCE.LANDING_COLUMN_REGISTRY L ON L.LANDING_COLUMN_ID = D.SOURCE_COLUMN_ID
              JOIN SOURCE.LANDING_TABLE_REGISTRY TBL ON TBL.LANDING_ID = L.LANDING_ID
              JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY C ON C.TARGET_COLUMN_ID = D.TARGET_COLUMN_ID
              JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY TGT ON TGT.TARGET_TABLE_ID = C.TARGET_TABLE_ID
             WHERE D.RUN_ID = %s AND D.IS_CURRENT AND D.DECISION <> 'REJECTED' AND D.TARGET_COLUMN_ID IS NOT NULL
             GROUP BY TBL.SOURCE_TABLE, TGT.TARGET_TABLE
            """,
            (run_id,),
        )
    except Exception:
        mappings = []
    edges = [{"from": m["source_table"], "to": m["target_table"], "kind": "mapped", "weight": m["edges"]}
             for m in mappings]
    if not edges:
        for src in sources:
            for tgt in [t for t in targets if t.get("selected")]:
                edges.append({
                    "from": src["object_name"], "to": tgt["target_table"],
                    "kind": "planned", "weight": 1,
                })
    suggestions = suggest_models(profile, targets, [s["object_name"] for s in sources])
    from services.source.er_graph import infer_joins, isolated_tables

    profiled_columns: dict[str, list[dict]] = {}
    for col in profile:
        profiled_columns.setdefault(col["table_name"], []).append(col)
    joins = infer_joins(profiled_columns)
    return {
        "intent": intent or None,
        "source": {
            "database": (intent.get("source") or {}).get("database") or run.get("source_database"),
            "schema": (intent.get("source") or {}).get("schema") or run.get("source_schema"),
        },
        "sources": sources,
        "targets": targets,
        "edges": edges,
        "joins": joins,
        "isolated": isolated_tables(profiled_columns, joins),
        "suggestions": suggestions,
        "profiled": bool(profile),
    }


@app.post("/api/runs/{run_id}/transition")
def transition(run_id: str, body: Transition, db: Db = Depends(current_db)):
    to_state = body.to_state.upper()
    if to_state in PROCEDURE_OWNED_STATES:
        current = db.query("SELECT CURRENT_STATE FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
        if not current or current[0]["current_state"] != "FAILED":
            raise HTTPException(409, f"TRANSITION_REJECTED: {to_state} is reached through its stage action, "
                                     "not a manual transition")
    try:
        return db.call(
            "CALL CORE.TRANSITION_RUN(%s, %s, %s, %s)",
            (run_id, body.to_state, body.reason, "{}"),
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc


class ModelDesignIn(BaseModel):
    preset: Optional[Literal["GDP", "COMPANY", "CUSTOM", "NONE"]] = None
    custom: Optional[dict[str, Any]] = None
    instructions: Optional[str] = Field(default=None, max_length=4000)
    base_version: Optional[int] = None
    target_database: Optional[str] = Field(default=None, max_length=255)
    target_schema: Optional[str] = Field(default=None, max_length=255)


class ModelDesignSave(BaseModel):
    design: dict[str, Any]
    conventions: dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = Field(default=None, max_length=2000)


MODEL_APPLY_STATES = {"PROFILING_COMPLETE", "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "MAPPING_REVIEW"}


@app.get("/api/runs/{run_id}/model")
def get_model_design(run_id: str, db: Db = Depends(current_db)):
    """Every version of the run's target model design (the registered 1:1 target becomes version 1)."""
    from services.modeling.design import get_design

    try:
        return invoke_source(db, get_design, run_id)
    except AssertionError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/model/design")
def design_model(run_id: str, body: ModelDesignIn, db: Db = Depends(current_db)):
    """AI design grounded in the run's profiles, the domain's models and knowledge, and the modeling skills."""
    from services.modeling.design import design_model as handler

    try:
        return invoke_source(db, handler, run_id, body.model_dump_json())
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.put("/api/runs/{run_id}/model")
def save_model_design(run_id: str, body: ModelDesignSave, db: Db = Depends(current_db)):
    """The developer's edit, stored as a new version and validated."""
    from services.modeling.design import save_design

    try:
        return invoke_source(db, save_design, run_id, body.model_dump_json())
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/model/validate")
def validate_model_design(run_id: str, body: ModelDesignSave, db: Db = Depends(current_db)):
    from services.modeling.design import validate_design

    try:
        return invoke_source(db, validate_design, run_id, body.model_dump_json())
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/model/{version}/approve")
def approve_model_design(run_id: str, version: int, db: Db = Depends(current_db)):
    """Use this version as the run's target: registered with MODEL_SPEC and kept as MODEL_DEFINITION knowledge.
    While mapping is in review the run goes back to mapping so candidates are regenerated for the new model."""
    from services.modeling.design import apply_design

    current = db.query("SELECT CURRENT_STATE FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not current:
        raise HTTPException(404, "run not found")
    state = current[0]["current_state"]
    if state not in MODEL_APPLY_STATES:
        raise HTTPException(409, f"The run is at {state}. Reopen mapping before changing the target model; "
                                 "you can still edit and save versions.")
    try:
        result = _source_call(db, "CALL MODELING.APPLY_MODEL_DESIGN(%s, %s)", apply_design, run_id, version)
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    sent_back = False
    if state == "MAPPING_REVIEW":
        try:
            db.call("CALL CORE.REVIEW_TRANSITION(%s, %s, %s, %s, %s)",
                    (run_id, "MAPPING_PENDING", "REQUEST_CHANGES", None, f"Target model changed to design v{version}"))
            sent_back = True
        except Exception:
            pass  # the model is applied; the reviewer can send mapping back by hand
    _drop_run(run_id)
    return {**result, "mapping_reset": sent_back}


@app.get("/api/runs/{run_id}/model/diff")
def model_design_diff(run_id: str, base: int, compare: int, db: Db = Depends(current_db)):
    from services.modeling.design import diff

    found = {r["version"]: _json(r["design_json"]) or {} for r in db.query(
        "SELECT VERSION, DESIGN_JSON FROM MODELING.MODEL_DESIGN WHERE RUN_ID = %s AND VERSION IN (%s, %s)",
        (run_id, base, compare))}
    if base not in found or compare not in found:
        raise HTTPException(404, "version not found")
    return {"base": base, "compare": compare, "changes": diff(found[base], found[compare])}


@app.post("/api/runs/{run_id}/review")
def review(run_id: str, body: Review, db: Db = Depends(current_db)):
    if str(body.to_state).upper() == "DBT_APPROVED" and str(body.decision).upper() == "APPROVE":
        current = db.query("SELECT CURRENT_STATE FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
        lanes = _run_lanes(db, run_id, current[0]["current_state"] if current else None)
        if lanes and not lanes["gate"]["ready"]:
            raise HTTPException(409, "Code review needs every parallel lane finished first: "
                                     + "; ".join(lanes["gate"]["waiting_on"]))
    try:
        return db.call(
            "CALL CORE.REVIEW_TRANSITION(%s, %s, %s, %s, %s)",
            (run_id, body.to_state, body.decision, body.business_justification, body.comments),
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc


SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,254}$")


def _ident(value: str, field: str) -> str:
    """User input -> the name as stored in INFORMATION_SCHEMA. Unquoted names resolve upper case; a quoted name
    ("orderItems") keeps its exact spelling. Callers quote it (_quote_ident) wherever it reaches SQL text."""
    from services.source.identifiers import normalize

    value = (value or "").strip()
    try:
        name = normalize(value)
    except AssertionError as exc:
        raise HTTPException(400, f"{field}: {exc}") from exc
    if not SAFE_IDENT.match(value) and not (len(value) >= 2 and value[0] == value[-1] == '"'):
        raise HTTPException(400, f"{field} must be an identifier, or a quoted name for mixed case or symbols")
    return name


def _table_ident(value: str, field: str = "table") -> str:
    """A table picked from the catalog listing: its exact stored spelling ("members", "Order Items"). A quoted
    form is accepted too. Unlike _ident, an unquoted lowercase name is not folded to upper case, because it came
    from INFORMATION_SCHEMA as-is."""
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return _ident(value, field)
    if not value or len(value) > 255 or '"' in value or any(ord(ch) < 32 for ch in value):
        raise HTTPException(400, f"{field} must be a table name from the catalog")
    return value


def _column_type(column: dict) -> str:
    kind = (column.get("data_type") or "").upper()
    if kind == "TEXT" and column.get("character_maximum_length") is not None:
        return f"VARCHAR({int(column['character_maximum_length'])})"
    if kind == "NUMBER" and column.get("numeric_precision") is not None:
        return f"NUMBER({int(column['numeric_precision'])},{int(column.get('numeric_scale') or 0)})"
    return kind


class TargetBind(BaseModel):
    database: str
    schema_name: str = Field(alias="schema")
    table: str
    domain_id: Optional[str] = Field(default=None, max_length=64)
    domain_name: Optional[str] = Field(default=None, max_length=128)


class IntentPatch(BaseModel):
    path: Optional[str] = None
    tables: Optional[list[str]] = None
    targets: Optional[list[dict]] = None
    domain_id: Optional[str] = None
    domain_name: Optional[str] = None
    source_system_name: Optional[str] = None


@app.get("/api/sources/databases")
def source_databases(db: Db = Depends(current_db)):
    return {"databases": db.query(
        f"""
        SELECT DATABASE_NAME, TYPE, COMMENT FROM {DATABASE}.INFORMATION_SCHEMA.DATABASES
         WHERE TYPE IN ('STANDARD', 'IMPORTED DATABASE') AND DATABASE_NAME <> %s
         ORDER BY TYPE, DATABASE_NAME
        """,
        (DATABASE,),
    )}


@app.get("/api/sources")
def source_connections(db: Db = Depends(current_db)):
    """Registered source connections; registering once lets every later run reuse the same connection."""
    return {"sources": db.query(
        """
        SELECT S.SOURCE_SYSTEM_ID, S.SOURCE_SYSTEM_NAME, S.SOURCE_TYPE, S.OWNER, S.SECURITY_CLASSIFICATION,
               S.CONNECTION_TYPE, ARRAY_SIZE(S.CONFIGURATION_JSON:landed_tables) AS LANDED_TABLES,
               S.CONFIGURATION_JSON:last_landed_at::VARCHAR AS LAST_LANDED_AT,
               S.CONFIGURATION_JSON:database::VARCHAR AS DATABASE_NAME,
               S.CONFIGURATION_JSON:schema::VARCHAR AS SCHEMA_NAME,
               S.CONFIGURATION_JSON:url::VARCHAR AS LOCATION,
               IFF(S.CONNECTION_TYPE = 'oracle', OBJECT_CONSTRUCT(
                   'host', S.CONFIGURATION_JSON:host, 'port', S.CONFIGURATION_JSON:port,
                   'service', COALESCE(S.CONFIGURATION_JSON:service_name, S.CONFIGURATION_JSON:sid),
                   'schema_owner', S.CONFIGURATION_JSON:schema_owner, 'runtime', S.CONFIGURATION_JSON:runtime,
                   'protocol', S.CONFIGURATION_JSON:protocol, 'health', S.CONFIGURATION_JSON:health,
                   'schedule', S.CONFIGURATION_JSON:schedule:cron, 'last_job', S.CONFIGURATION_JSON:last_job,
                   'ready', S.CONFIGURATION_JSON:oracle_procedure IS NOT NULL OR S.CONFIGURATION_JSON:runtime = 'api_host'),
                   NULL) AS ORACLE,
               S.CREATED_AT::VARCHAR AS CREATED_AT,
               (SELECT COUNT(*) FROM CORE.WORKFLOW_RUN R WHERE R.SOURCE_SYSTEM_ID = S.SOURCE_SYSTEM_ID) AS RUNS,
               (SELECT MAX(R.CREATED_AT)::VARCHAR FROM CORE.WORKFLOW_RUN R
                 WHERE R.SOURCE_SYSTEM_ID = S.SOURCE_SYSTEM_ID) AS LAST_RUN_AT
          FROM SOURCE.SOURCE_REGISTRY S
         WHERE S.ACTIVE_FLAG
         ORDER BY LAST_RUN_AT DESC NULLS LAST, S.SOURCE_SYSTEM_NAME
        """
    )}


@app.get("/api/sources/{connection_id}/catalog")
def source_catalog(connection_id: str, database: str = "", schema: str = "", db: Db = Depends(current_db)):
    """Inspect a registered connection (objects, row counts, columns) without creating or touching a run."""
    from services.source.procedures import fetch_schema_catalog

    try:
        return _source_call(db, "CALL SOURCE.FETCH_SCHEMA_CATALOG(%s, %s, %s)", fetch_schema_catalog,
                            connection_id, database or None, schema or None)
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/landing/targets")
def landing_targets(db: Db = Depends(current_db)):
    """Where runs can land: schemas of the platform database, and whether Iceberg landing is configured."""
    schemas = db.query(
        f"""
        SELECT SCHEMA_NAME FROM {DATABASE}.INFORMATION_SCHEMA.SCHEMATA
         WHERE SCHEMA_NAME NOT IN ('INFORMATION_SCHEMA', 'CORE', 'SOURCE', 'PROFILE', 'KNOWLEDGE', 'MAPPING',
                                   'CONTRACT', 'CODEGEN', 'AUDIT', 'METADATA', 'MODELING', 'GOVERNANCE', 'CODE',
                                   'JIRA', 'QUALITY')
         ORDER BY SCHEMA_NAME
        """
    )
    volume = db.query(
        "SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'LANDING_EXTERNAL_VOLUME' AND IS_CURRENT"
    )
    return {
        "default": {"landing_database": DATABASE, "landing_schema": "LANDING", "storage_type": "MANAGED"},
        "database": DATABASE,
        "schemas": [s["schema_name"] for s in schemas],
        "iceberg_available": bool(volume),
    }


@app.get("/api/profiles")
def cached_profiles(database: str, schema: str, db: Db = Depends(current_db)):
    """Persistent table profiles for a source schema, for 'Profiled (cached)' badges before any run lands."""
    try:
        found = db.query(
            """
            SELECT SOURCE_NAME, DATABASE_NAME, SCHEMA_NAME, TABLE_NAME, ROW_COUNT, COLUMN_COUNT, IS_APPROXIMATE,
                   PROFILED_IN_RUN, PROFILED_BY, PROFILED_AT::VARCHAR AS PROFILED_AT
              FROM METADATA.TABLE_PROFILES
             WHERE DATABASE_NAME = %s AND SCHEMA_NAME = %s
             ORDER BY TABLE_NAME
            """,
            (_ident(database, "database"), _ident(schema, "schema")),
        )
    except HTTPException:
        raise
    except Exception:
        found = []
    return {"profiles": found}


class LandingTarget(BaseModel):
    landing_database: Optional[str] = Field(default=None, max_length=255)
    landing_schema: str = Field(min_length=1, max_length=255)
    storage_type: str = Field(default="MANAGED", pattern=r"^(IN_PLACE|MANAGED|ICEBERG)$")


@app.put("/api/runs/{run_id}/target")
def set_landing_target(run_id: str, body: LandingTarget, db: Db = Depends(current_db)):
    from services.source.procedures import set_landing_target as set_landing_target_handler

    payload = json.dumps({"landing_database": body.landing_database or DATABASE,
                          "landing_schema": body.landing_schema, "storage_type": body.storage_type})
    try:
        return _source_call(db, "CALL SOURCE.SET_LANDING_TARGET(%s, %s)", set_landing_target_handler,
                            run_id, payload)
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/catalog/schemas")
def catalog_schemas(database: str, db: Db = Depends(current_db)):
    database = _ident(database, "database")
    return {"schemas": db.query(
        f"""
        SELECT SCHEMA_NAME FROM {_quote_ident(database)}.INFORMATION_SCHEMA.SCHEMATA
         WHERE SCHEMA_NAME <> 'INFORMATION_SCHEMA'
         ORDER BY SCHEMA_NAME
        """
    )}


@app.get("/api/catalog/tables")
def catalog_tables(database: str, schema: str, db: Db = Depends(current_db)):
    database = _ident(database, "database")
    schema = _ident(schema, "schema")
    return {"tables": db.query(
        f"""
        SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, COMMENT
          FROM {_quote_ident(database)}.INFORMATION_SCHEMA.TABLES
         WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
         ORDER BY TABLE_NAME
        """,
        (schema,),
    )}


@app.get("/api/catalog/columns")
def catalog_columns(database: str, schema: str, table: str, db: Db = Depends(current_db)):
    database, schema, table = _ident(database, "database"), _ident(schema, "schema"), _table_ident(table)
    return {"columns": db.query(
        f"""
        SELECT COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION
          FROM {_quote_ident(database)}.INFORMATION_SCHEMA.COLUMNS
         WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
         ORDER BY ORDINAL_POSITION
        """,
        (schema, table),
    )}


def _targets_with_columns(db: Db) -> list[dict]:
    rows = db.query(
        """
        SELECT T.TARGET_TABLE_ID, D.DOMAIN_ID, D.DOMAIN_NAME, T.TARGET_DATABASE, T.TARGET_SCHEMA,
               T.TARGET_TABLE, T.TABLE_TYPE, T.GRAIN
          FROM KNOWLEDGE.TARGET_TABLE_REGISTRY T
          JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = T.DOMAIN_ID
         WHERE T.ACTIVE_FLAG
         ORDER BY T.TARGET_TABLE
        """
    )
    cols = db.query(
        """
        SELECT T.TARGET_TABLE, C.COLUMN_NAME
          FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY C
          JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = C.TARGET_TABLE_ID
         WHERE T.ACTIVE_FLAG
        """
    )
    by_table: dict[str, list[str]] = {}
    for col in cols:
        by_table.setdefault(col["target_table"], []).append(col["column_name"])
    from services.source.catalog_display import is_hidden_target

    out = []
    for row in rows:
        if is_hidden_target(row):
            continue
        fqn = f"{row['target_database']}.{row['target_schema']}.{row['target_table']}"
        out.append({**row, "fqn": fqn, "columns": by_table.get(row["target_table"], [])})
    return out


def _catalog_profile(db: Db, database: Optional[str], schema: Optional[str], tables: list[str]) -> list[dict]:
    profile: list[dict] = []
    if not database or not schema:
        return [{"table_name": table, "column_name": ""} for table in tables]
    for table in tables:
        try:
            db_name, sch, tbl = _ident(database, "database"), _ident(schema, "schema"), _table_ident(table)
            cols = db.query(
                f"""
                SELECT COLUMN_NAME FROM {_quote_ident(db_name)}.INFORMATION_SCHEMA.COLUMNS
                 WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                """,
                (sch, tbl),
            )
            if cols:
                for col in cols:
                    profile.append({"table_name": table, "column_name": col["column_name"]})
            else:
                profile.append({"table_name": table, "column_name": ""})
        except Exception:
            profile.append({"table_name": table, "column_name": ""})
    return profile


def _match_knowledge(db: Db, tables: list[str]) -> dict:
    """What the platform already knows about these source tables: glossary synonyms (synonym -> target column),
    earlier runs that mapped them, and approved model designs whose lineage lists them."""
    from services.source.model_match import norm

    names = sorted({str(t).upper() for t in tables})
    synonyms: dict[str, str] = {}
    history: list[dict] = []
    lineage: dict[str, set] = {}
    try:
        for k in db.query("SELECT CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE IS_CURRENT AND STATUS = 'ACTIVE' "
                          "AND KNOWLEDGE_TYPE = 'GLOSSARY'"):
            content = _json(k.get("content_json")) or {}
            target = norm(content.get("target_column"))
            for word in content.get("synonyms") or []:
                if target and norm(word):
                    synonyms.setdefault(norm(word), target)
    except Exception:
        pass
    try:
        history = db.query("""SELECT DISTINCT UPPER(O.OBJECT_NAME) AS SOURCE_TABLE, UPPER(R.TARGET_MODEL) AS TARGET_FQN,
                                     R.RUN_NAME, R.RUN_ID
                                FROM CORE.WORKFLOW_RUN R JOIN SOURCE.SOURCE_OBJECT O ON O.RUN_ID = R.RUN_ID AND O.SELECTED_FLAG
                               WHERE R.TARGET_MODEL IS NOT NULL AND R.DELETED_AT IS NULL
                                 AND R.CURRENT_STATE NOT IN ('DRAFT', 'SOURCE_PENDING', 'SOURCE_REGISTERED', 'CANCELLED')
                                 AND ARRAY_CONTAINS(UPPER(O.OBJECT_NAME)::VARIANT, PARSE_JSON(%s)::ARRAY)""",
                           (json.dumps(names),))
    except Exception:
        history = []
    try:
        for k in db.query("SELECT CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE IS_CURRENT AND STATUS = 'ACTIVE' "
                          "AND KNOWLEDGE_TYPE = 'MODEL_DEFINITION' AND CONTENT_JSON:fqn IS NOT NULL"):
            content = _json(k.get("content_json")) or {}
            sources = {str(s).split(".")[0].upper() for c in content.get("columns") or [] for s in c.get("source_columns") or []}
            if sources & set(names):
                lineage.setdefault(str(content.get("fqn")).upper(), set()).update(sources)
    except Exception:
        pass
    return {"synonyms": synonyms, "history": history, "lineage": lineage}


def _suggest_for_catalog(db: Db, database: Optional[str], schema: Optional[str], tables: list[str],
                         domain: Optional[str] = None) -> dict:
    from services.source.catalog_display import display_domain_name, workspace_targets
    from services.source.intent import suggest_models
    from services.source.model_match import recommend, score_targets

    targets = _targets_with_columns(db)
    scoped = workspace_targets(targets, database, schema)
    profile = _catalog_profile(db, database, schema, tables)
    suggestions = suggest_models(profile, targets, tables)
    # richer evidence from knowledge: glossary synonyms, earlier runs and approved model designs
    source_cols: dict[str, list[str]] = {}
    for row in profile:
        if row.get("column_name"):
            source_cols.setdefault(str(row["table_name"]).upper(), []).append(str(row["column_name"]))
    for t in tables:
        source_cols.setdefault(str(t).upper(), [])
    knowledge = _match_knowledge(db, tables)
    matches = score_targets(source_cols, [{**t, "fqn": t.get("fqn") or ".".join(
        str(t.get(k)) for k in ("target_database", "target_schema", "target_table"))} for t in targets],
        knowledge["synonyms"], knowledge["history"], knowledge["lineage"], domain)
    rich = {str(m["fqn"]).upper(): m for m in matches}
    suggestions = [({**item, **rich[str(item.get("fqn") or "").upper()]} if str(item.get("fqn") or "").upper() in rich else item)
                   for item in suggestions]
    listed = {str(i.get("fqn") or "").upper() for i in suggestions}
    suggestions = [m for m in matches if str(m["fqn"]).upper() not in listed and m["score"] >= 0.3] + suggestions
    scoped_fqns = {str(t.get("fqn") or "").upper() for t in scoped}
    existing = []
    for item in suggestions:
        if item["kind"] != "existing":
            continue
        fqn = str(item.get("fqn") or "").upper()
        if item["score"] >= 0.2 or fqn in scoped_fqns:
            existing.append({**item, "domain_name": display_domain_name(item.get("domain_name"))})
    proposed = [item for item in suggestions if item["kind"] == "proposed"]
    seen = {str(item.get("fqn") or "").upper() for item in existing}
    for row in scoped:
        fqn = str(row.get("fqn") or "")
        if not fqn or fqn.upper() in seen:
            continue
        seen.add(fqn.upper())
        existing.append({
            "kind": "existing",
            "target_table": row.get("target_table"),
            "fqn": fqn,
            "domain_name": display_domain_name(row.get("domain_name")),
            "score": 0.75,
            "overlap_columns": [],
            "reason": "Registered model for this catalog",
        })
    if domain:
        for row in targets:
            fqn = str(row.get("fqn") or "")
            if str(row.get("domain_name") or "").upper() != domain.upper() or not fqn or fqn.upper() in seen:
                continue
            seen.add(fqn.upper())
            existing.append({
                "kind": "existing", "target_table": row.get("target_table"), "fqn": fqn,
                "domain_name": display_domain_name(row.get("domain_name")), "score": 0.45, "overlap_columns": [],
                "reason": f"{display_domain_name(domain)} domain model (detected from the source)",
            })
        shown = str(display_domain_name(domain) or domain).upper()
        existing.sort(key=lambda i: (str(i.get("domain_name") or "").upper() not in {domain.upper(), shown},
                                     -float(i.get("score") or 0)))
    for item in existing:
        hit = rich.get(str(item.get("fqn") or "").upper())
        if hit and hit["score"] > float(item.get("score") or 0):
            item.update({**hit, "domain_name": item.get("domain_name")})
    existing.sort(key=lambda i: -float(i.get("score") or 0))
    related = bool(existing) or bool(scoped)
    proposed_name = proposed[0]["target_table"] if proposed else "DIM_SOURCE"
    bands = _ui_bands(db)
    return {
        "recommendation": recommend([i for i in existing if i.get("coverage_target") is not None] or existing,
                                    proposed_name, float(bands.get("model_match_strong") or 0.6)),
        "proposed": proposed[0] if proposed else None,
        "related": related,
        "suggestions": existing if related else existing + proposed,
        "targets": [{**row, "domain_name": display_domain_name(row.get("domain_name"))} for row in scoped],
        "tables": tables,
    }


@app.put("/api/runs/{run_id}/intent")
def put_run_intent(run_id: str, body: IntentPatch, db: Db = Depends(current_db)):
    if not db.query("SELECT 1 AS X FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,)):
        raise HTTPException(404, "run not found")
    current = _load_intent(db, run_id) or {
        "path": "profile_suggest", "run_name": "", "source": {}, "targets": [],
        "model_existing": False, "created_at": "",
    }
    source = dict(current.get("source") or {})
    if body.tables is not None:
        source["tables"] = body.tables
    if body.source_system_name:
        source["source_system_name"] = body.source_system_name
    current["source"] = source
    if body.path:
        current["path"] = body.path
        current["model_existing"] = body.path == "map_existing"
    if body.targets is not None:
        current["targets"] = body.targets
    if body.domain_id is not None:
        current["domain_id"] = body.domain_id
    if body.domain_name is not None:
        current["domain_name"] = body.domain_name
    _save_intent(db, run_id, current)
    return {"intent": current}


@app.get("/api/catalog/target-suggestions")
def catalog_target_suggestions(database: str = "", schema: str = "", tables: str = "", db: Db = Depends(current_db)):
    table_list = [part.strip() for part in (tables or "").split(",") if part.strip()]
    detected = _infer_domains(db, table_list, [], schema or None)[:1] if table_list else []
    domain = detected[0]["domain_name"] if detected and detected[0]["confidence"] >= _min_confidence(db) else None
    return _suggest_for_catalog(db, database or None, schema or None, table_list, domain)


class PreviewGraph(BaseModel):
    database: str
    schema_name: str = Field(alias="schema")
    tables: list[str] = Field(default_factory=list, max_length=60)
    targets: list[str] = Field(default_factory=list)


@app.post("/api/catalog/preview-graph")
def catalog_preview_graph(body: PreviewGraph, db: Db = Depends(current_db)):
    """Pre-run ER preview: source columns, inferred joins and source->target overlap edges."""
    from services.source.catalog_display import display_domain_name
    from services.source.er_graph import infer_joins, isolated_tables, key_columns, mapping_edges

    database, schema = _ident(body.database, "database"), _ident(body.schema_name, "schema")
    tables = [_table_ident(t) for t in body.tables]
    meta = {r["table_name"]: r for r in db.query(
        f"""
        SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT FROM {_quote_ident(database)}.INFORMATION_SCHEMA.TABLES
         WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
        """,
        (schema,),
    )}
    if not tables:
        tables = sorted(meta)[:60]
    columns: dict[str, list[dict]] = {t: [] for t in tables}
    if tables:
        placeholders = ", ".join(["%s"] * len(tables))
        for col in db.query(
            f"""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, ORDINAL_POSITION
              FROM {_quote_ident(database)}.INFORMATION_SCHEMA.COLUMNS
             WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ({placeholders})
             ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            (schema, *tables),
        ):
            columns.setdefault(col["table_name"], []).append(col)
    wanted = {t.upper() for t in body.targets}
    targets = [
        {**t, "domain_name": display_domain_name(t.get("domain_name")), "selected": True}
        for t in _targets_with_columns(db)
        if str(t.get("fqn") or "").upper() in wanted
    ]
    # unregistered model tables (e.g. DIM_* in the source schema) still get their live columns
    for fqn in sorted(wanted - {str(t["fqn"]).upper() for t in targets}):
        parts = fqn.split(".")
        if len(parts) != 3:
            continue
        try:
            tdb, tsch, ttbl = (_ident(p, "target") for p in parts)
            cols = db.query(
                f"""
                SELECT COLUMN_NAME FROM {_quote_ident(tdb)}.INFORMATION_SCHEMA.COLUMNS
                 WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION
                """,
                (tsch, ttbl),
            )
        except Exception:
            cols = []
        targets.append({
            "target_table": parts[2], "fqn": fqn, "domain_name": None,
            "columns": [c["column_name"] for c in cols], "selected": True,
        })
    joins = infer_joins(columns)
    sources = []
    for name in tables:
        keys = key_columns(name, columns.get(name, []))
        row = meta.get(name) or {}
        sources.append({
            "object_name": name,
            "object_type": row.get("table_type") or "TABLE",
            "row_count_estimate": row.get("row_count"),
            "selected_flag": True,
            "missing": name not in meta,
            "columns": [
                {"name": c["column_name"], "type": c["data_type"], "nullable": c.get("is_nullable") == "YES",
                 "pk": c["column_name"] in keys}
                for c in columns.get(name, [])
            ],
        })
    return {
        "intent": None,
        "source": {"database": database, "schema": schema},
        "sources": sources,
        "targets": targets,
        "edges": mapping_edges(columns, targets),
        "joins": joins,
        "isolated": isolated_tables(columns, joins),
        "suggestions": [],
        "profiled": False,
    }


class CreateDomain(BaseModel):
    domain_name: str = Field(min_length=2, max_length=128)
    description: Optional[str] = Field(default=None, max_length=4000)


def _domain_snapshot(db: Db, domain_id: Optional[str], kind: str, note: Optional[str] = None) -> Optional[int]:
    """Record a domain version after a change (no-op when nothing changed). Never fails the request."""
    if not domain_id:
        return None
    from services.knowledge.domain_versions import snapshot

    try:
        return snapshot(lambda sql, params: db.query(sql, params), db.execute, domain_id, kind, note)
    except Exception:
        return None


@app.post("/api/domains")
def create_domain(body: CreateDomain, db: Db = Depends(current_db)):
    name = body.domain_name.strip()
    existing = db.query(
        "SELECT DOMAIN_ID, DOMAIN_NAME, ACTIVE_FLAG FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = UPPER(%s)",
        (name,),
    )
    if existing:
        found = existing[0]
        if found.get("active_flag") is False:
            raise HTTPException(409, f"A deleted domain is already named {found['domain_name']}. Restore it from "
                                     "Domains (show deleted) instead of creating it again.")
        return {"domain": {"domain_id": found["domain_id"], "domain_name": found["domain_name"]}, "created": False}
    import uuid
    domain_id = str(uuid.uuid4())
    try:
        db.execute(
            """
            INSERT INTO KNOWLEDGE.DOMAIN_REGISTRY (DOMAIN_ID, DOMAIN_NAME, DESCRIPTION, OWNER)
            SELECT %s, %s, %s, CURRENT_USER()
            """,
            (domain_id, name, body.description),
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    if _has_domain_members(db):  # whoever creates a domain owns it
        db.execute("""MERGE INTO KNOWLEDGE.DOMAIN_MEMBER T USING (SELECT %s AS DOMAIN_ID, CURRENT_USER() AS USER_NAME) S
                         ON T.DOMAIN_ID = S.DOMAIN_ID AND T.USER_NAME = S.USER_NAME AND T.ROLE = 'OWNER'
                      WHEN NOT MATCHED THEN INSERT (DOMAIN_ID, USER_NAME, ROLE) VALUES (S.DOMAIN_ID, S.USER_NAME, 'OWNER')""",
                   (domain_id,))
    _domain_snapshot(db, domain_id, "CREATE", "Domain created")
    return {"domain": {"domain_id": domain_id, "domain_name": name}, "created": True}


def _has_domain_members(db: Db) -> bool:
    try:
        db.query("SELECT 1 FROM KNOWLEDGE.DOMAIN_MEMBER LIMIT 1")
        return True
    except Exception:
        return False


@app.get("/api/runs/{run_id}/target-suggestions")
def target_suggestions(run_id: str, tables: str = "", db: Db = Depends(current_db)):
    table_list = [part.strip() for part in (tables or "").split(",") if part.strip()]
    intent = _load_intent(db, run_id) or {}
    source = intent.get("source") or {}
    database, schema = source.get("database"), source.get("schema")
    return _suggest_for_catalog(db, database, schema, table_list)


@app.get("/api/targets")
def list_targets(db: Db = Depends(current_db)):
    from services.source.catalog_display import display_domain_name, is_hidden_target

    rows = db.query(
        """
        SELECT T.TARGET_TABLE_ID, D.DOMAIN_NAME, T.TARGET_DATABASE, T.TARGET_SCHEMA, T.TARGET_TABLE,
               T.TABLE_TYPE, T.GRAIN,
               (SELECT COUNT(*) FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY C
                 WHERE C.TARGET_TABLE_ID = T.TARGET_TABLE_ID) AS COLUMNS
          FROM KNOWLEDGE.TARGET_TABLE_REGISTRY T
          JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = T.DOMAIN_ID
         WHERE T.ACTIVE_FLAG
         ORDER BY T.TARGET_TABLE
        """
    )
    visible = []
    for row in rows:
        if is_hidden_target(row):
            continue
        visible.append({**row, "domain_name": display_domain_name(row.get("domain_name"))})
    return {"targets": visible}


@app.post("/api/targets/register")
def register_target(body: TargetBind, db: Db = Depends(current_db)):
    """Read the table with the caller's privileges, then store the snapshot through the procedure."""
    database, schema, table = _ident(body.database, "database"), _ident(body.schema_name, "schema"), _ident(body.table, "table")
    try:
        raw = db.query(
            f"""
            SELECT COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION, IS_NULLABLE, COMMENT,
                   CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE
              FROM {_quote_ident(database)}.INFORMATION_SCHEMA.COLUMNS
             WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
             ORDER BY ORDINAL_POSITION
            """,
            (schema, table),
        )
    except Exception as exc:
        raise HTTPException(400, f"TARGET_NOT_VISIBLE: {database}.{schema}.{table}: {exc}") from exc
    if not raw:
        raise HTTPException(400, f"TARGET_NOT_VISIBLE: {database}.{schema}.{table}")
    # Business keys come from the table's declared primary key; without one the STTM decides. Guessing from names
    # (every NOT NULL *_ID) marked foreign keys as keys and produced the wrong grain.
    try:
        pk = {r.get("column_name") for r in db.query(
            f"SHOW PRIMARY KEYS IN TABLE {_quote_ident(database)}.{_quote_ident(schema)}.{_quote_ident(table)}")}
    except Exception:
        pk = set()
    payload = {
        "database": database, "schema": schema, "table": table,
        "domain_id": body.domain_id, "domain_name": body.domain_name,
        "columns": [{
            "column_name": c["column_name"],
            "data_type": _column_type(c),
            "ordinal_position": c["ordinal_position"],
            "nullable": c["is_nullable"] == "YES",
            "comment": c["comment"],
            "business_key": c["column_name"] in pk,
        } for c in raw],
    }
    try:
        return db.call("CALL KNOWLEDGE.REGISTER_TARGET_TABLE(%s)", (json.dumps(payload),))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


def _record_cost(db: Db, run_id: str, stage: str, model: Optional[str], usage: Optional[dict], started: float) -> None:
    """AUDIT.COST_USAGE row for an AI call the API makes directly (same rate table as the procedures).
    Never fails the request it describes."""
    try:
        from services.common.cost import estimate, rates_for

        usage = usage or {}
        prompt, completion = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or prompt + completion)
        rates = rates_for(model, _config(db, "RATE_CARD", {}) or {},
                          _config(db, "CALIBRATED_RATES", {}) or {},
                          _config(db, "CREDITS_PER_MILLION_TOKENS", {}) or {})
        db.execute(
            """INSERT INTO AUDIT.COST_USAGE (COST_USAGE_ID, RUN_ID, STAGE, AGENT, MODEL, INPUT_TOKENS, OUTPUT_TOKENS,
                   TOTAL_TOKENS, TOOL_CALL_COUNT, SEARCH_CALL_COUNT, CODE_CALL_COUNT, DURATION_MS, ESTIMATED_COST,
                   QUERY_ID, COST_SOURCE)
               SELECT %s, %s, %s, 'PLATFORM', %s, %s, %s, %s, 1, 0, 0, %s, %s, %s, %s""",
            (str(uuid.uuid4()), run_id, stage, model, prompt, completion, total,
             int((time.time() - started) * 1000), estimate(prompt, completion, rates), usage.get("query_id"),
             "ESTIMATE" if total else "NONE"),
        )
    except Exception:
        pass


def _config(db: Db, key: str, default: Any = None) -> Any:
    """Newest current PLATFORM_CONFIG value (older rows left current can never win)."""
    try:
        found = db.query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s AND IS_CURRENT "
                         "ORDER BY VERSION DESC LIMIT 1", (key,))
    except Exception:
        return default
    if not found:
        return default
    value = _json(found[0].get("config_value"))
    return found[0].get("config_value") if value is None else value


def _model_for(db: Db, stage: Optional[str] = None) -> str:
    """The model an AI step uses: the stage's own (Admin, LLM_MODEL_BY_STAGE) or the platform default."""
    from services.common.llm import DEFAULT_MODEL, resolve_model

    return resolve_model(str(_config(db, "LLM_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL),
                         _config(db, "LLM_MODEL_BY_STAGE", {}) if stage else {}, stage)


def _source_call(db: Db, proc: str, handler, *args):
    if USE_CALLER:
        return invoke_source(db, handler, *args)
    return db.call(proc, args)


@app.post("/api/runs/{run_id}/source")
def register_source(run_id: str, body: RegisterSource, db: Db = Depends(current_db)):
    from services.source.procedures import register_source as register_source_handler

    payload = {
        "SOURCE_SYSTEM_NAME": body.source_system_name,
        "SOURCE_TYPE": body.source_type,
        "DATABASE": body.database,
        "SCHEMA": body.schema_name,
        "OWNER": body.owner,
        "SECURITY_CLASSIFICATION": body.security_classification,
        "LANDING_DATABASE": body.landing_database,
        "LANDING_SCHEMA": body.landing_schema,
        "STORAGE_TYPE": body.storage_type,
    }
    payload_json = json.dumps({k: v for k, v in payload.items() if v})
    try:
        return _source_call(
            db,
            "CALL SOURCE.REGISTER_SOURCE(%s, %s)",
            register_source_handler,
            run_id,
            payload_json,
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/access")
def validate_access(run_id: str, body: AccessRequest, db: Db = Depends(current_db)):
    from services.source.procedures import validate_source_access

    selected_json = json.dumps(body.selected)
    try:
        return _source_call(
            db,
            "CALL SOURCE.VALIDATE_SOURCE_ACCESS(%s, %s)",
            validate_source_access,
            run_id,
            selected_json,
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/landing")
def execute_landing(run_id: str, db: Db = Depends(current_db)):
    from services.source.procedures import execute_landing as execute_landing_handler

    try:
        return _source_call(db, "CALL SOURCE.EXECUTE_LANDING(%s)", execute_landing_handler, run_id)
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/prepare")
def prepare_source(run_id: str, body: AccessRequest, db: Db = Depends(current_db)):
    """One action for the Source stage: validate access to the selection, then land it as-is."""
    from services.source.prepare import ACCESS, LANDING, prepare_plan

    rows = db.query("SELECT CURRENT_STATE, FAILED_FROM_STATE FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not rows:
        raise HTTPException(404, "run not found")
    try:
        steps = prepare_plan(rows[0]["current_state"], rows[0].get("failed_from_state"))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    out: dict = {"passed": True, "checks": [], "tables": [], "steps": steps, "state": None}
    for step in steps:
        if step.startswith("retry:"):
            try:
                db.call("CALL CORE.TRANSITION_RUN(%s, %s, %s, %s)",
                        (run_id, step.split(":", 1)[1], "retry from Validate & land", "{}"))
            except Exception as exc:
                raise _snowflake_error(exc) from exc
        elif step == ACCESS:
            result = validate_access(run_id, body, db) or {}
            out.update(checks=result.get("checks", []), state=result.get("state"))
            if not result.get("passed"):
                out["passed"] = False
                out["failed_step"] = ACCESS
                return out
        elif step == LANDING:
            result = execute_landing(run_id, db) or {}
            out.update(tables=result.get("tables", []), state=result.get("state"))
            state = result.get("state") if isinstance(result.get("state"), dict) else {}
            selected = db.query("SELECT COUNT(*) AS N FROM SOURCE.SOURCE_OBJECT WHERE RUN_ID = %s AND SELECTED_FLAG",
                                (run_id,))
            wanted = int(selected[0]["n"] or 0) if selected else 0
            landed = sum(1 for t in out["tables"] if t.get("status") == "COMPLETE")
            # a landing error midway returns only the tables tried so far, all COMPLETE, with the run FAILED
            if (any(t.get("status") != "COMPLETE" for t in out["tables"]) or state.get("current_state") == "FAILED"
                    or landed < wanted):
                out["passed"] = False
                out["failed_step"] = LANDING
    return out


@app.get("/api/runs/{run_id}/source")
def source_overview(run_id: str, db: Db = Depends(current_db)):
    source = db.query(
        """
        SELECT S.SOURCE_SYSTEM_ID, S.SOURCE_SYSTEM_NAME, S.SOURCE_TYPE, S.OWNER, S.SECURITY_CLASSIFICATION,
               R.SOURCE_DATABASE, R.SOURCE_SCHEMA
          FROM CORE.WORKFLOW_RUN R
          JOIN SOURCE.SOURCE_REGISTRY S ON S.SOURCE_SYSTEM_ID = R.SOURCE_SYSTEM_ID
         WHERE R.RUN_ID = %s
        """,
        (run_id,),
    )
    objects = db.query(
        """
        SELECT OBJECT_NAME, OBJECT_TYPE, ROW_COUNT_ESTIMATE, BYTES, LAST_ALTERED::VARCHAR AS LAST_ALTERED,
               SELECTED_FLAG
          FROM SOURCE.SOURCE_OBJECT WHERE RUN_ID = %s ORDER BY OBJECT_NAME
        """,
        (run_id,),
    )
    checks = db.query(
        """
        SELECT CHECK_NAME, STATUS, DETAIL, REMEDIATION, CHECKED_AT::VARCHAR AS CHECKED_AT
          FROM SOURCE.SOURCE_ACCESS_CHECK WHERE RUN_ID = %s ORDER BY CHECKED_AT DESC, CHECK_NAME
        """,
        (run_id,),
    )
    landing = db.query(
        """
        SELECT T.LANDING_ID, T.SOURCE_TABLE, T.LANDING_DATABASE || '.' || T.LANDING_SCHEMA || '.' || T.LANDING_TABLE
                 AS LANDING_TABLE, T.INGESTION_METHOD, T.INGESTION_STATUS, T.SOURCE_ROW_COUNT, T.ROW_COUNT,
               T.QUERY_ID, T.ERROR_MESSAGE, T.CREATED_AT::VARCHAR AS CREATED_AT,
               (SELECT COUNT(*) FROM SOURCE.LANDING_COLUMN_REGISTRY C WHERE C.LANDING_ID = T.LANDING_ID) AS COLUMNS
          FROM SOURCE.LANDING_TABLE_REGISTRY T WHERE T.RUN_ID = %s ORDER BY T.CREATED_AT DESC, T.SOURCE_TABLE
        """,
        (run_id,),
    )
    return {"source": source[0] if source else None, "objects": objects, "checks": checks, "landing": landing}


@app.get("/api/runs/{run_id}/audit")
def audit(run_id: str, db: Db = Depends(current_db)):
    events = db.query(
        """
        SELECT EVENT_ID, FROM_STATE, TO_STATE, ACTOR_TYPE, ACTOR, REASON,
               CREATED_AT::VARCHAR AS CREATED_AT
          FROM CORE.WORKFLOW_EVENT
         WHERE RUN_ID = %s
         ORDER BY CREATED_AT
        """,
        (run_id,),
    )
    reviews = db.query(
        """
        SELECT REVIEW_ID, STAGE, DECISION, BUSINESS_JUSTIFICATION, REVIEWER,
               REVIEWED_AT::VARCHAR AS REVIEWED_AT
          FROM CORE.REVIEW_DECISION
         WHERE RUN_ID = %s
         ORDER BY REVIEWED_AT
        """,
        (run_id,),
    )
    return {"events": events, "reviews": reviews}


@app.get("/api/audit")
def recent_audit(run_id: Optional[str] = None, actor_type: Optional[str] = None, to_state: Optional[str] = None,
                 since: Optional[str] = None, until: Optional[str] = None, q: Optional[str] = None,
                 offset: int = 0, limit: int = 100, db: Db = Depends(current_db)):
    """Workflow events with filters and paging (`total` counts all matches)."""
    from services.workflow.listing import MAX_AUDIT, audit_where, page

    try:
        where, params = audit_where(run_id, actor_type, to_state, since, until, q)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    offset, limit = page(offset, limit, MAX_AUDIT)
    events = db.query(
        f"""
        SELECT E.EVENT_ID, E.RUN_ID, R.RUN_NAME, E.FROM_STATE, E.TO_STATE, E.ACTOR_TYPE, E.ACTOR,
               E.REASON, E.CREATED_AT::VARCHAR AS CREATED_AT, COUNT(*) OVER () AS TOTAL_MATCHES
          FROM CORE.WORKFLOW_EVENT E
          JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = E.RUN_ID
         WHERE {where}
         ORDER BY E.CREATED_AT DESC
         LIMIT %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    total = int(events[0]["total_matches"]) if events else 0
    for e in events:
        e.pop("total_matches", None)
    return {"events": events, "total": total, "offset": offset, "limit": limit}


@app.get("/api/costs")
def costs(group_by: str = "stage", since: Optional[str] = None, until: Optional[str] = None, limit: int = 50,
          db: Db = Depends(current_db)):
    """AI usage and estimated cost from AUDIT.COST_USAGE, grouped by stage, model, run or day."""
    from services.workflow.listing import cost_query

    try:
        sql, params = cost_query(group_by, since, until, limit)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    _reconcile_if_due(db)
    rows = db.query(sql, tuple(params))
    measures = ("calls", "input_tokens", "output_tokens", "total_tokens", "estimated_cost", "credits",
                "actual_credits", "estimated_credits", "actual_calls")
    # Totals cover every match, not only the page: the same grouped query without ORDER BY / LIMIT, summed.
    every = re.sub(r"\s+ORDER BY\s+[^\n]*\s+LIMIT\s+\d+\s*$", "", sql.rstrip())
    found = db.query(f"SELECT {', '.join(f'COALESCE(SUM({m}), 0) AS {m.upper()}' for m in measures)} FROM ({every})",
                     tuple(params))
    totals = {k: float((found[0] if found else {}).get(k) or 0) for k in measures}
    price = _config(db, "CREDIT_PRICE_USD", None)
    try:
        price = float(price) if price not in (None, "", 0) else None
    except (TypeError, ValueError):
        price = None
    return {"group_by": group_by, "rows": rows, "totals": totals, "credit_price_usd": price,
            "reconcile": _RECONCILE_STATE.get("last"), "calibrated_rates": _config(db, "CALIBRATED_RATES", {}) or {}}


@app.get("/api/metrics/summary")
def metrics_summary(db: Db = Depends(current_db)):
    """Server-side counts for the dashboard and sidebar (correct at any number of runs) plus 30-day AI cost."""
    from services.workflow.listing import summarise
    from services.workflow.state_machine import lifecycle_status

    groups = db.query(
        """SELECT CURRENT_STATE, CURRENT_STAGE, STATUS, COALESCE(IS_ARCHIVED, FALSE) AS IS_ARCHIVED, COUNT(*) AS N
             FROM CORE.WORKFLOW_RUN WHERE DELETED_AT IS NULL AND ENVIRONMENT <> 'TEST'
            GROUP BY 1, 2, 3, 4""")
    out = summarise(groups, lifecycle_status)
    try:
        cost = db.query("""SELECT COUNT(*) AS CALLS, COALESCE(SUM(TOTAL_TOKENS), 0) AS TOKENS,
                                  COALESCE(SUM(COALESCE(ACTUAL_CREDITS, ESTIMATED_COST, 0)), 0) AS COST,
                                  COALESCE(SUM(ACTUAL_CREDITS), 0) AS ACTUAL,
                                  COALESCE(SUM(IFF(ACTUAL_CREDITS IS NULL, ESTIMATED_COST, 0)), 0) AS ESTIMATED
                             FROM AUDIT.COST_USAGE WHERE CREATED_AT >= DATEADD('day', -30, CURRENT_TIMESTAMP())""")
        out["cost_30d"] = {"calls": int(cost[0]["calls"] or 0), "tokens": int(cost[0]["tokens"] or 0),
                           "estimated_cost": float(cost[0]["cost"] or 0), "credits": float(cost[0]["cost"] or 0),
                           "actual_credits": float(cost[0]["actual"] or 0),
                           "estimated_credits": float(cost[0]["estimated"] or 0)}
    except Exception:
        out["cost_30d"] = None
    return out


_DOMAIN_TARGETS_SQL = """
    SELECT T.TARGET_TABLE_ID, T.TARGET_DATABASE, T.TARGET_SCHEMA, T.TARGET_TABLE, T.DESCRIPTION, {spec} AS MODEL_SPEC,
           (SELECT COUNT(*) FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY C WHERE C.TARGET_TABLE_ID = T.TARGET_TABLE_ID) AS COLUMN_COUNT
      FROM KNOWLEDGE.TARGET_TABLE_REGISTRY T
     WHERE T.DOMAIN_ID = %s AND T.ACTIVE_FLAG
     ORDER BY T.TARGET_TABLE
"""


@app.get("/api/domains/{domain_id}")
def domain_detail(domain_id: str, db: Db = Depends(current_db)):
    """Targets (hub and spokes with column counts and model spec), detection signals, source systems, knowledge mix."""
    try:
        found = db.query("SELECT DOMAIN_ID, DOMAIN_NAME, DESCRIPTION, OWNER, CONFIG FROM KNOWLEDGE.DOMAIN_REGISTRY "
                         "WHERE DOMAIN_ID = %s", (domain_id,))
        targets = db.query(_DOMAIN_TARGETS_SQL.format(spec="T.MODEL_SPEC"), (domain_id,))
    except Exception:
        found = db.query("SELECT DOMAIN_ID, DOMAIN_NAME, DESCRIPTION, OWNER, NULL AS CONFIG "
                         "FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,))
        targets = db.query(_DOMAIN_TARGETS_SQL.format(spec="NULL"), (domain_id,))
    if not found:
        raise HTTPException(404, "domain not found")
    domain = found[0]
    config = _json(domain.pop("config", None)) or {}
    for t in targets:
        spec = _json(t.pop("model_spec", None)) or {}
        t["role"] = spec.get("role")
        t["hub_fk"] = spec.get("hub_fk")
        t["hkey_columns"] = spec.get("hkey_columns") or []
        t["minimum_mapping"] = spec.get("minimum_mapping") or []
        t["lookups"] = sorted((spec.get("reference_ctes") or {}).keys())
        t["casts"] = len(spec.get("casts") or [])
    targets.sort(key=lambda t: (t["role"] != "hub", t["target_table"]))
    knowledge = db.query(
        "SELECT KNOWLEDGE_TYPE, COUNT(*) AS N FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE DOMAIN_ID = %s AND IS_CURRENT "
        "GROUP BY KNOWLEDGE_TYPE ORDER BY N DESC",
        (domain_id,),
    )
    from services.knowledge.domain_admin import can_delete, repository_pack_names

    deletable, reason = can_delete(domain_id, domain["domain_name"], config, repository_pack_names())
    return {"domain": domain, "targets": targets, "signals": config.get("signals") or {},
            "source_systems": config.get("source_systems") or [], "contract": config.get("contract"),
            "silver": {"database": config.get("silver_database"), "schema": config.get("silver_schema")},
            "knowledge": knowledge, "origin": config.get("origin") or "repository",
            "deletable": deletable, "not_deletable_reason": reason,
            "active_runs": _domain_active_runs(db, domain_id),
            "deleted": {"at": config.get("deleted_at"), "by": config.get("deleted_by")} if config.get("deleted_at") else None}


@app.get("/api/domains")
def domains(db: Db = Depends(current_db)):
    return {"domains": db.query(
        """
        SELECT D.DOMAIN_ID, D.DOMAIN_NAME, D.DESCRIPTION, D.OWNER, D.ACTIVE_FLAG, D.VERSION,
               D.CONFIG:standard::VARCHAR AS STANDARD,
               (SELECT COUNT(*) FROM KNOWLEDGE.DOMAIN_KNOWLEDGE K
                 WHERE K.DOMAIN_ID = D.DOMAIN_ID AND K.IS_CURRENT) AS KNOWLEDGE_ITEMS,
               (SELECT COUNT(*) FROM KNOWLEDGE.TARGET_TABLE_REGISTRY T
                 WHERE T.DOMAIN_ID = D.DOMAIN_ID AND T.ACTIVE_FLAG) AS TARGET_TABLES
          FROM KNOWLEDGE.DOMAIN_REGISTRY D
         ORDER BY D.DOMAIN_NAME
        """
    )}


@app.post("/api/admin/apply")
def apply_platform(db: Db = Depends(current_db)):
    """Re-apply procedures, skills and the supervisor using the signed-in Snowflake session."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from infrastructure.deploy_snowflake import apply_from_session
    _graph_cache.update(at=0.0, graph=None)
    try:
        return {"ok": True, "log": apply_from_session(db.conn, DATABASE, WAREHOUSE)}
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/admin/workflow")
def workflow_graph(db: Db = Depends(current_db)):
    states = db.query(
        "SELECT STATE, STAGE, KIND, ORDINAL, PHASE, ENABLED, GRAPH_VERSION FROM CORE.WORKFLOW_STATE ORDER BY ORDINAL"
    )
    transitions = db.query(
        "SELECT FROM_STATE, TO_STATE, ACTOR, ENABLED FROM CORE.WORKFLOW_TRANSITION ORDER BY FROM_STATE, TO_STATE"
    )
    return {"states": states, "transitions": transitions}


class ProfileOptions(BaseModel):
    force_refresh: bool = False
    refresh_tables: list[str] = Field(default_factory=list, max_length=500)
    concurrency_limit: int = Field(default=5, ge=1, le=16)


class ProfileRefresh(BaseModel):
    table: str = Field(min_length=1, max_length=255)


@app.post("/api/runs/{run_id}/profile")
def run_profiling(run_id: str, body: Optional[ProfileOptions] = None, db: Db = Depends(current_db)):
    _prewarm_run_profiles(db, run_id, bool(body and body.force_refresh))
    try:
        if body is None:
            return db.call("CALL PROFILE.RUN_PROFILING(%s)", (run_id,))
        return db.call("CALL PROFILE.RUN_PROFILING(%s, %s)", (run_id, body.model_dump_json()))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/profile/refresh")
def refresh_table_profile(run_id: str, body: ProfileRefresh, db: Db = Depends(current_db)):
    """Bust the persistent profile of one table and re-profile it; no workflow transition."""
    try:
        return db.call("CALL PROFILE.REFRESH_TABLE_PROFILE(%s, %s)", (run_id, body.table))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


def _profile_cache_status(db: Db, run_id: str) -> list[dict]:
    """Cache status per table of the run: landed tables first, else the tables planned in the intent."""
    try:
        landed = db.query(
            """
            SELECT L.SOURCE_TABLE AS TABLE_NAME, L.SOURCE_DATABASE AS DATABASE_NAME, L.SOURCE_SCHEMA AS SCHEMA_NAME,
                   P.PROFILED_AT::VARCHAR AS PROFILED_AT, P.ROW_COUNT,
                   P.COLUMN_COUNT, P.IS_APPROXIMATE, P.PROFILE_STAGE_PATH, P.PROFILED_IN_RUN,
                   P.KEY_CANDIDATES, P.PII_COLUMNS, P.AVG_NULL_PERCENTAGE, P.QUALITY_JSON
              FROM SOURCE.LANDING_TABLE_REGISTRY L
              LEFT JOIN SOURCE.SOURCE_REGISTRY S ON S.SOURCE_SYSTEM_ID = L.SOURCE_SYSTEM_ID
              LEFT JOIN METADATA.TABLE_PROFILES P
                     ON P.SOURCE_NAME = S.SOURCE_SYSTEM_NAME AND P.DATABASE_NAME = L.SOURCE_DATABASE
                    AND P.SCHEMA_NAME = L.SOURCE_SCHEMA AND P.TABLE_NAME = L.SOURCE_TABLE
             WHERE L.RUN_ID = %s AND L.INGESTION_STATUS = 'COMPLETE'
           QUALIFY ROW_NUMBER() OVER (PARTITION BY L.SOURCE_TABLE ORDER BY L.CREATED_AT DESC) = 1
             ORDER BY L.SOURCE_TABLE
            """,
            (run_id,),
        )
    except Exception:
        return []
    from services.profiling.insights import scorecard

    out = []
    for r in landed:
        dims = _json(r.pop("quality_json", None))
        out.append({**r, "quality": scorecard(dims, None) if dims else None,
                    "status": "CACHED" if r.get("profile_stage_path") else "UNPROFILED"})
    return out


def _cached_profile_columns(db: Db, tables: list[dict]) -> list[dict]:
    """Columns from staged profile documents, shaped like PROFILE_REGISTRY rows (older or purged runs)."""
    from services.profiling.profiler import STAGE_PATH

    out: list[dict] = []
    for t in tables:
        path = t.get("profile_stage_path") or ""
        if not STAGE_PATH.match(path):
            continue
        try:
            found = db.query(f"SELECT $1 AS DOC FROM @METADATA.PROFILES_STAGE/{path} "
                             "(FILE_FORMAT => 'METADATA.PROFILE_JSON_FORMAT')")
        except Exception:
            continue
        doc = found[0]["doc"] if found else None
        doc = json.loads(doc) if isinstance(doc, str) else doc
        for c in (doc or {}).get("columns", []):
            s = c.get("statistics") or {}
            out.append({
                "profile_id": f"cache:{t['table_name']}:{c.get('column_name')}",
                "table_name": t["table_name"], "column_name": c.get("column_name"), "data_type": c.get("data_type"),
                "semantic_type": c.get("semantic_type"), "pii_classification": c.get("pii_classification") or "NONE",
                "row_count": s.get("row_count"), "null_percentage": s.get("null_percentage"),
                "distinct_percentage": s.get("distinct_percentage"), "cardinality": c.get("cardinality"),
                "potential_key_flag": bool(c.get("potential_key")), "potential_foreign_key_flag": False,
                "generated_description": c.get("description"), "profile_status": "CACHED",
                "values": (s.get("enum_values")
                           or [f.get("value") for f in s.get("frequency_distribution") or []])[:8],
            })
    return out


@app.get("/api/runs/{run_id}/profile")
def get_profile(run_id: str, db: Db = Depends(current_db)):
    columns = db.query(
        """
        SELECT PROFILE_ID, TABLE_NAME, COLUMN_NAME, DATA_TYPE, SEMANTIC_TYPE, PII_CLASSIFICATION,
               ROW_COUNT, NULL_PERCENTAGE, DISTINCT_PERCENTAGE, CARDINALITY, POTENTIAL_KEY_FLAG,
               POTENTIAL_FOREIGN_KEY_FLAG, GENERATED_DESCRIPTION, PROFILE_STATUS,
               STATISTICS_JSON:cache::VARCHAR AS CACHE
          FROM PROFILE.PROFILE_REGISTRY
         WHERE RUN_ID = %s AND IS_CURRENT
         ORDER BY TABLE_NAME, COLUMN_NAME
        """,
        (run_id,),
    )
    tables = _profile_cache_status(db, run_id)
    source = "registry"
    if not columns and any(t["status"] == "CACHED" for t in tables):
        columns = _cached_profile_columns(db, tables)
        source = "cache" if columns else source
    return {"columns": columns, "tables": tables, "source": source}


@app.post("/api/runs/{run_id}/domain")
def identify_domain(run_id: str, db: Db = Depends(current_db)):
    try:
        return db.call("CALL KNOWLEDGE.IDENTIFY_DOMAIN(%s)", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


class DomainChoice(BaseModel):
    domain_id: str = Field(min_length=1, max_length=64)


@app.put("/api/runs/{run_id}/domain")
def confirm_domain(run_id: str, body: DomainChoice, db: Db = Depends(current_db)):
    """A reviewer picks the knowledge pack when detection was not confident (or overrides it before mapping)."""
    run = db.query("SELECT CURRENT_STATE FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")
    if run[0]["current_state"] not in ("PROFILING_COMPLETE", "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "FAILED"):
        raise HTTPException(409, "The pack can only be changed before mapping starts.")
    if not db.query("SELECT 1 FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s AND ACTIVE_FLAG", (body.domain_id,)):
        raise HTTPException(400, "unknown or inactive domain")
    db.execute("UPDATE CORE.WORKFLOW_RUN SET DOMAIN_ID = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE RUN_ID = %s",
               (body.domain_id, run_id))
    db.execute("UPDATE KNOWLEDGE.DOMAIN_RECOMMENDATION SET STATUS = IFF(DOMAIN_ID = %s, 'ACCEPTED', 'PROPOSED'), "
               "DECIDED_BY = IFF(DOMAIN_ID = %s, CURRENT_USER(), DECIDED_BY), "
               "DECIDED_AT = IFF(DOMAIN_ID = %s, CURRENT_TIMESTAMP(), DECIDED_AT) WHERE RUN_ID = %s",
               (body.domain_id, body.domain_id, body.domain_id, run_id))
    _drop_run(run_id)
    return {"domain_id": body.domain_id, "confirmed": True}


@app.get("/api/runs/{run_id}/domain")
def get_domain(run_id: str, db: Db = Depends(current_db)):
    return {"recommendations": db.query(
        """
        SELECT R.RECOMMENDATION_ID, D.DOMAIN_NAME, R.DOMAIN_ID, R.CONFIDENCE, R.RECOMMENDATION,
               R.STATUS, R.DECIDED_BY, R.CREATED_AT::VARCHAR AS CREATED_AT
          FROM KNOWLEDGE.DOMAIN_RECOMMENDATION R
          JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
         WHERE R.RUN_ID = %s
         ORDER BY R.CONFIDENCE DESC
        """,
        (run_id,),
    )}


@app.post("/api/runs/{run_id}/mapping")
def generate_mapping(run_id: str, db: Db = Depends(current_db)):
    try:
        return db.call("CALL MAPPING.GENERATE_MAPPING_CANDIDATES(%s)", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/mapping/decisions")
def save_mapping(run_id: str, body: MappingDecisions, db: Db = Depends(current_db)):
    try:
        return db.call("CALL MAPPING.SAVE_MAPPING_DECISIONS(%s, %s)",
                       (run_id, json.dumps(body.decisions)))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


def _mapping_target_table(db: Db, run_id: str) -> Optional[dict]:
    """The run's target model, resolved exactly like MAPPING.SAVE_MAPPING_DECISIONS does."""
    found = db.query(
        """
        SELECT TB.TARGET_TABLE_ID, TB.TARGET_TABLE, TB.TARGET_DATABASE, TB.TARGET_SCHEMA, TB.GRAIN
          FROM CORE.WORKFLOW_RUN R
          JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY TB ON TB.ACTIVE_FLAG
               AND COALESCE(R.TARGET_MODEL, '') <> ''
               AND UPPER(TB.TARGET_TABLE) = UPPER(SPLIT_PART(R.TARGET_MODEL, '.', -1))
               AND (ARRAY_SIZE(SPLIT(R.TARGET_MODEL, '.')) < 3
                    OR (UPPER(TB.TARGET_DATABASE) = UPPER(SPLIT_PART(R.TARGET_MODEL, '.', 1))
                        AND UPPER(TB.TARGET_SCHEMA) = UPPER(SPLIT_PART(R.TARGET_MODEL, '.', 2))))
               AND (ARRAY_SIZE(SPLIT(R.TARGET_MODEL, '.')) = 3 OR TB.DOMAIN_ID = R.DOMAIN_ID)
         WHERE R.RUN_ID = %s
         ORDER BY IFF(TB.DOMAIN_ID = R.DOMAIN_ID, 0, 1), TB.CREATED_AT LIMIT 1
        """,
        (run_id,),
    )
    return found[0] if found else None


def _mapping_targets(db: Db, target_table_id: str) -> list[dict]:
    return db.query(
        """
        SELECT T.TARGET_COLUMN_ID, T.COLUMN_NAME, T.DATA_TYPE, T.NULLABLE, T.IS_BUSINESS_KEY, T.SEMANTIC_TYPE,
               T.IS_PII, T.BUSINESS_DEFINITION AS DEFINITION
          FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY T
         WHERE T.TARGET_TABLE_ID = %s
         ORDER BY T.ORDINAL_POSITION
        """,
        (target_table_id,),
    )


def _mapping_profile(db: Db, run_id: str) -> dict[str, dict]:
    out = {}
    for p in db.query(
        """
        SELECT SOURCE_COLUMN_ID, TABLE_NAME, COLUMN_NAME, DATA_TYPE, SEMANTIC_TYPE, NULL_PERCENTAGE,
               DISTINCT_PERCENTAGE, CARDINALITY, GENERATED_DESCRIPTION, PII_CLASSIFICATION,
               STATISTICS_JSON:enum_values AS ENUM_VALUES, STATISTICS_JSON:frequency_distribution AS TOP_VALUES
          FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = %s AND IS_CURRENT
        """,
        (run_id,),
    ):
        values = None
        for key in ("enum_values", "top_values"):
            raw = p.get(key)
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raw = None
            if raw:
                values = [v.get("value", next(iter(v.values()), None)) if isinstance(v, dict) else v for v in raw][:8]
                break
        out[p["source_column_id"]] = {
            "source_column_id": p["source_column_id"], "source_table": p["table_name"],
            "column_name": p["column_name"], "data_type": p["data_type"], "semantic_type": p["semantic_type"],
            "null_percentage": p["null_percentage"], "distinct_percentage": p["distinct_percentage"],
            "cardinality": p["cardinality"], "description": p["generated_description"],
            "pii": p["pii_classification"], "values": values,
        }
    return out or _mapping_profile_from_cache(db, run_id)


def _mapping_profile_from_cache(db: Db, run_id: str) -> dict[str, dict]:
    """Runs without PROFILE_REGISTRY rows still give the copilot statistics, from the persistent profiles."""
    tables = [t for t in _profile_cache_status(db, run_id) if t["status"] == "CACHED"]
    if not tables:
        return {}
    cached = {(c["table_name"], c["column_name"]): c for c in _cached_profile_columns(db, tables)}
    out = {}
    for col in db.query(
        """
        SELECT C.LANDING_COLUMN_ID, T.SOURCE_TABLE, C.COLUMN_NAME
          FROM SOURCE.LANDING_COLUMN_REGISTRY C
          JOIN SOURCE.LANDING_TABLE_REGISTRY T ON T.LANDING_ID = C.LANDING_ID
         WHERE T.RUN_ID = %s AND T.INGESTION_STATUS = 'COMPLETE'
        """,
        (run_id,),
    ):
        c = cached.get((col["source_table"], col["column_name"]))
        if not c:
            continue
        values = None if c["pii_classification"] != "NONE" else c.get("values")
        out[col["landing_column_id"]] = {
            "source_column_id": col["landing_column_id"], "source_table": col["source_table"],
            "column_name": c["column_name"], "data_type": c["data_type"], "semantic_type": c["semantic_type"],
            "null_percentage": c["null_percentage"], "distinct_percentage": c["distinct_percentage"],
            "cardinality": c["cardinality"], "description": c["generated_description"],
            "pii": c["pii_classification"], "values": values,
        }
    return out


@app.get("/api/runs/{run_id}/mapping")
def get_mapping(run_id: str, db: Db = Depends(current_db)):
    candidates = db.query(
        """
        SELECT C.CANDIDATE_ID, C.SOURCE_COLUMN_ID, L.COLUMN_NAME AS SOURCE_COLUMN, L.DATA_TYPE AS SOURCE_DATATYPE,
               TBL.SOURCE_TABLE, C.TARGET_COLUMN_ID, T.COLUMN_NAME AS TARGET_COLUMN, T.DATA_TYPE AS TARGET_DATATYPE,
               T.NULLABLE, T.IS_BUSINESS_KEY, C.FINAL_SCORE, C.RANK, C.CONFIDENCE, C.RECOMMENDATION,
               C.GENERATED_REASON, C.TRANSFORMATION, C.SEMANTIC_SCORE, C.KEYWORD_SCORE, C.DATATYPE_SCORE,
               C.STATISTICAL_SCORE, C.DOMAIN_SCORE, C.CONTEXT_SCORE, C.HISTORICAL_SCORE,
               C.EVIDENCE_JSON:llm:preferred_target::VARCHAR AS LLM_PREFERRED,
               C.EVIDENCE_JSON:llm:agrees::BOOLEAN AS LLM_AGREES
          FROM MAPPING.MAPPING_CANDIDATE C
          JOIN SOURCE.LANDING_COLUMN_REGISTRY L ON L.LANDING_COLUMN_ID = C.SOURCE_COLUMN_ID
          JOIN SOURCE.LANDING_TABLE_REGISTRY TBL ON TBL.LANDING_ID = L.LANDING_ID
          JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY T ON T.TARGET_COLUMN_ID = C.TARGET_COLUMN_ID
         WHERE C.RUN_ID = %s AND C.IS_CURRENT
         ORDER BY TBL.SOURCE_TABLE, L.COLUMN_NAME, C.RANK
        """,
        (run_id,),
    )
    decisions = db.query(
        """
        SELECT D.DECISION_ID, D.SOURCE_COLUMN_ID, D.TARGET_COLUMN_ID, D.CANDIDATE_ID, D.DECISION,
               D.TRANSFORMATION, D.BUSINESS_JUSTIFICATION, D.REVIEWER, D.REVIEWED_AT::VARCHAR AS REVIEWED_AT
          FROM MAPPING.MAPPING_DECISION D
         WHERE D.RUN_ID = %s AND D.IS_CURRENT
        """,
        (run_id,),
    )
    table = _mapping_target_table(db, run_id)
    targets = _mapping_targets(db, table["target_table_id"]) if table else []
    profile = _mapping_profile(db, run_id) if candidates else {}
    from services.common.standard import SYSTEM_DERIVED as system_derived
    sources = {c["source_column_id"] for c in candidates}
    decided = {d["source_column_id"] for d in decisions}
    mapped = {d["target_column_id"] for d in decisions if d["decision"] != "REJECTED" and d["target_column_id"]}
    missing = [t["column_name"] for t in targets
               if not t["nullable"] and t["semantic_type"] not in system_derived and t["target_column_id"] not in mapped]
    undecided = sorted({c["source_column"] for c in candidates if c["source_column_id"] not in decided})
    return {
        "candidates": candidates, "decisions": decisions, "targets": targets,
        "target_table": table, "profile": profile, "bands": _ui_bands(db, run_id),
        "status": {
            "source_columns": len(sources), "decided": len(sources & decided),
            "undecided": undecided, "missing_required_targets": missing,
            "complete": bool(sources) and sources <= decided and not missing,
        },
    }


class MappingAssist(BaseModel):
    source_column_ids: list[str] = Field(default_factory=list, max_length=200)
    instructions: str = Field(default="", max_length=2000)


@app.post("/api/runs/{run_id}/mapping/assist")
def mapping_assist(run_id: str, body: MappingAssist, db: Db = Depends(current_db)):
    """AI copilot: proposes a decision per column. Read-only — nothing is saved until the reviewer accepts."""
    from services.mapping import assist

    data = get_mapping(run_id, db)
    table = data["target_table"]
    if not table or not data["candidates"]:
        raise HTTPException(409, "Generate mapping candidates first")
    decided = {d["source_column_id"] for d in data["decisions"]}
    wanted = set(body.source_column_ids) or {c["source_column_id"] for c in data["candidates"]} - decided
    by_source: dict[str, list] = {}
    for c in data["candidates"]:
        by_source.setdefault(c["source_column_id"], []).append(c)
    sources = []
    for sid in sorted(wanted, key=lambda s: (by_source.get(s, [{}])[0].get("source_table") or "",
                                             by_source.get(s, [{}])[0].get("source_column") or "")):
        if sid not in by_source:
            continue
        first = by_source[sid][0]
        sources.append(data["profile"].get(sid) or {
            "source_column_id": sid, "source_table": first["source_table"], "column_name": first["source_column"],
            "data_type": first["source_datatype"]})
    sources = sources[:assist.MAX_COLUMNS]
    if not sources:
        return {"suggestions": [], "model": None, "skipped": 0}
    taken = {d["target_column_id"]: d["source_column_id"] for d in data["decisions"]
             if d["decision"] != "REJECTED" and d["target_column_id"] and d["source_column_id"] not in wanted}
    prompt = assist.build_prompt(sources, by_source, data["targets"], table["target_table"], taken, body.instructions)
    model = _model_for(db, "MAPPING")
    started = time.time()
    try:
        result, query_id = db.query_with_id(
            "SELECT AI_COMPLETE(model => %s, prompt => %s, "
            "model_parameters => {'temperature': 0, 'max_tokens': 8000}, "
            "response_format => PARSE_JSON(%s), show_details => TRUE) AS R",
            (model, prompt, json.dumps({"type": "json", "schema": assist.SCHEMA})),
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    details = result[0]["r"] if result else None
    details = json.loads(details) if isinstance(details, str) else (details or {})
    _record_cost(db, run_id, "MAPPING", details.get("model", model),
                 {**(details.get("usage") or {}), "query_id": query_id}, started)
    structured = details.get("structured_output") or []
    if not structured:
        raise HTTPException(502, "Cortex returned no structured answer for these columns. Try fewer columns or again.")
    raw = structured[0].get("raw_message")
    try:
        raw = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError as exc:
        raise HTTPException(502, "Cortex returned an answer that was not valid JSON. Try again.") from exc
    suggestions = assist.normalize(raw, sources, by_source, data["targets"], taken)
    for s in suggestions:
        s["decision"] = assist.to_decision(s)
    return {"suggestions": suggestions, "model": details.get("model", model),
            "skipped": max(0, len(wanted) - len(sources))}


@app.post("/api/runs/{run_id}/sttm")
def generate_sttm(run_id: str, db: Db = Depends(current_db)):
    try:
        return db.call("CALL CONTRACT.GENERATE_STTM(%s)", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/runs/{run_id}/sttm")
def get_sttm(run_id: str, db: Db = Depends(current_db)):
    header = db.query(
        """
        SELECT STTM_ID, STTM_VERSION, STATUS, TABLE_DESIGN, CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT,
               REVIEWED_BY, REVIEWED_AT::VARCHAR AS REVIEWED_AT
          FROM CONTRACT.STTM_REGISTRY WHERE RUN_ID = %s
         ORDER BY STTM_VERSION DESC LIMIT 1
        """,
        (run_id,),
    )
    lines = []
    if header:
        lines = db.query(
            """
            SELECT STTM_LINE_ID, SOURCE_TABLE, SOURCE_COLUMN, SOURCE_DATATYPE, TARGET_COLUMN, TARGET_DATATYPE,
                   MAPPING_TYPE, TRANSFORMATION, BUSINESS_RULE, BUSINESS_DEFINITION, NULLABLE_RULE,
                   UNIQUENESS_RULE, HUMAN_APPROVED, REVIEWER, MAPPING_CONFIDENCE, JOIN_LOGIC
              FROM CONTRACT.STTM_LINE WHERE STTM_ID = %s ORDER BY TARGET_COLUMN
            """,
            (header[0]["sttm_id"],),
        )
    return {"sttm": header[0] if header else None, "lines": lines}


class JoinEdit(BaseModel):
    left_table: str = Field(min_length=1, max_length=255)
    right_table: str = Field(min_length=1, max_length=255)
    keys: list[str] = Field(default_factory=list, max_length=10)
    join_type: str = Field(default="LEFT", pattern=r"^(LEFT|INNER)$")
    cardinality: Optional[str] = Field(default=None, pattern=r"^(1:1|N:1|1:N|N:N)$")
    remove: bool = False


class JoinPlanEdit(BaseModel):
    driving_table: Optional[str] = Field(default=None, max_length=255)
    joins: list[JoinEdit] = Field(default_factory=list, max_length=50)


@app.put("/api/runs/{run_id}/sttm/joins")
def update_sttm_joins(run_id: str, body: JoinPlanEdit, db: Db = Depends(current_db)):
    """Edit the STTM join plan while it is under review; dbt generation follows the edited plan."""
    try:
        return db.call("CALL CONTRACT.UPDATE_JOIN_GRAPH(%s, %s)", (run_id, body.model_dump_json()))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/runs/{run_id}/sttm/preview")
def sttm_preview(run_id: str, db: Db = Depends(current_db)):
    """The SELECT the confirmed joins and mappings produce, before dbt generation."""
    from services.sttm.join_graph import preview_sql

    data = get_sttm(run_id, db)
    if not data["sttm"]:
        raise HTTPException(404, "no STTM for this run yet")
    design = _json(data["sttm"].get("table_design")) or {}
    return {"sql": preview_sql(design.get("join_graph") or {}, data["lines"], design.get("target_table") or "TARGET"),
            "join_graph": design.get("join_graph")}


@app.post("/api/runs/{run_id}/sttm/refine")
def refine_sttm_transform(run_id: str, body: TransformPrompt, db: Db = Depends(current_db)):
    try:
        return db.call("CALL CONTRACT.REFINE_TRANSFORMATION(%s, %s)", (run_id, body.model_dump_json()))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/sttm/apply")
def apply_sttm_transform(run_id: str, body: TransformApply, db: Db = Depends(current_db)):
    try:
        return db.call("CALL CONTRACT.APPLY_TRANSFORMATION(%s, %s)", (run_id, body.model_dump_json()))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/sttm/export")
def export_sttm_csv(run_id: str, db: Db = Depends(current_db)):
    try:
        return db.call("CALL CONTRACT.EXPORT_STTM_CSV(%s)", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/soda")
def generate_soda(run_id: str, db: Db = Depends(current_db)):
    try:
        return db.call("CALL CONTRACT.GENERATE_SODA(%s)", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/soda/import")
def import_soda(run_id: str, body: ClientExpectations, db: Db = Depends(current_db)):
    payload: dict | list
    if body.brief or body.text:
        payload = {"text": body.brief or body.text, "filename": body.filename or ""}
    elif body.rows:
        payload = body.rows
    else:
        raise HTTPException(400, "Upload a client brief or a JSON/CSV array of requirement rows")
    try:
        return db.call("CALL CONTRACT.IMPORT_CLIENT_EXPECTATIONS(%s, %s)",
                       (run_id, json.dumps(payload)))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/soda/decisions")
def save_soda(run_id: str, body: SodaDecisions, db: Db = Depends(current_db)):
    from services.soda.custom import validate

    for d in body.decisions:  # an edited definition is checked like a new check before it is stored
        if str(d.get("decision") or "").upper() == "MODIFIED" and isinstance(d.get("definition"), dict):
            found = db.query("SELECT TARGET_COLUMN, SEVERITY FROM CONTRACT.SODA_EXPECTATION_REGISTRY "
                             "WHERE EXPECTATION_ID = %s AND RUN_ID = %s AND IS_CURRENT", (d.get("expectation_id"), run_id))
            if not found:  # an unknown id would be stored without validation
                raise HTTPException(400, f"No current check {d.get('expectation_id')} in this run; reload and try again.")
            cleaned, problems = validate({"target_column": found[0]["target_column"],
                                          "severity": found[0]["severity"], "definition": d["definition"]})
            if problems:
                raise HTTPException(400, "; ".join(problems))
            d["definition"] = cleaned["definition"]
    try:
        return db.call("CALL CONTRACT.SAVE_SODA_DECISIONS(%s, %s)",
                       (run_id, json.dumps(body.decisions)))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


class SodaCheckIn(BaseModel):
    target_column: Optional[str] = Field(default=None, max_length=255)
    severity: Literal["FAIL", "WARN"] = "FAIL"
    requirement: Optional[str] = Field(default=None, max_length=4000)
    definition: dict[str, Any]


@app.post("/api/runs/{run_id}/soda/checks")
def add_soda_check(run_id: str, body: SodaCheckIn, db: Db = Depends(current_db)):
    """A check written by a person (guided form or raw definition), validated for its kind, added as PROPOSED."""
    from services.soda.custom import validate

    check, problems = validate(body.model_dump())
    if problems:
        raise HTTPException(400, "; ".join(problems))
    try:
        current = db.query("""SELECT STTM_ID, DOMAIN_ID, TARGET_TABLE, VERSION FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                               WHERE RUN_ID = %s AND IS_CURRENT ORDER BY VERSION DESC LIMIT 1""", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    if not current:
        raise HTTPException(409, "Generate the data quality checks first; custom checks are added to that set.")
    c = current[0]
    expectation_id = str(uuid.uuid4())
    try:
        db.execute("""INSERT INTO CONTRACT.SODA_EXPECTATION_REGISTRY (EXPECTATION_ID, RUN_ID, STTM_ID, DOMAIN_ID, TARGET_TABLE,
                             TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION, SEVERITY, ORIGIN, CLIENT_REQUIREMENT, STATUS, VERSION,
                             IS_CURRENT, CREATED_BY)
                      SELECT %s, %s, %s, %s, %s, NULLIF(%s, ''), %s, PARSE_JSON(%s), %s, 'USER', NULLIF(%s, ''), 'PROPOSED',
                             %s, TRUE, CURRENT_USER()""",
                   (expectation_id, run_id, c["sttm_id"], c["domain_id"], c["target_table"], check["target_column"] or "",
                    check["check_type"], json.dumps(check["definition"]), check["severity"], check["requirement"] or "",
                    c["version"]))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    from services.soda.expectations import render_check

    return {"expectation_id": expectation_id, "check": check, "sodacl": render_check({**check, "target_table": c["target_table"]})}


@app.post("/api/runs/{run_id}/soda/scan")
def scan_soda(run_id: str, db: Db = Depends(current_db)):
    """Run the checks in Snowflake now (built model, or today's source data when it is not built yet) and keep the
    results. Uses the signed-in role, so it reads exactly what that role may read."""
    from services.quality.scan import run_scan

    try:
        return invoke_source(db, run_scan, run_id, "UI")
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/runs/{run_id}/soda/scans")
def soda_scans(run_id: str, db: Db = Depends(current_db)):
    """Scan history, the latest scan's results, and each check's recent outcomes for trends."""
    try:
        scans = db.query("""SELECT SCAN_ID, TARGET, MODE, STARTED_AT::VARCHAR AS STARTED_AT, DURATION_MS, CHECKS, PASSED,
                                   WARNED, FAILED, NOT_EVALUATED, ERRORS, HEALTH, ROWS_SCANNED, TRIGGERED_BY, CREATED_BY
                              FROM QUALITY.CHECK_RUN WHERE RUN_ID = %s ORDER BY STARTED_AT DESC LIMIT 30""", (run_id,))
    except Exception:
        return {"scans": [], "latest": [], "history": {}, "ready": False}
    latest: list[dict] = []
    history: dict[str, list] = {}
    if scans:
        latest = db.query("""SELECT EXPECTATION_ID, TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, KIND, DIMENSION, SEVERITY,
                                    OUTCOME, MEASURED, THRESHOLD, FAILED_ROWS, DETAIL, SAMPLE_ROWS, SQL_TEXT, DURATION_MS
                               FROM QUALITY.CHECK_RESULT WHERE SCAN_ID = %s
                              ORDER BY ARRAY_POSITION(OUTCOME::VARIANT, ARRAY_CONSTRUCT('FAIL','ERROR','WARN','NOT_EVALUATED','PASS')),
                                       TARGET_COLUMN""", (scans[0]["scan_id"],))
        for r in latest:
            r["sample"] = _json(r.pop("sample_rows", None))
        for r in db.query("""SELECT EXPECTATION_ID, OUTCOME, MEASURED, CREATED_AT::VARCHAR AS AT
                               FROM QUALITY.CHECK_RESULT WHERE RUN_ID = %s
                              QUALIFY ROW_NUMBER() OVER (PARTITION BY EXPECTATION_ID ORDER BY CREATED_AT DESC) <= 12
                              ORDER BY CREATED_AT""", (run_id,)):
            history.setdefault(r["expectation_id"] or "", []).append({"outcome": r["outcome"], "measured": r["measured"], "at": r["at"]})
    return {"scans": scans, "latest": latest, "history": history, "ready": True}


def _soda_kit(db: Db, run_id: str) -> tuple[dict, str]:
    from services.soda.kit import build_kit

    run = db.query("SELECT RUN_NAME FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")
    checks = db.query("""SELECT TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION, SEVERITY, STATUS,
                                CLIENT_REQUIREMENT AS REQUIREMENT
                           FROM CONTRACT.SODA_EXPECTATION_REGISTRY WHERE RUN_ID = %s AND IS_CURRENT""", (run_id,))
    if not checks:
        raise HTTPException(409, "Generate the data quality checks first.")
    for c in checks:
        c["definition"] = _json(c.pop("check_definition")) or {}
    target = db.query("""SELECT T.TARGET_DATABASE, T.TARGET_SCHEMA, COALESCE(S.TABLE_DESIGN:target_table::VARCHAR, T.TARGET_TABLE) AS T
                           FROM CONTRACT.STTM_REGISTRY S JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = S.TARGET_TABLE_ID
                          WHERE S.RUN_ID = %s ORDER BY S.STTM_VERSION DESC LIMIT 1""", (run_id,))
    ctx = db.query("""SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() AS ACCOUNT,
                             CURRENT_WAREHOUSE() AS WAREHOUSE, CURRENT_ROLE() AS ROLE""")[0]
    database = target[0]["target_database"] if target else "<DATABASE>"
    schema = target[0]["target_schema"] if target else "<SCHEMA>"
    table = (target[0]["t"] if target else checks[0]["target_table"]).upper()
    files = build_kit(run[0]["run_name"], ctx["account"] or "<account>", database, schema, table,
                      ctx["warehouse"] or "<WAREHOUSE>", ctx["role"] or "<ROLE>", checks)
    return files, re.sub(r"[^A-Za-z0-9_-]+", "-", run[0]["run_name"]).strip("-").lower() or "soda-kit"


@app.get("/api/runs/{run_id}/soda/kit")
def soda_kit(run_id: str, db: Db = Depends(current_db)):
    files, slug = _soda_kit(db, run_id)
    return {"files": files, "slug": slug}


@app.get("/api/runs/{run_id}/soda/kit.zip")
def soda_kit_zip(run_id: str, db: Db = Depends(current_db)):
    from services.soda.kit import zip_kit

    files, slug = _soda_kit(db, run_id)
    return Response(content=zip_kit(files), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{slug}-soda-kit.zip"'})


class QaAsk(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class QaTest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    sql: str = Field(min_length=6, max_length=16000)
    objective: Optional[str] = Field(default=None, max_length=4000)
    expected: Optional[str] = Field(default=None, max_length=1000)
    category: Optional[str] = Field(default="CUSTOM", max_length=32)
    severity: Optional[str] = Field(default="MEDIUM", max_length=16)
    target_column: Optional[str] = Field(default=None, max_length=256)
    prompt: Optional[str] = Field(default=None, max_length=4000)


@app.get("/api/runs/{run_id}/qa")
def qa_suite(run_id: str, db: Db = Depends(current_db)):
    """Functional QA test SQL for the run's target, derived from the current STTM, plus saved queries."""
    from services.qa.procedures import qa_suite as handler

    try:
        return _source_call(db, "CALL CONTRACT.QA_SUITE(%s)", handler, run_id)
    except Exception as exc:
        raise _snowflake_error(exc) from exc


class QaSignoff(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    note: Optional[str] = Field(default=None, max_length=4000)
    override: bool = False


@app.get("/api/runs/{run_id}/qa/signoff")
def get_qa_signoff(run_id: str, db: Db = Depends(current_db)):
    return {"signoff": _qa_signoff(db, run_id)}


@app.post("/api/runs/{run_id}/qa/signoff")
def qa_signoff(run_id: str, body: QaSignoff, db: Db = Depends(current_db)):
    """A tester approves or rejects the QA tests of the current STTM version; a rejection needs a reason."""
    from services.workflow.lanes import POST_STTM

    current = db.query("SELECT CURRENT_STATE FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not current:
        raise HTTPException(404, "run not found")
    if current[0]["current_state"] not in POST_STTM or current[0]["current_state"] in ("DBT_APPROVED", "COMPLETED"):
        raise HTTPException(409, "QA can be signed off after the STTM is approved and before code review is approved.")
    if body.decision == "REJECTED" and not (body.note or "").strip():
        raise HTTPException(400, "Say what failed when rejecting the QA tests.")
    note = (body.note or "").strip()
    sttm = db.query("SELECT STTM_ID FROM CONTRACT.STTM_REGISTRY WHERE RUN_ID = %s AND STATUS IN ('APPROVED', 'REVIEW') "
                    "ORDER BY STTM_VERSION DESC LIMIT 1", (run_id,))
    if not sttm:
        raise HTTPException(409, "The run has no STTM in review or approved yet.")
    if body.decision == "APPROVED":
        from services.qa.run import blocking, latest

        last, results = latest(db.query, run_id)
        # an approval covers tested work: the latest QA run must be on the STTM version being signed off
        if not last or str(last.get("sttm_id") or "") != str(sttm[0]["sttm_id"] or ""):
            raise HTTPException(409, "Run QA on the current STTM version before signing off.")
        failing = blocking([{"outcome": r["outcome"], "severity": r["severity"]} for r in results])
        if failing and not body.override:
            raise HTTPException(409, f"The last QA run has {len(failing)} failing critical or high tests. Fix them and "
                                     "run again, or approve with an override and a reason.")
        if failing:
            if len(note) < 15:
                raise HTTPException(400, "An override needs a reason of at least 15 characters.")
            note = f"[override: {len(failing)} critical/high tests failing] {note}"
    db.execute("INSERT INTO CONTRACT.QA_SIGNOFF (SIGNOFF_ID, RUN_ID, STTM_ID, DECISION, NOTE) "
               "SELECT %s, %s, %s, %s, NULLIF(%s, '')",
               (str(uuid.uuid4()), run_id, sttm[0]["sttm_id"], body.decision, note))
    _drop_run(run_id)
    return {"signoff": _qa_signoff(db, run_id)}


@app.post("/api/runs/{run_id}/qa/ask")
def qa_ask(run_id: str, body: QaAsk, db: Db = Depends(current_db)):
    """Natural language to one guarded, read-only test query (compiled, never executed)."""
    from services.qa.procedures import qa_ask as handler

    try:
        return _source_call(db, "CALL CONTRACT.QA_ASK(%s, %s)", handler, run_id, body.question)
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/qa/tests")
def qa_save(run_id: str, body: QaTest, db: Db = Depends(current_db)):
    from services.qa.procedures import qa_save as handler

    try:
        return _source_call(db, "CALL CONTRACT.QA_SAVE(%s, %s)", handler, run_id, body.model_dump_json())
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.put("/api/runs/{run_id}/qa/tests/{test_id}")
def qa_update(run_id: str, test_id: str, body: QaTest, db: Db = Depends(current_db)):
    """Edit a saved test (SQL, title, expected, severity); checked by the guard and compiled again."""
    from services.qa.procedures import qa_update as handler

    try:
        return invoke_source(db, handler, run_id, test_id, body.model_dump_json())
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


class QaRunIn(BaseModel):
    test_ids: Optional[list[str]] = Field(default=None, max_length=500)


@app.post("/api/runs/{run_id}/qa/run")
def qa_run(run_id: str, body: QaRunIn, db: Db = Depends(current_db)):
    """Run all (or the chosen) QA tests in Snowflake with the signed-in role and keep the results."""
    from services.qa.run import run_tests

    try:
        result = invoke_source(db, run_tests, run_id, body.test_ids, "UI")
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {k: v for k, v in result.items() if k != "results"}


@app.get("/api/runs/{run_id}/qa/results")
def qa_results(run_id: str, db: Db = Depends(current_db)):
    """The latest QA run with every test's result, and each test's recent outcomes."""
    from services.qa.run import latest

    last, results = latest(db.query, run_id)
    if last is None and not results:
        try:
            db.query("SELECT 1 FROM QUALITY.QA_RUN LIMIT 0")
        except Exception:
            return {"run": None, "results": [], "history": {}, "runs": [], "ready": False}
    for r in results:
        r["sample"] = _json(r.pop("sample_rows", None))
        r["columns"] = _json(r.get("columns"))
    history: dict[str, list] = {}
    runs: list[dict] = []
    if last:
        for r in db.query("""SELECT TEST_ID, OUTCOME, CREATED_AT::VARCHAR AS AT FROM QUALITY.QA_RESULT WHERE RUN_ID = %s
                             QUALIFY ROW_NUMBER() OVER (PARTITION BY TEST_ID ORDER BY CREATED_AT DESC) <= 10
                             ORDER BY CREATED_AT""", (run_id,)):
            history.setdefault(r["test_id"], []).append({"outcome": r["outcome"], "at": r["at"]})
        runs = db.query("""SELECT QA_RUN_ID, STARTED_AT::VARCHAR AS STARTED_AT, TESTS, PASSED, FAILED, REVIEW, NOT_RUN, ERRORS,
                                  CREATED_BY FROM QUALITY.QA_RUN WHERE RUN_ID = %s ORDER BY STARTED_AT DESC LIMIT 20""",
                        (run_id,))
    return {"run": last, "results": results, "history": history, "runs": runs, "ready": True}


class QaPlanIn(BaseModel):
    focus: Optional[str] = Field(default="", max_length=1000)


@app.post("/api/runs/{run_id}/qa/plan")
def qa_plan(run_id: str, body: QaPlanIn, db: Db = Depends(current_db)):
    """AI test plan grounded in domain knowledge, the profile and the approved checks (guarded, compiled, not saved)."""
    from services.qa.procedures import qa_plan as handler

    try:
        return invoke_source(db, handler, run_id, body.focus or "")
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.delete("/api/runs/{run_id}/qa/tests/{test_id}")
def qa_delete(run_id: str, test_id: str, db: Db = Depends(current_db)):
    if not db.query("SELECT 1 AS X FROM CONTRACT.QA_TEST_CASE WHERE RUN_ID = %s AND TEST_ID = %s AND NOT IS_DELETED",
                    (run_id, test_id)):
        raise HTTPException(404, "That QA test does not exist in this run.")
    db.execute("UPDATE CONTRACT.QA_TEST_CASE SET IS_DELETED = TRUE WHERE RUN_ID = %s AND TEST_ID = %s",
               (run_id, test_id))
    return {"deleted": test_id}


@app.post("/api/runs/{run_id}/soda/backtest")
def backtest_soda(run_id: str, db: Db = Depends(current_db)):
    """Dry-run the current checks on today's source data with the signed-in role."""
    from services.soda.procedures import backtest_soda as backtest_handler

    try:
        return _source_call(db, "CALL CONTRACT.BACKTEST_SODA(%s)", backtest_handler, run_id)
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/runs/{run_id}/soda")
def get_soda(run_id: str, db: Db = Depends(current_db)):
    from services.quality.gx import render_suite
    from services.soda.expectations import render_check, render_yaml
    checks = db.query(
        """
        SELECT EXPECTATION_ID, TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION, SEVERITY,
               ORIGIN, CLIENT_REQUIREMENT, STATUS, VERSION, REVIEWED_BY
          FROM CONTRACT.SODA_EXPECTATION_REGISTRY
         WHERE RUN_ID = %s AND IS_CURRENT
         ORDER BY TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE
        """,
        (run_id,),
    )
    briefs = db.query(
        """
        SELECT TITLE, CONTENT, SOURCE_REFERENCE, CREATED_AT::VARCHAR AS CREATED_AT
          FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
         WHERE IS_CURRENT AND SOURCE_REFERENCE = %s
         ORDER BY VERSION DESC LIMIT 1
        """,
        (f"soda.brief.{run_id}",),
    )
    def _definition(value):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return {}
        return value or {}

    table = next((c["target_table"] for c in checks if c.get("target_table")), "dataset")
    rendered = []
    for c in checks:
        item = {
            "target_table": c["target_table"], "target_column": c["target_column"],
            "check_type": c["check_type"], "definition": _definition(c.get("check_definition")),
            "severity": c["severity"], "requirement": c.get("client_requirement"),
            "status": c["status"], "origin": c.get("origin"),
        }
        c["sodacl"] = render_check(item)
        c["evidence"] = item["definition"].get("evidence")
        c["backtest"] = item["definition"].get("backtest")
        rendered.append(item)
    return {
        "checks": checks,
        "brief": briefs[0] if briefs else None,
        "yaml": render_yaml(str(table).lower(), rendered),
        "gx_suite": render_suite(str(table), rendered),
        "status": {
            "total": len(checks),
            "proposed": sum(1 for c in checks if c["status"] == "PROPOSED"),
            "approved": sum(1 for c in checks if c["status"] == "APPROVED"),
            "rejected": sum(1 for c in checks if c["status"] == "REJECTED"),
        },
    }


def _clean_dbt_plan(body: Optional[DbtPlan]) -> dict:
    raw = (body or DbtPlan()).model_dump(exclude_none=True)
    # A blank prefix is meaningful (projects without an audit-column namespace), so keep it.
    return {key: value for key, value in raw.items() if key == "prefix" or (value != "" and value != [])}


def _is_dbt_arity_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "too many arguments" in text or "expected 1, got 2" in text


def _save_dbt_plan(db: Db, run_id: str, payload: dict) -> None:
    run = db.query("SELECT DOMAIN_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not run or not run[0].get("domain_id"):
        return
    ref = f"dbt.branch.{run_id}"
    db.execute(
        "UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, STATUS = 'SUPERSEDED', UPDATED_AT = CURRENT_TIMESTAMP() "
        "WHERE SOURCE_REFERENCE = %s AND IS_CURRENT",
        (ref,),
    )
    version_rows = db.query(
        "SELECT COALESCE(MAX(VERSION), 0) + 1 AS V FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = %s",
        (ref,),
    )
    version = version_rows[0]["v"] if version_rows else 1
    db.execute(
        """
        INSERT INTO KNOWLEDGE.DOMAIN_KNOWLEDGE
          (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS,
           SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, SOURCE_RUN_ID)
        SELECT UUID_STRING(), %s, 'TRANSFORMATION_RULE', %s, %s, PARSE_JSON(%s),
               PARSE_JSON('["DBT","BRANCH"]'), %s, 'ACTIVE', %s, TRUE, CURRENT_USER(), MD5('|' || %s), 'DBT', %s
        """,
        (
            run[0]["domain_id"],
            f"dbt branch plan {run_id}"[:500],
            json.dumps(payload)[:8000],
            json.dumps(payload),
            ref,
            version,
            ref,
            run_id,
        ),
    )


def _is_transition_rejected(exc: Exception) -> bool:
    return "TRANSITION_REJECTED" in str(exc)


def _generate_dbt_overlay(db: Db, run_id: str, payload: dict):
    from services.dbt.overlay import generate_via_db
    return generate_via_db(db, run_id, payload)


# ------------------------------------------------------------------ the run's repository (configured in Admin)
REPO_LOCATION_KEYS = ("origin", "repo", "git_repository", "api_integration", "allowed_prefixes", "project_dir")


def _stored_dbt_plan(db: Db, run_id: str) -> dict:
    try:
        found = db.query("SELECT CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE IS_CURRENT AND SOURCE_REFERENCE = %s",
                         (f"dbt.branch.{run_id}",))
    except Exception:
        return {}
    plan = _json(found[0].get("content_json")) if found else {}
    return plan if isinstance(plan, dict) else {}


def _dbt_repos(db: Db, domain_id: Optional[str]) -> list[dict]:
    """Repositories offered to the dbt workspace for a domain: enabled, used for dbt, serving the domain (none listed: all)."""
    try:
        found = db.query("""SELECT REPO_ID, NAME, GIT_URL, PROVIDER, BRANCH, GIT_REPOSITORY, API_INTEGRATION, STATUS, STATS,
                                   DOMAIN_IDS, COALESCE(DBT_PROJECT_DIR, '') AS DBT_PROJECT_DIR,
                                   COALESCE(OPEN_PR, TRUE) AS OPEN_PR, COALESCE(DRAFT_PR, FALSE) AS DRAFT_PR
                              FROM CODE.REPO WHERE ENABLED AND COALESCE(USE_FOR_DBT, TRUE) ORDER BY NAME""")
    except Exception:
        return []
    out = []
    for r in found:
        domains = _json(r.pop("domain_ids", None)) or []
        if domains and domain_id not in domains:
            continue
        stats = _json(r.pop("stats", None)) or {}
        r["project_roots"] = stats.get("dbt_project_roots") or []
        r["dbt_projects"] = stats.get("dbt_projects") or []
        out.append(r)
    return out


def _dbt_repo(db: Db, domain_id: Optional[str], plan: dict) -> tuple[Optional[dict], list[dict], bool]:
    """(repository the run uses, the candidates, whether the plan is a legacy hand-made setup)."""
    candidates = _dbt_repos(db, domain_id)
    wanted = plan.get("code_repo_id")
    if wanted:
        return next((c for c in candidates if c["repo_id"] == wanted), None), candidates, False
    if plan.get("git_repository"):
        return None, candidates, True  # set up by hand before repositories were configured in Admin
    return (candidates[0] if len(candidates) == 1 else None), candidates, False


def _resolve_dbt_payload(db: Db, run_id: str, payload: dict) -> dict:
    """Fill the repository location from Admin's configuration; the browser never chooses an origin or a clone."""
    from app.code_api import _branches, _missing_branch
    from services.dbt.workspace import safe_fqn

    for key in REPO_LOCATION_KEYS:
        payload.pop(key, None)
    run = db.query("SELECT DOMAIN_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")
    prior = _stored_dbt_plan(db, run_id)
    repo, candidates, legacy = _dbt_repo(db, run[0].get("domain_id"), {**prior, **payload})
    if payload.get("code_repo_id") and not repo:
        raise HTTPException(409, "That repository is no longer offered to this run's domain (disconnected, disabled or "
                                 "moved to another domain). Pick another one, or ask an admin in Admin, Integrations.")
    if repo:
        payload.update(code_repo_id=repo["repo_id"], git_repository=repo["git_repository"], origin=repo["git_url"],
                       repo=repo["git_url"], api_integration=repo.get("api_integration") or "",
                       project_dir=repo.get("dbt_project_dir") or "")
        base = (payload.get("base_branch") or ("" if prior.get("code_repo_id") != repo["repo_id"] else prior.get("base_branch"))
                or repo["branch"])
        found, error = _branches(db, safe_fqn(repo["git_repository"]))
        names = [b["name"] for b in found]
        if base not in names:
            if error and not found:
                raise _snowflake_error(Exception(error))
            raise _missing_branch(base, names, repo["git_url"])
        payload["base_branch"] = base
        if payload.get("cut_branch") and payload["cut_branch"] == base:
            raise HTTPException(400, "The new branch must differ from the cut-from branch")
        if (repo.get("provider") or "") != "GITHUB":
            payload["push"] = False  # pull requests from the platform are GitHub only for now
    elif legacy:
        # A hand-made setup keeps the location stored with the run's plan; the browser still cannot change it.
        for key in REPO_LOCATION_KEYS:
            if prior.get(key) not in (None, ""):
                payload[key] = prior[key]
    elif len(candidates) > 1:
        raise HTTPException(409, "Several repositories serve this domain; pick one for this run: "
                                 + ", ".join(c["name"] for c in candidates))
    else:
        payload["push"] = False  # no repository: generate to the stage only
    return payload


@app.post("/api/runs/{run_id}/dbt")
def generate_dbt(run_id: str, body: Optional[DbtPlan] = None, db: Db = Depends(current_db)):
    payload = _resolve_dbt_payload(db, run_id, _clean_dbt_plan(body))
    result = _generate_dbt(run_id, payload, db)
    if payload.get("push") and isinstance(result, dict) and not result.get("error"):
        result["publish"] = _publish_dbt(db, run_id, {})
    return result


def _generate_dbt(run_id: str, payload: dict, db: Db):
    try:
        _save_dbt_plan(db, run_id, payload)
    except Exception:
        pass
    try:
        return db.call("CALL CODEGEN.GENERATE_DBT(%s, %s)", (run_id, json.dumps(payload)))
    except Exception as two_arg:
        if _is_transition_rejected(two_arg):
            try:
                return _generate_dbt_overlay(db, run_id, payload)
            except Exception as overlay_exc:
                raise _snowflake_error(overlay_exc) from overlay_exc
        if not _is_dbt_arity_error(two_arg):
            raise _snowflake_error(two_arg) from two_arg
        try:
            return db.call("CALL CODEGEN.GENERATE_DBT(%s)", (run_id,))
        except Exception as one_arg:
            if _is_transition_rejected(one_arg):
                try:
                    return _generate_dbt_overlay(db, run_id, payload)
                except Exception as overlay_exc:
                    raise _snowflake_error(overlay_exc) from overlay_exc
            raise _snowflake_error(one_arg) from one_arg


def _github_publisher_ready(db: Db) -> bool:
    try:
        return bool(db.query("SHOW PROCEDURES LIKE 'PUBLISH_DBT_PR' IN SCHEMA CODEGEN"))
    except Exception:
        return False


def _publish_dbt(db: Db, run_id: str, payload: dict) -> dict:
    if not _github_publisher_ready(db):
        return {"status": "NOT_CONFIGURED",
                "detail": "GitHub publishing is not set up. An admin can set it up in the GitHub publishing card."}
    try:
        return db.call("CALL CODEGEN.PUBLISH_DBT_PR(%s, %s)", (run_id, json.dumps(payload)))
    except Exception as exc:
        return {"status": "FAILED", "detail": str(exc)[:1000]}


class DbtPublish(BaseModel):
    # where to push was fixed at generation from the configured repository; only the branch names may be confirmed
    base_branch: Optional[str] = Field(default=None, max_length=200)
    cut_branch: Optional[str] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, max_length=200)
    draft: bool = False
    create_project: bool = True


@app.post("/api/runs/{run_id}/dbt/publish")
def publish_dbt(run_id: str, body: DbtPublish, db: Db = Depends(current_db)):
    """Push the latest generation to a new GitHub branch and open a PR (CODEGEN.PUBLISH_DBT_PR)."""
    plan = _stored_dbt_plan(db, run_id)
    if plan.get("code_repo_id"):
        found = db.query("SELECT ENABLED, COALESCE(USE_FOR_DBT, TRUE) AS USE_FOR_DBT, PROVIDER, NAME FROM CODE.REPO WHERE REPO_ID = %s",
                         (plan["code_repo_id"],))
        if not found or not found[0]["enabled"] or not found[0]["use_for_dbt"]:
            raise HTTPException(409, "The repository this run was generated for is no longer connected or used for dbt. "
                                     "Generate again with a configured repository.")
        if found[0].get("provider") != "GITHUB":
            raise HTTPException(409, f"{found[0]['name']} is not on GitHub; pull requests from the platform are GitHub only, "
                                     "so this run stays stage only.")
    elif not plan.get("origin"):
        raise HTTPException(409, "This run has no repository to publish to. Generate with a repository configured in Admin, Integrations.")
    return _publish_dbt(db, run_id, body.model_dump(exclude_none=True))


def _services_import(db: Db) -> str:
    """IMPORTS of the deployed GENERATE_DBT, so the publisher runs the same code package."""
    for row in db.query("DESC PROCEDURE CODEGEN.GENERATE_DBT(VARCHAR, VARCHAR)"):
        if str(row.get("property") or "").lower() == "imports":
            value = str(row.get("value") or "").strip("[] ")
            if value:
                return value.split(",")[0].strip().strip("'\"")
    raise HTTPException(409, "Deploy the platform first (Admin > Apply) so CODEGEN.GENERATE_DBT exists.")


def _set_config(db: Db, key: str, value: dict, description: str) -> None:
    db.execute("UPDATE CORE.PLATFORM_CONFIG SET IS_CURRENT = FALSE WHERE CONFIG_KEY = %s AND IS_CURRENT", (key,))
    db.execute(
        """
        INSERT INTO CORE.PLATFORM_CONFIG (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION, VERSION, IS_CURRENT, CREATED_BY)
        SELECT %s, PARSE_JSON(%s), %s,
               COALESCE((SELECT MAX(VERSION) FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s), 1) + 1,
               TRUE, CURRENT_USER()
        """,
        (key, json.dumps(value), description, key),
    )


@app.get("/api/dbt/github")
def github_publish_status(db: Db = Depends(current_db)):
    config = db.query(
        "SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'GITHUB_PUBLISH' AND IS_CURRENT"
    )
    value = config[0]["config_value"] if config else None
    value = json.loads(value) if isinstance(value, str) else value
    return {"ready": _github_publisher_ready(db), "config": value or None}


class GithubSetup(BaseModel):
    token: Optional[str] = Field(default=None, max_length=255)
    secret: str = Field(default="CODEGEN.GITHUB_TOKEN", max_length=256)
    external_access_integration: str = Field(default="GDP_GITHUB_ACCESS", max_length=256)


@app.post("/api/dbt/github/setup")
def github_publish_setup(body: GithubSetup, db: Db = Depends(current_db)):
    """One-time admin setup: network rule, secret, external access integration and the publisher procedure."""
    from services.dbt.publish import procedure_sql, setup_sql

    database = DATABASE.upper()
    secret = body.secret.strip().upper()
    secret = secret if secret.count(".") == 2 else f"{database}.{secret}"
    eai = body.external_access_integration.strip().upper()
    token = (body.token or "").strip()
    if token and not re.fullmatch(r"[A-Za-z0-9_\-]{20,255}", token):
        raise HTTPException(400, "That does not look like a GitHub token (ghp_… or github_pat_…).")
    try:
        statements = setup_sql(database, eai, secret, create_secret=bool(token))
        ddl = procedure_sql(database, _services_import(db), eai, secret)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    log: list[dict] = []
    for sql in [*statements, ddl]:
        shown = sql
        run = sql.replace("'<github token>'", "'" + token + "'") if token else sql
        try:
            db.execute(run)
            log.append({"sql": shown, "ok": True})
        except Exception as exc:
            error = (str(exc).replace(token, "***") if token else str(exc))[:500]  # mask first: a cut never splits the token
            log.append({"sql": shown, "ok": False, "error": error})
            return {"ready": False, "log": log,
                    "detail": "A step needs more privileges (CREATE INTEGRATION / CREATE SECRET / CREATE NETWORK RULE). "
                              "Ask an admin to run the SQL shown, or retry with a role that has them."}
    _set_config(db, "GITHUB_PUBLISH", {"secret": secret, "external_access_integration": eai},
                "GitHub publishing for generated dbt branches and pull requests")
    return {"ready": True, "log": log}


class GithubCheck(BaseModel):
    origin: str = Field(min_length=10, max_length=1024)


@app.post("/api/dbt/github/check")
def github_check(body: GithubCheck, db: Db = Depends(current_db)):
    """Read-only: can the stored token see the repository, and does the account have push access?"""
    if not _github_publisher_ready(db):
        return {"status": "NOT_CONFIGURED", "detail": "Set up GitHub publishing first."}
    try:
        return db.call("CALL CODEGEN.PUBLISH_DBT_PR(%s, %s)", ("", json.dumps({"check_only": True, "origin": body.origin})))
    except Exception as exc:
        return {"status": "FAILED", "detail": str(exc)[:600]}


class GithubToken(BaseModel):
    token: str = Field(min_length=20, max_length=255)


@app.post("/api/dbt/github/token")
def github_rotate_token(body: GithubToken, db: Db = Depends(current_db)):
    """Replace the token in the publishing secret. The value goes straight to Snowflake and is never echoed."""
    from services.dbt.publish import NAME

    token = body.token.strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,255}", token):
        raise HTTPException(400, "That does not look like a GitHub token (ghp_… or github_pat_…).")
    config = github_publish_status(db).get("config") or {}
    secret = str(config.get("secret") or "")
    if not NAME.match(secret):
        raise HTTPException(409, "GitHub publishing is not set up yet.")
    try:
        db.execute(f"ALTER SECRET {secret} SET SECRET_STRING = '{token}'")
    except Exception as exc:
        raise HTTPException(400, str(exc).replace(token, "***")[:400]) from None
    return {"rotated": True, "secret": secret}


class GitRepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    origin: str = Field(min_length=10, max_length=1024)
    api_integration: str = Field(min_length=1, max_length=256)
    git_credentials: Optional[str] = Field(default=None, max_length=512)


@app.post("/api/dbt/git-repository")
def create_git_repository(body: GitRepositoryCreate, db: Db = Depends(current_db)):
    """Create a Snowflake git repository clone for an integration that has none yet, then FETCH it."""
    from services.dbt.workspace import quote_exact, safe_fqn

    name = re.sub(r"[^A-Za-z0-9_]", "_", body.name.strip()).upper().strip("_") or "DBT_REPO"
    origin = body.origin.strip()
    if not re.fullmatch(r"https://[A-Za-z0-9.\-]+/[A-Za-z0-9_.\-/]+", origin):
        raise HTTPException(400, "Origin must be an https git URL")
    fqn = f"{DATABASE.upper()}.CODEGEN.{name}"
    creds = f" GIT_CREDENTIALS = {safe_fqn(body.git_credentials)}" if body.git_credentials else ""
    try:
        db.execute(
            f"CREATE GIT REPOSITORY IF NOT EXISTS {fqn} API_INTEGRATION = {quote_exact(body.api_integration)} "
            f"ORIGIN = '{origin}'{creds} COMMENT = 'GDP dbt skeleton source'"
        )
        db.execute(f"ALTER GIT REPOSITORY {fqn} FETCH")
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"git_repository": fqn, "origin": origin, "api_integration": body.api_integration}


@app.get("/api/runs/{run_id}/dbt")
def get_dbt(run_id: str, db: Db = Depends(current_db)):
    gen = db.query(
        """
        SELECT GENERATION_ID, GENERATION_VERSION, GENERATION_STATUS, FILES_GENERATED, STAGE_PATH,
               SKILL_VERSION, MODEL_VERSION, CREATED_AT::VARCHAR AS CREATED_AT
          FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE RUN_ID = %s
         ORDER BY GENERATION_VERSION DESC LIMIT 1
        """,
        (run_id,),
    )
    artifacts = []
    if gen:
        artifacts = db.query(
            """
            SELECT ARTIFACT_ID, ARTIFACT_TYPE, FILE_PATH, CONTENT_SHA256, CONTENT
              FROM CODEGEN.GENERATED_ARTIFACT WHERE GENERATION_ID = %s ORDER BY FILE_PATH
            """,
            (gen[0]["generation_id"],),
        )
    extras: dict = {"branch": None, "skills": None, "workspace": None, "report": None, "skeleton_base": None}
    release = {"release/branch.json": "branch", "release/skills.json": "skills", "release/workspace.json": "workspace",
               "release/generation-report.json": "report", "release/skeleton-base.json": "skeleton_base"}
    for art in artifacts:
        key = release.get(art.get("file_path"))
        if not key:
            continue
        try:
            extras[key] = json.loads(art.get("content") or "{}")
        except ValueError:
            extras[key] = None
    try:
        publication = db.query(
            """
            SELECT STATUS, ORIGIN, BASE_BRANCH, HEAD_BRANCH, COMMIT_SHA, FILES_PUSHED, PR_NUMBER, PR_URL,
                   DBT_PROJECT, DETAIL, GENERATION_ID, CREATED_AT::VARCHAR AS CREATED_AT
              FROM CODEGEN.GIT_PUBLICATION WHERE RUN_ID = %s ORDER BY CREATED_AT DESC LIMIT 1
            """,
            (run_id,),
        )
    except Exception:
        publication = []
    return {"generation": gen[0] if gen else None, "artifacts": artifacts, **extras,
            "publication": publication[0] if publication else None, **_dbt_setup(db, run_id)}


def _dbt_setup(db: Db, run_id: str) -> dict:
    """What the run's dbt workspace needs from Admin's configuration: its repository (or the choices), the default new
    branch from the modeling standard, and whether GitHub publishing is ready."""
    from services.common.standard import conventions_for, run_standard
    from services.dbt.procedures import branch_slug

    run = db.query("SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not run:
        return {}
    plan = _stored_dbt_plan(db, run_id)
    repo, candidates, legacy = _dbt_repo(db, run[0].get("domain_id"), plan)
    try:
        prefix = conventions_for(lambda sql, params: db.query(sql.replace("?", "%s"), tuple(params)),
                                 run_standard(run[0]))["branch_prefix"]
    except Exception:
        prefix = "feat/onboard-"
    return {"repository": repo, "candidates": candidates, "legacy": legacy,
            "legacy_setup": {k: plan.get(k) for k in ("git_repository", "origin", "base_branch")} if legacy else None,
            "default_cut_branch": f"{prefix}{branch_slug(run[0].get('run_name') or '', run_id)}",
            "publishing": {"ready": _github_publisher_ready(db)}}


def _quote_fqn(name: str) -> str:
    """Quote hyphenated Snowflake identifiers so ALTER/SHOW/LIST accept DBT-DEMO."""
    ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
    quoted_ok = re.compile(r"^[A-Za-z0-9_$-]+$")
    parts = []
    for raw in (name or "").split("."):
        part = raw.strip()
        if not part:
            continue
        if part.startswith('"') and part.endswith('"') and len(part) >= 3:
            inner = part[1:-1]
            if '"' in inner or not quoted_ok.match(inner):
                raise ValueError(f"unsafe identifier: {name}")
            parts.append(f'"{inner}"')
        elif ident.match(part):
            parts.append(part)
        elif quoted_ok.match(part):
            parts.append(f'"{part}"')
        else:
            raise ValueError(f"unsafe identifier: {name}")
    if not parts:
        raise ValueError("missing object name")
    return ".".join(parts)


@app.get("/api/runs/{run_id}/dbt/branches")
def get_dbt_branches(run_id: str, repo: str, fetch: bool = True, db: Db = Depends(current_db)):
    from services.dbt.workspace import latest_branch, parse_git_branches, parse_listed_branches

    if not (repo or "").strip():
        raise HTTPException(400, "Pick a Snowflake GIT REPOSITORY first.")
    empty = {
        "run_id": run_id, "repo": repo, "fetched": False, "fetch_warning": "",
        "branches": [], "latest": "main",
    }
    try:
        repo_sql = _quote_fqn(repo)
    except ValueError as exc:
        return {**empty, "fetch_warning": str(exc)}

    warning = ""
    fetched = False
    if fetch:
        try:
            db.execute(f"ALTER GIT REPOSITORY {repo_sql} FETCH")
            fetched = True
        except Exception as exc:
            warning = str(exc)[:400]
    branches: list = []
    for sql in (
        f"SHOW GIT BRANCHES IN GIT REPOSITORY {repo_sql}",
        f"SHOW GIT BRANCHES IN {repo_sql}",
    ):
        try:
            branches = parse_git_branches(db.query(sql) or [])
            if branches:
                break
        except Exception as exc:
            warning = warning or str(exc)[:400]
    if not branches:
        try:
            branches = parse_listed_branches(db.query(f"LIST @{repo_sql}/branches/") or [])
        except Exception as exc:
            warning = warning or str(exc)[:400]
    from services.dbt.workspace import grant_hint
    return {
        "run_id": run_id,
        "repo": repo_sql,
        "fetched": fetched,
        "fetch_warning": warning,
        "grant_sql": grant_hint(warning, db.role) if warning else None,
        "branches": branches,
        "latest": latest_branch(branches),
    }


@app.get("/api/runs/{run_id}/dbt/workspace")
def get_dbt_workspace(run_id: str, git: bool = True, db: Db = Depends(current_db)):
    """Skills and models for the dbt workspace; with git, also the account's Git integrations and clones (slow)."""
    from services.dbt.workspace import discover
    from services.knowledge.usage import STAGE_SKILLS

    workspace = discover(lambda sql: db.query(sql)) if git else {"integrations": [], "git_repositories": [],
                                                                    "dbt_projects": [], "warnings": []}
    try:
        skills = db.query(
            """
            SELECT SKILL_NAME, VERSION, DESCRIPTION, SKILL_TYPE
              FROM KNOWLEDGE.SKILL_REGISTRY
             WHERE IS_CURRENT AND STATUS = 'ACTIVE'
               AND UPPER(REPLACE(SKILL_NAME, '_', '-')) IN ('DBT-ONBOARD-SOURCE', 'SILVER-MODEL', 'GDP-DOMAIN-SKILL')
             ORDER BY SKILL_NAME
            """,
        )
    except Exception:
        skills = []
    if not skills:
        skills = [{"skill_name": n, "version": None, "description": "", "skill_type": "DBT"}
                  for n in STAGE_SKILLS["DBT"]]
    from services.common.models import discover_models
    models = discover_models(lambda sql: db.query(sql))
    return {"run_id": run_id, "skills": skills, "models": models["models"],
            "default_model": models["default"], **workspace}


class DbtReview(BaseModel):
    file_path: str = Field(min_length=1, max_length=400)
    model: Optional[str] = Field(default=None, max_length=120)


def _skill_content(db: Db, name: str) -> str:
    rows = db.query(
        """
        SELECT CONTENT FROM KNOWLEDGE.SKILL_REGISTRY
         WHERE IS_CURRENT AND UPPER(REPLACE(SKILL_NAME, '_', '-')) = %s
         ORDER BY VERSION DESC LIMIT 1
        """,
        (name.upper().replace("_", "-"),),
    )
    return str((rows[0].get("content") if rows else "") or "")


def _domain_skill(db: Db, run_id: str, budget: int = 9000) -> str:
    """GDP dbt skill rules plus the run domain's contract and target model definition."""
    from services.knowledge.usage import DBT_SKILL, compose_domain_context

    content = _skill_content(db, DBT_SKILL)
    try:
        run = db.query(
            """
            SELECT D.DOMAIN_NAME, R.DOMAIN_ID, R.TARGET_MODEL FROM CORE.WORKFLOW_RUN R
              LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
             WHERE R.RUN_ID = %s
            """,
            (run_id,),
        )
    except Exception:
        run = []
    if not run:
        return compose_domain_context(content, budget=budget)
    domain = run[0].get("domain_name")
    target = (str(run[0].get("target_model") or "").split(".")[-1] or None)
    definitions = []
    if target and run[0].get("domain_id"):
        try:
            definitions = [f"{r['title']}: {r['content']}" for r in db.query(
                """
                SELECT TITLE, CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                 WHERE DOMAIN_ID = %s AND IS_CURRENT AND KNOWLEDGE_TYPE = 'MODEL_DEFINITION'
                   AND CONTENT_JSON:target_table::STRING = %s
                """,
                (run[0]["domain_id"], target.upper()),
            )]
        except Exception:
            definitions = []
    return compose_domain_context(content, domain, target, definitions, budget)


def _sttm_context(db: Db, run_id: str) -> str:
    try:
        lines = db.query(
            """
            SELECT L.TARGET_COLUMN, L.TARGET_DATATYPE, L.SOURCE_TABLE, L.SOURCE_COLUMN, L.MAPPING_TYPE, L.TRANSFORMATION
              FROM CONTRACT.STTM_LINE L
              JOIN CONTRACT.STTM_REGISTRY S ON S.STTM_ID = L.STTM_ID
             WHERE S.RUN_ID = %s AND S.STATUS IN ('REVIEW', 'APPROVED')
            QUALIFY DENSE_RANK() OVER (ORDER BY S.STTM_VERSION DESC) = 1
             ORDER BY L.TARGET_COLUMN LIMIT 120
            """,
            (run_id,),
        )
    except Exception:
        lines = []
    return "\n".join(
        f"{r.get('source_table') or '-'}.{r.get('source_column') or '-'} -> {r.get('target_column')} "
        f"{r.get('target_datatype') or ''} [{r.get('mapping_type') or ''}] {r.get('transformation') or ''}"
        for r in lines
    )


@app.post("/api/runs/{run_id}/dbt/review")
def review_dbt(run_id: str, body: DbtReview, db: Db = Depends(current_db)):
    """Cortex review of one generated file against the DBT-ONBOARD-SOURCE skill. Read-only; apply via /enhance."""
    from services.dbt.review import review_file

    gen = db.query(
        "SELECT GENERATION_ID FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE RUN_ID = %s ORDER BY GENERATION_VERSION DESC LIMIT 1",
        (run_id,),
    )
    if not gen:
        raise HTTPException(400, "Generate dbt first, then review a file.")
    files = {r["file_path"]: r.get("content") or "" for r in db.query(
        "SELECT FILE_PATH, CONTENT FROM CODEGEN.GENERATED_ARTIFACT WHERE GENERATION_ID = %s "
        "AND (FILE_PATH = %s OR FILE_PATH = 'release/generation-report.json')",
        (gen[0]["generation_id"], body.file_path.strip()),
    )}
    path = body.file_path.strip()
    if path not in files:
        raise HTTPException(404, f"No generated file {path}")
    notes = files.get("release/generation-report.json", "")
    try:
        report = json.loads(notes) if notes else {}
        notes = json.dumps({k: report.get(k) for k in ("source_unique_id", "dedup_order", "anomalies", "hub", "counts")})
    except ValueError:
        pass
    from app.code_api import run_code_context

    code = run_code_context(db, run_id, "DBT", question=path)
    skill = _domain_skill(db, run_id) + (f"\n\n{code['text']}" if code["text"] else "")
    started = time.time()
    try:
        fetch, ids = _fetch_with_ids(db)
        reviewed = review_file(fetch, path, files[path], skill, _sttm_context(db, run_id), notes,
                               model=body.model or _model_for(db, "DBT"))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    reviewed["code_citations"] = code["citations"]
    _record_cost(db, run_id, "DBT", reviewed.get("model"),
                 {**(reviewed.get("usage") or {}), "query_id": ids[-1] if ids else None}, started)
    return reviewed


@app.post("/api/runs/{run_id}/dbt/enhance")
def enhance_dbt(run_id: str, body: DbtEnhance, db: Db = Depends(current_db)):
    import hashlib
    from services.dbt.enhance import enhance_file

    gen = db.query(
        """
        SELECT GENERATION_ID, GENERATION_STATUS FROM CODEGEN.DBT_GENERATION_REGISTRY
         WHERE RUN_ID = %s ORDER BY GENERATION_VERSION DESC LIMIT 1
        """,
        (run_id,),
    )
    if not gen:
        raise HTTPException(400, "Generate dbt first, then enhance a file.")
    generation_id = gen[0]["generation_id"]
    path = body.file_path.strip()
    if body.apply:
        if not (body.content or "").strip():
            raise HTTPException(400, "Apply needs the previewed file content.")
        content = body.content
        if not db.query("SELECT 1 AS X FROM CODEGEN.GENERATED_ARTIFACT WHERE GENERATION_ID = %s AND FILE_PATH = %s",
                        (generation_id, path)):
            raise HTTPException(404, f"No generated file {path}")
        if (gen[0].get("generation_status") or "").upper() == "APPROVED":
            raise HTTPException(409, "This generation is approved; regenerate dbt to change its files.")
        if db.query("SELECT 1 AS X FROM CODEGEN.GIT_PUBLICATION WHERE GENERATION_ID = %s AND STATUS = 'PUBLISHED' LIMIT 1",
                    (generation_id,)):
            raise HTTPException(409, "This generation is already published to Git; regenerate dbt to change its files.")
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        db.execute(
            "UPDATE CODEGEN.GENERATED_ARTIFACT SET CONTENT = %s, CONTENT_SHA256 = %s "
            "WHERE GENERATION_ID = %s AND FILE_PATH = %s",
            (content, sha, generation_id, path),
        )
        return {"applied": True, "file_path": path, "content": content}

    rows = db.query(
        "SELECT CONTENT FROM CODEGEN.GENERATED_ARTIFACT WHERE GENERATION_ID = %s AND FILE_PATH = %s",
        (generation_id, path),
    )
    if not rows:
        raise HTTPException(404, f"No generated file {path}")
    if not (body.prompt or "").strip():
        raise HTTPException(400, "Describe the change you want Cortex to make.")
    try:
        sttm = db.query(
            """
            SELECT L.TARGET_COLUMN, L.SOURCE_TABLE, L.SOURCE_COLUMN, L.TRANSFORMATION
              FROM CONTRACT.STTM_LINE L
              JOIN CONTRACT.STTM_REGISTRY S ON S.STTM_ID = L.STTM_ID
             WHERE S.RUN_ID = %s AND S.STATUS IN ('REVIEW', 'APPROVED')
             ORDER BY S.STTM_VERSION DESC, L.TARGET_COLUMN LIMIT 40
            """,
            (run_id,),
        )
    except Exception:
        sttm = []
    context = "\n".join(
        f"{r.get('source_table')}.{r.get('source_column')} -> {r.get('target_column')}: {r.get('transformation') or ''}"
        for r in sttm
    )
    try:
        from services.dbt.review import skill_excerpt
        skill = skill_excerpt(_domain_skill(db, run_id, 5000), 5000)
    except Exception:
        skill = ""
    if skill:
        context = f"{context}\n\nFOLLOW THESE GDP-DBT-ONBOARD-SOURCE RULES AND DOMAIN CONTRACT:\n{skill}"
    from app.code_api import run_code_context

    code = run_code_context(db, run_id, "DBT", question=f"{path} {body.prompt.strip()[:300]}")
    if code["text"]:
        context = f"{context}\n\n{code['text']}"
    started = time.time()
    try:
        fetch, ids = _fetch_with_ids(db)
        enhanced = enhance_file(
            fetch,
            path,
            rows[0]["content"] or "",
            body.prompt.strip(),
            model=body.model or _model_for(db, "DBT"),
            context=context,
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    _record_cost(db, run_id, "DBT", enhanced.get("model"),
                 {**(enhanced.get("usage") or {}), "query_id": ids[-1] if ids else None}, started)
    return enhanced


@app.post("/api/runs/{run_id}/validation")
def validate_dbt(run_id: str, db: Db = Depends(current_db)):
    try:
        return db.call("CALL CODEGEN.VALIDATE_DBT(%s)", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/runs/{run_id}/validation")
def get_validation(run_id: str, db: Db = Depends(current_db)):
    return {"runs": db.query(
        """
        SELECT VALIDATION_ID, VALIDATION_TYPE, STATUS, ERROR_COUNT, WARNING_COUNT, RESULT_JSON,
               STARTED_AT::VARCHAR AS STARTED_AT, COMPLETED_AT::VARCHAR AS COMPLETED_AT
          FROM CODEGEN.VALIDATION_RUN WHERE RUN_ID = %s
         ORDER BY STARTED_AT DESC
        """,
        (run_id,),
    )}


@app.post("/api/knowledge/search")
def search_knowledge(body: KnowledgeQuery, db: Db = Depends(current_db)):
    try:
        return db.call("CALL KNOWLEDGE.SEARCH_KNOWLEDGE(%s, %s, %s, %s)",
                       (body.query, body.domain, body.knowledge_type, body.limit))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


class CopilotAsk(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    page: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    conversation_id: Optional[str] = Field(default=None, max_length=36)


@app.get("/api/copilot/suggestions")
def copilot_suggestions(path: str = "/", database: str = "", schema: str = "", table: str = ""):
    """Starter questions for the page (no model call)."""
    from services.common.copilot import parse_page, suggestions

    page = parse_page({"path": path, "database": database, "schema": schema, "table": table})
    return {"page": page, "suggestions": suggestions(page)}


@app.post("/api/copilot/ask")
def copilot_ask(body: CopilotAsk, db: Db = Depends(current_db)):
    """Answer about the current page: its run, profiles, checks and tests, plus domain knowledge; cited."""
    from services.common.copilot import ask

    started = time.time()
    history = [{"role": str(m.get("role")), "content": str(m.get("content") or "")[:4000]} for m in body.history]
    try:
        result = invoke_source(db, ask, json.dumps(body.page), body.question, json.dumps(history), body.conversation_id)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    run_id = (body.page.get("path") or "").split("/")[2] if str(body.page.get("path") or "").startswith("/runs/") else None
    _record_cost(db, run_id if run_id and len(run_id) == 36 else None, "COPILOT", result.get("model"),
                 result.pop("usage", None), started)
    return result


@app.post("/api/agent/stream")
def agent_stream(body: AgentMessage, db: Db = Depends(current_db)):
    return StreamingResponse(stream_agent(db, body.run_id, body.message), media_type="text/event-stream")


# ---------------------------------------------------------------- Sources hub: in-place profiling and profile store

_jobs_lock = threading.Lock()
_profile_jobs: dict[str, dict] = {}


def _profile_source(db: Db, source_id: str, tables: list[str], force: bool) -> dict:
    from services.profiling.procedures import profile_source_tables as profile_source_handler

    payload = json.dumps({"tables": tables, "force_refresh": force})
    return _source_call(db, "CALL SOURCE.PROFILE_SOURCE_TABLES(%s, %s)", profile_source_handler, source_id, payload)


def _prewarm_run_profiles(db: Db, run_id: str, force: bool = False) -> None:
    """Profile the run's tables in place with the caller's role first, so RUN_PROFILING binds cached
    profiles instead of reading data under the procedure owner. Best effort: on failure the procedure profiles."""
    try:
        run = db.query("SELECT SOURCE_SYSTEM_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
        tables = [r["source_table"] for r in db.query(
            "SELECT DISTINCT SOURCE_TABLE FROM SOURCE.LANDING_TABLE_REGISTRY WHERE RUN_ID = %s "
            "AND INGESTION_STATUS = 'COMPLETE'", (run_id,))]
        if run and run[0]["source_system_id"] and tables:
            _profile_source(db, run[0]["source_system_id"], tables, force)
    except Exception:
        pass


def _source_row(db: Db, source_id: str) -> dict:
    found = db.query(
        """
        SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, SOURCE_TYPE, OWNER, DOMAIN_ID,
               CONFIGURATION_JSON:database::VARCHAR AS DATABASE_NAME, CONFIGURATION_JSON:schema::VARCHAR AS SCHEMA_NAME,
               CREATED_AT::VARCHAR AS CREATED_AT
          FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = %s AND ACTIVE_FLAG
        """,
        (source_id,),
    )
    if not found:
        raise HTTPException(404, "source not found")
    return found[0]


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _source_tables(db: Db, src: dict) -> list[dict]:
    """Objects of the source schema with the fingerprint the profiler uses to decide freshness."""
    from services.profiling.profiler import source_fingerprint
    from services.source.identifiers import format_data_type

    from concurrent.futures import ThreadPoolExecutor

    database = _quote_ident(src["database_name"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        tables_future = pool.submit(db.query, f"""
        SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, BYTES, LAST_ALTERED::VARCHAR AS LAST_ALTERED
          FROM {database}.INFORMATION_SCHEMA.TABLES
         WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ('BASE TABLE', 'VIEW', 'MATERIALIZED VIEW')
         ORDER BY TABLE_NAME LIMIT 1000
        """, (src["schema_name"],))
        columns_future = pool.submit(db.query, f"""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE
          FROM {database}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s
         ORDER BY TABLE_NAME, ORDINAL_POSITION
        """, (src["schema_name"],))
        tables = tables_future.result()
        column_rows = columns_future.result()
    columns: dict[str, list[tuple[str, str]]] = {}
    for c in column_rows:
        columns.setdefault(c["table_name"], []).append((c["column_name"], format_data_type(
            c["data_type"], c["character_maximum_length"], c["numeric_precision"], c["numeric_scale"])))
    for t in tables:
        cols = columns.get(t["table_name"], [])
        t["column_count"] = len(cols)
        t["column_names"] = [c[0] for c in cols]
        t["fingerprint"] = source_fingerprint(cols, t["row_count"], t["last_altered"])
    return tables


def _store_rows(db: Db, source_names: Optional[list[str]] = None) -> list[dict]:
    where, params = ("", ())
    if source_names:
        where, params = ("WHERE ARRAY_CONTAINS(SOURCE_NAME::VARIANT, PARSE_JSON(%s)::ARRAY)", (json.dumps(source_names),))
    try:
        return db.query(
            f"""
            SELECT SOURCE_NAME, DATABASE_NAME, SCHEMA_NAME, TABLE_NAME, ROW_COUNT, COLUMN_COUNT, PROFILE_STAGE_PATH,
                   SOURCE_FINGERPRINT, IS_APPROXIMATE, PROFILED_IN_RUN, PROFILED_BY, PROFILED_AT::VARCHAR AS PROFILED_AT,
                   STATUS, STATUS_UPDATED_AT::VARCHAR AS STATUS_UPDATED_AT, ERROR_MESSAGE, AVG_NULL_PERCENTAGE,
                   KEY_CANDIDATES, PII_COLUMNS
              FROM METADATA.TABLE_PROFILES {where}
            """,
            params,
        )
    except Exception:
        return []


def _table_status(entry: Optional[dict], fingerprint: str) -> str:
    if not entry:
        return "UNPROFILED"
    status = entry.get("status") or "STAGED_READY_FOR_MODELING"
    if status in ("PROFILING", "FAILED"):
        return status
    if not entry.get("source_fingerprint"):
        return "UNPROFILED"
    return "STAGED_READY_FOR_MODELING" if entry["source_fingerprint"] == fingerprint else "STALE"


def _active_jobs(source_id: Optional[str] = None) -> list[dict]:
    with _jobs_lock:
        return [dict(j) for j in _profile_jobs.values()
                if j["status"] == "RUNNING" and (source_id is None or j["source_id"] == source_id)]


_HEALTH_TTL = 300.0
_health_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _schema_table_counts(db: Db, sources: list[dict]) -> dict[tuple[str, str], tuple[Optional[int], Optional[str]]]:
    """Table count per (database, schema): one INFORMATION_SCHEMA query per database (not per source), run in
    parallel and cached for a few minutes. A database the role cannot read maps to (None, error)."""
    from concurrent.futures import ThreadPoolExecutor

    user = getattr(db, "user", "") or ""
    wanted: dict[str, set[str]] = {}
    out: dict[tuple[str, str], tuple[Optional[int], Optional[str]]] = {}
    now = time.time()
    for s in sources:
        key = (s["database_name"], s["schema_name"])
        hit = _health_cache.get((user,) + key)  # type: ignore[arg-type]
        if hit and now - hit[0] < _HEALTH_TTL:
            out[key] = (hit[1]["count"], hit[1]["error"])
        else:
            wanted.setdefault(s["database_name"], set()).add(s["schema_name"])

    def count(database: str, schemas: set[str]):
        try:
            marks = ", ".join(["%s"] * len(schemas))
            found = db.query(
                f"SELECT TABLE_SCHEMA, COUNT(*) AS N FROM {_quote_ident(database)}.INFORMATION_SCHEMA.TABLES "
                f"WHERE TABLE_SCHEMA IN ({marks}) AND TABLE_TYPE IN ('BASE TABLE', 'VIEW', 'MATERIALIZED VIEW') "
                "GROUP BY TABLE_SCHEMA",
                tuple(sorted(schemas)),
            )
            counts = {r["table_schema"]: int(r["n"]) for r in found}
            return database, {sc: (counts.get(sc, 0), None) for sc in schemas}
        except Exception as exc:
            error = str(_snowflake_error(exc).detail)[:300]
            return database, {sc: (None, error) for sc in schemas}

    if wanted:
        with ThreadPoolExecutor(max_workers=min(8, len(wanted))) as pool:
            for database, per_schema in pool.map(lambda kv: count(*kv), wanted.items()):
                for schema, value in per_schema.items():
                    out[(database, schema)] = value
                    _health_cache[(user, database, schema)] = (time.time(), {"count": value[0], "error": value[1]})  # type: ignore[index]
    return out


@app.get("/api/sources/overview")
def sources_overview(fresh: bool = False, db: Db = Depends(current_db)):
    """Every registered source with health, inventory size and profile-store coverage."""
    sources = source_connections(db)["sources"]
    by_source: dict[str, list[dict]] = {}
    for row in _store_rows(db):
        by_source.setdefault(row["source_name"], []).append(row)
    if fresh:
        _health_cache.clear()
    counts = _schema_table_counts(db, sources)
    out = []
    for s in sources:
        health, detail = "HEALTHY", ""
        table_count, error = counts.get((s["database_name"], s["schema_name"]), (None, "not checked"))
        if error is not None:
            external = str(s["source_type"]).startswith("EXTERNAL_")
            health = "NOT_LANDED" if external else "UNREACHABLE"
            detail = "Not landed into Snowflake yet" if external else error
        rows = [r for r in by_source.get(s["source_system_name"], [])
                if r["database_name"] == s["database_name"] and r["schema_name"] == s["schema_name"]]
        staged = [r for r in rows if (r.get("status") or "STAGED_READY_FOR_MODELING") == "STAGED_READY_FOR_MODELING"
                  and r.get("source_fingerprint")]
        if s.get("oracle"):
            s["oracle"] = _json(s["oracle"]) if isinstance(s["oracle"], str) else s["oracle"]
            with _jobs_lock:
                job_id = _oracle_running.get(s["source_system_id"])
                job = _ingest_jobs.get(job_id) if job_id else None
            s["oracle"]["running"] = {"kind": job["kind"], "tables": len(job["tables"]),
                                      "done": sum(1 for t in job["tables"].values()
                                                  if t.get("phase") in ("DONE", "FAILED", "CANCELLED"))} \
                if job and job["status"] == "RUNNING" else None
        out.append({
            **s, "health": health, "health_detail": detail, "table_count": table_count,
            "staged_tables": len(staged), "profiling_tables": sum(r.get("status") == "PROFILING" for r in rows),
            "failed_tables": sum(r.get("status") == "FAILED" for r in rows),
            "last_profiled_at": max((r["profiled_at"] for r in staged), default=None),
            "active_jobs": len(_active_jobs(s["source_system_id"])),
        })
    return {
        "sources": out,
        "totals": {
            "sources": len(out),
            "tables": sum(s["table_count"] or 0 for s in out),
            "staged": sum(s["staged_tables"] for s in out),
            "profiling": sum(s["profiling_tables"] for s in out),
        },
    }


@app.get("/api/sources/{source_id}/inventory")
def source_inventory(source_id: str, db: Db = Depends(current_db)):
    """Tables of one source with profile status: UNPROFILED, PROFILING, STAGED_READY_FOR_MODELING,
    STALE (the source changed since it was profiled) or FAILED."""
    src = _source_row(db, source_id)
    try:
        tables = _source_tables(db, src)
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    store = {r["table_name"]: r for r in _store_rows(db, [src["source_system_name"]])
             if r["database_name"] == src["database_name"] and r["schema_name"] == src["schema_name"]}
    domains: dict[str, str] = {}
    try:
        for r in db.query(
            """
            SELECT O.OBJECT_NAME, D.DOMAIN_NAME
              FROM SOURCE.SOURCE_OBJECT O
              JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = O.RUN_ID
              JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
             WHERE O.SOURCE_SYSTEM_ID = %s AND O.SELECTED_FLAG
           QUALIFY ROW_NUMBER() OVER (PARTITION BY O.OBJECT_NAME ORDER BY O.DISCOVERED_AT DESC) = 1
            """,
            (source_id,),
        ):
            domains[r["object_name"]] = r["domain_name"]
    except Exception:
        pass
    inferred, _ = _inferred_table_domains(db, tables, domains, src["schema_name"])
    inventory = []
    for t in tables:
        entry = store.get(t["table_name"])
        status = _table_status(entry, t["fingerprint"])
        staged = bool(entry and entry.get("source_fingerprint"))
        inventory.append({
            "table_name": t["table_name"], "table_type": t["table_type"], "row_count": t["row_count"],
            "bytes": t["bytes"], "column_count": t["column_count"], "last_altered": t["last_altered"],
            "status": status,
            "domain_name": domains.get(t["table_name"]) or (inferred.get(t["table_name"]) or {}).get("domain_name"),
            "domain_inferred": t["table_name"] not in domains and t["table_name"] in inferred,
            "domain_confidence": (inferred.get(t["table_name"]) or {}).get("confidence"),
            "stage_path": entry["profile_stage_path"] if staged else None,
            "profiled_at": entry["profiled_at"] if staged else None,
            "profiled_by": entry.get("profiled_by") if staged else None,
            "avg_null_percentage": entry.get("avg_null_percentage") if staged else None,
            "key_candidates": entry.get("key_candidates") if staged else None,
            "pii_columns": entry.get("pii_columns") if staged else None,
            "is_approximate": entry.get("is_approximate") if staged else None,
            "error_message": entry.get("error_message") if entry and status == "FAILED" else None,
        })
    return {"source": src, "tables": inventory, "jobs": _active_jobs(source_id)}


@app.get("/api/sources/{source_id}/profiles/{table}")
def source_table_profile(source_id: str, table: str, db: Db = Depends(current_db)):
    """The staged profile document of one table, read from @METADATA.PROFILES_STAGE."""
    from services.profiling.profiler import STAGE_PATH

    src = _source_row(db, source_id)
    entry = next((r for r in _store_rows(db, [src["source_system_name"]])
                  if r["table_name"] == table and r["database_name"] == src["database_name"]
                  and r["schema_name"] == src["schema_name"]), None)
    if not entry or not entry.get("source_fingerprint") or not STAGE_PATH.match(entry["profile_stage_path"] or ""):
        raise HTTPException(404, f"{table} has no staged profile yet")
    found = db.query(f"SELECT $1 AS DOC FROM @METADATA.PROFILES_STAGE/{entry['profile_stage_path']} "
                     "(FILE_FORMAT => 'METADATA.PROFILE_JSON_FORMAT')")
    if not found:
        raise HTTPException(404, f"the staged file for {table} is missing; re-profile it")
    doc = found[0]["doc"]
    return {"entry": entry, "profile": json.loads(doc) if isinstance(doc, str) else doc}


class SourceProfileRequest(BaseModel):
    tables: list[str] = Field(min_length=1, max_length=500)
    force_refresh: bool = False
    wait: bool = False


@app.post("/api/sources/{source_id}/profile-tables")
def profile_source_tables(source_id: str, body: SourceProfileRequest, db: Db = Depends(current_db)):
    """Profile exactly the given tables in place (no copy). Runs in the background unless `wait` is set."""
    _source_row(db, source_id)
    tables = sorted(set(body.tables))
    if body.wait:
        try:
            return {"status": "DONE", "result": _profile_source(db, source_id, tables, body.force_refresh)}
        except Exception as exc:
            raise _snowflake_error(exc) from exc
    job = {"job_id": str(uuid.uuid4()), "source_id": source_id, "tables": tables, "force_refresh": body.force_refresh,
           "status": "RUNNING", "started_at": time.time(), "finished_at": None, "result": None, "error": None}

    def work() -> None:
        try:
            job.update(status="DONE", result=_profile_source(db, source_id, tables, body.force_refresh))
        except Exception as exc:
            job.update(status="FAILED", error=str(_snowflake_error(exc).detail))
        job["finished_at"] = time.time()

    with _jobs_lock:  # check and register under one lock, so two requests cannot both start the same tables
        busy = {t for j in _profile_jobs.values() if j["status"] == "RUNNING" and j["source_id"] == source_id
                for t in j["tables"]} & set(tables)
        if busy:
            raise HTTPException(409, f"already profiling: {', '.join(sorted(busy)[:5])}")
        _profile_jobs[job["job_id"]] = job
    threading.Thread(target=work, name=f"profile-{job['job_id'][:8]}", daemon=True).start()
    return {"status": "QUEUED", "job_id": job["job_id"], "tables": tables}


@app.get("/api/profile-jobs/{job_id}")
def profile_job(job_id: str, db: Db = Depends(current_db)):
    with _jobs_lock:
        job = _profile_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return dict(job)


class SourceConnectionCreate(BaseModel):
    source_system_name: Optional[str] = Field(default=None, max_length=64)
    source_type: str = Field(default="SNOWFLAKE_DATABASE", pattern=r"^(SNOWFLAKE_DATABASE|SNOWFLAKE_SHARE)$")
    database: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(min_length=1, max_length=255, alias="schema")
    domain_id: Optional[str] = None


@app.post("/api/sources")
def create_source_connection(body: SourceConnectionCreate, db: Db = Depends(current_db)):
    from services.source.procedures import register_connection

    name = body.source_system_name or re.sub(r"[^A-Za-z0-9_]", "_", body.schema_name).strip("_") or "SOURCE"
    if not re.match(r"^[A-Za-z]", name):
        name = f"SRC_{name}"
    payload = json.dumps({k: v for k, v in {
        "SOURCE_SYSTEM_NAME": name[:64], "SOURCE_TYPE": body.source_type, "DATABASE": body.database,
        "SCHEMA": body.schema_name, "DOMAIN_ID": body.domain_id}.items() if v})
    try:
        return _source_call(db, "CALL SOURCE.REGISTER_CONNECTION(%s)", register_connection, payload)
    except Exception as exc:
        raise _snowflake_error(exc) from exc


class ModelTarget(BaseModel):
    fqn: str = Field(min_length=1, max_length=768)
    target_table: str = Field(min_length=1, max_length=255)
    domain_name: Optional[str] = None
    target_table_id: Optional[str] = None


class ModelingRunRequest(BaseModel):
    tables: list[str] = Field(min_length=1, max_length=500)
    run_name: Optional[str] = Field(default=None, max_length=256)
    domain_id: Optional[str] = None
    targets: list[ModelTarget] = Field(default_factory=list, max_length=50)
    proposed_name: Optional[str] = Field(default=None, max_length=255)
    proposed_schema: Optional[str] = Field(default=None, max_length=255)
    modeling_standard: Literal["GDP", "GENERIC"] = "GENERIC"


def _proposed_name(tables: list[str]) -> str:
    from services.source.intent import proposed_model_name

    return proposed_model_name(tables)


def _register_proposed_target(db: Db, src: dict, tables: list[str], domain_id: Optional[str],
                              name: Optional[str], schema: Optional[str]) -> dict:
    """'Propose a new model' becomes a real target: registered from the selected tables' own columns and types,
    so mapping has something concrete to map onto instead of falling back to an unrelated model. Nothing is
    created in Snowflake; the dbt stage builds it."""
    database = src["database_name"]
    rows_ = db.query(
        f"""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION, IS_NULLABLE, COMMENT,
               CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE
          FROM {_quote_ident(database)}.INFORMATION_SCHEMA.COLUMNS
         WHERE TABLE_SCHEMA = %s AND ARRAY_CONTAINS(TABLE_NAME::VARIANT, PARSE_JSON(%s)::ARRAY)
         ORDER BY TABLE_NAME, ORDINAL_POSITION
        """,
        (src["schema_name"], json.dumps(tables)),
    )
    order = {t: i for i, t in enumerate(tables)}
    rows_.sort(key=lambda r: (order.get(r["table_name"], 99), r["ordinal_position"]))
    seen: set[str] = set()
    columns = []
    for r in rows_:
        if r["column_name"] in seen:
            continue
        seen.add(r["column_name"])
        columns.append({"column_name": r["column_name"], "data_type": _column_type(r),
                        "ordinal_position": len(columns) + 1, "nullable": True,
                        "comment": r["comment"] or f"From {r['table_name']}.{r['column_name']}", "business_key": False})
    if not columns:
        raise HTTPException(400, "The selected tables have no visible columns to propose a model from.")
    try:
        pk = {r.get("column_name") for r in db.query(
            f"SHOW PRIMARY KEYS IN TABLE {_quote_ident(database)}.{_quote_ident(src['schema_name'])}.{_quote_ident(tables[0])}")}
    except Exception:
        pk = set()
    for c in columns:
        c["business_key"] = c["column_name"] in pk
    payload = {"database": database, "schema": schema or "SILVER", "table": name or _proposed_name(tables),
               "columns": columns, "domain_id": domain_id}
    try:
        registered = db.call("CALL KNOWLEDGE.REGISTER_TARGET_TABLE(%s)", (json.dumps(payload),))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"fqn": registered["target_model"], "target_table": payload["table"],
            "target_table_id": registered["target_table_id"], "domain_id": registered.get("domain_id")}


@app.post("/api/sources/{source_id}/modeling-run")
def send_to_modeling(source_id: str, body: ModelingRunRequest, db: Db = Depends(current_db)):
    """Create a run from staged tables: register, validate access and bind the tables in place (no copy),
    then bind their cached profiles. The run opens at mapping without re-profiling or re-ingesting."""
    src = _source_row(db, source_id)
    tables = sorted(set(body.tables))
    run_name = body.run_name or f"{src['source_system_name']} modeling {time.strftime('%Y-%m-%d %H:%M')}"
    targets = [t.model_dump() for t in body.targets]
    proposed = None
    if not targets:
        proposed = _register_proposed_target(db, src, tables, body.domain_id, body.proposed_name, body.proposed_schema)
    intent = {
        "path": "map_existing" if targets else "profile_suggest", "run_name": run_name,
        "proposed_target": proposed,
        "model_existing": bool(targets), "targets": targets,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "domain_id": body.domain_id,
        "modeling_standard": body.modeling_standard,
        "source": {"origin": "snowflake", "connection_id": source_id, "database": src["database_name"],
                   "schema": src["schema_name"], "source_system_name": src["source_system_name"],
                   "source_type": src["source_type"], "tables": tables},
        "target": {"storage_type": "IN_PLACE"},
    }
    created = create_run(CreateRun(run_name=run_name, domain_id=body.domain_id or (proposed or {}).get("domain_id"),
                                   target_model=targets[0]["fqn"] if targets else proposed["fqn"], intent=intent,
                                   modeling_standard=body.modeling_standard), db)
    run_id = created.get("run_id")
    if created.get("registration_error"):
        return {"run_id": run_id, "stage": "SOURCE", "error": created["registration_error"]}
    prepared = prepare_source(run_id, AccessRequest(selected=tables), db)
    if not prepared.get("passed"):
        return {"run_id": run_id, "stage": "SOURCE", "error": "access check failed; open the run to see which table"}
    try:
        profiled = run_profiling(run_id, None, db)
    except HTTPException as exc:
        return {"run_id": run_id, "stage": "PROFILING", "error": exc.detail}
    summary = (profiled or {}).get("profiled") or {}
    return {"run_id": run_id, "stage": "MAPPING", "cache_hits": summary.get("cache_hits"),
            "computed": summary.get("computed"), "state": (profiled or {}).get("state")}


# ---------------------------------------------------------------- Catalog-first profiling: any database, any schema


def _catalog_store_rows(db: Db, database: str, schema: str) -> dict[str, dict]:
    """Latest profile-store row per table of database.schema, whichever source name profiled it."""
    try:
        found = db.query(
            """
            SELECT SOURCE_NAME, DATABASE_NAME, SCHEMA_NAME, TABLE_NAME, ROW_COUNT, COLUMN_COUNT, PROFILE_STAGE_PATH,
                   SOURCE_FINGERPRINT, IS_APPROXIMATE, PROFILED_IN_RUN, PROFILED_BY, PROFILED_AT::VARCHAR AS PROFILED_AT,
                   STATUS, STATUS_UPDATED_AT::VARCHAR AS STATUS_UPDATED_AT, ERROR_MESSAGE, AVG_NULL_PERCENTAGE,
                   KEY_CANDIDATES, PII_COLUMNS, QUALITY_JSON
              FROM METADATA.TABLE_PROFILES
             WHERE DATABASE_NAME = %s AND SCHEMA_NAME = %s
           QUALIFY ROW_NUMBER() OVER (PARTITION BY TABLE_NAME
                                      ORDER BY IFF(STATUS = 'PROFILING', 0, 1), STATUS_UPDATED_AT DESC NULLS LAST) = 1
            """,
            (database, schema),
        )
    except Exception:
        return {}
    return {r["table_name"]: r for r in found}


def _registered_source(db: Db, database: str, schema: str) -> Optional[dict]:
    found = db.query(
        """
        SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, SOURCE_TYPE,
               CONFIGURATION_JSON:database::VARCHAR AS DATABASE_NAME, CONFIGURATION_JSON:schema::VARCHAR AS SCHEMA_NAME
          FROM SOURCE.SOURCE_REGISTRY
         WHERE ACTIVE_FLAG AND CONFIGURATION_JSON:database::VARCHAR = %s AND CONFIGURATION_JSON:schema::VARCHAR = %s
         ORDER BY CREATED_AT LIMIT 1
        """,
        (database, schema),
    )
    return found[0] if found else None


_DOMAIN_VOCAB: dict = {"at": 0.0, "domains": []}
_RULES_CACHE: dict = {}


def _rules(db: Db, domain_id: Optional[str] = None) -> dict:
    """Thresholds and name hints (services.common.rules) for the API, cached for a minute per domain, and made
    active for the rest of this request so shared helpers (insights, inference) read the same values."""
    from services.common.rules import activate, load_rules

    key = domain_id or ""
    hit = _RULES_CACHE.get(key)
    if not hit or time.time() - hit[0] > 60:
        try:
            from services.source.catalog_display import configure

            found = db.query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG "
                             "WHERE CONFIG_KEY = 'CATALOG_DISPLAY' AND IS_CURRENT")
            if found:
                configure(_json(found[0].get("config_value")))
        except Exception:
            pass
        hit = (time.time(), load_rules(lambda sql, params: db.query(sql.replace("?", "%s"), tuple(params)),
                                       domain_id))
        _RULES_CACHE[key] = hit
    activate(hit[1])
    return hit[1]


def _ui_bands(db: Db, run_id: Optional[str] = None) -> dict:
    """Confidence bands for the pages, from the rules of the run's domain (platform rules without a run)."""
    from services.common.rules import DEFAULTS, ui_bands

    try:
        domain = None
        if run_id:
            found = db.query("SELECT DOMAIN_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
            domain = found[0]["domain_id"] if found else None
        return ui_bands(_rules(db, domain))
    except Exception:
        return ui_bands(DEFAULTS)


def _min_confidence(db: Db, kind: str = "inferred") -> float:
    return float(_rules(db)[f"domain.{kind}_min_confidence"])


def _domain_vocab(db: Db) -> list[dict]:
    """Detection vocabulary per domain (signals, glossary synonyms, target columns), cached for a minute."""
    from services.knowledge.terms import entity_tokens, token_set

    if time.time() - _DOMAIN_VOCAB["at"] < 60 and _DOMAIN_VOCAB["domains"]:
        return _DOMAIN_VOCAB["domains"]
    try:
        registry = db.query("SELECT DOMAIN_ID, DOMAIN_NAME, CONFIG FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG")
    except Exception:
        registry = db.query("SELECT DOMAIN_ID, DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG")
    terms: dict[str, set] = {d["domain_id"]: set() for d in registry}
    for k in db.query("SELECT DOMAIN_ID, CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE IS_CURRENT "
                      "AND STATUS = 'ACTIVE' AND KNOWLEDGE_TYPE = 'GLOSSARY'"):
        content = _json(k.get("content_json")) or {}
        for word in (content.get("synonyms") or []) + [content.get("target_column") or ""]:
            terms.setdefault(k["domain_id"], set()).update(token_set(word))
    for c in db.query("""SELECT T.DOMAIN_ID, C.COLUMN_NAME, T.TARGET_TABLE FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY C
                           JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = C.TARGET_TABLE_ID
                          WHERE T.ACTIVE_FLAG"""):
        terms.setdefault(c["domain_id"], set()).update(token_set(c["column_name"]) | entity_tokens(c["target_table"]))
    out = [{"domain_id": d["domain_id"], "name": d["domain_name"], "terms": terms.get(d["domain_id"], set()),
            "signals": (_json(d.get("config")) or {}).get("signals"),
            "standard": (_json(d.get("config")) or {}).get("standard")} for d in registry]
    _DOMAIN_VOCAB.update(at=time.time(), domains=out)
    return out


def _infer_domains(db: Db, tables: list[str], columns: list[str], schema: Optional[str] = None) -> list[dict]:
    """Ranked domain candidates for a set of source tables. Only domains with detection signals compete, so the
    generic GDP pack never claims an unrelated table."""
    from services.knowledge.domain import infer_domain

    try:
        vocab = [d for d in _domain_vocab(db) if d.get("signals")]
    except Exception:
        return []
    names = list(tables) + ([schema] if schema else [])
    standards = {d["domain_id"]: d.get("standard") for d in vocab}
    return [{"domain_id": r["domain_id"], "domain_name": r["domain_name"], "confidence": r["confidence"],
             "standard": standards.get(r["domain_id"]),
             "signals": r["evidence"]["signals"][:6], "matched_terms": r["evidence"]["matched_terms"][:10]}
            for r in infer_domain(names, columns, vocab)]


def _inferred_table_domains(db: Db, tables: list[dict], known: dict[str, str],
                            schema: Optional[str]) -> tuple[dict[str, dict], list[dict]]:
    """Per-table inferred domain for tables no run has classified yet, plus the schema-level candidates.
    A single table carries few signals, so it inherits the schema's lead domain when it hits that domain's signals."""
    schema_domain = _infer_domains(db, [t["table_name"] for t in tables],
                                   [c for t in tables for c in t.get("column_names") or []], schema)[:3]
    lead = schema_domain[0] if schema_domain and schema_domain[0]["confidence"] >= _min_confidence(db) else None
    inferred: dict[str, dict] = {}
    for t in tables:
        if t["table_name"] in known:
            continue
        ranked = _infer_domains(db, [t["table_name"]], t.get("column_names") or [])
        own = next((r for r in ranked if lead and r["domain_id"] == lead["domain_id"]), None)
        if ranked and ranked[0]["confidence"] >= _min_confidence(db):
            inferred[t["table_name"]] = ranked[0]
        elif own and own["signals"] and own["confidence"] >= _min_confidence(db, "inherited"):
            inferred[t["table_name"]] = own
    return inferred, [d for d in schema_domain if d["confidence"] >= _min_confidence(db)]


def _run_domains(db: Db, database: str, schema: str) -> dict[str, str]:
    try:
        return {r["object_name"]: r["domain_name"] for r in db.query(
            """
            SELECT O.OBJECT_NAME, D.DOMAIN_NAME
              FROM SOURCE.SOURCE_OBJECT O
              JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = O.RUN_ID
              JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
             WHERE O.SOURCE_DATABASE = %s AND O.SOURCE_SCHEMA = %s AND O.SELECTED_FLAG
           QUALIFY ROW_NUMBER() OVER (PARTITION BY O.OBJECT_NAME ORDER BY O.DISCOVERED_AT DESC) = 1
            """,
            (database, schema),
        )}
    except Exception:
        return {}


def _catalog_inventory(db: Db, database: str, schema: str) -> dict:
    """Tables, profile store, run domains, the registered source and the domain vocabulary are independent
    lookups, so they run in parallel; the slowest one sets the response time instead of their sum."""
    from concurrent.futures import ThreadPoolExecutor

    src = {"database_name": database, "schema_name": schema}
    with ThreadPoolExecutor(max_workers=5) as pool:
        tables_f = pool.submit(_source_tables, db, src)
        store_f = pool.submit(_catalog_store_rows, db, database, schema)
        domains_f = pool.submit(_run_domains, db, database, schema)
        registered_f = pool.submit(_registered_source, db, database, schema)
        pool.submit(_domain_vocab, db)  # warms the cached vocabulary used by domain inference
        try:
            tables = tables_f.result()
        except Exception as exc:
            raise _snowflake_error(exc) from exc
        store, domains, registered = store_f.result(), domains_f.result(), registered_f.result()
    jobs = _active_jobs(registered["source_system_id"]) if registered else []
    busy = {t for j in jobs for t in j["tables"]}
    from services.profiling.insights import scorecard

    _backfill_quality(db, [e for e in store.values() if e.get("source_fingerprint") and not e.get("quality_json")])
    inferred, schema_domain = _inferred_table_domains(db, tables, domains, schema)
    inventory = []
    for t in tables:
        entry = store.get(t["table_name"])
        status = "PROFILING" if t["table_name"] in busy else _table_status(entry, t["fingerprint"])
        dims = _json(entry.get("quality_json")) if entry else None
        staged = bool(entry and entry.get("source_fingerprint"))
        inventory.append({
            "table_name": t["table_name"], "table_type": t["table_type"], "row_count": t["row_count"],
            "bytes": t["bytes"], "column_count": t["column_count"], "last_altered": t["last_altered"],
            "status": status,
            "domain_name": domains.get(t["table_name"]) or (inferred.get(t["table_name"]) or {}).get("domain_name"),
            "domain_inferred": t["table_name"] not in domains and t["table_name"] in inferred,
            "domain_confidence": (inferred.get(t["table_name"]) or {}).get("confidence"),
            "stage_path": entry["profile_stage_path"] if staged else None,
            "profiled_at": entry["profiled_at"] if staged else None,
            "profiled_by": entry.get("profiled_by") if staged else None,
            "avg_null_percentage": entry.get("avg_null_percentage") if staged else None,
            "key_candidates": entry.get("key_candidates") if staged else None,
            "pii_columns": entry.get("pii_columns") if staged else None,
            "is_approximate": entry.get("is_approximate") if staged else None,
            "error_message": entry.get("error_message") if entry and status == "FAILED" else None,
            "quality": scorecard(dims, t["last_altered"]) if staged and dims else None,
        })
    return {"database": database, "schema": schema, "source": registered, "tables": inventory, "jobs": jobs,
            "domain_candidates": [d for d in schema_domain if d["confidence"] >= _min_confidence(db)]}


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _read_stage_doc(db: Db, stage_path: str) -> Optional[dict]:
    from services.profiling.profiler import STAGE_PATH

    if not STAGE_PATH.match(stage_path or ""):
        return None
    found = db.query(f"SELECT $1 AS DOC FROM @METADATA.PROFILES_STAGE/{stage_path} "
                     "(FILE_FORMAT => 'METADATA.PROFILE_JSON_FORMAT')")
    return _json(found[0]["doc"]) if found else None


def _backfill_quality(db: Db, entries: list[dict], limit: int = 25) -> None:
    """Profiles staged before quality scoring existed get their dimensions once, then read from the index."""
    from services.profiling.insights import quality_dimensions

    for entry in entries[:limit]:
        try:
            doc = _read_stage_doc(db, entry["profile_stage_path"])
            if not doc:
                continue
            dims = quality_dimensions(doc)
            db.execute(
                "UPDATE METADATA.TABLE_PROFILES SET QUALITY_JSON = PARSE_JSON(%s) WHERE SOURCE_NAME = %s "
                "AND DATABASE_NAME = %s AND SCHEMA_NAME = %s AND TABLE_NAME = %s",
                (json.dumps(dims), entry["source_name"], entry["database_name"], entry["schema_name"], entry["table_name"]),
            )
            entry["quality_json"] = dims
        except Exception:
            continue


def _live_table(db: Db, database: str, schema: str, table: str) -> tuple[list[tuple[str, str]], Optional[int], Optional[str]]:
    from services.source.identifiers import format_data_type

    cols = [(c["column_name"], format_data_type(c["data_type"], c["character_maximum_length"], c["numeric_precision"],
                                                c["numeric_scale"]))
            for c in db.query(
                f"""
                SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE
                  FROM {_quote_ident(database)}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                 ORDER BY ORDINAL_POSITION
                """,
                (schema, table))]
    meta = db.query(f"SELECT ROW_COUNT, LAST_ALTERED::VARCHAR AS LAST_ALTERED FROM {_quote_ident(database)}.INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (schema, table))
    return cols, (meta[0]["row_count"] if meta else None), (meta[0]["last_altered"] if meta else None)


@app.get("/api/catalog/inventory")
def catalog_inventory(database: str, schema: str, db: Db = Depends(current_db)):
    """Tables of any database.schema the role can read, with profile-store status. Read only."""
    return _catalog_inventory(db, _ident(database, "database"), _ident(schema, "schema"))


@app.get("/api/catalog/profile")
def catalog_table_profile(database: str, schema: str, table: str, db: Db = Depends(current_db)):
    """The staged profile document of any profiled table, read from @METADATA.PROFILES_STAGE."""
    from services.profiling.profiler import STAGE_PATH

    database, schema, table = _ident(database, "database"), _ident(schema, "schema"), _table_ident(table)
    from services.profiling import insights

    entry = _catalog_store_rows(db, database, schema).get(table)
    if not entry or not entry.get("source_fingerprint") or not STAGE_PATH.match(entry["profile_stage_path"] or ""):
        raise HTTPException(404, f"{table} has no staged profile yet")
    doc = _read_stage_doc(db, entry["profile_stage_path"])
    if not doc:
        raise HTTPException(404, f"the staged file for {table} is missing; re-profile it")
    try:
        columns, rows, last_altered = _live_table(db, database, schema, table)
        change = insights.drift(doc, columns, rows) if columns else None
    except Exception:
        last_altered, change = None, None
    checks = insights.suggested_checks(doc)
    entry.pop("quality_json", None)
    return {"entry": entry, "profile": doc,
            "scorecard": insights.scorecard(insights.quality_dimensions(doc), last_altered),
            "checks": checks, "checks_yaml": insights.checks_yaml(table, checks), "drift": change}


def _tags_for(db: Db, entity_type: str, keys: list[str]) -> dict[str, list[str]]:
    """Tags per key; empty before V019 is deployed."""
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}
    try:
        found = db.query("""SELECT ENTITY_KEY, ARRAY_AGG(TAG) WITHIN GROUP (ORDER BY TAG) AS TAGS
                              FROM CORE.TAG_ASSIGNMENT
                             WHERE ENTITY_TYPE = %s AND ARRAY_CONTAINS(ENTITY_KEY::VARIANT, PARSE_JSON(%s)::ARRAY)
                             GROUP BY ENTITY_KEY""", (entity_type, json.dumps(keys)))
    except Exception:
        return {}
    return {r["entity_key"]: _json(r["tags"]) or [] for r in found}


class TagsIn(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=50)


@app.get("/api/tags")
def list_tags(entity_type: Optional[str] = None, db: Db = Depends(current_db)):
    """Every tag in use (optionally for one entity type) with how often it is used, for autocomplete and filters."""
    from services.common.tags import color_for

    try:
        rows = db.query("""SELECT A.TAG, COUNT(*) AS N, MAX(T.COLOR) AS COLOR, MAX(T.DESCRIPTION) AS DESCRIPTION
                             FROM CORE.TAG_ASSIGNMENT A LEFT JOIN CORE.TAG T ON T.TAG = A.TAG
                            WHERE (%s IS NULL OR A.ENTITY_TYPE = %s)
                            GROUP BY A.TAG ORDER BY N DESC, A.TAG LIMIT 500""",
                        (entity_type.upper() if entity_type else None, entity_type.upper() if entity_type else None))
    except Exception:
        return {"tags": [], "ready": False}
    return {"tags": [{"tag": r["tag"], "count": r["n"], "color": r["color"] or color_for(r["tag"]),
                      "description": r["description"]} for r in rows], "ready": True}


@app.get("/api/tags/{entity_type}/{key}")
def get_tags(entity_type: str, key: str, db: Db = Depends(current_db)):
    from services.common.tags import entity_key

    try:
        canonical = entity_key(entity_type, key)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"entity_type": entity_type.upper(), "key": canonical,
            "tags": _tags_for(db, entity_type.upper(), [canonical]).get(canonical, [])}


@app.put("/api/tags/{entity_type}/{key}")
def set_tags(entity_type: str, key: str, body: TagsIn, db: Db = Depends(current_db)):
    """Replace the tags of one profile, run or model."""
    from services.common.tags import color_for, entity_key, normalize_tag, normalize_tags

    try:
        canonical = entity_key(entity_type, key)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    kind = entity_type.upper()
    tags = normalize_tags(body.tags)
    bad = [t for t in body.tags if t.strip() and normalize_tag(t) is None]
    if bad:
        raise HTTPException(400, f"Not a valid tag: {bad[0]}. Use letters, digits, '-', '_', '.', ':' or '/' (40 max).")
    try:
        with transaction(db):  # replace as one unit: a failure midway keeps the previous tags
            db.execute("DELETE FROM CORE.TAG_ASSIGNMENT WHERE ENTITY_TYPE = %s AND ENTITY_KEY = %s", (kind, canonical))
            for tag in tags:
                db.execute("""MERGE INTO CORE.TAG T USING (SELECT %s AS TAG) S ON T.TAG = S.TAG
                              WHEN NOT MATCHED THEN INSERT (TAG, COLOR) VALUES (S.TAG, %s)""", (tag, color_for(tag)))
                db.execute("INSERT INTO CORE.TAG_ASSIGNMENT (ENTITY_TYPE, ENTITY_KEY, TAG) SELECT %s, %s, %s",
                           (kind, canonical, tag))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"entity_type": kind, "key": canonical, "tags": tags}


@app.get("/api/profiles/store")
def profile_store(db: Db = Depends(current_db)):
    """Every staged profile across all databases, newest first."""
    # In-place Oracle profiles (source ORACLE_<NAME>) describe tables outside Snowflake; they show in the Oracle
    # panel, not as Snowflake schemas to open.
    rows = [r for r in _store_rows(db) if (r.get("source_fingerprint") or r.get("status") == "PROFILING")
            and not str(r.get("source_name") or "").upper().startswith("ORACLE_")]
    rows.sort(key=lambda r: r.get("status_updated_at") or r.get("profiled_at") or "", reverse=True)
    tags = _tags_for(db, "PROFILE", [f"{r.get('database_name')}.{r.get('schema_name')}.{r.get('table_name')}".upper() for r in rows])
    for r in rows:
        r["tags"] = tags.get(f"{r.get('database_name')}.{r.get('schema_name')}.{r.get('table_name')}".upper(), [])
    return {"profiles": rows}


class CatalogTarget(BaseModel):
    database: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(min_length=1, max_length=255, alias="schema")


def _ensure_source(db: Db, database: str, schema: str) -> dict:
    """The registered connection for database.schema, registering one on first use (profiling or modeling).
    Browsing never registers anything."""
    from services.source.procedures import register_connection

    existing = _registered_source(db, database, schema)
    if existing:
        return existing
    kind = db.query(f"SELECT TYPE FROM {DATABASE}.INFORMATION_SCHEMA.DATABASES WHERE DATABASE_NAME = %s", (database,))
    source_type = "SNOWFLAKE_SHARE" if kind and kind[0]["type"] == "IMPORTED DATABASE" else "SNOWFLAKE_DATABASE"
    clean = lambda v: re.sub(r"[^A-Za-z0-9_]", "_", v).strip("_") or "SOURCE"  # noqa: E731
    base = clean(schema)
    candidates = [base, f"{clean(database)}_{base}", f"{clean(database)}_{base}_{uuid.uuid4().hex[:4].upper()}"]
    last_error: Exception | None = None
    for name in candidates:
        name = (name if re.match(r"^[A-Za-z]", name) else f"SRC_{name}")[:64]
        payload = json.dumps({"SOURCE_SYSTEM_NAME": name, "SOURCE_TYPE": source_type,
                              "DATABASE": database, "SCHEMA": schema})
        try:
            _source_call(db, "CALL SOURCE.REGISTER_CONNECTION(%s)", register_connection, payload)
            break
        except Exception as exc:
            last_error = exc
            if "SOURCE_NAME_CONFLICT" not in str(exc):
                raise _snowflake_error(exc) from exc
    registered = _registered_source(db, database, schema)
    if not registered:
        raise _snowflake_error(last_error or RuntimeError("could not register the source"))
    return registered


class CatalogProfileRequest(CatalogTarget):
    tables: list[str] = Field(min_length=1, max_length=500)
    force_refresh: bool = False


@app.post("/api/catalog/profile-tables")
def catalog_profile_tables(body: CatalogProfileRequest, db: Db = Depends(current_db)):
    """Profile any tables of any database.schema in place; registers the connection on first use."""
    src = _ensure_source(db, _ident(body.database, "database"), _ident(body.schema_name, "schema"))
    return profile_source_tables(src["source_system_id"],
                                 SourceProfileRequest(tables=body.tables, force_refresh=body.force_refresh), db)


class CatalogModelingRequest(CatalogTarget):
    tables: list[str] = Field(min_length=1, max_length=500)
    run_name: Optional[str] = Field(default=None, max_length=256)
    domain_id: Optional[str] = None
    targets: list[ModelTarget] = Field(default_factory=list, max_length=50)
    proposed_name: Optional[str] = Field(default=None, max_length=255)
    proposed_schema: Optional[str] = Field(default=None, max_length=255)
    modeling_standard: Literal["GDP", "GENERIC"] = "GENERIC"


@app.post("/api/catalog/modeling-run")
def catalog_modeling_run(body: CatalogModelingRequest, db: Db = Depends(current_db)):
    src = _ensure_source(db, _ident(body.database, "database"), _ident(body.schema_name, "schema"))
    return send_to_modeling(src["source_system_id"], ModelingRunRequest(
        tables=body.tables, run_name=body.run_name, domain_id=body.domain_id, targets=body.targets,
        proposed_name=body.proposed_name, proposed_schema=body.proposed_schema,
        modeling_standard=body.modeling_standard), db)


def _value_relationships(db: Db, database: str, schema: str, docs: dict) -> list[dict]:
    """Joins the names do not reveal, proven by one overlap query over the staged tables (never blocks analyze)."""
    from services.profiling import insights

    pairs = insights.overlap_candidates(docs)
    sql = insights.overlap_sql(pairs, lambda t, c: (
        f"{_quote_ident(database)}.{_quote_ident(schema)}.{_quote_ident(t)}", _quote_ident(c)))
    if not sql:
        return []
    try:
        return insights.overlap_relationships(pairs, db.query(sql))
    except Exception:
        return []


class CatalogAnalyzeRequest(CatalogTarget):
    tables: list[str] = Field(min_length=1, max_length=60)


@app.post("/api/catalog/analyze")
def catalog_analyze(body: CatalogAnalyzeRequest, db: Db = Depends(current_db)):
    """Everything the modeling panel needs for a set of staged tables, from their profiles only:
    ER graph with profile-inferred relationships, a quality scorecard per table and domain model matches."""
    from services.profiling import insights

    database, schema = _ident(body.database, "database"), _ident(body.schema_name, "schema")
    tables = sorted({_table_ident(t) for t in body.tables})
    _rules(db)
    store = _catalog_store_rows(db, database, schema)
    meta = {t["table_name"]: t for t in _source_tables(db, {"database_name": database, "schema_name": schema})}
    docs, summary = {}, []
    for name in tables:
        entry = store.get(name)
        doc = _read_stage_doc(db, entry["profile_stage_path"]) if entry and entry.get("source_fingerprint") else None
        if doc:
            docs[name] = doc
        last_altered = (meta.get(name) or {}).get("last_altered")
        summary.append({
            "table": name, "staged": bool(doc), "row_count": (doc or {}).get("row_count"),
            "column_count": (doc or {}).get("column_count"),
            "quality": insights.scorecard(insights.quality_dimensions(doc), last_altered) if doc else None,
            "key_candidates": [c["column_name"] for c in (doc or {}).get("columns", []) if c.get("potential_key")],
            "pii_columns": [c["column_name"] for c in (doc or {}).get("columns", [])
                            if (c.get("pii_classification") or "NONE") != "NONE"],
        })
    relationships = insights.infer_relationships(docs)
    relationships += _value_relationships(db, database, schema, docs)
    graph = catalog_preview_graph(PreviewGraph(database=database, schema=schema, tables=tables, targets=[]), db)
    profiled_pairs = {frozenset((j["left"], j["right"])) for j in relationships}
    graph["joins"] = relationships + [j for j in graph.get("joins") or []
                                      if frozenset((j["left"], j["right"])) not in profiled_pairs]
    linked = {j["left"] for j in graph["joins"]} | {j["right"] for j in graph["joins"]}
    graph["isolated"] = sorted(t for t in tables if t not in linked) if len(tables) > 1 else []
    keys = {name: {c["column_name"] for c in doc["columns"] if c.get("potential_key")} for name, doc in docs.items()}
    for src in graph.get("sources") or []:
        for c in src.get("columns") or []:
            c["pk"] = c["pk"] or c["name"] in keys.get(src["object_name"], set())
    graph["profiled"] = True
    columns = [c["column_name"] for doc in docs.values() for c in doc.get("columns", [])]
    if not columns:
        columns = [c for t in tables for c in (meta.get(t) or {}).get("column_names") or []]
    candidates = _infer_domains(db, tables, columns, schema)[:3]
    detected = candidates[0] if candidates and candidates[0]["confidence"] >= _min_confidence(db) else None
    schema_domains: list = []
    if meta:
        try:
            inferred, schema_domains = _inferred_table_domains(db, list(meta.values()), {}, schema)
        except Exception:
            inferred = {}
    if not detected and meta:
        # the same rule as the schema view: a table inherits the schema's lead domain when it hits its signals
        picked = [inferred[t] for t in tables if t in inferred]
        if picked:
            best = max(picked, key=lambda d: d["confidence"])
            detected = {**best, "inherited_from_schema": bool(schema_domains) and best["domain_id"] == schema_domains[0]["domain_id"]}
            candidates = [detected] + [c for c in candidates if c["domain_id"] != detected["domain_id"]]
    return {"tables": summary, "relationships": relationships, "graph": graph,
            "domain": {"detected": detected, "candidates": candidates,
                       "schema": schema_domains[0] if schema_domains else None},
            "bands": _ui_bands(db),
            # The panel always asks "GDP or not"; this is only the preselection.
            "suggested_standard": "GDP" if detected and detected.get("standard") == "GDP" else "GENERIC",
            "models": _suggest_for_catalog(db, database, schema, tables,
                                           detected["domain_name"] if detected else None)}


# ---------------------------------------------------------------- External sources: register, upload, land

MAX_UPLOAD_BYTES = 200 * 1024 * 1024


@app.get("/api/connectors")
def connectors():
    from services.source.external import connector_catalog

    return {"connectors": connector_catalog()}


class ExternalSourceCreate(BaseModel):
    source_system_name: str = Field(min_length=1, max_length=64)
    connector: str = Field(min_length=1, max_length=32)
    config: dict = Field(default_factory=dict)
    owner: Optional[str] = Field(default=None, max_length=256)
    security_classification: Optional[str] = Field(default=None, max_length=32)


@app.post("/api/sources/external")
def create_external_source(body: ExternalSourceCreate, db: Db = Depends(current_db)):
    """Register an external system. Only the name of a Snowflake SECRET or STORAGE INTEGRATION is kept."""
    from services.source.external import register_external_source

    try:
        return _source_call(db, "CALL SOURCE.REGISTER_EXTERNAL_SOURCE(%s)", register_external_source,
                            body.model_dump_json())
    except Exception as exc:
        raise _snowflake_error(exc) from exc


def _external_row(db: Db, source_id: str) -> dict:
    src = _source_row(db, source_id)
    if not str(src["source_type"]).startswith("EXTERNAL_"):
        raise HTTPException(400, "not an external source")
    return src


@app.get("/api/sources/{source_id}/files")
def external_files(source_id: str, db: Db = Depends(current_db)):
    from services.source.external import list_external_files

    _external_row(db, source_id)
    try:
        return _source_call(db, "CALL SOURCE.LIST_EXTERNAL_FILES(%s)", list_external_files, source_id)
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/sources/{source_id}/upload")
def upload_external_files(source_id: str, files: list[UploadFile] = File(...), db: Db = Depends(current_db)):
    """Upload files to the source's internal stage (file-upload sources). Nothing is loaded until Land.
    A plain def: FastAPI runs it in the threadpool, so the blocking Snowflake PUTs never stall the event loop."""
    import tempfile

    src = _external_row(db, source_id)
    found = db.query("SELECT CONNECTION_TYPE FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = %s", (source_id,))
    if not found or found[0]["connection_type"] != "upload":
        raise HTTPException(400, "files can only be uploaded to a file-upload source; cloud sources read their bucket")
    database, schema = src["database_name"], src["schema_name"]  # stored spelling; quoted where used
    uploaded = []
    tmp = Path(tempfile.mkdtemp())
    try:
        for f in files:
            name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(f.filename or "file").name)[:200]
            if not re.search(r"\.(csv|tsv|txt|parquet|json|ndjson)(\.gz)?$", name, re.I):
                raise HTTPException(400, f"{name}: only CSV, Parquet and JSON files are supported")
            data = f.file.read(MAX_UPLOAD_BYTES + 1)  # the parsed form's spooled file; sync read in this worker thread
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"{name} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
            local = tmp / name
            local.write_bytes(data)
            try:
                db.execute(f"PUT 'file://{local.as_posix()}' @{_quote_ident(database)}.{_quote_ident(schema)}.FILES AUTO_COMPRESS = FALSE OVERWRITE = TRUE")
            except Exception as exc:
                raise _snowflake_error(exc) from exc
            uploaded.append({"file": name, "bytes": len(data)})
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    return {"uploaded": uploaded}


class LandRequest(BaseModel):
    files: list[str] = Field(default_factory=list, max_length=200)
    table: Optional[str] = Field(default=None, max_length=255)


@app.post("/api/sources/{source_id}/land")
def land_external(source_id: str, body: LandRequest, db: Db = Depends(current_db)):
    """Load staged files into tables of the source's landing schema; they can then be profiled in place."""
    from services.source.external import land_external_files

    _external_row(db, source_id)
    try:
        return _source_call(db, "CALL SOURCE.LAND_EXTERNAL_FILES(%s, %s)", land_external_files, source_id,
                            body.model_dump_json())
    except Exception as exc:
        raise _snowflake_error(exc) from exc


# ---------------------------------------------------------------- Rules: thresholds and name hints as configuration


class RulesUpdate(BaseModel):
    overrides: dict = Field(default_factory=dict)
    note: Optional[str] = Field(default=None, max_length=2000)


def _put_config(db: Db, key: str, value: dict, description: str) -> None:
    with transaction(db):
        db.execute("UPDATE CORE.PLATFORM_CONFIG SET IS_CURRENT = FALSE WHERE CONFIG_KEY = %s AND IS_CURRENT", (key,))
        db.execute(
            """
            INSERT INTO CORE.PLATFORM_CONFIG (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION, VERSION, IS_CURRENT, CREATED_BY)
            SELECT %s, PARSE_JSON(%s), %s,
                   COALESCE((SELECT MAX(VERSION) FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s), 0) + 1,
                   TRUE, CURRENT_USER()
            """,
            (key, json.dumps(value), description, key),
        )


def _clean_overrides(overrides: dict) -> dict:
    """Only known keys with values of the right kind; anything else is reported, not silently dropped."""
    from services.common.rules import DEFAULTS, merged

    unknown = sorted(set(overrides) - set(DEFAULTS))
    if unknown:
        raise HTTPException(400, f"unknown rule keys: {', '.join(unknown)}")
    applied = merged(overrides)
    rejected = sorted(k for k, v in overrides.items() if v is not None and applied[k] == DEFAULTS[k] and v != DEFAULTS[k])
    if rejected:
        raise HTTPException(400, f"invalid values for: {', '.join(rejected)}")
    return {k: applied[k] for k in overrides if overrides[k] is not None}


@app.get("/api/config/rules")
def get_rules(domain_id: Optional[str] = None, db: Db = Depends(current_db)):
    """Effective rules (defaults, platform overrides, then the domain's), plus the bands the UI shows."""
    from services.common.rules import DEFAULTS, ui_bands

    effective = _rules(db, domain_id)
    return {"rules": effective, "defaults": DEFAULTS, "ui": ui_bands(effective),
            "overridden": sorted(k for k in DEFAULTS if effective[k] != DEFAULTS[k])}


@app.put("/api/config/rules")
def put_rules(body: RulesUpdate, db: Db = Depends(current_db)):
    """Platform-wide overrides; keys not sent fall back to their defaults."""
    overrides = _clean_overrides(body.overrides)
    _put_config(db, "RULES", overrides, "Rule thresholds and name hints (platform overrides)")
    _RULES_CACHE.clear()
    return get_rules(None, db)


@app.put("/api/domains/{domain_id}/rules")
def put_domain_rules(domain_id: str, body: RulesUpdate, db: Db = Depends(current_db)):
    """Overrides for one domain, on top of the platform's."""
    overrides = _clean_overrides(body.overrides)
    found = db.query("SELECT DOMAIN_ID FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,))
    if not found:
        raise HTTPException(404, "domain not found")
    db.execute(
        """UPDATE KNOWLEDGE.DOMAIN_REGISTRY
              SET CONFIG = OBJECT_INSERT(COALESCE(CONFIG, OBJECT_CONSTRUCT()), 'rules', PARSE_JSON(%s), TRUE)
            WHERE DOMAIN_ID = %s""",
        (json.dumps(overrides), domain_id),
    )
    _RULES_CACHE.clear()
    _domain_snapshot(db, domain_id, "RULES", body.note or "Rules changed")
    return get_rules(domain_id, db)


# ---------------------------------------------------------------- AI suggestions next to rule results

SUGGESTION_STAGES = ("PROFILING", "DOMAIN", "STTM", "SODA", "DBT")


class SuggestionDecision(BaseModel):
    suggestion_id: Optional[str] = None
    scope_key: str = Field(min_length=1, max_length=1024)
    item: dict
    decision: Literal["ACCEPTED", "REJECTED"]
    note: Optional[str] = Field(default=None, max_length=2000)


def _suggestion_stage(stage: str) -> str:
    stage = stage.upper()
    if stage not in SUGGESTION_STAGES:
        raise HTTPException(400, f"stage must be one of {', '.join(SUGGESTION_STAGES)}")
    return stage


@app.get("/api/runs/{run_id}/suggestions/{stage}")
def get_suggestions(run_id: str, stage: str, db: Db = Depends(current_db)):
    """Earlier AI suggestions for this stage (no model call)."""
    from services.common.suggestion_stages import run_suggestions

    try:
        return invoke_source(db, run_suggestions, _suggestion_stage(stage), run_id, False, True)
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/suggestions/{stage}")
def ask_suggestions(run_id: str, stage: str, refresh: bool = False, db: Db = Depends(current_db)):
    """Ask the model to review the rule results: one call per table, reused while the inputs are unchanged."""
    from services.common.suggestion_stages import run_suggestions

    try:
        return invoke_source(db, run_suggestions, _suggestion_stage(stage), run_id, refresh, False)
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/suggestions/{stage}/decision")
def decide_suggestion(run_id: str, stage: str, body: SuggestionDecision, db: Db = Depends(current_db)):
    from services.common.suggestion_stages import decide

    try:
        result = invoke_source(db, decide, _suggestion_stage(stage), run_id, body.model_dump_json())
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    _drop_run(run_id)
    return result


# ---------------------------------------------------------------- Knowledge packs: add a domain without code


def _current_db_name(db: Db) -> str:
    found = db.query("SELECT CURRENT_DATABASE() AS D")
    name = (found[0].get("d") if found else None) or ""
    assert re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", name), "no current database"
    return name


class PackImport(BaseModel):
    pack: dict


class PackDraft(BaseModel):
    text: str = Field(min_length=20, max_length=200_000)
    standard: Optional[Literal["GDP", "GENERIC"]] = "GENERIC"


@app.post("/api/domains/import")
def import_domain_pack(body: PackImport, db: Db = Depends(current_db)):
    """Register a pack (domain, targets, columns, knowledge) exactly as the deploy seeds the repository packs."""
    from services.knowledge.packs import import_pack

    try:
        result = import_pack(db.execute, _current_db_name(db), body.pack)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    _DOMAIN_VOCAB.update(at=0.0, domains=[])
    _RULES_CACHE.clear()
    _domain_snapshot(db, result.get("domain_id"), "IMPORT", "Pack imported")
    return result


@app.post("/api/domains/draft")
def draft_domain_pack(body: PackDraft, db: Db = Depends(current_db)):
    """AI drafts a pack from any contract document; nothing is saved until the reviewer imports it."""
    from services.knowledge.packs import draft_pack

    started = time.time()
    try:
        result = invoke_source(db, draft_pack, body.text, body.standard)
    except AssertionError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    _record_cost(db, None, "KNOWLEDGE", result.get("model"), result.pop("usage", None), started)
    return result


@app.get("/api/domains/{domain_id}/export")
def export_domain_pack(domain_id: str, db: Db = Depends(current_db)):
    from services.knowledge.packs import export_pack

    try:
        return {"pack": export_pack(lambda sql, params: db.query(sql, params), domain_id)}
    except AssertionError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------- Knowledge management


class KnowledgeItemIn(BaseModel):
    domain_id: Optional[str] = Field(default=None, max_length=64)
    knowledge_type: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=8000)
    content_json: Any = None
    origin: Literal["USER", "COPILOT"] = "USER"
    change_note: Optional[str] = Field(default=None, max_length=2000)


class KnowledgeAnswerIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    domain: Optional[str] = None
    knowledge_type: Optional[str] = None


_KNOWLEDGE_COLUMNS = """K.KNOWLEDGE_ID, K.DOMAIN_ID, D.DOMAIN_NAME, K.KNOWLEDGE_TYPE, K.TITLE, K.CONTENT, K.CONTENT_JSON,
       K.SOURCE_REFERENCE, K.STATUS, K.VERSION, K.CREATED_BY, K.CREATED_AT::VARCHAR AS CREATED_AT,
       K.UPDATED_AT::VARCHAR AS UPDATED_AT, K.LINEAGE_ID, K.ORIGIN, K.SOURCE_RUN_ID, K.CONFIDENCE, K.CHANGE_NOTE,
       K.VERIFIED_BY, K.VERIFIED_AT::VARCHAR AS VERIFIED_AT, K.REVIEW_DUE::VARCHAR AS REVIEW_DUE, K.IS_CURRENT,
       K.REVIEWED_BY, K.REVIEW_NOTE, K.TAGS"""


def _knowledge_item(db: Db, knowledge_id: str) -> dict:
    found = db.query(f"""SELECT {_KNOWLEDGE_COLUMNS} FROM KNOWLEDGE.DOMAIN_KNOWLEDGE K
                          JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = K.DOMAIN_ID
                         WHERE K.KNOWLEDGE_ID = %s""", (knowledge_id,))
    if not found:
        raise HTTPException(404, "knowledge item not found")
    return _shape_knowledge(found[0])


def _shape_knowledge(row: dict) -> dict:
    from services.knowledge.manage import editable

    row["content_json"] = _json(row.get("content_json"))
    row["tags"] = _json(row.get("tags")) or []
    ok, reason = editable(row.get("created_by"), row.get("origin"))
    row["editable"], row["read_only_reason"] = ok, reason
    return row


@app.get("/api/knowledge")
def list_knowledge(domain_id: Optional[str] = None, knowledge_type: Optional[str] = None,
                   status: Optional[str] = None, q: Optional[str] = None, offset: int = 0, limit: int = 50,
                   db: Db = Depends(current_db)):
    """Current version of each knowledge item, filtered and paged."""
    from services.knowledge.manage import list_query

    try:
        where, params = list_query(domain_id, knowledge_type, status, q, offset, limit)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    items = db.query(f"""SELECT {_KNOWLEDGE_COLUMNS} FROM KNOWLEDGE.DOMAIN_KNOWLEDGE K
                          JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = K.DOMAIN_ID
                         WHERE {where} ORDER BY K.UPDATED_AT DESC NULLS LAST, K.TITLE LIMIT %s OFFSET %s""",
                     tuple(params))
    total = db.query(f"SELECT COUNT(*) AS N FROM KNOWLEDGE.DOMAIN_KNOWLEDGE K WHERE {where}", tuple(params[:-2]))
    return {"items": [_shape_knowledge(r) for r in items], "total": int(total[0]["n"]) if total else 0}


@app.post("/api/knowledge")
def add_knowledge(body: KnowledgeItemIn, db: Db = Depends(current_db)):
    from services.knowledge.manage import new_key, prepare_item

    if not body.domain_id:
        raise HTTPException(400, "choose the domain this knowledge belongs to")
    item, problems = prepare_item(body.knowledge_type, body.title, body.content, body.content_json)
    if problems:
        raise HTTPException(400, "; ".join(problems))
    domain = db.query("SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s AND ACTIVE_FLAG",
                      (body.domain_id,))
    if not domain:
        raise HTTPException(400, "unknown or deleted domain")
    from services.knowledge.writer import lineage_id, mode_for

    knowledge_id = str(uuid.uuid4())
    key = new_key(domain[0]["domain_name"], item["knowledge_type"], item["title"])
    queued = mode_for(_config(db, "KNOWLEDGE_LEARNING_POLICY", {}), item["knowledge_type"], body.origin) == "review"
    db.execute("""INSERT INTO KNOWLEDGE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT,
                         CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID,
                         ORIGIN, CHANGE_NOTE)
                  SELECT %s, %s, %s, %s, %s, PARSE_JSON(NULLIF(%s, '')), PARSE_JSON(%s), %s, %s, 1, %s,
                         CURRENT_USER(), %s, %s, %s""",
               (knowledge_id, body.domain_id, item["knowledge_type"], item["title"], item["content"],
                json.dumps(item["content_json"]) if item["content_json"] is not None else "",
                json.dumps(["UI"] if body.origin == "USER" else ["COPILOT"]), key,
                "PROPOSED" if queued else "ACTIVE", not queued, lineage_id(body.domain_id, key), body.origin,
                body.change_note or ("Saved from the copilot" if body.origin == "COPILOT" else "Added in Knowledge")))
    _DOMAIN_VOCAB.update(at=0.0, domains=[])
    return _knowledge_item(db, knowledge_id)


@app.put("/api/knowledge/{knowledge_id}")
def edit_knowledge(knowledge_id: str, body: KnowledgeItemIn, db: Db = Depends(current_db)):
    """An edit is a new version of the item; the previous version stays in its history."""
    from services.knowledge.manage import prepare_item

    current = _knowledge_item(db, knowledge_id)
    if not current["editable"]:
        raise HTTPException(409, current["read_only_reason"])
    item, problems = prepare_item(body.knowledge_type or current["knowledge_type"], body.title, body.content,
                                  body.content_json)
    if problems:
        raise HTTPException(400, "; ".join(problems))
    new_id = _new_knowledge_version(db, current, item["knowledge_type"], item["title"], item["content"],
                                    item["content_json"], body.change_note or "Edited in Knowledge")
    _DOMAIN_VOCAB.update(at=0.0, domains=[])
    return _knowledge_item(db, new_id)


def _new_knowledge_version(db: Db, current: dict, kind: str, title: str, content: str, content_json: Any,
                           note: str, status: Optional[str] = None) -> str:
    """A person's change: the version in use is superseded, the new one is current (same lineage)."""
    from services.knowledge.writer import lineage_id

    if not current.get("is_current", True):
        raise HTTPException(409, "Only the version in use can be changed. Open the current version or roll back to this one.")
    lineage = current.get("lineage_id") or (lineage_id(current["domain_id"], current["source_reference"])
                                            if current.get("source_reference") else current["knowledge_id"])
    new_id = str(uuid.uuid4())
    with transaction(db):  # supersede and insert together; a concurrent edit that got there first wins
        top = db.query("SELECT COALESCE(MAX(VERSION), 0) AS V FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE LINEAGE_ID = %s",
                       (lineage,))
        version = max(int(top[0]["v"] if top else 0), int(current["version"])) + 1
        if not db.execute_count("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, UPDATED_AT = CURRENT_TIMESTAMP(),
                                         STATUS = IFF(STATUS IN ('ACTIVE', 'RETIRED'), 'SUPERSEDED', STATUS)
                                   WHERE KNOWLEDGE_ID = %s AND IS_CURRENT""", (current["knowledge_id"],)):
            raise HTTPException(409, "Someone changed this item in the meantime. Reload it and try again.")
        db.execute("""INSERT INTO KNOWLEDGE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT,
                             CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID,
                             ORIGIN, SOURCE_RUN_ID, CHANGE_NOTE)
                      SELECT %s, %s, %s, %s, %s, PARSE_JSON(NULLIF(%s, '')), PARSE_JSON(%s), %s, %s, %s, TRUE,
                             CURRENT_USER(), %s, 'USER', %s, %s""",
                   (new_id, current["domain_id"], kind, title, content,
                    json.dumps(content_json) if content_json is not None else "",
                    json.dumps(sorted(set((current.get("tags") or []) + ["UI"]))), current["source_reference"],
                    status or (current["status"] if current["status"] in ("ACTIVE", "DRAFT", "RETIRED") else "ACTIVE"),
                    version, lineage, current.get("source_run_id"), note))
    return new_id


def _set_knowledge_status(db: Db, knowledge_id: str, status: str) -> dict:
    current = _knowledge_item(db, knowledge_id)
    if not current["editable"]:
        raise HTTPException(409, current["read_only_reason"])
    db.execute("UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET STATUS = %s, UPDATED_AT = CURRENT_TIMESTAMP() "
               "WHERE KNOWLEDGE_ID = %s", (status, knowledge_id))
    _DOMAIN_VOCAB.update(at=0.0, domains=[])
    return _knowledge_item(db, knowledge_id)


@app.post("/api/knowledge/{knowledge_id}/retire")
def retire_knowledge(knowledge_id: str, db: Db = Depends(current_db)):
    return _set_knowledge_status(db, knowledge_id, "RETIRED")


@app.post("/api/knowledge/{knowledge_id}/restore")
def restore_knowledge(knowledge_id: str, db: Db = Depends(current_db)):
    return _set_knowledge_status(db, knowledge_id, "ACTIVE")


@app.get("/api/knowledge/{knowledge_id}/history")
def knowledge_history(knowledge_id: str, db: Db = Depends(current_db)):
    current = _knowledge_item(db, knowledge_id)
    versions = db.query(f"""SELECT {_KNOWLEDGE_COLUMNS} FROM KNOWLEDGE.DOMAIN_KNOWLEDGE K
                             JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = K.DOMAIN_ID
                            WHERE K.LINEAGE_ID = %s ORDER BY K.VERSION DESC, K.CREATED_AT DESC""",
                        (current["lineage_id"],)) if current.get("lineage_id") else []
    return {"versions": [_shape_knowledge(v) for v in versions] or [current]}


@app.post("/api/knowledge/answer")
def answer_knowledge(body: KnowledgeAnswerIn, db: Db = Depends(current_db)):
    """Search, then answer from the hits only, with citations."""
    from services.knowledge.manage import answer

    started = time.time()
    try:
        result = invoke_source(db, answer, _current_db_name(db), body.question, body.domain, body.knowledge_type)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    _record_cost(db, None, "KNOWLEDGE", result.get("model"), result.pop("usage", None), started)
    return result


# ---------------------------------------------------------------- Admin: platform settings


class PlatformSetting(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: Any = None
    reset: bool = False


def _platform_settings(db: Db) -> dict:
    from services.common.platform_config import ADMIN_SETTINGS as SETTINGS
    from services.common.platform_config import DEFAULTS, KNOWN_MODELS
    from services.common.standard import PRESETS

    found = {r["config_key"]: r for r in db.query(
        """SELECT CONFIG_KEY, CONFIG_VALUE, VERSION, CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT
             FROM CORE.PLATFORM_CONFIG WHERE IS_CURRENT AND ARRAY_CONTAINS(CONFIG_KEY::VARIANT, PARSE_JSON(%s)::ARRAY)
           QUALIFY ROW_NUMBER() OVER (PARTITION BY CONFIG_KEY ORDER BY VERSION DESC) = 1""",
        (json.dumps(list(SETTINGS)),))}
    settings = {}
    for key in SETTINGS:
        row = found.get(key)
        value = _json(row["config_value"]) if row else DEFAULTS[key]
        settings[key] = {"value": value, "default": DEFAULTS[key], "customised": bool(row) and value != DEFAULTS[key],
                         "changed_by": row["created_by"] if row else None, "changed_at": row["created_at"] if row else None}
    return {"settings": settings, "known_models": KNOWN_MODELS, "presets": PRESETS}


@app.get("/api/config/platform")
def get_platform_settings(db: Db = Depends(current_db)):
    return _platform_settings(db)


@app.put("/api/config/platform")
def put_platform_setting(body: PlatformSetting, db: Db = Depends(current_db)):
    """Change one setting (validated), or reset it to the platform default."""
    from services.common.platform_config import DEFAULTS, validate

    value = DEFAULTS.get(body.key) if body.reset else body.value
    cleaned, problems = validate(body.key, value)
    if problems:
        raise HTTPException(400, "; ".join(problems))
    _put_config(db, body.key, cleaned, f"Set from Admin by {'reset' if body.reset else 'edit'}")
    if body.key == "CATALOG_DISPLAY":
        from services.source.catalog_display import configure

        configure(cleaned)
    _RULES_CACHE.clear()
    _MODELS_CACHE.update(at=0.0, data=None)
    return _platform_settings(db)


@app.get("/api/config/catalog-display")
def get_catalog_display(db: Db = Depends(current_db)):
    """The hidden-catalog lists the web app applies (single source with the API's own filtering)."""
    from services.common.platform_config import DEFAULTS

    try:
        found = db.query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'CATALOG_DISPLAY' "
                         "AND IS_CURRENT ORDER BY VERSION DESC LIMIT 1")
    except Exception:
        found = []
    return (_json(found[0]["config_value"]) if found else None) or DEFAULTS["CATALOG_DISPLAY"]


# ---------------------------------------------------------------- Domains: soft delete and restore


def _domain_active_runs(db: Db, domain_id: str) -> list[dict]:
    """Runs still in flight on this domain (not completed, cancelled, archived or deleted)."""
    try:
        return db.query(
            """SELECT RUN_ID, RUN_NAME, CURRENT_STATE FROM CORE.WORKFLOW_RUN
                WHERE DOMAIN_ID = %s AND CURRENT_STATE NOT IN ('COMPLETED', 'CANCELLED')
                  AND NOT COALESCE(IS_ARCHIVED, FALSE) AND DELETED_AT IS NULL
                ORDER BY CREATED_AT DESC LIMIT 50""", (domain_id,))
    except Exception:
        return []


def _domain_row(db: Db, domain_id: str) -> dict:
    found = db.query("SELECT DOMAIN_ID, DOMAIN_NAME, ACTIVE_FLAG, CONFIG FROM KNOWLEDGE.DOMAIN_REGISTRY "
                     "WHERE DOMAIN_ID = %s", (domain_id,))
    if not found:
        raise HTTPException(404, "domain not found")
    row = found[0]
    row["config"] = _json(row.get("config")) or {}
    return row


def _domain_caches_changed() -> None:
    _DOMAIN_VOCAB.update(at=0.0, domains=[])
    _RULES_CACHE.clear()


@app.delete("/api/domains/{domain_id}")
def delete_domain(domain_id: str, force: bool = False, db: Db = Depends(current_db)):
    """Soft delete of a UI-added domain: the domain, its targets and its knowledge are deactivated; run history keeps
    the domain id. Repository and platform domains are refused; runs in flight block unless force=true."""
    from services.knowledge.domain_admin import can_delete, repository_pack_names

    row = _domain_row(db, domain_id)
    ok, reason = can_delete(domain_id, row["domain_name"], row["config"], repository_pack_names())
    if not ok:
        raise HTTPException(409, reason)
    active = _domain_active_runs(db, domain_id)
    if active and not force:
        raise HTTPException(409, f"{len(active)} run(s) still use {row['domain_name']}: "
                                 + ", ".join(r["run_name"] for r in active[:5])
                                 + ". Finish or archive them, or delete anyway.")
    with transaction(db):
        # The ids retired by this delete are kept on the domain, so a restore brings back only those (never what a
        # user retired on purpose before the delete).
        retired = json.dumps([r["knowledge_id"] for r in db.query(
            "SELECT KNOWLEDGE_ID FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE DOMAIN_ID = %s AND IS_CURRENT AND STATUS = 'ACTIVE'",
            (domain_id,))])
        db.execute("""UPDATE KNOWLEDGE.DOMAIN_REGISTRY
                         SET ACTIVE_FLAG = FALSE, UPDATED_AT = CURRENT_TIMESTAMP(),
                             CONFIG = OBJECT_INSERT(OBJECT_INSERT(OBJECT_INSERT(COALESCE(CONFIG, OBJECT_CONSTRUCT()),
                                      'deleted_at', CURRENT_TIMESTAMP()::VARCHAR, TRUE), 'deleted_by', CURRENT_USER(), TRUE),
                                      'retired_knowledge', PARSE_JSON(%s), TRUE)
                       WHERE DOMAIN_ID = %s""", (retired, domain_id))
        db.execute("UPDATE KNOWLEDGE.TARGET_TABLE_REGISTRY SET ACTIVE_FLAG = FALSE WHERE DOMAIN_ID = %s", (domain_id,))
        db.execute("UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET STATUS = 'RETIRED', UPDATED_AT = CURRENT_TIMESTAMP() "
                   "WHERE DOMAIN_ID = %s AND IS_CURRENT AND STATUS = 'ACTIVE' "
                   "AND KNOWLEDGE_ID IN (SELECT VALUE::VARCHAR FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))))",
                   (domain_id, retired))
    _domain_caches_changed()
    _domain_snapshot(db, domain_id, "DELETE", "Domain deleted")
    return {"domain_id": domain_id, "deleted": True, "active_runs": len(active)}


@app.post("/api/domains/{domain_id}/restore")
def restore_domain(domain_id: str, db: Db = Depends(current_db)):
    """Undo a delete: the domain, its targets (those with columns) and its retired knowledge come back."""
    row = _domain_row(db, domain_id)
    if not row["config"].get("deleted_at"):
        raise HTTPException(409, f"{row['domain_name']} is not deleted.")
    retired = row["config"].get("retired_knowledge")
    with transaction(db):
        db.execute("""UPDATE KNOWLEDGE.DOMAIN_REGISTRY
                         SET ACTIVE_FLAG = TRUE, UPDATED_AT = CURRENT_TIMESTAMP(),
                             CONFIG = OBJECT_DELETE(CONFIG, 'deleted_at', 'deleted_by', 'retired_knowledge')
                       WHERE DOMAIN_ID = %s""", (domain_id,))
        db.execute("""UPDATE KNOWLEDGE.TARGET_TABLE_REGISTRY T SET ACTIVE_FLAG = TRUE
                       WHERE T.DOMAIN_ID = %s AND EXISTS (SELECT 1 FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY C
                                                           WHERE C.TARGET_TABLE_ID = T.TARGET_TABLE_ID)""", (domain_id,))
        if isinstance(retired, list):
            db.execute("UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET STATUS = 'ACTIVE', UPDATED_AT = CURRENT_TIMESTAMP() "
                       "WHERE DOMAIN_ID = %s AND IS_CURRENT AND STATUS = 'RETIRED' "
                       "AND KNOWLEDGE_ID IN (SELECT VALUE::VARCHAR FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))))",
                       (domain_id, json.dumps(retired)))
        else:  # deleted before the ids were recorded: only items retired at (or after) the delete itself
            db.execute("UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET STATUS = 'ACTIVE', UPDATED_AT = CURRENT_TIMESTAMP() "
                       "WHERE DOMAIN_ID = %s AND IS_CURRENT AND STATUS = 'RETIRED' "
                       "AND UPDATED_AT >= DATEADD(second, -5, TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR))",
                       (domain_id, str(row["config"].get("deleted_at"))))
    _domain_caches_changed()
    _domain_snapshot(db, domain_id, "RESTORE", "Domain restored")
    return {"domain_id": domain_id, "restored": True}


# ---------------------------------------------------------------- Domains: AI review of the pack, Ask the domain


class DomainDecision(BaseModel):
    suggestion_id: Optional[str] = None
    item: dict
    decision: Literal["ACCEPTED", "REJECTED"]
    note: Optional[str] = Field(default=None, max_length=2000)


class DomainQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


def _domain_ai(db: Db, fn, *args):
    try:
        return invoke_source(db, fn, *args)
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/domains/{domain_id}/suggestions")
def domain_suggestions(domain_id: str, db: Db = Depends(current_db)):
    """Earlier AI review of this domain's pack (no model call)."""
    from services.knowledge.domain_ai import review

    return _domain_ai(db, review, domain_id, False, True)


@app.post("/api/domains/{domain_id}/suggestions")
def review_domain(domain_id: str, refresh: bool = False, db: Db = Depends(current_db)):
    """Ask the model to review the pack: one call per pack version, reused until the pack changes."""
    from services.knowledge.domain_ai import review

    return _domain_ai(db, review, domain_id, refresh, False)


@app.post("/api/domains/{domain_id}/suggestions/decision")
def decide_domain_suggestion(domain_id: str, body: DomainDecision, db: Db = Depends(current_db)):
    from services.knowledge.domain_ai import decide

    result = _domain_ai(db, decide, domain_id, body.model_dump_json())
    _domain_caches_changed()
    if body.decision == "ACCEPTED":
        _domain_snapshot(db, domain_id, "SUGGESTION", f"AI suggestion accepted: {str(body.item.get('kind') or '').lower()} "
                                                      f"{body.item.get('title') or body.item.get('name') or ''}".strip())
    return result


@app.post("/api/domains/{domain_id}/ask")
def ask_domain(domain_id: str, body: DomainQuestion, db: Db = Depends(current_db)):
    """Answer a question about the domain from its pack and knowledge, citing only what the model was shown."""
    from services.knowledge.domain_ai import ask

    started = time.time()
    result = _domain_ai(db, ask, domain_id, body.question)
    _record_cost(db, None, "KNOWLEDGE", result.get("model"), result.pop("usage", None), started)
    return result


# ---------------------------------------------------------------- Oracle sources: setup, test, catalog, profile, ingest

_ingest_jobs: dict[str, dict] = {}


class OracleSetup(BaseModel):
    """How the Snowflake runtime gets the password and network access: new objects, or ones that already exist."""
    secret_mode: Literal["new", "existing"] = "new"
    password: Optional[str] = Field(default=None, max_length=1024)
    secret: Optional[str] = Field(default=None, max_length=255)
    integration_mode: Literal["new", "existing"] = "new"
    external_access_integration: Optional[str] = Field(default=None, max_length=255)


class OraclePassword(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class OracleConnection(BaseModel):
    config: dict[str, Any]


class ConnectString(BaseModel):
    text: str = Field(min_length=3, max_length=4000)


class IntegrationCheck(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    secret: Optional[str] = Field(default=None, max_length=255)


class OracleTables(BaseModel):
    tables: list[str] = Field(min_length=1, max_length=500)


class OraclePreview(BaseModel):
    table: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=20, ge=1, le=100)


class OracleIngest(OracleTables):
    mode: Literal["replace", "append", "merge"] = "replace"
    storage: Literal["MANAGED", "ICEBERG"] = "MANAGED"
    watermark_columns: dict[str, str] = Field(default_factory=dict)
    merge_keys: dict[str, list[str]] = Field(default_factory=dict)
    lookback_minutes: int = Field(default=0, ge=0, le=10080)
    profile_after_landing: bool = True


class OracleSchedule(OracleTables):
    mode: Literal["append", "merge", "replace"] = "append"
    cron: str = Field(min_length=9, max_length=120)
    watermark_columns: dict[str, str] = Field(default_factory=dict)
    merge_keys: dict[str, list[str]] = Field(default_factory=dict)
    lookback_minutes: int = Field(default=0, ge=0, le=10080)
    warehouse: Optional[str] = Field(default=None, max_length=255)


_oracle_running: dict[str, str] = {}   # source id -> job id of its running profile/load


def _oracle_source(db: Db, source_id: str) -> dict:
    found = db.query("SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, CONNECTION_TYPE, CONFIGURATION_JSON "
                     "FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = %s AND ACTIVE_FLAG", (source_id,))
    if not found or found[0]["connection_type"] != "oracle":
        raise HTTPException(404, "Oracle source not found")
    src = found[0]
    src["config"] = _json(src["configuration_json"]) or {}
    return src


def _save_oracle_config(db: Db, source_id: str, change) -> dict:
    """Read-modify-write of the source configuration (procedures write loads into it too)."""
    cfg = _oracle_source(db, source_id)["config"]
    change(cfg)
    db.execute("UPDATE SOURCE.SOURCE_REGISTRY SET CONFIGURATION_JSON = PARSE_JSON(%s), CONFIGURATION_REFERENCE = %s, "
               "UPDATED_AT = CURRENT_TIMESTAMP() WHERE SOURCE_SYSTEM_ID = %s",
               (json.dumps(cfg, default=str), cfg.get("secret") or cfg.get("password_env"), source_id))
    return cfg


def _call_cancellable(db: Db, sql: str, params: tuple, job: Optional[dict]) -> Any:
    """CALL a procedure asynchronously so a running query can be cancelled from the job (SYSTEM$CANCEL_QUERY)."""
    cur = db.conn.cursor()
    try:
        cur.execute_async(sql, params)
        query_id = cur.sfqid
        if job is not None:
            job["query_id"] = query_id
        while db.conn.is_still_running(db.conn.get_query_status(query_id)):
            if job is not None and job.get("cancel_requested"):
                db.query("SELECT SYSTEM$CANCEL_QUERY(%s)", (query_id,))
            time.sleep(0.5)
        cur.get_results_from_sfqid(query_id)
        row = cur.fetchone()
        value = row[0] if row else None
        return json.loads(value) if isinstance(value, str) else value
    finally:
        if job is not None:
            job.pop("query_id", None)
        cur.close()


def _oracle_call(db: Db, source_id: str, action: str, payload: dict, progress=None, job: Optional[dict] = None) -> dict:
    """Run an Oracle action in the source's runtime: its Snowflake procedure, or this API host."""
    from services.source.oracle.procedures import api_host_password, object_names, run

    cfg = _oracle_source(db, source_id)["config"]
    cancelled = (lambda: bool(job and job.get("cancel_requested")))
    if cfg.get("runtime", "snowflake") == "api_host":
        try:
            password = api_host_password(cfg)
        except AssertionError as exc:
            if action not in ("test", "diagnose"):
                raise HTTPException(409, str(exc)) from exc
            password = ""
        return invoke_source(db, run, source_id, action, json.dumps(payload), password, progress, cancelled)
    if not cfg.get("oracle_procedure"):
        raise HTTPException(409, "Finish the Snowflake setup for this source first (secret, network access and "
                                 "procedure).")
    proc = object_names(cfg["database"], cfg["schema"])["procedure"]
    params = (source_id, action, json.dumps(payload))
    if job is not None:
        try:
            return _call_cancellable(db, f"CALL {proc}(%s, %s, %s)", params, job)
        except AttributeError:  # connector without async support
            pass
    result = db.call(f"CALL {proc}(%s, %s, %s)", params)
    return _json(result) if isinstance(result, str) else result


def _oracle_errors(fn):
    from services.source.oracle.errors import explain

    try:
        return fn()
    except HTTPException:
        raise
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        why = explain(exc)
        if why["fix"]:
            raise HTTPException(why["status"] if why["status"] >= 400 else 400,
                                f"{why['title']}. {why['fix']}" + (f" ({why['code']})" if why["code"] else "")) from exc
        raise _snowflake_error(exc) from exc


# ---- discovery: what already exists in Snowflake for the access step


_DISCOVERY_CACHE: dict[str, tuple[float, Any]] = {}


def _discover(db: Db, key: str, sql: str, refresh: bool = False) -> list:
    """SHOW ... IN ACCOUNT can take seconds on a big account: cached for five minutes per role."""
    cache_key = f"{db.role}:{key}"
    hit = _DISCOVERY_CACHE.get(cache_key)
    if hit and not refresh and time.time() - hit[0] < 300:
        return hit[1]
    found = db.query(sql)
    _DISCOVERY_CACHE[cache_key] = (time.time(), found)
    return found


@app.get("/api/oracle/secrets")
def oracle_secrets(refresh: bool = False, db: Db = Depends(current_db)):
    """PASSWORD secrets this role can see (names only; secret values are never readable)."""
    try:
        found = _discover(db, "secrets", "SHOW SECRETS IN ACCOUNT", refresh)
    except Exception as exc:
        return {"secrets": [], "error": f"This role cannot list secrets: {str(exc)[:200]}"}
    out = []
    for r in found:
        kind = str(r.get("secret_type") or r.get("type") or "").upper()
        if kind and kind != "PASSWORD":
            continue
        out.append({"name": f"{r.get('database_name')}.{r.get('schema_name')}.{r.get('name')}",
                    "comment": r.get("comment"), "owner": r.get("owner"),
                    "created_on": str(r.get("created_on") or "")[:19]})
    return {"secrets": sorted(out, key=lambda s: s["name"])}


@app.get("/api/oracle/secrets/describe")
def oracle_secret_describe(name: str, db: Db = Depends(current_db)):
    """The user name stored in a PASSWORD secret (DESCRIBE never returns the password)."""
    from services.source.oracle.procedures import NAME

    if not NAME.match(name):
        raise HTTPException(400, "secret must be a Snowflake object name")
    try:
        props = {str(r.get("property") or "").upper(): r.get("value") for r in db.query(f"DESCRIBE SECRET {name}")}
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"name": name, "username": props.get("USERNAME"), "type": props.get("SECRET_TYPE")}


@app.get("/api/oracle/integrations")
def oracle_integrations(refresh: bool = False, db: Db = Depends(current_db)):
    try:
        found = _discover(db, "integrations", "SHOW EXTERNAL ACCESS INTEGRATIONS", refresh)
    except Exception as exc:
        return {"integrations": [], "error": f"This role cannot list integrations: {str(exc)[:200]}"}
    return {"integrations": sorted([{"name": r.get("name"), "enabled": str(r.get("enabled")).lower() == "true",
                                     "comment": r.get("comment")} for r in found], key=lambda r: r["name"])}


def _rule_allows(value: str, host: str, port: int) -> bool:
    """A HOST_PORT network rule value ('host', 'host:port', '*.domain:port'; no port means 443) vs host:port."""
    rule_host, _, rule_port = value.lower().partition(":")
    if int(rule_port or 443) != int(port):
        return False
    host = host.lower()
    return rule_host == host or (rule_host.startswith("*.") and host.endswith(rule_host[1:]))


@app.post("/api/oracle/integrations/check")
def oracle_integration_check(body: IntegrationCheck, db: Db = Depends(current_db)):
    """Does an existing integration allow this Oracle listener and this secret? Each answer separately."""
    from services.source.oracle.procedures import NAME

    if not NAME.match(body.name) or (body.secret and not NAME.match(body.secret)):
        raise HTTPException(400, "names must be Snowflake object names")
    try:
        props = {str(r.get("property") or "").upper(): str(r.get("property_value") or r.get("value") or "")
                 for r in db.query(f"DESCRIBE INTEGRATION {body.name}")}
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    rules = [x.strip() for x in props.get("ALLOWED_NETWORK_RULES", "").strip("[]").split(",") if x.strip()]
    secrets = [x.strip().upper() for x in props.get("ALLOWED_AUTHENTICATION_SECRETS", "").strip("[]").split(",")
               if x.strip()]
    reachable, values, unreadable = False, [], []
    for rule in rules:
        try:
            for r in db.query(f"DESCRIBE NETWORK RULE {rule}"):
                vals = [v.strip().strip("'\"").lower() for v in str(r.get("value_list") or "").split(",") if v.strip()]
                values += vals
                reachable = reachable or any(_rule_allows(v, body.host, body.port) for v in vals)
        except Exception:
            unreadable.append(rule)  # DESCRIBE NETWORK RULE needs OWNERSHIP; the connection test settles it
    allows_host: Optional[bool] = True if reachable else (None if unreadable else False)
    secret_ok = None
    if body.secret:
        short = body.secret.upper().split(".")[-1]
        secret_ok = any(s == body.secret.upper() or s.split(".")[-1] == short for s in secrets) or "ALL" in secrets
    enabled = props.get("ENABLED", "true").lower() == "true"
    return {"name": body.name, "enabled": enabled, "allows_host": allows_host, "allows_secret": secret_ok,
            "network_values": values[:20], "secrets": secrets[:20], "unreadable_rules": unreadable,
            "usable": enabled and allows_host is not False and secret_ok is not False,
            "note": (f"This role cannot read {', '.join(unreadable)} (DESCRIBE needs ownership), so the allowed host is "
                     "not verified here; the connection test confirms it.") if unreadable and not reachable else None}


@app.post("/api/oracle/parse")
def oracle_parse(body: ConnectString):
    from services.source.oracle.connect import parse_connect_string

    try:
        return parse_connect_string(body.text)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/oracle/env-check")
def oracle_env_check(name: str, db: Db = Depends(current_db)):
    """Whether an environment variable is set on the API host (never its value)."""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$", name):
        raise HTTPException(400, "not an environment variable name")
    return {"name": name, "present": bool(os.environ.get(name))}


# ---- one source: setup, credentials, connection, health


@app.get("/api/sources/{source_id}/oracle")
def oracle_overview(source_id: str, db: Db = Depends(current_db)):
    """Everything the source page shows without contacting Oracle: connection, access, health, loads, schedule."""
    src = _oracle_source(db, source_id)
    cfg = src["config"]
    with _jobs_lock:
        running = _oracle_running.get(source_id)
        job = _ingest_jobs.get(running) if running else None
    return {"id": source_id, "name": src["source_system_name"],
            "connection": {k: cfg.get(k) for k in ("host", "port", "service_name", "sid", "user", "schema_owner",
                                                   "protocol", "ssl_server_dn_match", "runtime", "password_env",
                                                   "wallet_dir")},
            "access": {"secret": cfg.get("secret"), "integration": cfg.get("external_access_integration"),
                       "procedure": cfg.get("oracle_procedure"), "created": cfg.get("created_objects") or [],
                       "ready": bool(cfg.get("oracle_procedure")) or cfg.get("runtime") == "api_host",
                       "password_env_present": bool(os.environ.get(cfg["password_env"]))
                       if cfg.get("password_env") else None},
            "landing": {"database": cfg.get("database"), "schema": cfg.get("schema")},
            "health": cfg.get("health"), "loads": cfg.get("loads") or {}, "history": cfg.get("load_history") or {},
            "schedule": cfg.get("schedule"), "last_scheduled_run": cfg.get("last_scheduled_run"),
            "last_job": cfg.get("last_job"),
            "running_job": json.loads(json.dumps(job, default=str)) if job and job["status"] == "RUNNING" else None}


@app.post("/api/sources/{source_id}/oracle/setup")
def oracle_setup(source_id: str, body: OracleSetup, db: Db = Depends(current_db)):
    """Snowflake runtime: the PASSWORD secret (new, or an existing one), network access (a new network rule and
    integration, or an existing integration that allows this listener and secret) and the source's procedure.
    A new password is used only while its statement runs and is scrubbed from any error."""
    from services.source.oracle.procedures import NAME, object_names, procedure_sql, setup_sql

    src = _oracle_source(db, source_id)
    cfg = src["config"]
    if cfg.get("runtime", "snowflake") != "snowflake":
        raise HTTPException(409, "This source runs on the API host; its password comes from an environment variable.")
    names = object_names(cfg["database"], cfg["schema"])
    if body.secret_mode == "new" and not body.password:
        raise HTTPException(400, "enter the Oracle password, or choose an existing secret")
    if body.secret_mode == "existing" and not (body.secret and NAME.match(body.secret)):
        raise HTTPException(400, "choose the existing secret")
    secret = body.secret if body.secret_mode == "existing" else names["secret"]
    eai = (body.external_access_integration or f"{src['source_system_name']}_ORACLE_ACCESS").strip().upper()
    if body.integration_mode == "existing":
        check = oracle_integration_check(IntegrationCheck(name=eai, host=cfg["host"], port=int(cfg.get("port") or 1521),
                                                          secret=secret), db)
        problems = [p for p, bad in (("it is disabled", not check["enabled"]),
                                     (f"its network rules do not allow {cfg['host']}:{cfg.get('port')}",
                                      check["allows_host"] is False),
                                     (f"it does not allow the secret {secret}", check["allows_secret"] is False)) if bad]
        if problems:
            raise HTTPException(409, f"{eai} cannot be used: " + "; ".join(problems) +
                                ". Choose another integration or let the platform create one.")
    password = body.password or ""
    escaped = password.replace("\\", "\\\\").replace("'", "''")
    try:
        statements = setup_sql(cfg["database"], cfg["schema"], eai, cfg["host"], int(cfg.get("port") or 1521),
                               cfg["user"], secret=secret, create_secret=body.secret_mode == "new",
                               create_integration=body.integration_mode == "new")
        ddl = procedure_sql(cfg["database"], cfg["schema"], _services_import(db), eai, secret)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    log: list[dict] = []
    for sql in [*statements, ddl]:
        try:
            db.execute(sql.replace("'<oracle password>'", "'" + escaped + "'"))
            log.append({"sql": sql, "ok": True})
        except Exception as exc:
            text = str(exc)[:500].replace(password, "***") if password else str(exc)[:500]
            log.append({"sql": sql, "ok": False, "error": text})
            step = next((label for prefix, label in (
                ("CREATE OR REPLACE SECRET", "CREATE SECRET"), ("CREATE OR REPLACE NETWORK RULE", "CREATE NETWORK RULE"),
                ("CREATE OR REPLACE EXTERNAL ACCESS", "CREATE INTEGRATION")) if sql.startswith(prefix)),
                "CREATE PROCEDURE (or the oracledb package)")
            grants = []
            if step.startswith("CREATE PROCEDURE") and "privilege" in text.lower():
                role = (db.query("SELECT CURRENT_ROLE() AS R")[0]["r"] or "").upper()
                grants = [f"GRANT USAGE ON INTEGRATION {eai} TO ROLE {role}",
                          f"GRANT USAGE ON DATABASE {secret.split('.')[0]} TO ROLE {role}",
                          f"GRANT USAGE ON SCHEMA {'.'.join(secret.split('.')[:2])} TO ROLE {role}",
                          f"GRANT READ ON SECRET {secret} TO ROLE {role}"] if secret.count(".") == 2 else []
            return {"ready": False, "log": log, "failed_step": step, "grants": grants,
                    "detail": (f"This role may not use {eai} or {secret}. An admin can grant it (statements shown), "
                               "then run setup again." if grants else
                               f"This role could not run {step}. An admin can run the statements shown (with the real "
                               "password in place of the placeholder), then choose Use existing for the secret and "
                               "integration here.")}
    created = (["secret"] if body.secret_mode == "new" else []) + (["integration"] if body.integration_mode == "new" else [])

    def change(c: dict) -> None:
        c.update(oracle_procedure=names["procedure"], external_access_integration=eai, secret=secret,
                 created_objects=sorted(set(c.get("created_objects") or []) | set(created)))

    _save_oracle_config(db, source_id, change)
    if created:
        _DISCOVERY_CACHE.clear()
    return {"ready": True, "log": log, "secret": secret, "integration": eai}


@app.post("/api/sources/{source_id}/oracle/password")
def oracle_password(source_id: str, body: OraclePassword, db: Db = Depends(current_db)):
    """Point the source's secret at a new Oracle password (after a rotation in Oracle)."""
    from services.source.oracle.procedures import rotate_password_sql

    cfg = _oracle_source(db, source_id)["config"]
    if cfg.get("runtime") == "api_host":
        raise HTTPException(409, f"Set the new password in {cfg.get('password_env')} on the API host and restart it.")
    if not cfg.get("secret"):
        raise HTTPException(409, "This source has no secret yet; finish setup first.")
    escaped = body.password.replace("\\", "\\\\").replace("'", "''")
    try:
        db.execute(rotate_password_sql(cfg["secret"]).replace("'<oracle password>'", "'" + escaped + "'"))
    except Exception as exc:
        raise HTTPException(403, f"Could not update {cfg['secret']}: {str(exc)[:300].replace(body.password, '***')}") from exc
    return {"updated": cfg["secret"]}


@app.put("/api/sources/{source_id}/oracle/connection")
def oracle_connection(source_id: str, body: OracleConnection, db: Db = Depends(current_db)):
    """Change where Oracle is (host, port, service, TLS, owner). The network rule follows when the platform owns it."""
    from services.source.external import validate_config
    from services.source.oracle.procedures import network_rule_sql

    src = _oracle_source(db, source_id)
    cfg = src["config"]
    editable = ("host", "port", "service_name", "sid", "user", "schema_owner", "protocol", "ssl_server_dn_match",
                "password_env", "wallet_dir", "wallet_password_env")
    merged = {k: v for k, v in cfg.items() if k in editable}
    merged.update({k: v for k, v in body.config.items() if k in editable})
    if "sid" in body.config and body.config.get("sid"):
        merged.pop("service_name", None)
    if "service_name" in body.config and body.config.get("service_name"):
        merged.pop("sid", None)
    merged["runtime"] = cfg.get("runtime", "snowflake")
    try:
        clean = validate_config("oracle", merged)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    moved = (clean["host"], str(clean["port"])) != (cfg.get("host"), str(cfg.get("port")))
    note = None
    if moved and clean["runtime"] == "snowflake" and cfg.get("oracle_procedure"):
        if "integration" in (cfg.get("created_objects") or []):
            try:
                db.execute(network_rule_sql(cfg["database"], cfg["schema"], clean["host"], int(clean["port"])))
            except Exception as exc:
                raise HTTPException(403, f"Could not update the network rule: {str(exc)[:300]}") from exc
        else:
            note = (f"{cfg.get('external_access_integration')} was chosen, not created here: make sure it allows "
                    f"{clean['host']}:{clean['port']}.")

    def change(c: dict) -> None:
        for k in editable:
            c.pop(k, None)
        c.update({k: v for k, v in clean.items() if k in editable})
        c["health"] = None

    _save_oracle_config(db, source_id, change)
    return {"connection": {k: clean.get(k) for k in editable}, "note": note}


@app.post("/api/sources/{source_id}/oracle/test")
def oracle_test(source_id: str, db: Db = Depends(current_db)):
    """Step-by-step connection check. Never raises for Oracle problems: they come back as failed steps."""
    from services.source.oracle.errors import explain

    started = time.time()
    try:
        result = _oracle_call(db, source_id, "diagnose", {})
    except HTTPException as exc:
        result = {"ok": False, "status": "fail", "server": {}, "headline": exc.detail,
                  "checks": [{"id": "setup", "label": "Platform setup", "status": "fail", "detail": exc.detail,
                              "fix": None}]}
    except Exception as exc:
        why = explain(exc)
        result = {"ok": False, "status": "fail", "server": {}, "headline": why["title"],
                  "checks": [{"id": "runtime", "label": "Runtime", "status": "fail", "detail": why["title"],
                              "fix": why["fix"], "code": why["code"], "error": why["detail"]}]}
    return {**result, "elapsed_ms": int((time.time() - started) * 1000)}


@app.delete("/api/sources/{source_id}/oracle")
def oracle_remove(source_id: str, drop_landed: bool = False, db: Db = Depends(current_db)):
    """Remove the source: its schedule and procedure, the secret / integration the platform created (never ones that
    were chosen), and optionally the landed schema. Profiles stay in the profile store."""
    from services.source.oracle.procedures import cleanup_sql

    src = _oracle_source(db, source_id)
    cfg = src["config"]
    with _jobs_lock:
        if _oracle_running.get(source_id):
            raise HTTPException(409, "A load or profile is running for this source; stop it first.")
    log = []
    statements = cleanup_sql(cfg)
    if drop_landed and cfg.get("database") and cfg.get("schema"):
        statements.append(f"DROP SCHEMA IF EXISTS {cfg['database']}.{cfg['schema']}")
    for sql in statements:
        try:
            db.execute(sql)
            log.append({"sql": sql, "ok": True})
        except Exception as exc:
            log.append({"sql": sql, "ok": False, "error": str(exc)[:300]})
    db.execute("UPDATE SOURCE.SOURCE_REGISTRY SET ACTIVE_FLAG = FALSE, UPDATED_AT = CURRENT_TIMESTAMP() "
               "WHERE SOURCE_SYSTEM_ID = %s", (source_id,))
    return {"removed": src["source_system_name"], "log": log}


# ---- tables


@app.post("/api/sources/{source_id}/oracle/catalog")
def oracle_catalog(source_id: str, db: Db = Depends(current_db)):
    """Tables, views and materialized views of the schema owner with estimates, keys and suggested watermarks, plus
    what was profiled or landed already."""
    result = _oracle_errors(lambda: _oracle_call(db, source_id, "catalog", {}))
    src = _oracle_source(db, source_id)
    cfg = src["config"]
    try:
        profiled = {r["table_name"]: r for r in db.query(
            """SELECT TABLE_NAME, ROW_COUNT, STATUS, PROFILED_AT::VARCHAR AS PROFILED_AT, PII_COLUMNS, KEY_CANDIDATES,
                      PROFILE_STAGE_PATH FROM METADATA.TABLE_PROFILES WHERE SOURCE_NAME = %s""",
            (f"ORACLE_{src['source_system_name']}",))}
    except Exception:
        profiled = {}
    loads = cfg.get("loads") or {}
    by_oracle = {v.get("oracle_table", "").split(".", 1)[-1]: {**v, "landed_as": k} for k, v in loads.items()}
    for t in result.get("tables", []):
        t["profile"] = profiled.get(t["table"])
        t["load"] = by_oracle.get(t["table"])
    return {**result, "landing": {"database": cfg.get("database"), "schema": cfg.get("schema")},
            "runtime": cfg.get("runtime", "snowflake"), "ready": bool(cfg.get("oracle_procedure"))
            or cfg.get("runtime") == "api_host"}


@app.post("/api/sources/{source_id}/oracle/columns")
def oracle_columns(source_id: str, body: OracleTables, db: Db = Depends(current_db)):
    return _oracle_errors(lambda: _oracle_call(db, source_id, "columns", {"tables": body.tables}))


@app.post("/api/sources/{source_id}/oracle/preview")
def oracle_preview(source_id: str, body: OraclePreview, db: Db = Depends(current_db)):
    """The first rows of a table, read live from Oracle with the same conversions as a load."""
    return _oracle_errors(lambda: _oracle_call(db, source_id, "preview", {"tables": [body.table], "limit": body.limit}))


@app.get("/api/sources/{source_id}/oracle/profile")
def oracle_profile_doc(source_id: str, table: str, db: Db = Depends(current_db)):
    """The in-place Oracle profile document of one table (same shape as Snowflake profiles)."""
    src = _oracle_source(db, source_id)
    found = db.query("SELECT PROFILE_STAGE_PATH FROM METADATA.TABLE_PROFILES WHERE SOURCE_NAME = %s AND TABLE_NAME = %s "
                     "AND STATUS <> 'PROFILING' ORDER BY PROFILED_AT DESC LIMIT 1",
                     (f"ORACLE_{src['source_system_name']}", table))
    if not found or not found[0]["profile_stage_path"]:
        raise HTTPException(404, f"{table} has not been profiled in Oracle yet")
    doc = _read_stage_doc(db, found[0]["profile_stage_path"])
    if not doc:
        raise HTTPException(404, "profile document not found")
    from services.profiling import insights

    return {"profile": doc, "scorecard": insights.scorecard(insights.quality_dimensions(doc), None)}


# ---- jobs: profile in Oracle, extract and land


def _start_oracle_job(db: Db, source_id: str, kind: str, tables: list[str], payload: dict) -> dict:
    with _jobs_lock:
        running = _oracle_running.get(source_id)
        if running and _ingest_jobs.get(running, {}).get("status") == "RUNNING":
            raise HTTPException(409, "A profile or load is already running for this source; wait for it or stop it.")
        job_id = str(uuid.uuid4())
        job = {"job_id": job_id, "source_id": source_id, "kind": kind, "status": "RUNNING", "started_at": time.time(),
               "finished_at": None, "tables": {t: {"phase": "QUEUED"} for t in tables}, "result": None, "error": None,
               "options": {k: v for k, v in payload.items() if k != "tables"}, "cancel_requested": False}
        _ingest_jobs[job_id] = job
        _oracle_running[source_id] = job_id

    def progress(event: dict) -> None:
        with _jobs_lock:
            entry = job["tables"].setdefault(event.get("table", "?"), {})
            if event.get("phase") == "EXTRACTING" and "rows" in event:
                elapsed = max(0.001, time.time() - entry.setdefault("extract_started", time.time()))
                entry["rows_per_s"] = int(int(event["rows"]) / elapsed)
            entry.update({k: v for k, v in event.items() if k != "table"})

    def work() -> None:
        results = []
        try:
            for table in tables:  # one table per call: progress per table in either runtime
                if job["cancel_requested"]:
                    progress({"table": table, "phase": "CANCELLED"})
                    results.append({"table": table, "status": "CANCELLED"})
                    continue
                progress({"table": table, "phase": "EXTRACTING" if kind == "ingest" else "PROFILING",
                          "started": time.time()})
                try:
                    out = _oracle_call(db, source_id, "extract" if kind == "ingest" else "profile",
                                       {**payload, "tables": [table]}, progress, job)
                    row = (out.get("tables") or [{}])[0]
                except HTTPException as exc:
                    row = {"table": table, "status": "FAILED", "error": exc.detail}
                except Exception as exc:
                    from services.source.oracle.errors import explain

                    cancelled = job["cancel_requested"] and "cancel" in str(exc).lower()
                    row = {"table": table, "status": "CANCELLED" if cancelled else "FAILED", "error": str(exc)[:600],
                           "explain": None if cancelled else explain(exc)}
                if kind == "ingest" and row.get("status") == "LANDED" and payload.get("profile_after_landing") \
                        and not job["cancel_requested"]:
                    progress({"table": table, "phase": "PROFILING"})
                    try:
                        landed = row["landed_as"].rsplit(".", 1)[-1]
                        profile_source_tables(source_id, SourceProfileRequest(tables=[landed], force_refresh=True,
                                                                               wait=True), db)
                        row["profiled"] = True
                    except Exception as exc:
                        row["profile_error"] = str(exc)[:300]
                done = row.get("status") in ("LANDED", "PROFILED")
                progress({"table": table, "phase": "DONE" if done else row.get("status", "FAILED"),
                          "finished": time.time(),
                          **{k: row.get(k) for k in ("rows_loaded", "rows_extracted", "row_count", "error", "explain",
                                                     "verification", "drift", "merged", "skipped", "duration_s",
                                                     "note", "profiled", "profile_error")}})
                results.append(row)
            statuses = {r.get("status") for r in results}
            job["status"] = ("CANCELLED" if job["cancel_requested"] else
                             "DONE" if statuses <= {"LANDED", "PROFILED"} else
                             "FAILED" if not statuses & {"LANDED", "PROFILED"} else "PARTIAL")
        except Exception as exc:
            job["status"], job["error"] = "FAILED", str(exc)[:800]
        finally:
            job["result"], job["finished_at"] = results, time.time()
            with _jobs_lock:
                if _oracle_running.get(source_id) == job_id:
                    _oracle_running.pop(source_id, None)
            summary = {"job_id": job_id, "kind": kind, "status": job["status"],
                       "at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                       "tables": len(tables), "failed": [r["table"] for r in results
                                                         if r.get("status") not in ("LANDED", "PROFILED")],
                       "rows": sum(int(r.get("rows_loaded") or 0) for r in results),
                       "duration_s": round(job["finished_at"] - job["started_at"], 1)}
            try:
                _save_oracle_config(db, source_id, lambda c: c.update(last_job=summary))
            except Exception:
                pass

    threading.Thread(target=work, name=f"oracle-{kind}", daemon=True).start()
    return {"job_id": job_id, "status": "RUNNING", "tables": tables}


@app.post("/api/sources/{source_id}/oracle/profile")
def oracle_profile(source_id: str, body: OracleTables, db: Db = Depends(current_db)):
    """Profile the chosen tables inside Oracle (sampled above 200k rows); only statistics leave Oracle."""
    _oracle_source(db, source_id)
    return _start_oracle_job(db, source_id, "profile", body.tables, {})


@app.post("/api/sources/{source_id}/oracle/ingest")
def oracle_ingest(source_id: str, body: OracleIngest, db: Db = Depends(current_db)):
    """Extract the chosen tables to Parquet, land them (replace, append or merge), verify and profile them."""
    _oracle_source(db, source_id)
    return _start_oracle_job(db, source_id, "ingest", body.tables,
                             {"mode": body.mode, "storage": body.storage, "watermark_columns": body.watermark_columns,
                              "merge_keys": body.merge_keys, "lookback_minutes": body.lookback_minutes,
                              "profile_after_landing": body.profile_after_landing})


@app.get("/api/ingest-jobs/{job_id}")
def ingest_job(job_id: str, db: Db = Depends(current_db)):
    with _jobs_lock:
        job = _ingest_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found (jobs are kept while the API runs)")
        return json.loads(json.dumps(job, default=str))


@app.post("/api/ingest-jobs/{job_id}/cancel")
def cancel_ingest_job(job_id: str, db: Db = Depends(current_db)):
    """Stop after the current batch (API host) or cancel the running Snowflake call; later tables are skipped."""
    with _jobs_lock:
        job = _ingest_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        if job["status"] != "RUNNING":
            return {"job_id": job_id, "status": job["status"]}
        job["cancel_requested"] = True
    return {"job_id": job_id, "status": "CANCELLING"}


# ---- schedules (Snowflake runtime): a task calls the source's procedure


@app.put("/api/sources/{source_id}/oracle/schedule")
def oracle_schedule(source_id: str, body: OracleSchedule, db: Db = Depends(current_db)):
    from services.source.oracle.procedures import schedule_sql

    cfg = _oracle_source(db, source_id)["config"]
    if cfg.get("runtime") == "api_host":
        raise HTTPException(409, "Schedules run inside Snowflake; this source runs on the API host.")
    if not cfg.get("oracle_procedure"):
        raise HTTPException(409, "Finish the Snowflake setup first.")
    warehouse = (body.warehouse or (db.query("SELECT CURRENT_WAREHOUSE() AS W")[0]["w"] or "")).upper()
    payload = {"tables": body.tables, "mode": body.mode, "watermark_columns": body.watermark_columns,
               "merge_keys": body.merge_keys, "lookback_minutes": body.lookback_minutes, "scheduled": True}
    try:
        statements = schedule_sql(cfg["database"], cfg["schema"], source_id, warehouse, body.cron, payload)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    for sql in statements:
        try:
            db.execute(sql)
        except Exception as exc:
            raise HTTPException(403, f"Could not create the schedule (needs CREATE TASK on the schema and EXECUTE TASK "
                                     f"on the account): {str(exc)[:300]}") from exc
    schedule = {"cron": body.cron, "tables": body.tables, "mode": body.mode, "warehouse": warehouse,
                "watermark_columns": body.watermark_columns, "merge_keys": body.merge_keys,
                "lookback_minutes": body.lookback_minutes, "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
    _save_oracle_config(db, source_id, lambda c: c.update(schedule=schedule))
    return {"schedule": schedule}


@app.delete("/api/sources/{source_id}/oracle/schedule")
def oracle_unschedule(source_id: str, db: Db = Depends(current_db)):
    from services.source.oracle.procedures import unschedule_sql

    cfg = _oracle_source(db, source_id)["config"]
    for sql in unschedule_sql(cfg["database"], cfg["schema"]):
        try:
            db.execute(sql)
        except Exception as exc:
            raise _snowflake_error(exc) from exc
    _save_oracle_config(db, source_id, lambda c: c.update(schedule=None))
    return {"schedule": None}



def _fetch_with_ids(db: Db):
    """A fetch_rows for helpers that call AI_COMPLETE themselves, remembering each statement's query id."""
    ids: list[str] = []

    def fetch(sql: str, params: tuple = ()):
        found, query_id = db.query_with_id(sql, params)
        if query_id:
            ids.append(query_id)
        return found

    return fetch, ids


# ---------------------------------------------------------------- Admin: models available to this account

_MODELS_CACHE: dict = {"at": 0.0, "data": None}
_RECONCILE_STATE: dict = {"at": 0.0, "last": None}


class ModelTest(BaseModel):
    model: str = Field(min_length=2, max_length=128)


def _account_models(db: Db, refresh: bool = False) -> dict:
    """Models this account can use: SHOW MODELS / inference profiles, the Cortex catalog, and the account's
    CORTEX_MODELS_ALLOWLIST. Cached for ten minutes."""
    from services.common.models import account_models

    if not refresh and _MODELS_CACHE["data"] and time.time() - _MODELS_CACHE["at"] < 600:
        return _MODELS_CACHE["data"]

    def execute(sql: str):
        return db.query(sql)

    data = account_models(execute, str(_config(db, "LLM_MODEL", "") or ""))
    _MODELS_CACHE.update(at=time.time(), data=data)
    return data


@app.get("/api/config/models")
def config_models(refresh: bool = False, db: Db = Depends(current_db)):
    from services.common.llm import STAGES

    data = _account_models(db, refresh)
    return {**data, "default": _model_for(db), "by_stage": _config(db, "LLM_MODEL_BY_STAGE", {}) or {},
            "stages": list(STAGES)}


@app.post("/api/config/models/test")
def test_model(body: ModelTest, db: Db = Depends(current_db)):
    """A tiny AI_COMPLETE to prove the model answers in this account's region (costs a few tokens)."""
    started = time.time()
    try:
        found, query_id = db.query_with_id(
            "SELECT AI_COMPLETE(model => %s, prompt => 'Reply with the single word OK.', "
            "model_parameters => {'temperature': 0, 'max_tokens': 5}, show_details => TRUE) AS R", (body.model,))
    except Exception as exc:
        text = str(exc)
        reason = ("not available in this account or region" if "unknown model" in text.lower()
                  or "not supported" in text.lower() or "unavailable" in text.lower() else text[:300])
        return {"model": body.model, "ok": False, "error": reason, "latency_ms": int((time.time() - started) * 1000)}
    details = _json(found[0]["r"]) if found else {}
    _record_cost(db, None, "ADMIN", body.model, {**((details or {}).get("usage") or {}), "query_id": query_id}, started)
    reply = (((details or {}).get("choices") or [{}])[0].get("messages") or "")
    return {"model": body.model, "ok": True, "reply": str(reply)[:40], "latency_ms": int((time.time() - started) * 1000),
            "usage": (details or {}).get("usage")}


@app.post("/api/costs/reconcile")
def reconcile_costs(db: Db = Depends(current_db)):
    """Actual credits from Snowflake's Cortex usage views by query id; calls that had no rate get an estimate."""
    from services.common.cost import backfill_estimates, calibrated_rates, reconcile

    query = (lambda sql, params: db.query(sql, params))
    result = reconcile(query, lambda sql, params: db.execute(sql, params))
    calibrated = calibrated_rates(query)
    if calibrated and calibrated != (_config(db, "CALIBRATED_RATES", {}) or {}):
        _put_config(db, "CALIBRATED_RATES", calibrated, "Billed credits per million tokens, learned on reconcile")
    result["calibrated_rates"] = calibrated
    try:
        result["estimates_filled"] = backfill_estimates(
            query, lambda sql, params: db.execute(sql, params),
            _config(db, "RATE_CARD", {}) or {}, _config(db, "CREDITS_PER_MILLION_TOKENS", {}) or {}, calibrated)
    except Exception as exc:
        result["estimates_filled"] = 0
        result["backfill_error"] = str(exc)[:200]
    result["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _RECONCILE_STATE.update(at=time.time(), last=result)
    return result


def _reconcile_if_due(db: Db) -> None:
    """At most hourly, on the way into cost views (Snowflake's usage views lag; more often adds nothing)."""
    if time.time() - _RECONCILE_STATE["at"] < 3600:
        return
    _RECONCILE_STATE["at"] = time.time()

    def run():
        try:
            reconcile_costs(db)
        except Exception:
            pass

    threading.Thread(target=run, name="cost-reconcile", daemon=True).start()


from app.governance import router as governance_router  # noqa: E402

app.include_router(governance_router)

from app.skills_api import router as skills_router  # noqa: E402

app.include_router(skills_router)

from app.skill_builder_api import router as skill_builder_router  # noqa: E402

app.include_router(skill_builder_router)

from app.knowledge_api import router as knowledge_router  # noqa: E402

app.include_router(knowledge_router)

from app.domains_api import router as domains_router  # noqa: E402

app.include_router(domains_router)

from app.code_api import router as code_router  # noqa: E402

app.include_router(code_router)

from app.jira_api import router as jira_router  # noqa: E402

app.include_router(jira_router)

from app.qa_api import router as qa_router  # noqa: E402

app.include_router(qa_router)

from app.ops_api import router as ops_router  # noqa: E402

app.include_router(ops_router)

from app.incidents_api import router as incidents_router  # noqa: E402

app.include_router(incidents_router)
