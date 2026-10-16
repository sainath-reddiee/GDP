"""Snowflake procedure handlers for the resolution engine (snowflake/procedures/resolve.sql). The ops worker calls
SP_CLAIM_AND_RESOLVE and SP_RESUME_APPROVED; the decisions run here, inside Snowflake."""

from __future__ import annotations

from typing import Any, Dict

from services.ops.sqlio import as_db
from services.resolve import engine
from services.resolve.fixes import Executor
from services.resolve.rca import Analyst


def _parts(session: Any):
    db = as_db(session)
    database = engine.database_of(db)
    prefs = engine.load_preference(db)
    agent_on = prefs.get("rca_mode", "AGENT") == "AGENT"
    return db, Executor(db, database, use_agent=agent_on), Analyst(db, database)


def claim_and_resolve_entry(session: Any, holder: str, limit: Any = None) -> Dict[str, Any]:
    """CALL CORE.SP_CLAIM_AND_RESOLVE('<holder>', <limit>)."""
    db, executor, analyst = _parts(session)
    return engine.claim_and_resolve(db, str(holder or "worker")[:256], executor, analyst,
                                    limit=int(limit) if limit not in (None, "") else None)


def resume_approved_entry(session: Any, holder: str) -> Dict[str, Any]:
    """CALL CORE.SP_RESUME_APPROVED('<holder>')."""
    db, executor, analyst = _parts(session)
    return engine.resume_approved(db, executor, analyst)


def publish_entry(session: Any, issue_id: str, actor: str) -> Dict[str, Any]:
    """CALL CORE.SP_PUBLISH_KNOWN_ISSUE('<issue id>', '<reviewer>')."""
    from services.resolve import publish

    return publish.publish(as_db(session), issue_id, str(actor or ""))
