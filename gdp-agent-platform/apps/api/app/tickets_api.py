"""Tickets (/tickets): one list of Jira issues (read live, as the signed-in person, through their own Jira account) and
platform cases (CASES schema), with one set of filters and saved views (V050). The rules live in services/tickets.py.

Jira is searched only for a person with JIRA.READ and a connected account; otherwise the list has cases only and says
why. Cases are read through the cases API's own query, so domain visibility applies exactly as on a case page.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _snowflake_error, current_db
from services import tickets as tk

router = APIRouter()
VIEWS = "CORE.TICKET_VIEW"
JIRA_LIMIT = 100


def _holds(db: Db, privilege: str) -> bool:
    """Whether the caller holds a privilege (true when governance is not deployed, as the middleware lets it through)."""
    from app.governance import identity, ready

    try:
        if not ready(db):
            return True
        privileges = set(identity(db)["privileges"])
    except Exception:
        return True
    return "*" in privileges or privilege in privileges


def _jira(db: Db, f: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """(rows, state): the Jira half, or no rows and why (not allowed, not set up, not connected, Jira error)."""
    from app import jira_api
    from services.jira.client import JiraError

    if not _holds(db, "JIRA.READ"):
        return [], {"connected": False, "reason": "Jira needs the JIRA.READ privilege."}
    try:
        client, conn, cfg = jira_api._client(db)
    except HTTPException as exc:
        return [], {"connected": False, "reason": str(exc.detail), "connect": exc.status_code == 428}
    field = jira_api.sprint_field(client, conn["cloud_id"])
    jql = tk.jql(f, (cfg.get("default_project") or "").strip().upper())
    fields = ["summary", "status", "priority", "issuetype", "assignee", "updated", "created", "project", "labels"] + ([field] if field else [])
    try:
        found = client.search(jql, fields=fields, page_size=50, max_pages=JIRA_LIMIT // 50)
    except JiraError as exc:
        return [], {"connected": True, "reason": f"Jira: {exc.message}", "jql": jql}
    me = conn.get("account_id")
    rows = [tk.from_jira(i, conn.get("site_url") or "", field, me) for i in found]
    keys = [r["key"] for r in rows]
    if keys:   # how many platform objects each issue is linked to (runs, tests, tables)
        try:
            for r in db.query(f"""SELECT ISSUE_KEY, COUNT(*) AS N FROM JIRA.ISSUE_LINK
                                   WHERE NOT IS_DELETED AND ISSUE_KEY IN ({', '.join(['%s'] * len(keys))}) GROUP BY 1""", tuple(keys)):
                for row in rows:
                    if row["key"] == r["issue_key"]:
                        row["linked"] = int(r["n"])
        except Exception:
            pass
    return rows, {"connected": True, "jql": jql, "sprint_field": field, "site_url": conn.get("site_url"),
                  "truncated": len(found) >= JIRA_LIMIT}


def _cases(db: Db, f: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from app import cases_api

    status = {"open": None, "done": ",".join(tk.CASE_DONE), "any": "any"}[f["status"]]
    try:
        out = cases_api.list_cases(status=status, severity=",".join(f.get("priority") or []) or None,
                                   kind=",".join(f.get("kind") or []) or None, domain_id=f.get("domain_id"),
                                   mine=f.get("assignee") == "me", team_id=None, q=f.get("q"),
                                   sla="breached" if f.get("sla_breached") else None, limit=200, offset=0, db=db)
    except HTTPException:
        raise
    except Exception as exc:
        if "does not exist" in str(exc).lower():
            return [], {"deployed": False}
        raise _snowflake_error(exc) from exc
    return [tk.from_case(c, db.user) for c in out["cases"]], {"deployed": True, "total": out.get("total")}


def _copilot(db: Db, rows: List[Dict[str, Any]]) -> None:
    """Mark Jira tickets that have a Copilot conversation of the caller's, with its open drafts."""
    keys = [r["key"] for r in rows if "JIRA" in r["sources"]]
    if not keys:
        return
    try:
        found = db.query(f"""SELECT S.JIRA_KEY, ANY_VALUE(S.SESSION_ID) AS SESSION_ID, COUNT_IF(C.STATUS = 'PROPOSED') AS DRAFTS
                               FROM CORE.COPILOT_SESSION S LEFT JOIN CORE.COPILOT_CHANGE C ON C.SESSION_ID = S.SESSION_ID
                              WHERE S.CREATED_BY = CURRENT_USER() AND S.STATUS <> 'DELETED'
                                AND S.JIRA_KEY IN ({', '.join(['%s'] * len(keys))}) GROUP BY 1""", tuple(keys))
    except Exception:
        return
    by = {r["jira_key"]: {"session_id": r["session_id"], "drafts": int(r["drafts"] or 0)} for r in found}
    for r in rows:
        if r["key"] in by:
            r["copilot"] = by[r["key"]]


@router.get("/api/tickets")
def list_tickets(source: str = "all", status: str = "open", assignee: str = "any",
                 priority: str = Query(default="", max_length=40), type: str = Query(default="", max_length=200),
                 kind: str = Query(default="", max_length=200), project: str = Query(default="", max_length=40),
                 sprint: str = Query(default="", max_length=20), updated_from: str = Query(default="", max_length=10),
                 updated_to: str = Query(default="", max_length=10), domain_id: str = Query(default="", max_length=36),
                 sla_breached: bool = False, linked: bool = False, copilot: bool = False,
                 q: str = Query(default="", max_length=200), db: Db = Depends(current_db)):
    """Jira issues and cases in one list, newest first, with the state of each source."""
    try:
        f = tk.clean({"source": source, "status": status, "assignee": assignee, "priority": priority, "type": type,
                      "kind": kind, "project": project, "sprint": sprint, "updated_from": updated_from,
                      "updated_to": updated_to, "domain_id": domain_id, "sla_breached": sla_breached,
                      "linked": linked, "copilot": copilot, "q": q})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    jira_rows, jira_state = _jira(db, f) if tk.jira_wanted(f) else ([], {"skipped": True})
    case_rows, case_state = _cases(db, f) if tk.cases_wanted(f) else ([], {"skipped": True})
    if f.get("updated_from") or f.get("updated_to"):   # the cases query has no date filter: applied here
        lo, hi = f.get("updated_from") or "", f.get("updated_to") or "9999"
        case_rows = [r for r in case_rows if lo <= str(r.get("updated") or "")[:10] <= hi]
    rows = tk.merge(jira_rows, case_rows)
    _copilot(db, rows)
    rows = tk.platform_filter(rows, f)
    return {"tickets": rows[:300], "filters": f, "jira": jira_state, "cases": case_state,
            "counts": {"jira": sum(1 for r in rows if "JIRA" in r["sources"]),
                       "case": sum(1 for r in rows if "CASE" in r["sources"]), "total": len(rows)}}


# ---------------------------------------------------------------- saved views

class ViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    filters: Dict[str, Any] = Field(default_factory=dict)
    shared: bool = False


def _view(r: Dict[str, Any], user: str) -> Dict[str, Any]:
    filters = r.get("filters")
    return {"view_id": r["view_id"], "name": r["name"], "shared": bool(r.get("shared")), "owner": r.get("created_by"),
            "mine": str(r.get("created_by") or "").upper() == user.upper(),
            "filters": json.loads(filters) if isinstance(filters, str) else (filters or {})}


@router.get("/api/tickets/views")
def list_views(db: Db = Depends(current_db)):
    """My saved views and the ones others shared."""
    try:
        found = db.query(f"""SELECT VIEW_ID, NAME, FILTERS, SHARED, CREATED_BY FROM {VIEWS}
                              WHERE CREATED_BY = CURRENT_USER() OR SHARED ORDER BY IFF(CREATED_BY = CURRENT_USER(), 0, 1), NAME""")
    except Exception as exc:
        if "does not exist" in str(exc).lower():
            return {"views": [], "deployed": False}
        raise _snowflake_error(exc) from exc
    return {"views": [_view(r, db.user) for r in found], "deployed": True}


@router.post("/api/tickets/views")
def save_view(body: ViewIn, db: Db = Depends(current_db)):
    """Save the current filters as a named view (replaces my view of the same name)."""
    try:
        f = tk.clean(body.filters)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    view_id = str(uuid.uuid4())
    try:
        db.execute(f"DELETE FROM {VIEWS} WHERE CREATED_BY = CURRENT_USER() AND LOWER(NAME) = LOWER(%s)", (body.name.strip(),))
        db.execute(f"INSERT INTO {VIEWS} (VIEW_ID, NAME, FILTERS, SHARED) SELECT %s, %s, PARSE_JSON(%s), %s",
                   (view_id, body.name.strip(), json.dumps(f), body.shared))
    except Exception as exc:
        if "does not exist" in str(exc).lower():
            raise HTTPException(503, "Saved views need migration V050. An admin applies it in Admin, Deploy.") from None
        raise _snowflake_error(exc) from exc
    return {"view_id": view_id, "name": body.name.strip(), "filters": f, "shared": body.shared}


@router.delete("/api/tickets/views/{view_id}")
def delete_view(view_id: str, db: Db = Depends(current_db)):
    """Delete one of my views (someone else's shared view cannot be deleted)."""
    if not db.execute_count(f"DELETE FROM {VIEWS} WHERE VIEW_ID = %s AND CREATED_BY = CURRENT_USER()", (view_id,)):
        raise HTTPException(404, "view not found, or it is not yours")
    return {"view_id": view_id, "deleted": True}
