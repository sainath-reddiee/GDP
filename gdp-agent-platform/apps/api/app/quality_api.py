"""Data Quality Store (/dashboard/dq-store): Soda / Great Expectations checks per table and column, without a run.

The catalog (GOVERNANCE.DQ_CHECKS_CATALOG, V037) keeps each check in the platform's neutral form, which the scanner
runs, with the rendered SodaCL beside it. Checks are added from a one-line SodaCL string, the structured editor,
a table's profile or a run's approved checks; a table is scanned now or on a schedule (a Snowflake task that calls
QUALITY.RUN_TABLE_SCAN). The logic lives in services/catalog/dq.py; this router validates input and maps errors.
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import DATABASE, WAREHOUSE, Db
from app.main import _json, _snowflake_error, current_db
from app.source_invoke import invoke_source
from services.catalog import dq

router = APIRouter()
CATALOG_FIELDS = """CHECK_ID, TABLE_NAME, COLUMN_NAME, CHECK_TYPE, CHECK_DEFINITION, SODA_DSL, SEVERITY, STATUS, ORIGIN,
                    DOMAIN_ID, SCHEDULE_CRON, LAST_RUN_STATUS, LAST_RUN_AT::VARCHAR AS LAST_RUN_AT, LAST_MEASURED,
                    CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT, UPDATED_AT::VARCHAR AS UPDATED_AT"""


def _call(db: Db, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return invoke_source(db, partial(fn, **kwargs) if kwargs else fn, *args)
    except HTTPException:
        raise
    except AssertionError as exc:
        message = str(exc) or "request rejected"
        raise HTTPException(404 if "not found" in message.lower() else 400, message) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


def _domain_filter(db: Db, domain: Optional[str]) -> Optional[str]:
    if not domain:
        return None
    if len(domain) == 36 and domain.count("-") == 4:
        return domain
    found = db.query("SELECT DOMAIN_ID FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = UPPER(%s) LIMIT 1", (domain,))
    if not found:
        raise HTTPException(404, f"domain {domain} not found")
    return found[0]["domain_id"]


def _row(r: dict) -> dict:
    r["check_definition"] = _json(r.get("check_definition")) or {}
    r["headline"] = dq.assertion({"target_column": r.get("column_name"), "severity": r.get("severity"),
                                  "definition": r["check_definition"], "soda_dsl": r.get("soda_dsl")})
    return r


@router.get("/api/dq/catalog")
def catalog(domain: Optional[str] = None, table: Optional[str] = None, column: Optional[str] = None,
            status: Optional[str] = None, q: Optional[str] = None, db: Db = Depends(current_db)):
    where, params = [], []
    domain_id = _domain_filter(db, domain)
    if domain_id:
        where.append("DOMAIN_ID = %s")
        params.append(domain_id)
    if table:
        where.append("TABLE_NAME ILIKE %s")
        params.append(f"%{table}%")
    if column:
        where.append("COLUMN_NAME ILIKE %s")
        params.append(f"%{column}%")
    if status:
        where.append("STATUS = %s")
        params.append(status.upper())
    else:
        where.append("STATUS <> 'RETIRED'")
    if q:
        where.append("(TABLE_NAME ILIKE %s OR COLUMN_NAME ILIKE %s OR SODA_DSL ILIKE %s)")
        params += [f"%{q}%"] * 3
    try:
        found = db.query(f"SELECT {CATALOG_FIELDS} FROM GOVERNANCE.DQ_CHECKS_CATALOG WHERE {' AND '.join(where)} "
                         "ORDER BY TABLE_NAME, COLUMN_NAME NULLS FIRST, CHECK_TYPE LIMIT 5000", tuple(params))
    except Exception:
        return {"tables": [], "deployed": False}
    rows = [_row(r) for r in found]
    try:
        schedules = {r["table_name"]: r for r in db.query(
            "SELECT TABLE_NAME, CRON, TASK_NAME, ENABLED FROM GOVERNANCE.DQ_SCHEDULE")}
    except Exception:
        schedules = {}
    tree = dq.build_tree(rows)
    for t in tree:
        s = schedules.get(t["table"])
        t["schedule"] = {"cron": s["cron"], "enabled": bool(s.get("enabled"))} if s else None
    return {"tables": tree, "deployed": True, "checks": len(rows)}


class CheckIn(BaseModel):
    check_id: Optional[str] = Field(default=None, max_length=64)
    table: str = Field(min_length=5, max_length=800)
    column: Optional[str] = Field(default=None, max_length=256)
    severity: Literal["WARN", "FAIL"] = "WARN"
    requirement: Optional[str] = Field(default=None, max_length=4000)
    definition: Optional[dict] = None
    dsl: Optional[str] = Field(default=None, max_length=2000)
    domain_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/dq/parse")
def parse(body: CheckIn):
    """Preview a one-line SodaCL check or a definition: the neutral form, the rendered SodaCL, or the problems."""
    check = {"target_column": body.column, "severity": body.severity, "requirement": body.requirement,
             "definition": body.definition}
    if body.dsl and not body.definition:
        parsed, problems = dq.parse_dsl(body.dsl)
        if problems:
            return {"ok": False, "problems": problems}
        check.update({k: v for k, v in parsed.items() if v is not None})
    cleaned, problems = dq.normalize(check)
    if problems:
        return {"ok": False, "problems": problems}
    return {"ok": True, "check": cleaned, "headline": cleaned["headline"]}


@router.post("/api/dq/catalog")
def save_check(body: CheckIn, db: Db = Depends(current_db)):
    if not body.definition and not body.dsl:
        raise HTTPException(400, "send a definition or a one-line SodaCL check")
    return _call(db, dq.upsert, json.dumps(body.model_dump()))


class StatusIn(BaseModel):
    status: Literal["ACTIVE", "PAUSED", "RETIRED"]


@router.post("/api/dq/catalog/{check_id}/status")
def check_status(check_id: str, body: StatusIn, db: Db = Depends(current_db)):
    return _call(db, dq.set_status, check_id, body.status)


class SeedIn(BaseModel):
    table: Optional[str] = Field(default=None, max_length=800)
    run_id: Optional[str] = Field(default=None, max_length=36)
    domain_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/dq/catalog/seed")
def seed(body: SeedIn, db: Db = Depends(current_db)):
    """Add the checks a table's profile suggests, or a run's approved checks (onto its built target)."""
    if bool(body.table) == bool(body.run_id):
        raise HTTPException(400, "send a table or a run_id")
    if body.table:
        return _call(db, dq.seed_from_profile, body.table, body.domain_id)
    return _call(db, dq.promote_from_run, body.run_id)


class RunIn(BaseModel):
    table: str = Field(min_length=5, max_length=800)
    check_ids: Optional[list[str]] = Field(default=None, max_length=500)


@router.post("/api/dq/run")
def run(body: RunIn, db: Db = Depends(current_db)):
    """Scan a table now (the immediate backtest), with the caller's role."""
    return _call(db, dq.run_table, body.table, body.check_ids, "UI")


@router.get("/api/dq/history")
def history(table: str, limit: int = 20, db: Db = Depends(current_db)):
    """The table's recent scans and, per check, its last outcomes (oldest first) for sparklines."""
    limit = max(1, min(int(limit), 60))
    try:
        scans = db.query(f"""SELECT SCAN_ID, STARTED_AT::VARCHAR AS STARTED_AT, CHECKS, PASSED, WARNED, FAILED, ERRORS,
                                    NOT_EVALUATED, HEALTH, ROWS_SCANNED, TRIGGERED_BY, DURATION_MS
                               FROM QUALITY.CHECK_RUN WHERE TABLE_FQN = %s AND SCOPE = 'TABLE'
                              ORDER BY STARTED_AT DESC LIMIT {limit}""", (table,))
        results = db.query(f"""SELECT CATALOG_CHECK_ID, OUTCOME, MEASURED, FAILED_ROWS, DETAIL,
                                      CREATED_AT::VARCHAR AS CREATED_AT
                                 FROM QUALITY.CHECK_RESULT WHERE TABLE_FQN = %s AND CATALOG_CHECK_ID IS NOT NULL
                              QUALIFY ROW_NUMBER() OVER (PARTITION BY CATALOG_CHECK_ID ORDER BY CREATED_AT DESC) <= {limit}
                                ORDER BY CREATED_AT""", (table,))
    except Exception:
        return {"scans": [], "checks": {}}
    series: dict = {}
    for r in results:
        series.setdefault(r["catalog_check_id"], []).append(
            {"outcome": r["outcome"], "measured": r.get("measured"), "failed_rows": r.get("failed_rows"),
             "detail": r.get("detail"), "at": r.get("created_at")})
    return {"scans": scans, "checks": series}


class ScheduleIn(BaseModel):
    table: str = Field(min_length=5, max_length=800)
    cron: str = Field(min_length=9, max_length=64)


@router.put("/api/dq/schedules")
def set_schedule(body: ScheduleIn, db: Db = Depends(current_db)):
    try:
        task, statements = dq.schedule_sql(DATABASE, body.table, body.cron, WAREHOUSE)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not db.query("SELECT 1 FROM GOVERNANCE.DQ_CHECKS_CATALOG WHERE TABLE_NAME = %s AND STATUS = 'ACTIVE' LIMIT 1",
                    (body.table,)):
        raise HTTPException(409, "add an active check to the table before scheduling it")
    try:
        for statement in statements:
            db.execute(statement)
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    db.execute("""MERGE INTO GOVERNANCE.DQ_SCHEDULE T USING (SELECT %s AS TABLE_NAME, %s AS CRON, %s AS TASK_NAME) S
                    ON T.TABLE_NAME = S.TABLE_NAME
                  WHEN MATCHED THEN UPDATE SET CRON = S.CRON, TASK_NAME = S.TASK_NAME, ENABLED = TRUE,
                       UPDATED_AT = CURRENT_TIMESTAMP()
                  WHEN NOT MATCHED THEN INSERT (TABLE_NAME, CRON, TASK_NAME) VALUES (S.TABLE_NAME, S.CRON, S.TASK_NAME)""",
               (body.table, body.cron.strip(), task))
    db.execute("UPDATE GOVERNANCE.DQ_CHECKS_CATALOG SET SCHEDULE_CRON = %s WHERE TABLE_NAME = %s",
               (body.cron.strip(), body.table))
    return {"table": body.table, "cron": body.cron.strip(), "task": task}


@router.delete("/api/dq/schedules")
def delete_schedule(table: str, db: Db = Depends(current_db)):
    found = db.query("SELECT TASK_NAME FROM GOVERNANCE.DQ_SCHEDULE WHERE TABLE_NAME = %s", (table,))
    if not found:
        raise HTTPException(404, "this table has no schedule")
    task = found[0]["task_name"]
    if not task.startswith(f"{DATABASE}.QUALITY.DQ_"):
        raise HTTPException(409, "the stored task name is not a Data Quality Store task")
    try:
        db.execute(f"DROP TASK IF EXISTS {task}")
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    db.execute("DELETE FROM GOVERNANCE.DQ_SCHEDULE WHERE TABLE_NAME = %s", (table,))
    db.execute("UPDATE GOVERNANCE.DQ_CHECKS_CATALOG SET SCHEDULE_CRON = NULL WHERE TABLE_NAME = %s", (table,))
    return {"table": table, "schedule": None}


@router.get("/api/dq/summary")
def summary(domain: Optional[str] = None, db: Db = Depends(current_db)):
    """For the dashboard: active checks, pass and fail of their last runs, and the share of profiled tables covered."""
    domain_id = _domain_filter(db, domain)
    clause, params = ("AND DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    try:
        found = db.query(f"""SELECT COUNT(*) AS CHECKS, COUNT(DISTINCT TABLE_NAME) AS TABLES,
                                    COUNT_IF(LAST_RUN_STATUS = 'PASS') AS PASSED, COUNT_IF(LAST_RUN_STATUS = 'WARN') AS WARNED,
                                    COUNT_IF(LAST_RUN_STATUS IN ('FAIL', 'ERROR')) AS FAILED,
                                    COUNT_IF(LAST_RUN_STATUS IS NULL) AS NEVER_RUN,
                                    COUNT(DISTINCT IFF(SCHEDULE_CRON IS NOT NULL, TABLE_NAME, NULL)) AS SCHEDULED
                               FROM GOVERNANCE.DQ_CHECKS_CATALOG WHERE STATUS = 'ACTIVE' {clause}""", params)
    except Exception:
        return {"deployed": False, "checks": 0, "tables": 0, "pass_rate": None, "coverage": None}
    s = found[0] if found else {}
    try:
        profiled = db.query(f"""SELECT COUNT(DISTINCT UPPER(DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TABLE_NAME)) AS N
                                  FROM GOVERNANCE.DATA_PROFILES WHERE TRUE {clause}""", params)[0]["n"]
    except Exception:
        profiled = 0
    try:  # approved checks that live in runs (not promoted to the store); shown beside the store's own count
        run_clause, run_params = ("AND DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
        run_checks = int(db.query(f"""SELECT COUNT(*) AS N FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                        WHERE IS_CURRENT AND STATUS = 'APPROVED' {run_clause}""", run_params)[0]["n"])
    except Exception:
        run_checks = 0
    evaluated = int(s.get("passed") or 0) + int(s.get("warned") or 0) + int(s.get("failed") or 0)
    tables = int(s.get("tables") or 0)
    return {"deployed": True, "checks": int(s.get("checks") or 0), "tables": tables,
            "passed": int(s.get("passed") or 0), "warned": int(s.get("warned") or 0), "failed": int(s.get("failed") or 0),
            "never_run": int(s.get("never_run") or 0), "scheduled_tables": int(s.get("scheduled") or 0),
            "run_checks": run_checks,
            "pass_rate": round(100 * int(s.get("passed") or 0) / evaluated, 1) if evaluated else None,
            "coverage": round(100 * min(tables, int(profiled)) / int(profiled), 1) if profiled else None}
