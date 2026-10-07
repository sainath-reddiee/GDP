from services.common.platform_config import validate


def test_model_names_are_checked():
    assert validate("LLM_MODEL", " Claude-Sonnet-4-5 ") == ("claude-sonnet-4-5", [])
    assert validate("LLM_MODEL", "drop table x;")[1]
    assert validate("NOT_A_SETTING", 1)[1] == ["unknown setting NOT_A_SETTING"]


def test_rates_are_numbers_and_keep_a_default():
    value, problems = validate("CREDITS_PER_MILLION_TOKENS", {"claude-sonnet-4-5": "2.5"})
    assert not problems and value == {"claude-sonnet-4-5": 2.5, "default": 0.0}
    assert validate("CREDITS_PER_MILLION_TOKENS", {"x": -1})[1] == ["x: rate cannot be negative"]


def test_catalog_lists_are_complete_and_typed():
    value, problems = validate("CATALOG_DISPLAY", {"hidden_target_databases": [" POC_DB ", ""]})
    assert not problems and value["hidden_target_databases"] == ["POC_DB"] and value["strip_tokens"] == ["GDP"]
    assert validate("CATALOG_DISPLAY", {"hidden_target_tables": "X"})[1] == ["hidden_target_tables must be a list"]
    assert validate("CATALOG_DISPLAY", {"bogus": []})[1] == ["unknown list bogus"]


def test_standard_overrides_only_accept_preset_keys_of_the_right_type():
    assert validate("MODELING_STANDARD.GENERIC", {"scd_type": 1, "dbt_profile": "acme"}) == \
        ({"scd_type": 1, "dbt_profile": "acme"}, [])
    problems = validate("MODELING_STANDARD.GDP", {"scd_type": "two", "nope": 1})[1]
    assert "unknown convention nope" in problems and "scd_type must be a int" in problems
