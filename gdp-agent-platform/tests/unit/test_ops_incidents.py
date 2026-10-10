"""Ops incidents (PR O2): fingerprints, detection kinds and timing, open / occurrence / reopen / new, storm grouping,
routing and mutes, severity, Jira ticket dedupe and throttling, Teams cards, the outbox (backoff, dead letter, team
rate limit), escalation, auto-resolve, webhook URL validation and the governance mapping of the new routes."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from services.governance.policy import (  # noqa: E402
    SYSTEM_ROLES, carries_secret, decide, effective_privileges, privilege_for,
)
from services.jira.client import JiraClient, JiraError  # noqa: E402
from services.ops import detect, incidents as inc, notify, tickets  # noqa: E402

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
ROLE_PRIVS = {name: spec["privileges"] for name, spec in SYSTEM_ROLES.items()}
GRANTS = {name: spec["inherits"] for name, spec in SYSTEM_ROLES.items()}


def iso(dt):
    return dt.isoformat()


# ---------------------------------------------------------------- in-memory store

class MemoryStore:
    def __init__(self, now=NOW):
        self.now = now
        self.incidents, self.events, self.notes, self.claims = {}, [], [], {}
        self.dag_meta, self.env_meta, self.rule_list, self.teams, self.deps = {}, {}, [], {}, set()
        self.cfg = {"reopen_hours": 24}
        self.jira = {"site_url": "https://acme.atlassian.net", "default_project": "OPS"}
        self.sla = ([], [])

    # settings and metadata
    def settings(self):
        return inc.settings_from(self.cfg)

    def jira_config(self):
        return dict(self.jira)

    def dags(self, keys):
        return {k: self.dag_meta[k] for k in keys if k in self.dag_meta}

    def envs(self):
        return self.env_meta

    def rules(self):
        return list(self.rule_list)

    def team(self, team_id):
        return self.teams.get(team_id)

    # claims and events
    def claim(self, key):
        if key in self.claims:
            return False
        self.claims[key] = {"incident_id": None, "kind": "claim", "stale": False}
        return True

    def reclaim_stale(self, key, minutes):
        c = self.claims.get(key)
        if c and c["kind"] == "claim" and c["stale"]:
            c["stale"] = False
            return True
        return False

    def release_claim(self, key):
        self.claims[key].update({"kind": "claim", "stale": True})

    def bind(self, key, incident_id, kind, actor="system", detail=None):
        self.claims[key].update({"incident_id": incident_id, "kind": kind})
        self.events.append({"incident_id": incident_id, "kind": kind, "actor": actor, "detail": detail or {}})

    def claimed_incident(self, key):
        return (self.claims.get(key) or {}).get("incident_id")

    def event(self, incident_id, kind, actor="system", detail=None):
        self.events.append({"incident_id": incident_id, "kind": kind, "actor": actor, "detail": detail or {}})

    # incidents
    def get(self, incident_id):
        found = self.incidents.get(incident_id)
        return dict(found) if found else None

    def latest_by_fingerprint(self, fp):
        found = [i for i in self.incidents.values() if i["fingerprint"] == fp]
        return dict(max(found, key=lambda i: i["first_seen"])) if found else None

    def task_incident_for_run(self, env_id, dag_id, run_id):
        return next((dict(i) for i in self.incidents.values() if (i["env_id"], i["dag_id"], i["run_id"]) == (env_id, dag_id, run_id)
                     and i.get("task_id")), None)

    def active_for_dags(self, keys):
        return [dict(i) for i in self.incidents.values() if (i["env_id"], i["dag_id"]) in keys and i["status"] in inc.ACTIVE]

    def children(self, incident_id):
        return [dict(i) for i in self.incidents.values() if i.get("parent_incident_id") == incident_id]

    def insert(self, incident):
        row = {c: None for c in inc.INCIDENT_COLUMNS}
        row.update(incident)
        self.incidents[incident["incident_id"]] = row

    def update(self, incident_id, fields, add_occurrence=False, only_status=None):
        row = self.incidents.get(incident_id)
        if not row or (only_status and row["status"] not in only_status):
            return 0
        row.update(fields)
        if add_occurrence:
            row["occurrences"] = int(row.get("occurrences") or 0) + 1
        return 1

    def storm_parent(self, env_id, dag_id):
        ups = {u for e, u, d in self.deps if e == env_id and d == dag_id and u != d}
        for i in self.incidents.values():
            if i["env_id"] == env_id and i["dag_id"] in ups and i["status"] in ("OPEN", "ACK") and not i.get("parent_incident_id") \
                    and datetime.fromisoformat(i["last_seen"]) >= self.now - timedelta(hours=2):
                return i["incident_id"]
        return None

    def escalation_candidates(self):
        out = []
        for i in self.incidents.values():
            if i["status"] == "OPEN" and not i.get("parent_incident_id") and i.get("team_id") in self.teams \
                    and int(i.get("escalations") or 0) < inc.MAX_ESCALATIONS:
                out.append({**i, "escalation_minutes": self.teams[i["team_id"]].get("escalation_minutes")})
        return out

    def expired_mutes(self):
        return [dict(i) for i in self.incidents.values() if i["status"] == "MUTED" and i.get("muted_until")
                and datetime.fromisoformat(i["muted_until"]) < self.now]

    def sla_inputs(self):
        return self.sla

    def ticketed_open(self):
        return [dict(i) for i in self.incidents.values() if i.get("jira_key") and i["status"] in ("OPEN", "ACK")]

    def enqueue(self, channel, kind, dedupe_key, payload=None, incident_id=None, team_id=None, target=None, status="PENDING",
                delay_seconds=0, error=None):
        if any(n["dedupe_key"] == dedupe_key for n in self.notes):
            return False
        self.notes.append({"channel": channel, "kind": kind, "dedupe_key": dedupe_key, "payload": payload,
                           "incident_id": incident_id, "team_id": team_id, "target": target, "status": status})
        return True

    # helpers
    def kinds(self, incident_id):
        return [e["kind"] for e in self.events if e["incident_id"] == incident_id]

    def notes_for(self, incident_id, channel=None):
        return [n for n in self.notes if n["incident_id"] == incident_id and (channel is None or n["channel"] == channel)]


def task(run="r1", state="failed", tid="load", try_number=1, ended=None, error="ValueError: bad row 42", dag="orders", env="prod",
         map_index=-1):
    return {"env_id": env, "dag_id": dag, "run_id": run, "task_id": tid, "map_index": map_index, "try_number": try_number,
            "state": state, "ended_at": iso(ended or NOW - timedelta(minutes=5)), "updated_at": iso(ended or NOW - timedelta(minutes=5)),
            "error_excerpt": error}


def run(run_id="r1", state="failed", ended=None, dag="orders", env="prod", run_type="scheduled", started=None):
    return {"env_id": env, "dag_id": dag, "run_id": run_id, "state": state, "run_type": run_type, "external_trigger": False,
            "started_at": iso(started or NOW - timedelta(minutes=30)),
            "ended_at": iso(ended or NOW - timedelta(minutes=4)) if state in ("failed", "success") else None,
            "updated_at": iso(ended or NOW - timedelta(minutes=4))}


def store_with_dag(**dag):
    s = MemoryStore()
    s.env_meta = {"prod": {"env_id": "prod", "name": "Production"}}
    s.dag_meta[("prod", "orders")] = {"env_id": "prod", "dag_id": "orders", "criticality": "HIGH", "team_id": "data",
                                      "tags": ["sales"], "owners": ["ana"], **dag}
    s.teams["data"] = {"team_id": "data", "name": "Data team", "jira_project": "DATA", "jira_component": "Pipelines",
                       "jira_assignee_account_id": "acc-1", "escalation_minutes": 30}
    return s


# ---------------------------------------------------------------- fingerprint

def test_fingerprint_normalizes_ids_times_numbers_paths_and_literals():
    one = """Traceback (most recent call last):
  File "/usr/local/airflow/dags/orders.py", line 12, in load
snowflake.connector.errors.ProgrammingError: 002003 (42S02): SQL compilation error: Object 'DB.SALES.ORDERS_20240501' does not exist at /tmp/a1b2c3d4e5f6/x.sql (query 01b2c3d4-0000-1111-2222-333344445555) 2024-05-01T02:00:00Z"""
    two = """Traceback (most recent call last):
  File "/usr/local/airflow/dags/orders.py", line 99, in load
snowflake.connector.errors.ProgrammingError: 002003 (42S02): SQL compilation error: Object 'DB.SALES.ORDERS_20240502' does not exist at /tmp/ffeeddccbbaa/y.sql (query 99b2c3d4-0000-1111-2222-333344445555) 2025-01-01T00:00:00Z"""
    assert detect.last_exception_line(one).startswith("snowflake.connector.errors.ProgrammingError")
    assert detect.signature(one) == detect.signature(two)
    sig = detect.signature(one)
    assert "<str>" in sig and "<path>" in sig and "<id>" in sig and "<ts>" in sig and "<n>" in sig and "42" not in sig
    assert detect.fingerprint("prod", "orders", "load", one) == detect.fingerprint("prod", "orders", "load", two)
    other = "KeyError: 'customer_id'"
    assert detect.fingerprint("prod", "orders", "load", one) != detect.fingerprint("prod", "orders", "load", other)
    assert detect.fingerprint("prod", "orders", "load", one) != detect.fingerprint("stage", "orders", "load", one)
    assert len(detect.fingerprint("prod", "orders", None, None)) == 40
    # Airflow log prefixes are ignored
    assert detect.last_exception_line("[2024-05-01, 02:00:00 UTC] {taskinstance.py:1937} ERROR - ValueError: x") == "ValueError: x"


# ---------------------------------------------------------------- detection

def test_detect_kinds():
    dag = {"env_id": "prod", "dag_id": "orders", "criticality": "HIGH"}
    env = {"name": "Production"}
    found = detect.from_task(task(), dag, env, {})
    assert found["kind"] == "RETRIES_EXHAUSTED" and found["task_id"] == "load" and found["occurrence_key"] == "task:r1:load:-1:1"
    assert detect.from_task(task(state="up_for_retry"), dag, env, {"alert_on_retry_for_critical": True}) is None
    critical = {**dag, "criticality": "CRITICAL"}
    assert detect.from_task(task(state="up_for_retry"), critical, env, {"alert_on_retry_for_critical": False}) is None
    retrying = detect.from_task(task(state="up_for_retry"), critical, env, {"alert_on_retry_for_critical": True})
    assert retrying["kind"] == "RETRIES_EXHAUSTED" and "retrying" in retrying["title"]
    assert retrying["fingerprint"] == detect.from_task(task(try_number=2), critical, env, {})["fingerprint"]
    for state in ("upstream_failed", "success", "running", "skipped"):
        assert detect.from_task(task(state=state), dag, env, {}) is None
    assert detect.from_run(run(), dag, env)["kind"] == "FAILED"
    assert detect.from_run(run(state="success"), dag, env) is None
    # a DAG run failing because of a task failure in the same batch is one incident, not two
    both = detect.candidates([run()], [task()], {("prod", "orders"): dag}, {"prod": env}, {})
    assert [c["kind"] for c in both] == ["RETRIES_EXHAUSTED"]
    assert [c["kind"] for c in detect.candidates([run()], [], {}, {}, {})] == ["FAILED"]
    # the excerpt is redacted before it is kept
    secret = detect.from_task(task(error="ValueError: password=hunter2 rejected"), dag, env, {})
    assert "hunter2" not in secret["error_excerpt"]


def test_cron_helper_without_croniter():
    every = detect.prev_fire("*/15 * * * *", datetime(2026, 10, 10, 12, 7, tzinfo=timezone.utc))
    assert every == datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    assert detect.prev_fire("0 9 * * 1-5", saturday) == datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
    assert detect.prev_fire("@daily", saturday) == datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc)
    assert detect.prev_fire("30 6 * * MON", saturday) == datetime(2026, 10, 5, 6, 30, tzinfo=timezone.utc)
    # read in the DAG's timezone: 06:00 in Kolkata is 00:30 UTC
    assert detect.prev_fire("0 6 * * *", saturday, "Asia/Kolkata") == datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        detect.parse_cron("61 * * * *")


def test_late_timing():
    dag = {"env_id": "prod", "dag_id": "orders", "criticality": "HIGH", "expected_by_cron": "0 6 * * *"}
    env = {"name": "Production"}
    at_seven = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc)
    late = detect.late(dag, env, datetime(2026, 10, 9, 5, 50, tzinfo=timezone.utc), at_seven)
    assert late["kind"] == "LATE" and late["occurrence_key"] == "late:2026-10-10T06:00:00+00:00"
    assert late["at"] == "2026-10-10T06:00:00.000000+00:00"
    assert detect.late(dag, env, None, at_seven)["kind"] == "LATE"
    assert detect.late(dag, env, datetime(2026, 10, 10, 5, 0, tzinfo=timezone.utc), at_seven) is None
    # before today's expected time, yesterday's success is enough
    assert detect.late(dag, env, datetime(2026, 10, 9, 5, 50, tzinfo=timezone.utc),
                       datetime(2026, 10, 10, 5, 59, tzinfo=timezone.utc)) is None
    assert detect.late({**dag, "is_paused": True}, env, None, at_seven) is None
    assert detect.late({**dag, "expected_by_cron": None}, env, None, at_seven) is None


def test_long_running_timing_and_end():
    dag = {"env_id": "prod", "dag_id": "orders", "criticality": "HIGH", "max_duration_min": 60}
    running = run(state="running", started=NOW - timedelta(minutes=90))
    found = detect.long_running(running, dag, {}, NOW)
    assert found["kind"] == "LONG_RUNNING" and found["occurrence_key"] == "long:r1" and found["severity"] == "P3"
    assert detect.long_running(run(state="running", started=NOW - timedelta(minutes=30)), dag, {}, NOW) is None
    assert detect.long_running(running, {**dag, "max_duration_min": None}, {}, NOW) is None
    incident = {"env_id": "prod", "dag_id": "orders", "kind": "LONG_RUNNING", "status": "OPEN", "run_id": "r1",
                "last_seen": iso(NOW)}
    finished = detect.successes([run(state="failed", ended=NOW - timedelta(minutes=1))], [])
    assert detect.resolves(incident, finished[0])          # the run ended (even before the SLA check's clock)
    other = detect.successes([run(run_id="r2", state="success")], [])
    assert not detect.resolves(incident, other[0])


def test_severity():
    assert detect.severity("CRITICAL", "FAILED", ("prod", "Production")) == "P1"
    assert detect.severity("CRITICAL", "FAILED", ("dev", "Development")) == "P2"
    assert detect.severity("HIGH", "RETRIES_EXHAUSTED", ("prd-eu",)) == "P2"
    assert detect.severity("HIGH", "LONG_RUNNING", ("production",)) == "P3"
    assert detect.severity("CRITICAL", "FAILED", ("prod",), run_type="backfill") == "P2"
    assert detect.severity("CRITICAL", "FAILED", ("prod",), run_type="manual", external_trigger=True) == "P2"
    assert detect.severity(None, "FAILED", ("dev",)) == "P4"
    assert detect.severity("LOW", "FAILED", ("produce",)) == "P4"     # not a prod name
    assert detect.severity("LOW", "FAILED", ("dev",), override="p1") == "P1"


# ---------------------------------------------------------------- open, occurrence, reopen, new

def cand(store, t=None, **kw):
    t = t or task(**kw)
    dag = store.dag_meta[("prod", "orders")]
    return detect.from_task(t, dag, store.env_meta["prod"], {})


def test_a_failed_attempt_releases_its_claim():
    s = store_with_dag()
    real_insert = s.insert

    def broken(incident):
        raise RuntimeError("warehouse suspended")

    s.insert = broken
    with pytest.raises(RuntimeError):
        inc.open_or_update(s, cand(s), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)
    s.insert = real_insert
    retry = inc.open_or_update(s, cand(s), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)
    assert retry["action"] == "opened" and retry["incident_id"] in s.incidents


def test_open_occurrence_duplicate_reopen_and_new():
    s = store_with_dag()
    first = inc.open_or_update(s, cand(s), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)
    iid = first["incident_id"]
    assert first["action"] == "opened"
    row = s.incidents[iid]
    assert row["status"] == "OPEN" and row["team_id"] == "data" and row["severity"] == "P2" and row["jira_state"] == "PENDING"
    assert {n["kind"] for n in s.notes_for(iid)} == {"opened", "create"}
    # the same sighting again (push and poll): nothing changes
    assert inc.open_or_update(s, cand(s), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)["action"] == "duplicate"
    assert s.incidents[iid]["occurrences"] == 1
    # a retry failure of the same error in the next run: one more occurrence, no new ticket
    second = inc.open_or_update(s, cand(s, run="r2", ended=NOW - timedelta(minutes=1)), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)
    assert second == {"action": "occurrence", "incident_id": iid} and s.incidents[iid]["occurrences"] == 2
    assert s.incidents[iid]["run_id"] == "r2" and len(s.notes_for(iid, "JIRA")) == 1
    assert [n["kind"] for n in s.notes_for(iid, "TEAMS")] == ["opened", "reoccurred"]
    # resolved, then failing again within the reopen window: reopened
    inc.resolve(s, iid, "fixed the source file", "ana", NOW)
    later = NOW + timedelta(hours=2)
    again = inc.open_or_update(s, cand(s, run="r3", ended=later), s.settings(), s.dag_meta[("prod", "orders")], [], later)
    assert again == {"action": "reopened", "incident_id": iid}
    assert s.incidents[iid]["status"] == "OPEN" and s.incidents[iid]["resolved_at"] is None and "reopened" in s.kinds(iid)
    # an old failure read again after the resolution is stale
    inc.resolve(s, iid, "fixed again", "ana", later)
    stale = inc.open_or_update(s, cand(s, run="r0", ended=NOW - timedelta(hours=1)), s.settings(), s.dag_meta[("prod", "orders")], [], later)
    assert stale["action"] == "stale" and s.incidents[iid]["status"] == "RESOLVED"
    # beyond the reopen window: a new incident
    much_later = later + timedelta(hours=30)
    fresh = inc.open_or_update(s, cand(s, run="r9", ended=much_later), s.settings(), s.dag_meta[("prod", "orders")], [], much_later)
    assert fresh["action"] == "opened" and fresh["incident_id"] != iid


def test_dag_failure_is_covered_by_its_task_incident_and_late_excerpt_is_filled():
    s = store_with_dag()
    no_excerpt = cand(s, error=None)
    opened = inc.open_or_update(s, no_excerpt, s.settings(), s.dag_meta[("prod", "orders")], [], NOW)
    covered = inc.open_or_update(s, detect.from_run(run(), s.dag_meta[("prod", "orders")], {}), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)
    assert covered == {"action": "covered", "incident_id": opened["incident_id"]} and len(s.incidents) == 1
    # the poller fills the excerpt later: the same sighting gets it, with the fingerprint it gives
    with_excerpt = cand(s)
    assert inc.open_or_update(s, with_excerpt, s.settings(), s.dag_meta[("prod", "orders")], [], NOW)["action"] == "duplicate"
    row = s.incidents[opened["incident_id"]]
    assert row["error_excerpt"] and row["fingerprint"] == with_excerpt["fingerprint"]


def test_storm_child_has_no_ticket_and_no_card():
    s = store_with_dag()
    s.dag_meta[("prod", "billing")] = {"env_id": "prod", "dag_id": "billing", "team_id": "data"}
    s.deps.add(("prod", "orders", "billing"))
    parent = inc.open_or_update(s, cand(s), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)
    child_c = detect.from_task(task(dag="billing", error="KeyError: 'x'"), s.dag_meta[("prod", "billing")], {}, {})
    child = inc.open_or_update(s, child_c, s.settings(), s.dag_meta[("prod", "billing")], [], NOW)
    row = s.incidents[child["incident_id"]]
    assert child["action"] == "child" and row["kind"] == "UPSTREAM" and row["parent_incident_id"] == parent["incident_id"]
    assert row["jira_state"] == "SKIPPED" and s.notes_for(child["incident_id"]) == []
    assert "child_added" in s.kinds(parent["incident_id"]) and "grouped" in s.kinds(child["incident_id"])
    # resolving the parent resolves the child
    inc.resolve(s, parent["incident_id"], "upstream fixed", "ana", NOW)
    assert s.incidents[child["incident_id"]]["status"] == "RESOLVED" and s.incidents[child["incident_id"]]["resolved_by"] == "system"


def test_routing_priority_override_and_mutes():
    dag = {"env_id": "prod", "dag_id": "sales_daily", "tags": ["finance"], "owners": ["ana"], "team_id": "fallback"}
    rules = [{"rule_id": "a", "priority": 10, "dag_pattern": "sales_*", "team_id": "sales", "enabled": True},
             {"rule_id": "b", "priority": 5, "tag": "FINANCE", "team_id": "finance", "enabled": True},
             {"rule_id": "c", "priority": 1, "dag_pattern": "sales_*", "env_id": "stage", "team_id": "stage", "enabled": True},
             {"rule_id": "d", "priority": 0, "owner": "bob", "team_id": "bob", "enabled": True},
             {"rule_id": "e", "priority": 0, "dag_pattern": "*", "team_id": "off", "enabled": False}]
    assert inc.route(rules, dag, "prod")["rule_id"] == "b"
    assert inc.route(rules, dag, "stage")["rule_id"] == "c"
    assert inc.route(rules, {**dag, "tags": []}, "prod")["rule_id"] == "a"
    assert inc.route(rules, {"dag_id": "other"}, "prod") is None

    s = store_with_dag()
    rule = {"rule_id": "r", "priority": 1, "dag_pattern": "ord*", "team_id": "ops", "severity_override": "P1", "enabled": True}
    opened = inc.open_or_update(s, cand(s), s.settings(), s.dag_meta[("prod", "orders")], [rule], NOW)
    row = s.incidents[opened["incident_id"]]
    assert row["team_id"] == "ops" and row["severity"] == "P1"
    # unrouted: created, ticketed, no card
    s2 = store_with_dag(team_id=None)
    lone = inc.open_or_update(s2, cand(s2), s2.settings(), s2.dag_meta[("prod", "orders")], [], NOW)
    assert s2.incidents[lone["incident_id"]]["team_id"] is None
    assert [n["channel"] for n in s2.notes_for(lone["incident_id"])] == ["JIRA"]
    # a rule mute and a DAG mute open the incident MUTED with no ticket or card
    for muted_rule, dag_mute in (({**rule, "mute_until": iso(NOW + timedelta(hours=3)), "mute_reason": "maintenance"}, None),
                                 (None, iso(NOW + timedelta(hours=1)))):
        s3 = store_with_dag(mute_until=dag_mute, mute_reason="deploy")
        result = inc.open_or_update(s3, cand(s3), s3.settings(), s3.dag_meta[("prod", "orders")],
                                    [muted_rule] if muted_rule else [], NOW)
        row = s3.incidents[result["incident_id"]]
        assert result["action"] == "muted" and row["status"] == "MUTED" and row["jira_state"] == "SKIPPED"
        assert s3.notes_for(result["incident_id"]) == [] and "muted" in s3.kinds(result["incident_id"])
        # the mute ends: back to OPEN, card and ticket queued
        s3.now = NOW + timedelta(hours=4)
        assert inc.escalate(s3, s3.now)["unmuted"] == 1
        assert s3.incidents[result["incident_id"]]["status"] == "OPEN"
        assert {n["channel"] for n in s3.notes_for(result["incident_id"])} == {"TEAMS", "JIRA"}
    # an expired rule mute does nothing
    s4 = store_with_dag()
    old = {**rule, "mute_until": iso(NOW - timedelta(hours=1))}
    assert inc.open_or_update(s4, cand(s4), s4.settings(), s4.dag_meta[("prod", "orders")], [old], NOW)["action"] == "opened"


def test_actions_and_errors():
    s = store_with_dag()
    iid = inc.open_or_update(s, cand(s), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)["incident_id"]
    assert inc.ack(s, iid, "ana", NOW)["status"] == "ACK" and s.incidents[iid]["assignee"] == "ana"
    with pytest.raises(inc.ActionError):
        inc.mute(s, iid, iso(NOW + timedelta(days=31)), "too long", "ana", NOW)
    with pytest.raises(inc.ActionError):
        inc.mute(s, iid, iso(NOW - timedelta(minutes=1)), "past", "ana", NOW)
    assert inc.mute(s, iid, iso(NOW + timedelta(days=2)), "known issue", "ana", NOW)["status"] == "MUTED"
    assert inc.assign(s, iid, "bob", "ana")["assignee"] == "bob"
    inc.comment(s, iid, "token=abc123secret seen in the log", "ana")
    assert "abc123secret" not in json.dumps(s.events)
    with pytest.raises(inc.ActionError) as err:
        inc.resolve(s, iid, "no", "ana", NOW)
    assert err.value.status == 422
    inc.resolve(s, iid, "rotated the key", "ana", NOW)
    with pytest.raises(inc.ActionError):
        inc.ack(s, iid, "ana", NOW)
    assert inc.reopen(s, iid, "ana", NOW)["status"] == "OPEN"
    with pytest.raises(inc.ActionError) as missing:
        inc.ack(s, "nope", "ana")
    assert missing.value.status == 404


# ---------------------------------------------------------------- auto-resolve

def test_auto_resolve_on_a_later_success():
    s = store_with_dag()
    result = inc.process(s, [run()], [task()], s.settings(), NOW)
    assert result["actions"] == {"opened": 1} and len(s.incidents) == 1
    iid = next(iter(s.incidents))
    s.incidents[iid]["jira_key"] = "DATA-7"
    # a success of another map index in the same run does not resolve it
    inc.process(s, [], [task(state="success", map_index=3, ended=NOW + timedelta(minutes=1))], s.settings(), NOW)
    s.incidents[iid]["map_index"] = 1
    inc.process(s, [], [task(state="success", map_index=3, ended=NOW + timedelta(minutes=1))], s.settings(), NOW)
    assert s.incidents[iid]["status"] == "OPEN"
    # the next run's success of the same task resolves it as 'system', with a card and a Jira comment
    later = NOW + timedelta(hours=1)
    out = inc.process(s, [run(run_id="r2", state="success", ended=later)],
                      [task(run="r2", state="success", ended=later)], s.settings(), later)
    assert out["resolved"] == 1
    row = s.incidents[iid]
    assert row["status"] == "RESOLVED" and row["resolved_by"] == "system" and "succeeded" in row["resolution"]
    assert {n["kind"] for n in s.notes_for(iid)} >= {"resolved", "resolve"}
    # an older success never resolves a newer failure
    s2 = store_with_dag()
    inc.process(s2, [], [task()], s2.settings(), NOW)
    inc.process(s2, [], [task(run="r0", state="success", ended=NOW - timedelta(hours=1))], s2.settings(), NOW)
    assert next(iter(s2.incidents.values()))["status"] == "OPEN"


def test_sla_job_opens_late_once():
    s = store_with_dag()
    dag = {**s.dag_meta[("prod", "orders")], "expected_by_cron": "0 6 * * *", "last_success_at": iso(NOW - timedelta(days=2))}
    s.sla = ([dag], [])
    assert inc.sla_check(s, s.settings(), NOW)["actions"] == {"opened": 1}
    assert inc.sla_check(s, s.settings(), NOW + timedelta(minutes=2))["actions"] == {"duplicate": 1}
    row = next(iter(s.incidents.values()))
    assert row["kind"] == "LATE" and row["task_id"] is None


# ---------------------------------------------------------------- escalation

def test_escalation_schedule_and_p1_immediate():
    opened = NOW
    assert inc.next_escalation_at(opened, 30, "P3", 0) == NOW + timedelta(minutes=30)
    assert inc.next_escalation_at(opened, 30, "P3", 1) == NOW + timedelta(minutes=90)
    assert inc.next_escalation_at(opened, 30, "P3", 2) == NOW + timedelta(minutes=150)
    assert inc.next_escalation_at(opened, 30, "P3", 3) is None
    assert inc.next_escalation_at(opened, 30, "P1", 0) == NOW
    assert inc.next_escalation_at(opened, 30, "P1", 1) == NOW + timedelta(minutes=60)

    s = store_with_dag()
    iid = inc.open_or_update(s, cand(s), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)["incident_id"]
    assert inc.escalate(s, NOW + timedelta(minutes=10))["escalated"] == 0
    assert inc.escalate(s, NOW + timedelta(minutes=31))["escalated"] == 1
    assert inc.escalate(s, NOW + timedelta(minutes=32))["escalated"] == 0       # next one at +90
    esc = [n for n in s.notes_for(iid, "TEAMS") if n["kind"] == "escalated"]
    assert len(esc) == 1 and esc[0]["target"] == "escalation" and "escalated" in s.kinds(iid)
    for minutes in (91, 151, 400):
        inc.escalate(s, NOW + timedelta(minutes=minutes))
    assert s.incidents[iid]["escalations"] == 3
    # acknowledged incidents are not escalated; P1 escalates at once
    s2 = store_with_dag(criticality="CRITICAL")
    p1 = inc.open_or_update(s2, cand(s2), s2.settings(), s2.dag_meta[("prod", "orders")], [], NOW)["incident_id"]
    assert s2.incidents[p1]["severity"] == "P1"
    assert inc.escalate(s2, NOW)["escalated"] == 1
    s3 = store_with_dag()
    acked = inc.open_or_update(s3, cand(s3), s3.settings(), s3.dag_meta[("prod", "orders")], [], NOW)["incident_id"]
    inc.ack(s3, acked, "ana", NOW)
    assert inc.escalate(s3, NOW + timedelta(hours=5))["escalated"] == 0


# ---------------------------------------------------------------- Jira tickets

class FakeJira:
    def __init__(self, found=None, fail=False):
        self.found, self.fail = found or [], fail
        self.created, self.comments, self.moved, self.searches = [], [], [], []

    def search(self, jql, fields=None, page_size=50, max_pages=5):
        self.searches.append(jql)
        return self.found

    def issue_types(self, project):
        return [{"id": "1", "name": "Task", "subtask": False}, {"id": "2", "name": "Bug", "subtask": False}]

    def _call(self, method, path, body=None, ok=(200, 201, 204)):
        if self.fail:
            raise JiraError(400, "Field 'components' is invalid")
        self.created.append(body)
        return {"key": f"DATA-{len(self.created)}"}

    def add_comment(self, key, adf):
        self.comments.append((key, json.dumps(adf)))

    def transitions(self, key):
        return [{"id": "11", "name": "Start", "to": "In Progress", "category": "indeterminate"},
                {"id": "31", "name": "Close", "to": "Done", "category": "done"}]

    def transition(self, key, tid):
        self.moved.append((key, tid))


def opened_incident(s, **kw):
    return inc.open_or_update(s, cand(s, **kw), s.settings(), s.dag_meta[("prod", "orders")], [], NOW)["incident_id"]


def test_ticket_dedupe_levels():
    s = store_with_dag()
    iid = opened_incident(s)
    # 1. the incident already has a key
    s.incidents[iid]["jira_key"] = "DATA-1"
    jira = FakeJira()
    assert tickets.raise_ticket(s, iid, jira, s.settings())["state"] == "exists" and not jira.created and not jira.searches
    s.incidents[iid]["jira_key"] = None
    # 2. an open ticket with the fingerprint label is linked
    jira = FakeJira(found=[{"key": "DATA-9"}])
    result = tickets.raise_ticket(s, iid, jira, s.settings())
    assert result == {"state": "linked", "key": "DATA-9", "detail": "linked to DATA-9"} and not jira.created
    assert f'labels = "gdp-inc-{s.incidents[iid]["fingerprint"][:8]}"' in jira.searches[0] and "statusCategory != Done" in jira.searches[0]
    s.incidents[iid]["jira_key"] = None
    # 3. another worker holds the claim: no second ticket
    s.claim(f"jira:create:{iid}")
    jira = FakeJira()
    assert tickets.raise_ticket(s, iid, jira, s.settings())["state"] == "in_progress" and not jira.created
    # a failed attempt releases the claim; the retry creates exactly one ticket with the team's routing
    s.release_claim(f"jira:create:{iid}")
    with pytest.raises(JiraError):
        tickets.raise_ticket(s, iid, FakeJira(fail=True), s.settings())
    assert s.incidents[iid]["jira_state"] == "FAILED" and "ticket_failed" in s.kinds(iid)
    jira = FakeJira()
    result = tickets.raise_ticket(s, iid, jira, {"public_base_url": "https://gdp.example.com"})
    assert result["state"] == "raised" and result["key"] == "DATA-1" and len(jira.created) == 1
    fields = jira.created[0]["fields"]
    assert fields["project"] == {"key": "DATA"} and fields["issuetype"] == {"id": "2"}
    assert fields["labels"] == ["gdp-ops", f"gdp-inc-{s.incidents[iid]['fingerprint'][:8]}"]
    assert fields["components"] == [{"name": "Pipelines"}] and fields["assignee"] == {"accountId": "acc-1"}
    body = json.dumps(fields["description"])
    assert "https://gdp.example.com/incidents/" in body and "Impact" in body and "ValueError" in body
    assert s.incidents[iid]["jira_key"] == "DATA-1" and s.incidents[iid]["jira_state"] == "OPEN"
    # the default project when the team has none
    s2 = store_with_dag(team_id=None)
    other = opened_incident(s2)
    jira = FakeJira()
    tickets.raise_ticket(s2, other, jira, s2.settings())
    assert jira.created[0]["fields"]["project"] == {"key": "OPS"} and "components" not in jira.created[0]["fields"]


def test_no_bot_marks_not_raised(monkeypatch):
    monkeypatch.delenv("JIRA_BOT_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_BOT_TOKEN", raising=False)
    assert tickets.bot_client({"site_url": "https://acme.atlassian.net"}) is None
    s = store_with_dag()
    iid = opened_incident(s)
    row = {"incident_id": iid, "kind": "create"}
    assert tickets.handle(s, row, None, s.settings(), NOW) == ("SKIPPED", tickets.NO_BOT)
    assert s.incidents[iid]["jira_state"] == "NOT_RAISED"
    assert any(e["kind"] == "ticket_not_raised" and e["detail"]["reason"] == "ticket not raised: no Jira bot configured"
               for e in s.events)
    monkeypatch.setenv("JIRA_BOT_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_BOT_TOKEN", "tok")
    assert tickets.bot_client({"site_url": ""}) is None
    assert isinstance(tickets.bot_client({"site_url": "https://acme.atlassian.net"}), JiraClient)


def test_jira_basic_client_keeps_oauth_working():
    calls = []

    def http(method, url, headers, body):
        calls.append((url, headers["Authorization"]))
        return 200, {"accountId": "x"}

    JiraClient.basic(http, "https://acme.atlassian.net/", "bot@example.com", "tok").myself()
    JiraClient(http, "0123abcd-0000-1111-2222-333344445555", "acc").myself()
    assert calls[0] == ("https://acme.atlassian.net/rest/api/3/myself", "Basic Ym90QGV4YW1wbGUuY29tOnRvaw==")
    assert calls[1][0].startswith("https://api.atlassian.com/ex/jira/") and calls[1][1] == "Bearer acc"
    with pytest.raises(ValueError):
        JiraClient.basic(http, "ftp://acme.atlassian.net", "bot@example.com", "tok")


def test_recurrence_throttle_and_resolve_transition():
    s = store_with_dag()
    iid = opened_incident(s)
    s.incidents[iid].update({"jira_key": "DATA-3", "occurrences": 4, "jira_synced_occurrences": 1,
                             "jira_commented_at": iso(NOW - timedelta(minutes=10))})
    jira = FakeJira()
    assert tickets.handle(s, {"incident_id": iid, "kind": "recur"}, jira, {}, NOW)[0] == "SKIPPED" and not jira.comments
    s.incidents[iid]["jira_commented_at"] = iso(NOW - timedelta(hours=2))
    assert tickets.handle(s, {"incident_id": iid, "kind": "recur"}, jira, {}, NOW)[0] == "SENT"
    assert "3 more time" in jira.comments[0][1] and s.incidents[iid]["jira_synced_occurrences"] == 4
    assert tickets.handle(s, {"incident_id": iid, "kind": "recur"}, jira, {}, NOW + timedelta(hours=2))[0] == "SKIPPED"
    # occurrences within the hour queue one recur row per hour bucket at most
    for minute in (1, 2, 3):
        inc.open_or_update(s, cand(s, run=f"x{minute}", ended=NOW + timedelta(minutes=minute)), s.settings(), s.dag_meta[("prod", "orders")], [],
                           NOW + timedelta(minutes=minute))
    assert len([n for n in s.notes_for(iid, "JIRA") if n["kind"] == "recur"]) == 1
    # resolve: comment, and a transition only when enabled
    inc.resolve(s, iid, "reran after the fix", "system", NOW)
    jira = FakeJira()
    tickets.handle(s, {"incident_id": iid, "kind": "resolve"}, jira, {"transition_on_resolve": False}, NOW)
    assert jira.comments and not jira.moved
    tickets.handle(s, {"incident_id": iid, "kind": "resolve"}, jira, {"transition_on_resolve": True, "done_status": "Done"}, NOW)
    assert jira.moved == [("DATA-3", "31")] and s.incidents[iid]["jira_state"] == "DONE"


def test_jira_done_marks_mitigated():
    s = store_with_dag()
    iid = opened_incident(s)
    s.incidents[iid]["jira_key"] = "DATA-5"
    jira = FakeJira(found=[{"key": "DATA-5", "fields": {"status": {"name": "Done"}}}])
    assert tickets.sync_done(s, jira) == 1 and s.incidents[iid]["status"] == "MITIGATED"
    assert 'key in ("DATA-5")' in jira.searches[0]
    assert tickets.sync_done(s, None) == 0
    # a new failure after Jira Done reopens it
    inc.open_or_update(s, cand(s, run="r5", ended=NOW + timedelta(minutes=5)), s.settings(), s.dag_meta[("prod", "orders")], [], NOW + timedelta(minutes=5))
    assert s.incidents[iid]["status"] == "OPEN" and "reopened" in s.kinds(iid)


# ---------------------------------------------------------------- Teams cards

WEBHOOK = "https://prod-12.westus.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke?sig=SECRETSIG"


def sample_incident(**kw):
    return {"incident_id": "i-1", "env_id": "prod", "dag_id": "orders", "task_id": "load", "run_id": "r1", "status": "OPEN",
            "severity": "P1", "title": "Task load failed", "occurrences": 3, "jira_key": "DATA-1",
            "error_excerpt": "ValueError: password=hunter2 token: ghp_abcdefghijklmnopqrstuvwxyz0123 for ana@example.com", **kw}


def test_card_payload_has_no_secrets_and_the_right_buttons():
    link = notify.links(sample_incident(), "https://gdp.example.com/", "https://acme.atlassian.net/browse/DATA-1",
                        "https://airflow.example.com/dags/orders/grid?dag_run_id=r1")
    message = notify.card("opened", sample_incident(), link, "Data team")
    text = json.dumps(message)
    assert message["type"] == "message"
    attachment = message["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["content"]["type"] == "AdaptiveCard" and attachment["content"]["version"] == "1.4"
    for secret in ("hunter2", "ghp_abcdefghijklmnopqrstuvwxyz0123", "ana@example.com", "SECRETSIG"):
        assert secret not in text
    actions = attachment["content"]["actions"]
    assert [a["title"] for a in actions] == ["Acknowledge", "Open incident", "Open Jira", "Open Airflow log"]
    assert all(a["type"] == "Action.OpenUrl" for a in actions)
    assert actions[0]["url"] == "https://gdp.example.com/incidents/i-1?ack=1"
    assert actions[1]["url"] == "https://gdp.example.com/incidents/i-1"
    resolved = notify.card("resolved", sample_incident(status="RESOLVED"), link)
    assert "Acknowledge" not in json.dumps(resolved) and "hunter2" not in json.dumps(resolved)
    for kind in ("escalated", "reoccurred"):
        assert notify.card(kind, sample_incident(), link)["attachments"][0]["content"]["actions"][0]["title"] == "Acknowledge"
    # no public URL: no platform buttons
    bare = notify.card("opened", sample_incident(), notify.links(sample_incident(), None))
    assert "actions" not in bare["attachments"][0]["content"]
    storm = notify.storm_card("Data team", 12, ["A failed", "B failed"], "https://gdp.example.com")
    assert "12 more alerts" in json.dumps(storm)
    assert "SECRETSIG" not in json.dumps(notify.test_card("Data team", "alerts", None))


def test_webhook_url_validation():
    for good in ("https://acme.webhook.office.com/webhookb2/x", WEBHOOK, "https://x.powerautomate.com/flows/1",
                 "https://default1234.environment.api.powerplatform.com/powerautomate/automations/direct/workflows/x"):
        assert notify.validate_webhook_url(good) == good
    for bad in ("http://acme.webhook.office.com/x", "https://example.com/hook", "https://webhook.office.com.evil.com/x",
                "https://user:pw@acme.webhook.office.com/x", "https://acme.webhook.office.com:8443/x", "ftp://a.logic.azure.com/x",
                "", "https://outlook.office.com/webhook/x"):
        with pytest.raises(ValueError):
            notify.validate_webhook_url(bad)


# ---------------------------------------------------------------- outbox

def test_backoff_schedule_and_dead_letter():
    assert [notify.backoff_seconds(n) for n in (1, 2, 3, 4, 5)] == [60, 300, 900, 3600, 3600]
    assert notify.after_failure(1) == ("FAILED", 60) and notify.after_failure(5) == ("FAILED", 3600)
    assert notify.after_failure(6) == ("DEAD", None)


def test_team_rate_limit_plan():
    rows = [{"notification_id": str(i), "team_id": "a", "kind": "opened"} for i in range(3)]
    rows += [{"notification_id": "e", "team_id": "a", "kind": "escalated"}, {"notification_id": "b", "team_id": "b", "kind": "opened"}]
    send, suppress = notify.plan_rate_limit(rows, {"a": 9}, 10)
    assert [r["notification_id"] for r in send] == ["0", "e", "b"]
    assert [r["notification_id"] for r in suppress] == ["1", "2"]


class OutboxDb:
    """Answers the outbox's statements: due rows, sent counts, the team webhook, the incident for its card."""

    def __init__(self, rows, sent=0, status=500, echo=True):
        self.rows, self.sent, self.status, self.echo = rows, sent, status, echo
        self.marks, self.merges, self.posts = [], [], []

    def query(self, sql, params=()):
        if "FROM OPS.NOTIFICATION WHERE STATUS IN ('PENDING', 'FAILED', 'SENDING')" in sql:
            return self.rows
        if "COUNT(*) AS N FROM OPS.NOTIFICATION" in sql:
            return [{"team_id": "a", "n": self.sent}] if self.sent else []
        if "TEAMS_WEBHOOK_SECRET AS A" in sql:   # sealed by the API, never Snowflake ENCRYPT with a bound key
            return [{"a": notify.seal_webhook(WEBHOOK, params[0], "TEAMS_WEBHOOK_SECRET"), "e": None}]
        if "FROM OPS.INCIDENT I LEFT JOIN OPS.TEAM" in sql:
            return [{**sample_incident(), "team_name": "Data team", "airflow_url": None, "api_version": None}]
        return []

    def execute(self, sql, params=()):
        if "UPDATE OPS.NOTIFICATION" in sql:
            self.marks.append(params)

    def execute_count(self, sql, params=()):
        self.merges.append(params)
        return 1

    def http(self, url, payload):
        self.posts.append((url, payload))
        return self.status, (f"bad request for {url}" if self.echo else "")


def test_outbox_retries_then_dead_and_never_leaks_the_url(monkeypatch):
    monkeypatch.setenv("AIP_SECRET_KEY", "k" * 32)
    db = OutboxDb([{"notification_id": "n1", "incident_id": "i-1", "team_id": "a", "channel": "TEAMS", "kind": "opened",
                    "target_secret": "alerts", "payload": "{}", "attempts": 0}])
    counts = notify.run_outbox(db, {"public_base_url": "https://gdp.example.com"}, http=db.http)
    assert counts["failed"] == 1 and db.posts[0][0] == WEBHOOK
    status, error, _, _, delay, _ = db.marks[0]
    assert status == "FAILED" and delay == 60 and "SECRETSIG" not in error and "[webhook]" in error
    card = json.dumps(db.posts[0][1])
    assert "hunter2" not in card and "Acknowledge" in card
    db = OutboxDb([{"notification_id": "n1", "incident_id": "i-1", "team_id": "a", "channel": "TEAMS", "kind": "opened",
                    "target_secret": "alerts", "payload": "{}", "attempts": 5}])
    assert notify.run_outbox(db, {}, http=db.http)["dead"] == 1 and db.marks[0][0] == "DEAD"
    db = OutboxDb([{"notification_id": "n1", "incident_id": "i-1", "team_id": "a", "channel": "TEAMS", "kind": "opened",
                    "target_secret": "alerts", "payload": "{}", "attempts": 2}], status=202)
    assert notify.run_outbox(db, {}, http=db.http)["sent"] == 1 and db.marks[0][0] == "SENT"
    # transport errors say nothing about the URL either
    ok, detail = notify.send(WEBHOOK, {}, http=lambda url, payload: (_ for _ in ()).throw(OSError(url)))
    assert not ok and "SECRETSIG" not in detail


def test_outbox_rate_limit_folds_into_one_storm_summary(monkeypatch):
    monkeypatch.setenv("AIP_SECRET_KEY", "k" * 32)
    rows = [{"notification_id": f"n{i}", "incident_id": "i-1", "team_id": "a", "channel": "TEAMS", "kind": "opened",
             "target_secret": "alerts", "payload": "{}", "attempts": 0} for i in range(4)]
    db = OutboxDb(rows, sent=9, status=202)
    counts = notify.run_outbox(db, {"rate_limit_per_10min": 10}, http=db.http, now=NOW)
    assert counts["sent"] == 1 and counts["suppressed"] == 3 and len(db.posts) == 1
    storm_keys = {p[0] for p in db.merges if str(p[0]).startswith("storm:")}
    assert storm_keys == {f"storm:a:{int(notify.window_start(NOW).timestamp())}"}
    assert sum(1 for m in db.marks if m[0] == "SUPPRESSED") == 3


def test_outbox_jira_rows_use_the_handler():
    db = OutboxDb([{"notification_id": "j1", "incident_id": "i-1", "team_id": None, "channel": "JIRA", "kind": "create",
                    "target_secret": None, "payload": "{}", "attempts": 0}])
    seen = []
    counts = notify.run_outbox(db, {}, jira=lambda _db, row: seen.append(row["kind"]) or ("SKIPPED", tickets.NO_BOT))
    assert seen == ["create"] and counts["skipped"] == 1 and db.marks[0][0] == "SKIPPED"


# ---------------------------------------------------------------- governance and routes

OPS_O2_ROUTES = [
    ("GET", "/api/ops/incidents", "OPS.VIEW"), ("GET", "/api/ops/incidents/summary", "OPS.VIEW"),
    ("GET", "/api/ops/incidents/i1", "OPS.VIEW"), ("GET", "/api/ops/teams", "OPS.VIEW"), ("GET", "/api/ops/routing", "OPS.VIEW"),
    ("GET", "/api/ops/settings", "OPS.VIEW"),
    ("POST", "/api/ops/incidents/i1/ack", "OPS.OPERATE"), ("POST", "/api/ops/incidents/i1/reopen", "OPS.OPERATE"),
    ("POST", "/api/ops/incidents/i1/assign", "OPS.OPERATE"), ("POST", "/api/ops/incidents/i1/resolve", "OPS.OPERATE"),
    ("POST", "/api/ops/incidents/i1/mute", "OPS.OPERATE"), ("POST", "/api/ops/incidents/i1/comment", "OPS.OPERATE"),
    ("POST", "/api/ops/incidents/i1/ticket", "OPS.OPERATE"), ("POST", "/api/ops/incidents/bulk", "OPS.OPERATE"),
    ("POST", "/api/ops/teams", "INTEGRATION.MANAGE"), ("PUT", "/api/ops/teams/t1", "INTEGRATION.MANAGE"),
    ("DELETE", "/api/ops/teams/t1", "INTEGRATION.MANAGE"), ("POST", "/api/ops/teams/t1/webhook", "INTEGRATION.MANAGE"),
    ("DELETE", "/api/ops/teams/t1/webhook", "INTEGRATION.MANAGE"), ("POST", "/api/ops/teams/t1/test", "INTEGRATION.MANAGE"),
    ("POST", "/api/ops/routing", "INTEGRATION.MANAGE"), ("PUT", "/api/ops/routing/r1", "INTEGRATION.MANAGE"),
    ("DELETE", "/api/ops/routing/r1", "INTEGRATION.MANAGE"), ("PUT", "/api/ops/settings", "INTEGRATION.MANAGE"),
]


def test_governance_mapping_of_incident_routes():
    for method, path, priv in OPS_O2_ROUTES:
        found, _, matched = privilege_for(method, path)
        assert matched and found == priv, (method, path, found)
    _, support = effective_privileges(["SUPPORT_ENGINEER"], ROLE_PRIVS, GRANTS)
    _, viewer = effective_privileges(["VIEWER"], ROLE_PRIVS, GRANTS)
    for method, path, priv in OPS_O2_ROUTES:
        if priv == "OPS.OPERATE":
            assert decide(priv, support, None)[0] == "ALLOW" and decide(priv, viewer, None)[0] == "FORBID"
    assert decide("INTEGRATION.MANAGE", support, None)[0] == "FORBID"
    assert carries_secret("POST", "/api/ops/teams/t1/webhook") and not carries_secret("DELETE", "/api/ops/teams/t1/webhook")


def test_api_routes_are_registered():
    import app.main as main

    paths = {(m, r.path) for r in main.app.routes for m in getattr(r, "methods", set())}
    for method, path in [("GET", "/api/ops/incidents"), ("GET", "/api/ops/incidents/summary"),
                         ("GET", "/api/ops/incidents/{incident_id}"), ("POST", "/api/ops/incidents/{incident_id}/ack"),
                         ("POST", "/api/ops/incidents/bulk"), ("GET", "/api/ops/teams"), ("POST", "/api/ops/teams/{team_id}/webhook"),
                         ("DELETE", "/api/ops/teams/{team_id}/webhook"), ("POST", "/api/ops/teams/{team_id}/test"),
                         ("GET", "/api/ops/routing"), ("PUT", "/api/ops/routing/{rule_id}"), ("GET", "/api/ops/settings"),
                         ("PUT", "/api/ops/settings")]:
        assert (method, path) in paths, (method, path)
    # the summary route is matched before the incident detail route
    order = [r.path for r in main.app.routes]
    assert order.index("/api/ops/incidents/summary") < order.index("/api/ops/incidents/{incident_id}")
