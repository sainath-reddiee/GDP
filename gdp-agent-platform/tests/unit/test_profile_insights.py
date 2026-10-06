from datetime import datetime, timezone

from services.profiling import insights


def col(name, family="TEXT", data_type="VARCHAR(20)", null=0.0, distinct=50.0, distinct_count=50, key=False,
        patterns=None, enum=None, mn=None, mx=None, freq=None, pii="NONE"):
    return {
        "column_name": name, "family": family, "data_type": data_type, "potential_key": key, "pii_classification": pii,
        "patterns": patterns or [],
        "statistics": {"row_count": 100, "null_percentage": null, "distinct_percentage": distinct,
                       "distinct_count": distinct_count, "enum_values": enum, "min": mn, "max": mx,
                       "frequency_distribution": freq or []},
    }


CUSTOMER = {"row_count": 100, "profiled_at": "2026-10-01T00:00:00", "columns": [
    col("ID", "NUMBER", "NUMBER(38,0)", distinct=100, distinct_count=100, key=True, mn="1", mx="100",
        freq=[{"value": "7", "count": 1}]),
    col("STATUS", enum=["A", "I"], distinct=2, distinct_count=2),
    col("EMAIL", null=10.0, patterns=[{"pattern": "aaaa@aaaa.aaa", "count": 96}, {"pattern": "aaa@aa.aa", "count": 4}]),
]}
ORDERS = {"row_count": 400, "columns": [
    col("ORDER_ID", "NUMBER", "NUMBER(38,0)", distinct=100, distinct_count=400, key=True, mn="1", mx="400"),
    col("CUSTOMER_ID", "NUMBER", "NUMBER(38,0)", distinct=20, distinct_count=80, mn="3", mx="99",
        freq=[{"value": "7", "count": 9}]),
    col("UPDATED_AT", "TIMESTAMP", "TIMESTAMP_NTZ(9)"),
]}


def test_quality_dimensions_and_scorecard():
    dims = insights.quality_dimensions(CUSTOMER)
    assert dims["completeness"] == round(100 - 10 / 3, 1)
    assert dims["uniqueness"] == 100.0
    assert dims["validity"] == round((100 + 96) / 2, 1)
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    card = insights.scorecard(dims, "2026-10-05 10:00:00.000 -0700", now)
    assert card["dimensions"]["freshness"] == 100.0 and card["grade"] == "A"
    stale = insights.scorecard(dims, "2025-01-01 00:00:00", now)
    assert stale["dimensions"]["freshness"] == 20.0 and stale["overall"] < card["overall"]
    assert insights.scorecard(dims, None, now)["dimensions"]["freshness"] is None


def test_suggested_checks_and_yaml():
    checks = insights.suggested_checks(CUSTOMER)
    text = [c["check"] for c in checks]
    assert "duplicate_count(ID) = 0" in text and "missing_count(ID) = 0" in text
    assert any(c.get("valid_values") == ["A", "I"] for c in checks)
    regex = next(c for c in checks if c["column"] == "EMAIL")["valid_regex"]
    assert regex == r"^[a-z][a-z][a-z][a-z]@[a-z][a-z][a-z][a-z]\.[a-z][a-z][a-z]$"
    yaml = insights.checks_yaml("CUSTOMER", checks)
    assert yaml.startswith("checks for CUSTOMER:\n  - row_count > 0") and "valid values: ['A', 'I']" in yaml
    assert any(c["check"].startswith("freshness(UPDATED_AT)") for c in insights.suggested_checks(ORDERS))


def test_drift_reports_columns_and_rows():
    out = insights.drift(CUSTOMER, [("ID", "NUMBER(38,0)"), ("STATUS", "VARCHAR(5)"), ("PHONE", "VARCHAR(20)")], 120)
    assert out["added"] == ["PHONE"] and out["removed"] == ["EMAIL"]
    assert out["retyped"] == [{"column": "STATUS", "from": "VARCHAR(20)", "to": "VARCHAR(5)"}]
    assert out["row_count"] == {"before": 100, "after": 120, "delta": 20, "pct": 20.0}
    assert out["changed"] and out["schema_changed"]
    same = insights.drift(CUSTOMER, [(c["column_name"], c["data_type"]) for c in CUSTOMER["columns"]], 100)
    assert not same["changed"]


def test_relationships_from_profiles():
    joins = insights.infer_relationships({"CUSTOMERS": CUSTOMER, "ORDERS": ORDERS})
    assert len(joins) == 1
    j = joins[0]
    assert (j["left"], j["right"], j["keys"], j["cardinality"]) == ("ORDERS", "CUSTOMERS", ["CUSTOMER_ID=ID"], "N:1")
    assert j["confidence"] >= 0.9 and any("range" in e for e in j["evidence"])


def test_one_to_one_links_are_reported_once_and_names_alone_do_not_get_uniqueness():
    a = {"columns": [col("CLIENT_ID", key=True, distinct=100, distinct_count=76, mn="A", mx="Z"),
                     col("CLIENT_NAME", key=True, distinct=100, distinct_count=76)]}
    b = {"columns": [col("CLIENT_ID", key=True, distinct=100, distinct_count=76, mn="A", mx="Z")]}
    joins = insights.infer_relationships({"STG_CLIENT": a, "CLIENT_XREF": b})
    assert len(joins) == 1 and joins[0]["cardinality"] == "1:1"
    checks = [c["check"] for c in insights.suggested_checks(a)]
    assert "duplicate_count(CLIENT_ID) = 0" in checks and "duplicate_count(CLIENT_NAME) = 0" not in checks
