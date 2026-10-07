"""Snowflake sessions for the backend.

AIP_AUTH=dev  one shared session from the named connection in ~/.snowflake/connections.toml
              (browser SSO on this account). Every action is attributed to that user.
AIP_AUTH=pat  each user signs in with their own programmatic access token; procedures see
              that user as CURRENT_USER(). Tokens are held in memory only, never persisted.

Windows Credential Manager often rejects Snowflake's OAuth token write (CredWrite 1783).
That must not abort a successful sign-in, and it must not retry SSO on every HTTP request.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import snowflake.connector

AUTH_MODE = os.environ.get("AIP_AUTH", "dev")
DATABASE = os.environ.get("AIP_DATABASE", "DEV_AI_PLATFORM")
CONNECTION = os.environ.get("AIP_SNOWFLAKE_CONNECTION", "anblicksorg-anblicksorg_aws")
WAREHOUSE = os.environ.get("AIP_WAREHOUSE", "DBT_WH")
ACCOUNT = os.environ.get("AIP_ACCOUNT")
ROLE = os.environ.get("AIP_ROLE")
SESSION_TTL_SECONDS = 8 * 3600
_AUTH_COOLDOWN = 20.0

assert AUTH_MODE in ("dev", "pat"), f"AIP_AUTH must be dev or pat, got {AUTH_MODE}"
assert AUTH_MODE != "pat" or ACCOUNT, "AIP_ACCOUNT is required when AIP_AUTH=pat"


class SnowflakeSessionError(RuntimeError):
    """Sign-in failed or is cooling down. The API turns this into HTTP 503."""


def _patch_keyring() -> None:
    """Keep tokens in process memory when Windows CredWrite rejects them."""
    try:
        import keyring
    except Exception:
        return
    memory: dict[tuple[str, str], str] = {}
    orig_set = keyring.set_password
    orig_get = keyring.get_password
    orig_delete = keyring.delete_password

    def set_password(service, username, password):
        memory[(service, username)] = password
        try:
            orig_set(service, username, password)
        except Exception:
            pass

    def get_password(service, username):
        cached = memory.get((service, username))
        if cached:
            return cached
        try:
            return orig_get(service, username)
        except Exception:
            return None

    def delete_password(service, username):
        memory.pop((service, username), None)
        try:
            orig_delete(service, username)
        except Exception:
            pass

    keyring.set_password = set_password
    keyring.get_password = get_password
    keyring.delete_password = delete_password


_patch_keyring()


@dataclass
class Db:
    conn: Any
    user: str
    role: str = ""
    token: Optional[str] = None
    expires_at: float = float("inf")

    @property
    def host(self) -> str:
        return self.conn.host

    def execute(self, sql: str, params: tuple = ()) -> None:
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
        finally:
            cur.close()

    def call(self, sql: str, params: tuple = ()) -> Any:
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            value = row[0]
            return json.loads(value) if isinstance(value, str) else value
        finally:
            cur.close()

    def query_with_id(self, sql: str, params: tuple = ()) -> tuple[list[dict], Optional[str]]:
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            columns = [d[0].lower() for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()], cur.sfqid
        finally:
            cur.close()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            columns = [d[0].lower() for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        finally:
            cur.close()


_lock = threading.Lock()
_dev: Optional[Db] = None
_sessions: dict[str, Db] = {}
_last_fail = ""
_last_fail_at = 0.0


def _identity(conn) -> tuple[str, str]:
    cur = conn.cursor()
    try:
        user, role = cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE()").fetchone()
        return user, role
    finally:
        cur.close()


def _dead(db: Optional[Db]) -> bool:
    if db is None:
        return True
    try:
        return bool(db.conn.is_closed())
    except Exception:
        return True


def _friendly(exc: BaseException) -> str:
    text = str(exc)
    if "CredWrite" in text or "stub received bad data" in text.lower():
        return (
            "Snowflake signed in, but Windows Credential Manager refused to store the token "
            "(CredWrite 1783). The API now keeps the token in memory — refresh once."
        )
    if "250001" in text or "Authentication" in text:
        return "Snowflake authentication failed. Complete the browser SSO prompt, then refresh once."
    return text[:400]


def _open_dev():
    kwargs: dict[str, Any] = {
        "connection_name": CONNECTION,
        "warehouse": WAREHOUSE,
        "database": DATABASE,
        "client_session_keep_alive": True,
        "client_store_temporary_credential": True,
        "login_timeout": 90,
    }
    if ROLE:
        kwargs["role"] = ROLE
    return snowflake.connector.connect(**kwargs)


def dev_db() -> Db:
    global _dev, _last_fail, _last_fail_at
    with _lock:
        if not _dead(_dev):
            return _dev
        if _last_fail and time.time() - _last_fail_at < _AUTH_COOLDOWN:
            raise SnowflakeSessionError(
                "Snowflake sign-in is cooling down after a failed attempt. "
                "Wait a few seconds and refresh once — do not keep reloading."
            )
        try:
            conn = _open_dev()
            user, role = _identity(conn)
            _dev = Db(conn, user, role)
            _last_fail = ""
            return _dev
        except SnowflakeSessionError:
            raise
        except Exception as exc:
            _last_fail = _friendly(exc)
            _last_fail_at = time.time()
            raise SnowflakeSessionError(_last_fail) from exc


def open_pat_session(user: str, token: str) -> tuple[str, Db]:
    conn = snowflake.connector.connect(
        account=ACCOUNT, user=user, authenticator="PROGRAMMATIC_ACCESS_TOKEN", token=token,
        warehouse=WAREHOUSE, database=DATABASE, **({"role": ROLE} if ROLE else {}),
    )
    user, role = _identity(conn)
    db = Db(conn, user, role, token, time.time() + SESSION_TTL_SECONDS)
    session_id = secrets.token_urlsafe(32)
    with _lock:
        _sessions[session_id] = db
    return session_id, db


def lookup_session(session_id: Optional[str]) -> Optional[Db]:
    if not session_id:
        return None
    with _lock:
        db = _sessions.get(session_id)
        if db and (db.expires_at < time.time() or _dead(db)):
            _sessions.pop(session_id, None)
            db = None
    return db


def close_session(session_id: Optional[str]) -> None:
    with _lock:
        db = _sessions.pop(session_id or "", None)
    if db:
        db.conn.close()


_ROLE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,254}$")


def _quote_role(name: str) -> str:
    value = (name or "").strip()
    if _ROLE_IDENT.match(value):
        return value
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def list_grantable_roles(db: Db) -> list[str]:
    roles: set[str] = {db.role} if db.role else set()
    try:
        for row in db.query("SELECT ROLE_NAME FROM INFORMATION_SCHEMA.APPLICABLE_ROLES ORDER BY ROLE_NAME"):
            if row.get("role_name"):
                roles.add(str(row["role_name"]))
    except Exception:
        pass
    if len(roles) > 1 or (roles and db.role not in roles):
        return sorted(roles, key=str.upper)
    try:
        db.execute(f'SHOW GRANTS TO USER "{db.user}"')
        for row in db.query(
            """
            SELECT "name" AS name, "privilege" AS privilege, "granted_on" AS granted_on
              FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))
            """
        ):
            if (row.get("granted_on") or "").upper() == "ROLE" and (row.get("privilege") or "").upper() == "USAGE":
                if row.get("name"):
                    roles.add(str(row["name"]))
    except Exception:
        pass
    return sorted(roles, key=str.upper) if roles else ([db.role] if db.role else [])


def apply_work_role(db: Db, role: Optional[str]) -> None:
    picked = (role or "").strip()
    if not picked or picked == db.role:
        return
    allowed = set(list_grantable_roles(db))
    if allowed and picked not in allowed:
        raise SnowflakeSessionError(f"Role {picked} is not available for {db.user}")
    try:
        db.execute(f"USE ROLE {_quote_role(picked)}")
        db.role = _identity(db.conn)[1]
    except SnowflakeSessionError:
        raise
    except Exception as exc:
        raise SnowflakeSessionError(f"Could not activate role {picked}: {_friendly(exc)}") from exc
