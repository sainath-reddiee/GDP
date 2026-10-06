import json
from pathlib import Path

import pytest

from services.profiling import procedures, profiler
from services.profiling.procedures import TableRef, parse_options, profile_tables


def test_stage_segments_are_sanitized_and_unique():
    assert profiler.stage_segment("CRM_CUSTOMER") == "CRM_CUSTOMER"
    dashed, spaced, lower = (profiler.stage_segment(n) for n in ("crm-customer", "crm customer", "crm_customer"))
    assert len({dashed, spaced, lower, "CRM_CUSTOMER"}) == 4
    assert all(profiler.STAGE_PATH.match(f"A/B/C/{s}.json") for s in (dashed, spaced, lower))
    path = profiler.profile_stage_path("crm", "demo db", "Crm", "../etc/passwd")
    assert profiler.STAGE_PATH.match(path) and ".." not in path and path.count("/") == 3


def test_fingerprint_tracks_columns_rows_and_last_altered():
    cols = [("ID", "NUMBER(38,0)"), ("NM", "VARCHAR(20)")]
    base = profiler.source_fingerprint(cols, 100, "2026-10-01")
    assert base == profiler.source_fingerprint(list(cols), 100, "2026-10-01")
    assert base != profiler.source_fingerprint(cols, 101, "2026-10-01")
    assert base != profiler.source_fingerprint(cols, 100, "2026-10-02")
    assert base != profiler.source_fingerprint(cols[:1], 100, "2026-10-01")


def test_document_drops_run_scoped_fields_and_checksum_is_stable():
    col = {"column_name": "ID", "landing_column_id": "x", "potential_foreign_key": "T.ID", "data_type": "NUMBER"}
    doc = profiler.profile_document({"source_name": "S", "database": "D", "schema": "C", "table": "T"},
                                    5, [col], "fp", False, "m", "2026-10-06T00:00:00Z")
    assert doc["columns"] == [{"column_name": "ID", "data_type": "NUMBER"}]
    assert profiler.document_checksum(doc) == profiler.document_checksum(json.loads(json.dumps(doc)))
    assert profiler.cache_is_fresh({"SOURCE_FINGERPRINT": "fp", "PROFILER_VERSION": profiler.PROFILER_VERSION}, "fp")
    assert not profiler.cache_is_fresh({"SOURCE_FINGERPRINT": "fp", "PROFILER_VERSION": "1"}, "fp")
    assert not profiler.cache_is_fresh(None, "fp")


def test_large_tables_use_approximate_distinct_and_sampling():
    cols = [("ID", "NUMBER"), ("NM", "VARCHAR")]
    assert not profiler.is_large(profiler.LARGE_TABLE_ROWS)
    assert profiler.is_large(profiler.LARGE_TABLE_ROWS + 1)
    exact = profiler.stats_sql('"D"."S"."T"', cols)
    approx = profiler.stats_sql('"D"."S"."T"', cols, approximate=True)
    assert "COUNT(DISTINCT" in exact and "APPROX_COUNT_DISTINCT" not in exact
    assert "COUNT(DISTINCT" not in approx and approx.count("APPROX_COUNT_DISTINCT") == 2
    freq = profiler.frequencies_sql('"D"."S"."T"', cols, approximate=True)
    assert freq.count(f"SAMPLE ({profiler.SAMPLE_ROWS} ROWS)") == 2 and freq.count("UNION ALL") == 1
    assert "SAMPLE" not in profiler.frequencies_sql('"D"."S"."T"', cols)
    assert profiler.patterns_sql('"D"."S"."T"', [("ID", "NUMBER")]) is None


def test_approximate_stats_clamp_and_key_tolerance():
    row = {"N0": 1000, "V0": 1000, "D0": 1010}
    stats = profiler.column_stats(row, 0, 1000, approximate=True)
    assert stats["distinct_count"] == 1000
    near = {**stats, "distinct_count": 985}
    assert profiler.potential_key(1000, near, approximate=True)
    assert not profiler.potential_key(1000, near)
    assert not profiler.potential_key(1000, {**near, "null_count": 1}, approximate=True)


def test_histogram_specs_and_buckets():
    cols = [("AMT", "NUMBER(10,2)"), ("NM", "VARCHAR"), ("FLAT", "NUMBER")]
    specs = profiler.histogram_specs(cols, {"MIN0": "0", "MAX0": "100", "MIN2": "5", "MAX2": "5"})
    assert specs == [(0, "AMT", 0.0, 100.0)]
    sql = profiler.histogram_sql('"D"."S"."T"', specs)
    assert "WIDTH_BUCKET(\"AMT\", 0.0, 100.0, 10)" in sql
    hist = profiler.build_histogram(0.0, 100.0, {1: 3, 10: 2})
    assert len(hist) == 10 and hist[0] == {"lower": 0.0, "upper": 10.0, "count": 3} and hist[9]["count"] == 2


def test_grouped_rows_restore_frequency_order():
    raw = [{"C": 1, "V": "b", "N": 2}, {"C": 0, "V": "x", "N": 1}, {"C": 1, "V": "a", "N": 5}]
    grouped = profiler.group_rows(raw, "V")
    assert [v["value"] for v in grouped[1]] == ["a", "b"] and grouped[0] == [{"value": "x", "count": 1}]


def test_profile_options():
    assert parse_options(None) == (set(), procedures.DEFAULT_CONCURRENCY)
    assert parse_options('{"force_refresh": true, "concurrency_limit": 8}') == (True, 8)
    assert parse_options('{"refresh_tables": ["T1"]}') == ({"T1"}, procedures.DEFAULT_CONCURRENCY)
    with pytest.raises(AssertionError):
        parse_options('{"concurrency_limit": 99}')
    with pytest.raises(AssertionError):
        parse_options('{"tables": []}')


# ---------------------------------------------------------------- cache behaviour with a fake Snowpark session


class Row(dict):
    def as_dict(self):
        return dict(self)


class FakeFile:
    def __init__(self, stage):
        self.stage = stage

    def put(self, local, remote, auto_compress=False, overwrite=True):
        prefix = remote.split(f"@{procedures.PROFILE_STAGE}/", 1)[1]
        self.stage[f"{prefix}/{Path(local).name}"] = Path(local).read_text(encoding="utf-8")


class FakeSession:
    """Answers the cache queries; profile computation and LLM enrichment are stubbed separately."""

    def __init__(self, metadata_deployed=True):
        self.index, self.stage = {}, {}
        self.file = FakeFile(self.stage)
        self.metadata_deployed = metadata_deployed

    def sql(self, sql, params=None):
        return FakeResult(self, sql, params or [])


class FakeResult:
    def __init__(self, session, sql, params):
        self.session, self.sql, self.params = session, sql, params

    def collect_nowait(self):
        raise AttributeError("no async in tests")

    def collect(self):
        s, p, fake = self.sql, self.params, self.session
        if "FROM METADATA.TABLE_PROFILES" in s:
            if not fake.metadata_deployed:
                raise RuntimeError("Schema 'METADATA' does not exist or not authorized")
            names = json.loads(p[0])
            return [Row(r) for r in fake.index.values() if r["SOURCE_NAME"] in names]
        if "MERGE INTO METADATA.TABLE_PROFILES" in s:
            fake.index[tuple(p[:4])] = {
                "SOURCE_NAME": p[0], "DATABASE_NAME": p[1], "SCHEMA_NAME": p[2], "TABLE_NAME": p[3],
                "ROW_COUNT": int(p[4]), "PROFILE_STAGE_PATH": p[6], "PROFILE_CHECKSUM": p[7],
                "SOURCE_FINGERPRINT": p[8], "PROFILER_VERSION": p[10]}
            return [Row({"number of rows inserted": 1})]
        if s.startswith(f"SELECT $1 AS DOC FROM @{procedures.PROFILE_STAGE}/"):
            path = s.split(f"{procedures.PROFILE_STAGE}/", 1)[1].split(" ", 1)[0]
            return [Row({"DOC": fake.stage[path]})] if path in fake.stage else []
        raise AssertionError(f"unexpected SQL: {s}")


def _refs(n=5, rows=100):
    return [TableRef("ITEST", "DEMO", "CRM", f"T{i}", f'"AI"."LANDING"."ITEST__T{i}"', rows,
                     (("ID", "NUMBER(38,0)"), ("NM", "VARCHAR(20)")), "2026-10-01") for i in range(n)]


@pytest.fixture
def computed(monkeypatch):
    calls = []

    def fake_compute(session, refs, limit):
        calls.append([r.table for r in refs])
        out = {}
        for r in refs:
            columns = []
            for name, data_type in r.columns:
                stats = {"row_count": r.row_count, "null_count": 0, "physical_null_count": 0,
                         "placeholder_null_count": 0, "distinct_count": r.row_count, "min": "1", "max": "9",
                         "null_percentage": 0.0, "distinct_percentage": 100.0, "duplicate_count": 0}
                columns.append(profiler.build_profile(name, data_type, stats, [], []))
            out[r.key] = (columns, r.row_count)
        return out

    monkeypatch.setattr(procedures, "_compute", fake_compute)
    monkeypatch.setattr(procedures, "_enrich", lambda *a, **k: "test-model")
    return calls


def test_five_tables_are_staged_then_reused_by_a_second_run(computed):
    session = FakeSession()
    first = profile_tables(session, _refs(), concurrency_limit=2, run_id="run-1")
    assert computed == [["T0", "T1", "T2", "T3", "T4"]]
    assert len(session.stage) == 5 and all(profiler.STAGE_PATH.match(p) for p in session.stage)
    assert len(session.index) == 5 and all(r.persisted and not r.cached for r in first.values())

    second = profile_tables(session, _refs(), run_id="run-2")
    assert len(computed) == 2 and computed[1] == []
    assert all(r.cached for r in second.values())
    assert second[_refs()[0].key].model == "test-model"


def test_stale_or_forced_tables_are_recomputed(computed):
    session = FakeSession()
    profile_tables(session, _refs())
    changed = _refs()
    changed[2] = TableRef(**{**changed[2].__dict__, "row_count": 101})
    profile_tables(session, changed)
    assert computed[-1] == ["T2"]
    profile_tables(session, changed, force_refresh={"T4"})
    assert computed[-1] == ["T4"]
    profile_tables(session, changed, force_refresh=True)
    assert computed[-1] == ["T0", "T1", "T2", "T3", "T4"]


def test_profiling_works_before_metadata_is_deployed(computed):
    session = FakeSession(metadata_deployed=False)
    result = profile_tables(session, _refs(2))
    assert computed == [["T0", "T1"]]
    assert all(not r.persisted and not r.cached for r in result.values()) and not session.stage
