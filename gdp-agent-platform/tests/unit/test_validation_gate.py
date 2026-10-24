"""The validation gate: a minimum score (rules validation.min_score) and, while validation.block_on_errors holds, no
error, enforced where the run moves (VALIDATE_DBT), with the reasons recorded so the run says what to improve."""

from contextlib import contextmanager

import pytest

from services.common.rules import DEFAULTS, merged, using
from services.validation import checks
from services.validation import procedures as vp


def _check(kind, status, errors=0, warnings=0):
    return {"validation_type": kind, "status": status, "error_count": errors, "warning_count": warnings, "findings": []}


PASSING = [_check("NAMING", "PASSED"), _check("SCHEMA", "PASSED", warnings=2), _check("SODA_CONFIG", "PASSED"),
           _check("SKILL", "PASSED")]


def test_defaults_and_overrides():
    assert DEFAULTS["validation.min_score"] == 80.0 and DEFAULTS["validation.block_on_errors"] is True
    r = merged({"validation.min_score": "90", "validation.block_on_errors": False})
    assert r["validation.min_score"] == 90.0 and r["validation.block_on_errors"] is False
    # a value of the wrong kind is ignored, as for every rule
    assert merged({"validation.block_on_errors": "no"})["validation.block_on_errors"] is True


def test_score_counts_warnings_as_passed_and_leaves_out_skipped_and_summary():
    assert checks.score(PASSING) == 100.0
    results = PASSING + [_check("DBT_COMPILE", "SKIPPED", warnings=1), _check("SUMMARY", "FAILED", errors=9),
                         _check("DATATYPE", "FAILED", errors=1)]
    assert checks.score(results) == pytest.approx(80.0)
    assert checks.score([]) is None and checks.score([_check("DBT_COMPILE", "SKIPPED")]) is None


def test_gate_passes_at_the_minimum_with_no_errors():
    verdict = checks.gate(PASSING, 80.0)
    assert verdict["status"] == "PASSED" and verdict["reasons"] == [] and verdict["score"] == 100.0
    assert verdict["warnings"] == 2 and verdict["min_score"] == 80.0


def test_gate_flags_a_score_below_the_minimum():
    results = [_check("NAMING", "PASSED"), _check("SCHEMA", "FAILED", errors=1), _check("SODA_CONFIG", "FAILED", errors=2)]
    verdict = checks.gate(results, 80.0, block_on_errors=False)
    assert verdict["status"] == "FAILED" and verdict["score"] == 33.3
    assert verdict["reasons"] == ["score 33.3% is below the 80% minimum"]


def test_errors_block_even_above_the_minimum_unless_the_rule_allows_them():
    results = PASSING * 2 + [_check("DATATYPE", "FAILED", errors=1)]  # 8 of 9 = 88.9%
    assert checks.gate(results, 80.0)["reasons"] == ["1 error must be fixed"]
    assert checks.gate(results, 80.0, block_on_errors=False)["status"] == "PASSED"
    both = checks.gate(results, 95.0)
    assert both["status"] == "FAILED" and len(both["reasons"]) == 2


def test_the_gate_compares_the_exact_score_not_the_rounded_one():
    # 79.96% rounds to 80.0 for display but is still below an 80% minimum
    results = [_check(f"C{i}", "PASSED") for i in range(1999)] + [_check(f"F{i}", "FAILED") for i in range(501)]
    verdict = checks.gate(results, 80.0, block_on_errors=False)
    assert verdict["score"] == 80.0 and verdict["status"] == "FAILED"


def test_nothing_evaluated_never_passes():
    verdict = checks.gate([_check("DBT_COMPILE", "SKIPPED", warnings=1)], 80.0)
    assert verdict["status"] == "FAILED" and verdict["reasons"] == ["no check was evaluated"]


def test_summary_keeps_its_contract():
    assert checks.summary(PASSING) == ("PASSED", 0, 2)


class FakeStage:
    def __init__(self, session, run_id):
        self.state = "VALIDATION_PENDING"
        self.run = {"RUN_ID": run_id, "PIPELINE": "SILVER"}
        self.moves = []

    def require(self, *states):
        assert self.state in states

    def walk(self, states, reason):
        self.state = "VALIDATION_RUNNING"

    def move(self, to, reason, payload=None, in_transaction=None):
        if in_transaction:
            in_transaction("event")
        self.moves.append((to, reason, payload))
        self.state = to

    def fail(self, exc):
        raise AssertionError(exc)

    def payload(self):
        return {"current_state": self.state}


@pytest.fixture
def validate(monkeypatch):
    """validate_dbt over fixed check results; returns (outcome, stage, rows written)."""
    written = []
    stage_box = {}

    def make_stage(session, run_id):
        stage_box["stage"] = FakeStage(session, run_id)
        return stage_box["stage"]

    @contextmanager
    def tool_call(session, run_id, tool, inputs):
        yield type("Call", (), {"summary": "", "status": "SUCCEEDED", "error": None})()

    monkeypatch.setattr(vp, "Stage", make_stage)
    monkeypatch.setattr(vp, "tool_call", tool_call)
    monkeypatch.setattr(vp, "_current_generation", lambda s, r: {"GENERATION_ID": "g1", "STTM_ID": "s1"})
    monkeypatch.setattr(vp, "use_stage", lambda *a, **k: "guidance")
    monkeypatch.setattr(vp, "_files", lambda s, g: {"dbt_project.yml": "name: x"})
    monkeypatch.setattr(vp, "_sttm_lines", lambda s, i: [])
    monkeypatch.setattr(vp, "_compile", lambda *a: {})
    monkeypatch.setattr(vp, "_remember_fixed", lambda *a: None)
    monkeypatch.setattr(vp, "insert_rows", lambda session, table, cols, exprs, rows: written.extend(rows))
    # the code standards agent (tested in test_code_standards.py) judges nothing here unless a test says so
    monkeypatch.setattr(vp, "_review_standards", lambda *a: {"checks": stage_box.get("standards", [])})

    def run(results, rules=None, standards=None):
        stage_box["standards"] = standards or []
        monkeypatch.setattr(vp.checks, "run", lambda files, lines, soda: [dict(r) for r in results])
        monkeypatch.setattr(vp, "gold_compile", lambda result, pipeline, files: _check("DBT_COMPILE", "PASSED"))
        with using(merged(rules or {})):
            out = vp.validate_dbt(object(), "run-1")
        return out, stage_box["stage"], written

    return run


def test_validate_dbt_moves_a_run_at_the_gate_on_to_code_review(validate):
    out, stage, written = validate(PASSING)
    assert out["status"] == "PASSED" and out["score"] == 100.0 and out["min_score"] == 80.0
    assert [m[0] for m in stage.moves] == ["VALIDATION_PASSED", "DBT_REVIEW"]
    summary = [r for r in written if r[3] == "SUMMARY"][0]
    assert summary[-1]["gate"]["status"] == "PASSED"


def test_validate_dbt_flags_a_run_below_the_minimum_with_the_reason(validate):
    results = [_check("NAMING", "PASSED"), _check("SCHEMA", "FAILED", errors=1), _check("SODA_CONFIG", "FAILED", errors=1),
               _check("DATATYPE", "FAILED", errors=1)]
    out, stage, written = validate(results, {"validation.block_on_errors": False})
    # 2 of 5 evaluated (with the compile check) pass: 40%
    assert out["status"] == "FAILED" and out["score"] == 40.0
    assert out["reasons"] == ["score 40% is below the 80% minimum"]
    to, reason, payload = stage.moves[-1]
    assert to == "VALIDATION_FAILED" and "below the 80% minimum" in reason and payload["score"] == 40.0
    summary = [r for r in written if r[3] == "SUMMARY"][0]
    assert summary[4] == "FAILED" and summary[-1]["gate"]["reasons"] == out["reasons"]


def test_validate_dbt_reads_the_minimum_from_the_rules(validate):
    out, stage, _ = validate(PASSING + [_check("DATATYPE", "FAILED")], {"validation.min_score": 90})
    # 5 of 6 (with the compile check) = 83.3%: enough for the default, not for a domain that asks for 90%
    assert out["status"] == "FAILED" and out["min_score"] == 90.0
    assert stage.moves[-1][0] == "VALIDATION_FAILED"


def test_the_standards_agent_counts_in_the_score_and_replaces_the_skill_note(validate):
    agent = [_check("STANDARDS_NAMING", "PASSED"), _check("STANDARDS_TYPES", "FAILED", errors=2),
             _check("STANDARDS_KEYS", "FAILED", errors=1)]
    out, stage, _ = validate(PASSING, standards=agent)
    kinds = [r["validation_type"] for r in out["results"]]
    # the agent's areas are checks of their own: 6 of 8 pass (with the compile check), 75%, and its errors block
    assert "STANDARDS_TYPES" in kinds and kinds.count("SKILL") == 1  # PASSING carries one SKILL row of its own
    assert out["status"] == "FAILED" and out["score"] == 75.0 and stage.moves[-1][0] == "VALIDATION_FAILED"
    assert "3 errors must be fixed" in out["reasons"]


def test_with_the_agent_off_the_skill_note_is_recorded_as_before(validate):
    out, _, _ = validate([_check("NAMING", "PASSED")], {"validation.standards_review": False},
                         standards=[_check("STANDARDS_TYPES", "FAILED", errors=1)])
    kinds = [r["validation_type"] for r in out["results"]]
    assert "STANDARDS_TYPES" not in kinds and kinds[-1] == "SKILL" and out["status"] == "PASSED"
