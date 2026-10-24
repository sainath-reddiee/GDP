"""Home search by meaning (services/search/resources.py, apps/api/app/search_api.py): synonyms and stems in the keyword
half, the intent a query carries, reciprocal rank fusion with Cortex Search, and the API's fallback, filters and
domain visibility with a fake database."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from services.search import resources as rs  # noqa: E402


def doc(kind, rid, title, **over):
    return {"key": rs.key(kind, rid), "kind": kind, "id": rid, "title": title, **over}


DOCS = [
    doc("DOMAIN", "d1", "CUSTOMER", subtitle="Customers and their contact points"),
    doc("DOMAIN", "d2", "PROPERTY", subtitle="Buildings, sites and their addresses"),
    doc("RUN", "r1", "YARDI property load", subtitle="FAILED", status="FAILED", domain_id="d2"),
    doc("WORKFLOW", "w1", "Opportunity from Salesforce", status="OPEN", waiting_on="MAPPING.APPROVE"),
    doc("TABLE", "t1", "PROPERTY_ADDRESS", subtitle="PROPERTY · PROPERTY", domain_id="d2"),
]


@pytest.mark.parametrize("query,expected", [
    ("client", "DOMAIN:d1"),                     # a synonym finds the CUSTOMER domain
    ("buildings", "DOMAIN:d2"),                  # a stem and a synonym: building is property
    ("loading yardi", "RUN:r1"),                 # stems: loading is load
    ("opportunity salesforce", "WORKFLOW:w1"),   # the exact name ranks first
    ("addr", "TABLE:t1"),                        # a prefix of a name part (PROPERTY_ADDRESS)
])
def test_keyword_half_understands_synonyms_stems_and_prefixes(query, expected):
    ranked = rs.keyword_rank(rs.parse(query)["terms"], DOCS)
    assert ranked and ranked[0][0] == expected, ranked


def test_a_query_carries_intent_and_intent_words_are_not_searched():
    assert rs.parse("waiting on me") == {"text": "waiting on me", "terms": [], "kinds": [], "mine": True, "failed": False}
    failed = rs.parse("failed yardi runs")
    assert failed["failed"] and failed["kinds"] == ["RUN"] and failed["terms"] == ["yardi"]
    tables = rs.parse("property tables")
    assert tables["kinds"] == ["TABLE"] and tables["terms"] == ["property"]
    meaning = rs.parse("what does gross area mean")
    assert meaning["kinds"] == ["KNOWLEDGE"] and meaning["terms"] == ["gross", "area"]
    assert rs.parse("my approvals")["mine"]


def test_reciprocal_rank_fusion_puts_what_both_halves_found_first():
    semantic = [{"key": "DOMAIN:d1", "kind": "DOMAIN", "id": "d1", "title": "CUSTOMER"},
                {"key": "KNOWLEDGE:k1", "kind": "KNOWLEDGE", "id": "k1", "title": "Client means customer"}]
    keyword = [("DOMAIN:d2", 3.0, ["name: property"]), ("DOMAIN:d1", 2.0, ["client ~ customer"])]
    out = rs.merge(semantic, keyword, {d["key"]: d for d in DOCS}, limit=10)
    assert [r["id"] for r in out] == ["d1", "d2", "k1"]
    assert out[0]["match"] == "both" and "similar meaning" in out[0]["why"]
    assert out[2]["href"] == "/os/knowledge?id=k1" and out[1]["href"] == "/os/knowledge/domains/d2"


def test_cortex_request_filters_kinds_and_never_asks_this_service_for_knowledge():
    assert "filter" not in rs.cortex_request("x")
    assert rs.cortex_request("x", ["RUN"])["filter"] == {"@eq": {"KIND": "RUN"}}
    assert rs.cortex_request("x", ["RUN", "KNOWLEDGE", "TABLE"])["filter"] == {"@or": [{"@eq": {"KIND": "RUN"}}, {"@eq": {"KIND": "TABLE"}}]}


def test_run_links_open_the_board_of_their_stage():
    assert rs.href("RUN", "r", state="MAPPING_REVIEW").startswith("/os/design?")
    assert rs.href("RUN", "r", state="DBT_REVIEW").startswith("/os/build?")


# ---------------------------------------------------------------- the API


class FakeDb:
    def __init__(self, cortex=None):
        self.cortex = cortex   # None: the search service is not deployed

    def query(self, sql, params=()):
        if "SEARCH_PREVIEW" in sql:
            if self.cortex is None:
                raise RuntimeError("Cortex Search Service RESOURCE_SEARCH does not exist")
            service = params[0].rsplit(".", 1)[1]
            return [{"r": json.dumps({"results": self.cortex.get(service, [])})}]
        if "FROM CORE.DOMAIN_REGISTRY" in sql:
            return [{"domain_id": "d1", "domain_name": "CUSTOMER", "description": "Customers", "owner": "x", "active_flag": True},
                    {"domain_id": "d2", "domain_name": "PROPERTY", "description": "Buildings", "owner": "x", "active_flag": True},
                    {"domain_id": "secret", "domain_name": "PAYROLL", "description": "Salaries", "owner": "x", "active_flag": True}]
        if "FROM CORE.ONBOARDING_REQUEST" in sql:
            return [{"request_id": "w1", "request_type": "SOURCE", "title": "Opportunity from Salesforce", "domain_id": "d1",
                     "status": "OPEN", "stage": "DESIGN", "waiting_on": "MAPPING.APPROVE", "waiting_label": "approve the mapping"},
                    {"request_id": "w2", "request_type": "SOURCE", "title": "Yardi leases", "domain_id": "d2",
                     "status": "OPEN", "stage": "ENABLE", "waiting_on": "SOURCE.CONNECT", "waiting_label": "land Yardi"}]
        if "FROM CORE.WORKFLOW_RUN" in sql:
            return [{"run_id": "r1", "run_name": "YARDI property", "domain_id": "d2", "current_state": "FAILED",
                     "failure_reason": "landing timed out"},
                    {"run_id": "r2", "run_name": "SALESFORCE accounts", "domain_id": "d1", "current_state": "STTM_REVIEW"},
                    {"run_id": "r3", "run_name": "PAYROLL run", "domain_id": "secret", "current_state": "FAILED"}]
        return []


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.governance as gov
    import app.main as main
    import app.search_api as api

    async def open_door(request, call_next):
        return await call_next(request)
    monkeypatch.setattr(gov, "middleware", open_door)
    who = {"user": "ANA", "roles": set(), "privileges": {"STTM.APPROVE", "RUN.OPERATE"}}
    monkeypatch.setattr(api, "_who", lambda db: who)
    monkeypatch.setattr(api.domain_access, "can_see", lambda db, user, did, privs=(), roles=(): did != "secret")
    state = {"db": FakeDb()}
    main.app.dependency_overrides[main.current_db] = lambda: state["db"]
    try:
        yield TestClient(main.app), state
    finally:
        main.app.dependency_overrides.pop(main.current_db, None)


def test_without_the_search_service_the_keyword_half_answers(client):
    c, _ = client
    out = c.get("/api/search", params={"q": "client"}).json()
    assert out["mode"] == "keyword" and out["results"][0]["title"] == "CUSTOMER"
    assert all(r["domain_name"] != "PAYROLL" for r in out["results"])


def test_hybrid_fuses_cortex_hits_and_keeps_only_visible_ones(client):
    c, state = client
    state["db"] = FakeDb(cortex={
        "RESOURCE_SEARCH": [{"RESOURCE_ID": "secret", "KIND": "DOMAIN", "TITLE": "PAYROLL"},
                            {"RESOURCE_ID": "r1", "KIND": "RUN", "TITLE": "YARDI property", "STATUS": "FAILED"}],
        "KNOWLEDGE_SEARCH": [{"KNOWLEDGE_ID": "k1", "TITLE": "Lease", "CONTENT": "A rental agreement", "KNOWLEDGE_TYPE": "GLOSSARY"}]})
    out = c.get("/api/search", params={"q": "why did the building load break"}).json()
    assert out["mode"] == "hybrid"
    ids = [r["id"] for r in out["results"]]
    assert ids[0] == "r1" and "secret" not in ids and "r3" not in ids   # failed intent keeps runs, visibility drops PAYROLL


def test_waiting_on_me_lists_my_gates_and_failed_runs_i_operate(client):
    c, _ = client
    out = c.get("/api/search", params={"q": "waiting on me"}).json()
    assert out["mode"] == "filter"
    assert {(r["kind"], r["id"]) for r in out["results"]} == {("RUN", "r1"), ("RUN", "r2")}


def test_topic_words_are_not_mistaken_for_intent():
    assert not rs.parse("error handling rule")["failed"] and rs.parse("error handling rule")["terms"] == ["error", "handling", "rule"]
    assert not rs.parse("how to do a lease mapping")["mine"]
    assert rs.href("RUN", "r", state="LANDING_RUNNING") == "/os/enable"
    assert rs.href("RUN", "r", state="LANDING_COMPLETE").startswith("/os/design?")
