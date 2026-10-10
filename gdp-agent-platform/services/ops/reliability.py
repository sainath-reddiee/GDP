"""Reliability report: per team incidents, P1s, MTTR and MTTA, repeats, the most failing DAGs, repeat fingerprints and
the AI's cost; and the weekly Teams digest that carries it.

Definitions (period = the last `days` days, incidents counted by FIRST_SEEN, storm children left out):
  mttr_min  mean minutes from the incident's (last) opening to its resolution, over incidents resolved in the period
  mtta_min  mean minutes from opening to the first acknowledgement, over incidents acknowledged
  repeats   incidents that occurred more than once, or whose fingerprint already had an incident in the period
The digest is posted on Mondays from 09:00 UTC when the setting weekly_digest is on, once per team and ISO week (the
outbox dedupe key teams:digest:<team>:<year>-W<week> makes it idempotent across workers and restarts).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from services.ops.normalize import ts
from services.ops.redact import redact

DIGEST_WEEKDAY = 0      # Monday
DIGEST_HOUR = 9         # 09:00 UTC
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


def team_stats(incidents: List[Dict[str, Any]], teams: Dict[str, str]) -> List[Dict[str, Any]]:
    """[{team_id, name, incidents, p1, mttr_min, mtta_min, repeats}] from incident rows (lower-case OPS.INCIDENT
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
                               FROM OPS.INCIDENT I JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = I.ENV_ID
                              WHERE {where} LIMIT 20000""", tuple(params))
    team_rows = db.query("SELECT TEAM_ID, NAME FROM OPS.TEAM" + (" WHERE TEAM_ID = %s" if team_id else ""),
                         (team_id,) if team_id else ())
    teams = {t["team_id"]: t["name"] for t in team_rows}
    run_params: List[Any] = [start.isoformat()]
    run_where = "COALESCE(R.STARTED_AT, R.LOGICAL_DATE) >= TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)"
    if team_id:
        run_where += " AND D.TEAM_ID = %s"
        run_params.append(team_id)
    runs = db.query(f"""SELECT R.ENV_ID, R.DAG_ID, COUNT_IF(R.STATE = 'success') AS OK, COUNT_IF(R.STATE = 'failed') AS FAILED
                          FROM OPS.DAG_RUN R JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = R.ENV_ID
                          LEFT JOIN OPS.DAG D ON D.ENV_ID = R.ENV_ID AND D.DAG_ID = R.DAG_ID
                         WHERE {run_where} GROUP BY R.ENV_ID, R.DAG_ID""", tuple(run_params))
    ai = {"diagnoses": 0, "cost_usd": 0.0}
    try:
        found = db.query(f"""SELECT COUNT(*) AS N FROM OPS.INCIDENT_EVENT V
                              {"JOIN OPS.INCIDENT I ON I.INCIDENT_ID = V.INCIDENT_ID" if team_id else ""}
                             WHERE V.KIND = 'diagnosed' AND V.CREATED_AT >= TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)
                             {"AND I.TEAM_ID = %s" if team_id else ""}""", tuple(params[:2]) if team_id else (params[0],))
        ai["diagnoses"] = int(found[0]["n"] or 0) if found else 0
        cost = db.query("""SELECT COALESCE(SUM(ESTIMATED_COST), 0) AS USD FROM AUDIT.COST_USAGE
                            WHERE STAGE = 'OPS' AND CREATED_AT >= TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)""", (params[0],))
        ai["cost_usd"] = round(float(cost[0]["usd"] or 0), 4) if cost else 0.0
    except Exception:
        pass
    stats = team_stats(incidents, teams)
    if team_id:
        stats = [t for t in stats if t["team_id"] == team_id]
    return {"period": {"from": start.isoformat(timespec="seconds"), "to": now.isoformat(timespec="seconds"), "days": days},
            "teams": stats, "top_dags": top_dags(runs), "repeats": repeat_fingerprints(incidents), "ai": ai}


# ---------------------------------------------------------------- weekly digest

def iso_week(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def digest_due(now: datetime, settings: Dict[str, Any]) -> bool:
    return bool(settings.get("weekly_digest")) and now.weekday() == DIGEST_WEEKDAY and now.hour >= DIGEST_HOUR


def digest_key(team_id: str, now: datetime) -> str:
    return f"teams:digest:{team_id}:{iso_week(now)}"


def _fmt(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.0f} min"


def digest_card(team_name: str, data: Dict[str, Any], base: Optional[str]) -> Dict[str, Any]:
    from services.ops.notify import _message

    team = (data.get("teams") or [{}])[0] if data.get("teams") else {}
    facts = [{"title": "Incidents", "value": str(team.get("incidents", 0))}, {"title": "P1", "value": str(team.get("p1", 0))},
             {"title": "MTTR", "value": _fmt(team.get("mttr_min"))}, {"title": "MTTA", "value": _fmt(team.get("mtta_min"))},
             {"title": "Repeats", "value": str(team.get("repeats", 0))},
             {"title": "AI diagnoses", "value": f"{data['ai']['diagnoses']} (about ${data['ai']['cost_usd']:.2f})"}]
    body: List[Dict[str, Any]] = [
        {"type": "TextBlock", "text": f"Weekly reliability: {redact(team_name)[:120]}", "weight": "Bolder", "size": "Medium", "wrap": True},
        {"type": "TextBlock", "text": f"{data['period']['from'][:10]} to {data['period']['to'][:10]}", "isSubtle": True, "wrap": True},
        {"type": "FactSet", "facts": facts},
    ]
    if data.get("top_dags"):
        body.append({"type": "TextBlock", "wrap": True, "text": "Most failing DAGs:\n" + "\n".join(
            f"- {redact(d['dag_id'])[:120]} ({d['env_id']}): {d['failures']} failures" for d in data["top_dags"][:5])})
    if data.get("repeats"):
        body.append({"type": "TextBlock", "wrap": True, "text": "Repeat failures:\n" + "\n".join(
            f"- {redact(r['title'])[:150]} ({r['count']}x)" for r in data["repeats"][:5])})
    root = (base or "").rstrip("/")
    return _message(body, [{"type": "Action.OpenUrl", "title": "Open incidents", "url": f"{root}/incidents"}] if root else [])


def run_digest(db: Any, now: Optional[datetime] = None) -> Dict[str, int]:
    """Queue this week's digest card for every team with a Teams webhook (once per ISO week)."""
    from services.ops.incidents import SqlStore
    from services.ops.notify import enqueue

    now = now or datetime.now(timezone.utc)
    settings = SqlStore(db).settings()
    if not digest_due(now, settings):
        return {"queued": 0}
    queued = 0
    for team in db.query("SELECT TEAM_ID, NAME FROM OPS.TEAM WHERE TEAMS_WEBHOOK_SECRET IS NOT NULL ORDER BY TEAM_ID"):
        key = digest_key(team["team_id"], now)
        if db.query("SELECT 1 AS X FROM OPS.NOTIFICATION WHERE DEDUPE_KEY = %s LIMIT 1", (key,)):
            continue
        data = report(db, 7, team["team_id"], now)
        if enqueue(db, "TEAMS", "digest", key, digest_card(team["name"], data, settings.get("public_base_url")),
                   team_id=team["team_id"], target="alerts"):
            queued += 1
    return {"queued": queued}
