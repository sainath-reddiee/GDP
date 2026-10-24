"""Reliability report: per team incidents, P1s, MTTR and MTTA, repeats, the most failing DAGs, repeat fingerprints and
the AI's cost.

Definitions (period = the last `days` days, incidents counted by FIRST_SEEN, storm children left out):
  mttr_min  mean minutes from the incident's (last) opening to its resolution, over incidents resolved in the period
  mtta_min  mean minutes from opening to the first acknowledgement, over incidents acknowledged
  repeats   incidents that occurred more than once, or whose fingerprint already had an incident in the period
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from services.ops.normalize import ts
from services.ops.redact import redact

UNROUTED = "unrouted"


def _dt(value: Any) -> Optional[datetime]:
    iso = ts(value)
    return datetime.fromisoformat(iso) if iso else None


def _minutes(a: Any, b: Any) -> Optional[float]:
    start, end = _dt(a), _dt(b)
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds() / 60.0


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    found = [v for v in values if v is not None]
    return round(sum(found) / len(found), 1) if found else None


def _price(value: Any) -> Optional[float]:
    """The credit price in USD from its config value (a number, or JSON text of one); None when unset or not a number."""
    if isinstance(value, str):
        value = value.strip().strip('"')
    try:
        return None if value in (None, "", "null") else float(value)
    except (TypeError, ValueError):
        return None


def team_stats(incidents: List[Dict[str, Any]], teams: Dict[str, str]) -> List[Dict[str, Any]]:
    """[{team_id, name, incidents, p1, mttr_min, mtta_min, repeats}] from incident rows (lower-case CORE.INCIDENT
    columns), one entry per team that has incidents (and every named team), most incidents first."""
    rows = sorted([i for i in incidents if not i.get("parent_incident_id")], key=lambda i: ts(i.get("first_seen")) or "")
    seen_fp: Dict[str, int] = {}
    repeat_ids = set()
    for i in rows:
        fp = i.get("fingerprint")
        if int(i.get("occurrences") or 1) > 1 or (fp and seen_fp.get(fp)):
            repeat_ids.add(i.get("incident_id"))
        if fp:
            seen_fp[fp] = seen_fp.get(fp, 0) + 1
    by_team: Dict[str, List[Dict[str, Any]]] = {}
    for i in rows:
        by_team.setdefault(i.get("team_id") or UNROUTED, []).append(i)
    out = []
    for team_id in sorted(set(by_team) | set(teams)):
        items = by_team.get(team_id, [])
        out.append({
            "team_id": None if team_id == UNROUTED else team_id,
            "name": teams.get(team_id) or ("Unrouted" if team_id == UNROUTED else team_id),
            "incidents": len(items),
            "p1": sum(1 for i in items if str(i.get("severity")) == "P1"),
            "mttr_min": _mean(_minutes(i.get("opened_at") or i.get("first_seen"), i.get("resolved_at")) for i in items),
            "mtta_min": _mean(_minutes(i.get("opened_at") or i.get("first_seen"), i.get("acked_at")) for i in items),
            "repeats": sum(1 for i in items if i.get("incident_id") in repeat_ids),
        })
    out.sort(key=lambda t: (-t["incidents"], str(t["name"])))
    return out


def repeat_fingerprints(incidents: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    """[{fingerprint, title, count}] where count is every occurrence across the period's incidents (> 1 only)."""
    found: Dict[str, Dict[str, Any]] = {}
    for i in incidents:
        fp = i.get("fingerprint")
        if not fp:
            continue
        entry = found.setdefault(fp, {"fingerprint": fp, "title": redact(str(i.get("title") or ""))[:300], "count": 0})
        entry["count"] += int(i.get("occurrences") or 1)
    repeats = [e for e in found.values() if e["count"] > 1]
    repeats.sort(key=lambda e: (-e["count"], e["title"]))
    return repeats[:limit]


def top_dags(runs: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    """[{env_id, dag_id, failures, success_rate}] from per-DAG counts {env_id, dag_id, ok, failed}, most failures first."""
    out = []
    for r in runs:
        ok, failed = int(r.get("ok") or 0), int(r.get("failed") or 0)
        if failed <= 0:
            continue
        out.append({"env_id": r["env_id"], "dag_id": r["dag_id"], "failures": failed,
                    "success_rate": round(ok / (ok + failed), 3) if ok + failed else None})
    out.sort(key=lambda d: (-d["failures"], d["success_rate"] if d["success_rate"] is not None else 1, d["dag_id"]))
    return out[:limit]


def report(db: Any, days: int = 7, team_id: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    days = max(1, min(int(days or 7), 90))
    start = now - timedelta(days=days)
    params: List[Any] = [start.isoformat()]
    where = "I.FIRST_SEEN >= TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR) AND I.PARENT_INCIDENT_ID IS NULL"
    if team_id:
        where += " AND I.TEAM_ID = %s"
        params.append(team_id)
    incidents = db.query(f"""SELECT I.INCIDENT_ID, I.FINGERPRINT, I.TEAM_ID, I.SEVERITY, I.TITLE, I.OCCURRENCES, I.FIRST_SEEN,
                                    I.OPENED_AT, I.RESOLVED_AT, I.ACKED_AT, I.PARENT_INCIDENT_ID
                               FROM CORE.INCIDENT I JOIN CORE.AIRFLOW_ENV E ON E.ENV_ID = I.ENV_ID
                              WHERE {where} LIMIT 20000""", tuple(params))
    team_rows = db.query("SELECT TEAM_ID, NAME FROM CORE.TEAM" + (" WHERE TEAM_ID = %s" if team_id else ""),
                         (team_id,) if team_id else ())
    teams = {t["team_id"]: t["name"] for t in team_rows}
    run_params: List[Any] = [start.isoformat()]
    run_where = "COALESCE(R.STARTED_AT, R.LOGICAL_DATE) >= TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)"
    if team_id:
        run_where += " AND D.TEAM_ID = %s"
        run_params.append(team_id)
    runs = db.query(f"""SELECT R.ENV_ID, R.DAG_ID, COUNT_IF(R.STATE = 'success') AS OK, COUNT_IF(R.STATE = 'failed') AS FAILED
                          FROM CORE.DAG_RUN R JOIN CORE.AIRFLOW_ENV E ON E.ENV_ID = R.ENV_ID
                          LEFT JOIN CORE.DAG D ON D.ENV_ID = R.ENV_ID AND D.DAG_ID = R.DAG_ID
                         WHERE {run_where} GROUP BY R.ENV_ID, R.DAG_ID""", tuple(run_params))
    ai: Dict[str, Any] = {"diagnoses": 0, "credits": 0.0, "credit_price_usd": None, "cost_usd": None}
    try:
        found = db.query(f"""SELECT COUNT(*) AS N FROM CORE.INCIDENT_EVENT V
                              {"JOIN CORE.INCIDENT I ON I.INCIDENT_ID = V.INCIDENT_ID" if team_id else ""}
                             WHERE V.KIND = 'diagnosed' AND V.CREATED_AT >= TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)
                             {"AND I.TEAM_ID = %s" if team_id else ""}""", tuple(params[:2]) if team_id else (params[0],))
        ai["diagnoses"] = int(found[0]["n"] or 0) if found else 0
        # COST_USAGE holds Snowflake credits (the reconciled actual when known, else the estimate), never dollars:
        # money needs the credit price an admin set
        cost = db.query("""SELECT COALESCE(SUM(COALESCE(ACTUAL_CREDITS, ESTIMATED_COST, 0)), 0) AS CREDITS
                             FROM CORE.COST_USAGE
                            WHERE STAGE IN ('OPS', 'RESOLVE') AND CREATED_AT >= TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)""",
                        (params[0],))
        ai["credits"] = round(float(cost[0]["credits"] or 0), 4) if cost else 0.0
    except Exception:
        pass
    try:   # the credit price an admin set (Admin, CREDIT_PRICE_USD), to show the credits as money
        found = db.query("""SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'CREDIT_PRICE_USD' AND IS_CURRENT
                             ORDER BY VERSION DESC LIMIT 1""")
        ai["credit_price_usd"] = _price(found[0].get("config_value")) if found else None
    except Exception:
        ai["credit_price_usd"] = None
    if ai["credit_price_usd"] is not None:
        ai["cost_usd"] = round(ai["credits"] * ai["credit_price_usd"], 2)
    stats = team_stats(incidents, teams)
    if team_id:
        stats = [t for t in stats if t["team_id"] == team_id]
    try:   # known fixes that keep firing: each one points at a cause worth repairing for good
        from services.resolve.publish import recurring

        recurring_fixes = recurring(db, days=max(days, 30))
    except Exception:
        recurring_fixes = []
    return {"period": {"from": start.isoformat(timespec="seconds"), "to": now.isoformat(timespec="seconds"), "days": days},
            "teams": stats, "top_dags": top_dags(runs), "repeats": repeat_fingerprints(incidents), "ai": ai,
            "recurring_fixes": recurring_fixes}
