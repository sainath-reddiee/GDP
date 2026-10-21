"""Regression tests for Data Quality bugs: SodaCL that Soda could not parse, validation gaps, scans that errored or
measured the wrong thing, alerts that never fired, loosened suites approved without acknowledgement, a cron that
could inject SQL, and edits that left empty drafts open."""

import json
from datetime import datetime, timezone

import pytest
import yaml

from services.catalog import dq, dq_suite
from services.quality import alerts, scan
from services.soda import cloud_ci, kit
from services.soda.custom import validate
from services.soda.expectations import render_check, render_yaml
from services.soda.extract import requirement_from_row


def soda(*checks):
    """The rendered checks file, parsed as YAML (fails the test when Soda could not read it either)."""
    text = render_yaml("orders", list(checks))
    return text, yaml.safe_load(text)["checks for orders"]


def normal(target_column, definition, severity="FAIL", requirement=None):
    cleaned, problems = dq.normalize({"target_column": target_column, "severity": severity, "requirement": requirement,
                                      "definition": definition})
    assert not problems, problems
    return cleaned


# ---------------------------------------------------------------- SodaCL rendering


def test_names_values_and_conditions_are_quoted_so_the_file_parses():
    text, parsed = soda(
        normal("STATUS", {"kind": "accepted_values", "values": ["it's \"x\"", "a\\b", 3]},
               requirement="Status: must be ACTIVE #1\nor INACTIVE"),
        normal("EMAIL", {"kind": "not_null", "missing_values": ["N/A", "it's"]}),
        normal(None, {"kind": "failed_rows", "condition": "'X' = STATUS OR NOTE = 'a: b'"}),
        normal(None, {"kind": "metric", "name": "ratio", "expression": '"A" / COUNT(*)',
                      "threshold": {"op": "<", "value": 1}}))
    first = parsed[0]["invalid_count(STATUS) = 0"]
    assert first["name"] == "Status: must be ACTIVE #1 or INACTIVE"
    assert first["valid values"] == ["it's \"x\"", "a\\b", 3]
    assert parsed[1]["missing_count(EMAIL) = 0"]["missing values"] == ["N/A", "it's"]
    assert parsed[2]["failed rows"]["fail condition"] == "'X' = STATUS OR NOTE = 'a: b'"
    assert parsed[3]["ratio < 1"]["ratio expression"] == '"A" / COUNT(*)'


def test_percent_limits_are_inclusive_like_the_scanner_and_accept_numbers_as_text():
    check = normal("EMAIL", {"kind": "missing_percent", "max_percent": "5"})
    assert check["definition"]["max_percent"] == 5.0 and check["soda_dsl"].strip() == "- missing_percent(EMAIL) <= 5%"
    assert normal("ID", {"kind": "duplicate_percent", "max_percent": 0})["soda_dsl"].strip() == "- duplicate_percent(ID) <= 0%"
    regex = normal("CODE", {"kind": "regex", "pattern": "^[A-Z]+$", "max_invalid_percent": "2.5"})
    assert regex["definition"]["max_invalid_percent"] == 2.5 and "invalid_percent(CODE) <= 2.5%" in regex["soda_dsl"]
    _, problems = validate({"target_column": "EMAIL", "definition": {"kind": "missing_percent", "max_percent": "lots"}})
    assert problems == ["max_percent must be a number between 0 and 100"]


def test_change_over_time_is_rendered_in_percent():
    rendered = render_check({"severity": "FAIL", "definition": {"kind": "change_over_time", "max_decrease_percent": 20,
                                                                  "max_increase_percent": 50}})
    assert rendered.startswith("  - change percent for row_count between -20% and +50%")


def test_a_check_without_a_soda_form_is_a_comment_not_an_unknown_metric():
    row = requirement_from_row("DB.S.T", {"target_column": "NOTE", "check_type": "VALIDITY"})
    text, parsed = soda(row, normal("ID", {"kind": "unique"}))
    assert "custom(" not in text and "# not rendered: custom check on NOTE" in text
    assert parsed == ["duplicate_count(ID) = 0"]


def test_client_check_types_map_to_real_kinds():
    email = requirement_from_row("DB.S.T", {"target_column": "EMAIL", "check_type": "EMAIL"})
    assert email["definition"] == {"kind": "format", "format": "email"}
    missing = requirement_from_row("DB.S.T", {"target_column": "EMAIL", "check_type": "MISSING_PERCENT", "threshold": "< 5%"})
    assert missing["definition"] == {"kind": "missing_percent", "max_percent": 5}
    dups = requirement_from_row("DB.S.T", {"target_column": "ID", "check_type": "duplicate percent", "threshold": "1"})
    assert dups["definition"]["kind"] == "duplicate_percent" and dups["definition"]["max_percent"] == 1


# ---------------------------------------------------------------- validation


def test_range_bounds_must_be_numbers_low_first():
    for definition, message in (({"kind": "range", "min": "abc"}, "must be numbers"),
                                ({"kind": "range", "min": "2020-01-01"}, "must be numbers"),
                                ({"kind": "range", "min": 5, "max": 1}, "not be above")):
        _, problems = validate({"target_column": "AMOUNT", "definition": definition})
        assert any(message in p for p in problems), (definition, problems)
    assert not validate({"target_column": "AMOUNT", "definition": {"kind": "range", "min": 0, "max": 9.5}})[1]


def test_freshness_is_a_whole_number_of_units():
    cleaned, problems = validate({"target_column": "TS", "definition": {"kind": "freshness", "threshold": "30 m"}})
    assert not problems and cleaned["definition"]["threshold"] == "30m"
    for bad in ("1.5h", "2w", ""):
        assert validate({"target_column": "TS", "definition": {"kind": "freshness", "threshold": bad}})[1], bad
    assert dq.parse_dsl("freshness(TS) < 1.5h")[1] and not dq.parse_dsl("freshness(TS) < 6h")[1]


def test_column_names_in_lists_are_plain_identifiers():
    bad = [({"kind": "unique", "columns": ["A", 'B"; x']}, "unique columns"),
           ({"kind": "schema", "required": ["A: B", "x]"]}, "schema required"),
           ({"kind": "schema", "types": {"A": "VARCHAR; DROP"}}, "not a column type"),
           ({"kind": "reference", "reference_table": "DB.S.REF", "reference_column": "X) OR 1=1 --"}, "reference column")]
    for definition, message in bad:
        _, problems = validate({"target_column": "A", "definition": definition})
        assert any(message in p for p in problems), (definition, problems)
    cleaned, problems = validate({"definition": {"kind": "schema", "types": {"id": "number(38, 0)"}}})
    assert not problems and cleaned["definition"]["types"] == {"id": "NUMBER(38, 0)"}


def test_conditions_and_expressions_cannot_hold_a_subquery():
    for definition in ({"kind": "failed_rows", "condition": "ID IN (SELECT ID FROM HR.PII.EMP)"},
                       {"kind": "metric", "name": "x", "expression": "(SELECT MAX(SSN) FROM HR.PII.EMP)",
                        "threshold": {"op": "<", "value": 1}}):
        _, problems = validate({"definition": definition})
        assert any("plain SQL" in p for p in problems), problems
    with pytest.raises(AssertionError):
        scan.safe_condition("(SELECT 1)")
    assert scan.safe_condition("EXTRACT(YEAR FROM ORDER_DATE) > 2000")


def test_column_case_is_kept_and_matched_to_the_table_at_scan_time():
    check = normal("email", {"kind": "not_null"})
    assert check["target_column"] == "email"   # quoted lowercase columns stay reachable
    resolved = scan.resolve_columns([{"target_column": "email", "definition": {"kind": "not_null"}},
                                     {"target_column": "note", "definition": {"kind": "not_null"}},
                                     {"target_column": None, "definition": {"kind": "unique", "columns": ["id", "Code"]}},
                                     {"target_column": None, "definition": {"kind": "freshness"}}],
                                    {"EMAIL": "TEXT", "note": "TEXT", "ID": "NUMBER", "CODE": "TEXT", "LOADED_AT": "TIMESTAMP_LTZ"})
    assert [r["target_column"] for r in resolved[:2]] == ["EMAIL", "note"]
    assert resolved[2]["definition"]["columns"] == ["ID", "CODE"] and resolved[3]["definition"]["column"] == "LOADED_AT"


# ---------------------------------------------------------------- scanning


def test_freshness_compares_instants_for_zoned_timestamps():
    checks = [{"target_column": "TS", "definition": {"kind": "freshness", "threshold": "6h"}}]
    ltz, _, _ = scan.plan(checks, "DB.S.T", {"TS": "TIMESTAMP_LTZ"})
    ntz, _, _ = scan.plan(checks, "DB.S.T", {"TS": "TIMESTAMP_NTZ"})
    assert "MAX(\"TS\")::TIMESTAMP_LTZ, CURRENT_TIMESTAMP()" in ltz and "CONVERT_TIMEZONE" not in ltz
    assert "CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())" in ntz


def test_a_hub_reference_by_model_name_is_read_beside_the_scanned_table():
    checks = [{"target_column": "HUB_SKEY", "definition": {"kind": "reference", "reference_table": "company_core",
                                                          "reference_column": "HUB_SKEY"}}]
    _, reads, standalone = scan.plan(checks, "DB.SILVER.COMPANY_ADDRESS")
    assert 0 not in reads and "FROM DB.SILVER.COMPANY_CORE R" in standalone[0]


class ScanSession:
    """Answers the scanner's queries; the shared aggregate fails when it reads the BAD column."""

    def __init__(self):
        self.calls = []

    def sql(self, sql, params=None):
        session, flat = self, " ".join(sql.split())
        self.calls.append(flat)

        class Result:
            def collect(self_inner):
                class Row(dict):
                    def as_dict(self):
                        return dict(self)
                if "INFORMATION_SCHEMA.COLUMNS" in flat:
                    return [Row(COLUMN_NAME="ID", DATA_TYPE="NUMBER"), Row(COLUMN_NAME="EMAIL", DATA_TYPE="TEXT")]
                if '"BAD"' in flat and "M1" in flat and "M0" in flat:
                    raise RuntimeError("SQL compilation error: invalid identifier '\"BAD\"'")
                if '"BAD"' in flat:
                    raise RuntimeError("SQL compilation error: invalid identifier '\"BAD\"'")
                if flat.startswith("SELECT COUNT(*) AS N FROM"):
                    return [Row(N=10)]
                if "AS M0" in flat:
                    return [Row(M0=0)]
                return []
        return Result()


def test_one_bad_column_errors_only_its_own_check():
    checks = [{"expectation_id": "e0", "target_column": "email", "severity": "FAIL", "definition": {"kind": "not_null"}},
              {"expectation_id": "e1", "target_column": "BAD", "severity": "FAIL", "definition": {"kind": "not_null"}},
              {"expectation_id": "e2", "target_column": None, "severity": "FAIL", "definition": {"kind": "row_count", "gt": 0}}]
    out, _ = scan._scan_model(ScanSession(), None, checks, [], "DB.S.T", guard=lambda sql, allowed: (True, [], sql))
    by_id = {r["expectation_id"]: r for r in out}
    assert by_id["e0"]["outcome"] == "PASS" and by_id["e2"]["outcome"] == "PASS"
    assert by_id["e1"]["outcome"] == "ERROR" and "invalid identifier" in by_id["e1"]["detail"]


# ---------------------------------------------------------------- alerts


def state(outcome, streak=0, kind="not_null", cid="c1"):
    return {"catalog_check_id": cid, "outcome": outcome, "streak": streak, "kind": kind, "check_type": kind.upper()}


def test_a_persistent_freshness_warning_is_stale_data():
    failing, stale = alerts.classify([state("WARN", 3, "freshness")], warn_after=3)
    assert stale and not failing
    assert alerts.classify([state("WARN", 1, "freshness")], warn_after=3) == ([], [])


def test_the_digest_pass_rate_counts_errors():
    rate, evaluated = alerts._pass_rate(["PASS"] + ["ERROR"] * 9)
    assert evaluated == 10 and rate == 10.0


def test_a_later_partial_scan_does_not_hide_an_earlier_failure(monkeypatch):
    raised = []
    monkeypatch.setattr(alerts, "raise_incident", lambda store, cand, *a: raised.append(cand) or {"action": "opened"})
    monkeypatch.setattr(alerts, "owner_dag", lambda *a: {"dag_id": "d"})
    history = [{"catalog_check_id": "A", "scan_id": "s1", "outcome": "FAIL", "check_type": "NOT_NULL",
                "created_at": "2026-10-21T10:00:00+00:00"},
               {"catalog_check_id": "B", "scan_id": "s2", "outcome": "PASS", "check_type": "NOT_NULL",
                "created_at": "2026-10-21T10:01:00+00:00"}]
    route = alerts.route_for({}, None)
    now = datetime(2026, 10, 21, 10, 5, tzinfo=timezone.utc)
    scan_ = {"scan_id": "s2", "table_fqn": "DB.S.T", "started_at": "2026-10-21T10:01:00+00:00", "batch_scan_ids": ["s1", "s2"]}
    class Store:   # nothing open to resolve
        def latest_by_fingerprint(self, fp):
            return None

    out = alerts.evaluate_table(Store(), scan_, history, route, {}, [], {}, now)
    assert out.get(alerts.KIND_FAILED) == "opened" and raised


def test_a_reopened_incident_takes_the_new_title_and_severity(monkeypatch):
    class Store:
        def __init__(self):
            self.updates, self.events = [], []

        def latest_by_fingerprint(self, fp):
            return {"incident_id": "i1", "status": "RESOLVED", "severity": "P3", "error_excerpt": "Check set: old"}

        def update(self, iid, fields):
            self.updates.append((iid, fields))

        def event(self, iid, kind, who, detail):
            self.events.append(kind)

    store = Store()
    monkeypatch.setattr(alerts.inc, "open_or_update", lambda *a: {"action": "reopened", "incident_id": "i1"})
    cand = {"kind": alerts.KIND_FAILED, "title": "2 checks failing on T", "severity": "P1",
            "error_excerpt": "Table: DB.S.T\nCheck set: new"}
    alerts.raise_incident(store, cand, {}, [], {}, datetime.now(timezone.utc))
    assert store.updates and store.updates[0][1]["title"] == "2 checks failing on T"
    assert store.updates[0][1]["severity"] == alerts.higher_of("P3", "P1")


# ---------------------------------------------------------------- suites


def ck(definition, **kw):
    return {"check_id": "c1", "target_column": "X", "severity": "FAIL", "status": "ACTIVE", "definition": definition, **kw}


@pytest.mark.parametrize("before, after", [
    ({"kind": "range", "min": 0, "max": 100}, {"kind": "range", "min": -1e6, "max": 100}),
    ({"kind": "row_count", "min": 1000, "max": 5000}, {"kind": "row_count", "min": 0, "max": 5000}),
    ({"kind": "avg", "threshold": {"between": [10, 20]}}, {"kind": "avg", "threshold": {"between": [-1e9, 1e9]}}),
    ({"kind": "freshness", "threshold": "6h"}, {"kind": "freshness", "threshold": "2d"}),
    ({"kind": "range", "min": 0, "max": 100}, {"kind": "range", "max": 100}),
    ({"kind": "not_null"}, {"kind": "accepted_values", "values": ["A"]}),
])
def test_relaxed_bounds_and_changed_kinds_are_loosened(before, after):
    assert dq_suite._change_class(ck(before), ck(after))[0] == "LOOSENED"


def test_stricter_bounds_are_tightened():
    assert dq_suite._change_class(ck({"kind": "range", "min": 0, "max": 100}),
                                  ck({"kind": "range", "min": 5, "max": 90}))[0] == "TIGHTENED"


def test_an_edit_that_changes_nothing_opens_no_draft(monkeypatch):
    approved = [dq_suite.check_record({"target_column": "EMAIL", "severity": "FAIL", "definition": {"kind": "not_null"}})[0]]
    monkeypatch.setattr(dq_suite, "_ensure", lambda s, t, d, u: {"suite_id": "s1", "approved_version": 1})
    monkeypatch.setattr(dq_suite, "_versions", lambda s, sid: [])
    monkeypatch.setattr(dq_suite, "_checks", lambda s, sid, v: [dict(c) for c in approved])
    monkeypatch.setattr(dq_suite, "_summary", lambda s, h, c: {"counts": {}, "loosened": False, "changed": 0})
    opened = []
    monkeypatch.setattr(dq_suite, "open_draft", lambda *a, **k: opened.append(a))
    out = dq_suite.stage_many(None, "DB.S.T", [{"target_column": "EMAIL", "severity": "FAIL",
                                                "definition": {"kind": "not_null"}}], "PROFILE", "ana")
    assert out["staged"] is False and out["status"] == "UNCHANGED" and out["skipped"] == 1 and not opened
    with pytest.raises(AssertionError, match="not in the draft"):
        dq_suite.stage_status(None, "DB.S.T", "retired-check", "ACTIVE", "ana")
    assert not opened   # a refused edit opened nothing either


def test_a_decision_on_a_version_changed_since_it_was_read_is_a_conflict(monkeypatch):
    version = {"version": 2, "status": "IN_REVIEW", "etag": "now", "authors": ["ANA"], "submitted_by": "ANA"}
    monkeypatch.setattr(dq_suite, "_versions", lambda s, sid: [version])
    with pytest.raises(dq_suite.Conflict):
        dq_suite._in_review(None, {"suite_id": "s1"}, 2, etag="then")
    assert dq_suite._in_review(None, {"suite_id": "s1"}, 2, etag="now") == version


# ---------------------------------------------------------------- schedules, requirements, CI


def test_a_cron_cannot_close_the_schedule_string():
    with pytest.raises(AssertionError):
        dq.schedule_sql("AIP", "DB.S.T", "0 6 * * *'/**/AS/**/DELETE/**/FROM/**/DB.S.T--", "WH")
    task, statements = dq.schedule_sql("AIP", "DB.S.T", "*/15 6-18 * * 1-5 Europe/London", "WH")
    assert "USING CRON */15 6-18 * * 1-5 Europe/London'" in statements[0]


def test_requirement_checks_keep_the_table_column_case():
    from services.catalog.dq_requirements import to_check

    raw, _ = to_check({"target_column": "EMAIL", "definition": {"kind": "not_null"}, "severity": "FAIL"}, ["email", "ID"])
    assert raw["target_column"] == "email"


def test_soda_warnings_do_not_fail_ci():
    assert "max(worst, 0 if code == 1 else code)" in cloud_ci.SCRIPT   # Soda exits 1 when checks only warn
    assert "|| [ $? -eq 1 ]" in kit.workflow()
