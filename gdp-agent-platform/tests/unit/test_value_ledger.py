"""The value ledger: baselines, partial credit, measured vs assumed, net value, weekly trend, repair times from incident
history, the knowledge score, a report that survives missing tables, and the reliability cost fix."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from services.common import platform_config
from services.value import baselines, ledger, report

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def iso(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat()


# ---------------------------------------------------------------- baselines


def test_baselines_validate_and_mark_what_an_admin_changed():
    clean, problems = baselines.validate({"activities": {"auto_fix": {"minutes": 30}}, "blended_rate_usd": 60})
    assert not problems and clean["activities"]["auto_fix"]["minutes"] == 30 and clean["blended_rate_usd"] == 60
    assert clean["activities"]["dbt_file"]["minutes"] == 60   # left out: keeps the default
    eff = baselines.effective({"activities": {"auto_fix": {"minutes": 30}}})
    assert eff["activities"]["auto_fix"]["basis"] == "edited"
    assert eff["activities"]["dbt_file"]["basis"] == "default" and eff["rate_basis"] == "default"
    _, bad = baselines.validate({"activities": {"nope": 1, "auto_fix": {"minutes": -2}}, "credit": {"MODIFIED": 2}})
    assert any("unknown activity" in p for p in bad) and any("auto_fix minutes" in p for p in bad)
    assert any("credit MODIFIED" in p for p in bad)
    _, weights = baselines.validate({"knowledge_weights": {"auto_fix_share": 0.9, "dag_coverage": 0.9, "fix_coverage": 0}})
    assert "knowledge weights must add up to 1" in weights


def test_platform_config_knows_the_baselines():
    assert "VALUE_BASELINES" in platform_config.SETTINGS and "VALUE_BASELINES" not in platform_config.ADMIN_SETTINGS
    assert platform_config.DEFAULTS["VALUE_BASELINES"] == baselines.DEFAULTS
    assert platform_config.validate("VALUE_BASELINES", {"min_samples": 0})[1]


# ---------------------------------------------------------------- hours and money


def test_entries_credit_human_edits_and_keep_counts_and_minutes_apart():
    base = baselines.effective({"activities": {"mapping_column": {"minutes": 10}}})
    counts = {"mapping_column": [{"week": "2026-09-28", "kind": "APPROVED", "n": 6},
                                 {"week": "2026-09-28", "kind": "MODIFIED", "n": 4},
                                 {"week": "2026-10-05", "kind": "REJECTED", "n": 3}],
              "auto_fix": [{"week": "2026-10-05", "kind": "APPROVED", "n": 2}], "sttm_line": None}
    rows = {r["activity"]: r for r in ledger.entries(counts, base)}
    mapping = rows["mapping_column"]
    assert mapping["count"] == 13 and mapping["credited"] == 8.0 and mapping["hours"] == round(8 * 10 / 60, 1)
    assert mapping["count_basis"] == "measured" and mapping["minutes_basis"] == "admin set"
    assert rows["auto_fix"]["hours"] == 1.5 and rows["auto_fix"]["minutes_basis"] == "assumed default"
    assert rows["sttm_line"]["available"] is False and rows["sttm_line"]["hours"] is None
    assert rows["dbt_file"]["available"] is False   # no rows at all: its source was not read


def test_net_value_needs_a_credit_price():
    priced = ledger.net(10, 75, 4.0, 3.0)
    assert priced == {"hours": 10.0, "value_usd": 750.0, "ai_credits": 4.0, "ai_cost_usd": 12.0, "net_usd": 738.0,
                      "needs_price": False}
    unpriced = ledger.net(10, 75, 4.0, None)
    assert unpriced["net_usd"] is None and unpriced["ai_cost_usd"] is None and unpriced["needs_price"] is True


def test_weekly_trend_combines_activities_and_cost():
    base = baselines.effective({})
    counts = {"auto_fix": [{"week": "2026-09-28", "n": 4}], "rca_ticket": [{"week": "2026-10-05", "n": 2}]}
    trend = ledger.weekly(counts, base, [{"week": "2026-10-05", "credits": 1.5}], 2.0)
    assert [w["week"] for w in trend] == ["2026-09-28", "2026-10-05"]
    assert trend[0]["hours"] == 3.0 and trend[1]["hours"] == 1.0 and trend[1]["ai_cost_usd"] == 3.0


# ---------------------------------------------------------------- repair time


def ev(iid, kind, minutes, fp="fp1", **detail):
    return {"incident_id": iid, "fingerprint": fp, "kind": kind, "created_at": iso(minutes),
            "detail": json.dumps(detail) if detail else None}


def test_episodes_follow_the_timeline_not_the_latest_row():
    events = [ev("i1", "opened", 0), ev("i1", "resolved", 90),                       # a person: 90 min
              ev("i1", "reopened", 200, at=iso(195)), ev("i1", "auto_fixed", 205),    # automatic: 10 min
              ev("i1", "resolved", 260, auto=True),
              ev("i2", "opened", 0, fp="fp2"), ev("i2", "resolved", 30, auto=True),  # a rerun succeeded by itself
              ev("i3", "opened", 300, fp="fp3"), ev("i3", "auto_fixed", 304)]        # repaired, still open
    eps = {(e["incident_id"], e["kind"]): e for e in ledger.episodes(events)}
    assert eps[("i1", "MANUAL")]["minutes"] == 90.0
    assert eps[("i1", "AUTO")]["minutes"] == 10.0   # from the failure time the reopen recorded
    assert eps[("i2", "SELF")]["minutes"] == 30.0
    assert eps[("i3", "AUTO")]["minutes"] == 4.0


def test_repair_time_comparisons_are_measured_only_with_enough_samples():
    eps = []
    for i in range(5):
        eps.append({"incident_id": f"m{i}", "fingerprint": f"f{i}", "start": iso(i * 10), "kind": "MANUAL", "minutes": 100.0})
        eps.append({"incident_id": f"a{i}", "fingerprint": f"f{i}", "start": iso(1000 + i * 10), "kind": "AUTO", "minutes": 10.0})
    first, second, third = ledger.mttr(eps, None, T0 + timedelta(minutes=900), 5)
    assert first["measured"] and first["automatic"]["median_min"] == 10.0 and first["manual"]["median_min"] == 100.0
    assert first["change_pct"] == -90.0
    assert second["measured"] and second["before"]["n"] == 5 and second["after"]["n"] == 5
    assert third["failures"] == 5 and third["measured"] and third["change_pct"] == -90.0
    few = ledger.mttr(eps[:4], None, None, 5)
    assert not few[0]["measured"] and few[0]["change_pct"] is None and few[1]["cutover"] is None


# ---------------------------------------------------------------- knowledge score


def test_knowledge_score_uses_measured_ratios_and_skips_unknown_parts():
    weights = baselines.DEFAULTS["knowledge_weights"]
    now = {"failures": 10, "auto_fixed": 6, "active_dags": 8, "profiled_dags": 8, "signatures": 4,
           "covered_signatures": 2, "knowledge_items": 120, "knowledge_new": 9, "known_fixes": 5, "runbooks": 16}
    before = {"failures": 10, "auto_fixed": 2, "active_dags": 8, "profiled_dags": 4, "signatures": 4,
              "covered_signatures": 1}
    out = ledger.knowledge(now, before, weights)
    assert out["score"] == round(100 * (0.5 * 0.6 + 0.25 * 1.0 + 0.25 * 0.5))
    assert out["previous_score"] < out["score"]
    assert out["components"]["dag_coverage"]["numerator"] == 8 and out["counts"]["runbooks"] == 16
    empty = ledger.knowledge({"active_dags": 4, "profiled_dags": 2}, {}, weights)
    assert empty["score"] == 50 and empty["previous_score"] is None   # only the part that can be measured


# ---------------------------------------------------------------- the report


class Db:
    def __init__(self, answers):
        self.answers = answers

    def query(self, sql, params=()):
        for fragment, rows in self.answers.items():
            if fragment in sql:
                if isinstance(rows, Exception):
                    raise rows
                return rows
        return []


def test_report_shows_missing_sources_as_not_available():
    db = Db({"FROM CORE.PLATFORM_CONFIG": [{"config_value": json.dumps({"blended_rate_usd": 100}), "version": 2,
                                           "created_by": "ANA", "created_at": "2026-10-01"}],
             "FROM CORE.MAPPING_DECISION": RuntimeError("no access"),
             "FROM CORE.RESOLUTION_RUN": [{"w": "2026-10-05", "kind": "APPROVED", "n": 4}],
             "FROM CORE.COST_USAGE": [{"w": "2026-10-05", "credits": 2}]})
    out = report.report(db, 30, now=T0)
    rows = {r["activity"]: r for r in out["activities"]}
    assert rows["mapping_column"]["available"] is False and rows["auto_fix"]["hours"] == 3.0
    assert out["baselines"]["version"] == 2 and out["baselines"]["changed_by"] == "ANA"
    assert out["value"]["value_usd"] == 300.0
    assert out["value"]["ai_credits"] == 2.0


# ---------------------------------------------------------------- reliability: credits are not dollars


def test_reliability_reports_credits_and_money_only_with_a_price():
    from services.ops import reliability

    class R(Db):
        pass

    base = {"FROM CORE.INCIDENT I": [], "FROM CORE.TEAM": [], "FROM CORE.DAG_RUN R": [],
            "INCIDENT_EVENT V": [{"n": 3}], "FROM CORE.COST_USAGE": [{"credits": 2.5}]}
    no_price = reliability.report(R(base), 7, now=T0)["ai"]
    assert no_price == {"diagnoses": 3, "credits": 2.5, "credit_price_usd": None, "cost_usd": None}
    priced = reliability.report(R({**base, "FROM CORE.PLATFORM_CONFIG": [{"config_value": "3.0", "version": 1}]}),
                                7, now=T0)["ai"]
    assert priced["cost_usd"] == 7.5


@pytest.mark.parametrize("value", [None, "", "abc"])
def test_no_price_means_no_money(value):
    assert report._price(value) is None
