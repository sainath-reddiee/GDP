"""Proposed skills (services.knowledge.scout_run): what the skill scout drafted from repeated, approved work, and why.

A proposal's status follows its skill version: PROPOSED (a DRAFT nothing loads), CANDIDATE (a person set it as the
candidate on the Skills page), PROMOTED (in production), DISMISSED (a person said no) or DISCARDED (the scout's own
checks failed). Governance: reads are open to signed-in users, as the Skills page is; a scan and a dismissal need
SKILL.EDIT, and a scan that drafts also AI.USE (checked here).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _snowflake_error, current_db
from app.ops_api import _iso

router = APIRouter()
COLUMNS = ("PROPOSAL_ID, CLUSTER_KEY, SOURCE, STATUS, SKILL_ID, SKILL_NAME, TITLE, STAGE, EVIDENCE_COUNT, DOMAIN_COUNT, "
           "EVIDENCE, REPLACEMENTS, TRANSFER_SCORE, TRANSFER_LABEL, EVAL, ISSUES, DISCARD_REASON, MODEL, DISMISSED_BY, "
           "DISMISSED_AT, DISMISS_NOTE, CREATED_BY, CREATED_AT, UPDATED_AT")


def _who(db: Db) -> Dict[str, Any]:
    from app.cases_api import _who as who

    return who(db)


def _json(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in ("{", "["):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _labels(db: Db, names: List[str]) -> Dict[str, Dict[str, str]]:
    if not names:
        return {}
    marks = ", ".join(["%s"] * len(names))
    out: Dict[str, Dict[str, str]] = {}
    for r in db.query(f"SELECT SKILL_NAME, LABEL, SKILL_ID FROM CORE.SKILL_LABEL WHERE SKILL_NAME IN ({marks})", tuple(names)):
        out.setdefault(r["skill_name"], {})[r["label"]] = r["skill_id"]
    return out


def _shape(row: Dict[str, Any], labels: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    out = {k: _iso(_json(v)) for k, v in dict(row).items()}
    status = out["status"]
    if status == "PROPOSED" and out.get("skill_id"):
        mine = labels.get(out.get("skill_name") or "", {})
        if mine.get("production") == out["skill_id"]:
            status = "PROMOTED"
        elif mine.get("candidate") == out["skill_id"]:
            status = "CANDIDATE"
    out["state"] = status
    return out


@router.get("/api/skill-proposals")
def list_proposals(status: Optional[str] = Query(None, max_length=16), limit: int = Query(100, ge=1, le=500),
                   db: Db = Depends(current_db)):
    try:
        found = db.query(f"SELECT {COLUMNS} FROM CORE.SKILL_PROPOSAL ORDER BY CREATED_AT DESC LIMIT {int(limit)}")
    except Exception:   # before V067
        return {"items": [], "available": False, "counts": {}}
    labels = _labels(db, sorted({r["skill_name"] for r in found if r.get("skill_name")}))
    items = [_shape(r, labels) for r in found]
    counts: Dict[str, int] = {}
    for i in items:
        counts[i["state"]] = counts.get(i["state"], 0) + 1
    if status:
        items = [i for i in items if i["state"] == status.upper()]
    return {"items": items, "available": True, "counts": counts}


@router.get("/api/skill-proposals/settings")
def scout_settings(db: Db = Depends(current_db)):
    """The scout's settings with defaults (changed through PUT /api/config/platform, key SKILL_SCOUT)."""
    from services.knowledge import scout_run

    return scout_run.load_settings(db)


@router.get("/api/skill-proposals/for-skill/{skill_name}")
def for_skill(skill_name: str, db: Db = Depends(current_db)):
    """The newest proposal that filed a version of this skill (the banner on the skill's page), or null."""
    try:
        found = db.query(f"""SELECT {COLUMNS} FROM CORE.SKILL_PROPOSAL WHERE SKILL_NAME = %s AND SKILL_ID IS NOT NULL
                               ORDER BY CREATED_AT DESC LIMIT 1""", (skill_name.upper(),))
    except Exception:
        return {"proposal": None}
    return {"proposal": _shape(found[0], _labels(db, [found[0]["skill_name"]])) if found else None}


@router.get("/api/skill-proposals/{proposal_id}")
def get_proposal(proposal_id: str, db: Db = Depends(current_db)):
    try:
        found = db.query(f"SELECT {COLUMNS} FROM CORE.SKILL_PROPOSAL WHERE PROPOSAL_ID = %s", (proposal_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    if not found:
        raise HTTPException(404, "proposal not found")
    return _shape(found[0], _labels(db, [found[0]["skill_name"]] if found[0].get("skill_name") else []))


class ScanIn(BaseModel):
    dry_run: bool = True
    key: Optional[str] = Field(None, max_length=256)


@router.post("/api/skill-proposals/scan")
def scan(body: ScanIn, db: Db = Depends(current_db)):
    """Dry run: the clusters the scout sees and what it would draft next (no AI). Real: drafts at most one cluster now
    (the given key, else the first due), up to a few minutes of AI calls."""
    from services.knowledge import scout_run

    if body.dry_run:
        try:
            return scout_run.plan(db)
        except Exception as exc:
            raise _snowflake_error(exc) from exc
    who = _who(db)
    if not ("AI.USE" in who["privileges"] or "*" in who["privileges"]):
        raise HTTPException(403, "Not allowed: drafting a proposed skill needs the AI.USE privilege.")
    try:
        db.query("SELECT 1 AS X FROM CORE.SKILL_PROPOSAL LIMIT 1")
    except Exception:
        raise HTTPException(409, "The skill scout needs the latest deploy (migration V067).") from None
    try:
        return scout_run.scan(db, limit=1, keys=[body.key] if body.key else None)
    except Exception as exc:
        raise _snowflake_error(exc) from exc


class DismissIn(BaseModel):
    note: str = Field(min_length=3, max_length=2000)


@router.post("/api/skill-proposals/{proposal_id}/dismiss")
def dismiss(proposal_id: str, body: DismissIn, db: Db = Depends(current_db)):
    """Say no to a proposal. Its DRAFT version stays in the registry's history; the scout proposes the theme again
    only once its evidence doubles."""
    who = _who(db)
    changed = db.execute_count("""UPDATE CORE.SKILL_PROPOSAL SET STATUS = 'DISMISSED', DISMISSED_BY = %s,
                                         DISMISSED_AT = CURRENT_TIMESTAMP(), DISMISS_NOTE = %s, UPDATED_AT = CURRENT_TIMESTAMP()
                                   WHERE PROPOSAL_ID = %s AND STATUS = 'PROPOSED'""", (who["user"], body.note, proposal_id))
    if changed != 1:
        raise HTTPException(409, "Only a proposed skill can be dismissed.")
    return get_proposal(proposal_id, db)
