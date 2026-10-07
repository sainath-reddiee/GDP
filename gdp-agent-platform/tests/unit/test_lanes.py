from services.workflow.lanes import apply, lanes

RAIL = [{"stage": "STTM", "status": "COMPLETE"}, {"stage": "SODA", "status": "ACTIVE"},
        {"stage": "DBT", "status": "COMPLETE"}, {"stage": "VALIDATION", "status": "COMPLETE"},
        {"stage": "REVIEW", "status": "REVIEW_REQUIRED"}]


def test_data_quality_is_complete_once_every_check_is_decided_even_after_dbt_moved_on():
    out = lanes("DBT_REVIEW", {"APPROVED": 68}, None, 0)
    assert out["SODA"] == {"status": "COMPLETE", "note": "68 approved", "done": True}
    assert lanes("DBT_REVIEW", {"APPROVED": 60, "PROPOSED": 8}, None, 0)["SODA"]["note"] == "8 to confirm"


def test_code_review_waits_for_every_lane():
    out = lanes("DBT_REVIEW", {"APPROVED": 68}, None, 0)
    assert not out["gate"]["ready"] and out["gate"]["waiting_on"] == ["QA tests: sign off"]
    stages = apply(RAIL, "DBT_REVIEW", out)
    review = next(s for s in stages if s["stage"] == "REVIEW")
    assert review["status"] == "LOCKED" and review["note"] == "waiting on 1"
    assert [s["stage"] for s in stages][:3] == ["STTM", "QA", "SODA"]  # QA lane listed with the others


def test_signed_off_qa_and_decided_checks_open_code_review():
    out = lanes("DBT_REVIEW", {"APPROVED": 5, "REJECTED": 2}, {"decision": "APPROVED"}, 0)
    assert out["gate"]["ready"]
    review = next(s for s in apply(RAIL, "DBT_REVIEW", out) if s["stage"] == "REVIEW")
    assert review["status"] == "REVIEW_REQUIRED"


def test_rejected_qa_blocks_and_validation_must_pass():
    out = lanes("VALIDATION_PENDING", {"APPROVED": 5}, {"decision": "REJECTED"}, 0)
    assert out["QA"]["status"] == "BLOCKED"
    assert out["gate"]["waiting_on"] == ["QA tests: rejected", "Validation: not passed yet"]
    assert lanes("MAPPING_REVIEW", {}, None, 0) is None
