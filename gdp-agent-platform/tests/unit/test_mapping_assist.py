from services.mapping.assist import build_prompt, normalize, to_decision

TARGETS = [
    {"target_column_id": "t1", "column_name": "ADDRESS", "data_type": "VARCHAR(100)", "nullable": False},
    {"target_column_id": "t2", "column_name": "CITY", "data_type": "VARCHAR(50)", "nullable": True},
    {"target_column_id": "t3", "column_name": "ZIP", "data_type": "VARCHAR(10)", "nullable": True},
]
SOURCES = [
    {"source_column_id": "s1", "column_name": "ADDR_LINE", "data_type": "TEXT", "source_table": "ADDRESSES"},
    {"source_column_id": "s2", "column_name": "STREET", "data_type": "TEXT", "source_table": "ADDRESSES"},
    {"source_column_id": "s3", "column_name": "POSTCODE", "data_type": "NUMBER", "source_table": "ADDRESSES"},
    {"source_column_id": "s4", "column_name": "GEOMETRY", "data_type": "TEXT", "source_table": "ADDRESSES"},
]
CANDS = {
    "s1": [{"candidate_id": "c1", "target_column_id": "t1", "target_column": "ADDRESS", "final_score": 0.7, "rank": 1}],
    "s2": [{"candidate_id": "c2", "target_column_id": "t1", "target_column": "ADDRESS", "final_score": 0.6, "rank": 1}],
    "s3": [{"candidate_id": "c3", "target_column_id": "t2", "target_column": "CITY", "final_score": 0.3, "rank": 1}],
}


def test_prompt_lists_targets_flags_and_candidates():
    prompt = build_prompt(SOURCES, CANDS, TARGETS, "SILVER_ADDRESS", {"t2": "sX"}, "prefer street level")
    assert "ADDRESS VARCHAR(100) REQUIRED" in prompt
    assert "CITY VARCHAR(50) (already mapped by the reviewer)" in prompt
    assert "id=s1 ADDRESSES.ADDR_LINE" in prompt and "ADDRESS 70%" in prompt
    assert "prefer street level" in prompt


def test_normalize_validates_ids_and_resolves_collisions():
    raw = {"columns": [
        {"source_column_id": "s1", "action": "APPROVE", "target_column": "ADDRESS", "confidence": 0.9, "reason": "a"},
        {"source_column_id": "s2", "action": "APPROVE", "target_column": "ADDRESS", "confidence": 0.6, "reason": "b"},
        {"source_column_id": "s3", "action": "ALTERNATIVE", "target_column": "zip", "confidence": 0.8, "reason": "c",
         "transformation": "LPAD(POSTCODE::VARCHAR, 5, '0')"},
        {"source_column_id": "s4", "action": "NULL", "target_column": "", "confidence": 0.7, "reason": "d"},
        {"source_column_id": "bogus", "action": "APPROVE", "target_column": "ADDRESS", "confidence": 1, "reason": "x"},
        {"source_column_id": "s1", "action": "NULL", "target_column": "", "confidence": 1, "reason": "dup"},
    ]}
    out = {s["source_column_id"]: s for s in normalize(raw, SOURCES, CANDS, TARGETS, {})}
    assert set(out) == {"s1", "s2", "s3", "s4"}
    assert out["s1"]["action"] == "APPROVE" and out["s1"]["candidate_id"] == "c1"
    assert out["s2"]["action"] == "NULL" and "already claimed" in out["s2"]["note"]
    assert out["s3"]["action"] == "ALTERNATIVE" and out["s3"]["target_column_id"] == "t3"
    assert out["s4"]["action"] == "NULL"


def test_normalize_respects_targets_taken_by_reviewer():
    raw = {"columns": [{"source_column_id": "s1", "action": "APPROVE", "target_column": "ADDRESS",
                        "confidence": 0.9, "reason": "a"}]}
    assert normalize(raw, SOURCES, CANDS, TARGETS, {"t1": "s9"})[0]["action"] == "NULL"


def test_to_decision_matches_save_contract():
    approve = to_decision({"action": "APPROVE", "source_column_id": "s1", "candidate_id": "c1", "reason": "r"})
    assert approve == {"decision": "APPROVED", "source_column_id": "s1", "candidate_id": "c1",
                       "business_justification": "AI copilot: r"}
    modify = to_decision({"action": "MODIFY", "source_column_id": "s1", "candidate_id": "c1",
                          "transformation": "TRIM(X)", "reason": "r"})
    assert modify["decision"] == "MODIFIED" and modify["transformation"] == "TRIM(X)"
    assert to_decision({"action": "NULL", "source_column_id": "s4", "reason": "r"})["decision"] == "REJECTED"
