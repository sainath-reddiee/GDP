"""Push-first capture: the listener sends the traceback and the SQL, short pushed errors are upgraded from the log and
re-parsed while their failure waits, claims wait briefly for a real error, a pushed failure wakes the resolver, and
polling slows to a safety interval outside the DAGs' run windows."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "airflow_plugins" / "gdp_listener"))

from gdp_listener import core as plugin  # noqa: E402
from services.ops import lease, schedule, store  # noqa: E402
from services.ops.detect import next_fire, prev_fire  # noqa: E402
from services.ops.normalize import excerpt, push_row  # noqa: E402

UTC = timezone.utc


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Recorder:
    """A Db that answers by SQL fragment and records every statement."""

    def __init__(self, answers=None, count=1):
        self.answers, self.count, self.sent = dict(answers or {}), count, []

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
        return self.count(sql, params) if callable(self.count) else self.count


# ---------------------------------------------------------------- the listener plugin


def _raise():
    def inner():
        raise ValueError("Currency code 'XYZ' is not in the reference list")

    try:
        inner()
    except ValueError as exc:
        return exc


def test_plugin_sends_the_traceback_and_the_rendered_sql():
    ti = Obj(dag_id="d", run_id="r", task_id="t", map_index=-1, try_number=1, start_date=None, end_date=None,
             duration=None, hostname="h", task=Obj(sql=["SELECT 1 FROM CORE.A", "INSERT INTO CORE.B SELECT 2"]))
    payload = plugin.task_payload(ti, "failed", _raise())
    assert payload["error"].startswith("Traceback (most recent call last)")
    assert payload["error"].rstrip().endswith("ValueError: Currency code 'XYZ' is not in the reference list")
    assert payload["sql"] == "SELECT 1 FROM CORE.A;\nINSERT INTO CORE.B SELECT 2"


def test_plugin_skips_sql_files_and_non_sql_tasks():
    assert plugin.task_sql(Obj(sql="queries/load.sql")) is None
    assert plugin.task_sql(Obj()) is None
    assert plugin.task_sql(Obj(sql="SELECT " + "x" * 5000)).__len__() == plugin.SQL_MAX
    assert plugin.error_text(ValueError("no traceback")) == "ValueError: no traceback"
    assert plugin.error_text("plain") == "plain"


def test_plugin_trims_both_fields_when_the_body_is_too_big():
    raw = plugin.encode({"kind": "task_instance", "error": "e" * 70000, "sql": "s" * 3000})
    assert len(raw) < plugin.MAX_BODY_BYTES


# ---------------------------------------------------------------- what the platform stores


def test_pushed_sql_is_kept_in_front_of_the_error():
    body = {"kind": "task_instance", "dag_id": "d", "run_id": "r", "task_id": "t", "try_number": 1, "state": "failed",
            "error": "snowflake.connector.errors.ProgrammingError: 000904 (42000): invalid identifier 'AMOUNT_USD'",
            "sql": "SELECT SUM(AMOUNT_USD)\n  FROM CORE.LAB_ORDERS_CLEAN"}
    _, row = push_row("e", body)
    assert row["excerpt_source"] == "PUSH"
    assert row["error_excerpt"].splitlines()[0] == "Running statement: SELECT SUM(AMOUNT_USD) FROM CORE.LAB_ORDERS_CLEAN"
    assert "invalid identifier 'AMOUNT_USD'" in row["error_excerpt"]


def test_excerpt_keeps_the_statement_without_a_traceback():
    text = "INFO Running statement: SELECT 1 FROM CORE.X\nERROR Task failed: ProgrammingError: boom"
    assert excerpt(text).splitlines() == ["INFO Running statement: SELECT 1 FROM CORE.X",
                                          "ERROR Task failed: ProgrammingError: boom"]


def test_short_pushed_errors_are_read_again_from_the_log(monkeypatch):
    db = Recorder({"FROM CORE.TASK_RUN": [{"dag_id": "d", "run_id": "r", "task_id": "t", "map_index": -1, "try_number": 1}]})

    class Client:
        def task_log(self, *a, **kw):
            return {"text": "Traceback (most recent call last):\n  File x\nValueError: real error"}

    seen = []
    monkeypatch.setattr("services.resolve.engine.reparse", lambda db, env, t, text: seen.append((env, t["task_id"], text)))
    assert store.fill_error_excerpts(db, Client(), "e") == 1
    select, update = db.sent[0][0], db.sent[1][0]
    assert "EXCERPT_SOURCE = 'PUSH'" in select and "POSITION('Traceback" in select
    assert "EXCERPT_SOURCE = 'LOG'" in update
    assert seen and seen[0][2].endswith("ValueError: real error")


def test_reparse_updates_only_a_waiting_failure():
    from services.resolve import engine

    db = Recorder(count=1)
    text = "Traceback (most recent call last):\n  File x\nsnowflake.connector.errors.ProgrammingError: 002003: Object 'X' does not exist"
    assert engine.reparse(db, "e", {"dag_id": "d", "run_id": "r", "task_id": "t", "map_index": -1, "try_number": 1}, text)
    update = db.sent[0]
    assert "WHERE DEDUP_KEY = %s AND STATUS = 'PENDING'" in update[0] and update[1][0] == "ProgrammingError"
    assert "UPDATE CORE.INCIDENT SET ERROR_EXCERPT" in db.sent[1][0]
    assert not engine.reparse(Recorder(), "e", {"dag_id": "d"}, "(the log has no error lines)")


def test_claim_waits_for_an_error_class():
    from services.resolve import engine

    db = Recorder()
    engine.claim(db, "w", 5)
    sql = " ".join(db.sent[0][0].split())
    assert "(EXCEPTION_CLASS IS NOT NULL OR CREATED_AT < DATEADD(second, -120, CURRENT_TIMESTAMP()))" in sql


def test_dag_list_is_read_at_most_every_30_minutes():
    assert store.dag_list_due(Recorder({"FROM CORE.DAG": [{"age_min": 5, "new_dags": 0}]}), "e") is False
    assert store.dag_list_due(Recorder({"FROM CORE.DAG": [{"age_min": 31, "new_dags": 0}]}), "e") is True
    assert store.dag_list_due(Recorder({"FROM CORE.DAG": [{"age_min": 5, "new_dags": 1}]}), "e") is True
    assert store.dag_list_due(Recorder({"FROM CORE.DAG": [{"age_min": None}]}), "e") is True


# ---------------------------------------------------------------- waking the resolver


def test_wake_is_taken_once():
    flags = {}

    class Db:
        def execute(self, sql, params=()):
            if "SET CURSOR_VALUE = 'WAKE'" in sql:
                flags[params[0]] = "WAKE"

        def execute_count(self, sql, params=()):
            if flags.get(params[0]) == "WAKE":
                flags[params[0]] = None
                return 1
            return 0

    db = Db()
    assert lease.take_wake(db, "resolve") is False
    lease.wake(db, "resolve")
    assert lease.take_wake(db, "resolve") is True and lease.take_wake(db, "resolve") is False


def test_worker_runs_resolve_when_woken(monkeypatch):
    from app import worker

    ran = []
    w = worker.Worker(db_factory=lambda: Recorder())
    monkeypatch.setattr(worker, "due_envs", lambda db, state=None: [])
    monkeypatch.setattr(worker.lease, "take_wake", lambda db, job: job == "resolve")
    monkeypatch.setattr(w, "_safe", lambda db, job, fn: ran.append(job))
    for name in worker.INTERVALS:
        w._last[name] = 1e18   # nothing is due by its interval
    w.once()
    assert ran == ["detect", "resolve"]


# ---------------------------------------------------------------- schedule-aware polling


def at(h, m=0, day=16):
    return datetime(2026, 10, day, h, m, tzinfo=UTC)


def test_next_fire_matches_prev_fire():
    assert next_fire("0 2 * * *", at(1, 30)) == at(2)
    assert next_fire("0 2 * * *", at(2)) == at(2, day=17)
    assert next_fire("*/15 * * * *", at(2, 1)) == at(2, 15)
    assert prev_fire("0 2 * * *", at(3)) == at(2)


def test_run_windows():
    daily = {"schedule": "0 2 * * *", "max_duration_min": 45}
    assert schedule.in_run_window([daily], at(1, 50))            # 10 minutes before it fires
    assert schedule.in_run_window([daily], at(2, 55))            # 45 + 15 minutes after
    assert not schedule.in_run_window([daily], at(3, 5))
    assert not schedule.in_run_window([daily], at(12))
    assert not schedule.in_run_window([{**daily, "is_paused": True}], at(1, 50))
    by_p95 = {"schedule": "0 2 * * *", "p95_s": 7200}
    assert schedule.in_run_window([by_p95], at(4, 5))
    sla = {"schedule": "Dataset", "expected_by_cron": "0 6 * * *", "max_duration_min": 30}
    assert schedule.in_run_window([sla], at(5, 20)) and schedule.in_run_window([sla], at(6, 10))
    assert not schedule.in_run_window([sla], at(4))
    assert schedule.cron_of("None") is None and schedule.cron_of("@daily") == "@daily"


def test_effective_interval():
    assert schedule.effective_interval(60, push_live=False, in_window=False) == 60
    assert schedule.effective_interval(60, push_live=True, in_window=True) == 60
    assert schedule.effective_interval(60, push_live=True, in_window=False) == schedule.SAFETY_POLL_SECONDS
    assert schedule.effective_interval(5, push_live=False, in_window=False) == schedule.MIN_POLL_SECONDS


def test_due_envs_slows_down_pushing_environments():
    from app import worker

    rows = [{"env_id": "push", "poll_seconds": 60, "push_enabled": True, "age_s": 120},
            {"env_id": "poll", "poll_seconds": 60, "push_enabled": False, "age_s": 120},
            {"env_id": "new", "poll_seconds": 60, "push_enabled": True, "age_s": None}]
    db = Recorder({"FROM CORE.AIRFLOW_ENV": rows})
    quiet = {"push_live": True, "in_window": False}
    assert [e["env_id"] for e in worker.due_envs(db, lambda env_id: quiet)] == ["poll", "new"]
    busy = {"push_live": True, "in_window": True}
    assert [e["env_id"] for e in worker.due_envs(db, lambda env_id: busy)] == ["push", "poll", "new"]
    assert [e["env_id"] for e in worker.due_envs(db)] == ["push", "poll", "new"]


@pytest.mark.parametrize("enabled,last,expected", [(True, "2026-10-16", True), (True, None, False), (False, "x", False)])
def test_push_counts_only_while_events_arrive(enabled, last, expected):
    db = Recorder({"FROM CORE.AIRFLOW_ENV": [{"push_enabled": enabled, "last_push_at": last}],
                   "FROM CORE.DAG D": [{"schedule": "0 2 * * *"}]})
    assert schedule.env_state(db, "e", at(12))["push_live"] is expected


# ---------------------------------------------------------------- matching that does not depend on the role


def test_signature_ignores_the_role_named_in_snowflake_privilege_hints():
    from services.ops.detect import signature

    base = ("002003 (42S02): SQL compilation error: Object 'DB.CORE.LAB_CUSTOMER_DIM' does not exist or not authorized. "
            "Your primary role {} or one of your secondary roles must have at least one privilege granted on TABLE "
            "DB.CORE.LAB_CUSTOMER_DIM.")
    assert signature(base.format("SYSADMIN")) == signature(base.format("DATA_ENGINEER"))
    assert signature(base.format("SYSADMIN")).endswith("does not exist or not authorized.")


def test_a_step_the_agent_already_ran_reports_its_own_output():
    from services.resolve import fixes

    class Db:
        def query(self, sql, params=()):
            if "SELECT STEP_NO, STATUS" in sql:
                return [{"step_no": 1, "status": "FAILED"}]
            if "SELECT OUTPUT" in sql:
                return [{"output": '"ValueError: DB.CORE.LAB_ORDERS_CLEAN is outside the agent fix scope"'}]
            return []

    out = fixes.run_step(Db(), "DB", "r1", "f1", [{"step_no": 1, "action": "DB.CORE.FIX_ADD_COLUMN"}], 1)
    assert out["status"] == "FAILED" and "outside the agent fix scope" in out["output"]
