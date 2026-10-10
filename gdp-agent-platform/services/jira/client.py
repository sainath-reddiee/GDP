"""Jira Cloud REST v3 through OAuth 2.0 (3LO), with HTTP injected so it can be tested without a network.

`http(method, url, headers, body)` -> (status, payload): payload is the parsed JSON body, or text when the response is
not JSON. Every call acts as the signed-in engineer (their access token), so Jira's own permissions apply.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

AUTH = "https://auth.atlassian.com"
API = "https://api.atlassian.com"
SCOPES = ["read:jira-work", "write:jira-work", "read:jira-user", "offline_access"]
ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,30}-\d{1,9}$")
LIST_FIELDS = ["summary", "status", "priority", "issuetype", "assignee", "reporter", "updated", "created", "project", "labels"]
DETAIL_FIELDS = LIST_FIELDS + ["description", "comment", "attachment", "environment"]
MAX_PAGES = 5

Http = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Tuple[int, Any]]


class JiraError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


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
    status, payload = http("POST", f"{AUTH}/oauth/token", {"Content-Type": "application/json"}, body)
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
    status, payload = http("GET", f"{API}/oauth/token/accessible-resources", {"Authorization": f"Bearer {access_token}",
                                                                               "Accept": "application/json"}, None)
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
        status, payload = self.http(method, self.base + path, {"Authorization": f"Bearer {self.token}", "Accept": "application/json",
                                                              "Content-Type": "application/json"}, body)
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
