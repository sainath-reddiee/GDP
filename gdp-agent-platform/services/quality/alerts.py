"""Data quality alerting: failing checks, stale tables and a daily digest reach people without opening the DQ Store.

Incidents ride on the ops incident engine (services.ops.incidents) in CORE.INCIDENT with ENV_ID 'dq' and DAG_ID the
table, so they get the same inbox, ack, mute, escalation, reopen window, Jira ticket and timeline as Airflow failures:
  DQ_FAILED  a table whose current state (the latest result of each ACTIVE check) has a FAIL or ERROR check, or a WARN
             check that warned WARN_AFTER_RUNS scans in a row (CORE.DQ_ALERT_ROUTE, default 3; 0 never);
  DQ_STALE   a failed freshness check, or a scheduled scan (CORE.DQ_SCHEDULE) that did not run by its cron time plus
             STALE_GRACE_MINUTES.
One incident per table and kind (the fingerprint). The failing check set is part of the excerpt ("Check set: <hash>"):
a scan that changes the set updates the incident (timeline event checks_changed, title, severity); the same set seen
again is an occurrence. Each sighting is claimed once (the scan id), so a scan read twice changes nothing.
A later state with nothing failing resolves the incident as 'system'. The state is per check, so a scan of a few
chosen checks never resolves a failure it did not re-run.
Routing: a routing rule (CORE.ROUTING_RULE) whose tag is the domain's name or id, or whose pattern matches the table,
wins; else the domain's team in CORE.DQ_ALERT_ROUTE; else unrouted (still ticketed to the Jira default project).
Severity: FAIL_SEVERITY (default P2) for FAIL checks; one level lower for ERROR or WARN only, and for stale tables.

Digest: once a day (ops setting dq_digest_hour, UTC, default 6) one CORE.DQ_DIGEST row per domain with active checks:
pass rate now and a week ago, newly failing checks (failing now, not 24 hours ago), still failing, stale tables and
open incidents. With DIGEST_JIRA on the route it is also filed as a Jira issue through the outbox (kind dq_digest).

Every database access goes through a store (DqStore over the API's Db, or an in-memory one in tests).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from services.ops import detect
from services.ops import incidents as inc
from services.ops.redact import redact

log = logging.getLogger("gdp.quality.alerts")
DQ_ENV = "dq"
KIND_FAILED = "DQ_FAILED"
KIND_STALE = "DQ_STALE"
KINDS = (KIND_FAILED, KIND_STALE)
FAILING = ("FAIL", "ERROR")
MISSED = "Missed scan"           # title prefix of a stale incident raised by a scan that did not run
DAG_ID_MAX = 250                 # CORE.INCIDENT.DAG_ID
DEFAULT_ROUTE = {"team_id": None, "fail_severity": "P2", "warn_after_runs": 3, "stale_grace_minutes": 120,
                 "digest_enabled": True, "digest_jira": False}
DEFAULT_DIGEST_HOUR = 6
MISSED_SLACK = timedelta(minutes=5)
SCAN_LIMIT = 500
DIGEST_LIST_MAX = 25


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: Any) -> Optional[datetime]:
    return inc._dt(value)


# ---------------------------------------------------------------- pure: identity, links, routes

def dag_id_for(table: str) -> str:
    """The incident's DAG_ID for a table: the table itself, or a prefix and a hash when it is longer than the column."""
    name = str(table or "").upper()
    if len(name) <= DAG_ID_MAX:
        return name
    return f"{name[:DAG_ID_MAX - 17]}~{hashlib.sha1(name.encode('utf-8')).hexdigest()[:16]}"


def fingerprint(kind: str, table: str) -> str:
    return hashlib.sha1(f"dq\x1f{kind}\x1f{str(table or '').upper()}".encode("utf-8")).hexdigest()


def suite_link(base: Optional[str], table: str) -> str:
    """The DQ Store suite page of the table (absolute when the platform's public URL is set)."""
    return f"{(base or '').rstrip('/')}/dashboard/dq-store/suite?table={quote(str(table or ''), safe='')}"


def table_of(incident: Dict[str, Any]) -> str:
    """The table a DQ incident is about: the excerpt's "Table:" line (DAG_ID may be shortened)."""
    for line in str(incident.get("error_excerpt") or "").splitlines():
        if line.startswith("Table: "):
            return line[len("Table: "):].strip()
    return str(incident.get("dag_id") or "")


def is_dq(incident: Dict[str, Any]) -> bool:
    return str(incident.get("env_id") or "") == DQ_ENV or str(incident.get("kind") or "") in KINDS


def route_for(routes: Dict[str, Dict[str, Any]], domain_id: Optional[str]) -> Dict[str, Any]:
    out = dict(DEFAULT_ROUTE)
    for k, v in (routes.get(domain_id or "") or {}).items():
        if v is not None:
            out[k] = v
    out["warn_after_runs"] = max(0, int(out.get("warn_after_runs") or 0))
    out["stale_grace_minutes"] = max(0, int(out.get("stale_grace_minutes") or 0))
    sev = str(out.get("fail_severity") or "P2").upper()
    out["fail_severity"] = sev if sev in detect.SEVERITIES else "P2"
    return out


def lower(severity: str) -> str:
    """One level less urgent (P4 stays P4)."""
    i = detect.SEVERITIES.index(severity) if severity in detect.SEVERITIES else 1
    return detect.SEVERITIES[min(i + 1, len(detect.SEVERITIES) - 1)]


def higher_of(a: Optional[str], b: Optional[str]) -> str:
    ranks = [s for s in (a, b) if s in detect.SEVERITIES]
    return min(ranks, key=detect.SEVERITIES.index) if ranks else "P3"


def owner_dag(table: str, domain: Dict[str, Any], route: Dict[str, Any]) -> Dict[str, Any]:
    """What incident routing matches on: the table as the DAG id, the domain's name and id as tags, its owner."""
    tags = [t for t in (domain.get("domain_name"), domain.get("domain_id")) if t]
    owners = [domain["owner"]] if domain.get("owner") else []
    return {"env_id": DQ_ENV, "dag_id": dag_id_for(table), "team_id": route.get("team_id"), "tags": tags, "owners": owners}


# ---------------------------------------------------------------- pure: classifying a table's state

def is_freshness(check: Dict[str, Any]) -> bool:
    return "freshness" in {str(check.get(k) or "").lower() for k in ("kind", "check_type")} \
        or str(check.get("dimension") or "").lower() == "timeliness"


def state_of(history: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The latest result of each check, with `streak`: how many of its newest results in a row were WARN.
    `history` holds a few newest results per check (catalog_check_id, outcome, created_at, ...)."""
    by_check: Dict[str, List[Dict[str, Any]]] = {}
    for r in history:
        by_check.setdefault(str(r.get("catalog_check_id") or ""), []).append(r)
    out: List[Dict[str, Any]] = []
    for check_id, results in by_check.items():
        if not check_id:
            continue
        results.sort(key=lambda r: _dt(r.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        streak = 0
        for r in results:
            if str(r.get("outcome") or "").upper() != "WARN":
                break
            streak += 1
        out.append({**results[0], "outcome": str(results[0].get("outcome") or "").upper(), "streak": streak})
    return sorted(out, key=lambda r: (str(r.get("target_column") or ""), str(r.get("check_type") or ""), str(r["catalog_check_id"])))


def classify(state: List[Dict[str, Any]], warn_after: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(failing, stale): the checks that raise DQ_FAILED and the failed freshness checks that raise DQ_STALE."""
    failing: List[Dict[str, Any]] = []
    stale: List[Dict[str, Any]] = []
    for r in state:
        outcome = r["outcome"]
        if is_freshness(r):
            if outcome in FAILING:
                stale.append(r)
            continue
        if outcome in FAILING or (outcome == "WARN" and warn_after > 0 and int(r.get("streak") or 0) >= warn_after):
            failing.append(r)
    return failing, stale


def check_set(checks: Iterable[Dict[str, Any]]) -> str:
    """A short hash of which checks fail and how (the dedupe part of a DQ incident)."""
    keys = sorted(f"{c.get('catalog_check_id')}|{c.get('outcome')}" for c in checks)
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:12]


def set_in(excerpt: Optional[str]) -> Optional[str]:
    for line in str(excerpt or "").splitlines():
        if line.startswith("Check set: "):
            return line[len("Check set: "):].strip()
    return None


def _num(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f.is_integer() else f"{f:.4g}"


def check_line(c: Dict[str, Any]) -> str:
    """One failing check: outcome, column and check, measured value against the threshold, failing rows."""
    outcome = c["outcome"] + (f" x{c['streak']}" if c["outcome"] == "WARN" and int(c.get("streak") or 0) > 1 else "")
    what = " / ".join(p for p in (c.get("target_column") or "table", c.get("check_type") or c.get("kind") or "check") if p)
    text = f"{outcome:<7} {what}: measured {_num(c.get('measured'))} vs threshold {c.get('threshold') or 'n/a'}"
    if c.get("failed_rows"):
        text += f" ({_num(c['failed_rows'])} failing rows)"
    if c["outcome"] == "ERROR" and c.get("detail"):
        text += f": {str(c['detail'])[:200]}"
    return text


def severity_for(kind: str, checks: List[Dict[str, Any]], route: Dict[str, Any]) -> str:
    base = route["fail_severity"]
    if kind == KIND_STALE:
        return lower(base)
    return base if any(c["outcome"] == "FAIL" for c in checks) else lower(base)


def candidate(kind: str, table: str, checks: List[Dict[str, Any]], route: Dict[str, Any], *, at: Any,
              occurrence: str, run_id: Optional[str], base_url: Optional[str], note: Optional[str] = None,
              title: Optional[str] = None) -> Dict[str, Any]:
    """The incident candidate (the shape services.ops.incidents.open_or_update takes) for a table's failing checks."""
    short = table.split(".")[-1] if table else table
    if title is None:
        n = len(checks)
        title = (f"Data quality: {n} failing check{'s' if n != 1 else ''} on {short}" if kind == KIND_FAILED
                 else f"Stale data: freshness check failing on {short}")
    lines = [f"Table: {table}"]
    if note:
        lines.append(note)
    lines += [check_line(c) for c in checks[:40]]
    if len(checks) > 40:
        lines.append(f"... and {len(checks) - 40} more")
    lines += [f"DQ Store: {suite_link(base_url, table)}", f"Check set: {check_set(checks) if checks else 'none'}"]
    return {"env_id": DQ_ENV, "dag_id": dag_id_for(table), "task_id": None, "map_index": None, "run_id": run_id,
            "kind": kind, "fingerprint": fingerprint(kind, table), "severity": severity_for(kind, checks, route),
            "title": f"{title} ({table})"[:500], "error_excerpt": redact("\n".join(lines))[:4000], "at": at,
            "occurrence_key": f"{kind}:{occurrence}"}


# ---------------------------------------------------------------- applying

def _active(store: Any, kind: str, table: str) -> Optional[Dict[str, Any]]:
    found = store.latest_by_fingerprint(fingerprint(kind, table))
    return found if found and found.get("status") in inc.ACTIVE else None


def raise_incident(store: Any, cand: Dict[str, Any], dag: Dict[str, Any], rules: List[Dict[str, Any]],
                   settings: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Open, reopen or update the table's incident for this sighting. A changed check set updates the title, the
    severity (never less urgent) and the timeline."""
    before = _active(store, cand["kind"], table_of(cand))
    result = inc.open_or_update(store, cand, settings, dag, rules, now)
    if result["action"] == "occurrence" and before:
        old, new = set_in(before.get("error_excerpt")), set_in(cand["error_excerpt"])
        if old != new:
            severity = higher_of(before.get("severity"), cand["severity"])
            store.update(before["incident_id"], {"title": cand["title"], "severity": severity,
                                                 "error_excerpt": cand["error_excerpt"]})
            store.event(before["incident_id"], "checks_changed", inc.SYSTEM,
                        {"from": old, "to": new, "severity": severity})
            result = {**result, "changed": True}
    return result


def resolve_if_open(store: Any, kind: str, table: str, reason: str, when: datetime) -> Optional[str]:
    found = _active(store, kind, table)
    if not found:
        return None
    opened = _dt(found.get("opened_at"))
    try:
        inc.resolve(store, found["incident_id"], reason, inc.SYSTEM, max(when, opened) if opened else when, auto=True)
    except inc.ActionError:
        return None
    return found["incident_id"]


def evaluate_table(store: Any, scan: Dict[str, Any], history: List[Dict[str, Any]], route: Dict[str, Any],
                   domain: Dict[str, Any], rules: List[Dict[str, Any]], settings: Dict[str, Any],
                   now: datetime) -> Dict[str, Any]:
    """Raise or resolve the table's DQ_FAILED and DQ_STALE incidents from its current state after `scan`."""
    table = str(scan["table_fqn"])
    state = state_of(history)
    failing, stale = classify(state, route["warn_after_runs"])
    in_scan = {str(r.get("catalog_check_id")) for r in history if r.get("scan_id") == scan["scan_id"]}
    at = inc._iso(scan.get("started_at")) or now.isoformat()
    when = _dt(at) or now
    when = min(when, now)
    dag = owner_dag(table, domain, route)
    base = settings.get("public_base_url")
    out: Dict[str, Any] = {}
    for kind, checks in ((KIND_FAILED, failing), (KIND_STALE, stale)):
        if checks:
            # a sighting only when this scan evaluated one of the checks: a scan of other checks says nothing new
            if any(str(c.get("catalog_check_id")) in in_scan for c in checks):
                cand = candidate(kind, table, checks, route, at=at, occurrence=scan["scan_id"], run_id=scan["scan_id"],
                                 base_url=base)
                out[kind] = raise_incident(store, cand, dag, rules, settings, now)["action"]
            continue
        if kind == KIND_STALE:
            reason = f"Resolved automatically: scan {scan['scan_id'][:8]} ran and no freshness check is failing."
        else:
            reason = f"Resolved automatically: no check on the table is failing after scan {scan['scan_id'][:8]}."
        if resolve_if_open(store, kind, table, reason, when):
            out[kind] = "resolved"
    return out


def process_scans(store: Any, settings: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Evaluate every table scanned since the cursor (its newest scan), then move the cursor on. Idempotent."""
    now = now or utcnow()
    settings = settings or store.settings()
    if settings.get("dq_alerts") is False:
        return {"tables": 0, "disabled": True}
    since = store.scan_cursor()
    scans, newest = store.scans_since(since, SCAN_LIMIT)
    latest: Dict[str, Dict[str, Any]] = {}
    for s in scans:
        key = str(s.get("table_fqn") or "").upper()
        if key and (key not in latest or (_dt(s.get("started_at")) or now) >= (_dt(latest[key].get("started_at")) or now)):
            latest[key] = s
    actions: Dict[str, int] = {}
    errors: List[str] = []
    if latest:
        routes, domains, rules = store.routes(), store.domains(), store.rules()
        for scan in latest.values():
            route = route_for(routes, scan.get("domain_id"))
            domain = domains.get(scan.get("domain_id") or "") or {"domain_id": scan.get("domain_id")}
            try:
                history = store.table_history(scan["table_fqn"], max(1, route["warn_after_runs"]))
                result = evaluate_table(store, scan, history, route, domain, rules, settings, now)
            except Exception as exc:   # one table never holds up the others; the cursor still moves on
                text = redact(f"{type(exc).__name__}: {exc}")[:300]
                log.warning("dq alert for %s skipped: %s", scan.get("table_fqn"), text)
                errors.append(text)
                continue
            for kind, action in result.items():
                actions[f"{kind}:{action}"] = actions.get(f"{kind}:{action}", 0) + 1
    if newest and newest != since:
        store.set_scan_cursor(newest)
    summary: Dict[str, Any] = {"tables": len(latest), "actions": actions}
    if errors:
        summary.update({"skipped": len(errors), "errors": errors[:5]})
    return summary


# ---------------------------------------------------------------- missed scheduled scans

def split_cron(value: str) -> Tuple[str, Optional[str]]:
    """'0 6 * * * Europe/London' -> ('0 6 * * *', 'Europe/London'); no zone -> UTC."""
    parts = str(value or "").split()
    return " ".join(parts[:5]), (parts[5] if len(parts) > 5 else None)


def missed_due(schedule: Dict[str, Any], grace_minutes: int, now: datetime) -> Optional[datetime]:
    """The cron time a scheduled scan missed (its fire time plus the grace passed with no scan since), or None."""
    expr, tz = split_cron(schedule.get("cron") or "")
    try:
        due = detect.prev_fire(expr, now - timedelta(minutes=grace_minutes), tz)
    except ValueError:
        return None
    if due is None:
        return None
    since = _dt(schedule.get("updated_at"))
    if since and since > due:
        return None   # scheduled (or changed) after that fire time
    last = _dt(schedule.get("last_scan_at"))
    if last and last >= due - MISSED_SLACK:
        return None
    return due


def check_schedules(store: Any, settings: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """DQ_STALE for every enabled schedule whose scan did not run by its cron time plus the domain's grace."""
    now = now or utcnow()
    settings = settings or store.settings()
    if settings.get("dq_alerts") is False:
        return {"schedules": 0, "disabled": True}
    schedules = store.schedules()
    if not schedules:
        return {"schedules": 0, "actions": {}}
    routes, domains, rules = store.routes(), store.domains(), store.rules()
    actions: Dict[str, int] = {}
    for s in schedules:
        route = route_for(routes, s.get("domain_id"))
        due = missed_due(s, route["stale_grace_minutes"], now)
        if due is None:
            continue
        table = str(s["table_name"])
        domain = domains.get(s.get("domain_id") or "") or {"domain_id": s.get("domain_id")}
        last = inc._iso(s.get("last_scan_at"))
        note = (f"The scheduled scan ({s.get('cron')}) due at {due.isoformat()} did not run"
                + (f"; the last scan ran at {last}." if last else "; the table was never scanned."))
        cand = candidate(KIND_STALE, table, [], route, at=(due + timedelta(minutes=route["stale_grace_minutes"])).isoformat(),
                         occurrence=f"missed:{due.strftime('%Y%m%d%H%M')}", run_id=None,
                         base_url=settings.get("public_base_url"), note=note,
                         title=f"{MISSED}: the scheduled data quality scan of {table.split('.')[-1]} did not run")
        try:
            result = raise_incident(store, cand, owner_dag(table, domain, route), rules, settings, now)
        except Exception as exc:
            log.warning("missed-scan alert for %s skipped: %s", table, redact(f"{type(exc).__name__}: {exc}")[:300])
            continue
        actions[result["action"]] = actions.get(result["action"], 0) + 1
    return {"schedules": len(schedules), "actions": actions}


# ---------------------------------------------------------------- daily digest

def _rate(passed: int, evaluated: int) -> Optional[float]:
    return round(100.0 * passed / evaluated, 1) if evaluated else None


def _pass_rate(outcomes: Iterable[Optional[str]]) -> Tuple[Optional[float], int]:
    seen = [str(o).upper() for o in outcomes if o and str(o).upper() in ("PASS", "WARN", "FAIL")]
    return _rate(sum(1 for o in seen if o == "PASS"), len(seen)), len(seen)


def digest(domain: Dict[str, Any], rows: List[Dict[str, Any]], day: date, open_incidents: int = 0,
           stale_tables: Optional[List[str]] = None, base_url: Optional[str] = None) -> Dict[str, Any]:
    """One domain's digest from its active checks: each row has the check (catalog_check_id, table_name,
    target_column, check_type), its latest result (outcome, measured, threshold, last_at), its latest result from
    before the last 24 hours (day_outcome) and from before 7 days ago (week_outcome)."""
    rate_now, evaluated = _pass_rate(r.get("outcome") for r in rows)
    rate_week, evaluated_week = _pass_rate(r.get("week_outcome") for r in rows)
    failing = [r for r in rows if str(r.get("outcome") or "").upper() in FAILING]
    newly = [r for r in failing if str(r.get("day_outcome") or "").upper() not in FAILING]
    still = [r for r in failing if str(r.get("day_outcome") or "").upper() in FAILING]
    recovered = [r for r in rows if str(r.get("day_outcome") or "").upper() in FAILING
                 and str(r.get("outcome") or "").upper() == "PASS"]

    def item(r: Dict[str, Any]) -> Dict[str, Any]:
        return {"check_id": r.get("catalog_check_id"), "table": r.get("table_name"), "column": r.get("target_column"),
                "check": r.get("check_type"), "outcome": str(r.get("outcome") or "").upper(),
                "measured": r.get("measured"), "threshold": r.get("threshold"), "at": inc._iso(r.get("last_at")),
                "link": suite_link(base_url, str(r.get("table_name") or ""))}

    trend = round(rate_now - rate_week, 1) if rate_now is not None and rate_week is not None else None
    return {
        "domain_id": domain.get("domain_id"), "domain_name": domain.get("domain_name") or domain.get("domain_id"),
        "owner": domain.get("owner"), "date": day.isoformat(),
        "checks": len(rows), "tables": len({r.get("table_name") for r in rows}), "evaluated": evaluated,
        "never_run": sum(1 for r in rows if not r.get("outcome")),
        "pass_rate": rate_now, "pass_rate_week_ago": rate_week, "evaluated_week_ago": evaluated_week, "trend": trend,
        "failing": len(failing), "newly_failing": [item(r) for r in newly[:DIGEST_LIST_MAX]], "new_failing": len(newly),
        "still_failing": len(still), "recovered": len(recovered), "open_incidents": int(open_incidents or 0),
        "stale_tables": sorted(stale_tables or [])[:DIGEST_LIST_MAX],
        "link": f"{(base_url or '').rstrip('/')}/dashboard/dq-store",
    }


def digest_summary(d: Dict[str, Any]) -> str:
    rate = "no results yet" if d.get("pass_rate") is None else f"{d['pass_rate']}% passing"
    trend = d.get("trend")
    if trend is not None:
        rate += f" ({'+' if trend >= 0 else ''}{trend} pts vs last week)"
    return f"Data quality digest for {d.get('domain_name')}, {d.get('date')}: {rate}, {d.get('new_failing', 0)} newly failing"


def digest_text(d: Dict[str, Any]) -> str:
    """The digest as plain text (Jira, logs)."""
    lines = [digest_summary(d) + ".",
             f"{d.get('checks', 0)} active checks on {d.get('tables', 0)} tables; {d.get('failing', 0)} failing now "
             f"({d.get('still_failing', 0)} still failing, {d.get('recovered', 0)} recovered since yesterday); "
             f"{d.get('open_incidents', 0)} open data quality incidents."]
    if d.get("newly_failing"):
        lines.append("Newly failing:")
        lines += [f"- {i.get('table')} {i.get('column') or ''} {i.get('check') or ''}: {i.get('outcome')}, measured "
                  f"{_num(i.get('measured'))} vs threshold {i.get('threshold') or 'n/a'}" for i in d["newly_failing"]]
    if d.get("stale_tables"):
        lines.append("Stale or missed scans: " + ", ".join(d["stale_tables"]))
    lines.append(f"DQ Store: {d.get('link')}")
    return "\n".join(" ".join(line.split()) if not line.startswith("-") else line for line in lines)


def run_digests(store: Any, settings: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Once a day, after dq_digest_hour (UTC): store each domain's digest and queue its deliveries."""
    now = now or utcnow()
    settings = settings or store.settings()
    if settings.get("dq_alerts") is False:
        return {"due": False, "disabled": True}
    try:
        hour = int(settings.get("dq_digest_hour", DEFAULT_DIGEST_HOUR))
    except (TypeError, ValueError):
        hour = DEFAULT_DIGEST_HOUR
    if now.hour < hour:
        return {"due": False}
    day = now.date()
    rows = store.digest_rows()
    by_domain: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if r.get("domain_id"):
            by_domain.setdefault(str(r["domain_id"]), []).append(r)
    if not by_domain:
        return {"due": True, "domains": 0, "stored": 0}
    routes, domains = store.routes(), store.domains()
    open_by, stale_by = store.open_dq_incidents()
    stored = queued = 0
    for domain_id, checks in by_domain.items():
        route = route_for(routes, domain_id)
        if not route.get("digest_enabled"):
            continue
        domain = domains.get(domain_id) or {"domain_id": domain_id}
        tables = {str(c.get("table_name") or "").upper() for c in checks}
        content = digest(domain, checks, day, sum(n for t, n in open_by.items() if t in tables),
                         [t for t in stale_by if t in tables], settings.get("public_base_url"))
        if not store.save_digest(domain_id, day, route.get("team_id"), domain.get("owner"), content):
            continue   # today's digest is already stored (and queued)
        stored += 1
        if route.get("digest_jira"):
            queued += bool(store.enqueue("JIRA", "dq_digest", f"dq:digest:jira:{domain_id}:{day.isoformat()}", content,
                                         team_id=route.get("team_id")))
    return {"due": True, "domains": len(by_domain), "stored": stored, "queued": queued}


# ---------------------------------------------------------------- the SQL store

class DqStore(inc.SqlStore):
    """The incident store plus the data quality reads and writes alerting needs (CORE.CHECK_RUN, CHECK_RESULT,
    DQ_CHECKS_CATALOG, DQ_SCHEDULE, DQ_ALERT_ROUTE, DQ_DIGEST)."""

    CURSOR_JOB = "dq_alerts"
    OVERLAP = timedelta(minutes=2)

    def scan_cursor(self) -> Optional[str]:
        found = self.db.query("SELECT CURSOR_VALUE FROM CORE.JOB_LEASE WHERE JOB_NAME = %s", (self.CURSOR_JOB,))
        return (found[0].get("cursor_value") if found else None) or None

    def set_scan_cursor(self, value: str) -> None:
        self.db.execute("UPDATE CORE.JOB_LEASE SET CURSOR_VALUE = %s WHERE JOB_NAME = %s", (value, self.CURSOR_JOB))

    def scans_since(self, since: Optional[str], limit: int = SCAN_LIMIT) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Table scans stored after the cursor (minus a small overlap; claims make a second read harmless; the last
        day when there is no cursor) and the newest STARTED_AT read."""
        start = _dt(since)
        bound = "TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)" if start else "DATEADD(day, -1, CURRENT_TIMESTAMP())"
        params: Tuple[Any, ...] = ((start - self.OVERLAP).isoformat(),) if start else ()
        found = self.db.query(f"""SELECT SCAN_ID, TABLE_FQN, DOMAIN_ID, STARTED_AT, CHECKS, PASSED, WARNED, FAILED, ERRORS,
                                         TRIGGERED_BY FROM CORE.CHECK_RUN
                                   WHERE SCOPE = 'TABLE' AND TABLE_FQN IS NOT NULL AND STARTED_AT > {bound}
                                   ORDER BY STARTED_AT LIMIT {int(limit)}""", params)
        rows = [inc._row(r) or {} for r in found]
        stamps = [_dt(r.get("started_at")) for r in rows if r.get("started_at")]
        newest = max(stamps).isoformat() if stamps else since
        if start and newest and _dt(newest) and _dt(newest) < start:
            newest = since
        return rows, newest

    def table_history(self, table: str, per_check: int) -> List[Dict[str, Any]]:
        """The newest `per_check` results of each ACTIVE catalog check of the table."""
        found = self.db.query(f"""
            SELECT R.CATALOG_CHECK_ID, R.SCAN_ID, R.TARGET_COLUMN, R.CHECK_TYPE, R.KIND, R.DIMENSION, R.SEVERITY, R.OUTCOME,
                   R.MEASURED, R.THRESHOLD, R.FAILED_ROWS, R.DETAIL, R.CREATED_AT
              FROM CORE.CHECK_RESULT R JOIN CORE.DQ_CHECKS_CATALOG C ON C.CHECK_ID = R.CATALOG_CHECK_ID
             WHERE C.TABLE_NAME = %s AND C.STATUS = 'ACTIVE'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY R.CATALOG_CHECK_ID ORDER BY R.CREATED_AT DESC) <= {int(per_check)}""",
                              (table,))
        return [inc._row(r) or {} for r in found]

    def routes(self) -> Dict[str, Dict[str, Any]]:
        try:
            found = self.db.query("""SELECT DOMAIN_ID, TEAM_ID, FAIL_SEVERITY, WARN_AFTER_RUNS, STALE_GRACE_MINUTES,
                                            DIGEST_ENABLED, DIGEST_JIRA FROM CORE.DQ_ALERT_ROUTE""")
        except Exception:
            return {}   # before V069: every domain gets the defaults, unrouted
        return {r["domain_id"]: r for r in found}

    def domains(self) -> Dict[str, Dict[str, Any]]:
        return {r["domain_id"]: r for r in self.db.query(
            "SELECT DOMAIN_ID, DOMAIN_NAME, OWNER FROM CORE.DOMAIN_REGISTRY WHERE COALESCE(ACTIVE_FLAG, TRUE)")}

    def schedules(self) -> List[Dict[str, Any]]:
        found = self.db.query("""
            SELECT S.TABLE_NAME, S.CRON, S.UPDATED_AT, D.DOMAIN_ID, L.LAST_SCAN_AT
              FROM CORE.DQ_SCHEDULE S
              LEFT JOIN (SELECT TABLE_NAME, MAX(DOMAIN_ID) AS DOMAIN_ID FROM CORE.DQ_CHECKS_CATALOG
                          WHERE STATUS = 'ACTIVE' GROUP BY TABLE_NAME) D ON D.TABLE_NAME = S.TABLE_NAME
              LEFT JOIN (SELECT TABLE_FQN, MAX(STARTED_AT) AS LAST_SCAN_AT FROM CORE.CHECK_RUN
                          WHERE SCOPE = 'TABLE' GROUP BY TABLE_FQN) L ON L.TABLE_FQN = S.TABLE_NAME
             WHERE S.ENABLED""")
        return [inc._row(r) or {} for r in found]

    def digest_rows(self) -> List[Dict[str, Any]]:
        found = self.db.query("""
            WITH C AS (SELECT CHECK_ID, TABLE_NAME, COLUMN_NAME, CHECK_TYPE, DOMAIN_ID FROM CORE.DQ_CHECKS_CATALOG
                        WHERE STATUS = 'ACTIVE' AND DOMAIN_ID IS NOT NULL),
                 R AS (SELECT R.CATALOG_CHECK_ID, R.OUTCOME, R.MEASURED, R.THRESHOLD, R.CREATED_AT FROM CORE.CHECK_RESULT R
                        WHERE R.CATALOG_CHECK_ID IN (SELECT CHECK_ID FROM C)
                          AND R.CREATED_AT >= DATEADD(day, -35, CURRENT_TIMESTAMP())),
                 N AS (SELECT * FROM R QUALIFY ROW_NUMBER() OVER (PARTITION BY CATALOG_CHECK_ID ORDER BY CREATED_AT DESC) = 1),
                 D AS (SELECT CATALOG_CHECK_ID, OUTCOME FROM R WHERE CREATED_AT < DATEADD(hour, -24, CURRENT_TIMESTAMP())
                       QUALIFY ROW_NUMBER() OVER (PARTITION BY CATALOG_CHECK_ID ORDER BY CREATED_AT DESC) = 1),
                 W AS (SELECT CATALOG_CHECK_ID, OUTCOME FROM R WHERE CREATED_AT < DATEADD(day, -7, CURRENT_TIMESTAMP())
                       QUALIFY ROW_NUMBER() OVER (PARTITION BY CATALOG_CHECK_ID ORDER BY CREATED_AT DESC) = 1)
            SELECT C.CHECK_ID AS CATALOG_CHECK_ID, C.TABLE_NAME, C.COLUMN_NAME AS TARGET_COLUMN, C.CHECK_TYPE, C.DOMAIN_ID,
                   N.OUTCOME, N.MEASURED, N.THRESHOLD, N.CREATED_AT AS LAST_AT, D.OUTCOME AS DAY_OUTCOME,
                   W.OUTCOME AS WEEK_OUTCOME
              FROM C LEFT JOIN N ON N.CATALOG_CHECK_ID = C.CHECK_ID LEFT JOIN D ON D.CATALOG_CHECK_ID = C.CHECK_ID
              LEFT JOIN W ON W.CATALOG_CHECK_ID = C.CHECK_ID""")
        return [inc._row(r) or {} for r in found]

    def open_dq_incidents(self) -> Tuple[Dict[str, int], List[str]]:
        """({table: open DQ incidents}, tables with an open DQ_STALE incident), tables upper-cased."""
        found = self.db.query(f"""SELECT KIND, ERROR_EXCERPT, DAG_ID FROM CORE.INCIDENT
                                   WHERE ENV_ID = '{DQ_ENV}' AND STATUS IN ('OPEN', 'ACK', 'MITIGATED', 'MUTED')""")
        counts: Dict[str, int] = {}
        stale: List[str] = []
        for r in found:
            table = table_of(r).upper()
            counts[table] = counts.get(table, 0) + 1
            if r.get("kind") == KIND_STALE:
                stale.append(table)
        return counts, stale

    def save_digest(self, domain_id: str, day: date, team_id: Optional[str], owner: Optional[str],
                    content: Dict[str, Any]) -> bool:
        """Store the domain's digest for the day unless one is stored already. True when it was added."""
        return self.db.execute_count("""
            MERGE INTO CORE.DQ_DIGEST T USING (SELECT %s AS DOMAIN_ID, TO_DATE(%s) AS DIGEST_DATE) S
               ON T.DOMAIN_ID = S.DOMAIN_ID AND T.DIGEST_DATE = S.DIGEST_DATE
            WHEN NOT MATCHED THEN INSERT (DOMAIN_ID, DIGEST_DATE, TEAM_ID, OWNER, PASS_RATE, NEW_FAILING, CONTENT)
                 VALUES (S.DOMAIN_ID, S.DIGEST_DATE, %s, %s, %s, %s, PARSE_JSON(%s))""",
                                     (domain_id, day.isoformat(), team_id, owner, content.get("pass_rate"),
                                      int(content.get("new_failing") or 0), json.dumps(content, default=str))) >= 1
