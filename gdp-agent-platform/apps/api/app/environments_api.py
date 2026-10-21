"""Environment bindings (Admin, Environments): the logical names QA packs and Data Quality suite files use, mapped to
DATABASE or DATABASE.SCHEMA per environment (services/common/environments.py).

The setting is CORE.PLATFORM_CONFIG key ENVIRONMENT_BINDINGS: every save is a new version (CONFIG.EDIT, approved by a
PLATFORM_ADMIN under the default policy). The preview resolves a pack's queries with the bindings being edited, runs
the read-only guard and an EXPLAIN against the target with the caller's role, and stores nothing (ADMIN.VIEW).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import DATABASE, Db
from app.main import _config, _put_config, current_db
from app.qa_api import _call, _domain_id, _svc
from services.common import environments as envs

router = APIRouter()


def _state(db: Db) -> dict:
    value = _config(db, envs.CONFIG_KEY, None)
    cleaned, problems = envs.validate(value)
    try:
        history = db.query(
            """SELECT VERSION, IS_CURRENT, CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT, DESCRIPTION
                 FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s ORDER BY VERSION DESC LIMIT 20""", (envs.CONFIG_KEY,))
    except Exception:
        history = []
    return {"config": cleaned, "saved": value is not None, "problems": problems, "history": history,
            "platform_database": DATABASE, "reserved": sorted(envs.RESERVED)}


@router.get("/api/config/environments")
def get_environments(db: Db = Depends(current_db)):
    """The bindings of every environment, which one this deployment is, and the saved versions."""
    return _state(db)


class EnvironmentsIn(BaseModel):
    config: dict[str, Any]
    note: Optional[str] = Field(default=None, max_length=500)


@router.put("/api/config/environments")
def put_environments(body: EnvironmentsIn, db: Db = Depends(current_db)):
    """Save the bindings as a new version (validated; nothing is saved while problems remain)."""
    cleaned, problems = envs.validate(body.config)
    if problems:
        raise HTTPException(400, "; ".join(problems[:10]))
    _put_config(db, envs.CONFIG_KEY, cleaned, (body.note or "Environment bindings set from Admin")[:500])
    return _state(db)


class PreviewIn(BaseModel):
    config: Optional[dict[str, Any]] = None
    environment: Optional[str] = Field(default=None, max_length=32)
    pack_id: str = Field(min_length=36, max_length=36)
    domain_id: str = Field(min_length=1, max_length=128)
    version: Optional[int] = Field(default=None, ge=1)


@router.post("/api/config/environments/preview")
def preview_environments(body: PreviewIn, db: Db = Depends(current_db)):
    """A pack's queries resolved with these (unsaved) bindings for one environment: resolved SQL, guard result and
    EXPLAIN against the target. Nothing runs and nothing is stored."""
    if body.config is not None:
        _, problems = envs.validate(body.config)
        if problems:
            raise HTTPException(400, "; ".join(problems[:10]))
    detail = _call(db, _svc("packs").get_pack, body.pack_id, body.version)
    version = body.version or detail.get("approved_version") or detail["pack"]["version"]
    return _call(db, _svc("packs").dry_run, _domain_id(db, body.domain_id), body.pack_id, int(version),
                 body.environment, None, json.dumps(body.config) if body.config is not None else None)


@router.get("/api/qa/environments")
def qa_environments(db: Db = Depends(current_db)):
    """The environments a QA pack can run against (names and bindings, for the QA Store's run menu)."""
    cleaned, _ = envs.validate(_config(db, envs.CONFIG_KEY, None))
    return {"current": cleaned.get("current"),
            "environments": [{"name": e["name"], "description": e.get("description") or "",
                              "current": e["name"] == cleaned.get("current"), "bindings": e["bindings"]}
                             for e in cleaned["environments"]]}
