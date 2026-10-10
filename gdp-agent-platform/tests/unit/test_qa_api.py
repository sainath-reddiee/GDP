"""QA workspace router: input limits, how the QA services are called and how their errors map to HTTP. The services
are replaced by fakes, so this checks the API contract only."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
import app.governance as gov  # noqa: E402
import app.qa_api as qa_api  # noqa: E402


class FakeDb:
    user, role = "ANA", "QA"

    def __init__(self):
        self.executed = []

    def query(self, sql, params=()):
        self.executed.append((sql, params))
        return []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))


@pytest.fixture
def api(monkeypatch):
    calls = []

    def record(name, result=None, error=None):
        def fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            if error:
                raise error
            return result(*args, **kwargs) if callable(result) else result
        return fn

    ctx = {"target_table_id": "t1", "target": {"fqn": "DB.S.ORDERS", "name": "ORDERS"}, "lines": [{}, {}, {}],
           "allowed": ["DB.S.ORDERS"], "pii_columns": ["EMAIL"], "pii_basis": "profile", "prompt": "x" * 50000,
           "source_columns": {"A": ["ID NUMBER"]}}
    scope = SimpleNamespace(table_context=record("table_context", ctx),
                            list_tables=record("list_tables", [{"target_table_id": "t1"}]))
    procedures = SimpleNamespace(
        table_suite=record("table_suite", {"tests": []}),
        table_ask=record("table_ask", {"sql": "select 1", "valid": True}),
        table_save=record("table_save", {"test_id": "x1", "saved": True}),
        create_suite=record("create_suite", {"suite_id": "s1"}),
        update_suite=record("update_suite", {"suite_id": "s1", "updated": True}),
        delete_suite=record("delete_suite", error=AssertionError("suite not found")),
        list_suites=record("list_suites", [{"suite_id": "s1"}]),
        update_table_test=record("update_table_test", error=AssertionError("the SQL reads a table outside this scope")),
        delete_table_test=record("delete_table_test", {"deleted": "x1"}),
    )
    run = SimpleNamespace(
        run_scope=record("run_scope", {"qa_run_id": "q1", "tests": 2, "results": [{"big": True}]}),
        latest_table=record("latest_table", ({"qa_run_id": "q1"}, [{"test_id": "x1", "sample_rows": '[{"A": 1}]',
                                                                    "columns": '["A"]'}])),
        history=record("history", [{"qa_run_id": "q1"}]),
    )
    for name, module in (("scope", scope), ("procedures", procedures), ("run", run)):
        monkeypatch.setitem(sys.modules, f"services.qa.{name}", module)
    monkeypatch.setattr(qa_api, "invoke_source", lambda db, handler, *args: handler("SESSION", *args))

    async def open_door(request, call_next):
        return await call_next(request)
    monkeypatch.setattr(gov, "middleware", open_door)
    db = FakeDb()
    main.app.dependency_overrides[main.current_db] = lambda: db
    try:
        yield TestClient(main.app), calls, db
    finally:
        main.app.dependency_overrides.pop(main.current_db, None)


def test_tables_and_context_summary(api):
    client, calls, _ = api
    assert client.get("/api/qa/tables", params={"domain_id": "d1"}).json() == {"tables": [{"target_table_id": "t1"}]}
    assert calls[-1] == ("list_tables", ("SESSION", "d1"), {})
    body = client.get("/api/qa/tables/t1").json()
    assert body["target"]["fqn"] == "DB.S.ORDERS" and body["lines"] == 3 and body["pii_basis"] == "profile"
    assert "prompt" not in body and "source_columns" not in body   # large prompt material stays on the server
    assert client.get("/api/qa/tables/t1/suite").status_code == 200 and calls[-1][1] == ("SESSION", "t1")


def test_suites_crud_and_errors(api):
    client, calls, _ = api
    assert client.get("/api/qa/suites", params={"target_table_id": "t1"}).json() == {"suites": [{"suite_id": "s1"}]}
    assert calls[-1] == ("list_suites", ("SESSION",), {"target_table_id": "t1", "domain_id": None})
    res = client.post("/api/qa/suites", json={"target_table_id": "t1", "name": "  Smoke ", "description": "core checks"})
    assert res.json() == {"suite_id": "s1"} and calls[-1][1] == ("SESSION", "t1", "Smoke", "core checks")
    assert client.put("/api/qa/suites/s1", json={"name": "Regression"}).json()["updated"]
    assert calls[-1][1] == ("SESSION", "s1", "Regression", None)   # description not sent: kept as it is
    res = client.delete("/api/qa/suites/s1")
    assert res.status_code == 404 and res.json()["detail"] == "suite not found"
    assert client.post("/api/qa/suites", json={"target_table_id": "t1", "name": ""}).status_code == 422
    assert client.post("/api/qa/suites", json={"target_table_id": "t1", "name": "x" * 201}).status_code == 422


def test_tests_save_update_delete(api):
    client, calls, _ = api
    test = {"title": "No null keys", "sql": "select * from DB.S.ORDERS where ID is null", "expected": "0 rows", "suite_id": "s1"}
    assert client.post("/api/qa/tables/t1/tests", json=test).json()["saved"]
    name, args, _ = calls[-1]
    assert name == "table_save" and args[1] == "t1" and '"suite_id":"s1"' in args[2]
    res = client.put("/api/qa/tests/x1", json=test)
    assert res.status_code == 409 and "outside this scope" in res.json()["detail"]
    assert client.delete("/api/qa/tests/x1").json() == {"deleted": "x1"}
    assert client.post("/api/qa/tables/t1/tests", json={**test, "sql": "x" * 16001}).status_code == 422


def test_run_ask_results_and_history(api):
    client, calls, _ = api
    res = client.post("/api/qa/tables/t1/run", json={"test_ids": ["x1"], "suite_id": "s1"}).json()
    assert res == {"qa_run_id": "q1", "tests": 2}   # per-test results are fetched from /results, not returned here
    assert calls[-1] == ("run_scope", ("SESSION",), {"target_table_id": "t1", "suite_id": "s1", "test_ids": ["x1"],
                                                    "triggered_by": "UI"})
    client.post("/api/qa/suites/s1/run", json={})
    assert calls[-1] == ("run_scope", ("SESSION",), {"suite_id": "s1", "test_ids": None, "triggered_by": "UI"})
    assert client.post("/api/qa/tables/t1/run", json={"test_ids": ["x"] * 501}).status_code == 422
    assert client.post("/api/qa/tables/t1/ask", json={"question": "are order totals positive?"}).json()["valid"]
    assert client.post("/api/qa/tables/t1/ask", json={"question": "x" * 2001}).status_code == 422
    found = client.get("/api/qa/results", params={"target_table_id": "t1", "suite_id": "s1"}).json()
    assert found["run"] == {"qa_run_id": "q1"} and found["results"][0]["sample"] == [{"A": 1}]
    assert found["results"][0]["columns"] == ["A"] and "sample_rows" not in found["results"][0]
    _, args, _ = calls[-1]
    assert callable(args[0]) and args[1:] == ("t1", "s1")   # latest_table gets the API's query function
    assert client.get("/api/qa/tables/t1/history", params={"limit": 500}).json() == {"runs": [{"qa_run_id": "q1"}]}
    assert calls[-1][1][1:] == ("t1", 100)


def test_snowflake_errors_keep_their_mapping(api, monkeypatch):
    client, _, _ = api

    def boom(*args, **kwargs):
        raise RuntimeError("SQL compilation error: AssertionError: the target table is inactive\n")
    monkeypatch.setattr(sys.modules["services.qa.procedures"], "table_suite", boom)
    res = client.get("/api/qa/tables/t1/suite")
    assert res.status_code == 400 and res.json()["detail"] == "the target table is inactive"
