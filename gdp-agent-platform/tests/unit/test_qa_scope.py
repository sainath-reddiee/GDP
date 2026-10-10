import json

import pytest

from services.qa import run as qa_run
from services.qa.run import hidden_columns, latest, run_scope, run_tests
from services.qa.scope import pii_columns, table_context


class _Row(dict):
    def as_dict(self):
        return dict(self)


class _Result:
    def __init__(self, data):
        self.data = data

    def collect(self):
        return [_Row(r) for r in self.data]


class Session:
    """Answers each statement with the rows of the first handler whose text appears in it; records every call."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.calls = []

    def sql(self, text, params=None):
        self.calls.append((text, list(params or [])))
        for needle, answer in self.handlers:
            if needle in text:
                if isinstance(answer, Exception):
                    raise answer
                return _Result(answer(params) if callable(answer) else answer)
        return _Result([])

    def inserts(self, table):
        return [(t, p) for t, p in self.calls if t.startswith(f"INSERT INTO {table} ")]


REGISTRY = {"TARGET_TABLE_ID": "t1", "DOMAIN_ID": "d1", "TARGET_DATABASE": "DB", "TARGET_SCHEMA": "MART",
            "TARGET_TABLE": "DIM_CUSTOMER", "BUSINESS_KEYS": json.dumps(["CUSTOMER_ID"]), "ACTIVE_FLAG": True,
            "MODEL_SPEC": json.dumps({"role": "spoke", "hub": "hub_customer"})}
COLUMNS = [{"COLUMN_NAME": "CUSTOMER_ID", "DATA_TYPE": "NUMBER", "IS_BUSINESS_KEY": True, "IS_PII": False,
            "SEMANTIC_TYPE": None, "BUSINESS_DEFINITION": None, "ACCEPTED_VALUES": None},
           {"COLUMN_NAME": "EMAIL", "DATA_TYPE": "VARCHAR", "IS_BUSINESS_KEY": False, "IS_PII": False,
            "SEMANTIC_TYPE": None, "BUSINESS_DEFINITION": None, "ACCEPTED_VALUES": None},
           {"COLUMN_NAME": "SEGMENT", "DATA_TYPE": "VARCHAR", "IS_BUSINESS_KEY": False, "IS_PII": False,
            "SEMANTIC_TYPE": None, "BUSINESS_DEFINITION": None, "ACCEPTED_VALUES": None}]
STTM = {"STTM_ID": "s1", "RUN_ID": "r1", "TARGET_TABLE_ID": "t1", "DOMAIN_ID": "d1",
        "TABLE_DESIGN": json.dumps({"business_keys": ["CUSTOMER_ID"]})}


def _line(target, source_column):
    return {"TARGET_COLUMN": target, "TARGET_DATATYPE": "VARCHAR", "SOURCE_TABLE": "CUSTOMERS",
            "SOURCE_COLUMN": source_column, "MAPPING_TYPE": "DIRECT", "TRANSFORMATION": None, "NULLABLE_RULE": None,
            "ACCEPTED_VALUES": None, "DEFAULT_VALUE": None, "BUSINESS_DEFINITION": None,
            "TARGET_DATABASE": "DB", "TARGET_SCHEMA": "MART", "TARGET_TABLE": "DIM_CUSTOMER",
            "SOURCE_DATABASE": "DB", "SOURCE_SCHEMA": "RAW", "SOURCE_TABLE": "CUSTOMERS"}


LINES = [_line("CUSTOMER_ID", "CUST_ID"), _line("CONTACT", "CONTACT_INFO"), _line("SEGMENT", "SEG")]


def _handlers(sttm=True, profile=None, columns=COLUMNS, tags=(), extra=()):
    return list(extra) + [
        ("FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE TARGET_TABLE_ID", [REGISTRY]),
        ("FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY", columns),
        ("FROM CONTRACT.STTM_REGISTRY", [STTM] if sttm else []),
        ("FROM CONTRACT.STTM_LINE", LINES),
        ("FROM SOURCE.LANDING_TABLE_REGISTRY WHERE RUN_ID", [
            {"SOURCE_TABLE": "CUSTOMERS", "LANDING_DATABASE": "DB", "LANDING_SCHEMA": "LANDING",
             "LANDING_TABLE": "CUSTOMERS"}]),
        ("FROM PROFILE.PROFILE_REGISTRY", profile or []),
        ("FROM CORE.TAG_ASSIGNMENT", [{"TAG": t} for t in tags]),
    ]


def test_table_context_without_sttm_uses_the_registry():
    ctx = table_context(Session(_handlers(sttm=False)), "t1")
    assert ctx["scope"] == "TABLE" and ctx["target_table_id"] == "t1" and ctx["domain_id"] == "d1"
    assert ctx["sttm_id"] is None and ctx["run_id"] is None and ctx["retired"] is False
    assert ctx["target"] == {"fqn": "DB.MART.DIM_CUSTOMER", "name": "DIM_CUSTOMER"}
    assert ctx["business_keys"] == ["CUSTOMER_ID"]
    assert ctx["allowed"] == ["DB.MART.DIM_CUSTOMER", "DB.MART.HUB_CUSTOMER"]
    assert [l["target_column"] for l in ctx["lines"]] == ["CUSTOMER_ID", "EMAIL", "SEGMENT"]
    assert ctx["lines"][0]["mapping_type"] == "TARGET_ONLY" and ctx["sources"] == {}
    for key in ("design", "spec", "source_columns", "graph", "pii_basis", "pii_columns"):
        assert key in ctx


def test_table_context_with_sttm_adds_sources_to_allowed():
    ctx = table_context(Session(_handlers()), "t1")
    assert ctx["sttm_id"] == "s1" and ctx["run_id"] == "r1"
    assert ctx["sources"] == {"CUSTOMERS": "DB.LANDING.CUSTOMERS"}
    assert set(ctx["allowed"]) == {"DB.LANDING.CUSTOMERS", "DB.MART.DIM_CUSTOMER", "DB.MART.HUB_CUSTOMER"}
    assert [l["source_column"] for l in ctx["lines"]] == ["CUST_ID", "CONTACT_INFO", "SEG"]


def test_missing_and_retired_tables():
    with pytest.raises(AssertionError, match="does not exist"):
        table_context(Session([("FROM KNOWLEDGE.TARGET_TABLE_REGISTRY", [])]), "nope")
    retired = Session(_handlers(sttm=False, extra=[("FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE TARGET_TABLE_ID",
                                                    [{**REGISTRY, "ACTIVE_FLAG": False}])]))
    assert table_context(retired, "t1")["retired"] is True
    with pytest.raises(AssertionError, match="retired"):
        run_scope(retired, target_table_id="t1")


def test_pii_basis_profile_maps_sources_to_targets():
    profile = [{"TABLE_NAME": "CUSTOMERS", "COLUMN_NAME": "CONTACT_INFO", "PII_CLASSIFICATION": "EMAIL"},
               {"TABLE_NAME": "CUSTOMERS", "COLUMN_NAME": "SEG", "PII_CLASSIFICATION": "NONE"}]
    ctx = table_context(Session(_handlers(profile=profile)), "t1")
    assert ctx["pii_basis"] == "profile"
    assert {"CONTACT_INFO", "CONTACT", "EMAIL"} <= set(ctx["pii_columns"])
    assert "SEGMENT" not in ctx["pii_columns"]


def test_pii_basis_heuristic_and_conservative():
    curated = [dict(c, IS_PII=c["COLUMN_NAME"] == "SEGMENT") for c in COLUMNS]
    session = Session(_handlers(sttm=False, columns=curated))
    pii, basis = pii_columns(session, table_context(session, "t1"))
    assert basis == "heuristic" and {"SEGMENT", "EMAIL"} <= pii
    tagged = Session(_handlers(sttm=False, columns=curated, tags=["pii"]))
    assert table_context(tagged, "t1")["pii_basis"] == "conservative"
    assert table_context(Session(_handlers(sttm=False)), "t1")["pii_basis"] == "conservative"


def test_conservative_masks_every_non_key_column():
    columns = ["CUSTOMER_ID", "N", "SEGMENT"]
    assert hidden_columns({"target_column": "SEGMENT"}, columns, set(), {"CUSTOMER_ID"}) == set()
    assert hidden_columns({"target_column": "SEGMENT"}, columns, set(), {"CUSTOMER_ID"}, True) == {"N", "SEGMENT"}


SAVED_TABLE = {"TEST_ID": "tt1", "CATEGORY": "CUSTOM", "TITLE": "Segments set", "OBJECTIVE": None,
               "SQL_TEXT": "SELECT CUSTOMER_ID FROM DB.MART.DIM_CUSTOMER WHERE SEGMENT IS NULL;", "EXPECTED": "0 rows",
               "SEVERITY": "HIGH", "TARGET_COLUMN": "SEGMENT", "ORIGIN": "USER", "PROMPT": None, "CREATED_BY": "me",
               "CREATED_AT": None, "SCOPE": "TABLE", "SUITE_ID": "su1", "TARGET_TABLE_ID": "t1"}


def _fake_execute(calls):
    def execute(session, test, allowed, pii, keys=(), conservative=False):
        calls.append({"test": test["test_id"], "allowed": list(allowed), "conservative": conservative})
        return {k: test.get(k) for k in ("test_id", "category", "title", "severity", "origin", "expected",
                                         "target_column")} | {"outcome": "PASS", "sql": test["sql"], "duration_ms": 1}
    return execute


def test_run_scope_table_path_stores_run_id_null_and_new_columns(monkeypatch):
    calls = []
    monkeypatch.setattr(qa_run, "execute", _fake_execute(calls))
    monkeypatch.setattr(qa_run, "_target_built", lambda session, fqn: True)
    session = Session(_handlers(sttm=False, extra=[("WHERE SCOPE = 'TABLE' AND TARGET_TABLE_ID", [SAVED_TABLE])]))
    out = run_scope(session, target_table_id="t1", suite_id="su1", triggered_by="JIRA",
                    trigger_detail={"issue": "QA-1"})
    assert out["scope"] == "TABLE" and out["run_id"] is None and out["tests"] == 1 and out["suite_id"] == "su1"
    assert calls == [{"test": "tt1", "allowed": ["DB.MART.DIM_CUSTOMER", "DB.MART.HUB_CUSTOMER"], "conservative": True}]
    (run_sql, run_params), = session.inserts("QUALITY.QA_RUN")
    assert "SCOPE" in run_sql and "TRIGGER_DETAIL" in run_sql
    assert run_params[1] == "" and run_params[-5:] == ["d1", "t1", "su1", "TABLE", json.dumps({"issue": "QA-1"})]
    (res_sql, res_params), = session.inserts("QUALITY.QA_RESULT")
    assert "TARGET_TABLE_ID, SUITE_ID" in res_sql and res_params[2] == "" and res_params[-2:] == ["t1", "su1"]


def test_run_scope_by_suite_alone_finds_its_table(monkeypatch):
    calls = []
    monkeypatch.setattr(qa_run, "execute", _fake_execute(calls))
    monkeypatch.setattr(qa_run, "_target_built", lambda session, fqn: True)
    session = Session(_handlers(sttm=False, extra=[
        ("FROM CONTRACT.QA_TEST_SUITE", [{"SUITE_ID": "su1", "TARGET_TABLE_ID": "t1", "IS_DEFAULT": False}]),
        ("WHERE SCOPE = 'TABLE' AND TARGET_TABLE_ID", [SAVED_TABLE])]))
    out = run_scope(session, suite_id="su1")
    assert out["target_table_id"] == "t1" and out["suite_id"] == "su1" and [c["test"] for c in calls] == ["tt1"]


def test_run_scope_dropped_table_is_not_run(monkeypatch):
    monkeypatch.setattr(qa_run, "_target_built", lambda session, fqn: False)
    session = Session(_handlers(sttm=False, extra=[("WHERE SCOPE = 'TABLE' AND TARGET_TABLE_ID", [SAVED_TABLE])]))
    out = run_scope(session, target_table_id="t1")
    assert out["not_run"] == 1 and "does not exist" in out["results"][0]["detail"]


def test_run_path_includes_the_targets_table_tests(monkeypatch):
    calls = []
    monkeypatch.setattr(qa_run, "execute", _fake_execute(calls))
    monkeypatch.setattr(qa_run, "_target_built", lambda session, fqn: True)
    session = Session(_handlers(extra=[("WHERE SCOPE = 'TABLE' AND TARGET_TABLE_ID", [SAVED_TABLE]),
                                       ("WHERE RUN_ID = ? AND NOT IS_DELETED", [])]))
    out = run_tests(session, "r1", ["tt1"], "UI")
    assert out["scope"] == "RUN" and out["run_id"] == "r1" and out["target_table_id"] == "t1"
    assert [c["test"] for c in calls] == ["tt1"] and calls[0]["conservative"] is False
    (_, res_params), = session.inserts("QUALITY.QA_RESULT")
    assert res_params[2] == "r1" and res_params[3] == "tt1" and res_params[-2:] == ["t1", "su1"]


def test_run_tests_wrapper_keeps_its_contract(monkeypatch):
    seen = {}
    monkeypatch.setattr(qa_run, "run_scope", lambda session, **kw: seen.update(kw) or {"tests": 0})
    run_tests(object(), "r1", ["a"], "API")
    assert seen == {"run_id": "r1", "test_ids": ["a"], "triggered_by": "API"}


def test_latest_excludes_deleted_table_tests():
    calls = []

    def query(sql, params):
        calls.append((sql, params))
        if "FROM QUALITY.QA_RUN WHERE" in sql:
            return [{"qa_run_id": "q1", "sttm_id": "s1"}]
        return []

    latest(query, "r1")
    sql, params = calls[1]
    assert "(RUN_ID = %s OR SCOPE = 'TABLE') AND COALESCE(IS_DELETED, FALSE)" in sql and params == ("r1", "s1", "r1")
