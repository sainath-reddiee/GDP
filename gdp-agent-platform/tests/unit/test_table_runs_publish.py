"""Multi-target mapping, phase 5: a Silver run's table runs (hub and spokes) published as one pull request. Each
table's own files keep their paths; files a generation writes at one fixed path get a path per table; shared files are
kept once, the hub's first. The Gold star schema publishes exactly as before. Snowflake and GitHub are fakes."""

import contextlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.dbt import publish, table_runs  # noqa: E402
from test_model_workbench import Session  # noqa: E402

HUB = {"entity": "PROPERTY_CORE", "files": {
    "models/property/hub/property_core.sql": "select 1", "models/property/hub/_property_core.yml": "version: 2\nsources: []\n",
    "macros/property_utils.sql": "{% macro m() %}1{% endmacro %}", "dbt_project.yml": "name: gdp\n",
    "mappings/sttm.json": '{"t": "core"}', "soda/checks.yml": "checks for property_core: []",
    "release/generation-report.json": "{}", "release/branch.json": "{}", "release/watermark_property_core.sql": "w1"}}
SPOKE = {"entity": "PROPERTY_ADDRESS", "files": {
    "models/property/spoke/property_address.sql": "select 2", "macros/property_utils.sql": "{% macro m() %}2{% endmacro %}",
    "dbt_project.yml": "name: gdp\nvars: {}\n", "mappings/sttm.json": '{"t": "addr"}', "soda/checks.yml": "checks for property_address: []",
    "release/hub_patch.sql": "alter", "release/watermark_property_address.sql": "w2"}}


def test_each_table_keeps_its_files_and_fixed_paths_get_one_per_table():
    out = table_runs.merge_files([HUB, SPOKE])
    files = out["files"]
    assert files["models/property/hub/property_core.sql"] == "select 1" and files["models/property/spoke/property_address.sql"] == "select 2"
    # the YAML of a table is kept whole (a sources section is not rewritten)
    assert files["models/property/hub/_property_core.yml"] == "version: 2\nsources: []\n"
    assert files["mappings/property_core.sttm.json"] == '{"t": "core"}' and files["mappings/property_address.sttm.json"] == '{"t": "addr"}'
    assert files["soda/property_core.yml"].startswith("checks for property_core")
    assert files["soda/property_address.yml"].startswith("checks for property_address")
    assert "release/property_core.generation-report.json" in files and "release/property_address.hub_patch.sql" in files
    assert not {"mappings/sttm.json", "soda/checks.yml", "release/branch.json"} & set(files)
    assert out["moved"]["PROPERTY_ADDRESS"]["soda/checks.yml"] == "soda/property_address.yml"


def test_shared_files_keep_the_hubs_copy_and_differences_are_reported():
    out = table_runs.merge_files([HUB, SPOKE])
    assert out["files"]["macros/property_utils.sql"].endswith("1{% endmacro %}") and out["files"]["dbt_project.yml"] == "name: gdp\n"
    assert out["conflicts"] == ["macros/property_utils.sql (kept PROPERTY_CORE's)"]       # dbt_project.yml is not reported
    same = table_runs.merge_files([HUB, {**SPOKE, "files": {"macros/property_utils.sql": HUB["files"]["macros/property_utils.sql"]}}])
    assert same["conflicts"] == []


# ---------------------------------------------------------------- publishing

def publish_session():
    return Session({
        "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?": [{"RUN_ID": "p1", "RUN_NAME": "LIGHTBOX", "DOMAIN_ID": "d1"}],
        "WHERE R.PARENT_RUN_ID = ?": [{"RUN_ID": "c1", "MODEL_ENTITY": "PROPERTY_CORE", "GENERATION_ID": "g1"},
                                      {"RUN_ID": "c2", "MODEL_ENTITY": "PROPERTY_ADDRESS", "GENERATION_ID": "g2"}],
        "FROM CORE.GENERATED_ARTIFACT WHERE GENERATION_ID = ?": lambda p: [
            {"FILE_PATH": k, "CONTENT": v} for k, v in (HUB if p[0] == "g1" else SPOKE)["files"].items()],
    })


@pytest.fixture
def pushed(monkeypatch):
    seen = {}

    def fake_publish(request, origin, base, head, files, title, body, message, draft=False, root=""):
        seen.update(origin=origin, head=head, files=files, title=title, body=body, message=message)
        return {"status": "PUBLISHED", "pull_request": {"url": "https://github.com/acme/dbt/pull/3"}}

    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(publish.github, "publish", fake_publish)
    monkeypatch.setattr(publish.github, "urllib_request", lambda token: None)
    monkeypatch.setattr(publish, "_plan", lambda s, r, p: {"origin": "https://github.com/acme/dbt", "base_branch": "main"})
    monkeypatch.setattr(publish, "_record", lambda *a: None)
    monkeypatch.setattr(publish, "tool_call", lambda *a, **k: contextlib.nullcontext(type("C", (), {})()))
    monkeypatch.setattr(publish, "conventions_for", lambda *a, **k: {"branch_prefix": "feature/", "pr_title": "{name} v{version}"})
    return seen


def test_a_silver_parent_publishes_every_table_as_one_pull_request(pushed):
    out = publish.publish_dbt_pr(publish_session(), "p1", '{"table_runs": true}')   # CORE.PUBLISH_DBT_PR dispatches it
    assert out["status"] == "PUBLISHED" and out["tables"] == ["PROPERTY_CORE", "PROPERTY_ADDRESS"]
    assert "soda/property_address.yml" in pushed["files"] and "mappings/sttm.json" not in pushed["files"]
    assert pushed["title"] == "LIGHTBOX v2: hub and spokes" and pushed["message"] == "LIGHTBOX: silver hub and spokes (PROPERTY_CORE, PROPERTY_ADDRESS)"
    assert "Target tables: PROPERTY_CORE, PROPERTY_ADDRESS." in pushed["body"] and pushed["head"].startswith("feature/")
    assert out["moved"]["PROPERTY_CORE"]["mappings/sttm.json"] == "mappings/property_core.sttm.json"


def test_the_gold_star_schema_publishes_as_before(pushed, monkeypatch):
    monkeypatch.setattr("services.gold.star.merge_files", lambda parts: {"files": {"a.sql": "x"}, "conflicts": []})
    out = publish.publish_star_schema(publish_session(), "p1", {})
    assert out["status"] == "PUBLISHED" and "moved" not in out
    assert pushed["title"] == "LIGHTBOX v2: star schema" and pushed["message"] == "LIGHTBOX: gold star schema (PROPERTY_CORE, PROPERTY_ADDRESS)"
    assert "Star schema: PROPERTY_CORE, PROPERTY_ADDRESS." in pushed["body"]


def test_every_table_run_needs_its_code(pushed):
    s = publish_session()
    s.answers["WHERE R.PARENT_RUN_ID = ?"] = [{"RUN_ID": "c1", "MODEL_ENTITY": "PROPERTY_CORE", "GENERATION_ID": None}]
    with pytest.raises(AssertionError, match="generate the code of every table first: PROPERTY_CORE"):
        publish.publish_table_runs(s, "p1", {})
    s.answers["WHERE R.PARENT_RUN_ID = ?"] = []
    with pytest.raises(AssertionError, match="approve the mapping first"):
        publish.publish_table_runs(s, "p1", {})


# ---------------------------------------------------------------- the endpoint

@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    import app.governance as gov
    import app.main as main

    async def open_door(request, call_next):
        return await call_next(request)

    class Db:
        def __init__(self):
            self.run = {"parent_run_id": None, "pipeline": "SILVER"}
            self.children = [{"x": 1}]

        def query(self, sql, params=()):
            if "SELECT PARENT_RUN_ID, COALESCE(PIPELINE" in sql:
                return [self.run]
            if "WHERE PARENT_RUN_ID = %s AND DELETED_AT IS NULL LIMIT 1" in sql:
                return self.children
            return []

    db = Db()
    sent = {}
    monkeypatch.setattr(gov, "middleware", open_door)
    monkeypatch.setattr(main, "_publish_dbt", lambda d, run_id, payload: sent.update(payload) or {"status": "PUBLISHED"})
    main.app.dependency_overrides[main.current_db] = lambda: db
    yield TestClient(main.app), db, sent
    main.app.dependency_overrides.clear()


def test_the_endpoint_publishes_a_silver_parent_only(api):
    client, db, sent = api
    r = client.post("/api/runs/p1/table-runs/publish", json={"draft": True})
    assert r.status_code == 200 and sent == {"table_runs": True, "draft": True}
    db.children = []
    assert client.post("/api/runs/p1/table-runs/publish", json={}).status_code == 409
    db.run = {"parent_run_id": "p0", "pipeline": "SILVER"}
    assert client.post("/api/runs/p1/table-runs/publish", json={}).status_code == 400
    db.run = {"parent_run_id": None, "pipeline": "GOLD"}
    assert "Gold panel" in client.post("/api/runs/p1/table-runs/publish", json={}).json()["detail"]


def test_publishing_table_runs_needs_the_dbt_privilege():
    from services.governance.policy import privilege_for

    assert privilege_for("POST", "/api/runs/p1/table-runs/publish")[0] == "DBT.EDIT"
