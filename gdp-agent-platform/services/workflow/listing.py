"""Query builders for the run list, the audit trail, cost and dashboard metrics (pure, %s binds).

Every filter is a bind, never interpolated text; the only interpolated parts are constant SQL chosen from fixed maps.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

MAX_RUNS = 200
# Same set the run pages treat as "needs review" (apps/web/lib/run-insights.ts REVIEW_STATES).
REVIEW_STATES = ("MAPPING_REVIEW", "STTM_REVIEW", "SODA_REVIEW", "DBT_REVIEW", "VALIDATION_FAILED")
_REVIEW_SQL = ", ".join(f"'{s}'" for s in REVIEW_STATES)
MAX_AUDIT = 500
COST_GROUPS = {
    "stage": "C.STAGE",
    "model": "COALESCE(C.MODEL, 'unknown')",
    "run": "C.RUN_ID",
    "day": "TO_VARCHAR(DATE_TRUNC('day', C.CREATED_AT), 'YYYY-MM-DD')",
}
SORTS = {"newest": "R.CREATED_AT DESC", "oldest": "R.CREATED_AT ASC", "updated": "R.UPDATED_AT DESC NULLS LAST",
         "name": "R.RUN_NAME ASC"}


def runs_where(predicate: str, include_test: bool, q: Optional[str], domain_id: Optional[str],
               stage: Optional[str], needs_review: bool, tag: Optional[str] = None) -> Tuple[str, List[Any]]:
    where = ["R.DELETED_AT IS NULL", "(R.ENVIRONMENT <> 'TEST' OR %s)", predicate]
    params: List[Any] = [bool(include_test)]
    if q and q.strip():
        like = f"%{q.strip()[:200]}%"
        where.append("(R.RUN_NAME ILIKE %s OR R.TARGET_MODEL ILIKE %s OR S.SOURCE_SYSTEM_NAME ILIKE %s "
                     "OR D.DOMAIN_NAME ILIKE %s OR R.CREATED_BY ILIKE %s)")
        params += [like] * 5
    if domain_id:
        where.append("R.DOMAIN_ID = %s"); params.append(domain_id)
    if stage:
        where.append("R.CURRENT_STAGE = %s"); params.append(stage.upper())
    if needs_review:
        where.append(f"R.CURRENT_STATE IN ({_REVIEW_SQL}) AND NOT COALESCE(R.IS_ARCHIVED, FALSE)")
    if tag:
        where.append("EXISTS (SELECT 1 FROM CORE.TAG_ASSIGNMENT T WHERE T.ENTITY_TYPE = 'RUN' "
                     "AND T.ENTITY_KEY = R.RUN_ID AND T.TAG = %s)")
        params.append(tag)
    return " AND ".join(where), params


def page(offset: Any, limit: Any, cap: int) -> Tuple[int, int]:
    return max(0, int(offset or 0)), max(1, min(int(limit or 50), cap))


def audit_where(run_id: Optional[str], actor_type: Optional[str], to_state: Optional[str], since: Optional[str],
                until: Optional[str], q: Optional[str]) -> Tuple[str, List[Any]]:
    where, params = ["1 = 1"], []
    if run_id:
        where.append("E.RUN_ID = %s"); params.append(run_id)
    if actor_type:
        assert actor_type.upper() in ("HUMAN", "SYSTEM", "AGENT"), "actor type must be HUMAN, SYSTEM or AGENT"
        where.append("E.ACTOR_TYPE = %s"); params.append(actor_type.upper())
    if to_state:
        where.append("E.TO_STATE = %s"); params.append(to_state.upper())
    if since:
        where.append("E.CREATED_AT >= %s::TIMESTAMP_LTZ"); params.append(since)
    if until:
        where.append("E.CREATED_AT < DATEADD('day', 1, %s::DATE)"); params.append(until)
    if q and q.strip():
        like = f"%{q.strip()[:200]}%"
        where.append("(R.RUN_NAME ILIKE %s OR E.REASON ILIKE %s OR E.ACTOR ILIKE %s)")
        params += [like] * 3
    return " AND ".join(where), params


def cost_query(group_by: str, since: Optional[str], until: Optional[str], limit: int = 50) -> Tuple[str, List[Any]]:
    assert group_by in COST_GROUPS, f"group_by must be one of {sorted(COST_GROUPS)}"
    key = COST_GROUPS[group_by]
    where, params = ["1 = 1"], []
    if since:
        where.append("C.CREATED_AT >= %s::TIMESTAMP_LTZ"); params.append(since)
    if until:
        where.append("C.CREATED_AT < DATEADD('day', 1, %s::DATE)"); params.append(until)
    run_name = ", MAX(R.RUN_NAME) AS RUN_NAME" if group_by == "run" else ""
    join = " LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = C.RUN_ID" if group_by == "run" else ""
    order = "KEY ASC" if group_by == "day" else "CREDITS DESC, TOTAL_TOKENS DESC"
    sql = (f"""SELECT {key} AS KEY{run_name}, COUNT(*) AS CALLS, SUM(C.INPUT_TOKENS) AS INPUT_TOKENS,
                     SUM(C.OUTPUT_TOKENS) AS OUTPUT_TOKENS, SUM(C.TOTAL_TOKENS) AS TOTAL_TOKENS,
                     SUM(COALESCE(C.ACTUAL_CREDITS, C.ESTIMATED_COST, 0)) AS CREDITS,
                     SUM(COALESCE(C.ACTUAL_CREDITS, C.ESTIMATED_COST, 0)) AS ESTIMATED_COST,
                     SUM(C.ACTUAL_CREDITS) AS ACTUAL_CREDITS,
                     SUM(IFF(C.ACTUAL_CREDITS IS NULL, C.ESTIMATED_COST, 0)) AS ESTIMATED_CREDITS,
                     COUNT_IF(C.ACTUAL_CREDITS IS NOT NULL) AS ACTUAL_CALLS,
                     SUM(C.DURATION_MS) AS DURATION_MS
                FROM AUDIT.COST_USAGE C{join}
               WHERE {' AND '.join(where)}
               GROUP BY {key}
               ORDER BY {order}
               LIMIT {max(1, min(int(limit), 400))}""")
    return sql, params


def summarise(rows: List[Dict[str, Any]], lifecycle) -> Dict[str, Any]:
    """Dashboard counts from (current_state, current_stage, status, is_archived, n) groups."""
    out: Dict[str, Any] = {"total": 0, "lifecycle": {}, "by_stage": {}, "needs_review": 0, "failed": 0,
                           "cancelled": 0, "archived": 0}
    for r in rows:
        get = (lambda k: r.get(k, r.get(k.upper())))
        n = int(get("n") or 0)
        archived = bool(get("is_archived"))
        life = lifecycle(get("current_state"), archived)
        out["lifecycle"][life] = out["lifecycle"].get(life, 0) + n
        if archived:
            out["archived"] += n
            continue
        out["total"] += n
        state = str(get("current_state") or "")
        if life == "RUNNING":
            stage = get("current_stage") or "SOURCE"
            stage = "PROFILING" if stage == "DOMAIN" else stage
            out["by_stage"][stage] = out["by_stage"].get(stage, 0) + n
        if state in REVIEW_STATES:
            out["needs_review"] += n
        if state == "CANCELLED":
            out["cancelled"] += n
        elif life == "FAILED":
            out["failed"] += n
    return out
