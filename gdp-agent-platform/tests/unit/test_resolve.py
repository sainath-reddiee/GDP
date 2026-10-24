"""Airflow failure resolution (services.resolve): the rules of slides 3 and 4, the engine's routing, the agent answer
parser and the Jira note."""

import json

import pytest

from services.resolve import agent, engine, rules

FAILURE = {"failure_id": "f1", "env_id": "prod", "incident_id": "i1", "dag_id": "sales_daily", "task_id": "load_orders",
           "run_id": "r1", "exception_class": "ProgrammingError",
           "exception_text": "SQL compilation error: Object 'ORDERS_STG' does not exist", "status": "IN_PROGRESS"}
EXCERPT = ("[2026-10-15, 01:00:00 UTC] {taskinstance.py:1234} ERROR - Task failed\n"
           "snowflake.connector.errors.ProgrammingError: 002003 (42S02): SQL compilation error: Object 'ORDERS_STG' does "
           "not exist or not authorized. query_id: 01b2c3d4-0000-1111-2222-333344445555\n"
           "Database Error in model stg_orders (models/staging/stg_orders.sql)")


# ---------------------------------------------------------------- what a failure is

def test_parse_failure_reads_class_query_and_dbt_model():
    parsed = rules.parse_failure(EXCERPT)
    assert parsed["exception_class"] == "ProgrammingError"
    assert parsed["query_id"] == "01b2c3d4-0000-1111-2222-333344445555"
    assert parsed["dbt_node_id"] == "model.stg_orders"


def test_parse_failure_class_from_the_exception_line():
    parsed = rules.parse_failure("Traceback...\nairflow.exceptions.AirflowException: Bash command failed. The command returned 1")
    assert parsed["exception_class"] == "AirflowException"
    assert parsed["exception_text"].startswith("Bash command failed")


def test_signature_ignores_times_ids_and_numbers():
    a = rules.error_signature("d", "t", "ValueError", "ValueError: bad row 123 at 2026-10-01 10:00:00 id 'abc'")
    b = rules.error_signature("d", "t", "ValueError", "ValueError: bad row 987 at 2026-10-02 11:30:00 id 'xyz'")
    c = rules.error_signature("d", "other", "ValueError", "ValueError: bad row 987 at 2026-10-02 11:30:00 id 'xyz'")
    assert a == b and a != c and len(a) == 40


def test_dedup_key_is_one_per_try_and_fits():
    assert rules.dedup_key("e", "d", "t", "r", 0, 1) != rules.dedup_key("e", "d", "t", "r", 0, 2)
    assert len(rules.dedup_key("e" * 64, "d" * 250, "t" * 250, "r" * 250, 3, 9)) <= 600


# ---------------------------------------------------------------- guardrails

def test_preference_defaults_are_safe():
    p = rules.preference(None)
    assert p["auto_resolve_enabled"] is False and p["default_approval_required"] is True
    assert rules.preference({"search_score_min": 7, "max_parallel_tasks": 0, "rca_mode": "x"}) | {} and \
        rules.preference({"search_score_min": 7})["search_score_min"] == 1.0
    assert rules.preference({"max_parallel_tasks": 0})["max_parallel_tasks"] == 1
    assert rules.preference({"rca_mode": "weird"})["rca_mode"] == "AGENT"


def test_task_scope_wins_over_dag_scope_and_no_scope_is_out():
    scopes = [{"dag_id": "sales_daily", "task_id": None, "flow_mode": "AUTO", "enabled": True},
              {"dag_id": "sales_daily", "task_id": "load_orders", "flow_mode": "APPROVAL_ALWAYS", "enabled": True},
              {"dag_id": "hr", "task_id": None, "flow_mode": "AUTO", "enabled": False}]
    assert rules.flow_mode(scopes, "sales_daily", "load_orders") == "APPROVAL_ALWAYS"
    assert rules.flow_mode(scopes, "sales_daily", "other") == "AUTO"
    assert rules.flow_mode(scopes, "hr", "x") == "DISABLED"
    assert rules.flow_mode(scopes, "finance", "x") is None


def test_approval_route_precedence():
    routes = [{"dag_id": "d", "task_id": None, "approver": "LEAD"}, {"dag_id": "d", "task_id": "t", "approver": "OWNER"}]
    assert rules.approver_for(routes, "d", "t")["approver"] == "OWNER"
    assert rules.approver_for(routes, "d", "x")["approver"] == "LEAD"
    assert rules.approver_for(routes, "z", "t") is None


# ---------------------------------------------------------------- search

def _issue(**kw):
    base = {"issue_id": "k1", "status": "PUBLISHED", "dag_id": "sales_daily", "task_id": None, "exception_class": None,
            "match_text": "dag sales_daily | ProgrammingError | sql compilation error: object <str> does not exist",
            "error_signature": "zzz", "fix_type": "PROCEDURE", "approval_required": False}
    base.update(kw)
    return base


def test_only_published_fixes_that_apply_and_score_count():
    f = {**FAILURE, "error_signature": "sig"}
    assert rules.best_hit([(_issue(status="DRAFT"), 0.99)], f, 0.75)[0] is None
    assert rules.best_hit([(_issue(dag_id="other"), 0.99)], f, 0.75)[0] is None
    assert rules.best_hit([(_issue(), 0.5)], f, 0.75) == (None, 0.5)   # weak score: a miss
    hit, score = rules.best_hit([(_issue(), 0.8), (_issue(issue_id="k2", error_signature="sig"), 0.1)], f, 0.75)
    assert hit["issue_id"] == "k2" and score == 1.0   # the same signature beats the search score


def test_word_overlap_is_the_fallback_score():
    f = {**FAILURE, "error_signature": "sig"}
    hit, score = rules.best_hit([(_issue(match_text=rules.match_text(f["dag_id"], f["task_id"], f["exception_class"],
                                                                     f["exception_text"])), None)], f, 0.75)
    assert hit and score == 1.0


# ---------------------------------------------------------------- the decision (slide 3)

@pytest.mark.parametrize("mode,hit,flag,expected", [
    (None, True, False, "OUT_OF_SCOPE"), ("DISABLED", True, False, "OUT_OF_SCOPE"),
    ("TICKET_ONLY", True, False, "TICKET"), ("AUTO", False, False, "TICKET"),
    ("APPROVAL_ALWAYS", True, False, "APPROVAL"), ("AUTO", True, True, "APPROVAL"), ("AUTO", True, False, "RUN"),
    ("AUTO", True, None, "APPROVAL"),   # the default needs approval
])
def test_decide(mode, hit, flag, expected):
    prefs = rules.preference({"default_approval_required": True})
    assert rules.decide(prefs, mode, _issue(approval_required=flag) if hit else None) == expected


def test_transitions_follow_slide_four():
    assert rules.can_move("PENDING", "IN_PROGRESS") and rules.can_move("IN_PROGRESS", "AWAITING_APPROVAL")
    assert rules.can_move("AWAITING_APPROVAL", "RESOLVED") and rules.can_move("AWAITING_APPROVAL", "REJECTED")
    for done in ("RESOLVED", "TICKETED", "REJECTED", "OUT_OF_SCOPE"):
        assert not rules.can_move(done, "PENDING")
    assert not rules.can_move("PENDING", "RESOLVED")


# ---------------------------------------------------------------- what a fix may call

def test_fix_procedures_are_core_fix_only():
    assert rules.fix_procedure("core.fix_refresh_stage", "AIP") == "AIP.CORE.FIX_REFRESH_STAGE"
    assert rules.fix_procedure("AIP.CORE.FIX_X", "AIP") == "AIP.CORE.FIX_X"
    for bad in ("CORE.SP_CLAIM_AND_RESOLVE", "OTHER.CORE.FIX_X", "CORE.FIX_X; DROP TABLE T", "PUBLIC.FIX_X", ""):
        with pytest.raises(ValueError):
            rules.fix_procedure(bad, "AIP")


def test_skill_steps_are_ordered_unique_fix_procedures():
    steps = rules.skill_steps([{"step_no": 2, "action": "CORE.FIX_B", "input": {"x": 1}}, {"step_no": 1, "action": "CORE.FIX_A"}], "AIP")
    assert [s["step_no"] for s in steps] == [1, 2] and steps[1]["input"] == json.dumps({"x": 1})
    with pytest.raises(ValueError):
        rules.skill_steps([{"step_no": 1, "action": "CORE.FIX_A"}, {"step_no": 1, "action": "CORE.FIX_B"}], "AIP")
    with pytest.raises(ValueError):
        rules.skill_steps([{"action": "CORE.RUN_PROFILING"}], "AIP")
    with pytest.raises(ValueError):
        rules.skill_steps([], "AIP")


def test_publish_needs_a_reviewer_other_than_the_author():
    draft = {"status": "DRAFT", "match_text": "x", "fix_type": "PROCEDURE", "procedure_fqn": "CORE.FIX_A", "created_by": "ANA"}
    with pytest.raises(ValueError, match="other than its author"):
        rules.publish_check(draft, "ana", database="AIP")
    rules.publish_check(draft, "BEN", database="AIP")
    with pytest.raises(ValueError):
        rules.publish_check({**draft, "status": "PUBLISHED"}, "BEN", database="AIP")
    with pytest.raises(ValueError):
        rules.publish_check({**draft, "fix_type": "SKILL", "skill_id": "s"}, "BEN", skill=None, database="AIP")


# ---------------------------------------------------------------- engine routing (no database)

class Recorder:
    def __init__(self):
        self.calls = []

    def query(self, sql, params=()):
        return []

    def execute(self, sql, params=()):
        self.calls.append(("execute", sql.split()[0], params))

    def execute_count(self, sql, params=()):
        return 1


class Exec:
    database = "AIP"


@pytest.fixture
def routed(monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "move", lambda db, fid, cur, tgt, fields=None: seen.setdefault("moves", []).append((cur, tgt)) or True)
    monkeypatch.setattr(engine, "open_ticket", lambda db, f, cur, a, p, actor, reason, prior=None, rca=None: {"action": "ticketed", "reason": reason})
    monkeypatch.setattr(engine, "request_approval", lambda db, f, hit, routes, p: {"action": "approval", "issue": hit["issue_id"]})
    monkeypatch.setattr(engine, "run_fix", lambda db, f, hit, p, actor, ex, cur: seen.get("fix") or {"action": "resolved"})
    return seen


def test_out_of_scope_without_a_scope(routed, monkeypatch):
    monkeypatch.setattr(engine, "search_fixes", lambda *a, **k: pytest.fail("no search when out of scope"))
    out = engine.resolve_one(Recorder(), FAILURE, rules.preference({}), [], [], Exec(), None)
    assert out["action"] == "out_of_scope" and routed["moves"] == [("IN_PROGRESS", "OUT_OF_SCOPE")]


def test_ticket_only_skips_the_search(routed, monkeypatch):
    monkeypatch.setattr(engine, "search_fixes", lambda *a, **k: pytest.fail("TICKET_ONLY skips the search"))
    scopes = [{"dag_id": "sales_daily", "task_id": None, "flow_mode": "TICKET_ONLY", "enabled": True}]
    assert engine.resolve_one(Recorder(), FAILURE, rules.preference({}), scopes, [], Exec(), None)["action"] == "ticketed"


def test_miss_tickets_and_never_writes_a_known_issue(routed, monkeypatch):
    db = Recorder()
    monkeypatch.setattr(engine, "search_fixes", lambda *a, **k: [(_issue(), 0.3)])
    scopes = [{"dag_id": "sales_daily", "task_id": None, "flow_mode": "AUTO", "enabled": True}]
    out = engine.resolve_one(db, {**FAILURE, "error_signature": "sig"}, rules.preference({}), scopes, [], Exec(), None)
    assert out["action"] == "ticketed" and "best 0.3" in out["reason"]
    assert not any("KNOWN_ISSUE" in str(c) for c in db.calls)


def test_hit_runs_or_waits_for_approval(routed, monkeypatch):
    scopes = [{"dag_id": "sales_daily", "task_id": None, "flow_mode": "AUTO", "enabled": True}]
    monkeypatch.setattr(engine, "search_fixes", lambda *a, **k: [(_issue(approval_required=False), 0.9)])
    assert engine.resolve_one(Recorder(), {**FAILURE, "error_signature": "s"}, rules.preference({}), scopes, [], Exec(), None)["action"] == "resolved"
    monkeypatch.setattr(engine, "search_fixes", lambda *a, **k: [(_issue(approval_required=True), 0.9)])
    assert engine.resolve_one(Recorder(), {**FAILURE, "error_signature": "s"}, rules.preference({}), scopes, [], Exec(), None)["action"] == "approval"


def test_a_failed_fix_reaches_a_person(routed, monkeypatch):
    scopes = [{"dag_id": "sales_daily", "task_id": None, "flow_mode": "AUTO", "enabled": True}]
    monkeypatch.setattr(engine, "search_fixes", lambda *a, **k: [(_issue(approval_required=False, title="Recreate stage"), 0.9)])
    routed["fix"] = {"action": "fix_failed", "error": "boom"}
    out = engine.resolve_one(Recorder(), {**FAILURE, "error_signature": "s"}, rules.preference({}), scopes, [], Exec(), None)
    assert out["action"] == "ticketed" and "Recreate stage failed: boom" in out["reason"]


def test_nothing_is_claimed_while_auto_resolution_is_off(monkeypatch):
    monkeypatch.setattr(engine, "load_preference", lambda db: rules.preference({"auto_resolve_enabled": False}))
    monkeypatch.setattr(engine, "claim", lambda *a, **k: pytest.fail("must not claim"))
    assert engine.claim_and_resolve(Recorder(), "w", Exec(), None) == {"enabled": False, "claimed": 0, "actions": {}}


def test_ingest_skips_retrying_and_dag_level_failures():
    db = Recorder()
    assert engine.ingest(db, {"kind": "FAILED", "dag_id": "d", "env_id": "e"}, "i") is None
    assert engine.ingest(db, {"kind": "RETRIES_EXHAUSTED", "dag_id": "d", "env_id": "e", "task_id": "t", "retrying": True}, "i") is None
    fid = engine.ingest(db, {"kind": "RETRIES_EXHAUSTED", "dag_id": "d", "env_id": "e", "task_id": "t", "run_id": "r",
                             "occurrence_key": "task:r:t:-1:3", "error_excerpt": EXCERPT}, "i")
    assert fid


# ---------------------------------------------------------------- the agent's answer

def test_agent_final_text_from_event_list_and_sse():
    events = [{"event": "response.text.delta", "data": {"text": "par"}},
              {"event": "response", "data": {"content": [{"type": "thinking", "thinking": {"text": "x"}},
                                                         {"type": "text", "text": '{"cause": "upstream late"}'}]}}]
    assert agent.json_answer(agent.final_text(json.dumps(events)))["cause"] == "upstream late"
    sse = 'event: response.text.delta\ndata: {"text": "{\\"cause\\": "}\n\nevent: response.text.delta\ndata: {"text": "\\"a\\"}"}\n'
    assert agent.json_answer(agent.final_text(sse)) == {"cause": "a"}
    with pytest.raises(agent.AgentUnavailable):
        agent.final_text("[]")


def test_agent_is_unavailable_outside_snowflake():
    with pytest.raises(agent.AgentUnavailable):
        agent.run("AIP", "CORE", "RCA_AGENT", "hi")


# ---------------------------------------------------------------- the Jira note

def test_resolution_note_raises_the_ticket_and_stores_the_key(monkeypatch):
    from services.ops import tickets

    class Store:
        def __init__(self):
            self.db, self.events = Recorder(), []

        def event(self, *a, **k):
            self.events.append(a)

    class Client:
        def __init__(self):
            self.comments = []

        def add_comment(self, key, body):
            self.comments.append(key)

    monkeypatch.setattr(tickets, "raise_ticket", lambda store, iid, client, settings, record=False: {"state": "raised", "key": "OPS-7"})
    store, client = Store(), Client()
    row = {"payload": json.dumps({"failure_id": "f1", "text": "Cause: x", "raise": True})}
    status, _ = tickets.resolution_note(store, {"incident_id": "i1", "jira_key": None}, row, client, {})
    assert status == "SENT" and client.comments == ["OPS-7"]
    assert any(c[2] == ("OPS-7", "f1") for c in store.db.calls)
    status, detail = tickets.resolution_note(store, {"incident_id": "i1", "jira_key": None},
                                             {"payload": json.dumps({"text": "approve?", "raise": False})}, client, {})
    assert status == "SKIPPED" and "no ticket" in detail


# ---------------------------------------------------------------- governance

def test_resolution_privileges_and_routes():
    from services.governance.policy import ADDED_PRIVILEGES, PRIVILEGES, SYSTEM_VERSION, no_queue, privilege_for

    assert SYSTEM_VERSION >= 8 and set(ADDED_PRIVILEGES[8]) == {"OPS.FIX_APPROVE", "OPS.FIX_PUBLISH", "OPS.RESOLVE_ADMIN"}
    assert all(p in PRIVILEGES for p in ADDED_PRIVILEGES[8])
    assert privilege_for("POST", "/api/ops/resolve/approvals/a1/approve")[0] == "OPS.FIX_APPROVE"
    assert privilege_for("POST", "/api/ops/resolve/fixes/k1/publish")[0] == "OPS.FIX_PUBLISH"
    assert privilege_for("POST", "/api/ops/resolve/fixes")[0] == "OPS.OPERATE"
    assert privilege_for("PUT", "/api/ops/resolve/preference")[0] == "OPS.RESOLVE_ADMIN"
    assert privilege_for("GET", "/api/ops/resolve/failures")[0] == "OPS.VIEW"
    assert no_queue("POST", "/api/ops/resolve/approvals/a1/reject") and no_queue("POST", "/api/ops/resolve/fixes/k1/publish")


def test_session_binds_write_none_as_null():
    from services.ops.sqlio import bind

    assert bind("UPDATE T SET A = %s, B = %s WHERE C = %s AND D LIKE '5%%'", ("x", None, 3)) == \
        ("UPDATE T SET A = ?, B = NULL WHERE C = ? AND D LIKE '5%'", ["x", 3])
    assert bind("SELECT 1", ()) == ("SELECT 1", [])
