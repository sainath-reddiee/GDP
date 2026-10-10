"""Jira Cloud REST v3 and the Agile API through OAuth 2.0 (3LO), with HTTP injected so it can be tested without a network.

`http(method, url, headers, body)` -> (status, payload) or (status, payload, response headers): payload is the parsed
JSON body, or text when the response is not JSON. Every call acts as the signed-in engineer (their access token), so
Jira's own permissions apply. A 429 or 503 is retried once when Jira asks to wait RETRY_WAIT seconds or less;
otherwise the JiraError carries retry_after so the caller can pass it on.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

AUTH = "https://auth.atlassian.com"
API = "https://api.atlassian.com"
# the Agile scopes (boards and sprints) were added after the first release: older connections lack them
AGILE_SCOPES = ["read:board-scope:jira-software", "read:sprint:jira-software", "read:project:jira"]
SCOPES = ["read:jira-work", "write:jira-work", "read:jira-user", "offline_access"] + AGILE_SCOPES
ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,30}-\d{1,9}$")
PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,30}$")
LIST_FIELDS = ["summary", "status", "priority", "issuetype", "assignee", "reporter", "updated", "created", "project", "labels"]
DETAIL_FIELDS = LIST_FIELDS + ["description", "comment", "attachment", "environment"]
MAX_PAGES = 5
RETRY_WAIT = 10      # seconds: a longer Retry-After is passed on to the caller instead of waiting here
DEFAULT_WAIT = 2     # a 429 or 503 without a usable Retry-After
_sleep = time.sleep

Http = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Tuple[Any, ...]]


class JiraError(Exception):
    def __init__(self, status: int, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def _unpack(result: Tuple[Any, ...]) -> Tuple[int, Any, Dict[str, str]]:
    """(status, payload, lower-case headers) from an http() answer with or without headers."""
    headers = result[2] if len(result) > 2 and isinstance(result[2], dict) else {}
    return int(result[0]), result[1], {str(k).lower(): str(v) for k, v in headers.items()}


def retry_after(headers: Dict[str, str]) -> Optional[int]:
    """Seconds from a Retry-After header; None when it is absent or not a number of seconds."""
    value = (headers.get("retry-after") or "").strip()
    return int(value) if value.isdigit() else None


def check_project(key: str) -> str:
    value = (key or "").strip().upper()
    if not PROJECT_KEY.match(value):
        raise ValueError(f"not a project key: {key}")
    return value


def _message(payload: Any) -> str:
    if isinstance(payload, dict):
        parts = list(payload.get("errorMessages") or [])
        parts += [f"{k}: {v}" for k, v in (payload.get("errors") or {}).items()]
        for key in ("message", "error_description", "error"):
            if payload.get(key):
                parts.append(str(payload[key]))
        if parts:
            return "; ".join(str(p) for p in parts)[:600]
    return str(payload or "")[:600]


def check_key(key: str) -> str:
    value = (key or "").strip().upper()
    if not ISSUE_KEY.match(value):
        raise ValueError(f"not an issue key: {key}")
    return value


def jql_string(value: str) -> str:
    """A JQL string literal."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- OAuth 2.0 (3LO)

def authorize_url(client_id: str, redirect_uri: str, state: str, scopes: Optional[List[str]] = None) -> str:
    return f"{AUTH}/authorize?" + urlencode({
        "audience": "api.atlassian.com", "client_id": client_id, "scope": " ".join(scopes or SCOPES),
        "redirect_uri": redirect_uri, "state": state, "response_type": "code", "prompt": "consent",
    })


def _token(http: Http, body: Dict[str, Any]) -> Dict[str, Any]:
    status, payload, _ = _unpack(http("POST", f"{AUTH}/oauth/token", {"Content-Type": "application/json"}, body))
    if status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
        raise JiraError(status, f"Atlassian refused the sign-in: {_message(payload)}")
    return payload


def exchange_code(http: Http, client_id: str, client_secret: str, code: str, redirect_uri: str) -> Dict[str, Any]:
    """{access_token, refresh_token, expires_in, scope} for the code Atlassian sent to the callback."""
    return _token(http, {"grant_type": "authorization_code", "client_id": client_id, "client_secret": client_secret,
                         "code": code, "redirect_uri": redirect_uri})


def refresh_tokens(http: Http, client_id: str, client_secret: str, refresh_token: str) -> Dict[str, Any]:
    """New tokens. Atlassian rotates refresh tokens: the returned refresh_token replaces the old one."""
    return _token(http, {"grant_type": "refresh_token", "client_id": client_id, "client_secret": client_secret,
                         "refresh_token": refresh_token})


def accessible_resources(http: Http, access_token: str) -> List[Dict[str, Any]]:
    status, payload, _ = _unpack(http("GET", f"{API}/oauth/token/accessible-resources",
                                      {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}, None))
    if status != 200 or not isinstance(payload, list):
        raise JiraError(status, f"could not list Jira sites: {_message(payload)}")
    return payload


def normal_site(url: str) -> str:
    return (url or "").strip().lower().rstrip("/").replace("http://", "https://")


def pick_site(resources: List[Dict[str, Any]], site_url: Optional[str]) -> Dict[str, Any]:
    """The configured site among those the user granted; the only one when no site is configured."""
    if site_url:
        wanted = normal_site(site_url)
        for r in resources:
            if normal_site(r.get("url", "")) == wanted:
                return r
        raise JiraError(403, f"Your Atlassian account did not grant access to {site_url}. Sign in again and pick that site.")
    if len(resources) == 1:
        return resources[0]
    if not resources:
        raise JiraError(403, "Your Atlassian account has no Jira site for this app.")
    raise JiraError(409, "Several Jira sites are available; an admin sets the site URL in Admin, Integrations, Jira.")


# ---------------------------------------------------------------- REST v3

class JiraClient:
    def __init__(self, http: Http, cloud_id: str, access_token: str):
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", cloud_id or ""):
            raise ValueError("invalid cloud id")
        self.http, self.cloud_id, self.token = http, cloud_id, access_token
        self.base = f"{API}/ex/jira/{cloud_id}"

    def _call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, ok: Tuple[int, ...] = (200, 201, 204)) -> Any:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json", "Content-Type": "application/json"}
        status, payload, answer = _unpack(self.http(method, self.base + path, headers, body))
        if status in (429, 503):
            wait = retry_after(answer)
            if wait is None or wait <= RETRY_WAIT:   # one retry after a short wait
                _sleep(DEFAULT_WAIT if wait is None else wait)
                status, payload, answer = _unpack(self.http(method, self.base + path, headers, body))
                wait = retry_after(answer)
            if status in (429, 503):
                busy = "Jira is limiting requests" if status == 429 else "Jira is unavailable"
                raise JiraError(status, _message(payload) or busy, wait)
        if status not in ok:
            raise JiraError(status, _message(payload) or f"Jira answered {status}")
        return payload

    def myself(self) -> Dict[str, Any]:
        return self._call("GET", "/rest/api/3/myself")

    def search(self, jql: str, fields: Optional[List[str]] = None, page_size: int = 50, max_pages: int = MAX_PAGES) -> List[Dict[str, Any]]:
        """Issues matching JQL, following nextPageToken; stops on a repeated token so a misbehaving server cannot loop."""
        issues: List[Dict[str, Any]] = []
        token, seen = None, set()
        for _ in range(max(1, max_pages)):
            body: Dict[str, Any] = {"jql": jql, "fields": fields or LIST_FIELDS, "maxResults": max(1, min(page_size, 100))}
            if token:
                body["nextPageToken"] = token
            page = self._call("POST", "/rest/api/3/search/jql", body) or {}
            issues += page.get("issues") or []
            token = page.get("nextPageToken")
            if not token or page.get("isLast") or token in seen:
                break
            seen.add(token)
        return issues

    def search_page(self, jql: str, next_token: Optional[str] = None, max_results: int = 50,
                    fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """One page of issues: {"issues": [...], "next": token for the following page, or None on the last one}."""
        body: Dict[str, Any] = {"jql": jql, "fields": fields or LIST_FIELDS, "maxResults": max(1, min(int(max_results), 100))}
        if next_token:
            body["nextPageToken"] = next_token
        page = self._call("POST", "/rest/api/3/search/jql", body) or {}
        token = page.get("nextPageToken")
        last = page.get("isLast") or not token or token == next_token   # a repeated token would loop the caller
        return {"issues": page.get("issues") or [], "next": None if last else token}

    def parse_jql(self, jql: str) -> List[str]:
        """Jira's strict validation of one query: its errors, empty when the query is valid."""
        payload = self._call("POST", "/rest/api/3/jql/parse?validation=strict", {"queries": [jql]}) or {}
        return [str(e) for q in payload.get("queries") or [] for e in q.get("errors") or []][:20]

    def boards(self, name_filter: str = "", start: int = 0, max_results: int = 50) -> List[Dict[str, Any]]:
        """Boards the user can see (Agile API), optionally filtered by name."""
        query: Dict[str, Any] = {"startAt": max(0, int(start)), "maxResults": max(1, min(int(max_results), 50))}
        if name_filter.strip():
            query["name"] = name_filter.strip()[:100]
        payload = self._call("GET", "/rest/agile/1.0/board?" + urlencode(query)) or {}
        return [{"id": b.get("id"), "name": b.get("name"), "type": b.get("type"),
                 "project_key": (b.get("location") or {}).get("projectKey")} for b in payload.get("values") or []]

    def sprints(self, board_id: int, states: Optional[List[str]] = None, max_pages: int = 4) -> List[Dict[str, Any]]:
        """Sprints of a board (Agile API), optionally only some states (active, future, closed)."""
        board = int(board_id)
        wanted = [s for s in states or [] if s in ("active", "future", "closed")]
        out: List[Dict[str, Any]] = []
        start = 0
        for _ in range(max(1, max_pages)):
            query: Dict[str, Any] = {"startAt": start, "maxResults": 50}
            if wanted:
                query["state"] = ",".join(wanted)
            payload = self._call("GET", f"/rest/agile/1.0/board/{board}/sprint?" + urlencode(query)) or {}
            values = payload.get("values") or []
            out += [{"id": s.get("id"), "name": s.get("name"), "state": s.get("state"), "start": s.get("startDate"),
                     "end": s.get("endDate")} for s in values]
            if payload.get("isLast", True) or not values:
                break
            start += len(values)
        return out

    def sprint_issues(self, sprint_id: int, next_token: Optional[str] = None, max_results: int = 50) -> Dict[str, Any]:
        return self.search_page(f"sprint = {int(sprint_id)} ORDER BY Rank ASC", next_token, max_results)

    def issue_types(self, project_key: str) -> List[Dict[str, Any]]:
        """Issue types the user can create in the project."""
        payload = self._call("GET", f"/rest/api/3/issue/createmeta/{check_project(project_key)}/issuetypes?maxResults=100") or {}
        values = payload.get("issueTypes") or payload.get("values") or []
        return [{"id": str(t.get("id")), "name": t.get("name"), "subtask": bool(t.get("subtask"))} for t in values]

    def create_issue(self, project_key: str, issue_type_id: str, summary_text: str, description_adf: Dict[str, Any],
                     labels: Optional[List[str]] = None) -> Dict[str, Any]:
        """{"id", "key", "self"} of the new issue."""
        if not re.fullmatch(r"\d{1,20}", str(issue_type_id)):
            raise ValueError("invalid issue type id")
        fields: Dict[str, Any] = {"project": {"key": check_project(project_key)}, "issuetype": {"id": str(issue_type_id)},
                                  "summary": " ".join((summary_text or "").split())[:255], "description": description_adf}
        if labels:
            fields["labels"] = ["-".join(str(label).split())[:255] for label in labels]   # Jira labels have no spaces
        return self._call("POST", "/rest/api/3/issue", {"fields": fields}) or {}

    def issue(self, key: str) -> Dict[str, Any]:
        return self._call("GET", f"/rest/api/3/issue/{quote(check_key(key))}?fields={','.join(DETAIL_FIELDS)}")

    def attachment_url(self, attachment_id: str) -> str:
        if not re.fullmatch(r"\d{1,20}", str(attachment_id)):
            raise ValueError("invalid attachment id")
        return f"{self.base}/rest/api/3/attachment/content/{attachment_id}"

    def add_comment(self, key: str, adf: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("POST", f"/rest/api/3/issue/{quote(check_key(key))}/comment", {"body": adf})

    def transitions(self, key: str) -> List[Dict[str, Any]]:
        payload = self._call("GET", f"/rest/api/3/issue/{quote(check_key(key))}/transitions") or {}
        return [{"id": t.get("id"), "name": t.get("name"), "to": (t.get("to") or {}).get("name"),
                 "category": ((t.get("to") or {}).get("statusCategory") or {}).get("key")} for t in payload.get("transitions") or []]

    def transition(self, key: str, transition_id: str) -> None:
        if not re.fullmatch(r"\d{1,10}", str(transition_id)):
            raise ValueError("invalid transition id")
        self._call("POST", f"/rest/api/3/issue/{quote(check_key(key))}/transitions", {"transition": {"id": str(transition_id)}})

    def remote_link(self, key: str, url: str, title: str, global_id: str) -> None:
        """A link from the issue back to the run (idempotent through globalId)."""
        self._call("POST", f"/rest/api/3/issue/{quote(check_key(key))}/remotelink",
                   {"globalId": global_id, "object": {"url": url, "title": title[:255]}})


# ---------------------------------------------------------------- shaping

def summary(issue: Dict[str, Any], site_url: str = "") -> Dict[str, Any]:
    f = issue.get("fields") or {}
    status = f.get("status") or {}
    return {
        "key": issue.get("key"), "id": issue.get("id"), "summary": f.get("summary") or "",
        "status": status.get("name"), "status_category": (status.get("statusCategory") or {}).get("key"),
        "priority": (f.get("priority") or {}).get("name"), "type": (f.get("issuetype") or {}).get("name"),
        "assignee": (f.get("assignee") or {}).get("displayName"), "reporter": (f.get("reporter") or {}).get("displayName"),
        "project": (f.get("project") or {}).get("key"), "labels": f.get("labels") or [],
        "updated": f.get("updated"), "created": f.get("created"),
        "url": f"{site_url.rstrip('/')}/browse/{issue.get('key')}" if site_url and issue.get("key") else None,
    }


TEXT_TYPES = ("text/", "application/json", "application/sql", "application/x-sql", "application/csv")
TEXT_SUFFIXES = (".csv", ".sql", ".txt", ".json", ".log", ".md", ".tsv", ".yml", ".yaml")


def detail(issue: Dict[str, Any], site_url: str = "") -> Dict[str, Any]:
    from services.jira.adf import to_text

    f = issue.get("fields") or {}
    comments = ((f.get("comment") or {}).get("comments") or [])[-30:]
    attachments = []
    for a in f.get("attachment") or []:
        name, mime = str(a.get("filename") or ""), str(a.get("mimeType") or "")
        attachments.append({"id": str(a.get("id")), "name": name, "mime": mime, "size": a.get("size"),
                            "author": (a.get("author") or {}).get("displayName"), "created": a.get("created"),
                            "previewable": mime.startswith(TEXT_TYPES) or name.lower().endswith(TEXT_SUFFIXES)})
    return {
        **summary(issue, site_url),
        "description": to_text(f.get("description")), "environment": to_text(f.get("environment")),
        "comments": [{"id": c.get("id"), "author": (c.get("author") or {}).get("displayName"), "created": c.get("created"),
                      "text": to_text(c.get("body"), 8000)} for c in comments],
        "attachments": attachments,
    }
