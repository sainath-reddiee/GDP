"""Environments routes (apps/api/app/environments_api.py) and the QA execute options for environments: validation,
versioned saves, the preview's unsaved bindings, dry runs and runs on a named environment. Services are fakes."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402  first: the routers import from it
import app.environments_api as environments_api  # noqa: E402
import app.governance as gov  # noqa: E402
import app.qa_api as qa_api  # noqa: E402

PACK = "11111111-2222-3333-4444-555555555555"
SAVED = {"current": "DEV", "environments": [{"name": "DEV", "description": "", "bindings": {"silver": "DEV_DB"}},
                                            {"name": "DA", "description": "", "bindings": {"silver": "DA_DB"}}]}


class FakeDb:
    user, role, shared = "ANA", "ADMIN", True

    def __init__(self, saved=None):
        self.saved, self.executed = saved, []

    def query(self, sql, params=()):
        if "FROM CORE.DOMAIN_REGISTRY" in sql:
            return [{"domain_id": "d1"}]
        if "SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG" in sql:
            return [{"config_value": json.dumps(self.saved)}] if self.saved is not None else []
        if "SELECT VERSION, IS_CURRENT" in sql:
            return [{"version": 1, "is_current": True, "created_by": "ANA", "created_at": "2026-10-21", "description": "x"}]
        return []

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), params))


@pytest.fixture
def api(monkeypatch):
    calls = []

    def record(name, result):
        def fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result
        return fn

    packs = SimpleNamespace(execute=record("execute", {"stored": True}), dry_run=record("dry_run", {"ok": True}),
                            get_pack=record("get_pack", {"pack": {"version": 3}, "approved_version": 2}))
    monkeypatch.setitem(sys.modules, "services.qa.packs", packs)
    monkeypatch.setattr(qa_api, "invoke_source", lambda db, handler, *args: handler("SESSION", *args))

    async def open_door(request, call_next):
        return await call_next(request)
    monkeypatch.setattr(gov, "middleware", open_door)
    db = FakeDb(SAVED)
    main.app.dependency_overrides[main.current_db] = lambda: db
    try:
        yield TestClient(main.app), calls, db
    finally:
        main.app.dependency_overrides.pop(main.current_db, None)


def test_read_and_save_bindings(api):
    client, _, db = api
    body = client.get("/api/config/environments").json()
    assert body["config"] == SAVED and body["saved"] and body["reserved"] == ["domain"] and body["history"][0]["version"] == 1
    res = client.put("/api/config/environments", json={"config": {"current": "dev", "environments": [
        {"name": "dev", "bindings": {"silver": "dev_db"}}]}})
    assert res.status_code == 200
    insert = next(p for s, p in db.executed if s.startswith("INSERT INTO CORE.PLATFORM_CONFIG"))
    assert insert[0] == "ENVIRONMENT_BINDINGS" and json.loads(insert[1]) == {
        "current": "DEV", "environments": [{"name": "DEV", "description": "", "bindings": {"silver": "DEV_DB"}}]}
    assert any(s.startswith("UPDATE CORE.PLATFORM_CONFIG SET IS_CURRENT = FALSE") for s, _ in db.executed)
    bad = client.put("/api/config/environments", json={"config": {"current": "DEV", "environments": [
        {"name": "DEV", "bindings": {"domain": "X"}}]}})
    assert bad.status_code == 400 and "reserved" in bad.json()["detail"]


def test_preview_passes_unsaved_bindings_to_the_dry_run(api):
    client, calls, _ = api
    proposed = {"current": "DEV", "environments": [{"name": "DEV", "bindings": {"silver": "OTHER_DB"}}]}
    res = client.post("/api/config/environments/preview", json={"config": proposed, "environment": "DEV", "pack_id": PACK,
                                                                "domain_id": "COMPANY"})
    assert res.status_code == 200
    name, args, _ = calls[-1]
    assert name == "dry_run" and args[:5] == ("SESSION", "d1", PACK, 2, "DEV") and json.loads(args[6]) == proposed
    bad = client.post("/api/config/environments/preview", json={"config": {"current": "X", "environments": []},
                                                                "pack_id": PACK, "domain_id": "d1"})
    assert bad.status_code == 400


def test_qa_environments_lists_names_for_the_run_menu(api):
    client, _, _ = api
    body = client.get("/api/qa/environments").json()
    assert body["current"] == "DEV" and [e["name"] for e in body["environments"]] == ["DEV", "DA"]
    assert body["environments"][0]["current"] and not body["environments"][1]["current"]


def test_execute_on_an_environment_and_dry_run(api):
    client, calls, _ = api
    client.post("/api/qa/execute", json={"domain_id": "d1", "pack_id": PACK, "environment": "DA"})
    assert calls[-1][0] == "execute" and calls[-1][2] == {"environment": "DA"}
    client.post("/api/qa/execute", json={"domain_id": "d1", "pack_id": PACK})
    assert calls[-1][0] == "execute" and calls[-1][2] == {}   # no environment: this deployment, as before
    client.post("/api/qa/execute", json={"domain_id": "d1", "pack_id": PACK, "version": 2, "environment": "DA", "dry_run": True})
    assert calls[-1] == ("dry_run", ("SESSION", "d1", PACK, 2, "DA", None), {})
    res = client.post("/api/qa/execute", json={"domain_id": "d1", "sql": "SELECT 1 FROM A.B.C", "dry_run": True})
    assert res.status_code == 400


def test_logical_pack_file(api, monkeypatch):
    client, _, _ = api
    query = {"query_name": "orphans", "severity": "ERROR", "expectation": "0 rows", "tags": [], "description": None,
             "sql_expression": "SELECT ID FROM DEV_DB.COMPANY.CORE"}
    sys.modules["services.qa.packs"].get_pack = lambda *a: {"pack": {"name": "Integrity", "tags": []}, "queries": [query]}
    plain = client.get(f"/api/qa-packs/{PACK}/file").text
    logical = client.get(f"/api/qa-packs/{PACK}/file", params={"logical": "true"}).text
    assert "DEV_DB.COMPANY.CORE" in plain and "{{silver}}.COMPANY.CORE" in logical and "DEV_DB" not in logical


def test_environments_router_is_registered():
    assert environments_api.router.routes
