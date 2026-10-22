"""Datadog monitors as ops incidents: a monitor group in Alert (and No Data, or Warn, when the DATADOG setting lists
them) opens an incident in CORE.INCIDENT with ENV_ID 'datadog', and the incident resolves itself when the group
recovers. Incidents ride on the ops incident engine (services.ops.incidents), so they get the same inbox, ack, mute,
escalation, reopen window, routing and Jira ticket as Airflow failures. No Teams: Jira and in-app only.

One incident per monitor and group (the fingerprint). Each trigger of a group is one sighting, claimed once (monitor,
group, state and the time Datadog says it triggered; No Data by the monitor's last state change, which Datadog does not
move on every evaluation), so a poll that reads the same trigger again changes nothing, and a group that recovers and
triggers again later is an occurrence or a reopen. An incident this module resolved (recovered, or the monitor left
the watched set) reopens when the group is seen alerting again, even without a new trigger; one a person resolved
stays resolved until the monitor triggers again.
Silenced: a group muted in Datadog (options.silenced) or under an active downtime opens nothing. A monitor with many
alerting groups opens at most MAX_GROUPS of them per poll (a multi-alert monitor over every host is not 1000 tickets).
The incident's DAG_ID is the monitor's dag_id tag when it has one (so it sits with the pipeline's own incidents and its
routing rules), else 'datadog: <monitor name>'; the monitor's tags are the DAG tags routing rules match (team:data).
Severity: the monitor's priority (P1 to P4, P5 counts as P4), else the setting's default_severity; one level less for
Warn and No Data.
Resolve: an open incident whose group is no longer in a chosen state resolves as 'system'; one whose monitor is gone
(deleted, or no longer has the monitor tags) resolves too, with that reason, but only when the whole list was read.
Nothing resolves when the read failed.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.ops import datadog as dd
from services.ops import detect
from services.ops import incidents as inc
from services.ops.redact import redact

log = logging.getLogger("gdp.ops.datadog")

DD_ENV = "datadog"
KIND = "DD_MONITOR"
DAG_ID_MAX = 250
NO_GROUP = "*"
MAX_GROUPS = 25
RESOLVED_HERE = ("Datadog monitor recovered", "the Datadog monitor no longer exists or no longer has the watched tags")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(monitor_id: Any, group: str) -> str:
    return hashlib.sha1(f"datadog\x1f{monitor_id}\x1f{group}".encode("utf-8")).hexdigest()


def tag_value(tags: List[str], name: str) -> Optional[str]:
    for t in tags:
        key, _, value = str(t).partition(":")
        if key == name and value:
            return value
    return None


def dag_id_for(monitor: Dict[str, Any]) -> str:
    tags = [str(t) for t in monitor.get("tags") or []]
    name = tag_value(tags, "dag_id") or f"datadog: {monitor.get('name') or monitor.get('id')}"
    if len(name) <= DAG_ID_MAX:
        return name
    return f"{name[:DAG_ID_MAX - 17]}~{hashlib.sha1(name.encode('utf-8')).hexdigest()[:16]}"


def _stamp(value: Any) -> Optional[str]:
    """Datadog's epoch (seconds or milliseconds) or ISO time as ISO UTC."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10 ** 11 else value
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    parsed = dd._dt(value)
    return parsed.isoformat() if parsed else None


def _scope_covers(scope: Any, group: str) -> bool:
    """A mute or downtime scope ('*', 'env:prod', ['env:prod', 'host:a']) covers the group when each of its tags is
    one of the group's ('env:prod,host:a')."""
    items = scope if isinstance(scope, list) else [scope]
    tags = set(str(group).split(",")) if group != NO_GROUP else set()
    for item in items:
        wanted = [t.strip() for t in str(item or "*").split(",") if t.strip()]
        if not wanted or wanted == ["*"] or set(wanted) <= tags:
            return True
    return False


def silenced(monitor: Dict[str, Any], group: str, now: datetime) -> bool:
    """Muted in Datadog (options.silenced: scope -> until, None for ever) or under an active downtime."""
    for scope, until in ((monitor.get("options") or {}).get("silenced") or {}).items():
        if (until is None or (isinstance(until, (int, float)) and until > now.timestamp())) and _scope_covers(scope, group):
            return True
    for d in monitor.get("matching_downtimes") or []:
        if isinstance(d, dict) and d.get("active") and _scope_covers(d.get("scope") or "*", group):
            return True
    return False


def alerting_groups(monitor: Dict[str, Any], states: List[str], now: Optional[datetime] = None
                    ) -> List[Tuple[str, str, Optional[str]]]:
    """(group, state, triggered at) of each group in one of `states` that is not silenced, at most MAX_GROUPS (most
    recently triggered first); a monitor without groups is one group '*'. The trigger time falls back to the monitor's
    last state change, then to the current hour, so a sighting key is never empty."""
    now = now or utcnow()
    changed = _stamp(monitor.get("overall_state_modified"))
    bucket = now.replace(minute=0, second=0, microsecond=0).isoformat()
    groups = (monitor.get("state") or {}).get("groups") or {}
    out: List[Tuple[str, str, Optional[str]]] = []
    if groups:
        for name, g in groups.items():
            status = (g or {}).get("status")
            if status in states:
                at = changed if status == "No Data" else _stamp(g.get("last_triggered_ts"))
                out.append((str(name or NO_GROUP), status, at or changed or bucket))
    elif monitor.get("overall_state") in states:
        out.append((NO_GROUP, monitor["overall_state"], changed or bucket))
    out = [g for g in out if not silenced(monitor, g[0], now)]
    out.sort(key=lambda g: g[2] or "", reverse=True)
    return out[:MAX_GROUPS]


def severity_for(monitor: Dict[str, Any], state: str, setting: Dict[str, Any]) -> str:
    priority = monitor.get("priority")
    base = (detect.SEVERITIES[min(int(priority), 4) - 1] if isinstance(priority, int) and priority >= 1
            else setting.get("default_severity") or "P3")
    if state == "Alert":
        return base
    i = detect.SEVERITIES.index(base) if base in detect.SEVERITIES else 2
    return detect.SEVERITIES[min(i + 1, len(detect.SEVERITIES) - 1)]


def candidate(monitor: Dict[str, Any], group: str, state: str, at: Optional[str], setting: Dict[str, Any],
              app_url: str, now: datetime, reopen_after: Optional[str] = None) -> Dict[str, Any]:
    """The incident candidate (the shape services.ops.incidents.open_or_update takes) for one alerting group. It is
    seen now (the monitor is alerting now); `reopen_after` (when this module resolved the group's last incident) makes
    it a new sighting of the same trigger, so that incident reopens."""
    mid = monitor.get("id")
    name = str(monitor.get("name") or f"monitor {mid}")
    tags = [str(t) for t in monitor.get("tags") or []]
    where = f" ({group})" if group != NO_GROUP else ""
    lines = [f"Datadog monitor: {name} (id {mid}, {monitor.get('type') or 'type unknown'})",
             f"State: {state}{where}", f"Triggered: {at or 'unknown'}"]
    if monitor.get("query"):
        lines.append(f"Query: {str(monitor['query'])[:1000]}")
    if tags:
        lines.append(f"Tags: {', '.join(tags[:30])}")
    if monitor.get("message"):
        lines.append(f"Message: {str(monitor['message'])[:1500]}")
    lines.append(f"Datadog: {dd.monitor_link(app_url, mid)}")
    return {"env_id": DD_ENV, "dag_id": dag_id_for(monitor), "task_id": tag_value(tags, "task_id"), "map_index": None,
            "run_id": None, "kind": KIND, "fingerprint": fingerprint(mid, group),
            "severity": severity_for(monitor, state, setting),
            "title": f"Datadog {state}: {name}{where}"[:500], "error_excerpt": redact("\n".join(lines))[:4000],
            "at": now.isoformat(),
            "occurrence_key": f"{mid}:{group}:{state}:{at or ''}" + (f":after:{reopen_after}" if reopen_after else "")}


def owner_dag(monitor: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    tags = [str(t) for t in monitor.get("tags") or []]
    return {"env_id": DD_ENV, "dag_id": cand["dag_id"], "tags": tags,
            "owners": [v for v in (tag_value(tags, "owner"), tag_value(tags, "team")) if v]}


def process(store: Any, cli: Optional[dd.Client] = None, setting: Optional[Dict[str, Any]] = None,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Open or update an incident for every alerting group, then resolve the ones that recovered. Idempotent."""
    now = now or utcnow()
    setting = setting or dd.setting(store.db)
    if not setting.get("enabled") or not setting.get("monitors"):
        return {"disabled": True}
    cli = cli or dd.client()
    monitors = cli.monitors(setting.get("monitor_tags") or [], setting.get("monitor_states"))   # raises: nothing resolves
    complete = len(monitors) < dd.MONITOR_PAGE * dd.MONITOR_PAGES   # the whole list: a missing monitor is really gone
    settings, rules = store.settings(), store.rules()
    states = list(setting.get("monitor_states") or dd.DEFAULT_SETTING["monitor_states"])
    alerting: Dict[str, Dict[str, Any]] = {}
    watched = {str(m.get("id")) for m in monitors}
    actions: Dict[str, int] = {}
    errors: List[str] = []
    for m in monitors:
        for group, state, at in alerting_groups(m, states, now):
            fp = fingerprint(m.get("id"), group)
            alerting[fp] = {}   # alerting: never resolved below, even when this sighting fails to apply
            try:
                before = store.latest_by_fingerprint(fp)
                again = bool(before and before.get("status") == "RESOLVED" and before.get("resolved_by") == inc.SYSTEM
                             and before.get("resolution") in RESOLVED_HERE)
                cand = candidate(m, group, state, at, setting, cli.cfg["app_url"], now,
                                 reopen_after=str(before.get("resolved_at")) if again else None)
                alerting[fp] = cand
                result = inc.open_or_update(store, cand, settings, owner_dag(m, cand), rules, now)
                if result["action"] == "occurrence" and before and before.get("title") != cand["title"]:
                    # Alert after No Data (or the other way): the incident now says what the monitor says
                    store.update(before["incident_id"], {"title": cand["title"], "error_excerpt": cand["error_excerpt"],
                                                         "severity": min((s for s in (before.get("severity"), cand["severity"])
                                                                          if s in detect.SEVERITIES),
                                                                         key=detect.SEVERITIES.index, default=cand["severity"])})
                    store.event(before["incident_id"], "state_changed", inc.SYSTEM, {"title": cand["title"]})
            except Exception as exc:   # one monitor never holds up the others
                errors.append(redact(f"{m.get('id')}: {type(exc).__name__}: {exc}")[:300])
                continue
            actions[result["action"]] = actions.get(result["action"], 0) + 1
    resolved = 0
    for row in store.active_datadog():
        if row.get("fingerprint") in alerting:
            continue
        monitor_id = str(row.get("monitor_id") or "")
        if monitor_id and monitor_id not in watched and not complete:
            continue   # the list was cut at the page cap: the monitor may be on a page not read
        reason = RESOLVED_HERE[0] if monitor_id in watched or not monitor_id else RESOLVED_HERE[1]
        try:
            inc.resolve(store, row["incident_id"], reason, inc.SYSTEM, now, auto=True)
            resolved += 1
        except inc.ActionError:
            pass
        except Exception as exc:   # one incident never holds up the others
            errors.append(redact(f"resolve {row.get('incident_id')}: {type(exc).__name__}: {exc}")[:300])
    summary: Dict[str, Any] = {"monitors": len(monitors), "alerting": len(alerting), "actions": actions, "resolved": resolved}
    if errors:
        summary.update({"skipped": len(errors), "errors": errors[:5]})
    return summary


class DatadogStore(inc.SqlStore):
    """The incident store plus the read monitor alerting needs."""

    def active_datadog(self) -> List[Dict[str, Any]]:
        """Open Datadog incidents with the monitor id from their excerpt ('(id <n>, ...')."""
        found = self.db.query("""SELECT INCIDENT_ID, FINGERPRINT, STATUS,
                                        REGEXP_SUBSTR(ERROR_EXCERPT, '\\\\(id ([0-9]+),', 1, 1, 'e', 1) AS MONITOR_ID
                                   FROM CORE.INCIDENT WHERE ENV_ID = %s AND STATUS IN ('OPEN', 'ACK', 'MITIGATED', 'MUTED')""",
                              (DD_ENV,))
        return [inc._row(r) or {} for r in found]
