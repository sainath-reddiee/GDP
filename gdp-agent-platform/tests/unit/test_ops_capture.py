"""Ops capture (PR O1): Airflow v1 and v2 normalizers, redaction, signed push (valid, invalid, skew, replay), job
leases (single holder, takeover), the poll cursor overlap, the MWAA adapter, DAG to code parsing, the listener
plugin's pure parts and the governance mapping of the new routes."""

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "airflow_plugins" / "gdp_listener"))

from gdp_listener import core as plugin  # noqa: E402
from services.governance.policy import (  # noqa: E402
    ADDED_PRIVILEGES, SYSTEM_ROLES, SYSTEM_VERSION, decide, effective_privileges, privilege_for, read_only,
)
from services.ops import code_map, lease, signing  # noqa: E402
from services.ops.mwaa import Mwaa, MwaaError, api_version_for, keep_tail, log_text, supports_updated_at  # noqa: E402
from services.ops.normalize import dag_row, dag_run_row, excerpt, newer, push_row, task_row, ts  # noqa: E402
from services.ops.redact import redact, redact_count  # noqa: E402
from services.ops.store import dedupe, poll_env, poll_since  # noqa: E402

# ---------------------------------------------------------------- recorded-style payloads

V1_DAG = {"dag_id": "orders_daily", "fileloc": "/usr/local/airflow/dags/sales/orders.py", "owners": ["data-eng"],
          "tags": [{"name": "sales"}, {"name": "p1"}], "is_paused": False, "is_active": True,
          "schedule_interval": {"__type": "CronExpression", "value": "0 2 * * *"},
          "timetable_description": "At 02:00", "description": None}
V2_DAG = {"dag_id": "orders_daily", "dag_display_name": "orders_daily", "fileloc": "/usr/local/airflow/dags/sales/orders.py",
          "owners": ["data-eng"], "tags": [{"name": "sales", "dag_id": "orders_daily"}], "is_paused": True,
          "is_stale": True, "timetable_summary": "0 2 * * *", "timetable_description": "At 02:00"}
V1_RUN = {"dag_id": "orders_daily", "dag_run_id": "scheduled__2024-05-01T02:00:00+00:00",
          "execution_date": "2024-05-01T02:00:00+00:00", "logical_date": "2024-05-01T02:00:00+00:00",
          "start_date": "2024-05-02T02:00:05.123456+00:00", "end_date": "2024-05-02T02:10:05.123456+00:00",
          "state": "failed", "run_type": "scheduled", "external_trigger": False, "note": None,
          "last_scheduling_decision": "2024-05-02T02:10:06+00:00"}
V2_RUN = {"dag_id": "orders_daily", "dag_run_id": "manual__2025-06-01T10:00:00+00:00", "logical_date": "2025-06-01T10:00:00Z",
          "queued_at": "2025-06-01T10:00:00Z", "start_date": "2025-06-01T10:00:01Z", "end_date": None,
          "run_after": "2025-06-01T10:00:00Z", "state": "running", "run_type": "manual", "triggered_by": "ui"}
V1_TASK = {"task_id": "load", "dag_id": "orders_daily", "dag_run_id": "scheduled__2024-05-01T02:00:00+00:00",
           "execution_date": "2024-05-01T02:00:00+00:00", "start_date": "2024-05-02T02:01:00+00:00",
           "end_date": "2024-05-02T02:03:30+00:00", "duration": 150.0, "state": "failed", "try_number": 2, "map_index": -1,
           "max_tries": 1, "hostname": "ip-10-0-0-1.ec2.internal", "operator": "BashOperator", "pool": "default_pool"}
V2_TASK = {"id": "0190", "task_id": "load", "dag_id": "orders_daily", "dag_run_id": "manual__2025-06-01T10:00:00+00:00",
           "map_index": 3, "logical_date": "2025-06-01T10:00:00Z", "start_date": "2025-06-01T10:00:02Z", "end_date": None,
           "duration": None, "state": "running", "try_number": 1, "operator_name": "DbtRunLocalOperator", "hostname": ""}


def test_normalize_v1_and_v2_dags():
    one, two = dag_row("prod", V1_DAG), dag_row("prod", V2_DAG)
    assert one["schedule"] == "0 2 * * *" and one["tags"] == ["sales", "p1"] and one["is_active"] is True
    assert two["schedule"] == "0 2 * * *" and two["tags"] == ["sales"] and two["is_active"] is False and two["is_paused"] is True
    assert one["owners"] == ["data-eng"] and one["fileloc"].endswith("orders.py")
    assert dag_row("e", {"dag_id": "x", "schedule_interval": {"__type": "TimeDelta", "days": 1, "seconds": 0}})["schedule"] == "every 86400s"


def test_normalize_v1_and_v2_runs():
    one = dag_run_row("prod", V1_RUN)
    assert one["run_id"] == V1_RUN["dag_run_id"] and one["state"] == "failed" and one["duration_s"] == 600.0
    assert one["logical_date"] == "2024-05-01T02:00:00.000000+00:00" and one["external_trigger"] is False
    assert one["updated_at"] == "2024-05-02T02:10:06.000000+00:00"   # the newest of its timestamps
    two = dag_run_row("prod", V2_RUN)
    assert two["state"] == "running" and two["ended_at"] is None and two["duration_s"] is None
    assert two["external_trigger"] is True and two["updated_at"] == "2025-06-01T10:00:01.000000+00:00"


def test_normalize_v1_and_v2_tasks():
    one = task_row("prod", V1_TASK)
    assert (one["map_index"], one["try_number"], one["duration_s"], one["operator"]) == (-1, 2, 150.0, "BashOperator")
    assert one["log_ref"] == "orders_daily/scheduled__2024-05-01T02:00:00+00:00/load/2/-1"
    two = task_row("prod", V2_TASK)
    assert two["map_index"] == 3 and two["operator"] == "DbtRunLocalOperator" and two["hostname"] is None
    assert ts("2025-06-01 10:00:02") == "2025-06-01T10:00:02.000000+00:00" and ts("not a time") is None


def test_latest_state_wins_and_dedupe():
    running = {"state": "running", "updated_at": "2024-05-02T02:00:00+00:00"}
    done = {"state": "failed", "updated_at": "2024-05-02T02:10:00+00:00"}
    assert newer(running, done) and not newer(done, running)            # out of order: the older push loses
    assert newer({"state": "running", "updated_at": done["updated_at"]}, done)   # tie: finished wins
    assert not newer(done, {"state": "running", "updated_at": done["updated_at"]})
    rows = dedupe([{"env_id": "e", "dag_id": "d", "run_id": "r", **done}, {"env_id": "e", "dag_id": "d", "run_id": "r", **running}],
                  ("env_id", "dag_id", "run_id"))
    assert len(rows) == 1 and rows[0]["state"] == "failed"


def test_push_rows():
    kind, row = push_row("prod", {"kind": "task_instance", "dag_id": "d", "run_id": "r", "task_id": "t", "try_number": 1,
                                  "state": "failed", "start": "2024-01-01T00:00:00Z", "end": "2024-01-01T00:00:10Z",
                                  "error": "Traceback (most recent call last):\n  x\nValueError: password=hunter2",
                                  "updated_at": "2024-01-01T00:00:11Z"})
    assert kind == "task_instance" and row["source"] == "PUSH" and row["duration_s"] == 10.0
    assert "hunter2" not in row["error_excerpt"] and row["updated_at"].startswith("2024-01-01T00:00:11")
    with pytest.raises(ValueError):
        push_row("prod", {"kind": "nope", "dag_id": "d", "run_id": "r"})
    with pytest.raises(ValueError):
        push_row("prod", {"kind": "dag_run", "dag_id": "d"})


# ---------------------------------------------------------------- redaction

def test_redaction_of_secrets_and_pii():
    samples = {
        "postgres://bob:s3cr3t@db.internal:5432/x": "s3cr3t",
        "key AKIAABCDEFGHIJKLMNOP used": "AKIAABCDEFGHIJKLMNOP",
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY": "wJalrXUtnFEMI",
        '{"password": "hunter2"}': "hunter2",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz": "abcdefghijklmnop",
        "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlX3g": "eyJhbGciOiJIUzI1NiJ9",
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----": "MIIEvQIBADANBg",
        "snowflake_pat=ver:1-hint:123-ETMsDgAAAZ": "ETMsDgAAAZ",
        "contact jane.doe@example.com": "jane.doe@example.com",
        "call +1 415 555 0100 now": "555 0100",
        "or (415) 555-0100": "555-0100",
    }
    for text, secret in samples.items():
        assert secret not in redact(text), text
    harmless = "[2024-05-01 12:00:00,123] {taskinstance.py:1234} INFO - run_id=scheduled__2024-05-01T00:00:00+00:00 rows=1234567"
    assert redact_count(harmless) == (harmless, 0)


def test_error_excerpt_keeps_the_traceback():
    log = "line\n" * 50 + "Traceback (most recent call last):\n  File x\nsnowflake.Error: 002003: Object 'T' does not exist\n"
    found = excerpt(log)
    assert found.startswith("Traceback") and "002003" in found
    assert len(excerpt("Error " + "x" * 10000)) == 4000


# ---------------------------------------------------------------- signing, ingest

SECRET = "a" * 64


def test_signature_valid_invalid_and_skew():
    raw = b'{"kind":"dag_run"}'
    now = 1_700_000_000
    sig = signing.sign(SECRET, str(now), raw)
    assert signing.verify(SECRET, str(now), raw, sig)
    assert not signing.verify(SECRET, str(now), raw + b" ", sig)
    assert not signing.verify("b" * 64, str(now), raw, sig)
    assert signing.check_headers("prod", str(now), "evt-12345678", sig, now=now + 299) == (True, "")
    assert signing.check_headers("prod", str(now), "evt-12345678", sig, now=now + 301)[0] is False
    assert signing.check_headers("prod", str(now), "short", sig, now=now)[1] == "invalid event id"
    assert signing.check_headers(None, str(now), "evt-12345678", sig, now=now)[1].startswith("missing")
    # the plugin's copy signs exactly like the platform
    assert plugin.sign(SECRET, str(now), raw) == sig
    headers = plugin.headers("prod", SECRET, raw, now=now, event_id="evt-12345678")
    assert headers["X-GDP-Signature"] == sig and set(signing.HEADER_NAMES) <= set(headers)


class IngestDb:
    def __init__(self):
        self.events, self.statements = set(), []

    def execute_count(self, sql, params=()):
        if "INTO OPS.EVENT" in sql:
            if params[0] in self.events:
                return 0
            self.events.add(params[0])
            return 1
        return 1

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split())[:60], params))

    def query(self, sql, params=()):
        return []


@pytest.fixture
def ingest_client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main
    import app.ops_api as ops_api

    db = IngestDb()
    monkeypatch.setattr(ops_api, "system_db", lambda: db)
    monkeypatch.setattr(ops_api, "_push_secret", lambda db, env_id: (SECRET, True) if env_id == "prod" else (None, False))
    return TestClient(main.app), db


def _signed(body, env="prod", event_id=None, stamp=None, secret=SECRET):
    raw = json.dumps(body).encode()
    stamp = str(int(time.time()) if stamp is None else stamp)
    return raw, {"X-GDP-Env": env, "X-GDP-Timestamp": stamp, "X-GDP-Event-Id": event_id or f"evt-{time.time_ns()}",
                 "X-GDP-Signature": signing.sign(secret, stamp, raw), "Content-Type": "application/json"}


def test_ingest_without_a_session_and_replay(ingest_client):
    client, db = ingest_client
    body = {"kind": "task_instance", "dag_id": "orders_daily", "run_id": "r1", "task_id": "load", "try_number": 1,
            "state": "failed", "error": "boom", "updated_at": "2024-01-01T00:00:00Z"}
    raw, headers = _signed(body, event_id="evt-00000001")
    res = client.post("/api/ops/ingest", content=raw, headers=headers)
    assert res.status_code == 200 and res.json()["kind"] == "task_instance"
    assert any("MERGE INTO OPS.TASK_RUN" in s for s, _ in db.statements)
    again = client.post("/api/ops/ingest", content=raw, headers=headers)
    assert again.status_code == 409 and "Replayed" in again.json()["detail"]


def test_ingest_rejects_bad_signature_skew_unknown_env_and_size(ingest_client):
    client, _ = ingest_client
    body = {"kind": "dag_run", "dag_id": "d", "run_id": "r", "state": "success"}
    raw, headers = _signed(body, secret="c" * 64)
    assert client.post("/api/ops/ingest", content=raw, headers=headers).status_code == 401
    raw, headers = _signed(body, stamp=int(time.time()) - 600)
    assert client.post("/api/ops/ingest", content=raw, headers=headers).status_code == 401
    raw, headers = _signed(body, env="other")
    assert client.post("/api/ops/ingest", content=raw, headers=headers).status_code == 401
    raw, headers = _signed({**body, "pad": "x" * 70000})
    assert client.post("/api/ops/ingest", content=raw, headers=headers).status_code == 413
    raw, headers = _signed({"kind": "dag_run", "dag_id": "d"})
    assert client.post("/api/ops/ingest", content=raw, headers=headers).status_code == 400
    assert client.post("/api/ops/ingest", content=b"{}").status_code == 400


# ---------------------------------------------------------------- leases

class LeaseDb:
    """In-memory OPS.JOB_LEASE that understands the lease module's statements, with a controllable clock."""

    def __init__(self):
        self.rows, self.now = {}, 1000.0

    def execute(self, sql, params=()):
        if sql.startswith("MERGE INTO OPS.JOB_LEASE"):
            self.rows.setdefault(params[0], {"holder": None, "until": None})
        elif sql.startswith("UPDATE OPS.JOB_LEASE SET HOLDER = NULL"):
            cursor, job, holder = params
            row = self.rows.get(job)
            if row and row["holder"] == holder:
                row.update(holder=None, until=None)

    def execute_count(self, sql, params=()):
        if sql.startswith("UPDATE OPS.JOB_LEASE SET HOLDER = %s"):
            holder, seconds, job, same = params
            row = self.rows[job]
            if row["holder"] is None or row["holder"] == same or row["until"] is None or row["until"] < self.now:
                row.update(holder=holder, until=self.now + seconds)
                return 1
            return 0
        if sql.startswith("UPDATE OPS.JOB_LEASE SET LEASE_UNTIL"):
            seconds, job, holder = params
            row = self.rows.get(job)
            if row and row["holder"] == holder:
                row["until"] = self.now + seconds
                return 1
            return 0
        raise AssertionError(sql)


def test_lease_single_holder_and_takeover():
    db = LeaseDb()
    assert lease.acquire(db, "poll:prod", "w1")
    assert not lease.acquire(db, "poll:prod", "w2")       # held and not expired
    assert lease.acquire(db, "poll:prod", "w1")           # the holder may re-acquire (renew)
    db.now += 30
    assert lease.renew(db, "poll:prod", "w1") and not lease.renew(db, "poll:prod", "w2")
    db.now += 61                                          # w1 crashed: its lease ran out
    assert lease.acquire(db, "poll:prod", "w2")
    assert not lease.renew(db, "poll:prod", "w1")         # the old holder learns it lost the lease
    lease.release(db, "poll:prod", "w2")
    assert lease.acquire(db, "poll:prod", "w1")


def test_held_runs_once_and_releases():
    db = LeaseDb()
    with lease.held(db, "outbox", "w1", renew_every=60) as got:
        assert got
        with lease.held(db, "outbox", "w2", renew_every=60) as other:
            assert not other
    assert db.rows["outbox"]["holder"] is None


def test_manual_poll_and_worker_share_the_lease():
    from app import worker

    db = LeaseDb()
    db.query = lambda sql, params=(): [{"env_id": "prod", "mwaa_env": "m", "region": "us-east-1", "cursor_value": None}]
    calls = []
    assert lease.acquire(db, "poll:prod", "worker-a")      # the worker is polling
    assert worker.run_poll(db, "prod", holder="api-b", poller=lambda d, e: calls.append(e))["ran"] is False
    assert calls == []                                     # "poll now" never double-runs
    db.now += 120                                          # the worker died mid-poll
    result = worker.run_poll(db, "prod", holder="api-b", poller=lambda d, e: {"runs": 1})
    assert result == {"ran": True, "ok": True, "runs": 1} and db.rows["poll:prod"]["holder"] is None
    failed = worker.run_poll(db, "prod", holder="api-b", poller=lambda d, e: (_ for _ in ()).throw(MwaaError("credentials", "no creds")))
    assert failed == {"ran": True, "ok": False, "error": "no creds"}


# ---------------------------------------------------------------- cursor and polling

def test_poll_cursor_overlap_window():
    now = "2024-05-02T12:00:00+00:00"
    assert poll_since(None, now) == "2024-05-01T12:00:00.000000+00:00"           # first poll: a day back
    assert poll_since("2024-05-02T11:55:00+00:00", now) == "2024-05-02T11:40:00.000000+00:00"   # 15 minute overlap
    assert poll_since("2024-05-02T13:00:00+00:00", now) == "2024-05-02T12:00:00.000000+00:00"   # never in the future


class FakeClient:
    def __init__(self, version="2.8.1"):
        self.version_text, self.calls = version, []

    def invoke_rest_api(self, **request):
        self.calls.append(request)
        path = request["Path"]
        if path == "/version":
            return {"RestApiStatusCode": 200, "RestApiResponse": {"version": self.version_text}}
        if path == "/dags":
            return {"RestApiResponse": {"dags": [V1_DAG], "total_entries": 1}}
        if path == "/dags/~/dagRuns":
            return {"RestApiResponse": {"dag_runs": [V1_RUN], "total_entries": 1}}
        if path.endswith("/taskInstances"):
            return {"RestApiResponse": {"task_instances": [V1_TASK], "total_entries": 1}}
        raise AssertionError(path)


class PollDb:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split()), params))

    def execute_count(self, sql, params=()):
        self.execute(sql, params)
        return 1

    def query(self, sql, params=()):
        return []


def test_poll_env_upserts_and_stores_the_cursor():
    client, db = FakeClient(), PollDb()
    env = {"env_id": "prod", "mwaa_env": "m", "region": "us-east-1", "cursor_value": "2024-05-02T11:55:00+00:00"}
    summary = poll_env(db, env, client=Mwaa("m", "us-east-1", client=client), now="2024-05-02T12:00:00+00:00")
    assert summary["runs"] == 1 and summary["tasks"] == 1 and summary["complete"] and summary["api_version"] == "v1"
    run_call = next(c for c in client.calls if c["Path"] == "/dags/~/dagRuns")
    assert run_call["QueryParameters"]["updated_at_gte"] == "2024-05-02T11:40:00.000000+00:00"
    task_call = next(c for c in client.calls if c["Path"].endswith("/taskInstances"))
    assert "scheduled__2024-05-01T02%3A00%3A00%2B00%3A00" in task_call["Path"]     # run ids are URL-encoded
    update = next(p for s, p in db.statements if s.startswith("UPDATE OPS.AIRFLOW_ENV"))
    assert update[2] == "2024-05-02T12:00:00.000000+00:00" and update[0] == "v1"
    merged = [s for s, _ in db.statements if s.startswith("MERGE INTO OPS.")]
    assert any("OPS.DAG_RUN" in s for s in merged) and any("OPS.TASK_RUN" in s for s in merged)


def test_old_airflow_uses_start_date_filter():
    client = FakeClient("2.5.3")
    mw = Mwaa("m", "us-east-1", client=client)
    mw.dag_runs("~", "2024-01-01T00:00:00+00:00")
    assert "start_date_gte" in client.calls[-1]["QueryParameters"]
    assert supports_updated_at("2.6.0") and not supports_updated_at("2.5.3") and api_version_for("3.0.2") == "v2"


# ---------------------------------------------------------------- MWAA adapter

class Boom(Exception):
    def __init__(self, code, rest=None, http=None):
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": http}}
        if rest:
            self.response["RestApiStatusCode"] = rest


def test_backoff_on_throttling_then_success():
    sleeps, answers = [], [Boom("ThrottlingException", http=429), Boom("RestApiServerException", rest=503),
                           {"RestApiResponse": {"version": "3.0.1"}}]

    class Client:
        def invoke_rest_api(self, **request):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    mw = Mwaa("m", "us-east-1", client=Client(), sleep=sleeps.append)
    assert mw.version() == {"version": "3.0.1", "api_version": "v2"} and sleeps == [1.0, 2.0]


def test_errors_are_mapped():
    class NoCredentialsError(Exception):
        pass

    def failing(exc):
        class Client:
            def invoke_rest_api(self, **request):
                raise exc
        return Mwaa("m", "us-east-1", client=Client(), sleep=lambda s: None)

    for exc, kind in [(NoCredentialsError(), "credentials"), (Boom("AccessDeniedException"), "access_denied"),
                      (Boom("ResourceNotFoundException"), "not_found"), (Boom("ThrottlingException", http=429), "throttled"),
                      (Boom("RestApiClientException", rest=403), "access_denied")]:
        with pytest.raises(MwaaError) as caught:
            failing(exc).invoke("GET", "/version")
        assert caught.value.kind == kind, exc
    with pytest.raises(MwaaError) as caught:
        failing(Boom("AccessDeniedException")).invoke("GET", "/dags")
    assert "airflow:InvokeRestApi" in str(caught.value)


def test_task_log_continuation_and_cap():
    pages = [{"content": "a" * 100, "continuation_token": "t1"}, {"content": [{"timestamp": "T", "level": "error", "event": "boom"}],
                                                                   "continuation_token": "t2"},
             {"content": "", "continuation_token": "t2"}]
    seen = []

    class Client:
        def invoke_rest_api(self, **request):
            seen.append(request.get("QueryParameters", {}).get("token"))
            return {"RestApiResponse": pages.pop(0)}

    log = Mwaa("m", "us-east-1", client=Client()).task_log("d", "r", "t", 1, map_index=2)
    assert seen == [None, "t1", "t2"] and log["text"].endswith("T ERROR boom") and not log["truncated"]
    text, cut = keep_tail("x\n" * 200000, cap=1024)
    assert cut and len(text.encode()) <= 1024
    assert log_text("[('host', '*** reading\\nline')]") == "*** reading\nline"


# ---------------------------------------------------------------- DAG to code

def test_code_map_paths_and_selectors():
    assert code_map.dag_relative_path("/usr/local/airflow/dags/sales/orders.py") == "sales/orders.py"
    assert code_map.candidate_repo_path("/usr/local/airflow/dags/sales/orders.py", "airflow/dags/") == "airflow/dags/sales/orders.py"
    assert code_map.candidate_repo_path("/usr/local/airflow/dags/x.py", "dags/x_v2.py") == "dags/x_v2.py"
    assert code_map.candidate_repo_path(None) is None
    cmd = "cd /usr/local/airflow/dbt && dbt deps && dbt run --profiles-dir . --select stg_orders+ tag:daily --exclude x"
    assert code_map.dbt_selectors(cmd) == ["stg_orders+", "tag:daily"]
    assert code_map.dbt_selectors("dbt build -s 'orders customers' --target prod") == ["orders", "customers"]
    assert code_map.dbt_selectors("/opt/venv/bin/dbt test --models=fct_sales") == ["fct_sales"]
    assert code_map.dbt_selectors("echo dbt is great") == []
    assert code_map.cosmos_model("dbt_tg.stg_orders.run") == "stg_orders"
    assert code_map.cosmos_model("stg_orders_test") == "stg_orders" and code_map.cosmos_model("load") is None
    assert code_map.task_models("tg.fct_sales.run", "DbtRunLocalOperator") == ["fct_sales"]
    assert code_map.task_models("trigger", "DbtCloudRunJobOperator") == []


# ---------------------------------------------------------------- listener plugin (no Airflow import)

class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_plugin_payloads_and_config(monkeypatch):
    from datetime import datetime, timezone

    run = Obj(dag_id="d", run_id="r", run_type="scheduled", logical_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
              start_date=datetime(2024, 1, 1, 0, 0, 5), end_date=None, external_trigger=False)
    body = plugin.dag_run_payload(run, "running")
    assert body["kind"] == "dag_run" and body["start"] == "2024-01-01T00:00:05+00:00" and body["end"] is None
    kind, row = push_row("prod", body)
    assert kind == "dag_run" and row["state"] == "running"
    ti = Obj(dag_id="d", run_id="r", task_id="t", map_index=-1, try_number=2, task=Obj(), start_date=None, end_date=None,
             duration=None, hostname="h")
    payload = plugin.task_payload(ti, "failed", ValueError("bad " * 1000))
    assert payload["operator"] == "Obj" and payload["error"].startswith("ValueError") is False and len(payload["error"]) == 2000
    assert push_row("prod", payload)[1]["try_number"] == 2
    for key in ("GDP_INGEST_URL", "GDP_ENV_ID", "GDP_PUSH_SECRET", "AIRFLOW__GDP__INGEST_URL", "AIRFLOW__GDP__ENV_ID",
                "AIRFLOW__GDP__PUSH_SECRET"):
        monkeypatch.delenv(key, raising=False)
    assert plugin.config() is None and plugin.emit(body) is False
    monkeypatch.setenv("AIRFLOW__GDP__INGEST_URL", "https://gdp.example.com/bff/ops/ingest")
    monkeypatch.setenv("AIRFLOW__GDP__ENV_ID", "prod")
    monkeypatch.setenv("AIRFLOW__GDP__PUSH_SECRET", SECRET)
    sent = []
    monkeypatch.setattr(plugin, "post", lambda url, raw, headers, timeout=3: sent.append((url, raw, headers)) or 200)
    assert plugin.emit(body, background=False)
    url, raw, headers = sent[0]
    assert signing.verify(SECRET, headers["X-GDP-Timestamp"], raw, headers["X-GDP-Signature"])
    monkeypatch.setattr(plugin, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert plugin.emit(body, background=False)      # a failed send never raises into Airflow


# ---------------------------------------------------------------- governance

ROLE_PRIVS = {name: spec["privileges"] for name, spec in SYSTEM_ROLES.items()}
GRANTS = {name: spec["inherits"] for name, spec in SYSTEM_ROLES.items()}
OPS_ROUTES = [("GET", "/api/ops/summary", "OPS.VIEW"), ("GET", "/api/ops/envs", "OPS.VIEW"), ("GET", "/api/ops/dags", "OPS.VIEW"),
              ("GET", "/api/ops/dag", "OPS.VIEW"), ("GET", "/api/ops/run", "OPS.VIEW"), ("GET", "/api/ops/task-log", "OPS.VIEW"),
              ("POST", "/api/ops/envs", "INTEGRATION.MANAGE"), ("PUT", "/api/ops/envs/prod", "INTEGRATION.MANAGE"),
              ("DELETE", "/api/ops/envs/prod", "INTEGRATION.MANAGE"), ("POST", "/api/ops/envs/prod/test", "INTEGRATION.MANAGE"),
              ("POST", "/api/ops/envs/prod/push-secret", "INTEGRATION.MANAGE"), ("POST", "/api/ops/envs/prod/poll", "OPS.OPERATE"),
              ("PUT", "/api/ops/dag", "OPS.OPERATE"), ("POST", "/api/ops/ingest", None)]


def test_ops_governance_mapping():
    for method, path, priv in OPS_ROUTES:
        found, _, matched = privilege_for(method, path)
        assert matched and found == priv, (method, path, found)
    assert SYSTEM_VERSION == 6 and ADDED_PRIVILEGES[6] == ["OPS.VIEW", "OPS.OPERATE"]
    roles, support = effective_privileges(["SUPPORT_ENGINEER"], ROLE_PRIVS, GRANTS)
    assert "VIEWER" in roles and {"OPS.VIEW", "OPS.OPERATE", "AI.USE", "JIRA.READ", "JIRA.WRITE"} <= support
    assert "INTEGRATION.MANAGE" not in support and "RUN.OPERATE" not in support
    _, engineer = effective_privileges(["DATA_ENGINEER"], ROLE_PRIVS, GRANTS)
    _, viewer = effective_privileges(["VIEWER"], ROLE_PRIVS, GRANTS)
    assert {"OPS.VIEW", "OPS.OPERATE"} <= engineer and "OPS.VIEW" in viewer and "OPS.OPERATE" not in viewer
    assert read_only(viewer)
    for privs in (support, engineer):
        assert decide("OPS.OPERATE", privs, None)[0] == "ALLOW"
    assert decide("OPS.OPERATE", viewer, None)[0] == "FORBID" and decide("OPS.VIEW", viewer, None)[0] == "ALLOW"
