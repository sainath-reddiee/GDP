"""Permanent fixes (services.resolve.permanent): the code change that removes the cause of a known fix that keeps firing.

Governance (services.governance.policy): reads need OPS.VIEW; asking for a draft OPS.OPERATE (and AI.USE, checked
here); accepting or rejecting OPS.FIX_PUBLISH, never the person who drafted it (four eyes); opening the pull request
DBT.EDIT, after a dry run by the same person; retiring or keeping the known fix after the merge OPS.FIX_PUBLISH. Calls
run on the caller's session; the pull request itself is opened by the publishing procedure.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import Db
from app.main import current_db
from app.ops_api import _iso
from services.resolve import permanent

router = APIRouter()
MANUAL_DRAFTS_PER_HOUR = 10


def _who(db: Db) -> Dict[str, Any]:
    from app.cases_api import _who as who

    return who(db)


def _need(who: Dict[str, Any], privilege: str, what: str) -> None:
    if not (privilege in who["privileges"] or "*" in who["privileges"]):
        raise HTTPException(403, f"Not allowed: {what} needs the {privilege} privilege.")


def _out(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return None if row is None else {k: _iso(v) for k, v in dict(row).items()}


def _call(fn):
    from app.main import _snowflake_error

    try:
        return fn()
    except permanent.PermanentFixError as exc:
        raise HTTPException(exc.status, exc.message) from None
    except HTTPException:
        raise
    except AssertionError as exc:
        raise HTTPException(502, str(exc)[:300]) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


def _detail(db: Db, fix: Dict[str, Any], who: Dict[str, Any]) -> Dict[str, Any]:
    from services.resolve.engine import get_issue

    issue = get_issue(db, fix["issue_id"]) or {}
    drafter = str(fix.get("drafted_by") or "").upper() == str(who["user"] or "").upper()
    return {**_out(fix), "issue": {"issue_id": fix["issue_id"], "title": issue.get("title"), "status": issue.get("status"),
                                   "fix_type": issue.get("fix_type")},
            "you_drafted": drafter, "branch_name": permanent.branch_name(fix)}


@router.get("/api/ops/permanent-fixes")
def list_fixes(status: Optional[str] = Query(None, max_length=16), issue_id: Optional[str] = Query(None, max_length=36),
               limit: int = Query(100, ge=1, le=500), db: Db = Depends(current_db)):
    try:
        found = permanent.list_fixes(db, status, issue_id, limit)
    except Exception:   # before V066
        return {"items": [], "available": False}
    return {"items": [_out(r) for r in found], "available": True}


@router.get("/api/ops/permanent-fixes/{fix_id}")
def get_fix(fix_id: str, db: Db = Depends(current_db)):
    fix = _call(lambda: permanent.get(db, fix_id))
    if not fix:
        raise HTTPException(404, "permanent fix not found")
    return _detail(db, fix, _who(db))


class DraftIn(BaseModel):
    instructions: Optional[str] = Field(None, max_length=2000)
    supersede: bool = False


@router.post("/api/ops/permanent-fixes/draft/{issue_id}")
def draft_fix(issue_id: str, body: Optional[DraftIn] = None, db: Db = Depends(current_db)):
    """Draft the permanent fix of a published known fix (an AI call, up to a minute). 409 while one is open, unless
    supersede."""
    who = _who(db)
    _need(who, "AI.USE", "drafting a permanent fix")
    body = body or DraftIn()
    recent = _call(lambda: db.query("""SELECT COUNT(*) AS N FROM CORE.PERMANENT_FIX WHERE DRAFTED_BY = %s
                                        AND CREATED_AT >= DATEADD(hour, -1, CURRENT_TIMESTAMP())""", (who["user"],)))
    if int((recent[0].get("n") if recent else 0) or 0) >= MANUAL_DRAFTS_PER_HOUR:
        raise HTTPException(429, f"At most {MANUAL_DRAFTS_PER_HOUR} permanent fix drafts per hour.",
                            headers={"Retry-After": "600"})
    fix = _call(lambda: permanent.draft(db, issue_id, who["user"], "MANUAL", body.instructions, body.supersede))
    return _detail(db, fix, who)


class DecideIn(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/api/ops/permanent-fixes/{fix_id}/accept")
def accept_fix(fix_id: str, body: Optional[DecideIn] = None, db: Db = Depends(current_db)):
    who = _who(db)
    fix = _call(lambda: permanent.decide(db, fix_id, who["user"], True, (body or DecideIn()).note))
    return _detail(db, fix, who)


@router.post("/api/ops/permanent-fixes/{fix_id}/reject")
def reject_fix(fix_id: str, body: DecideIn, db: Db = Depends(current_db)):
    who = _who(db)
    fix = _call(lambda: permanent.decide(db, fix_id, who["user"], False, body.note))
    return _detail(db, fix, who)


class PublishIn(BaseModel):
    dry_run: bool = True
    preview_token: Optional[str] = Field(None, max_length=4000)


@router.post("/api/ops/permanent-fixes/{fix_id}/publish")
def publish_fix(fix_id: str, body: PublishIn, db: Db = Depends(current_db),
                x_aip_replay: Optional[str] = Header(default=None)):
    """A dry run returns {branch, base_branch, repo, files, diff, preview_token}; the real publish ({dry_run: false,
    preview_token}) opens a draft pull request on fix/known-issue-<n> and returns the publisher's result."""
    from app.governance import _valid_replay
    from app.main import _publish_dbt

    who = _who(db)
    replay = bool(_valid_replay(x_aip_replay))
    result = _call(lambda: permanent.publish(db, fix_id, who["user"], body.dry_run, body.preview_token, replay=replay,
                                             publisher=lambda payload: _publish_dbt(db, "", payload)))
    if body.dry_run:
        return result
    fix = permanent.get(db, fix_id) or {}
    return {"result": result, "fix": _detail(db, fix, who) if fix else None}


class KnownFixIn(BaseModel):
    retire: bool


@router.post("/api/ops/permanent-fixes/{fix_id}/known-fix")
def known_fix(fix_id: str, body: KnownFixIn, db: Db = Depends(current_db)):
    """After the merge: retire the known fix (the cause is gone) or keep it."""
    who = _who(db)
    fix = _call(lambda: permanent.known_fix_decision(db, fix_id, who["user"], body.retire))
    return _detail(db, fix, who)
