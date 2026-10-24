"""Autopilot to the next gate (services/workflow/autopilot.py), for Silver and Gold runs.

POST /api/runs/{id}/autopilot   start: every automatic step runs in order, as the signed-in person, until the run
                                reaches a human gate, a step fails, or a step needs a privilege the person lacks
                                (RUN.OPERATE). A Gold run with table runs drives each of them the same way.
GET  /api/runs/{id}/autopilot   the job: status (RUNNING | STOPPED | FAILED), the steps it ran with their time, and
                                where it stopped (the gate and what the person does there)

Each step is the same function a person's click calls, and the governance check of that click is made first
(app.governance.preview_call): autopilot never does what the person could not do themselves. Steps are recorded in
the run's audit trail by the procedures as usual. Jobs live in this API process (like profile jobs).
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException

from app.db import Db
from app.main import current_db
from services.workflow import autopilot as ap

router = APIRouter()
_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
WAIT_SECONDS, WAIT_LIMIT = 5, 24


def _autonomous(db: Db) -> bool:
    """Setting AUTOPILOT_AUTONOMOUS (default on): autopilot decides the table run's gates itself."""
    try:
        from app.governance import settings

        return bool(settings(db).get("AUTOPILOT_AUTONOMOUS", True))
    except Exception:
        return True


def _approve(db: Db, run_id: str, to_state: str, what: str):
    from app import main as m

    return m.review(run_id, m.Review(to_state=to_state, decision="APPROVE",
                                     business_justification=ap.JUSTIFICATION.format(what=what)), db)


def _approve_mapping(db: Db, run_id: str):
    """Decide every open mapping pair with its best candidate, then approve the mapping."""
    from app import main as m

    candidates = db.query("""SELECT C.CANDIDATE_ID, C.SOURCE_COLUMN_ID, C.TARGET_COLUMN_ID, C.RANK, C.FINAL_SCORE,
                                    C.RECOMMENDATION, COALESCE(C.TARGET_TABLE_ID, T.TARGET_TABLE_ID) AS TARGET_TABLE_ID
                               FROM CORE.MAPPING_CANDIDATE C
                               LEFT JOIN CORE.TARGET_COLUMN_REGISTRY T ON T.TARGET_COLUMN_ID = C.TARGET_COLUMN_ID
                              WHERE C.RUN_ID = %s AND C.IS_CURRENT""", (run_id,))
    current = db.query("""SELECT D.SOURCE_COLUMN_ID, D.TARGET_COLUMN_ID, D.DECISION,
                                 COALESCE(D.TARGET_TABLE_ID, T.TARGET_TABLE_ID) AS TARGET_TABLE_ID
                            FROM CORE.MAPPING_DECISION D
                            LEFT JOIN CORE.TARGET_COLUMN_REGISTRY T ON T.TARGET_COLUMN_ID = D.TARGET_COLUMN_ID
                           WHERE D.RUN_ID = %s AND D.IS_CURRENT""", (run_id,))
    decisions = ap.auto_mapping_decisions(
        candidates, [(d["source_column_id"], d.get("target_table_id")) for d in current],
        [(d["target_column_id"], d.get("target_table_id")) for d in current
         if d["decision"] != "REJECTED" and d.get("target_column_id")])
    if decisions:
        m.save_mapping(run_id, m.MappingDecisions(decisions=decisions), db)
    return _approve(db, run_id, "MAPPING_APPROVED", f"mapping accepted from the best candidates ({len(decisions)} decided)")


def _approve_soda(db: Db, run_id: str):
    """At the checks gate: approve it. Past it (the code moved the run on): approve the checks still proposed."""
    state = db.query("SELECT CURRENT_STATE FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))[0]["current_state"]
    if state == "SODA_REVIEW":
        return _approve(db, run_id, "SODA_APPROVED", "proposed Data Quality checks accepted")
    db.execute("""UPDATE CORE.SODA_EXPECTATION_REGISTRY SET STATUS = 'APPROVED', REVIEWED_BY = CURRENT_USER(),
                         REVIEWED_AT = CURRENT_TIMESTAMP()
                   WHERE RUN_ID = %s AND IS_CURRENT AND STATUS = 'PROPOSED'""", (run_id,))
    try:
        from app.main import invoke_source
        from services.soda.procedures import store_approved_set

        invoke_source(db, store_approved_set, run_id)   # kept in the DQ store, as a person's approval does
    except Exception:
        pass
    return {"ok": True}


def _signoff_qa(db: Db, run_id: str):
    """Run the QA suite on the current STTM, then sign it off; blocking failures stop autopilot for a person."""
    from app import main as m

    m.qa_run(run_id, m.QaRunIn(), db)
    return m.qa_signoff(run_id, m.QaSignoff(decision="APPROVED", note=ap.JUSTIFICATION.format(
        what="QA suite ran with no failing critical or high tests")), db)


def _facts(db: Db, run_id: str) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """(state, facts, run row) the decision needs, read fresh each step."""
    from app.main import _load_intent

    run = db.query("""SELECT RUN_ID, DOMAIN_ID, CURRENT_STATE, TARGET_MODEL, FAILURE_REASON, COALESCE(PIPELINE, 'SILVER') AS PIPELINE,
                             PARENT_RUN_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s""", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")
    r = run[0]
    intent = _load_intent(db, run_id) or {}
    facts: Dict[str, Any] = {"pipeline": r["pipeline"], "has_target": bool(r.get("target_model")),
                             "planned_tables": len((intent.get("source") or {}).get("tables") or []),
                             "failure_reason": r.get("failure_reason")}
    if str(r["pipeline"]).upper() == "GOLD" and not r.get("parent_run_id"):
        from app.gold_api import _children, _gold_model

        model = _gold_model(db, r["domain_id"]) if r.get("domain_id") else None
        facts["gold_model"] = {"approved": bool(model and model.get("approved_version")), "open": bool(model and model.get("open_version"))}
        facts["has_children"] = bool(_children(db, run_id))
    elif not r.get("parent_run_id"):   # a Silver run mapped to several tables continues in its table runs
        from services.mapping.children import progress

        states = [c["current_state"] for c in db.query("""SELECT CURRENT_STATE FROM CORE.WORKFLOW_RUN
                                                           WHERE PARENT_RUN_ID = %s AND DELETED_AT IS NULL""", (run_id,))]
        facts["has_children"] = bool(states)
        facts["table_runs"] = progress(states)
    sttm = db.query("""SELECT STTM_ID FROM CORE.STTM_REGISTRY WHERE RUN_ID = %s AND STATUS = 'APPROVED'
                        ORDER BY STTM_VERSION DESC LIMIT 1""", (run_id,))
    facts["soda_generated"] = bool(db.query("""SELECT 1 FROM CORE.SODA_EXPECTATION_REGISTRY WHERE RUN_ID = %s AND IS_CURRENT
                                               LIMIT 1""", (run_id,)))
    facts["soda_proposed"] = bool(db.query("""SELECT 1 FROM CORE.SODA_EXPECTATION_REGISTRY WHERE RUN_ID = %s AND IS_CURRENT
                                              AND STATUS = 'PROPOSED' LIMIT 1""", (run_id,)))
    from app.main import _qa_signoff

    facts["qa_signed"] = str((_qa_signoff(db, run_id) or {}).get("decision") or "").upper() == "APPROVED"
    facts["autonomous"] = _autonomous(db)
    gen = db.query("""SELECT GENERATION_ID FROM CORE.DBT_GENERATION_REGISTRY WHERE RUN_ID = %s AND STTM_ID = %s
                        AND GENERATION_STATUS IN ('GENERATED', 'APPROVED') ORDER BY GENERATION_VERSION DESC LIMIT 1""",
                   (run_id, sttm[0]["sttm_id"] if sttm else ""))
    facts["dbt_generated"] = bool(gen)
    facts["validated"] = bool(gen) and bool(db.query("""SELECT 1 FROM CORE.VALIDATION_RUN WHERE RUN_ID = %s
                                                          AND GENERATION_ID = %s AND VALIDATION_TYPE = 'SUMMARY' LIMIT 1""",
                                                       (run_id, gen[0]["generation_id"])))
    return r["current_state"], facts, r


def _call(db: Db, run_id: str, action: str) -> Tuple[str, str, Callable[[], Any], Optional[Dict[str, Any]]]:
    """(method, path, call, body) of an action: the route a person's click uses, the function behind it, and the body
    its governance check needs (a review's privilege depends on the state it approves)."""
    from app import main as m

    def prepare():
        intent = m._load_intent(db, run_id) or {}
        tables = (intent.get("source") or {}).get("tables") or []
        return m.prepare_source(run_id, m.AccessRequest(selected=tables), db)

    def propose():
        from app.gold_api import propose_gold
        return propose_gold(run_id, db)

    def star():
        from app.gold_api import build_star_schema
        return build_star_schema(run_id, db)

    table = {
        "prepare_source": ("POST", f"/api/runs/{run_id}/prepare", prepare),
        "profile": ("POST", f"/api/runs/{run_id}/profile", lambda: m.run_profiling(run_id, None, db)),
        "identify_domain": ("POST", f"/api/runs/{run_id}/domain", lambda: m.identify_domain(run_id, db)),
        "propose_gold_model": ("POST", f"/api/runs/{run_id}/gold/propose", propose),
        "build_star_schema": ("POST", f"/api/runs/{run_id}/gold/star-schema", star),
        "generate_mapping": ("POST", f"/api/runs/{run_id}/mapping", lambda: m.generate_mapping(run_id, db)),
        "generate_sttm": ("POST", f"/api/runs/{run_id}/sttm", lambda: m.generate_sttm(run_id, db)),
        "generate_soda": ("POST", f"/api/runs/{run_id}/soda", lambda: m.generate_soda(run_id, db)),
        "generate_dbt": ("POST", f"/api/runs/{run_id}/dbt", lambda: m.generate_dbt(run_id, None, db)),
        "validate": ("POST", f"/api/runs/{run_id}/validation", lambda: m.validate_dbt(run_id, db)),
        "approve_mapping": ("POST", f"/api/runs/{run_id}/review", lambda: _approve_mapping(db, run_id),
                            {"to_state": "MAPPING_APPROVED", "decision": "APPROVE"}),
        "approve_sttm": ("POST", f"/api/runs/{run_id}/review",
                         lambda: _approve(db, run_id, "STTM_APPROVED", "transformations generated from the approved mapping"),
                         {"to_state": "STTM_APPROVED", "decision": "APPROVE"}),
        "approve_soda": ("POST", f"/api/runs/{run_id}/review", lambda: _approve_soda(db, run_id),
                         {"to_state": "SODA_APPROVED", "decision": "APPROVE"}),
        "signoff_qa": ("POST", f"/api/runs/{run_id}/qa/signoff", lambda: _signoff_qa(db, run_id), None),
        "approve_code": ("POST", f"/api/runs/{run_id}/review",
                         lambda: _approve(db, run_id, "DBT_APPROVED", "validation passed, checks approved and QA signed off"),
                         {"to_state": "DBT_APPROVED", "decision": "APPROVE"}),
    }
    entry = table[action]
    return entry if len(entry) == 4 else (*entry, None)


def drive(db: Db, run_id: str, job: Dict[str, Any], log: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """Run the automatic steps of one run until a gate (the loop, testable with a fake db and calls)."""
    from app.governance import preview_call

    done: List[str] = []
    waits = 0
    for _ in range(ap.MAX_STEPS):
        state, facts, _run = _facts(db, run_id)
        nxt = ap.next_action(state, facts)
        if nxt["kind"] == "WAIT":
            waits += 1
            if waits > WAIT_LIMIT:
                return {"kind": "BLOCKED", "label": "A step has been running for a while; check the run console"}
            time.sleep(WAIT_SECONDS)
            continue
        if nxt["kind"] != "ACTION":
            return nxt
        action = nxt["action"]
        if done.count(action) >= 2:   # the same step did not move the run on: stop instead of looping
            return {"kind": "BLOCKED", "label": f"{nxt['label']} did not move the run on; open the run to see why"}
        method, path, call, body = _call(db, run_id, action)
        verdict = preview_call(db, method, path, body)
        if verdict.get("verdict") != "ALLOW":
            return {"kind": "BLOCKED", "label": f"{nxt['label']} needs {verdict.get('privilege')}"
                    + (f" (approval by {verdict['approver_role']})" if verdict.get("approver_role") else "")
                    + "; ask someone who holds it, or run this step yourself once you do"}
        started = time.time()
        step = {"action": action, "label": nxt["label"], "from_state": state, "started_at": started}
        try:
            result = call()
            error = (result or {}).get("error") if isinstance(result, dict) else None
        except HTTPException as exc:
            error = str(exc.detail)
        except Exception as exc:   # a step's own failure: reported, the run keeps its state
            error = str(exc)[:600]
        step.update(seconds=round(time.time() - started, 1), error=error)
        job["steps"].append(step)
        if log:
            log(step)
        done.append(action)
        if error:
            return {"kind": "BLOCKED", "label": f"{nxt['label']} failed: {error}"}
    return {"kind": "BLOCKED", "label": "Stopped after the most steps one start runs; start autopilot again"}


def _start(db: Db, run_id: str) -> Dict[str, Any]:
    with _lock:
        running = next((j for j in _jobs.values() if j["run_id"] == run_id and j["status"] == "RUNNING"), None)
        if running:
            return running
        job = {"job_id": str(uuid.uuid4()), "run_id": run_id, "status": "RUNNING", "started_at": time.time(),
               "finished_at": None, "steps": [], "stop": None, "children": []}
        _jobs[job["job_id"]] = job

    from app.governance import _persona, current_persona

    persona = current_persona()   # autopilot acts with the persona the person started it as, never more

    def work() -> None:
        _persona.set(persona)
        try:
            stop = drive(db, run_id, job)
            _state, facts, run = _facts(db, run_id)
            if facts.get("has_children"):   # a Gold run drives its table runs to their own next gates
                from app.gold_api import _children

                for child in _children(db, run_id):
                    child_job = {"steps": []}
                    child_stop = drive(db, child["run_id"], child_job)
                    job["children"].append({"run_id": child["run_id"], "entity": child.get("entity"),
                                            "steps": child_job["steps"], "stop": child_stop})
                stop = {"kind": "GATE", "label": "Each table run went as far as autopilot can take it; see the star schema",
                        "where": "star schema"}
            job.update(status="STOPPED", stop=stop)
        except Exception as exc:
            job.update(status="FAILED", stop={"kind": "BLOCKED", "label": str(exc)[:600]})
        job["finished_at"] = time.time()

    threading.Thread(target=work, name=f"autopilot-{run_id[:8]}", daemon=True).start()
    return job


@router.post("/api/runs/{run_id}/autopilot")
def start_autopilot(run_id: str, db: Db = Depends(current_db)):
    _facts(db, run_id)   # 404 for an unknown run
    return _start(db, run_id)


@router.get("/api/runs/{run_id}/autopilot")
def autopilot_status(run_id: str, db: Db = Depends(current_db)):
    with _lock:
        jobs = sorted((j for j in _jobs.values() if j["run_id"] == run_id), key=lambda j: j["started_at"], reverse=True)
    if jobs:
        return jobs[0]
    state, facts, _run = _facts(db, run_id)
    return {"run_id": run_id, "status": "IDLE", "steps": [], "children": [], "next": ap.next_action(state, facts)}
