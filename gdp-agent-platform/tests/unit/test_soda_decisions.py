import json

import pytest

from services.soda.decisions import DecisionPayloadError, parse_decision_payload, stored_status


def test_single_object_is_a_batch():
    items = parse_decision_payload(json.dumps({
        "expectation_id": " e1 ", "decision": "approved", "justification": "  grain  ",
    }))
    assert items == [{
        "expectation_id": "e1", "decision": "APPROVED", "justification": "grain",
        "requirement": None, "definition": None,
    }]


def test_last_duplicate_wins():
    items = parse_decision_payload(json.dumps([
        {"expectation_id": "e1", "decision": "REJECTED"},
        {"expectation_id": "e1", "decision": "APPROVED", "requirement": "keep"},
    ]))
    assert len(items) == 1
    assert items[0]["decision"] == "APPROVED"
    assert items[0]["requirement"] == "keep"


def test_rejects_empty_and_invalid():
    with pytest.raises(DecisionPayloadError):
        parse_decision_payload("[]")
    with pytest.raises(DecisionPayloadError):
        parse_decision_payload("{")
    with pytest.raises(DecisionPayloadError):
        parse_decision_payload(json.dumps([{"expectation_id": "e1", "decision": "SKIP"}]))
    with pytest.raises(DecisionPayloadError):
        parse_decision_payload(json.dumps([{"decision": "APPROVED"}]))
    with pytest.raises(DecisionPayloadError):
        parse_decision_payload(json.dumps(["e1"]))


def test_batch_cap():
    payload = [{"expectation_id": f"e{i}", "decision": "APPROVED"} for i in range(501)]
    with pytest.raises(DecisionPayloadError, match="at most"):
        parse_decision_payload(json.dumps(payload))


def test_modified_stores_as_approved():
    assert stored_status("MODIFIED") == "APPROVED"
    assert stored_status("REJECTED") == "REJECTED"
