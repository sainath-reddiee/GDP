"""Ops hardening: secrets sealed in the application (never a key or a secret in SQL text), the poll cursor on truncated
pages, the listener's retry state, QA bugs checked live, incidents that stay reopened, the detect cursor on a cut read,
per-candidate errors, redacted event payloads, the signed event id, ingest rate limits and caches, the outbox claim and
lost leases, tickets for resolved incidents, removed runs on old Airflow, and idempotent incident inserts."""

import importlib
import json
import re
import sys
import threading
import time
import types
from collections import OrderedDict
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "airflow_plugins" / "gdp_listener"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_jira_inbox as ji  # noqa: E402
import test_ops_incidents as oi  # noqa: E402
from gdp_listener import core as plugin  # noqa: E402
from services.common import secretbox  # noqa: E402
from services.jira.client import JiraError  # noqa: E402
from services.ops import incidents as inc, lease, notify, signing, store as ops_store, tickets  # noqa: E402
from services.ops.mwaa import Mwaa  # noqa: E402
from services.ops.normalize import ts  # noqa: E402

KEY = "k" * 32
NOW = oi.NOW
CLOUD = "11111111-2222-3333-4444-555555555555"


def _modules():
    importlib.import_module("app.main")
    import app.incidents_api as incidents_api
    import app.jira_api as jira_api
    import app.ops_api as ops_api

    return ops_api, incidents_api, jira_api


class CaptureDb:
    """Records every statement and its parameters; answers queries by the first matching SQL fragment."""

    user = "ANA"
    shared = True

    def __init__(self, answers=None, count=1):
        self.answers, self.count, self.sent = dict(answers or {}), count, []

    def query(self, sql, params=()):
        self.sent.append((sql, params))
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return rows(sql, params) if callable(rows) else [dict(r) for r in rows]
        return []

    def execute(self, sql, params=()):
        self.sent.append((sql, params))

    def execute_count(self, sql, params=()):
        self.sent.append((sql, params))
        return self.count(sql, params) if callable(self.count) else self.count


def assert_no_secret_in_sql(db, *secrets):
    for sql, params in db.sent:
        assert not re.search(r"\b(EN|DE)CRYPT\s*\(", sql), sql
        for secret in secrets:
            assert secret not in sql
            for p in params:
                if isinstance(p, str):
                    assert secret not in p, (sql, p)
                if isinstance(p, (bytes, bytearray)):
                    assert secret.encode() not in bytes(p)


# ---------------------------------------------------------------- 1. secrets sealed in the application

def test_secretbox_round_trip_tamper_wrong_key_context_and_legacy():
    sealed = secretbox.seal("https://a.logic.azure.com/x?sig=S3CRET", KEY, secretbox.TEAMS_WEBHOOK, "t1:COL")
    assert sealed.startswith(secretbox.PREFIX) and b"S3CRET" not in sealed
    assert secretbox.open_secret(sealed, KEY, secretbox.TEAMS_WEBHOOK, "t1:COL").endswith("sig=S3CRET")
    assert secretbox.open_secret(bytearray(sealed), KEY, secretbox.TEAMS_WEBHOOK, "t1:COL").endswith("S3CRET")
    assert secretbox.open_secret(sealed.hex(), KEY, secretbox.TEAMS_WEBHOOK, "t1:COL").endswith("S3CRET")
    assert secretbox.seal("x", KEY, secretbox.TEAMS_WEBHOOK) != secretbox.seal("x", KEY, secretbox.TEAMS_WEBHOOK)  # fresh nonce
    assert secretbox.open_secret(None, KEY, secretbox.TEAMS_WEBHOOK) is None
    tampered = sealed[:-1] + bytes([sealed[-1] ^ 1])
    for value, key, purpose, context in ((tampered, KEY, secretbox.TEAMS_WEBHOOK, "t1:COL"),
                                         (sealed, "z" * 32, secretbox.TEAMS_WEBHOOK, "t1:COL"),     # wrong key
                                         (sealed, KEY, secretbox.PUSH_SECRET, "t1:COL"),            # other purpose
                                         (sealed, KEY, secretbox.TEAMS_WEBHOOK, "t2:COL"),          # copied to another row
                                         (sealed[:20], KEY, secretbox.TEAMS_WEBHOOK, "t1:COL")):    # truncated
        with pytest.raises(secretbox.SecretError) as err:
            secretbox.open_secret(value, key, purpose, context)
        assert not isinstance(err.value, secretbox.LegacySecret)
    legacy = bytes.fromhex("8a2f9c0d11e7b3a4c5d6e7f8091a2b3c4d5e6f708192a3b4")   # what Snowflake ENCRYPT() stored
    assert secretbox.is_legacy(legacy) and not secretbox.is_legacy(sealed)
    with pytest.raises(secretbox.LegacySecret):
        secretbox.open_secret(legacy, KEY, secretbox.JIRA_TOKEN)
    with pytest.raises(secretbox.SecretError):
        secretbox.seal("x", "short", secretbox.JIRA_TOKEN)


def test_key_precedence_per_feature(monkeypatch):
    monkeypatch.setenv("AIP_SECRET_KEY", "a" * 32)
    monkeypatch.setenv("JIRA_TOKEN_KEY", "j" * 32)
    assert secretbox.ops_key() == "a" * 32 and secretbox.jira_key() == "j" * 32
    monkeypatch.delenv("AIP_SECRET_KEY")
    assert secretbox.ops_key() == "j" * 32
    monkeypatch.setenv("JIRA_TOKEN_KEY", "short")
    assert secretbox.ops_key() == "" and secretbox.jira_key() == ""


def test_no_bound_key_left_in_any_sql():
    pattern = re.compile(r"\b(EN|DE)CRYPT\s*\(\s*[A-Z_%(]")
    for folder in (ROOT / "apps" / "api" / "app", ROOT / "services"):
        for path in folder.rglob("*.py"):
            assert not pattern.search(path.read_text(encoding="utf-8")), path


def test_push_secret_write_binds_only_ciphertext(monkeypatch):
    ops_api, _, _ = _modules()
    monkeypatch.setenv("AIP_SECRET_KEY", KEY)
    db = CaptureDb({"FROM OPS.AIRFLOW_ENV WHERE ENV_ID": [{"env_id": "prod", "name": "Prod", "mwaa_env": "m", "region": "us-east-1"}]})
    out = ops_api.rotate_push_secret("prod", db=db, x_aip_replay=None)
    assert_no_secret_in_sql(db, KEY, out["secret"])
    sql, params = next((s, p) for s, p in db.sent if "SET PUSH_SECRET" in s)
    assert isinstance(params[0], bytes) and params[1:] == ("prod",)
    assert ops_api.open_push_secret(params[0], "prod", KEY) == out["secret"]
    assert ops_api.open_push_secret(params[0], "other-env", KEY) is None   # bound to its environment


def test_push_secret_legacy_value_needs_a_new_secret(monkeypatch):
    ops_api, _, _ = _modules()
    monkeypatch.setenv("AIP_SECRET_KEY", KEY)
    monkeypatch.setattr(ops_api, "_secrets", OrderedDict())
    db = CaptureDb({"PUSH_SECRET AS S": [{"s": bytes.fromhex("aa" * 40), "push_enabled": True}]})
    assert ops_api._push_secret(db, "prod") == (None, True)
    assert_no_secret_in_sql(db, KEY)
    row = {"env_id": "prod", "name": "P", "mwaa_env": "m", "region": "r", "push_secret_legacy": True, "has_push_secret": True}
    assert "generate a new push secret" in ops_api._env_out(row)["push_secret_detail"]


def test_webhook_write_and_read_never_put_the_url_or_key_in_sql(monkeypatch):
    _, incidents_api, _ = _modules()
    monkeypatch.setenv("AIP_SECRET_KEY", KEY)
    url = "https://prod-1.westus.logic.azure.com/workflows/x/triggers/manual/paths/invoke?sig=TOPSECRET"
    team = {"team_id": "data", "name": "Data"}
    db = CaptureDb({"FROM OPS.TEAM WHERE TEAM_ID": [team]})
    incidents_api.set_webhook("data", incidents_api.WebhookIn(kind="alerts", url=url), db=db, x_aip_replay=None)
    assert_no_secret_in_sql(db, KEY, "TOPSECRET")
    sealed = next(p[0] for s, p in db.sent if "SET TEAMS_WEBHOOK_SECRET = %s" in s)
    assert isinstance(sealed, bytes)
    reader = CaptureDb({"TEAMS_WEBHOOK_SECRET AS A": [{"a": sealed, "e": None}]})
    assert notify.webhook_url(reader, "data", "escalation") == url     # escalation falls back to alerts
    assert_no_secret_in_sql(reader, KEY)
    legacy = CaptureDb({"TEAMS_WEBHOOK_SECRET AS A": [{"a": bytes.fromhex("bb" * 40), "e": None}]})
    with pytest.raises(RuntimeError) as err:
        notify.webhook_url(legacy, "data", "alerts")
    assert "set the webhook again" in str(err.value)
    tester = CaptureDb({"FROM OPS.TEAM WHERE TEAM_ID": lambda sql, p: [{**team, "u": bytes.fromhex("bb" * 40)}]})
    assert incidents_api.test_webhook("data", incidents_api.TestIn(kind="alerts"), db=tester) == \
        {"ok": False, "detail": notify.WEBHOOK_AGAIN}


def test_jira_tokens_are_sealed_and_legacy_rows_ask_to_reconnect(monkeypatch):
    _, _, jira_api = _modules()
    monkeypatch.setenv("JIRA_TOKEN_KEY", KEY)
    db = CaptureDb()
    jira_api._store_refresh(db, "ANA", CLOUD, "refresh-SECRET-1", access_token="access-SECRET-2", expires_in=600,
                            site_url="https://team.atlassian.net")
    assert_no_secret_in_sql(db, KEY, "refresh-SECRET-1", "access-SECRET-2")
    merged = next(p for s, p in db.sent if "MERGE INTO JIRA.USER_TOKEN" in s)
    sealed_refresh = [p for p in merged if isinstance(p, bytes)]
    assert len(sealed_refresh) == 2 and jira_api._open_token(sealed_refresh[0], "refresh", "ANA", CLOUD) == "refresh-SECRET-1"
    access = next(p for s, p in db.sent if "SET ACCESS_TOKEN = %s" in s)[0]
    assert jira_api._open_token(access, "access", "ANA", CLOUD) == "access-SECRET-2"
    with pytest.raises(secretbox.SecretError):
        jira_api._open_token(access, "refresh", "ANA", CLOUD)   # an access token cannot pass for a refresh token
    reader = CaptureDb({"REFRESH_TOKEN AS T": [{"t": sealed_refresh[0], "a": access, "ttl": 500, "v": 1, "leased": False}]})
    row = jira_api._token_row(reader, CLOUD)
    assert row["t"] == "refresh-SECRET-1" and row["a"] == "access-SECRET-2"
    assert_no_secret_in_sql(reader, KEY)
    legacy = CaptureDb({"REFRESH_TOKEN AS T": [{"t": bytes.fromhex("cc" * 40), "a": None, "ttl": 0, "v": 1, "leased": False}]})
    with pytest.raises(HTTPException) as err:
        jira_api._token_row(legacy, CLOUD)
    assert err.value.status_code == 428 and "connect Jira again" in err.value.detail
    stale_access = CaptureDb({"REFRESH_TOKEN AS T": [{"t": sealed_refresh[0], "a": bytes.fromhex("cc" * 40), "ttl": 500,
                                                      "v": 1, "leased": False}]})
    assert jira_api._token_row(stale_access, CLOUD)["a"] is None   # only refreshed, the user stays connected


def test_jira_refresh_under_lease_binds_only_ciphertext(monkeypatch):
    _, _, jira_api = _modules()
    monkeypatch.setenv("JIRA_TOKEN_KEY", KEY)
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "client-SECRET")
    sealed = jira_api._seal_token("refresh-OLD", "refresh", "ANA", CLOUD)
    db = CaptureDb({"REFRESH_TOKEN AS T": [{"t": sealed, "a": None, "ttl": 0, "v": 3, "leased": True}]})
    monkeypatch.setattr(jira_api, "refresh_tokens", lambda http, cid, secret, token: {
        "access_token": "access-NEW", "refresh_token": "refresh-NEW", "expires_in": 3600})
    token, _ = jira_api._refresh_with_lease(db, {"client_id": "c"}, CLOUD, 3, "me")
    assert token == "access-NEW"
    assert_no_secret_in_sql(db, KEY, "refresh-NEW", "access-NEW", "refresh-OLD", "client-SECRET")
    params = next(p for s, p in db.sent if "SET REFRESH_TOKEN = COALESCE(%s" in s)
    assert jira_api._open_token(params[0], "refresh", "ANA", CLOUD) == "refresh-NEW"
    assert jira_api._open_token(params[1], "access", "ANA", CLOUD) == "access-NEW"


# ---------------------------------------------------------------- 2. and 13. polling

def _runs(n, field="updated_at"):
    base = 1_714_644_000   # 2024-05-02T10:00:00Z
    out = []
    for i in range(n):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + i))
        out.append({"dag_id": "d", "dag_run_id": f"r{i}", "state": "success", "start_date": stamp, "end_date": stamp,
                    field: stamp, "run_type": "scheduled"})
    return out


class PagingClient:
    """Airflow with more changed runs than one poll reads; records the dagRuns query parameters."""

    def __init__(self, runs, version="2.8.1", gone=()):
        self.runs, self.version, self.gone, self.calls = runs, version, set(gone), []

    def invoke_rest_api(self, **request):
        path, params = request["Path"], request.get("QueryParameters") or {}
        if path == "/version":
            return {"RestApiResponse": {"version": self.version}}
        if path == "/dags":
            return {"RestApiResponse": {"dags": [{"dag_id": "d"}], "total_entries": 1}}
        if path == "/dags/~/dagRuns":
            self.calls.append(params)
            offset, limit = int(params["offset"]), int(params["limit"])
            return {"RestApiResponse": {"dag_runs": self.runs[offset:offset + limit], "total_entries": len(self.runs)}}
        if path.endswith("/taskInstances"):
            return {"RestApiResponse": {"task_instances": [], "total_entries": 0}}
        run_id = path.rsplit("/", 1)[-1]
        if run_id in self.gone:
            error = Exception("gone")
            error.response = {"Error": {"Code": "RestApiClientException"}, "RestApiStatusCode": 404}
            raise error
        raise AssertionError(path)


class PollDb(CaptureDb):
    def __init__(self, stale=()):
        super().__init__({"STATE IN ('running'": [{"dag_id": d, "run_id": r} for d, r in stale]})


def test_truncated_poll_resumes_after_the_last_run_read():
    runs = _runs(2500)
    client, db = PagingClient(runs), PollDb()
    env = {"env_id": "prod", "mwaa_env": "m", "region": "us-east-1", "cursor_value": "2024-05-02T10:10:00+00:00"}
    summary = ops_store.poll_env(db, env, client=Mwaa("m", "us-east-1", client=client), now="2024-05-03T00:00:00+00:00",
                                 max_task_fetch=10_000)
    assert all(c["order_by"] == "updated_at" and "updated_at_gte" in c for c in client.calls)
    assert len(client.calls) == ops_store.MAX_RUN_PAGES and not summary["complete"]
    # the old cursor would make the next poll read the same 2000 runs again; it moves to the last run read instead
    assert summary["cursor"] == ts(runs[1999]["updated_at"]) == "2024-05-02T10:33:19.000000+00:00"
    nxt = ops_store.poll_since(summary["cursor"], "2024-05-03T00:00:00+00:00")
    assert nxt < summary["cursor"]                                  # the overlap window is kept


def test_truncated_poll_on_old_airflow_orders_by_start_date():
    runs = _runs(2100, field="start_date")
    client = PagingClient(runs, version="2.5.3")
    summary = ops_store.poll_env(PollDb(), {"env_id": "prod", "mwaa_env": "m", "region": "us-east-1", "cursor_value": None},
                                 client=Mwaa("m", "us-east-1", client=client), now="2024-05-03T00:00:00+00:00",
                                 max_task_fetch=10_000)
    assert all(c["order_by"] == "start_date" and "start_date_gte" in c for c in client.calls)
    assert summary["cursor"] == ts(runs[1999]["start_date"])
    assert ops_store.later_than("2024-05-02T10:00:01+00:00", "2024-05-02T10:00:00Z")
    assert not ops_store.later_than(None, "2024-05-02T10:00:00Z")


def test_old_airflow_marks_runs_it_no_longer_has_removed():
    client = PagingClient(_runs(3, field="start_date"), version="2.5.3", gone={"gone-run"})
    db = PollDb(stale=[("d", "gone-run")])
    ops_store.poll_env(db, {"env_id": "prod", "mwaa_env": "m", "region": "us-east-1", "cursor_value": None},
                       client=Mwaa("m", "us-east-1", client=client), now="2024-05-03T00:00:00+00:00")
    stale_sql = next(s for s, _ in db.sent if "STATE IN ('running'" in s and "SELECT DAG_ID, RUN_ID" in s)
    assert "ORDER BY LOADED_AT" in stale_sql            # the least recently loaded first, so the re-read rotates
    removed = [(s, p) for s, p in db.sent if "SET STATE = 'removed'" in s]
    assert {("OPS.DAG_RUN" in s, "OPS.TASK_RUN" in s) for s, _ in removed} == {(True, False), (False, True)}
    assert all(json.loads(p[0]) == [["d", "gone-run"]] and p[1] == "prod" for _, p in removed)
    assert all("STATE IN ('running'" in s for s, _ in removed)   # only rows still active


# ---------------------------------------------------------------- 3. the listener reports the real state

class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_failed_hook_reports_up_for_retry(monkeypatch):
    assert plugin.failed_state(Obj(state="up_for_retry")) == "up_for_retry"
    assert plugin.failed_state(Obj(state=Obj(value="up_for_retry"))) == "up_for_retry"
    assert plugin.failed_state(Obj(state="TaskInstanceState.UP_FOR_RETRY")) == "up_for_retry"
    assert plugin.failed_state(Obj(state=None)) == "failed" and plugin.failed_state(Obj()) == "failed"
    airflow = types.ModuleType("airflow")
    airflow.__version__ = "2.10.3"
    listeners = types.ModuleType("airflow.listeners")
    listeners.hookimpl = lambda fn: fn
    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.listeners", listeners)
    monkeypatch.delitem(sys.modules, "gdp_listener.listener", raising=False)
    listener = importlib.import_module("gdp_listener.listener")
    sent = []
    monkeypatch.setattr(plugin, "emit", lambda payload, *a, **k: sent.append(payload) or True)
    ti = Obj(dag_id="d", run_id="r", task_id="t", map_index=-1, try_number=1, state="up_for_retry", start_date=None,
             end_date=None, duration=None, hostname="h")
    listener.on_task_instance_failed(None, ti, ValueError("boom"))
    ti.state = "failed"
    listener.on_task_instance_failed(None, ti, ValueError("boom"))
    assert [p["state"] for p in sent] == ["up_for_retry", "failed"] and sent[0]["error"].startswith("ValueError")
    sys.modules.pop("gdp_listener.listener", None)


# ---------------------------------------------------------------- 4. QA bugs: the linked bug's live status decides

@pytest.fixture
def bug_api(monkeypatch):
    _, _, jira_api = _modules()
    fake = type("Run", (), {"latest_table": staticmethod(lambda query, table_id, suite_id=None: ({"qa_run_id": "q1"}, [dict(ji.RESULT)]))})
    monkeypatch.setitem(sys.modules, "services.qa.run", fake)

    def use(client):
        monkeypatch.setattr(jira_api, "_client", lambda db: (client, dict(ji.CONN), dict(ji.CFG)))
        return jira_api
    return use


def test_closed_linked_bug_is_skipped_and_a_new_one_created(bug_api):
    client = ji.Jira(issue=lambda key: ji.issue(key, "Done", "done"), search_page=lambda *a: {"issues": [], "next": None},
                     issue_types=lambda project: [{"id": "2", "name": "Bug", "subtask": False}],
                     create_issue=lambda *a: {"id": "90", "key": "QA-90"}, remote_link=lambda *a: None)
    module = bug_api(client)
    db = ji.bug_db(**{"ORIGIN = 'BUG'": [{"issue_key": "QA-5"}]})
    out = ji.bug(module, db)
    assert out["key"] == "QA-90" and out["created"] is True
    status = db.wrote("UPDATE JIRA.ISSUE_LINK SET STATUS = %s, STATUS_CATEGORY")
    assert status and status[0] == ("Done", "done", "QA-5")      # the stale link learns it is closed


def test_deleted_linked_bug_is_skipped(bug_api):
    client = ji.Jira(errors={"issue": JiraError(404, "gone")}, search_page=lambda *a: {"issues": [], "next": None},
                     issue_types=lambda project: [{"id": "2", "name": "Bug", "subtask": False}],
                     create_issue=lambda *a: {"id": "91", "key": "QA-91"}, remote_link=lambda *a: None)
    module = bug_api(client)
    db = ji.bug_db(**{"ORIGIN = 'BUG'": [{"issue_key": "QA-5"}]})
    assert ji.bug(module, db)["key"] == "QA-91"
    assert db.wrote("SET ISSUE_STATE = 'DELETED'") == [("QA-5",)]


def test_transitions_keep_the_status_category(bug_api):
    options = [{"id": "31", "name": "Close", "to": "Done", "category": "done"}]
    client = ji.Jira(transitions=lambda key: options, transition=lambda key, tid: None)
    module = bug_api(client)
    db = ji.Db()
    module.transition("QA-1", module.TransitionIn(transition_id="31"), db=db)
    module.bulk(module.BulkIn(action="transition", keys=["QA-2"], transition_name="close"), db=db)
    assert db.wrote("STATUS_CATEGORY = COALESCE") == [("Done", "done", "QA-1"), ("Done", "done", "QA-2")]


# ---------------------------------------------------------------- 5. an incident reopened after Jira Done stays open

class ReopenJira(oi.FakeJira):
    def __init__(self, found=None, status="done", reopen=True):
        super().__init__(found=found)
        self.status, self.reopen = status, reopen

    def issue(self, key):
        return {"key": key, "fields": {"status": {"name": "Done", "statusCategory": {"key": self.status}}}}

    def transitions(self, key):
        options = [{"id": "31", "name": "Close", "to": "Done", "category": "done"}]
        if self.reopen:
            options.append({"id": "41", "name": "Reopen", "to": "To Do", "category": "new"})
        return options


def done_issue(key, changed):
    return {"key": key, "fields": {"status": {"name": "Done"}, "statuscategorychangedate": changed}}


def test_reopened_incident_is_not_mitigated_by_the_old_done():
    s = oi.store_with_dag()
    iid = oi.opened_incident(s)
    s.incidents[iid]["jira_key"] = "DATA-5"
    assert tickets.sync_done(s, ReopenJira(found=[done_issue("DATA-5", oi.iso(NOW + timedelta(minutes=1)))])) == 1
    later = NOW + timedelta(minutes=5)
    inc.open_or_update(s, oi.cand(s, run="r5", ended=later), s.settings(), s.dag_meta[("prod", "orders")], [], later)
    row = s.incidents[iid]
    assert row["status"] == "OPEN" and row["jira_state"] == inc.REOPENED
    assert [n["kind"] for n in s.notes_for(iid, "JIRA") if n["kind"] == "reopen"] == ["reopen"]
    # the ticket is still Done in Jira: while the reopen is queued, jira_sync leaves the incident alone
    stale = ReopenJira(found=[done_issue("DATA-5", oi.iso(NOW + timedelta(minutes=1)))])
    assert tickets.sync_done(s, stale) == 0 and s.incidents[iid]["status"] == "OPEN"
    # the reopen action moves the ticket out of Done and comments
    jira = ReopenJira()
    status, detail = tickets.handle(s, {"incident_id": iid, "kind": "reopen"}, jira, s.settings(), later)
    assert status == "SENT" and jira.moved == [("DATA-5", "41")] and jira.comments and s.incidents[iid]["jira_state"] == "OPEN"
    # a Done older than the reopen never mitigates it; a new Done does
    assert tickets.sync_done(s, stale) == 0 and s.incidents[iid]["status"] == "OPEN"
    fresh = ReopenJira(found=[done_issue("DATA-5", oi.iso(later + timedelta(minutes=30)))])
    assert tickets.sync_done(s, fresh) == 1 and s.incidents[iid]["status"] == "MITIGATED"


def test_reopen_without_a_way_back_comments_and_keeps_open_tickets_as_they_are():
    s = oi.store_with_dag()
    iid = oi.opened_incident(s)
    s.incidents[iid].update({"jira_key": "DATA-6", "status": "RESOLVED", "resolved_at": oi.iso(NOW)})
    inc.reopen(s, iid, "ana", NOW + timedelta(minutes=1))
    assert s.incidents[iid]["jira_state"] == inc.REOPENED
    jira = ReopenJira(reopen=False)
    status, detail = tickets.handle(s, {"incident_id": iid, "kind": "reopen"}, jira, {}, NOW)
    assert status == "SENT" and jira.comments and not jira.moved and "no transition out of Done" in detail
    not_done = ReopenJira(status="indeterminate")
    tickets.handle(s, {"incident_id": iid, "kind": "reopen"}, not_done, {}, NOW)
    assert not not_done.moved   # a ticket still in progress is not moved back


# ---------------------------------------------------------------- 6. the detect cursor on a read cut at its limit

def test_detect_cursor_caps_at_the_cut_list():
    runs = [{"loaded_at": f"2026-10-10T10:00:0{i}+00:00"} for i in range(3)]
    tasks = [{"loaded_at": "2026-10-10T10:05:00+00:00"}]
    assert inc.detect_cursor(runs, tasks, 3, None) == ("2026-10-10T10:00:02.000000+00:00", True)
    assert inc.detect_cursor(runs, tasks, 10, None) == ("2026-10-10T10:05:00.000000+00:00", False)
    both = inc.detect_cursor(runs, [{"loaded_at": "2026-10-10T09:59:00+00:00"}], 1, None)
    assert both == ("2026-10-10T09:59:00.000000+00:00", True)
    assert inc.detect_cursor([], [], 5, "2026-10-10T08:00:00+00:00") == ("2026-10-10T08:00:00+00:00", False)


def test_worker_detect_resumes_exactly_after_a_cut_read(monkeypatch):
    from app import worker

    calls = []

    class Store:
        def changed_since(self, since, limit=5000, inclusive=False):
            calls.append((since, inclusive))
            return [{"x": 1}], [], "2026-10-10T10:00:02+00:00", True

    db = CaptureDb({"JOB_NAME = 'detect'": [{"cursor_value": "2026-10-10T09:00:00+00:00"}]})
    monkeypatch.setattr(worker, "_store", lambda db: Store())
    monkeypatch.setattr(inc, "process", lambda store, runs, tasks: {"candidates": 0})
    worker.detect(db)
    assert calls == [("2026-10-10T08:58:00+00:00", False)]          # normal read: two minute overlap
    stored = next(p for s, p in db.sent if "SET CURSOR_VALUE" in s)
    assert stored == ("2026-10-10T10:00:02+00:00|exact",)
    assert worker.detect_window(stored[0]) == ("2026-10-10T10:00:02+00:00", True)   # no overlap, rows at it included
    assert worker.detect_window("not a time") == (None, False) and worker.detect_window(None) == (None, False)


# ---------------------------------------------------------------- 7. long keys and one bad candidate

def test_occurrence_key_always_fits():
    s = oi.store_with_dag()
    short = oi.cand(s)
    assert inc.occurrence_key(short).startswith("occ:prod:orders:load:")      # unchanged: stored claims still match
    long = {**short, "dag_id": "d" * 250, "task_id": "t" * 250, "occurrence_key": "x" * 300}
    key = inc.occurrence_key(long)
    assert len(key) <= 160 and key.startswith("occ:prod:" + "d" * 60 + ":") and re.fullmatch(r".*:[0-9a-f]{40}", key)
    assert key != inc.occurrence_key({**long, "occurrence_key": "y" * 300})


def test_one_bad_candidate_does_not_stop_detection(monkeypatch):
    s = oi.store_with_dag()
    real = inc.open_or_update

    def flaky(store, candidate, *args, **kwargs):
        if candidate.get("task_id") == "bad":
            raise RuntimeError("String 'x...' is too long and would be truncated")
        return real(store, candidate, *args, **kwargs)
    monkeypatch.setattr(inc, "open_or_update", flaky)
    out = inc.process(s, [], [oi.task(tid="bad"), oi.task(tid="good", run="r2")], now=NOW)
    assert out["skipped"] == 1 and "too long" in out["errors"][0] and out["actions"] == {"opened": 1}


# ---------------------------------------------------------------- 8. event payloads are stored redacted

def test_pushed_event_is_stored_redacted(monkeypatch):
    ops_api, _, _ = _modules()
    secret = "s" * 64
    monkeypatch.setattr(ops_api, "_push_secret", lambda db, env_id: (secret, True))
    body = {"kind": "task_instance", "dag_id": "d", "run_id": "r", "task_id": "t", "try_number": 1, "state": "failed",
            "error": "Traceback\n" + "x" * 9000 + "\nValueError: password=hunter2 for ana@example.com",
            "log": "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG", "nested": {"note": "token: ghp_abcdefghijklmnopqrstuvwxyz0123"},
            "updated_at": "2024-01-01T00:00:00Z"}
    raw = json.dumps(body).encode()
    stamp, event_id = str(int(time.time())), "evt-redact-0001"
    db = CaptureDb()
    ops_api.ingest_event(db, "prod", stamp, event_id, signing.sign(secret, stamp, event_id, raw), raw)
    stored = next(p for s, p in db.sent if "INTO OPS.EVENT" in s)[4]
    for leaked in ("hunter2", "ana@example.com", "wJalrXUtnFEMI", "ghp_abcdefghij"):
        assert leaked not in stored
    assert len(json.loads(stored)["error"]) <= 4000


# ---------------------------------------------------------------- 9. the event id is signed

def test_signature_covers_the_event_id():
    raw, stamp = b'{"kind":"dag_run"}', "1700000000"
    sig = signing.sign("a" * 64, stamp, "evt-00000001", raw)
    assert signing.verify("a" * 64, stamp, "evt-00000001", raw, sig)
    assert not signing.verify("a" * 64, stamp, "evt-00000002", raw, sig)    # a replay under a fresh id fails
    headers = plugin.headers("prod", "a" * 64, raw, now=1700000000, event_id="evt-00000001")
    assert headers["X-GDP-Signature"] == sig == plugin.sign("a" * 64, stamp, "evt-00000001", raw)


# ---------------------------------------------------------------- 10. ingest: bounded cache, negative results, rate limits

def test_secret_cache_is_bounded_and_caches_unknown_envs(monkeypatch):
    ops_api, _, _ = _modules()
    monkeypatch.setenv("AIP_SECRET_KEY", KEY)
    monkeypatch.setattr(ops_api, "_secrets", OrderedDict())
    db = CaptureDb()
    for i in range(100):
        ops_api._push_secret(db, f"env-{i}")
    assert len(ops_api._secrets) == ops_api._SECRET_CACHE_MAX
    reads = len(db.sent)
    assert ops_api._push_secret(db, "env-99") == (None, False) and len(db.sent) == reads   # negative result cached
    assert ops_api._push_secret(db, "Bad_Env!") == (None, False) and len(db.sent) == reads  # invalid id: no read


def test_rate_limits_per_env_and_per_address(monkeypatch):
    ops_api, _, _ = _modules()
    monkeypatch.setattr(ops_api, "_rates", OrderedDict())
    monkeypatch.setattr(ops_api, "_secrets", OrderedDict({"known": (time.time() + 60, "s" * 64, True)}))
    now = 1000.0
    for _ in range(ops_api.RATE_UNKNOWN_ENV):
        ops_api.check_rate("10.0.0.1", "unknown-env", now)
    with pytest.raises(HTTPException) as err:
        ops_api.check_rate("10.0.0.1", "unknown-env", now)
    assert err.value.status_code == 429 and err.value.headers["Retry-After"] == "60"
    for _ in range(ops_api.RATE_PER_ENV):
        ops_api.check_rate("10.0.0.2", "known", now)
    with pytest.raises(HTTPException):
        ops_api.check_rate("10.0.0.3", "known", now)
    ops_api.check_rate("10.0.0.3", "known", now + 61)           # the window slides
    monkeypatch.setattr(ops_api, "_rates", OrderedDict())
    monkeypatch.setattr(ops_api, "RATE_PER_CLIENT", 3)
    for i in range(3):
        ops_api.check_rate("10.0.0.9", f"env-{i}", now)
    with pytest.raises(HTTPException) as err:
        ops_api.check_rate("10.0.0.9", "env-9", now)
    assert "address" in err.value.detail


def test_ingest_answers_429_before_any_database_read(monkeypatch):
    from fastapi.testclient import TestClient

    ops_api, _, _ = _modules()
    import app.main as main

    monkeypatch.setattr(ops_api, "_rates", OrderedDict())
    monkeypatch.setattr(ops_api, "RATE_UNKNOWN_ENV", 2)
    reads = []
    monkeypatch.setattr(ops_api, "system_db", lambda: reads.append(1) or CaptureDb())
    client = TestClient(main.app)
    raw = b'{"kind":"dag_run"}'
    codes = []
    for i in range(3):
        stamp, event_id = str(int(time.time())), f"evt-flood-{i:04d}"
        codes.append(client.post("/api/ops/ingest", content=raw, headers={
            "X-GDP-Env": "nobody", "X-GDP-Timestamp": stamp, "X-GDP-Event-Id": event_id,
            "X-GDP-Signature": signing.sign("x" * 64, stamp, event_id, raw)}).status_code)
    assert codes == [401, 401, 429] and len(reads) == 2


# ---------------------------------------------------------------- 11. the outbox claims rows; a lost lease stops the job

class ClaimDb(oi.OutboxDb):
    def __init__(self, rows, taken=()):
        super().__init__(rows, status=202)
        self.taken, self.claims = set(taken), []

    def execute_count(self, sql, params=()):
        if "SET STATUS = 'SENDING'" in sql:
            self.claims.append(params[0])
            assert "NEXT_AT <= CURRENT_TIMESTAMP()" in sql and "'SENDING'" in sql.split("WHERE", 1)[1]
            return 0 if params[0] in self.taken else 1
        return super().execute_count(sql, params)


def _rows(*ids):
    return [{"notification_id": i, "incident_id": "i-1", "team_id": "a", "channel": "TEAMS", "kind": "opened",
             "target_secret": "alerts", "payload": "{}", "attempts": 0} for i in ids]


def test_outbox_sends_only_rows_it_claimed(monkeypatch):
    monkeypatch.setenv("AIP_SECRET_KEY", KEY)
    db = ClaimDb(_rows("n1", "n2"), taken={"n2"})
    counts = notify.run_outbox(db, {}, http=db.http, keep_going=lambda: True)
    assert counts["sent"] == 1 and counts["busy"] == 1 and len(db.posts) == 1 and db.claims == ["n1", "n2"]
    assert [m[-1] for m in db.marks] == ["n1"]


def test_outbox_stops_when_the_lease_is_lost(monkeypatch):
    monkeypatch.setenv("AIP_SECRET_KEY", KEY)
    db = ClaimDb(_rows("n1", "n2"))
    answers = iter([True, False])
    counts = notify.run_outbox(db, {}, http=db.http, keep_going=lambda: next(answers))
    assert counts["sent"] == 1 and db.claims == ["n1"]


def _wait(event, seconds=2.0):
    end = time.time() + seconds
    while not event.is_set() and time.time() < end:
        time.sleep(0.01)
    return event.is_set()


@pytest.mark.parametrize("renewal", ["taken", "error"])
def test_lost_renewal_turns_still_held_false(renewal):
    from test_ops_capture import LeaseDb

    db = LeaseDb()
    with lease.held(db, "outbox", "w1", renew_every=0.01) as got:
        assert got and lease.still_held()
        if renewal == "taken":
            db.rows["outbox"]["holder"] = "w2"          # another worker took the lease over
        else:
            def boom(sql, params=()):
                raise RuntimeError("network")
            db.execute_count = boom
        assert _wait(got.lost) and not lease.still_held()
    assert lease.still_held()                             # outside a lease again


def test_worker_outbox_uses_the_lease_check(monkeypatch):
    from app import worker

    seen = {}
    monkeypatch.setattr(notify, "run_outbox", lambda db, settings, jira=None, **kw: seen.update(kw) or {})
    monkeypatch.setattr(worker, "_store", lambda db: types.SimpleNamespace(settings=lambda: {}, jira_config=lambda: {}))
    monkeypatch.setattr(worker, "_bot", lambda store: None)
    worker.outbox(CaptureDb())
    assert "keep_going" not in seen     # the default is lease.still_held of the job's lease


# ---------------------------------------------------------------- 12. no orphan tickets after resolve

def test_create_after_resolve_is_skipped():
    s = oi.store_with_dag()
    iid = oi.opened_incident(s)
    inc.resolve(s, iid, "fixed upstream", "ana", NOW)
    jira = oi.FakeJira()
    assert tickets.handle(s, {"incident_id": iid, "kind": "create"}, jira, {}, NOW)[0] == "SKIPPED"
    assert not jira.created and s.incidents[iid]["jira_state"] == "SKIPPED" and "ticket_skipped" in s.kinds(iid)
    muted = oi.store_with_dag()
    other = oi.opened_incident(muted)
    muted.incidents[other]["status"] = "MUTED"
    assert tickets.raise_ticket(muted, other, oi.FakeJira(), {})["state"] == "SKIPPED"


def test_create_racing_a_resolve_closes_the_new_ticket():
    s = oi.store_with_dag()
    iid = oi.opened_incident(s)

    class RacingJira(oi.FakeJira):
        def _call(self, method, path, body=None, ok=(200, 201, 204)):
            created = super()._call(method, path, body, ok)
            s.incidents[iid].update({"status": "RESOLVED", "resolution": "fixed", "resolved_by": "ana"})
            return created

    jira = RacingJira()
    result = tickets.raise_ticket(s, iid, jira, {"transition_on_resolve": True, "done_status": "Done"})
    assert result["state"] == "raised" and "resolved meanwhile" in result["detail"]
    assert jira.comments and jira.moved == [("DATA-1", "31")] and s.incidents[iid]["jira_state"] == "DONE"


# ---------------------------------------------------------------- 14. idempotent incident inserts and transactions

def test_retry_after_a_failed_bind_never_duplicates_the_incident():
    s = oi.store_with_dag()
    real_bind = s.bind
    state = {"fail": True}

    def bind(key, incident_id, kind, actor="system", detail=None):
        if state.pop("fail", False):
            raise RuntimeError("connection reset")
        return real_bind(key, incident_id, kind, actor, detail)
    s.bind = bind
    with pytest.raises(RuntimeError):
        inc.open_or_update(s, oi.cand(s), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)
    assert len(s.incidents) == 1                                  # inserted before the failure
    retry = inc.open_or_update(s, oi.cand(s), s.settings(), s.dag_meta[("prod", "orders")], [], NOW + timedelta(seconds=5))
    assert retry["action"] == "opened" and len(s.incidents) == 1 and retry["incident_id"] in s.incidents
    assert retry["incident_id"] == inc.incident_id_for(inc.occurrence_key(oi.cand(s)))
    assert len([n for n in s.notes if n["channel"] == "TEAMS" and n["kind"] == "opened"]) == 1
    assert len([n for n in s.notes if n["channel"] == "JIRA" and n["kind"] == "create"]) == 1


class TxDb(CaptureDb):
    shared = False


def test_sql_store_insert_is_a_merge_and_writes_share_a_transaction():
    db = TxDb(count=0)
    store = inc.SqlStore(db)
    assert store.insert({"incident_id": "i-1", "fingerprint": "f", "first_seen": "2026-10-10T10:00:00+00:00"}) == 0
    sql, params = db.sent[-1]
    assert sql.startswith("MERGE INTO OPS.INCIDENT T USING (SELECT %s AS INCIDENT_ID)") and params[:2] == ("i-1", "i-1")
    with store.atomic():
        db.execute("UPDATE X")
    assert [s for s, _ in db.sent[-3:]] == ["BEGIN", "UPDATE X", "COMMIT"]
    with pytest.raises(ValueError):
        with store.atomic():
            raise ValueError("boom")
    assert db.sent[-1][0] == "ROLLBACK"
    shared = CaptureDb()
    with inc.SqlStore(shared).atomic():
        shared.execute("UPDATE Y")
    assert [s for s, _ in shared.sent] == ["UPDATE Y"]           # the shared session never gets BEGIN


def test_worker_gets_its_own_non_shared_session(monkeypatch):
    import app.db as dbmod
    from app import worker

    assert worker.Worker().db_factory is dbmod.worker_db
    opened = []

    class Conn:
        def is_closed(self):
            return False

    monkeypatch.setattr(dbmod, "AUTH_MODE", "dev")
    monkeypatch.setattr(dbmod, "_worker", None)
    monkeypatch.setattr(dbmod, "_open_dev", lambda: opened.append(Conn()) or opened[-1])
    monkeypatch.setattr(dbmod, "_identity", lambda conn: ("ANA", "R"))
    first = dbmod.worker_db()
    assert first.shared is False and dbmod.worker_db() is first and len(opened) == 1


def test_lease_handle_is_thread_local():
    from test_ops_capture import LeaseDb

    db = LeaseDb()
    seen = {}
    with lease.held(db, "outbox", "w1", renew_every=60) as got:
        got.lost.set()
        thread = threading.Thread(target=lambda: seen.update(other=lease.still_held()))
        thread.start()
        thread.join()
        assert not lease.still_held() and seen["other"] is True
