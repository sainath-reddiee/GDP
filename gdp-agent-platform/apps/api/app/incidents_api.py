"""Ops incidents: the inbox, incident detail and actions, support teams with their Teams webhooks, routing rules and
ops settings (services.ops.incidents, notify and tickets do the work).

Governance (services.governance.policy): reads need OPS.VIEW; incident actions and bulk actions OPS.OPERATE; teams,
webhooks, test sends, routing rules and settings INTEGRATION.MANAGE. Calls run on the caller's session; the actor on the
timeline is the signed-in user.

Teams webhook URLs are stored ENCRYPTed with the API host's key (AIP_SECRET_KEY, else JIRA_TOKEN_KEY) and never returned:
teams only say has_teams_webhook and has_escalation_webhook. The request carrying a URL cannot be queued for approval.

PR O3 (AI root cause and support assistant): diagnose, ask and postmortem need AI.USE (services.ops.diagnose); retry and
the safe-to-retry mark need OPS.OPERATE (services.ops.retry; a governance policy on OPS.OPERATE makes them approvable,
off by default); impact and the reliability report need OPS.VIEW. A person's resolution with a note of 20 characters or
more is remembered as INCIDENT_RESOLUTION knowledge (services.ops.resolution).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _json, _set_config, current_db
from app.ops_api import _RUNS_SQL, _iso, _mwaa_http, _ops_error, _q, _run_out, _x, links, slug
from services.jira.client import JiraError, check_project
from services.ops import incidents as inc
from services.ops import notify, tickets
from services.ops.diagnose import ai_out
from services.ops.mwaa import MwaaError
from services.ops.detect import SEVERITIES, STATUSES
from services.ops.normalize import ts

router = APIRouter()
LIST_LIMIT = 200
BULK_MAX = 100
_GLOB = re.compile(r"^[A-Za-z0-9_.\-*?\[\]!]{1,250}$")


# ---------------------------------------------------------------- plumbing

def _store(db: Db) -> inc.SqlStore:
    return inc.SqlStore(db)


def _action_http(exc: inc.ActionError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _jira_site(db: Db) -> Optional[str]:
    return str(_store(db).jira_config().get("site_url") or "").rstrip("/") or None


def _csv(value: Optional[str], allowed: tuple, name: str) -> List[str]:
    items = [v.strip().upper() for v in (value or "").split(",") if v.strip()]
    bad = [v for v in items if v not in allowed]
    if bad:
        raise HTTPException(422, f"{name} must be among {', '.join(allowed)}")
    return items


def _iso_in(value: Optional[str], name: str) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    found = ts(value)
    if not found:
        raise HTTPException(422, f"{name} must be an ISO time, for example 2026-01-31T18:00:00Z")
    return found


# ---------------------------------------------------------------- inbox

_LIST_SELECT = """
SELECT I.INCIDENT_ID, I.FINGERPRINT, I.ENV_ID, I.DAG_ID, I.TASK_ID, I.MAP_INDEX, I.RUN_ID, I.KIND, I.STATUS, I.SEVERITY,
       I.TEAM_ID, T.NAME AS TEAM_NAME, I.ASSIGNEE, I.FIRST_SEEN, I.LAST_SEEN, I.OCCURRENCES, I.PARENT_INCIDENT_ID,
       COALESCE(C.N, 0) AS CHILDREN, I.JIRA_KEY, I.JIRA_STATE, I.TITLE, I.ERROR_EXCERPT, I.AI_SUMMARY, I.AI, I.RESOLUTION,
       I.RESOLVED_BY, I.RESOLVED_AT, I.ACKED_BY, I.ACKED_AT, I.MUTED_UNTIL, I.MUTE_REASON, I.ESCALATIONS,
       E.AIRFLOW_URL, E.API_VERSION
  FROM OPS.INCIDENT I
  JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = I.ENV_ID
  LEFT JOIN OPS.TEAM T ON T.TEAM_ID = I.TEAM_ID
  LEFT JOIN (SELECT PARENT_INCIDENT_ID, COUNT(*) AS N FROM OPS.INCIDENT WHERE PARENT_INCIDENT_ID IS NOT NULL
              GROUP BY PARENT_INCIDENT_ID) C ON C.PARENT_INCIDENT_ID = I.INCIDENT_ID"""


def _list_out(r: Dict[str, Any], site: Optional[str]) -> Dict[str, Any]:
    return {"incident_id": r["incident_id"], "fingerprint": r.get("fingerprint"), "env_id": r["env_id"], "dag_id": r["dag_id"],
            "task_id": r.get("task_id"), "run_id": r.get("run_id"), "kind": r.get("kind"), "status": r.get("status"),
            "severity": r.get("severity"), "team_id": r.get("team_id"), "team_name": r.get("team_name"),
            "assignee": r.get("assignee"), "first_seen": _iso(r.get("first_seen")), "last_seen": _iso(r.get("last_seen")),
            "occurrences": int(r.get("occurrences") or 1), "parent_incident_id": r.get("parent_incident_id"),
            "children": int(r.get("children") or 0), "jira_key": r.get("jira_key"),
            "jira_url": tickets.jira_url(site, r.get("jira_key")), "jira_state": r.get("jira_state"),
            "title": r.get("title"), "error_excerpt": r.get("error_excerpt"), "ai_summary": r.get("ai_summary")}


@router.get("/api/ops/incidents")
def list_incidents(status: Optional[str] = Query(default=None, max_length=200),
                   severity: Optional[str] = Query(default=None, max_length=40), team_id: Optional[str] = None,
                   env_id: Optional[str] = None, dag_id: Optional[str] = Query(default=None, max_length=250),
                   mine: bool = False, q: Optional[str] = Query(default=None, max_length=200),
                   limit: int = Query(default=50, ge=1, le=LIST_LIMIT), offset: int = Query(default=0, ge=0),
                   db: Db = Depends(current_db)):
    where: List[str] = []
    params: List[Any] = []
    if (status or "").strip().lower() != "any":
        statuses = _csv(status, STATUSES, "status") or ["OPEN", "ACK"]
        where.append(f"I.STATUS IN ({', '.join(['%s'] * len(statuses))})")
        params += statuses
    severities = _csv(severity, SEVERITIES, "severity")
    if severities:
        where.append(f"I.SEVERITY IN ({', '.join(['%s'] * len(severities))})")
        params += severities
    for column, value in (("I.TEAM_ID", team_id), ("I.ENV_ID", env_id), ("I.DAG_ID", dag_id)):
        if value:
            where.append(f"{column} = %s")
            params.append(value)
    if mine:
        where.append("UPPER(I.ASSIGNEE) = UPPER(%s)")
        params.append(db.user)
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append("(I.TITLE ILIKE %s OR I.DAG_ID ILIKE %s OR I.TASK_ID ILIKE %s OR I.ERROR_EXCERPT ILIKE %s)")
        params += [like] * 4
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = _q(db, _LIST_SELECT + clause + f""" ORDER BY I.SEVERITY, I.LAST_SEEN DESC NULLS LAST
                                              LIMIT {int(limit)} OFFSET {int(offset)}""", tuple(params))
    total = _q(db, "SELECT COUNT(*) AS N FROM OPS.INCIDENT I JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = I.ENV_ID" + clause, tuple(params))
    site = _jira_site(db)
    return {"incidents": [_list_out(r, site) for r in rows], "total": int(total[0]["n"] or 0) if total else 0}


@router.get("/api/ops/incidents/summary")
def incidents_summary(db: Db = Depends(current_db)):
    found = _q(db, """
        SELECT COUNT_IF(I.STATUS = 'OPEN') AS OPEN_N, COUNT_IF(I.STATUS = 'ACK') AS ACK_N,
               COUNT_IF(I.STATUS = 'OPEN' AND I.SEVERITY = 'P1') AS P1_OPEN,
               COUNT_IF(I.STATUS IN ('OPEN', 'ACK') AND UPPER(I.ASSIGNEE) = UPPER(%s)) AS MINE_OPEN,
               COUNT_IF(I.STATUS IN ('OPEN', 'ACK') AND I.TEAM_ID IS NULL AND I.PARENT_INCIDENT_ID IS NULL) AS UNROUTED
          FROM OPS.INCIDENT I JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = I.ENV_ID
         WHERE I.STATUS IN ('OPEN', 'ACK')""", (db.user,))
    row = found[0] if found else {}
    return {"open": int(row.get("open_n") or 0), "ack": int(row.get("ack_n") or 0), "p1_open": int(row.get("p1_open") or 0),
            "mine_open": int(row.get("mine_open") or 0), "unrouted": int(row.get("unrouted") or 0)}


def _detail(db: Db, incident_id: str) -> Dict[str, Any]:
    found = _q(db, _LIST_SELECT + " WHERE I.INCIDENT_ID = %s", (incident_id,))
    if not found:
        raise HTTPException(404, f"Incident {incident_id} not found")
    r = found[0]
    site = _jira_site(db)
    incident = {**_list_out(r, site), "map_index": r.get("map_index"), "ai": ai_out(_json(r.get("ai"))),
                "resolution": r.get("resolution"), "resolved_by": r.get("resolved_by"), "resolved_at": _iso(r.get("resolved_at")),
                "acked_by": r.get("acked_by"), "acked_at": _iso(r.get("acked_at")),
                "airflow_url": links(r.get("airflow_url"), r.get("api_version"), r["dag_id"], r.get("run_id")),
                "muted_until": _iso(r.get("muted_until")), "mute_reason": r.get("mute_reason"),
                "escalations": int(r.get("escalations") or 0)}
    events = _q(db, """SELECT EVENT_ID, KIND, ACTOR, DETAIL, CREATED_AT FROM OPS.INCIDENT_EVENT
                        WHERE INCIDENT_ID = %s AND KIND <> 'claim' ORDER BY CREATED_AT, EVENT_ID LIMIT 500""", (incident_id,))
    notes = _q(db, """SELECT NOTIFICATION_ID, CHANNEL, KIND, STATUS, ATTEMPTS, ERROR, CREATED_AT, SENT_AT FROM OPS.NOTIFICATION
                       WHERE INCIDENT_ID = %s ORDER BY CREATED_AT DESC LIMIT 100""", (incident_id,))
    runs = _q(db, _RUNS_SQL.format(extra="", limit=20), (r["env_id"], r["dag_id"], r["env_id"], r["dag_id"]))
    children = _q(db, """SELECT INCIDENT_ID, DAG_ID, TASK_ID, STATUS FROM OPS.INCIDENT WHERE PARENT_INCIDENT_ID = %s
                          ORDER BY FIRST_SEEN LIMIT 200""", (incident_id,))
    return {
        "incident": incident,
        "events": [{"event_id": e["event_id"], "kind": e["kind"], "actor": e.get("actor"),
                    "detail": _json(e.get("detail")) if e.get("detail") is not None else None,
                    "created_at": _iso(e.get("created_at"))} for e in events],
        "notifications": [{"notification_id": n["notification_id"], "channel": n["channel"], "kind": n["kind"],
                           "status": n["status"], "attempts": int(n.get("attempts") or 0), "error": n.get("error"),
                           "created_at": _iso(n.get("created_at")), "sent_at": _iso(n.get("sent_at"))} for n in notes],
        "runs": [_run_out(x) for x in runs],
        "children": [{"incident_id": c["incident_id"], "dag_id": c["dag_id"], "task_id": c.get("task_id"),
                      "status": c["status"]} for c in children],
    }


@router.get("/api/ops/incidents/{incident_id}")
def get_incident(incident_id: str, db: Db = Depends(current_db)):
    return _detail(db, incident_id)


# ---------------------------------------------------------------- actions

class AssignIn(BaseModel):
    assignee: Optional[str] = Field(default=None, max_length=256)


class ResolveIn(BaseModel):
    resolution: str = Field(min_length=3, max_length=4000)


class MuteIn(BaseModel):
    until: str = Field(min_length=10, max_length=40)
    reason: Optional[str] = Field(default=None, max_length=500)


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _act(db: Db, incident_id: str, fn) -> Dict[str, Any]:
    try:
        fn(_store(db))
    except inc.ActionError as exc:
        raise _action_http(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _ops_error(exc) from exc
    return _detail(db, incident_id)


@router.post("/api/ops/incidents/{incident_id}/ack")
def ack_incident(incident_id: str, db: Db = Depends(current_db)):
    return _act(db, incident_id, lambda s: inc.ack(s, incident_id, db.user))


@router.post("/api/ops/incidents/{incident_id}/reopen")
def reopen_incident(incident_id: str, db: Db = Depends(current_db)):
    return _act(db, incident_id, lambda s: inc.reopen(s, incident_id, db.user))


@router.post("/api/ops/incidents/{incident_id}/assign")
def assign_incident(incident_id: str, body: AssignIn, db: Db = Depends(current_db)):
    return _act(db, incident_id, lambda s: inc.assign(s, incident_id, body.assignee, db.user))


def _learn(db: Db, incident_id: str) -> None:
    """Remember a person's resolution as INCIDENT_RESOLUTION knowledge (written by the service identity when there is
    one, since engineers' roles may not write knowledge; the person is the recorded author); never fails the resolve."""
    from app.db import SnowflakeSessionError, system_db
    from services.ops.resolution import remember_resolution

    try:
        writer = system_db()
    except SnowflakeSessionError:
        writer = db
    if remember_resolution(writer, incident_id, db.user) is None and writer is not db:
        remember_resolution(db, incident_id, db.user)


@router.post("/api/ops/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str, body: ResolveIn, db: Db = Depends(current_db)):
    out = _act(db, incident_id, lambda s: inc.resolve(s, incident_id, body.resolution, db.user))
    _learn(db, incident_id)
    return out


@router.post("/api/ops/incidents/{incident_id}/mute")
def mute_incident(incident_id: str, body: MuteIn, db: Db = Depends(current_db)):
    until = _iso_in(body.until, "until")
    return _act(db, incident_id, lambda s: inc.mute(s, incident_id, until, body.reason, db.user))


@router.post("/api/ops/incidents/{incident_id}/comment")
def comment_incident(incident_id: str, body: CommentIn, db: Db = Depends(current_db)):
    return _act(db, incident_id, lambda s: inc.comment(s, incident_id, body.text, db.user))


@router.post("/api/ops/incidents/{incident_id}/ticket")
def ticket_incident(incident_id: str, db: Db = Depends(current_db)):
    """Raise the Jira ticket now (or retry a failed one) with the bot."""
    def work(s: inc.SqlStore) -> None:
        if not s.get(incident_id):
            raise inc.ActionError(f"Incident {incident_id} not found", 404)
        try:
            tickets.raise_ticket(s, incident_id, tickets.bot_client(s.jira_config()), s.settings(), actor=db.user)
        except JiraError as exc:
            raise HTTPException(502, f"Jira refused the ticket: {exc.message[:300]}") from exc

    return _act(db, incident_id, work)


# ---------------------------------------------------------------- AI root cause, ask, postmortem, impact (PR O3)

class DiagnoseIn(BaseModel):
    force: bool = False


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


def _ai_call(fn):
    from services.ops.diagnose import DiagnoseError

    try:
        return fn()
    except DiagnoseError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except HTTPException:
        raise
    except AssertionError as exc:   # Cortex returned no structured answer
        raise HTTPException(502, str(exc)[:300]) from exc
    except Exception as exc:
        raise _ops_error(exc) from exc


@router.post("/api/ops/incidents/{incident_id}/diagnose")
def diagnose_incident(incident_id: str, body: Optional[DiagnoseIn] = None, db: Db = Depends(current_db)):
    """The AI diagnosis, reused while the fingerprint is unchanged unless force is set."""
    from services.ops.diagnose import diagnose

    result = _ai_call(lambda: diagnose(db, incident_id, force=bool(body and body.force), actor=db.user))
    return {"ai": ai_out(result["ai"])}


@router.post("/api/ops/incidents/{incident_id}/ask")
def ask_incident(incident_id: str, body: AskIn, db: Db = Depends(current_db)):
    from services.ops.diagnose import ask

    return _ai_call(lambda: ask(db, incident_id, body.question, actor=db.user))


@router.post("/api/ops/incidents/{incident_id}/postmortem")
def postmortem_incident(incident_id: str, db: Db = Depends(current_db)):
    from services.ops.diagnose import postmortem

    return _ai_call(lambda: postmortem(db, incident_id, actor=db.user))


@router.get("/api/ops/incidents/{incident_id}/impact")
def incident_impact(incident_id: str, db: Db = Depends(current_db)):
    """Models, tables, domains, STTMs and QA affected by the incident's task (code graph; no AI)."""
    from services.ops.context import impact, load_incident

    incident = _ai_call(lambda: load_incident(db, incident_id))
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")
    return impact(db, incident)


# ---------------------------------------------------------------- retry in Airflow (PR O3)

class RetryIn(BaseModel):
    dry_run: bool = True
    downstream: bool = False
    task_ids: Optional[List[str]] = Field(default=None, max_length=50)
    preview_token: Optional[str] = Field(default=None, max_length=4000)
    override_reason: Optional[str] = Field(default=None, max_length=1000)


class RetrySafetyIn(BaseModel):
    safe_to_retry: Literal["yes", "no", "after_fix"]
    reason: Optional[str] = Field(default=None, max_length=500)


def _retry_call(fn):
    from services.ops.retry import RetryError

    try:
        return fn()
    except RetryError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except MwaaError as exc:
        raise _mwaa_http(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _ops_error(exc) from exc


@router.post("/api/ops/incidents/{incident_id}/retry")
def retry_incident(incident_id: str, body: RetryIn, db: Db = Depends(current_db),
                   x_aip_replay: Optional[str] = Header(default=None)):
    """Clear the failed task instances in Airflow: a dry run returns what would be cleared and a preview_token; the
    real retry ({dry_run: false, preview_token, override_reason?}) clears exactly what was previewed."""
    from app.governance import _valid_replay
    from services.ops.retry import retry

    if body.task_ids is not None and any(not t.strip() or len(t) > 250 for t in body.task_ids):
        raise HTTPException(422, "task_ids must be Airflow task ids")
    replay = bool(_valid_replay(x_aip_replay))
    return _retry_call(lambda: retry(db, incident_id, dry_run=body.dry_run, user=db.user, downstream=body.downstream,
                                     task_ids=body.task_ids, preview_token=body.preview_token,
                                     override_reason=body.override_reason, replay=replay))


@router.post("/api/ops/incidents/{incident_id}/retry-safety")
def mark_retry_safety(incident_id: str, body: RetrySafetyIn, db: Db = Depends(current_db)):
    """A person's own safe-to-retry call; it wins over the AI's."""
    from services.ops.retry import mark

    _retry_call(lambda: mark(db, incident_id, body.safe_to_retry, body.reason, db.user))
    return _detail(db, incident_id)


# ---------------------------------------------------------------- reliability (PR O3)

@router.get("/api/ops/reliability")
def reliability(days: int = Query(default=7, ge=1, le=90), team_id: Optional[str] = Query(default=None, max_length=64),
                db: Db = Depends(current_db)):
    from services.ops.reliability import report

    try:
        return report(db, days, team_id or None)
    except Exception as exc:
        raise _ops_error(exc) from exc


class BulkIn(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=BULK_MAX)
    action: Literal["ack", "assign", "resolve"]
    assignee: Optional[str] = Field(default=None, max_length=256)
    resolution: Optional[str] = Field(default=None, max_length=4000)


@router.post("/api/ops/incidents/bulk")
def bulk_incidents(body: BulkIn, db: Db = Depends(current_db)):
    if body.action == "resolve" and len((body.resolution or "").strip()) < 3:
        raise HTTPException(422, "Describe the resolution (at least 3 characters).")
    store = _store(db)
    results = []
    for incident_id in dict.fromkeys(body.ids):
        try:
            if body.action == "ack":
                inc.ack(store, incident_id, db.user)
            elif body.action == "assign":
                inc.assign(store, incident_id, body.assignee, db.user)
            else:
                inc.resolve(store, incident_id, body.resolution or "", db.user)
                _learn(db, incident_id)
            results.append({"incident_id": incident_id, "ok": True})
        except inc.ActionError as exc:
            results.append({"incident_id": incident_id, "ok": False, "error": str(exc)})
        except Exception as exc:
            results.append({"incident_id": incident_id, "ok": False, "error": _ops_error(exc).detail})
    return {"results": results}


# ---------------------------------------------------------------- teams

class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    jira_project: Optional[str] = Field(default=None, max_length=32)
    jira_component: Optional[str] = Field(default=None, max_length=255)
    jira_assignee_account_id: Optional[str] = Field(default=None, max_length=128)
    escalation_minutes: int = Field(default=30, ge=1, le=1440)
    members: List[str] = Field(default_factory=list, max_length=200)


class TeamPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    jira_project: Optional[str] = Field(default=None, max_length=32)
    jira_component: Optional[str] = Field(default=None, max_length=255)
    jira_assignee_account_id: Optional[str] = Field(default=None, max_length=128)
    escalation_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    members: Optional[List[str]] = Field(default=None, max_length=200)


def _team_values(values: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(values)
    for key in ("name", "jira_component", "jira_assignee_account_id"):
        if key in out and isinstance(out[key], str):
            out[key] = out[key].strip() or None
    if out.get("jira_project"):
        try:
            out["jira_project"] = check_project(out["jira_project"])
        except ValueError:
            raise HTTPException(422, "jira_project must be a Jira project key, for example OPS") from None
    elif "jira_project" in out:
        out["jira_project"] = None
    if out.get("members") is not None:
        out["members"] = list(dict.fromkeys(m.strip()[:256] for m in out["members"] if m and m.strip()))
    return out


def _team_out(r: Dict[str, Any]) -> Dict[str, Any]:
    members = _json(r.get("members"))
    return {"team_id": r["team_id"], "name": r["name"], "jira_project": r.get("jira_project"),
            "jira_component": r.get("jira_component"), "jira_assignee_account_id": r.get("jira_assignee_account_id"),
            "has_teams_webhook": bool(r.get("has_teams_webhook")),
            "escalation_minutes": int(r.get("escalation_minutes") or inc.DEFAULT_ESCALATION_MINUTES),
            "has_escalation_webhook": bool(r.get("has_escalation_webhook")),
            "members": members if isinstance(members, list) else []}


_TEAM_SELECT = """SELECT TEAM_ID, NAME, JIRA_PROJECT, JIRA_COMPONENT, JIRA_ASSIGNEE_ACCOUNT_ID,
                         TEAMS_WEBHOOK_SECRET IS NOT NULL AS HAS_TEAMS_WEBHOOK, ESCALATION_MINUTES,
                         ESCALATION_WEBHOOK_SECRET IS NOT NULL AS HAS_ESCALATION_WEBHOOK, MEMBERS FROM OPS.TEAM"""


def _team(db: Db, team_id: str) -> Dict[str, Any]:
    found = _q(db, _TEAM_SELECT + " WHERE TEAM_ID = %s", (team_id,))
    if not found:
        raise HTTPException(404, f"Team {team_id} not found")
    return _team_out(found[0])


@router.get("/api/ops/teams")
def list_teams(db: Db = Depends(current_db)):
    return {"teams": [_team_out(r) for r in _q(db, _TEAM_SELECT + " ORDER BY NAME")]}


@router.post("/api/ops/teams")
def create_team(body: TeamIn, db: Db = Depends(current_db)):
    values = _team_values(body.model_dump())
    if not values.get("name"):
        raise HTTPException(422, "name is required")
    base = slug(values["name"])
    taken = {r["team_id"] for r in _q(db, "SELECT TEAM_ID FROM OPS.TEAM WHERE TEAM_ID LIKE %s", (base + "%",))}
    team_id = next((c for c in [base] + [f"{base}-{i}" for i in range(2, 100)] if c not in taken), None)
    if not team_id:
        raise HTTPException(409, "Too many teams with this name; choose another name")
    _x(db, """INSERT INTO OPS.TEAM (TEAM_ID, NAME, JIRA_PROJECT, JIRA_COMPONENT, JIRA_ASSIGNEE_ACCOUNT_ID, ESCALATION_MINUTES, MEMBERS)
              SELECT %s, %s, %s, %s, %s, %s, PARSE_JSON(%s)""",
       (team_id, values["name"], values.get("jira_project"), values.get("jira_component"),
        values.get("jira_assignee_account_id"), values["escalation_minutes"], json.dumps(values.get("members") or [])))
    return _team(db, team_id)


@router.put("/api/ops/teams/{team_id}")
def update_team(team_id: str, body: TeamPatch, db: Db = Depends(current_db)):
    _team(db, team_id)
    values = _team_values({k: getattr(body, k) for k in body.model_fields_set})
    if "name" in values and not values["name"]:
        raise HTTPException(422, "name cannot be empty")
    if "escalation_minutes" in values and values["escalation_minutes"] is None:
        raise HTTPException(422, "escalation_minutes cannot be empty")
    sets, params = [], []
    for key, value in values.items():
        if key == "members":
            sets.append("MEMBERS = PARSE_JSON(%s)")
            params.append(json.dumps(value or []))
        else:
            sets.append(f"{key.upper()} = %s")
            params.append(value)
    if sets:
        _x(db, f"UPDATE OPS.TEAM SET {', '.join(sets)}, UPDATED_AT = CURRENT_TIMESTAMP() WHERE TEAM_ID = %s",
           tuple(params) + (team_id,))
    return _team(db, team_id)


@router.delete("/api/ops/teams/{team_id}")
def delete_team(team_id: str, db: Db = Depends(current_db)):
    _team(db, team_id)
    used = _q(db, "SELECT COUNT(*) AS N FROM OPS.ROUTING_RULE WHERE TEAM_ID = %s", (team_id,))
    if used and int(used[0]["n"] or 0):
        raise HTTPException(409, f"{int(used[0]['n'])} routing rule(s) send incidents to this team; change or delete them first.")
    _x(db, "DELETE FROM OPS.TEAM WHERE TEAM_ID = %s", (team_id,))
    return {"deleted": True, "team_id": team_id}


class WebhookIn(BaseModel):
    kind: Literal["alerts", "escalation"] = "alerts"
    url: str = Field(min_length=10, max_length=2048)


def _key() -> str:
    key = notify.secret_key()
    if not key:
        raise HTTPException(409, "No encryption key on the API host: set AIP_SECRET_KEY (16+ random characters; "
                                 "JIRA_TOKEN_KEY is used when it is absent) and restart the API.")
    return key


@router.post("/api/ops/teams/{team_id}/webhook")
def set_webhook(team_id: str, body: WebhookIn, db: Db = Depends(current_db),
                x_aip_replay: Optional[str] = Header(default=None)):
    """Store a team's Teams webhook URL, encrypted. It is never shown again."""
    if x_aip_replay:
        raise HTTPException(403, "A webhook URL cannot be set through an approval; someone with INTEGRATION.MANAGE sets it directly.")
    try:
        url = notify.validate_webhook_url(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    key = _key()
    _team(db, team_id)
    column = notify.webhook_column(body.kind)
    _x(db, f"UPDATE OPS.TEAM SET {column} = ENCRYPT(%s, %s), UPDATED_AT = CURRENT_TIMESTAMP() WHERE TEAM_ID = %s",
       (url, key, team_id))
    return {"ok": True}


@router.delete("/api/ops/teams/{team_id}/webhook")
def delete_webhook(team_id: str, kind: Literal["alerts", "escalation"] = "alerts", db: Db = Depends(current_db)):
    _team(db, team_id)
    column = notify.webhook_column(kind)
    _x(db, f"UPDATE OPS.TEAM SET {column} = NULL, UPDATED_AT = CURRENT_TIMESTAMP() WHERE TEAM_ID = %s", (team_id,))
    return {"ok": True}


class TestIn(BaseModel):
    kind: Literal["alerts", "escalation"] = "alerts"


@router.post("/api/ops/teams/{team_id}/test")
def test_webhook(team_id: str, body: TestIn, db: Db = Depends(current_db)):
    """Send one test card now (not through the outbox)."""
    team = _team(db, team_id)
    key = _key()
    column = notify.webhook_column(body.kind)
    try:
        found = db.query(f"""SELECT IFF({column} IS NULL, NULL, TO_VARCHAR(DECRYPT({column}, %s), 'UTF-8')) AS U
                               FROM OPS.TEAM WHERE TEAM_ID = %s""", (key, team_id))
    except Exception:
        return {"ok": False, "detail": "The stored webhook cannot be decrypted with this host's key; set the URL again."}
    url = found[0].get("u") if found else None
    if not url:
        return {"ok": False, "detail": f"The team has no {body.kind} webhook."}
    settings = _store(db).settings()
    ok, detail = notify.send(url, notify.test_card(team["name"], body.kind, settings.get("public_base_url")))
    return {"ok": ok, "detail": "Test card sent." if ok else f"The webhook refused the card: {detail}"}


# ---------------------------------------------------------------- routing rules

class RuleIn(BaseModel):
    priority: int = Field(default=100, ge=0, le=100000)
    dag_pattern: Optional[str] = Field(default=None, max_length=250)
    tag: Optional[str] = Field(default=None, max_length=250)
    owner: Optional[str] = Field(default=None, max_length=250)
    env_id: Optional[str] = Field(default=None, max_length=64)
    team_id: Optional[str] = Field(default=None, max_length=64)
    severity_override: Optional[str] = Field(default=None, max_length=2)
    mute_until: Optional[str] = Field(default=None, max_length=40)
    mute_reason: Optional[str] = Field(default=None, max_length=500)
    enabled: bool = True


class RulePatch(BaseModel):
    priority: Optional[int] = Field(default=None, ge=0, le=100000)
    dag_pattern: Optional[str] = Field(default=None, max_length=250)
    tag: Optional[str] = Field(default=None, max_length=250)
    owner: Optional[str] = Field(default=None, max_length=250)
    env_id: Optional[str] = Field(default=None, max_length=64)
    team_id: Optional[str] = Field(default=None, max_length=64)
    severity_override: Optional[str] = Field(default=None, max_length=2)
    mute_until: Optional[str] = Field(default=None, max_length=40)
    mute_reason: Optional[str] = Field(default=None, max_length=500)
    enabled: Optional[bool] = None


def _rule_values(db: Db, values: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in values.items():
        out[key] = (value.strip() or None) if isinstance(value, str) else value
    if out.get("dag_pattern") and not _GLOB.match(out["dag_pattern"]):
        raise HTTPException(422, "dag_pattern is a glob on the DAG id, for example sales_* (letters, digits, _ . - * ? [ ])")
    if out.get("severity_override"):
        out["severity_override"] = out["severity_override"].upper()
        if out["severity_override"] not in SEVERITIES:
            raise HTTPException(422, "severity_override must be P1, P2, P3 or P4")
    if "mute_until" in out:
        out["mute_until"] = _iso_in(out.get("mute_until"), "mute_until")
    if out.get("team_id") and not _q(db, "SELECT 1 AS X FROM OPS.TEAM WHERE TEAM_ID = %s", (out["team_id"],)):
        raise HTTPException(422, f"Team {out['team_id']} not found")
    if out.get("env_id") and not _q(db, "SELECT 1 AS X FROM OPS.AIRFLOW_ENV WHERE ENV_ID = %s", (out["env_id"],)):
        raise HTTPException(422, f"Airflow environment {out['env_id']} not found")
    if "enabled" in out and out["enabled"] is None:
        raise HTTPException(422, "enabled cannot be empty")
    if "priority" in out and out["priority"] is None:
        raise HTTPException(422, "priority cannot be empty")
    return out


def _rule_out(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"rule_id": r["rule_id"], "priority": int(r.get("priority") or 0), "dag_pattern": r.get("dag_pattern"),
            "tag": r.get("tag"), "owner": r.get("owner"), "env_id": r.get("env_id"), "team_id": r.get("team_id"),
            "severity_override": r.get("severity_override"), "mute_until": _iso(r.get("mute_until")),
            "mute_reason": r.get("mute_reason"), "enabled": bool(r.get("enabled"))}


_RULE_SELECT = """SELECT RULE_ID, PRIORITY, DAG_PATTERN, TAG, OWNER, ENV_ID, TEAM_ID, SEVERITY_OVERRIDE, MUTE_UNTIL, MUTE_REASON,
                         ENABLED FROM OPS.ROUTING_RULE"""


def _rule(db: Db, rule_id: str) -> Dict[str, Any]:
    found = _q(db, _RULE_SELECT + " WHERE RULE_ID = %s", (rule_id,))
    if not found:
        raise HTTPException(404, f"Routing rule {rule_id} not found")
    return _rule_out(found[0])


@router.get("/api/ops/routing")
def list_rules(db: Db = Depends(current_db)):
    return {"rules": [_rule_out(r) for r in _q(db, _RULE_SELECT + " ORDER BY PRIORITY, CREATED_AT")]}


_RULE_COLUMNS = ("priority", "dag_pattern", "tag", "owner", "env_id", "team_id", "severity_override", "mute_until",
                 "mute_reason", "enabled")


@router.post("/api/ops/routing")
def create_rule(body: RuleIn, db: Db = Depends(current_db)):
    import uuid

    values = _rule_values(db, body.model_dump())
    rule_id = str(uuid.uuid4())
    cols = list(_RULE_COLUMNS)
    _x(db, f"""INSERT INTO OPS.ROUTING_RULE (RULE_ID, {', '.join(c.upper() for c in cols)})
               SELECT %s, {', '.join('TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)' if c == 'mute_until' else '%s' for c in cols)}""",
       (rule_id,) + tuple(values.get(c) for c in cols))
    return _rule(db, rule_id)


@router.put("/api/ops/routing/{rule_id}")
def update_rule(rule_id: str, body: RulePatch, db: Db = Depends(current_db)):
    _rule(db, rule_id)
    values = _rule_values(db, {k: getattr(body, k) for k in body.model_fields_set})
    sets = [(c, values[c]) for c in _RULE_COLUMNS if c in values]
    if sets:
        _x(db, f"""UPDATE OPS.ROUTING_RULE SET {', '.join(c.upper() + (' = TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)' if c == 'mute_until' else ' = %s')
                                                          for c, _ in sets)}, UPDATED_AT = CURRENT_TIMESTAMP()
                   WHERE RULE_ID = %s""", tuple(v for _, v in sets) + (rule_id,))
    return _rule(db, rule_id)


@router.delete("/api/ops/routing/{rule_id}")
def delete_rule(rule_id: str, db: Db = Depends(current_db)):
    _rule(db, rule_id)
    _x(db, "DELETE FROM OPS.ROUTING_RULE WHERE RULE_ID = %s", (rule_id,))
    return {"deleted": True, "rule_id": rule_id}


# ---------------------------------------------------------------- settings

class SettingsIn(BaseModel):
    reopen_hours: Optional[int] = Field(default=None, ge=0, le=720)
    alert_on_retry_for_critical: Optional[bool] = None
    transition_on_resolve: Optional[bool] = None
    done_status: Optional[str] = Field(default=None, max_length=60)
    rate_limit_per_10min: Optional[int] = Field(default=None, ge=1, le=500)
    public_base_url: Optional[str] = Field(default=None, max_length=500)
    ai_auto: Optional[bool] = None
    ai_severities: Optional[List[str]] = Field(default=None, max_length=4)
    weekly_digest: Optional[bool] = None


SETTING_KEYS = ("reopen_hours", "alert_on_retry_for_critical", "transition_on_resolve", "done_status", "rate_limit_per_10min",
                "public_base_url", "ai_auto", "ai_severities", "weekly_digest")


def _settings_out(db: Db) -> Dict[str, Any]:
    store = _store(db)
    settings = store.settings()
    seen = _q(db, "SELECT MAX(LAST_RUN_AT) AS AT FROM OPS.JOB_LEASE WHERE JOB_NAME = 'worker'")
    site = str(store.jira_config().get("site_url") or "").rstrip("/") or None
    return {"jira_bot": bool(tickets.bot_credentials()) and bool(site), "jira_site": site,
            **{k: settings.get(k) for k in SETTING_KEYS},
            "worker_seen_at": _iso(seen[0].get("at")) if seen else None}


@router.get("/api/ops/settings")
def get_settings(db: Db = Depends(current_db)):
    return _settings_out(db)


@router.put("/api/ops/settings")
def put_settings(body: SettingsIn, db: Db = Depends(current_db)):
    current = {k: v for k, v in _store(db).settings().items() if k in SETTING_KEYS}
    for key in body.model_fields_set:
        value = getattr(body, key)
        if isinstance(value, str):
            value = value.strip()
        if key == "public_base_url":
            value = (value or "").rstrip("/") or None
            if value and not re.fullmatch(r"(https://[A-Za-z0-9.\-]+(:\d+)?|http://(localhost|127\.0\.0\.1)(:\d+)?)(/[\w\-./]*)?", value):
                raise HTTPException(422, "public_base_url is the web app's address, for example https://gdp.example.com")
        if key == "done_status" and not value:
            value = "Done"
        if key == "ai_severities" and value is not None:
            wanted = {str(v).strip().upper() for v in value}
            if wanted - set(SEVERITIES):
                raise HTTPException(422, "ai_severities must be among P1, P2, P3, P4")
            value = [s for s in SEVERITIES if s in wanted]
        if value is None and key != "public_base_url":
            raise HTTPException(422, f"{key} cannot be empty")
        current[key] = value
    try:
        _set_config(db, inc.CONFIG_KEY, current,
                    "Ops incidents: reopen window, Teams rate limit, Jira transition, public URL, AI diagnosis, weekly digest")
    except Exception as exc:
        raise _ops_error(exc) from exc
    return _settings_out(db)
