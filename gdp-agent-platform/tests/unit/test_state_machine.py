from pathlib import Path

import pytest

from services.workflow.graph import load_graph
from services.workflow.state_machine import RunContext, evaluate, failed_from_after, stage_rail

GRAPH_FILE = Path(__file__).resolve().parents[2] / "snowflake" / "database" / "seed" / "workflow_graph.json"


@pytest.fixture(scope="module")
def graph():
    return load_graph(GRAPH_FILE)


def test_system_forward_transition(graph):
    d = evaluate(graph, RunContext("ACCESS_APPROVED"), "LANDING_PENDING", "SYSTEM")
    assert d.allowed and d.new_status == "IN_PROGRESS" and d.new_stage == "SOURCE"


def test_skipping_stages_is_rejected(graph):
    d = evaluate(graph, RunContext("ACCESS_APPROVED"), "PROFILING_PENDING", "SYSTEM")
    assert not d.allowed and "not allowed" in d.reason


def test_system_cannot_approve_review_gate(graph):
    d = evaluate(graph, RunContext("MAPPING_REVIEW"), "MAPPING_APPROVED", "SYSTEM")
    assert not d.allowed and "HUMAN" in d.reason


def test_human_can_approve_review_gate(graph):
    d = evaluate(graph, RunContext("MAPPING_REVIEW"), "MAPPING_APPROVED", "HUMAN")
    assert d.allowed


def test_human_cannot_drive_system_steps(graph):
    d = evaluate(graph, RunContext("LANDING_PENDING"), "LANDING_RUNNING", "HUMAN")
    assert not d.allowed


def test_entering_review_sets_awaiting_review(graph):
    d = evaluate(graph, RunContext("MAPPING_PENDING"), "MAPPING_REVIEW", "SYSTEM")
    assert d.allowed and d.new_status == "AWAITING_REVIEW"


def test_phase2_blocked(graph):
    d = evaluate(graph, RunContext("COMPLETED"), "DEPLOYMENT_PENDING", "SYSTEM")
    assert not d.allowed and "not enabled" in d.reason


def test_validation_failure_blocks_review(graph):
    d = evaluate(graph, RunContext("VALIDATION_FAILED"), "DBT_REVIEW", "SYSTEM")
    assert not d.allowed
    assert evaluate(graph, RunContext("VALIDATION_FAILED"), "DBT_PENDING", "HUMAN").allowed


def test_retry_returns_to_retry_target_of_failed_state(graph):
    ctx = RunContext("FAILED", failed_from_state="LANDING_RUNNING")
    assert evaluate(graph, ctx, "LANDING_PENDING", "SYSTEM").allowed
    assert not evaluate(graph, ctx, "PROFILING_PENDING", "SYSTEM").allowed
    assert not evaluate(graph, RunContext("FAILED"), "LANDING_PENDING", "SYSTEM").allowed


def test_failed_from_bookkeeping():
    assert failed_from_after(RunContext("LANDING_RUNNING"), "FAILED") == "LANDING_RUNNING"
    assert failed_from_after(RunContext("FAILED", "LANDING_RUNNING"), "LANDING_PENDING") is None
    assert failed_from_after(RunContext("FAILED", "LANDING_RUNNING"), "CANCELLED") == "LANDING_RUNNING"


def test_cancel_from_review_by_human(graph):
    assert evaluate(graph, RunContext("STTM_REVIEW"), "CANCELLED", "HUMAN").allowed


def test_stage_rail_mapping_review(graph):
    rail = {r["stage"]: r["status"] for r in stage_rail(graph, "MAPPING_REVIEW", None)}
    assert rail["PROFILING"] == "COMPLETE"
    assert rail["MAPPING"] == "REVIEW_REQUIRED"
    assert rail["STTM"] == "LOCKED" and rail["DBT"] == "LOCKED"
    assert "DEPLOYMENT" not in rail


def test_finished_stage_opens_the_next_one(graph):
    rail = {r["stage"]: r["status"] for r in stage_rail(graph, "LANDING_COMPLETE", None)}
    assert rail["SOURCE"] == "COMPLETE"
    assert rail["PROFILING"] == "ACTIVE"
    assert rail["DOMAIN"] == "LOCKED"


@pytest.mark.parametrize("state", ["SOURCE_REGISTERED", "ACCESS_APPROVED", "LANDING_RUNNING"])
def test_access_and_landing_checkpoints_keep_source_active(graph, state):
    rail = {r["stage"]: r["status"] for r in stage_rail(graph, state, None)}
    assert rail["SOURCE"] == "ACTIVE" and rail["PROFILING"] == "LOCKED"


def test_stage_rail_failed_anchors_on_failed_stage(graph):
    rail = {r["stage"]: r["status"] for r in stage_rail(graph, "FAILED", "LANDING_RUNNING")}
    assert rail["SOURCE"] == "FAILED" and rail["PROFILING"] == "LOCKED"


def test_stage_rail_order(graph):
    stages = [r["stage"] for r in stage_rail(graph, "CREATED", None)]
    assert stages == ["SOURCE", "PROFILING", "DOMAIN", "MAPPING",
                      "STTM", "SODA", "DBT", "VALIDATION", "REVIEW"]


def test_sttm_approval_opens_soda_and_dbt(graph):
    rail = {r["stage"]: r["status"] for r in stage_rail(graph, "STTM_APPROVED", None)}
    assert rail["STTM"] == "COMPLETE"
    assert rail["SODA"] == "ACTIVE" and rail["DBT"] == "ACTIVE"
    assert rail["VALIDATION"] == "LOCKED"


def test_soda_review_does_not_lock_dbt(graph):
    rail = {r["stage"]: r["status"] for r in stage_rail(graph, "SODA_REVIEW", None)}
    assert rail["SODA"] == "REVIEW_REQUIRED"
    assert rail["DBT"] == "ACTIVE"
    assert rail["VALIDATION"] == "ACTIVE"
