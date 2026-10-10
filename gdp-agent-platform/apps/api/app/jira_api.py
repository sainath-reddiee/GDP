"""Jira Cloud for QA, per engineer: each user connects their own Atlassian account (OAuth 2.0 3LO), so every read and
write is made as them and Jira's own permissions apply.

Setup on the API host (never in the database or the browser):
  JIRA_CLIENT_SECRET  the OAuth app's secret
  JIRA_TOKEN_KEY      a long random value; refresh tokens are stored ENCRYPTed with it
  JIRA_CLIENT_ID      optional; can also be set in Admin, Integrations, Jira
Refresh tokens rotate on every use and the new one replaces the stored one. Access tokens live in memory only.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _json, _set_config, _snowflake_error, _source_call, current_db
from services.jira import adf
from services.jira.client import (
    JiraClient, JiraError, accessible_resources, authorize_url, check_key, detail, exchange_code, jql_string, pick_site,
    refresh_tokens, summary,
)

router = APIRouter()
CONFIG_KEY = "JIRA"
DEFAULT_REDIRECT = "http://localhost:3000/bff/jira/callback"
STATE_MINUTES = 15
PREVIEW_BYTES = 256 * 1024
# 428, not 401: the web app treats 401 as "the platform session ended" and signs the user out
NOT_CONNECTED = 428
_access: dict[tuple[str, str], tuple[str, float]] = {}   # (user, cloud) -> (access token, expiry)
_locks: dict[str, threading.Lock] = {}


# ---------------------------------------------------------------- plumbing

def _http(method: str, url: str, headers: dict, body: Optional[dict]) -> tuple[int, Any]:
    with httpx.Client(timeout=25, follow_redirects=False) as client:
        r = client.request(method, url, headers=headers, json=body)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def _config(db: Db) -> dict:
    found = db.query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s AND IS_CURRENT", (CONFIG_KEY,))
    cfg = (_json(found[0]["config_value"]) if found else None) or {}
    cfg["client_id"] = (cfg.get("client_id") or os.environ.get("JIRA_CLIENT_ID") or "").strip()
    cfg["redirect_uri"] = (cfg.get("redirect_uri") or os.environ.get("JIRA_REDIRECT_URI") or DEFAULT_REDIRECT).strip()
    return cfg


def _secret() -> str:
    return (os.environ.get("JIRA_CLIENT_SECRET") or "").strip()


def _token_key() -> str:
    key = (os.environ.get("JIRA_TOKEN_KEY") or "").strip()
    return key if len(key) >= 16 else ""


def _missing(cfg: dict) -> list[str]:
    missing = []
    if not cfg.get("client_id"):
        missing.append("the OAuth app's client ID (Admin, Integrations, Jira)")
    if not _secret():
        missing.append("JIRA_CLIENT_SECRET on the API host")
    if not _token_key():
        missing.append("JIRA_TOKEN_KEY (16+ characters) on the API host")
    return missing


def _ready(cfg: dict) -> None:
    missing = _missing(cfg)
    if missing:
        raise HTTPException(409, "Jira is not set up yet: missing " + "; ".join(missing) + ".")


def _log(db: Db, issue_key: str, action: str, status: str, cloud_id: Optional[str] = None, run_id: Optional[str] = None,
         detail_: Optional[dict] = None, error: Optional[str] = None) -> None:
    try:
        db.execute("""INSERT INTO JIRA.ACTION_LOG (ACTION_ID, ISSUE_KEY, CLOUD_ID, RUN_ID, ACTION, DETAIL, STATUS, ERROR)
                      SELECT %s, %s, NULLIF(%s, ''), NULLIF(%s, ''), %s, PARSE_JSON(%s), %s, NULLIF(%s, '')""",
                   (str(uuid.uuid4()), issue_key, cloud_id or "", run_id or "", action, json.dumps(detail_ or {}), status, (error or "")[:2000]))
    except Exception:
        pass


def _jira_error(exc: JiraError) -> HTTPException:
    if exc.status in (401, 403):
        return HTTPException(403, f"Jira refused this as your account: {exc.message}")
    if exc.status == 404:
        return HTTPException(404, f"Not found in Jira, or you cannot see it: {exc.message}")
    return HTTPException(502, f"Jira: {exc.message}")


def _key(key: str) -> str:
    """An issue key from the request; a malformed one is the caller's mistake (400), not a server error."""
    try:
        return check_key(key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _store_refresh(db: Db, user: str, cloud_id: str, refresh_token: str, **fields: Any) -> None:
    key = _token_key()
    db.execute("""MERGE INTO JIRA.USER_TOKEN T USING (SELECT %s AS USER_NAME, %s AS CLOUD_ID) S
                    ON T.USER_NAME = S.USER_NAME AND T.CLOUD_ID = S.CLOUD_ID
                  WHEN MATCHED THEN UPDATE SET REFRESH_TOKEN = ENCRYPT(%s, %s), UPDATED_AT = CURRENT_TIMESTAMP(),
                       SITE_URL = COALESCE(NULLIF(%s, ''), T.SITE_URL), ACCOUNT_ID = COALESCE(NULLIF(%s, ''), T.ACCOUNT_ID),
                       DISPLAY_NAME = COALESCE(NULLIF(%s, ''), T.DISPLAY_NAME), SCOPES = COALESCE(PARSE_JSON(NULLIF(%s, '')), T.SCOPES)
                  WHEN NOT MATCHED THEN INSERT (USER_NAME, CLOUD_ID, SITE_URL, ACCOUNT_ID, DISPLAY_NAME, REFRESH_TOKEN, SCOPES)
                       VALUES (S.USER_NAME, S.CLOUD_ID, NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''), ENCRYPT(%s, %s), PARSE_JSON(NULLIF(%s, '')))""",
               (user, cloud_id, refresh_token, key, fields.get("site_url", ""), fields.get("account_id", ""), fields.get("display_name", ""),
                fields.get("scopes", ""), fields.get("site_url", ""), fields.get("account_id", ""), fields.get("display_name", ""),
                refresh_token, key, fields.get("scopes", "")))


def _connection(db: Db, cfg: dict) -> Optional[dict]:
    """The signed-in user's Jira connection for the configured site (the latest one when no site is configured)."""
    rows = db.query("""SELECT CLOUD_ID, SITE_URL, ACCOUNT_ID, DISPLAY_NAME, CONNECTED_AT::VARCHAR AS CONNECTED_AT,
                              UPDATED_AT::VARCHAR AS UPDATED_AT
                         FROM JIRA.USER_TOKEN WHERE USER_NAME = %s ORDER BY UPDATED_AT DESC""", (db.user,))
    site = (cfg.get("site_url") or "").lower().rstrip("/")
    for r in rows:
        if not site or (r.get("site_url") or "").lower().rstrip("/") == site:
            return r
    return None


def _client(db: Db) -> tuple[JiraClient, dict, dict]:
    """(client acting as the signed-in user, their connection, config). Refreshes and rotates the token when needed."""
    cfg = _config(db)
    _ready(cfg)
    conn = _connection(db, cfg)
    if not conn:
        raise HTTPException(NOT_CONNECTED, "Connect your Jira account first.")
    cache_key = (db.user, conn["cloud_id"])
    lock = _locks.setdefault(db.user, threading.Lock())
    with lock:
        token, expires = _access.get(cache_key, ("", 0.0))
        if not token or expires - time.time() < 60:
            found = db.query("""SELECT TO_VARCHAR(DECRYPT(REFRESH_TOKEN, %s), 'UTF-8') AS T FROM JIRA.USER_TOKEN
                                 WHERE USER_NAME = %s AND CLOUD_ID = %s""", (_token_key(), db.user, conn["cloud_id"]))
            if not found or not found[0].get("t"):
                raise HTTPException(NOT_CONNECTED, "Your Jira connection could not be read; connect again.")
            try:
                tokens = refresh_tokens(_http, cfg["client_id"], _secret(), found[0]["t"])
            except JiraError as exc:
                if exc.status in (400, 401, 403):
                    db.execute("DELETE FROM JIRA.USER_TOKEN WHERE USER_NAME = %s AND CLOUD_ID = %s", (db.user, conn["cloud_id"]))
                    raise HTTPException(NOT_CONNECTED, "Your Jira sign-in has expired or was revoked; connect again.") from None
                raise _jira_error(exc) from None
            if tokens.get("refresh_token"):
                _store_refresh(db, db.user, conn["cloud_id"], tokens["refresh_token"])  # rotated: the old one is now dead
            token, expires = tokens["access_token"], time.time() + int(tokens.get("expires_in") or 3600)
            _access[cache_key] = (token, expires)
    return JiraClient(_http, conn["cloud_id"], token), conn, cfg


def _web_base(cfg: dict) -> str:
    parsed = urlparse(cfg.get("redirect_uri") or DEFAULT_REDIRECT)
    return f"{parsed.scheme}://{parsed.netloc}"


# ---------------------------------------------------------------- setup and connection

@router.get("/api/jira/status")
def status(db: Db = Depends(current_db)):
    """What is set up, and whether the signed-in user is connected."""
    try:
        cfg = _config(db)
        conn = _connection(db, cfg)
        users = db.query("SELECT COUNT(DISTINCT USER_NAME) AS N FROM JIRA.USER_TOKEN")[0]["n"]
    except Exception as exc:
        if "does not exist" in str(exc):
            return {"ready": False, "installed": False}
        raise _snowflake_error(exc) from exc
    return {"installed": True, "ready": not _missing(cfg), "missing": _missing(cfg),
            "client_id": bool(cfg.get("client_id")), "client_secret": bool(_secret()), "token_key": bool(_token_key()),
            "site_url": cfg.get("site_url"), "default_project": cfg.get("default_project"), "redirect_uri": cfg["redirect_uri"],
            "connected": conn, "users_connected": int(users or 0)}


class JiraConfigIn(BaseModel):
    site_url: Optional[str] = Field(default=None, max_length=300)
    client_id: Optional[str] = Field(default=None, max_length=200)
    redirect_uri: Optional[str] = Field(default=None, max_length=500)
    default_project: Optional[str] = Field(default=None, max_length=40)


@router.put("/api/jira/config")
def set_config(body: JiraConfigIn, db: Db = Depends(current_db)):
    """The Jira site, OAuth app client ID, callback URL and default project. The client secret never comes here."""
    current = {k: v for k, v in _config(db).items() if k in ("site_url", "client_id", "redirect_uri", "default_project")}
    site = (body.site_url if body.site_url is not None else current.get("site_url") or "").strip().rstrip("/")
    if site and not re.fullmatch(r"https://[A-Za-z0-9.\-]+(:\d+)?", site):
        raise HTTPException(400, "Site URL: https://yourteam.atlassian.net")
    redirect = (body.redirect_uri if body.redirect_uri is not None else current.get("redirect_uri") or DEFAULT_REDIRECT).strip()
    if not re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d+)?/bff/jira/callback", redirect):
        raise HTTPException(400, "Callback URL: the web app's address followed by /bff/jira/callback")
    client_id = (body.client_id if body.client_id is not None else current.get("client_id") or "").strip()
    if client_id and not re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", client_id):
        raise HTTPException(400, "That does not look like an OAuth client ID")
    project = (body.default_project if body.default_project is not None else current.get("default_project") or "").strip().upper()
    if project and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,30}", project):
        raise HTTPException(400, "Project key: letters and digits, for example QA")
    value = {"site_url": site, "client_id": client_id, "redirect_uri": redirect, "default_project": project}
    _set_config(db, CONFIG_KEY, value, "Jira Cloud connection for QA (the client secret stays on the API host)")
    return status(db)


class ConnectIn(BaseModel):
    return_to: Optional[str] = Field(default=None, max_length=500)


@router.post("/api/jira/connect")
def connect(body: ConnectIn, db: Db = Depends(current_db)):
    """The Atlassian sign-in URL for the signed-in user (with a one-time state tied to them)."""
    cfg = _config(db)
    _ready(cfg)
    back = body.return_to or "/"
    if not re.fullmatch(r"/(?![/\\])[^\\\s]*", back):  # a same-site path: never //host or /\host
        back = "/"
    state = secrets.token_urlsafe(32)
    db.execute(f"DELETE FROM JIRA.OAUTH_STATE WHERE CREATED_AT < DATEADD(minute, -{STATE_MINUTES}, CURRENT_TIMESTAMP())")
    db.execute("INSERT INTO JIRA.OAUTH_STATE (STATE, USER_NAME, RETURN_TO) VALUES (%s, %s, %s)", (state, db.user, back))
    return {"url": authorize_url(cfg["client_id"], cfg["redirect_uri"], state)}


class CallbackIn(BaseModel):
    code: str = Field(min_length=4, max_length=4000)
    state: str = Field(min_length=16, max_length=128)


@router.post("/api/jira/callback")
def callback(body: CallbackIn, db: Db = Depends(current_db)):
    """Finish the sign-in: exchange the code, pick the site, remember the (encrypted) refresh token."""
    cfg = _config(db)
    _ready(cfg)
    found = db.query(f"""SELECT RETURN_TO FROM JIRA.OAUTH_STATE WHERE STATE = %s AND USER_NAME = %s
                           AND CREATED_AT >= DATEADD(minute, -{STATE_MINUTES}, CURRENT_TIMESTAMP())""", (body.state, db.user))
    db.execute("DELETE FROM JIRA.OAUTH_STATE WHERE STATE = %s", (body.state,))
    if not found:
        raise HTTPException(400, "This sign-in link expired or was started by someone else. Connect again.")
    try:
        tokens = exchange_code(_http, cfg["client_id"], _secret(), body.code, cfg["redirect_uri"])
        site = pick_site(accessible_resources(_http, tokens["access_token"]), cfg.get("site_url"))
        me = JiraClient(_http, site["id"], tokens["access_token"]).myself()
    except JiraError as exc:
        _log(db, "-", "CONNECT", "FAILED", error=exc.message)
        raise HTTPException(400 if exc.status in (400, 401) else 502, exc.message) from None
    if not tokens.get("refresh_token"):
        raise HTTPException(400, "Atlassian did not return a refresh token; the app needs the offline_access scope.")
    _store_refresh(db, db.user, site["id"], tokens["refresh_token"], site_url=site.get("url", ""), account_id=me.get("accountId", ""),
                   display_name=me.get("displayName", ""), scopes=json.dumps(str(tokens.get("scope") or "").split()))
    _access[(db.user, site["id"])] = (tokens["access_token"], time.time() + int(tokens.get("expires_in") or 3600))
    _log(db, "-", "CONNECT", "DONE", cloud_id=site["id"], detail_={"site": site.get("url")})
    return {"return_to": found[0]["return_to"] or "/", "display_name": me.get("displayName"), "site_url": site.get("url")}


@router.delete("/api/jira/connection")
def disconnect(db: Db = Depends(current_db)):
    """Forget the signed-in user's Jira tokens (they can also revoke the app in their Atlassian account)."""
    db.execute("DELETE FROM JIRA.USER_TOKEN WHERE USER_NAME = %s", (db.user,))
    for k in [k for k in _access if k[0] == db.user]:
        _access.pop(k, None)
    _log(db, "-", "DISCONNECT", "DONE")
    return {"connected": None}


# ---------------------------------------------------------------- issues

@router.get("/api/jira/issues")
def issues(scope: str = "mine", q: str = "", project: str = "", run_id: Optional[str] = None, db: Db = Depends(current_db)):
    """Issues for the QA workbench: assigned to me (open), linked to a run, or a search by key or text."""
    client, conn, cfg = _client(db)
    project = (project or cfg.get("default_project") or "").strip().upper()
    if project and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,30}", project):
        raise HTTPException(400, "invalid project key")
    where: list[str] = []
    if scope == "run":
        if not run_id:
            raise HTTPException(400, "run_id is required")
        keys = [r["issue_key"] for r in db.query("SELECT DISTINCT ISSUE_KEY FROM JIRA.ISSUE_LINK WHERE RUN_ID = %s AND NOT IS_DELETED",
                                                (run_id,))]
        if not keys:
            return {"issues": [], "jql": None}
        where.append("key in (" + ", ".join(check_key(k) for k in keys[:100]) + ")")
    elif scope == "search":
        text = q.strip()
        if not text:
            return {"issues": [], "jql": None}
        try:
            where.append(f"key = {check_key(text)}")
        except ValueError:
            where.append(f"text ~ {jql_string(text[:200])}")
            if project:
                where.append(f"project = {project}")
    else:
        where += ["assignee = currentUser()", "statusCategory != Done"]
        if project:
            where.append(f"project = {project}")
    jql = " AND ".join(where) + " ORDER BY updated DESC"
    try:
        found = client.search(jql, page_size=50, max_pages=2 if scope != "run" else 3)
    except JiraError as exc:
        raise _jira_error(exc) from None
    site = conn.get("site_url") or ""
    linked = {}
    if run_id:
        for r in db.query("SELECT ISSUE_KEY, COUNT(*) AS N FROM JIRA.ISSUE_LINK WHERE RUN_ID = %s AND NOT IS_DELETED GROUP BY 1", (run_id,)):
            linked[r["issue_key"]] = int(r["n"])
    return {"issues": [{**summary(i, site), "linked": linked.get(i.get("key"), 0)} for i in found], "jql": jql}


@router.get("/api/jira/issues/{key}")
def issue(key: str, run_id: Optional[str] = None, db: Db = Depends(current_db)):
    client, conn, _ = _client(db)
    try:
        found = client.issue(check_key(key))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except JiraError as exc:
        raise _jira_error(exc) from None
    links = db.query("""SELECT LINK_ID, RUN_ID, QA_TEST_ID, TARGET_TABLE, LINKED_BY, LINKED_AT::VARCHAR AS LINKED_AT
                          FROM JIRA.ISSUE_LINK WHERE ISSUE_KEY = %s AND NOT IS_DELETED ORDER BY LINKED_AT DESC""", (check_key(key),))
    history = db.query("""SELECT ACTION, STATUS, ERROR, ACTED_BY, ACTED_AT::VARCHAR AS ACTED_AT, RUN_ID, DETAIL FROM JIRA.ACTION_LOG
                           WHERE ISSUE_KEY = %s ORDER BY ACTED_AT DESC LIMIT 20""", (check_key(key),))
    for h in history:
        h["detail"] = _json(h.get("detail"))
    return {**detail(found, conn.get("site_url") or ""), "links": links, "history": history}


def _attachment_text(client: JiraClient, attachment_id: str, limit: int = PREVIEW_BYTES) -> tuple[str, bool]:
    """First `limit` bytes of an attachment as text (the content URL redirects to Atlassian's media store)."""
    with httpx.Client(timeout=25, follow_redirects=True) as http:
        with http.stream("GET", client.attachment_url(attachment_id), headers={"Authorization": f"Bearer {client.token}"}) as r:
            if r.status_code != 200:
                raise JiraError(r.status_code, "could not download the attachment")
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > limit:
                    break
    return data[:limit].decode("utf-8", errors="replace"), len(data) > limit


@router.get("/api/jira/issues/{key}/attachments/{attachment_id}")
def attachment(key: str, attachment_id: str, db: Db = Depends(current_db)):
    """A text attachment (CSV, SQL, logs) for preview, capped at 256 KB."""
    client, _, _ = _client(db)
    try:
        meta = next((a for a in detail(client.issue(_key(key)))["attachments"] if a["id"] == attachment_id), None)
        if not meta:
            raise HTTPException(404, "attachment not on this issue")
        if not meta["previewable"]:
            raise HTTPException(415, "only text attachments (CSV, SQL, TXT, JSON, logs) can be previewed")
        text, truncated = _attachment_text(client, attachment_id)
    except JiraError as exc:
        raise _jira_error(exc) from None
    return {"name": meta["name"], "text": text, "truncated": truncated}


# ---------------------------------------------------------------- links to runs and QA tests

@router.get("/api/runs/{run_id}/jira/links")
def run_links(run_id: str, db: Db = Depends(current_db)):
    return {"links": db.query("""SELECT L.LINK_ID, L.ISSUE_KEY, L.QA_TEST_ID, T.TITLE AS TEST_TITLE, L.SUMMARY, L.STATUS, L.LINKED_BY,
                                       L.LINKED_AT::VARCHAR AS LINKED_AT
                                  FROM JIRA.ISSUE_LINK L LEFT JOIN CONTRACT.QA_TEST_CASE T ON T.TEST_ID = L.QA_TEST_ID
                                 WHERE L.RUN_ID = %s AND NOT L.IS_DELETED ORDER BY L.LINKED_AT DESC""", (run_id,))}


class LinkIn(BaseModel):
    issue_key: str = Field(min_length=3, max_length=64)
    qa_test_id: Optional[str] = Field(default=None, max_length=64)
    remote_link: bool = False


@router.post("/api/runs/{run_id}/jira/links")
def link(run_id: str, body: LinkIn, db: Db = Depends(current_db)):
    """Link an issue to the run (and optionally to a QA test). With remote_link, also add a link to the run in Jira."""
    client, conn, cfg = _client(db)
    key = _key(body.issue_key)
    run = db.query("SELECT RUN_NAME, TARGET_MODEL FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")
    if body.qa_test_id and not db.query("SELECT 1 FROM CONTRACT.QA_TEST_CASE WHERE TEST_ID = %s AND RUN_ID = %s AND NOT IS_DELETED",
                                        (body.qa_test_id, run_id)):
        raise HTTPException(404, "that QA test is not in this run")
    try:
        found = summary(client.issue(key), conn.get("site_url") or "")
    except JiraError as exc:
        raise _jira_error(exc) from None
    exists = db.query("""SELECT LINK_ID FROM JIRA.ISSUE_LINK WHERE ISSUE_KEY = %s AND RUN_ID = %s AND NOT IS_DELETED
                           AND COALESCE(QA_TEST_ID, '') = %s""", (key, run_id, body.qa_test_id or ""))
    if not exists:
        db.execute("""INSERT INTO JIRA.ISSUE_LINK (LINK_ID, ISSUE_KEY, CLOUD_ID, RUN_ID, QA_TEST_ID, TARGET_TABLE, SUMMARY, STATUS)
                      VALUES (%s, %s, %s, %s, NULLIF(%s, ''), %s, %s, %s)""",
                   (str(uuid.uuid4()), key, conn["cloud_id"], run_id, body.qa_test_id or "", run[0].get("target_model") or "",
                    (found["summary"] or "")[:1000], found.get("status") or ""))
        _log(db, key, "LINK", "DONE", conn["cloud_id"], run_id, {"qa_test_id": body.qa_test_id})
    remote = None
    if body.remote_link:
        url = f"{_web_base(cfg)}/runs/{run_id}/qa"
        try:
            client.remote_link(key, url, f"QA run: {run[0]['run_name']}", f"agentic-pipeline:run:{run_id}")
            remote = "added"
            _log(db, key, "REMOTE_LINK", "DONE", conn["cloud_id"], run_id, {"url": url})
        except JiraError as exc:
            remote = f"not added: {exc.message}"
            _log(db, key, "REMOTE_LINK", "FAILED", conn["cloud_id"], run_id, error=exc.message)
    return {"linked": key, "already": bool(exists), "remote_link": remote, "issue": found}


@router.delete("/api/runs/{run_id}/jira/links/{link_id}")
def unlink(run_id: str, link_id: str, db: Db = Depends(current_db)):
    found = db.query("SELECT ISSUE_KEY, CLOUD_ID FROM JIRA.ISSUE_LINK WHERE LINK_ID = %s AND RUN_ID = %s AND NOT IS_DELETED", (link_id, run_id))
    if not found:
        raise HTTPException(404, "link not found")
    db.execute("UPDATE JIRA.ISSUE_LINK SET IS_DELETED = TRUE WHERE LINK_ID = %s", (link_id,))
    _log(db, found[0]["issue_key"], "UNLINK", "DONE", found[0]["cloud_id"], run_id)
    return {"unlinked": link_id}


# ---------------------------------------------------------------- AI triage, results report, comment, transition

@router.post("/api/runs/{run_id}/jira/{key}/triage")
def triage(run_id: str, key: str, db: Db = Depends(current_db)):
    """Diagnosis of the reported bug against this run, with guarded, compiled tests to reproduce it (not saved)."""
    from services.jira.triage import triage_issue

    client, conn, _ = _client(db)
    try:
        info = detail(client.issue(_key(key)), conn.get("site_url") or "")
        texts = []
        for a in [a for a in info["attachments"] if a["previewable"] and (a.get("size") or 0) <= 64 * 1024][:2]:
            try:
                texts.append({"name": a["name"], "text": _attachment_text(client, a["id"], 8000)[0]})
            except JiraError:
                continue
    except JiraError as exc:
        raise _jira_error(exc) from None
    payload = {k: info.get(k) for k in ("key", "summary", "description", "environment", "comments", "type", "priority", "status")}
    payload["attachments_text"] = texts
    try:
        result = _source_call(db, "CALL JIRA.TRIAGE_ISSUE(%s, %s)", triage_issue, run_id, json.dumps(payload))
    except Exception as exc:
        _log(db, info["key"], "TRIAGE", "FAILED", conn["cloud_id"], run_id, error=str(exc)[:500])
        raise _snowflake_error(exc) from exc
    result = _json(result) if isinstance(result, str) else result
    _log(db, info["key"], "TRIAGE", "DONE", conn["cloud_id"], run_id,
         {"tests": len(result.get("tests") or []), "reproducible": result.get("reproducible")})
    return result


@router.get("/api/runs/{run_id}/jira/{key}/report")
def report(run_id: str, key: str, db: Db = Depends(current_db)):
    """A draft comment from the latest results of the QA tests linked to this issue in this run."""
    from services.jira.triage import report_markdown

    key = _key(key)
    cfg = _config(db)
    run = db.query("SELECT RUN_NAME FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")
    results = db.query("""SELECT T.TEST_ID, T.TITLE, T.EXPECTED, R.OUTCOME, R.ROWS_RETURNED, R.MEASURED, R.CREATED_AT::VARCHAR AS RAN_AT
                            FROM JIRA.ISSUE_LINK L
                            JOIN CONTRACT.QA_TEST_CASE T ON T.TEST_ID = L.QA_TEST_ID
                            LEFT JOIN QUALITY.QA_RESULT R ON R.TEST_ID = L.QA_TEST_ID AND R.RUN_ID = L.RUN_ID
                           WHERE L.ISSUE_KEY = %s AND L.RUN_ID = %s AND NOT L.IS_DELETED
                         QUALIFY ROW_NUMBER() OVER (PARTITION BY T.TEST_ID ORDER BY R.CREATED_AT DESC NULLS LAST) = 1""", (key, run_id))
    url = f"{_web_base(cfg)}/runs/{run_id}/qa"
    return {"markdown": report_markdown(key, run[0]["run_name"], url, results), "results": results, "run_url": url}


class CommentIn(BaseModel):
    markdown: str = Field(min_length=1, max_length=30000)
    run_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/jira/issues/{key}/comment")
def comment(key: str, body: CommentIn, db: Db = Depends(current_db)):
    """Post the (previewed and confirmed) comment to the issue as the signed-in user."""
    client, conn, _ = _client(db)
    key = _key(key)
    try:
        posted = client.add_comment(key, adf.from_markdown(body.markdown))
    except JiraError as exc:
        _log(db, key, "COMMENT", "FAILED", conn["cloud_id"], body.run_id, error=exc.message)
        raise _jira_error(exc) from None
    _log(db, key, "COMMENT", "DONE", conn["cloud_id"], body.run_id, {"comment_id": (posted or {}).get("id"), "preview": adf.plain(body.markdown, 300)})
    return {"comment_id": (posted or {}).get("id"), "url": f"{(conn.get('site_url') or '').rstrip('/')}/browse/{key}"}


@router.get("/api/jira/issues/{key}/transitions")
def transitions(key: str, db: Db = Depends(current_db)):
    client, _, _ = _client(db)
    try:
        return {"transitions": client.transitions(_key(key))}
    except JiraError as exc:
        raise _jira_error(exc) from None


class TransitionIn(BaseModel):
    transition_id: str = Field(min_length=1, max_length=10)
    run_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/jira/issues/{key}/transition")
def transition(key: str, body: TransitionIn, db: Db = Depends(current_db)):
    """Move the issue to another status (one Jira allows for this user)."""
    client, conn, _ = _client(db)
    key = _key(key)
    try:
        allowed = {t["id"]: t for t in client.transitions(key)}
        if body.transition_id not in allowed:
            raise HTTPException(400, "That status change is not available for this issue any more; reload and pick again.")
        client.transition(key, body.transition_id)
    except JiraError as exc:
        _log(db, key, "TRANSITION", "FAILED", conn["cloud_id"], body.run_id, error=exc.message)
        raise _jira_error(exc) from None
    to = allowed[body.transition_id].get("to")
    db.execute("UPDATE JIRA.ISSUE_LINK SET STATUS = %s WHERE ISSUE_KEY = %s AND NOT IS_DELETED", (to or "", key))
    _log(db, key, "TRANSITION", "DONE", conn["cloud_id"], body.run_id, {"to": to, "transition": allowed[body.transition_id].get("name")})
    return {"status": to}
