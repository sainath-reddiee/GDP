"""Fixes from the review of the Airflow resolution work: downstream clears, retry caps, approvals that cannot change
under the approver, a catalogue that survives Airflow errors, statements kept in excerpts, and the loop guard."""

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "unit"))

import test_ops_incidents as oi  # noqa: E402
from services.ops import catalog, pipeline, tickets  # noqa: E402
from services.ops import incidents as inc  # noqa: E402
from services.ops.normalize import excerpt  # noqa: E402
from services.resolve import actions, bridge, engine, fixes, publish  # noqa: E402


class Db:
    """Answers by SQL fragment (a list, or a callable of the params) and records every statement."""

    def __init__(self, answers=None, count=1):
        self.answers, self.count, self.sent = dict(answers or {}), count, []

    def _rows(self, sql, params):
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return rows(params) if callable(rows) else [dict(r) for r in rows]
        return []

    def query(self, sql, params=()):
        self.sent.append((sql, params))
        return self._rows(sql, params)

    def execute(self, sql, params=()):
        self.sent.append((sql, params))

    def execute_count(self, sql, params=()):
        self.sent.append((sql, params))
        return self.count(sql, params) if callable(self.count) else self.count

    def statements(self, fragment):
        return [(s, p) for s, p in self.sent if fragment in s]


# ---------------------------------------------------------------- the clear after a fix


def test_a_fixed_task_is_cleared_with_its_downstream_tasks():
    calls = []

    class Client:
        def clear_task_instances(self, dag_id, task_ids, run_id, dry_run=True, only_failed=True, include_downstream=False):
            calls.append({"downstream": include_downstream, "only_failed": only_failed, "dry_run": dry_run})

    db = Db({"WHERE F.CLEAR_STATE = 'REQUESTED'": [{"failure_id": "f1", "env_id": "e", "dag_id": "d", "task_id": "t",
                                                    "run_id": "r", "incident_id": None}]})
    assert bridge.clear_requested(db, mwaa_factory=lambda row: Client()) == {"cleared": 1, "failed": 0}
    assert calls == [{"downstream": True, "only_failed": True, "dry_run": False}]
    requeue = db.sent[0][0]
    assert "SET CLEAR_STATE = 'REQUESTED'" in requeue and "CLEAR_STATE = 'CLEARING'" in requeue


# ---------------------------------------------------------------- a failure the engine cannot process


@pytest.mark.parametrize("before,status", [(0, "PENDING"), (1, "PENDING"), (2, "TICKETED")])
def test_errors_are_retried_then_handed_to_a_person(before, status):
    db = Db({"SELECT COALESCE(ATTEMPTS, 0)": [{"n": before, "incident_id": "i1"}]})
    assert engine.retry_or_give_up(db, "f1", "RuntimeError: boom") == status
    update = db.statements("UPDATE CORE.FAILURE_EVENT SET STATUS = %s, ATTEMPTS")[0][1]
    assert update[:2] == (status, before + 1)
    queued = db.statements("MERGE INTO CORE.NOTIFICATION")
    assert bool(queued) == (status == "TICKETED")


def test_an_approved_fix_that_vanished_hands_the_failure_to_a_person():
    db = Db({"WHERE A.STATUS = 'APPROVED'": [{"approval_id": "a1", "failure_id": "f1", "issue_id": "gone",
                                               "decided_by": "ANA"}],
             "FROM CORE.FAILURE_EVENT WHERE FAILURE_ID": [{"failure_id": "f1", "status": "AWAITING_APPROVAL",
                                                          "incident_id": "i1", "dag_id": "d"}]})
    out = engine.resume_approved(db, executor=None, analyst=None)
    assert out["failed"] == 1
    moved = db.statements("UPDATE CORE.FAILURE_EVENT SET STATUS = %s")[0][1]
    assert moved[0] == "TICKETED"


# ---------------------------------------------------------------- the fix-and-rerun loop


def test_a_fix_that_keeps_not_repairing_the_task_is_ticketed():
    failure = {"failure_id": "f3", "env_id": "e", "dag_id": "d", "task_id": "t", "run_id": "r", "incident_id": None,
               "exception_class": "ProgrammingError", "exception_text": "x", "error_signature": "s"}
    hit = {"issue_id": "k1", "title": "Rerun", "status": "PUBLISHED", "approval_required": False, "error_signature": "s",
           "fix_type": "PROCEDURE", "procedure_fqn": "DB.CORE.FIX_RERUN_ONLY"}
    db = Db({"STATUS = 'PUBLISHED' AND ERROR_SIGNATURE": [hit], "AS N FROM CORE.FAILURE_EVENT": [{"n": 2}]})

    class Analyst:
        def analyse(self, failure, prefs):
            return {"cause": "persistent", "mode": "grounded"}

    class Executor:
        database = "DB"

        def call_procedure(self, *a):
            raise AssertionError("the fix must not run a third time")

    prefs = {"search_score_min": 0.75, "default_approval_required": True}
    out = engine.resolve_one(db, failure, prefs, [{"dag_id": "d", "task_id": None, "flow_mode": "AUTO", "enabled": True}],
                             [], Executor(), Analyst(), search=lambda f: [])
    assert out["action"] == "ticketed"
    moved = db.statements("SET STATUS = %s")[-1][1]
    assert moved[0] == "TICKETED" and any("already ran 2 times in this run" in str(p) for p in moved)


# ---------------------------------------------------------------- what the approver reviews is what runs


def test_a_draft_waiting_for_approval_cannot_change():
    db = Db({"FROM CORE.KNOWN_ISSUE WHERE ISSUE_ID": [{"issue_id": "k1", "status": "DRAFT"}],
             "FROM CORE.APPROVAL_REQUEST WHERE ISSUE_ID": [{"x": 1}]})
    with pytest.raises(ValueError, match="waits for an approval"):
        publish.update(db, "k1", {"title": "changed"}, "OPERATOR")
    locked = Db({"JOIN CORE.APPROVAL_REQUEST A": [{"x": 1}]})
    assert "waits for an approval" in publish.skill_locked(locked, "s1")
    assert publish.skill_locked(Db(), "s1") is None


def test_the_step_tool_refuses_a_fix_that_was_not_approved():
    failure = {"failure_id": "f1", "status": "AWAITING_APPROVAL", "issue_id": "k1"}
    db = Db({"FROM CORE.FAILURE_EVENT WHERE FAILURE_ID": [failure],
             "FROM CORE.KNOWN_ISSUE WHERE ISSUE_ID": [{"issue_id": "k1", "status": "PUBLISHED", "skill_id": "s1"}],
             "FROM CORE.FIX_SKILL WHERE SKILL_ID": [{"skill_id": "s1", "steps": json.dumps(
                 [{"step_no": 1, "action": "CORE.FIX_DEDUP_ON_KEY"}])}]})
    assert fixes.run_step_entry(db, "f1", "r1", 1) == {"error": "the fix for this failure has not been approved"}


# ---------------------------------------------------------------- the catalogue survives Airflow errors


class Airflow:
    def __init__(self, source="with DAG('d'):\n    t = SQLExecuteQueryOperator(task_id='t', sql='SELECT 1 FROM A.B')\n",
                 tasks=None, source_error=None, tasks_error=None):
        self.source, self.tasks, self.source_error, self.tasks_error = source, tasks, source_error, tasks_error

    def dag_source(self, dag_id):
        if self.source_error:
            raise self.source_error
        return self.source

    def dag_tasks(self, dag_id):
        if self.tasks_error:
            raise self.tasks_error
        return self.tasks if self.tasks is not None else [{"task_id": "t", "operator_name": "SQLExecuteQueryOperator",
                                                            "downstream_task_ids": []}]


@pytest.mark.parametrize("client", [Airflow(source_error=TimeoutError("read timed out")),
                                    Airflow(tasks_error=RuntimeError("503"))])
def test_a_read_error_keeps_the_existing_catalogue(client):
    db = Db()
    out = catalog.catalogue_dag(db, client, "e", {"dag_id": "d", "fileloc": "/dags/d.py", "source_hash": "old"})
    assert out["changed"] is False and out["error"]
    assert not db.statements("MERGE INTO CORE.DAG_TASK") and not db.statements("DELETE FROM CORE.DAG_TASK")
    stamp = db.statements("UPDATE CORE.DAG SET CATALOGED_AT")[0]
    assert stamp[1][0] == 1 - catalog.CATALOG_HOURS   # tried again within the hour, rows untouched


def test_a_task_list_change_rebuilds_an_unchanged_file():
    text = Airflow().source
    one = [{"task_id": "t", "operator_name": "X", "downstream_task_ids": []}]
    two = one + [{"task_id": "load_new_table", "operator_name": "X", "downstream_task_ids": []}]
    assert catalog.source_hash(text, one) != catalog.source_hash(text, two)
    db = Db()
    out = catalog.catalogue_dag(db, Airflow(tasks=two), "e", {"dag_id": "d", "fileloc": "/dags/d.py",
                                                             "source_hash": catalog.source_hash(text, one)})
    assert out["changed"] is True and out["tasks"] == 2


def test_a_poll_spends_at_most_its_budget_on_the_catalogue(monkeypatch):
    ticks = iter(range(0, 1000, 15))
    monkeypatch.setattr(catalog.time, "monotonic", lambda: next(ticks))
    db = Db({"FROM CORE.DAG\n": [{"dag_id": f"d{i}", "fileloc": f"/dags/d{i}.py", "source_hash": None} for i in range(10)]})
    out = catalog.refresh(db, Airflow(), "e", profiles=False, budget_seconds=20)
    assert 1 <= len(out["changed"]) < 10   # the rest stay due for the next poll


def test_a_stage_named_by_a_variable_is_not_recorded():
    reads, writes = catalog.object_refs(["COPY INTO @__VAR__/out FROM CORE.X"])
    assert "@" not in writes and reads == ["CORE.X"]


def test_writers_match_whatever_database_part_a_task_writes():
    assert pipeline.table_key("PROD_DB.CORE.ORDERS") == pipeline.table_key("core.orders") == "CORE.ORDERS"
    view = (ROOT / "snowflake" / "views" / "resolution_graph.sql").read_text()
    assert "'SAME_TABLE'" in view


# ---------------------------------------------------------------- capture


def test_a_long_traceback_never_pushes_out_the_statement():
    text = "Running statement: SELECT SUM(AMOUNT_USD) FROM CORE.LAB_ORDERS_CLEAN\n" + \
        "Traceback (most recent call last):\n" + "  File x, line 1\n" * 600 + "ProgrammingError: invalid identifier"
    out = excerpt(text)
    assert out.startswith("Running statement: SELECT SUM(AMOUNT_USD)") and out.endswith("invalid identifier")
    assert len(out) <= 4000 + 1


def test_an_incident_never_resolves_before_it_opened():
    s = oi.store_with_dag()
    now = oi.NOW
    inc.process(s, [], [oi.task(ended=now - timedelta(minutes=10))], s.settings(), now)
    iid = next(iter(s.incidents))
    inc.process(s, [], [oi.task(run="r2", state="success", ended=now - timedelta(minutes=5))], s.settings(),
                now + timedelta(minutes=1))
    row = s.incidents[iid]
    assert row["status"] == "RESOLVED" and row["resolved_at"] >= row["opened_at"]


def test_live_fixes_are_re_signed_for_todays_matching():
    from services.resolve import rules

    excerpt_text = ("snowflake.connector.errors.ProgrammingError: 002003 (42S02): Object 'DB.CORE.X' does not exist or "
                    "not authorized. Your primary role SYSADMIN or one of your secondary roles must have at least one "
                    "privilege granted on TABLE DB.CORE.X.")
    db = Db({"FROM CORE.KNOWN_ISSUE K LEFT JOIN CORE.INCIDENT": [{
        "issue_id": "k1", "dag_id": "d", "task_id": "t", "exception_class": "ProgrammingError", "error_signature": "old",
        "match_text": "dag d | task t | programmingerror | object <str> does not exist or not authorized. your primary "
                      "role sysadmin or one of your secondary roles must have at least one privilege granted on table "
                      "db.core.x.", "error_excerpt": excerpt_text}]})
    assert publish.refresh_signatures(db) == 1
    sig, text, _ = db.statements("UPDATE CORE.KNOWN_ISSUE SET ERROR_SIGNATURE")[0][1]
    assert sig == rules.error_signature("d", "t", "ProgrammingError", excerpt_text)
    assert "primary role" not in text and text.endswith("not authorized.")


# ---------------------------------------------------------------- safe actions


def test_dedup_keeps_the_table_and_a_repeatable_row():
    sql = actions.dedup_sql({"table": "DB.S.T", "key_columns": ["ID"], "keep_latest_by": "LOADED_AT",
                             "order_columns": ["AMOUNT", "LOADED_AT"]})
    assert sql.startswith("INSERT OVERWRITE INTO DB.S.T SELECT * FROM DB.S.T")
    assert "PARTITION BY ID ORDER BY LOADED_AT DESC NULLS LAST, AMOUNT" in sql
    db = Db({"INFORMATION_SCHEMA.COLUMNS": [{"column_name": "ID", "data_type": "NUMBER"},
                                            {"column_name": "AMOUNT", "data_type": "NUMBER"}]})
    clean = actions.check_step(db, "DEDUP_ON_KEY", {"table": "DB.S.T", "key_columns": ["ID"]}, "DB", ["DB.S.*"])
    assert clean["order_columns"] == ["AMOUNT"]


def test_add_column_finishes_a_fill_an_earlier_attempt_left_undone():
    cols = [{"column_name": "AMOUNT", "data_type": "NUMBER"}, {"column_name": "AMOUNT_USD", "data_type": "NUMBER"}]
    given = {"table": "DB.S.T", "column": "AMOUNT_USD", "data_type": "NUMBER(12,2)", "fill_from": "AMOUNT"}
    pending = Db({"INFORMATION_SCHEMA.COLUMNS": cols, "IS NULL AND AMOUNT IS NOT NULL": [{"n": 3}]})
    assert actions.check_step(pending, "ADD_COLUMN", given, "DB", ["DB.S.*"], running=True)["exists"] is True
    with pytest.raises(ValueError, match="already exists"):   # a proposal never targets an existing column
        actions.check_step(pending, "ADD_COLUMN", given, "DB", ["DB.S.*"])
    done = Db({"INFORMATION_SCHEMA.COLUMNS": cols, "IS NULL AND AMOUNT IS NOT NULL": [{"n": 0}]})
    with pytest.raises(ValueError, match="already exists"):   # nothing left to fill: the fix does not repair this
        actions.check_step(done, "ADD_COLUMN", given, "DB", ["DB.S.*"], running=True)


# ---------------------------------------------------------------- tickets


def test_a_person_resolving_a_reopened_incident_is_not_an_automatic_fix():
    s = oi.store_with_dag()
    iid = oi.opened_incident(s)
    s.incidents[iid].update({"jira_key": "DATA-4", "status": "RESOLVED", "resolution": "manual fix"})
    s.event(iid, "auto_fixed", "system", {})
    s.event(iid, "reopened", "system", {})
    jira = oi.FakeJira()
    tickets.resolved(s, s.incidents[iid], jira, {"transition_on_resolve": False, "close_auto_fixed": True,
                                                  "done_status": "Done"})
    assert not jira.moved
