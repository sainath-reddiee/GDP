"""Source control (V043): every pull request the app opened for a governed asset (domain models, data quality suites,
QA packs, STTM mappings), with filters, and one action that reads their state back from GitHub.

The rows are written by the publishers (services/dbt/publish.py through services/git/roundtrip.py); the sync runs in
CODEGEN.PUBLISH_DBT_PR, which holds the GitHub token, so the token never reaches the API.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import Db
from app.main import _snowflake_error, current_db
from services.git import roundtrip

router = APIRouter()


@router.get("/api/source-control/prs")
def list_prs(kind: Optional[str] = None, state: Optional[str] = None, domain: Optional[str] = None,
             q: Optional[str] = None, asset: Optional[str] = None, db: Db = Depends(current_db)):
    from app.main import _dbt_repos, _github_publisher_ready

    try:
        sql, params = roundtrip.list_sql(kind or None, state or None, domain or None, q, asset or None)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        found = [roundtrip.shape(r) for r in db.query(sql, params)]
        everything = found if asset or not (kind or state or domain or q) else [roundtrip.shape(r) for r in db.query(*roundtrip.list_sql())]
    except Exception:  # before V043
        return {"prs": [], "counts": roundtrip.counts([]), "deployed": False, "publisher_ready": False, "repos": []}
    repos = [{k: r.get(k) for k in ("repo_id", "name", "git_url", "branch")} for r in _dbt_repos(db, None)
             if (r.get("provider") or "") == "GITHUB"]
    return {"prs": found, "counts": roundtrip.counts(everything), "deployed": True,
            "publisher_ready": _github_publisher_ready(db), "repos": repos,
            "kinds": {k: v["label"] for k, v in roundtrip.KINDS.items()}}


class SyncIn(BaseModel):
    kind: Optional[Literal["MODEL", "DQ_SUITE", "QA_PACK", "STTM"]] = None
    force: bool = True


@router.post("/api/source-control/sync")
def sync_prs(body: SyncIn, db: Db = Depends(current_db)):
    """Check every open pull request (or one kind's) on GitHub now."""
    from app.main import _github_publisher_ready

    if not _github_publisher_ready(db):
        return {"checked": 0, "merged": [], "closed": [], "open": 0, "errors": [], "detail": "GitHub publishing is not set up"}
    request = {"force": body.force, **({"kind": body.kind} if body.kind else {})}
    try:
        return db.call("CALL CODEGEN.PUBLISH_DBT_PR(%s, %s)", ("", json.dumps({"sync_git_prs": request}))) or {}
    except Exception as exc:
        raise _snowflake_error(exc) from exc
