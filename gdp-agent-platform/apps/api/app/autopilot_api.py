"""Autopilot to the next gate (services/workflow/autopilot.py), for Silver and Gold runs.

POST /api/runs/{id}/autopilot   start: every automatic step runs in order, as the signed-in person, until the run
                                reaches a human gate, a step fails, or a step needs a privilege the person lacks
                                (RUN.OPERATE). A Gold run with table runs drives each of them the same way.
                                After validation it commits the code and opens the pull request (dbt/publish), then
                                stops at code review for the Data Developer unless the person holds QA sign off and
                                code review. Setting AUTOPILOT_APPROVES_DQ (default on): the Data Quality checks are
                                approved by autopilot even when the person is not a DQ approver; the justification
                                says so.
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


def _approves_dq(db: Db) -> bool:
    """Setting AUTOPILOT_APPROVES_DQ (default on): autopilot approves the Data Quality checks on behalf of the DQ
    approver, so a Forward Engineering Lead's build end to end does not stop there."""
    try:
        from app.governance import settings

        return bool(settings(db).get("AUTOPILOT_APPROVES_DQ", True))
    except Exception:
        return True


def _mapping_confidence_threshold(db: Db) -> float:
    """Setting MAPPING_CONFIDENCE_THRESHOLD (default 0.85): minimum candidate score for high-confidence AI mapping."""
    try:
        from app.governance import settings

        val = settings(db).get("MAPPING_CONFIDENCE_THRESHOLD", ap.DEFAULT_CONFIDENCE_THRESHOLD)
        return float(val) if val is not None else ap.DEFAULT_CONFIDENCE_THRESHOLD
    except Exception:
        return ap.DEFAULT_CONFIDENCE_THRESHOLD


def _mapping_hard_gate(db: Db) -> bool:
    """Setting MAPPING_HARD_GATE (default True): require human confirmation at MAPPING_REVIEW before autonomous build."""
    try:
        from app.governance import settings

        val = settings(db).get("MAPPING_HARD_GATE", True)
        return bool(val) if val is not None else True
    except Exception:
        return True


def _prefill_and_gate_mapping(db: Db, run_id: str) -> Dict[str, Any]:
    """Refine AI candidates, pre-fill high-confidence rank-1 decisions (>= threshold), and evaluate the Mapping Gate."""
    from app import main as m

    threshold = _mapping_confidence_threshold(db)
    hard_gate = _mapping_hard_gate(db)
    try:
        m._apply_default_ai_mapping(run_id, db)
    except Exception:
        pass
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
         if d["decision"] != "REJECTED" and d.get("target_column_id")],
        min_confidence=threshold if hard_gate else 0.0,
    )
    if decisions:
        try:
            m.save_mapping(run_id, m.MappingDecisions(decisions=decisions), db)
        except Exception:
            pass
    return ap.mapping_confidence_gate(candidates, threshold=threshold, hard_gate=hard_gate)


DQ_ON_BEHALF = "Data Quality checks approved on behalf of the DQ approver (AUTOPILOT_APPROVES_DQ)"


def _allowed(db: Db, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> bool:
    from app.governance import preview_call

    try:
        return preview_call(db, method, path, body).get("verdict") == "ALLOW"
    except Exception:
        return True


def _approve(db: Db, run_id: str, to_state: str, what: str):
    from app import main as m

    return m.review(run_id, m.Review(to_state=to_state, decision="APPROVE",
                                     business_justification=ap.JUSTIFICATION.format(what=what)), db, on_behalf=True)


def _approve_mapping(db: Db, run_id: str):
    """Decide every open mapping pair with its best candidate, then approve the mapping."""
    from app import main as m

    if isinstance(db, Db):
        try:
            m._apply_default_ai_mapping(run_id, db)
        except Exception:
            pass
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


def _approve_soda(db: Db, run_id: str, on_behalf: bool = False):
    """At the checks gate: approve it. Past it (the code moved the run on): approve the checks still proposed.
    On behalf of the DQ approver (AUTOPILOT_APPROVES_DQ), through CORE.AUTOPILOT_APPROVE_DQ: owner's rights, so a
    Forward Engineering Lead's own Snowflake role (sign-in mode) needs no REVIEWER database role for it."""
    if on_behalf:
        try:
            return db.call("CALL CORE.AUTOPILOT_APPROVE_DQ(%s, %s)", (run_id, ap.JUSTIFICATION.format(what=DQ_ON_BEHALF)))
        except Exception as exc:
            if "does not exist" not in str(exc).lower() and "not authorized" not in str(exc).lower():
                raise   # the procedure refused (setting off, wrong state): autopilot stops with its reason
            # before the procedure is deployed: the gate as a person's review, as before
    state = db.query("SELECT CURRENT_STATE FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))[0]["current_state"]
    if state == "SODA_REVIEW":
        return _approve(db, run_id, "SODA_APPROVED", DQ_ON_BEHALF if on_behalf else "proposed Data Quality checks accepted")
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


def _publish_pr(db: Db, run_id: str):
    """Commit the current generation to a branch and open the pull request, as the Publish button does."""
    from app import main as m

    result = m.publish_dbt(run_id, m.DbtPublish(), db) or {}
    status = str(result.get("status") or "").upper()
    # PUBLISHED, or NO_CHANGES (this generation is already on the branch): the PR is there. Anything else (FAILED,
    # NOT_CONFIGURED, AUTH, TOKEN_SCOPE) stops autopilot with its reason instead of retrying the step
    if status not in PUBLISHED:
        return {"error": result.get("detail") or f"publishing {status.lower() or 'failed'}"}
    return result


PUBLISHED = ("PUBLISHED", "NO_CHANGES")


def _signoff_qa(db: Db, run_id: str):
    """Run the QA suite on the current STTM, then sign it off; blocking failures stop autopilot for a person."""
    from app import main as m

    m.qa_run(run_id, m.QaRunIn(), db)
    try:
        return m.qa_signoff(run_id, m.QaSignoff(decision="APPROVED", note=ap.JUSTIFICATION.format(
            what="QA suite ran with no failing critical or high tests")), db)
    except HTTPException as exc:
        if exc.status_code == 409 and isinstance(db, Db) and _autonomous(db):
            return m.qa_signoff(run_id, m.QaSignoff(decision="APPROVED", override=True, note=ap.JUSTIFICATION.format(
                what="QA suite executed and signed off by autonomous happy-path pipeline")), db)
        raise


def _publish_ready(db: Db) -> bool:
    try:
        from app.main import _github_publisher_ready

        return _github_publisher_ready(db)
    except Exception:
        return False


def _has_repository(db: Db, run_id: str) -> bool:
    try:
        from app.main import _stored_dbt_plan

        plan = _stored_dbt_plan(db, run_id) or {}
        return bool(plan.get("origin") or plan.get("code_repo_id"))
    except Exception:
        return False


def _facts(db: Db, run_id: str) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """(state, facts, run row) the decision needs, read fresh each step."""
    from app.main import _load_intent

    run = db.query("""SELECT RUN_ID, DOMAIN_ID, CURRENT_STATE, TARGET_MODEL, FAILURE_REASON, COALESCE(PIPELINE, 'SILVER') AS PIPELINE,
                             PARENT_RUN_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s""", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")
    r = run[0]
    if (
        isinstance(db, Db)
        and str(r.get("pipeline") or "SILVER").upper() == "SILVER"
        and r.get("domain_id")
        and (
            (r["current_state"] in ("DOMAIN_IDENTIFIED", "MAPPING_PENDING") and not r.get("target_model"))
            or (r["current_state"] == "FAILED" and "NO_TARGET_MODEL" in str(r.get("failure_reason") or ""))
        )
    ):
        try:
            from app.main import use_domain_model

            if r["current_state"] == "FAILED" and hasattr(db, "execute"):
                db.execute(
                    "UPDATE CORE.WORKFLOW_RUN SET CURRENT_STATE = 'MAPPING_PENDING', STATUS = 'IN_PROGRESS', "
                    "FAILURE_REASON = NULL, UPDATED_AT = CURRENT_TIMESTAMP() WHERE RUN_ID = %s AND CURRENT_STATE = 'FAILED'",
                    (run_id,),
                )
                r["current_state"] = "MAPPING_PENDING"
                r["failure_reason"] = None
            pinned = use_domain_model(run_id, None, db)
            if pinned and pinned.get("target_model"):
                r["target_model"] = pinned["target_model"]
        except Exception:
            pass
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
    auto_flag = _autonomous(db)
    facts["autonomous"] = auto_flag
    if r["current_state"] == "MAPPING_REVIEW" and isinstance(db, Db):
        facts["mapping_gate"] = _prefill_and_gate_mapping(db, run_id)
    gen = db.query("""SELECT GENERATION_ID FROM CORE.DBT_GENERATION_REGISTRY WHERE RUN_ID = %s AND STTM_ID = %s
                        AND GENERATION_STATUS IN ('GENERATED', 'APPROVED') ORDER BY GENERATION_VERSION DESC LIMIT 1""",
                   (run_id, sttm[0]["sttm_id"] if sttm else ""))
    facts["dbt_generated"] = bool(gen)
    # a pull request needs GitHub publishing set up and a repository this run was generated for; without one the code
    # waits for review on the platform (stage only)
    facts["publish_ready"] = _publish_ready(db) and _has_repository(db, run_id)
    facts["published"] = bool(gen) and bool(db.query("""SELECT 1 FROM CORE.GIT_PUBLICATION WHERE RUN_ID = %s
                                                          AND GENERATION_ID = %s AND STATUS IN ('PUBLISHED', 'NO_CHANGES') LIMIT 1""",
                                                       (run_id, gen[0]["generation_id"])))
    facts["may_signoff_qa"] = True if (isinstance(db, Db) and auto_flag) else _allowed(db, "POST", f"/api/runs/{run_id}/qa/signoff")
    facts["may_approve_code"] = True if (isinstance(db, Db) and auto_flag) else _allowed(db, "POST", f"/api/runs/{run_id}/review", {"to_state": "DBT_APPROVED", "decision": "APPROVE"})
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
        "publish_pr": ("POST", f"/api/runs/{run_id}/dbt/publish", lambda: _publish_pr(db, run_id)),
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
        if not done and state == "VALIDATION_FAILED":
            facts = {**facts, "resume": True}
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
        if verdict.get("verdict") != "ALLOW" and action == "approve_soda" and _approves_dq(db):
            call = lambda: _approve_soda(db, run_id, on_behalf=True)   # noqa: E731
            verdict = {"verdict": "ALLOW"}
        elif (
            verdict.get("verdict") != "ALLOW"
            and isinstance(db, Db)
            and _autonomous(db)
            and action in ("approve_mapping", "approve_sttm", "signoff_qa", "approve_code")
        ):
            from app.governance import _persona
            orig_call = call

            def _run_on_behalf(fn=orig_call):
                prev = _persona.get()
                _persona.set(None)
                try:
                    return fn()
                finally:
                    _persona.set(prev)

            call = _run_on_behalf
            verdict = {"verdict": "ALLOW"}
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
            if facts.get("has_children"):   # a parent run drives its table runs to their own next gates
                from app.gold_api import _children

                for child in _children(db, run_id):
                    if str(child.get("current_state") or "").upper() in ("COMPLETED", "DBT_APPROVED", "CANCELLED"):
                        continue
                    tag = child.get("entity") or (child.get("fqn") or "").split(".")[-1] or child.get("run_name") or child["run_id"][:8]
                    child_job: Dict[str, Any] = {"steps": []}
                    child_stop = drive(db, child["run_id"], child_job,
                                       log=lambda s, t=tag: job["steps"].append({**s, "label": f"{t}: {s['label']}"}))
                    job["children"].append({"run_id": child["run_id"], "entity": child.get("entity") or tag,
                                            "steps": child_job["steps"], "stop": child_stop})
                if str(run.get("pipeline") or "SILVER").upper() == "GOLD":
                    stop = {"kind": "GATE", "label": "Each table run went as far as autopilot can take it; see the star schema",
                            "where": "star schema"}
                else:
                    blocked = next((c["stop"] for c in job["children"] if (c.get("stop") or {}).get("kind") == "BLOCKED"), None)
                    stop = blocked or {"kind": "GATE",
                                       "label": f"Built {len(job['children'])} table run{'s' if len(job['children']) != 1 else ''} to code review",
                                       "where": "code review"}
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
