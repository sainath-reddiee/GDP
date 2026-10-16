"""Automatic fixes on record in Jira: a successful fix queues a note that raises the ticket with the cause and the fix,
the ticket closes when the rerun resolves the incident, and known fixes that keep firing are reported."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "unit"))

import test_ops_incidents as oi  # noqa: E402
from services.ops import tickets  # noqa: E402
from services.resolve import engine, publish  # noqa: E402


class Recorder:
    def __init__(self, answers=None):
        self.answers, self.sent = dict(answers or {}), []

    def query(self, sql, params=()):
        self.sent.append((sql, params))
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return [dict(r) for r in rows]
        return []

    def execute(self, sql, params=()):
        self.sent.append((sql, params))

    def execute_count(self, sql, params=()):
        self.sent.append((sql, params))
        return 1


class Executor:
    database = "DB1"

    def call_procedure(self, fqn, failure_id):
        return "added AMOUNT_USD"


FAILURE = {"failure_id": "f1", "incident_id": "i1", "env_id": "e", "dag_id": "lab_invalid_column",
           "task_id": "publish_revenue", "exception_class": "ProgrammingError",
           "exception_text": "000904 (42000): invalid identifier 'AMOUNT_USD'", "issue_id": "k1", "rca": None}
ISSUE = {"issue_id": "k1", "title": "Add AMOUNT_USD to LAB_ORDERS_CLEAN", "fix_type": "PROCEDURE",
         "procedure_fqn": "DB1.CORE.FIX_LAB_ADD_AMOUNT_USD", "proposed_by_agent": True, "published_by": "SATYASAINATH",
         "rationale": "The table documents AMOUNT_USD as AMOUNT in USD; the column was never added."}


def jira_payload(db):
    for params in [p for s, p in db.sent if "MERGE INTO CORE.NOTIFICATION" in s]:
        for p in params:
            if isinstance(p, str) and p.startswith("{") and '"record"' in p:
                return json.loads(p)
    return None


def test_a_successful_fix_raises_a_ticket_on_record():
    db = Recorder()
    out = engine.run_fix(db, FAILURE, ISSUE, {"airflow_clear_enabled": True}, "system", Executor(), "IN_PROGRESS")
    assert out["action"] == "resolved" and out["clear"] == "REQUESTED"
    payload = jira_payload(db)
    assert payload and payload["raise"] is True and payload["record"] is True and payload["failure_id"] == "f1"
    text = payload["text"]
    assert 'known fix "Add AMOUNT_USD to LAB_ORDERS_CLEAN" (DB1.CORE.FIX_LAB_ADD_AMOUNT_USD)' in text
    assert "Cause: The table documents AMOUNT_USD" in text
    assert "approved by SATYASAINATH" in text and "closes when the rerun succeeds" in text


def test_ticketing_automatic_fixes_can_be_turned_off():
    db = Recorder({"FROM CORE.PLATFORM_CONFIG": [{"config_value": json.dumps({"ticket_auto_fixes": False})}]})
    engine.run_fix(db, FAILURE, ISSUE, {"airflow_clear_enabled": False}, "system", Executor(), "IN_PROGRESS")
    assert jira_payload(db) is None


def test_fixed_text_without_clear_or_agent():
    text = engine.fixed_text({**FAILURE, "rca": {"cause": "the staging table was dropped"}},
                             {"title": "Recreate staging", "fix_type": "SKILL", "skill_name": "recreate_stg"}, "SKIPPED")
    assert "(skill recreate_stg)" in text and "Cause: the staging table was dropped" in text
    assert "clearing Airflow tasks is off" in text and "approved by" not in text


def test_an_auto_fixed_incident_is_closed_on_resolve():
    s = oi.store_with_dag()
    iid = oi.opened_incident(s)
    s.incidents[iid].update({"jira_key": "DATA-9", "status": "RESOLVED", "resolution": "rerun succeeded"})
    jira = oi.FakeJira()
    tickets.resolved(s, s.incidents[iid], jira, {"transition_on_resolve": False, "close_auto_fixed": True, "done_status": "Done"})
    assert jira.comments and not jira.moved   # not an automatic fix: left open as before
    s.event(iid, "auto_fixed", "system", {"issue_id": "k1"})
    tickets.resolved(s, s.incidents[iid], jira, {"transition_on_resolve": False, "close_auto_fixed": True, "done_status": "Done"})
    assert jira.moved == [("DATA-9", "31")]
    jira = oi.FakeJira()
    tickets.resolved(s, s.incidents[iid], jira, {"transition_on_resolve": False, "close_auto_fixed": False})
    assert not jira.moved


def test_a_record_ticket_is_raised_and_closed_even_after_the_incident_resolved():
    s = oi.store_with_dag()
    s.db = Recorder()
    iid = oi.opened_incident(s)
    s.incidents[iid].update({"status": "RESOLVED", "resolution": "rerun succeeded", "resolved_by": "system"})
    s.event(iid, "auto_fixed", "system", {})
    jira = oi.FakeJira()
    settings = {"close_auto_fixed": True, "done_status": "Done"}
    assert tickets.raise_ticket(s, iid, jira, settings)["state"] == "SKIPPED"   # an ordinary create still skips
    s.incidents[iid]["jira_state"] = None
    row = {"incident_id": iid, "kind": "resolution",
           "payload": json.dumps({"failure_id": "f1", "text": "Fixed automatically", "raise": True, "record": True})}
    status, detail = tickets.handle(s, row, jira, settings)
    assert status == "SENT" and jira.created and jira.moved == [("DATA-1", "31")]


def test_recurring_known_fixes():
    db = Recorder({"FROM (SELECT R.ISSUE_ID": [
        {"issue_id": "k1", "title": "Add AMOUNT_USD", "status": "PUBLISHED", "proposed_by_agent": True, "runs_30d": 4,
         "ok_30d": 3, "dags_30d": 2, "last_run_at": "2026-10-15", "dag_ids": '["a", "b"]'}]})
    found = publish.recurring(db)
    assert found == [{"issue_id": "k1", "title": "Add AMOUNT_USD", "status": "PUBLISHED", "proposed_by_agent": True,
                      "runs": 4, "ok": 3, "success_rate": 0.75, "dags": 2, "dag_ids": ["a", "b"],
                      "last_run_at": "2026-10-15"}]
    assert db.sent[0][1] == (30, 2)


def test_known_fix_list_carries_recent_usage():
    db = Recorder()
    publish.list_issues(db, "PUBLISHED", "amount")
    sql, params = db.sent[0]
    assert "U.RUNS_30D, U.OK_30D, U.DAGS_30D, U.LAST_RUN_AT" in sql
    assert params[0] == publish.RECENT_DAYS and params[1] == "PUBLISHED"
