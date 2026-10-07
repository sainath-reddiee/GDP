from pathlib import Path

from services.knowledge.domain_admin import can_delete, repository_pack_names
from services.knowledge.domain_ai import SPEC, citation_keys, keep_citations, pack_context
from services.knowledge.packs import GDP_DOMAIN_ID, prepare

REPO = repository_pack_names()


def test_repository_packs_are_found():
    assert {"GDP", "COMPANY", "OPPORTUNITY", "PROPERTY"} <= REPO


def test_only_ui_added_domains_can_be_deleted():
    assert can_delete(GDP_DOMAIN_ID, "GDP", {}, REPO)[0] is False
    assert can_delete("x", "general", {"origin": "imported"}, REPO)[0] is False
    ok, reason = can_delete("x", "COMPANY", {"origin": "imported"}, REPO)
    assert not ok and "domain_pack.json" in reason  # a repo pack even if someone re-imported it
    assert can_delete("x", "ORDERS", {"origin": "repository"}, set())[0] is False
    assert can_delete("x", "ORDERS", {"origin": "imported"}, REPO) == (True, None)


def test_imports_are_marked_as_ui_added():
    pack, problems = prepare({"domain": {"name": "orders"}, "targets": [], "knowledge": []})
    assert not problems and pack["domain"]["origin"] == "imported"


PACK = {"domain": {"name": "ORDERS", "signals": {"tables": {"ORDER": 3}}},
        "targets": [{"table": "ORDER_FACT", "columns": [{"name": "ORDER_ID", "type": "VARCHAR"}]}],
        "knowledge": [{"key": "orders.glossary.order_id", "type": "GLOSSARY", "title": "Order id", "content": "x" * 900}]}


def test_pack_context_is_trimmed_and_answers_cite_only_what_was_shown():
    ctx = pack_context(PACK)
    assert len(ctx["knowledge"][0]["content"]) <= 301
    keys = citation_keys(ctx, [{"SOURCE_REFERENCE": "orders.rule.1", "TITLE": "Rule 1"}])
    kept = keep_citations([{"kind": "x", "key": "order_fact.order_id"}, {"kind": "x", "key": "MADE_UP"},
                           {"kind": "x", "key": "ORDER_FACT.ORDER_ID"}, {"key": "orders.rule.1"}], keys)
    assert [c["key"] for c in kept] == ["ORDER_FACT.ORDER_ID", "ORDERS.RULE.1"]
    assert kept[0]["kind"] == "COLUMN" and kept[1]["title"] == "Rule 1"


def test_review_items_have_stable_keys():
    item = {"kind": "glossary", "target_table": None, "target_column": "order_id", "value": "ORD_NO", "reason": "r"}
    assert SPEC.valid(item) and SPEC.key(item) == "GLOSSARY||ORDER_ID|ORD_NO"
