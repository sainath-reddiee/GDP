"""Jira Cloud for QA, per engineer: each user connects their own Atlassian account (OAuth 2.0 3LO), so every read and
write is made as them and Jira's own permissions apply.

Setup on the API host (never in the database or the browser):
  JIRA_CLIENT_SECRET  the OAuth app's secret
  JIRA_TOKEN_KEY      a long random value; refresh and access tokens are sealed in the API with a key derived from it
                      (services.common.secretbox, AES-GCM) and stored as ciphertext; neither the key nor a token is
                      ever part of a SQL statement
  JIRA_CLIENT_ID      optional; can also be set in Admin, Integrations, Jira
Refresh tokens rotate on every use and the new one replaces the stored one. Several API replicas share one
USER_TOKEN row, so a refresh is guarded by a lease on that row (compare-and-swap on TOKEN_VERSION): one replica
refreshes and stores both tokens, the others wait for the new version and use the stored access token. Without that,
the losing replica would present a refresh token Atlassian just rotated away and disconnect a valid account.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import secrets
import socket
import threading
import time
import uuid
from typing import Any, Callable, Literal, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _json, _set_config, _snowflake_error, _source_call, current_db
from services.common import secretbox
from services.jira import adf
from services.jira.client import (
    AGILE_SCOPES, JiraClient, JiraError, accessible_resources, authorize_url, check_key, check_project, detail, exchange_code,
    jql_string, pick_site, refresh_tokens, summary,
)

router = APIRouter()
CONFIG_KEY = "JIRA"
DEFAULT_REDIRECT = "http://localhost:3000/bff/jira/callback"
STATE_MINUTES = 15
PREVIEW_BYTES = 256 * 1024
# 428, not 401: the web app treats 401 as "the platform session ended" and signs the user out
NOT_CONNECTED = 428
# (user, cloud, account id, token version) -> (access token, expiry). The version moves on every refresh and sign-in
# on any replica, so a token another replica replaced (or a reconnect to another account) is never served from here
_access: dict[tuple[str, str, str, int], tuple[str, float]] = {}
_locks: dict[str, threading.Lock] = {}
_REPLICA = f"{socket.gethostname()[:40]}:{os.getpid()}"
LEASE_WAIT = 10.0   # seconds a replica waits for another one's refresh
LEASE_POLL = 0.5
_sleep = time.sleep
RECONNECT_AGILE = "Reconnect Jira to read boards and sprints"
TRIAGE_ATTACHMENT_BYTES = 64 * 1024
BULK_MAX = 50


# ---------------------------------------------------------------- plumbing

def _http(method: str, url: str, headers: dict, body: Optional[dict]) -> tuple[int, Any, dict]:
    with httpx.Client(timeout=25, follow_redirects=False) as client:
        r = client.request(method, url, headers=headers, json=body)
    answer = {k: v for k, v in r.headers.items() if k.lower() == "retry-after"}
    try:
        return r.status_code, r.json(), answer
    except ValueError:
        return r.status_code, r.text, answer


def _config(db: Db) -> dict:
    found = db.query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s AND IS_CURRENT", (CONFIG_KEY,))
    cfg = (_json(found[0]["config_value"]) if found else None) or {}
    cfg["client_id"] = (cfg.get("client_id") or os.environ.get("JIRA_CLIENT_ID") or "").strip()
    cfg["redirect_uri"] = (cfg.get("redirect_uri") or os.environ.get("JIRA_REDIRECT_URI") or DEFAULT_REDIRECT).strip()
    return cfg


def _secret() -> str:
    return (os.environ.get("JIRA_CLIENT_SECRET") or "").strip()


def _token_key() -> str:
    return secretbox.jira_key()


RECONNECT_FORMAT = ("Your Jira connection was stored in an older format (or with another key) and cannot be read; "
                    "connect Jira again.")


def _seal_token(token: str, kind: str, user: str, cloud_id: str) -> bytes:
    """A refresh or access token sealed for JIRA.USER_TOKEN, bound to its row (kind, user, site)."""
    return secretbox.seal(token, _token_key(), secretbox.JIRA_TOKEN, f"{kind}:{user}:{cloud_id}")


def _open_token(value: Any, kind: str, user: str, cloud_id: str) -> Optional[str]:
    """The token, None for NULL. secretbox.LegacySecret for a value stored by Snowflake ENCRYPT() before tokens were
    sealed here, secretbox.SecretError for another key or a changed value: either way the user connects again."""
    return secretbox.open_secret(value, _token_key(), secretbox.JIRA_TOKEN, f"{kind}:{user}:{cloud_id}")


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
         detail_: Optional[dict] = None, error: Optional[str] = None, idempotency_key: Optional[str] = None) -> None:
    extra, value = (", IDEMPOTENCY_KEY", ", %s") if idempotency_key else ("", "")
    params = (str(uuid.uuid4()), issue_key, cloud_id or "", run_id or "", action, json.dumps(detail_ or {}), status, (error or "")[:2000])
    try:
        db.execute(f"""INSERT INTO JIRA.ACTION_LOG (ACTION_ID, ISSUE_KEY, CLOUD_ID, RUN_ID, ACTION, DETAIL, STATUS, ERROR{extra})
                       SELECT %s, %s, NULLIF(%s, ''), NULLIF(%s, ''), %s, PARSE_JSON(%s), %s, NULLIF(%s, ''){value}""",
                   params + ((idempotency_key,) if idempotency_key else ()))
    except Exception:
        pass


def _jira_error(exc: JiraError) -> HTTPException:
    if exc.status == 400:   # bad JQL and the like: Jira's own message is the useful part
        return HTTPException(400, f"Jira: {exc.message}")
    if exc.status in (401, 403):
        return HTTPException(403, f"Jira refused this as your account: {exc.message}")
    if exc.status == 404:
        return HTTPException(404, f"Not found in Jira, or you cannot see it: {exc.message}")
    if exc.status in (429, 503):
        wait = exc.retry_after if exc.retry_after is not None else 30
        what = "Jira is limiting requests" if exc.status == 429 else "Jira is unavailable"
        return HTTPException(exc.status, f"{what}; try again in {wait} seconds.", headers={"Retry-After": str(wait)})
    return HTTPException(502, f"Jira: {exc.message}")


def _key(key: str) -> str:
    """An issue key from the request; a malformed one is the caller's mistake (400), not a server error."""
    try:
        return check_key(key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _missing_column(exc: BaseException) -> bool:
    """True when V028 (stored access token, version and lease columns) is not applied to this database yet."""
    return "invalid identifier" in str(exc).lower()


def _store_access(db: Db, user: str, cloud_id: str, access_token: str, expires_in: int) -> None:
    """Share a fresh access token with the other replicas and start a new token version (after a sign-in)."""
    try:
        db.execute("""UPDATE JIRA.USER_TOKEN SET ACCESS_TOKEN = %s,
                             ACCESS_EXPIRES_AT = DATEADD(second, %s, CURRENT_TIMESTAMP()),
                             TOKEN_VERSION = COALESCE(TOKEN_VERSION, 0) + 1, LEASE_BY = NULL, LEASE_UNTIL = NULL
                       WHERE USER_NAME = %s AND CLOUD_ID = %s""",
                   (_seal_token(access_token, "access", user, cloud_id), int(expires_in), user, cloud_id))
    except Exception as exc:
        if not _missing_column(exc):
            raise


def _store_refresh(db: Db, user: str, cloud_id: str, refresh_token: str, access_token: str = "", expires_in: int = 3600,
                   **fields: Any) -> None:
    sealed = _seal_token(refresh_token, "refresh", user, cloud_id)
    db.execute("""MERGE INTO JIRA.USER_TOKEN T USING (SELECT %s AS USER_NAME, %s AS CLOUD_ID) S
                    ON T.USER_NAME = S.USER_NAME AND T.CLOUD_ID = S.CLOUD_ID
                  WHEN MATCHED THEN UPDATE SET REFRESH_TOKEN = %s, UPDATED_AT = CURRENT_TIMESTAMP(),
                       SITE_URL = COALESCE(NULLIF(%s, ''), T.SITE_URL), ACCOUNT_ID = COALESCE(NULLIF(%s, ''), T.ACCOUNT_ID),
                       DISPLAY_NAME = COALESCE(NULLIF(%s, ''), T.DISPLAY_NAME), SCOPES = COALESCE(PARSE_JSON(NULLIF(%s, '')), T.SCOPES)
                  WHEN NOT MATCHED THEN INSERT (USER_NAME, CLOUD_ID, SITE_URL, ACCOUNT_ID, DISPLAY_NAME, REFRESH_TOKEN, SCOPES)
                       VALUES (S.USER_NAME, S.CLOUD_ID, NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''), %s, PARSE_JSON(NULLIF(%s, '')))""",
               (user, cloud_id, sealed, fields.get("site_url", ""), fields.get("account_id", ""), fields.get("display_name", ""),
                fields.get("scopes", ""), fields.get("site_url", ""), fields.get("account_id", ""), fields.get("display_name", ""),
                sealed, fields.get("scopes", "")))
    if access_token:
        _store_access(db, user, cloud_id, access_token, expires_in)


_CONNECTION_SQL = """SELECT CLOUD_ID, SITE_URL, ACCOUNT_ID, DISPLAY_NAME, CONNECTED_AT::VARCHAR AS CONNECTED_AT,
                             UPDATED_AT::VARCHAR AS UPDATED_AT, SCOPES{version}
                        FROM JIRA.USER_TOKEN WHERE USER_NAME = %s ORDER BY UPDATED_AT DESC"""


def _connection(db: Db, cfg: dict) -> Optional[dict]:
    """The signed-in user's Jira connection for the configured site (the latest one when no site is configured), with
    its token version (0 before V028)."""
    try:
        rows = db.query(_CONNECTION_SQL.format(version=", COALESCE(TOKEN_VERSION, 0) AS TOKEN_VERSION"), (db.user,))
    except Exception as exc:
        if not _missing_column(exc):
            raise
        rows = db.query(_CONNECTION_SQL.format(version=""), (db.user,))
    for r in rows:
        r["scopes"] = [str(s) for s in _json(r.get("scopes")) or []]
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
    cache_key = (db.user, conn["cloud_id"], str(conn.get("account_id") or ""), int(conn.get("token_version") or 0))
    client = JiraClient(_http, conn["cloud_id"], _cached_token(db, cfg, cache_key))
    client.http = _reauthorizing_http(db, cfg, client, cache_key)
    return client, conn, cfg


def _cached_token(db: Db, cfg: dict, cache_key: tuple[str, str, str, int], force: bool = False) -> str:
    """First tier: one refresh per user inside this process; entries of older token versions are dropped."""
    user, cloud_id = cache_key[0], cache_key[1]
    with _locks.setdefault(user, threading.Lock()):
        token, expires = ("", 0.0) if force else _access.get(cache_key, ("", 0.0))
        if not token or expires - time.time() < 60:
            token, expires = _shared_token(db, cfg, cloud_id)
            for k in [k for k in _access if k[:2] == (user, cloud_id) and k != cache_key]:
                _access.pop(k, None)
            _access[cache_key] = (token, expires)
    return token


RECONNECT_REJECTED = "Jira no longer accepts your sign-in; connect Jira again."


def _expire_access(db: Db, cache_key: tuple[str, str, str, int]) -> None:
    """Jira refused the access token: forget it here and mark the stored one expired (only the version it came from,
    so a token another replica just stored is kept), so the next read refreshes it."""
    _access.pop(cache_key, None)
    try:
        db.execute("""UPDATE JIRA.USER_TOKEN SET ACCESS_EXPIRES_AT = NULL
                       WHERE USER_NAME = %s AND CLOUD_ID = %s AND COALESCE(TOKEN_VERSION, 0) = %s""",
                   (db.user, cache_key[1], cache_key[3]))
    except Exception:
        pass   # before V028 there is no stored access token to expire


def _reauthorizing_http(db: Db, cfg: dict, client: JiraClient, cache_key: tuple[str, str, str, int]) -> Callable:
    """The client's transport: on a 401 from Jira the access token is dropped (here and in USER_TOKEN), a fresh one
    is fetched through the shared token path and the call is made once more. A second 401 in the same request means
    the sign-in itself is no longer accepted: 428, connect again."""
    state = {"retried": False}

    def call(method: str, url: str, headers: dict, body: Optional[dict]) -> tuple:
        answer = _http(method, url, headers, body)   # (status, payload[, headers])
        if answer[0] != 401:
            return answer
        _expire_access(db, cache_key)
        if state["retried"]:
            raise HTTPException(NOT_CONNECTED, RECONNECT_REJECTED)
        state["retried"] = True
        token = _cached_token(db, cfg, cache_key, force=True)
        client.token, client.auth = token, f"Bearer {token}"
        answer = _http(method, url, {**headers, "Authorization": client.auth}, body)
        if answer[0] == 401:
            _expire_access(db, cache_key)
            raise HTTPException(NOT_CONNECTED, RECONNECT_REJECTED)
        return answer

    return call


REVOKED = "Your Jira sign-in has expired or was revoked; connect again."
_ROW_SQL = """SELECT REFRESH_TOKEN AS T, ACCESS_TOKEN AS A,
                     DATEDIFF(second, CURRENT_TIMESTAMP(), ACCESS_EXPIRES_AT) AS TTL, COALESCE(TOKEN_VERSION, 0) AS V,
                     (LEASE_UNTIL IS NOT NULL AND LEASE_UNTIL >= CURRENT_TIMESTAMP()) AS LEASED
                FROM JIRA.USER_TOKEN WHERE USER_NAME = %s AND CLOUD_ID = %s"""


def _token_row(db: Db, cloud_id: str) -> Optional[dict]:
    """The user's token row with both tokens opened. A refresh token that cannot be opened (stored by the old
    Snowflake ENCRYPT(), or with another key) means the user must connect again (428); an access token that cannot be
    opened is only refreshed."""
    rows = db.query(_ROW_SQL, (db.user, cloud_id))
    if not rows:
        return None
    row = dict(rows[0])
    try:
        row["t"] = _open_token(row.get("t"), "refresh", db.user, cloud_id)
    except secretbox.SecretError:
        raise HTTPException(NOT_CONNECTED, RECONNECT_FORMAT) from None
    try:
        row["a"] = _open_token(row.get("a"), "access", db.user, cloud_id)
    except secretbox.SecretError:
        row["a"] = None
    return row


def _usable(row: Optional[dict]) -> Optional[tuple[str, float]]:
    """The stored access token and its expiry, when it is valid for more than a minute."""
    ttl = int((row or {}).get("ttl") or 0)
    if row and row.get("a") and ttl > 60:
        return row["a"], time.time() + ttl
    return None


def _shared_token(db: Db, cfg: dict, cloud_id: str) -> tuple[str, float]:
    """Second tier, across replicas: use the stored access token while it is valid; otherwise claim the row's lease
    (compare-and-swap on TOKEN_VERSION) and refresh, or wait for the replica that holds it."""
    try:
        row = _token_row(db, cloud_id)
    except Exception as exc:
        if _missing_column(exc):
            return _refresh_legacy(db, cfg, cloud_id)
        raise
    if not row or not row.get("t"):
        raise HTTPException(NOT_CONNECTED, "Your Jira connection could not be read; connect again.")
    usable = _usable(row)
    if usable:
        return usable
    version = int(row.get("v") or 0)
    me = f"{_REPLICA}:{uuid.uuid4().hex[:12]}"
    claimed = db.execute_count("""UPDATE JIRA.USER_TOKEN SET LEASE_BY = %s, LEASE_UNTIL = DATEADD(second, 30, CURRENT_TIMESTAMP())
                                   WHERE USER_NAME = %s AND CLOUD_ID = %s AND COALESCE(TOKEN_VERSION, 0) = %s
                                     AND (LEASE_UNTIL IS NULL OR LEASE_UNTIL < CURRENT_TIMESTAMP())""",
                               (me, db.user, cloud_id, version))
    if not claimed:
        return _wait_for_refresh(db, cloud_id, version)
    return _refresh_with_lease(db, cfg, cloud_id, version, me)


def _release(db: Db, cloud_id: str, me: str) -> None:
    try:
        db.execute_count("UPDATE JIRA.USER_TOKEN SET LEASE_BY = NULL, LEASE_UNTIL = NULL WHERE USER_NAME = %s AND CLOUD_ID = %s AND LEASE_BY = %s",
                         (db.user, cloud_id, me))
    except Exception:
        pass   # the lease runs out on its own after 30 seconds


def _refresh_with_lease(db: Db, cfg: dict, cloud_id: str, version: int, me: str) -> tuple[str, float]:
    """This replica holds the lease: refresh, store both tokens as the next version and free the lease."""
    try:
        row = _token_row(db, cloud_id)   # read again under the lease: a sign-in may have replaced the refresh token
        if not row or not row.get("t"):
            raise HTTPException(NOT_CONNECTED, "Your Jira connection could not be read; connect again.")
        tokens = refresh_tokens(_http, cfg["client_id"], _secret(), row["t"])
    except JiraError as exc:
        if exc.status in (400, 401, 403):
            # revoked, unless another replica rotated the token after our lease ran out: only our own version goes
            gone = db.execute_count("""DELETE FROM JIRA.USER_TOKEN WHERE USER_NAME = %s AND CLOUD_ID = %s
                                          AND COALESCE(TOKEN_VERSION, 0) = %s AND LEASE_BY = %s""", (db.user, cloud_id, version, me))
            if not gone:
                usable = _usable(_token_row(db, cloud_id))
                if usable:
                    return usable
            raise HTTPException(NOT_CONNECTED, REVOKED) from None
        _release(db, cloud_id, me)
        raise _jira_error(exc) from None
    except BaseException:
        _release(db, cloud_id, me)
        raise
    expires_in = int(tokens.get("expires_in") or 3600)
    rotated = tokens.get("refresh_token") or ""
    stored = db.execute_count("""UPDATE JIRA.USER_TOKEN SET REFRESH_TOKEN = COALESCE(%s, REFRESH_TOKEN),
                                        ACCESS_TOKEN = %s, ACCESS_EXPIRES_AT = DATEADD(second, %s, CURRENT_TIMESTAMP()),
                                        TOKEN_VERSION = COALESCE(TOKEN_VERSION, 0) + 1, LEASE_BY = NULL, LEASE_UNTIL = NULL,
                                        UPDATED_AT = CURRENT_TIMESTAMP()
                                  WHERE USER_NAME = %s AND CLOUD_ID = %s AND LEASE_BY = %s""",
                              (_seal_token(rotated, "refresh", db.user, cloud_id) if rotated else None,
                               _seal_token(tokens["access_token"], "access", db.user, cloud_id), expires_in, db.user, cloud_id, me))
    if not stored:
        _log(db, "-", "TOKEN", "FAILED", cloud_id=cloud_id, error="the token lease ran out before the refreshed token was stored")
    return tokens["access_token"], time.time() + expires_in


def _wait_for_refresh(db: Db, cloud_id: str, version: int) -> tuple[str, float]:
    """Another replica holds the lease: wait for the version it stores, then use its access token."""
    waited = 0.0
    while waited < LEASE_WAIT:
        _sleep(LEASE_POLL)
        waited += LEASE_POLL
        row = _token_row(db, cloud_id)
        if not row:
            raise HTTPException(NOT_CONNECTED, REVOKED)
        if int(row.get("v") or 0) > version:
            usable = _usable(row)
            if usable:
                return usable
        elif not row.get("leased"):
            break   # the other replica gave up without a new token
    raise HTTPException(503, "Your Jira sign-in is being refreshed by another request; try again in a moment.")


def _refresh_legacy(db: Db, cfg: dict, cloud_id: str) -> tuple[str, float]:
    """Before V028: refresh under the in-process lock only."""
    found = db.query("SELECT REFRESH_TOKEN AS T FROM JIRA.USER_TOKEN WHERE USER_NAME = %s AND CLOUD_ID = %s",
                     (db.user, cloud_id))
    try:
        refresh = _open_token(found[0].get("t"), "refresh", db.user, cloud_id) if found else None
    except secretbox.SecretError:
        raise HTTPException(NOT_CONNECTED, RECONNECT_FORMAT) from None
    if not refresh:
        raise HTTPException(NOT_CONNECTED, "Your Jira connection could not be read; connect again.")
    try:
        tokens = refresh_tokens(_http, cfg["client_id"], _secret(), refresh)
    except JiraError as exc:
        if exc.status in (400, 401, 403):
            db.execute("DELETE FROM JIRA.USER_TOKEN WHERE USER_NAME = %s AND CLOUD_ID = %s", (db.user, cloud_id))
            raise HTTPException(NOT_CONNECTED, REVOKED) from None
        raise _jira_error(exc) from None
    if tokens.get("refresh_token"):
        _store_refresh(db, db.user, cloud_id, tokens["refresh_token"])  # rotated: the old one is now dead
    return tokens["access_token"], time.time() + int(tokens.get("expires_in") or 3600)


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
    _store_refresh(db, db.user, site["id"], tokens["refresh_token"], access_token=tokens["access_token"],
                   expires_in=int(tokens.get("expires_in") or 3600), site_url=site.get("url", ""), account_id=me.get("accountId", ""),
                   display_name=me.get("displayName", ""), scopes=json.dumps(str(tokens.get("scope") or "").split()))
    for k in [k for k in _access if k[:2] == (db.user, site["id"])]:
        _access.pop(k, None)   # the sign-in stored a new token version; the next request reads it from the row
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

def _mine_where(project: str) -> list[str]:
    """JQL conditions for "open issues assigned to me", optionally in one project."""
    return ["assignee = currentUser()", "statusCategory != Done"] + ([f"project = {project}"] if project else [])


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
        where += _mine_where(project)
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

VISIBLE_BATCH = 50


def _visible_issues(client: JiraClient, keys: list[str]) -> Optional[dict[str, dict]]:
    """{key: summary} of the issues the caller can see in Jira, by `key in (...)` searches with their own token.
    Jira refuses a whole JQL that names a key it does not know or the caller cannot see (400), so such a batch is
    asked again issue by issue. None when Jira cannot be asked (then nothing beyond keys is shown)."""
    seen: dict[str, dict] = {}
    for i in range(0, len(keys), VISIBLE_BATCH):
        batch = keys[i:i + VISIBLE_BATCH]
        try:
            for found in client.search("key in (" + ", ".join(batch) + ")", None, 100, 1):
                seen[str(found.get("key"))] = summary(found)
        except JiraError as exc:
            if exc.status != 400:
                return None
            for key in batch:
                try:
                    seen[key] = summary(client.issue(key))
                except JiraError as one:
                    if one.status not in (403, 404):
                        return None
    return seen


def visible_links(db: Db, rows: list[dict]) -> list[dict]:
    """Stored links shown within Jira's permissions. The stored SUMMARY and STATUS were read with whoever made the link,
    so a caller connected to Jira gets only the links whose issue they can see, with its live summary and status; a
    caller who is not connected gets keys and the stored status only, never a summary."""
    if not rows:
        return rows
    keys = []
    for r in rows:
        try:
            keys.append(check_key(str(r.get("issue_key") or "")))
        except ValueError:
            continue
    try:
        client, _, _ = _client(db)
    except HTTPException as exc:
        if exc.status_code not in (NOT_CONNECTED, 409):
            raise
        return [{**r, "summary": None} for r in rows]
    seen = _visible_issues(client, list(dict.fromkeys(keys)))
    if seen is None:
        return [{**r, "summary": None} for r in rows]
    out = []
    for r in rows:
        live = seen.get(str(r.get("issue_key") or ""))
        if live:
            out.append({**r, "summary": live.get("summary"), "status": live.get("status") or r.get("status")})
    return out


@router.get("/api/runs/{run_id}/jira/links")
def run_links(run_id: str, db: Db = Depends(current_db)):
    """The run's links, within the caller's Jira permissions (see visible_links)."""
    return {"links": visible_links(db, db.query(
        """SELECT L.LINK_ID, L.ISSUE_KEY, L.QA_TEST_ID, T.TITLE AS TEST_TITLE, L.SUMMARY, L.STATUS, L.LINKED_BY,
                  L.LINKED_AT::VARCHAR AS LINKED_AT
             FROM JIRA.ISSUE_LINK L LEFT JOIN CONTRACT.QA_TEST_CASE T ON T.TEST_ID = L.QA_TEST_ID
            WHERE L.RUN_ID = %s AND NOT L.IS_DELETED ORDER BY L.LINKED_AT DESC""", (run_id,)))}


class LinkIn(BaseModel):
    issue_key: str = Field(min_length=3, max_length=64)
    qa_test_id: Optional[str] = Field(default=None, max_length=64)
    remote_link: bool = False


LINK_SCOPE = ("target_table_id", "suite_id", "domain_id", "origin", "source_result_id", "issue_id", "status_category")


def _save_link(db: Db, key: str, cloud_id: str, issue: dict, run_id: Optional[str] = None, qa_test_id: Optional[str] = None,
               target_table: Optional[str] = None, **scope: Any) -> tuple[str, bool]:
    """(link id, already linked). Links a run, test, table or suite to an issue once: one MERGE inserts the link only
    when no live link with the same issue and scope exists, so two requests at once cannot both insert. Run-only links
    use the columns from V027 only; table, suite, origin and issue id columns (V028) are written when given."""
    extra = {k: scope[k] for k in LINK_SCOPE if scope.get(k)}
    table_scoped = bool(scope.get("target_table_id") or scope.get("suite_id"))

    def same(t: str) -> str:   # the same live link, on columns prefixed with t
        out = f"{t}ISSUE_KEY = %s AND NOT {t}IS_DELETED AND COALESCE({t}RUN_ID, '') = %s AND COALESCE({t}QA_TEST_ID, '') = %s"
        if table_scoped:
            out += f" AND COALESCE({t}TARGET_TABLE_ID, '') = %s AND COALESCE({t}SUITE_ID, '') = %s"
        return out + (f" AND COALESCE({t}ORIGIN, 'LINK') = %s" if scope.get("origin") else "")

    where, match = same(""), same("T.")
    params: tuple = (key, run_id or "", qa_test_id or "")
    if table_scoped:
        params += (scope.get("target_table_id") or "", scope.get("suite_id") or "")
    if scope.get("origin"):
        params += (scope["origin"],)
    link_id = str(uuid.uuid4())
    columns = ["LINK_ID", "ISSUE_KEY", "CLOUD_ID", "RUN_ID", "QA_TEST_ID", "TARGET_TABLE", "SUMMARY", "STATUS"] + [k.upper() for k in extra]
    values = ["%s", "%s", "%s", "NULLIF(%s, '')", "NULLIF(%s, '')", "NULLIF(%s, '')", "%s", "%s"] + ["%s"] * len(extra)
    row = (link_id, key, cloud_id, run_id or "", qa_test_id or "", target_table or "", (issue.get("summary") or "")[:1000],
           issue.get("status") or "") + tuple(str(v) for v in extra.values())
    # the match binds the scope values again: S only carries the row to insert
    inserted = db.execute_count(
        f"""MERGE INTO JIRA.ISSUE_LINK T
              USING (SELECT {', '.join(f'{v} AS {c}' for c, v in zip(columns, values))}) S ON {match}
            WHEN NOT MATCHED THEN INSERT ({', '.join(columns)}) VALUES ({', '.join('S.' + c for c in columns)})""",
        row + params)
    if not inserted:
        exists = db.query(f"SELECT LINK_ID FROM JIRA.ISSUE_LINK WHERE {where} ORDER BY LINKED_AT LIMIT 1", params)
        return (exists[0]["link_id"] if exists else link_id), True
    _log(db, key, "LINK", "DONE", cloud_id, run_id, {"qa_test_id": qa_test_id, **{k: v for k, v in extra.items() if k != "issue_id"}})
    return link_id, False


def _remote_link(db: Db, client: JiraClient, cloud_id: str, key: str, url: str, title: str, global_id: str,
                 run_id: Optional[str] = None) -> str:
    """A link from the issue back to the platform (Jira keeps one per globalId); a failure is reported, not raised."""
    try:
        client.remote_link(key, url, title, global_id)
    except JiraError as exc:
        _log(db, key, "REMOTE_LINK", "FAILED", cloud_id, run_id, error=exc.message)
        return f"not added: {exc.message}"
    _log(db, key, "REMOTE_LINK", "DONE", cloud_id, run_id, {"url": url})
    return "added"


def _qa_url(cfg: dict, target_table_id: str, suite_id: Optional[str] = None) -> str:
    url = f"{_web_base(cfg)}/qa?tab=results&table={quote(target_table_id, safe='')}"
    return url + (f"&suite={quote(suite_id, safe='')}" if suite_id else "")


def _link_target(db: Db, target_table_id: Optional[str] = None, suite_id: Optional[str] = None, test_id: Optional[str] = None,
                 run_id: Optional[str] = None) -> dict:
    """What a link points at, completed and checked: a test brings its table and suite, a suite its table, a table its
    domain and name, a run its name and target. 404 when something does not exist, 400 when the parts disagree."""
    out: dict = {"target_table_id": target_table_id or None, "suite_id": suite_id or None, "qa_test_id": test_id or None,
                 "run_id": run_id or None, "domain_id": None, "target_table": None, "run_name": None}
    if not any(out[k] for k in ("target_table_id", "suite_id", "qa_test_id", "run_id")):
        raise HTTPException(400, "Link the ticket to a table, suite, test or run.")

    def fill(field: str, value: Any, what: str) -> None:
        if value and out.get(field) and out[field] != value:
            raise HTTPException(400, f"that {what} belongs to another {field.replace('_id', '').replace('_', ' ')}")
        out[field] = out.get(field) or value or None

    if test_id:
        found = db.query("""SELECT TEST_ID, RUN_ID, TARGET_TABLE_ID, SUITE_ID, DOMAIN_ID FROM CONTRACT.QA_TEST_CASE
                             WHERE TEST_ID = %s AND NOT COALESCE(IS_DELETED, FALSE)""", (test_id,))
        if not found:
            raise HTTPException(404, "QA test not found")
        for field in ("run_id", "target_table_id", "suite_id", "domain_id"):
            fill(field, found[0].get(field), "test")
    if out["suite_id"]:
        found = db.query("""SELECT TARGET_TABLE_ID, DOMAIN_ID FROM CONTRACT.QA_TEST_SUITE
                             WHERE SUITE_ID = %s AND NOT COALESCE(IS_DELETED, FALSE)""", (out["suite_id"],))
        if not found:
            raise HTTPException(404, "suite not found")
        fill("target_table_id", found[0].get("target_table_id"), "suite")
        fill("domain_id", found[0].get("domain_id"), "suite")
    if out["target_table_id"]:
        found = db.query("""SELECT DOMAIN_ID, TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                             WHERE TARGET_TABLE_ID = %s""", (out["target_table_id"],))
        if not found:
            raise HTTPException(404, "target table not found")
        out["domain_id"] = out["domain_id"] or found[0].get("domain_id")
        out["target_table"] = ".".join(str(found[0].get(k) or "") for k in ("target_database", "target_schema", "target_table"))
    if out["run_id"]:
        found = db.query("SELECT RUN_NAME, TARGET_MODEL FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (out["run_id"],))
        if not found:
            raise HTTPException(404, "run not found")
        out["run_name"] = found[0].get("run_name")
        out["target_table"] = out["target_table"] or found[0].get("target_model")
    return out


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
    _, already = _save_link(db, key, conn["cloud_id"], found, run_id=run_id, qa_test_id=body.qa_test_id,
                            target_table=run[0].get("target_model"))
    remote = None
    if body.remote_link:
        remote = _remote_link(db, client, conn["cloud_id"], key, f"{_web_base(cfg)}/runs/{run_id}/qa",
                              f"QA run: {run[0]['run_name']}", f"agentic-pipeline:run:{run_id}", run_id)
    return {"linked": key, "already": already, "remote_link": remote, "issue": found}


@router.delete("/api/runs/{run_id}/jira/links/{link_id}")
def unlink(run_id: str, link_id: str, db: Db = Depends(current_db)):
    found = db.query("SELECT ISSUE_KEY, CLOUD_ID FROM JIRA.ISSUE_LINK WHERE LINK_ID = %s AND RUN_ID = %s AND NOT IS_DELETED", (link_id, run_id))
    if not found:
        raise HTTPException(404, "link not found")
    db.execute("UPDATE JIRA.ISSUE_LINK SET IS_DELETED = TRUE WHERE LINK_ID = %s", (link_id,))
    _log(db, found[0]["issue_key"], "UNLINK", "DONE", found[0]["cloud_id"], run_id)
    return {"unlinked": link_id}


# ---------------------------------------------------------------- AI triage, results report, comment, transition

def _issue_payload(client: JiraClient, conn: dict, key: str) -> tuple[dict, dict]:
    """(issue detail, what triage reads): the issue text, the first two small text attachments, and the attachments
    left out (binary, larger than 64 KB, or over the limit) so the prompt can say so."""
    try:
        info = detail(client.issue(_key(key)), conn.get("site_url") or "")
        texts, skipped = [], []
        for a in info["attachments"]:
            small = (a.get("size") or 0) <= TRIAGE_ATTACHMENT_BYTES
            reason = ("binary" if not a["previewable"] else "larger than 64 KB" if not small else
                      "over the attachment limit" if len(texts) >= 2 else "")
            if not reason:
                try:
                    texts.append({"name": a["name"], "text": _attachment_text(client, a["id"], 8000)[0]})
                    continue
                except JiraError:
                    reason = "could not be downloaded"
            skipped.append({"name": a["name"], "mime": a.get("mime"), "size": a.get("size"), "reason": reason})
    except JiraError as exc:
        raise _jira_error(exc) from None
    payload = {k: info.get(k) for k in ("key", "summary", "description", "environment", "comments", "type", "priority", "status")}
    payload["attachments_text"], payload["attachments_skipped"] = texts, skipped
    return info, payload


@router.post("/api/runs/{run_id}/jira/{key}/triage")
def triage(run_id: str, key: str, db: Db = Depends(current_db)):
    """Diagnosis of the reported bug against this run, with guarded, compiled tests to reproduce it (not saved)."""
    from services.jira.triage import triage_issue

    client, conn, _ = _client(db)
    info, payload = _issue_payload(client, conn, key)
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


def _set_link_status(db: Db, key: str, status: Optional[str], category: Optional[str]) -> None:
    """Keep the stored status and its category together on every link of the issue (create_bug reads the category)."""
    db.execute("""UPDATE JIRA.ISSUE_LINK SET STATUS = %s, STATUS_CATEGORY = COALESCE(NULLIF(%s, ''), STATUS_CATEGORY)
                   WHERE ISSUE_KEY = %s AND NOT IS_DELETED""", (status or "", category or "", key))


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
    _set_link_status(db, key, to, allowed[body.transition_id].get("category"))
    _log(db, key, "TRANSITION", "DONE", conn["cloud_id"], body.run_id, {"to": to, "transition": allowed[body.transition_id].get("name")})
    return {"status": to}


# ---------------------------------------------------------------- inbox: JQL search, saved filters, boards and sprints

def _linked_counts(db: Db, keys: list[str]) -> dict[str, int]:
    """ISSUE_LINK rows per issue key."""
    keys = [k for k in dict.fromkeys(keys) if k][:100]
    if not keys:
        return {}
    found = db.query(f"""SELECT ISSUE_KEY, COUNT(*) AS N FROM JIRA.ISSUE_LINK
                          WHERE ISSUE_KEY IN ({', '.join(['%s'] * len(keys))}) AND NOT IS_DELETED GROUP BY ISSUE_KEY""", tuple(keys))
    return {r["issue_key"]: int(r["n"]) for r in found}


def _page(db: Db, conn: dict, page: dict) -> dict:
    site = conn.get("site_url") or ""
    shaped = [summary(i, site) for i in page.get("issues") or []]
    linked = _linked_counts(db, [i["key"] for i in shaped])
    return {"issues": [{**i, "linked": linked.get(i["key"], 0)} for i in shaped], "next": page.get("next")}


def _next(token: Optional[str]) -> Optional[str]:
    token = (token or "").strip()
    if len(token) > 2000:
        raise HTTPException(400, "invalid page token")
    return token or None


def _filter(db: Db, filter_id: str) -> dict:
    """A saved filter the user owns or that is shared; 404 otherwise."""
    found = db.query("""SELECT FILTER_ID, USER_NAME, NAME, JQL, SHARED FROM JIRA.SAVED_FILTER
                         WHERE FILTER_ID = %s AND (USER_NAME = %s OR SHARED)""", (filter_id, db.user))
    if not found:
        raise HTTPException(404, "filter not found")
    return _filter_out(db, found[0])


def _filter_out(db: Db, r: dict) -> dict:
    return {"filter_id": r["filter_id"], "name": r["name"], "jql": r["jql"], "shared": bool(r.get("shared")),
            "owner": r.get("user_name"), "mine": r.get("user_name") == db.user}


@router.get("/api/jira/search")
def search(jql: str = Query(default="", max_length=4000), next_token: Optional[str] = Query(default=None, alias="next"),
           max_results: int = Query(default=50, alias="max"), filter_id: Optional[str] = None, db: Db = Depends(current_db)):
    """One page of issues for any JQL (or a saved filter); no JQL means my open issues. Pass `next` for the next page."""
    client, conn, cfg = _client(db)
    if filter_id:
        jql = _filter(db, filter_id)["jql"]
    jql = jql.strip()
    if not jql:
        project = (cfg.get("default_project") or "").strip().upper()
        jql = " AND ".join(_mine_where(project if re.fullmatch(r"[A-Z][A-Z0-9_]{0,30}", project) else "")) + " ORDER BY updated DESC"
    try:
        page = client.search_page(jql, _next(next_token), max(1, min(int(max_results), 100)))
    except JiraError as exc:
        raise _jira_error(exc) from None
    return {**_page(db, conn, page), "jql": jql}


class JqlIn(BaseModel):
    jql: str = Field(min_length=1, max_length=4000)


@router.post("/api/jira/jql/validate")
def validate_jql(body: JqlIn, db: Db = Depends(current_db)):
    """Jira's strict JQL validation: {"ok", "errors"}."""
    client, _, _ = _client(db)
    try:
        errors = client.parse_jql(body.jql.strip())
    except JiraError as exc:
        if exc.status == 400:
            return {"ok": False, "errors": [exc.message]}
        raise _jira_error(exc) from None
    return {"ok": not errors, "errors": errors}


@router.get("/api/jira/filters")
def filters(db: Db = Depends(current_db)):
    """My saved filters and the ones others shared."""
    try:
        found = db.query("""SELECT FILTER_ID, USER_NAME, NAME, JQL, SHARED FROM JIRA.SAVED_FILTER
                             WHERE USER_NAME = %s OR SHARED ORDER BY IFF(USER_NAME = %s, 0, 1), NAME""", (db.user, db.user))
    except Exception as exc:
        if "does not exist" in str(exc):
            return {"filters": []}   # V029 not applied yet
        raise _snowflake_error(exc) from exc
    return {"filters": [_filter_out(db, r) for r in found]}


class FilterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    jql: str = Field(min_length=1, max_length=4000)
    shared: bool = False


@router.post("/api/jira/filters")
def filter_create(body: FilterIn, db: Db = Depends(current_db)):
    """Save a JQL filter (checked by Jira first); names are unique per user."""
    name, jql = body.name.strip(), body.jql.strip()
    if not name or not jql:
        raise HTTPException(400, "A filter needs a name and JQL.")
    client, _, _ = _client(db)
    try:
        errors = client.parse_jql(jql)
    except JiraError as exc:
        raise _jira_error(exc) from None
    if errors:
        raise HTTPException(400, "Jira: " + "; ".join(errors))
    if db.query("SELECT 1 FROM JIRA.SAVED_FILTER WHERE USER_NAME = %s AND UPPER(NAME) = UPPER(%s)", (db.user, name)):
        raise HTTPException(409, f"You already have a filter named {name}.")
    filter_id = str(uuid.uuid4())
    db.execute("INSERT INTO JIRA.SAVED_FILTER (FILTER_ID, USER_NAME, NAME, JQL, SHARED) VALUES (%s, %s, %s, %s, %s)",
               (filter_id, db.user, name, jql, bool(body.shared)))
    return {"filter_id": filter_id, "name": name, "jql": jql, "shared": bool(body.shared), "owner": db.user, "mine": True}


@router.delete("/api/jira/filters/{filter_id}")
def filter_delete(filter_id: str, db: Db = Depends(current_db)):
    """Only the owner deletes a filter; anyone else gets 404, as if it did not exist."""
    if not db.execute_count("DELETE FROM JIRA.SAVED_FILTER WHERE FILTER_ID = %s AND USER_NAME = %s", (filter_id, db.user)):
        raise HTTPException(404, "filter not found")
    return {"deleted": filter_id}


def _agile(conn: dict, call: Callable[[], Any]) -> Any:
    """An Agile API call. Connections made before the Agile scopes were added must sign in again: known from the
    stored scopes, or, when none were stored, from Jira refusing the call."""
    scopes = set(conn.get("scopes") or [])
    if scopes and not set(AGILE_SCOPES) <= scopes:
        raise HTTPException(NOT_CONNECTED, RECONNECT_AGILE)
    try:
        return call()
    except JiraError as exc:
        if exc.status in (401, 403) and not scopes:
            raise HTTPException(NOT_CONNECTED, RECONNECT_AGILE) from None
        raise _jira_error(exc) from None


@router.get("/api/jira/boards")
def boards(q: str = Query(default="", max_length=100), start: int = 0, db: Db = Depends(current_db)):
    client, conn, _ = _client(db)
    return {"boards": _agile(conn, lambda: client.boards(q, max(0, int(start))))}


@router.get("/api/jira/boards/{board_id}/sprints")
def sprints(board_id: int, state: str = "active,future", db: Db = Depends(current_db)):
    client, conn, _ = _client(db)
    states = [s.strip().lower() for s in state.split(",") if s.strip()]
    if any(s not in ("active", "future", "closed") for s in states):
        raise HTTPException(400, "state: active, future or closed, comma separated")
    return {"sprints": _agile(conn, lambda: client.sprints(board_id, states))}


@router.get("/api/jira/sprints/{sprint_id}/issues")
def sprint_issues(sprint_id: int, next_token: Optional[str] = Query(default=None, alias="next"),
                  max_results: int = Query(default=50, alias="max"), db: Db = Depends(current_db)):
    client, conn, _ = _client(db)
    page = _agile(conn, lambda: client.sprint_issues(sprint_id, _next(next_token), max(1, min(int(max_results), 100))))
    return _page(db, conn, page)


@router.get("/api/jira/projects/{project_key}/issue-types")
def issue_types(project_key: str, db: Db = Depends(current_db)):
    client, _, _ = _client(db)
    try:
        return {"types": client.issue_types(project_key)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except JiraError as exc:
        raise _jira_error(exc) from None


# ---------------------------------------------------------------- bugs from failing QA tests

def bug_label(test_id: str, target_table_id: str) -> str:
    """The label that marks the one open bug for a test on a table, whoever raised it."""
    return "gdp-qa-" + hashlib.sha1(f"{test_id}:{target_table_id}".encode()).hexdigest()[:8]


class BugIn(BaseModel):
    test_id: str = Field(min_length=1, max_length=64)
    target_table_id: str = Field(min_length=1, max_length=36)
    qa_run_id: Optional[str] = Field(default=None, max_length=36)
    project_key: Optional[str] = Field(default=None, max_length=40)
    issue_type_id: Optional[str] = Field(default=None, max_length=20)
    summary: Optional[str] = Field(default=None, max_length=255)
    idempotency_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")


def _browse(conn: dict, key: str) -> str:
    return f"{(conn.get('site_url') or '').rstrip('/')}/browse/{key}"


def _latest_result(db: Db, target_table_id: str, test_id: str) -> Optional[dict]:
    """The test's latest result on the table, without sample rows."""
    _, found = importlib.import_module("services.qa.run").latest_table(db.query, target_table_id)
    for r in found or []:
        if r.get("test_id") == test_id:
            return {k: v for k, v in r.items() if k not in ("sample_rows", "sample")}
    return None


@router.post("/api/jira/bugs")
def create_bug(body: BugIn, db: Db = Depends(current_db)):
    """Raise a Jira bug from a QA test's latest result on a table, once. An open bug for the same test and table is
    returned instead: one linked here, one found by its label in Jira, or the result of an earlier request with the
    same idempotency key. The description has counts only, never sample rows."""
    client, conn, cfg = _client(db)
    test_id, table_id = body.test_id, body.target_table_id
    answer = lambda key, created: {"key": key, "url": _browse(conn, key), "created": created, "existing": not created}  # noqa: E731
    skipped: set[str] = set()   # linked bugs this caller cannot see: skipped for this request only

    # 1. an open bug already linked to this test and table
    open_bug = _open_linked_bug(db, client, cfg, test_id, table_id, skipped)
    if open_bug:
        return answer(open_bug, False)
    # 2. a repeated request (same idempotency key) gets the first answer
    earlier = db.query("""SELECT ISSUE_KEY, DETAIL FROM JIRA.ACTION_LOG WHERE IDEMPOTENCY_KEY = %s AND ACTION = 'BUG' AND STATUS = 'DONE'
                           ORDER BY ACTED_AT DESC LIMIT 1""", (body.idempotency_key,))
    if earlier:
        stored = _json(earlier[0].get("detail")) or {}
        if stored.get("key"):
            return {k: stored.get(k) for k in ("key", "url", "created", "existing")}
        return answer(earlier[0]["issue_key"], False)

    table = _link_target(db, target_table_id=table_id)
    test = db.query("""SELECT TEST_ID, TITLE, SEVERITY, EXPECTED, OBJECTIVE, TARGET_TABLE_ID, SUITE_ID FROM CONTRACT.QA_TEST_CASE
                        WHERE TEST_ID = %s AND NOT COALESCE(IS_DELETED, FALSE)""", (test_id,))
    if test and test[0].get("target_table_id") and test[0]["target_table_id"] != table_id:
        raise HTTPException(400, "that test belongs to another table")
    result = _latest_result(db, table_id, test_id)
    if not result:
        if not test:
            raise HTTPException(404, "QA test not found")
        raise HTTPException(409, "This test has no result on this table yet; run it first.")
    if not test:
        # a generated test is not stored as a test case: its latest result carries what the bug needs
        test = [{k: result.get(k) for k in ("title", "severity", "expected", "objective", "suite_id")}]
    label = bug_label(test_id, table_id)
    link_fields = dict(target_table_id=table_id, suite_id=test[0].get("suite_id") or result.get("suite_id"),
                       domain_id=table["domain_id"], origin="BUG")

    def done(key: str, created: bool, issue: dict) -> dict:
        source = _result_id(db, result, test_id)
        _save_link(db, key, conn["cloud_id"], issue, qa_test_id=test_id, target_table=table["target_table"],
                   source_result_id=source, issue_id=issue.get("id"), status_category=issue.get("status_category"), **link_fields)
        out = answer(key, created)
        _log(db, key, "BUG", "DONE", conn["cloud_id"], None, {**out, "test_id": test_id, "target_table_id": table_id,
                                                               "qa_run_id": body.qa_run_id or result.get("qa_run_id")},
             idempotency_key=body.idempotency_key)
        return out

    # claim the label before searching and creating, so two requests at once cannot both create a bug
    claim = _claim_bug(db, label, test_id, table_id)
    if not claim:
        pending = _bug_claim(db, label)
        if pending and pending.get("key"):
            return answer(pending["key"], False)
        raise HTTPException(409, "A bug is being created for this test; try again in a moment.")
    try:
        # a request that finished just before the claim has linked its bug by now
        open_bug = _open_linked_bug(db, client, cfg, test_id, table_id, skipped)
        if open_bug:
            _finish_bug_claim(db, claim, "DONE", open_bug)
            return answer(open_bug, False)
        out = _find_or_create_bug(db, client, cfg, conn, body, test, result, table, label, link_fields, done)
    except BaseException:
        _finish_bug_claim(db, claim, "RELEASED")
        raise
    _finish_bug_claim(db, claim, "DONE", out["key"])
    return out


def _find_or_create_bug(db: Db, client: JiraClient, cfg: dict, conn: dict, body: BugIn, test: list, result: dict, table: dict,
                        label: str, link_fields: dict, done: Callable[[str, bool, dict], dict]) -> dict:
    """Steps 3 and 4 of create_bug, under the label's claim: an open bug with the label in Jira, else a new one."""
    test_id, table_id = body.test_id, body.target_table_id
    # 3. an open bug in Jira with this test's label (raised by someone else, or linked before the link was recorded)
    try:
        found = client.search_page(f"labels = {jql_string(label)} AND statusCategory != Done ORDER BY created ASC", None, 1)["issues"]
    except JiraError as exc:
        raise _jira_error(exc) from None
    if found:
        return done(found[0]["key"], False, summary(found[0]))

    # 4. a new bug
    try:
        project = check_project(body.project_key or cfg.get("default_project") or "")
    except ValueError:
        raise HTTPException(400, "Pick a Jira project: no default project is set in Admin, Integrations, Jira.") from None
    title = test[0].get("title") or result.get("title") or test_id
    url = _qa_url(cfg, table_id, link_fields["suite_id"])
    from services.jira.triage import bug_markdown

    description = adf.from_markdown(bug_markdown({**test[0], "test_id": test_id}, result, table["target_table"], url))
    try:
        type_id = body.issue_type_id or next((t["id"] for t in client.issue_types(project)
                                              if str(t.get("name") or "").lower() == "bug" and not t.get("subtask")), None)
        if not type_id:
            raise HTTPException(400, f"Project {project} has no Bug issue type; pick one.")
        created = client.create_issue(project, type_id, body.summary or f"QA test failed: {title} on {table['target_table']}",
                                      description, [label, "gdp-qa"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except JiraError as exc:
        _log(db, "-", "BUG", "FAILED", conn["cloud_id"], None, {"test_id": test_id, "target_table_id": table_id}, exc.message)
        raise _jira_error(exc) from None
    key = check_key(created.get("key") or "")
    _remote_link(db, client, conn["cloud_id"], key, url, f"QA test: {title}"[:255], f"gdp:qa:test:{test_id}")
    return done(key, True, {"id": created.get("id"), "summary": body.summary or f"QA test failed: {title}", "status": None})


def _gone_for_everyone(cfg: dict, key: str) -> bool:
    """True only when the issue is confirmed deleted: the Jira bot (an automation account, when configured) cannot find
    it either. One user's 404 may only mean that user cannot see it."""
    try:
        bot = importlib.import_module("services.ops.tickets").bot_client(cfg, _http)
    except Exception:
        return False
    if bot is None:
        return False
    try:
        bot.issue(key)
    except JiraError as exc:
        return exc.status == 404
    except Exception:
        return False
    return False


def _open_linked_bug(db: Db, client: JiraClient, cfg: dict, test_id: str, table_id: str, skipped: set[str]) -> Optional[str]:
    """The key of an open bug already linked to this test and table. The stored status category is only a hint (set when
    the link is made, may be stale or empty): the bug's live status in Jira decides, and a closed one is skipped. A 404
    is skipped for this request without a write; the link is marked DELETED only when the bot confirms it is gone."""
    linked = db.query("""SELECT ISSUE_KEY FROM JIRA.ISSUE_LINK
                          WHERE ORIGIN = 'BUG' AND QA_TEST_ID = %s AND TARGET_TABLE_ID = %s AND NOT IS_DELETED
                            AND LOWER(COALESCE(STATUS_CATEGORY, '')) <> 'done' AND COALESCE(ISSUE_STATE, 'OK') <> 'DELETED'
                          ORDER BY LINKED_AT DESC LIMIT 5""", (test_id, table_id))
    for key in dict.fromkeys(r["issue_key"] for r in linked):
        if key in skipped:
            continue
        try:
            live = summary(client.issue(key))
        except JiraError as exc:
            if exc.status == 404:   # deleted in Jira, or not visible to this user: not this user's call to make
                skipped.add(key)
                if _gone_for_everyone(cfg, key):
                    db.execute("UPDATE JIRA.ISSUE_LINK SET ISSUE_STATE = 'DELETED' WHERE ISSUE_KEY = %s AND NOT IS_DELETED", (key,))
                continue
            raise _jira_error(exc) from None
        _set_link_status(db, key, live.get("status"), live.get("status_category"))
        if str(live.get("status_category") or "").lower() != "done":
            return key
    return None


BUG_CLAIM_SECONDS = 120   # a claim older than this is from a request that died; the next request takes it over


def _claim_bug(db: Db, label: str, test_id: str, table_id: str) -> Optional[str]:
    """Claim the bug label (a PENDING BUG_CLAIM row in JIRA.ACTION_LOG keyed by the label). MERGE serialises on the
    table, so exactly one request inserts the row (or takes over a stale one); the claim id, or None when another
    request holds it."""
    claim = str(uuid.uuid4())
    taken = db.execute_count(
        f"""MERGE INTO JIRA.ACTION_LOG T USING (SELECT %s AS ISSUE_KEY) S
              ON T.ISSUE_KEY = S.ISSUE_KEY AND T.ACTION = 'BUG_CLAIM' AND T.STATUS = 'PENDING'
            WHEN MATCHED AND T.ACTED_AT < DATEADD(second, -{BUG_CLAIM_SECONDS}, CURRENT_TIMESTAMP()) THEN UPDATE
                 SET ACTION_ID = %s, ACTED_BY = CURRENT_USER(), ACTED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (ACTION_ID, ISSUE_KEY, ACTION, DETAIL, STATUS)
                 VALUES (%s, S.ISSUE_KEY, 'BUG_CLAIM', PARSE_JSON(%s), 'PENDING')""",
        (label, claim, claim, json.dumps({"test_id": test_id, "target_table_id": table_id})))
    return claim if taken == 1 else None


def _bug_claim(db: Db, label: str) -> Optional[dict]:
    """The latest claim on a label: {"status", "key"} (key once its bug is known)."""
    found = db.query("""SELECT STATUS, DETAIL FROM JIRA.ACTION_LOG WHERE ISSUE_KEY = %s AND ACTION = 'BUG_CLAIM'
                         ORDER BY ACTED_AT DESC LIMIT 1""", (label,))
    if not found:
        return None
    return {"status": found[0].get("status"), "key": (_json(found[0].get("detail")) or {}).get("key")}


def _finish_bug_claim(db: Db, claim: str, status: str, key: Optional[str] = None) -> None:
    """DONE with the bug's key, or RELEASED after a failure so the next request can try again."""
    try:
        db.execute("""UPDATE JIRA.ACTION_LOG SET STATUS = %s,
                             DETAIL = IFF(%s = '', DETAIL, OBJECT_INSERT(COALESCE(DETAIL, OBJECT_CONSTRUCT())::OBJECT, 'key', %s, TRUE))
                       WHERE ACTION_ID = %s AND ACTION = 'BUG_CLAIM'""", (status, key or "", key or "", claim))
    except Exception:
        pass   # a claim left PENDING runs out after BUG_CLAIM_SECONDS


def _result_id(db: Db, result: dict, test_id: str) -> Optional[str]:
    if not result.get("qa_run_id"):
        return None
    try:
        found = db.query("""SELECT RESULT_ID FROM QUALITY.QA_RESULT WHERE QA_RUN_ID = %s AND TEST_ID = %s
                             ORDER BY CREATED_AT DESC LIMIT 1""", (result["qa_run_id"], test_id))
    except Exception:
        return None
    return found[0]["result_id"] if found else None


# ---------------------------------------------------------------- bulk actions

class BulkLink(BaseModel):
    target_table_id: str = Field(min_length=1, max_length=36)
    suite_id: Optional[str] = Field(default=None, max_length=36)
    test_id: Optional[str] = Field(default=None, max_length=64)


class BulkIn(BaseModel):
    action: Literal["link", "comment", "transition"]
    keys: list[str] = Field(min_length=1, max_length=BULK_MAX)
    comment: Optional[str] = Field(default=None, max_length=30000)
    transition_name: Optional[str] = Field(default=None, max_length=100)
    link: Optional[BulkLink] = None
    # one per bulk submit: a retried request skips the issues already commented under it
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")


@router.post("/api/jira/bulk")
def bulk(body: BulkIn, db: Db = Depends(current_db)):
    """Link, comment on or move up to 50 issues, one after another. Each issue reports its own outcome; once Jira
    rate limits, the remaining issues are skipped rather than hammered."""
    if body.action == "comment" and not (body.comment or "").strip():
        raise HTTPException(400, "comment is required")
    if body.action == "transition" and not (body.transition_name or "").strip():
        raise HTTPException(400, "transition_name is required")
    if body.action == "link" and not body.link:
        raise HTTPException(400, "link is required")
    client, conn, cfg = _client(db)
    target = _link_target(db, body.link.target_table_id, body.link.suite_id, body.link.test_id) if body.link and body.action == "link" else None
    document = adf.from_markdown(body.comment or "") if body.action == "comment" else None
    wanted = (body.transition_name or "").strip().lower()
    results: list[dict] = []
    limited = False
    posted_before: set[str] = set()
    if body.action == "comment" and body.idempotency_key:
        posted_before = {r["issue_key"] for r in db.query(
            """SELECT DISTINCT ISSUE_KEY FROM JIRA.ACTION_LOG
                WHERE IDEMPOTENCY_KEY = %s AND ACTION = 'COMMENT' AND STATUS = 'DONE'""", (body.idempotency_key,))}
    for raw in body.keys:
        if limited:
            results.append({"key": raw, "ok": False, "skipped": True, "error": "rate limited"})
            continue
        try:
            key = check_key(raw)
        except ValueError as exc:
            results.append({"key": raw, "ok": False, "error": str(exc)})
            continue
        try:
            if body.action == "comment":
                if key in posted_before:
                    results.append({"key": key, "ok": True, "already": True})
                    continue
                posted = client.add_comment(key, document)
                _log(db, key, "COMMENT", "DONE", conn["cloud_id"], None, {"comment_id": (posted or {}).get("id"), "bulk": True,
                                                                         "preview": adf.plain(body.comment, 300)},
                     idempotency_key=body.idempotency_key)
            elif body.action == "transition":
                options = client.transitions(key)
                pick = next((t for t in options if str(t.get("name") or "").lower() == wanted), None) \
                    or next((t for t in options if str(t.get("to") or "").lower() == wanted), None)
                if not pick:
                    results.append({"key": key, "ok": False, "error": f"'{body.transition_name}' is not available for this issue"})
                    continue
                client.transition(key, pick["id"])
                _set_link_status(db, key, pick.get("to"), pick.get("category"))
                _log(db, key, "TRANSITION", "DONE", conn["cloud_id"], None, {"to": pick.get("to"), "transition": pick.get("name"), "bulk": True})
            else:
                issue = summary(client.issue(key), conn.get("site_url") or "")
                _save_link(db, key, conn["cloud_id"], issue, qa_test_id=target["qa_test_id"], target_table=target["target_table"],
                           target_table_id=target["target_table_id"], suite_id=target["suite_id"], domain_id=target["domain_id"],
                           issue_id=issue.get("id"), status_category=issue.get("status_category"))
            results.append({"key": key, "ok": True})
        except JiraError as exc:
            if exc.status == 429:
                limited = True
                wait = f" (retry after {exc.retry_after} seconds)" if exc.retry_after is not None else ""
                results.append({"key": key, "ok": False, "error": f"rate limited{wait}"})
            else:
                results.append({"key": key, "ok": False, "error": _jira_error(exc).detail})
            _log(db, key, body.action.upper(), "FAILED", conn["cloud_id"], None, {"bulk": True}, exc.message)
        except HTTPException:   # e.g. 428: Jira no longer accepts the sign-in, so the rest would fail the same way
            raise
        except Exception as exc:   # a failed database write fails this issue only
            results.append({"key": key, "ok": False, "error": str(exc)[:300]})
    return {"results": results}
