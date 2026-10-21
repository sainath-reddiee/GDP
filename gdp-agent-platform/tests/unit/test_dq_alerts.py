"""Data quality alerting (V069, services/quality/alerts.py): a table's failing checks open, update and resolve one
incident per table and kind on the ops incident engine; warn streaks, freshness and missed scheduled scans; routing to
the domain's support team; the daily digest and its Jira issue; the governance mapping of the new routes."""

import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from services.governance.policy import privilege_for  # noqa: E402
from services.ops import incidents as inc, tickets  # noqa: E402
from services.quality import alerts  # noqa: E402
from tests.unit.test_ops_incidents import NOW, FakeJira, MemoryStore  # noqa: E402

TABLE = "DEV_AI_PLATFORM.SALES.ORDERS"
DOMAIN = {"domain_id": "d-sales", "domain_name": "Sales", "owner": "ana@acme.com"}


class DqMemoryStore(MemoryStore):
    """The incident store plus the data quality reads alerting needs, held in memory."""

    def __init__(self):
        super().__init__()
        self.history = []          # every CHECK_RESULT row: catalog_check_id, scan_id, outcome, created_at, ...
        self.scan_rows = []
        self.cursor = None
        self.route_rows = {"d-sales": {"domain_id": "d-sales", "team_id": "data", "fail_severity": "P2",
                                       "warn_after_runs": 3, "stale_grace_minutes": 60, "digest_enabled": True,
                                       "digest_jira": False}}
        self.schedule_rows = []
        self.digests = {}
        self.digest_input = []
        self.teams["data"] = {"team_id": "data", "name": "Data team", "jira_project": "DATA", "escalation_minutes": 30}

    def scan_cursor(self):
        return self.cursor

    def set_scan_cursor(self, value):
        self.cursor = value

    def scans_since(self, since, limit=500):
        start = datetime.fromisoformat(since) if since else None
        found = [s for s in self.scan_rows if start is None or datetime.fromisoformat(s["started_at"]) > start]
        newest = max((s["started_at"] for s in found), default=since)
        return found, newest

    def table_history(self, table, per_check):
        out = {}
        for r in sorted(self.history, key=lambda r: r["created_at"], reverse=True):
            out.setdefault(r["catalog_check_id"], [])
            if len(out[r["catalog_check_id"]]) < per_check:
                out[r["catalog_check_id"]].append(r)
        return [r for rows in out.values() for r in rows]

    def routes(self):
        return dict(self.route_rows)

    def domains(self):
        return {DOMAIN["domain_id"]: dict(DOMAIN)}

    def schedules(self):
        return list(self.schedule_rows)

    def digest_rows(self):
        return list(self.digest_input)

    def open_dq_incidents(self):
        counts, stale = {}, []
        for i in self.incidents.values():
            if i["env_id"] == "dq" and i["status"] in inc.ACTIVE:
                t = alerts.table_of(i).upper()
                counts[t] = counts.get(t, 0) + 1
                if i["kind"] == alerts.KIND_STALE:
                    stale.append(t)
        return counts, stale

    def save_digest(self, domain_id, day, team_id, owner, content):
        if (domain_id, day) in self.digests:
            return False
        self.digests[(domain_id, day)] = content
        return True

    # helpers
    def scan(self, scan_id, at, results):
        """Store one table scan: results are (check_id, outcome, extra) tuples."""
        stamp = at.isoformat()
        row = {"scan_id": scan_id, "table_fqn": TABLE, "domain_id": "d-sales", "started_at": stamp}
        self.scan_rows.append(row)
        for check_id, outcome, extra in results:
            self.history.append({"catalog_check_id": check_id, "scan_id": scan_id, "outcome": outcome, "created_at": stamp,
                                 "target_column": extra.get("column", "AMOUNT"), "check_type": extra.get("type", "not_null"),
                                 "kind": extra.get("kind", extra.get("type", "not_null")), "measured": extra.get("measured", 0),
                                 "threshold": extra.get("threshold", "= 0"), "failed_rows": extra.get("failed_rows"),
                                 "dimension": extra.get("dimension")})
        return row

    def dq_incidents(self, kind=None):
        return [i for i in self.incidents.values() if i["env_id"] == "dq" and (kind is None or i["kind"] == kind)]


def evaluate(s, scan, now=NOW):
    route = alerts.route_for(s.routes(), "d-sales")
    history = s.table_history(TABLE, max(1, route["warn_after_runs"]))
    return alerts.evaluate_table(s, scan, history, route, DOMAIN, s.rules(), {**s.settings(), "public_base_url": "https://gdp.acme.com"}, now)


# ---------------------------------------------------------------- pure

def test_state_and_classify_warn_streaks_freshness_and_errors():
    t0 = NOW - timedelta(hours=3)
    history = [
        {"catalog_check_id": "c1", "outcome": "FAIL", "created_at": (t0 + timedelta(hours=2)).isoformat()},
        {"catalog_check_id": "c2", "outcome": "WARN", "created_at": (t0 + timedelta(hours=2)).isoformat()},
        {"catalog_check_id": "c2", "outcome": "WARN", "created_at": (t0 + timedelta(hours=1)).isoformat()},
        {"catalog_check_id": "c2", "outcome": "PASS", "created_at": t0.isoformat()},
        {"catalog_check_id": "c3", "outcome": "FAIL", "kind": "freshness", "created_at": t0.isoformat()},
        {"catalog_check_id": "c4", "outcome": "ERROR", "created_at": t0.isoformat()},
        {"catalog_check_id": "c5", "outcome": "PASS", "created_at": (t0 + timedelta(hours=2)).isoformat()},
        {"catalog_check_id": "c5", "outcome": "FAIL", "created_at": t0.isoformat()},
    ]
    state = {r["catalog_check_id"]: r for r in alerts.state_of(history)}
    assert state["c2"]["streak"] == 2 and state["c5"]["outcome"] == "PASS"
    failing, stale = alerts.classify(list(state.values()), warn_after=3)
    assert [c["catalog_check_id"] for c in failing] == ["c1", "c4"] and [c["catalog_check_id"] for c in stale] == ["c3"]
    failing, _ = alerts.classify(list(state.values()), warn_after=2)
    assert {c["catalog_check_id"] for c in failing} == {"c1", "c2", "c4"}
    assert alerts.classify(list(state.values()), warn_after=0)[0] == failing[:1] + [state["c4"]]


def test_identity_is_one_per_table_and_kind_and_fits_the_columns():
    assert alerts.fingerprint("DQ_FAILED", TABLE) == alerts.fingerprint("DQ_FAILED", TABLE.lower())
    assert alerts.fingerprint("DQ_FAILED", TABLE) != alerts.fingerprint("DQ_STALE", TABLE)
    long = "DB.SCHEMA." + "T" * 400
    assert len(alerts.dag_id_for(long)) == 250 and alerts.dag_id_for(long) != alerts.dag_id_for(long + "X")
    assert alerts.split_cron("0 6 * * * Europe/London") == ("0 6 * * *", "Europe/London")
    assert alerts.lower("P2") == "P3" and alerts.lower("P4") == "P4" and alerts.higher_of("P3", "P2") == "P2"


# ---------------------------------------------------------------- open, update, resolve

def test_failing_scan_opens_one_routed_incident_with_checks_and_a_dq_store_link():
    s = DqMemoryStore()
    scan = s.scan("scan-1", NOW - timedelta(minutes=5), [
        ("c1", "FAIL", {"column": "CUSTOMER_ID", "type": "not_null", "measured": 12, "threshold": "= 0", "failed_rows": 12}),
        ("c2", "PASS", {}),
    ])
    assert evaluate(s, scan) == {"DQ_FAILED": "opened"}
    [incident] = s.dq_incidents()
    assert incident["kind"] == "DQ_FAILED" and incident["status"] == "OPEN" and incident["severity"] == "P2"
    assert incident["team_id"] == "data" and incident["dag_id"] == TABLE and incident["run_id"] == "scan-1"
    assert "1 failing check on ORDERS" in incident["title"]
    excerpt = incident["error_excerpt"]
    assert f"Table: {TABLE}" in excerpt and "CUSTOMER_ID / not_null: measured 12 vs threshold = 0 (12 failing rows)" in excerpt
    assert "https://gdp.acme.com/dashboard/dq-store/suite?table=DEV_AI_PLATFORM.SALES.ORDERS" in excerpt
    assert re.search(r"Check set: [0-9a-f]{12}", excerpt)
    assert [n["kind"] for n in s.notes_for(incident["incident_id"], "JIRA")] == ["create"]
    # the same scan read again (poll overlap, a second worker) changes nothing
    assert evaluate(s, scan) == {"DQ_FAILED": "duplicate"}
    assert s.incidents[incident["incident_id"]]["occurrences"] == 1


def test_the_same_failures_again_are_an_occurrence_and_a_changed_set_updates_the_incident():
    s = DqMemoryStore()
    evaluate(s, s.scan("scan-1", NOW - timedelta(hours=2), [("c1", "ERROR", {}), ("c2", "PASS", {})]))
    [incident] = s.dq_incidents()
    iid = incident["incident_id"]
    assert incident["severity"] == "P3"   # ERROR only: one level below FAIL_SEVERITY
    assert evaluate(s, s.scan("scan-2", NOW - timedelta(hours=1), [("c1", "ERROR", {}), ("c2", "PASS", {})])) == {"DQ_FAILED": "occurrence"}
    assert s.incidents[iid]["occurrences"] == 2 and "checks_changed" not in s.kinds(iid)
    result = evaluate(s, s.scan("scan-3", NOW - timedelta(minutes=5), [("c1", "ERROR", {}), ("c2", "FAIL", {"column": "EMAIL"})]))
    assert result == {"DQ_FAILED": "occurrence"} and len(s.dq_incidents()) == 1
    current = s.incidents[iid]
    assert "checks_changed" in s.kinds(iid) and current["severity"] == "P2" and "2 failing checks" in current["title"]
    assert "EMAIL / not_null" in current["error_excerpt"]


def test_a_clean_state_resolves_and_a_scan_of_other_checks_does_not():
    s = DqMemoryStore()
    # each scan is evaluated a minute after it ran (the worker's cadence)
    late = timedelta(minutes=1)
    evaluate(s, s.scan("scan-1", NOW - timedelta(hours=2), [("c1", "FAIL", {}), ("c2", "PASS", {})]), NOW - timedelta(hours=2) + late)
    [incident] = s.dq_incidents()
    iid = incident["incident_id"]
    s.incidents[iid]["jira_key"] = "DATA-7"
    # only c2 re-run: c1 still fails in the table's state, so nothing new and nothing resolved
    assert evaluate(s, s.scan("scan-2", NOW - timedelta(hours=1), [("c2", "PASS", {})]), NOW - timedelta(hours=1) + late) == {}
    assert s.incidents[iid]["status"] == "OPEN" and s.incidents[iid]["occurrences"] == 1
    assert evaluate(s, s.scan("scan-3", NOW - timedelta(minutes=5), [("c1", "PASS", {}), ("c2", "PASS", {})]),
                    NOW - timedelta(minutes=4)) == {"DQ_FAILED": "resolved"}
    resolved = s.incidents[iid]
    assert resolved["status"] == "RESOLVED" and resolved["resolved_by"] == "system" and "scan-3"[:8] in resolved["resolution"]
    assert [n["kind"] for n in s.notes_for(iid, "JIRA")] == ["create", "resolve"]
    # failing again within the reopen window reopens the same incident
    assert evaluate(s, s.scan("scan-4", NOW, [("c1", "FAIL", {})]), NOW + timedelta(minutes=1)) == {"DQ_FAILED": "reopened"}
    assert s.incidents[iid]["status"] == "OPEN" and len(s.dq_incidents()) == 1


def test_warn_raises_only_after_n_scans_in_a_row():
    s = DqMemoryStore()
    for n, minutes in enumerate((180, 120), start=1):
        assert evaluate(s, s.scan(f"scan-{n}", NOW - timedelta(minutes=minutes), [("c1", "WARN", {})])) == {}
    assert not s.dq_incidents()
    assert evaluate(s, s.scan("scan-3", NOW - timedelta(minutes=60), [("c1", "WARN", {})])) == {"DQ_FAILED": "opened"}
    [incident] = s.dq_incidents()
    assert incident["severity"] == "P3" and "WARN x3" in incident["error_excerpt"]
    s.route_rows["d-sales"]["warn_after_runs"] = 0
    assert evaluate(s, s.scan("scan-4", NOW - timedelta(minutes=30), [("c1", "WARN", {})])) == {"DQ_FAILED": "resolved"}


def test_a_routing_rule_on_the_domain_name_wins_over_the_domain_team():
    s = DqMemoryStore()
    s.teams["sales-ops"] = {"team_id": "sales-ops", "name": "Sales ops", "jira_project": "SOPS", "escalation_minutes": 15}
    s.rule_list = [{"rule_id": "r1", "priority": 1, "tag": "Sales", "team_id": "sales-ops", "enabled": True,
                    "severity_override": "P1"},
                   {"rule_id": "r2", "priority": 0, "env_id": "prod", "team_id": "data", "enabled": True}]
    evaluate(s, s.scan("scan-1", NOW, [("c1", "FAIL", {})]))
    [incident] = s.dq_incidents()
    assert incident["team_id"] == "sales-ops" and incident["severity"] == "P1"


def test_unrouted_domain_still_opens_and_tickets():
    s = DqMemoryStore()
    s.route_rows = {}
    evaluate(s, s.scan("scan-1", NOW, [("c1", "FAIL", {})]))
    [incident] = s.dq_incidents()
    assert incident["team_id"] is None and incident["severity"] == "P2"
    assert s.notes_for(incident["incident_id"], "JIRA")


# ---------------------------------------------------------------- freshness and missed scans

def test_failed_freshness_raises_stale_and_a_fresh_scan_resolves_it():
    s = DqMemoryStore()
    fresh = {"column": "LOADED_AT", "type": "freshness", "kind": "freshness", "measured": 30, "threshold": "< 24h"}
    assert evaluate(s, s.scan("scan-1", NOW - timedelta(hours=1), [("f1", "FAIL", fresh), ("c1", "PASS", {})])) == {"DQ_STALE": "opened"}
    [incident] = s.dq_incidents()
    assert incident["kind"] == "DQ_STALE" and incident["severity"] == "P3" and "Stale data" in incident["title"]
    assert evaluate(s, s.scan("scan-2", NOW, [("f1", "PASS", fresh), ("c1", "PASS", {})])) == {"DQ_STALE": "resolved"}


def test_missed_scheduled_scan_raises_stale_once_per_fire_time_and_the_next_scan_resolves_it():
    s = DqMemoryStore()
    s.schedule_rows = [{"table_name": TABLE, "cron": "0 6 * * * UTC", "domain_id": "d-sales",
                        "updated_at": (NOW - timedelta(days=3)).isoformat(),
                        "last_scan_at": (NOW - timedelta(days=1, hours=6)).isoformat()}]
    # NOW is 12:00 UTC: the 06:00 scan is due, the grace (60 min) has passed and no scan ran since yesterday
    assert alerts.check_schedules(s, s.settings(), NOW)["actions"] == {"opened": 1}
    [incident] = s.dq_incidents()
    assert incident["kind"] == "DQ_STALE" and incident["title"].startswith(alerts.MISSED)
    assert "due at 2026-10-10T06:00:00+00:00 did not run" in incident["error_excerpt"]
    assert alerts.check_schedules(s, s.settings(), NOW + timedelta(minutes=10))["actions"] == {"duplicate": 1}
    # inside the grace, or scheduled after the fire time, nothing is missed
    s.schedule_rows[0]["updated_at"] = (NOW - timedelta(hours=1)).isoformat()
    assert alerts.missed_due(s.schedule_rows[0], 60, NOW) is None
    assert alerts.missed_due({**s.schedule_rows[0], "updated_at": None}, 60, NOW.replace(hour=6, minute=30)) is None
    # the scan runs: the table is no longer stale
    assert evaluate(s, s.scan("scan-1", NOW + timedelta(minutes=20), [("c1", "PASS", {})]),
                    NOW + timedelta(minutes=21)) == {"DQ_STALE": "resolved"}


def test_process_scans_takes_each_tables_newest_scan_and_moves_the_cursor():
    s = DqMemoryStore()
    s.scan("scan-1", NOW - timedelta(minutes=30), [("c1", "FAIL", {})])
    s.scan("scan-2", NOW - timedelta(minutes=10), [("c1", "FAIL", {})])
    out = alerts.process_scans(s, s.settings(), NOW)
    assert out["tables"] == 1 and out["actions"] == {"DQ_FAILED:opened": 1}
    assert s.cursor == (NOW - timedelta(minutes=10)).isoformat()
    assert alerts.process_scans(s, s.settings(), NOW) == {"tables": 0, "actions": {}}
    assert alerts.process_scans(s, {**s.settings(), "dq_alerts": False}, NOW)["disabled"]


# ---------------------------------------------------------------- digest

def digest_rows():
    def row(cid, table, outcome, day, week, column="AMOUNT"):
        return {"catalog_check_id": cid, "table_name": table, "target_column": column, "check_type": "not_null",
                "domain_id": "d-sales", "outcome": outcome, "measured": 3, "threshold": "= 0",
                "last_at": NOW.isoformat(), "day_outcome": day, "week_outcome": week}
    return [row("c1", TABLE, "FAIL", "PASS", "PASS"),            # newly failing
            row("c2", TABLE, "FAIL", "FAIL", "PASS"),            # still failing
            row("c3", TABLE, "PASS", "FAIL", "FAIL"),            # recovered
            row("c4", "DEV_AI_PLATFORM.SALES.CUSTOMERS", "PASS", "PASS", None),
            row("c5", "DEV_AI_PLATFORM.SALES.CUSTOMERS", None, None, None)]   # never run


def test_digest_content_pass_rate_trend_and_newly_failing():
    d = alerts.digest(DOMAIN, digest_rows(), date(2026, 10, 10), open_incidents=2, stale_tables=[TABLE],
                      base_url="https://gdp.acme.com")
    assert d["checks"] == 5 and d["tables"] == 2 and d["evaluated"] == 4 and d["never_run"] == 1
    assert d["pass_rate"] == 50.0 and d["pass_rate_week_ago"] == 66.7 and d["trend"] == -16.7
    assert d["new_failing"] == 1 and d["newly_failing"][0]["check_id"] == "c1" and d["still_failing"] == 1
    assert d["recovered"] == 1 and d["failing"] == 2 and d["open_incidents"] == 2 and d["stale_tables"] == [TABLE]
    assert d["newly_failing"][0]["link"].startswith("https://gdp.acme.com/dashboard/dq-store/suite?table=")
    text = alerts.digest_text(d)
    assert text.startswith("Data quality digest for Sales, 2026-10-10: 50.0% passing (-16.7 pts vs last week), 1 newly failing.")
    assert f"- {TABLE} AMOUNT not_null: FAIL, measured 3 vs threshold = 0" in text and "Stale or missed scans" in text


def test_run_digests_once_a_day_after_the_hour_with_jira_only_when_asked():
    s = DqMemoryStore()
    s.digest_input = digest_rows()
    assert alerts.run_digests(s, s.settings(), NOW.replace(hour=5)) == {"due": False}
    out = alerts.run_digests(s, s.settings(), NOW)
    assert out == {"due": True, "domains": 1, "stored": 1, "queued": 0} and (("d-sales", NOW.date()) in s.digests)
    assert alerts.run_digests(s, s.settings(), NOW + timedelta(hours=1))["stored"] == 0
    s.digests.clear()
    s.route_rows["d-sales"]["digest_jira"] = True
    assert alerts.run_digests(s, s.settings(), NOW)["queued"] == 1
    [note] = [n for n in s.notes if n["kind"] == "dq_digest"]
    assert note["channel"] == "JIRA" and note["team_id"] == "data" and note["payload"]["domain_id"] == "d-sales"
    s.digests.clear()
    s.route_rows["d-sales"]["digest_enabled"] = False
    assert alerts.run_digests(s, s.settings(), NOW)["stored"] == 0


def test_digest_jira_issue_is_filed_once_in_the_team_project():
    s = DqMemoryStore()
    content = alerts.digest(DOMAIN, digest_rows(), date(2026, 10, 10))
    row = {"kind": "dq_digest", "channel": "JIRA", "team_id": "data", "payload": content, "incident_id": None}
    jira = FakeJira()
    status, detail = tickets.handle(s, row, jira, s.settings(), NOW)
    assert status == "SENT" and "DATA-1" in detail
    fields = jira.created[0]["fields"]
    assert fields["project"] == {"key": "DATA"} and "gdp-dq-digest" in fields["labels"]
    assert tickets.digest_label("d-sales", "2026-10-10") in fields["labels"]
    assert fields["summary"].startswith("Data quality digest for Sales")
    again = FakeJira(found=[{"key": "DATA-1"}])
    assert tickets.handle(s, row, again, s.settings(), NOW) == ("SENT", "the digest is already DATA-1") and not again.created
    assert tickets.handle(s, row, None, s.settings(), NOW) == ("SKIPPED", tickets.NO_BOT)


def test_dq_incident_ticket_body_names_the_table_checks_and_suite():
    s = DqMemoryStore()
    evaluate(s, s.scan("scan-1", NOW, [("c1", "FAIL", {"column": "CUSTOMER_ID"})]))
    [incident] = s.dq_incidents()
    body = tickets.description(incident, tickets._links(s, incident, {"public_base_url": "https://gdp.acme.com"}))
    text = str(body)
    assert f"Table: {TABLE}" in text and "Failing checks" in text and "Suite in the DQ Store" in text
    assert "Environment" not in text and "Airflow run" not in text


# ---------------------------------------------------------------- wiring

def test_governance_mapping_of_dq_alerting_routes():
    for method, path, priv in [("PUT", "/api/ops/dq-alerting/routes/d1", "INTEGRATION.MANAGE"),
                               ("POST", "/api/ops/dq-alerting/run", "OPS.OPERATE"),
                               ("GET", "/api/ops/dq-alerting/routes", "OPS.VIEW")]:
        found, _, matched = privilege_for(method, path)
        assert matched and found == priv, (method, path, found)
    for path in ("/api/dq/failing", "/api/dq/digests"):
        assert privilege_for("GET", path)[0] is None


def test_routes_are_registered_and_the_worker_runs_the_jobs():
    import app.main as main
    from app import worker

    paths = {(m, r.path) for r in main.app.routes for m in getattr(r, "methods", set())}
    for method, path in [("GET", "/api/dq/failing"), ("GET", "/api/dq/digests"), ("GET", "/api/ops/dq-alerting/routes"),
                         ("PUT", "/api/ops/dq-alerting/routes/{domain_id}"), ("POST", "/api/ops/dq-alerting/run")]:
        assert (method, path) in paths, (method, path)
    assert worker.INTERVALS["dq_alerts"] == 120 and worker.INTERVALS["dq_digest"] == 900


def test_migration_v069_is_additive_and_granted():
    sql = (ROOT / "snowflake" / "database" / "migrations" / "V069__dq_alerting.sql").read_text(encoding="utf-8")
    body = re.sub(r"--[^\n]*", "", sql).upper()
    assert "CREATE TABLE IF NOT EXISTS {{DATABASE}}.CORE.DQ_ALERT_ROUTE" in body
    assert "CREATE TABLE IF NOT EXISTS {{DATABASE}}.CORE.DQ_DIGEST" in body
    assert not re.search(r"\b(DROP|DELETE|TRUNCATE)\b", body)
    for table in ("DQ_ALERT_ROUTE", "DQ_DIGEST"):
        assert f"GRANT SELECT, INSERT, UPDATE ON TABLE {{{{DATABASE}}}}.CORE.{table} TO DATABASE ROLE {{{{DATABASE}}}}.OPS_SERVICE" in body
        assert f"GRANT SELECT ON TABLE {{{{DATABASE}}}}.CORE.{table} TO DATABASE ROLE {{{{DATABASE}}}}.OPS_VIEWER" in body
