import json
from pathlib import Path

from services.knowledge.packs import draft_from_answer, import_pack, prepare

ROOT = Path(__file__).resolve().parents[2]


def test_repository_packs_prepare_cleanly_except_protected_gdp():
    for path in sorted((ROOT / "domain").glob("*/domain_pack.json")):
        pack = json.loads(path.read_text(encoding="utf-8"))
        _, problems = prepare(pack)
        if pack["domain"]["name"] == "GDP":
            assert any("cannot be replaced" in p for p in problems)
        else:
            assert not problems, (path.parent.name, problems[:3])


def test_prepare_namespaces_keys_and_defaults():
    pack, problems = prepare({"domain": {"name": "orders"}, "targets": [
        {"table": "ORDER_FACT", "columns": [{"name": "ORDER_ID", "type": "VARCHAR"}]}],
        "knowledge": [{"key": "g1", "type": "GLOSSARY", "title": "ORDER_ID",
                       "content_json": {"target_column": "ORDER_ID", "synonyms": "ORD_NO"}}]})
    assert not problems
    assert pack["domain"]["name"] == "ORDERS" and pack["domain"]["standard"] == "GENERIC"
    assert pack["knowledge"][0]["key"] == "orders.g1"  # cannot collide with another domain's item
    assert pack["knowledge"][0]["content_json"]["synonyms"] == ["ORD_NO"]  # repaired, not rejected
    assert pack["targets"][0]["schema"] == "ORDERS"


def test_ai_answer_becomes_a_reviewable_pack():
    answer = {"name": "claims", "description": "Insurance claims",
              "table_signals": [{"term": "claim", "weight": 3}, {"term": "", "weight": 2}],
              "column_signals": [{"term": "policy", "weight": "x"}],
              "targets": [{"table": "claim_fact", "business_keys": ["CLAIM_ID"],
                           "columns": [{"name": "claim_id", "type": "VARCHAR", "business_key": True},
                                       {"name": "  "}, "junk"]}, {"table": ""}],
              "rules": [{"kind": "GLOSSARY", "target_column": "claim_id", "title": "Claim id", "content": "x",
                         "synonyms": ["CLM_NO"]},
                        {"kind": "TRANSFORMATION_RULE", "target_column": "LOSS_DATE", "title": "Loss date",
                         "content": "parse", "expression": "TRY_TO_DATE({col}, 'DD/MM/YYYY')"},
                        {"kind": "weird", "title": "note", "content": "free text"}]}
    pack = draft_from_answer(answer, "GENERIC")
    assert pack["domain"]["signals"] == {"tables": {"CLAIM": 3}, "columns": {"POLICY": 1}}
    assert [c["name"] for c in pack["targets"][0]["columns"]] == ["CLAIM_ID"] and len(pack["targets"]) == 1
    assert [k["type"] for k in pack["knowledge"]] == ["GLOSSARY", "TRANSFORMATION_RULE", "BUSINESS_RULE"]
    _, problems = prepare(pack)
    assert not problems, problems


def test_import_merges_every_registry_with_stable_ids():
    issued = []
    pack = draft_from_answer({"name": "claims", "targets": [{"table": "CLAIM_FACT", "columns": [
        {"name": "CLAIM_ID", "type": "VARCHAR"}]}]})
    first = import_pack(lambda sql, params=None: issued.append((sql, params)), "DB", pack)
    tables = {sql.split("MERGE INTO ")[1].split()[0] for sql, _ in issued}
    assert tables == {"DB.KNOWLEDGE.DOMAIN_REGISTRY", "DB.KNOWLEDGE.TARGET_TABLE_REGISTRY",
                      "DB.KNOWLEDGE.TARGET_COLUMN_REGISTRY"}
    again = import_pack(lambda sql, params=None: None, "DB", pack)
    assert first["domain_id"] == again["domain_id"] and first["columns"] == 1  # re-import updates in place


def test_gdp_pack_cannot_be_overwritten_from_the_ui():
    import pytest

    with pytest.raises(AssertionError, match="cannot be replaced"):
        import_pack(lambda *a: None, "DB", {"domain": {"name": "gdp"}, "targets": [], "knowledge": []})
