"""Second batch of captured signals: gate review reasons, model and proposal rejections, DQ Store suite decisions,
incident postmortems, dbt review findings, failing QA tests, recurring failures across runs and QA outcomes."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.knowledge import signals  # noqa: E402


def test_a_stage_sent_back_keeps_its_reason():
    run = {"RUN_ID": "r1"}
    item = signals.gate_feedback(run, "STTM", "STTM_REVIEW", "REQUEST_CHANGES", "EMAIL must be lower-cased", None,
                                 "CUSTOMER")
    assert item["kind"] == "BUSINESS_RULE" and item["key"] == "review.sttm.r1"
    assert "lower-cased" in item["content"] and item["content_json"]["target_table"] == "CUSTOMER"
    assert signals.gate_feedback(run, "STTM", "STTM_REVIEW", "APPROVE", "fine", None) is None
    assert signals.gate_feedback(run, "STTM", "STTM_REVIEW", "REJECT", None, None) is None


def test_model_and_proposal_rejections():
    item = signals.model_rejection("SALES", 3, "ORDER grain is wrong", ["ORDER_FACT"])
    assert item["key"] == "model.review.SALES.v3" and "ORDER_FACT" in item["content"]
    change = signals.change_rejection("SALES", "DB.SILVER.CUSTOMER", "TABLE_MODEL", "duplicate of PARTY")
    assert change["key"] == "model.change.rejected.SALES.CUSTOMER" and change["content_json"]["target_table"] == "CUSTOMER"
    assert signals.model_rejection("SALES", 1, "  ") is None


def test_dq_suite_decisions():
    checks = [{"check_type": "NOT_NULL", "target_column": "ID"}, {"kind": "freshness"}]
    approved = signals.suite_decision("DB.SILVER.CUSTOMER", 2, "APPROVED", checks)
    assert approved["kind"] == "SODA_PATTERN" and approved["content_json"]["target_table"] == "CUSTOMER"
    assert "not_null on ID" in approved["content"] and approved["content_json"]["decision"] == "SUITE_APPROVED"
    rejected = signals.suite_decision("DB.SILVER.CUSTOMER", 3, "REJECTED", [], "too strict on EMAIL")
    assert rejected["content_json"]["decision"] == "SUITE_REJECTED"  # never read as a single rejected check
    assert signals.suite_decision("T", 1, "REJECTED", [], "") is None


def test_postmortem_keeps_cause_and_follow_ups_keyed_by_fingerprint():
    incident = {"incident_id": "i1", "fingerprint": "fp1", "dag_id": "load_crm", "env_id": "prod", "title": "Load failed"}
    item = signals.postmortem_item(incident, {"cause": "warehouse suspended", "fix": "resume it",
                                              "follow_ups": ["alert on suspension"]})
    assert item["kind"] == "INCIDENT_RESOLUTION" and item["key"] == "ops.postmortem.fp1"
    assert "warehouse suspended" in item["content"] and item["content_json"]["follow_ups"] == ["alert on suspension"]
    assert signals.postmortem_item({**incident, "fingerprint": None}, {"cause": "x"}) is None
    assert signals.postmortem_item(incident, {"cause": ""}) is None


def test_dbt_review_keeps_errors_and_warnings_only():
    findings = [{"severity": "error", "rule": "Rule 2", "message": "missing NULLIF(TRIM())"},
                {"severity": "info", "rule": "Style", "message": "fine"}]
    item = signals.dbt_review_item("DB.SILVER.CUSTOMER", "models/customer.sql", findings)
    assert item["kind"] == "DBT_PATTERN" and len(item["content_json"]["findings"]) == 1
    assert signals.dbt_review_item("T", "m.sql", [{"severity": "info", "message": "ok"}]) is None


def test_keep_never_raises(monkeypatch):
    from services.knowledge import writer

    def boom(session, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(writer, "remember", boom)
    assert signals.keep(None, "d1", signals.model_rejection("S", 1, "why"), "MODELING") is None
    assert signals.keep(None, None, signals.model_rejection("S", 1, "why"), "MODELING") is None


def test_failing_qa_tests_are_kept_with_their_outcome(monkeypatch):
    from services.knowledge import writer
    from services.qa import run as qa_run

    seen = []
    monkeypatch.setattr(writer, "remember", lambda session, **kw: seen.append(kw))
    results = [{"test_id": "t1", "outcome": "FAIL", "origin": "AI", "title": "No orphan orders", "rows_returned": 4,
                "detail": "4 orders without a customer", "sql": "select 1"},
               {"test_id": "t2", "outcome": "PASS", "origin": "USER", "title": "Keys unique", "sql": "select 2"},
               {"test_id": "t3", "outcome": "ERROR", "origin": "AI", "title": "Broken", "sql": "x"},
               {"test_id": "t4", "outcome": "FAIL", "origin": "STTM", "title": "Generated", "sql": "y"}]
    qa_run._remember(None, "r1", {"domain_id": "d1", "target": {"name": "ORDERS"}}, results)
    by_id = {kw["content_json"]["test_id"]: kw for kw in seen}
    assert set(by_id) == {"t1", "t2"}
    assert by_id["t1"]["content_json"]["last_outcome"] == "FAIL" and "4 row(s) broke it" in by_id["t1"]["content"]
    assert "FAILED" in by_id["t1"]["tags"] and "PASSED" in by_id["t2"]["tags"]


def test_recurring_failures_share_one_item_across_runs(monkeypatch):
    from services.knowledge import writer
    from services.quality import scan

    seen = []
    monkeypatch.setattr(writer, "remember", lambda session, **kw: seen.append(kw))
    monkeypatch.setattr(scan, "_rows", lambda session, sql, params: [{"EXPECTATION_ID": "e1"}])
    result = {"outcome": "FAIL", "expectation_id": "e1", "target_table": "DB.S.CUSTOMER", "target_column": "EMAIL",
              "kind": "missing", "detail": "3 nulls"}
    for run in ("run-one", "run-two"):
        scan._remember_failures(None, run, {"DOMAIN_ID": "d1"}, [result])
    assert {kw["key"] for kw in seen} == {"quality.recurring.DB.S.CUSTOMER.EMAIL.MISSING"}
    assert seen[0]["content_json"]["target_table"] == "CUSTOMER" and "by_domain" not in seen[0]


def test_qa_tests_feed_the_track_record():
    from services.knowledge import outcomes

    stats = outcomes.attribute(
        [{"lineage_id": "L1", "knowledge_id": "k1", "run_id": "r1", "stage": "QA", "uses": 1, "last_used": "x"}],
        {"L1": {"target_column": "EMAIL"}},
        [{"run_id": "r1", "stage": "QA", "table": "CUSTOMER", "column": "EMAIL", "outcome": "ACCEPTED"},
         {"run_id": "r1", "stage": "QA", "table": "CUSTOMER", "column": "EMAIL", "outcome": "REJECTED"}])
    assert "QA" in outcomes.STAGES
    assert stats[("L1", "QA")]["observed"] == 2 and stats[("L1", "QA")]["accepted"] == 1
