"""Domains as versioned, owned products: version history with plain-language summaries, diffs and rollback, owners and
stewards, the domain's own rules, and what the domain cards show. Snapshots come from services/knowledge/domain_versions.py.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _RULES_CACHE, _domain_caches_changed, _domain_snapshot, _json, _rules, current_db
from services.knowledge import domain_versions as dv

router = APIRouter()
ROLES = ("OWNER", "STEWARD", "EXPERT")


def _q(db: Db):
    return lambda sql, params: db.query(sql, params)


def _domain(db: Db, domain_id: str) -> dict:
    found = db.query("SELECT DOMAIN_ID, DOMAIN_NAME, DESCRIPTION, OWNER, ACTIVE_FLAG, VERSION, CONFIG "
                     "FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,))
    if not found:
        raise HTTPException(404, "domain not found")
    row = found[0]
    row["config"] = _json(row.get("config")) or {}
    return row


def _baseline(db: Db, domain_id: str) -> None:
    """Make sure the state before a change is on record (domains created before versioning)."""
    if not dv.latest(_q(db), domain_id):
        _domain_snapshot(db, domain_id, "BASELINE", "State before the first recorded change")


@router.get("/api/domain-cards")
def domain_cards(db: Db = Depends(current_db)):
    """Per domain: members, the newest version (who, when, what kind), runs in the last 30 days, stale knowledge."""
    out: dict[str, dict] = {}
    try:
        for m in db.query("SELECT DOMAIN_ID, USER_NAME, ROLE FROM KNOWLEDGE.DOMAIN_MEMBER ORDER BY ROLE, USER_NAME"):
            out.setdefault(m["domain_id"], {}).setdefault("members", []).append({"user": m["user_name"], "role": m["role"]})
        for v in db.query("""SELECT DOMAIN_ID, VERSION, CHANGE_KIND, CHANGE_NOTE, CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT
                               FROM KNOWLEDGE.DOMAIN_VERSION
                              QUALIFY ROW_NUMBER() OVER (PARTITION BY DOMAIN_ID ORDER BY VERSION DESC) = 1"""):
            out.setdefault(v["domain_id"], {})["last_change"] = v
    except Exception:
        pass
    for r in db.query("""SELECT DOMAIN_ID, COUNT(*) AS RUNS FROM CORE.WORKFLOW_RUN
                          WHERE DOMAIN_ID IS NOT NULL AND CREATED_AT >= DATEADD(DAY, -30, CURRENT_TIMESTAMP()) GROUP BY 1"""):
        out.setdefault(r["domain_id"], {})["runs_30d"] = int(r["runs"])
    try:
        for r in db.query("""SELECT DOMAIN_ID, COUNT_IF(STATUS = 'PROPOSED') AS INBOX,
                                    COUNT_IF(IS_CURRENT AND STATUS = 'ACTIVE' AND (REVIEW_DUE < CURRENT_DATE() OR (VERIFIED_AT IS NULL
                                             AND COALESCE(UPDATED_AT, CREATED_AT) < DATEADD(DAY, -180, CURRENT_TIMESTAMP())))) AS STALE,
                                    COUNT_IF(IS_CURRENT AND VERIFIED_AT IS NOT NULL) AS VERIFIED
                               FROM KNOWLEDGE.DOMAIN_KNOWLEDGE GROUP BY 1"""):
            out.setdefault(r["domain_id"], {}).update(inbox=int(r["inbox"]), stale=int(r["stale"]), verified=int(r["verified"]))
    except Exception:
        pass
    return {"cards": out}


@router.get("/api/domains/{domain_id}/versions")
def versions(domain_id: str, db: Db = Depends(current_db)):
    _domain(db, domain_id)
    rows = db.query("""SELECT DOMAIN_ID, VERSION, DESCRIPTION, OWNER, ACTIVE_FLAG, CONFIG_JSON, TARGETS_JSON, CHANGE_KIND,
                              CHANGE_NOTE, CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT
                         FROM KNOWLEDGE.DOMAIN_VERSION WHERE DOMAIN_ID = %s ORDER BY VERSION""", (domain_id,))
    out, prev = [], None
    for r in rows:
        snap = dv.from_row(r)
        out.append({"version": r["version"], "change_kind": r["change_kind"], "change_note": r["change_note"],
                    "created_by": r["created_by"], "created_at": r["created_at"], "summary": dv.summary(prev, snap),
                    "targets": len(snap["targets"]), "columns": sum(len(t.get("columns") or []) for t in snap["targets"])})
        prev = snap
    current = dv.state(_q(db), domain_id)
    pending = bool(rows) and current is not None and dv.checksum(current) != db.query(
        "SELECT CHECKSUM FROM KNOWLEDGE.DOMAIN_VERSION WHERE DOMAIN_ID = %s ORDER BY VERSION DESC LIMIT 1", (domain_id,))[0]["checksum"]
    return {"versions": list(reversed(out)), "unrecorded_changes": pending}


def _version(db: Db, domain_id: str, version: int) -> dict:
    found = db.query("SELECT * FROM KNOWLEDGE.DOMAIN_VERSION WHERE DOMAIN_ID = %s AND VERSION = %s", (domain_id, version))
    if not found:
        raise HTTPException(404, f"version {version} not found")
    return dv.from_row(found[0])


@router.get("/api/domains/{domain_id}/diff")
def diff(domain_id: str, base: int, head: int, db: Db = Depends(current_db)):
    a, b = _version(db, domain_id, base), _version(db, domain_id, head)
    files = dv.diff(a, b)
    return {"base": base, "head": head, "summary": dv.summary(a, b), "files": files,
            "added": sum(f["added"] for f in files), "removed": sum(f["removed"] for f in files)}


class Rollback(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/domains/{domain_id}/rollback/{version}")
def rollback(domain_id: str, version: int, body: Rollback, db: Db = Depends(current_db)):
    """Bring back an earlier version's description, owner, rules, detection signals, source systems and contract, as a
    new version. Target models and knowledge are not rewritten: they have their own history."""
    row = _domain(db, domain_id)
    old = _version(db, domain_id, version)
    _baseline(db, domain_id)
    config = dv.rollback_config(row["config"], old["config"])
    db.execute("""UPDATE KNOWLEDGE.DOMAIN_REGISTRY SET DESCRIPTION = %s, OWNER = %s, CONFIG = PARSE_JSON(%s),
                         UPDATED_AT = CURRENT_TIMESTAMP() WHERE DOMAIN_ID = %s""",
               (old["description"], old["owner"], json.dumps(config, default=str), domain_id))
    _RULES_CACHE.clear()
    _domain_caches_changed()
    new = _domain_snapshot(db, domain_id, "ROLLBACK", body.note or f"Rolled back to version {version}")
    return {"domain_id": domain_id, "version": new, "rolled_back_to": version, "changed": new is not None}


class DomainEdit(BaseModel):
    description: Optional[str] = Field(default=None, max_length=4000)
    owner: Optional[str] = Field(default=None, max_length=256)
    note: Optional[str] = Field(default=None, max_length=2000)


@router.put("/api/domains/{domain_id}")
def edit(domain_id: str, body: DomainEdit, db: Db = Depends(current_db)):
    row = _domain(db, domain_id)
    _baseline(db, domain_id)
    db.execute("UPDATE KNOWLEDGE.DOMAIN_REGISTRY SET DESCRIPTION = %s, OWNER = %s, UPDATED_AT = CURRENT_TIMESTAMP() "
               "WHERE DOMAIN_ID = %s",
               (body.description if body.description is not None else row["description"],
                (body.owner or "").strip().upper() or row["owner"], domain_id))
    _domain_caches_changed()
    version = _domain_snapshot(db, domain_id, "EDIT", body.note or "Details edited")
    return {"domain_id": domain_id, "version": version}


@router.get("/api/domains/{domain_id}/members")
def members(domain_id: str, db: Db = Depends(current_db)):
    _domain(db, domain_id)
    rows = db.query("""SELECT USER_NAME, ROLE, ADDED_BY, ADDED_AT::VARCHAR AS ADDED_AT FROM KNOWLEDGE.DOMAIN_MEMBER
                        WHERE DOMAIN_ID = %s ORDER BY ARRAY_POSITION(ROLE::VARIANT, ['OWNER', 'STEWARD', 'EXPERT']), USER_NAME""",
                    (domain_id,))
    return {"members": rows, "roles": list(ROLES)}


class Member(BaseModel):
    user: str = Field(min_length=1, max_length=256)
    role: Literal["OWNER", "STEWARD", "EXPERT"]


class Members(BaseModel):
    members: list[Member] = Field(max_length=100)


@router.put("/api/domains/{domain_id}/members")
def set_members(domain_id: str, body: Members, db: Db = Depends(current_db)):
    _domain(db, domain_id)
    wanted = {(m.user.strip().upper(), m.role) for m in body.members if m.user.strip()}
    if not any(r == "OWNER" for _, r in wanted):  # checked after blank names are dropped, before anything is written
        raise HTTPException(400, "A domain needs at least one owner")
    current = {(r["user_name"], r["role"]) for r in db.query(
        "SELECT USER_NAME, ROLE FROM KNOWLEDGE.DOMAIN_MEMBER WHERE DOMAIN_ID = %s", (domain_id,))}
    for user, role in current - wanted:
        db.execute("DELETE FROM KNOWLEDGE.DOMAIN_MEMBER WHERE DOMAIN_ID = %s AND USER_NAME = %s AND ROLE = %s", (domain_id, user, role))
    for user, role in wanted - current:
        db.execute("INSERT INTO KNOWLEDGE.DOMAIN_MEMBER (DOMAIN_ID, USER_NAME, ROLE) VALUES (%s, %s, %s)", (domain_id, user, role))
    owners = sorted(u for u, r in wanted if r == "OWNER")
    db.execute("UPDATE KNOWLEDGE.DOMAIN_REGISTRY SET OWNER = %s WHERE DOMAIN_ID = %s", (owners[0], domain_id))
    _domain_snapshot(db, domain_id, "EDIT", "Owner changed")  # only records when the registry owner changed
    return members(domain_id, db)


@router.get("/api/domains/{domain_id}/rules")
def domain_rules(domain_id: str, db: Db = Depends(current_db)):
    """What a domain overrides, and the platform value it overrides (readable by anyone who can see the domain)."""
    from services.common.rules import DEFAULTS

    row = _domain(db, domain_id)
    platform = _rules(db, None)
    effective = _rules(db, domain_id)
    return {"defaults": DEFAULTS, "platform": platform, "effective": effective,
            "overrides": (row["config"].get("rules") or {})}


@router.get("/api/domains/{domain_id}/columns")
def target_columns(domain_id: str, db: Db = Depends(current_db)):
    """Target models of the domain with their columns (the same shape a version stores)."""
    snap = dv.state(_q(db), domain_id)
    if snap is None:
        raise HTTPException(404, "domain not found")
    return {"targets": snap["targets"]}
