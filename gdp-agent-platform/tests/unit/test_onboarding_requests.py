"""Onboarding requests (V075): where a request stands and who acts next (services/onboarding/requests.py), and the
API's validation, permissions, duplicate and link checks (apps/api/app/requests_api.py) with a fake database."""

import sys
from pathlib import Path

import pytest

from services.governance.policy import privilege_for
from services.onboarding import requests as rq

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

DOMAIN = {"domain_id": "d1", "domain_name": "OPPORTUNITY", "active_flag": True, "approved_version": 1}
HEALTHY = {"source_system_id": "s1", "source_system_name": "SALESFORCE", "health": "HEALTHY", "health_detail": ""}


def req(**over):
    return {"request_id": "r1", "request_type": "SOURCE", "domain_id": "d1", "status": "OPEN", "stage": "NEW", **over}


def item(**over):
    return {"item_id": "i1", "request_id": "r1", "source_system_id": "s1", "source_name": "SALESFORCE", "run_id": None, **over}


def run(state, **over):
    return {"run_id": "run1", "run_name": "SALESFORCE opportunity", "domain_id": "d1", "source_system_id": "s1",
            "current_state": state, "parent_run_id": None, **over}


def assess(items, sources=None, runs=None, domain=DOMAIN, children=None, **r):
    return rq.assess(req(**r), items, domain, {"s1": HEALTHY} if sources is None else sources, runs or {}, children or {})


# ---------------------------------------------------------------- a source on its way through the platform


def test_a_source_not_registered_waits_on_the_administrator():
    a = assess([item(source_system_id=None, source_name="Yardi")])
    assert a["stage"] == "ENABLE" and a["waiting_on"] == "SOURCE.CONNECT" and a["items"]["i1"]["status"] == "MISSING"
    assert "register and connect Yardi" in a["waiting_label"]


def test_an_unreachable_or_unlanded_source_stays_with_the_administrator():
    down = assess([item()], sources={"s1": {**HEALTHY, "health": "UNREACHABLE", "health_detail": "SQL access denied"}})
    assert down["stage"] == "ENABLE" and down["items"]["i1"]["status"] == "UNREACHABLE" and "SQL access denied" in down["items"]["i1"]["detail"]
    file = assess([item()], sources={"s1": {**HEALTHY, "health": "NOT_LANDED"}})
    assert file["stage"] == "ENABLE" and file["waiting_on"] == "SOURCE.CONNECT" and file["items"]["i1"]["status"] == "NOT_LANDED"


def test_a_source_that_is_no_longer_registered_needs_attention():
    a = assess([item()], sources={})
    assert a["stage"] == "ENABLE" and a["items"]["i1"]["status"] == "ATTENTION" and "no longer registered" in a["attention"]


def test_a_ready_source_without_a_run_waits_on_the_administrator_to_start_it():
    # starting the run that onboards a source into the domain is part of onboarding: the administrator's
    a = assess([item()])
    assert a["stage"] == "ENABLE" and a["waiting_on"] == "SOURCE.CONNECT" and a["items"]["i1"]["status"] == "NO_RUN"
    assert "start the run that onboards SALESFORCE" in a["waiting_label"]
    # a data product's Gold run stays with the Forward Engineering Lead
    product = assess([item(source_system_id=None, source_name="Gold run: pipeline")], request_type="PRODUCT")
    assert product["stage"] == "BUILD" and product["waiting_on"] == "RUN.CREATE"


@pytest.mark.parametrize("state,stage,privilege", [
    ("SOURCE_REGISTERED", "ENABLE", "SOURCE.CONNECT"),
    ("LANDING_RUNNING", "ENABLE", "SOURCE.CONNECT"),
    ("PROFILING_RUNNING", "DESIGN", "PROFILE.RUN"),
    ("DOMAIN_IDENTIFIED", "DESIGN", "MAPPING.DECIDE"),
    ("MAPPING_REVIEW", "DESIGN", "MAPPING.APPROVE"),
    ("STTM_REVIEW", "DESIGN", "STTM.APPROVE"),
    # the design ends at the approved transformations: the Forward Engineering Lead builds end to end from there
    ("STTM_APPROVED", "BUILD", "RUN.OPERATE"),
    ("SODA_REVIEW", "BUILD", "RUN.OPERATE"),
    ("SODA_APPROVED", "BUILD", "RUN.OPERATE"),
    ("VALIDATION_RUNNING", "BUILD", "RUN.OPERATE"),
    ("DBT_REVIEW", "REVIEW", "REVIEW.APPROVE"),
])
def test_a_linked_run_decides_the_stage_and_who_acts(state, stage, privilege):
    a = assess([item(run_id="run1")], runs={"run1": run(state)})
    assert (a["stage"], a["waiting_on"]) == (stage, privilege)


def test_every_workflow_state_is_placed():
    # a state the graph adds later must be placed here too, or a request would sit in DESIGN with a vague label
    import json
    graph = json.loads((Path(__file__).resolve().parents[2] / "snowflake/database/seed/workflow_graph.json").read_text())
    states = [s if isinstance(s, str) else s.get("state") for s in graph["states"]]
    unplaced = [s for s in states if s not in rq.RUN_STATES and s not in rq.DONE_STATES and s not in ("FAILED", "CANCELLED")]
    assert unplaced == []


def test_a_completed_run_finishes_the_request():
    for state in ("COMPLETED", "DBT_APPROVED"):
        a = assess([item(run_id="run1")], runs={"run1": run(state)})
        assert a["stage"] == "DONE" and a["status"] == "DONE"


def test_a_failed_run_needs_attention_at_the_step_it_failed():
    a = assess([item(run_id="run1")], runs={"run1": run("FAILED", failed_from_state="MAPPING_PENDING", failure_reason="Cortex timed out")})
    assert a["stage"] == "DESIGN" and a["items"]["i1"]["status"] == "ATTENTION" and "Cortex timed out" in a["attention"]
    assert a["waiting_on"] == "RUN.OPERATE"


def test_a_failed_validation_is_build_with_attention():
    a = assess([item(run_id="run1")], runs={"run1": run("VALIDATION_FAILED")})
    assert a["stage"] == "BUILD" and a["attention"] and a["items"]["i1"]["status"] == "ATTENTION"


def test_a_cancelled_archived_or_missing_run_asks_for_another():
    for r in (run("CANCELLED"), run("DBT_REVIEW", is_archived=True)):
        a = assess([item(run_id="run1")], runs={"run1": r})
        assert a["items"]["i1"]["status"] == "ATTENTION" and a["waiting_on"] == "SOURCE.CONNECT"
        assert "start a new run for SALESFORCE, or link another" in a["waiting_label"]
    gone = assess([item(run_id="run1")], runs={})
    assert gone["items"]["i1"]["status"] == "ATTENTION" and "no longer exists" in gone["attention"]


def test_a_run_of_another_domain_is_flagged():
    a = assess([item(run_id="run1")], runs={"run1": run("DBT_REVIEW", domain_id="d2")})
    assert a["items"]["i1"]["status"] == "ATTENTION" and "another domain" in a["attention"]


def test_a_multi_table_run_stands_where_its_slowest_table_run_stands():
    kids = {"run1": [run("COMPLETED", run_id="c1", parent_run_id="run1"), run("STTM_REVIEW", run_id="c2", parent_run_id="run1"),
                     run("CANCELLED", run_id="c3", parent_run_id="run1")]}
    a = assess([item(run_id="run1")], runs={"run1": run("MAPPING_APPROVED")}, children=kids)
    assert a["stage"] == "DESIGN" and a["waiting_on"] == "STTM.APPROVE" and "1 of 2 table runs done" in a["items"]["i1"]["detail"]
    done = {"run1": [run("COMPLETED", run_id="c1", parent_run_id="run1"), run("DBT_APPROVED", run_id="c2", parent_run_id="run1")]}
    assert assess([item(run_id="run1")], runs={"run1": run("MAPPING_APPROVED")}, children=done)["stage"] == "DONE"


# ---------------------------------------------------------------- the request as a whole


def test_the_request_is_at_its_earliest_part_and_every_role_with_work_is_listed():
    items = [item(), item(item_id="i2", source_system_id=None, source_name="Yardi")]
    a = assess(items)
    assert a["stage"] == "ENABLE" and a["waiting_on"] == "SOURCE.CONNECT"
    # both sources are with the administrator: one to register, one whose run is to start
    assert {w["privilege"] for w in a["waits"]} == {"SOURCE.CONNECT"} and len(a["waits"]) == 2


def test_a_new_domain_waits_on_a_steward_and_a_retired_one_needs_attention():
    new = rq.assess(req(request_type="DOMAIN", domain_id=None, proposed_domain="TENANT"), [], None, {}, {})
    # onboarding a domain is the Data OS Administrator's
    assert new["stage"] == "DOMAIN" and new["waiting_on"] == "DOMAIN.ONBOARD" and "onboard the TENANT domain" in new["waiting_label"]
    retired = assess([item()], domain={**DOMAIN, "active_flag": False})
    assert retired["stage"] == "DOMAIN" and "retired" in retired["attention"]


def test_a_domain_without_an_approved_model_needs_the_designer():
    a = assess([item(run_id="run1")], runs={"run1": run("DBT_REVIEW")}, domain={**DOMAIN, "approved_version": None})
    assert a["stage"] == "DESIGN" and a["waiting_on"] == "MODEL.APPROVE"


def test_removed_sources_are_ignored_and_a_domain_only_request_finishes_when_the_model_is_approved():
    a = assess([item(), item(item_id="i2", removed_at="2026-10-23")], runs={})
    assert a["items"]["i2"]["status"] == "REMOVED" and a["stage"] == "ENABLE"
    assert rq.assess(req(request_type="DOMAIN"), [], DOMAIN, {}, {})["status"] == "DONE"


def test_an_enhance_domain_request_waits_on_the_designer_until_a_newer_model_is_approved():
    r = req(request_type="DOMAIN", intent="ENHANCE", base_model_version=1)
    waiting = rq.assess(r, [], DOMAIN, {}, {})
    assert waiting["stage"] == "DESIGN" and waiting["waiting_on"] == "MODEL.EDIT" and "extend the OPPORTUNITY model" in waiting["waiting_label"]
    assert rq.assess(r, [], {**DOMAIN, "approved_version": 2}, {}, {})["status"] == "DONE"
    # a new domain request that found its domain is done once the model is approved, as before
    assert rq.assess(req(request_type="DOMAIN", base_model_version=1), [], DOMAIN, {}, {})["status"] == "DONE"
    assert rq.intent({}) == "NEW" and rq.intent({"intent": "enhance"}) == "ENHANCE"


def test_an_enhance_source_skips_the_administrator_and_waits_on_the_designer_to_start_its_run():
    a = assess([item()], intent="ENHANCE")
    assert a["stage"] == "DESIGN" and a["waiting_on"] == "RUN.CREATE" and "enhancement run for SALESFORCE" in a["waiting_label"]
    assert a["items"]["i1"]["status"] == "NO_RUN" and a["items"]["i1"]["phase"] == "IMPACT" and a["phase"] == "IMPACT"
    # a new source request with the same ready source waits on the administrator to start its run, as before
    assert assess([item()])["waiting_on"] == "SOURCE.CONNECT"


def test_an_enhance_source_not_landed_waits_on_the_administrator_to_land_the_delta():
    a = assess([item()], sources={"s1": {**HEALTHY, "health": "NOT_LANDED"}}, intent="ENHANCE")
    assert a["stage"] == "ENABLE" and a["waiting_on"] == "SOURCE.CONNECT" and "land the added tables of SALESFORCE" in a["waiting_label"]


def test_an_enhance_source_run_is_designed_as_impact_then_built_as_usual():
    a = assess([item(run_id="run1")], runs={"run1": run("MAPPING_REVIEW")}, intent="ENHANCE")
    assert a["stage"] == "DESIGN" and a["waiting_on"] == "MAPPING.APPROVE" and a["items"]["i1"]["phase"] == "IMPACT"
    b = assess([item(run_id="run1")], runs={"run1": run("DBT_REVIEW")}, intent="ENHANCE")
    assert b["stage"] == "REVIEW"


def test_an_enhance_request_links_only_a_run_started_after_it_was_filed():
    old = run("COMPLETED", created_at="2026-10-01T00:00:00")
    new = run("PROFILING_RUNNING", run_id="run2", created_at="2026-10-20T00:00:00")
    assert rq.pick_run("s1", "d1", [old])["run_id"] == "run1"
    assert rq.pick_run("s1", "d1", [old], after="2026-10-10T00:00:00") is None
    assert rq.pick_run("s1", "d1", [old, new], after="2026-10-10T00:00:00")["run_id"] == "run2"


def test_an_enhance_source_earns_no_onboarded_milestone():
    now = {"stage": "DESIGN", "status": "DESIGN"}
    assert rq.milestones({"status": "NO_RUN", "source_name": "SALESFORCE"}, now, enhance=True) == []
    built = rq.milestones({"status": "DESIGN", "source_name": "SALESFORCE"}, {"stage": "BUILD", "status": "BUILD"}, enhance=True)
    assert [k for k, _ in built] == ["DESIGN_STORED"]


def test_a_data_product_waits_on_a_gold_run():
    a = rq.assess(req(request_type="PRODUCT"), [item(source_system_id=None, source_name="Gold: OPPORTUNITY")], DOMAIN, {}, {})
    assert a["stage"] == "BUILD" and a["waiting_on"] == "RUN.CREATE" and a["items"]["i1"]["status"] == "NO_RUN"


def test_a_closed_request_waits_on_nobody():
    for status, stage in (("CANCELLED", "CLOSED"), ("REJECTED", "CLOSED"), ("DONE", "DONE")):
        a = assess([item()], status=status)
        assert (a["stage"], a["status"], a["waiting_on"], a["waits"]) == (stage, status, None, [])


# ---------------------------------------------------------------- linking what already exists


def test_a_named_source_matches_its_registration_ignoring_case_and_spaces():
    sources = [{"source_system_id": "s1", "source_system_name": "ZOOMINFO_DEV"}, {"source_system_id": "s2", "source_system_name": "OLD", "active_flag": False}]
    assert rq.match_source("zoominfo_dev ", sources)["source_system_id"] == "s1"
    assert rq.match_source("old", sources) is None and rq.match_source("Yardi", sources) is None
    # the name the wizard pre-fills for a request (YARDI_VOYAGER for "Yardi Voyager") matches back
    assert rq.match_source("Yardi Voyager", [{"source_system_id": "y", "source_system_name": "YARDI_VOYAGER"}])["source_system_id"] == "y"
    assert rq.match_source(" _- ", sources) is None


def test_the_existing_run_is_picked_only_when_it_fits():
    runs = [run("MAPPING_REVIEW", run_id="a", created_at="2026-10-01"), run("DBT_REVIEW", run_id="b", created_at="2026-09-01"),
            run("COMPLETED", run_id="kid", parent_run_id="b"), run("COMPLETED", run_id="arch", is_archived=True),
            run("COMPLETED", run_id="other", domain_id="d2"), run("CANCELLED", run_id="c"), run("FAILED", run_id="f")]
    assert rq.pick_run("s1", "d1", runs)["run_id"] == "b"     # most advanced top-level run of the domain
    assert rq.pick_run("s9", "d1", runs) is None


# ---------------------------------------------------------------- validation and governance routes


@pytest.mark.parametrize("body,needle", [
    ({"request_type": "NOPE", "title": "x", "domain_id": "d1"}, "Choose what is requested"),
    ({"request_type": "SOURCE", "title": " ", "domain_id": "d1", "sources": [{"source_name": "A"}]}, "title"),
    ({"request_type": "SOURCE", "title": "x", "domain_id": "d1", "sources": []}, "at least one source"),
    ({"request_type": "SOURCE", "title": "x", "domain_id": "d1", "sources": [{"source_name": "A"}, {"source_name": "a"}]}, "named once"),
    ({"request_type": "SOURCE", "title": "x", "sources": [{"source_name": "A"}]}, "Pick the domain"),
    ({"request_type": "DOMAIN", "title": "x"}, "Name the new domain"),
    ({"request_type": "DOMAIN", "title": "x", "domain_id": "d1"}, "A domain request is for a new domain"),
    ({"request_type": "SOURCE", "title": "x", "domain_id": "d1", "priority": "URGENT", "sources": [{"source_name": "A"}]}, "Priority"),
    ({"request_type": "SOURCE", "intent": "REDO", "title": "x", "domain_id": "d1", "sources": [{"source_name": "A"}]}, "new or enhance"),
    ({"request_type": "SOURCE", "intent": "ENHANCE", "title": "x", "domain_id": "d1", "sources": []}, "Pick the source to enhance"),
    ({"request_type": "DOMAIN", "intent": "ENHANCE", "title": "x", "business_outcome": "Add leases"}, "Pick the domain to enhance"),
    ({"request_type": "DOMAIN", "intent": "ENHANCE", "title": "x", "domain_id": "d1"}, "Say what the domain needs"),
])
def test_validation_says_what_is_wrong(body, needle):
    assert any(needle in p for p in rq.validate(body, domain_exists=True))


def test_an_enhance_domain_request_on_an_existing_domain_is_valid():
    assert rq.validate({"request_type": "DOMAIN", "intent": "ENHANCE", "title": "x", "domain_id": "d1", "business_outcome": "Add leases"}, True) == []


def test_a_retired_domain_is_refused():
    assert any("retired" in p for p in rq.validate({"request_type": "SOURCE", "title": "x", "domain_id": "d1", "sources": [{"source_name": "A"}]}, False))
    assert rq.validate({"request_type": "SOURCE", "title": "x", "domain_id": "d1", "sources": [{"source_name": "A"}]}, True) == []


def test_request_writes_are_checked_by_the_endpoint_and_nothing_else_slips_through():
    for path in ("/api/onboarding-requests", "/api/onboarding-requests/r1/sources", "/api/onboarding-requests/r1/sources/i1/run",
                 "/api/onboarding-requests/r1/sources/i1/source", "/api/onboarding-requests/r1/sources/i1/remove",
                 "/api/onboarding-requests/r1/domain", "/api/onboarding-requests/r1/reject", "/api/onboarding-requests/r1/reopen",
                 "/api/onboarding-requests/r1/sources/i1/enhance-run", "/api/onboarding-requests/r1/files",
                 "/api/onboarding-requests/r1/files/f1/remove"):
        assert privilege_for("POST", path)[:1] == (None,), path
    assert privilege_for("POST", "/api/onboarding-requests/r1/delete")[0] == "RUN.OPERATE"


# ---------------------------------------------------------------- the API, with a fake database


class FakeDb:
    user = "ANA"

    def __init__(self):
        self.handlers, self.writes = [], []

    def on(self, marker, value):
        self.handlers.insert(0, (marker, value))
        return self

    def query(self, sql, params=()):
        for marker, value in self.handlers:
            if marker in sql:
                return [dict(r) for r in (value(sql, params) if callable(value) else value)]
        return []

    def execute(self, sql, params=()):
        self.writes.append((" ".join(sql.split()), params))

    def wrote(self, needle):
        return [p for s, p in self.writes if needle in s]


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    import app.governance as gov
    import app.main as main
    import app.requests_api as api_mod

    async def open_door(request, call_next):
        return await call_next(request)
    monkeypatch.setattr(gov, "middleware", open_door)
    who = {"user": "ANA", "roles": set(), "privileges": {"DOMAIN.EDIT"}}
    monkeypatch.setattr(api_mod, "_who", lambda db: who)
    monkeypatch.setattr(api_mod, "_sources", lambda db: {"s1": HEALTHY})
    monkeypatch.setattr(api_mod.domain_access, "can_see", lambda db, user, domain_id, privs=(), roles=(): domain_id != "secret")
    monkeypatch.setattr(api_mod.domain_access, "named_stewards", lambda db, domain_id: ["OWNER1"] if domain_id == "d1" else [])
    db = FakeDb().on("FROM CORE.DOMAIN_REGISTRY D", [DOMAIN])
    main.app.dependency_overrides[main.current_db] = lambda: db
    try:
        yield TestClient(main.app), db, who
    finally:
        main.app.dependency_overrides.pop(main.current_db, None)


NEW = {"request_type": "SOURCE", "title": "Opportunity from Salesforce", "domain_id": "d1", "sources": [{"source_name": "SALESFORCE"}]}


def test_filing_needs_ownership_or_a_builder_privilege(api):
    client, db, who = api
    who["privileges"] = {"AUDIT.VIEW", "OPS.VIEW"}          # a viewer, not an owner of the domain
    res = client.post("/api/onboarding-requests", json=NEW)
    assert res.status_code == 403 and "owner or steward" in res.json()["detail"]
    assert not db.wrote("INSERT INTO CORE.ONBOARDING_REQUEST")
    who["user"] = "OWNER1"                                   # the domain's owner may file without a privilege
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(requested_by="OWNER1")])
    assert client.post("/api/onboarding-requests", json=NEW).status_code == 200
    assert db.wrote("INSERT INTO CORE.ONBOARDING_REQUEST (")


def test_filing_validates_and_hides_domains_the_caller_cannot_see(api):
    client, db, _ = api
    assert client.post("/api/onboarding-requests", json={**NEW, "sources": []}).status_code == 422
    db.on("FROM CORE.DOMAIN_REGISTRY D", [{**DOMAIN, "domain_id": "secret"}])
    assert client.post("/api/onboarding-requests", json={**NEW, "domain_id": "secret"}).status_code == 404


def test_a_request_for_a_new_domain_that_already_exists_is_refused(api):
    client, db, _ = api
    db.on("UPPER(DOMAIN_NAME) = UPPER", [{"domain_id": "d1", "domain_name": "OPPORTUNITY"}])
    res = client.post("/api/onboarding-requests", json={"request_type": "DOMAIN", "title": "x", "proposed_domain": "opportunity"})
    assert res.status_code == 422 and "already exists" in res.json()["detail"]


def test_a_duplicate_open_request_is_reported_unless_allowed(api):
    client, db, _ = api
    db.on("SELECT DISTINCT R.REQUEST_ID", [{"request_id": "r0", "title": "Earlier", "stage": "ENABLE", "requested_by": "BO", "source_name": "SALESFORCE"}])
    res = client.post("/api/onboarding-requests", json=NEW)
    assert res.status_code == 409 and res.json()["detail"]["duplicates"][0]["request_id"] == "r0"
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req()])
    assert client.post("/api/onboarding-requests", json={**NEW, "allow_duplicate": True}).status_code == 200


def test_a_run_is_linked_only_when_it_fits(api):
    client, db, who = api
    who["privileges"] = {"RUN.CREATE"}
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(requested_by="BO")])
    db.on("WHERE ITEM_ID = %s AND REQUEST_ID", [item()])
    url = "/api/onboarding-requests/r1/sources/i1/run"
    for bad, needle in ((run("DBT_REVIEW", parent_run_id="p"), "multi-table"), (run("DBT_REVIEW", source_system_id="s2"), "another source"),
                        (run("DBT_REVIEW", domain_id="d2"), "another domain")):
        db.on("WHERE RUN_ID = %s AND DELETED_AT IS NULL", [bad])
        res = client.post(url, json={"run_id": "run1"})
        assert res.status_code == 422 and needle in res.json()["detail"]
    db.on("WHERE RUN_ID = %s AND DELETED_AT IS NULL", [run("DBT_REVIEW")])
    assert client.post(url, json={"run_id": "run1"}).status_code == 200 and db.wrote("SET RUN_ID = %s")
    who["privileges"] = {"SOURCE.CONNECT"}
    assert client.post(url, json={"run_id": "run1"}).status_code == 403


def test_only_the_administrator_links_a_registration(api):
    client, db, who = api
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(requested_by="BO")])
    db.on("WHERE ITEM_ID = %s AND REQUEST_ID", [item(source_system_id=None, source_name="Salesforce prod")])
    url = "/api/onboarding-requests/r1/sources/i1/source"
    assert client.post(url, json={"source_system_id": "s1"}).status_code == 403
    who["privileges"] = {"SOURCE.CONNECT"}
    assert client.post(url, json={"source_system_id": "nope"}).status_code == 422
    assert client.post(url, json={"source_system_id": "s1"}).status_code == 200 and db.wrote("SET SOURCE_SYSTEM_ID = %s")


def test_reject_needs_a_reason_and_not_your_own_request_and_a_closed_request_is_read_only(api):
    client, db, who = api
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(requested_by="ANA")])
    assert client.post("/api/onboarding-requests/r1/reject", json={"note": ""}).status_code == 422
    assert client.post("/api/onboarding-requests/r1/reject", json={"note": "not allowed"}).status_code == 403
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(requested_by="ANA", status="CANCELLED")])
    assert client.post("/api/onboarding-requests/r1/sources", json={"source_name": "X"}).status_code == 409
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(requested_by="ANA", status="DONE")])
    assert client.post("/api/onboarding-requests/r1/reopen", json={}).status_code == 409


def test_the_last_source_of_a_source_request_cannot_be_removed(api):
    client, db, _ = api
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(requested_by="ANA")])
    db.on("WHERE ITEM_ID = %s AND REQUEST_ID", [item()])
    db.on("FROM CORE.ONBOARDING_REQUEST_ITEM WHERE REQUEST_ID IN", [item()])
    res = client.post("/api/onboarding-requests/r1/sources/i1/remove")
    assert res.status_code == 409 and "cancel the request" in res.json()["detail"]


def test_before_the_migration_the_api_says_to_deploy_it(api):
    client, db, _ = api

    def missing(sql, params):
        raise RuntimeError("SQL compilation error: Object 'DEV_AI_PLATFORM.CORE.ONBOARDING_REQUEST' does not exist or not authorized.")
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", missing)
    res = client.get("/api/onboarding-requests")
    assert res.status_code == 503 and "V075" in res.json()["detail"]


def test_an_enhance_source_needs_a_registered_source(api):
    client, db, _ = api
    res = client.post("/api/onboarding-requests", json={**NEW, "intent": "ENHANCE", "sources": [{"source_name": "Yardi"}]})
    assert res.status_code == 422 and "not registered" in res.json()["detail"]
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(intent="ENHANCE")])
    assert client.post("/api/onboarding-requests", json={**NEW, "intent": "ENHANCE"}).status_code == 200
    (row,) = db.wrote("INSERT INTO CORE.ONBOARDING_REQUEST (")
    assert row[-2:] == ("ENHANCE", None)


def test_an_enhance_domain_request_records_the_model_it_was_filed_against(api):
    client, db, _ = api
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(request_type="DOMAIN", intent="ENHANCE", base_model_version=1)])
    res = client.post("/api/onboarding-requests", json={"request_type": "DOMAIN", "intent": "ENHANCE", "title": "Leases",
                                                        "domain_id": "d1", "business_outcome": "Add the lease entity"})
    assert res.status_code == 200 and res.json()["intent"] == "ENHANCE" and res.json()["waiting_on"] == "MODEL.EDIT"
    (row,) = db.wrote("INSERT INTO CORE.ONBOARDING_REQUEST (")
    assert row[-2:] == ("ENHANCE", 1)


def test_before_v077_every_request_reads_as_new(api):
    client, db, _ = api

    def legacy(sql, params):
        if "INTENT" in sql:
            raise RuntimeError("SQL compilation error: invalid identifier 'INTENT'")
        return [req()]
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", legacy)
    res = client.get("/api/onboarding-requests")
    assert res.status_code == 200 and res.json()["requests"][0]["intent"] == "NEW"


def test_the_designer_starts_the_enhancement_run_of_an_enhance_source_request(api, monkeypatch):
    client, db, who = api
    import app.main as main
    made = {}

    def fake_create_run(body, db):
        made["body"] = body
        return {"run_id": "run9"}
    monkeypatch.setattr(main, "create_run", fake_create_run)
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(intent="ENHANCE", requested_by="ANA")])
    db.on("FROM CORE.ONBOARDING_REQUEST_ITEM WHERE ITEM_ID", [item(source_note="Add the campaign tables")])
    who["privileges"] = {"AUDIT.VIEW"}
    res = client.post("/api/onboarding-requests/r1/sources/i1/enhance-run")
    assert res.status_code == 403 and "RUN.CREATE" in res.json()["detail"]
    who["privileges"] = {"RUN.CREATE"}
    assert client.post("/api/onboarding-requests/r1/sources/i1/enhance-run").status_code == 200
    assert made["body"].intent["enhance"] == {"request_id": "r1", "scope": "Add the campaign tables"}
    assert made["body"].domain_id == "d1"
    assert db.wrote("UPDATE CORE.ONBOARDING_REQUEST_ITEM SET RUN_ID") and db.wrote("UPDATE CORE.ONBOARDING_REQUEST_ITEM SET RUN_ID")[0][0] == "run9"
    # only an Enhance source request has one
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(requested_by="ANA")])
    assert client.post("/api/onboarding-requests/r1/sources/i1/enhance-run").status_code == 422


def test_a_data_product_request_gets_a_gold_run_item_so_it_is_not_done_on_filing(api):
    client, db, _ = api
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(request_type="PRODUCT")])
    res = client.post("/api/onboarding-requests", json={"request_type": "PRODUCT", "title": "Pipeline forecast", "domain_id": "d1"})
    assert res.status_code == 200
    added = [p for p in db.wrote("INSERT INTO CORE.ONBOARDING_REQUEST_ITEM")]
    assert added and added[0][3] == "Gold run: Pipeline forecast" and added[0][2] is None


def test_a_run_linked_during_a_refresh_is_judged_with_its_table_runs(monkeypatch):
    import app.requests_api as api_mod

    monkeypatch.setattr(api_mod, "_sources", lambda db: {"s1": HEALTHY})
    parent = run("MAPPING_APPROVED", created_at="2026-10-23")
    kids = [run("STTM_REVIEW", run_id="c1", parent_run_id="run1"), run("COMPLETED", run_id="c2", parent_run_id="run1")]

    def runs_query(sql, params):
        if "SOURCE_SYSTEM_ID IN" in sql:
            return [parent]
        return ([parent] if "run1" in params else []) + [k for k in kids if "run1" in params]
    db = FakeDb().on("FROM CORE.WORKFLOW_RUN WHERE", runs_query).on("FROM CORE.ONBOARDING_REQUEST_ITEM WHERE REQUEST_ID IN", [item()]) \
        .on("FROM CORE.DOMAIN_REGISTRY D", [DOMAIN])
    out = api_mod.refresh(db, [req()])[0]
    assert out["items"][0]["run_id"] == "run1" and out["stage"] == "DESIGN" and out["waiting_on"] == "STTM.APPROVE"
    assert "1 of 2 table runs done" in out["items"][0]["status_detail"]


def test_a_multi_table_run_names_the_step_of_its_earliest_table_first():
    kids = {"run1": [run("DBT_REVIEW", run_id="c1", parent_run_id="run1"), run("STTM_REVIEW", run_id="c2", parent_run_id="run1")]}
    a = assess([item(run_id="run1")], runs={"run1": run("MAPPING_APPROVED")}, children=kids)
    assert a["stage"] == "DESIGN" and a["waiting_on"] == "STTM.APPROVE"
    # every role still sees its part, QA's sign-off at code review among them
    assert [w["privilege"] for w in a["waits"]] == ["STTM.APPROVE", "REVIEW.APPROVE", "QA.SIGNOFF"]


# ---------------------------------------------------------------- phases and milestones (the owner's words)


def test_phases_follow_a_source_from_the_administrator_to_done():
    assert assess([item(source_system_id=None, source_name="Yardi")])["phase"] == "ENABLE"
    # registered and landed, its run not started: still with the administrator
    assert assess([item()])["phase"] == "ENABLE"
    # the administrator started the run and it landed: onboarded, waiting for the designer
    assert assess([item(run_id="run1")], runs={"run1": run("LANDING_COMPLETE")})["phase"] == "ONBOARDED"
    assert assess([item(run_id="run1")], runs={"run1": run("PROFILING_PENDING")})["phase"] == "ONBOARDED"
    assert assess([item(run_id="run1")], runs={"run1": run("PROFILING_RUNNING")})["phase"] == "DESIGN"
    assert assess([item(run_id="run1")], runs={"run1": run("MAPPING_REVIEW")})["phase"] == "DESIGN"
    assert assess([item(run_id="run1")], runs={"run1": run("SODA_APPROVED")})["phase"] == "STORED"
    assert assess([item(run_id="run1")], runs={"run1": run("VALIDATION_RUNNING")})["phase"] == "BUILD"
    flagged = assess([item(run_id="run1")], runs={"run1": run("VALIDATION_FAILED")})
    assert flagged["phase"] == "FLAGGED" and flagged["items"]["i1"]["phase"] == "FLAGGED"
    assert assess([item(run_id="run1")], runs={"run1": run("DBT_REVIEW")})["phase"] == "REVIEW"
    assert assess([item(run_id="run1")], runs={"run1": run("DBT_APPROVED")})["phase"] == "DONE"


def test_the_request_phase_is_the_earliest_part_at_its_stage():
    a = assess([item(run_id="run1"), item(item_id="i2", source_system_id="s2", source_name="ZOOMINFO", run_id="run2")],
               sources={"s1": HEALTHY, "s2": {**HEALTHY, "source_system_id": "s2"}},
               runs={"run1": run("LANDING_COMPLETE"), "run2": run("PROFILING_RUNNING", run_id="run2", source_system_id="s2")})
    # both are in DESIGN: one onboarded and waiting for the designer, one being profiled
    assert a["stage"] == "DESIGN" and a["phase"] == "ONBOARDED"
    assert a["items"]["i2"]["phase"] == "DESIGN"


def test_phases_of_a_domain_still_to_design_a_product_and_a_closed_request():
    unapproved = {**DOMAIN, "approved_version": None}
    assert assess([], domain=unapproved)["phase"] == "DESIGN"
    # the source is onboarded while the designer still models the domain: the earlier phase shows
    assert assess([item(run_id="run1")], runs={"run1": run("LANDING_COMPLETE")}, domain=unapproved)["phase"] == "ONBOARDED"
    # a source still with the administrator holds the request at Enable
    assert assess([item()], domain=unapproved)["phase"] == "ENABLE"
    assert assess([], domain=None, domain_id=None, proposed_domain="LEASE")["phase"] == "DOMAIN"
    product = assess([item(source_system_id=None, source_name="Gold run: pipeline")], request_type="PRODUCT")
    assert product["stage"] == "BUILD" and product["phase"] == "READY"
    assert assess([item()], status="CANCELLED")["phase"] == "CLOSED"


def test_a_multi_table_run_reads_the_phase_of_its_least_advanced_table():
    kids = [run("SODA_APPROVED", run_id="c1", parent_run_id="run1"), run("STTM_REVIEW", run_id="c2", parent_run_id="run1")]
    a = assess([item(run_id="run1")], runs={"run1": run("CREATED")}, children={"run1": kids})
    assert a["stage"] == "DESIGN" and a["phase"] == "DESIGN"


def test_milestones_mark_a_source_onboarded_and_a_design_stored():
    onboarded = rq.milestones({"status": "MISSING", "source_name": "Yardi"}, {"stage": "DESIGN", "status": "NO_RUN"})
    assert [k for k, _ in onboarded] == ["SOURCE_ONBOARDED"] and "Yardi is onboarded" in onboarded[0][1]
    assert rq.milestones({"status": "NOT_LANDED"}, {"stage": "DESIGN", "status": "DESIGN"})[0][0] == "SOURCE_ONBOARDED"
    stored = rq.milestones({"status": "DESIGN", "source_name": "Yardi"}, {"stage": "BUILD", "status": "BUILD"})
    assert [k for k, _ in stored] == ["DESIGN_STORED"]
    # a source last seen with the administrator and now already building: onboarded (its design was never seen open)
    both = rq.milestones({"status": "UNREACHABLE"}, {"stage": "BUILD", "status": "BUILD"})
    assert [k for k, _ in both] == ["SOURCE_ONBOARDED"]
    # a run started for a registered source: onboarded (and, seen only once it is in review, its design stored)
    assert [k for k, _ in rq.milestones({"status": "NO_RUN"}, {"stage": "DESIGN", "status": "DESIGN"})] == ["SOURCE_ONBOARDED"]
    assert [k for k, _ in rq.milestones({"status": "NO_RUN"}, {"stage": "REVIEW", "status": "REVIEW"})] == ["SOURCE_ONBOARDED", "DESIGN_STORED"]
    # a data product's Gold run earns neither
    assert rq.milestones({"status": "NO_RUN"}, {"stage": "BUILD", "status": "BUILD"}, product=True) == []


def test_no_milestone_while_still_with_the_administrator_or_when_removed_or_first_seen():
    assert rq.milestones({"status": "MISSING"}, {"stage": "ENABLE", "status": "NOT_LANDED"}) == []
    assert rq.milestones({"status": "MISSING"}, {"stage": "DONE", "status": "REMOVED"}) == []
    assert rq.milestones({"status": None}, {"stage": "DESIGN", "status": "NO_RUN"}) == []
    assert rq.milestones({"status": "MISSING"}, {"stage": "ENABLE", "status": "NO_RUN"}) == []
    assert rq.milestones({"status": "BUILD"}, {"stage": "REVIEW", "status": "REVIEW"}) == []


# ---------------------------------------------------------------- files on a request (V079)


def _stage(monkeypatch):
    """The document stage as a dict: PUT and GET keep bytes in memory; register_upload records the domain documents."""
    import app.domain_sources_api as ds

    stage, documents = {}, []
    monkeypatch.setattr(ds, "_put", lambda db, path, data: stage.__setitem__(path, data))
    monkeypatch.setattr(ds, "_get", lambda db, path: stage[path])
    monkeypatch.setattr(ds, "_files_ready", lambda db: True)

    def register(db, domain_id, name, data):
        documents.append((domain_id, name, data))
        return {"file_name": name, "source_id": f"doc{len(documents)}"}
    monkeypatch.setattr(ds, "register_upload", register)
    return stage, documents


def test_a_domain_request_keeps_files_of_any_format_until_the_domain_exists(api, monkeypatch):
    client, db, who = api
    stage, documents = _stage(monkeypatch)
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(request_type="DOMAIN", domain_id=None, proposed_domain="LEASE",
                                                      requested_by="ANA")])
    res = client.post("/api/onboarding-requests/r1/files", files=[
        ("files", ("lease_dictionary.xlsx", b"PK\x03\x04 sheet", "application/octet-stream")),
        ("files", ("sample_domain.json", b'{"domain": {"name": "LEASE"}}', "application/json")),
        ("files", ("empty.txt", b"", "text/plain"))])
    assert res.status_code == 200, res.text
    out = res.json()["files"]
    assert [o.get("error") for o in out] == [None, None, "the file is empty"]
    assert sorted(p.split("/")[-1] for p in stage) == ["lease_dictionary.xlsx", "sample_domain.json"]
    assert all(p.startswith("requests/r1/") for p in stage)
    assert len(db.wrote("INSERT INTO CORE.ONBOARDING_REQUEST_FILE")) == 2
    assert any(p[3] == "FILE_ADDED" for p in db.wrote("INSERT INTO CORE.ONBOARDING_REQUEST_EVENT"))
    assert documents == []                                  # no domain yet: nothing handed over


def test_files_are_handed_to_the_domain_once_it_exists(api, monkeypatch):
    client, db, who = api
    stage, documents = _stage(monkeypatch)
    stage["requests/r1/f1/glossary.pdf"] = b"%PDF glossary"
    db.on("FROM CORE.ONBOARDING_REQUEST_FILE", [{"file_id": "f1", "request_id": "r1", "file_name": "glossary.pdf",
                                                 "sha256": "x", "stage_path": "requests/r1/f1/glossary.pdf"}])
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(request_type="DOMAIN", domain_id=None, proposed_domain="OPPORTUNITY",
                                                      requested_by="ANA")])
    res = client.post("/api/onboarding-requests/r1/domain", json={"domain_id": "d1"})
    assert res.status_code == 200, res.text
    assert documents == [("d1", "glossary.pdf", b"%PDF glossary")]
    assert db.wrote("UPDATE CORE.ONBOARDING_REQUEST_FILE SET DOMAIN_SOURCE_ID") == [("doc1", "f1")]
    assert any(p[3] == "FILES_HANDED_OVER" for p in db.wrote("INSERT INTO CORE.ONBOARDING_REQUEST_EVENT"))


def test_only_the_requester_or_the_domain_side_adds_files(api, monkeypatch):
    client, db, who = api
    _stage(monkeypatch)
    who["privileges"] = {"AUDIT.VIEW"}
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", [req(request_type="DOMAIN", domain_id=None, requested_by="SOMEONE")])
    res = client.post("/api/onboarding-requests/r1/files", files=[("files", ("a.csv", b"a,b", "text/csv"))])
    assert res.status_code == 403
    who["privileges"] = {"DOMAIN.ONBOARD"}                 # the Data OS Administrator may
    assert client.post("/api/onboarding-requests/r1/files", files=[("files", ("a.csv", b"a,b", "text/csv"))]).status_code == 200


def test_qa_and_dq_approvers_also_have_work_at_their_gates():
    review = assess([item(run_id="run1")], runs={"run1": run("DBT_REVIEW")})
    assert review["waiting_on"] == "REVIEW.APPROVE" and [w["privilege"] for w in review["waits"]] == ["REVIEW.APPROVE", "QA.SIGNOFF"]
    checks = assess([item(run_id="run1")], runs={"run1": run("SODA_REVIEW")})
    assert checks["waiting_on"] == "RUN.OPERATE" and "SODA.APPROVE" in [w["privilege"] for w in checks["waits"]]


def test_an_open_request_is_never_cut_by_the_limit(api):
    client, db, who = api
    old = [req(request_id=f"o{n}", status="OPEN", stage="ENABLE", updated_at=f"2026-01-{n % 28 + 1:02d}") for n in range(30)]
    closed = [req(request_id=f"c{n}", status="DONE", stage="DONE", updated_at="2026-10-01") for n in range(30)]
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", old + closed)
    out = client.get("/api/onboarding-requests", params={"status": "ALL", "limit": 10}).json()["requests"]
    ids = [r["request_id"] for r in out]
    assert all(f"o{n}" in ids for n in range(30))          # every open request, however old
    assert not any(i.startswith("c") for i in ids)         # the limit is spent on open ones first
    db.on("FROM CORE.ONBOARDING_REQUEST WHERE", closed)     # the fake ignores the status filter the query applies
    assert len(client.get("/api/onboarding-requests", params={"status": "CLOSED", "limit": 10}).json()["requests"]) == 10


def test_source_health_is_read_once_for_many_reads_and_fresh_after_a_link(monkeypatch):
    import app.main as main
    import app.requests_api as api_mod

    calls = []
    monkeypatch.setattr(main, "sources_overview", lambda db=None, fresh=False: calls.append(1) or {"sources": [HEALTHY]})
    api_mod.invalidate_sources()
    for _ in range(5):
        assert api_mod._sources(None) == {"s1": HEALTHY}
    assert len(calls) == 1                       # five Home loads, one sources overview
    api_mod.invalidate_sources()                 # a link: the next read sees the change
    api_mod._sources(None)
    assert len(calls) == 2


def test_new_domain_requests_look_their_domains_up_in_one_query():
    import app.requests_api as api_mod

    db = FakeDb().on("UPPER(DOMAIN_NAME) IN", [{"domain_id": "d9", "domain_name": "TENANT"}])
    out = api_mod._domains_named(db, ["tenant", " Lease ", "TENANT"])
    assert out == {"TENANT": {"domain_id": "d9", "domain_name": "TENANT"}}
