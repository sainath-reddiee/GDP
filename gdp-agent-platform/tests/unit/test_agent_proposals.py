"""Agent-proposed fixes (services.resolve.actions, propose and the engine's proposal path): the safe-action catalogue,
the fix scope, the checks against Snowflake, and one approval that publishes and runs the fix."""

import json

import pytest

from services.resolve import actions, engine, propose, rules

DB = "DEV_AI_PLATFORM"
SCOPE = "DEV_AI_PLATFORM.CORE.LAB_*, ANALYTICS.STAGING.*"
TABLES = {"DEV_AI_PLATFORM.CORE.LAB_ORDERS_CLEAN": {"ORDER_ID": "NUMBER", "AMOUNT": "NUMBER"},
          "DEV_AI_PLATFORM.CORE.LAB_ORDERS_RAW": {"ORDER_ID": "NUMBER", "AMOUNT": "NUMBER"}}


@pytest.fixture(autouse=True)
def snowflake(monkeypatch):
    monkeypatch.setattr(actions, "columns_of", lambda db, fqn: TABLES.get(fqn))
    monkeypatch.setattr(actions, "stage_exists", lambda db, fqn: False)


def plan(*steps):
    return [{"action": a, "input": i} for a, i in steps]


def test_valid_plan_becomes_catalogue_skill_steps():
    steps = actions.validate_plan(None, plan(("ADD_COLUMN", {"table": "CORE.LAB_ORDERS_CLEAN", "column": "amount_usd",
                                                             "data_type": "number(12,2)", "fill_from": "AMOUNT"})), DB, SCOPE)
    assert steps[0]["action"] == "CORE.FIX_ADD_COLUMN"
    assert json.loads(steps[0]["input"]) == {"table": "DEV_AI_PLATFORM.CORE.LAB_ORDERS_CLEAN", "column": "AMOUNT_USD",
                                             "data_type": "NUMBER(12,2)", "fill_from": "AMOUNT"}
    assert actions.risk_of(steps) == "schema"
    rules.skill_steps(steps, DB)   # what the resolver will accept to run


@pytest.mark.parametrize("bad,why", [
    (plan(("DROP_TABLE", {"table": "CORE.LAB_X"})), "unknown action"),
    (plan(("ADD_COLUMN", {"table": "CORE.INCIDENT", "column": "X", "data_type": "NUMBER"})), "outside the agent fix scope"),
    (plan(("ADD_COLUMN", {"table": "OTHER_DB.STAGING.T", "column": "X", "data_type": "NUMBER"})), "outside the agent fix scope"),
    (plan(("ADD_COLUMN", {"table": "ANALYTICS.STAGING.NOT_THERE", "column": "X", "data_type": "NUMBER"})), "does not exist"),
    (plan(("ADD_COLUMN", {"table": "CORE.LAB_ORDERS_CLEAN", "column": "AMOUNT", "data_type": "NUMBER"})), "already exists"),
    (plan(("ADD_COLUMN", {"table": "CORE.LAB_ORDERS_CLEAN", "column": "X", "data_type": "NUMBER; DROP TABLE T"})), "not allowed"),
    (plan(("ADD_COLUMN", {"table": "CORE.LAB_ORDERS_CLEAN", "column": "X", "data_type": "NUMBER", "fill_from": "NOPE"})), "does not exist"),
    (plan(("ADD_COLUMN", {"table": "CORE.LAB_NOT_THERE", "column": "X", "data_type": "NUMBER"})), "does not exist"),
    (plan(("CREATE_TABLE_FROM", {"table": "CORE.LAB_ORDERS_RAW", "source_table": "CORE.LAB_ORDERS_CLEAN"})), "already exists"),
    (plan(("CREATE_TABLE_FROM", {"table": "CORE.LAB_ORDERS_STG", "source_table": "CORE.LAB_GHOST"})), "does not exist"),
    (plan(("DEDUP_ON_KEY", {"table": "CORE.LAB_ORDERS_RAW", "key_columns": ["NOPE"]})), "key columns"),
    (plan(("ADD_COLUMN", {"table": "CORE.LAB_X'; --", "column": "X", "data_type": "NUMBER"})), "plain object name"),
    ([], "one to five"),
])
def test_unsafe_or_wrong_plans_are_refused(bad, why):
    with pytest.raises(ValueError, match=why):
        actions.validate_plan(None, bad, DB, SCOPE)


def test_platform_tables_are_never_fixable_even_with_a_wide_scope():
    with pytest.raises(ValueError, match="platform object"):
        actions.validate_plan(None, plan(("ADD_COLUMN", {"table": "CORE.INCIDENT", "column": "X", "data_type": "NUMBER"})),
                              DB, "DEV_AI_PLATFORM.*.*")


def test_no_scope_no_proposals():
    with pytest.raises(ValueError, match="no agent fix scope"):
        actions.validate_plan(None, plan(("RERUN_ONLY", {})), DB, "")


def test_create_table_from_and_rerun_only():
    steps = actions.validate_plan(None, plan(("CREATE_TABLE_FROM", {"table": "CORE.LAB_ORDERS_STG", "source_table": "CORE.LAB_ORDERS_RAW"}),
                                             ("RERUN_ONLY", {})), DB, SCOPE)
    assert [s["action"] for s in steps] == ["CORE.FIX_CREATE_TABLE_FROM", "CORE.FIX_RERUN_ONLY"]


# ---------------------------------------------------------------- the proposer

PREFS = rules.preference({"agent_proposals_enabled": True, "agent_fix_scope": SCOPE})
FAILURE = {"failure_id": "f1", "dag_id": "lab_invalid_column", "task_id": "publish_revenue", "exception_class": "ProgrammingError",
           "exception_text": "000904 (42000): SQL compilation error: error line 1 at position 11 invalid identifier 'AMOUNT_USD'"}
EXCERPT = 'return run("SELECT SUM(AMOUNT_USD) FROM CORE.LAB_ORDERS_CLEAN")[0][0]\nsnowflake.connector.errors.ProgrammingError: ...'


def test_the_agents_own_plan_is_used_when_valid():
    rca = {"cause": "AMOUNT_USD is missing", "proposed_fix": {"rationale": "add it", "steps": plan(
        ("ADD_COLUMN", {"table": "DEV_AI_PLATFORM.CORE.LAB_ORDERS_CLEAN", "column": "AMOUNT_USD", "data_type": "NUMBER(12,2)"}))}}
    out = propose.propose(None, FAILURE, rca, PREFS, DB, EXCERPT, complete=lambda *a, **k: pytest.fail("no model call needed"))
    assert out["source"] == "agent" and out["steps"][0]["action"] == "CORE.FIX_ADD_COLUMN"


def test_the_grounded_proposer_sees_the_named_tables_and_is_checked(monkeypatch):
    monkeypatch.setattr(actions, "tables_in_scope", lambda db, p, d: list(TABLES))
    seen = {}

    def model(session, prompt, schema, **kw):
        seen["prompt"] = prompt
        return ({"fixable": True, "rationale": "add the column from AMOUNT", "steps": plan(
            ("ADD_COLUMN", {"table": "DEV_AI_PLATFORM.CORE.LAB_ORDERS_CLEAN", "column": "AMOUNT_USD",
                            "data_type": "NUMBER(12,2)", "fill_from": "AMOUNT"}))}, {}, "m")
    out = propose.propose(object(), FAILURE, {"cause": "c"}, PREFS, DB, EXCERPT, complete=model)
    assert out["source"] == "model"
    assert "table DEV_AI_PLATFORM.CORE.LAB_ORDERS_CLEAN: columns ORDER_ID NUMBER, AMOUNT NUMBER" in seen["prompt"]


def test_an_invented_object_is_dropped_with_a_note(monkeypatch):
    monkeypatch.setattr(actions, "tables_in_scope", lambda db, p, d: [])
    rca = {"cause": "c"}
    out = propose.propose(object(), FAILURE, rca, PREFS, DB, EXCERPT, complete=lambda *a, **k: (
        {"fixable": True, "rationale": "r", "steps": plan(("ADD_COLUMN", {"table": "CORE.LAB_IMAGINARY", "column": "X",
                                                                          "data_type": "NUMBER"}))}, {}, "m"))
    assert out is None and "rejected by the checks" in rca["proposal_note"]


def test_off_means_no_proposal():
    assert propose.propose(None, FAILURE, {}, rules.preference({}), DB, EXCERPT) is None


# ---------------------------------------------------------------- the engine: a miss becomes a proposal, approved once

class Db:
    def __init__(self):
        self.sql = []

    def query(self, sql, params=()):
        self.sql.append(sql)
        if "SELECT A.APPROVAL_ID, A.STATUS" in sql:
            return [{"approval_id": "a1", "status": "OPEN", "approver": "LAB_APPROVER", "failure_id": "f1", "issue_id": "k1",
                     "kind": "PROPOSAL", "created_by": engine.AGENT_AUTHOR, "published_by": None, "issue_status": "DRAFT"}]
        return []

    def execute(self, sql, params=()):
        self.sql.append(sql)

    def execute_count(self, sql, params=()):
        self.sql.append(sql)
        return 1


def test_a_miss_with_a_valid_plan_becomes_an_approval_of_kind_proposal(monkeypatch):
    calls = {}
    from services.resolve import publish
    monkeypatch.setattr(publish, "save_skill", lambda db, data, actor, skill_id=None: {"skill_id": "s1", "skill_name": data["skill_name"]})
    monkeypatch.setattr(publish, "draft_from_incident", lambda db, iid: {"error_signature": "sig", "match_text": "m"})
    monkeypatch.setattr(publish, "create", lambda db, data, actor: calls.setdefault("issue", {**data, "issue_id": "k1", "created_by": actor}))
    monkeypatch.setattr(engine, "request_approval", lambda db, f, issue, routes, prefs, kind="RUN", rca=None, text=None:
                        calls.update(kind=kind, rca=rca, text=text) or {"action": "approval", "approval_id": "a1"})
    monkeypatch.setattr(engine, "_incident_event", lambda *a, **k: None)
    plan_out = {"steps": [{"step_no": 1, "action": "CORE.FIX_ADD_COLUMN", "input": "{}", "note": "ADD_COLUMN: add"}],
                "rationale": "add the column", "source": "agent", "risk": "schema"}
    out = engine.propose_fix(Db(), {**FAILURE, "incident_id": "i1", "error_signature": "sig"}, {"cause": "missing column"},
                             PREFS, DB, [], proposer=lambda *a: plan_out)
    assert out["action"] == "proposed" and calls["kind"] == "PROPOSAL"
    assert calls["issue"]["created_by"] == engine.AGENT_AUTHOR and calls["issue"]["fix_type"] == "SKILL"
    assert calls["rca"]["proposal"]["steps"] == plan_out["steps"] and "approve it once" in calls["text"].lower()


def test_approving_a_proposal_publishes_it_as_the_approver_and_drops_the_approval_flag(monkeypatch):
    from services.resolve import publish
    seen = {}
    monkeypatch.setattr(publish, "publish", lambda db, issue_id, actor: seen.update(issue=issue_id, by=actor))
    monkeypatch.setattr(engine, "_incident_event", lambda *a, **k: None)
    db = Db()
    out = engine.decide_approval(db, "a1", "LAB_APPROVER", True, None, may_approve_any=False)
    assert out["status"] == "APPROVED" and seen == {"issue": "k1", "by": "LAB_APPROVER"}
    assert any("APPROVAL_REQUIRED = FALSE" in s for s in db.sql)
