"""Run assets in the stores (apps/api/app/hub_runs_api.py): run models, dbt generations and their store edits, run data
quality checks and their decisions, run QA tests and promotion into pack drafts. Snowflake is replaced by a fake that
answers by SQL substring."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
import app.governance as gov  # noqa: E402
import app.hub_runs_api as runs_api  # noqa: E402
import app.qa_api as qa_api  # noqa: E402
from services.governance.policy import privilege_for, store_queue  # noqa: E402


class FakeDb:
    user, role = "ANA", "DATA_ENGINEER"

    def __init__(self, answers=None, fail=()):
        self.answers, self.fail = answers or {}, fail
        self.executed = []

    def _answer(self, sql, params):
        for needle in self.fail:
            if needle in sql:
                raise RuntimeError(f"Object '{needle}' does not exist or not authorized")
        for needle, value in self.answers.items():
            if needle in sql:
                return value(params) if callable(value) else value
        return []

    def query(self, sql, params=()):
        self.executed.append((sql, params))
        return self._answer(sql, params)

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        self._answer(sql, params)


@pytest.fixture
def client(monkeypatch):
    async def open_door(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(gov, "middleware", open_door)
    holder = {"db": FakeDb()}
    main.app.dependency_overrides[main.current_db] = lambda: holder["db"]
    yield TestClient(main.app), holder
    main.app.dependency_overrides.clear()


def sqls(db, needle):
    return [(s, p) for s, p in db.executed if needle in s]


DOMAIN = {"FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s OR UPPER(DOMAIN_NAME)": [{"domain_id": "d1"}]}


# ---------------------------------------------------------------- routing and governance

def test_run_models_route_wins_over_design_id(client):
    c, holder = client
    holder["db"] = FakeDb({"FROM MODELING.MODEL_DESIGN": [
        {"design_id": "m1", "run_id": "r1", "run_name": "CRM", "version": 2, "status": "APPROVED",
         "design_json": json.dumps({"entities": [{"name": "DIM_CUSTOMER", "kind": "dimension", "columns": [{}, {}]}]})}]})
    body = c.get("/api/hub/models/runs").json()
    assert body["deployed"] is True
    assert body["models"][0]["entity_count"] == 1
    assert body["models"][0]["entities"][0] == {"name": "DIM_CUSTOMER", "kind": "dimension", "columns": 2}
    assert "design_json" not in body["models"][0]


def test_store_write_routes_always_queue():
    assert store_queue("POST", "/api/dq/run-checks/decisions")
    assert privilege_for("POST", "/api/dq/run-checks/decisions")[0] == "SODA.EDIT"
    # a pack draft is not live: promoting tests needs PACKAGE.EDIT but is not a store queue
    assert privilege_for("POST", "/api/qa/run-tests/promote")[0] == "PACKAGE.EDIT"
    assert not store_queue("POST", "/api/qa/run-tests/promote")


def test_domain_scope_reaches_sql(client):
    c, holder = client
    holder["db"] = FakeDb(dict(DOMAIN))
    c.get("/api/hub/dbt?domain=COMPANY")
    c.get("/api/dq/run-checks?domain=COMPANY")
    c.get("/api/qa/run-tests?domain=COMPANY")
    c.get("/api/hub/models/runs?domain=COMPANY")
    scoped = [p for s, p in holder["db"].executed if "DOMAIN_REGISTRY WHERE DOMAIN_ID = %s OR" not in s]
    assert len(scoped) == 4 and all(p[-1] == "d1" for p in scoped)


def test_unknown_domain_is_404(client):
    c, _ = client
    assert c.get("/api/qa/run-tests?domain=NOPE").status_code == 404


# ---------------------------------------------------------------- dbt

def test_dbt_list_falls_back_before_the_sttm_catalog(client):
    c, holder = client
    holder["db"] = FakeDb({"FROM CODEGEN.DBT_GENERATION_REGISTRY": [{"generation_id": "g1", "run_id": "r1"}]},
                          fail=("GOVERNANCE.STTM_CATALOG",))
    body = c.get("/api/hub/dbt").json()
    assert body == {"generations": [{"generation_id": "g1", "run_id": "r1"}], "deployed": True}


def test_dbt_is_read_only_in_the_store(client):
    c, _ = client
    assert c.post("/api/hub/dbt/g1/files", json={"file_path": "x.sql", "content": "select 1", "note": "abc"}).status_code in (404, 405)
    assert not store_queue("POST", "/api/hub/dbt/g1/files")


def _gen_db(previous=True):
    files = {"g1": {"models/a.sql": "select 1 as id\n", "models/b.sql": "select 2\n", "release/report.json": "{}"},
             "g0": {"models/a.sql": "select id\n", "models/old.sql": "select 0\n", "release/report.json": "[]"}}
    return FakeDb({
        "FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE GENERATION_ID = %s": lambda p: [
            {"generation_id": p[0], "run_id": "r1", "generation_version": 2 if p[0] == "g1" else 1, "generation_status": "GENERATED"}],
        "AND GENERATION_VERSION < %s": [{"generation_id": "g0", "generation_version": 1}] if previous else [],
        "SELECT FILE_PATH, CONTENT FROM CODEGEN.GENERATED_ARTIFACT": lambda p: [
            {"file_path": k, "content": v} for k, v in files[p[0]].items()],
    })


def test_dbt_diff_against_the_previous_generation(client):
    c, holder = client
    holder["db"] = _gen_db()
    body = c.get("/api/hub/dbt/g1/diff").json()
    assert body["base"] == {"generation_id": "g0", "generation_version": 1}
    by = {f["file_path"]: f for f in body["files"]}
    assert set(by) == {"models/a.sql", "models/b.sql", "models/old.sql"}  # release files are not shown
    assert by["models/a.sql"]["status"] == "changed" and "+select 1 as id" in by["models/a.sql"]["patch"]
    assert by["models/b.sql"]["status"] == "added" and by["models/old.sql"]["status"] == "removed"


def test_dbt_diff_of_a_first_generation(client):
    c, holder = client
    holder["db"] = _gen_db(previous=False)
    body = c.get("/api/hub/dbt/g1/diff").json()
    assert body["base"] is None and {f["status"] for f in body["files"]} == {"added"}


def test_dbt_detail(client):
    c, holder = client
    holder["db"] = FakeDb({
        "FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE GENERATION_ID = %s": [
            {"generation_id": "g1", "run_id": "r1", "generation_version": 3, "generation_status": "APPROVED"}],
        "FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE RUN_ID = %s": [{"generation_id": "g1", "generation_version": 3}],
        "FROM CODEGEN.GENERATED_ARTIFACT": [{"file_path": "dbt_project.yml", "content": "name: x"}],
    })
    body = c.get("/api/hub/dbt/g1").json()
    assert body["latest"] is True and "edit_makes_version" not in body and body["files"][0]["file_path"] == "dbt_project.yml"


# ---------------------------------------------------------------- run checks

def test_run_checks_render_and_group(client):
    c, holder = client
    holder["db"] = FakeDb({"FROM CONTRACT.SODA_EXPECTATION_REGISTRY E": [
        {"expectation_id": "e1", "run_id": "r1", "run_name": "CRM", "target_table": "DIM_CUSTOMER", "target_column": "EMAIL",
         "check_type": "NOT_NULL", "check_definition": json.dumps({"kind": "not_null", "evidence": "0.4% null"}),
         "severity": "FAIL", "status": "PROPOSED", "outcome": "FAIL", "sample_rows": json.dumps([{"EMAIL": None}])},
        {"expectation_id": "e2", "run_id": "r1", "run_name": "CRM", "target_table": "DIM_CUSTOMER", "target_column": "ID",
         "check_type": "UNIQUE", "check_definition": {"kind": "unique"}, "severity": "FAIL", "status": "APPROVED"}]})
    body = c.get("/api/dq/run-checks?status=proposed&run=r1&table=dim").json()
    first = body["checks"][0]
    assert first["evidence"] == "0.4% null" and "evidence" not in first["definition"]
    assert first["sample_rows"] == [{"EMAIL": None}] and first["sodacl"]
    assert body["runs"] == [{"run_id": "r1", "run_name": "CRM", "domain_name": None, "current_state": None, "checks": 2,
                             "proposed": 1, "approved": 1, "rejected": 0, "failing": 1}]
    sql, params = sqls(holder["db"], "FROM CONTRACT.SODA_EXPECTATION_REGISTRY E")[0]
    assert params == ("PROPOSED", "r1", "%dim%") and "E.STATUS = %s" in sql
    assert c.get("/api/dq/run-checks?status=bogus").status_code == 400


def test_run_check_coverage(client):
    c, holder = client
    holder["db"] = FakeDb({"FROM CONTRACT.STTM_LINE L": [{"target_column": "ID"}, {"target_column": "EMAIL"},
                                                          {"target_column": "NAME"}],
                           "SELECT DISTINCT TARGET_COLUMN": [{"target_column": "id"}]})
    body = c.get("/api/dq/run-checks/coverage?run=r1").json()
    assert body["covered"] == 1 and body["coverage"] == 33.3
    assert [m["target_column"] for m in body["missing"]] == ["EMAIL", "NAME"]


def test_run_check_decisions_go_through_the_run_logic(client):
    c, holder = client
    holder["db"] = FakeDb({"CALL CONTRACT.SAVE_SODA_DECISIONS": {"decided": 1, "complete": False}})
    holder["db"].call = lambda sql, params=(): holder["db"].executed.append((sql, params)) or {"decided": 1}
    r = c.post("/api/dq/run-checks/decisions", json={"run_id": "r1", "decisions": [
        {"expectation_id": "e1", "decision": "APPROVED", "justification": "agreed with the client"}]})
    assert r.status_code == 200 and r.json() == {"decided": 1}
    sql, params = sqls(holder["db"], "SAVE_SODA_DECISIONS")[0]
    assert params[0] == "r1" and json.loads(params[1]) == [
        {"expectation_id": "e1", "decision": "APPROVED", "justification": "agreed with the client"}]
    assert c.post("/api/dq/run-checks/decisions", json={"run_id": "r1", "decisions": [
        {"expectation_id": "e1", "decision": "MAYBE"}]}).status_code == 422


# ---------------------------------------------------------------- run tests

def test_run_tests_summary_and_history(client):
    c, holder = client
    holder["db"] = FakeDb({"FROM CONTRACT.QA_TEST_CASE T": [
        {"test_id": "t1", "title": "No orphan orders", "outcome": "PASS", "history": json.dumps([{"outcome": "FAIL"}, {"outcome": "PASS"}])},
        {"test_id": "t2", "title": "Dup keys", "outcome": "FAIL", "history": None},
        {"test_id": "t3", "title": "Never ran", "outcome": None, "history": None}]})
    body = c.get("/api/qa/run-tests?run=r1&q=orphan").json()
    assert body["summary"] == {"tests": 3, "executed": 2, "passed": 1, "failed": 1, "pass_rate": 50.0}
    assert body["tests"][0]["history"][0] == {"outcome": "FAIL"} and body["tests"][1]["history"] == []
    assert sqls(holder["db"], "FROM CONTRACT.QA_TEST_CASE T")[0][1] == ("r1", "%orphan%", "%orphan%", "%orphan%")


def test_pack_queries_from_tests():
    out = runs_api.pack_queries([
        {"title": "No orphan orders!", "sql_text": "SELECT 1", "severity": "HIGH", "category": "INTEGRITY", "expected": None},
        {"title": "No orphan orders!", "sql_text": "SELECT 2", "severity": "LOW", "objective": "why"}], {"no_orphan_orders"})
    assert [q["query_name"] for q in out] == ["no_orphan_orders_2", "no_orphan_orders_3"]
    assert out[0]["severity"] == "ERROR" and out[1]["severity"] == "WARN"
    assert out[0]["expectation"] == "0 rows" and out[0]["tags"] == ["integrity"] and out[1]["description"] == "why"


TESTS = {"FROM CONTRACT.QA_TEST_CASE\n": [{"test_id": "t1", "title": "Orphans", "sql_text": "SELECT 1 FROM X", "severity": "HIGH"},
                                          {"test_id": "t2", "title": "Dups", "sql_text": "SELECT 2 FROM X", "severity": "LOW"}]}


def test_promote_tests_into_a_new_pack(client, monkeypatch):
    c, holder = client
    holder["db"] = FakeDb({**DOMAIN, "WHERE NOT COALESCE(IS_DELETED, FALSE) AND TEST_ID IN": TESTS["FROM CONTRACT.QA_TEST_CASE\n"]})
    seen = {}

    class Packs:
        @staticmethod
        def create_pack(session, did, payload):
            seen.update(did=did, payload=json.loads(payload))
            return {"pack_id": "p9", "version": 1}

    monkeypatch.setattr(qa_api, "_svc", lambda name: Packs)
    monkeypatch.setattr(qa_api, "_call", lambda db, fn, *a: fn(None, *a))
    body = c.post("/api/qa/run-tests/promote", json={"test_ids": ["t1", "t2", "t1"], "domain": "COMPANY", "name": "From runs"}).json()
    assert body["pack_id"] == "p9" and body["added"] == 2
    assert seen["did"] == "d1" and [q["query_name"] for q in seen["payload"]["queries"]] == ["orphans", "dups"]
    assert c.post("/api/qa/run-tests/promote", json={"test_ids": ["t1", "t2"], "domain": "COMPANY"}).status_code == 400


def test_promote_tests_into_an_existing_pack_skips_known_sql(client, monkeypatch):
    c, holder = client
    holder["db"] = FakeDb({**DOMAIN, "WHERE NOT COALESCE(IS_DELETED, FALSE) AND TEST_ID IN": TESTS["FROM CONTRACT.QA_TEST_CASE\n"]})
    saved = {}

    class Packs:
        @staticmethod
        def get_pack(session, pid, version):
            return {"pack": {"pack_id": pid, "domain_id": "d1", "name": "Keys", "version": 4, "tags": []},
                    "queries": [{"query_name": "orphans", "sql_expression": "select 1  from x", "severity": "ERROR"}]}

        @staticmethod
        def save_draft(session, pid, payload):
            saved.update(json.loads(payload))
            return {"version": 5}

    monkeypatch.setattr(qa_api, "_svc", lambda name: Packs)
    monkeypatch.setattr(qa_api, "_call", lambda db, fn, *a: fn(None, *a))
    body = c.post("/api/qa/run-tests/promote", json={"test_ids": ["t1", "t2"], "domain": "d1", "pack_id": "p1"}).json()
    assert body["added"] == 1 and body["skipped"] == 1
    assert saved["base_version"] == 4 and [q["query_name"] for q in saved["queries"]] == ["orphans", "dups"]


def test_promote_rejects_missing_tests(client):
    c, holder = client
    holder["db"] = FakeDb({**DOMAIN, "WHERE NOT COALESCE(IS_DELETED, FALSE) AND TEST_ID IN": []})
    assert c.post("/api/qa/run-tests/promote", json={"test_ids": ["t1"], "domain": "d1", "name": "x"}).status_code == 404
