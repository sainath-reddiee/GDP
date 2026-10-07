import pytest

from services.common.cost import backfill_estimates, calibrated_rates, estimate, rates_for, reconcile
from services.common.llm import DEFAULT_MODEL, resolve_model
from services.common.models import account_models, parse_allowlist
from services.common.platform_config import ADMIN_SETTINGS, validate
from services.workflow.listing import cost_query


def test_stage_model_wins_over_default():
    assert resolve_model("claude-sonnet-4-5", {"MAPPING": "openai-gpt-4.1"}, "mapping") == "openai-gpt-4.1"
    assert resolve_model("claude-sonnet-4-5", {"MAPPING": ""}, "MAPPING") == "claude-sonnet-4-5"
    assert resolve_model("claude-sonnet-4-5", {"QA": "x"}, None) == "claude-sonnet-4-5"
    assert resolve_model("", None, "QA") == DEFAULT_MODEL


def test_rates_order_card_then_calibrated_then_legacy():
    card = {"m1": {"input": 1.0, "output": 3.0}}
    assert rates_for("m1", card, {"m1": 9.0}, {"default": 5}) == (1.0, 3.0, "rate card")
    assert rates_for("m2", card, {"m2": 2.5}, {"default": 5}) == (2.5, 2.5, "calibrated")
    assert rates_for("m3", card, {}, {"default": 5}) == (5.0, 5.0, "single rate")
    assert rates_for("m3", {}, {}, {"default": 0})[2] == "none"


def test_estimate_charges_input_and_output_separately():
    assert estimate(1_000_000, 500_000, (1.0, 4.0, "rate card")) == 3.0
    assert estimate(0, 0, (1.0, 4.0, "x")) == 0.0


def test_calibrated_rates_own_calls_win_over_account_history():
    def query(sql, params):
        if "CORTEX_AI_FUNCTIONS_USAGE_HISTORY" in sql:
            return [{"MODEL": "m", "CREDITS": 3.0, "TOKENS": 1_000_000}, {"MODEL": "a", "CREDITS": 1.0, "TOKENS": 500_000}]
        return [{"MODEL": "m", "CREDITS": 2.0, "TOKENS": 1_000_000}, {"MODEL": "z", "CREDITS": 0, "TOKENS": 10}]
    assert calibrated_rates(query) == {"m": 2.0, "a": 2.0}
    assert calibrated_rates(lambda s, p: (_ for _ in ()).throw(RuntimeError("no table"))) == {}


def test_reconcile_reports_missing_privilege_and_keeps_estimates():
    def execute(sql, params):
        raise RuntimeError("Object does not exist or not authorized")

    out = reconcile(lambda s, p: [{"n": 3}], execute)
    assert out["access"] is False and out["awaiting_billing"] == 3 and "IMPORTED PRIVILEGES" in out["detail"]


def test_reconcile_reads_every_view_and_counts_new_actuals():
    seen, actual = [], iter([5, 7])

    def query(sql, params):
        if "ACTUAL_CREDITS IS NOT NULL" in sql and "QUERY_ID IS" not in sql:
            return [{"n": next(actual)}]
        return [{"n": 1}]

    out = reconcile(query, lambda s, p: seen.append(s))
    assert out["access"] and out["reconciled"] == 2 and len(seen) == 2
    assert "CORTEX_AI_FUNCTIONS_USAGE_HISTORY" in seen[0] and "ON C.QUERY_ID = U.QUERY_ID" in seen[0]


def test_backfill_only_models_with_a_rate():
    updates = []

    def query(sql, params):
        return [{"model": "a"}, {"model": "b"}] if "DISTINCT MODEL" in sql else []

    n = backfill_estimates(query, lambda s, p: updates.append(p), {"a": {"input": 1, "output": 2}}, {"default": 0})
    assert n == 1 and updates[0] == (1.0, 2.0, "a")


def test_allowlist_parsing_and_flags():
    assert parse_allowlist([{"key": "CORTEX_MODELS_ALLOWLIST", "value": "All"}]) is None
    assert parse_allowlist([{"key": "CORTEX_MODELS_ALLOWLIST", "value": "None"}]) == []
    assert parse_allowlist([{"key": "CORTEX_MODELS_ALLOWLIST", "value": "claude-sonnet-4-5, Mistral-Large2"}]) == [
        "claude-sonnet-4-5", "mistral-large2"]

    def execute(sql):
        if sql.startswith("SHOW PARAMETERS"):
            return [{"key": "CORTEX_MODELS_ALLOWLIST", "value": "claude-sonnet-4-5"}]
        if sql.startswith("SHOW MODELS"):
            return [{"name": "my-fine-tune"}]
        return []

    data = account_models(execute, "claude-sonnet-4-5")
    flags = {m["name"]: m["available"] for m in data["models"]}
    assert flags["claude-sonnet-4-5"] is True and flags["openai-gpt-4.1"] is False and "my-fine-tune" in flags


def test_settings_validation():
    assert "CATALOG_DISPLAY" not in ADMIN_SETTINGS
    value, problems = validate("LLM_MODEL_BY_STAGE", {"mapping": "OpenAI-GPT-4.1", "QA": ""})
    assert value == {"MAPPING": "openai-gpt-4.1"} and not problems
    assert validate("LLM_MODEL_BY_STAGE", {"NOPE": "x"})[1]
    value, problems = validate("RATE_CARD", {"claude-sonnet-4-5": {"input": "1.5", "output": 7}})
    assert value == {"claude-sonnet-4-5": {"input": 1.5, "output": 7.0}} and not problems
    assert validate("RATE_CARD", {"m-1": {"input": -1}})[1]
    assert validate("CREDIT_PRICE_USD", "3")[0] == 3.0
    assert validate("CREDIT_PRICE_USD", "")[0] is None
    assert validate("CREDIT_PRICE_USD", "abc")[1]


def test_cost_query_prefers_actual_credits():
    sql, _ = cost_query("model", None, None)
    assert "COALESCE(C.ACTUAL_CREDITS, C.ESTIMATED_COST, 0)" in sql and "ACTUAL_CREDITS" in sql
    assert "ORDER BY CREDITS DESC" in sql


def test_show_models_normalised_and_filtered():
    def execute(sql):
        if sql.startswith("SHOW MODELS"):
            return [{"name": "CLAUDE-SONNET-4-5"}, {"name": "SNOWFLAKE-ARCTIC-EMBED-M"}, {"name": "GEMINI-3-PRO"},
                    {"name": "ARCTIC-TRANSCRIBE"}]
        if sql.startswith("SHOW INFERENCE"):
            raise RuntimeError("001003 (42000): SQL compilation error: syntax error line 1")
        return []

    data = account_models(execute, "claude-sonnet-4-5")
    names = [m["name"] for m in data["models"]]
    assert names.count("claude-sonnet-4-5") == 1 and "gemini-3-pro" in names
    assert not any("embed" in n or "transcribe" in n for n in names)
    assert data["warnings"] == []
    assert next(m for m in data["models"] if m["name"] == "gemini-3-pro")["family"] == "google"
