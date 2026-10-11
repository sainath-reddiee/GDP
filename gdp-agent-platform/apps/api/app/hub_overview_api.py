"""Command Center overview (/dashboard): the hero ribbon and the six asset store cards in one call.

Every block fails soft: a block whose tables are not deployed (or that the caller's role cannot read) comes back as
null and the dashboard shows a dash, so one missing store never blanks the page. `domain` is a domain id or name.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.db import Db
from app.main import current_db

router = APIRouter()

PROFILES = """
WITH P AS (
    SELECT PROFILE_ID, UPPER(DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TABLE_NAME) AS FQN, MAX(PROFILED_AT) AS AT
      FROM GOVERNANCE.DATA_PROFILES WHERE TRUE {domain} GROUP BY 1, 2
), R AS (
    SELECT P.*, ROW_NUMBER() OVER (PARTITION BY FQN ORDER BY AT DESC) AS RN FROM P
), LATEST AS (
    SELECT D.COLUMN_NAME, D.NULL_PCT, D.CARDINALITY, D.PII_CLASSIFICATION, R.FQN
      FROM GOVERNANCE.DATA_PROFILES D JOIN R ON R.PROFILE_ID = D.PROFILE_ID AND R.RN = 1
), PREV AS (
    SELECT D.COLUMN_NAME, D.NULL_PCT, R.FQN
      FROM GOVERNANCE.DATA_PROFILES D JOIN R ON R.PROFILE_ID = D.PROFILE_ID AND R.RN = 2
), DRIFT AS (
    SELECT L.FQN, L.COLUMN_NAME FROM LATEST L JOIN PREV V ON V.FQN = L.FQN AND V.COLUMN_NAME = L.COLUMN_NAME
     WHERE ABS(COALESCE(L.NULL_PCT, 0) - COALESCE(V.NULL_PCT, 0)) >= 10
)
SELECT (SELECT COUNT(DISTINCT FQN) FROM LATEST) AS TABLES,
       (SELECT COUNT(*) FROM LATEST) AS COLUMNS,
       (SELECT COUNT_IF(CARDINALITY = 'HIGH') FROM LATEST) AS HIGH_CARDINALITY,
       (SELECT COUNT_IF(COALESCE(PII_CLASSIFICATION, 'NONE') <> 'NONE') FROM LATEST) AS PII,
       (SELECT COUNT(*) FROM DRIFT) AS DRIFT,
       (SELECT COUNT(DISTINCT FQN) FROM DRIFT) AS DRIFT_TABLES,
       (SELECT COUNT_IF(COALESCE(NULL_PCT, 0) < 20) FROM LATEST L
         WHERE NOT EXISTS (SELECT 1 FROM DRIFT X WHERE X.FQN = L.FQN AND X.COLUMN_NAME = L.COLUMN_NAME)) AS HEALTHY"""

# Profiles kept in the persistent profile store (METADATA.TABLE_PROFILES) for tables the catalog has not seen yet:
# cached profiles are reused without a new pass, so they never reach GOVERNANCE.DATA_PROFILES.
LEGACY_PROFILES = """
SELECT COUNT(*) AS TABLES, SUM(COALESCE(T.COLUMN_COUNT, 0)) AS COLUMNS, SUM(COALESCE(T.PII_COLUMNS, 0)) AS PII
  FROM METADATA.TABLE_PROFILES T {join}
 WHERE T.SOURCE_FINGERPRINT IS NOT NULL AND COALESCE(T.STATUS, '') <> 'FAILED' AND NOT STARTSWITH(T.SOURCE_NAME, 'ORACLE_') {exclude}"""
CATALOG_TABLES = """UPPER(T.DATABASE_NAME || '.' || T.SCHEMA_NAME || '.' || T.TABLE_NAME) NOT IN (
    SELECT DISTINCT UPPER(DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TABLE_NAME) FROM GOVERNANCE.DATA_PROFILES)"""

RUN_DESIGNS = """
WITH D AS (
    SELECT M.RUN_ID, M.STATUS, ROW_NUMBER() OVER (PARTITION BY M.RUN_ID ORDER BY M.VERSION DESC) AS RN
      FROM MODELING.MODEL_DESIGN M {join}
)
SELECT COUNT_IF(RN = 1 AND STATUS IN ('APPROVED', 'DRAFT')) AS DESIGNS, COUNT_IF(RN = 1 AND STATUS = 'DRAFT') AS IN_DESIGN FROM D"""

RUN_PUBLICATIONS = """
WITH P AS (
    SELECT G.RUN_ID, G.STATUS, G.HEAD_BRANCH,
           ROW_NUMBER() OVER (PARTITION BY G.RUN_ID ORDER BY G.CREATED_AT DESC) AS RN
      FROM CODEGEN.GIT_PUBLICATION G {join}
)
SELECT COUNT(DISTINCT IFF(STATUS = 'PUBLISHED', RUN_ID, NULL)) AS PUBLISHED,
       COUNT(DISTINCT IFF(STATUS = 'PUBLISHED', HEAD_BRANCH, NULL)) AS BRANCHES,
       COUNT_IF(RN = 1 AND STATUS = 'FAILED') AS FAILED FROM P"""

RUN_CHECKS = """
WITH C AS (
    SELECT EXPECTATION_ID, TARGET_TABLE FROM CONTRACT.SODA_EXPECTATION_REGISTRY
     WHERE IS_CURRENT AND STATUS = 'APPROVED' {domain}
), R AS (
    SELECT EXPECTATION_ID, OUTCOME FROM QUALITY.CHECK_RESULT
     WHERE EXPECTATION_ID IN (SELECT EXPECTATION_ID FROM C)
   QUALIFY ROW_NUMBER() OVER (PARTITION BY EXPECTATION_ID ORDER BY CREATED_AT DESC) = 1
)
SELECT (SELECT COUNT(*) FROM C) AS CHECKS, (SELECT COUNT(DISTINCT UPPER(TARGET_TABLE)) FROM C) AS TABLES,
       COUNT_IF(OUTCOME = 'PASS') AS PASSED, COUNT_IF(OUTCOME = 'WARN') AS WARNED,
       COUNT_IF(OUTCOME IN ('FAIL', 'ERROR')) AS FAILED, COUNT_IF(OUTCOME <> 'NOT_EVALUATED') AS EVALUATED FROM R"""

RUN_TESTS = """
SELECT COUNT(*) AS TESTS FROM CONTRACT.QA_TEST_CASE
 WHERE NOT COALESCE(IS_DELETED, FALSE) {domain}"""

# the latest execution of each run's (or table's) suite; domain packs are counted by the QA Store summary
RUN_QA = """
WITH Q AS (
    SELECT TESTS, PASSED, FAILED, ERRORS,
           ROW_NUMBER() OVER (PARTITION BY COALESCE(RUN_ID, TARGET) ORDER BY STARTED_AT DESC) AS RN
      FROM QUALITY.QA_RUN WHERE COALESCE(SCOPE, 'RUN') <> 'DOMAIN' {domain}
)
SELECT SUM(TESTS) AS TESTS, SUM(PASSED) AS PASSED, SUM(FAILED) AS FAILED, SUM(ERRORS) AS ERRORS, COUNT(*) AS SUITES
  FROM Q WHERE RN = 1"""

STTM = """
WITH L AS (
    SELECT CATALOG_ID, STATUS, ROW_NUMBER() OVER (PARTITION BY CATALOG_ID ORDER BY VERSION DESC) AS RN
      FROM GOVERNANCE.STTM_CATALOG WHERE TRUE {domain}
)
SELECT COUNT_IF(RN = 1) AS MAPPINGS, COUNT_IF(RN = 1 AND STATUS = 'APPROVED') AS VERIFIED,
       COUNT_IF(RN = 1 AND STATUS IN ('DRAFT', 'REVIEW')) AS PENDING FROM L"""

MODELS = """
WITH M AS (
    SELECT DESIGN_ID, STATUS, BRANCH, ROW_NUMBER() OVER (PARTITION BY DESIGN_ID ORDER BY VERSION DESC) AS RN
      FROM GOVERNANCE.MODEL_DESIGNS WHERE TRUE {domain}
)
SELECT COUNT_IF(RN = 1) AS DESIGNS, COUNT_IF(RN = 1 AND STATUS = 'PUBLISHED') AS PUBLISHED,
       COUNT_IF(RN = 1 AND BRANCH IS NOT NULL) AS BRANCHES,
       COUNT_IF(RN = 1 AND STATUS IN ('DRAFT', 'FINALIZED')) AS IN_DESIGN,
       COUNT_IF(RN = 1 AND STATUS = 'PUBLISH_FAILED') AS FAILED FROM M"""


def _soft(fn: Callable[[], Any]) -> Any:
    """A block's value, or None when its tables are missing or unreadable (HTTP errors from a store included)."""
    try:
        return fn()
    except Exception:
        return None


def _resolve(db: Db, domain: Optional[str]) -> Optional[dict]:
    if not domain:
        return None
    found = db.query("""SELECT DOMAIN_ID, DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY
                         WHERE DOMAIN_ID = %s OR UPPER(DOMAIN_NAME) = UPPER(%s) LIMIT 1""", (domain, domain))
    if not found:
        raise HTTPException(404, f"domain {domain} not found")
    return found[0]


def _n(row: Optional[dict], key: str) -> int:
    return int((row or {}).get(key) or 0)


def sources(db: Db, domain_id: Optional[str]) -> dict:
    clause, params = ("AND DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    by_type = {r["source_type"]: int(r["n"]) for r in db.query(
        f"SELECT SOURCE_TYPE, COUNT(*) AS N FROM SOURCE.SOURCE_REGISTRY WHERE ACTIVE_FLAG {clause} GROUP BY 1", params)}
    try:
        ext = db.query(f"""SELECT COUNT(*) AS N, COUNT_IF(STATUS = 'ACTIVE') AS ACTIVE
                             FROM PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG WHERE STATUS <> 'RETIRED' {clause}""", params)[0]
    except Exception:
        ext = {}
    shares = by_type.get("SNOWFLAKE_SHARE", 0)
    databases = by_type.get("SNOWFLAKE_DATABASE", 0) + by_type.get("DATABASE", 0)
    other = sum(by_type.values()) - shares - databases
    return {"total": sum(by_type.values()) + _n(ext, "n"), "shares": shares, "databases": databases, "other": other,
            "external": _n(ext, "n"), "external_active": _n(ext, "active")}


def profiling(db: Db, domain_id: Optional[str]) -> dict:
    """Tables and columns from both profile stores; drift and health only from the catalog (it keeps every pass)."""
    try:
        row = db.query(PROFILES.format(domain="AND DOMAIN_ID = %s" if domain_id else ""), (domain_id,) if domain_id else ())[0]
        catalog = True
    except Exception:
        row, catalog = {}, False
    join = "JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = T.PROFILED_IN_RUN AND R.DOMAIN_ID = %s" if domain_id else ""
    params = (domain_id,) if domain_id else ()
    legacy: Optional[dict] = None
    for exclude in ([f"AND {CATALOG_TABLES}"] if catalog else []) + [""]:
        try:
            found = db.query(LEGACY_PROFILES.format(join=join, exclude=exclude), params)
            legacy = found[0] if found else {}
            break
        except Exception:
            continue
    if not catalog and legacy is None:
        raise LookupError("no profile store is readable")
    legacy = legacy or {}
    columns = _n(row, "columns")
    return {"tables": _n(row, "tables") + _n(legacy, "tables"), "columns": columns + _n(legacy, "columns"),
            "pii": _n(row, "pii") + _n(legacy, "pii"), "catalog_tables": _n(row, "tables"),
            "high_cardinality": _n(row, "high_cardinality"), "drift": _n(row, "drift"),
            "drift_tables": _n(row, "drift_tables"),
            "health": round(100 * _n(row, "healthy") / columns, 1) if columns else None}


def sttm(db: Db, domain_id: Optional[str]) -> dict:
    row = db.query(STTM.format(domain="AND DOMAIN_ID = %s" if domain_id else ""), (domain_id,) if domain_id else ())[0]
    return {"mappings": _n(row, "mappings"), "verified": _n(row, "verified"), "pending": _n(row, "pending")}


def _first(db: Db, sql: str, params: tuple) -> dict:
    try:
        found = db.query(sql, params)
        return found[0] if found else {}
    except Exception:
        return {}


def _run_join(alias: str, domain_id: Optional[str]) -> tuple:
    return ((f"JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = {alias}.RUN_ID AND R.DOMAIN_ID = %s", (domain_id,))
            if domain_id else ("", ()))


def models(db: Db, domain_id: Optional[str], domain_name: Optional[str]) -> dict:
    """Run model designs and dbt publications plus the Modeling Store's own designs."""
    clause = "AND (UPPER(DOMAIN_NAME) = UPPER(%s) OR UPPER(PARENT_DOMAIN) = UPPER(%s))" if domain_name else ""
    hub = _first(db, MODELS.format(domain=clause), (domain_name, domain_name) if domain_name else ())
    join, params = _run_join("M", domain_id)
    runs = _first(db, RUN_DESIGNS.format(join=join), params)
    join, params = _run_join("G", domain_id)
    pubs = _first(db, RUN_PUBLICATIONS.format(join=join), params)
    if not (hub or runs or pubs):
        raise LookupError("no model store is readable")
    return {"designs": _n(hub, "designs") + _n(runs, "designs"), "published": _n(hub, "published") + _n(pubs, "published"),
            "branches": _n(hub, "branches") + _n(pubs, "branches"), "in_design": _n(hub, "in_design") + _n(runs, "in_design"),
            "failed": _n(hub, "failed") + _n(pubs, "failed"), "run_designs": _n(runs, "designs"),
            "hub_designs": _n(hub, "designs")}


def qa(db: Db, domain_id: Optional[str]) -> Optional[dict]:
    """Domain packs (QA Store) plus the tests saved in runs and tables and their latest executions."""
    from app.qa_api import _call, _svc

    try:
        packs = _call(db, _svc("packs").summary, domain_id)
    except Exception:
        packs = None
    clause = "AND (DOMAIN_ID = %s OR RUN_ID IN (SELECT RUN_ID FROM CORE.WORKFLOW_RUN WHERE DOMAIN_ID = %s))" if domain_id else ""
    params = (domain_id, domain_id) if domain_id else ()
    saved = _first(db, RUN_TESTS.format(domain=clause), params)
    runs = _first(db, RUN_QA.format(domain=clause), params)
    if packs is None and not saved and not runs:
        raise LookupError("no QA store is readable")
    out = dict(packs or {"packs": 0, "approved_packs": 0, "rules": 0, "pending": 0, "domains": 0, "top_tags": [],
                         "pass_rate": None})
    tests, passed = _n(runs, "tests"), _n(runs, "passed")
    out.update({"run_tests": _n(saved, "tests"), "executed_tests": tests, "run_suites": _n(runs, "suites"),
                "run_pass_rate": round(100 * passed / tests, 1) if tests else None})
    if out.get("pass_rate") is None:
        out["pass_rate"] = out["run_pass_rate"]
    return out


def dq(db: Db, domain_id: Optional[str], profiled_tables: int = 0) -> Optional[dict]:
    """Approved checks in runs plus the Data Quality Store's catalog, with the pass rate of each check's latest result."""
    from app.quality_api import summary

    try:
        store = summary(domain_id, db)
    except Exception:
        store = {"deployed": False}
    runs = _first(db, RUN_CHECKS.format(domain="AND DOMAIN_ID = %s" if domain_id else ""), (domain_id,) if domain_id else ())
    if not store.get("deployed") and not runs:
        return None
    s = {k: int(store.get(k) or 0) for k in ("checks", "tables", "passed", "warned", "failed", "never_run",
                                             "scheduled_tables")} if store.get("deployed") else {}
    passed = _n(runs, "passed") + s.get("passed", 0)
    warned = _n(runs, "warned") + s.get("warned", 0)
    failed = _n(runs, "failed") + s.get("failed", 0)
    evaluated = _n(runs, "evaluated") + (s.get("passed", 0) + s.get("warned", 0) + s.get("failed", 0))
    checks = _n(runs, "checks") + s.get("checks", 0)
    tables = _n(runs, "tables") + s.get("tables", 0)
    return {"deployed": True, "checks": checks, "run_checks": _n(runs, "checks"), "store_checks": s.get("checks", 0),
            "tables": tables, "passed": passed, "warned": warned, "failed": failed,
            "never_run": max(0, checks - evaluated), "scheduled_tables": s.get("scheduled_tables", 0),
            "pass_rate": round(100 * passed / evaluated, 1) if evaluated else None,
            "coverage": round(100 * min(tables, profiled_tables) / profiled_tables, 1) if profiled_tables else None}


# ---------------------------------------------------------------- developer stats (the hero ribbon)

DELIVERY = """
WITH E AS (
    SELECT E.RUN_ID, E.TO_STATE, E.CREATED_AT FROM CORE.WORKFLOW_EVENT E
      JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = E.RUN_ID
     WHERE E.CREATED_AT >= DATEADD('day', -7, CURRENT_TIMESTAMP()) AND E.TO_STATE IN ('COMPLETED', 'FAILED')
       AND R.DELETED_AT IS NULL AND COALESCE(R.ENVIRONMENT, '') <> 'TEST' {domain}
), DONE AS (
    SELECT E.RUN_ID, E.TO_STATE, E.CREATED_AT, R.CREATED_AT AS STARTED_AT FROM E JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = E.RUN_ID
)
SELECT (SELECT COUNT(DISTINCT RUN_ID) FROM DONE WHERE TO_STATE = 'COMPLETED') AS COMPLETED,
       (SELECT COUNT(DISTINCT RUN_ID) FROM DONE WHERE TO_STATE = 'FAILED') AS FAILED,
       (SELECT MEDIAN(DATEDIFF('minute', STARTED_AT, CREATED_AT)) FROM DONE WHERE TO_STATE = 'COMPLETED') AS LEAD_MINUTES,
       (SELECT COUNT(*) FROM CORE.WORKFLOW_RUN R WHERE R.DELETED_AT IS NULL AND NOT COALESCE(R.IS_ARCHIVED, FALSE)
           AND COALESCE(R.ENVIRONMENT, '') <> 'TEST' AND R.CURRENT_STATE NOT IN ('CREATED', 'COMPLETED', 'FAILED', 'CANCELLED') {domain}) AS ACTIVE,
       (SELECT COUNT(*) FROM CORE.WORKFLOW_RUN R WHERE R.DELETED_AT IS NULL AND COALESCE(R.ENVIRONMENT, '') <> 'TEST'
           AND R.CREATED_AT >= DATEADD('day', -7, CURRENT_TIMESTAMP()) {domain}) AS STARTED"""

DELIVERY_DAILY = """
SELECT TO_VARCHAR(E.CREATED_AT::DATE) AS DAY, COUNT_IF(E.TO_STATE = 'COMPLETED') AS COMPLETED, COUNT_IF(E.TO_STATE = 'FAILED') AS FAILED
  FROM CORE.WORKFLOW_EVENT E JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = E.RUN_ID
 WHERE E.CREATED_AT >= DATEADD('day', -7, CURRENT_TIMESTAMP()) AND E.TO_STATE IN ('COMPLETED', 'FAILED')
   AND R.DELETED_AT IS NULL AND COALESCE(R.ENVIRONMENT, '') <> 'TEST' {domain}
 GROUP BY 1 ORDER BY 1"""


def delivery(db: Db, domain_id: Optional[str]) -> dict:
    """Runs over the last 7 days: completed, failed, success rate and median lead time (start to completion), with
    the runs active now and a day-by-day series."""
    clause, params = ("AND R.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    row = db.query(DELIVERY.format(domain=clause), params * 3)[0]
    daily = db.query(DELIVERY_DAILY.format(domain=clause), params)
    done, failed = _n(row, "completed"), _n(row, "failed")
    lead = row.get("lead_minutes")
    return {"completed": done, "failed": failed, "started": _n(row, "started"), "active": _n(row, "active"),
            "success_rate": round(100 * done / (done + failed), 1) if done + failed else None,
            "lead_minutes": int(lead) if lead is not None else None,
            "daily": [{"day": d["day"], "completed": int(d["completed"] or 0), "failed": int(d["failed"] or 0)} for d in daily]}


WAITING = """
SELECT COUNT(*) AS RUNS, MAX(DATEDIFF('minute', COALESCE(W.AT, R.UPDATED_AT, R.CREATED_AT), CURRENT_TIMESTAMP())) AS OLDEST_MINUTES
  FROM CORE.WORKFLOW_RUN R
  LEFT JOIN (SELECT RUN_ID, TO_STATE, MAX(CREATED_AT) AS AT FROM CORE.WORKFLOW_EVENT GROUP BY 1, 2) W
         ON W.RUN_ID = R.RUN_ID AND W.TO_STATE = R.CURRENT_STATE
 WHERE R.DELETED_AT IS NULL AND NOT COALESCE(R.IS_ARCHIVED, FALSE) AND R.CURRENT_STATE IN ({review}) {domain}"""


def waiting(db: Db, domain_id: Optional[str]) -> dict:
    """What is waiting on a person: runs at a review step, change requests in Approvals, and asset versions in review
    (domain models, data quality suites, QA packs), with the oldest wait."""
    from services.workflow.listing import REVIEW_STATES

    clause, params = ("AND R.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    runs = db.query(WAITING.format(review=", ".join(f"'{s}'" for s in REVIEW_STATES), domain=clause), params)[0]
    requests = _soft(lambda: db.query("""SELECT COUNT(*) AS N, MAX(DATEDIFF('minute', CREATED_AT, CURRENT_TIMESTAMP())) AS OLDEST
                                           FROM GOVERNANCE.CHANGE_REQUEST WHERE STATUS = 'PENDING'""")[0]) or {}
    reviews = 0
    for sql in ("SELECT COUNT(*) AS N FROM GOVERNANCE.MODEL_DESIGNS WHERE MODEL_ID IS NOT NULL AND STATUS = 'IN_REVIEW'"
                + (" AND DOMAIN_ID = %s" if domain_id else ""),
                "SELECT COUNT(*) AS N FROM GOVERNANCE.DQ_SUITE_VERSION V JOIN GOVERNANCE.DQ_SUITE S ON S.SUITE_ID = V.SUITE_ID "
                "WHERE V.STATUS = 'IN_REVIEW'" + (" AND S.DOMAIN_ID = %s" if domain_id else ""),
                "SELECT COUNT(*) AS N FROM GOVERNANCE.DOMAIN_QA_PACKS WHERE STATUS = 'PENDING_APPROVAL'"
                + (" AND DOMAIN_ID = %s" if domain_id else "")):
        reviews += _n(_soft(lambda: db.query(sql, params)[0]), "n")
    oldest = [v for v in (runs.get("oldest_minutes"), requests.get("oldest")) if v is not None]
    return {"runs": _n(runs, "runs"), "requests": _n(requests, "n"), "versions": reviews,
            "total": _n(runs, "runs") + _n(requests, "n") + reviews,
            "oldest_minutes": int(max(oldest)) if oldest else None}


FRESHNESS = """
WITH L AS (
    SELECT L.LANDING_DATABASE, L.LANDING_SCHEMA, L.LANDING_TABLE, MAX(L.INGESTED_AT) AS LAST_LOAD
      FROM SOURCE.LANDING_TABLE_REGISTRY L LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = L.RUN_ID
     WHERE L.INGESTION_STATUS = 'COMPLETE' AND COALESCE(L.INGESTION_METHOD, '') <> 'IN_PLACE' {domain}
     GROUP BY 1, 2, 3
), F AS (
    SELECT DISTINCT L.LANDING_DATABASE, L.LANDING_SCHEMA, L.LANDING_TABLE
      FROM SOURCE.LANDING_TABLE_REGISTRY L LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = L.RUN_ID
     WHERE L.INGESTION_STATUS = 'FAILED' AND L.CREATED_AT >= DATEADD('day', -7, CURRENT_TIMESTAMP()) {domain}
)
SELECT (SELECT COUNT(*) FROM L) AS TABLES,
       (SELECT COUNT_IF(LAST_LOAD >= DATEADD('hour', -24, CURRENT_TIMESTAMP())) FROM L) AS FRESH,
       (SELECT COUNT_IF(LAST_LOAD < DATEADD('day', -7, CURRENT_TIMESTAMP())) FROM L) AS STALE,
       (SELECT MAX(DATEDIFF('hour', LAST_LOAD, CURRENT_TIMESTAMP())) FROM L) AS OLDEST_HOURS,
       (SELECT COUNT(*) FROM F) AS FAILED_LOADS"""


def freshness(db: Db, domain_id: Optional[str]) -> dict:
    """Landed tables (copies, not tables profiled in place): loaded in the last 24 hours, not loaded for over 7 days,
    the oldest load, loads that failed in the last 7 days, and external pipelines registered but not active."""
    clause, params = ("AND R.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    row = db.query(FRESHNESS.format(domain=clause), params * 2)[0]
    ext = _soft(lambda: db.query("SELECT COUNT_IF(STATUS IN ('REGISTERED', 'PAUSED')) AS IDLE FROM PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG "
                                 "WHERE STATUS <> 'RETIRED'" + (" AND DOMAIN_ID = %s" if domain_id else ""), params)[0]) or {}
    hours = row.get("oldest_hours")
    return {"tables": _n(row, "tables"), "fresh": _n(row, "fresh"), "stale": _n(row, "stale"),
            "failed_loads": _n(row, "failed_loads"), "idle_pipelines": _n(ext, "idle"),
            "oldest_hours": int(hours) if hours is not None else None}


FAILING_QA = """
WITH R AS (
    SELECT Q.TEST_ID, Q.OUTCOME, ROW_NUMBER() OVER (PARTITION BY Q.TEST_ID ORDER BY Q.CREATED_AT DESC) AS RN
      FROM QUALITY.QA_RESULT Q
)
SELECT COUNT(*) AS TESTS, COUNT(DISTINCT COALESCE(T.TARGET_FQN, T.RUN_ID)) AS TARGETS
  FROM R JOIN CONTRACT.QA_TEST_CASE T ON T.TEST_ID = R.TEST_ID
 WHERE R.RN = 1 AND R.OUTCOME IN ('FAIL', 'ERROR') AND NOT COALESCE(T.IS_DELETED, FALSE) {domain}"""


def failing(db: Db, domain_id: Optional[str], dq_block: Optional[dict]) -> dict:
    """Checks failing now: data quality checks whose latest result failed, QA tests whose latest execution failed or
    errored, and the tables they cover."""
    clause, params = ("AND (T.DOMAIN_ID = %s OR T.RUN_ID IN (SELECT RUN_ID FROM CORE.WORKFLOW_RUN WHERE DOMAIN_ID = %s))",
                      (domain_id, domain_id)) if domain_id else ("", ())
    qa_row = _soft(lambda: db.query(FAILING_QA.format(domain=clause), params)[0]) or {}
    dq_tables = _soft(lambda: db.query("SELECT COUNT(DISTINCT TABLE_NAME) AS N FROM GOVERNANCE.DQ_CHECKS_CATALOG "
                                       "WHERE STATUS = 'ACTIVE' AND LAST_RUN_STATUS = 'FAIL'"
                                       + (" AND DOMAIN_ID = %s" if domain_id else ""), (domain_id,) if domain_id else ())[0]) or {}
    dq_failed = int((dq_block or {}).get("failed") or 0)
    return {"dq_checks": dq_failed, "qa_tests": _n(qa_row, "tests"), "total": dq_failed + _n(qa_row, "tests"),
            "tables": _n(dq_tables, "n") + _n(qa_row, "targets")}


@router.get("/api/hub/overview")
def overview(domain: Optional[str] = None, db: Db = Depends(current_db)):
    """Every block at once: the independent blocks run in parallel (each is a few small queries), and the answer is
    kept 20 seconds per user, role and domain (dropped by any write, app/perf.py)."""
    from app.perf import read_cache

    return read_cache(db, ("overview", (domain or "").upper()), 20, lambda: _overview(db, domain))


def _overview(db: Db, domain: Optional[str]) -> dict:
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    scope = _resolve(db, domain)
    did = scope["domain_id"] if scope else None
    name = scope["domain_name"] if scope else None
    jobs = {
        "sources": lambda: sources(db, did),
        "profiling": lambda: profiling(db, did),
        "models": lambda: models(db, did, name),
        "sttm": lambda: sttm(db, did),
        "qa": lambda: qa(db, did),
        "delivery": lambda: delivery(db, did),
        "waiting": lambda: waiting(db, did),
        "freshness": lambda: freshness(db, did),
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        # each job runs in a copy of this request's context, so its statements count in the request's timing
        futures = {k: pool.submit(contextvars.copy_context().run, _soft, fn) for k, fn in jobs.items()}
        blocks = {k: f.result() for k, f in futures.items()}
    blocks["dq"] = _soft(lambda: dq(db, did, (blocks["profiling"] or {}).get("tables", 0)))
    blocks["failing"] = _soft(lambda: failing(db, did, blocks["dq"]))
    return {"domain": scope, **{k: blocks[k] for k in ("sources", "profiling", "models", "sttm", "qa", "dq",
                                                       "delivery", "waiting", "freshness", "failing")}}
