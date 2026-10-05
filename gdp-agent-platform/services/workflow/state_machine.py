"""Deterministic transition evaluation. No I/O: callers load the graph and the run row."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from services.workflow.graph import (
    CANCELLED_STATE,
    FAILED_STATE,
    GUARD_RETRY,
    WorkflowGraph,
)

RUN_STATUS_BY_KIND = {
    "PENDING": "IN_PROGRESS",
    "DONE": "IN_PROGRESS",
    "RUNNING": "RUNNING",
    "REVIEW": "AWAITING_REVIEW",
    "BLOCKED": "BLOCKED",
    "FAILED": "FAILED",
}


@dataclass(frozen=True)
class RunContext:
    current_state: str
    failed_from_state: Optional[str] = None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    new_status: Optional[str] = None
    new_stage: Optional[str] = None


def run_status_for(graph: WorkflowGraph, state: str) -> str:
    if state == CANCELLED_STATE:
        return "CANCELLED"
    s = graph.states[state]
    if s.kind == "TERMINAL":
        return "COMPLETED"
    return RUN_STATUS_BY_KIND[s.kind]


def evaluate(graph: WorkflowGraph, run: RunContext, to_state: str, actor: str) -> Decision:
    """Decide whether `actor` ('SYSTEM' or 'HUMAN') may move the run to `to_state`."""
    assert actor in ("SYSTEM", "HUMAN"), f"actor must be SYSTEM or HUMAN, got {actor}"

    if run.current_state not in graph.states:
        return Decision(False, f"current state {run.current_state} is unknown")
    if to_state not in graph.states:
        return Decision(False, f"target state {to_state} is unknown")
    if not graph.states[to_state].enabled:
        return Decision(False, f"state {to_state} is not enabled in this phase")

    t = graph.find(run.current_state, to_state)
    if t is None:
        return Decision(False, f"transition {run.current_state} -> {to_state} is not allowed")
    if not t.enabled:
        return Decision(False, f"transition {run.current_state} -> {to_state} is disabled")
    if t.actor != "ANY" and t.actor != actor:
        return Decision(False, f"transition {run.current_state} -> {to_state} requires a {t.actor} actor")

    if t.guard == GUARD_RETRY:
        if not run.failed_from_state:
            return Decision(False, "retry requires the state the run failed from")
        expected = graph.states[run.failed_from_state].retry_to
        if to_state != expected:
            return Decision(False, f"run failed in {run.failed_from_state}; retry must go to {expected}")
    elif t.guard is not None:
        return Decision(False, f"unknown guard {t.guard}")

    return Decision(True, "ok", run_status_for(graph, to_state), graph.states[to_state].stage)


def failed_from_after(run: RunContext, to_state: str) -> Optional[str]:
    """Value of FAILED_FROM_STATE after the transition."""
    if to_state == FAILED_STATE:
        return run.current_state
    if run.current_state == FAILED_STATE and to_state != CANCELLED_STATE:
        return None
    return run.failed_from_state


def stage_order(graph: WorkflowGraph) -> List[str]:
    first_ordinal: Dict[str, int] = {}
    for s in graph.states.values():
        if s.enabled and s.stage:
            first_ordinal[s.stage] = min(first_ordinal.get(s.stage, s.ordinal), s.ordinal)
    return sorted(first_ordinal, key=first_ordinal.__getitem__)


FORK_AFTER = "STTM"
FORK_STAGES = frozenset({"SODA", "DBT"})


def stage_rail(graph: WorkflowGraph, current_state: str, interrupted_from: Optional[str]) -> List[dict]:
    """Per-stage status for the UI: COMPLETE, ACTIVE, REVIEW_REQUIRED, BLOCKED, FAILED, CANCELLED, LOCKED.

    `interrupted_from` is the state a FAILED or CANCELLED run was in when it stopped.
    After STTM is done, Soda and dbt stay unlocked together — they are not a linear gate.
    """
    stages = stage_order(graph)
    current = graph.states[current_state]

    if current_state == "COMPLETED":
        return [{"stage": st, "status": "COMPLETE"} for st in stages]

    if current_state in (FAILED_STATE, CANCELLED_STATE):
        assert interrupted_from in graph.states, \
            f"{current_state} run needs the state it stopped in, got {interrupted_from}"
        anchor = graph.states[interrupted_from].stage
        anchor_status = "FAILED" if current_state == FAILED_STATE else "CANCELLED"
    else:
        anchor = current.stage
        anchor_status = {"REVIEW": "REVIEW_REQUIRED", "BLOCKED": "BLOCKED", "DONE": "COMPLETE"}.get(current.kind, "ACTIVE")

    anchor_index = stages.index(anchor)
    sttm_index = stages.index(FORK_AFTER) if FORK_AFTER in stages else -1
    # A finished stage (LANDING_COMPLETE, PROFILING_COMPLETE, ...) opens the next stage.
    opened_next = current.kind == "DONE" and current_state not in (FAILED_STATE, CANCELLED_STATE)
    sttm_done = sttm_index >= 0 and (anchor_index > sttm_index or (opened_next and anchor == FORK_AFTER))
    rail = []
    for i, st in enumerate(stages):
        if sttm_done and st in FORK_STAGES:
            if st == anchor:
                status = anchor_status
            elif anchor in {"VALIDATION", "REVIEW"} and st == "DBT":
                status = "COMPLETE"
            else:
                status = "ACTIVE"
        elif sttm_done and st == "VALIDATION" and anchor in {"SODA", "DBT", "VALIDATION"}:
            status = anchor_status if st == anchor else "ACTIVE"
        elif i < anchor_index or (opened_next and i == anchor_index):
            status = "COMPLETE"
        elif opened_next and i == anchor_index + 1:
            status = "ACTIVE"
        elif i == anchor_index:
            status = anchor_status
        else:
            status = "LOCKED"
        rail.append({"stage": st, "status": status})
    return rail
