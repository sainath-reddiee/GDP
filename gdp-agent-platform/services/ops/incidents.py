"""Incidents: open, update, reopen, group, route, mute, escalate and resolve, with a timeline event for every change.

The rules (services.ops.detect produces the candidates):
  - the same fingerprint while an incident is open (OPEN, ACK, MITIGATED, MUTED) adds an occurrence;
  - resolved within reopen_hours (default 24): the incident is reopened;
  - otherwise a new incident opens.
  A sighting is counted once: its occurrence key (run, task, map index, try) is claimed in OPS.INCIDENT_EVENT, so the
  same run read by push and poll, or twice by the poll overlap, changes nothing.
Storm control: a new failure in a DAG downstream (OPS.DAG_DEPENDENCY, one or two hops, filled by services.ops.deps) of an
OPEN or ACK incident seen in the last 2 hours becomes its child (KIND UPSTREAM, PARENT_INCIDENT_ID) with no ticket and no card.
Routing: the first enabled ROUTING_RULE by PRIORITY whose DAG pattern (glob), tag, owner and environment all match
gives the team (and may override the severity or mute); else the DAG's own team; else the incident is unrouted (still
created, still ticketed to the Jira default project, no card).
Mutes: a rule's MUTE_UNTIL or the DAG's MUTE_UNTIL opens the incident MUTED: no ticket, no card, until the mute ends.
Auto-resolve: a later successful run of the same task (or DAG, for DAG-level kinds) resolves it as 'system'.

Deliveries go through the outbox (services.ops.notify): Teams cards on channel TEAMS, Jira bot actions on channel JIRA
(create, recur, resolve, reopen; services.ops.tickets handles them).

Every database access goes through a store (SqlStore over the API's Db, or an in-memory one in tests).
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.ops import detect
from services.ops.normalize import ts
from services.ops.redact import redact

log = logging.getLogger("gdp.ops.incidents")
ACTIVE = ("OPEN", "ACK", "MITIGATED", "MUTED")
REOPENED = "REOPENED"   # JIRA_STATE while the Jira reopen action is queued: jira_sync must not mitigate meanwhile
OCCURRENCE_KEY_MAX = 200
INCIDENT_NAMESPACE = uuid.UUID("6f1d2c4e-5b7a-4c3e-9a8d-0e1f2a3b4c5d")
STORM_HOURS = 2
MAX_ESCALATIONS = 3
DEFAULT_ESCALATION_MINUTES = 30
RECUR_COMMENT_SECONDS = 3600
OCCURRENCE_CLAIM_MINUTES = 5
SYSTEM = "system"
CONFIG_KEY = "OPS"
TS_COLUMNS = {"first_seen", "last_seen", "opened_at", "resolved_at", "acked_at", "muted_until", "last_escalated_at",
              "jira_commented_at"}
INCIDENT_COLUMNS = ("incident_id", "fingerprint", "env_id", "dag_id", "task_id", "map_index", "run_id", "kind", "status",
                    "severity", "team_id", "assignee", "title", "error_excerpt", "first_seen", "last_seen", "opened_at",
                    "occurrences", "parent_incident_id", "jira_key", "jira_state", "jira_synced_occurrences",
                    "jira_commented_at", "ai_summary", "resolution", "resolved_by", "resolved_at", "acked_by", "acked_at",
                    "muted_until", "mute_reason", "escalations", "last_escalated_at")


class ActionError(Exception):
    """An action that does not apply to the incident's current state (the API answers 409) or is not found (404)."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: Any) -> Optional[datetime]:
    iso = ts(value)
    return datetime.fromisoformat(iso) if iso else None


def _iso(value: Any) -> Optional[str]:
    return ts(value)


def settings_from(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Ops settings with defaults; unknown keys are kept for forward compatibility."""
    out = dict(detect.DEFAULT_SETTINGS)
    for k, v in (config or {}).items():
        if v is not None:
            out[k] = v
    out["reopen_hours"] = max(0, int(out.get("reopen_hours") or 0))
    out["rate_limit_per_10min"] = max(1, int(out.get("rate_limit_per_10min") or 10))
    severities = out.get("ai_severities")
    if isinstance(severities, str):
        severities = [v.strip() for v in severities.split(",")]
    out["ai_severities"] = [v for v in detect.SEVERITIES if v in {str(x).upper() for x in (severities or [])}]
    out["ai_auto"] = bool(out.get("ai_auto"))
    out["weekly_digest"] = bool(out.get("weekly_digest"))
    return out


# ---------------------------------------------------------------- routing (pure)

def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value]
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


def rule_matches(rule: Dict[str, Any], dag: Dict[str, Any], env_id: str) -> bool:
    if rule.get("enabled") is False:
        return False
    if rule.get("env_id") and rule["env_id"] != env_id:
        return False
    if rule.get("dag_pattern") and not fnmatch.fnmatchcase(str(dag.get("dag_id") or ""), str(rule["dag_pattern"])):
        return False
    if rule.get("tag") and str(rule["tag"]).lower() not in {t.lower() for t in _as_list(dag.get("tags"))}:
        return False
    if rule.get("owner") and str(rule["owner"]).lower() not in {o.lower() for o in _as_list(dag.get("owners"))}:
        return False
    return True


def route(rules: Iterable[Dict[str, Any]], dag: Dict[str, Any], env_id: str) -> Optional[Dict[str, Any]]:
    """The first matching rule by priority (then creation order)."""
    ordered = sorted(enumerate(rules), key=lambda p: (int(p[1].get("priority") or 0), p[0]))
    return next((r for _, r in ordered if rule_matches(r, dag, env_id)), None)


def mute_until(rule: Optional[Dict[str, Any]], dag: Dict[str, Any], now: datetime) -> Tuple[Optional[datetime], Optional[str]]:
    """(until, reason) of the longest active mute from the rule or the DAG, or (None, None)."""
    found: List[Tuple[datetime, str]] = []
    for source, what in ((rule or {}, "routing rule"), (dag, "DAG mute")):
        until = _dt(source.get("mute_until"))
        if until and until > now:
            found.append((until, source.get("mute_reason") or f"muted by {what}"))
    return max(found, key=lambda f: f[0]) if found else (None, None)


# ---------------------------------------------------------------- decisions (pure)

def decide(existing: Optional[Dict[str, Any]], candidate: Dict[str, Any], settings: Dict[str, Any], now: datetime) -> str:
    """'occurrence' | 'reopen' | 'new' | 'stale' for a candidate whose fingerprint matches `existing` (the newest
    incident with that fingerprint, or None)."""
    if not existing:
        return "new"
    status = str(existing.get("status") or "").upper()
    if status in ACTIVE:
        return "occurrence"
    resolved_at = _dt(existing.get("resolved_at"))
    seen = _dt(candidate.get("at"))
    if resolved_at and seen and seen <= resolved_at:
        return "stale"   # a failure from before the resolution, read again
    if resolved_at and now - resolved_at <= timedelta(hours=float(settings.get("reopen_hours", 24))):
        return "reopen"
    return "new"


def next_escalation_at(opened_at: Any, minutes: Optional[int], severity: str, done: int) -> Optional[datetime]:
    """When the next escalation is due: after the team's escalation minutes (P1: at once), then every 2x that
    interval, at most MAX_ESCALATIONS times. None when all are done."""
    start = _dt(opened_at)
    if start is None or int(done or 0) >= MAX_ESCALATIONS:
        return None
    step = timedelta(minutes=max(1, int(minutes or DEFAULT_ESCALATION_MINUTES)))
    first = start if str(severity).upper() == "P1" else start + step
    return first + int(done or 0) * 2 * step


def hour_bucket(now: datetime) -> str:
    return now.strftime("%Y%m%d%H")


# ---------------------------------------------------------------- the SQL store

def _row(r: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if r is None:
        return None
    out = dict(r)
    for k in TS_COLUMNS | {"created_at", "updated_at"}:
        if k in out and out[k] is not None:
            out[k] = _iso(out[k])
    return out


def detect_cursor(runs: List[Dict[str, Any]], tasks: List[Dict[str, Any]], limit: int,
                  since: Optional[str]) -> Tuple[Optional[str], bool]:
    """(cursor, truncated) after reading changed runs and task runs ordered by LOADED_AT. Normally the newest LOADED_AT
    read. When a list came back with `limit` rows, rows after its last one were not read: the cursor stops at that
    list's newest LOADED_AT (the smallest such maximum when both were cut), so nothing is skipped."""
    maxima: List[str] = []
    capped: List[str] = []
    for rows in (runs, tasks):
        stamps = [_iso(r.get("loaded_at")) for r in rows if r.get("loaded_at") is not None]
        if not stamps:
            continue
        top = max(stamps, key=_dt)
        maxima.append(top)
        if len(rows) >= limit:
            capped.append(top)
    if capped:
        return min(capped, key=_dt), True
    return (max(maxima, key=_dt) if maxima else since), False


class SqlStore:
    """OPS incident tables over the API's Db (query/execute/execute_count with %s parameters)."""

    def __init__(self, db: Any):
        self.db = db

    # ---- settings and metadata
    def settings(self) -> Dict[str, Any]:
        try:
            found = self.db.query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s AND IS_CURRENT "
                                  "ORDER BY VERSION DESC LIMIT 1", (CONFIG_KEY,))
        except Exception:
            found = []
        value = found[0].get("config_value") if found else None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = None
        return settings_from(value if isinstance(value, dict) else None)

    def jira_config(self) -> Dict[str, Any]:
        try:
            found = self.db.query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'JIRA' AND IS_CURRENT "
                                  "ORDER BY VERSION DESC LIMIT 1")
        except Exception:
            return {}
        value = found[0].get("config_value") if found else None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = None
        return value if isinstance(value, dict) else {}

    def dags(self, keys: Iterable[Tuple[str, str]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        pairs = sorted({k for k in keys if k[0] and k[1]})
        if not pairs:
            return {}
        found = self.db.query("""
            SELECT D.ENV_ID, D.DAG_ID, D.CRITICALITY, D.TEAM_ID, D.TAGS, D.OWNERS, D.MUTE_UNTIL, D.MUTE_REASON, D.IS_PAUSED,
                   D.IS_ACTIVE, D.EXPECTED_BY_CRON, D.MAX_DURATION_MIN, D.TIMEZONE
              FROM OPS.DAG D JOIN TABLE(FLATTEN(INPUT => PARSE_JSON(%s))) F
                ON D.ENV_ID = F.VALUE[0]::VARCHAR AND D.DAG_ID = F.VALUE[1]::VARCHAR""", (json.dumps([list(p) for p in pairs]),))
        return {(r["env_id"], r["dag_id"]): _row(r) for r in found}

    def envs(self) -> Dict[str, Dict[str, Any]]:
        return {r["env_id"]: r for r in self.db.query("SELECT ENV_ID, NAME, AIRFLOW_URL, API_VERSION FROM OPS.AIRFLOW_ENV")}

    def rules(self) -> List[Dict[str, Any]]:
        return [_row(r) for r in self.db.query("""SELECT RULE_ID, PRIORITY, DAG_PATTERN, TAG, OWNER, ENV_ID, TEAM_ID,
                                                         SEVERITY_OVERRIDE, MUTE_UNTIL, MUTE_REASON, ENABLED
                                                    FROM OPS.ROUTING_RULE WHERE ENABLED ORDER BY PRIORITY, CREATED_AT""")]

    def team(self, team_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not team_id:
            return None
        found = self.db.query("""SELECT TEAM_ID, NAME, JIRA_PROJECT, JIRA_COMPONENT, JIRA_ASSIGNEE_ACCOUNT_ID, ESCALATION_MINUTES,
                                        TEAMS_WEBHOOK_SECRET IS NOT NULL AS HAS_TEAMS_WEBHOOK
                                   FROM OPS.TEAM WHERE TEAM_ID = %s""", (team_id,))
        return found[0] if found else None

    # ---- idempotency claims and the timeline
    def claim(self, key: str) -> bool:
        return self.db.execute_count("""
            MERGE INTO OPS.INCIDENT_EVENT T USING (SELECT %s AS K) S ON T.IDEMPOTENCY_KEY = S.K
            WHEN NOT MATCHED THEN INSERT (EVENT_ID, KIND, ACTOR, IDEMPOTENCY_KEY) VALUES (%s, 'claim', 'system', S.K)""",
                                     (key, str(uuid.uuid4()))) >= 1

    def reclaim_stale(self, key: str, minutes: int) -> bool:
        """Take over a claim nobody finished (no incident bound) that is older than `minutes`."""
        return self.db.execute_count("""UPDATE OPS.INCIDENT_EVENT SET CREATED_AT = CURRENT_TIMESTAMP()
                                         WHERE IDEMPOTENCY_KEY = %s AND KIND = 'claim'
                                           AND CREATED_AT < DATEADD(minute, %s, CURRENT_TIMESTAMP())""",
                                     (key, -abs(int(minutes)))) >= 1

    def bind(self, key: str, incident_id: Optional[str], kind: str, actor: str = SYSTEM,
             detail: Optional[Dict[str, Any]] = None) -> None:
        self.db.execute("""UPDATE OPS.INCIDENT_EVENT SET INCIDENT_ID = %s, KIND = %s, ACTOR = %s, DETAIL = PARSE_JSON(%s)
                            WHERE IDEMPOTENCY_KEY = %s""", (incident_id, kind, actor, json.dumps(detail or {}, default=str), key))

    def release_claim(self, key: str) -> None:
        """Make an unfinished claim free to take again at once (after a failed attempt)."""
        self.db.execute("""UPDATE OPS.INCIDENT_EVENT SET KIND = 'claim', CREATED_AT = DATEADD(day, -1, CURRENT_TIMESTAMP())
                            WHERE IDEMPOTENCY_KEY = %s""", (key,))

    def ticketed_open(self) -> List[Dict[str, Any]]:
        return [_row(r) for r in self.db.query(self._SELECT + " WHERE JIRA_KEY IS NOT NULL AND STATUS IN ('OPEN', 'ACK') "
                                                             f"AND COALESCE(JIRA_STATE, '') <> '{REOPENED}' "
                                                             "ORDER BY LAST_SEEN DESC LIMIT 500")]

    @contextlib.contextmanager
    def atomic(self):
        """One transaction around a group of writes when the session is the caller's own (the worker's dedicated
        connection); the shared dev or service session gets none (app.db.transaction)."""
        if getattr(self.db, "shared", True):
            yield self
            return
        from app.db import transaction

        with transaction(self.db):
            yield self

    def claimed_incident(self, key: str) -> Optional[str]:
        found = self.db.query("SELECT INCIDENT_ID FROM OPS.INCIDENT_EVENT WHERE IDEMPOTENCY_KEY = %s LIMIT 1", (key,))
        return found[0].get("incident_id") if found else None

    def event(self, incident_id: str, kind: str, actor: str = SYSTEM, detail: Optional[Dict[str, Any]] = None) -> None:
        self.db.execute("""INSERT INTO OPS.INCIDENT_EVENT (EVENT_ID, INCIDENT_ID, KIND, ACTOR, DETAIL)
                           SELECT %s, %s, %s, %s, PARSE_JSON(%s)""",
                        (str(uuid.uuid4()), incident_id, kind, actor or SYSTEM, json.dumps(detail or {}, default=str)))

    # ---- incidents
    _SELECT = "SELECT " + ", ".join(c.upper() for c in INCIDENT_COLUMNS) + " FROM OPS.INCIDENT"
    _SELECT_I = "SELECT " + ", ".join("I." + c.upper() for c in INCIDENT_COLUMNS) + " FROM OPS.INCIDENT I"

    def get(self, incident_id: str) -> Optional[Dict[str, Any]]:
        found = self.db.query(self._SELECT + " WHERE INCIDENT_ID = %s", (incident_id,))
        return _row(found[0]) if found else None

    def latest_by_fingerprint(self, fp: str) -> Optional[Dict[str, Any]]:
        found = self.db.query(self._SELECT + " WHERE FINGERPRINT = %s ORDER BY FIRST_SEEN DESC NULLS LAST LIMIT 1", (fp,))
        return _row(found[0]) if found else None

    def task_incident_for_run(self, env_id: str, dag_id: str, run_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not run_id:
            return None
        found = self.db.query(self._SELECT + " WHERE ENV_ID = %s AND DAG_ID = %s AND RUN_ID = %s AND TASK_ID IS NOT NULL "
                                             "ORDER BY LAST_SEEN DESC LIMIT 1", (env_id, dag_id, run_id))
        return _row(found[0]) if found else None

    def active_for_dags(self, keys: Iterable[Tuple[str, str]]) -> List[Dict[str, Any]]:
        pairs = sorted({k for k in keys if k[0] and k[1]})
        if not pairs:
            return []
        found = self.db.query(self._SELECT_I + """
            JOIN TABLE(FLATTEN(INPUT => PARSE_JSON(%s))) F ON I.ENV_ID = F.VALUE[0]::VARCHAR AND I.DAG_ID = F.VALUE[1]::VARCHAR
            WHERE I.STATUS IN ('OPEN', 'ACK', 'MITIGATED', 'MUTED')""", (json.dumps([list(p) for p in pairs]),))
        return [_row(r) for r in found]

    def children(self, incident_id: str) -> List[Dict[str, Any]]:
        return [_row(r) for r in self.db.query(self._SELECT + " WHERE PARENT_INCIDENT_ID = %s ORDER BY FIRST_SEEN", (incident_id,))]

    def insert(self, incident: Dict[str, Any]) -> int:
        """Insert unless a row with the INCIDENT_ID exists (the id is derived from the sighting's claim, so a retry
        after a failed attempt never adds a second incident). The number of rows added."""
        cols = [c for c in INCIDENT_COLUMNS if c in incident]
        values = ", ".join("TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)" if c in TS_COLUMNS else "%s" for c in cols)
        return self.db.execute_count(
            f"MERGE INTO OPS.INCIDENT T USING (SELECT %s AS INCIDENT_ID) S ON T.INCIDENT_ID = S.INCIDENT_ID "
            f"WHEN NOT MATCHED THEN INSERT ({', '.join(c.upper() for c in cols)}) VALUES ({values})",
            (incident["incident_id"],) + tuple(incident[c] for c in cols))

    def update(self, incident_id: str, fields: Dict[str, Any], add_occurrence: bool = False,
               only_status: Optional[Iterable[str]] = None) -> int:
        sets, params = [], []
        for k, v in fields.items():
            if k not in INCIDENT_COLUMNS or k == "incident_id":
                raise ValueError(f"unknown incident column {k}")
            sets.append(f"{k.upper()} = " + ("TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)" if k in TS_COLUMNS else "%s"))
            params.append(v)
        if add_occurrence:
            sets.append("OCCURRENCES = COALESCE(OCCURRENCES, 0) + 1")
        sets.append("UPDATED_AT = CURRENT_TIMESTAMP()")
        where = "INCIDENT_ID = %s"
        params.append(incident_id)
        if only_status:
            statuses = list(only_status)
            where += f" AND STATUS IN ({', '.join(['%s'] * len(statuses))})"
            params += statuses
        return self.db.execute_count(f"UPDATE OPS.INCIDENT SET {', '.join(sets)} WHERE {where}", tuple(params))

    def storm_parent(self, env_id: str, dag_id: str) -> Optional[str]:
        """An open incident of a DAG upstream of this one (OPS.DAG_DEPENDENCY: datasets, sensors, triggers, the code
        graph or set by hand), one or two hops up; the nearest and newest wins."""
        try:
            found = self.db.query(f"""
                WITH UP AS (
                    SELECT UPSTREAM_DAG_ID AS DAG_ID, 1 AS HOPS FROM OPS.DAG_DEPENDENCY
                     WHERE ENV_ID = %s AND DOWNSTREAM_DAG_ID = %s AND UPSTREAM_DAG_ID <> DOWNSTREAM_DAG_ID
                    UNION ALL
                    SELECT D2.UPSTREAM_DAG_ID, 2 FROM OPS.DAG_DEPENDENCY D1
                      JOIN OPS.DAG_DEPENDENCY D2 ON D2.ENV_ID = D1.ENV_ID AND D2.DOWNSTREAM_DAG_ID = D1.UPSTREAM_DAG_ID
                     WHERE D1.ENV_ID = %s AND D1.DOWNSTREAM_DAG_ID = %s AND D1.UPSTREAM_DAG_ID <> D1.DOWNSTREAM_DAG_ID
                       AND D2.UPSTREAM_DAG_ID NOT IN (D2.DOWNSTREAM_DAG_ID, %s))
                SELECT I.INCIDENT_ID FROM OPS.INCIDENT I JOIN UP ON UP.DAG_ID = I.DAG_ID
                 WHERE I.ENV_ID = %s AND I.STATUS IN ('OPEN', 'ACK') AND I.PARENT_INCIDENT_ID IS NULL
                   AND I.LAST_SEEN >= DATEADD(hour, -{STORM_HOURS}, CURRENT_TIMESTAMP())
                 ORDER BY UP.HOPS, I.LAST_SEEN DESC LIMIT 1""", (env_id, dag_id, env_id, dag_id, dag_id, env_id))
        except Exception:
            return None   # no dependency information: never grouped
        return found[0]["incident_id"] if found else None

    def escalation_candidates(self) -> List[Dict[str, Any]]:
        found = self.db.query(self._SELECT_I.replace("SELECT ", "SELECT T.ESCALATION_MINUTES, ", 1) + """
            JOIN OPS.TEAM T ON T.TEAM_ID = I.TEAM_ID
            WHERE I.STATUS = 'OPEN' AND I.PARENT_INCIDENT_ID IS NULL AND COALESCE(I.ESCALATIONS, 0) < %s""", (MAX_ESCALATIONS,))
        return [_row(r) for r in found]

    def expired_mutes(self) -> List[Dict[str, Any]]:
        return [_row(r) for r in self.db.query(self._SELECT + " WHERE STATUS = 'MUTED' AND MUTED_UNTIL IS NOT NULL "
                                                             "AND MUTED_UNTIL < CURRENT_TIMESTAMP()")]

    def sla_inputs(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """(DAGs with an expected-by cron and their last success, running runs of DAGs with a max duration)."""
        dags = self.db.query("""
            SELECT D.ENV_ID, D.DAG_ID, D.CRITICALITY, D.TEAM_ID, D.TAGS, D.OWNERS, D.MUTE_UNTIL, D.MUTE_REASON, D.IS_PAUSED,
                   D.IS_ACTIVE, D.EXPECTED_BY_CRON, D.MAX_DURATION_MIN, D.TIMEZONE, S.LAST_SUCCESS_AT
              FROM OPS.DAG D JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = D.ENV_ID
              LEFT JOIN (SELECT ENV_ID, DAG_ID, MAX(COALESCE(ENDED_AT, UPDATED_AT)) AS LAST_SUCCESS_AT FROM OPS.DAG_RUN
                          WHERE STATE = 'success' GROUP BY ENV_ID, DAG_ID) S ON S.ENV_ID = D.ENV_ID AND S.DAG_ID = D.DAG_ID
             WHERE E.ENABLED AND (D.EXPECTED_BY_CRON IS NOT NULL OR D.MAX_DURATION_MIN IS NOT NULL)""")
        runs = self.db.query("""
            SELECT R.ENV_ID, R.DAG_ID, R.RUN_ID, R.RUN_TYPE, R.STATE, R.STARTED_AT, R.EXTERNAL_TRIGGER
              FROM OPS.DAG_RUN R JOIN OPS.DAG D ON D.ENV_ID = R.ENV_ID AND D.DAG_ID = R.DAG_ID
             WHERE R.STATE = 'running' AND D.MAX_DURATION_MIN IS NOT NULL
               AND R.STARTED_AT < DATEADD(minute, -D.MAX_DURATION_MIN, CURRENT_TIMESTAMP())""")
        return [_row(d) for d in dags], [_row(r) for r in runs]

    def changed_since(self, since: Optional[str], limit: int = 5000, inclusive: bool = False
                      ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[str], bool]:
        """Runs and task runs loaded after `since` (from it, when inclusive; the last hour when None), the cursor to
        store and whether a list was cut at `limit` (see detect_cursor)."""
        op = ">=" if inclusive else ">"
        bound = "TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)" if since else "DATEADD(hour, -1, CURRENT_TIMESTAMP())"
        params: Tuple[Any, ...] = (since,) if since else ()
        runs = self.db.query(f"""SELECT ENV_ID, DAG_ID, RUN_ID, RUN_TYPE, STATE, STARTED_AT, ENDED_AT, EXTERNAL_TRIGGER,
                                        UPDATED_AT, LOADED_AT FROM OPS.DAG_RUN WHERE LOADED_AT {op} {bound}
                                  ORDER BY LOADED_AT LIMIT {int(limit)}""", params)
        tasks = self.db.query(f"""SELECT ENV_ID, DAG_ID, RUN_ID, TASK_ID, MAP_INDEX, TRY_NUMBER, STATE, STARTED_AT, ENDED_AT,
                                         ERROR_EXCERPT, UPDATED_AT, LOADED_AT FROM OPS.TASK_RUN WHERE LOADED_AT {op} {bound}
                                   ORDER BY LOADED_AT LIMIT {int(limit)}""", params)
        cursor, truncated = detect_cursor(runs, tasks, limit, since)
        return [_row(r) for r in runs], [_row(t) for t in tasks], cursor, truncated

    # ---- outbox
    def enqueue(self, channel: str, kind: str, dedupe_key: str, payload: Optional[Dict[str, Any]] = None,
                incident_id: Optional[str] = None, team_id: Optional[str] = None, target: Optional[str] = None,
                status: str = "PENDING", delay_seconds: int = 0, error: Optional[str] = None) -> bool:
        from services.ops.notify import enqueue

        return enqueue(self.db, channel, kind, dedupe_key, payload, incident_id, team_id, target, status, delay_seconds, error)


# ---------------------------------------------------------------- notifications for incident changes

def _notify(store: Any, incident: Dict[str, Any], kind: str, now: datetime, note: Optional[str] = None,
            jira_kind: Optional[str] = None, dedupe: Optional[str] = None, jira_again: bool = False) -> None:
    """Queue the Teams card (when the incident has a team) and the Jira action for one change. The first Jira create
    of an incident is queued once ever; `jira_again` queues another one (a reopened incident that has no ticket)."""
    iid = incident["incident_id"]
    suffix = dedupe or now.isoformat()
    if kind and incident.get("team_id"):
        target = "escalation" if kind == "escalated" else "alerts"
        store.enqueue("TEAMS", kind, f"teams:{kind}:{iid}:{suffix}", {"note": note} if note else {},
                      incident_id=iid, team_id=incident["team_id"], target=target)
    if jira_kind:
        once = jira_kind == "create" and not jira_again
        store.enqueue("JIRA", jira_kind, f"jira:{jira_kind}:{iid}" + ("" if once else f":{suffix}"),
                      {"note": note} if note else {}, incident_id=iid, team_id=incident.get("team_id"))


# ---------------------------------------------------------------- open or update

def occurrence_key(candidate: Dict[str, Any]) -> str:
    """The idempotency key of one sighting (OPS.INCIDENT_EVENT.IDEMPOTENCY_KEY, VARCHAR(500)). Short keys keep their
    readable form (claims already stored still match); a key over OCCURRENCE_KEY_MAX characters (long DAG, task or
    run ids) becomes occ:<env>:<dag prefix>:<sha1 of the full key>, which always fits."""
    full = f"occ:{candidate['env_id']}:{candidate['dag_id']}:{candidate.get('task_id') or ''}:{candidate['occurrence_key']}"
    if len(full) <= OCCURRENCE_KEY_MAX:
        return full
    digest = hashlib.sha1(full.encode("utf-8")).hexdigest()
    return f"occ:{str(candidate['env_id'])[:40]}:{str(candidate['dag_id'])[:60]}:{digest}"


def incident_id_for(key: str) -> str:
    """The incident id a sighting opens: derived from its claim key, so a retry after a failed attempt (the claim was
    released, or taken over when stale) finds the incident the first attempt inserted instead of adding another."""
    return str(uuid.uuid5(INCIDENT_NAMESPACE, key))


def _atomic(store: Any):
    atomic = getattr(store, "atomic", None)
    return atomic() if callable(atomic) else contextlib.nullcontext()


def open_or_update(store: Any, candidate: Dict[str, Any], settings: Dict[str, Any], dag: Optional[Dict[str, Any]] = None,
                   rules: Optional[List[Dict[str, Any]]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Apply one candidate. Returns {action, incident_id}: opened | child | muted | occurrence | reopened | duplicate |
    covered | stale. The claim and every write it leads to share one transaction where the session allows it."""
    now = now or utcnow()
    dag = dag or {"env_id": candidate["env_id"], "dag_id": candidate["dag_id"]}
    key = occurrence_key(candidate)
    with _atomic(store):
        # a claim left by an attempt that crashed before binding an incident is taken over, never kept forever
        if not store.claim(key) and not store.reclaim_stale(key, OCCURRENCE_CLAIM_MINUTES):
            return _duplicate(store, key, candidate)
        try:
            return _apply(store, key, candidate, settings, dag, rules, now)
        except Exception:
            store.release_claim(key)
            raise


def _apply(store: Any, key: str, candidate: Dict[str, Any], settings: Dict[str, Any], dag: Dict[str, Any],
           rules: Optional[List[Dict[str, Any]]], now: datetime) -> Dict[str, Any]:
    if candidate["kind"] == "FAILED":
        covered = store.task_incident_for_run(candidate["env_id"], candidate["dag_id"], candidate.get("run_id"))
        if covered:
            store.bind(key, covered["incident_id"], "run_failed", SYSTEM, {"run_id": candidate.get("run_id")})
            return {"action": "covered", "incident_id": covered["incident_id"]}

    existing = store.latest_by_fingerprint(candidate["fingerprint"])
    if existing and existing.get("incident_id") == incident_id_for(key):
        # this sighting's own incident, inserted by an attempt that failed before binding it: finish opening it
        # instead of counting the same sighting again as an occurrence
        return _open_new(store, key, candidate, settings, dag, rules or [], now)
    action = decide(existing, candidate, settings, now)
    seen = candidate.get("at") or now.isoformat()

    if action == "stale":
        store.bind(key, existing["incident_id"], "stale", SYSTEM, {"run_id": candidate.get("run_id")})
        return {"action": "stale", "incident_id": existing["incident_id"]}

    if action == "occurrence":
        iid = existing["incident_id"]
        fields: Dict[str, Any] = {"last_seen": max(seen, existing.get("last_seen") or seen, key=lambda v: _dt(v)),
                                  "run_id": candidate.get("run_id") or existing.get("run_id"),
                                  "map_index": candidate.get("map_index")}
        if candidate.get("error_excerpt"):
            fields["error_excerpt"] = candidate["error_excerpt"]
        status = existing["status"]
        reopened = status == "MITIGATED" or (status == "MUTED" and (_dt(existing.get("muted_until")) or now) <= now)
        # back from Jira Done: the ticket must be reopened too, and jira_sync must not mitigate it again meanwhile
        jira_reopen = status == "MITIGATED" and bool(existing.get("jira_key"))
        if reopened:
            fields.update({"status": "OPEN", "opened_at": now.isoformat(), "escalations": 0, "last_escalated_at": None,
                           "muted_until": None, "mute_reason": None})
        if jira_reopen:
            fields["jira_state"] = REOPENED
        store.update(iid, fields, add_occurrence=True)
        store.bind(key, iid, "occurrence", SYSTEM, {"run_id": candidate.get("run_id"), "at": seen,
                                                    "occurrences": int(existing.get("occurrences") or 1) + 1})
        current = {**existing, **fields, "occurrences": int(existing.get("occurrences") or 1) + 1}
        if reopened:
            store.event(iid, "reopened", SYSTEM, {"reason": "failed again" + (" after Jira Done" if status == "MITIGATED" else " after the mute ended")})
        if current["status"] != "MUTED" and not current.get("parent_incident_id"):
            bucket = hour_bucket(now)
            jira = "reopen" if jira_reopen else ("recur" if current.get("jira_key") else None)
            _notify(store, current, "reoccurred", now, dedupe=bucket if not jira_reopen else now.isoformat(), jira_kind=jira)
        return {"action": "occurrence", "incident_id": iid}

    if action == "reopen":
        iid = existing["incident_id"]
        fields = {"status": "OPEN", "last_seen": seen, "run_id": candidate.get("run_id"), "map_index": candidate.get("map_index"),
                  "opened_at": now.isoformat(), "resolution": None, "resolved_by": None, "resolved_at": None,
                  "acked_by": None, "acked_at": None, "escalations": 0, "last_escalated_at": None}
        if candidate.get("error_excerpt"):
            fields["error_excerpt"] = candidate["error_excerpt"]
        if existing.get("jira_key") and not existing.get("parent_incident_id"):
            fields["jira_state"] = REOPENED
        elif not existing.get("parent_incident_id"):
            fields["jira_state"] = "PENDING"
        store.update(iid, fields, add_occurrence=True)
        store.bind(key, iid, "reopened", SYSTEM, {"run_id": candidate.get("run_id"), "at": seen,
                                                  "previous_resolution": existing.get("resolution")})
        current = {**existing, **fields}
        if not current.get("parent_incident_id"):
            _notify(store, current, "opened", now, note="Reopened: it failed again within the reopen window.",
                    jira_kind="reopen" if existing.get("jira_key") else "create", dedupe=now.isoformat(), jira_again=True)
        return {"action": "reopened", "incident_id": iid}

    return _open_new(store, key, candidate, settings, dag, rules or [], now)


def _duplicate(store: Any, key: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    """The sighting was already counted. A failure first seen without its error excerpt gets it now (and the
    fingerprint the excerpt gives, while it is still a single occurrence)."""
    iid = store.claimed_incident(key)
    if iid and candidate.get("error_excerpt"):
        current = store.get(iid)
        if current and not current.get("error_excerpt"):
            fields: Dict[str, Any] = {"error_excerpt": candidate["error_excerpt"]}
            if int(current.get("occurrences") or 1) <= 1 and current.get("kind") == candidate.get("kind"):
                fields["fingerprint"] = candidate["fingerprint"]
            store.update(iid, fields)
    return {"action": "duplicate", "incident_id": iid}


def _open_new(store: Any, key: str, candidate: Dict[str, Any], settings: Dict[str, Any], dag: Dict[str, Any],
              rules: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    env_id, dag_id = candidate["env_id"], candidate["dag_id"]
    rule = route(rules, dag, env_id)
    team_id = (rule or {}).get("team_id") or dag.get("team_id") or None
    severity = detect.severity(None, candidate["kind"], override=(rule or {}).get("severity_override")) \
        if (rule or {}).get("severity_override") else candidate["severity"]
    until, reason = mute_until(rule, dag, now)
    parent = None if until else store.storm_parent(env_id, dag_id)
    seen = candidate.get("at") or now.isoformat()
    iid = incident_id_for(key)
    status = "MUTED" if until else "OPEN"
    incident = {
        "incident_id": iid, "fingerprint": candidate["fingerprint"], "env_id": env_id, "dag_id": dag_id,
        "task_id": candidate.get("task_id"), "map_index": candidate.get("map_index"), "run_id": candidate.get("run_id"),
        "kind": "UPSTREAM" if parent else candidate["kind"], "status": status, "severity": severity, "team_id": team_id,
        "title": redact(candidate.get("title") or "")[:500], "error_excerpt": candidate.get("error_excerpt"),
        "first_seen": seen, "last_seen": seen, "opened_at": now.isoformat(), "occurrences": 1,
        "parent_incident_id": parent, "jira_state": "SKIPPED" if (parent or until) else "PENDING",
        "jira_synced_occurrences": 1, "escalations": 0,
        "muted_until": until.isoformat() if until else None, "mute_reason": reason,
    }
    if store.get(iid) is None:   # a retry of a sighting whose first attempt inserted the incident and then failed
        store.insert(incident)
    detail = {"kind": candidate["kind"], "severity": severity, "team_id": team_id, "unrouted": team_id is None,
              "rule_id": (rule or {}).get("rule_id"), "run_id": candidate.get("run_id"), "at": seen}
    store.bind(key, iid, "opened", SYSTEM, detail)
    if parent:
        store.event(iid, "grouped", SYSTEM, {"parent_incident_id": parent, "reason": "an upstream DAG has an open incident"})
        store.event(parent, "child_added", SYSTEM, {"incident_id": iid, "dag_id": dag_id})
        return {"action": "child", "incident_id": iid}
    if until:
        store.event(iid, "muted", SYSTEM, {"until": until.isoformat(), "reason": reason})
        return {"action": "muted", "incident_id": iid}
    _notify(store, incident, "opened", now, jira_kind="create", dedupe="new")   # once per incident, retries included
    return {"action": "opened", "incident_id": iid}


# ---------------------------------------------------------------- processing captured runs

def process(store: Any, runs: List[Dict[str, Any]], tasks: List[Dict[str, Any]], settings: Optional[Dict[str, Any]] = None,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Detect and apply incidents for changed runs and tasks, then auto-resolve from their successes. Idempotent."""
    if not runs and not tasks:
        return {"candidates": 0, "actions": {}, "resolved": 0}
    now = now or utcnow()
    settings = settings or store.settings()
    keys = {(r["env_id"], r["dag_id"]) for r in list(runs) + list(tasks)}
    dags = store.dags(keys)
    envs = store.envs()
    rules = store.rules()
    found = detect.candidates(runs, tasks, dags, envs, settings)
    found.sort(key=lambda c: (_dt(c.get("at")) or now, 0 if c.get("task_id") else 1))
    actions: Dict[str, int] = {}
    errors: List[str] = []
    for c in found:
        # one bad candidate (a value the database refuses, a transient error) is logged and skipped; the others are
        # still applied and the caller's cursor still moves on
        try:
            result = open_or_update(store, c, settings, dags.get((c["env_id"], c["dag_id"])), rules, now)
        except Exception as exc:
            text = redact(f"{type(exc).__name__}: {exc}")[:300]
            log.warning("incident candidate %s/%s/%s skipped: %s", c.get("env_id"), c.get("dag_id"), c.get("task_id"), text)
            errors.append(text)
            continue
        actions[result["action"]] = actions.get(result["action"], 0) + 1
    resolved = auto_resolve(store, detect.successes(runs, tasks), settings, now)
    summary: Dict[str, Any] = {"candidates": len(found), "actions": actions, "resolved": len(resolved)}
    if errors:
        summary.update({"skipped": len(errors), "errors": errors[:5]})
    return summary


def auto_resolve(store: Any, signals: List[Dict[str, Any]], settings: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    now = now or utcnow()
    if not signals:
        return []
    done: List[str] = []
    for incident in store.active_for_dags({(s["env_id"], s["dag_id"]) for s in signals}):
        signal = next((s for s in signals if detect.resolves(incident, s)), None)
        if signal is None:
            continue
        what = "task" if incident.get("task_id") else "DAG"
        try:
            resolve(store, incident["incident_id"], f"Resolved automatically: a later {what} run succeeded "
                                                    f"({signal.get('run_id') or 'run'}).", SYSTEM, now, auto=True)
            done.append(incident["incident_id"])
        except ActionError:
            continue
    return done


def sla_check(store: Any, settings: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """LATE and LONG_RUNNING candidates for every DAG with an expected-by cron or a max duration."""
    now = now or utcnow()
    settings = settings or store.settings()
    dags, runs = store.sla_inputs()
    envs = store.envs()
    rules = store.rules()
    by_key = {(d["env_id"], d["dag_id"]): d for d in dags}
    found: List[Dict[str, Any]] = []
    for d in dags:
        c = detect.late(d, envs.get(d["env_id"], {}), d.get("last_success_at"), now)
        if c:
            found.append(c)
    for r in runs:
        d = by_key.get((r["env_id"], r["dag_id"]))
        if d:
            c = detect.long_running(r, d, envs.get(r["env_id"], {}), now)
            if c:
                found.append(c)
    actions: Dict[str, int] = {}
    skipped = 0
    for c in found:
        try:
            result = open_or_update(store, c, settings, by_key.get((c["env_id"], c["dag_id"])), rules, now)
        except Exception as exc:
            log.warning("SLA candidate %s/%s skipped: %s", c.get("env_id"), c.get("dag_id"),
                        redact(f"{type(exc).__name__}: {exc}")[:300])
            skipped += 1
            continue
        actions[result["action"]] = actions.get(result["action"], 0) + 1
    return {"candidates": len(found), "actions": actions, **({"skipped": skipped} if skipped else {})}


# ---------------------------------------------------------------- escalation and mute expiry

def escalate(store: Any, now: Optional[datetime] = None) -> Dict[str, int]:
    now = now or utcnow()
    escalated = unmuted = 0
    for incident in store.escalation_candidates():
        done = int(incident.get("escalations") or 0)
        due = next_escalation_at(incident.get("opened_at") or incident.get("first_seen"), incident.get("escalation_minutes"),
                                 incident.get("severity") or "P4", done)
        if due is None or due > now:
            continue
        level = done + 1
        if store.update(incident["incident_id"], {"escalations": level, "last_escalated_at": now.isoformat()},
                        only_status=("OPEN",)) < 1:
            continue
        store.event(incident["incident_id"], "escalated", SYSTEM, {"level": level, "of": MAX_ESCALATIONS})
        _notify(store, {**incident, "escalations": level}, "escalated", now,
                note=f"Not acknowledged: escalation {level} of {MAX_ESCALATIONS}.",
                dedupe=f"{incident.get('opened_at')}:{level}")
        escalated += 1
    for incident in store.expired_mutes():
        if store.update(incident["incident_id"], {"status": "OPEN", "opened_at": now.isoformat(), "muted_until": None,
                                                  "mute_reason": None, "escalations": 0}, only_status=("MUTED",)) < 1:
            continue
        store.event(incident["incident_id"], "unmuted", SYSTEM, {"reason": "the mute ended"})
        current = {**incident, "status": "OPEN"}
        if not incident.get("parent_incident_id"):
            jira = None
            if not incident.get("jira_key") and incident.get("jira_state") in (None, "SKIPPED", "PENDING"):
                store.update(incident["incident_id"], {"jira_state": "PENDING"})
                jira = "create"
            _notify(store, current, "opened", now, note="The mute ended and the incident is still open.", jira_kind=jira,
                    dedupe=now.isoformat())
        unmuted += 1
    return {"escalated": escalated, "unmuted": unmuted}


# ---------------------------------------------------------------- actions

def _get(store: Any, incident_id: str) -> Dict[str, Any]:
    found = store.get(incident_id)
    if not found:
        raise ActionError(f"Incident {incident_id} not found", 404)
    return found


def ack(store: Any, incident_id: str, actor: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    incident = _get(store, incident_id)
    if incident["status"] == "ACK":
        return incident
    if incident["status"] != "OPEN":
        raise ActionError(f"Only an open incident can be acknowledged (it is {incident['status']}).")
    store.update(incident_id, {"status": "ACK", "acked_by": actor, "acked_at": now.isoformat(),
                               "assignee": incident.get("assignee") or actor}, only_status=("OPEN",))
    store.event(incident_id, "ack", actor, {})
    return _get(store, incident_id)


def assign(store: Any, incident_id: str, assignee: Optional[str], actor: str) -> Dict[str, Any]:
    incident = _get(store, incident_id)
    if incident["status"] == "RESOLVED":
        raise ActionError("A resolved incident cannot be assigned; reopen it first.")
    value = (assignee or "").strip()[:256] or None
    store.update(incident_id, {"assignee": value})
    store.event(incident_id, "assigned", actor, {"assignee": value, "previous": incident.get("assignee")})
    return _get(store, incident_id)


def resolve(store: Any, incident_id: str, resolution: str, actor: str, now: Optional[datetime] = None,
            auto: bool = False) -> Dict[str, Any]:
    now = now or utcnow()
    incident = _get(store, incident_id)
    if incident["status"] == "RESOLVED":
        raise ActionError("The incident is already resolved.")
    text = (resolution or "").strip()
    if len(text) < 3:
        raise ActionError("Describe the resolution (at least 3 characters).", 422)
    stamp = now.isoformat()
    if store.update(incident_id, {"status": "RESOLVED", "resolution": text[:4000], "resolved_by": actor,
                                  "resolved_at": stamp}, only_status=ACTIVE) < 1:
        raise ActionError("The incident changed meanwhile; reload it.")
    store.event(incident_id, "resolved", actor, {"resolution": text[:4000], "auto": auto})
    current = {**incident, "status": "RESOLVED", "resolution": text, "resolved_by": actor, "resolved_at": stamp}
    if not incident.get("parent_incident_id") and incident["status"] != "MUTED":
        _notify(store, current, "resolved", now, note=text[:600],
                jira_kind="resolve" if incident.get("jira_key") or incident.get("jira_state") == "PENDING" else None,
                dedupe=stamp)
    for child in store.children(incident_id):
        if child.get("status") in ACTIVE:
            try:
                resolve(store, child["incident_id"], f"Resolved with the upstream incident {incident_id[:8]}.", SYSTEM, now,
                        auto=True)
            except ActionError:
                pass
    return _get(store, incident_id)


MAX_MUTE_DAYS = 30


def mute(store: Any, incident_id: str, until: Any, reason: Optional[str], actor: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    incident = _get(store, incident_id)
    if incident["status"] == "RESOLVED":
        raise ActionError("A resolved incident cannot be muted.")
    end = _dt(until)
    if end is None or end <= now:
        raise ActionError("Mute until must be a time in the future.", 422)
    if end - now > timedelta(days=MAX_MUTE_DAYS):
        raise ActionError(f"A mute lasts at most {MAX_MUTE_DAYS} days.", 422)
    text = (reason or "").strip()[:500] or None
    store.update(incident_id, {"status": "MUTED", "muted_until": end.isoformat(), "mute_reason": text})
    store.event(incident_id, "muted", actor, {"until": end.isoformat(), "reason": text})
    return _get(store, incident_id)


def reopen(store: Any, incident_id: str, actor: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    incident = _get(store, incident_id)
    if incident["status"] in ("OPEN", "ACK"):
        return incident
    fields: Dict[str, Any] = {"status": "OPEN", "opened_at": now.isoformat(), "resolution": None, "resolved_by": None,
                              "resolved_at": None, "acked_by": None, "acked_at": None, "muted_until": None,
                              "mute_reason": None, "escalations": 0, "last_escalated_at": None}
    if incident.get("jira_key") and not incident.get("parent_incident_id"):
        fields["jira_state"] = REOPENED   # until the ticket is reopened, jira_sync leaves the incident open
    store.update(incident_id, fields)
    store.event(incident_id, "reopened", actor, {"previous_status": incident["status"],
                                                 "previous_resolution": incident.get("resolution")})
    if not incident.get("parent_incident_id"):
        jira = "reopen" if incident.get("jira_key") else None
        _notify(store, {**incident, "status": "OPEN"}, "opened", now, note=f"Reopened by {actor}.", jira_kind=jira,
                dedupe=now.isoformat())
    return _get(store, incident_id)


def comment(store: Any, incident_id: str, text: str, actor: str) -> Dict[str, Any]:
    _get(store, incident_id)
    body = (text or "").strip()
    if not body:
        raise ActionError("The comment is empty.", 422)
    store.event(incident_id, "comment", actor, {"text": redact(body)[:4000]})
    return _get(store, incident_id)
