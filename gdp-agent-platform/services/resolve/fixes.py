"""Fix executors: a PROCEDURE fix is one CALL; a SKILL fix runs its steps in order (slide 2: "PROCEDURE: one CALL ·
SKILL: agent runs steps").

Every step is a fix procedure (CORE.FIX_*), never free SQL, and is recorded in CORE.RESOLUTION_STEP. A step runs once
and only after the step before it succeeded, whoever asks: the resolution agent (through CORE.SP_RUN_FIX_STEP, its
only tool) or the deterministic runner that finishes whatever the agent did not. So an agent that stops half way, or
is not available, never runs a step twice or out of order.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from services.ops.redact import redact
from services.resolve import agent, rules

AGENT_NAME = "RESOLUTION_AGENT"


def _first_value(rows: List[Dict[str, Any]]) -> Any:
    if not rows:
        return None
    value = next(iter(rows[0].values()), None)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def call(db: Any, fqn: str, failure_id: str, given: Optional[str] = None) -> Any:
    """CALL a checked fix procedure with the failure id (and the step input, when there is one)."""
    if given is None:
        return _first_value(db.query(f"CALL {fqn}(%s)", (failure_id,)))
    return _first_value(db.query(f"CALL {fqn}(%s, %s)", (failure_id, given)))


def steps_done(db: Any, resolution_id: str) -> Dict[int, str]:
    return {int(r["step_no"]): str(r["status"]) for r in
            db.query("SELECT STEP_NO, STATUS FROM CORE.RESOLUTION_STEP WHERE RESOLUTION_ID = %s", (resolution_id,))}


def run_step(db: Any, database: str, resolution_id: str, failure_id: str, steps: List[Dict[str, Any]],
             step_no: int) -> Dict[str, Any]:
    """Run one step of a skill: {step_no, status, output}. Refuses a step that already ran or whose previous step has
    not succeeded."""
    by_no = {s["step_no"]: s for s in steps}
    if step_no not in by_no:
        raise ValueError(f"the skill has no step {step_no}")
    done = steps_done(db, resolution_id)
    if step_no in done:   # the resolution agent ran it already: report what it did, not just that it ran
        found = db.query("SELECT OUTPUT FROM CORE.RESOLUTION_STEP WHERE RESOLUTION_ID = %s AND STEP_NO = %s",
                         (resolution_id, step_no))
        stored = found[0].get("output") if found else None
        if isinstance(stored, str):
            try:
                stored = json.loads(stored)
            except ValueError:
                pass
        return {"step_no": step_no, "status": done[step_no], "output": stored if stored not in (None, "") else "already ran"}
    earlier = [n for n in by_no if n < step_no]
    if earlier and done.get(max(earlier)) != "SUCCESS":
        raise ValueError(f"step {max(earlier)} has not succeeded yet")
    step = by_no[step_no]
    db.execute("""INSERT INTO CORE.RESOLUTION_STEP (RESOLUTION_ID, STEP_NO, ACTION, STATUS)
                  VALUES (%s, %s, %s, 'RUNNING')""", (resolution_id, step_no, step["action"]))
    try:
        output, status = call(db, rules.fix_procedure(step["action"], database), failure_id, step.get("input")), "SUCCESS"
    except Exception as exc:
        output, status = redact(f"{type(exc).__name__}: {exc}")[:2000], "FAILED"
    db.execute("""UPDATE CORE.RESOLUTION_STEP SET STATUS = %s, OUTPUT = PARSE_JSON(%s), FINISHED_AT = CURRENT_TIMESTAMP()
                   WHERE RESOLUTION_ID = %s AND STEP_NO = %s""",
               (status, json.dumps(output, default=str), resolution_id, step_no))
    return {"step_no": step_no, "status": status, "output": output}


class Executor:
    """What SP_RUN_FIX needs: the platform database, CALL for a procedure fix, and the step runner for a skill."""

    def __init__(self, db: Any, database: str, use_agent: bool = True):
        self.db, self.database, self.use_agent = db, database, use_agent

    def call_procedure(self, fqn: str, failure_id: str) -> Any:
        return call(self.db, fqn, failure_id)

    def run_skill(self, resolution_id: str, failure: Dict[str, Any], skill: Dict[str, Any]) -> Dict[str, Any]:
        steps = rules.skill_steps(skill.get("steps"), self.database)
        used_agent, agent_note = False, None
        if self.use_agent:
            try:
                answer = agent.run(self.database, "CORE", AGENT_NAME, agent_message(resolution_id, failure, skill, steps))
                used_agent, agent_note = True, answer[:1000]
            except agent.AgentUnavailable as exc:
                agent_note = f"agent not used: {exc}"[:500]
        results = []
        for step in steps:   # finish (or do) whatever the agent did not; refuses repeats
            result = run_step(self.db, self.database, resolution_id, failure["failure_id"], steps, step["step_no"])
            results.append(result)
            if result["status"] != "SUCCESS":
                raise RuntimeError(f"step {step['step_no']} ({step['action']}) failed: {result['output']}")
        return {"skill": skill.get("skill_name"), "steps": results, "agent": used_agent, "agent_note": agent_note}


def agent_message(resolution_id: str, failure: Dict[str, Any], skill: Dict[str, Any], steps: List[Dict[str, Any]]) -> str:
    listing = "\n".join(f"{s['step_no']}. {s['action']}" + (f" ({s['note']})" if s.get("note") else "") for s in steps)
    return (f"Run the published fix '{skill.get('skill_name')}' for failure {failure['failure_id']} "
            f"(DAG {failure.get('dag_id')}, task {failure.get('task_id')}).\n"
            f"Call run_fix_step with FAILURE_ID={failure['failure_id']}, RESOLUTION_ID={resolution_id} and each STEP_NO "
            f"in order. Stop at the first step that does not succeed and say which one.\nSteps:\n{listing}")


def run_step_entry(session: Any, failure_id: str, resolution_id: str, step_no: Any) -> Dict[str, Any]:
    """Procedure handler: CALL CORE.SP_RUN_FIX_STEP(failure, resolution, step): the resolution agent's only tool."""
    from services.ops.sqlio import as_db
    from services.resolve.engine import database_of, get_failure, get_issue, get_skill

    db = as_db(session)
    failure = get_failure(db, failure_id)
    if not failure or str(failure.get("status")) not in ("IN_PROGRESS", "AWAITING_APPROVAL"):
        return {"error": "the failure is not being fixed"}
    issue = get_issue(db, failure.get("issue_id") or "") or {}
    skill = get_skill(db, issue.get("skill_id"))
    if str(issue.get("status")) != "PUBLISHED" or not skill:
        return {"error": "the failure has no published skill fix"}
    if str(failure.get("status")) == "AWAITING_APPROVAL" and not db.query(
            """SELECT 1 AS X FROM CORE.APPROVAL_REQUEST WHERE FAILURE_ID = %s AND ISSUE_ID = %s
                 AND STATUS IN ('APPROVED', 'DONE') LIMIT 1""", (failure_id, issue["issue_id"])):
        return {"error": "the fix for this failure has not been approved"}
    database = database_of(db)
    try:
        return run_step(db, database, resolution_id, failure_id, rules.skill_steps(skill.get("steps"), database), int(step_no))
    except ValueError as exc:
        return {"error": str(exc)}
