"""Governed removal of a registered target table or a source system (V068).

A removal never deletes anything: the row is deactivated with who removed it, when and why, and can be restored.
Every remove and restore waits for another person's approval (services.governance.policy STORE_ROUTES): the
request is queued, the approver sees the reason, and approving replays the call. Targets are governed by DOMAIN.EDIT
(approver DATA_STEWARD), sources by SOURCE.CONNECT (approver PLATFORM_ADMIN).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import current_db

router = APIRouter()


class RemoveIn(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)


class RestoreIn(BaseModel):
    reason: Optional[str] = Field(None, max_length=2000)


def _one(db: Db, sql: str, params: tuple) -> Optional[Dict[str, Any]]:
    found = db.query(sql, params)
    return dict(found[0]) if found else None


def _set(db: Db, table: str, key: str, row_id: str, active: bool, reason: Optional[str]) -> None:
    """Deactivate or reactivate one row; before V068 (no RETIRED_* columns) only the flag changes."""
    if active:
        sql = f"""UPDATE CORE.{table} SET ACTIVE_FLAG = TRUE, RETIRED_AT = NULL, RETIRED_BY = NULL, RETIRE_REASON = NULL
                   WHERE {key} = %s"""
        params: tuple = (row_id,)
    else:
        sql = f"""UPDATE CORE.{table} SET ACTIVE_FLAG = FALSE, RETIRED_AT = CURRENT_TIMESTAMP(), RETIRED_BY = CURRENT_USER(),
                         RETIRE_REASON = %s WHERE {key} = %s"""
        params = (reason, row_id)
    try:
        db.execute(sql, params)
    except Exception as exc:
        if "RETIRED_AT" not in str(exc).upper() and "invalid identifier" not in str(exc).lower():
            raise
        db.execute(f"UPDATE CORE.{table} SET ACTIVE_FLAG = %s WHERE {key} = %s", (active, row_id))


# ---------------------------------------------------------------- target tables

TARGET_SQL = """SELECT T.TARGET_TABLE_ID, T.TARGET_DATABASE, T.TARGET_SCHEMA, T.TARGET_TABLE, T.ACTIVE_FLAG, T.DOMAIN_ID,
                       D.DOMAIN_NAME FROM CORE.TARGET_TABLE_REGISTRY T
                  LEFT JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = T.DOMAIN_ID WHERE T.TARGET_TABLE_ID = %s"""


def _target(db: Db, target_id: str) -> Dict[str, Any]:
    row = _one(db, TARGET_SQL, (target_id,))
    if not row:
        raise HTTPException(404, "That table is not registered.")
    return row


@router.post("/api/targets/{target_id}/retire")
def retire_target(target_id: str, body: RemoveIn, db: Db = Depends(current_db)):
    """Remove a registered table from its domain (for example a test table): it stops appearing in the catalogue,
    mapping and modeling. Runs that already used it keep their history."""
    row = _target(db, target_id)
    if row.get("active_flag") is False:
        raise HTTPException(409, f"{row['target_table']} is already removed.")
    _set(db, "TARGET_TABLE_REGISTRY", "TARGET_TABLE_ID", target_id, False, body.reason.strip())
    _changed()
    return {"target_table_id": target_id, "table": row["target_table"], "domain": row.get("domain_name"),
            "removed": True}


@router.post("/api/targets/{target_id}/restore")
def restore_target(target_id: str, body: Optional[RestoreIn] = None, db: Db = Depends(current_db)):
    row = _target(db, target_id)
    if row.get("active_flag") is not False:
        raise HTTPException(409, f"{row['target_table']} is not removed.")
    _set(db, "TARGET_TABLE_REGISTRY", "TARGET_TABLE_ID", target_id, True, None)
    _changed()
    return {"target_table_id": target_id, "table": row["target_table"], "domain": row.get("domain_name"),
            "removed": False}


# ---------------------------------------------------------------- sources

SOURCE_SQL = """SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, SOURCE_TYPE, CONNECTION_TYPE, ACTIVE_FLAG
                  FROM CORE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = %s"""


def _source(db: Db, source_id: str) -> Dict[str, Any]:
    row = _one(db, SOURCE_SQL, (source_id,))
    if not row:
        raise HTTPException(404, "That source is not registered.")
    return row


@router.post("/api/sources/{source_id}/retire")
def retire_source(source_id: str, body: RemoveIn, db: Db = Depends(current_db)):
    """Remove a registered source system: it stops appearing in onboarding and the stores. Landed data and the runs
    that used it are kept. An Oracle source's extraction objects are removed separately (Remove Oracle setup)."""
    row = _source(db, source_id)
    if row.get("active_flag") is False:
        raise HTTPException(409, f"{row['source_system_name']} is already removed.")
    _set(db, "SOURCE_REGISTRY", "SOURCE_SYSTEM_ID", source_id, False, body.reason.strip())
    _changed()
    return {"source_system_id": source_id, "source": row["source_system_name"], "removed": True}


@router.post("/api/sources/{source_id}/restore")
def restore_source(source_id: str, body: Optional[RestoreIn] = None, db: Db = Depends(current_db)):
    row = _source(db, source_id)
    if row.get("active_flag") is not False:
        raise HTTPException(409, f"{row['source_system_name']} is not removed.")
    _set(db, "SOURCE_REGISTRY", "SOURCE_SYSTEM_ID", source_id, True, None)
    _changed()
    return {"source_system_id": source_id, "source": row["source_system_name"], "removed": False}


@router.get("/api/removals")
def list_removals(db: Db = Depends(current_db)):
    """What has been removed and can be restored: tables and sources, newest first (empty before V068)."""
    out: Dict[str, Any] = {"targets": [], "sources": []}
    try:
        out["targets"] = db.query("""SELECT T.TARGET_TABLE_ID, T.TARGET_TABLE, T.TARGET_DATABASE, T.TARGET_SCHEMA, D.DOMAIN_NAME,
                                            T.RETIRED_BY, T.RETIRED_AT, T.RETIRE_REASON
                                       FROM CORE.TARGET_TABLE_REGISTRY T LEFT JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = T.DOMAIN_ID
                                      WHERE NOT T.ACTIVE_FLAG AND T.RETIRED_AT IS NOT NULL ORDER BY T.RETIRED_AT DESC LIMIT 200""")
        out["sources"] = db.query("""SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, SOURCE_TYPE, RETIRED_BY, RETIRED_AT, RETIRE_REASON
                                       FROM CORE.SOURCE_REGISTRY WHERE NOT ACTIVE_FLAG AND RETIRED_AT IS NOT NULL
                                      ORDER BY RETIRED_AT DESC LIMIT 200""")
    except Exception:
        pass
    for key in out:
        out[key] = [{k: (str(v) if k == "retired_at" and v is not None else v) for k, v in r.items()} for r in out[key]]
    return out


def _changed() -> None:
    """Domain, catalogue and onboarding lists are cached in the API: drop them so the change shows at once."""
    try:
        from app.main import _domain_caches_changed

        _domain_caches_changed()
    except Exception:
        pass
