"""Governance in the API: who may call what, change requests routed to approver roles, and the admin endpoints.

The middleware runs before every /api call. It resolves the caller's Snowflake user (dev connection or token
session), their app roles and privileges (cached a minute), and decides with services.governance.policy:
ALLOW passes through; FORBID returns 403; REQUEST stores the call as a change request and returns 202. Approving
a request replays the original call under the approver's session, so Snowflake's own grants still apply.

Until migration V020 is deployed the middleware lets everything through (nothing to enforce against).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import AUTH_MODE, Db, dev_db, lookup_session
from services.governance.policy import (
    ALL, DEFAULT_POLICIES, PRIVILEGES, SYSTEM_ROLES, can_approve, decide, effective_privileges, privilege_for, summarize,
)

router = APIRouter()
_SECRET = secrets.token_bytes(32)
_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}
TTL = 60.0
DEFAULT_SETTINGS = {"DEFAULT_ROLE": "VIEWER", "SUPER_SELF_APPROVE": True, "ENFORCE": True}


def _cached(key: str, loader, ttl: float = TTL):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = loader()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


def invalidate() -> None:
    with _lock:
        _cache.clear()


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _event(db: Db, kind: str, target: str, detail: dict) -> None:
    try:
        db.execute("INSERT INTO GOVERNANCE.GOVERNANCE_EVENT (EVENT_ID, EVENT_TYPE, TARGET, DETAIL) "
                   "SELECT %s, %s, %s, PARSE_JSON(%s)", (str(uuid.uuid4()), kind, target[:500], json.dumps(detail)))
    except Exception:
        pass


# ---------------------------------------------------------------- readiness and bootstrap

def ready(db: Db) -> bool:
    def check():
        try:
            db.query("SELECT 1 FROM GOVERNANCE.APP_ROLE LIMIT 0")
            return True
        except Exception:
            return False
    return _cached("ready", check)


_seeded = {"done": False}
_seed_lock = threading.Lock()
# Snowflake does not enforce primary keys: keep one row per key (cleans up any duplicate from a concurrent start).
_DEDUPE = {
    "APP_ROLE": "ROLE_NAME", "ROLE_PRIVILEGE": "ROLE_NAME, PRIVILEGE", "ROLE_GRANT": "ROLE_NAME, GRANTED_ROLE",
    "USER_ROLE": "USER_NAME, ROLE_NAME", "APPROVAL_POLICY": "PRIVILEGE", "SETTING": "SETTING_KEY",
}


def _merge(db: Db, table: str, keys: dict, extra: Optional[dict] = None) -> None:
    """Insert the row when its key is not there yet (idempotent under concurrent callers)."""
    cols = {**keys, **(extra or {})}
    on = " AND ".join(f"T.{k} = S.{k}" for k in keys)
    select = ", ".join(f"%s AS {k}" for k in cols)
    db.execute(f"MERGE INTO GOVERNANCE.{table} T USING (SELECT {select}) S ON {on} "
               f"WHEN NOT MATCHED THEN INSERT ({', '.join(cols)}) VALUES ({', '.join('S.' + k for k in cols)})",
               tuple(cols.values()))


def bootstrap(db: Db) -> None:
    """Once per process: create missing system roles (their privileges and inheritance only when the role is new),
    missing default policies, and make the first user (plus AIP_SUPER_ADMINS) super admin when nobody holds a role."""
    if _seeded["done"]:
        return
    with _seed_lock:
        if _seeded["done"]:
            return
        for table, keys in _DEDUPE.items():
            try:
                db.execute(f"INSERT OVERWRITE INTO GOVERNANCE.{table} SELECT * FROM GOVERNANCE.{table} "
                           f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {keys} ORDER BY {keys}) = 1")
            except Exception:
                pass
        existing = {r["role_name"] for r in db.query("SELECT ROLE_NAME FROM GOVERNANCE.APP_ROLE")}
        for name, spec in SYSTEM_ROLES.items():
            if name in existing:
                continue
            _merge(db, "APP_ROLE", {"ROLE_NAME": name}, {"DESCRIPTION": spec["description"], "IS_SYSTEM": True})
            for p in spec["privileges"]:
                _merge(db, "ROLE_PRIVILEGE", {"ROLE_NAME": name, "PRIVILEGE": p})
            for g in spec["inherits"]:
                _merge(db, "ROLE_GRANT", {"ROLE_NAME": name, "GRANTED_ROLE": g})
        for priv, role in DEFAULT_POLICIES.items():
            _merge(db, "APPROVAL_POLICY", {"PRIVILEGE": priv}, {"APPROVER_ROLE": role})
        if not db.query("SELECT 1 FROM GOVERNANCE.USER_ROLE LIMIT 1"):
            admins = {db.user.upper()} | {u.strip().upper() for u in os.environ.get("AIP_SUPER_ADMINS", "").split(",") if u.strip()}
            for user in admins:
                _merge(db, "USER_ROLE", {"USER_NAME": user, "ROLE_NAME": "SUPER_ADMIN"}, {"GRANTED_BY": "BOOTSTRAP"})
            _event(db, "BOOTSTRAP", ",".join(sorted(admins)), {"role": "SUPER_ADMIN"})
        _seeded["done"] = True
    invalidate()


# ---------------------------------------------------------------- model loaded from Snowflake

def settings(db: Db) -> dict:
    def load():
        out = dict(DEFAULT_SETTINGS)
        for r in db.query("SELECT SETTING_KEY, SETTING_VALUE FROM GOVERNANCE.SETTING"):
            out[r["setting_key"]] = _json(r["setting_value"])
        return out
    return _cached("settings", load)


def role_model(db: Db) -> tuple[dict, dict]:
    def load():
        privs: dict[str, list[str]] = {}
        for r in db.query("SELECT ROLE_NAME, PRIVILEGE FROM GOVERNANCE.ROLE_PRIVILEGE"):
            privs.setdefault(r["role_name"], []).append(r["privilege"])
        grants: dict[str, list[str]] = {}
        for r in db.query("SELECT ROLE_NAME, GRANTED_ROLE FROM GOVERNANCE.ROLE_GRANT"):
            grants.setdefault(r["role_name"], []).append(r["granted_role"])
        return privs, grants
    return _cached("roles", load)


def policies(db: Db) -> dict[str, dict]:
    def load():
        return {r["privilege"]: {"requires_approval": bool(r["requires_approval"]), "approver_role": r["approver_role"],
                                 "four_eyes": bool(r["four_eyes"]), "allow_self": bool(r["allow_self"]),
                                 "active": bool(r["active"])}
                for r in db.query("SELECT * FROM GOVERNANCE.APPROVAL_POLICY")}
    return _cached("policies", load)


def identity(db: Db) -> dict:
    """The caller's roles and privileges (bootstrapping on first use)."""
    bootstrap(db)
    user = db.user.upper()

    def load():
        roles = [r["role_name"] for r in db.query("SELECT ROLE_NAME FROM GOVERNANCE.USER_ROLE WHERE USER_NAME = %s", (user,))]
        if not roles and settings(db).get("DEFAULT_ROLE"):
            roles = [str(settings(db)["DEFAULT_ROLE"]).upper()]
        role_privs, grants = role_model(db)
        all_roles, privs = effective_privileges(roles, role_privs, grants)
        return {"user": user, "granted": sorted(roles), "roles": sorted(all_roles), "privileges": sorted(privs)}
    return _cached(f"user:{user}", load)


# ---------------------------------------------------------------- middleware

def _db_for(headers) -> Optional[Db]:
    try:
        return dev_db() if AUTH_MODE == "dev" else lookup_session(headers.get("x-aip-session"))
    except Exception:
        return None


def _replay_token(request_id: str) -> str:
    return request_id + "." + hmac.new(_SECRET, request_id.encode(), hashlib.sha256).hexdigest()


def _valid_replay(token: Optional[str]) -> Optional[str]:
    if not token or "." not in token:
        return None
    request_id = token.split(".", 1)[0]
    return request_id if hmac.compare_digest(token, _replay_token(request_id)) else None


async def middleware(request: Request, call_next):
    path, method = request.url.path, request.method.upper()
    if not path.startswith("/api/") or method == "OPTIONS" or path.startswith("/api/auth/"):
        return await call_next(request)
    body: Any = None
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
    privilege, title, _ = privilege_for(method, path, body if isinstance(body, dict) else None)
    if not privilege or _valid_replay(request.headers.get("x-aip-replay")):
        return await call_next(request)
    db = await run_in_threadpool(_db_for, request.headers)
    if db is None or not await run_in_threadpool(ready, db):
        return await call_next(request)  # signed-out callers get the handler's own 401; before V020 nothing is enforced
    try:
        who = await run_in_threadpool(identity, db)
        if not settings(db).get("ENFORCE", True):
            return await call_next(request)
        verdict, reason = decide(privilege, set(who["privileges"]), policies(db).get(privilege))
    except Exception:
        return await call_next(request)
    if verdict == "ALLOW":
        return await call_next(request)
    if verdict == "FORBID":
        return JSONResponse({"detail": f"Not allowed: this {reason}. Ask a governance admin for a role that has it."},
                            status_code=403)
    policy = policies(db)[privilege]
    request_id = str(uuid.uuid4())
    parts = path.strip("/").split("/")
    run_id = parts[2] if len(parts) > 2 and parts[1] == "runs" else None
    await run_in_threadpool(lambda: db.execute(
        """INSERT INTO GOVERNANCE.CHANGE_REQUEST (REQUEST_ID, PRIVILEGE, TITLE, SUMMARY, RUN_ID, METHOD, PATH, PAYLOAD,
                                                  REQUESTED_BY, APPROVER_ROLE, ALLOW_SELF, STATUS)
           SELECT %s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), %s, %s, %s, 'PENDING'""",
        (request_id, privilege, title or PRIVILEGES.get(privilege, ("", privilege))[1], summarize(method, path, body, title)[:1000],
         run_id, method, path + (f"?{request.url.query}" if request.url.query else ""), json.dumps(body),
         who["user"], policy["approver_role"], policy["allow_self"])))
    _event(db, "REQUESTED", request_id, {"privilege": privilege, "path": path})
    return JSONResponse({"pending_approval": True, "request_id": request_id, "approver_role": policy["approver_role"],
                         "detail": f"Sent to {policy['approver_role']} for approval. It is applied as soon as they "
                                   f"approve it (Approvals, request {request_id[:8]})."}, status_code=202)


# ---------------------------------------------------------------- endpoints

def gov_db(x_aip_session: Optional[str] = Header(default=None)) -> Db:
    db = dev_db() if AUTH_MODE == "dev" else lookup_session(x_aip_session)
    if db is None:
        raise HTTPException(401, "Sign in required")
    if not ready(db):
        raise HTTPException(409, "Governance is not deployed yet (migration V020).")
    return db


def summary(db: Db) -> dict:
    """For /api/auth/me: roles, privileges and how many requests wait for this user."""
    if not ready(db):
        return {"governance": False, "roles": [], "privileges": [ALL], "pending_for_me": 0}
    who = identity(db)
    pending = 0
    try:
        rows = db.query("SELECT APPROVER_ROLE, REQUESTED_BY, ALLOW_SELF FROM GOVERNANCE.CHANGE_REQUEST WHERE STATUS = 'PENDING'")
        roles = set(who["roles"])
        pending = sum(1 for r in rows if can_approve({"status": "PENDING", **r}, who["user"], roles, set(who["privileges"]),
                                                     bool(settings(db).get("SUPER_SELF_APPROVE", True)))[0])
    except Exception:
        pass
    return {"governance": True, "roles": who["roles"], "granted_roles": who["granted"],
            "privileges": who["privileges"], "pending_for_me": pending}


def _requests(db: Db, where: str = "1 = 1", params: tuple = ()) -> list[dict]:
    rows = db.query(f"""SELECT REQUEST_ID, PRIVILEGE, TITLE, SUMMARY, RUN_ID, METHOD, PATH, PAYLOAD, REQUESTED_BY,
                               APPROVER_ROLE, ALLOW_SELF, STATUS, DECIDED_BY, DECISION_NOTE, RESULT,
                               CREATED_AT::VARCHAR AS CREATED_AT, DECIDED_AT::VARCHAR AS DECIDED_AT
                          FROM GOVERNANCE.CHANGE_REQUEST WHERE {where}
                         ORDER BY CREATED_AT DESC LIMIT 300""", params)
    for r in rows:
        r["payload"] = _json(r.get("payload"))
        r["result"] = _json(r.get("result"))
    return rows


@router.get("/api/governance/requests")
def list_requests(scope: str = "inbox", status: Optional[str] = None, db: Db = Depends(gov_db)):
    """inbox: what I can decide; mine: what I asked for; all: everything (APPROVAL.VIEW)."""
    who = identity(db)
    roles, privs = set(who["roles"]), set(who["privileges"])
    status_sql, params = ("STATUS = %s", (status.upper(),)) if status else ("1 = 1", ())
    if scope == "mine":
        rows = _requests(db, f"REQUESTED_BY = %s AND {status_sql}", (who["user"], *params))
    elif scope == "all":
        if "APPROVAL.VIEW" not in privs and ALL not in privs:
            raise HTTPException(403, "Needs the APPROVAL.VIEW privilege.")
        rows = _requests(db, status_sql, params)
    else:
        rows = [r for r in _requests(db, "STATUS = 'PENDING'")
                if can_approve(r, who["user"], roles, privs, bool(settings(db).get("SUPER_SELF_APPROVE", True)))[0]]
    for r in rows:
        r["can_decide"] = can_approve(r, who["user"], roles, privs, bool(settings(db).get("SUPER_SELF_APPROVE", True)))[0]
    return {"requests": rows, "user": who["user"]}


class Decision(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


def _one(db: Db, request_id: str) -> dict:
    found = _requests(db, "REQUEST_ID = %s", (request_id,))
    if not found:
        raise HTTPException(404, "request not found")
    return found[0]


@router.post("/api/governance/requests/{request_id}/approve")
async def approve_request(request_id: str, body: Decision, request: Request):
    """Approve and apply: the original call is replayed under the approver's session."""
    import httpx

    db = await run_in_threadpool(gov_db, request.headers.get("x-aip-session"))
    req = await run_in_threadpool(_one, db, request_id)
    who = await run_in_threadpool(identity, db)
    ok, why = can_approve(req, who["user"], set(who["roles"]), set(who["privileges"]),
                          bool(settings(db).get("SUPER_SELF_APPROVE", True)))
    if not ok:
        raise HTTPException(403, why)
    await run_in_threadpool(lambda: db.execute(
        "UPDATE GOVERNANCE.CHANGE_REQUEST SET STATUS = 'APPROVED', DECIDED_BY = %s, DECISION_NOTE = %s, "
        "DECIDED_AT = CURRENT_TIMESTAMP() WHERE REQUEST_ID = %s AND STATUS = 'PENDING'",
        (who["user"], body.note, request_id)))
    headers = {"x-aip-replay": _replay_token(request_id), "content-type": "application/json"}
    for h in ("x-aip-session", "x-aip-role"):
        if request.headers.get(h):
            headers[h] = request.headers[h]
    from app.main import app as api_app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://governance", timeout=600) as client:
        response = await client.request(req["method"], req["path"], headers=headers,
                                        content=json.dumps(req["payload"]) if req["payload"] is not None else None)
    try:
        result = response.json()
    except ValueError:
        result = {"text": response.text[:2000]}
    status = "APPLIED" if response.status_code < 400 else "FAILED"
    await run_in_threadpool(lambda: db.execute(
        "UPDATE GOVERNANCE.CHANGE_REQUEST SET STATUS = %s, RESULT = PARSE_JSON(%s) WHERE REQUEST_ID = %s",
        (status, json.dumps({"status_code": response.status_code, "body": result}, default=str)[:60000], request_id)))
    await run_in_threadpool(_event, db, status, request_id, {"by": who["user"], "status_code": response.status_code})
    return {"request_id": request_id, "status": status, "status_code": response.status_code, "result": result}


@router.post("/api/governance/requests/{request_id}/reject")
def reject_request(request_id: str, body: Decision, db: Db = Depends(gov_db)):
    req = _one(db, request_id)
    who = identity(db)
    ok, why = can_approve(req, who["user"], set(who["roles"]), set(who["privileges"]),
                          bool(settings(db).get("SUPER_SELF_APPROVE", True)))
    if not ok:
        raise HTTPException(403, why)
    if not (body.note or "").strip():
        raise HTTPException(400, "Say why the request is rejected.")
    db.execute("UPDATE GOVERNANCE.CHANGE_REQUEST SET STATUS = 'REJECTED', DECIDED_BY = %s, DECISION_NOTE = %s, "
               "DECIDED_AT = CURRENT_TIMESTAMP() WHERE REQUEST_ID = %s AND STATUS = 'PENDING'",
               (who["user"], body.note, request_id))
    _event(db, "REJECTED", request_id, {"by": who["user"], "note": body.note})
    return {"request_id": request_id, "status": "REJECTED"}


@router.post("/api/governance/requests/{request_id}/cancel")
def cancel_request(request_id: str, db: Db = Depends(gov_db)):
    req = _one(db, request_id)
    who = identity(db)
    if req["requested_by"].upper() != who["user"] or req["status"] != "PENDING":
        raise HTTPException(403, "Only the requester can cancel a pending request.")
    db.execute("UPDATE GOVERNANCE.CHANGE_REQUEST SET STATUS = 'CANCELLED', DECIDED_AT = CURRENT_TIMESTAMP() "
               "WHERE REQUEST_ID = %s", (request_id,))
    _event(db, "CANCELLED", request_id, {"by": who["user"]})
    return {"request_id": request_id, "status": "CANCELLED"}


@router.get("/api/governance/privileges")
def list_privileges(db: Db = Depends(gov_db)):
    return {"privileges": [{"privilege": k, "group": g, "description": d} for k, (g, d) in PRIVILEGES.items()]}


@router.get("/api/governance/roles")
def list_roles(db: Db = Depends(gov_db)):
    role_privs, grants = role_model(db)
    members: dict[str, list[str]] = {}
    for r in db.query("SELECT USER_NAME, ROLE_NAME FROM GOVERNANCE.USER_ROLE ORDER BY USER_NAME"):
        members.setdefault(r["role_name"], []).append(r["user_name"])
    return {"roles": [{"role": r["role_name"], "description": r["description"], "system": bool(r["is_system"]),
                       "privileges": sorted(role_privs.get(r["role_name"], [])), "inherits": sorted(grants.get(r["role_name"], [])),
                       "members": members.get(r["role_name"], [])}
                      for r in db.query("SELECT * FROM GOVERNANCE.APP_ROLE ORDER BY IS_SYSTEM DESC, ROLE_NAME")]}


class RoleIn(BaseModel):
    role: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    description: Optional[str] = Field(default=None, max_length=500)
    privileges: list[str] = Field(default_factory=list)
    inherits: list[str] = Field(default_factory=list)


def _write_role(db: Db, body: RoleIn, create: bool) -> dict:
    name = body.role.upper()
    unknown = [p for p in body.privileges if p not in PRIVILEGES and p != ALL]
    if unknown:
        raise HTTPException(400, f"Unknown privilege {unknown[0]}")
    known_roles = {r["role_name"] for r in db.query("SELECT ROLE_NAME FROM GOVERNANCE.APP_ROLE")}
    bad = [r for r in body.inherits if r.upper() not in known_roles or r.upper() == name]
    if bad:
        raise HTTPException(400, f"Cannot inherit {bad[0]}")
    if create:
        if name in known_roles:
            raise HTTPException(409, f"{name} already exists")
        db.execute("INSERT INTO GOVERNANCE.APP_ROLE (ROLE_NAME, DESCRIPTION) SELECT %s, %s", (name, body.description))
    else:
        if name not in known_roles:
            raise HTTPException(404, f"{name} not found")
        if name == "SUPER_ADMIN":
            raise HTTPException(400, "SUPER_ADMIN always holds every privilege.")
        db.execute("UPDATE GOVERNANCE.APP_ROLE SET DESCRIPTION = %s WHERE ROLE_NAME = %s", (body.description, name))
        db.execute("DELETE FROM GOVERNANCE.ROLE_PRIVILEGE WHERE ROLE_NAME = %s", (name,))
        db.execute("DELETE FROM GOVERNANCE.ROLE_GRANT WHERE ROLE_NAME = %s", (name,))
    for p in dict.fromkeys(body.privileges):
        db.execute("INSERT INTO GOVERNANCE.ROLE_PRIVILEGE (ROLE_NAME, PRIVILEGE) SELECT %s, %s", (name, p))
    for g in dict.fromkeys(r.upper() for r in body.inherits):
        db.execute("INSERT INTO GOVERNANCE.ROLE_GRANT (ROLE_NAME, GRANTED_ROLE) SELECT %s, %s", (name, g))
    _event(db, "ROLE_CREATED" if create else "ROLE_CHANGED", name, body.model_dump())
    invalidate()
    return {"role": name}


@router.post("/api/governance/roles")
def create_role(body: RoleIn, db: Db = Depends(gov_db)):
    return _write_role(db, body, True)


@router.put("/api/governance/roles/{role}")
def update_role(role: str, body: RoleIn, db: Db = Depends(gov_db)):
    body.role = role
    return _write_role(db, body, False)


@router.delete("/api/governance/roles/{role}")
def delete_role(role: str, db: Db = Depends(gov_db)):
    name = role.upper()
    found = db.query("SELECT IS_SYSTEM FROM GOVERNANCE.APP_ROLE WHERE ROLE_NAME = %s", (name,))
    if not found:
        raise HTTPException(404, f"{name} not found")
    if found[0]["is_system"]:
        raise HTTPException(400, "System roles cannot be deleted; remove their members or privileges instead.")
    for table in ("USER_ROLE", "ROLE_PRIVILEGE", "APP_ROLE"):
        db.execute(f"DELETE FROM GOVERNANCE.{table} WHERE ROLE_NAME = %s", (name,))
    db.execute("DELETE FROM GOVERNANCE.ROLE_GRANT WHERE ROLE_NAME = %s OR GRANTED_ROLE = %s", (name, name))
    _event(db, "ROLE_DELETED", name, {})
    invalidate()
    return {"deleted": name}


@router.get("/api/governance/users")
def list_users(db: Db = Depends(gov_db)):
    """Users with roles, plus users seen in the platform (run owners, requesters) without one."""
    assigned: dict[str, list[str]] = {}
    for r in db.query("SELECT USER_NAME, ROLE_NAME FROM GOVERNANCE.USER_ROLE ORDER BY ROLE_NAME"):
        assigned.setdefault(r["user_name"], []).append(r["role_name"])
    seen = set()
    try:
        seen = {str(r["u"]).upper() for r in db.query(
            "SELECT DISTINCT CREATED_BY AS U FROM CORE.WORKFLOW_RUN WHERE CREATED_BY IS NOT NULL "
            "UNION SELECT DISTINCT REQUESTED_BY FROM GOVERNANCE.CHANGE_REQUEST")}
    except Exception:
        pass
    users = sorted(set(assigned) | seen)
    return {"users": [{"user": u, "roles": assigned.get(u, [])} for u in users],
            "default_role": settings(db).get("DEFAULT_ROLE")}


class UserRoles(BaseModel):
    roles: list[str] = Field(default_factory=list, max_length=20)


@router.put("/api/governance/users/{user}")
def set_user_roles(user: str, body: UserRoles, db: Db = Depends(gov_db)):
    name = user.strip().upper()
    if not name:
        raise HTTPException(400, "user is required")
    roles = list(dict.fromkeys(r.upper() for r in body.roles))
    known = {r["role_name"] for r in db.query("SELECT ROLE_NAME FROM GOVERNANCE.APP_ROLE")}
    bad = [r for r in roles if r not in known]
    if bad:
        raise HTTPException(400, f"Unknown role {bad[0]}")
    if "SUPER_ADMIN" not in roles:
        others = db.query("SELECT COUNT(*) AS N FROM GOVERNANCE.USER_ROLE WHERE ROLE_NAME = 'SUPER_ADMIN' AND USER_NAME <> %s",
                          (name,))
        if not others[0]["n"] and db.query("SELECT 1 FROM GOVERNANCE.USER_ROLE WHERE USER_NAME = %s AND ROLE_NAME = 'SUPER_ADMIN'", (name,)):
            raise HTTPException(400, "This is the last super admin; grant SUPER_ADMIN to someone else first.")
    db.execute("DELETE FROM GOVERNANCE.USER_ROLE WHERE USER_NAME = %s", (name,))
    for r in roles:
        db.execute("INSERT INTO GOVERNANCE.USER_ROLE (USER_NAME, ROLE_NAME) SELECT %s, %s", (name, r))
    _event(db, "USER_ROLES", name, {"roles": roles})
    invalidate()
    return {"user": name, "roles": roles}


@router.get("/api/governance/policies")
def list_policies(db: Db = Depends(gov_db)):
    current = policies(db)
    return {"policies": [{"privilege": k, "group": g, "description": d, **(current.get(k) or
                          {"requires_approval": False, "approver_role": None, "four_eyes": False, "allow_self": False,
                           "active": False})}
                         for k, (g, d) in PRIVILEGES.items() if k not in ("ADMIN.VIEW", "APPROVAL.VIEW", "AUDIT.VIEW",
                                                                         "REQUEST.CHANGES")]}


class PolicyIn(BaseModel):
    requires_approval: bool = True
    approver_role: str = Field(min_length=2, max_length=64)
    four_eyes: bool = False
    allow_self: bool = False
    active: bool = True


@router.put("/api/governance/policies/{privilege}")
def set_policy(privilege: str, body: PolicyIn, db: Db = Depends(gov_db)):
    if privilege not in PRIVILEGES:
        raise HTTPException(404, "unknown privilege")
    if not db.query("SELECT 1 FROM GOVERNANCE.APP_ROLE WHERE ROLE_NAME = %s", (body.approver_role.upper(),)):
        raise HTTPException(400, f"Unknown role {body.approver_role}")
    db.execute("""MERGE INTO GOVERNANCE.APPROVAL_POLICY P USING (SELECT %s AS PRIVILEGE) S ON P.PRIVILEGE = S.PRIVILEGE
                  WHEN MATCHED THEN UPDATE SET REQUIRES_APPROVAL = %s, APPROVER_ROLE = %s, FOUR_EYES = %s, ALLOW_SELF = %s,
                       ACTIVE = %s, UPDATED_BY = CURRENT_USER(), UPDATED_AT = CURRENT_TIMESTAMP()
                  WHEN NOT MATCHED THEN INSERT (PRIVILEGE, REQUIRES_APPROVAL, APPROVER_ROLE, FOUR_EYES, ALLOW_SELF, ACTIVE)
                       VALUES (S.PRIVILEGE, %s, %s, %s, %s, %s)""",
               (privilege, body.requires_approval, body.approver_role.upper(), body.four_eyes, body.allow_self, body.active,
                body.requires_approval, body.approver_role.upper(), body.four_eyes, body.allow_self, body.active))
    _event(db, "POLICY", privilege, body.model_dump())
    invalidate()
    return {"privilege": privilege, **body.model_dump()}


@router.get("/api/governance/settings")
def get_settings(db: Db = Depends(gov_db)):
    return {"settings": settings(db)}


class SettingsIn(BaseModel):
    DEFAULT_ROLE: Optional[str] = None
    SUPER_SELF_APPROVE: Optional[bool] = None
    ENFORCE: Optional[bool] = None


@router.put("/api/governance/settings")
def put_settings(body: SettingsIn, db: Db = Depends(gov_db)):
    for key, value in body.model_dump(exclude_none=True).items():
        if key == "DEFAULT_ROLE":
            value = str(value).upper()
            if value and not db.query("SELECT 1 FROM GOVERNANCE.APP_ROLE WHERE ROLE_NAME = %s", (value,)):
                raise HTTPException(400, f"Unknown role {value}")
        db.execute("""MERGE INTO GOVERNANCE.SETTING T USING (SELECT %s AS K) S ON T.SETTING_KEY = S.K
                      WHEN MATCHED THEN UPDATE SET SETTING_VALUE = PARSE_JSON(%s), UPDATED_BY = CURRENT_USER(),
                           UPDATED_AT = CURRENT_TIMESTAMP()
                      WHEN NOT MATCHED THEN INSERT (SETTING_KEY, SETTING_VALUE) VALUES (S.K, PARSE_JSON(%s))""",
                   (key, json.dumps(value), json.dumps(value)))
        _event(db, "SETTING", key, {"value": value})
    invalidate()
    return {"settings": settings(db)}


@router.get("/api/governance/events")
def list_events(db: Db = Depends(gov_db)):
    rows = db.query("""SELECT EVENT_ID, EVENT_TYPE, ACTOR, TARGET, DETAIL, CREATED_AT::VARCHAR AS CREATED_AT
                         FROM GOVERNANCE.GOVERNANCE_EVENT ORDER BY CREATED_AT DESC LIMIT 300""")
    for r in rows:
        r["detail"] = _json(r.get("detail"))
    return {"events": rows}
