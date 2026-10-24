"""Autopilot to the next gate: the next automatic step for every state of both pipelines, where it stops for a
person, the star schema plan of a Gold run, and the acceleration evidence."""

import sys
from datetime import datetime
from pathlib import Path

import pytest

from services.gold import star
from services.gold.propose import propose
from services.workflow import accelerator as acc
from services.workflow.autopilot import next_action
from tests.unit.test_gold_propose import CONSUMES, HUB, MASTERED, ROLE

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

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


FORWARD = {**BUILT, "publish_ready": True, "may_signoff_qa": False, "may_approve_code": False}


def test_build_end_to_end_opens_the_pull_request_then_waits_on_code_review():
    # a Forward Engineering Lead: validated code is committed into a PR, QA and code review are the Data Developer's
    assert next_action("DBT_REVIEW", FORWARD)["action"] == "publish_pr"
    out = next_action("DBT_REVIEW", {**FORWARD, "published": True})
    assert out["kind"] == "GATE" and out["where"] == "code review" and "Pull request opened" in out["label"]
    # a person who may sign off QA and review code goes on to approve
    assert next_action("DBT_REVIEW", {**FORWARD, "published": True, "may_signoff_qa": True})["action"] == "signoff_qa"
    assert next_action("DBT_REVIEW", {**FORWARD, "published": True, "qa_signed": True, "may_approve_code": True})["action"] == "approve_code"
    # GitHub publishing not set up: no PR step, still waits on code review
    assert next_action("DBT_REVIEW", {**FORWARD, "publish_ready": False})["where"] == "code review"


def _drive(monkeypatch, grants, approves_dq=True):
    """drive() over a simulated run: each step moves the state as the real procedure does; preview_call allows only
    the privileges in grants."""
    import app.main  # noqa: F401  (registers the routers, autopilot_api among them)
    from app import autopilot_api as api, governance
    from services.governance.policy import privilege_for

    run = {"state": "STTM_APPROVED", "facts": {**SILVER, "autonomous": True, "publish_ready": True,
                                                 "may_signoff_qa": "QA.SIGNOFF" in grants,
                                                 "may_approve_code": "REVIEW.APPROVE" in grants}}
    effects = {
        "generate_soda": ("SODA_REVIEW", {"soda_generated": True, "soda_proposed": True}),
        "approve_soda": ("SODA_APPROVED", {"soda_proposed": False}),
        "generate_dbt": ("VALIDATION_PENDING", {"dbt_generated": True}),
        "validate": ("DBT_REVIEW", {"validated": True}),
        "publish_pr": ("DBT_REVIEW", {"published": True}),
        "signoff_qa": ("DBT_REVIEW", {"qa_signed": True}),
        "approve_code": ("COMPLETED", {}),
    }
    calls = []

    def call_for(action, on_behalf=False):
        def call():
            calls.append(action + (" (on behalf)" if on_behalf else ""))
            run["state"], change = effects[action]
            run["facts"].update(change)
            return {"ok": True}
        return call

    def fake_call(db, run_id, action):
        body = {"to_state": "SODA_APPROVED", "decision": "APPROVE"} if action == "approve_soda" else None
        path = {"publish_pr": f"/api/runs/{run_id}/dbt/publish", "approve_soda": f"/api/runs/{run_id}/review",
                "signoff_qa": f"/api/runs/{run_id}/qa/signoff"}.get(action, f"/api/runs/{run_id}/soda")
        return "POST", path, call_for(action), body

    def preview(db, method, path, body=None):
        privilege = privilege_for(method, path, body)[0]
        return {"verdict": "ALLOW" if privilege in grants else "DENY", "privilege": privilege}

    monkeypatch.setattr(api, "_facts", lambda db, run_id: (run["state"], dict(run["facts"]), {}))
    monkeypatch.setattr(api, "_call", fake_call)
    monkeypatch.setattr(api, "_approves_dq", lambda db: approves_dq)
    monkeypatch.setattr(api, "_approve_soda", lambda db, run_id, on_behalf=False: call_for("approve_soda", on_behalf)())
    monkeypatch.setattr(governance, "preview_call", preview)
    job = {"steps": []}
    return api.drive(None, "run-1", job), calls


LEAD = {"RUN.OPERATE", "SODA.GENERATE", "DBT.GENERATE", "DBT.EDIT", "VALIDATION.RUN"}


def test_drive_as_forward_lead_approves_dq_opens_the_pr_and_stops_at_code_review(monkeypatch):
    from services.governance.policy import privilege_for

    grants = LEAD | {privilege_for("POST", f"/api/runs/r/{p}", None)[0] for p in ("soda", "dbt", "validation", "dbt/publish")}
    stop, calls = _drive(monkeypatch, grants)
    assert calls == ["generate_soda", "approve_soda (on behalf)", "generate_dbt", "validate", "publish_pr"]
    assert stop["kind"] == "GATE" and stop["where"] == "code review" and "Pull request opened" in stop["label"]


def test_drive_stops_at_dq_when_autopilot_may_not_approve_it(monkeypatch):
    from services.governance.policy import privilege_for

    grants = LEAD | {privilege_for("POST", f"/api/runs/r/{p}", None)[0] for p in ("soda", "dbt", "validation", "dbt/publish")}
    stop, calls = _drive(monkeypatch, grants, approves_dq=False)
    assert calls == ["generate_soda"] and stop["kind"] == "BLOCKED" and "SODA.APPROVE" in stop["label"]


@pytest.mark.parametrize("status,ok", [("PUBLISHED", True), ("NO_CHANGES", True), ("AUTH", False), ("TOKEN_SCOPE", False),
                                       ("NOT_CONFIGURED", False), ("FAILED", False)])
def test_publish_step_stops_with_the_reason_unless_the_pr_is_there(monkeypatch, status, ok):
    import app.main as m
    from app import autopilot_api as api

    monkeypatch.setattr(m, "publish_dbt", lambda run_id, body, db: {"status": status, "detail": f"{status} detail"})
    out = api._publish_pr(None, "run-1")
    assert ("error" not in out) == ok
    if not ok:
        assert out["error"] == f"{status} detail"


def test_dq_on_behalf_goes_through_the_narrow_procedure(monkeypatch):
    """Sign-in mode runs calls as the person's Snowflake role: the on-behalf approval uses CORE.AUTOPILOT_APPROVE_DQ
    (owner's rights, DATA_ENGINEER usage), never REVIEW_TRANSITION, which only REVIEWER may call."""
    import app.main  # noqa: F401
    from app import autopilot_api as api

    class Db:
        def __init__(self):
            self.calls = []

        def call(self, sql, params=()):
            self.calls.append(sql)
            return {"ok": True}

        def query(self, sql, params=()):
            raise AssertionError("no direct reads or reviews on behalf")

    db = Db()
    assert api._approve_soda(db, "run-1", on_behalf=True) == {"ok": True}
    assert db.calls == ["CALL CORE.AUTOPILOT_APPROVE_DQ(%s, %s)"]


def test_the_procedure_refuses_when_the_setting_is_off():
    from services.workflow.procedures import autopilot_approves_dq

    assert autopilot_approves_dq(None) and autopilot_approves_dq("true") and autopilot_approves_dq(True)
    assert not autopilot_approves_dq("false") and not autopilot_approves_dq(False)
