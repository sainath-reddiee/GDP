"""The onboarding flows end to end, persona by persona, without Snowflake.

Flow A, a new source: the Product Owner files a request -> the Data OS Administrator registers, lands and starts the
run -> the request shows "Source onboarded" -> the Data Designer profiles, models, maps and approves the STTM -> the
Forward Engineering Lead builds end to end (checks, code, validation, pull request) -> the Data Developer reviews.
Flow B, a source already ready (landed, design approved): the Forward Engineering Lead takes it straight away.
Flow C, enhance a source: the Data Designer starts the delta run, then the same design, build and review.

Every step checks four things against the real code: the request's stage and the privilege it waits on
(services/onboarding/requests.assess), that the persona doing the step holds that privilege and every endpoint privilege
its calls need (services/governance/policy, persona bundles of V076/V077), that the run's move is allowed by the
workflow graph (services/workflow/state_machine over workflow_graph.json), and what the requester's history records
(apps/api/app/requests_api._persist).
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from services.governance.policy import SYSTEM_ROLES, effective_privileges, privilege_for  # noqa: E402
from services.onboarding import requests as rq  # noqa: E402
from services.workflow import autopilot as ap  # noqa: E402
from services.workflow.graph import load_graph  # noqa: E402
from services.workflow.state_machine import RunContext, evaluate  # noqa: E402

GRAPH = load_graph(ROOT / "snowflake" / "database" / "seed" / "workflow_graph.json")

# the persona bundles as V076 seeds them, with V077's design role
PERSONAS = {
    "product_owner": ["DATA_STEWARD"],
    "admin": ["PLATFORM_ADMIN"],
    "designer": ["DATA_DESIGNER", "MODEL_APPROVER", "STTM_APPROVER", "DQ_APPROVER"],
    "forward_lead": ["DATA_ENGINEER", "SKILL_OWNER"],
    "developer": ["CODE_REVIEWER"],
    "qa": ["QA_LEAD"],
}


def grants(persona):
    roles = {k: v["privileges"] for k, v in SYSTEM_ROLES.items()}
    inherits = {k: v.get("inherits", []) for k, v in SYSTEM_ROLES.items()}
    return effective_privileges(PERSONAS[persona], roles, inherits)[1]


def holds(persona, privilege):
    g = grants(persona)
    return "*" in g or privilege in g


R = "/api/runs/run1"
REVIEW = f"{R}/review"
# the calls each step of a run makes: (method, path, body)
CALLS = {
    "profile": ("POST", f"{R}/profile", None),
    "identify_domain": ("POST", f"{R}/domain", None),
    "generate_mapping": ("POST", f"{R}/mapping", None),
    "approve_mapping": ("POST", REVIEW, {"to_state": "MAPPING_APPROVED", "decision": "APPROVE"}),
    "generate_sttm": ("POST", f"{R}/sttm", None),
    "approve_sttm": ("POST", REVIEW, {"to_state": "STTM_APPROVED", "decision": "APPROVE"}),
    "generate_soda": ("POST", f"{R}/soda", None),
    "generate_dbt": ("POST", f"{R}/dbt", None),
    "validate": ("POST", f"{R}/validation", None),
    "publish_pr": ("POST", f"{R}/dbt/publish", None),
    "approve_code": ("POST", REVIEW, {"to_state": "DBT_APPROVED", "decision": "APPROVE"}),
}


def may_call(persona, step):
    method, path, body = CALLS[step]
    privilege = privilege_for(method, path, body)[0]
    return privilege is None or holds(persona, privilege)


# The run's path through the graph and who acts there: (state, request stage, privilege waited on, persona, the steps
# that persona takes to move the run on). Build end to end approves the checks for the Forward Engineering Lead
# (AUTOPILOT_APPROVES_DQ), so SODA_REVIEW is theirs too.
NEW_SOURCE_PATH = [
    ("LANDING_COMPLETE", "DESIGN", "PROFILE.RUN", "designer", ["profile"]),
    ("PROFILING_PENDING", "DESIGN", "PROFILE.RUN", "designer", ["profile"]),
    ("PROFILING_RUNNING", "DESIGN", "PROFILE.RUN", "designer", []),
    ("PROFILING_COMPLETE", "DESIGN", "MAPPING.DECIDE", "designer", ["identify_domain"]),
    ("DOMAIN_IDENTIFIED", "DESIGN", "MAPPING.DECIDE", "designer", ["generate_mapping"]),
    ("MAPPING_PENDING", "DESIGN", "MAPPING.DECIDE", "designer", ["generate_mapping"]),
    ("MAPPING_REVIEW", "DESIGN", "MAPPING.APPROVE", "designer", ["approve_mapping"]),
    ("MAPPING_APPROVED", "DESIGN", "MAPPING.DECIDE", "designer", ["generate_sttm"]),
    ("STTM_PENDING", "DESIGN", "STTM.EDIT", "designer", ["generate_sttm"]),
    ("STTM_REVIEW", "DESIGN", "STTM.APPROVE", "designer", ["approve_sttm"]),
    ("STTM_APPROVED", "BUILD", "RUN.OPERATE", "forward_lead", ["generate_soda"]),
    ("SODA_PENDING", "BUILD", "RUN.OPERATE", "forward_lead", ["generate_soda"]),
    ("SODA_REVIEW", "BUILD", "RUN.OPERATE", "forward_lead", []),
    ("SODA_APPROVED", "BUILD", "RUN.OPERATE", "forward_lead", ["generate_dbt"]),
    ("DBT_PENDING", "BUILD", "RUN.OPERATE", "forward_lead", ["generate_dbt"]),
    ("DBT_GENERATING", "BUILD", "RUN.OPERATE", "forward_lead", []),
    ("VALIDATION_PENDING", "BUILD", "RUN.OPERATE", "forward_lead", ["validate"]),
    ("VALIDATION_RUNNING", "BUILD", "RUN.OPERATE", "forward_lead", []),
    ("VALIDATION_PASSED", "BUILD", "RUN.OPERATE", "forward_lead", ["publish_pr"]),
    ("DBT_REVIEW", "REVIEW", "REVIEW.APPROVE", "developer", ["approve_code"]),
]
HUMAN_GATES = {"MAPPING_REVIEW": "MAPPING_APPROVED", "STTM_REVIEW": "STTM_APPROVED", "SODA_REVIEW": "SODA_APPROVED",
               "DBT_REVIEW": "DBT_APPROVED"}

DOMAIN = {"domain_id": "d1", "domain_name": "PROPERTY", "active_flag": True, "approved_version": 1}
HEALTHY = {"source_system_id": "s1", "source_system_name": "YARDI", "health": "HEALTHY", "health_detail": ""}


def request(**over):
    return {"request_id": "r1", "request_type": "SOURCE", "domain_id": "d1", "status": "OPEN", "stage": "NEW",
            "requested_by": "PRODUCT_OWNER", "created_at": "2026-10-20T09:00:00", **over}


def item(**over):
    return {"item_id": "i1", "request_id": "r1", "source_system_id": None, "source_name": "Yardi", "run_id": None, **over}


def run(state):
    return {"run_id": "run1", "run_name": "YARDI property", "domain_id": "d1", "source_system_id": "s1",
            "current_state": state, "parent_run_id": None, "created_at": "2026-10-21T09:00:00"}


class History:
    """The requester's view: what _persist writes as the request moves (events and stage)."""

    def __init__(self):
        self.events = []

    def execute(self, sql, params=()):
        if "INSERT INTO CORE.ONBOARDING_REQUEST_EVENT" in sql:
            self.events.append(params[3])

    def query(self, sql, params=()):
        return []


def step(history, r, it, sources, runs):
    """One look at the request (what refresh does): assess, write the change, carry the new state forward."""
    from app import requests_api as api

    a = rq.assess(r, [it], DOMAIN, sources, runs)
    api._persist(history, r, [it], a)
    r.update(stage=a["stage"], waiting_on=a["waiting_on"], attention=(a["attention"] or "")[:1000] or None)
    if a["status"] != rq.OPEN:
        r["status"] = a["status"]
    s = a["items"]["i1"]
    it.update(status=s["status"], status_detail=(s.get("detail") or "")[:1000] or None)
    return a


@pytest.fixture(autouse=True)
def _api():
    import app.main  # noqa: F401  (registers the API's routers)


def walk_run(history, r, it, path):
    """The run along `path`: at each state the request waits on the persona's privilege, the persona may make the
    calls that move it on, and the graph allows the next move."""
    for n, (state, stage, privilege, persona, steps) in enumerate(path):
        a = step(history, r, it, {"s1": HEALTHY}, {"run1": run(state)})
        assert (a["stage"], a["waiting_on"]) == (stage, privilege), state
        assert holds(persona, privilege), f"{persona} cannot act at {state} ({privilege})"
        assert not holds("product_owner", privilege), f"the requester should not be the one acting at {state}"
        for s in steps:
            assert may_call(persona, s), f"{persona} may not {s} at {state}"
        nxt = HUMAN_GATES.get(state) or (path[n + 1][0] if n + 1 < len(path) else None)
        if nxt:
            actor = "HUMAN" if state in HUMAN_GATES else "SYSTEM"
            assert evaluate(GRAPH, RunContext(state), nxt, actor).allowed, f"{state} -> {nxt}"
    # code approved: the request is done
    assert evaluate(GRAPH, RunContext("DBT_APPROVED"), "COMPLETED", "SYSTEM").allowed
    return step(history, r, it, {"s1": HEALTHY}, {"run1": run("DBT_APPROVED")})


# ---------------------------------------------------------------- Flow A: a new source


def test_flow_a_new_source_from_request_to_pull_request_and_review():
    history = History()
    r, it = request(), item()

    # 1. the Product Owner files it (DATA_STEWARD: the domain's owner files; the API also lets owners file)
    a = step(history, r, it, {}, {})
    assert (a["stage"], a["waiting_on"], it["status"]) == ("ENABLE", "SOURCE.CONNECT", "MISSING")
    assert holds("admin", "SOURCE.CONNECT") and not holds("designer", "SOURCE.CONNECT")
    assert history.events == ["ROUTED"]

    # 2. the Data OS Administrator registers and lands Yardi; the request links it and waits on them to start the run
    it.update(source_system_id="s1", source_name="YARDI")
    a = step(history, r, it, {"s1": HEALTHY}, {})
    assert (a["stage"], a["waiting_on"], it["status"]) == ("ENABLE", "SOURCE.CONNECT", "NO_RUN")
    assert "start the run" in a["waiting_label"] and holds("admin", "RUN.CREATE")

    # 3. the administrator starts the run and lands it: the requester sees "Source onboarded", the Designer is next
    it["run_id"] = "run1"
    for state in ("CREATED", "SOURCE_REGISTERED", "ACCESS_APPROVED", "LANDING_RUNNING"):
        a = step(history, r, it, {"s1": HEALTHY}, {"run1": run(state)})
        assert (a["stage"], a["waiting_on"]) == ("ENABLE", "SOURCE.CONNECT"), state
    assert a["phase"] == "ENABLE"

    # 4-6. Designer designs, Forward Engineering Lead builds to the PR, Data Developer reviews
    done = walk_run(history, r, it, NEW_SOURCE_PATH)
    assert done["status"] == rq.DONE and r["status"] == rq.DONE

    # what the Product Owner's history shows, in order
    kinds = [k for k in history.events if k != "STAGE"]
    assert kinds == ["ROUTED", "SOURCE_ONBOARDED", "DESIGN_STORED", "DONE"], history.events
    assert history.events.count("STAGE") >= 3   # ENABLE -> DESIGN -> BUILD -> REVIEW


def test_the_design_is_the_designers_and_the_build_is_not():
    # the split the request routing promises: the Designer stops at the approved STTM
    for state, _stage, _priv, persona, steps in NEW_SOURCE_PATH:
        if persona == "designer":
            assert all(may_call("designer", s) for s in steps), state
        if persona == "forward_lead":
            assert not holds("designer", "RUN.OPERATE"), "the Designer should not run the build"
    # the Forward Engineering Lead may not approve the checks, QA or the code themselves: autopilot approves the checks
    # on their behalf (AUTOPILOT_APPROVES_DQ) and stops at code review
    assert not holds("forward_lead", privilege_for("POST", REVIEW, {"to_state": "SODA_APPROVED", "decision": "APPROVE"})[0])
    assert not may_call("forward_lead", "approve_code") and may_call("developer", "approve_code")
    assert may_call("forward_lead", "publish_pr")


# ---------------------------------------------------------------- Flow B: a source already ready


def test_flow_b_a_ready_source_goes_straight_to_the_forward_engineering_lead():
    # landed and designed already: the source's run in the domain sits at STTM_APPROVED
    ready = run("STTM_APPROVED")
    picked = rq.pick_run("s1", "d1", [ready])
    assert picked and picked["run_id"] == "run1"
    history = History()
    r, it = request(), item(source_system_id="s1", source_name="YARDI", run_id=picked["run_id"])
    a = step(history, r, it, {"s1": HEALTHY}, {"run1": ready})
    assert (a["stage"], a["waiting_on"]) == ("BUILD", "RUN.OPERATE") and holds("forward_lead", "RUN.OPERATE")
    assert "build end to end" in a["waiting_label"].lower()
    build = NEW_SOURCE_PATH[[p[0] for p in NEW_SOURCE_PATH].index("STTM_APPROVED"):]
    assert walk_run(history, r, it, build)["status"] == rq.DONE


# ---------------------------------------------------------------- Flow C: enhance an onboarded source


def test_flow_c_enhance_skips_the_administrator_and_designs_only_the_delta():
    history = History()
    r = request(intent="ENHANCE")
    it = item(source_system_id="s1", source_name="YARDI", source_note="Add the lease tables")
    a = step(history, r, it, {"s1": HEALTHY}, {})
    assert (a["stage"], a["waiting_on"], a["phase"]) == ("DESIGN", "RUN.CREATE", "IMPACT")
    assert holds("designer", "RUN.CREATE")
    # the run that first onboarded the source is not the enhancement's
    old = {**run("COMPLETED"), "created_at": "2026-09-01T00:00:00"}
    assert rq.pick_run("s1", "d1", [old], after=r["created_at"]) is None
    it["run_id"] = "run1"
    done = walk_run(history, r, it, NEW_SOURCE_PATH)
    assert done["status"] == rq.DONE
    assert "SOURCE_ONBOARDED" not in history.events and "DESIGN_STORED" in history.events


# ---------------------------------------------------------------- Build end to end (autopilot) from the approved STTM


def test_build_end_to_end_runs_to_the_pull_request_then_hands_over_to_code_review():
    """next_action from STTM_APPROVED as the Forward Engineering Lead, the run moving as each step does."""
    facts = {"pipeline": "SILVER", "has_target": True, "autonomous": True, "publish_ready": True,
             "may_signoff_qa": holds("forward_lead", "QA.SIGNOFF"), "may_approve_code": holds("forward_lead", "REVIEW.APPROVE")}
    moves = {"generate_soda": ("SODA_REVIEW", {"soda_generated": True, "soda_proposed": True}),
             "approve_soda": ("SODA_APPROVED", {"soda_proposed": False}),
             "generate_dbt": ("VALIDATION_PENDING", {"dbt_generated": True}),
             "validate": ("DBT_REVIEW", {"validated": True}),
             "publish_pr": ("DBT_REVIEW", {"published": True})}
    state, actions = "STTM_APPROVED", []
    for _ in range(ap.MAX_STEPS):
        nxt = ap.next_action(state, facts)
        if nxt["kind"] != "ACTION":
            break
        actions.append(nxt["action"])
        state, change = moves[nxt["action"]]
        facts.update(change)
    assert actions == ["generate_soda", "approve_soda", "generate_dbt", "validate", "publish_pr"]
    assert nxt["kind"] == "GATE" and nxt["where"] == "code review" and "Pull request opened" in nxt["label"]
    # the run now waits on the Data Developer, and the request says so
    a = rq.assess(request(), [item(source_system_id="s1", run_id="run1")], DOMAIN, {"s1": HEALTHY}, {"run1": run(state)})
    assert (a["stage"], a["waiting_on"]) == ("REVIEW", "REVIEW.APPROVE") and holds("developer", "REVIEW.APPROVE")
