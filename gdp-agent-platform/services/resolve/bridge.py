"""The worker's side of the resolution engine.

The decisions run in Snowflake procedures; the worker triggers them (no Snowflake tasks) and does the one step that
needs AWS credentials Snowflake does not hold: clearing the Airflow task after a successful fix (Airflow REST
clearTaskInstances, through services.ops.mwaa). The Jira step is the existing outbox (kind 'resolution').
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

from services.ops.redact import redact

log = logging.getLogger("gdp.resolve.bridge")
CLEAR_LIMIT = 20


def _call(db: Any, sql: str, params: tuple) -> Dict[str, Any]:
    """A procedure's VARIANT result as a dict (Db.call in the API and worker; a plain query elsewhere)."""
    if hasattr(db, "call"):
        value = db.call(sql, params)
    else:
        rows = db.query(sql, params)
        value = next(iter(rows[0].values()), None) if rows else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {"raw": value}
    return value if isinstance(value, dict) else {"raw": value}


def trigger(db: Any, holder: str) -> Dict[str, Any]:
    """CALL CORE.SP_CLAIM_AND_RESOLVE (it reads CONTROL_PREFERENCE first: with auto-resolution off it claims nothing)."""
    return _call(db, "CALL CORE.SP_CLAIM_AND_RESOLVE(%s, NULL)", (holder,))


def resume(db: Any, holder: str) -> Dict[str, Any]:
    """CALL CORE.SP_RESUME_APPROVED: run approved fixes, close rejected ones."""
    return _call(db, "CALL CORE.SP_RESUME_APPROVED(%s)", (holder,))


def has_work(db: Any) -> Dict[str, bool]:
    """Skip the procedure calls (and their warehouse time) when nothing waits."""
    found = db.query("""SELECT
          (SELECT COUNT(*) FROM CORE.FAILURE_EVENT WHERE STATUS = 'IN_PROGRESS' OR (STATUS = 'PENDING'
              AND (EXCEPTION_CLASS IS NOT NULL OR CREATED_AT < DATEADD(second, -120, CURRENT_TIMESTAMP())))) AS PENDING,
          (SELECT COUNT(*) FROM CORE.APPROVAL_REQUEST A JOIN CORE.FAILURE_EVENT F ON F.FAILURE_ID = A.FAILURE_ID
            WHERE A.STATUS IN ('APPROVED', 'REJECTED') AND F.STATUS = 'AWAITING_APPROVAL') AS DECIDED,
          (SELECT COUNT(*) FROM CORE.FAILURE_EVENT WHERE CLEAR_STATE = 'REQUESTED') AS CLEARS,
          (SELECT COUNT(*) FROM CORE.CONTROL_PREFERENCE WHERE IS_ACTIVE AND AUTO_RESOLVE_ENABLED) AS ENABLED""")
    r = found[0] if found else {}
    return {"resolve": int(r.get("pending") or 0) > 0 and int(r.get("enabled") or 0) > 0,
            "resume": int(r.get("decided") or 0) > 0, "clear": int(r.get("clears") or 0) > 0}


def clear_requested(db: Any, mwaa_factory: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Dict[str, int]:
    """Clear the Airflow task of every fixed failure that asks for it, so Airflow runs it again; the incident then
    resolves itself when the rerun succeeds. Each row is taken with a compare-and-set, so it is cleared once."""
    from services.ops.incidents import SqlStore

    counts = {"cleared": 0, "failed": 0}
    rows = db.query(f"""SELECT F.FAILURE_ID, F.ENV_ID, F.DAG_ID, F.TASK_ID, F.RUN_ID, F.INCIDENT_ID, E.KIND, E.MWAA_ENV, E.REGION, E.AIRFLOW_URL
                          FROM CORE.FAILURE_EVENT F JOIN CORE.AIRFLOW_ENV E ON E.ENV_ID = F.ENV_ID
                         WHERE F.CLEAR_STATE = 'REQUESTED' ORDER BY F.UPDATED_AT LIMIT {CLEAR_LIMIT}""")
    for r in rows:
        if db.execute_count("""UPDATE CORE.FAILURE_EVENT SET CLEAR_STATE = 'CLEARING' WHERE FAILURE_ID = %s
                                 AND CLEAR_STATE = 'REQUESTED'""", (r["failure_id"],)) != 1:
            continue
        try:
            if mwaa_factory:
                client = mwaa_factory(r)
            else:
                from services.ops.mwaa import client_for

                client = client_for(r)
            client.clear_task_instances(r["dag_id"], [r["task_id"]], r["run_id"], dry_run=False, include_downstream=False)
            state, detail = "DONE", f"cleared {r['task_id']} in run {r['run_id']}"
            counts["cleared"] += 1
        except Exception as exc:
            state, detail = "FAILED", redact(f"{type(exc).__name__}: {exc}")[:1000]
            counts["failed"] += 1
        db.execute("""UPDATE CORE.FAILURE_EVENT SET CLEAR_STATE = %s, CLEAR_DETAIL = %s, UPDATED_AT = CURRENT_TIMESTAMP()
                       WHERE FAILURE_ID = %s""", (state, detail, r["failure_id"]))
        if r.get("incident_id"):
            try:
                SqlStore(db).event(r["incident_id"], "retried" if state == "DONE" else "retry_failed", "system",
                                   {"tasks": [r["task_id"]], "run_id": r["run_id"], "reason": "known fix applied",
                                    **({"error": detail} if state == "FAILED" else {})})
            except Exception as exc:
                log.warning("timeline event for %s not written: %s", r["incident_id"], exc)
    return counts
