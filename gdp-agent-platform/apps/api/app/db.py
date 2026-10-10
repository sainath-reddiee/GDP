"""Snowflake sessions for the backend.

AIP_AUTH=dev  one shared session from the named connection in ~/.snowflake/connections.toml
              (browser SSO on this account). Every action is attributed to that user.
AIP_AUTH=pat  each user signs in with their own programmatic access token; procedures see
              that user as CURRENT_USER(). Tokens are held in memory only, never persisted.
system_db()   background work (Airflow ingest, "poll now"): the dev session in dev mode, otherwise a key-pair
              service user from AIP_SERVICE_USER and AIP_SERVICE_KEY_PATH. Shared, so never in a transaction.
worker_db()   the ops worker's own non-shared connection with the same identity, so it can use transactions.

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
from contextlib import contextmanager
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
    shared: bool = False  # the dev session, used by every request at once

    @property
    def host(self) -> str:
        return self.conn.host

    def execute(self, sql: str, params: tuple = ()) -> None:
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
        finally:
            cur.close()

    def execute_count(self, sql: str, params: tuple = ()) -> int:
        """Run a DML statement and return the number of rows it changed."""
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            return int(cur.rowcount or 0)
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


@contextmanager
def transaction(db: Any):
    """BEGIN ... COMMIT around a group of statements; ROLLBACK when any of them fails.

    The connection runs in autocommit mode, where an explicit BEGIN opens a transaction in Snowflake.
    The shared dev session gets no explicit transaction: statements from other requests running at the same time
    would join it, and a ROLLBACK here would undo their writes too.
    """
    if getattr(db, "shared", False):
        yield db
        return
    db.execute("BEGIN")
    try:
        yield db
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except Exception:
            pass
        raise
    db.execute("COMMIT")


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
            _dev = Db(conn, user, role, shared=True)
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


SERVICE_USER_ENV = "AIP_SERVICE_USER"
SERVICE_KEY_ENV = "AIP_SERVICE_KEY_PATH"
_system: Optional[Db] = None
_system_lock = threading.Lock()


def _open_service(user: str, key_path: str):
    """Key-pair (JWT) session for the service user. The key file path and passphrase come from the environment only."""
    kwargs: dict[str, Any] = {
        "account": ACCOUNT, "user": user, "authenticator": "SNOWFLAKE_JWT", "private_key_file": key_path,
        "warehouse": os.environ.get("AIP_SERVICE_WAREHOUSE") or WAREHOUSE, "database": DATABASE,
        "client_session_keep_alive": True, "login_timeout": 60,
    }
    passphrase = os.environ.get("AIP_SERVICE_KEY_PASSPHRASE")
    if passphrase:
        kwargs["private_key_file_pwd"] = passphrase
    role = os.environ.get("AIP_SERVICE_ROLE") or ROLE
    if role:
        kwargs["role"] = role
    return snowflake.connector.connect(**kwargs)


def system_db() -> Db:
    """The platform's own identity for background work (the ops worker, the Airflow ingest endpoint).

    Dev mode: the shared dev session. PAT mode: a key-pair session for AIP_SERVICE_USER with the private key at
    AIP_SERVICE_KEY_PATH (optional AIP_SERVICE_ROLE, AIP_SERVICE_WAREHOUSE, AIP_SERVICE_KEY_PASSPHRASE), opened once,
    shared by every caller and reopened when it dies. Raises SnowflakeSessionError when it is not configured."""
    global _system
    if AUTH_MODE == "dev":
        return dev_db()
    with _system_lock:
        if not _dead(_system):
            return _system
        user = (os.environ.get(SERVICE_USER_ENV) or "").strip()
        key_path = (os.environ.get(SERVICE_KEY_ENV) or "").strip()
        if not user or not key_path:
            raise SnowflakeSessionError(
                f"The service identity is not configured: set {SERVICE_USER_ENV} and {SERVICE_KEY_ENV} "
                "(a key-pair service user with the OPS_SERVICE database role) on this host.")
        if not os.path.isfile(key_path):
            raise SnowflakeSessionError(f"{SERVICE_KEY_ENV} does not point to a readable private key file.")
        try:
            conn = _open_service(user, key_path)
            found_user, role = _identity(conn)
        except Exception as exc:
            # the connector's message does not carry the key; keep it short anyway
            raise SnowflakeSessionError(f"The service user could not sign in: {str(exc)[:300]}") from exc
        _system = Db(conn, found_user, role, shared=True)
        return _system


_worker: Optional[Db] = None
_worker_lock = threading.Lock()


def worker_db() -> Db:
    """A dedicated, non-shared session for the ops worker loop, so its groups of writes can run in transactions
    (app.db.transaction) without catching statements of other callers.

    Dev mode: a second connection from the same named connection; the connector reuses the SSO token cached by the
    first sign-in (client_store_temporary_credential, kept in memory by _patch_keyring), so no second browser prompt in
    a process that already signed in. PAT mode: a second key-pair session for the service user. Opened once per
    process and reopened when it dies; SnowflakeSessionError when it cannot be opened."""
    global _worker
    with _worker_lock:
        if not _dead(_worker):
            return _worker
        if AUTH_MODE == "dev":
            try:
                conn = _open_dev()
                user, role = _identity(conn)
            except Exception as exc:
                raise SnowflakeSessionError(_friendly(exc)) from exc
        else:
            user_name = (os.environ.get(SERVICE_USER_ENV) or "").strip()
            key_path = (os.environ.get(SERVICE_KEY_ENV) or "").strip()
            if not user_name or not key_path or not os.path.isfile(key_path):
                system_db()   # raises the same configuration error the ingest gets
            try:
                conn = _open_service(user_name, key_path)
                user, role = _identity(conn)
            except Exception as exc:
                raise SnowflakeSessionError(f"The service user could not sign in: {str(exc)[:300]}") from exc
        _worker = Db(conn, user, role, shared=False)
        return _worker


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
