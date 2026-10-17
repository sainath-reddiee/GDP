"""Airflow failure resolution (services.resolve): the resolution queue and its audit, fix approvals, the known-fix
catalogue with multi-step fixes, and the guardrails (control preference, pipeline scopes, approval routes).

Governance (services.governance.policy): reads need OPS.VIEW; drafts OPS.OPERATE; publishing and retiring a fix
OPS.FIX_PUBLISH (never the author: four eyes); approving or rejecting a fix run OPS.FIX_APPROVE (the routed approver,
or any holder when none is routed); the guardrails OPS.RESOLVE_ADMIN. Calls run on the caller's session.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import Db
from app.main import current_db
from app.ops_api import _iso
from services.resolve import engine, publish, rules

router = APIRouter()
LIST_LIMIT = 200


# ---------------------------------------------------------------- plumbing

def _who(db: Db) -> Dict[str, Any]:
    from app.cases_api import _who as who

    return who(db)


def _holds(db: Db, privilege: str) -> bool:
    privs = _who(db)["privileges"]
    return privilege in privs or "*" in privs


def _out(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = {}
    for k, v in dict(row).items():
        if isinstance(v, str) and k in ("rca", "detail", "steps", "output") and v[:1] in ("{", "["):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = _iso(v)
    return out


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc).strip("'\""))
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(409 if isinstance(exc, ValueError) else 500, str(exc))


# ---------------------------------------------------------------- queue and audit

@router.get("/api/ops/resolve/summary")
def summary(db: Db = Depends(current_db)):
    prefs = engine.load_preference(db)
    counts = {r["status"]: int(r["n"]) for r in db.query(
        "SELECT STATUS, COUNT(*) AS N FROM CORE.FAILURE_EVENT WHERE CREATED_AT >= DATEADD(day, -30, CURRENT_TIMESTAMP()) "
        "GROUP BY STATUS")}
    fixes = {r["status"]: int(r["n"]) for r in db.query("SELECT STATUS, COUNT(*) AS N FROM CORE.KNOWN_ISSUE GROUP BY STATUS")}
    me = str(db.user or "").upper()
    approvals = db.query("""SELECT COUNT(*) AS N, COUNT_IF(UPPER(APPROVER) = %s OR APPROVER IS NULL) AS MINE
                              FROM CORE.APPROVAL_REQUEST WHERE STATUS = 'OPEN'""", (me,))
    a = approvals[0] if approvals else {}
    runs = {r["outcome"]: int(r["n"]) for r in db.query(
        "SELECT OUTCOME, COUNT(*) AS N FROM CORE.RESOLUTION_RUN WHERE STARTED_AT >= DATEADD(day, -30, CURRENT_TIMESTAMP()) "
        "GROUP BY OUTCOME")}
    return {"enabled": prefs["auto_resolve_enabled"], "preference": prefs, "statuses": counts, "fixes": fixes,
            "approvals_open": int(a.get("n") or 0), "approvals_mine": int(a.get("mine") or 0), "runs": runs,
            "recurring": [_out(r) for r in publish.recurring(db, limit=5)]}


@router.get("/api/ops/resolve/failures")
def list_failures(status: Optional[str] = None, dag: Optional[str] = None, incident_id: Optional[str] = None,
                  limit: int = Query(100, ge=1, le=LIST_LIMIT), db: Db = Depends(current_db)):
    where, params = ["1 = 1"], []
    if status:
        wanted = [s.strip().upper() for s in status.split(",") if s.strip()]
        bad = [s for s in wanted if s not in rules.STATUSES]
        if bad:
            raise HTTPException(422, f"status must be among {', '.join(rules.STATUSES)}")
        where.append(f"F.STATUS IN ({', '.join(['%s'] * len(wanted))})")
        params += wanted
    if dag:
        where.append("F.DAG_ID ILIKE %s")
        params.append(f"%{dag}%")
    if incident_id:
        where.append("F.INCIDENT_ID = %s")
        params.append(incident_id)
    rows = db.query(f"""SELECT F.FAILURE_ID, F.ENV_ID, F.INCIDENT_ID, F.DAG_ID, F.TASK_ID, F.RUN_ID, F.TRY_NUMBER,
                               F.EXCEPTION_CLASS, F.EXCEPTION_TEXT, F.STATUS, F.FLOW_MODE, F.ISSUE_ID, F.SEARCH_SCORE,
                               F.TICKET_ID, F.CLEAR_STATE, F.DETAIL, F.CREATED_AT, F.UPDATED_AT, K.TITLE AS ISSUE_TITLE,
                               I.SEVERITY, I.JIRA_KEY
                          FROM CORE.FAILURE_EVENT F
                          LEFT JOIN CORE.KNOWN_ISSUE K ON K.ISSUE_ID = F.ISSUE_ID
                          LEFT JOIN CORE.INCIDENT I ON I.INCIDENT_ID = F.INCIDENT_ID
                         WHERE {' AND '.join(where)} ORDER BY F.CREATED_AT DESC LIMIT {int(limit)}""", tuple(params))
    return [_out(r) for r in rows]


@router.get("/api/ops/resolve/failures/{failure_id}")
def failure_detail(failure_id: str, db: Db = Depends(current_db)):
    failure = engine.get_failure(db, failure_id)
    if not failure:
        raise HTTPException(404, f"failure {failure_id} not found")
    runs = db.query("""SELECT RESOLUTION_ID, ISSUE_ID, FIX_TYPE, FIX_NAME, OUTCOME, DETAIL, ACTOR, STARTED_AT, FINISHED_AT
                         FROM CORE.RESOLUTION_RUN WHERE FAILURE_ID = %s ORDER BY STARTED_AT""", (failure_id,))
    steps = db.query("""SELECT S.RESOLUTION_ID, S.STEP_NO, S.ACTION, S.STATUS, S.OUTPUT, S.STARTED_AT, S.FINISHED_AT
                          FROM CORE.RESOLUTION_STEP S JOIN CORE.RESOLUTION_RUN R ON R.RESOLUTION_ID = S.RESOLUTION_ID
                         WHERE R.FAILURE_ID = %s ORDER BY S.RESOLUTION_ID, S.STEP_NO""", (failure_id,))
    approvals = db.query("""SELECT APPROVAL_ID, ISSUE_ID, FIX_TYPE, FIX_NAME, STATUS, APPROVER, DECIDED_BY, DECISION_NOTE,
                                   REQUESTED_AT, DECIDED_AT, KIND FROM CORE.APPROVAL_REQUEST WHERE FAILURE_ID = %s
                             ORDER BY REQUESTED_AT""", (failure_id,))
    issue = engine.get_issue(db, failure["issue_id"]) if failure.get("issue_id") else None
    proposal = (failure.get("rca") or {}).get("proposal") if isinstance(failure.get("rca"), dict) else None
    if not issue and proposal and proposal.get("issue_id"):
        issue = engine.get_issue(db, proposal["issue_id"])
    return {"failure": _out(failure), "issue": _out(issue), "runs": [_out(r) for r in runs],
            "steps": [_out(s) for s in steps], "approvals": [_out(a) for a in approvals]}


# ---------------------------------------------------------------- approvals

class DecisionIn(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)


@router.get("/api/ops/resolve/approvals")
def list_approvals(scope: str = Query("open", pattern="^(open|mine|all)$"), db: Db = Depends(current_db)):
    where, params = ["1 = 1"], []
    if scope in ("open", "mine"):
        where.append("A.STATUS = 'OPEN'")
    if scope == "mine":
        where.append("(UPPER(A.APPROVER) = %s OR A.APPROVER IS NULL)")
        params.append(str(db.user or "").upper())
    rows = db.query(f"""SELECT A.APPROVAL_ID, A.FAILURE_ID, A.ISSUE_ID, A.FIX_TYPE, A.FIX_NAME, A.STATUS, A.APPROVER,
                               A.DECIDED_BY, A.DECISION_NOTE, A.REQUESTED_AT, A.DECIDED_AT, A.KIND, K.TITLE AS ISSUE_TITLE,
                               K.PROPOSED_BY_AGENT, K.RATIONALE, S.STEPS, F.RCA:cause::VARCHAR AS CAUSE,
                               F.DAG_ID, F.TASK_ID, F.RUN_ID, F.INCIDENT_ID, F.EXCEPTION_CLASS, F.SEARCH_SCORE
                          FROM CORE.APPROVAL_REQUEST A
                          JOIN CORE.FAILURE_EVENT F ON F.FAILURE_ID = A.FAILURE_ID
                          LEFT JOIN CORE.KNOWN_ISSUE K ON K.ISSUE_ID = A.ISSUE_ID
                          LEFT JOIN CORE.FIX_SKILL S ON S.SKILL_ID = K.SKILL_ID
                         WHERE {' AND '.join(where)} ORDER BY A.REQUESTED_AT DESC LIMIT {LIST_LIMIT}""", tuple(params))
    return [_out(r) for r in rows]


def _decide(db: Db, approval_id: str, approve: bool, note: Optional[str]) -> Dict[str, Any]:
    try:
        return engine.decide_approval(db, approval_id, str(db.user or ""), approve, note,
                                      may_approve_any=_holds(db, "OPS.RESOLVE_ADMIN"))
    except (LookupError, PermissionError, ValueError) as exc:
        raise _http(exc)


@router.post("/api/ops/resolve/approvals/{approval_id}/approve")
def approve(approval_id: str, body: DecisionIn, db: Db = Depends(current_db)):
    """The worker runs the approved fix within seconds (SP_RESUME_APPROVED)."""
    return _decide(db, approval_id, True, body.note)


@router.post("/api/ops/resolve/approvals/{approval_id}/reject")
def reject(approval_id: str, body: DecisionIn, db: Db = Depends(current_db)):
    if not (body.note or "").strip():
        raise HTTPException(422, "say why the fix should not run")
    return _decide(db, approval_id, False, body.note)


# ---------------------------------------------------------------- known fixes

class FixIn(BaseModel):
    title: Optional[str] = Field(None, max_length=500)
    error_signature: Optional[str] = Field(None, max_length=64)
    dag_id: Optional[str] = Field(None, max_length=250)
    task_id: Optional[str] = Field(None, max_length=250)
    exception_class: Optional[str] = Field(None, max_length=250)
    match_text: Optional[str] = Field(None, max_length=8000)
    fix_type: Optional[str] = Field(None, pattern="^(PROCEDURE|SKILL)$")
    procedure_fqn: Optional[str] = Field(None, max_length=512)
    skill_id: Optional[str] = Field(None, max_length=36)
    approval_required: Optional[bool] = None
    owner: Optional[str] = Field(None, max_length=256)
    notes: Optional[str] = Field(None, max_length=4000)
    source_incident_id: Optional[str] = Field(None, max_length=36)
    source_failure_id: Optional[str] = Field(None, max_length=36)


@router.get("/api/ops/resolve/fixes")
def list_fixes(status: Optional[str] = None, q: Optional[str] = Query(None, max_length=200), db: Db = Depends(current_db)):
    return [_out(r) for r in publish.list_issues(db, status, q)]


@router.get("/api/ops/resolve/fixes/draft")
def fix_draft(incident_id: str, db: Db = Depends(current_db)):
    """The fields a fix written from a solved incident starts with (nothing is saved)."""
    try:
        return publish.draft_from_incident(db, incident_id)
    except LookupError as exc:
        raise _http(exc)


@router.get("/api/ops/resolve/fixes/{issue_id}")
def fix_detail(issue_id: str, db: Db = Depends(current_db)):
    issue = engine.get_issue(db, issue_id)
    if not issue:
        raise HTTPException(404, f"fix {issue_id} not found")
    runs = db.query("""SELECT R.RESOLUTION_ID, R.FAILURE_ID, R.OUTCOME, R.ACTOR, R.STARTED_AT, F.DAG_ID, F.TASK_ID, F.RUN_ID
                         FROM CORE.RESOLUTION_RUN R JOIN CORE.FAILURE_EVENT F ON F.FAILURE_ID = R.FAILURE_ID
                        WHERE R.ISSUE_ID = %s ORDER BY R.STARTED_AT DESC LIMIT 50""", (issue_id,))
    return {"issue": _out(issue), "skill": _out(engine.get_skill(db, issue.get("skill_id"))), "runs": [_out(r) for r in runs]}


@router.post("/api/ops/resolve/fixes")
def create_fix(body: FixIn, db: Db = Depends(current_db)):
    try:
        return _out(publish.create(db, body.model_dump(exclude_none=True), str(db.user or "")))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.put("/api/ops/resolve/fixes/{issue_id}")
def update_fix(issue_id: str, body: FixIn, db: Db = Depends(current_db)):
    try:
        return _out(publish.update(db, issue_id, body.model_dump(exclude_unset=True), str(db.user or "")))
    except (LookupError, ValueError) as exc:
        raise _http(exc)


@router.post("/api/ops/resolve/fixes/{issue_id}/publish")
def publish_fix(issue_id: str, db: Db = Depends(current_db)):
    try:
        return _out(publish.publish(db, issue_id, str(db.user or "")))
    except (LookupError, ValueError) as exc:
        raise _http(exc)


@router.post("/api/ops/resolve/fixes/{issue_id}/retire")
def retire_fix(issue_id: str, db: Db = Depends(current_db)):
    try:
        return _out(publish.retire(db, issue_id, str(db.user or "")))
    except ValueError as exc:
        raise _http(exc)


class SkillIn(BaseModel):
    skill_name: str = Field(..., min_length=1, max_length=250)
    description: Optional[str] = Field(None, max_length=4000)
    steps: List[Dict[str, Any]] = Field(..., min_length=1, max_length=30)
    owner: Optional[str] = Field(None, max_length=256)


@router.get("/api/ops/resolve/skills")
def list_skills(db: Db = Depends(current_db)):
    return [_out(r) for r in publish.list_skills(db)]


@router.post("/api/ops/resolve/skills")
def create_skill(body: SkillIn, db: Db = Depends(current_db)):
    try:
        return _out(publish.save_skill(db, body.model_dump(), str(db.user or "")))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.put("/api/ops/resolve/skills/{skill_id}")
def update_skill(skill_id: str, body: SkillIn, db: Db = Depends(current_db)):
    try:
        return _out(publish.save_skill(db, body.model_dump(), str(db.user or ""), skill_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


# ---------------------------------------------------------------- guardrails

class PreferenceIn(BaseModel):
    auto_resolve_enabled: Optional[bool] = None
    search_score_min: Optional[float] = Field(None, ge=0, le=1)
    max_parallel_tasks: Optional[int] = Field(None, ge=1, le=50)
    airflow_clear_enabled: Optional[bool] = None
    default_approval_required: Optional[bool] = None
    graph_hops: Optional[int] = Field(None, ge=1, le=6)
    notify_target: Optional[str] = Field(None, max_length=256)
    rca_mode: Optional[str] = Field(None, pattern="^(AGENT|GROUNDED)$")
    agent_proposals_enabled: Optional[bool] = None
    agent_fix_scope: Optional[str] = Field(None, max_length=2000, pattern=r"^[A-Za-z0-9_$.*,\s]*$")
    permanent_fix_enabled: Optional[bool] = None
    permanent_fix_min_runs: Optional[int] = Field(None, ge=2, le=50)


@router.get("/api/ops/resolve/preference")
def get_preference(db: Db = Depends(current_db)):
    return engine.load_preference(db)


@router.put("/api/ops/resolve/preference")
def put_preference(body: PreferenceIn, db: Db = Depends(current_db)):
    fields = body.model_dump(exclude_unset=True)
    if fields:
        sets = ", ".join(f"{k.upper()} = %s" for k in fields)
        changed = db.execute_count(f"""UPDATE CORE.CONTROL_PREFERENCE SET {sets}, UPDATED_BY = %s,
                                              UPDATED_AT = CURRENT_TIMESTAMP() WHERE IS_ACTIVE""",
                                   tuple(fields.values()) + (str(db.user or ""),))
        if changed == 0:
            raise HTTPException(409, "no active control preference row (apply migration V062)")
    return engine.load_preference(db)


class ScopeIn(BaseModel):
    scope_id: Optional[str] = Field(None, max_length=36)
    dag_id: str = Field(..., min_length=1, max_length=250)
    task_id: Optional[str] = Field(None, max_length=250)
    flow_mode: str = Field("AUTO", pattern="^(AUTO|APPROVAL_ALWAYS|TICKET_ONLY|DISABLED)$")
    enabled: bool = True
    owner: Optional[str] = Field(None, max_length=256)


class RouteIn(BaseModel):
    route_id: Optional[str] = Field(None, max_length=36)
    dag_id: str = Field(..., min_length=1, max_length=250)
    task_id: Optional[str] = Field(None, max_length=250)
    approver: str = Field(..., min_length=1, max_length=256)
    notify_target: Optional[str] = Field(None, max_length=256)


@router.get("/api/ops/resolve/scopes")
def list_scopes(dag: Optional[str] = None, db: Db = Depends(current_db)):
    if dag:
        return [_out(r) for r in db.query("SELECT * FROM CORE.PIPELINE_SCOPE WHERE DAG_ID = %s ORDER BY TASK_ID NULLS FIRST", (dag,))]
    return [_out(r) for r in db.query("SELECT * FROM CORE.PIPELINE_SCOPE ORDER BY DAG_ID, TASK_ID NULLS FIRST")]


def _one_per_target(db: Db, table: str, key: str, row_id: Optional[str], dag_id: str, task_id: Optional[str]) -> Optional[str]:
    """The id of the row already covering this DAG and task (one row each), so a PUT updates instead of duplicating."""
    found = db.query(f"SELECT {key} AS ID FROM CORE.{table} WHERE DAG_ID = %s AND COALESCE(TASK_ID, '') = %s",
                     (dag_id, task_id or ""))
    existing = found[0]["id"] if found else None
    if row_id and existing and existing != row_id:
        raise HTTPException(409, f"another row already covers {dag_id}{'.' + task_id if task_id else ''}")
    return row_id or existing


@router.put("/api/ops/resolve/scopes")
def put_scope(body: ScopeIn, db: Db = Depends(current_db)):
    task = (body.task_id or "").strip() or None
    sid = _one_per_target(db, "PIPELINE_SCOPE", "SCOPE_ID", body.scope_id, body.dag_id, task)
    if sid:
        db.execute("""UPDATE CORE.PIPELINE_SCOPE SET DAG_ID = %s, TASK_ID = %s, FLOW_MODE = %s, ENABLED = %s, OWNER = %s,
                             UPDATED_BY = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE SCOPE_ID = %s""",
                   (body.dag_id, task, body.flow_mode, body.enabled, body.owner, str(db.user or ""), sid))
    else:
        sid = str(uuid.uuid4())
        db.execute("""INSERT INTO CORE.PIPELINE_SCOPE (SCOPE_ID, DAG_ID, TASK_ID, FLOW_MODE, ENABLED, OWNER, UPDATED_BY)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                   (sid, body.dag_id, task, body.flow_mode, body.enabled, body.owner, str(db.user or "")))
    return _out(db.query("SELECT * FROM CORE.PIPELINE_SCOPE WHERE SCOPE_ID = %s", (sid,))[0])


@router.delete("/api/ops/resolve/scopes/{scope_id}")
def delete_scope(scope_id: str, db: Db = Depends(current_db)):
    db.execute("DELETE FROM CORE.PIPELINE_SCOPE WHERE SCOPE_ID = %s", (scope_id,))
    return {"deleted": scope_id}


@router.get("/api/ops/resolve/routes")
def list_routes(dag: Optional[str] = None, db: Db = Depends(current_db)):
    if dag:
        return [_out(r) for r in db.query("SELECT * FROM CORE.APPROVAL_ROUTE WHERE DAG_ID = %s ORDER BY TASK_ID NULLS FIRST", (dag,))]
    return [_out(r) for r in db.query("SELECT * FROM CORE.APPROVAL_ROUTE ORDER BY DAG_ID, TASK_ID NULLS FIRST")]


@router.put("/api/ops/resolve/routes")
def put_route(body: RouteIn, db: Db = Depends(current_db)):
    task = (body.task_id or "").strip() or None
    rid = _one_per_target(db, "APPROVAL_ROUTE", "ROUTE_ID", body.route_id, body.dag_id, task)
    approver = body.approver.strip().upper()
    if rid:
        db.execute("""UPDATE CORE.APPROVAL_ROUTE SET DAG_ID = %s, TASK_ID = %s, APPROVER = %s, NOTIFY_TARGET = %s,
                             UPDATED_BY = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE ROUTE_ID = %s""",
                   (body.dag_id, task, approver, body.notify_target, str(db.user or ""), rid))
    else:
        rid = str(uuid.uuid4())
        db.execute("""INSERT INTO CORE.APPROVAL_ROUTE (ROUTE_ID, DAG_ID, TASK_ID, APPROVER, NOTIFY_TARGET, UPDATED_BY)
                      VALUES (%s, %s, %s, %s, %s, %s)""",
                   (rid, body.dag_id, task, approver, body.notify_target, str(db.user or "")))
    return _out(db.query("SELECT * FROM CORE.APPROVAL_ROUTE WHERE ROUTE_ID = %s", (rid,))[0])


@router.delete("/api/ops/resolve/routes/{route_id}")
def delete_route(route_id: str, db: Db = Depends(current_db)):
    db.execute("DELETE FROM CORE.APPROVAL_ROUTE WHERE ROUTE_ID = %s", (route_id,))
    return {"deleted": route_id}
