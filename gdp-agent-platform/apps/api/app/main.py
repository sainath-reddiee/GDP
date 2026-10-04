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
from app.db import AUTH_MODE, DATABASE, WAREHOUSE, Db, close_session, dev_db, lookup_session, open_pat_session

app = FastAPI(title="GDP Agent Platform API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
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
    if AUTH_MODE == "dev":
        return dev_db()
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


@app.post("/api/runs")
def create_run(body: CreateRun, db: Db = Depends(current_db)):
    payload = {
        "RUN_NAME": body.run_name,
        "TARGET_MODEL": body.target_model,
        "DOMAIN_ID": body.domain_id,
        "ENVIRONMENT": body.environment,
    }
    try:
        return db.call("CALL CORE.CREATE_RUN(%s)", (json.dumps(payload),))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, db: Db = Depends(current_db)):
    def load():
        try:
            state = db.call("CALL CORE.GET_WORKFLOW_STATE(%s)", (run_id,))
        except Exception as exc:
            raise _snowflake_error(exc) from exc
        state["run"] = db.query(
            """
            SELECT RUN_NAME, TARGET_MODEL, SOURCE_SYSTEM_ID, SOURCE_DATABASE, SOURCE_SCHEMA,
                   ENVIRONMENT, CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT
              FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s
            """,
            (run_id,),
        )[0]
        return state

    return _cached_run(run_id, load)


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


@app.get("/api/targets")
def list_targets(db: Db = Depends(current_db)):
    return {"targets": db.query(
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
    )}


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


@app.post("/api/runs/{run_id}/dbt")
def generate_dbt(run_id: str, db: Db = Depends(current_db)):
    try:
        return db.call("CALL CODEGEN.GENERATE_DBT(%s)", (run_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc


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
    return {"generation": gen[0] if gen else None, "artifacts": artifacts}


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
