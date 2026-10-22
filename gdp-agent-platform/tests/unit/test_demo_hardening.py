"""Real client data that used to break a load or a later stage: NaN floats and very long text in profiles, a profile
query one table cannot run, 19-digit Oracle numbers, impossible load times, and share keys typed in another case."""

import decimal
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from services.profiling import profiler  # noqa: E402


def test_nan_and_infinity_never_reach_a_stored_profile():
    row = {"N0": 3, "V0": 3, "D0": 3, "AVG0": float("nan"), "SD0": float("inf"), "P500": 2.0}
    stats = profiler.column_stats(row, 0, 3)
    assert stats["average"] is None and stats["stddev"] is None and stats["median"] == 2.0


def test_long_values_are_cut_in_the_query_itself():
    cols = [("NOTES", "TEXT")]
    assert f"LEFT(MIN(\"NOTES\")::VARCHAR, {profiler.VALUE_CHARS})" in profiler.stats_sql('"T"', cols)
    assert f"LEFT(\"NOTES\"::VARCHAR, {profiler.VALUE_CHARS}) AS V" in profiler.frequencies_sql('"T"', cols)
    assert f"LEFT(\"NOTES\"::VARCHAR, {profiler.VALUE_CHARS})" in profiler.patterns_sql('"T"', cols)


class _Job:
    def __init__(self, rows=None, error=None):
        self.rows, self.error = rows or [], error

    def result(self):
        if self.error:
            raise RuntimeError(self.error)
        return self.rows


class _Row(dict):
    def as_dict(self):
        return dict(self)


class _Session:
    def __init__(self, fail):
        self.fail = fail

    def sql(self, sql):
        session = self

        class R:
            def collect_nowait(self):
                return _Job(error="collation not supported" if session.fail in sql else None, rows=[_Row({"X": 1})])
        return R()


def test_a_part_of_a_profile_that_cannot_run_is_left_empty_not_fatal():
    from services.profiling.procedures import _gather

    errors = {}
    out = _gather(_Session("REGEXP"), ["SELECT REGEXP_REPLACE(...)", "SELECT 1"], 4, errors)
    assert out == [[], [{"X": 1}]] and "collation" in errors[0]
    with pytest.raises(RuntimeError):
        _gather(_Session("REGEXP"), ["SELECT REGEXP_REPLACE(...)"], 4)   # without `errors`: raises as before


def test_nineteen_digit_oracle_numbers_convert():
    from services.source.oracle.extract import _convert
    from services.source.oracle.types import ColumnType

    spec = ColumnType("NUMBER", "decimal:38,10", "NUMBER(38,10)", "NUMBER")
    notes = {}
    out = _convert([decimal.Decimal("1234567890123456789")], spec, notes)
    assert out == [decimal.Decimal("1234567890123456789.0000000000")]


@pytest.mark.parametrize("value, ok", [("2026-02-30", False), ("2026-10-22 13:75", False), ("2026-10-22T10:30", True),
                                       ("2026-10-22", True), ("", True)])
def test_impossible_load_times_are_refused(value, ok):
    from services.source.ingestion_control import _timestamp

    assert _timestamp(value)[1] is ok


def test_share_keys_are_found_whatever_the_case_they_were_typed_in():
    from app import main  # noqa: F401  (the API's routers import main first)
    from app.hub_sources_api import ShareOnboard, share_control_payload

    body = ShareOnboard(database="SHARE_DB", schema="S", tables=["orders"], source_system_name="CRM",
                        snaplogic={"primary_keys": {"orders": "ORDER_ID"}, "delta_keys": {"Orders": "UPDATED_AT"}})
    payload = share_control_payload(body, "SHARE_DB", "S", [{"object": "orders", "landing_table": "AI.LANDING.CRM__ORDERS"}])
    (table,) = payload["tables"]
    assert table["source_primary_keys"] == "ORDER_ID" and table["source_delta_key"] == "UPDATED_AT"


# ---------------------------------------------------------------- automatic resolution switched on after a backlog

class _ResolveDb:
    def __init__(self):
        self.updates, self.claimed = [], False

    def execute_count(self, sql, params=()):
        if "SET STATUS = 'OUT_OF_SCOPE'" in sql:
            self.updates.append((sql, params))
            return 2
        return 0

    def query(self, sql, params=()):
        if "FROM CORE.FAILURE_EVENT" in sql and "STATUS = 'PENDING'" in sql:
            self.claimed = True
        return []

    def execute(self, sql, params=()):
        pass


def test_a_backlog_is_set_aside_before_anything_is_claimed(monkeypatch):
    from services.resolve import engine, rules

    db = _ResolveDb()
    monkeypatch.setattr(engine, "load_preference", lambda db: {"auto_resolve_enabled": True, "max_parallel_tasks": 5})
    monkeypatch.setattr(engine, "load_scopes", lambda db: [])
    monkeypatch.setattr(engine, "load_routes", lambda db: [])
    monkeypatch.setattr(engine, "release_stale_claims", lambda db: 0)
    out = engine.claim_and_resolve(db, "w", executor=None, analyst=None)
    reasons = [p[0] for _, p in db.updates]
    assert len(reasons) == 3 and all(r.startswith("Not handled automatically:") for r in reasons)
    sqls = " ".join(q for q, _ in db.updates)
    assert "I.STATUS = 'RESOLVED'" in sqls and "T.STATE = 'success'" in sqls and "DATEADD(hour, -6" in sqls
    assert all("F.STATUS = 'PENDING'" in q for q, _ in db.updates) and db.claimed
    assert sum(out["set_aside"].values()) == 6
    assert "OUT_OF_SCOPE" in rules.TRANSITIONS["PENDING"]
    # off: nothing is set aside or claimed
    monkeypatch.setattr(engine, "load_preference", lambda db: {"auto_resolve_enabled": False})
    db = _ResolveDb()
    assert engine.claim_and_resolve(db, "w", executor=None, analyst=None)["enabled"] is False and not db.updates


def test_dags_filter_by_tag():
    from app import main  # noqa: F401
    from app import ops_api

    seen = {}
    original = ops_api._q
    try:
        ops_api._q = lambda db, sql, params: seen.update(sql=sql, params=params) or []
        ops_api._dags(None, env_id="prod", tag="gdp-silver")
    finally:
        ops_api._q = original
    assert "ARRAY_CONTAINS(%s::VARIANT, D.TAGS)" in seen["sql"] and "gdp-silver" in seen["params"]
