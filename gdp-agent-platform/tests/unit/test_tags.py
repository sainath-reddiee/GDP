import pytest

from services.common.tags import color_for, entity_key, normalize_tag, normalize_tags
from services.workflow.listing import runs_where


def test_tags_are_normalized():
    assert normalize_tag("  #Customer Data ") == "customer-data"
    assert normalize_tag("pii:email") == "pii:email" and normalize_tag("q3/2026") == "q3/2026"
    assert normalize_tag("bad tag!") is None and normalize_tag("") is None and normalize_tag("x" * 41) is None
    assert normalize_tags(["Gold", "gold", "#GOLD", "silver", "!!"]) == ["gold", "silver"]
    assert color_for("gold") == color_for("gold")


def test_entity_keys():
    assert entity_key("profile", 'db.raw."orders"') == "DB.RAW.ORDERS"
    assert entity_key("RUN", "abc-123") == "abc-123"
    with pytest.raises(AssertionError):
        entity_key("PROFILE", "ORDERS")
    with pytest.raises(AssertionError):
        entity_key("TABLE", "A.B.C")


def test_runs_filter_by_tag_is_a_bind():
    where, params = runs_where("1 = 1", False, None, None, None, False, "gold")
    assert "CORE.TAG_ASSIGNMENT" in where and params[-1] == "gold" and "gold" not in where
