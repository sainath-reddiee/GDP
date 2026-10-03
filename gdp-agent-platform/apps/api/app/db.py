"""Snowflake sessions for the backend.

AIP_AUTH=dev  one shared session from the named connection in ~/.snowflake/connections.toml
              (browser SSO on this account). Every action is attributed to that user.
AIP_AUTH=pat  each user signs in with their own programmatic access token; procedures see
              that user as CURRENT_USER(). Tokens are held in memory only, never persisted.
"""

from __future__ import annotations

import json
import os
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

assert AUTH_MODE in ("dev", "pat"), f"AIP_AUTH must be dev or pat, got {AUTH_MODE}"
assert AUTH_MODE != "pat" or ACCOUNT, "AIP_ACCOUNT is required when AIP_AUTH=pat"


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


def _identity(conn) -> tuple[str, str]:
    cur = conn.cursor()
    try:
        user, role = cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE()").fetchone()
        return user, role
    finally:
        cur.close()


def dev_db() -> Db:
    global _dev
    with _lock:
        if _dev is None or _dev.conn.is_closed():
            conn = snowflake.connector.connect(
                connection_name=CONNECTION, warehouse=WAREHOUSE, database=DATABASE,
                **({"role": ROLE} if ROLE else {}),
            )
            user, role = _identity(conn)
            _dev = Db(conn, user, role)
        return _dev


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
        if db and (db.expires_at < time.time() or db.conn.is_closed()):
            _sessions.pop(session_id, None)
            db = None
    return db


def close_session(session_id: Optional[str]) -> None:
    with _lock:
        db = _sessions.pop(session_id or "", None)
    if db:
        db.conn.close()
