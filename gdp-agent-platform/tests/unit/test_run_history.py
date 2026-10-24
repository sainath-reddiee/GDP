"""Run history at a glance (services/validation/history.py, GET /api/run-history)."""

import json

from services.validation.history import attempt, summarize


def summary(run_id, at, status="PASSED", gate=None, results=None, errors=0):
    body = {"results": results or []}
    if gate:
        body["gate"] = gate
    return {"run_id": run_id, "status": status, "error_count": errors, "result_json": json.dumps(body),
            "started_at": at, "completed_at": at}


CHECK = lambda kind, status: {"validation_type": kind, "status": status, "error_count": 0 if status != "FAILED" else 1}


def test_an_attempt_reads_the_gate_or_scores_the_checks_it_recorded():
    gated = attempt(summary("r", "2026-10-24", "FAILED", gate={"score": 72.0, "min_score": 80, "reasons": ["score 72% is below the 80% minimum"]}))
    assert gated["score"] == 72.0 and gated["min_score"] == 80 and gated["reasons"][0].startswith("score 72%")
    # before the gate rule: scored from the checks it recorded (skipped ones not evaluated)
    old = attempt(summary("r", "2026-10-01", results=[CHECK("NAMING", "PASSED"), CHECK("SCHEMA", "FAILED"), CHECK("DBT_COMPILE", "SKIPPED")]))
    assert old["score"] == 50.0 and old["min_score"] is None and old["reasons"] == []
    assert attempt({"run_id": "r", "status": "PASSED", "result_json": "not json"})["score"] is None


def test_runs_get_their_first_and_last_score_and_their_decisions_in_order():
    rows = [summary("a", "2026-10-02", "PASSED", gate={"score": 93.0}),
            summary("a", "2026-10-01", "FAILED", gate={"score": 68.0, "reasons": ["score 68% is below the 80% minimum"]}, errors=2),
            summary("zzz", "2026-10-01")]
    reviews = [{"run_id": "a", "stage": "DBT_REVIEW", "decision": "APPROVE", "reviewer": "DEV", "reviewed_at": "2026-10-03"},
               {"run_id": "a", "stage": "STTM", "decision": "APPROVE", "reviewer": "DESIGNER", "reviewed_at": "2026-09-30"}]
    out = summarize(["a", "b"], rows, reviews)
    assert set(out) == {"a", "b"}  # a run nobody asked for is left out
    a = out["a"]
    assert (a["attempts"], a["first_score"], a["last_score"], a["last_status"]) == (2, 68.0, 93.0, "PASSED")
    assert a["reasons"] == [] and a["failed_before"] is True and [d["reviewer"] for d in a["decisions"]] == ["DESIGNER", "DEV"]
    assert out["b"] == {"attempts": 0, "first_score": None, "last_score": None, "last_status": None, "last_at": None,
                        "reasons": [], "failed_before": False, "decisions": []}
    # passing twice is not a recovery
    twice = summarize(["c"], [summary("c", "2026-10-01"), summary("c", "2026-10-02")], [])["c"]
    assert twice["attempts"] == 2 and twice["failed_before"] is False
