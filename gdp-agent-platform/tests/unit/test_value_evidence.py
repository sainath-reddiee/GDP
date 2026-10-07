from datetime import datetime, timezone

from services.profiling.insights import (freshness_column, overlap_candidates, overlap_relationships, overlap_sql)
from services.profiling.profiler import date_format


def col(name, family, **stats):
    key = stats.pop("key", False)
    return {"column_name": name, "family": family, "potential_key": key, "pii_classification": "NONE",
            "statistics": stats}


def test_compact_and_text_timestamps_are_read_from_values():
    assert date_format([{"pattern": "99999999"}], ["20231127", "19991231"]) == "YYYYMMDD"
    assert date_format([{"pattern": "99999999"}], ["10002345", "20231127"]) is None  # ids, not dates
    assert date_format([{"pattern": "9999-99-99 99:99:99"}], []) == "YYYY-MM-DD HH24:MI:SS"
    assert date_format([{"pattern": "9999-99-99A99:99:99.999"}], []) == 'YYYY-MM-DD"T"HH24:MI:SS.FF'


def test_freshness_prefers_a_recent_varying_column_over_a_name():
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    cols = [col("CREATED_DATE", "DATE", max="2019-01-01", distinct_count=50, row_count=100),
            col("evt", "TIMESTAMP", max="2026-10-06 10:00:00", distinct_count=90, row_count=100),
            col("BATCH_TS", "TIMESTAMP", max="2026-10-06 10:00:00", distinct_count=1, row_count=100)]
    name, why = freshness_column(cols, now)
    assert name == "evt" and "0d old" in why


def test_relationship_found_from_values_when_names_differ():
    docs = {"MEMBERS": {"columns": [col("memberRef", "NUMBER", key=True, min=1, max=500, distinct_count=500)]},
            "BOOKINGS": {"columns": [col("holder", "NUMBER", min=1, max=500, distinct_count=420),
                                     col("amount", "NUMBER", min=3, max=300, distinct_count=900)]}}
    pairs = overlap_candidates(docs)
    assert [(p["child"], p["column"], p["parent"], p["key"]) for p in pairs] == \
        [("BOOKINGS", "holder", "MEMBERS", "memberRef")]  # amount has more distinct values than the key
    sql = overlap_sql(pairs, lambda t, c: (f'DB.S."{t}"', f'"{c}"'))
    assert 'SELECT DISTINCT "holder" AS V FROM DB.S."BOOKINGS"' in sql
    joins = overlap_relationships(pairs, [{"I": 0, "N": 420, "HIT": 420}])
    assert joins[0]["keys"] == ["holder=memberRef"] and joins[0]["source"] == "values"
    assert overlap_relationships(pairs, [{"i": 0, "n": 420, "hit": 100}]) == []  # weak overlap is not a join
