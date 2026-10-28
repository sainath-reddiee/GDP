"""Using the domain's approved model as it is: a run is pinned to the model's hub (every active entity becomes its
target set, services/modeling/run_link.pin_run) with no AI design and no proposal; any undecided proposal of the run
is withdrawn. Snowflake is a fake."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.modeling import run_link  # noqa: E402
from test_model_workbench import Session  # noqa: E402
from test_run_targets import property_spec  # noqa: E402


def summary_session(approved=1, spec=None):
    return Session({
        "FROM CORE.DOMAIN_MODEL WHERE DOMAIN_ID = ?": [{"MODEL_ID": "m1", "APPROVED_VERSION": approved, "DOMAIN_NAME": "PROPERTY"}],
        "SELECT SPEC FROM CORE.MODEL_DESIGNS WHERE MODEL_ID = ? AND VERSION = ?": [{"SPEC": json.dumps(spec or property_spec())}],
    })


def test_the_summary_is_the_approved_model_hub_first():
    out = run_link.domain_model_summary(summary_session(), "d1")
    assert out["model_id"] == "m1" and out["version"] == 1 and out["hub"] == "PROPERTY_CORE" and out["domain_name"] == "PROPERTY"
    assert [(e["entity"], e["role"], e["columns"]) for e in out["entities"]] == [
        ("PROPERTY_CORE", "HUB", 2), ("PROPERTY_ADDRESS", "SPOKE", 2), ("PROPERTY_OWNER", "SPOKE", 0), ("PROPERTY_USAGE", "SPOKE", 2)]
    assert out["entities"][0]["fqn"] == "DEV_GDP_SILVER_DB.PROPERTY.PROPERTY_CORE"    # retired PROPERTY_LEGACY is left out


def test_no_approved_model_no_summary():
    assert run_link.domain_model_summary(summary_session(approved=None), "d1") is None
    assert run_link.domain_model_summary(Session(), "d1") is None


def test_without_a_hub_the_first_table_with_columns_is_used():
    spec = property_spec()
    for e in spec["design"]["entities"]:
        if e["entity_name"] == "PROPERTY_CORE":
            e["attributes"] = []
    assert run_link.domain_model_summary(summary_session(spec=spec), "d1")["hub"] == "PROPERTY_ADDRESS"


def test_using_the_model_as_it_is_withdraws_the_runs_pending_proposals():
    s = Session({"WHERE RUN_ID = ? AND STATUS = 'PROPOSED'": [{"CHANGE_ID": "c1"}]})
    assert run_link.withdraw_run_proposals(s, "r1", "ana") == 1
    sql, params = s.find("SET STATUS = 'WITHDRAWN'")[0]
    assert params == ["ANA", "r1"] and "as it is" in sql
    assert run_link.withdraw_run_proposals(Session(), "r1", "ana") == 0


# ---------------------------------------------------------------- the endpoint

SUMMARY = {"model_id": "m1", "version": 1, "domain_name": "PROPERTY", "hub": "PROPERTY_CORE",
           "entities": [{"entity": "PROPERTY_CORE", "role": "HUB", "columns": 2, "fqn": "DB.PROPERTY.PROPERTY_CORE"}]}


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    import app.governance as gov
    import app.main as main

    async def open_door(request, call_next):
        return await call_next(request)

    class Db:
        user = "ana"
        run = {"domain_id": "d1", "current_state": "DOMAIN_IDENTIFIED"}

        def query(self, sql, params=()):
            return [self.run] if "SELECT DOMAIN_ID, CURRENT_STATE FROM CORE.WORKFLOW_RUN" in sql else []

    db = Db()
    calls = {}

    def invoke(d, fn, *args):
        calls.setdefault("invoked", []).append(fn.__name__)
        return {"domain_model_summary": calls.get("summary", SUMMARY), "withdraw_run_proposals": 2}[fn.__name__]

    monkeypatch.setattr(gov, "middleware", open_door)
    monkeypatch.setattr(main, "invoke_source", invoke)
    monkeypatch.setattr(main, "pin_run_model", lambda d, run, m, v, e, dv: calls.update(pin=(run, m, v, e, dv)) or
                        {"pinned": True, "target_model": "DB.PROPERTY.PROPERTY_CORE", "targets": [{"entity": e}]})
    main.app.dependency_overrides[main.current_db] = lambda: db
    yield TestClient(main.app), db, calls
    main.app.dependency_overrides.clear()


def test_the_run_is_pinned_to_the_hub_with_no_design(api):
    client, db, calls = api
    body = client.post("/api/runs/r1/model/use-domain-model").json()
    assert calls["pin"] == ("r1", "m1", 1, "PROPERTY_CORE", 0)          # design version 0: no design is approved
    assert body["entity"] == "PROPERTY_CORE" and body["withdrawn"] == 2 and body["targets"] == [{"entity": "PROPERTY_CORE"}]


@pytest.mark.parametrize("run, summary, text", [
    ({"domain_id": None, "current_state": "DOMAIN_IDENTIFIED"}, SUMMARY, "Identify the run's domain"),
    ({"domain_id": "d1", "current_state": "STTM_REVIEW"}, SUMMARY, "Reopen mapping"),
    ({"domain_id": "d1", "current_state": "DOMAIN_IDENTIFIED"}, None, "no approved model"),
    ({"domain_id": "d1", "current_state": "DOMAIN_IDENTIFIED"}, {**SUMMARY, "hub": None}, "no table with columns"),
])
def test_the_endpoint_refuses_when_there_is_nothing_to_use(api, run, summary, text):
    client, db, calls = api
    db.run, calls["summary"] = run, summary
    r = client.post("/api/runs/r1/model/use-domain-model")
    assert r.status_code == 409 and text in r.json()["detail"] and "pin" not in calls


def test_using_the_domain_model_needs_model_edit():
    from services.governance.policy import privilege_for

    assert privilege_for("POST", "/api/runs/r1/model/use-domain-model")[0] == "MODEL.EDIT"


def test_use_domain_model_filters_to_selected_entities(api):
    client, db, calls = api
    calls["summary"] = {
        "model_id": "m1",
        "version": 1,
        "domain_name": "PROPERTY",
        "hub": "PROPERTY_CORE",
        "entities": [
            {"entity": "PROPERTY_CORE", "role": "HUB", "columns": 2, "fqn": "DB.PROPERTY.PROPERTY_CORE"},
            {"entity": "PROPERTY_ADDRESS", "role": "SPOKE", "columns": 2, "fqn": "DB.PROPERTY.PROPERTY_ADDRESS"},
            {"entity": "PROPERTY_USAGE", "role": "SPOKE", "columns": 2, "fqn": "DB.PROPERTY.PROPERTY_USAGE"},
        ],
    }
    executed = []
    db.execute = lambda sql, params=(): executed.append((sql, params))
    res = client.post("/api/runs/r1/model/use-domain-model", json={"entities": ["PROPERTY_ADDRESS"]})
    assert res.status_code == 200
    body = res.json()
    assert calls["pin"] == ("r1", "m1", 1, "PROPERTY_ADDRESS", 0)
    assert body["entity"] == "PROPERTY_ADDRESS"
    assert [e["entity"] for e in body["entities"]] == ["PROPERTY_ADDRESS"]
    assert any("UPDATE CORE.RUN_TARGET SET INCLUDED" in sql for sql, _ in executed)

