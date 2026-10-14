"""Pipeline summaries and run acceleration (services/workflow/accelerator.py), read only.

GET /api/pipelines/{SILVER|GOLD}/summary   in flight, waiting at a gate, completed in 30 days, median cycle time and the
                                           share of mapping proposals accepted as generated (last 90 days)
GET /api/runs/{id}/accelerator             what the platform generated and what people changed, time per stage, and
                                           which human gates passed first time
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends

from app.db import Db
from app.main import current_db
from services.workflow import accelerator as acc

router = APIRouter()
GATES = "('MAPPING_REVIEW', 'STTM_REVIEW', 'SODA_REVIEW', 'DBT_REVIEW')"
_STAGE_OF: dict = {}


def _stage_of() -> dict:
    if not _STAGE_OF:
        graph = json.loads((Path(__file__).resolve().parents[3] / "snowflake/database/seed/workflow_graph.json").read_text(encoding="utf-8"))
        _STAGE_OF.update({s["state"]: s.get("stage") or "" for s in graph["states"]})
    return _STAGE_OF


def _scope(alias: str = "R") -> str:
    # top-level runs of the pipeline (a Gold run's table runs fold into it), live ones only
    # the same runs the pipeline's list shows by default: test-environment runs are left out
    return (f"COALESCE({alias}.PIPELINE, 'SILVER') = %s AND {alias}.PARENT_RUN_ID IS NULL "
            f"AND {alias}.DELETED_AT IS NULL AND NOT COALESCE({alias}.IS_ARCHIVED, FALSE) "
            f"AND COALESCE({alias}.ENVIRONMENT, '') <> 'TEST'")


@router.get("/api/pipelines/{pipeline}/summary")
def pipeline_summary(pipeline: Literal["SILVER", "GOLD"], db: Db = Depends(current_db)):
    try:
        row = db.query(f"""SELECT COUNT_IF(R.CURRENT_STATE NOT IN ('COMPLETED', 'CANCELLED')) AS IN_FLIGHT,
                                  COUNT_IF(R.CURRENT_STATE IN {GATES}) AS AT_GATE,
                                  COUNT_IF(R.CURRENT_STATE = 'COMPLETED' AND R.UPDATED_AT >= DATEADD(day, -30, CURRENT_TIMESTAMP())) AS COMPLETED_30D,
                                  MEDIAN(IFF(R.CURRENT_STATE = 'COMPLETED' AND R.UPDATED_AT >= DATEADD(day, -90, CURRENT_TIMESTAMP()),
                                             DATEDIFF('minute', R.CREATED_AT, R.UPDATED_AT), NULL)) AS MEDIAN_CYCLE_MINUTES
                             FROM CORE.WORKFLOW_RUN R WHERE {_scope()}""", (pipeline,))[0]
        decisions = db.query(f"""SELECT D.DECISION FROM MAPPING.MAPPING_DECISION D JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = D.RUN_ID
                                  WHERE D.IS_CURRENT AND D.REVIEWED_AT >= DATEADD(day, -90, CURRENT_TIMESTAMP())
                                    AND COALESCE(R.PIPELINE, 'SILVER') = %s AND R.DELETED_AT IS NULL""", (pipeline,))
    except Exception as exc:
        if "invalid identifier" in str(exc).lower():   # before V054/V055
            return {"pipeline": pipeline, "deployed": False}
        raise
    minutes = row.get("median_cycle_minutes")
    return {"pipeline": pipeline, "deployed": True, "in_flight": int(row.get("in_flight") or 0),
            "at_gate": int(row.get("at_gate") or 0), "completed_30d": int(row.get("completed_30d") or 0),
            "median_cycle_hours": round(float(minutes) / 60, 1) if minutes is not None else None,
            "generated_share": acc.generated_share(d["decision"] for d in decisions), "decisions": len(decisions)}


@router.get("/api/runs/{run_id}/accelerator")
def run_accelerator(run_id: str, db: Db = Depends(current_db)):
    events = db.query("""SELECT FROM_STATE, TO_STATE, ACTOR_TYPE, CREATED_AT FROM CORE.WORKFLOW_EVENT
                          WHERE RUN_ID = %s ORDER BY CREATED_AT, EVENT_ID""", (run_id,))
    decisions = db.query("SELECT DECISION FROM MAPPING.MAPPING_DECISION WHERE RUN_ID = %s AND IS_CURRENT", (run_id,))
    try:
        soda = db.query("""SELECT STATUS, COUNT(*) AS N FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                            WHERE RUN_ID = %s AND IS_CURRENT GROUP BY STATUS""", (run_id,))
    except Exception:
        soda = []
    by = {str(d["decision"]).upper(): 0 for d in decisions}
    for d in decisions:
        by[str(d["decision"]).upper()] += 1
    stages = acc.stage_times(events, _stage_of())
    return {"run_id": run_id,
            "mapping": {"decided": len(decisions), "accepted": by.get("APPROVED", 0),
                        "changed": by.get("MODIFIED", 0) + by.get("ALTERNATIVE_TARGET", 0), "rejected": by.get("REJECTED", 0),
                        "generated_share": acc.generated_share(d["decision"] for d in decisions)},
            "checks": {s["status"].lower(): int(s["n"]) for s in soda},
            "stages": stages, "gates": acc.gates(events),
            "automatic_minutes": round(sum(s["minutes"] or 0 for s in stages if not s["human_gate"]), 1),
            "automatic_steps": sum(1 for e in events if str(e.get("actor_type")).upper() == "SYSTEM")}
