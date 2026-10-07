import pytest

from services.knowledge.manage import editable, list_query, new_key, prepare_item


def test_list_filters_and_paging():
    where, params = list_query("d1", "GLOSSARY", "ACTIVE", " cust ", 100, 999)
    assert where == ("K.IS_CURRENT AND K.DOMAIN_ID = %s AND K.KNOWLEDGE_TYPE = %s AND K.STATUS = %s AND "
                     "(K.TITLE ILIKE %s OR K.CONTENT ILIKE %s OR K.SOURCE_REFERENCE ILIKE %s)")
    assert params == ["d1", "GLOSSARY", "ACTIVE", "%cust%", "%cust%", "%cust%", 200, 100]  # limit capped
    with pytest.raises(AssertionError):
        list_query(None, "NOT_A_TYPE", None, None, 0, 10)


def test_structured_types_need_usable_content():
    item, problems = prepare_item("glossary", "Order id", "Order number", {"target_column": "order_id", "synonyms": "ORD_NO"})
    assert not problems and item["content_json"] == {"target_column": "order_id", "synonyms": ["ORD_NO"]}
    assert prepare_item("GLOSSARY", "x", "y", None)[1] == ["GLOSSARY needs structured content (target column and its details)"]
    assert "structured content is not valid JSON" in prepare_item("BUSINESS_RULE", "x", "y", "{bad")[1]
    assert prepare_item("ONBOARDING_GUIDE", "Guide", "Read me", None)[1] == []
    assert set(prepare_item("NOPE", "", "", None)[1]) == {"unknown knowledge type NOPE", "a title is required", "content is required"}


def test_seeded_items_are_read_only_and_keys_are_namespaced():
    assert editable("SEED")[0] is False and editable("SATYA")[0] is True
    key = new_key("ORDERS", "GLOSSARY", "Order ID / number")
    assert key.startswith("orders.glossary.order_id_number.")
