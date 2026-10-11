"""QA and Data Quality Center (/dashboard/quality) and Onboarding Hub (/dashboard/onboarding).

The Quality Center is read only: it unifies data quality scans (QUALITY.CHECK_RUN / CHECK_RESULT, Soda checks from
CONTRACT.SODA_EXPECTATION_REGISTRY), Great Expectations suites rendered from the same checks (services/quality/gx.py),
QA executions (QUALITY.QA_RUN / QA_RESULT), readiness gates per run and Jira incidents (JIRA.ISSUE_LINK). Acting on
any of them stays on the run pages and the QA workspace.

Onboarding validates a domain_pack.json and its contract before the existing /api/domains/import registers the pack.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _json, current_db
from services.catalog import quality as cat_quality

router = APIRouter()


def _safe(db: Db, sql: str, params: tuple = ()) -> list[dict]:
    try:
        return db.query(sql, params)
    except Exception:
        return []


@router.get("/api/hub/quality/summary")
def summary(days: int = 30, db: Db = Depends(current_db)):
    days = max(1, min(int(days), 365))
    scans = _safe(db, """SELECT COUNT(*) AS SCANS, ROUND(AVG(HEALTH), 1) AS AVG_HEALTH, SUM(FAILED) AS FAILED,
                                SUM(WARNED) AS WARNED, SUM(CHECKS) AS CHECKS
                           FROM QUALITY.CHECK_RUN WHERE STARTED_AT >= DATEADD(DAY, -%s, CURRENT_TIMESTAMP())""", (days,))
    qa = _safe(db, """SELECT COUNT(*) AS RUNS, SUM(TESTS) AS TESTS, SUM(PASSED) AS PASSED, SUM(FAILED) AS FAILED,
                             SUM(ERRORS) AS ERRORS
                        FROM QUALITY.QA_RUN WHERE STARTED_AT >= DATEADD(DAY, -%s, CURRENT_TIMESTAMP())""", (days,))
    incidents = _safe(db, """SELECT COUNT(*) AS OPEN FROM JIRA.ISSUE_LINK
                              WHERE NOT IS_DELETED AND COALESCE(STATUS_CATEGORY, '') NOT IN ('done', 'Done')""")
    suites = _safe(db, "SELECT COUNT(*) AS N FROM CONTRACT.QA_TEST_SUITE WHERE NOT COALESCE(IS_DELETED, FALSE)")
    s, q = (scans or [{}])[0], (qa or [{}])[0]
    tests = int(q.get("tests") or 0)
    return {"days": days,
            "data_quality": {"scans": int(s.get("scans") or 0), "avg_health": s.get("avg_health"),
                             "failed_checks": int(s.get("failed") or 0), "warned_checks": int(s.get("warned") or 0),
                             "checks": int(s.get("checks") or 0)},
            "qa": {"runs": int(q.get("runs") or 0), "tests": tests, "failed": int(q.get("failed") or 0),
                   "errors": int(q.get("errors") or 0),
                   "pass_rate": round(100 * int(q.get("passed") or 0) / tests, 1) if tests else None},
            "incidents": {"open": int((incidents or [{}])[0].get("open") or 0)},
            "suites": int((suites or [{}])[0].get("n") or 0)}


@router.get("/api/hub/quality/executions")
def executions(kind: str = "all", q: Optional[str] = None, limit: int = 100, db: Db = Depends(current_db)):
    """Soda scans and QA runs in one timeline, newest first."""
    limit = max(1, min(int(limit), 500))
    out: list[dict] = []
    like = f"%{q}%" if q else "%"
    if kind in ("all", "soda"):
        for r in _safe(db, f"""SELECT C.SCAN_ID AS ID, C.RUN_ID, R.RUN_NAME, C.TARGET, C.MODE, C.CHECKS AS TOTAL,
                                      C.PASSED, C.WARNED, C.FAILED, C.ERRORS, C.HEALTH, C.TRIGGERED_BY,
                                      C.STARTED_AT::VARCHAR AS STARTED_AT, C.DURATION_MS
                                 FROM QUALITY.CHECK_RUN C LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = C.RUN_ID
                                WHERE COALESCE(C.TARGET, '') ILIKE %s OR COALESCE(R.RUN_NAME, '') ILIKE %s
                                ORDER BY C.STARTED_AT DESC LIMIT {limit}""", (like, like)):
            out.append({**r, "kind": "SODA"})
    if kind in ("all", "qa"):
        for r in _safe(db, f"""SELECT Q.QA_RUN_ID AS ID, Q.RUN_ID, R.RUN_NAME, Q.TARGET, Q.SCOPE AS MODE,
                                      Q.TESTS AS TOTAL, Q.PASSED, Q.REVIEW AS WARNED, Q.FAILED, Q.ERRORS,
                                      IFF(Q.TESTS > 0, ROUND(100 * Q.PASSED / Q.TESTS), NULL) AS HEALTH,
                                      Q.TRIGGERED_BY, Q.STARTED_AT::VARCHAR AS STARTED_AT, Q.DURATION_MS
                                 FROM QUALITY.QA_RUN Q LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = Q.RUN_ID
                                WHERE COALESCE(Q.TARGET, '') ILIKE %s OR COALESCE(R.RUN_NAME, '') ILIKE %s
                                ORDER BY Q.STARTED_AT DESC LIMIT {limit}""", (like, like)):
            out.append({**r, "kind": "QA"})
    out.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return {"executions": out[:limit]}


@router.get("/api/hub/quality/executions/{kind}/{execution_id}")
def execution_results(kind: str, execution_id: str, db: Db = Depends(current_db)):
    if kind.upper() == "SODA":
        rows = _safe(db, """SELECT TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, DIMENSION, SEVERITY, OUTCOME, MEASURED,
                                   THRESHOLD, FAILED_ROWS, DETAIL FROM QUALITY.CHECK_RESULT WHERE SCAN_ID = %s
                             ORDER BY OUTCOME, TARGET_COLUMN""", (execution_id,))
    elif kind.upper() == "QA":
        rows = _safe(db, """SELECT TEST_ID, CATEGORY, TITLE, SEVERITY, OUTCOME, ROWS_RETURNED, MEASURED, EXPECTED, DETAIL
                              FROM QUALITY.QA_RESULT WHERE QA_RUN_ID = %s ORDER BY OUTCOME, TITLE""", (execution_id,))
    else:
        raise HTTPException(400, "kind is SODA or QA")
    return {"results": rows}


@router.get("/api/hub/quality/suites")
def suites(db: Db = Depends(current_db)):
    qa = _safe(db, """SELECT S.SUITE_ID, S.NAME, S.DESCRIPTION, S.IS_DEFAULT, S.DOMAIN_ID, T.TARGET_TABLE,
                             (SELECT COUNT(*) FROM CONTRACT.QA_TEST_CASE C WHERE C.SUITE_ID = S.SUITE_ID) AS TESTS,
                             S.CREATED_AT::VARCHAR AS CREATED_AT
                        FROM CONTRACT.QA_TEST_SUITE S
                        LEFT JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = S.TARGET_TABLE_ID
                       WHERE NOT COALESCE(S.IS_DELETED, FALSE) ORDER BY S.NAME LIMIT 300""")
    packs = _safe(db, """SELECT E.RUN_ID, MAX(R.RUN_NAME) AS RUN_NAME, MAX(E.TARGET_TABLE) AS TARGET_TABLE,
                                COUNT(*) AS CHECKS, COUNT_IF(E.STATUS = 'APPROVED') AS APPROVED,
                                COUNT_IF(E.STATUS = 'PROPOSED') AS PROPOSED
                           FROM CONTRACT.SODA_EXPECTATION_REGISTRY E LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = E.RUN_ID
                          WHERE E.IS_CURRENT AND E.STATUS <> 'REJECTED'
                          GROUP BY E.RUN_ID ORDER BY MAX(E.CREATED_AT) DESC LIMIT 200""")
    return {"qa_suites": qa, "check_packs": packs}


@router.get("/api/hub/quality/gx/{run_id}")
def gx_suite(run_id: str, db: Db = Depends(current_db)):
    """The run's current checks as a Great Expectations (GX Core 1.x) suite and as SodaCL."""
    from services.quality.gx import render_suite

    rows = _safe(db, """SELECT TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION, SEVERITY, ORIGIN, STATUS
                          FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                         WHERE RUN_ID = %s AND IS_CURRENT AND STATUS <> 'REJECTED'""", (run_id,))
    if not rows:
        raise HTTPException(404, "this run has no data quality checks yet")
    checks = [{"target_column": r["target_column"], "check_type": r["check_type"], "severity": r["severity"],
               "origin": r["origin"], "status": r["status"], "definition": _json(r["check_definition"]) or {}}
              for r in rows]
    table = rows[0]["target_table"] or "model"
    return {"run_id": run_id, "target_table": table, "gx_suite": render_suite(table, checks)}


@router.get("/api/hub/quality/gates")
def gates(limit: int = 50, db: Db = Depends(current_db)):
    """Readiness per recent run that has reached data quality or QA."""
    limit = max(1, min(int(limit), 200))
    runs = _safe(db, f"""SELECT RUN_ID, RUN_NAME, CURRENT_STATE, TARGET_MODEL, UPDATED_AT::VARCHAR AS UPDATED_AT
                           FROM CORE.WORKFLOW_RUN
                          WHERE NOT COALESCE(IS_ARCHIVED, FALSE)
                            AND (RUN_ID IN (SELECT RUN_ID FROM QUALITY.CHECK_RUN)
                                 OR RUN_ID IN (SELECT RUN_ID FROM QUALITY.QA_RUN WHERE RUN_ID IS NOT NULL))
                          ORDER BY UPDATED_AT DESC LIMIT {limit}""")
    if not runs:
        return {"runs": []}
    ids = tuple(r["run_id"] for r in runs)
    marks = ", ".join(["%s"] * len(ids))
    scans = {r["run_id"]: r for r in _safe(db, f"""SELECT RUN_ID, HEALTH, FAILED FROM QUALITY.CHECK_RUN WHERE RUN_ID IN ({marks})
                                                   QUALIFY ROW_NUMBER() OVER (PARTITION BY RUN_ID ORDER BY STARTED_AT DESC) = 1""", ids)}
    qa = {r["run_id"]: r for r in _safe(db, f"""SELECT RUN_ID, TESTS, FAILED, ERRORS FROM QUALITY.QA_RUN WHERE RUN_ID IN ({marks})
                                                QUALIFY ROW_NUMBER() OVER (PARTITION BY RUN_ID ORDER BY STARTED_AT DESC) = 1""", ids)}
    signoffs = {r["run_id"]: r for r in _safe(db, f"""SELECT RUN_ID, DECISION, DECIDED_BY FROM CONTRACT.QA_SIGNOFF WHERE RUN_ID IN ({marks})
                                                      QUALIFY ROW_NUMBER() OVER (PARTITION BY RUN_ID ORDER BY DECIDED_AT DESC) = 1""", ids)}
    issues = {r["run_id"]: int(r["n"]) for r in _safe(db, f"""SELECT RUN_ID, COUNT(*) AS N FROM JIRA.ISSUE_LINK
                                                              WHERE RUN_ID IN ({marks}) AND NOT IS_DELETED
                                                                AND COALESCE(STATUS_CATEGORY, '') NOT IN ('done', 'Done')
                                                              GROUP BY RUN_ID""", ids)}
    out = []
    for r in runs:
        gate = cat_quality.readiness(scans.get(r["run_id"]), qa.get(r["run_id"]), signoffs.get(r["run_id"]),
                                     issues.get(r["run_id"], 0))
        out.append({**r, **gate})
    return {"runs": out}


@router.get("/api/hub/quality/incidents")
def incidents(open_only: bool = True, db: Db = Depends(current_db)):
    where = "WHERE NOT L.IS_DELETED" + (" AND COALESCE(L.STATUS_CATEGORY, '') NOT IN ('done', 'Done')" if open_only else "")
    rows = _safe(db, f"""SELECT L.LINK_ID, L.ISSUE_KEY, L.SUMMARY, L.STATUS, L.STATUS_CATEGORY, L.ORIGIN, L.RUN_ID,
                               R.RUN_NAME, L.TARGET_TABLE, L.QA_TEST_ID, L.LINKED_BY, L.LINKED_AT::VARCHAR AS LINKED_AT,
                               L.LAST_SYNCED_AT::VARCHAR AS LAST_SYNCED_AT
                          FROM JIRA.ISSUE_LINK L LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = L.RUN_ID
                         {where} ORDER BY L.LINKED_AT DESC LIMIT 300""")
    return {"incidents": rows}


# ---------------------------------------------------------------- onboarding


class OnboardingCheck(BaseModel):
    pack: Optional[dict] = None
    contract_name: Optional[str] = Field(default=None, max_length=255)
    contract_text: Optional[str] = Field(default=None, max_length=500_000)


@router.post("/api/hub/onboarding/validate")
def validate_onboarding(body: OnboardingCheck, db: Db = Depends(current_db)):
    """Dry run of a domain import: the pack exactly as /api/domains/import would prepare it, and the contract."""
    from services.knowledge.packs import prepare

    if body.pack is None and not body.contract_text:
        raise HTTPException(400, "upload a domain_pack.json, a contract, or both")
    out: dict = {}
    if body.pack is not None:
        prepared, problems = prepare(body.pack)
        name = prepared["domain"]["name"]
        exists = bool(_safe(db, "SELECT 1 FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = UPPER(%s) LIMIT 1",
                            (name,)))
        out["pack"] = {"ok": not problems, "problems": problems, "domain": name, "exists": exists,
                       "summary": {"targets": len(prepared["targets"]),
                                   "columns": sum(len(t.get("columns") or []) for t in prepared["targets"]),
                                   "knowledge": len(prepared["knowledge"])}}
    if body.contract_text:
        contract = cat_quality.validate_contract(body.contract_name or "contract.md", body.contract_text)
        contract.pop("contract", None)
        if body.pack is not None and contract.get("summary", {}).get("domain") \
                and contract["summary"]["domain"] != out["pack"]["domain"]:
            contract["problems"] = contract.get("problems", []) + [
                f"the contract is for {contract['summary']['domain']}, the pack for {out['pack']['domain']}"]
            contract["ok"] = False
        out["contract"] = contract
    out["ok"] = all(v.get("ok") for v in out.values() if isinstance(v, dict))
    return out
