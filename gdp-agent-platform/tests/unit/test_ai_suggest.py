import json

from services.common import ai_suggest
from services.common.suggestion_stages import SPECS
from services.profiling.profiler import apply_column_rules


class FakeDb:
    def __init__(self):
        self.cache, self.verdicts, self.inserts = [], [], []

    def rows(self, session, sql, params=None):
        if "FROM CORE.AI_SUGGESTION_DECISION" in sql:
            return self.verdicts
        if "FROM CORE.AI_SUGGESTION" in sql:
            return self.cache
        return []

    def insert(self, session, table, cols, exprs, values):
        self.inserts.append((table, dict(zip(cols, values[0]))))


def wire(monkeypatch, answer, calls):
    db = FakeDb()
    monkeypatch.setattr(ai_suggest, "rows", db.rows)
    monkeypatch.setattr(ai_suggest, "insert_rows", db.insert)
    monkeypatch.setattr(ai_suggest, "model_for", lambda s: "m1")
    monkeypatch.setattr(ai_suggest, "record_cost", lambda *a, **k: calls.append("cost"))

    def complete(session, prompt, schema, max_tokens=0, **kwargs):
        calls.append(prompt)
        return answer, {"total_tokens": 10}, "m1"
    monkeypatch.setattr(ai_suggest, "complete_json", complete)
    return db


def test_one_call_per_scope_then_cache_and_bad_items_dropped(monkeypatch):
    calls = []
    answer = {"items": [{"column": "tax_ref", "pii_classification": "SSN", "reason": "999-99-9999 shapes"},
                        {"column": "", "reason": "blank column"}, "junk"]}
    db = wire(monkeypatch, answer, calls)
    spec = SPECS["PROFILING"]
    first = ai_suggest.suggest(None, spec, "src.members", {"table": "members"}, run_id="r1")
    assert [i["column"] for i in first["items"]] == ["tax_ref"] and first["items"][0]["item_key"] == "tax_ref"
    assert len([c for c in calls if c != "cost"]) == 1 and "cost" in calls
    table, row = db.inserts[0]
    assert table == "CORE.AI_SUGGESTION" and row["FINGERPRINT"] == ai_suggest.fingerprint({"table": "members"})
    db.cache = [{"SUGGESTION_ID": "s1", "ITEMS": json.dumps(row["ITEMS"]), "MODEL": "m1", "CREATED_AT": "t"}]
    again = ai_suggest.suggest(None, spec, "src.members", {"table": "members"}, run_id="r1")
    assert again["cached"] and len([c for c in calls if c != "cost"]) == 1  # no second model call


def test_cached_only_never_calls_the_model(monkeypatch):
    calls = []
    wire(monkeypatch, {"items": []}, calls)
    out = ai_suggest.suggest(None, SPECS["SODA"], "target.x", {"a": 1}, cached_only=True)
    assert out["generated"] is False and calls == []


def test_rejected_items_are_hidden_and_named_in_the_prompt(monkeypatch):
    calls = []
    answer = {"items": [{"target_column": "ID", "role": "BUSINESS_KEY", "reason": "unique"}]}
    db = wire(monkeypatch, answer, calls)
    db.verdicts = [{"ITEM_KEY": "ID|BUSINESS_KEY", "DECISION": "REJECTED", "NOTE": None, "DECIDED_BY": "u",
                    "DECIDED_AT": "t"}]
    out = ai_suggest.suggest(None, SPECS["DBT"], "target.x", {"a": 1})
    assert out["items"] == [] and "ID|BUSINESS_KEY" in calls[0]


def test_model_failure_means_no_suggestions_not_an_error(monkeypatch):
    calls = []
    wire(monkeypatch, {}, calls)

    def boom(*a, **k):
        raise RuntimeError("cortex down")
    monkeypatch.setattr(ai_suggest, "complete_json", boom)
    out = ai_suggest.suggest(None, SPECS["STTM"], "target.x", {"a": 1})
    assert out["items"] == [] and "unavailable" in out["error"]


def test_accepted_column_rule_overrides_and_remasks():
    profiles = [{"column_name": "tax_ref", "semantic_type": "IDENTIFIER", "pii_classification": "NONE",
                 "sample_values": [{"value": "123-45-6789", "count": 1}],
                 "statistics": {"min": "1", "max": "9", "frequency_distribution": [{"value": "123-45-6789"}]}}]
    apply_column_rules(profiles, {"TAX_REF": {"pii_classification": "SSN", "date_format": None}})
    p = profiles[0]
    assert p["pii_classification"] == "SSN" and p["sample_values"][0]["value"] == "***6789"
    assert p["statistics"]["min"] is None and p["rule_source"] == "accepted column rule"
