"""Personas and people (V076; services/governance/personas.py): a super admin assigns personas, named bundles of
governance roles, to people, and edits the bundles; the platform grants the roles (CORE.USER_ROLE, as Admin, Access
does). Governance is a super admin's alone: every route here except a person's own personas needs ROLE.MANAGE, which
only "*" holds (policy.SUPER_ONLY), and policy.RULES / READ_RULES enforce it before the endpoint runs.
"""

from __future__ import annotations

import functools
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.governance import _event, gov_db, identity, invalidate, ready
from app.main import _snowflake_error, current_db
from services.governance import personas as ps
from services.governance.policy import effective_privileges

router = APIRouter()


def _missing(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("persona" in text) and ("does not exist" in text or "not authorized" in text)


def _deployed(fn):
    """Personas need V076: before it is deployed, say so instead of failing with a Snowflake error."""
    @functools.wraps(fn)  # FastAPI reads the endpoint's parameters through the wrapper
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            if _missing(exc):
                raise HTTPException(503, "Personas are not deployed yet: apply migration V076 "
                                         "(python infrastructure/deploy_snowflake.py --database DEV_AI_PLATFORM).") from exc
            raise _snowflake_error(exc) from exc
    return run


def _bundles(db: Db) -> ps.Bundles:
    out: ps.Bundles = {}
    for r in db.query("SELECT P.PERSONA_ID, R.ROLE_NAME FROM CORE.PERSONA P LEFT JOIN CORE.PERSONA_ROLE R ON R.PERSONA_ID = P.PERSONA_ID "
                      "WHERE COALESCE(P.ACTIVE, TRUE)"):
        out.setdefault(r["persona_id"], set())
        if r.get("role_name"):
            out[r["persona_id"]].add(r["role_name"])
    return out


def _held(db: Db) -> Dict[str, set]:
    out: Dict[str, set] = {}
    for r in db.query("SELECT USER_NAME, PERSONA_ID FROM CORE.USER_PERSONA"):
        out.setdefault(r["user_name"].upper(), set()).add(r["persona_id"])
    return out


def _user_roles(db: Db) -> Dict[str, set]:
    out: Dict[str, set] = {}
    for r in db.query("SELECT USER_NAME, ROLE_NAME FROM CORE.USER_ROLE"):
        out.setdefault(r["user_name"].upper(), set()).add(r["role_name"])
    return out


def _write_roles(db: Db, user: str, roles: List[str]) -> None:
    db.execute("DELETE FROM CORE.USER_ROLE WHERE USER_NAME = %s", (user,))
    for r in roles:
        db.execute("INSERT INTO CORE.USER_ROLE (USER_NAME, ROLE_NAME) SELECT %s, %s", (user, r))


# ---------------------------------------------------------------- the caller's own personas (anyone signed in)


@router.get("/api/me/personas")
def my_personas(db: Db = Depends(current_db)):
    """The personas the caller was given, and the ones their roles match (for people from before personas). Before
    V076 is deployed: deployed false, and "View as" falls back to the caller's privileges."""
    try:
        user = identity(db)["user"] if ready(db) else str(db.user or "").upper()
        held = sorted(_held(db).get(user, set()))
        roles = _user_roles(db).get(user, set())
        return {"user": user, "personas": held, "matching": ps.matching(roles, _bundles(db)), "deployed": True}
    except Exception as exc:
        if _missing(exc):
            return {"user": str(db.user or "").upper(), "personas": [], "matching": [], "deployed": False}
        raise _snowflake_error(exc) from exc


# ---------------------------------------------------------------- personas (super admin)


@router.get("/api/governance/personas")
@_deployed
def list_personas(db: Db = Depends(gov_db)):
    """Every persona with its roles, what those roles add up to, and who holds it."""
    from app.governance import role_model

    role_privs, grants = role_model(db)
    held = _held(db)
    rows = db.query("SELECT PERSONA_ID, LABEL, DESCRIPTION, POSITION, ACTIVE, UPDATED_BY, UPDATED_AT FROM CORE.PERSONA ORDER BY POSITION, PERSONA_ID")
    bundles = _bundles(db)
    out = []
    for r in rows:
        roles = sorted(bundles.get(r["persona_id"], set()))
        _, effective = effective_privileges(roles, role_privs, grants)
        out.append({**r, "updated_at": str(r.get("updated_at") or ""), "roles": roles, "effective": sorted(effective),
                    "holders": sorted(u for u, p in held.items() if r["persona_id"] in p)})
    known = db.query("SELECT ROLE_NAME, DESCRIPTION FROM CORE.APP_ROLE ORDER BY ROLE_NAME")
    return {"personas": out, "roles": [{"role": k["role_name"], "description": k["description"],
                                        "retired": k["role_name"] in ps.RETIRED_ROLES} for k in known]}


class BundleIn(BaseModel):
    roles: List[str] = Field(default_factory=list, max_length=20)
    label: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=1000)


@router.put("/api/governance/personas/{persona_id}")
@_deployed
def edit_persona(persona_id: str, body: BundleIn, db: Db = Depends(gov_db)):
    """Change a persona's bundle (and name); everyone who holds it moves with it."""
    found = db.query("SELECT PERSONA_ID FROM CORE.PERSONA WHERE PERSONA_ID = %s", (persona_id,))
    if not found:
        raise HTTPException(404, "No such persona.")
    roles = sorted({r.upper() for r in body.roles})
    known = {r["role_name"] for r in db.query("SELECT ROLE_NAME FROM CORE.APP_ROLE")}
    problems = ps.validate_bundle(persona_id, roles, known)
    if problems:
        raise HTTPException(422, " ".join(problems))
    bundles, held, user_roles = _bundles(db), _held(db), _user_roles(db)
    holders = {u: {"roles": user_roles.get(u, set()), "personas": p} for u, p in held.items()}
    moved = ps.holders_after_bundle_change(holders, persona_id, set(roles), bundles)
    supers = [u for u, r in user_roles.items() if ps.SUPER_ROLE in r]
    for user, new in moved.items():
        if ps.removes_last_super(user, new, supers):
            raise HTTPException(409, "That would leave the platform with no super admin.")
    db.execute("DELETE FROM CORE.PERSONA_ROLE WHERE PERSONA_ID = %s", (persona_id,))
    for r in roles:
        db.execute("INSERT INTO CORE.PERSONA_ROLE (PERSONA_ID, ROLE_NAME) SELECT %s, %s", (persona_id, r))
    if body.label or body.description is not None:
        db.execute("UPDATE CORE.PERSONA SET LABEL = COALESCE(%s, LABEL), DESCRIPTION = COALESCE(%s, DESCRIPTION), "
                   "UPDATED_BY = CURRENT_USER(), UPDATED_AT = CURRENT_TIMESTAMP() WHERE PERSONA_ID = %s",
                   ((body.label or "").strip() or None, body.description, persona_id))
    else:
        db.execute("UPDATE CORE.PERSONA SET UPDATED_BY = CURRENT_USER(), UPDATED_AT = CURRENT_TIMESTAMP() WHERE PERSONA_ID = %s", (persona_id,))
    for user, new in moved.items():
        _write_roles(db, user, new)
    _event(db, "PERSONA_CHANGED", persona_id, {"roles": roles, "holders_moved": sorted(moved)})
    invalidate()
    return list_personas(db=db)


# ---------------------------------------------------------------- people (super admin)


@router.get("/api/governance/people")
@_deployed
def list_people(db: Db = Depends(gov_db)):
    """Everyone with a role or a persona (and people seen on runs and requests): their personas, the roles they
    hold, the personas their roles match, and the domains they own or steward."""
    from app.governance import list_users

    held, user_roles, bundles = _held(db), _user_roles(db), _bundles(db)
    users = {u["user"].upper() for u in list_users(db=db)["users"]} | set(held)
    owned: Dict[str, List[Dict[str, str]]] = {}
    for r in db.query("SELECT M.USER_NAME, M.ROLE, D.DOMAIN_ID, D.DOMAIN_NAME FROM CORE.DOMAIN_MEMBER M "
                      "JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = M.DOMAIN_ID WHERE COALESCE(D.ACTIVE_FLAG, TRUE)"):
        owned.setdefault(r["user_name"].upper(), []).append({"domain_id": r["domain_id"], "domain_name": r["domain_name"], "role": r["role"]})
    people = []
    for u in sorted(users):
        roles = user_roles.get(u, set())
        mine = held.get(u, set())
        people.append({
            "user": u, "personas": sorted(mine), "roles": sorted(roles),
            "matching": [] if mine else ps.matching(roles, bundles),
            # roles held outside every persona the person holds (given directly in Admin, Access)
            "direct_roles": sorted(roles - ps.roles_of(mine, bundles)) if mine else [],
            "domains": owned.get(u, []),
        })
    from app.governance import settings
    return {"people": people, "default_role": settings(db).get("DEFAULT_ROLE")}


class PeoplePersonas(BaseModel):
    personas: List[str] = Field(default_factory=list, max_length=12)


@router.put("/api/governance/people/{user}/personas")
@_deployed
def assign_personas(user: str, body: PeoplePersonas, db: Db = Depends(gov_db)):
    """Give a person exactly these personas; their roles follow. A person may be named who has no role yet."""
    name = user.strip().upper()
    if not name or len(name) > 256:
        raise HTTPException(400, "Name the Snowflake user.")
    bundles = _bundles(db)
    wanted = sorted(set(body.personas))
    unknown = [p for p in wanted if p not in bundles]
    if unknown:
        raise HTTPException(422, f"Unknown persona {unknown[0]}.")
    before = _held(db).get(name, set())
    user_roles = _user_roles(db)
    current = user_roles.get(name, set())
    roles = ps.roles_after_assignment(current, before, wanted, bundles)
    supers = [u for u, r in user_roles.items() if ps.SUPER_ROLE in r]
    if ps.removes_last_super(name, roles, supers):
        raise HTTPException(409, "This is the last super admin; give the Super admin persona to someone else first.")
    db.execute("DELETE FROM CORE.USER_PERSONA WHERE USER_NAME = %s", (name,))
    for p in wanted:
        db.execute("INSERT INTO CORE.USER_PERSONA (USER_NAME, PERSONA_ID) SELECT %s, %s", (name, p))
    _write_roles(db, name, roles)
    _event(db, "USER_PERSONAS", name, {"personas": wanted, "before": sorted(before), "roles": roles})
    invalidate()
    return {"user": name, "personas": wanted, "roles": roles}
