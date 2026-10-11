"""Command Center overview (/dashboard): the hero ribbon and the six asset store cards in one call.

Every block fails soft: a block whose tables are not deployed (or that the caller's role cannot read) comes back as
null and the dashboard shows a dash, so one missing store never blanks the page. `domain` is a domain id or name.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.db import Db
from app.main import current_db

router = APIRouter()

LANDED = """
WITH L AS (
    SELECT L.RUN_ID, L.SOURCE_DATABASE, L.SOURCE_SCHEMA, L.SOURCE_TABLE, L.LANDING_DATABASE, L.LANDING_SCHEMA,
           L.LANDING_TABLE, L.INGESTION_METHOD, L.ROW_COUNT, L.INGESTED_AT,
           ROW_NUMBER() OVER (PARTITION BY L.LANDING_DATABASE, L.LANDING_SCHEMA, L.LANDING_TABLE
                              ORDER BY L.INGESTED_AT DESC NULLS LAST) AS RN
      FROM SOURCE.LANDING_TABLE_REGISTRY L
      LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = L.RUN_ID
     WHERE L.INGESTION_STATUS = 'COMPLETE' {domain}
), O AS (
    SELECT RUN_ID, SOURCE_DATABASE, SOURCE_SCHEMA, OBJECT_NAME, MAX(BYTES) AS BYTES
      FROM SOURCE.SOURCE_OBJECT GROUP BY 1, 2, 3, 4
)
SELECT TO_VARCHAR(L.INGESTED_AT::DATE) AS DAY, L.LANDING_DATABASE, L.LANDING_SCHEMA, L.LANDING_TABLE,
       L.INGESTION_METHOD, L.ROW_COUNT, O.BYTES AS SOURCE_BYTES
  FROM L LEFT JOIN O ON O.RUN_ID = L.RUN_ID AND O.SOURCE_DATABASE = L.SOURCE_DATABASE
                    AND O.SOURCE_SCHEMA = L.SOURCE_SCHEMA AND O.OBJECT_NAME = L.SOURCE_TABLE
 WHERE L.RN = 1"""

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


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _quoted(name: str) -> str:
    """A database name as a safe SQL identifier (plain names stay unquoted so they keep Snowflake's upper-casing)."""
    return name if _IDENT.match(name) else '"' + name.replace('"', '""') + '"'


def _landed_bytes(db: Db, rows: list) -> dict:
    """{(DB, SCHEMA, TABLE): bytes} read from each landing database's INFORMATION_SCHEMA. A database the role cannot
    read is skipped; its tables fall back to the source size."""
    by_db: dict = {}
    for r in rows:
        if r.get("landing_database") and r.get("landing_schema"):
            by_db.setdefault(str(r["landing_database"]).upper(), set()).add(str(r["landing_schema"]).upper())
    out: dict = {}
    for database, schemas in by_db.items():
        schemas = sorted(schemas)
        try:
            found = db.query(f"""SELECT TABLE_SCHEMA, TABLE_NAME, BYTES FROM {_quoted(database)}.INFORMATION_SCHEMA.TABLES
                                  WHERE TABLE_SCHEMA IN ({", ".join(["%s"] * len(schemas))})""", tuple(schemas))
        except Exception:
            continue
        for f in found:
            if f.get("bytes") is not None:
                out[(database, str(f["table_schema"]).upper(), str(f["table_name"]).upper())] = int(f["bytes"])
    return out


def ingested(db: Db, domain_id: Optional[str]) -> dict:
    """Landed tables (latest COMPLETE load per landing table) and their size: the landed table's own bytes where the
    caller can see the landing schema, else the source table's size at discovery. Tables profiled in place hold no
    copy and are counted apart."""
    rows = db.query(LANDED.format(domain="AND R.DOMAIN_ID = %s" if domain_id else ""), (domain_id,) if domain_id else ())
    copies = [r for r in rows if str(r.get("ingestion_method") or "").upper() != "IN_PLACE"]
    landed = _landed_bytes(db, copies)
    total, measured, days = 0, 0, {}
    for r in copies:
        key = (str(r.get("landing_database") or "").upper(), str(r.get("landing_schema") or "").upper(),
               str(r.get("landing_table") or "").upper())
        size = landed.get(key)
        if size is not None:
            measured += 1
        else:
            size = int(r.get("source_bytes") or 0)
        total += size
        day = days.setdefault(r.get("day") or "", {"day": r.get("day"), "bytes": 0, "tables": 0})
        day["bytes"] += size
        day["tables"] += 1
    series = [d for k, d in sorted(days.items()) if k][-14:]
    return {"bytes": total, "gb": round(total / 1024 ** 3, 3), "tables": len(copies),
            "rows": sum(int(r.get("row_count") or 0) for r in copies),
            "in_place": len(rows) - len(copies),
            "measured": "landed" if copies and measured == len(copies) else "source" if not measured else "mixed",
            "last_day": series[-1]["day"] if series else None,
            "daily": [{"day": d["day"], "bytes": d["bytes"], "gb": round(d["bytes"] / 1024 ** 3, 3), "tables": d["tables"]}
                      for d in series]}


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


@router.get("/api/hub/overview")
def overview(domain: Optional[str] = None, db: Db = Depends(current_db)):
    scope = _resolve(db, domain)
    did = scope["domain_id"] if scope else None
    name = scope["domain_name"] if scope else None
    blocks = {
        "ingested": _soft(lambda: ingested(db, did)),
        "sources": _soft(lambda: sources(db, did)),
        "profiling": _soft(lambda: profiling(db, did)),
        "models": _soft(lambda: models(db, did, name)),
        "sttm": _soft(lambda: sttm(db, did)),
        "qa": _soft(lambda: qa(db, did)),
    }
    blocks["dq"] = _soft(lambda: dq(db, did, (blocks["profiling"] or {}).get("tables", 0)))
    return {"domain": scope, **blocks}
