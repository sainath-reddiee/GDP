"""Command Center overview (/dashboard): the hero ribbon and the six asset store cards in one call.

Every block fails soft: a block whose tables are not deployed (or that the caller's role cannot read) comes back as
null and the dashboard shows a dash, so one missing store never blanks the page. `domain` is a domain id or name.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.db import Db
from app.main import current_db
from services.workflow.listing import IN_FLIGHT_SQL, REVIEW_SQL, RUN_POPULATION

router = APIRouter()

PROFILES = """
WITH P AS (
    SELECT PROFILE_ID, UPPER(DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TABLE_NAME) AS FQN, MAX(PROFILED_AT) AS AT
      FROM CORE.DATA_PROFILES WHERE TRUE {domain} GROUP BY 1, 2
), R AS (
    SELECT P.*, ROW_NUMBER() OVER (PARTITION BY FQN ORDER BY AT DESC) AS RN FROM P
), LATEST AS (
    SELECT D.COLUMN_NAME, D.NULL_PCT, D.CARDINALITY, D.PII_CLASSIFICATION, R.FQN
      FROM CORE.DATA_PROFILES D JOIN R ON R.PROFILE_ID = D.PROFILE_ID AND R.RN = 1
), PREV AS (
    SELECT D.COLUMN_NAME, D.NULL_PCT, R.FQN
      FROM CORE.DATA_PROFILES D JOIN R ON R.PROFILE_ID = D.PROFILE_ID AND R.RN = 2
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

# Profiles kept in the persistent profile store (CORE.TABLE_PROFILES) for tables the catalog has not seen yet:
# cached profiles are reused without a new pass, so they never reach CORE.DATA_PROFILES.
LEGACY_PROFILES = """
SELECT COUNT(*) AS TABLES, SUM(COALESCE(T.COLUMN_COUNT, 0)) AS COLUMNS, SUM(COALESCE(T.PII_COLUMNS, 0)) AS PII
  FROM CORE.TABLE_PROFILES T {join}
 WHERE T.SOURCE_FINGERPRINT IS NOT NULL AND COALESCE(T.STATUS, '') <> 'FAILED' AND NOT STARTSWITH(T.SOURCE_NAME, 'ORACLE_') {exclude}"""
CATALOG_TABLES = """UPPER(T.DATABASE_NAME || '.' || T.SCHEMA_NAME || '.' || T.TABLE_NAME) NOT IN (
    SELECT DISTINCT UPPER(DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TABLE_NAME) FROM CORE.DATA_PROFILES)"""

RUN_DESIGNS = """
WITH D AS (
    SELECT M.RUN_ID, M.STATUS, ROW_NUMBER() OVER (PARTITION BY M.RUN_ID ORDER BY M.VERSION DESC) AS RN
      FROM CORE.MODEL_DESIGN M {join}
)
SELECT COUNT_IF(RN = 1 AND STATUS IN ('APPROVED', 'DRAFT')) AS DESIGNS, COUNT_IF(RN = 1 AND STATUS = 'DRAFT') AS IN_DESIGN FROM D"""

RUN_PUBLICATIONS = """
WITH P AS (
    SELECT G.RUN_ID, G.STATUS, G.HEAD_BRANCH,
           ROW_NUMBER() OVER (PARTITION BY G.RUN_ID ORDER BY G.CREATED_AT DESC) AS RN
      FROM CORE.GIT_PUBLICATION G {join}
)
SELECT COUNT(DISTINCT IFF(STATUS = 'PUBLISHED', RUN_ID, NULL)) AS PUBLISHED,
       COUNT(DISTINCT IFF(STATUS = 'PUBLISHED', HEAD_BRANCH, NULL)) AS BRANCHES,
       COUNT_IF(RN = 1 AND STATUS = 'FAILED') AS FAILED FROM P"""

RUN_CHECKS = """
WITH C AS (
    SELECT EXPECTATION_ID, TARGET_TABLE FROM CORE.SODA_EXPECTATION_REGISTRY
     WHERE IS_CURRENT AND STATUS = 'APPROVED' {domain}
), R AS (
    SELECT EXPECTATION_ID, OUTCOME FROM CORE.CHECK_RESULT
     WHERE EXPECTATION_ID IN (SELECT EXPECTATION_ID FROM C)
   QUALIFY ROW_NUMBER() OVER (PARTITION BY EXPECTATION_ID ORDER BY CREATED_AT DESC) = 1
)
SELECT (SELECT COUNT(*) FROM C) AS CHECKS, (SELECT COUNT(DISTINCT UPPER(TARGET_TABLE)) FROM C) AS TABLES,
       COUNT_IF(OUTCOME = 'PASS') AS PASSED, COUNT_IF(OUTCOME = 'WARN') AS WARNED,
       COUNT_IF(OUTCOME IN ('FAIL', 'ERROR')) AS FAILED, COUNT_IF(OUTCOME <> 'NOT_EVALUATED') AS EVALUATED FROM R"""

RUN_TESTS = """
SELECT COUNT(*) AS TESTS FROM CORE.QA_TEST_CASE T
 WHERE NOT COALESCE(T.IS_DELETED, FALSE)
   AND (T.RUN_ID IS NULL OR T.RUN_ID IN (SELECT R.RUN_ID FROM CORE.WORKFLOW_RUN R WHERE {population})) {domain}"""

# every QA result: saved tests (not deleted) and the generated suites' tests (no saved case), in runs of the population
# or on tables (no run). Generated test ids repeat across runs, so a test is (RUN_ID, TEST_ID).
QA_RESULTS = """
    SELECT Q.RUN_ID, Q.TEST_ID, Q.OUTCOME, Q.CREATED_AT, T.TARGET_FQN
      FROM CORE.QA_RESULT Q
      LEFT JOIN CORE.QA_TEST_CASE T ON T.TEST_ID = Q.TEST_ID
      LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = Q.RUN_ID
     WHERE NOT COALESCE(T.IS_DELETED, FALSE) AND (R.RUN_ID IS NULL OR ({population})) {domain}"""

# each test's latest result; the pass rate is passed over executed (PASS, FAIL, ERROR), never NOT_RUN or REVIEW
RUN_QA = """
WITH B AS ({results}), L AS (
    SELECT * FROM B QUALIFY ROW_NUMBER() OVER (PARTITION BY RUN_ID, TEST_ID ORDER BY CREATED_AT DESC) = 1
)
SELECT COUNT_IF(OUTCOME IN ('PASS', 'FAIL', 'ERROR')) AS EXECUTED, COUNT_IF(OUTCOME = 'PASS') AS PASSED,
       COUNT_IF(OUTCOME IN ('FAIL', 'ERROR')) AS FAILED,
       COUNT(DISTINCT IFF(OUTCOME IN ('FAIL', 'ERROR'), COALESCE(TARGET_FQN, RUN_ID), NULL)) AS FAILED_TARGETS,
       (SELECT COUNT(*) FROM B WHERE CREATED_AT >= DATEADD('day', -7, CURRENT_TIMESTAMP())
           AND OUTCOME IN ('PASS', 'FAIL', 'ERROR')) AS RUN_7D
  FROM L"""

STTM = """
WITH L AS (
    SELECT C.CATALOG_ID, C.STATUS, S.STATUS AS RUN_STATUS,
           ROW_NUMBER() OVER (PARTITION BY C.CATALOG_ID ORDER BY C.VERSION DESC) AS RN
      FROM CORE.STTM_CATALOG C LEFT JOIN CORE.STTM_REGISTRY S ON S.STTM_ID = C.STTM_ID
     WHERE TRUE {domain}
), E AS (
    SELECT IFF(STATUS IN ('DRAFT', 'REVIEW') AND RUN_STATUS IN ('APPROVED', 'REJECTED'), RUN_STATUS, STATUS) AS STATUS
      FROM L WHERE RN = 1
)
SELECT COUNT(*) AS MAPPINGS, COUNT_IF(STATUS = 'APPROVED') AS APPROVED, COUNT_IF(STATUS = 'REVIEW') AS PENDING,
       COUNT_IF(STATUS = 'DRAFT') AS DRAFTS FROM E"""

DOMAIN_MODELS = """
SELECT COUNT(*) AS MODELS, COUNT_IF(D.APPROVED_VERSION IS NOT NULL) AS APPROVED,
       COUNT_IF(A.STATUS = 'PUBLISHED') AS PUBLISHED,
       COUNT_IF(O.STATUS = 'DRAFT') AS DRAFTS, COUNT_IF(O.STATUS = 'IN_REVIEW') AS IN_REVIEW
  FROM CORE.DOMAIN_MODEL D
  LEFT JOIN CORE.MODEL_DESIGNS A ON A.DESIGN_ID = D.MODEL_ID AND A.VERSION = D.APPROVED_VERSION
  LEFT JOIN CORE.MODEL_DESIGNS O ON O.DESIGN_ID = D.MODEL_ID AND O.VERSION = D.OPEN_VERSION
 WHERE COALESCE(D.LAYER, 'SILVER') = 'SILVER' {domain}"""

TABLE_MODELS = """
SELECT COUNT(*) AS TABLES, COUNT_IF(P.TABLE_MODEL_ID IS NOT NULL) AS PUBLISHED
  FROM CORE.TABLE_MODEL T
  LEFT JOIN (SELECT DISTINCT TABLE_MODEL_ID FROM CORE.TABLE_MODEL_VERSION WHERE STATUS = 'PUBLISHED') P
         ON P.TABLE_MODEL_ID = T.TABLE_MODEL_ID
 WHERE TRUE {domain}"""

# pull requests of model versions: every one still open, and assets whose latest attempt failed
MODEL_PRS = """
WITH G AS (
    SELECT STATE, ROW_NUMBER() OVER (PARTITION BY KIND, ASSET_ID ORDER BY VERSION DESC, UPDATED_AT DESC) AS RN
      FROM CORE.GIT_PR WHERE KIND IN ('MODEL', 'TABLE_MODEL') {domain}
)
SELECT COUNT_IF(STATE IN ('OPEN', 'DRAFT')) AS OPEN, COUNT_IF(RN = 1 AND STATE = 'FAILED') AS FAILED FROM G"""

# designs from before the Model Workbench (read-only)
LEGACY_MODELS = """
SELECT COUNT(DISTINCT DESIGN_ID) AS DESIGNS FROM CORE.MODEL_DESIGNS WHERE MODEL_ID IS NULL {domain}"""

def _soft(fn: Callable[[], Any]) -> Any:
    """A block's value, or None when its tables are missing or unreadable (HTTP errors from a store included)."""
    try:
        return fn()
    except Exception:
        return None


def _resolve(db: Db, domain: Optional[str]) -> Optional[dict]:
    if not domain:
        return None
    found = db.query("""SELECT DOMAIN_ID, DOMAIN_NAME FROM CORE.DOMAIN_REGISTRY
                         WHERE DOMAIN_ID = %s OR UPPER(DOMAIN_NAME) = UPPER(%s) LIMIT 1""", (domain, domain))
    if not found:
        raise HTTPException(404, f"domain {domain} not found")
    return found[0]


def _n(row: Optional[dict], key: str) -> int:
    return int((row or {}).get(key) or 0)


def sources(db: Db, domain_id: Optional[str]) -> dict:
    clause, params = ("AND DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    by_type = {r["source_type"]: int(r["n"]) for r in db.query(
        f"SELECT SOURCE_TYPE, COUNT(*) AS N FROM CORE.SOURCE_REGISTRY WHERE ACTIVE_FLAG {clause} GROUP BY 1", params)}
    try:
        ext = db.query(f"""SELECT COUNT(*) AS N, COUNT_IF(STATUS = 'ACTIVE') AS ACTIVE
                             FROM CORE.INGESTION_PIPELINE_CATALOG WHERE STATUS <> 'RETIRED' {clause}""", params)[0]
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
    row = db.query(STTM.format(domain="AND C.DOMAIN_ID = %s" if domain_id else ""), (domain_id,) if domain_id else ())[0]
    return {"mappings": _n(row, "mappings"), "approved": _n(row, "approved"), "pending": _n(row, "pending"),
            "drafts": _n(row, "drafts")}


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
    """The Model Workbench's domain models, the Table Model Store's table models and their pull requests; the run
    model designs and legacy hub designs only as counts on the side."""
    clause, params = ("AND D.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    domain = _first(db, DOMAIN_MODELS.format(domain=clause), params)
    tables = _first(db, TABLE_MODELS.format(domain=clause.replace("D.", "T.")), params)
    prs = _first(db, MODEL_PRS.format(domain=clause.replace("D.", "")), params)
    if not (domain or tables):
        raise LookupError("no model store is readable")
    legacy_clause = "AND (UPPER(DOMAIN_NAME) = UPPER(%s) OR UPPER(PARENT_DOMAIN) = UPPER(%s))" if domain_name else ""
    legacy = _first(db, LEGACY_MODELS.format(domain=legacy_clause), (domain_name, domain_name) if domain_name else ())
    join, run_params = _run_join("M", domain_id)
    runs = _first(db, RUN_DESIGNS.format(join=join), run_params)
    join, run_params = _run_join("G", domain_id)
    pubs = _first(db, RUN_PUBLICATIONS.format(join=join), run_params)
    return {"domain_models": _n(domain, "models"), "approved": _n(domain, "approved"), "published": _n(domain, "published"),
            "drafts": _n(domain, "drafts"), "in_review": _n(domain, "in_review"),
            "table_models": _n(tables, "tables"), "tables_published": _n(tables, "published"),
            "prs_open": _n(prs, "open"), "failed": _n(prs, "failed") + _n(pubs, "failed"),
            "run_designs": _n(runs, "designs"), "legacy_designs": _n(legacy, "designs")}


def qa(db: Db, domain_id: Optional[str]) -> Optional[dict]:
    """Domain packs (QA Store) plus the tests saved in runs and tables: how many, how many ran in the last 7 days and
    the pass rate of each test's latest result (passed over executed)."""
    from app.qa_api import _call, _svc

    try:
        packs = _call(db, _svc("packs").summary, domain_id)
    except Exception:
        packs = None
    clause = "AND (R.DOMAIN_ID = %s OR T.DOMAIN_ID = %s)" if domain_id else ""
    saved_clause = ("AND (T.DOMAIN_ID = %s OR T.RUN_ID IN (SELECT RUN_ID FROM CORE.WORKFLOW_RUN WHERE DOMAIN_ID = %s))"
                    if domain_id else "")
    saved = _first(db, RUN_TESTS.format(population=RUN_POPULATION, domain=saved_clause),
                   (domain_id, domain_id) if domain_id else ())
    runs = _first(db, RUN_QA.format(results=QA_RESULTS.format(population=RUN_POPULATION, domain=clause)),
                  (domain_id,) * 2 if domain_id else ())
    if packs is None and not saved and not runs:
        raise LookupError("no QA store is readable")
    out = dict(packs or {"packs": 0, "approved_packs": 0, "rules": 0, "pending": 0, "domains": 0, "top_tags": [],
                         "pass_rate": None})
    executed, passed = _n(runs, "executed"), _n(runs, "passed")
    out.update({"saved_tests": _n(saved, "tests"), "tests_7d": _n(runs, "run_7d"), "executed_tests": executed,
                "failed_tests": _n(runs, "failed"), "failed_targets": _n(runs, "failed_targets"),
                "test_pass_rate": rate(passed, executed)})
    return out


def rate(part: int, whole: int) -> Optional[float]:
    """part over whole as a percentage with one decimal, None when there is nothing to divide by. Pure."""
    return round(100 * part / whole, 1) if whole else None


DQ_COVERAGE = """
WITH T AS (
    SELECT DISTINCT UPPER(TARGET_TABLE) AS NAME FROM CORE.TARGET_TABLE_REGISTRY
     WHERE COALESCE(ACTIVE_FLAG, TRUE) {domain}
), C AS (
    SELECT UPPER(SPLIT_PART(TARGET_TABLE, '.', -1)) AS NAME FROM CORE.SODA_EXPECTATION_REGISTRY
     WHERE IS_CURRENT AND STATUS = 'APPROVED' {domain}
    UNION
    SELECT UPPER(SPLIT_PART(TABLE_NAME, '.', -1)) FROM CORE.DQ_CHECKS_CATALOG WHERE STATUS = 'ACTIVE' {domain}
)
SELECT COUNT(*) AS TARGETS, COUNT_IF(NAME IN (SELECT NAME FROM C)) AS COVERED FROM T"""


def dq(db: Db, domain_id: Optional[str]) -> Optional[dict]:
    """Approved checks in runs and the Data Quality Store's own checks (kept apart), the pass rate of each check's
    latest result, and coverage: registered target tables with at least one check."""
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
    cover = _first(db, DQ_COVERAGE.format(domain="AND DOMAIN_ID = %s" if domain_id else ""), (domain_id,) * 3 if domain_id else ())
    return {"deployed": True, "checks": checks, "run_checks": _n(runs, "checks"), "store_checks": s.get("checks", 0),
            "tables": tables, "passed": passed, "warned": warned, "failed": failed,
            "never_run": max(0, checks - evaluated), "scheduled_tables": s.get("scheduled_tables", 0),
            "pass_rate": rate(passed, evaluated), "targets": _n(cover, "targets"), "covered_targets": _n(cover, "covered"),
            "coverage": rate(_n(cover, "covered"), _n(cover, "targets"))}


# ---------------------------------------------------------------- developer stats (the hero ribbon)

RUNS = """
SELECT COUNT_IF({in_flight}) AS IN_FLIGHT, COUNT_IF(R.CURRENT_STATE = 'CREATED') AS DRAFTS,
       COUNT_IF({review}) AS NEEDS_REVIEW
  FROM CORE.WORKFLOW_RUN R WHERE {population} {domain}"""


def runs(db: Db, domain_id: Optional[str]) -> dict:
    """Exact run counts over the run list's population (not TEST, archived or deleted): in flight (started, not
    finished), drafts, and at a review step. The greeting, the chips and the tiles all read these."""
    clause, params = ("AND R.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    row = db.query(RUNS.format(in_flight=IN_FLIGHT_SQL, review=REVIEW_SQL, population=RUN_POPULATION, domain=clause), params)[0]
    return {"in_flight": _n(row, "in_flight"), "drafts": _n(row, "drafts"), "needs_review": _n(row, "needs_review")}


# each run's latest outcome in the last 7 days: a run that failed and then completed counts once, as completed
OUTCOMES = """
SELECT E.RUN_ID, E.TO_STATE, E.CREATED_AT, R.CREATED_AT AS STARTED_AT
  FROM CORE.WORKFLOW_EVENT E JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = E.RUN_ID
 WHERE E.CREATED_AT >= DATEADD('day', -7, CURRENT_TIMESTAMP()) AND E.TO_STATE IN ('COMPLETED', 'FAILED')
   AND {population} {domain}
QUALIFY ROW_NUMBER() OVER (PARTITION BY E.RUN_ID ORDER BY E.CREATED_AT DESC) = 1"""

DELIVERY = """
WITH O AS ({outcomes})
SELECT COUNT_IF(TO_STATE = 'COMPLETED') AS COMPLETED, COUNT_IF(TO_STATE = 'FAILED') AS FAILED,
       MEDIAN(IFF(TO_STATE = 'COMPLETED', DATEDIFF('minute', STARTED_AT, CREATED_AT), NULL)) AS LEAD_MINUTES,
       (SELECT COUNT(*) FROM CORE.WORKFLOW_RUN R WHERE {population} AND {in_flight} {domain}) AS ACTIVE,
       (SELECT COUNT(*) FROM CORE.WORKFLOW_RUN R WHERE {population}
           AND R.CREATED_AT >= DATEADD('day', -7, CURRENT_TIMESTAMP()) {domain}) AS STARTED
  FROM O"""

DELIVERY_DAILY = """
WITH O AS ({outcomes})
SELECT TO_VARCHAR(CREATED_AT::DATE) AS DAY, COUNT_IF(TO_STATE = 'COMPLETED') AS COMPLETED, COUNT_IF(TO_STATE = 'FAILED') AS FAILED
  FROM O GROUP BY 1 ORDER BY 1"""


def fill_days(rows: List[Dict[str, Any]], end: date, days: int = 7) -> List[Dict[str, Any]]:
    """One entry per day ending at `end` (or the latest day in `rows`, if later), days without outcomes as zeros, so
    the sparkline's spacing is real time. Pure."""
    by_day = {str(r["day"]): r for r in rows}
    last = max([end] + [date.fromisoformat(d) for d in by_day])
    out = []
    for back in range(days - 1, -1, -1):
        day = (last - timedelta(days=back)).isoformat()
        r = by_day.get(day) or {}
        out.append({"day": day, "completed": int(r.get("completed") or 0), "failed": int(r.get("failed") or 0)})
    return out


def delivery(db: Db, domain_id: Optional[str]) -> dict:
    """Runs over the last 7 days by their latest outcome: completed, failed, success rate and median lead time (start
    to completion), with the runs in flight now and a day-by-day series (every day present)."""
    clause, params = ("AND R.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    outcomes = OUTCOMES.format(population=RUN_POPULATION, domain=clause)
    row = db.query(DELIVERY.format(outcomes=outcomes, population=RUN_POPULATION, in_flight=IN_FLIGHT_SQL, domain=clause),
                   params * 3)[0]
    daily = db.query(DELIVERY_DAILY.format(outcomes=outcomes), params)
    done, failed = _n(row, "completed"), _n(row, "failed")
    lead = row.get("lead_minutes")
    return {"completed": done, "failed": failed, "started": _n(row, "started"), "active": _n(row, "active"),
            "success_rate": rate(done, done + failed),
            "lead_minutes": int(lead) if lead is not None else None,
            "daily": fill_days(daily, date.today())}


WAITING = """
SELECT COUNT(*) AS RUNS, MAX(DATEDIFF('minute', COALESCE(W.AT, R.UPDATED_AT, R.CREATED_AT), CURRENT_TIMESTAMP())) AS OLDEST_MINUTES
  FROM CORE.WORKFLOW_RUN R
  LEFT JOIN (SELECT RUN_ID, TO_STATE, MAX(CREATED_AT) AS AT FROM CORE.WORKFLOW_EVENT GROUP BY 1, 2) W
         ON W.RUN_ID = R.RUN_ID AND W.TO_STATE = R.CURRENT_STATE
 WHERE {population} AND {review} {domain}"""

# versions waiting on a reviewer, each with how long (minutes) since it was submitted
WAITING_VERSIONS = (
    "SELECT COUNT(*) AS N, MAX(DATEDIFF('minute', COALESCE(SUBMITTED_AT, CREATED_AT), CURRENT_TIMESTAMP())) AS OLDEST "
    "FROM CORE.MODEL_DESIGNS WHERE MODEL_ID IS NOT NULL AND STATUS = 'IN_REVIEW'{domain}",
    "SELECT COUNT(*) AS N, MAX(DATEDIFF('minute', COALESCE(V.SUBMITTED_AT, V.CREATED_AT), CURRENT_TIMESTAMP())) AS OLDEST "
    "FROM CORE.DQ_SUITE_VERSION V JOIN CORE.DQ_SUITE S ON S.SUITE_ID = V.SUITE_ID WHERE V.STATUS = 'IN_REVIEW'{s_domain}",
    # a pack counts once, however many of its versions are pending
    "SELECT COUNT(DISTINCT PACK_ID) AS N, MAX(DATEDIFF('minute', COALESCE(SUBMITTED_AT, CREATED_AT), CURRENT_TIMESTAMP())) AS OLDEST "
    "FROM CORE.DOMAIN_QA_PACKS WHERE STATUS = 'PENDING_APPROVAL'{domain}",
)


def waiting(db: Db, domain_id: Optional[str]) -> dict:
    """What is waiting on a person: runs at a review step, change requests in Approvals, and versions in review
    (domain models, data quality suites, QA packs), with the oldest wait of any of them."""
    clause, params = ("AND R.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    runs_row = db.query(WAITING.format(population=RUN_POPULATION, review=REVIEW_SQL, domain=clause), params)[0]
    requests = _soft(lambda: db.query("""SELECT COUNT(*) AS N, MAX(DATEDIFF('minute', CREATED_AT, CURRENT_TIMESTAMP())) AS OLDEST
                                           FROM CORE.CHANGE_REQUEST WHERE STATUS = 'PENDING'""")[0]) or {}
    reviews, oldest = 0, [runs_row.get("oldest_minutes"), requests.get("oldest")]
    for sql in WAITING_VERSIONS:
        q = sql.format(domain=" AND DOMAIN_ID = %s" if domain_id else "", s_domain=" AND S.DOMAIN_ID = %s" if domain_id else "")
        row = _soft(lambda: db.query(q, params)[0]) or {}
        reviews += _n(row, "n")
        oldest.append(row.get("oldest") if _n(row, "n") else None)
    known = [v for v in oldest if v is not None]
    return {"runs": _n(runs_row, "runs"), "requests": _n(requests, "n"), "versions": reviews,
            "total": _n(runs_row, "runs") + _n(requests, "n") + reviews,
            "oldest_minutes": int(max(known)) if known else None}


FRESHNESS = """
WITH L AS (
    SELECT L.LANDING_DATABASE, L.LANDING_SCHEMA, L.LANDING_TABLE, MAX(L.INGESTED_AT) AS LAST_LOAD
      FROM CORE.LANDING_TABLE_REGISTRY L LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = L.RUN_ID
     WHERE L.INGESTION_STATUS = 'COMPLETE' AND COALESCE(L.INGESTION_METHOD, '') <> 'IN_PLACE' {domain}
     GROUP BY 1, 2, 3
), F AS (
    SELECT L.INGESTION_STATUS, L.CREATED_AT
      FROM CORE.LANDING_TABLE_REGISTRY L LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = L.RUN_ID
     WHERE L.INGESTION_STATUS IN ('COMPLETE', 'FAILED') AND COALESCE(L.INGESTION_METHOD, '') <> 'IN_PLACE' {domain}
   QUALIFY ROW_NUMBER() OVER (PARTITION BY L.LANDING_DATABASE, L.LANDING_SCHEMA, L.LANDING_TABLE ORDER BY L.CREATED_AT DESC) = 1
)
SELECT (SELECT COUNT(*) FROM L) AS TABLES,
       (SELECT COUNT_IF(LAST_LOAD >= DATEADD('hour', -24, CURRENT_TIMESTAMP())) FROM L) AS FRESH,
       (SELECT COUNT_IF(LAST_LOAD < DATEADD('day', -7, CURRENT_TIMESTAMP())) FROM L) AS STALE,
       (SELECT MAX(DATEDIFF('hour', LAST_LOAD, CURRENT_TIMESTAMP())) FROM L) AS OLDEST_HOURS,
       (SELECT COUNT_IF(INGESTION_STATUS = 'FAILED' AND CREATED_AT >= DATEADD('day', -7, CURRENT_TIMESTAMP())) FROM F) AS FAILED_LOADS"""


def freshness(db: Db, domain_id: Optional[str]) -> dict:
    """Landed tables (copies, not tables profiled in place): loaded in the last 24 hours, between 1 and 7 days ago and
    not for over 7 days (the three add up), the oldest load, tables whose latest load failed this week, and external
    pipelines registered but not active."""
    clause, params = ("AND R.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    row = db.query(FRESHNESS.format(domain=clause), params * 2)[0]
    ext = _soft(lambda: db.query("SELECT COUNT_IF(STATUS IN ('REGISTERED', 'PAUSED')) AS IDLE FROM CORE.INGESTION_PIPELINE_CATALOG "
                                 "WHERE STATUS <> 'RETIRED'" + (" AND DOMAIN_ID = %s" if domain_id else ""), params)[0]) or {}
    hours = row.get("oldest_hours")
    tables, fresh, stale = _n(row, "tables"), _n(row, "fresh"), _n(row, "stale")
    return {"tables": tables, "fresh": fresh, "aging": max(0, tables - fresh - stale), "stale": stale,
            "failed_loads": _n(row, "failed_loads"), "idle_pipelines": _n(ext, "idle"),
            "oldest_hours": int(hours) if hours is not None else None}


# tables with a failing data quality check (FAIL or ERROR), in the store or approved in a run, each counted once
FAILING_DQ_TABLES = """
WITH S AS (
    SELECT UPPER(SPLIT_PART(TABLE_NAME, '.', -1)) AS NAME FROM CORE.DQ_CHECKS_CATALOG
     WHERE STATUS = 'ACTIVE' AND LAST_RUN_STATUS IN ('FAIL', 'ERROR') {domain}
), C AS (
    SELECT EXPECTATION_ID, TARGET_TABLE FROM CORE.SODA_EXPECTATION_REGISTRY WHERE IS_CURRENT AND STATUS = 'APPROVED' {domain}
), R AS (
    SELECT EXPECTATION_ID, OUTCOME FROM CORE.CHECK_RESULT WHERE EXPECTATION_ID IN (SELECT EXPECTATION_ID FROM C)
   QUALIFY ROW_NUMBER() OVER (PARTITION BY EXPECTATION_ID ORDER BY CREATED_AT DESC) = 1
)
SELECT COUNT(DISTINCT NAME) AS N FROM (
    SELECT NAME FROM S
    UNION ALL
    SELECT UPPER(SPLIT_PART(C.TARGET_TABLE, '.', -1)) FROM C JOIN R ON R.EXPECTATION_ID = C.EXPECTATION_ID
     WHERE R.OUTCOME IN ('FAIL', 'ERROR'))"""


def failing(db: Db, domain_id: Optional[str], dq_block: Optional[dict], qa_block: Optional[dict]) -> dict:
    """Checks failing now (FAIL or ERROR): data quality checks whose latest result failed, QA tests whose latest
    execution failed (the QA block's own count, so the tile and the QA card agree), and the tables they cover."""
    dq_tables = _soft(lambda: db.query(FAILING_DQ_TABLES.format(domain="AND DOMAIN_ID = %s" if domain_id else ""),
                                       (domain_id,) * 2 if domain_id else ())[0]) or {}
    dq_failed, qa_failed = _n(dq_block, "failed"), _n(qa_block, "failed_tests")
    return {"dq_checks": dq_failed, "qa_tests": qa_failed, "total": dq_failed + qa_failed,
            "tables": _n(dq_tables, "n") + _n(qa_block, "failed_targets")}


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
        "dq": lambda: dq(db, did),
        "runs": lambda: runs(db, did),
        "delivery": lambda: delivery(db, did),
        "waiting": lambda: waiting(db, did),
        "freshness": lambda: freshness(db, did),
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        # each job runs in a copy of this request's context, so its statements count in the request's timing
        futures = {k: pool.submit(contextvars.copy_context().run, _soft, fn) for k, fn in jobs.items()}
        blocks = {k: f.result() for k, f in futures.items()}
    blocks["failing"] = _soft(lambda: failing(db, did, blocks["dq"], blocks["qa"]))
    if blocks["runs"] is not None:  # failed this week: the same latest-outcome count as the Runs tile
        blocks["runs"]["failed_7d"] = (blocks["delivery"] or {}).get("failed")
    return {"domain": scope, **{k: blocks[k] for k in ("sources", "profiling", "models", "sttm", "qa", "dq", "runs",
                                                       "delivery", "waiting", "freshness", "failing")}}
