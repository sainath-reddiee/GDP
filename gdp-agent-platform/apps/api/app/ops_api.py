"""Ops for Airflow (Amazon MWAA): environments, DAG health, runs, task grids, task logs, and the signed push ingest.

Reads run on the caller's session (OPS.VIEW). Environment settings need INTEGRATION.MANAGE, poll now and DAG settings
need OPS.OPERATE (services.governance.policy). MWAA calls (connection test, task logs) go out from the API host through
services.ops.mwaa with the host's AWS credentials.

POST /api/ops/ingest has no user session: it runs as system_db(), and the caller proves itself with an HMAC signature
made with the environment's push secret (services.ops.signing). The push secret is generated here, stored ENCRYPTed
with AIP_SECRET_KEY (or JIRA_TOKEN_KEY) and shown once.

Deleting an environment removes its row and its poll lease; its DAG and run history stays in the OPS tables but is
hidden, because every read joins to OPS.AIRFLOW_ENV.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.db import Db, SnowflakeSessionError, system_db
from app.main import _json, _snowflake_error, current_db
from services.ops.mwaa import Mwaa, MwaaError
from services.ops.normalize import ts
from services.ops.redact import redact_count
from services.ops.signing import HEADER_NAMES, MAX_BODY_BYTES, check_headers, verify

router = APIRouter()
INGEST_PATH = "/bff/ops/ingest"
CRITICALITY = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
RUN_LIMIT = 50
DAG_LIMIT = 2000
_SECRET_TTL = 60.0
_secrets: Dict[str, Tuple[float, Optional[str], bool]] = {}
_secrets_lock = threading.Lock()
_REGION = re.compile(r"^[a-z]{2}(-gov|-iso[a-z]?)?-[a-z]+-\d$")
_MWAA_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,79}$")
_CRON = re.compile(r"^(@(yearly|annually|monthly|weekly|daily|midnight|hourly)|([0-9A-Za-z*/,\-?LW#]+\s+){4,5}[0-9A-Za-z*/,\-?LW#]+)$")


# ---------------------------------------------------------------- plumbing

def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return value


def _num(value: Any) -> Any:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _list(value: Any) -> list:
    found = _json(value)
    return found if isinstance(found, list) else []


def _q(db: Db, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    try:
        return db.query(sql, params)
    except Exception as exc:
        raise _ops_error(exc) from exc


def _x(db: Db, sql: str, params: tuple = ()) -> int:
    try:
        return db.execute_count(sql, params)
    except Exception as exc:
        raise _ops_error(exc) from exc


def _ops_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    text = str(exc)
    if "OPS." in text.upper() and ("does not exist" in text or "not authorized" in text):
        return HTTPException(409, "Ops is not deployed to this database yet (migration V030), or your role cannot read the OPS schema.")
    return _snowflake_error(exc)


def _mwaa_http(exc: MwaaError) -> HTTPException:
    if exc.kind == "credentials":
        return HTTPException(409, "AWS credentials are not available on the API host. Give the host an IAM role (or AWS_PROFILE) "
                                  "with airflow:InvokeRestApi and airflow:GetEnvironment.")
    if exc.kind == "access_denied":
        return HTTPException(403, str(exc))
    if exc.kind == "not_found":
        return HTTPException(404, str(exc))
    return HTTPException(502, str(exc))


def _mwaa(env: Dict[str, Any]) -> Mwaa:
    return Mwaa(env["mwaa_env"], env["region"])


def _env(db: Db, env_id: str) -> Dict[str, Any]:
    found = _q(db, """SELECT ENV_ID, NAME, KIND, MWAA_ENV, REGION, AIRFLOW_URL, API_VERSION, ENABLED, POLL_SECONDS,
                             PUSH_ENABLED FROM OPS.AIRFLOW_ENV WHERE ENV_ID = %s""", (env_id,))
    if not found:
        raise HTTPException(404, f"Airflow environment {env_id} not found")
    return found[0]


def _secret_key() -> str:
    key = (os.environ.get("AIP_SECRET_KEY") or os.environ.get("JIRA_TOKEN_KEY") or "").strip()
    if len(key) < 16:
        raise HTTPException(409, "No encryption key on the API host: set AIP_SECRET_KEY (16+ random characters; "
                                 "JIRA_TOKEN_KEY is used when it is absent) and restart the API.")
    return key


def links(base: Optional[str], api_version: Optional[str], dag_id: str, run_id: Optional[str] = None) -> Optional[str]:
    """Deep link into the Airflow UI: Airflow 3 (v2) has /dags/<id>/runs/<run>, Airflow 2 the grid view."""
    if not base:
        return None
    root = base.rstrip("/")
    dag = quote(dag_id, safe="")
    if api_version == "v2":
        return f"{root}/dags/{dag}" + (f"/runs/{quote(run_id, safe='')}" if run_id else "")
    return f"{root}/dags/{dag}/grid" + (f"?dag_run_id={quote(run_id, safe='')}" if run_id else "")


def _forget_secret(env_id: Optional[str] = None) -> None:
    with _secrets_lock:
        if env_id:
            _secrets.pop(env_id, None)
        else:
            _secrets.clear()


# ---------------------------------------------------------------- models

class EnvBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mwaa_env: str = Field(min_length=1, max_length=80)
    region: str = Field(min_length=6, max_length=32)
    airflow_url: Optional[str] = Field(default=None, max_length=1024)
    poll_seconds: int = Field(default=300, ge=60, le=3600)
    enabled: bool = True
    push_enabled: bool = False


class EnvPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    mwaa_env: Optional[str] = Field(default=None, min_length=1, max_length=80)
    region: Optional[str] = Field(default=None, min_length=6, max_length=32)
    airflow_url: Optional[str] = Field(default=None, max_length=1024)
    poll_seconds: Optional[int] = Field(default=None, ge=60, le=3600)
    enabled: Optional[bool] = None
    push_enabled: Optional[bool] = None


class DagSettings(BaseModel):
    team_id: Optional[str] = Field(default=None, max_length=64)
    criticality: Optional[str] = Field(default=None, max_length=16)
    expected_by_cron: Optional[str] = Field(default=None, max_length=120)
    max_duration_min: Optional[int] = Field(default=None, ge=1, le=10080)
    domain_id: Optional[str] = Field(default=None, max_length=36)
    repo_id: Optional[str] = Field(default=None, max_length=36)
    repo_path: Optional[str] = Field(default=None, max_length=2000)
    timezone: Optional[str] = Field(default=None, max_length=64)        # the expected-by cron's timezone (UTC when empty)
    mute_until: Optional[str] = Field(default=None, max_length=40)      # ISO time; incidents of the DAG open MUTED until then
    mute_reason: Optional[str] = Field(default=None, max_length=500)


def _check_env_fields(values: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(values)
    if out.get("name") is not None:
        out["name"] = out["name"].strip()
        if not out["name"]:
            raise HTTPException(422, "name is required")
    if out.get("mwaa_env") is not None and not _MWAA_NAME.match(out["mwaa_env"].strip()):
        raise HTTPException(422, "mwaa_env must be an MWAA environment name (letters, digits, - and _)")
    if out.get("region") is not None and not _REGION.match(out["region"].strip()):
        raise HTTPException(422, "region must be an AWS region such as us-east-1")
    if "airflow_url" in out:
        url = (out.get("airflow_url") or "").strip().rstrip("/")
        if url and not (url.startswith("https://") or url.startswith("http://localhost") or url.startswith("http://127.0.0.1")):
            raise HTTPException(422, "airflow_url must start with https://")
        out["airflow_url"] = url or None
    for key in ("mwaa_env", "region"):
        if out.get(key) is not None:
            out[key] = out[key].strip()
    return out


def slug(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48]
    return value or "airflow"


# ---------------------------------------------------------------- summary and environments

@router.get("/api/ops/summary")
def ops_summary(db: Db = Depends(current_db)):
    found = _q(db, """
        SELECT (SELECT COUNT(*) FROM OPS.AIRFLOW_ENV WHERE ENABLED) AS ENVS,
               (SELECT COUNT(*) FROM OPS.DAG D JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = D.ENV_ID
                 WHERE COALESCE(D.IS_ACTIVE, TRUE)) AS DAGS,
               (SELECT COUNT(DISTINCT R.ENV_ID, R.DAG_ID) FROM OPS.DAG_RUN R JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = R.ENV_ID
                 WHERE R.STATE = 'failed' AND COALESCE(R.ENDED_AT, R.UPDATED_AT) >= DATEADD(hour, -24, CURRENT_TIMESTAMP())) AS FAILING_24H,
               (SELECT COUNT(*) FROM OPS.DAG_RUN R JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = R.ENV_ID
                 WHERE R.STATE IN ('running', 'queued')) AS RUNNING,
               (SELECT MAX(LAST_POLL_AT) FROM OPS.AIRFLOW_ENV) AS LAST_POLL_AT,
               (SELECT MAX(LAST_RUN_AT) FROM OPS.JOB_LEASE WHERE JOB_NAME = 'worker') AS WORKER_SEEN_AT""")
    row = found[0] if found else {}
    return {"envs": int(row.get("envs") or 0), "dags": int(row.get("dags") or 0),
            "failing_24h": int(row.get("failing_24h") or 0), "running": int(row.get("running") or 0),
            "last_poll_at": _iso(row.get("last_poll_at")), "worker_seen_at": _iso(row.get("worker_seen_at"))}


def _env_out(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"env_id": r["env_id"], "name": r["name"], "kind": r.get("kind") or "MWAA", "mwaa_env": r["mwaa_env"],
            "region": r["region"], "airflow_url": r.get("airflow_url"), "api_version": r.get("api_version"),
            "airflow_version": r.get("airflow_version"), "enabled": bool(r.get("enabled")),
            "poll_seconds": int(r.get("poll_seconds") or 300), "push_enabled": bool(r.get("push_enabled")),
            "has_push_secret": bool(r.get("has_push_secret")), "last_poll_at": _iso(r.get("last_poll_at")),
            "last_attempt_at": _iso(r.get("last_attempt_at")), "last_error": r.get("last_error"),
            "dags": int(r.get("dags") or 0)}


_ENV_SELECT = """
    SELECT E.ENV_ID, E.NAME, E.KIND, E.MWAA_ENV, E.REGION, E.AIRFLOW_URL, E.API_VERSION, E.AIRFLOW_VERSION, E.ENABLED,
           E.POLL_SECONDS, E.PUSH_ENABLED, E.PUSH_SECRET IS NOT NULL AS HAS_PUSH_SECRET, E.LAST_POLL_AT, E.LAST_ATTEMPT_AT,
           E.LAST_ERROR, COALESCE(D.N, 0) AS DAGS
      FROM OPS.AIRFLOW_ENV E
      LEFT JOIN (SELECT ENV_ID, COUNT(*) AS N FROM OPS.DAG WHERE COALESCE(IS_ACTIVE, TRUE) GROUP BY ENV_ID) D
        ON D.ENV_ID = E.ENV_ID"""


@router.get("/api/ops/envs")
def list_envs(db: Db = Depends(current_db)):
    return {"envs": [_env_out(r) for r in _q(db, _ENV_SELECT + " ORDER BY E.NAME")]}


def _one_env_out(db: Db, env_id: str) -> Dict[str, Any]:
    found = _q(db, _ENV_SELECT + " WHERE E.ENV_ID = %s", (env_id,))
    if not found:
        raise HTTPException(404, f"Airflow environment {env_id} not found")
    return _env_out(found[0])


@router.post("/api/ops/envs")
def create_env(body: EnvBody, db: Db = Depends(current_db)):
    values = _check_env_fields(body.model_dump())
    base = slug(values["name"])
    taken = {r["env_id"] for r in _q(db, "SELECT ENV_ID FROM OPS.AIRFLOW_ENV WHERE ENV_ID LIKE %s", (base + "%",))}
    env_id = next((c for c in [base] + [f"{base}-{i}" for i in range(2, 100)] if c not in taken), None)
    if not env_id:
        raise HTTPException(409, "Too many environments with this name; choose another name")
    _x(db, """INSERT INTO OPS.AIRFLOW_ENV (ENV_ID, NAME, KIND, MWAA_ENV, REGION, AIRFLOW_URL, ENABLED, POLL_SECONDS, PUSH_ENABLED)
              SELECT %s, %s, 'MWAA', %s, %s, %s, %s, %s, %s""",
       (env_id, values["name"], values["mwaa_env"], values["region"], values.get("airflow_url"), values["enabled"],
        values["poll_seconds"], values["push_enabled"]))
    return _one_env_out(db, env_id)


@router.put("/api/ops/envs/{env_id}")
def update_env(env_id: str, body: EnvPatch, db: Db = Depends(current_db)):
    _env(db, env_id)
    values = _check_env_fields({k: getattr(body, k) for k in body.model_fields_set})
    for key in ("name", "mwaa_env", "region", "poll_seconds", "enabled", "push_enabled"):
        if key in values and values[key] is None:
            raise HTTPException(422, f"{key} cannot be empty")
    columns = {"name": "NAME", "mwaa_env": "MWAA_ENV", "region": "REGION", "airflow_url": "AIRFLOW_URL",
               "poll_seconds": "POLL_SECONDS", "enabled": "ENABLED", "push_enabled": "PUSH_ENABLED"}
    sets = [(columns[k], v) for k, v in values.items() if k in columns]
    if sets:
        if any(c in ("MWAA_ENV", "REGION") for c, _ in sets):
            sets.append(("API_VERSION", None))  # detected again on the next poll or test
        _x(db, f"UPDATE OPS.AIRFLOW_ENV SET {', '.join(c + ' = %s' for c, _ in sets)}, UPDATED_AT = CURRENT_TIMESTAMP() "
               "WHERE ENV_ID = %s", tuple(v for _, v in sets) + (env_id,))
    _forget_secret(env_id)
    return _one_env_out(db, env_id)


@router.delete("/api/ops/envs/{env_id}")
def delete_env(env_id: str, db: Db = Depends(current_db)):
    _env(db, env_id)
    _x(db, "DELETE FROM OPS.AIRFLOW_ENV WHERE ENV_ID = %s", (env_id,))
    _x(db, "DELETE FROM OPS.JOB_LEASE WHERE JOB_NAME = %s", (f"poll:{env_id}",))
    _forget_secret(env_id)
    return {"deleted": True, "env_id": env_id,
            "detail": "The environment is removed. Its captured history is kept but no longer shown."}


@router.post("/api/ops/envs/{env_id}/test")
def test_env(env_id: str, db: Db = Depends(current_db)):
    env = _env(db, env_id)
    try:
        mw = _mwaa(env)
        info = mw.version()
        dags, _ = mw.dags(limit=5)
    except MwaaError as exc:
        if exc.kind in ("credentials", "access_denied"):
            raise _mwaa_http(exc) from exc
        return {"ok": False, "version": None, "api_version": None, "dags_sample": [], "error": str(exc)}
    _x(db, "UPDATE OPS.AIRFLOW_ENV SET API_VERSION = %s, AIRFLOW_VERSION = %s WHERE ENV_ID = %s",
       (info["api_version"], info["version"][:32], env_id))
    return {"ok": True, "version": info["version"], "api_version": info["api_version"],
            "dags_sample": [str(d.get("dag_id")) for d in dags if d.get("dag_id")], "error": None}


@router.post("/api/ops/envs/{env_id}/poll")
def poll_env_now(env_id: str, db: Db = Depends(current_db)):
    from app import worker

    _env(db, env_id)
    try:
        system_db()
        return worker.poll_now(env_id)
    except SnowflakeSessionError:
        # no service identity on this host: poll with the caller's session (their role needs OPS_SERVICE)
        result = worker.poll_now(env_id, db_factory=lambda: db)
        if result.get("started"):
            result["detail"] += " It runs with your Snowflake session because no service user is configured."
        return result


@router.post("/api/ops/envs/{env_id}/push-secret")
def rotate_push_secret(env_id: str, db: Db = Depends(current_db), x_aip_replay: Optional[str] = Header(default=None)):
    """A new push secret, returned once. The old one stops working immediately."""
    if x_aip_replay:
        # an approval replay stores the response on the change request: a secret must never land there
        raise HTTPException(403, "A push secret cannot be issued through an approval. Someone with INTEGRATION.MANAGE "
                                 "must rotate it directly.")
    key = _secret_key()
    _env(db, env_id)
    secret = secrets.token_hex(32)
    _x(db, "UPDATE OPS.AIRFLOW_ENV SET PUSH_SECRET = ENCRYPT(%s, %s), UPDATED_AT = CURRENT_TIMESTAMP() WHERE ENV_ID = %s",
       (secret, key, env_id))
    _forget_secret(env_id)
    return {"env_id": env_id, "secret": secret, "header_names": HEADER_NAMES, "url_path": INGEST_PATH}


# ---------------------------------------------------------------- DAGs, runs, tasks, logs

_DAG_SQL = """
WITH LAST_RUN AS (
    SELECT ENV_ID, DAG_ID, STATE AS LAST_STATE, COALESCE(STARTED_AT, LOGICAL_DATE) AS LAST_RUN_AT, DURATION_S AS LAST_DURATION_S
      FROM OPS.DAG_RUN WHERE {run_filter}
   QUALIFY ROW_NUMBER() OVER (PARTITION BY ENV_ID, DAG_ID
                              ORDER BY COALESCE(STARTED_AT, LOGICAL_DATE, UPDATED_AT) DESC NULLS LAST) = 1
), STATS AS (
    SELECT ENV_ID, DAG_ID,
           COUNT_IF(AT >= DATEADD(day, -7, CURRENT_TIMESTAMP())) AS RUNS_7D,
           COUNT_IF(AT >= DATEADD(day, -7, CURRENT_TIMESTAMP()) AND STATE = 'success') AS OK_7D,
           COUNT_IF(AT >= DATEADD(day, -7, CURRENT_TIMESTAMP()) AND STATE = 'failed') AS FAIL_7D,
           COUNT_IF(STATE = 'success') AS OK_30D, COUNT_IF(STATE = 'failed') AS FAIL_30D,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY IFF(STATE = 'success', DURATION_S, NULL)) AS P50_S,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY IFF(STATE = 'success', DURATION_S, NULL)) AS P95_S
      FROM (SELECT ENV_ID, DAG_ID, STATE, DURATION_S, COALESCE(STARTED_AT, LOGICAL_DATE) AS AT FROM OPS.DAG_RUN
             WHERE {run_filter} AND COALESCE(STARTED_AT, LOGICAL_DATE) >= DATEADD(day, -30, CURRENT_TIMESTAMP()))
     GROUP BY ENV_ID, DAG_ID
)
SELECT D.ENV_ID, D.DAG_ID, D.OWNERS, D.TAGS, D.SCHEDULE, D.IS_PAUSED, D.IS_ACTIVE, D.CRITICALITY, D.TEAM_ID, D.DOMAIN_ID,
       D.FILELOC, D.EXPECTED_BY_CRON, D.MAX_DURATION_MIN, D.REPO_ID, D.REPO_PATH, D.DESCRIPTION, D.TIMEZONE, D.MUTE_UNTIL,
       D.MUTE_REASON, E.AIRFLOW_URL AS ENV_AIRFLOW_URL, E.API_VERSION,
       L.LAST_STATE, L.LAST_RUN_AT, L.LAST_DURATION_S, S.RUNS_7D, S.OK_7D, S.FAIL_7D, S.OK_30D, S.FAIL_30D, S.P50_S, S.P95_S
  FROM OPS.DAG D
  JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = D.ENV_ID
  LEFT JOIN LAST_RUN L ON L.ENV_ID = D.ENV_ID AND L.DAG_ID = D.DAG_ID
  LEFT JOIN STATS S ON S.ENV_ID = D.ENV_ID AND S.DAG_ID = D.DAG_ID
 WHERE {dag_filter}
 ORDER BY IFF(L.LAST_STATE = 'failed', 0, 1), D.DAG_ID
 LIMIT {limit}"""


def _rate(ok: Any, bad: Any) -> Optional[float]:
    ok, bad = int(ok or 0), int(bad or 0)
    return round(ok / (ok + bad), 3) if ok + bad else None


def _dag_out(r: Dict[str, Any], detail: bool = False) -> Dict[str, Any]:
    out = {"env_id": r["env_id"], "dag_id": r["dag_id"], "owners": _list(r.get("owners")), "tags": _list(r.get("tags")),
           "schedule": r.get("schedule"), "is_paused": r.get("is_paused"), "is_active": r.get("is_active"),
           "criticality": r.get("criticality"), "team_id": r.get("team_id"), "domain_id": r.get("domain_id"),
           "last_state": r.get("last_state"), "last_run_at": _iso(r.get("last_run_at")),
           "last_duration_s": _num(r.get("last_duration_s")), "success_7d": _rate(r.get("ok_7d"), r.get("fail_7d")),
           "success_30d": _rate(r.get("ok_30d"), r.get("fail_30d")), "runs_7d": int(r.get("runs_7d") or 0),
           "p50_s": _num(r.get("p50_s")), "p95_s": _num(r.get("p95_s")), "open_incidents": 0}
    if detail:
        out.update({"fileloc": r.get("fileloc"), "expected_by_cron": r.get("expected_by_cron"),
                    "max_duration_min": int(r["max_duration_min"]) if r.get("max_duration_min") is not None else None,
                    "repo_id": r.get("repo_id"), "repo_path": r.get("repo_path"), "description": r.get("description"),
                    "api_version": r.get("api_version"), "timezone": r.get("timezone"),
                    "mute_until": _iso(r.get("mute_until")), "mute_reason": r.get("mute_reason"),
                    "airflow_url": links(r.get("env_airflow_url"), r.get("api_version"), r["dag_id"])})
    return out


def _dags(db: Db, env_id: Optional[str] = None, dag_id: Optional[str] = None, q: Optional[str] = None,
          state: Optional[str] = None, team_id: Optional[str] = None, owner: Optional[str] = None,
          limit: int = DAG_LIMIT) -> List[Dict[str, Any]]:
    run_filter, run_params = "TRUE", []
    dag_filter, dag_params = ["TRUE"], []
    if env_id:
        run_filter, run_params = "ENV_ID = %s", [env_id]
        dag_filter.append("D.ENV_ID = %s")
        dag_params.append(env_id)
    if dag_id:
        run_filter += " AND DAG_ID = %s"
        run_params.append(dag_id)
        dag_filter.append("D.DAG_ID = %s")
        dag_params.append(dag_id)
    if q:
        dag_filter.append("D.DAG_ID ILIKE %s")
        dag_params.append(f"%{q.strip()}%")
    if state:
        dag_filter.append("L.LAST_STATE = %s")
        dag_params.append(state.strip().lower())
    if team_id:
        dag_filter.append("D.TEAM_ID = %s")
        dag_params.append(team_id)
    if owner:
        dag_filter.append("ARRAY_CONTAINS(%s::VARIANT, D.OWNERS)")
        dag_params.append(owner)
    sql = _DAG_SQL.format(run_filter=run_filter, dag_filter=" AND ".join(dag_filter), limit=int(limit))
    # LAST_RUN and STATS both use run_filter, in that order
    return _q(db, sql, tuple(run_params) + tuple(run_params) + tuple(dag_params))


def open_incident_counts(db: Db, env_id: Optional[str] = None, dag_id: Optional[str] = None) -> Dict[Tuple[str, str], int]:
    """Open and acknowledged incidents per DAG; empty before V031 is applied."""
    where, params = ["STATUS IN ('OPEN', 'ACK')"], []
    if env_id:
        where.append("ENV_ID = %s")
        params.append(env_id)
    if dag_id:
        where.append("DAG_ID = %s")
        params.append(dag_id)
    try:
        found = db.query(f"SELECT ENV_ID, DAG_ID, COUNT(*) AS N FROM OPS.INCIDENT WHERE {' AND '.join(where)} "
                         "GROUP BY ENV_ID, DAG_ID", tuple(params))
    except Exception:
        return {}
    return {(r["env_id"], r["dag_id"]): int(r["n"] or 0) for r in found}


def _with_counts(db: Db, rows: List[Dict[str, Any]], detail: bool = False, env_id: Optional[str] = None,
                 dag_id: Optional[str] = None) -> List[Dict[str, Any]]:
    counts = open_incident_counts(db, env_id, dag_id)
    out = []
    for r in rows:
        item = _dag_out(r, detail)
        item["open_incidents"] = counts.get((r["env_id"], r["dag_id"]), 0)
        out.append(item)
    return out


@router.get("/api/ops/dags")
def list_dags(env_id: Optional[str] = None, q: Optional[str] = Query(default=None, max_length=200),
              state: Optional[str] = Query(default=None, max_length=32), team_id: Optional[str] = None,
              owner: Optional[str] = Query(default=None, max_length=200), db: Db = Depends(current_db)):
    return {"dags": _with_counts(db, _dags(db, env_id=env_id or None, q=q or None, state=state or None,
                                           team_id=team_id or None, owner=owner or None), env_id=env_id or None)}


def _run_out(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"run_id": r["run_id"], "run_type": r.get("run_type"), "state": r.get("state"),
            "logical_date": _iso(r.get("logical_date")), "start": _iso(r.get("started_at")), "end": _iso(r.get("ended_at")),
            "duration_s": _num(r.get("duration_s")), "external_trigger": r.get("external_trigger"),
            "failed_tasks": int(r.get("failed_tasks") or 0)}


_RUNS_SQL = """
SELECT R.RUN_ID, R.RUN_TYPE, R.STATE, R.LOGICAL_DATE, R.STARTED_AT, R.ENDED_AT, R.DURATION_S, R.EXTERNAL_TRIGGER, R.NOTE,
       R.SOURCE, R.UPDATED_AT, COALESCE(F.N, 0) AS FAILED_TASKS
  FROM OPS.DAG_RUN R
  LEFT JOIN (SELECT RUN_ID, COUNT(DISTINCT TASK_ID, MAP_INDEX) AS N FROM OPS.TASK_RUN
              WHERE ENV_ID = %s AND DAG_ID = %s AND STATE IN ('failed', 'upstream_failed') GROUP BY RUN_ID) F
    ON F.RUN_ID = R.RUN_ID
 WHERE R.ENV_ID = %s AND R.DAG_ID = %s {extra}
 ORDER BY COALESCE(R.STARTED_AT, R.LOGICAL_DATE, R.UPDATED_AT) DESC NULLS LAST
 LIMIT {limit}"""


@router.get("/api/ops/dag")
def get_dag(env_id: str, dag_id: str, db: Db = Depends(current_db)):
    found = _dags(db, env_id=env_id, dag_id=dag_id, limit=1)
    if not found:
        raise HTTPException(404, f"DAG {dag_id} not found in {env_id}")
    runs = _q(db, _RUNS_SQL.format(extra="", limit=RUN_LIMIT), (env_id, dag_id, env_id, dag_id))
    return {"dag": _with_counts(db, found[:1], True, env_id, dag_id)[0], "runs": [_run_out(r) for r in runs]}


@router.put("/api/ops/dag")
def update_dag(env_id: str, dag_id: str, body: DagSettings, db: Db = Depends(current_db)):
    """Platform settings of a DAG. A field left out is kept; a field sent as null or "" is cleared."""
    if not _q(db, "SELECT 1 AS X FROM OPS.DAG D JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = D.ENV_ID "
                  "WHERE D.ENV_ID = %s AND D.DAG_ID = %s", (env_id, dag_id)):
        raise HTTPException(404, f"DAG {dag_id} not found in {env_id}")
    values: Dict[str, Any] = {}
    for key in body.model_fields_set:
        value = getattr(body, key)
        if isinstance(value, str):
            value = value.strip() or None
        values[key] = value
    if values.get("criticality"):
        values["criticality"] = values["criticality"].upper()
        if values["criticality"] not in CRITICALITY:
            raise HTTPException(422, f"criticality must be one of {', '.join(CRITICALITY)}")
    if values.get("expected_by_cron") and not _CRON.match(values["expected_by_cron"]):
        raise HTTPException(422, "expected_by_cron must be a cron expression (5 or 6 fields) or @daily, @hourly, ...")
    if values.get("repo_path"):
        values["repo_path"] = values["repo_path"].replace("\\", "/").strip("/")
    if values.get("timezone"):
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(values["timezone"])
        except Exception:
            raise HTTPException(422, "timezone must be an IANA name such as Europe/London") from None
    if values.get("mute_until"):
        until = ts(values["mute_until"])
        if not until:
            raise HTTPException(422, "mute_until must be an ISO time")
        if datetime.fromisoformat(until) - datetime.now(timezone.utc) > timedelta(days=30):
            raise HTTPException(422, "A DAG mute lasts at most 30 days")
        values["mute_until"] = until
    if values:
        sets = [(k.upper(), v) for k, v in values.items()]
        _x(db, f"UPDATE OPS.DAG SET {', '.join(c + (' = TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)' if c == 'MUTE_UNTIL' else ' = %s') for c, _ in sets)}, "
               "UPDATED_AT = CURRENT_TIMESTAMP() "
               "WHERE ENV_ID = %s AND DAG_ID = %s", tuple(v for _, v in sets) + (env_id, dag_id))
    return get_dag(env_id, dag_id, db)


@router.get("/api/ops/run")
def get_run(env_id: str, dag_id: str, run_id: str, db: Db = Depends(current_db)):
    env = _env(db, env_id)
    found = _q(db, _RUNS_SQL.format(extra="AND R.RUN_ID = %s", limit=1), (env_id, dag_id, env_id, dag_id, run_id))
    if not found:
        raise HTTPException(404, f"Run {run_id} of {dag_id} not found")
    run = {**_run_out(found[0]), "env_id": env_id, "dag_id": dag_id, "note": found[0].get("note"),
           "source": found[0].get("source"), "updated_at": _iso(found[0].get("updated_at")),
           "airflow_url": links(env.get("airflow_url"), env.get("api_version"), dag_id, run_id)}
    tasks = _q(db, """
        SELECT TASK_ID, MAP_INDEX, TRY_NUMBER, STATE, OPERATOR, STARTED_AT, ENDED_AT, DURATION_S, ERROR_EXCERPT, HOSTNAME,
               TRY_NUMBER = MAX(TRY_NUMBER) OVER (PARTITION BY TASK_ID, MAP_INDEX) AS IS_LATEST
          FROM OPS.TASK_RUN WHERE ENV_ID = %s AND DAG_ID = %s AND RUN_ID = %s
         ORDER BY STARTED_AT NULLS LAST, TASK_ID, MAP_INDEX, TRY_NUMBER LIMIT 5000""", (env_id, dag_id, run_id))
    return {"run": run, "tasks": [{"task_id": t["task_id"], "map_index": int(t["map_index"]), "try_number": int(t["try_number"]),
                                   "state": t.get("state"), "operator": t.get("operator"), "start": _iso(t.get("started_at")),
                                   "end": _iso(t.get("ended_at")), "duration_s": _num(t.get("duration_s")),
                                   "error_excerpt": t.get("error_excerpt"), "hostname": t.get("hostname"),
                                   "is_latest_try": bool(t.get("is_latest"))} for t in tasks]}


@router.get("/api/ops/task-log")
def task_log(env_id: str, dag_id: str, run_id: str, task_id: str, try_number: Optional[int] = Query(default=None, alias="try", ge=0),
             map_index: int = Query(default=-1, ge=-1), db: Db = Depends(current_db)):
    """The task's log from Airflow, every page up to 256 KB (the end kept), secrets and personal data redacted."""
    env = _env(db, env_id)
    if try_number is None:
        found = _q(db, """SELECT MAX(TRY_NUMBER) AS T FROM OPS.TASK_RUN WHERE ENV_ID = %s AND DAG_ID = %s AND RUN_ID = %s
                            AND TASK_ID = %s AND MAP_INDEX = %s""", (env_id, dag_id, run_id, task_id, map_index))
        try_number = int(found[0]["t"]) if found and found[0].get("t") is not None else 1
    try:
        log = _mwaa(env).task_log(dag_id, run_id, task_id, max(int(try_number), 1), map_index)
    except MwaaError as exc:
        return {"text": "", "truncated": False, "redacted": False, "source": "unavailable", "detail": str(exc)}
    text, count = redact_count(log.get("text") or "")
    detail = "" if text else "Airflow returned an empty log (it may have expired from CloudWatch)."
    return {"text": text, "truncated": bool(log.get("truncated")), "redacted": count > 0, "source": "airflow",
            "detail": detail, "try_number": try_number}


# ---------------------------------------------------------------- signed push from the Airflow listener plugin

def _push_secret(db: Db, env_id: str) -> Tuple[Optional[str], bool]:
    """(secret or None, push enabled), cached a minute per environment."""
    now = time.time()
    with _secrets_lock:
        hit = _secrets.get(env_id)
        if hit and hit[0] > now:
            return hit[1], hit[2]
    key = (os.environ.get("AIP_SECRET_KEY") or os.environ.get("JIRA_TOKEN_KEY") or "").strip()
    secret, enabled = None, False
    if key:
        try:
            found = db.query("""SELECT IFF(PUSH_SECRET IS NULL, NULL, TO_VARCHAR(DECRYPT(PUSH_SECRET, %s), 'UTF-8')) AS S,
                                       PUSH_ENABLED FROM OPS.AIRFLOW_ENV WHERE ENV_ID = %s""", (key, env_id))
        except Exception:
            found = []  # a key that changed cannot decrypt: the secret must be rotated
        if found:
            secret, enabled = found[0].get("s"), bool(found[0].get("push_enabled"))
    with _secrets_lock:
        _secrets[env_id] = (now + _SECRET_TTL, secret, enabled)
    return secret, enabled


def ingest_event(db: Db, env_id: str, timestamp: str, event_id: str, signature: str, raw: bytes) -> Dict[str, Any]:
    from services.ops import store
    from services.ops.normalize import push_row

    secret, enabled = _push_secret(db, env_id)
    if not secret or not verify(secret, timestamp, raw, signature):
        raise HTTPException(401, "Invalid signature or unknown environment")
    if not enabled:
        raise HTTPException(403, "Push is disabled for this environment")
    try:
        body = json.loads(raw.decode("utf-8"))
        assert isinstance(body, dict), "the body must be a JSON object"
        kind, row = push_row(env_id, body)
    except (ValueError, AssertionError, UnicodeDecodeError, TypeError, KeyError) as exc:
        raise HTTPException(400, f"Malformed event: {str(exc)[:200]}") from exc
    if not store.record_event(db, event_id, env_id, "PUSH", kind, body):
        raise HTTPException(409, "Replayed event: this event id was already received")
    try:
        store.ensure_dags(db, env_id, [row["dag_id"]])
        if kind == "dag_run":
            store.upsert_runs(db, [row])
        else:
            store.upsert_tasks(db, [row])
    except Exception as exc:
        try:
            store.mark_event(db, event_id, f"{type(exc).__name__}: {exc}")
        except Exception:
            pass
        raise HTTPException(500, "The event was received but could not be stored; the poller will pick the run up.") from exc
    store.mark_event(db, event_id)
    try:   # alert in seconds; the worker's detect job sees the row again, so a failure here loses nothing
        from app.worker import detect_rows

        detect_rows(db, [row] if kind == "dag_run" else [], [row] if kind != "dag_run" else [])
    except Exception:
        pass
    return {"ok": True, "event_id": event_id, "kind": kind}


@router.post("/api/ops/ingest")
async def ingest(request: Request):
    """Signed events from the Airflow listener plugin. No user session: the signature authenticates the caller."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise HTTPException(413, "Event too large (64 KB max)")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "Event too large (64 KB max)")
    h = request.headers
    env_id, timestamp = h.get("x-gdp-env"), h.get("x-gdp-timestamp")
    event_id, signature = h.get("x-gdp-event-id"), h.get("x-gdp-signature")
    ok, reason = check_headers(env_id, timestamp, event_id, signature)
    if not ok:
        raise HTTPException(400 if reason.startswith("missing") else 401, reason)

    def work() -> Dict[str, Any]:
        try:
            db = system_db()
        except SnowflakeSessionError as exc:
            raise HTTPException(503, f"Ingest is not available: {exc}") from exc
        return ingest_event(db, env_id, timestamp, event_id, signature, raw)

    return await run_in_threadpool(work)
