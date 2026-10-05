from collections import deque
from pathlib import Path

import pytest

from services.workflow.graph import build_graph, load_graph

GRAPH_FILE = Path(__file__).resolve().parents[2] / "snowflake" / "database" / "seed" / "workflow_graph.json"

PHASE1_STATES = {
    "CREATED", "SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING",
    "LANDING_RUNNING", "LANDING_COMPLETE", "PROFILING_PENDING", "PROFILING_RUNNING", "PROFILING_COMPLETE",
    "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "MAPPING_REVIEW", "MAPPING_APPROVED", "STTM_PENDING",
    "STTM_REVIEW", "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING",
    "DBT_GENERATING", "DBT_REVIEW", "DBT_APPROVED", "VALIDATION_PENDING", "VALIDATION_RUNNING",
    "VALIDATION_PASSED", "VALIDATION_FAILED", "COMPLETED", "FAILED", "CANCELLED",
}
PHASE2_STATES = {"DEPLOYMENT_PENDING", "DEPLOYED", "PIPELINE_ENABLED"}


@pytest.fixture(scope="module")
def graph():
    return load_graph(GRAPH_FILE)


def reachable(graph, start):
    seen, queue = {start}, deque([start])
    while queue:
        for t in graph.outgoing(queue.popleft()):
            if t.enabled and t.to_state not in seen:
                seen.add(t.to_state)
                queue.append(t.to_state)
    return seen


def test_states_match_master_prompt(graph):
    assert set(graph.states) == PHASE1_STATES | PHASE2_STATES


def test_phase2_states_disabled_and_unreachable(graph):
    for name in PHASE2_STATES:
        assert not graph.states[name].enabled
    assert not (reachable(graph, "CREATED") & PHASE2_STATES)


def test_every_phase1_state_reachable_from_created(graph):
    assert reachable(graph, "CREATED") == PHASE1_STATES


def test_happy_path_order(graph):
    path = ["CREATED", "SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING",
            "LANDING_RUNNING", "LANDING_COMPLETE", "PROFILING_PENDING", "PROFILING_RUNNING",
            "PROFILING_COMPLETE", "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "MAPPING_REVIEW", "MAPPING_APPROVED",
            "STTM_PENDING", "STTM_REVIEW", "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
            "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING", "VALIDATION_PASSED",
            "DBT_REVIEW", "DBT_APPROVED", "COMPLETED"]
    for a, b in zip(path, path[1:]):
        t = graph.find(a, b)
        assert t is not None and t.enabled, f"missing {a}->{b}"
    assert graph.find("STTM_APPROVED", "DBT_PENDING") is not None


def test_human_gates_are_exactly_the_review_approvals(graph):
    approvals = {(t.from_state, t.to_state) for t in graph.transitions
                 if t.actor == "HUMAN" and t.to_state.endswith("_APPROVED")}
    assert approvals == {
        ("MAPPING_REVIEW", "MAPPING_APPROVED"),
        ("STTM_REVIEW", "STTM_APPROVED"),
        ("SODA_REVIEW", "SODA_APPROVED"),
        ("DBT_REVIEW", "DBT_APPROVED"),
    }
    # Nothing but a HUMAN may leave a REVIEW state forward.
    for t in graph.transitions:
        if graph.states[t.from_state].kind == "REVIEW" and t.to_state not in ("FAILED", "CANCELLED"):
            assert t.actor == "HUMAN", f"{t.from_state}->{t.to_state} must be HUMAN"


def test_terminal_states_have_no_enabled_exits(graph):
    for name in ("COMPLETED", "CANCELLED"):
        assert not [t for t in graph.outgoing(name) if t.enabled]


def test_every_active_state_can_fail_and_cancel(graph):
    for s in graph.states.values():
        if s.enabled and s.kind not in ("TERMINAL", "FAILED"):
            assert graph.find(s.state, "FAILED").enabled
            assert graph.find(s.state, "CANCELLED").enabled


def test_rejects_duplicate_transition():
    raw = {"graph_version": "t", "states": [
        {"state": "CREATED", "stage": "S", "kind": "PENDING", "ordinal": 1, "phase": 1},
        {"state": "FAILED", "stage": None, "kind": "FAILED", "ordinal": 2, "phase": 1},
        {"state": "CANCELLED", "stage": None, "kind": "TERMINAL", "ordinal": 3, "phase": 1},
    ], "transitions": [{"from": "CREATED", "to": "FAILED", "actor": "SYSTEM"}]}
    with pytest.raises(AssertionError, match="duplicates an implicit"):
        build_graph(raw)
