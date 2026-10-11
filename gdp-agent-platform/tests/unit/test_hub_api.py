"""Lifecycle hub routers: request validation, what reaches SQL, orchestration of share onboarding, and graceful
behaviour before V035/V036 are deployed. Snowflake is replaced by a fake that answers by SQL substring."""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402  (first: main registers the hub routers)
import app.governance as gov  # noqa: E402
import app.hub_catalog_api as hub_catalog  # noqa: E402
import app.hub_models_api as hub_models  # noqa: E402
import app.hub_sources_api as hub_sources  # noqa: E402


class FakeDb:
    user, role = "ANA", "DATA_ENGINEER"

    def __init__(self, answers=None, fail=()):
        self.answers, self.fail = answers or {}, fail
        self.executed, self.calls = [], []

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

    def call(self, sql, params=()):
        self.calls.append((sql, params))
        return self._answer(sql, params)


@pytest.fixture
def client(monkeypatch):
    async def open_door(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(gov, "middleware", open_door)
    holder = {"db": FakeDb()}
    main.app.dependency_overrides[main.current_db] = lambda: holder["db"]
    yield TestClient(main.app), holder
    main.app.dependency_overrides.clear()


ANSWERS = {"source_name": "crm_oracle", "source_type": "ORACLE", "connection_type": "JDBC", "host": "db",
           "auth_secret": "s3cret", "objects": "ACCOUNTS", "frequency": "DAILY",
           "target_landing_table": "LANDING.CRM_ORACLE__ACCOUNTS"}


# ---------------------------------------------------------------- sources


def test_intake_form_defaults(client):
    c, _ = client
    body = c.get("/api/hub/intake-form").json()
    assert body["customized"] is False and body["form"]["sections"][0]["title"] == "Source"


def test_register_pipeline_seals_secrets(client, monkeypatch):
    c, holder = client
    monkeypatch.setenv("AIP_SECRET_KEY", "k" * 32)
    res = c.post("/api/hub/pipelines", json={"answers": {**ANSWERS, "domain_id": "d-1"}})
    assert res.status_code == 200, res.text
    assert res.json()["pipeline"]["domain_id"] == "d-1"
    assert res.json()["pipeline"]["has_secret"] is True and "secret_sealed" not in res.json()["pipeline"]
    insert = [p for sql, p in holder["db"].executed if "INSERT INTO PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG" in sql][0]
    assert isinstance(insert[5], bytes) and insert[5].startswith(b"gdp1:")
    assert "s3cret" not in json.dumps([v for v in insert if not isinstance(v, bytes)])


def test_register_pipeline_errors(client, monkeypatch):
    c, holder = client
    res = c.post("/api/hub/pipelines", json={"answers": {**ANSWERS, "source_type": "MAINFRAME"}})
    assert res.status_code == 422 and "source_type" in res.json()["detail"]["errors"]
    monkeypatch.delenv("AIP_SECRET_KEY", raising=False)
    monkeypatch.delenv("JIRA_TOKEN_KEY", raising=False)
    assert c.post("/api/hub/pipelines", json={"answers": ANSWERS}).status_code == 503
    holder["db"] = FakeDb({"WHERE SOURCE_NAME = %s": [{"source_id": "x"}]})
    assert c.post("/api/hub/pipelines", json={"answers": {**ANSWERS, "auth_secret": ""}}).status_code == 409


def test_list_pipelines_masks_and_tolerates_missing_table(client):
    c, holder = client
    holder["db"] = FakeDb({"FROM PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG": [
        {"source_id": "1", "source_name": "A", "secret_sealed": b"gdp1:x", "connection_metadata": '{"host": "h"}'}]})
    rows = c.get("/api/hub/pipelines").json()["pipelines"]
    assert rows == [{"source_id": "1", "source_name": "A", "connection_metadata": {"host": "h"}, "has_secret": True}]
    holder["db"] = FakeDb(fail=("INGESTION_PIPELINE_CATALOG",))
    assert c.get("/api/hub/pipelines").json() == {"pipelines": [], "deployed": False}


def _share_db(**extra):
    return FakeDb({"FROM INFORMATION_SCHEMA.DATABASES WHERE DATABASE_NAME": [{"type": "IMPORTED DATABASE"}],
                   "INFORMATION_SCHEMA.TABLES": [{"table_name": "ORDERS"}, {"table_name": "CUSTOMERS"}],
                   "CORE.CREATE_RUN": {"run_id": "run-1"}, **extra})


def test_share_onboarding_walks_the_run(client, monkeypatch):
    c, holder = client
    holder["db"] = _share_db()
    steps = []

    def fake_call(db, proc, handler, *args):
        steps.append((proc, args))
        if "REGISTER" in proc:
            return {"objects_discovered": 2}
        if "ACCESS" in proc:
            return {"passed": True, "checks": []}
        return {"tables": [{"object": "ORDERS", "status": "COMPLETE"}]}

    monkeypatch.setattr(hub_sources, "_source_call", fake_call)
    res = c.post("/api/hub/sources/share/onboard", json={"database": "share_db", "schema": "sales",
                                                         "tables": ["ORDERS"], "source_system_name": "PARTNER"})
    body = res.json()
    assert res.status_code == 200 and body["ok"] and body["continue_at"] == "/runs/run-1/profile"
    assert [p for p, _ in steps] == ["CALL SOURCE.REGISTER_SOURCE(%s, %s)", "CALL SOURCE.VALIDATE_SOURCE_ACCESS(%s, %s)",
                                     "CALL SOURCE.EXECUTE_STAGED_LANDING(%s)"]
    registered = json.loads(steps[0][1][1])
    assert registered["SOURCE_TYPE"] == "SNOWFLAKE_SHARE" and registered["DATABASE"] == "SHARE_DB"
    assert json.loads(steps[1][1][1]) == ["ORDERS"]


def test_share_onboarding_stops_at_the_failing_step(client, monkeypatch):
    c, holder = client
    holder["db"] = _share_db()
    monkeypatch.setattr(hub_sources, "_source_call",
                        lambda db, proc, h, *a: {"passed": False, "checks": [{"status": "FAILED", "detail": "no grant"}]}
                        if "ACCESS" in proc else {"objects_discovered": 1})
    body = c.post("/api/hub/sources/share/onboard", json={"database": "share_db", "schema": "sales",
                                                          "tables": ["ORDERS"], "source_system_name": "P"}).json()
    assert body["ok"] is False and body["steps"][-1] == {"step": "access", "ok": False, "detail": "no grant"}
    assert body["continue_at"] == "/runs/run-1/source"


def test_share_onboarding_input_checks(client):
    c, holder = client
    holder["db"] = _share_db()
    res = c.post("/api/hub/sources/share/onboard", json={"database": "share_db", "schema": "sales",
                                                         "tables": ["NOPE"], "source_system_name": "P"})
    assert res.status_code == 400 and "NOPE" in res.json()["detail"]
    holder["db"] = FakeDb({"FROM INFORMATION_SCHEMA.DATABASES WHERE DATABASE_NAME": [{"type": "STANDARD"}]})
    res = c.post("/api/hub/sources/share/preview", json={"database": "db", "schema": "s"})
    assert res.status_code == 400 and "not a mounted share" in res.json()["detail"]
    with pytest.raises(AssertionError):
        hub_sources.mount_sql("DB", "acct; drop", "SHARE")
    assert hub_sources.mount_sql("DB", "ORG.ACCT", "SALES_SHARE") == \
        'CREATE DATABASE IF NOT EXISTS "DB" FROM SHARE ORG.ACCT.SALES_SHARE'


# ---------------------------------------------------------------- profiles and STTM


def test_profile_search_merges_legacy_tables(client, monkeypatch):
    c, holder = client
    holder["db"] = FakeDb({
        "FROM GOVERNANCE.DATA_PROFILES": [{"profile_id": "p1", "table_fqn": "DB.S.A", "semantic_types": '["ID", null]',
                                           "total": 1, "database_name": "DB", "schema_name": "S", "table_name": "A"}],
        "FROM GOVERNANCE.PROFILE_BOOKMARK": [{"table_fqn": "DB.S.A"}],
    })
    monkeypatch.setattr(hub_catalog, "_store_rows", lambda db: [
        {"source_name": "X", "database_name": "DB", "schema_name": "S", "table_name": "A", "source_fingerprint": "f"},
        {"source_name": "X", "database_name": "DB", "schema_name": "S", "table_name": "B", "source_fingerprint": "f"},
        {"source_name": "ORACLE_X", "database_name": "O", "schema_name": "S", "table_name": "C", "source_fingerprint": "f"}])
    body = c.get("/api/hub/profiles").json()
    assert [r["table_fqn"] for r in body["profiles"]] == ["DB.S.A", "DB.S.B"] and body["total"] == 2
    assert body["profiles"][0]["bookmarked"] and body["profiles"][0]["semantic_types"] == ["ID"]
    assert body["profiles"][1]["catalog"] is False


def test_bookmarks_require_a_table_fqn(client):
    c, holder = client
    assert c.post("/api/hub/profiles/bookmarks", json={"table_fqn": "just_a_name"}).status_code == 400
    assert c.post("/api/hub/profiles/bookmarks", json={"table_fqn": "db.s.t"}).json()["table_fqn"] == "DB.S.T"
    merge = [p for sql, p in holder["db"].executed if "MERGE INTO GOVERNANCE.PROFILE_BOOKMARK" in sql][0]
    assert merge[:2] == ("ANA", "DB.S.T")


def _sttm_db(version=2):
    return FakeDb({"FROM GOVERNANCE.STTM_CATALOG WHERE CATALOG_ID = %s": lambda p: [{
        "catalog_id": "c1", "version": p[1] if len(p) > 1 else version, "source_system": "CRM",
        "target_entity": "DB.S.DIM_X", "run_id": None, "lines": json.dumps([
            {"target_column": "ID", "source_column": "ID", "mapping_type": "DIRECT", "target_datatype": "NUMBER"}]),
        "table_design": "{}", "source_entities": "[]"}]})


def test_sttm_edit_versions_and_conflicts(client, monkeypatch):
    c, holder = client
    holder["db"] = _sttm_db()
    written = {}

    def fake_invoke(db, handler, *args, **kwargs):
        written.update(args=args, kwargs=kwargs)
        return {"catalog_id": "c1", "version": 3, "lines": 1}

    monkeypatch.setattr(hub_catalog, "invoke_source", fake_invoke)
    stale = c.post("/api/hub/sttm/c1/edit", json={"base_version": 1, "edits": [{"target_column": "ID"}]})
    assert stale.status_code == 409
    bad = c.post("/api/hub/sttm/c1/edit", json={"base_version": 2, "edits": [{"target_column": "ID",
                                                                                "transformation": "1; DROP TABLE X"}]})
    assert bad.status_code == 400
    ok = c.post("/api/hub/sttm/c1/edit", json={"base_version": 2, "edits": [{"target_column": "ID",
                                                                               "transformation": "CAST(ID AS NUMBER)"}]})
    assert ok.status_code == 200 and ok.json()["version"] == 3
    handler_args = written["args"]
    assert handler_args[0] == "c1" and handler_args[2] is False  # not an approved replay
    payload = json.loads(handler_args[1])
    assert payload["base_version"] == 2 and payload["edits"][0]["transformation"] == "CAST(ID AS NUMBER)"
    empty = c.post("/api/hub/sttm/c1/edit", json={"base_version": 2})
    assert empty.status_code == 400 and "nothing to change" in empty.json()["detail"]
    full = c.post("/api/hub/sttm/c1/edit", json={
        "base_version": 2, "add_lines": [{"target_column": "NAME", "source_column": "NAME"}],
        "design": {"filters": [{"condition": "ACTIVE"}]},
        "joins": {"driving_table": "A", "joins": []}, "note": "n"})
    assert full.status_code == 200 and json.loads(written["args"][1])["joins"] == {"driving_table": "A", "joins": []}


def test_sttm_preview_context_refine_and_csv(client, monkeypatch):
    c, holder = client
    holder["db"] = _sttm_db()
    preview = c.get("/api/hub/sttm/c1/preview").json()
    assert "DB.S.DIM_X" in preview["sql"] and preview["filters"] == []
    ctx = c.get("/api/hub/sttm/c1/context").json()
    assert ctx["run_id"] is None and ctx["candidates"] == [] and isinstance(ctx["columns"], dict)
    refine = c.post("/api/hub/sttm/c1/refine", json={"target_column": "ID", "prompt": "trim it"})
    assert refine.status_code == 400 and "run" in refine.json()["detail"]
    csv = c.get("/api/hub/sttm/c1/export.csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv") and "target_column" in csv.text


def test_sttm_export_zip(client):
    c, holder = client
    holder["db"] = _sttm_db()
    res = c.get("/api/hub/sttm/c1/export")
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(res.content)).namelist()
    assert "dbt_project.yml" in names and "models/marts/gdp/dim_x.sql" in names


def test_catalog_reads_before_deploy(client):
    c, holder = client
    holder["db"] = FakeDb(fail=("GOVERNANCE.",))
    assert c.get("/api/hub/sttm").json() == {"mappings": [], "total": 0, "deployed": False}
    assert c.get("/api/hub/models/context").json()["deployed"] is False


# ---------------------------------------------------------------- models


SPEC = {"domain": "SALES_X", "mode": "NEW", "design": {"entities": [
    {"entity_name": "DIM_A", "business_keys": ["ID"],
     "attributes": [{"name": "ID", "datatype": "NUMBER", "is_pk": True, "nullable": False, "derived": True}]}]}}


def test_models_render_and_finalize(client, monkeypatch):
    c, holder = client
    holder["db"] = FakeDb({"FROM KNOWLEDGE.DOMAIN_REGISTRY": [{"domain_id": "d1", "domain_name": "COMPANY"}],
                           "COALESCE(MAX(VERSION), 0) AS V": [{"v": 1}]})
    monkeypatch.setattr(hub_models, "_dbt_repos", lambda db, domain: [])
    body = c.post("/api/hub/models/render", json={"spec": SPEC}).json()
    assert body["domain_pack"]["domain"]["name"] == "SALES_X" and body["branch"].startswith("feature/model-sales-x-")
    assert "CREATE TABLE IF NOT EXISTS SALES_X.DIM_A" in body["ddl"]
    clash = c.post("/api/hub/models/d9/finalize", json={"spec": {**SPEC, "domain": "COMPANY"}})
    assert clash.status_code == 422
    ok = c.post("/api/hub/models/d9/finalize", json={"spec": SPEC})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    insert = [p for sql, p in holder["db"].executed if "INSERT INTO GOVERNANCE.MODEL_DESIGNS" in sql][0]
    assert insert[5] == "FINALIZED" and "domain/sales_x/domain_pack.json" in json.loads(insert[9])


def test_models_publish_guards(client, monkeypatch):
    c, holder = client
    holder["db"] = FakeDb({"FROM GOVERNANCE.MODEL_DESIGNS WHERE DESIGN_ID": [{"domain_name": "SALES_X",
                                                                             "status": "DRAFT"}]})
    assert c.post("/api/hub/models/d1/publish", json={"version": 1, "repo_id": "r"}).status_code == 409
    holder["db"] = FakeDb({"FROM GOVERNANCE.MODEL_DESIGNS WHERE DESIGN_ID": [{"domain_name": "SALES_X",
                                                                             "status": "FINALIZED"}]})
    monkeypatch.setattr(hub_models, "_dbt_repos", lambda db, d: [{"repo_id": "r", "provider": "GITHUB", "branch": "main",
                                                                  "name": "repo"}])
    monkeypatch.setattr(hub_models, "_github_publisher_ready", lambda db: True)
    holder["db"].answers["CODEGEN.PUBLISH_DBT_PR"] = {"status": "PUBLISHED"}
    body = c.post("/api/hub/models/d1/publish", json={"version": 1, "repo_id": "r"}).json()
    assert body["status"] == "PUBLISHED" and body["head_branch"].startswith("feature/model-sales-x-")
    request = json.loads(holder["db"].calls[-1][1][1])["model_branch"]
    assert request == {"design_id": "d1", "version": 1, "repo_id": "r", "head": body["head_branch"], "base": "main",
                       "title": None}


# ---------------------------------------------------------------- quality and onboarding


def test_quality_gates(client):
    c, holder = client
    holder["db"] = FakeDb({
        "FROM CORE.WORKFLOW_RUN": [{"run_id": "r1", "run_name": "Run 1"}],
        "FROM QUALITY.CHECK_RUN WHERE RUN_ID IN": [{"run_id": "r1", "health": 98, "failed": 0}],
        "FROM QUALITY.QA_RUN WHERE RUN_ID IN": [{"run_id": "r1", "tests": 3, "failed": 0, "errors": 0}],
        "FROM CONTRACT.QA_SIGNOFF": [{"run_id": "r1", "decision": "APPROVED", "decided_by": "ANA"}],
    })
    run = c.get("/api/hub/quality/gates").json()["runs"][0]
    assert run["status"] == "READY" and run["run_name"] == "Run 1"


def test_onboarding_validate(client):
    c, _ = client
    pack = {"domain": {"name": "sales"}, "targets": [{"table": "ORDERS", "columns": [{"name": "ID"}]}], "knowledge": []}
    contract = "domain: billing\ntables:\n  - name: ORDERS\n    columns:\n      - name: ID\n"
    body = c.post("/api/hub/onboarding/validate", json={"pack": pack, "contract_name": "c.yaml",
                                                        "contract_text": contract}).json()
    assert body["pack"]["ok"] and body["pack"]["domain"] == "SALES" and not body["contract"]["ok"]
    assert any("BILLING" in p for p in body["contract"]["problems"]) and body["ok"] is False
    gdp = c.post("/api/hub/onboarding/validate", json={"pack": {"domain": {"name": "GDP"}}}).json()
    assert not gdp["pack"]["ok"]
    assert c.post("/api/hub/onboarding/validate", json={}).status_code == 400
