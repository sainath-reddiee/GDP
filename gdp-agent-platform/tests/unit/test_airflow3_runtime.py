"""Airflow 3 on MWAA, as a client runs it (REST v2): chained exceptions in error_detail read in Python's order, v2
task links, a clear that matched nothing, the poll's resume point without updated_at, a throttled task read part way,
and a log that is not in CloudWatch yet. Airflow is a fake."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from services.ops import store  # noqa: E402
from services.ops.mwaa import Mwaa, MwaaError, error_detail_text  # noqa: E402
from services.resolve.rules import parse_failure  # noqa: E402


def test_a_chained_error_reads_cause_first_and_the_raised_exception_last():
    # structlog's order: the raised exception first, then its cause
    details = [{"exc_type": "AirflowException", "exc_value": "Task failed", "is_cause": False,
                "frames": [{"filename": "/dags/a.py", "lineno": 9, "name": "run"}]},
               {"exc_type": "ProgrammingError", "exc_value": "002003: Object 'ORDERS' does not exist", "is_cause": True,
                "frames": [{"filename": "/dags/a.py", "lineno": 5, "name": "load"}]}]
    lines = error_detail_text(details)
    text = "\n".join(lines)
    assert text.index("ProgrammingError") < text.index("The above exception was the direct cause") < text.index("AirflowException")
    assert lines[-1] == "AirflowException: Task failed"   # what Python prints last, as in v1 logs and pushed errors
    context = [{**details[0]}, {**details[1], "is_cause": False}]
    assert "During handling of the above exception" in "\n".join(error_detail_text(context))
    assert parse_failure("\n".join(lines))["exception_class"]


class Fake:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def invoke(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        return self.answer


def test_v2_task_links_are_read_from_extra_links():
    mw = Mwaa("m", "us-east-1")
    mw.invoke = Fake({"extra_links": {"External DAG": "https://x/dags/upstream/grid"}, "total_entries": 1}).invoke
    assert mw.task_links("d", "r", "t", 0) == {"External DAG": "https://x/dags/upstream/grid"}
    mw.invoke = Fake({"External DAG": "https://x/dags/upstream/grid"}).invoke   # v1
    assert mw.task_links("d", "r", "t") == {"External DAG": "https://x/dags/upstream/grid"}


class BridgeDb:
    def __init__(self):
        self.updates = []

    def query(self, sql, params=()):
        if "FROM CORE.FAILURE_EVENT F JOIN CORE.AIRFLOW_ENV" in sql:
            return [{"failure_id": "f1", "env_id": "prod", "dag_id": "d", "task_id": "t", "run_id": "r", "incident_id": None,
                     "kind": "MWAA", "mwaa_env": "m", "region": "us-east-1", "airflow_url": None}]
        return []

    def execute(self, sql, params=()):
        if "SET CLEAR_STATE = %s" in sql:
            self.updates.append(params)

    def execute_count(self, sql, params=()):
        return 1


@pytest.mark.parametrize("answer, state", [({"task_instances": [], "total_entries": 0}, "FAILED"),
                                           ({"task_instances": [{"task_id": "t"}]}, "DONE")])
def test_a_clear_that_matched_nothing_is_not_a_retry(answer, state):
    from services.resolve.bridge import clear_requested

    class Client:
        def clear_task_instances(self, *a, **k):
            return answer

    db = BridgeDb()
    counts = clear_requested(db, mwaa_factory=lambda env: Client())
    assert db.updates[0][0] == state
    assert counts == ({"cleared": 0, "failed": 1} if state == "FAILED" else {"cleared": 1, "failed": 0})


def test_the_resume_point_works_without_updated_at():
    runs = [{"dag_id": "d", "dag_run_id": "r1", "start_date": "2026-10-22T10:00:00Z", "end_date": "2026-10-22T10:05:00Z",
             "state": "success"}]   # an Airflow 3 run: no updated_at
    at = lambda v: datetime.fromisoformat(v)  # noqa: E731
    assert at(store.last_fetched_at(runs, "updated_at")) == datetime(2026, 10, 22, 10, 5, tzinfo=timezone.utc)
    assert at(store.last_fetched_at([{"updated_at": "2026-10-22T11:00:00Z"}], "updated_at")) == datetime(2026, 10, 22, 11, tzinfo=timezone.utc)


def test_a_throttled_task_read_keeps_what_was_read():
    class Mw:
        def __init__(self, fail_at):
            self.fail_at, self.n = fail_at, 0

        def task_instances(self, dag_id, run_id):
            self.n += 1
            if self.n == self.fail_at:
                raise MwaaError("throttled", "slow down")
            return [{"dag_id": dag_id, "dag_run_id": run_id, "task_id": "t", "try_number": 1, "state": "failed"}]

    runs = [{"dag_id": "d", "run_id": f"r{i}"} for i in range(3)]
    tasks, unread = store._fetch_tasks(Mw(fail_at=2), "prod", runs)
    assert len(tasks) == 1 and [r["run_id"] for r in unread] == ["r1", "r2"]
    with pytest.raises(MwaaError):
        store._fetch_tasks(Mw(fail_at=1), "prod", runs)   # nothing read at all: the poll reports the error


def test_a_log_not_in_cloudwatch_yet_is_read_again_later():
    now = datetime.now(timezone.utc)

    class Db:
        def __init__(self, ended):
            self.ended, self.written = ended, []

        def query(self, sql, params=()):
            if "FROM CORE.TASK_RUN" in sql:
                return [{"dag_id": "d", "run_id": "r", "task_id": "t", "map_index": -1, "try_number": 1,
                         "started_at": None, "ended_at": self.ended.isoformat()}]
            return []

        def execute(self, sql, params=()):
            if "UPDATE CORE.TASK_RUN" in sql:
                self.written.append(params[0])

        def execute_count(self, sql, params=()):
            return 0

    class Mw:
        def task_log(self, *a, **k):
            return {"text": ""}

    fresh = Db(now - timedelta(minutes=2))
    assert store.fill_error_excerpts(fresh, Mw(), "prod") == 0 and fresh.written == []
    old = Db(now - timedelta(hours=1))
    store.fill_error_excerpts(old, Mw(), "prod")
    assert old.written == ["(the log has no error lines)"]
