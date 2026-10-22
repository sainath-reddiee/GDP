"""Datadog for Operations (services/ops/datadog.py, datadog_monitors.py): keys only from the environment and only sent
to Datadog's own hosts, the log search for an incident, the connection test, Datadog logs standing in for an unreadable
Airflow log in the AI diagnosis, monitors opening and resolving incidents on the ops incident engine, the setting, the
governance of the new routes and the worker job. Datadog is a fake HTTP function."""

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from services.common import platform_config  # noqa: E402
from services.governance.policy import privilege_for  # noqa: E402
from services.ops import context, datadog as dd, datadog_monitors as dm, incidents as inc  # noqa: E402
from tests.unit.test_ops_incidents import NOW, MemoryStore  # noqa: E402

ENV = {"DATADOG_SITE": "us3.datadoghq.com", "DATADOG_API_KEY": "a" * 32, "DATADOG_APP_KEY": "b" * 40}
ON = {**dd.DEFAULT_SETTING, "enabled": True}


class FakeHttp:
    """Answers by path; records every call. `answers[path]` is a list of (status, body, headers), used in order."""

    def __init__(self, answers):
        self.answers, self.calls = {k: list(v) for k, v in answers.items()}, []

    def __call__(self, method, url, headers, body, params):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body, "params": dict(params)})
        path = url.split(".com", 1)[1]
        queue = self.answers[path]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def client(answers):
    http = FakeHttp(answers)
    c = dd.Client(dd.config_from_env(ENV), http, sleep=lambda s: None)
    return c, http


# ---------------------------------------------------------------- configuration

def test_keys_come_from_the_environment_and_never_leave_it():
    cfg = dd.config_from_env(ENV)
    assert cfg["site"] == "us3.datadoghq.com" and cfg["app_url"] == "https://us3.datadoghq.com" and not cfg["problems"]
    assert dd.default_app_url("datadoghq.eu") == "https://app.datadoghq.eu" and dd.default_app_url("ap1.datadoghq.com") == "https://ap1.datadoghq.com"
    shown = dd.public(cfg)
    assert shown == {"site": "us3.datadoghq.com", "app_url": "https://us3.datadoghq.com", "api_key_set": True,
                     "app_key_set": True, "hosts_set": 0, "services_set": 0, "configured": True, "problems": []}
    assert "a" * 32 not in repr(shown) and "b" * 40 not in repr(shown)
    assert dd.normalize_site("https://app.datadoghq.eu/") == "datadoghq.eu" and dd.normalize_site("") == "datadoghq.com"
    missing = dd.config_from_env({})
    assert missing["problems"] == ["DATADOG_API_KEY is not set on the API host", "DATADOG_APP_KEY is not set on the API host"]
    with pytest.raises(dd.DatadogError) as e:
        dd.Client(missing)
    assert e.value.kind == "not_configured"


def test_an_unknown_site_is_refused_so_the_keys_only_ever_go_to_datadog():
    for site in ("evil.example.com", "datadoghq.com.evil.io", "api.datadoghq.com@evil.io"):
        cfg = dd.config_from_env({**ENV, "DATADOG_SITE": site})
        assert cfg["problems"] and "not a Datadog site" in cfg["problems"][0]
        with pytest.raises(dd.DatadogError):
            dd.Client(cfg)
    for site in ("datadoghq.com", "us5.datadoghq.com", "datadoghq.eu", "ap1.datadoghq.com", "ddog-gov.com"):
        assert not dd.config_from_env({**ENV, "DATADOG_SITE": site})["problems"], site
    assert dd.config_from_env({**ENV, "DATADOG_APP_URL": "http://insecure"})["problems"] == [
        "DATADOG_APP_URL must be an https address such as https://app.datadoghq.com"]
    c, http = client({"/api/v1/validate": [(200, {"valid": True}, {})]})
    assert c.validate() is True
    call = http.calls[0]
    assert call["url"] == "https://api.us3.datadoghq.com/api/v1/validate"
    assert call["headers"]["DD-API-KEY"] == "a" * 32 and "DD-APPLICATION-KEY" not in call["headers"]


def test_the_setting_is_validated_and_off_by_default():
    assert platform_config.DEFAULTS["DATADOG"]["enabled"] is False and "DATADOG" in platform_config.ADMIN_SETTINGS
    value, problems = platform_config.validate("DATADOG", {"enabled": True, "monitor_tags": ["team:data", " "],
                                                           "monitor_states": ["No Data", "Alert"], "log_limit": 50})
    assert problems == [] and value["enabled"] and value["monitor_tags"] == ["team:data"]
    assert value["monitor_states"] == ["Alert", "No Data"] and value["log_limit"] == 50
    _, problems = dd.validate_setting({"log_query": "dag_id:{dag} x", "log_limit": 5000, "monitor_states": ["Down"],
                                       "default_severity": "P9", "api_key": "x", "monitor_tags": ["bad tag!"]})
    joined = " | ".join(problems)
    for text in ("unknown Datadog setting api_key", "{dag}", "log_limit", "monitor_states", "default_severity",
                 "'bad tag!' is not a Datadog tag"):
        assert text in joined, text


# ---------------------------------------------------------------- logs

def test_the_log_query_escapes_values_and_drops_terms_without_one():
    q = dd.log_query("service:airflow dag_id:{dag_id} task_id:{task_id} run_id:{run_id}",
                     {"dag_id": "load orders", "task_id": None, "run_id": "scheduled__2026-10-22T00:00:00+00:00"})
    assert q == r"service:airflow dag_id:load\ orders run_id:scheduled__2026\-10\-22T00\:00\:00\+00\:00"
    assert dd.log_query("dag_id:{dag_id}", {"dag_id": 'x" OR *'}) == r'dag_id:x\"\ OR\ \*'
    # a dropped term never leaves a dangling operator (Datadog answers 400 to 'dag_id:x AND')
    assert dd.log_query("dag_id:{dag_id} AND task_id:{task_id}", {"dag_id": "x"}) == "dag_id:x"
    assert dd.log_query("task_id:{task_id} AND dag_id:{dag_id}", {"dag_id": "x"}) == "dag_id:x"
    assert dd.log_query("a AND task_id:{task_id} AND b", {}) == "a AND b"


def test_an_incident_s_logs_are_searched_in_its_window_and_redacted():
    c, http = client({"/api/v2/logs/events/search": [(200, {"data": [
        {"id": "l2", "attributes": {"timestamp": "2026-10-22T10:01:00Z", "status": "error", "service": "airflow",
                                    "message": "ProgrammingError password=hunter2", "tags": ["dag_id:orders", "pod:x"]}},
        {"id": "l1", "attributes": {"timestamp": "2026-10-22T10:00:00Z", "status": "info", "message": "starting"}}]}, {})]})
    incident = {"dag_id": "orders", "task_id": "load", "first_seen": "2026-10-22T10:00:00+00:00",
                "last_seen": "2026-10-22T10:05:00+00:00"}
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    out = dd.incident_logs(incident, ON, c, now=now)
    body = http.calls[0]["body"]
    assert body["filter"] == {"query": "dag_id:orders task_id:load", "from": "2026-10-22T09:30:00Z", "to": "2026-10-22T10:20:00Z"}
    assert body["page"]["limit"] == 200 and http.calls[0]["headers"]["DD-APPLICATION-KEY"] == "b" * 40
    assert out["logs"][0]["tags"] == ["dag_id:orders"] and "hunter2" not in out["logs"][0]["message"]
    assert out["link"].startswith("https://us3.datadoghq.com/logs?query=dag_id%3Aorders")
    assert dd.logs_text(out["logs"]).splitlines()[0].startswith("2026-10-22T10:00:00Z INFO starting")


def test_throttling_is_retried_and_errors_never_carry_a_key():
    c, http = client({"/api/v1/validate": [(429, None, {"x-ratelimit-reset": "1"}), (200, {"valid": True}, {})]})
    assert c.validate() and len(http.calls) == 2
    c, _ = client({"/api/v1/monitor": [(403, {"errors": ["Forbidden"]}, {})]})
    with pytest.raises(dd.DatadogError) as e:
        c.monitors()
    assert e.value.kind == "access_denied" and "Forbidden" in str(e.value)
    assert "a" * 32 not in str(e.value) and "b" * 40 not in str(e.value)
    c, http = client({"/api/v1/validate": [(503, None, {})]})
    with pytest.raises(dd.DatadogError) as e:
        c.validate()
    assert e.value.kind == "unavailable" and len(http.calls) == dd.TRIES


def test_the_connection_test_reports_each_read():
    c, _ = client({"/api/v1/validate": [(200, {"valid": True}, {})],
                   "/api/v2/logs/events/search": [(403, {"errors": ["missing scope logs_read_data"]}, {})],
                   "/api/v1/monitor": [(200, [{"id": 1}], {})]})
    result = c.check()
    assert result["api_key"] is True and result["monitors"] == 1 and result["logs"] is None
    assert "logs_read_data" in result["errors"]["logs"]
    c, _ = client({"/api/v1/validate": [(403, {"errors": ["Forbidden"]}, {})]})
    assert c.check()["errors"]["api_key"].startswith("Datadog refused the API key")


def test_monitors_page_through_the_list():
    page = [{"id": i} for i in range(dd.MONITOR_PAGE)]
    c, http = client({"/api/v1/monitor": [(200, page, {}), (200, [{"id": "last"}], {})]})
    found = c.monitors(["team:data"], ["Alert", "No Data"])
    assert len(found) == dd.MONITOR_PAGE + 1 and [x["params"]["page"] for x in http.calls] == [0, 1]
    assert http.calls[0]["params"]["monitor_tags"] == "team:data" and http.calls[0]["params"]["group_states"] == "alert,no data"


def test_datadog_logs_stand_in_for_an_airflow_log_that_cannot_be_read(monkeypatch):
    monkeypatch.setattr(dd, "setting", lambda db: ON)
    monkeypatch.setattr(dd, "incident_logs", lambda incident, value: {"logs": [
        {"at": "2026-10-22T10:00:00Z", "status": "error", "service": "airflow", "message": "Traceback (most recent call last):\nValueError: bad"}]})
    incident = {"env_id": "prod", "dag_id": "orders", "task_id": "load", "run_id": "r1", "error_excerpt": "short"}
    part = context.log_part(None, incident, {"env_id": "prod"}, None, None, fetch=True)
    assert part["source"] == "datadog" and "ValueError: bad" in part["text"] and "Datadog's logs" in part["text"]
    monkeypatch.setattr(dd, "setting", lambda db: {**ON, "diagnosis_logs": False})
    assert context.log_part(None, incident, {"env_id": "prod"}, None, None, fetch=True)["source"] == "stored excerpt"


# ---------------------------------------------------------------- monitors as incidents

class DdMemoryStore(MemoryStore):
    def active_datadog(self):
        out = []
        for i in self.incidents.values():
            if i["env_id"] == dm.DD_ENV and i["status"] in inc.ACTIVE:
                m = re.search(r"\(id ([0-9]+),", i.get("error_excerpt") or "")
                out.append({**i, "monitor_id": m.group(1) if m else None})
        return out


def monitor(mid=42, state="Alert", groups=None, **kw):
    m = {"id": mid, "name": "Orders load lag", "type": "log alert", "query": "logs(...)", "tags": ["team:data", "dag_id:orders"],
         "overall_state": state, "overall_state_modified": "2026-10-22T09:58:00+00:00", "priority": None, "message": "@ops lag"}
    if groups is not None:
        m["state"] = {"groups": groups}
    m.update(kw)
    return m


class FakeClient:
    def __init__(self, monitors):
        self.list = monitors
        self.cfg = {"app_url": "https://app.datadoghq.com"}

    def monitors(self, tags=None, states=None):
        if isinstance(self.list, Exception):
            raise self.list
        return self.list


def _dd_incidents(store):
    return [i for i in store.incidents.values() if i["env_id"] == dm.DD_ENV]


def test_an_alerting_monitor_opens_one_incident_and_a_second_read_changes_nothing():
    store = DdMemoryStore()
    cli = FakeClient([monitor(), monitor(7, "OK")])
    first = dm.process(store, cli, ON, NOW)
    assert first["actions"] == {"opened": 1} and first["alerting"] == 1
    (i,) = _dd_incidents(store)
    assert i["dag_id"] == "orders" and i["kind"] == dm.KIND and i["severity"] == "P3"
    assert i["title"] == "Datadog Alert: Orders load lag" and "https://app.datadoghq.com/monitors/42" in i["error_excerpt"]
    again = dm.process(store, cli, ON, NOW + timedelta(minutes=2))
    assert again["actions"] == {"duplicate": 1} and len(_dd_incidents(store)) == 1


def test_groups_priority_and_states():
    groups = {"env:prod": {"status": "Alert", "last_triggered_ts": 1792663200},
              "env:dev": {"status": "No Data", "last_nodata_ts": 1792663300},
              "env:qa": {"status": "Warn", "last_triggered_ts": 1792663400}}
    m = monitor(groups=groups, priority=1)
    found = dm.alerting_groups(m, ["Alert", "No Data"])
    assert sorted(g for g, _, _ in found) == ["env:dev", "env:prod"]
    assert dm.severity_for(m, "Alert", ON) == "P1" and dm.severity_for(m, "No Data", ON) == "P2"
    assert dm.severity_for(monitor(priority=5), "Alert", ON) == "P4"
    store = DdMemoryStore()
    dm.process(store, FakeClient([m]), ON, NOW)
    assert sorted(i["title"] for i in _dd_incidents(store)) == ["Datadog Alert: Orders load lag (env:prod)",
                                                                "Datadog No Data: Orders load lag (env:dev)"]
    assert dm.dag_id_for({"id": 3, "name": "CPU", "tags": []}) == "datadog: CPU"


def test_a_recovered_monitor_resolves_its_incident_and_a_gone_one_says_so():
    store = DdMemoryStore()
    dm.process(store, FakeClient([monitor(), monitor(43, name="Other")]), ON, NOW)
    assert len(_dd_incidents(store)) == 2
    result = dm.process(store, FakeClient([monitor(state="OK")]), ON, NOW + timedelta(minutes=5))
    assert result["resolved"] == 2
    by = {i["title"]: i for i in _dd_incidents(store)}
    assert by["Datadog Alert: Orders load lag"]["status"] == "RESOLVED"
    assert by["Datadog Alert: Orders load lag"]["resolution"] == "Datadog monitor recovered"
    assert "no longer exists" in by["Datadog Alert: Other"]["resolution"]
    # triggers again after recovering: the same incident is reopened inside the reopen window
    later = monitor(overall_state_modified="2026-10-22T11:00:00+00:00")
    assert dm.process(store, FakeClient([later]), ON, NOW + timedelta(hours=1))["actions"] == {"reopened": 1}


def test_nothing_resolves_when_the_read_fails_and_nothing_runs_while_off():
    store = DdMemoryStore()
    dm.process(store, FakeClient([monitor()]), ON, NOW)
    with pytest.raises(dd.DatadogError):
        dm.process(store, FakeClient(dd.DatadogError("unavailable", "down")), ON, NOW + timedelta(minutes=2))
    assert [i["status"] for i in _dd_incidents(store)] == ["OPEN"]
    assert dm.process(store, FakeClient([]), dd.DEFAULT_SETTING, NOW) == {"disabled": True}
    assert dm.process(store, FakeClient([]), {**ON, "monitors": False}, NOW) == {"disabled": True}


# ---------------------------------------------------------------- governance, worker, setup files

def test_governance_worker_and_setup_files(monkeypatch):
    assert privilege_for("GET", "/api/ops/datadog")[0] == "OPS.VIEW"
    assert privilege_for("GET", "/api/ops/incidents/i1/datadog-logs")[0] == "OPS.VIEW"
    assert privilege_for("POST", "/api/ops/datadog/test")[0] == "INTEGRATION.MANAGE"
    assert privilege_for("POST", "/api/ops/datadog/monitors/check")[0] == "OPS.OPERATE"
    from app import worker

    assert worker.INTERVALS["datadog_monitors"] == 120
    example = (ROOT / "docker" / "api.env.example").read_text(encoding="utf-8")
    for key in ("AWS_REGION=", "DATADOG_SITE=", "DATADOG_API_KEY=\n", "DATADOG_APP_KEY=\n", "DATADOG_APP_URL=\n"):
        assert key in example, key
    from infrastructure import ops_check

    for key in dd.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert ops_check.main(["--datadog", "--env-file", str(ROOT / "nonexistent.env")]) == 1   # no keys: fails, no call


# ---------------------------------------------------------------- a failed task's error from Datadog (the poll)

class PollDb:
    """CORE.TASK_RUN for fill_error_excerpts: one failed task; records the excerpt written."""

    def __init__(self):
        self.written = []

    def query(self, sql, params=()):
        if "FROM CORE.TASK_RUN" in sql:
            return [{"dag_id": "orders", "run_id": "r1", "task_id": "load", "map_index": -1, "try_number": 1,
                     "started_at": "2026-10-22T10:00:00+00:00", "ended_at": "2026-10-22T10:02:00+00:00"}]
        return []

    def execute(self, sql, params=()):
        if "UPDATE CORE.TASK_RUN SET ERROR_EXCERPT" in sql:
            self.written.append((params[0], params[1]))

    def execute_count(self, sql, params=()):
        return 0


class FakeMwaa:
    def __init__(self, text=None, fails=False):
        self.text, self.fails, self.calls = text, fails, 0

    def task_log(self, *a, **k):
        from services.ops.mwaa import MwaaError

        self.calls += 1
        if self.fails:
            raise MwaaError("access_denied", "no logs:GetLogEvents")
        return {"text": self.text}


TRACE = "Traceback (most recent call last):\n  File \"x.py\", line 1\nsnowflake.connector.errors.ProgrammingError: 002003: Object 'ORDERS' does not exist"


def test_a_failed_task_s_error_comes_from_datadog_when_airflow_s_log_cannot_be_read(monkeypatch):
    from services.ops import store

    seen = []
    monkeypatch.setattr(dd, "setting", lambda db: ON)
    monkeypatch.setattr(dd, "task_log_text", lambda task, value, cli=None, now=None: seen.append(task) or TRACE)
    db = PollDb()
    assert store.fill_error_excerpts(db, FakeMwaa(fails=True), "prod") == 1
    (text, source), = db.written
    assert source == "DATADOG" and "002003" in text and text.startswith("Traceback")
    assert seen[0]["dag_id"] == "orders" and seen[0]["env_id"] == "prod" and seen[0]["started_at"]
    # Airflow's log has the error: Datadog is not asked
    seen.clear()
    db = PollDb()
    store.fill_error_excerpts(db, FakeMwaa(TRACE), "prod")
    assert db.written[0][1] == "LOG" and not seen


def test_datadog_first_and_both_unreadable(monkeypatch):
    from services.ops import store

    monkeypatch.setattr(dd, "setting", lambda db: {**ON, "failure_logs": "first"})
    monkeypatch.setattr(dd, "task_log_text", lambda task, value, cli=None, now=None: TRACE)
    db, mw = PollDb(), FakeMwaa(TRACE)
    store.fill_error_excerpts(db, mw, "prod")
    assert db.written[0][1] == "DATADOG" and mw.calls == 0
    # neither can be read: nothing is stored, the next poll tries again
    def down(*a, **k):
        raise dd.DatadogError("unavailable", "down")
    monkeypatch.setattr(dd, "setting", lambda db: ON)
    monkeypatch.setattr(dd, "task_log_text", down)
    db = PollDb()
    assert store.fill_error_excerpts(db, FakeMwaa(fails=True), "prod") == 0 and db.written == []
    # Datadog off: unchanged behaviour
    monkeypatch.setattr(dd, "setting", lambda db: dd.DEFAULT_SETTING)
    db = PollDb()
    assert store.fill_error_excerpts(db, FakeMwaa(fails=True), "prod") == 0
    assert dd.validate_setting({"failure_logs": "sometimes"})[1] == ["failure_logs must be one of fallback, first, off"]


def test_the_task_window_is_its_run_time():
    c, http = client({"/api/v2/logs/events/search": [(200, {"data": []}, {})]})
    task = {"dag_id": "orders", "task_id": "load", "run_id": "r1", "try_number": 2,
            "started_at": "2026-10-22T10:00:00+00:00", "ended_at": "2026-10-22T10:02:00+00:00"}
    assert dd.task_log_text(task, ON, c, now=datetime(2026, 10, 22, 12, tzinfo=timezone.utc)) == ""
    f = http.calls[0]["body"]["filter"]
    assert f["from"] == "2026-10-22T09:59:00Z" and f["to"] == "2026-10-22T10:04:00Z"   # this try only, not the next


def test_airflow_log_without_error_lines_falls_back_and_info_only_datadog_is_not_stored(monkeypatch):
    from services.ops import store

    monkeypatch.setattr(dd, "setting", lambda db: ON)
    monkeypatch.setattr(dd, "task_log_text", lambda task, value, cli=None, now=None: TRACE)
    db = PollDb()
    store.fill_error_excerpts(db, FakeMwaa("INFO starting\nINFO done"), "prod")
    assert db.written[0][1] == "DATADOG" and "002003" in db.written[0][0]
    # Datadog has only INFO lines: Airflow's text is kept, and 'first' still reads Airflow
    monkeypatch.setattr(dd, "task_log_text", lambda task, value, cli=None, now=None: "2026 INFO airflow starting")
    db = PollDb()
    store.fill_error_excerpts(db, FakeMwaa("INFO starting"), "prod")
    assert db.written[0][1] == "LOG"
    monkeypatch.setattr(dd, "setting", lambda db: {**ON, "failure_logs": "first"})
    db, mw = PollDb(), FakeMwaa(TRACE)
    store.fill_error_excerpts(db, mw, "prod")
    assert db.written[0][1] == "LOG" and mw.calls == 1


def test_a_monitor_still_alerting_reopens_what_this_module_resolved_but_not_what_a_person_did():
    store = DdMemoryStore()
    dm.process(store, FakeClient([monitor()]), ON, NOW)
    # the admin narrows the tags: the monitor leaves the watched set and its incident resolves
    assert dm.process(store, FakeClient([]), ON, NOW + timedelta(minutes=2))["resolved"] == 1
    # tags restored, same trigger, still alerting: it reopens
    assert dm.process(store, FakeClient([monitor()]), ON, NOW + timedelta(minutes=4))["actions"] == {"reopened": 1}
    (i,) = _dd_incidents(store)
    assert i["status"] == "OPEN"
    # a person resolves it while the monitor still alerts: it stays resolved until the monitor triggers again
    inc.resolve(store, i["incident_id"], "handled by hand", "ana", NOW + timedelta(minutes=5))
    assert dm.process(store, FakeClient([monitor()]), ON, NOW + timedelta(minutes=6))["actions"] == {"duplicate": 1}
    assert _dd_incidents(store)[0]["status"] == "RESOLVED"


def test_muted_and_downtime_groups_open_nothing_and_groups_are_capped():
    groups = {"env:prod": {"status": "Alert", "last_triggered_ts": 1792663200},
              "env:dev": {"status": "Alert", "last_triggered_ts": 1792663300}}
    muted = monitor(groups=groups, options={"silenced": {"env:dev": None}})
    assert [g for g, _, _ in dm.alerting_groups(muted, ["Alert"], NOW)] == ["env:prod"]
    down = monitor(groups=groups, matching_downtimes=[{"active": True, "scope": ["*"]}])
    assert dm.alerting_groups(down, ["Alert"], NOW) == []
    expired = monitor(options={"silenced": {"*": int((NOW - timedelta(hours=1)).timestamp())}})
    assert len(dm.alerting_groups(expired, ["Alert"], NOW)) == 1
    many = monitor(groups={f"host:{i}": {"status": "Alert", "last_triggered_ts": 1792663200 + i} for i in range(60)})
    assert len(dm.alerting_groups(many, ["Alert"], NOW)) == dm.MAX_GROUPS
    # no trigger time anywhere: the key still changes per hour, never constant
    bare = monitor(overall_state_modified=None)
    assert dm.alerting_groups(bare, ["Alert"], NOW)[0][2] == NOW.replace(minute=0, second=0, microsecond=0).isoformat()


def test_a_cut_list_never_resolves_a_monitor_it_did_not_read(monkeypatch):
    store = DdMemoryStore()
    dm.process(store, FakeClient([monitor()]), ON, NOW)
    monkeypatch.setattr(dd, "MONITOR_PAGES", 1)
    monkeypatch.setattr(dd, "MONITOR_PAGE", 1)
    assert dm.process(store, FakeClient([monitor(99, "OK")]), ON, NOW + timedelta(minutes=2))["resolved"] == 0


# ---------------------------------------------------------------- host and service: one organisation, many apps

def test_host_and_service_come_from_the_environment_and_are_never_shown():
    cfg = dd.config_from_env({**ENV, "DATADOG_HOST": "ip-10-0-1-5, ip-10-0-2-*", "DATADOG_SERVICE": "airflow-gdp"})
    assert cfg["hosts"] == ["ip-10-0-1-5", "ip-10-0-2-*"] and cfg["services"] == ["airflow-gdp"] and not cfg["problems"]
    shown = dd.public(cfg)
    assert shown["hosts_set"] == 2 and shown["services_set"] == 1
    assert "ip-10-0-1-5" not in repr(shown) and "airflow-gdp" not in repr(shown)
    bad = dd.config_from_env({**ENV, "DATADOG_SERVICE": "ok, not ok"})
    assert bad["problems"] == ["DATADOG_SERVICE has 1 name(s) that are not Datadog service names"]


def test_the_setting_s_hosts_and_services_win_over_the_environment():
    value, problems = dd.validate_setting({"hosts": ["mwaa-worker-*", " "], "services": ["bad name!"]})
    assert value["hosts"] == ["mwaa-worker-*"] and problems == ["'bad name!' is not a Datadog service name"]
    cfg = {"hosts": ["env-host"], "services": ["env-svc"]}
    assert dd.scope({"hosts": ["admin-host"], "services": []}, cfg) == {"host": ["admin-host"], "service": ["env-svc"]}
    assert dd.scope(dd.DEFAULT_SETTING, None) == {"host": [], "service": []}


def test_host_and_service_narrow_every_log_search():
    scopes = {"host": ["i-0abc", "ip-10-*"], "service": ["airflow"]}
    # added when the search does not name them; * stays a wildcard, other characters are escaped
    assert dd.scoped_query("dag_id:{dag_id} task_id:{task_id}", {"dag_id": "orders"}, scopes) == \
        r"dag_id:orders host:(i\-0abc OR ip\-10\-*) service:airflow"
    # placed where the search names them, and not added twice
    assert dd.scoped_query("service:{service} dag_id:{dag_id}", {"dag_id": "orders"}, {"host": [], "service": ["mwaa"]}) == \
        "service:mwaa dag_id:orders"
    # none set: the search is unchanged, and a {host} term without hosts is left out
    assert dd.scoped_query("dag_id:{dag_id} host:{host}", {"dag_id": "x"}, {"host": [], "service": []}) == "dag_id:x"


def test_an_incident_s_search_carries_the_host_and_service():
    c, http = client({"/api/v2/logs/events/search": [(200, {"data": []}, {})]})
    c.cfg = {**c.cfg, "hosts": ["env-host"], "services": ["airflow-gdp"]}
    dd.incident_logs({"dag_id": "orders", "task_id": "load", "first_seen": "2026-10-22T10:00:00+00:00"},
                     {**ON, "hosts": ["admin-host"]}, c, now=datetime(2026, 10, 22, 12, tzinfo=timezone.utc))
    assert http.calls[0]["body"]["filter"]["query"] == "dag_id:orders task_id:load host:admin\\-host service:airflow\\-gdp"
