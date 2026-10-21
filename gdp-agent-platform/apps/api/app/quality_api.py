"""Data Quality Store (/dashboard/dq-store): Soda / Great Expectations checks per table and column, without a run.

The catalog (CORE.DQ_CHECKS_CATALOG, V037) keeps each check in the platform's neutral form, which the scanner
runs, with the rendered SodaCL beside it; a table is scanned now or on a schedule (a Snowflake task that calls
CORE.RUN_TABLE_SCAN). Each table's checks are versioned as a suite (V040): additions and changes from the one-line
SodaCL editor, the structured editor, a table's profile or a run's approved checks land in the table's draft, which
is submitted, reviewed (changes, validation against the approved domain model) and approved by another person; the
approved version is then written to the catalog (CORE.APPLY_DQ_SUITE) and can be published to Git as SodaCL and a
Great Expectations suite. The logic lives in services/catalog/dq.py and dq_suite.py; this router validates input and
maps errors.
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.db import DATABASE, WAREHOUSE, Db
from app.main import _json, _snowflake_error, _source_call, current_db
from app.perf import cached
from app.source_invoke import invoke_source
from services.catalog import dq, dq_suite
from services.common.platform_schema import fqn, resolve

router = APIRouter()
CATALOG_FIELDS = """CHECK_ID, TABLE_NAME, COLUMN_NAME, CHECK_TYPE, CHECK_DEFINITION, SODA_DSL, SEVERITY, STATUS, ORIGIN,
                    DOMAIN_ID, SCHEDULE_CRON, LAST_RUN_STATUS, LAST_RUN_AT::VARCHAR AS LAST_RUN_AT, LAST_MEASURED,
                    CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT, UPDATED_AT::VARCHAR AS UPDATED_AT"""
LINEAGE_FIELDS = "REQUIREMENT, SOURCE_RUN_ID"  # V038


def _call(db: Db, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return invoke_source(db, partial(fn, **kwargs) if kwargs else fn, *args)
    except HTTPException:
        raise
    except dq_suite.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except AssertionError as exc:
        message = str(exc) or "request rejected"
        raise HTTPException(404 if "not found" in message.lower() else 400, message) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


def _user(db: Db) -> str:
    return str(db.user or "").upper()


def _domain_filter(db: Db, domain: Optional[str]) -> Optional[str]:
    if not domain:
        return None
    if len(domain) == 36 and domain.count("-") == 4:
        return domain
    found = db.query("SELECT DOMAIN_ID FROM CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = UPPER(%s) LIMIT 1", (domain,))
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
    sql = (f"SELECT {{fields}} FROM CORE.DQ_CHECKS_CATALOG WHERE {' AND '.join(where)} "
           "ORDER BY TABLE_NAME, COLUMN_NAME NULLS FIRST, CHECK_TYPE LIMIT 5000")
    try:
        found = db.query(sql.format(fields=f"{CATALOG_FIELDS}, {LINEAGE_FIELDS}"), tuple(params))
    except Exception:
        try:  # before V038: no requirement or source run
            found = db.query(sql.format(fields=CATALOG_FIELDS), tuple(params))
        except Exception:
            return {"tables": [], "deployed": False}
    rows = [_row(r) for r in found]
    try:
        schedules = {r["table_name"]: r for r in db.query(
            "SELECT TABLE_NAME, CRON, TASK_NAME, ENABLED FROM CORE.DQ_SCHEDULE")}
    except Exception:
        schedules = {}
    tree = dq.build_tree(rows)
    suites = _suites(db, domain_id)
    for t in tree:
        s = schedules.get(t["table"])
        t["schedule"] = {"cron": s["cron"], "enabled": bool(s.get("enabled"))} if s else None
        t["suite"] = suites.pop(t["table"], None)
    # tables whose first checks are still a draft: not in the catalog yet, listed so the draft can be found
    term = (table or q or "").lower()
    for name, suite in sorted(suites.items()):
        if suite.get("open_version") and (not term or term in name.lower()):
            tree.append({"table": name, "domain_id": suite.get("domain_id"), "schedule_cron": None, "checks": 0,
                         "counts": {}, "pass_rate": None, "columns": [], "schedule": None, "suite": suite})
    return {"tables": tree, "deployed": True, "checks": len(rows)}


def _suites(db: Db, domain_id: Optional[str]) -> dict:
    """Suite state per table (V040): approved and open version, review state, pull request."""
    clause, params = ("WHERE S.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    try:
        found = db.query(f"""SELECT S.TABLE_NAME, S.DOMAIN_ID, S.APPROVED_VERSION, S.OPEN_VERSION, O.STATUS AS OPEN_STATUS,
                                    O.AUTHORS AS OPEN_AUTHORS, O.SUBMITTED_BY, A.PR_STATE, A.MODEL_VERSION
                               FROM CORE.DQ_SUITE S
                               LEFT JOIN CORE.DQ_SUITE_VERSION O ON O.SUITE_ID = S.SUITE_ID AND O.VERSION = S.OPEN_VERSION
                               LEFT JOIN CORE.DQ_SUITE_VERSION A ON A.SUITE_ID = S.SUITE_ID AND A.VERSION = S.APPROVED_VERSION
                               {clause}""", params)
    except Exception:
        return {}
    out = {}
    for r in found:
        r["open_authors"] = _json(r.get("open_authors")) or []
        out[r["table_name"]] = r
    return out


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
    """Add a check to the table's suite draft, or change one (`check_id`); a draft is opened when there is none."""
    if not body.definition and not body.dsl:
        raise HTTPException(400, "send a definition or a one-line SodaCL check")
    return _call(db, dq_suite.stage_check, json.dumps(body.model_dump()), _user(db))


class StatusIn(BaseModel):
    status: Literal["ACTIVE", "PAUSED", "RETIRED"]
    table: Optional[str] = Field(default=None, max_length=800)


@router.post("/api/dq/catalog/{check_id}/status")
def check_status(check_id: str, body: StatusIn, db: Db = Depends(current_db)):
    """Pause, resume or retire (remove) a check in the table's suite draft."""
    table = body.table
    if not table:
        found = db.query("SELECT TABLE_NAME FROM CORE.DQ_CHECKS_CATALOG WHERE CHECK_ID = %s", (check_id,))
        if not found:
            raise HTTPException(404, "check not found; pass its table")
        table = found[0]["table_name"]
    return _call(db, dq_suite.stage_status, table, check_id, body.status, _user(db))


class SeedIn(BaseModel):
    table: Optional[str] = Field(default=None, max_length=800)
    run_id: Optional[str] = Field(default=None, max_length=36)
    domain_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/dq/catalog/seed")
def seed(body: SeedIn, db: Db = Depends(current_db)):
    """Add the checks a table's profile suggests, or a run's approved checks (onto its built target), to the table's
    suite draft."""
    if bool(body.table) == bool(body.run_id):
        raise HTTPException(400, "send a table or a run_id")
    if body.table:
        return _call(db, dq_suite.seed_profile, body.table, _user(db), body.domain_id)
    return _call(db, dq_suite.seed_run, body.run_id, _user(db))


class GenerateIn(BaseModel):
    table: str = Field(min_length=5, max_length=800)
    text: str = Field(min_length=10, max_length=200_000)
    filename: str = Field(default="", max_length=255)
    domain_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/dq/catalog/generate")
def generate_from_requirement(body: GenerateIn, db: Db = Depends(current_db)):
    """The checks a client requirement asks for, staged into the table's suite draft (reviewed, backtested and approved
    like any change). CSV or JSON rows are mapped as given; a free-text brief is read by AI, so it also needs AI.USE."""
    from app.cases_api import _who
    from services.catalog import dq_requirements
    from services.soda.extract import parse_client_document

    try:
        structured = bool(parse_client_document(body.text, body.filename).get("rows"))
    except Exception as exc:
        raise HTTPException(400, f"could not read the requirement: {str(exc)[:200]}") from exc
    if not structured:
        privileges = _who(db)["privileges"]
        if "AI.USE" not in privileges and "*" not in privileges:
            raise HTTPException(403, "Not allowed: reading a free-text requirement with AI needs the AI.USE privilege. "
                                     "Upload it as CSV or JSON rows instead, or ask for AI.USE.")
    return _call(db, dq_requirements.generate, body.table, body.text, body.filename, _user(db), body.domain_id)


class RunIn(BaseModel):
    table: str = Field(min_length=5, max_length=800)
    check_ids: Optional[list[str]] = Field(default=None, max_length=500)


@router.post("/api/dq/run")
def run(body: RunIn, db: Db = Depends(current_db)):
    """Scan a table now (the immediate backtest), with the caller's role."""
    return _call(db, dq.run_table, body.table, body.check_ids, "UI")


def _table(value: str) -> str:
    """DATABASE.SCHEMA.TABLE as the catalog stores it (unquoted parts upper case), or 400."""
    try:
        return dq._fqn(value)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/dq/history")
def history(table: str, limit: int = 20, db: Db = Depends(current_db)):
    """The table's recent scans and, per check, its last outcomes (oldest first) for sparklines."""
    limit = max(1, min(int(limit), 60))
    table = _table(table)
    try:
        scans = db.query(f"""SELECT SCAN_ID, STARTED_AT::VARCHAR AS STARTED_AT, CHECKS, PASSED, WARNED, FAILED, ERRORS,
                                    NOT_EVALUATED, HEALTH, ROWS_SCANNED, TRIGGERED_BY, DURATION_MS
                               FROM CORE.CHECK_RUN WHERE TABLE_FQN = %s AND SCOPE = 'TABLE'
                              ORDER BY STARTED_AT DESC LIMIT {limit}""", (table,))
        results = db.query(f"""SELECT CATALOG_CHECK_ID, OUTCOME, MEASURED, FAILED_ROWS, DETAIL,
                                      CREATED_AT::VARCHAR AS CREATED_AT
                                 FROM CORE.CHECK_RESULT WHERE TABLE_FQN = %s AND CATALOG_CHECK_ID IS NOT NULL
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
    body.table = _table(body.table)
    try:
        task, statements = dq.schedule_sql(DATABASE, body.table, body.cron, WAREHOUSE)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not db.query("SELECT 1 FROM CORE.DQ_CHECKS_CATALOG WHERE TABLE_NAME = %s AND STATUS = 'ACTIVE' LIMIT 1",
                    (body.table,)):
        raise HTTPException(409, "add an active check to the table before scheduling it")
    try:
        for statement in statements:
            db.execute(statement)
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    db.execute("""MERGE INTO CORE.DQ_SCHEDULE T USING (SELECT %s AS TABLE_NAME, %s AS CRON, %s AS TASK_NAME) S
                    ON T.TABLE_NAME = S.TABLE_NAME
                  WHEN MATCHED THEN UPDATE SET CRON = S.CRON, TASK_NAME = S.TASK_NAME, ENABLED = TRUE,
                       UPDATED_AT = CURRENT_TIMESTAMP()
                  WHEN NOT MATCHED THEN INSERT (TABLE_NAME, CRON, TASK_NAME) VALUES (S.TABLE_NAME, S.CRON, S.TASK_NAME)""",
               (body.table, body.cron.strip(), task))
    db.execute("UPDATE CORE.DQ_CHECKS_CATALOG SET SCHEDULE_CRON = %s WHERE TABLE_NAME = %s",
               (body.cron.strip(), body.table))
    return {"table": body.table, "cron": body.cron.strip(), "task": task}


@router.delete("/api/dq/schedules")
def delete_schedule(table: str, db: Db = Depends(current_db)):
    table = _table(table)
    found = db.query("SELECT TASK_NAME FROM CORE.DQ_SCHEDULE WHERE TABLE_NAME = %s", (table,))
    if not found:
        raise HTTPException(404, "this table has no schedule")
    task = resolve(found[0]["task_name"] or "", "task")  # names stored before V061 still say QUALITY
    if not task.startswith(fqn(DATABASE, "DQ_")):
        raise HTTPException(409, "the stored task name is not a Data Quality Store task")
    try:
        db.execute(f"DROP TASK IF EXISTS {task}")
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    try:   # the suspended copy kept from before V061 (names before V061); a new environment has no QUALITY schema
        db.execute(f"DROP TASK IF EXISTS {task.replace(fqn(DATABASE, ''), f'{DATABASE}.QUALITY.', 1)}")
    except Exception:
        pass
    db.execute("DELETE FROM CORE.DQ_SCHEDULE WHERE TABLE_NAME = %s", (table,))
    db.execute("UPDATE CORE.DQ_CHECKS_CATALOG SET SCHEDULE_CRON = NULL WHERE TABLE_NAME = %s", (table,))
    return {"table": table, "schedule": None}


@router.get("/api/dq/summary")
@cached("dq-summary", 20)
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
                               FROM CORE.DQ_CHECKS_CATALOG WHERE STATUS = 'ACTIVE' {clause}""", params)
    except Exception:
        return {"deployed": False, "checks": 0, "tables": 0, "pass_rate": None, "coverage": None}
    s = found[0] if found else {}
    try:
        profiled = db.query(f"""SELECT COUNT(DISTINCT UPPER(DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TABLE_NAME)) AS N
                                  FROM CORE.DATA_PROFILES WHERE TRUE {clause}""", params)[0]["n"]
    except Exception:
        profiled = 0
    try:  # approved checks that live in runs (not promoted to the store); shown beside the store's own count
        run_clause, run_params = ("AND DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
        run_checks = int(db.query(f"""SELECT COUNT(*) AS N FROM CORE.SODA_EXPECTATION_REGISTRY
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


# ---------------------------------------------------------------- suites: versions, review, export, Git (V040)


@router.get("/api/dq/suites")
def suites(db: Db = Depends(current_db)):
    try:
        return {"suites": invoke_source(db, dq_suite.list_suites), "deployed": True}
    except Exception:
        return {"suites": [], "deployed": False}


@router.get("/api/dq/suites/one")
def suite(table: str = Query(min_length=5, max_length=800), version: Optional[int] = Query(default=None, ge=1),
          db: Db = Depends(current_db)):
    return _call(db, dq_suite.get_suite, table, version)


@router.get("/api/dq/suites/diff")
def suite_diff(table: str = Query(min_length=5, max_length=800), to: int = Query(ge=1),
               from_version: Optional[int] = Query(default=None, alias="from", ge=1), db: Db = Depends(current_db)):
    return _call(db, dq_suite.diff_versions, table, from_version, to)


@router.get("/api/dq/suites/export")
def suite_export(table: str = Query(min_length=5, max_length=800), version: Optional[int] = Query(default=None, ge=1),
                 format: Literal["soda", "gx", "portable"] = "soda", db: Db = Depends(current_db)):  # noqa: A002
    """SodaCL, a Great Expectations suite, or the portable file (logical table names through this environment's
    bindings) that imports into another environment."""
    name, media, content = _call(db, dq_suite.export_version, table, version, format)
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


class SuiteImportIn(BaseModel):
    filename: str = Field(default="", max_length=255)
    text: str = Field(min_length=2, max_length=1_000_000)
    domain: Optional[str] = Field(default=None, max_length=128)


@router.post("/api/dq/suites/import")
def suite_import(body: SuiteImportIn, db: Db = Depends(current_db)):
    """A portable suite file (.dq.json) into this environment: logical names resolved with its bindings, the checks
    added to the table's draft, which is then submitted and approved like any change."""
    try:
        dq_suite.parse_portable(body.text)
    except ValueError as exc:
        raise HTTPException(400, f"could not read {body.filename or 'the file'}: {exc}") from exc
    return _call(db, dq_suite.import_suite, body.text, _user(db), _domain_filter(db, body.domain))


class SuiteIn(BaseModel):
    table: str = Field(min_length=5, max_length=800)
    note: Optional[str] = Field(default=None, max_length=2000)


class OpenIn(SuiteIn):
    from_version: Optional[int] = Field(default=None, ge=1)


@router.post("/api/dq/suites/draft")
def suite_open(body: OpenIn, db: Db = Depends(current_db)):
    """Open the next version from the approved one, or from an older version (a rollback)."""
    return _call(db, dq_suite.open_draft, body.table, _user(db), body.from_version, body.note or "")


class DraftIn(BaseModel):
    table: str = Field(min_length=5, max_length=800)
    etag: str = Field(min_length=1, max_length=64)
    checks: list[dict] = Field(max_length=500)
    change_note: Optional[str] = Field(default=None, max_length=2000)


@router.put("/api/dq/suites/draft")
def suite_save(body: DraftIn, db: Db = Depends(current_db)):
    return _call(db, dq_suite.save_draft, body.table, json.dumps(body.model_dump()), _user(db))


@router.post("/api/dq/suites/submit")
def suite_submit(body: SuiteIn, db: Db = Depends(current_db)):
    return _call(db, dq_suite.submit, body.table, _user(db), body.note or "")


@router.post("/api/dq/suites/withdraw")
def suite_withdraw(body: SuiteIn, db: Db = Depends(current_db)):
    return _call(db, dq_suite.withdraw, body.table, _user(db))


class DecideIn(BaseModel):
    table: str = Field(min_length=5, max_length=800)
    version: int = Field(ge=1)
    acknowledge_loosened: bool = False
    reason: Optional[str] = Field(default=None, max_length=2000)
    etag: Optional[str] = Field(default=None, max_length=64)   # the version as the reviewer read it


def _apply(db: Db, suite_id: str, version: int) -> dict:
    try:
        return {"applied": True, **_source_call(db, "CALL CORE.APPLY_DQ_SUITE(%s, %s)", dq_suite.apply_suite_version,
                                                suite_id, version)}
    except AssertionError as exc:
        return {"applied": False, "error": str(exc)}
    except Exception as exc:
        return {"applied": False, "error": str(exc)[:500]}


@router.post("/api/dq/suites/approve")
def suite_approve(body: DecideIn, db: Db = Depends(current_db)):
    """Four eyes. The approved version is then written to the catalog the scanner reads; if that fails the approval
    stands and the version can be applied again."""
    out = _call(db, dq_suite.approve, body.table, body.version, _user(db), body.acknowledge_loosened, body.etag)
    return {**out, "catalog": _apply(db, out["suite_id"], body.version)}


@router.post("/api/dq/suites/apply")
def suite_apply(body: DecideIn, db: Db = Depends(current_db)):
    found = _call(db, dq_suite.get_suite, body.table, body.version)
    current = found.get("current") or {}
    if current.get("status") not in ("APPROVED", "PUBLISHED"):
        raise HTTPException(409, "only an approved version is applied to the catalog")
    return _apply(db, found["suite"]["suite_id"], body.version)


@router.post("/api/dq/suites/reject")
def suite_reject(body: DecideIn, db: Db = Depends(current_db)):
    return _call(db, dq_suite.reject, body.table, body.version, _user(db), body.reason or "", body.etag)


class PublishIn(BaseModel):
    table: str = Field(min_length=5, max_length=800)
    version: int = Field(ge=1)
    repo_id: str = Field(min_length=1, max_length=64)
    base: Optional[str] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, max_length=200)


@router.get("/api/dq/soda-cloud/ci")
def soda_cloud_ci():
    """The GitHub Actions workflow and scan script that publish merged suites to Soda Cloud (committed once by a
    person; the platform never holds Soda Cloud keys)."""
    from services.soda import cloud_ci

    return {"files": cloud_ci.files(), "secrets": cloud_ci.SECRETS}


@router.get("/api/dq/suites/publishing")
def suite_publishing(db: Db = Depends(current_db)):
    from app.main import _dbt_repos, _github_publisher_ready

    repos = [{k: r.get(k) for k in ("repo_id", "name", "git_url", "provider", "branch")} for r in _dbt_repos(db, None)
             if (r.get("provider") or "") == "GITHUB"]
    return {"repos": repos, "publisher_ready": _github_publisher_ready(db)}


@router.post("/api/dq/suites/publish")
def suite_publish(body: PublishIn, db: Db = Depends(current_db)):
    """Open a pull request with an approved version's SodaCL and Great Expectations files
    (feature/dq-<table>-v<version>). The version becomes PUBLISHED when the pull request is merged."""
    import re

    from app.main import _dbt_repos, _github_publisher_ready

    found = _call(db, dq_suite.get_suite, body.table, body.version)
    current = found.get("current") or {}
    if current.get("status") != "APPROVED":
        raise HTTPException(409, "Only an approved version is published; approve it first.")
    repo = next((r for r in _dbt_repos(db, None) if r["repo_id"] == body.repo_id), None)
    if not repo or (repo.get("provider") or "") != "GITHUB":
        raise HTTPException(409, "Choose a GitHub repository connected in Admin, Integrations.")
    if not _github_publisher_ready(db):
        return {"status": "NOT_CONFIGURED", "detail": "GitHub publishing is not set up. An admin can set it up in the GitHub publishing card."}
    slug = re.sub(r"[^a-z0-9]+", "-", found["suite"]["table_name"].split(".")[-1].lower()).strip("-") or "table"
    head = f"feature/dq-{slug}-v{int(body.version)}"
    request = {"suite_id": found["suite"]["suite_id"], "version": int(body.version), "repo_id": body.repo_id,
               "head": head, "base": body.base or repo.get("branch"), "title": body.title}
    try:
        result = db.call("CALL CORE.PUBLISH_DBT_PR(%s, %s)", ("", json.dumps({"dq_suite": request})))
    except Exception as exc:
        return {"status": "FAILED", "detail": str(exc)[:1000], "head_branch": head}
    return {**(result or {}), "head_branch": head}


class SyncIn(BaseModel):
    table: Optional[str] = Field(default=None, max_length=800)
    force: bool = False


@router.post("/api/dq/suites/pr-sync")
def suite_pr_sync(body: SyncIn, db: Db = Depends(current_db)):
    from app.main import _github_publisher_ready

    if not _github_publisher_ready(db):
        return {"checked": 0, "merged": [], "closed": [], "errors": [], "detail": "GitHub publishing is not set up"}
    try:
        suite_id = dq_suite.suite_id_for(dq._fqn(body.table)) if body.table else ""
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        return db.call("CALL CORE.PUBLISH_DBT_PR(%s, %s)",
                       ("", json.dumps({"sync_dq_prs": {"suite_id": suite_id, "force": body.force}}))) or {}
    except Exception as exc:
        raise _snowflake_error(exc) from exc
