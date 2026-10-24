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


AUTO = {**SILVER, "autonomous": True}
BUILT = {**AUTO, "soda_generated": True, "dbt_generated": True, "validated": True}


@pytest.mark.parametrize("state,facts,expected", [
    ("MAPPING_REVIEW", AUTO, "approve_mapping"),
    ("STTM_REVIEW", AUTO, "approve_sttm"),
    ("STTM_APPROVED", AUTO, "generate_soda"),
    ("SODA_REVIEW", {**AUTO, "soda_generated": True, "soda_proposed": True}, "approve_soda"),
    # the screenshot case: code generated and validated while the run waited at the checks gate
    ("SODA_REVIEW", {**BUILT, "soda_proposed": True}, "approve_soda"),
    ("SODA_APPROVED", BUILT, "validate"),
    ("SODA_APPROVED", {**AUTO, "soda_generated": True}, "generate_dbt"),
    ("DBT_REVIEW", {**BUILT, "soda_proposed": True}, "approve_soda"),
    ("DBT_REVIEW", BUILT, "signoff_qa"),
    ("DBT_REVIEW", {**BUILT, "qa_signed": True}, "approve_code"),
])
def test_autonomous_decides_every_table_gate(state, facts, expected):
    out = next_action(state, facts)
    assert out["kind"] == "ACTION" and out["action"] == expected


def test_autonomous_still_stops_where_only_a_person_can_act():
    assert next_action("VALIDATION_FAILED", AUTO)["kind"] == "BLOCKED"
    assert next_action("DOMAIN_IDENTIFIED", {**AUTO, "has_target": False})["where"] == "target model"
    gold = {"pipeline": "GOLD", "has_target": False, "autonomous": True, "gold_model": {"open": True}}
    assert next_action("DOMAIN_IDENTIFIED", gold)["where"] == "gold model"
    assert next_action("COMPLETED", AUTO)["kind"] == "DONE"


def test_auto_mapping_decisions_best_first_and_one_source_per_target():
    from services.workflow.autopilot import auto_mapping_decisions

    cands = [
        {"candidate_id": "a", "source_column_id": "S1", "target_column_id": "T1", "target_table_id": "TB", "rank": 1,
         "final_score": 0.6, "recommendation": "MANUAL"},
        {"candidate_id": "b", "source_column_id": "S2", "target_column_id": "T1", "target_table_id": "TB", "rank": 1,
         "final_score": 0.9, "recommendation": "AUTO_SUGGEST"},
        {"candidate_id": "c", "source_column_id": "S3", "target_column_id": "T3", "target_table_id": "TB", "rank": 1,
         "final_score": 0.8, "recommendation": "AUTO_SUGGEST"},
        {"candidate_id": "d", "source_column_id": "S3", "target_column_id": "T4", "target_table_id": "TB", "rank": 2,
         "final_score": 0.7, "recommendation": "MANUAL"},
        {"candidate_id": "e", "source_column_id": "S4", "target_column_id": "T5", "target_table_id": "TB", "rank": 1,
         "final_score": 0.9, "recommendation": "AUTO_SUGGEST"},
    ]
    out = {d["candidate_id"]: d["decision"] for d in auto_mapping_decisions(cands, [("S4", "TB")], [])}
    assert out == {"b": "APPROVED", "c": "APPROVED", "a": "REJECTED"}
    every = auto_mapping_decisions(cands, [], [("T3", "TB")])
    assert {d["candidate_id"]: d["decision"] for d in every}["c"] == "REJECTED"
    assert all(d["business_justification"].startswith("Autopilot:") for d in every)


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


def test_star_schema_publishes_as_one_pull_request():
    schema_fact = "version: 2\n\nmodels:\n  - name: fact_a\n    columns:\n      - name: x\n  - name: dim_s\n    columns:\n      - name: s\n"
    schema_bridge = "version: 2\n\nmodels:\n  - name: bridge_b\n    columns:\n      - name: y\n  - name: dim_s\n    columns:\n      - name: s\n"
    out = star.merge_files([
        {"entity": "FACT_A", "files": {"models/gold/d/fact_a.sql": "a", "models/gold/d/dim_s.sql": "s", "models/gold/d/_d__gold.yml": schema_fact,
                                       "soda/checks.yml": "checks for fact_a:", "mappings/sttm.json": "{}", "dbt_project.yml": "p1",
                                       "release/branch.json": "{}"}},
        {"entity": "BRIDGE_B", "files": {"models/gold/d/bridge_b.sql": "b", "models/gold/d/dim_s.sql": "s", "models/gold/d/_d__gold.yml": schema_bridge,
                                         "soda/checks.yml": "checks for bridge_b:", "dbt_project.yml": "p2"}},
    ])
    files = out["files"]
    assert {"models/gold/d/fact_a.sql", "models/gold/d/bridge_b.sql", "models/gold/d/dim_s.sql", "soda/gold/fact_a.yml",
            "soda/gold/bridge_b.yml", "mappings/gold/fact_a.sttm.json", "dbt_project.yml"} <= set(files)
    assert "release/branch.json" not in files and files["dbt_project.yml"] == "p1" and out["conflicts"] == []
    yml = files["models/gold/d/_d__gold.yml"]
    assert yml.count("- name: dim_s") == 1 and "- name: fact_a" in yml and "- name: bridge_b" in yml
    clash = star.merge_files([{"entity": "A", "files": {"models/x.sql": "1"}}, {"entity": "B", "files": {"models/x.sql": "2"}}])
    assert clash["conflicts"] == ["models/x.sql"] and clash["files"]["models/x.sql"] == "1"
