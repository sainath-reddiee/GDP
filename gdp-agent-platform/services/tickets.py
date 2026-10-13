"""One list of tickets (pure): Jira issues, read live as the signed-in person, and platform cases (CASES schema), with
one set of filters.

A Jira issue that is also a case (a case opened from it: source JIRA, source_ref the key) is one ticket with both
sources. Jira-side filters become JQL; case-side filters become the cases query; the platform filters (domain, SLA,
linked, Copilot) are applied to the merged rows. Nothing here talks to Jira or Snowflake.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

SOURCES = ("all", "jira", "case")
STATUSES = ("open", "done", "any")
ASSIGNEES = ("any", "me", "unassigned")
PRIORITIES = ("P1", "P2", "P3", "P4")
SPRINTS = ("active", "future", "none")
# Jira priority names onto the platform's case severities, so one priority filter covers both
JIRA_PRIORITY = {"highest": "P1", "blocker": "P1", "critical": "P1", "high": "P2", "major": "P2", "medium": "P3",
                 "low": "P4", "lowest": "P4", "minor": "P4", "trivial": "P4"}
PRIORITY_NAMES = {"P1": ["Highest", "Blocker", "Critical"], "P2": ["High", "Major"], "P3": ["Medium"],
                  "P4": ["Low", "Lowest", "Minor", "Trivial"]}
KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,30}-\d{1,9}$")
PROJECT = re.compile(r"^[A-Z][A-Z0-9_]{0,30}$")
CASE_DONE = ("RESOLVED", "CLOSED", "DUPLICATE")


def clean(f: Dict[str, Any]) -> Dict[str, Any]:
    """The filters, checked: unknown values raise ValueError (a 400), empty ones are dropped."""
    out: Dict[str, Any] = {}
    for name, allowed in (("source", SOURCES), ("status", STATUSES), ("assignee", ASSIGNEES)):
        value = str(f.get(name) or allowed[0]).lower()   # defaults: all sources, open, anyone
        if value not in allowed:
            raise ValueError(f"{name} must be one of {', '.join(allowed)}")
        out[name] = value
    priorities = [p.strip().upper() for p in str(f.get("priority") or "").split(",") if p.strip()]
    if any(p not in PRIORITIES for p in priorities):
        raise ValueError("priority is a comma list of P1, P2, P3, P4")
    out["priority"] = priorities
    for name in ("updated_from", "updated_to"):
        if f.get(name):
            out[name] = date.fromisoformat(str(f[name])[:10]).isoformat()   # ValueError when not a date
    if f.get("project"):
        project = str(f["project"]).strip().upper()
        if not PROJECT.match(project):
            raise ValueError("project is a Jira project key")
        out["project"] = project
    if f.get("sprint"):
        sprint = str(f["sprint"]).strip().lower()
        if sprint not in SPRINTS and not sprint.isdigit():
            raise ValueError("sprint is active, future, none or a sprint id")
        out["sprint"] = sprint
    if f.get("type"):
        types = [t.strip() for t in str(f["type"]).split(",") if t.strip()][:10]
        if any(not re.fullmatch(r"[A-Za-z0-9 _\-]{1,40}", t) for t in types):
            raise ValueError("type is a comma list of Jira issue type names")
        out["type"] = types
    if f.get("kind"):
        out["kind"] = [k.strip().upper() for k in str(f["kind"]).split(",") if k.strip()][:10]
    for name in ("domain_id", "q"):
        if f.get(name):
            out[name] = str(f[name]).strip()[:200]
    for name in ("sla_breached", "linked", "copilot"):
        if str(f.get(name) or "").lower() in ("1", "true", "yes"):
            out[name] = True
    return out


def as_query(f: Dict[str, Any]) -> Dict[str, Any]:
    """Checked filters in the page's URL shape (lists as comma text), as saved views store them."""
    return {k: ",".join(v) if isinstance(v, list) else v for k, v in f.items() if v not in (None, "", [], False)}


def _jql_text(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def jql(f: Dict[str, Any], default_project: str = "") -> str:
    """The Jira half of the filters as JQL (f from clean())."""
    where: List[str] = []
    q = f.get("q") or ""
    if q and KEY.match(q.upper()):
        return f"key = {q.upper()}"
    if q:
        where.append(f"text ~ {_jql_text(q)}")
    project = f.get("project") or ""
    if project:
        where.append(f"project = {project}")
    if f.get("status") == "open":
        where.append("statusCategory != Done")
    elif f.get("status") == "done":
        where.append("statusCategory = Done")
    if f.get("assignee") == "me":
        where.append("assignee = currentUser()")
    elif f.get("assignee") == "unassigned":
        where.append("assignee is EMPTY")
    if f.get("priority"):
        names = [n for p in f["priority"] for n in PRIORITY_NAMES[p]]
        where.append("priority in (" + ", ".join(_jql_text(n) for n in names) + ")")
    if f.get("type"):
        where.append("issuetype in (" + ", ".join(_jql_text(t) for t in f["type"]) + ")")
    if f.get("updated_from"):
        where.append(f'updated >= "{f["updated_from"]}"')
    if f.get("updated_to"):
        where.append(f'updated <= "{f["updated_to"]} 23:59"')
    sprint = f.get("sprint")
    if sprint == "active":
        where.append("sprint in openSprints()")
    elif sprint == "future":
        where.append("sprint in futureSprints()")
    elif sprint == "none":
        where.append("sprint is EMPTY")
    elif sprint:
        where.append(f"sprint = {int(sprint)}")
    if not where:   # Jira refuses an unbounded search: the default project, or my issues
        where.append(f"project = {default_project}" if default_project else "assignee = currentUser()")
    return " AND ".join(where) + " ORDER BY updated DESC"


def _sprint(value: Any) -> Optional[Dict[str, Any]]:
    """The issue's current sprint (active first, else the latest) from the sprint field."""
    items = [s for s in (value or []) if isinstance(s, dict)] if isinstance(value, list) else []
    if not items:
        return None
    pick = next((s for s in items if s.get("state") == "active"), None) or items[-1]
    return {"id": pick.get("id"), "name": pick.get("name"), "state": pick.get("state")}


def from_jira(issue: Dict[str, Any], site_url: str = "", sprint_field: Optional[str] = None,
              me: Optional[str] = None) -> Dict[str, Any]:
    f = issue.get("fields") or {}
    status = f.get("status") or {}
    priority = (f.get("priority") or {}).get("name")
    assignee = f.get("assignee") or {}
    key = issue.get("key")
    return {
        "id": f"jira:{key}", "sources": ["JIRA"], "key": key, "title": f.get("summary") or "",
        "status": status.get("name"), "done": (status.get("statusCategory") or {}).get("key") == "done",
        "priority": JIRA_PRIORITY.get(str(priority or "").lower()), "priority_name": priority,
        "type": (f.get("issuetype") or {}).get("name"), "assignee": assignee.get("displayName"),
        "assignee_is_me": bool(me and assignee.get("accountId") == me),
        "project": (f.get("project") or {}).get("key"), "labels": f.get("labels") or [],
        "updated": f.get("updated"), "created": f.get("created"),
        "url": f"{site_url.rstrip('/')}/browse/{key}" if site_url and key else None,
        "sprint": _sprint(f.get(sprint_field)) if sprint_field else None,
        "domain_id": None, "domain_name": None, "sla_due_at": None, "sla_breached": False,
        "case": None, "linked": 0, "copilot": None,
    }


def from_case(c: Dict[str, Any], me: Optional[str] = None) -> Dict[str, Any]:
    status = str(c.get("status") or "")
    jira_key = str(c.get("source_ref") or "").upper() if c.get("source") == "JIRA" else ""
    return {
        "id": f"case:{c.get('case_id')}", "sources": ["CASE"], "key": c.get("number"), "title": c.get("title") or "",
        "status": status.replace("_", " ").title() if status else None, "done": status in CASE_DONE,
        "priority": c.get("severity"), "priority_name": c.get("severity"), "type": str(c.get("kind") or "").replace("_", " ").title() or None,
        "assignee": c.get("assignee"), "assignee_is_me": bool(me and str(c.get("assignee") or "").upper() == me.upper()),
        "project": None, "labels": [], "updated": c.get("updated_at") or c.get("opened_at"), "created": c.get("opened_at"),
        "url": None, "sprint": None, "domain_id": c.get("domain_id"), "domain_name": c.get("domain_name"),
        "sla_due_at": c.get("sla_due_at"), "sla_breached": bool(c.get("sla_breached")),
        "case": {"case_id": c.get("case_id"), "number": c.get("number"), "status": status, "kind": c.get("kind"),
                 "jira_key": jira_key if KEY.match(jira_key) else None, "target_fqn": c.get("target_fqn"), "run_id": c.get("run_id"),
                 "links": int(c.get("links_count") or 0)},
        "linked": int(c.get("links_count") or 0) + (1 if c.get("target_table_id") or c.get("run_id") else 0), "copilot": None,
    }


def merge(jira_rows: Iterable[Dict[str, Any]], case_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Jira issues and cases as one list; a case opened from a Jira issue joins that issue's row."""
    by_key: Dict[str, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for row in jira_rows:
        by_key[str(row["key"])] = row
        out.append(row)
    for row in case_rows:
        jira_key = (row.get("case") or {}).get("jira_key")
        target = by_key.get(jira_key) if jira_key else None
        if target is None:
            out.append(row)
            continue
        target["sources"] = ["JIRA", "CASE"]
        target["case"] = row["case"]
        for k in ("domain_id", "domain_name", "sla_due_at", "sla_breached"):
            target[k] = row[k]
        target["linked"] = max(int(target.get("linked") or 0), int(row.get("linked") or 0))
    return out


def platform_filter(rows: List[Dict[str, Any]], f: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Domain, SLA, linked and Copilot filters, on the merged rows."""
    out = rows
    if f.get("domain_id"):
        out = [r for r in out if r.get("domain_id") == f["domain_id"]]
    if f.get("sla_breached"):
        out = [r for r in out if r.get("sla_breached")]
    if f.get("linked"):
        out = [r for r in out if int(r.get("linked") or 0) > 0]
    if f.get("copilot"):
        out = [r for r in out if r.get("copilot")]
    if f.get("assignee") == "unassigned":
        out = [r for r in out if not r.get("assignee")]
    return sorted(out, key=lambda r: str(r.get("updated") or ""), reverse=True)


def jira_wanted(f: Dict[str, Any]) -> bool:
    """Whether Jira is searched: not for case-only filters (kind, domain, SLA) or the case source."""
    return f.get("source") in ("all", "jira") and not f.get("kind") and not f.get("domain_id") and not f.get("sla_breached")


def cases_wanted(f: Dict[str, Any]) -> bool:
    """Whether cases are listed: not for Jira-only filters (project, sprint, type)."""
    return f.get("source") in ("all", "case") and not f.get("project") and not f.get("sprint") and not f.get("type")
