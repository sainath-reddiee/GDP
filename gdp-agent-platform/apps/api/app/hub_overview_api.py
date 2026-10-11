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

INGESTED = """
WITH L AS (
    SELECT L.RUN_ID, L.SOURCE_DATABASE, L.SOURCE_SCHEMA, L.SOURCE_TABLE, L.ROW_COUNT, L.INGESTED_AT,
           ROW_NUMBER() OVER (PARTITION BY L.LANDING_DATABASE, L.LANDING_SCHEMA, L.LANDING_TABLE
                              ORDER BY L.INGESTED_AT DESC NULLS LAST) AS RN
      FROM SOURCE.LANDING_TABLE_REGISTRY L
      LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = L.RUN_ID
     WHERE L.INGESTION_STATUS = 'COMPLETE' {domain}
), O AS (
    SELECT RUN_ID, SOURCE_DATABASE, SOURCE_SCHEMA, OBJECT_NAME, MAX(BYTES) AS BYTES
      FROM SOURCE.SOURCE_OBJECT GROUP BY 1, 2, 3, 4
)
SELECT TO_VARCHAR(L.INGESTED_AT::DATE) AS DAY, COUNT(*) AS TABLES, SUM(L.ROW_COUNT) AS ROWS_LANDED, SUM(O.BYTES) AS BYTES
  FROM L LEFT JOIN O ON O.RUN_ID = L.RUN_ID AND O.SOURCE_DATABASE = L.SOURCE_DATABASE
                    AND O.SOURCE_SCHEMA = L.SOURCE_SCHEMA AND O.OBJECT_NAME = L.SOURCE_TABLE
 WHERE L.RN = 1
 GROUP BY 1 ORDER BY 1"""

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


def ingested(db: Db, domain_id: Optional[str]) -> dict:
    days = db.query(INGESTED.format(domain="AND R.DOMAIN_ID = %s" if domain_id else ""), (domain_id,) if domain_id else ())
    total = sum(int(d.get("bytes") or 0) for d in days)
    return {"bytes": total, "gb": round(total / 1024 ** 3, 2), "tables": sum(int(d.get("tables") or 0) for d in days),
            "rows": sum(int(d.get("rows_landed") or 0) for d in days),
            "last_day": days[-1]["day"] if days else None,
            "daily": [{"day": d["day"], "gb": round(int(d.get("bytes") or 0) / 1024 ** 3, 3), "tables": int(d.get("tables") or 0)}
                      for d in days[-14:]]}


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
    row = db.query(PROFILES.format(domain="AND DOMAIN_ID = %s" if domain_id else ""), (domain_id,) if domain_id else ())[0]
    columns = _n(row, "columns")
    return {"tables": _n(row, "tables"), "columns": columns, "high_cardinality": _n(row, "high_cardinality"),
            "pii": _n(row, "pii"), "drift": _n(row, "drift"), "drift_tables": _n(row, "drift_tables"),
            "health": round(100 * _n(row, "healthy") / columns, 1) if columns else None}


def sttm(db: Db, domain_id: Optional[str]) -> dict:
    row = db.query(STTM.format(domain="AND DOMAIN_ID = %s" if domain_id else ""), (domain_id,) if domain_id else ())[0]
    return {"mappings": _n(row, "mappings"), "verified": _n(row, "verified"), "pending": _n(row, "pending")}


def models(db: Db, domain_name: Optional[str]) -> dict:
    clause = "AND (UPPER(DOMAIN_NAME) = UPPER(%s) OR UPPER(PARENT_DOMAIN) = UPPER(%s))" if domain_name else ""
    row = db.query(MODELS.format(domain=clause), (domain_name, domain_name) if domain_name else ())[0]
    return {"designs": _n(row, "designs"), "published": _n(row, "published"), "branches": _n(row, "branches"),
            "in_design": _n(row, "in_design"), "failed": _n(row, "failed")}


def qa(db: Db, domain_id: Optional[str]) -> Optional[dict]:
    from app.qa_api import _call, _svc

    return _call(db, _svc("packs").summary, domain_id)


def dq(db: Db, domain_id: Optional[str]) -> Optional[dict]:
    from app.quality_api import summary

    out = summary(domain_id, db)
    return out if out.get("deployed") else None


@router.get("/api/hub/overview")
def overview(domain: Optional[str] = None, db: Db = Depends(current_db)):
    scope = _resolve(db, domain)
    did = scope["domain_id"] if scope else None
    name = scope["domain_name"] if scope else None
    blocks = {
        "ingested": _soft(lambda: ingested(db, did)),
        "sources": _soft(lambda: sources(db, did)),
        "profiling": _soft(lambda: profiling(db, did)),
        "models": _soft(lambda: models(db, name)),
        "sttm": _soft(lambda: sttm(db, did)),
        "qa": _soft(lambda: qa(db, did)),
        "dq": _soft(lambda: dq(db, did)),
    }
    return {"domain": scope, **blocks}
