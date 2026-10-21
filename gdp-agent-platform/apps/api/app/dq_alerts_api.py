"""Data quality alerting (V069, services/quality/alerts.py): failing checks for the Command Center and My Work, the
daily digests, and which support team owns each domain's data quality incidents.

Governance (services.governance.policy): failing checks and digests are read by everyone signed in, like the DQ Store;
the routes are read with OPS.VIEW and changed with INTEGRATION.MANAGE (like support teams and routing rules); running
the evaluation now needs OPS.OPERATE. Calls run on the caller's session.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _json, current_db
from app.ops_api import _iso, _q, _x
from services.ops.detect import SEVERITIES
from services.quality import alerts

router = APIRouter()


def _soft(db: Db, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """Rows, or none before V069 (the new tables are missing)."""
    try:
        return db.query(sql, params)
    except Exception as exc:
        if "does not exist" in str(exc).lower() or "not authorized" in str(exc).lower():
            return []
        raise


# domains the caller owns or is a member of
_MY_DOMAINS = """(SELECT DOMAIN_ID FROM CORE.DOMAIN_REGISTRY WHERE UPPER(OWNER) = UPPER(%s)
                  UNION SELECT DOMAIN_ID FROM CORE.DOMAIN_MEMBER WHERE UPPER(USER_NAME) = UPPER(%s))"""

# the latest result of each ACTIVE catalog check that is failing now, with the table's open DQ incident
_FAILING = """
WITH F AS (
    SELECT C.CHECK_ID, C.TABLE_NAME, C.COLUMN_NAME, C.CHECK_TYPE, C.SEVERITY, C.DOMAIN_ID, R.OUTCOME, R.MEASURED,
           R.THRESHOLD, R.FAILED_ROWS, R.CREATED_AT
      FROM CORE.DQ_CHECKS_CATALOG C JOIN CORE.CHECK_RESULT R ON R.CATALOG_CHECK_ID = C.CHECK_ID
     WHERE C.STATUS = 'ACTIVE' {where}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY C.CHECK_ID ORDER BY R.CREATED_AT DESC) = 1
), I AS (
    SELECT DAG_ID, INCIDENT_ID, SEVERITY, STATUS, ASSIGNEE, KIND, TEAM_ID FROM CORE.INCIDENT
     WHERE ENV_ID = 'dq' AND KIND = 'DQ_FAILED' AND STATUS IN ('OPEN', 'ACK', 'MITIGATED', 'MUTED')
    QUALIFY ROW_NUMBER() OVER (PARTITION BY DAG_ID ORDER BY LAST_SEEN DESC) = 1
)
SELECT F.*, D.DOMAIN_NAME, I.INCIDENT_ID, I.SEVERITY AS INCIDENT_SEVERITY, I.STATUS AS INCIDENT_STATUS, I.ASSIGNEE
  FROM F LEFT JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = F.DOMAIN_ID
  LEFT JOIN I ON I.DAG_ID = {dag_id}
 WHERE F.OUTCOME IN ('FAIL', 'ERROR')
 ORDER BY IFF(F.OUTCOME = 'FAIL', 0, 1), F.CREATED_AT DESC"""
# the incident's DAG_ID is the upper-cased table (services.quality.alerts.dag_id_for; tables over 250 characters
# carry a hash and are matched on their prefix)
_DAG_ID = "IFF(LENGTH(F.TABLE_NAME) <= 250, UPPER(F.TABLE_NAME), LEFT(UPPER(F.TABLE_NAME), 233) || '~' || LEFT(SHA1(UPPER(F.TABLE_NAME)), 16))"


@router.get("/api/dq/failing")
def failing_checks(domain: Optional[str] = Query(default=None, max_length=36), mine: bool = False,
                   limit: int = Query(default=20, ge=1, le=200), db: Db = Depends(current_db)):
    """Checks failing now (FAIL or ERROR), newest first, each with its table's open data quality incident."""
    where, params = "", []
    if domain:
        where += " AND C.DOMAIN_ID = %s"
        params.append(domain)
    if mine:
        where += f" AND C.DOMAIN_ID IN {_MY_DOMAINS}"
        params += [db.user, db.user]
    try:
        rows = db.query(_FAILING.format(where=where, dag_id=_DAG_ID), tuple(params))
    except Exception as exc:
        if "does not exist" in str(exc).lower():
            return {"checks": [], "total": 0, "tables": 0, "incidents": 0, "deployed": False}
        raise
    out = [{"check_id": r["check_id"], "table": r["table_name"], "column": r.get("column_name"),
            "check_type": r.get("check_type"), "outcome": r["outcome"], "measured": r.get("measured"),
            "threshold": r.get("threshold"), "failed_rows": r.get("failed_rows"), "at": _iso(r.get("created_at")),
            "domain_id": r.get("domain_id"), "domain_name": r.get("domain_name"),
            "incident": ({"incident_id": r["incident_id"], "severity": r.get("incident_severity"),
                          "status": r.get("incident_status"), "assignee": r.get("assignee")}
                         if r.get("incident_id") else None),
            "href": alerts.suite_link(None, r["table_name"])} for r in rows]
    return {"checks": out[:limit], "total": len(out), "tables": len({c["table"] for c in out}),
            "incidents": len({c["incident"]["incident_id"] for c in out if c["incident"]}), "deployed": True}


@router.get("/api/dq/digests")
def digests(domain: Optional[str] = Query(default=None, max_length=36), mine: bool = False,
            days: int = Query(default=1, ge=1, le=30), db: Db = Depends(current_db)):
    """The newest daily digest of each domain (or the last `days` days of them)."""
    where, params = ["DIGEST_DATE >= DATEADD(day, %s, CURRENT_DATE())"], [-days]
    if domain:
        where.append("DOMAIN_ID = %s")
        params.append(domain)
    if mine:
        where.append(f"DOMAIN_ID IN {_MY_DOMAINS}")
        params += [db.user, db.user]
    qualify = "" if days > 1 else " QUALIFY ROW_NUMBER() OVER (PARTITION BY DOMAIN_ID ORDER BY DIGEST_DATE DESC) = 1"
    rows = _soft(db, f"""SELECT DOMAIN_ID, DIGEST_DATE, TEAM_ID, OWNER, PASS_RATE, NEW_FAILING, CONTENT, CREATED_AT
                           FROM CORE.DQ_DIGEST WHERE {' AND '.join(where)}{qualify}
                          ORDER BY DIGEST_DATE DESC, DOMAIN_ID LIMIT 200""", tuple(params))
    return {"digests": [{**(_json(r.get("content")) or {}), "domain_id": r["domain_id"], "date": str(r.get("digest_date")),
                         "team_id": r.get("team_id"), "created_at": _iso(r.get("created_at"))} for r in rows]}


# ---------------------------------------------------------------- routes (domain -> support team)

class RouteIn(BaseModel):
    team_id: Optional[str] = Field(default=None, max_length=64)
    fail_severity: str = Field(default="P2", max_length=2)
    warn_after_runs: int = Field(default=3, ge=0, le=100)
    stale_grace_minutes: int = Field(default=120, ge=0, le=10080)
    digest_enabled: bool = True
    digest_jira: bool = False


def _route_out(r: Dict[str, Any]) -> Dict[str, Any]:
    route = alerts.route_for({r["domain_id"]: r}, r["domain_id"]) if r.get("configured") else dict(alerts.DEFAULT_ROUTE)
    return {"domain_id": r["domain_id"], "domain_name": r.get("domain_name"), "owner": r.get("owner"),
            "team_name": r.get("team_name"), "configured": bool(r.get("configured")),
            **{k: route[k] for k in alerts.DEFAULT_ROUTE}}


_ROUTES_SQL = """SELECT D.DOMAIN_ID, D.DOMAIN_NAME, D.OWNER, R.TEAM_ID, R.FAIL_SEVERITY, R.WARN_AFTER_RUNS,
                        R.STALE_GRACE_MINUTES, R.DIGEST_ENABLED, R.DIGEST_JIRA, T.NAME AS TEAM_NAME,
                        R.DOMAIN_ID IS NOT NULL AS CONFIGURED
                   FROM CORE.DOMAIN_REGISTRY D LEFT JOIN CORE.DQ_ALERT_ROUTE R ON R.DOMAIN_ID = D.DOMAIN_ID
                   LEFT JOIN CORE.TEAM T ON T.TEAM_ID = R.TEAM_ID
                  WHERE COALESCE(D.ACTIVE_FLAG, TRUE)"""


@router.get("/api/ops/dq-alerting/routes")
def list_routes(db: Db = Depends(current_db)):
    return {"routes": [_route_out(r) for r in _soft(db, _ROUTES_SQL + " ORDER BY D.DOMAIN_NAME")],
            "defaults": dict(alerts.DEFAULT_ROUTE)}


@router.put("/api/ops/dq-alerting/routes/{domain_id}")
def put_route(domain_id: str, body: RouteIn, db: Db = Depends(current_db)):
    if not _q(db, "SELECT 1 AS X FROM CORE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,)):
        raise HTTPException(404, f"Domain {domain_id} not found")
    team = (body.team_id or "").strip() or None
    if team and not _q(db, "SELECT 1 AS X FROM CORE.TEAM WHERE TEAM_ID = %s", (team,)):
        raise HTTPException(422, f"Team {team} not found")
    severity = body.fail_severity.upper()
    if severity not in SEVERITIES:
        raise HTTPException(422, "fail_severity must be P1, P2, P3 or P4")
    values = (team, severity, body.warn_after_runs, body.stale_grace_minutes, body.digest_enabled, body.digest_jira)
    _x(db, """MERGE INTO CORE.DQ_ALERT_ROUTE T USING (SELECT %s AS DOMAIN_ID) S ON T.DOMAIN_ID = S.DOMAIN_ID
              WHEN MATCHED THEN UPDATE SET TEAM_ID = %s, FAIL_SEVERITY = %s, WARN_AFTER_RUNS = %s, STALE_GRACE_MINUTES = %s,
                   DIGEST_ENABLED = %s, DIGEST_JIRA = %s, UPDATED_BY = CURRENT_USER(), UPDATED_AT = CURRENT_TIMESTAMP()
              WHEN NOT MATCHED THEN INSERT (DOMAIN_ID, TEAM_ID, FAIL_SEVERITY, WARN_AFTER_RUNS, STALE_GRACE_MINUTES,
                   DIGEST_ENABLED, DIGEST_JIRA) VALUES (S.DOMAIN_ID, %s, %s, %s, %s, %s, %s)""",
       (domain_id, *values, *values))
    found = _q(db, _ROUTES_SQL + " AND D.DOMAIN_ID = %s", (domain_id,))
    return _route_out(found[0])


@router.post("/api/ops/dq-alerting/run")
def run_now(db: Db = Depends(current_db)):
    """Evaluate new scans and missed schedules now (the worker does it every 2 minutes)."""
    store = alerts.DqStore(db)
    settings = store.settings()
    return {"scans": alerts.process_scans(store, settings), "schedules": alerts.check_schedules(store, settings)}
