"""Backend for the onboarding console. Workflow authority stays in Snowflake procedures."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent import AGENT_NAME, stream_agent
from app.db import (
    AUTH_MODE, DATABASE, WAREHOUSE, Db, SnowflakeSessionError,
    close_session, dev_db, lookup_session, open_pat_session,
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
_run_cache: dict[str, tuple[float, dict]] = {}
_RUN_TTL = 3.0


def _cached_run(run_id: str, loader):
    now = time.time()
    with _run_lock:
        hit = _run_cache.get(run_id)
        if hit and now - hit[0] < _RUN_TTL:
            return hit[1]
    state = loader()
    with _run_lock:
        _run_cache[run_id] = (time.time(), state)
    return state


def _drop_run(run_id: str) -> None:
    with _run_lock:
        _run_cache.pop(run_id, None)


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


def current_db(x_aip_session: Optional[str] = Header(default=None)) -> Db:
    try:
        if AUTH_MODE == "dev":
            return dev_db()
    except SnowflakeSessionError as exc:
        raise HTTPException(503, str(exc)) from exc
    db = lookup_session(x_aip_session)
    if db is None:
        raise HTTPException(401, "Sign in required")
    return db


class Login(BaseModel):
    user: str = Field(min_length=1, max_length=256)
    token: str = Field(min_length=1)


class CreateRun(BaseModel):
    run_name: str = Field(min_length=1, max_length=256)
    target_model: Optional[str] = None
    domain_id: Optional[str] = None
    environment: str = "DEV"
    intent: Optional[dict] = None


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


class AccessRequest(BaseModel):
    selected: list[str] = Field(min_length=1, max_length=50)


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
    base_branch: Optional[str] = "main"
    cut_branch: Optional[str] = None
    repo: Optional[str] = None
    origin: Optional[str] = None
    git_repository: Optional[str] = None
    api_integration: Optional[str] = None
    dbt_project: Optional[str] = None
    allowed_prefixes: Optional[list[str]] = None
    push: bool = False
    fetch_skeleton: bool = True


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
    if any(k in message for k in ("TRANSITION_REJECTED", "CONCURRENT_UPDATE", "SOURCE_NAME_CONFLICT")):
        status = 409
    elif "BUSINESS_JUSTIFICATION is required" in message or "SOURCE_NOT_ACCESSIBLE" in message:
        status = 422
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
    return {"user": db.user, "role": db.role, "auth_mode": AUTH_MODE, "agent": AGENT_NAME}


@app.get("/api/runs")
def list_runs(include_test: bool = False, db: Db = Depends(current_db)):
    rows = db.query(
        """
        SELECT RUN_ID, RUN_NAME, CURRENT_STATE, CURRENT_STAGE, STATUS, TARGET_MODEL,
               CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT
          FROM CORE.WORKFLOW_RUN
         WHERE ENVIRONMENT <> 'TEST' OR %s
         ORDER BY CREATED_AT DESC
         LIMIT 100
        """,
        (include_test,),
    )
    return {"runs": rows}


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


def _domain_id_for_target(db: Db, target_model: Optional[str], domain_id: Optional[str]) -> Optional[str]:
    if domain_id:
        return domain_id
    if not target_model:
        return None
    name = target_model.split(".")[-1]
    found = db.query(
        """
        SELECT DOMAIN_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
         WHERE ACTIVE_FLAG AND UPPER(TARGET_TABLE) = UPPER(%s)
         ORDER BY TARGET_TABLE LIMIT 1
        """,
        (name,),
    )
    return found[0]["domain_id"] if found else None


def _save_intent(db: Db, run_id: str, intent: dict) -> None:
    from services.source.intent import intent_key
    key = intent_key(run_id)
    payload = {**intent, "run_id": run_id}
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
        "SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s AND IS_CURRENT",
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
    payload = {
        "RUN_NAME": body.run_name,
        "TARGET_MODEL": body.target_model,
        "DOMAIN_ID": _domain_id_for_target(db, body.target_model, body.domain_id),
        "ENVIRONMENT": body.environment,
    }
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
    return created


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, db: Db = Depends(current_db)):
    def load():
        try:
            state = db.call("CALL CORE.GET_WORKFLOW_STATE(%s)", (run_id,))
        except Exception as exc:
            raise _snowflake_error(exc) from exc
        state["run"] = db.query(
            """
            SELECT R.RUN_NAME, R.TARGET_MODEL, R.SOURCE_SYSTEM_ID, R.SOURCE_DATABASE, R.SOURCE_SCHEMA,
                   R.ENVIRONMENT, R.CREATED_BY, R.CREATED_AT::VARCHAR AS CREATED_AT,
                   R.DOMAIN_ID, D.DOMAIN_NAME
              FROM CORE.WORKFLOW_RUN R
              LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
             WHERE R.RUN_ID = %s
            """,
            (run_id,),
        )[0]
        return _unlock_parallel_tracks(state)

    return _cached_run(run_id, load)


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
    return {
        "intent": intent or None,
        "source": {
            "database": (intent.get("source") or {}).get("database") or run.get("source_database"),
            "schema": (intent.get("source") or {}).get("schema") or run.get("source_schema"),
        },
        "sources": sources,
        "targets": targets,
        "edges": edges,
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


@app.post("/api/runs/{run_id}/review")
def review(run_id: str, body: Review, db: Db = Depends(current_db)):
    try:
        return db.call(
            "CALL CORE.REVIEW_TRANSITION(%s, %s, %s, %s, %s)",
            (run_id, body.to_state, body.decision, body.business_justification, body.comments),
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc


SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,254}$")


def _ident(value: str, field: str) -> str:
    value = (value or "").strip()
    if not SAFE_IDENT.match(value):
        raise HTTPException(400, f"{field} must be an unquoted identifier")
    return value.upper()


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


@app.get("/api/catalog/schemas")
def catalog_schemas(database: str, db: Db = Depends(current_db)):
    database = _ident(database, "database")
    return {"schemas": db.query(
        f"""
        SELECT SCHEMA_NAME FROM {database}.INFORMATION_SCHEMA.SCHEMATA
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
          FROM {database}.INFORMATION_SCHEMA.TABLES
         WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
         ORDER BY TABLE_NAME
        """,
        (schema,),
    )}


@app.get("/api/catalog/columns")
def catalog_columns(database: str, schema: str, table: str, db: Db = Depends(current_db)):
    database, schema, table = _ident(database, "database"), _ident(schema, "schema"), _ident(table, "table")
    return {"columns": db.query(
        f"""
        SELECT COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION
          FROM {database}.INFORMATION_SCHEMA.COLUMNS
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
            db_name, sch, tbl = _ident(database, "database"), _ident(schema, "schema"), _ident(table, "table")
            cols = db.query(
                f"""
                SELECT COLUMN_NAME FROM {db_name}.INFORMATION_SCHEMA.COLUMNS
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


def _suggest_for_catalog(db: Db, database: Optional[str], schema: Optional[str], tables: list[str]) -> dict:
    from services.source.catalog_display import display_domain_name, workspace_targets
    from services.source.intent import suggest_models

    targets = _targets_with_columns(db)
    scoped = workspace_targets(targets, database, schema)
    profile = _catalog_profile(db, database, schema, tables)
    suggestions = suggest_models(profile, targets, tables)
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
    related = bool(existing) or bool(scoped)
    return {
        "related": related,
        "suggestions": existing if related else existing + proposed,
        "targets": [{**row, "domain_name": display_domain_name(row.get("domain_name"))} for row in scoped],
        "tables": tables,
    }


@app.put("/api/runs/{run_id}/intent")
def put_run_intent(run_id: str, body: IntentPatch, db: Db = Depends(current_db)):
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
    return _suggest_for_catalog(db, database or None, schema or None, table_list)


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
              FROM {database}.INFORMATION_SCHEMA.COLUMNS
             WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
             ORDER BY ORDINAL_POSITION
            """,
            (schema, table),
        )
    except Exception as exc:
        raise HTTPException(400, f"TARGET_NOT_VISIBLE: {database}.{schema}.{table}: {exc}") from exc
    if not raw:
        raise HTTPException(400, f"TARGET_NOT_VISIBLE: {database}.{schema}.{table}")
    payload = {
        "database": database, "schema": schema, "table": table,
        "columns": [{
            "column_name": c["column_name"],
            "data_type": _column_type(c),
            "ordinal_position": c["ordinal_position"],
            "nullable": c["is_nullable"] == "YES",
            "comment": c["comment"],
            "business_key": str(c["column_name"]).endswith("_ID") and c["is_nullable"] != "YES",
        } for c in raw],
    }
    try:
        return db.call("CALL KNOWLEDGE.REGISTER_TARGET_TABLE(%s)", (json.dumps(payload),))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/source")
def register_source(run_id: str, body: RegisterSource, db: Db = Depends(current_db)):
    payload = {
        "SOURCE_SYSTEM_NAME": body.source_system_name,
        "SOURCE_TYPE": body.source_type,
        "DATABASE": body.database,
        "SCHEMA": body.schema_name,
        "OWNER": body.owner,
        "SECURITY_CLASSIFICATION": body.security_classification,
    }
    try:
        return db.call("CALL SOURCE.REGISTER_SOURCE(%s, %s)",
                       (run_id, json.dumps({k: v for k, v in payload.items() if v})))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/access")
def validate_access(run_id: str, body: AccessRequest, db: Db = Depends(current_db)):
    try:
        return db.call("CALL SOURCE.VALIDATE_SOURCE_ACCESS(%s, %s)", (run_id, json.dumps(body.selected)))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.post("/api/runs/{run_id}/landing")
def execute_landing(run_id: str, db: Db = Depends(current_db)):
    try:
        return db.call("CALL SOURCE.EXECUTE_LANDING(%s)", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


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
def recent_audit(db: Db = Depends(current_db)):
    events = db.query(
        """
        SELECT E.EVENT_ID, E.RUN_ID, R.RUN_NAME, E.FROM_STATE, E.TO_STATE, E.ACTOR_TYPE, E.ACTOR,
               E.REASON, E.CREATED_AT::VARCHAR AS CREATED_AT
          FROM CORE.WORKFLOW_EVENT E
          JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = E.RUN_ID
         ORDER BY E.CREATED_AT DESC
         LIMIT 200
        """
    )
    return {"events": events}


@app.get("/api/skills")
def skills(db: Db = Depends(current_db)):
    return {"skills": db.query(
        """
        SELECT SKILL_ID, SKILL_NAME, SKILL_TYPE, DOMAIN_ID, VERSION, STAGE_PATH, STATUS,
               CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT
          FROM KNOWLEDGE.SKILL_REGISTRY
         ORDER BY SKILL_NAME, CREATED_AT DESC
        """
    )}


@app.get("/api/domains")
def domains(db: Db = Depends(current_db)):
    return {"domains": db.query(
        """
        SELECT D.DOMAIN_ID, D.DOMAIN_NAME, D.DESCRIPTION, D.OWNER, D.ACTIVE_FLAG, D.VERSION,
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


@app.post("/api/runs/{run_id}/profile")
def run_profiling(run_id: str, db: Db = Depends(current_db)):
    try:
        return db.call("CALL PROFILE.RUN_PROFILING(%s)", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/runs/{run_id}/profile")
def get_profile(run_id: str, db: Db = Depends(current_db)):
    return {"columns": db.query(
        """
        SELECT PROFILE_ID, TABLE_NAME, COLUMN_NAME, DATA_TYPE, SEMANTIC_TYPE, PII_CLASSIFICATION,
               ROW_COUNT, NULL_PERCENTAGE, DISTINCT_PERCENTAGE, CARDINALITY, POTENTIAL_KEY_FLAG,
               POTENTIAL_FOREIGN_KEY_FLAG, GENERATED_DESCRIPTION, PROFILE_STATUS
          FROM PROFILE.PROFILE_REGISTRY
         WHERE RUN_ID = %s AND IS_CURRENT
         ORDER BY TABLE_NAME, COLUMN_NAME
        """,
        (run_id,),
    )}


@app.post("/api/runs/{run_id}/domain")
def identify_domain(run_id: str, db: Db = Depends(current_db)):
    try:
        return db.call("CALL KNOWLEDGE.IDENTIFY_DOMAIN(%s)", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


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


@app.get("/api/runs/{run_id}/mapping")
def get_mapping(run_id: str, db: Db = Depends(current_db)):
    candidates = db.query(
        """
        SELECT C.CANDIDATE_ID, C.SOURCE_COLUMN_ID, L.COLUMN_NAME AS SOURCE_COLUMN, L.DATA_TYPE AS SOURCE_DATATYPE,
               TBL.SOURCE_TABLE, C.TARGET_COLUMN_ID, T.COLUMN_NAME AS TARGET_COLUMN, T.DATA_TYPE AS TARGET_DATATYPE,
               T.NULLABLE, T.IS_BUSINESS_KEY, C.FINAL_SCORE, C.RANK, C.CONFIDENCE, C.RECOMMENDATION,
               C.GENERATED_REASON, C.TRANSFORMATION, C.SEMANTIC_SCORE, C.KEYWORD_SCORE, C.DATATYPE_SCORE,
               C.STATISTICAL_SCORE, C.DOMAIN_SCORE, C.CONTEXT_SCORE, C.HISTORICAL_SCORE
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
    targets = db.query(
        """
        SELECT T.TARGET_COLUMN_ID, T.COLUMN_NAME, T.DATA_TYPE, T.NULLABLE, T.IS_BUSINESS_KEY, T.SEMANTIC_TYPE
          FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY T
          JOIN CORE.WORKFLOW_RUN R ON R.DOMAIN_ID IS NOT NULL
          JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY TB ON TB.TARGET_TABLE_ID = T.TARGET_TABLE_ID
               AND TB.DOMAIN_ID = R.DOMAIN_ID AND TB.ACTIVE_FLAG
         WHERE R.RUN_ID = %s
         ORDER BY T.ORDINAL_POSITION
        """,
        (run_id,),
    )
    sources = {c["source_column_id"] for c in candidates}
    decided = {d["source_column_id"] for d in decisions}
    mapped = {d["target_column_id"] for d in decisions if d["decision"] != "REJECTED" and d["target_column_id"]}
    missing = [t["column_name"] for t in targets
               if not t["nullable"] and t["semantic_type"] not in ("SURROGATE_KEY", "RECORD_SOURCE", "AUDIT_TIMESTAMP")
               and t["target_column_id"] not in mapped]
    undecided = [next(c["source_column"] for c in candidates if c["source_column_id"] == s)
                 for s in sources if s not in decided]
    return {
        "candidates": candidates, "decisions": decisions, "targets": targets,
        "status": {
            "source_columns": len(sources), "decided": len(sources) - len(undecided),
            "undecided": undecided, "missing_required_targets": missing,
            "complete": bool(sources) and not undecided and not missing,
        },
    }


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
                   UNIQUENESS_RULE, HUMAN_APPROVED, REVIEWER, MAPPING_CONFIDENCE
              FROM CONTRACT.STTM_LINE WHERE STTM_ID = %s ORDER BY TARGET_COLUMN
            """,
            (header[0]["sttm_id"],),
        )
    return {"sttm": header[0] if header else None, "lines": lines}


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
    try:
        return db.call("CALL CONTRACT.SAVE_SODA_DECISIONS(%s, %s)",
                       (run_id, json.dumps(body.decisions)))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/runs/{run_id}/soda")
def get_soda(run_id: str, db: Db = Depends(current_db)):
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
            "status": c["status"],
        }
        c["sodacl"] = render_check(item)
        rendered.append(item)
    return {
        "checks": checks,
        "brief": briefs[0] if briefs else None,
        "yaml": render_yaml(str(table).lower(), rendered),
        "status": {
            "total": len(checks),
            "proposed": sum(1 for c in checks if c["status"] == "PROPOSED"),
            "approved": sum(1 for c in checks if c["status"] == "APPROVED"),
            "rejected": sum(1 for c in checks if c["status"] == "REJECTED"),
        },
    }


def _clean_dbt_plan(body: Optional[DbtPlan]) -> dict:
    raw = (body or DbtPlan()).model_dump(exclude_none=True)
    return {key: value for key, value in raw.items() if value != "" and value != []}


def _is_dbt_arity_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "too many arguments" in text or "expected 1, got 2" in text


def _save_dbt_plan(db: Db, run_id: str, payload: dict) -> None:
    run = db.query("SELECT DOMAIN_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not run or not run[0].get("domain_id"):
        return
    ref = f"dbt.branch.{run_id}"
    db.execute(
        "UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, STATUS = 'RETIRED' "
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
           SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY)
        SELECT UUID_STRING(), %s, 'TRANSFORMATION_RULE', %s, %s, PARSE_JSON(%s),
               PARSE_JSON('["DBT","BRANCH"]'), %s, 'ACTIVE', %s, TRUE, CURRENT_USER()
        """,
        (
            run[0]["domain_id"],
            f"dbt branch plan {run_id}"[:500],
            json.dumps(payload)[:8000],
            json.dumps(payload),
            ref,
            version,
        ),
    )


def _is_transition_rejected(exc: Exception) -> bool:
    return "TRANSITION_REJECTED" in str(exc)


def _generate_dbt_overlay(db: Db, run_id: str, payload: dict):
    from services.dbt.overlay import generate_via_db
    return generate_via_db(db, run_id, payload)


@app.post("/api/runs/{run_id}/dbt")
def generate_dbt(run_id: str, body: Optional[DbtPlan] = None, db: Db = Depends(current_db)):
    payload = _clean_dbt_plan(body)
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
    extras: dict = {"branch": None, "skills": None, "workspace": None}
    for art in artifacts:
        path = art.get("file_path")
        if path not in {"release/branch.json", "release/skills.json", "release/workspace.json"}:
            continue
        key = path.split("/")[-1].split(".")[0]
        try:
            extras[key] = json.loads(art.get("content") or "{}")
        except ValueError:
            extras[key] = None
    return {"generation": gen[0] if gen else None, "artifacts": artifacts, **extras}


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
    return {
        "run_id": run_id,
        "repo": repo_sql,
        "fetched": fetched,
        "fetch_warning": warning,
        "branches": branches,
        "latest": latest_branch(branches),
    }


@app.get("/api/runs/{run_id}/dbt/workspace")
def get_dbt_workspace(run_id: str, db: Db = Depends(current_db)):
    from services.dbt.workspace import discover
    from services.knowledge.usage import STAGE_SKILLS

    workspace = discover(lambda sql: db.query(sql))
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


@app.post("/api/runs/{run_id}/dbt/enhance")
def enhance_dbt(run_id: str, body: DbtEnhance, db: Db = Depends(current_db)):
    import hashlib
    from services.dbt.enhance import enhance_file

    gen = db.query(
        """
        SELECT GENERATION_ID FROM CODEGEN.DBT_GENERATION_REGISTRY
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
        return enhance_file(
            lambda sql, params=(): db.query(sql, params),
            path,
            rows[0]["content"] or "",
            body.prompt.strip(),
            model=body.model,
            context=context,
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc


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


@app.post("/api/agent/stream")
def agent_stream(body: AgentMessage, db: Db = Depends(current_db)):
    return StreamingResponse(stream_agent(db, body.run_id, body.message), media_type="text/event-stream")
