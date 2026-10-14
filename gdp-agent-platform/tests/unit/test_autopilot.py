"""Autopilot to the next gate: the next automatic step for every state of both pipelines, where it stops for a
person, the star schema plan of a Gold run, and the acceleration evidence."""

from datetime import datetime

import pytest

from services.gold import star
from services.gold.propose import propose
from services.workflow import accelerator as acc
from services.workflow.autopilot import next_action
from tests.unit.test_gold_propose import CONSUMES, HUB, MASTERED, ROLE

SILVER = {"pipeline": "SILVER", "has_target": True, "planned_tables": 2}


@pytest.mark.parametrize("state,facts,expected", [
    ("CREATED", SILVER, "prepare_source"),
    ("SOURCE_REGISTERED", SILVER, "prepare_source"),
    ("LANDING_COMPLETE", SILVER, "profile"),
    ("PROFILING_COMPLETE", SILVER, "identify_domain"),
    ("DOMAIN_IDENTIFIED", SILVER, "generate_mapping"),
    ("MAPPING_APPROVED", SILVER, "generate_sttm"),
    ("STTM_APPROVED", SILVER, "generate_soda"),
    ("SODA_REVIEW", {**SILVER, "soda_generated": True}, "generate_dbt"),
    ("SODA_REVIEW", {**SILVER, "soda_generated": True, "dbt_generated": True}, "validate"),
    ("VALIDATION_PENDING", {**SILVER, "soda_generated": True, "dbt_generated": True}, "validate"),
])
def test_automatic_steps(state, facts, expected):
    out = next_action(state, facts)
    assert out["kind"] == "ACTION" and out["action"] == expected


@pytest.mark.parametrize("state,facts,where", [
    ("MAPPING_REVIEW", SILVER, "mapping"),
    ("STTM_REVIEW", SILVER, "sttm"),
    ("SODA_REVIEW", {**SILVER, "soda_generated": True, "dbt_generated": True, "validated": True}, "quality"),
    ("VALIDATION_PASSED", {**SILVER, "soda_generated": True, "dbt_generated": True, "validated": True}, "review"),
    ("DBT_REVIEW", SILVER, "code review"),
    ("DOMAIN_IDENTIFIED", {**SILVER, "has_target": False}, "target model"),
])
def test_stops_at_every_human_gate(state, facts, where):
    out = next_action(state, facts)
    assert out["kind"] == "GATE" and out["where"] == where


def test_gold_run_proposes_waits_for_approval_then_builds_its_star_schema():
    g = {"pipeline": "GOLD", "has_target": False, "planned_tables": 1}
    assert next_action("DOMAIN_IDENTIFIED", {**g, "gold_model": {}})["action"] == "propose_gold_model"
    assert next_action("DOMAIN_IDENTIFIED", {**g, "gold_model": {"open": True}})["where"] == "gold model"
    assert next_action("DOMAIN_IDENTIFIED", {**g, "gold_model": {"approved": True}})["action"] == "build_star_schema"
    assert next_action("DOMAIN_IDENTIFIED", {**g, "gold_model": {"approved": True}, "has_children": True})["kind"] == "GATE"
    # a table run (pinned) maps like any run
    assert next_action("DOMAIN_IDENTIFIED", {"pipeline": "GOLD", "has_target": True})["action"] == "generate_mapping"


def test_failures_running_steps_and_the_end():
    assert next_action("FAILED", {"failure_reason": "x"})["kind"] == "BLOCKED"
    assert next_action("VALIDATION_FAILED", SILVER)["kind"] == "BLOCKED"
    assert next_action("PROFILING_RUNNING", SILVER)["kind"] == "WAIT"
    assert next_action("COMPLETED", SILVER)["kind"] == "DONE"
    assert next_action("CREATED", {**SILVER, "planned_tables": 0})["kind"] == "BLOCKED"


def test_star_schema_plan_one_table_run_per_fact_and_bridge():
    out = propose("OPPORTUNITY", [HUB, ROLE], consumes=CONSUMES, mastered=MASTERED)
    layout = star.plan(out["spec"], out["lines"], [HUB, ROLE])
    assert [t["entity"] for t in layout["tables"]] == ["FACT_OPPORTUNITY", "FACT_OPPORTUNITY_COMPANY_ROLE"]
    assert layout["tables"][0]["source_table"] == "DEV_GDP_SILVER_DB.OPPORTUNITY.OPPORTUNITY_CORE"
    assert layout["tables"][1]["table_type"] == "BRIDGE"
    assert "DIM_OPPORTUNITY_STAGE" in layout["dimensions"] and not layout["unsourced"]
    assert star.least_advanced(["STTM_REVIEW", "MAPPING_REVIEW", "CANCELLED"], {"MAPPING_REVIEW": 110, "STTM_REVIEW": 140}) == "MAPPING_REVIEW"


def test_acceleration_evidence():
    assert acc.generated_share(["APPROVED", "APPROVED", "MODIFIED", "REJECTED"]) == 50.0
    assert acc.generated_share([]) is None
    t = lambda m: datetime(2026, 10, 14, 10, m)   # noqa: E731
    events = [{"from_state": None, "to_state": "CREATED", "actor_type": "SYSTEM", "created_at": t(0)},
              {"from_state": "CREATED", "to_state": "LANDING_COMPLETE", "actor_type": "SYSTEM", "created_at": t(2)},
              {"from_state": "LANDING_COMPLETE", "to_state": "MAPPING_REVIEW", "actor_type": "SYSTEM", "created_at": t(5)},
              {"from_state": "MAPPING_REVIEW", "to_state": "MAPPING_PENDING", "actor_type": "HUMAN", "created_at": t(9)},
              {"from_state": "MAPPING_PENDING", "to_state": "MAPPING_REVIEW", "actor_type": "SYSTEM", "created_at": t(10)},
              {"from_state": "MAPPING_REVIEW", "to_state": "MAPPING_APPROVED", "actor_type": "HUMAN", "created_at": t(20)}]
    stages = {s["stage"]: s for s in acc.stage_times(events, {"CREATED": "SOURCE", "LANDING_COMPLETE": "SOURCE",
                                                               "MAPPING_REVIEW": "MAPPING", "MAPPING_PENDING": "MAPPING",
                                                               "MAPPING_APPROVED": "MAPPING"})}
    assert stages["SOURCE"]["minutes"] == 5.0 and stages["MAPPING"]["human_gate"]
    gates = acc.gates(events)
    assert gates == [{"gate": "Mapping", "state": "MAPPING_REVIEW", "passed": True, "sent_back": 1, "first_time_right": False}]
