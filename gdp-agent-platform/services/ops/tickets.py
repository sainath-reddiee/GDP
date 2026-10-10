"""Jira tickets for incidents, raised by a bot account (JIRA_BOT_EMAIL and JIRA_BOT_TOKEN on the worker or API host,
basic auth against the site_url configured in Admin, Integrations, Jira). People still act as themselves through
OAuth; the bot is only for automated incident tickets.

One ticket per incident, labelled gdp-ops and gdp-inc-<first 8 of the fingerprint>, in the team's Jira project (with its
component and assignee), else the Jira default project. Dedupe, in order:
  1. the incident already has a JIRA_KEY;
  2. a label search finds a ticket for the fingerprint that is not Done (it is linked, not duplicated);
  3. an idempotency claim in OPS.INCIDENT_EVENT (jira:create:<incident>) so two workers never create two tickets;
     a claim nobody finished is taken over after CLAIM_MINUTES.
Recurrences add at most one comment an hour. A resolve comments and, when the setting transition_on_resolve is on,
moves the ticket to done_status. Without a bot the incident gets JIRA_STATE NOT_RAISED and a timeline event saying so.
The worker's jira_sync job marks incidents MITIGATED when their ticket went Done in Jira after they were (re)opened.
A reopened incident (it failed again after Jira Done, or someone reopened it) has JIRA_STATE REOPENED until the reopen
action moved its ticket out of Done (or commented when the workflow has no way back), so an old Done never mitigates it
again. A create that finds the incident resolved, muted or grouped raises nothing (JIRA_STATE SKIPPED); one that races a
resolve closes the new ticket at once.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.jira.client import JiraClient, JiraError, check_project, jql_string
from services.ops.incidents import REOPENED, SYSTEM, _dt
from services.ops.notify import airflow_link, links
from services.ops.redact import redact

LABEL = "gdp-ops"
CLAIM_MINUTES = 10
RECUR_SECONDS = 3600
NO_BOT = "ticket not raised: no Jira bot configured"
PREFERRED_TYPES = ("incident", "bug", "task")


def fp_label(fingerprint: str) -> str:
    return f"gdp-inc-{(fingerprint or '')[:8]}"


def bot_credentials() -> Optional[Tuple[str, str]]:
    email = (os.environ.get("JIRA_BOT_EMAIL") or "").strip()
    token = (os.environ.get("JIRA_BOT_TOKEN") or "").strip()
    return (email, token) if email and token else None


def _http(method: str, url: str, headers: dict, body: Optional[dict]) -> Tuple[int, Any, dict]:
    import httpx

    with httpx.Client(timeout=25, follow_redirects=False) as client:
        r = client.request(method, url, headers=headers, json=body)
    answer = {k: v for k, v in r.headers.items() if k.lower() == "retry-after"}
    try:
        return r.status_code, r.json(), answer
    except ValueError:
        return r.status_code, r.text, answer


def bot_client(jira_cfg: Dict[str, Any], http: Optional[Callable[..., Tuple[Any, ...]]] = None) -> Optional[JiraClient]:
    """The bot's client, or None when the bot or the site is not configured."""
    creds = bot_credentials()
    site = str((jira_cfg or {}).get("site_url") or "").strip()
    if not creds or not site:
        return None
    try:
        return JiraClient.basic(http or _http, site, creds[0], creds[1])
    except ValueError:
        return None


def jira_url(site: Optional[str], key: Optional[str]) -> Optional[str]:
    return f"{str(site).rstrip('/')}/browse/{key}" if site and key else None


# ---------------------------------------------------------------- ADF

def _text(value: str, href: Optional[str] = None) -> Dict[str, Any]:
    node: Dict[str, Any] = {"type": "text", "text": value}
    if href:
        node["marks"] = [{"type": "link", "attrs": {"href": href}}]
    return node


def _para(*parts: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "paragraph", "content": list(parts)}


def _heading(text: str, level: int = 3) -> Dict[str, Any]:
    return {"type": "heading", "attrs": {"level": level}, "content": [_text(text)]}


def description(incident: Dict[str, Any], link: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """The ticket body: title, where it failed, the redacted error, links, and an impact placeholder."""
    facts = [("Environment", incident.get("env_id")), ("DAG", incident.get("dag_id")), ("Task", incident.get("task_id")),
             ("Run", incident.get("run_id")), ("Kind", incident.get("kind")), ("Severity", incident.get("severity")),
             ("First seen", incident.get("first_seen"))]
    content: List[Dict[str, Any]] = [
        _heading(redact(str(incident.get("title") or "Incident"))[:250], 2),
        {"type": "bulletList", "content": [{"type": "listItem", "content": [_para(_text(f"{k}: {redact(str(v))[:250]}"))]}
                                           for k, v in facts if v]},
    ]
    excerpt = redact(str(incident.get("error_excerpt") or ""))[-3000:]
    if excerpt:
        content += [_heading("Error"), {"type": "codeBlock", "content": [_text(excerpt)]}]
    items = [(title, link.get(key)) for key, title in (("open", "Incident in the data platform"), ("airflow", "Airflow run"))
             if link.get(key)]
    if items:
        content += [_heading("Links"), {"type": "bulletList", "content": [
            {"type": "listItem", "content": [_para(_text(title, url))]} for title, url in items]}]
    content += [_heading("Impact"), _para(_text("To be assessed: downstream models and tables are listed on the incident."))]
    if incident.get("ai_summary"):
        content += [_heading("AI summary"), _para(_text(redact(str(incident["ai_summary"]))[:2000]))]
    content.append(_para(_text("Raised automatically by the data platform's ops bot. Work the incident in the platform; "
                               "this ticket is updated when it re-occurs or is resolved.")))
    return {"type": "doc", "version": 1, "content": content}


def _comment(text: str) -> Dict[str, Any]:
    return {"type": "doc", "version": 1, "content": [_para(_text(redact(text)[:4000]))]}


# ---------------------------------------------------------------- raising

def _issue_type(client: JiraClient, project: str) -> str:
    types = [t for t in client.issue_types(project) if not t.get("subtask")]
    if not types:
        raise JiraError(400, f"Project {project} has no issue type the bot can create")
    for wanted in PREFERRED_TYPES:
        for t in types:
            if str(t.get("name") or "").lower() == wanted:
                return str(t["id"])
    return str(types[0]["id"])


def _links(store: Any, incident: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Optional[str]]:
    env = store.envs().get(incident.get("env_id")) or {}
    return links(incident, settings.get("public_base_url"), None,
                 airflow_link(env.get("airflow_url"), env.get("api_version"), incident.get("dag_id") or "", incident.get("run_id")))


def raise_ticket(store: Any, incident_id: str, client: Optional[JiraClient], settings: Dict[str, Any],
                 actor: str = SYSTEM) -> Dict[str, Any]:
    """{state: exists | linked | raised | NOT_RAISED | in_progress, key, detail}. JiraError propagates (after the
    incident is marked FAILED) so the outbox retries."""
    incident = store.get(incident_id)
    if not incident:
        return {"state": "NOT_RAISED", "key": None, "detail": "the incident no longer exists"}
    if incident.get("jira_key"):
        return {"state": "exists", "key": incident["jira_key"], "detail": "the incident already has a ticket"}
    reason = skip_reason(incident)
    if reason:
        # resolved, muted or grouped before the create was sent: no ticket (a reopen or an ended mute queues a new create)
        store.update(incident_id, {"jira_state": "SKIPPED"})
        store.event(incident_id, "ticket_skipped", actor, {"reason": reason})
        return {"state": "SKIPPED", "key": None, "detail": reason}
    if client is None:
        store.update(incident_id, {"jira_state": "NOT_RAISED"})
        store.event(incident_id, "ticket_not_raised", actor, {"reason": NO_BOT})
        return {"state": "NOT_RAISED", "key": None, "detail": NO_BOT}
    cfg = store.jira_config()
    team = store.team(incident.get("team_id")) or {}
    try:
        project = check_project(team.get("jira_project") or cfg.get("default_project") or "")
    except ValueError:
        reason = "ticket not raised: no Jira project for the team and no default project"
        store.update(incident_id, {"jira_state": "NOT_RAISED"})
        store.event(incident_id, "ticket_not_raised", actor, {"reason": reason})
        return {"state": "NOT_RAISED", "key": None, "detail": reason}

    label = fp_label(incident["fingerprint"])
    found = client.search(f"labels = {jql_string(label)} AND statusCategory != Done ORDER BY created DESC",
                          fields=["summary", "status"], page_size=5, max_pages=1)
    if found and found[0].get("key"):
        key = found[0]["key"]
        store.update(incident_id, {"jira_key": key, "jira_state": "OPEN"})
        store.event(incident_id, "ticket_linked", actor, {"key": key, "reason": "an open ticket has the same fingerprint"})
        return {"state": "linked", "key": key, "detail": f"linked to {key}"}

    claim = f"jira:create:{incident_id}"
    if not store.claim(claim) and not store.reclaim_stale(claim, CLAIM_MINUTES):
        return {"state": "in_progress", "key": None, "detail": "another worker is raising this ticket"}
    try:
        fields: Dict[str, Any] = {
            "project": {"key": project}, "issuetype": {"id": _issue_type(client, project)},
            "summary": " ".join(f"[{incident.get('severity')}] {redact(str(incident.get('title') or ''))}".split())[:255],
            "description": description(incident, _links(store, incident, settings)), "labels": [LABEL, label],
        }
        if team.get("jira_component"):
            fields["components"] = [{"name": str(team["jira_component"])[:255]}]
        if team.get("jira_assignee_account_id"):
            fields["assignee"] = {"accountId": str(team["jira_assignee_account_id"])[:128]}
        created = client._call("POST", "/rest/api/3/issue", {"fields": fields}) or {}
        key = created.get("key")
        if not key:
            raise JiraError(502, "Jira did not return the new issue key")
    except JiraError as exc:
        store.release_claim(claim)
        store.update(incident_id, {"jira_state": "FAILED"})
        store.event(incident_id, "ticket_failed", actor, {"error": redact(exc.message)[:500], "status": exc.status})
        raise
    store.update(incident_id, {"jira_key": key, "jira_state": "OPEN"})
    store.bind(claim, incident_id, "ticket_raised", actor, {"key": key, "project": project})
    detail = f"raised {key} in {project}"
    # the incident may have been resolved while the ticket was being created: its resolve action found no ticket, so
    # close the new one now instead of leaving an orphan open in Jira
    current = store.get(incident_id) or {}
    if str(current.get("status") or "").upper() == "RESOLVED":
        try:
            _, closed = resolved(store, {**current, "jira_key": key}, client, settings)
            detail += f"; the incident was resolved meanwhile: {closed}"
        except JiraError as exc:
            store.event(incident_id, "ticket_failed", actor, {"error": redact(exc.message)[:500], "status": exc.status,
                                                            "step": "close after resolve"})
    return {"state": "raised", "key": key, "detail": detail}


def skip_reason(incident: Dict[str, Any]) -> Optional[str]:
    """Why no ticket is raised for the incident as it is now, or None."""
    status = str(incident.get("status") or "").upper()
    if status == "RESOLVED":
        return "ticket not raised: the incident was resolved before the ticket was sent"
    if status == "MUTED":
        return "ticket not raised: the incident is muted"
    if incident.get("parent_incident_id"):
        return "ticket not raised: the incident is grouped under an upstream incident"
    return None


# ---------------------------------------------------------------- updates

def recurrence(store: Any, incident: Dict[str, Any], client: JiraClient, now: datetime) -> Tuple[str, str]:
    """At most one comment an hour summing the occurrences since the last one."""
    key = incident.get("jira_key")
    if not key:
        return "SKIPPED", "no ticket"
    last = _dt(incident.get("jira_commented_at"))
    if last and now - last < timedelta(seconds=RECUR_SECONDS):
        return "SKIPPED", "throttled: commented less than an hour ago"
    total = int(incident.get("occurrences") or 1)
    new = total - int(incident.get("jira_synced_occurrences") or 1)
    if new <= 0:
        return "SKIPPED", "nothing new"
    client.add_comment(key, _comment(f"Re-occurred {new} more time(s), {total} in all. Last seen {incident.get('last_seen')} "
                                     f"in run {incident.get('run_id') or 'unknown'}."))
    store.update(incident["incident_id"], {"jira_synced_occurrences": total, "jira_commented_at": now.isoformat()})
    store.event(incident["incident_id"], "jira_commented", SYSTEM, {"key": key, "occurrences": total})
    return "SENT", f"commented on {key}"


def _done_transition(client: JiraClient, key: str, done_status: str) -> Optional[str]:
    options = client.transitions(key)
    wanted = (done_status or "").strip().lower()
    for t in options:
        if wanted and (str(t.get("to") or "").lower() == wanted or str(t.get("name") or "").lower() == wanted):
            return str(t["id"])
    if not wanted:
        return next((str(t["id"]) for t in options if t.get("category") == "done"), None)
    return None


def resolved(store: Any, incident: Dict[str, Any], client: JiraClient, settings: Dict[str, Any]) -> Tuple[str, str]:
    key = incident.get("jira_key")
    if not key:
        return "SKIPPED", "no ticket"
    who = incident.get("resolved_by") or SYSTEM
    client.add_comment(key, _comment(f"Resolved by {who}: {incident.get('resolution') or ''}"))
    detail = f"commented on {key}"
    if settings.get("transition_on_resolve"):
        transition = _done_transition(client, key, str(settings.get("done_status") or "Done"))
        if transition:
            client.transition(key, transition)
            store.update(incident["incident_id"], {"jira_state": "DONE"})
            detail += f" and moved it to {settings.get('done_status') or 'Done'}"
        else:
            detail += f"; no transition to {settings.get('done_status') or 'Done'} is available"
    store.event(incident["incident_id"], "jira_resolved", SYSTEM, {"key": key, "detail": detail})
    return "SENT", detail


def handle(store: Any, row: Dict[str, Any], client: Optional[JiraClient], settings: Dict[str, Any],
           now: Optional[datetime] = None) -> Tuple[str, str]:
    """The outbox handler for CHANNEL JIRA rows: (SENT | SKIPPED, detail); raises to retry."""
    now = now or datetime.now(timezone.utc)
    incident = store.get(row.get("incident_id") or "")
    if not incident:
        return "SKIPPED", "the incident no longer exists"
    kind = row.get("kind")
    if kind == "create":
        result = raise_ticket(store, incident["incident_id"], client, settings)
        if result["state"] == "in_progress":
            raise RuntimeError(result["detail"])
        return ("SKIPPED" if result["state"] in ("NOT_RAISED", "SKIPPED") else "SENT"), result["detail"]
    if client is None:
        return "SKIPPED", NO_BOT
    if kind == "recur":
        return recurrence(store, incident, client, now)
    if kind == "resolve":
        return resolved(store, incident, client, settings)
    if kind == "ai":   # the AI diagnosis summary (PR O3), queued by services.ops.diagnose
        if not incident.get("jira_key"):
            return "SKIPPED", "no ticket"
        from services.ops.notify import _payload

        text = str(_payload(row.get("payload")).get("text") or "").strip()
        if not text:
            return "SKIPPED", "empty AI summary"
        client.add_comment(incident["jira_key"], _comment(text))
        store.event(incident["incident_id"], "jira_commented", SYSTEM, {"key": incident["jira_key"], "ai": True})
        return "SENT", f"commented the AI diagnosis on {incident['jira_key']}"
    if kind == "reopen":
        return reopened(store, incident, client)
    return "SKIPPED", f"unknown Jira action {kind}"


REOPEN_NAMES = ("reopen", "reopened", "re-open", "to do", "open", "in progress")


def _open_transition(client: JiraClient, key: str) -> Optional[Dict[str, Any]]:
    """A transition out of Done: one named like a reopen, else any into a To Do ('new') or In Progress
    ('indeterminate') status; None when the workflow offers none."""
    options = [t for t in client.transitions(key) if t.get("category") != "done"]
    for name in REOPEN_NAMES:
        for t in options:
            if str(t.get("name") or "").lower() == name or str(t.get("to") or "").lower() == name:
                return t
    for category in ("new", "indeterminate"):
        for t in options:
            if t.get("category") == category:
                return t
    return None


def reopened(store: Any, incident: Dict[str, Any], client: JiraClient) -> Tuple[str, str]:
    """The failure is back: move the ticket out of Done when the workflow allows it, and comment either way. Then
    JIRA_STATE leaves REOPENED, so jira_sync may mitigate the incident again, but only for a Done that happens after
    this reopen (sync_done compares the status change time with OPENED_AT)."""
    key = incident.get("jira_key")
    if not key:
        return "SKIPPED", "no ticket"
    client.add_comment(key, _comment("Reopened in the data platform: the failure is back."))
    detail = f"commented on {key}"
    status = ((client.issue(key) or {}).get("fields") or {}).get("status") or {}
    is_done = (status.get("statusCategory") or {}).get("key") == "done"
    target = _open_transition(client, key) if is_done else None
    if not is_done:
        detail += " (it is not Done in Jira, so its status is kept)"
    elif target:
        client.transition(key, str(target["id"]))
        detail += f" and moved it to {target.get('to') or target.get('name')}"
    else:
        detail += "; no transition out of Done is available, the ticket stays as it is"
    if str(incident.get("status") or "").upper() in ("OPEN", "ACK"):
        store.update(incident["incident_id"], {"jira_state": "OPEN"})
    store.event(incident["incident_id"], "jira_commented", SYSTEM, {"key": key, "reopened": True, "detail": detail})
    return "SENT", detail


def done_after_open(issue: Dict[str, Any], incident: Dict[str, Any]) -> bool:
    """True when the ticket went Done after the incident was (re)opened. A ticket still Done from before a reopen
    (its workflow had no way back, or the reopen action has not run yet) must not mitigate the incident again. Without
    a change time from Jira, the Done counts."""
    fields = issue.get("fields") or {}
    changed = _dt(fields.get("statuscategorychangedate") or fields.get("updated"))
    opened = _dt(incident.get("opened_at"))
    return changed is None or opened is None or changed > opened


def sync_done(store: Any, client: Optional[JiraClient]) -> int:
    """Incidents whose ticket went Done in Jira after they were (re)opened become MITIGATED; one whose Jira reopen is
    still queued (JIRA_STATE REOPENED) is left alone. Returns how many changed."""
    if client is None:
        return 0
    changed = 0
    rows = [r for r in store.ticketed_open() if str(r.get("jira_state") or "").upper() != REOPENED]
    for i in range(0, len(rows), 50):
        chunk = rows[i:i + 50]
        by_key = {r["jira_key"]: r for r in chunk if r.get("jira_key")}
        if not by_key:
            continue
        keys = ", ".join(jql_string(k) for k in by_key)
        for issue in client.search(f"key in ({keys}) AND statusCategory = Done",
                                   fields=["status", "statuscategorychangedate", "updated"], page_size=50, max_pages=1):
            incident = by_key.get(issue.get("key"))
            if not incident or not done_after_open(issue, incident):
                continue
            if store.update(incident["incident_id"], {"status": "MITIGATED", "jira_state": "DONE"},
                            only_status=("OPEN", "ACK")) >= 1:
                status = ((issue.get("fields") or {}).get("status") or {}).get("name")
                store.event(incident["incident_id"], "mitigated", SYSTEM, {"key": issue.get("key"), "jira_status": status})
                changed += 1
    return changed
