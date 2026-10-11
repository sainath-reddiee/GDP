"""Model Workbench PR 3: publishing approved versions to GitHub, syncing pull request state back, and runs proposing
model changes (merge into the draft, pin the run on approval). Snowflake and GitHub are replaced by fakes."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.dbt import github, publish  # noqa: E402
from services.modeling import domain_model as dm  # noqa: E402
from services.modeling import render_v2, run_link, spec_v2  # noqa: E402
from test_model_workbench import Session, model_session, spec_fixture  # noqa: E402


# ---------------------------------------------------------------- GitHub pull request state


@pytest.mark.parametrize("body,state", [
    ({"state": "closed", "merged": True, "merge_commit_sha": "abc"}, "MERGED"),
    ({"state": "closed", "merged": False}, "CLOSED"),
    ({"state": "open", "draft": True}, "DRAFT"),
    ({"state": "open"}, "OPEN"),
])
def test_pull_status(body, state):
    seen = []

    def request(method, path, payload):
        seen.append((method, path))
        return 200, {"number": 7, "html_url": "https://github.com/acme/models/pull/7", **body}

    out = github.pull_status(request, "https://github.com/acme/models.git", 7)
    assert out["state"] == state and seen == [("GET", "/repos/acme/models/pulls/7")]
    assert out["merge_commit_sha"] == ("abc" if state == "MERGED" else None)


def test_artifacts_stay_under_the_domain_folder():
    files = render_v2.artifacts(spec_fixture(), {"domain": {"name": "SALES", "owner": "ana"}, "targets": [], "knowledge": [{"key": "k"}]})
    assert all(p.startswith("domain/sales/") for p in files)
    assert {"domain/sales/domain_pack.json", "domain/sales/model.sql", "domain/sales/data_dictionary.csv",
            "domain/sales/dbt/models/marts/sales/_schema.yml"} <= set(files)
    pack = json.loads(files["domain/sales/domain_pack.json"])
    assert pack["knowledge"] == [{"key": "k"}] and pack["domain"]["owner"] == "ana" and len(pack["targets"]) == 2


# ---------------------------------------------------------------- publish and sync


def _publish_session(status="APPROVED"):
    return Session({"JOIN GOVERNANCE.DOMAIN_MODEL M ON M.MODEL_ID = D.MODEL_ID": [
                        {"STATUS": status, "SPEC": json.dumps(spec_fixture()), "DOMAIN_ID": "d1", "DOMAIN_NAME": "SALES"}],
                    "FROM CODE.REPO WHERE REPO_ID = ? AND ENABLED": [{"GIT_URL": "https://github.com/acme/models", "BRANCH": "main",
                                                                      "PROVIDER": "GITHUB"}]})


REQ = {"model_id": "m1", "version": 3, "repo_id": "r1", "head": "feature/model-sales-v3"}


def test_publish_domain_model_pushes_the_approved_files(monkeypatch):
    seen = {}

    def fake_publish(request, origin, base, head, files, title, body, message, draft=False):
        seen.update(origin=origin, base=base, head=head, files=files, draft=draft, title=title)
        return {"status": "PUBLISHED", "pull_request": {"number": 9, "url": "https://github.com/acme/models/pull/9"}}

    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(github, "publish", fake_publish)
    monkeypatch.setattr("services.knowledge.packs.export_pack", lambda q, d: {"domain": {"name": "SALES"}, "knowledge": []})
    s = _publish_session()
    out = publish.publish_dbt_pr(s, "", json.dumps({"domain_model": REQ}))
    assert out["status"] == "PUBLISHED" and seen["head"] == "feature/model-sales-v3" and seen["base"] == "main"
    assert not seen["draft"] and all(p.startswith("domain/sales/") for p in seen["files"]) and "v3" in seen["title"]
    update = s.find("UPDATE GOVERNANCE.MODEL_DESIGNS SET STATUS = ?, REPO_ID = ?")[0][1]
    assert update[:6] == ["APPROVED", "r1", "feature/model-sales-v3", "https://github.com/acme/models/pull/9", "9", "OPEN"]


def test_publish_refuses_unapproved_versions_and_bad_branches(monkeypatch):
    monkeypatch.setattr(publish, "_token", lambda: "t")
    with pytest.raises(AssertionError, match="only an approved model version"):
        publish.publish_domain_model(_publish_session("IN_REVIEW"), REQ)
    with pytest.raises(AssertionError, match="feature/model-"):
        publish.publish_domain_model(_publish_session(), {**REQ, "head": "main2"})


def test_publish_failure_keeps_the_version_approved(monkeypatch):
    def boom(*a, **k):
        raise github.PublishError("create tree", 403, {"message": "no write access"})

    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(github, "publish", boom)
    monkeypatch.setattr("services.knowledge.packs.export_pack", lambda q, d: None)
    s = _publish_session()
    out = publish.publish_domain_model(s, REQ)
    assert out["status"] != "PUBLISHED"
    update = s.find("UPDATE GOVERNANCE.MODEL_DESIGNS SET STATUS = ?, REPO_ID = ?")[0][1]
    assert update[0] == "APPROVED" and update[5] == "FAILED"


def test_publish_with_nothing_new_marks_published(monkeypatch):
    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(github, "publish", lambda *a, **k: {"status": "NO_CHANGES", "pull_request": {}})
    monkeypatch.setattr("services.knowledge.packs.export_pack", lambda q, d: None)
    s = _publish_session()
    publish.publish_domain_model(s, REQ)
    assert s.find("UPDATE GOVERNANCE.MODEL_DESIGNS SET STATUS = ?, REPO_ID = ?")[0][1][0] == "PUBLISHED"


def test_sync_model_prs_marks_merged_and_closed(monkeypatch):
    states = {7: "MERGED", 8: "CLOSED", 9: "ERR"}

    def status(request, origin, number):
        if states[number] == "ERR":
            raise github.PublishError("read pull request", 404, {"message": "gone"})
        return {"state": states[number], "merge_commit_sha": "sha7" if number == 7 else None}

    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(github, "pull_status", status)
    s = Session({"JOIN CODE.REPO R ON R.REPO_ID = D.REPO_ID": [
        {"MODEL_ID": "m1", "VERSION": v, "PR_NUMBER": n, "GIT_URL": "https://github.com/acme/models"} for v, n in [(3, 7), (2, 8), (1, 9)]]})
    out = publish.publish_dbt_pr(s, "", json.dumps({"sync_model_prs": {"model_id": "m1"}}))
    assert out["checked"] == 2 and out["merged"] == [{"model_id": "m1", "version": 3}] and out["closed"] == [{"model_id": "m1", "version": 2}]
    assert out["errors"][0]["version"] == 1
    sql, params = s.find("FROM GOVERNANCE.MODEL_DESIGNS D JOIN CODE.REPO")[0]
    assert "DATEADD('second', -60" in sql and params == ["m1"]
    merged = [c for c in s.find("UPDATE GOVERNANCE.MODEL_DESIGNS SET PR_STATE = ?") if c[1][0] == "MERGED"][0]
    assert "STATUS = 'PUBLISHED'" in merged[0] and merged[1][1] == "sha7"
    closed = [c for c in s.find("UPDATE GOVERNANCE.MODEL_DESIGNS SET PR_STATE = ?") if c[1][0] == "CLOSED"][0]
    assert "STATUS = 'PUBLISHED'" not in closed[0]
    forced = Session()
    publish.sync_model_prs(forced, {"force": True})
    assert "DATEADD" not in forced.calls[0][0]


def test_sync_task_sql():
    stmts = publish.sync_task_sql("AIP", "COMPUTE_WH")
    assert "SCHEDULE = '30 MINUTE'" in stmts[0] and '{"sync_git_prs": {}}' in stmts[0] and stmts[1].endswith("RESUME")
    with pytest.raises(AssertionError):
        publish.sync_task_sql("AIP;DROP", "WH")


# ---------------------------------------------------------------- run proposals


def run_design(extra_column=False, errors=False):
    cols = [{"name": "CUSTOMER_ID", "datatype": "NUMBER(38,0)", "nullable": False, "is_pk": True, "source_columns": ["CRM.ID"]},
            {"name": "EMAIL", "datatype": "VARCHAR(200)", "source_columns": ["CRM.EMAIL"]},
            {"name": "COUNTRY", "datatype": "VARCHAR(2)", "source_columns": ["CRM.COUNTRY"]}]
    if extra_column:
        cols.append({"name": "LOYALTY_TIER", "datatype": "VARCHAR(20)", "source_columns": ["CRM.TIER"]})
    design = {"decision": "EXTEND_EXISTING", "primary_entity": "DIM_CUSTOMER", "target_database": "SILVER", "target_schema": "SALES",
              "entities": [{"entity_name": "DIM_CUSTOMER", "kind": "DIMENSION", "purpose": "Customers", "grain": "one row per customer",
                            "business_keys": ["CUSTOMER_ID"], "attributes": cols}]}
    issues = [{"severity": "ERROR", "message": "bad type"}] if errors else []
    return {"DESIGN_JSON": json.dumps(design), "ISSUES": json.dumps(issues)}


def approved_customer_spec():
    """The approved model with DIM_CUSTOMER exactly as the run designs it (same columns and lineage)."""
    spec = spec_fixture()
    proposed = run_link.importers.merge(spec, run_link.run_entities(json.loads(run_design()["DESIGN_JSON"])), "m1")
    return proposed


def run_session(design, model=True, approved_spec=None, extra=None):
    answers = {
        "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?": [{"RUN_ID": "r1", "RUN_NAME": "CRM", "DOMAIN_ID": "d1"}],
        "FROM MODELING.MODEL_DESIGN WHERE RUN_ID = ? AND VERSION = ?": [design],
        "FROM GOVERNANCE.DOMAIN_MODEL WHERE DOMAIN_ID = ?": [{"MODEL_ID": "m1", "APPROVED_VERSION": 4, "DOMAIN_NAME": "SALES"}] if model else [],
        "SELECT SPEC FROM GOVERNANCE.MODEL_DESIGNS": [{"SPEC": json.dumps(approved_spec or approved_customer_spec())}],
    }
    answers.update(extra or {})
    return Session(answers)


def test_a_design_the_model_already_has_pins_the_run():
    out = run_link.propose(run_session(run_design()), "r1", 2, "ana")
    assert out == {"model_id": "m1", "pinned": True, "version": 4, "entity": "DIM_CUSTOMER", "target_model": "SILVER.SALES.DIM_CUSTOMER"}


def test_a_design_with_changes_becomes_a_proposal():
    s = run_session(run_design(extra_column=True))
    out = run_link.propose(s, "r1", 2, "ana")
    assert out["status"] == "PROPOSED" and out["counts"]["ADDITIVE"] == 1 and not out["breaking"]
    assert s.find("SET STATUS = 'WITHDRAWN'")[0][1] == ["ANA", "r1"]  # an older open proposal of the run is replaced
    insert = s.find("INSERT INTO GOVERNANCE.MODEL_CHANGE")[0][1]
    entities = json.loads(insert[6])
    assert insert[1] == "m1" and str(insert[2]) == "4" and insert[5] == "DIM_CUSTOMER" and "PROPOSED" in insert
    assert [e["entity_name"] for e in entities] == ["DIM_CUSTOMER"]
    assert any(a["name"] == "LOYALTY_TIER" for a in entities[0]["attributes"])


def test_proposal_rules():
    with pytest.raises(AssertionError, match="fix 1 problem"):
        run_link.propose(run_session(run_design(errors=True)), "r1", 2, "ana")
    s = run_session(run_design(), extra={"SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?": [{"RUN_ID": "r1", "DOMAIN_ID": None}]})
    with pytest.raises(AssertionError, match="identify the run's domain"):
        run_link.propose(s, "r1", 2, "ana")


def test_a_domain_with_nothing_registered_starts_its_model_from_the_run(monkeypatch):
    made = {}
    monkeypatch.setattr(dm, "baseline", lambda s, d, u: None)
    monkeypatch.setattr(dm, "create_model", lambda s, d, payload, u: made.update(payload=json.loads(payload)) or {"model_id": "m9", "version": 1})
    s = run_session(run_design(), model=False, extra={"SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY": [{"DOMAIN_NAME": "SALES"}]})
    out = run_link.propose(s, "r1", 2, "ana")
    assert out["merged_into"] == 1 and out["status"] == "MERGED" and made["payload"]["origin"] == "RUN"
    assert made["payload"]["spec"]["design"]["entities"][0]["entity_name"] == "DIM_CUSTOMER"


def test_merge_change_opens_a_draft_and_merges(monkeypatch):
    calls = {}
    detail = {"open": None}

    def get_model(session, mid, version=None):
        return dict(detail)

    def open_draft(session, mid, user, from_version=None, note=""):
        detail["open"] = {"version": 5, "status": "DRAFT", "etag": "e5"}
        return {"version": 5}

    monkeypatch.setattr(dm, "get_model", get_model)
    monkeypatch.setattr(dm, "open_draft", open_draft)
    monkeypatch.setattr(dm, "_spec", lambda s, m, v: spec_fixture())
    monkeypatch.setattr(dm, "save_draft", lambda s, m, payload, u: calls.update(payload=json.loads(payload)) or {"etag": "e6", "counts": {"ADDITIVE": 1}})
    entity = spec_v2._entity({"entity_name": "DIM_CHANNEL", "attributes": [{"name": "CHANNEL_ID", "datatype": "INT"}]}, "m1")
    s = Session({"FROM GOVERNANCE.MODEL_CHANGE WHERE CHANGE_ID = ? AND MODEL_ID = ?": [{"STATUS": "PROPOSED", "PROPOSED_ENTITIES": json.dumps([entity])}]})
    out = run_link.merge_change(s, "m1", "c1", "ana")
    assert out == {"change_id": "c1", "status": "MERGED", "version": 5, "etag": "e6", "counts": {"ADDITIVE": 1}}
    assert calls["payload"]["etag"] == "e5" and "DIM_CHANNEL" in [e["entity_name"] for e in calls["payload"]["spec"]["design"]["entities"]]
    assert s.find("SET STATUS = 'MERGED', MERGED_INTO_VERSION = ?")[0][1] == [5, "ANA", "c1"]
    detail["open"] = {"version": 5, "status": "IN_REVIEW", "etag": "e5"}
    with pytest.raises(AssertionError, match="in review"):
        run_link.merge_change(s, "m1", "c1", "ana")


def test_on_approved_marks_merged_and_stale():
    s = Session({"STATUS = 'MERGED' AND MERGED_INTO_VERSION = ?": [{"CHANGE_ID": "c1", "RUN_ID": "r1", "RUN_DESIGN_VERSION": 2,
                                                                    "TARGET_ENTITY": "DIM_CUSTOMER"}]})
    out = run_link.on_approved(s, "m1", 5)
    assert out == [{"change_id": "c1", "run_id": "r1", "design_version": 2, "entity": "DIM_CUSTOMER"}]
    assert s.find("UPDATE GOVERNANCE.MODEL_CHANGE SET STATUS = 'APPROVED'")
    assert s.find("SET STATUS = 'STALE'")[0][1] == ["m1", 5]


def test_pin_run_points_the_run_at_the_approved_entity():
    s = Session({"SELECT D.SPEC, D.STATUS FROM GOVERNANCE.MODEL_DESIGNS": [{"SPEC": json.dumps(spec_fixture()), "STATUS": "APPROVED"}]})
    out = run_link.pin_run(s, "r1", "m1", 4, "dim_customer", 2)
    assert out["target_model"] == "SILVER.SALES.DIM_CUSTOMER"
    assert s.find("UPDATE CORE.WORKFLOW_RUN SET MODEL_ID = ?")[0][1] == ["m1", 4, "DIM_CUSTOMER", "SILVER.SALES.DIM_CUSTOMER", "r1"]
    assert s.find("UPDATE MODELING.MODEL_DESIGN SET STATUS = 'APPROVED'")[0][1] == ["r1", 2]
    with pytest.raises(AssertionError, match="not an active entity"):
        run_link.pin_run(s, "r1", "m1", 4, "LEGACY_TABLE")
    draft = Session({"SELECT D.SPEC, D.STATUS FROM GOVERNANCE.MODEL_DESIGNS": [{"SPEC": "{}", "STATUS": "DRAFT"}]})
    with pytest.raises(AssertionError, match="only use an approved"):
        run_link.pin_run(draft, "r1", "m1", 4, "DIM_CUSTOMER")


# ---------------------------------------------------------------- API


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    import app.governance as gov
    import app.main as main

    async def open_door(request, call_next):
        return await call_next(request)

    class Db:
        user = "ana"

        def __init__(self):
            self.calls = []

        def query(self, sql, params=()):
            if "SELECT CURRENT_STATE FROM CORE.WORKFLOW_RUN" in sql:
                return [{"current_state": "MAPPING_REVIEW"}]
            return []

        def call(self, sql, params=()):
            self.calls.append((sql, params))
            return {}

    db = Db()
    monkeypatch.setattr(gov, "middleware", open_door)
    monkeypatch.setattr(main, "_drop_run", lambda run_id: None)
    main.app.dependency_overrides[main.current_db] = lambda: db
    yield TestClient(main.app), main, db, monkeypatch
    main.app.dependency_overrides.clear()


def test_api_run_approve_is_closed_when_the_workbench_is_enforced(api):
    c, main, db, mp = api
    mp.setattr(main, "_workbench_enforced", lambda d: True)
    r = c.post("/api/runs/r1/model/2/approve")
    assert r.status_code == 409 and "Model Workbench" in r.json()["detail"]


def test_api_propose_pins_and_sends_mapping_back(api):
    c, main, db, mp = api
    mp.setattr(main, "invoke_source", lambda d, fn, *a: {"model_id": "m1", "pinned": True, "version": 4, "entity": "DIM_CUSTOMER"})
    mp.setattr(main, "_source_call", lambda d, proc, fn, *a: {"target_model": "SILVER.SALES.DIM_CUSTOMER", "args": a, "proc": proc})
    body = c.post("/api/runs/r1/model/2/propose").json()
    pin = body["pin"]
    assert pin["pinned"] and pin["mapping_reset"] and pin["args"] == ["r1", "m1", 4, "DIM_CUSTOMER", 2]
    assert "PIN_RUN_MODEL" in pin["proc"] and "REVIEW_TRANSITION" in db.calls[0][0]


def test_api_mapping_waits_for_a_pending_proposal(api):
    c, main, db, mp = api
    mp.setattr(main, "_workbench_enforced", lambda d: True)
    mp.setattr(main, "invoke_source", lambda d, fn, *a: {"change_id": "c1", "status": "PROPOSED"})
    r = c.post("/api/runs/r1/mapping")
    assert r.status_code == 409 and "waiting in the Model Workbench" in r.json()["detail"]


def test_api_workbench_approve_pins_merged_runs(api, monkeypatch):
    c, main, db, mp = api
    import app.model_workbench_api as wb

    mp.setattr(dm, "approve", lambda s, m, v, user, ack: {"status": "APPROVED"})
    mp.setattr(wb, "invoke_source", lambda d, fn, *a: fn(Session({"STATUS = 'MERGED' AND MERGED_INTO_VERSION = ?": [
        {"CHANGE_ID": "c1", "RUN_ID": "r7", "RUN_DESIGN_VERSION": 3, "TARGET_ENTITY": "DIM_CUSTOMER"}]}), *a))
    mp.setattr(wb, "_source_call", lambda d, proc, fn, *a: {"registered": []})
    mp.setattr(main, "pin_run_model", lambda d, run, m, v, e, dv: {"run_id": run, "pinned": True, "entity": e, "design_version": dv})
    body = c.post("/api/hub/domain-models/m1/versions/5/approve", json={}).json()
    assert body["runs"] == [{"run_id": "r7", "pinned": True, "entity": "DIM_CUSTOMER", "design_version": 3}]
