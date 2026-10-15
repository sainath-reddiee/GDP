"""Data Quality Store: SodaCL parsing, normalization, catalog tree, schedules, table scans, and the QA / DQ store
routes (API contract with a fake Snowflake)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402  (first: main registers the routers)
import app.governance as gov  # noqa: E402
import app.qa_api as qa_api  # noqa: E402
import app.quality_api as quality_api  # noqa: E402
from services.catalog import dq  # noqa: E402
from services.governance.policy import privilege_for  # noqa: E402


# ---------------------------------------------------------------- parse and render


@pytest.mark.parametrize("text, column, definition", [
    ("missing_count(EMAIL) = 0", "EMAIL", {"kind": "not_null"}),
    ("- missing_percent(PHONE) < 5%:", "PHONE", {"kind": "missing_percent", "max_percent": 5.0}),
    ("duplicate_count(ID) = 0", "ID", {"kind": "unique"}),
    ("duplicate_count(A, B) = 0", None, {"kind": "unique", "columns": ["A", "B"]}),
    ("duplicate_percent(ID) < 1%", "ID", {"kind": "duplicate_percent", "max_percent": 1.0}),
    ("freshness(LOADED_AT) < 6h", "LOADED_AT", {"kind": "freshness", "threshold": "6h"}),
    ("row_count > 0", None, {"kind": "row_count", "gt": 0}),
    ("row_count between 10 and 20", None, {"kind": "row_count", "min": 10, "max": 20}),
    ("avg(AMOUNT) between 1 and 9.5", "AMOUNT", {"kind": "avg", "threshold": {"between": [1.0, 9.5]}}),
    ("max(AGE) <= 120", "AGE", {"kind": "max", "threshold": {"op": "<=", "value": 120.0}}),
])
def test_parse_dsl(text, column, definition):
    parsed, problems = dq.parse_dsl(text)
    assert not problems and parsed["target_column"] == column and parsed["definition"] == definition
    cleaned, problems = dq.normalize({**parsed, "severity": "FAIL"})
    assert not problems and cleaned["soda_dsl"]


@pytest.mark.parametrize("text", ["", "missing_count(EMAIL) = 3", "row_count < 5", "values in (1,2)",
                                  "invalid_count(X) = 0", "freshness(TS) > 1d"])
def test_parse_dsl_rejects(text):
    parsed, problems = dq.parse_dsl(text)
    assert problems


def test_render_headline_and_validation():
    cleaned, problems = dq.normalize({"target_column": "EMAIL", "severity": "FAIL", "definition": {"kind": "not_null"}})
    assert dq.headline(cleaned["soda_dsl"]) == "missing_count(EMAIL) = 0" and cleaned["check_type"] == "NOT_NULL"
    _, problems = dq.normalize({"definition": {"kind": "not_null"}})
    assert problems == ["not_null needs a column"]
    _, problems = dq.normalize({"target_column": "X", "definition": {"kind": "teleport"}})
    assert "unknown check kind" in problems[0]


def test_same_check_ignores_evidence_and_case():
    a = {"target_column": "email", "definition": {"kind": "not_null", "evidence": "x"}}
    assert dq.same_check(a, {"target_column": "EMAIL", "definition": {"kind": "not_null"}})
    assert not dq.same_check(a, {"target_column": "EMAIL", "definition": {"kind": "unique"}})


def test_build_tree():
    rows = [{"table_name": "DB.S.T", "column_name": None, "last_run_status": "PASS", "domain_id": "d"},
            {"table_name": "DB.S.T", "column_name": "EMAIL", "last_run_status": "FAIL", "schedule_cron": "0 6 * * *"},
            {"table_name": "DB.S.T", "column_name": "EMAIL", "last_run_status": None},
            {"table_name": "DB.S.A", "column_name": "ID", "last_run_status": "PASS"}]
    tree = dq.build_tree(rows)
    assert [t["table"] for t in tree] == ["DB.S.A", "DB.S.T"]
    t = tree[1]
    assert t["checks"] == 3 and t["pass_rate"] == 50.0 and t["schedule_cron"] == "0 6 * * *"
    assert [c["column"] for c in t["columns"]] == [None, "EMAIL"] and t["counts"]["NEVER_RUN"] == 1


def test_schedule_sql():
    task, stmts = dq.schedule_sql("AIP", "DB.S.ORDERS", "0 6 * * * UTC", "WH")
    assert task.startswith("AIP.CORE.DQ_") and task == f"AIP.CORE.{dq.task_name('db.s.orders')}"
    assert "SCHEDULE = 'USING CRON 0 6 * * * UTC'" in stmts[0] and "CALL AIP.CORE.RUN_TABLE_SCAN('DB.S.ORDERS')" in stmts[0]
    assert stmts[1] == f"ALTER TASK {task} RESUME"
    for args in [("AIP", "DB.S.T", "every day", "WH"), ("AIP", "T", "0 6 * * *", "WH"), ("A;B", "DB.S.T", "0 6 * * *", "WH")]:
        with pytest.raises(AssertionError):
            dq.schedule_sql(*args)


# ---------------------------------------------------------------- Snowpark side


class Row(dict):
    def as_dict(self):
        return dict(self)


class Session:
    def __init__(self, answers=None):
        self.answers, self.calls = answers or {}, []

    def sql(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params or []))
        session = self

        class Result:
            def collect(self_inner):
                for needle, value in session.answers.items():
                    if needle in sql:
                        return [Row(r) for r in value]
                return []
        return Result()


def test_upsert_from_dsl_and_duplicates():
    session = Session({"SELECT CHECK_ID, COLUMN_NAME, CHECK_DEFINITION": []})
    out = dq.upsert(session, json.dumps({"table": "db.s.orders", "dsl": "missing_count(EMAIL) = 0", "severity": "FAIL"}))
    assert out["table"] == "DB.S.ORDERS" and out["column"] == "EMAIL" and out["headline"] == "missing_count(EMAIL) = 0"
    assert any(c[0].startswith("INSERT INTO CORE.DQ_CHECKS_CATALOG") for c in session.calls)
    dup = Session({"SELECT CHECK_ID, COLUMN_NAME, CHECK_DEFINITION": [
        {"CHECK_ID": "c1", "COLUMN_NAME": "EMAIL", "CHECK_DEFINITION": '{"kind": "not_null"}'}]})
    with pytest.raises(AssertionError, match="same check"):
        dq.upsert(dup, json.dumps({"table": "DB.S.ORDERS", "dsl": "missing_count(EMAIL) = 0"}))
    with pytest.raises(AssertionError, match="DATABASE.SCHEMA.TABLE"):
        dq.upsert(Session(), json.dumps({"table": "ORDERS", "dsl": "row_count > 0"}))


def test_upsert_keeps_requirement_and_source_run():
    session = Session()
    out = dq.upsert(session, json.dumps({"table": "DB.S.ORDERS", "dsl": "row_count > 0", "requirement": "Orders land daily",
                                         "source_run_id": "r1"}))
    lineage = [c for c in session.calls if "SET REQUIREMENT" in c[0]]
    assert lineage and lineage[0][1] == ["Orders land daily", "r1", out["check_id"]]


def test_upsert_works_before_v038():
    class OldSession(Session):
        def sql(self, sql, params=None):
            if "SET REQUIREMENT" in sql:
                raise RuntimeError("invalid identifier 'REQUIREMENT'")
            return super().sql(sql, params)

    out = dq.upsert(OldSession(), json.dumps({"table": "DB.S.ORDERS", "dsl": "row_count > 0", "requirement": "x"}))
    assert out["check_type"]


def test_run_table_stores_a_table_scan(monkeypatch):
    import services.quality.scan as scan

    session = Session({"FROM CORE.DQ_CHECKS_CATALOG WHERE TABLE_NAME": [
        {"CHECK_ID": "c1", "COLUMN_NAME": "EMAIL", "CHECK_TYPE": "NOT_NULL", "CHECK_DEFINITION": '{"kind": "not_null"}',
         "SEVERITY": "FAIL", "DOMAIN_ID": "d1"},
        {"CHECK_ID": "c2", "COLUMN_NAME": None, "CHECK_TYPE": "ROW_COUNT", "CHECK_DEFINITION": '{"kind": "row_count", "gt": 0}',
         "SEVERITY": "WARN", "DOMAIN_ID": "d1"}],
        "SELECT ROWS_SCANNED FROM CORE.CHECK_RUN": [{"ROWS_SCANNED": 90}]})
    seen = {}

    def fake_scan(sess, run_id, checks, lines, fqn, guard, previous):
        seen.update(run_id=run_id, fqn=fqn, previous=previous, n=len(checks))
        return ([{"outcome": "FAIL", "measured": 3.0, "target_column": "EMAIL"}, {"outcome": "PASS", "measured": 100}], 100)

    monkeypatch.setattr(scan, "_scan_model", fake_scan)
    out = dq.run_table(session, "DB.S.ORDERS")
    assert seen == {"run_id": None, "fqn": "DB.S.ORDERS", "previous": 90.0, "n": 2}
    assert out["health"] == 50 and out["failed"] == 1 and out["rows_scanned"] == 100
    sql = [c for c in session.calls]
    run_insert = [c for c in sql if c[0].startswith("INSERT INTO CORE.CHECK_RUN")][0]
    assert "'TABLE'" in run_insert[0] and "RUN_ID" not in run_insert[0].split("SELECT")[0]
    updates = [c for c in sql if c[0].startswith("UPDATE CORE.DQ_CHECKS_CATALOG")]
    assert [u[1][0] for u in updates] == ["FAIL", "PASS"] and updates[0][1][2] == "c1"


def test_scan_model_without_run_uses_previous_rows(monkeypatch):
    import services.quality.scan as scan

    executed = []

    class S:
        def sql(self, sql, params=None):
            executed.append(sql)

            class R:
                def collect(self_inner):
                    if "COUNT(*) AS N" in sql:
                        return [Row({"N": 120})]
                    return []
            return R()

    checks = [{"expectation_id": None, "target_table": "DB.S.T", "target_column": None, "check_type": "ROW_COUNT",
               "severity": "WARN", "definition": {"kind": "change_over_time", "max_percent": 10}}]
    results, total = scan._scan_model(S(), None, checks, [], "DB.S.T", lambda *a: (True, [], ""), 100.0)
    assert total == 120 and not any("RUN_ID = ?" in s for s in executed)


# ---------------------------------------------------------------- API


class FakeDb:
    user, role = "ANA", "DATA_ENGINEER"

    def __init__(self, answers=None, fail=()):
        self.answers, self.fail, self.executed = answers or {}, fail, []

    def query(self, sql, params=()):
        self.executed.append((sql, params))
        for needle in self.fail:
            if needle in sql:
                raise RuntimeError("missing")
        for needle, value in self.answers.items():
            if needle in sql:
                return value
        return []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))


@pytest.fixture
def client(monkeypatch):
    async def open_door(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(gov, "middleware", open_door)
    holder = {"db": FakeDb()}
    main.app.dependency_overrides[main.current_db] = lambda: holder["db"]
    yield TestClient(main.app), holder
    main.app.dependency_overrides.clear()


def test_dq_catalog_tree_and_before_deploy(client):
    c, holder = client
    holder["db"] = FakeDb({"FROM CORE.DQ_CHECKS_CATALOG": [
        {"check_id": "c1", "table_name": "DB.S.T", "column_name": "EMAIL", "check_definition": '{"kind": "not_null"}',
         "soda_dsl": "  - missing_count(EMAIL) = 0", "last_run_status": "PASS"}],
        "FROM CORE.DQ_SCHEDULE": [{"table_name": "DB.S.T", "cron": "0 6 * * *", "enabled": True}]})
    body = c.get("/api/dq/catalog").json()
    t = body["tables"][0]
    assert t["schedule"] == {"cron": "0 6 * * *", "enabled": True}
    assert t["columns"][0]["checks"][0]["headline"] == "missing_count(EMAIL) = 0"
    holder["db"] = FakeDb(fail=("DQ_CHECKS_CATALOG",))
    assert c.get("/api/dq/catalog").json() == {"tables": [], "deployed": False}


def test_dq_parse_preview(client):
    c, _ = client
    ok = c.post("/api/dq/parse", json={"table": "DB.S.T", "dsl": "missing_percent(EMAIL) < 2%"}).json()
    assert ok["ok"] and ok["headline"] == "missing_percent(EMAIL) < 2%"
    assert c.post("/api/dq/parse", json={"table": "DB.S.T", "dsl": "nonsense"}).json()["ok"] is False


def test_dq_writes_call_the_service(client, monkeypatch):
    c, holder = client
    calls = []
    monkeypatch.setattr(quality_api, "invoke_source", lambda db, fn, *a: calls.append((getattr(fn, "__name__", fn), a)) or {"ok": True})
    assert c.post("/api/dq/catalog", json={"table": "DB.S.T"}).status_code == 400
    c.post("/api/dq/catalog", json={"table": "DB.S.T", "dsl": "row_count > 0"})
    c.post("/api/dq/run", json={"table": "DB.S.T"})
    c.post("/api/dq/catalog/seed", json={"table": "DB.S.T"})
    assert c.post("/api/dq/catalog/seed", json={}).status_code == 400
    assert [x[0] for x in calls] == ["stage_check", "run_table", "seed_profile"]


def test_dq_schedule_requires_active_checks_and_valid_cron(client):
    c, holder = client
    assert c.put("/api/dq/schedules", json={"table": "DB.S.T", "cron": "whenever ok"}).status_code == 400
    assert c.put("/api/dq/schedules", json={"table": "DB.S.T", "cron": "0 6 * * *"}).status_code == 409
    holder["db"] = FakeDb({"WHERE TABLE_NAME = %s AND STATUS = 'ACTIVE'": [{"x": 1}]})
    res = c.put("/api/dq/schedules", json={"table": "DB.S.T", "cron": "0 6 * * *"})
    assert res.status_code == 200 and res.json()["task"].endswith(dq.task_name("DB.S.T"))
    assert any(s.startswith("CREATE OR REPLACE TASK") for s, _ in holder["db"].executed)


def test_qa_pack_routes(client, monkeypatch):
    c, holder = client
    holder["db"] = FakeDb({"FROM CORE.DOMAIN_REGISTRY": [{"domain_id": "11111111-1111-1111-1111-111111111111"}]})
    calls = []
    monkeypatch.setattr(qa_api, "invoke_source", lambda db, fn, *a: calls.append((getattr(fn, "__name__", "fn"), a)) or {"ok": True})
    body = {"name": "Integrity", "queries": [{"query_name": "orphans", "sql_expression": "SELECT 1 FROM DB.S.T"}]}
    c.post("/api/domains/COMPANY/qa-packs", json=body)
    assert calls[-1][0] == "create_pack" and calls[-1][1][0] == "11111111-1111-1111-1111-111111111111"
    c.put("/api/qa-packs/p1", json=body)
    c.post("/api/qa-packs/p1/submit", json={})
    c.post("/api/qa-packs/p1/approve")
    c.post("/api/qa/execute", json={"domain_id": "COMPANY", "pack_id": "p1"})
    assert [x[0] for x in calls] == ["create_pack", "save_draft", "submit", "approve", "execute"]
    assert c.post("/api/qa/execute", json={"domain_id": "COMPANY"}).status_code == 400
    holder["db"] = FakeDb()
    assert c.get("/api/qa-packs?domain=NOPE").status_code == 404


def test_qa_pack_file_export(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(qa_api, "invoke_source", lambda db, fn, *a: {
        "pack": {"name": "Integrity checks", "description": "d", "tags": ["sla"]},
        "queries": [{"query_name": "orphans", "severity": "WARN", "sql_expression": "SELECT 1 FROM DB.S.T",
                     "expectation": "0 rows", "tags": []}]})
    res = c.get("/api/qa-packs/p1/file")
    assert res.status_code == 200 and 'filename="integrity_checks.sql"' in res.headers["content-disposition"]
    from services.qa.pack_files import parse
    assert parse(res.text)["queries"][0]["severity"] == "WARN"


@pytest.mark.parametrize("method, path, privilege", [
    ("POST", "/api/dq/catalog", "SODA.EDIT"), ("POST", "/api/dq/catalog/seed", "SODA.EDIT"),
    ("POST", "/api/dq/catalog/x/status", "SODA.EDIT"), ("POST", "/api/dq/run", "PROFILE.RUN"),
    ("PUT", "/api/dq/schedules", "SODA.EDIT"), ("DELETE", "/api/dq/schedules", "SODA.EDIT"),
    ("POST", "/api/dq/parse", None), ("POST", "/api/qa-packs/x/retire", "PACKAGE.APPROVE"),
    ("POST", "/api/qa-packs/x/reject", "PACKAGE.EDIT"),
])
def test_store_routes_are_governed(method, path, privilege):
    assert privilege_for(method, path)[0] == privilege
