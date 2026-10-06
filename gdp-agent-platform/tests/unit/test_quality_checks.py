from services.quality.backtest import evaluate, metric_sql, plan
from services.quality.gx import render_suite
from services.quality.profile_checks import carry, profile_checks
from services.soda.expectations import from_sttm, render_yaml


def col(name, family="TEXT", nulls=0.0, enum=None, card="HIGH", patterns=None, max_length=None, rng=None, pii="NONE"):
    stats = {"row_count": 1000, "null_percentage": nulls, "enum_values": enum, "max_length": max_length}
    if rng:
        stats["numeric_range"] = {"min": rng[0], "max": rng[1]}
    return {"column_name": name, "family": family, "cardinality": card, "pii_classification": pii,
            "patterns": patterns or [], "statistics": stats}


DOCS = {"CRM_CUSTOMER": {"row_count": 1000, "columns": [
    col("CUST_ID", nulls=0.0, max_length=12),
    col("STATUS", enum=["A", "I", "X"], card="LOW"),
    col("SEGMENT", enum=["SMB", "ENT"], card="LOW", nulls=3.0),
    col("ZIP", patterns=[{"pattern": "99999", "count": 980}, {"pattern": "99999-9999", "count": 20}]),
    col("CREDIT", family="NUMBER", rng=(0, 50000), nulls=12.0),
    col("NAME", max_length=80),
]}}


def line(target, src, mapping="DIRECT", transformation=None, dtype="VARCHAR"):
    return {"target_column": target, "source_table": "CRM_CUSTOMER", "source_column": src, "mapping_type": mapping,
            "transformation": transformation, "target_datatype": dtype, "nullable_rule": True, "accepted_values": []}


LINES = [
    line("CUSTOMER_ID", "CUST_ID"),
    {**line("CUSTOMER_STATUS", "STATUS"), "nullable_rule": False, "accepted_values": ["A", "I"]},
    line("SEGMENT", "SEGMENT"),
    line("POSTAL_CODE", "ZIP"),
    line("CREDIT_LIMIT", "CREDIT", dtype="NUMBER(18,2)"),
    line("CUSTOMER_NAME", "NAME", mapping="TRANSFORM", transformation="INITCAP(TRIM(o.NAME))", dtype="VARCHAR(50)"),
    line("FULL_NAME", "NAME", mapping="DERIVED", transformation="COALESCE(NAME, 'n/a') || '!'"),
]


def test_carry_follows_value_preserving_mappings():
    assert carry(line("A", "X")) == "all"
    assert carry(line("A", "X", "TRANSFORM", "NULLIF(TRIM(o.\"X\"), '')")) == "shape"
    assert carry(line("A", "X", "TRANSFORM", "CAST(X AS VARCHAR(20))")) == "shape"
    assert carry(line("A", "X", "TRANSFORM", "COALESCE(X, 'n/a')")) == "none"
    assert carry({"target_column": "A", "source_column": None}) == "none"


def test_profile_checks_carry_evidence_and_respect_the_sttm():
    sttm = from_sttm("DIM_CUSTOMER", LINES, ["CUSTOMER_ID"])
    out = profile_checks("DIM_CUSTOMER", LINES, DOCS, sttm, driving_table="CRM_CUSTOMER")
    by = {(c["target_column"], c["definition"]["kind"]): c for c in out}
    assert by[("SEGMENT", "accepted_values")]["definition"]["values"] == ["SMB", "ENT"]
    assert by[("SEGMENT", "missing_percent")]["definition"]["max_percent"] == 6
    assert by[("POSTAL_CODE", "regex")]["definition"]["max_invalid_percent"] == 5
    assert by[("CREDIT_LIMIT", "range")]["definition"]["min"] == 0
    trunc = by[("CUSTOMER_NAME", "max_length")]
    assert trunc["severity"] == "FAIL" and "EXCEEDS" in trunc["definition"]["evidence"]
    assert not any(c["target_column"] == "FULL_NAME" for c in out)
    assert ("CUSTOMER_STATUS", "accepted_values") not in by
    status = next(c for c in sttm if c["target_column"] == "CUSTOMER_STATUS" and c["definition"]["kind"] == "accepted_values")
    assert "NOT in the STTM list: X" in status["definition"]["evidence"]
    volume = by[(None, "row_count")]
    assert (volume["definition"]["min"], volume["definition"]["max"]) == (900, 1100)
    assert all(c["origin"] == "PROFILE" for c in out)


def test_spoke_gets_hub_reference_check():
    spec = {"role": "spoke", "hub": "COMPANY_CORE", "hub_fk": "COMPANY_CORE_SKEY"}
    out = profile_checks("COMPANY_ADDRESS", [line("COMPANY_CORE_SKEY", None, "DERIVED")], {}, [], spec)
    ref = next(c for c in out if c["check_type"] == "REFERENCE")
    assert ref["severity"] == "FAIL" and ref["definition"]["reference_table"] == "company_core"


def test_sodacl_uses_alert_configuration_for_warnings():
    checks = profile_checks("DIM_CUSTOMER", LINES, DOCS, [], driving_table="CRM_CUSTOMER")
    yaml = render_yaml("dim_customer", checks)
    assert "  - missing_percent(SEGMENT):\n" in yaml and "warn: when > 6%" in yaml
    assert "  - invalid_percent(POSTAL_CODE):\n      valid regex: '^[0-9]{5}$'" in yaml
    assert "  - invalid_count(CUSTOMER_NAME) = 0:\n      valid max length: 50" in yaml
    assert "  - row_count:\n" in yaml and "warn: when not between 900 and 1100" in yaml
    warn_blocks = [b for b in yaml.split("\n  - ") if "warn: when" in b]
    assert warn_blocks and all(" = 0" not in b.splitlines()[0] for b in warn_blocks)


def test_gx_suite_maps_every_kind():
    sttm = from_sttm("DIM_CUSTOMER", LINES, ["CUSTOMER_ID"])
    checks = sttm + profile_checks("DIM_CUSTOMER", LINES, DOCS, sttm, driving_table="CRM_CUSTOMER")
    checks.append({"target_column": "UPDATED_AT", "check_type": "FRESHNESS", "severity": "WARN",
                   "definition": {"kind": "freshness", "threshold": "2d"}})
    suite = render_suite("DIM_CUSTOMER", checks)
    types = {e["type"] for e in suite["expectations"]}
    assert {"expect_table_row_count_to_be_between", "expect_column_values_to_be_unique",
            "expect_column_values_to_not_be_null", "expect_column_values_to_be_in_set",
            "expect_column_values_to_match_regex", "expect_column_values_to_be_between",
            "expect_column_value_lengths_to_be_between", "expect_table_columns_to_match_set",
            "unexpected_rows_expectation"} <= types
    seg = next(e for e in suite["expectations"] if e["type"] == "expect_column_values_to_not_be_null"
               and e["kwargs"].get("column") == "SEGMENT")
    assert seg["kwargs"]["mostly"] == 0.94 and seg["meta"]["severity"] == "warning"
    fresh = next(e for e in suite["expectations"] if e["type"] == "unexpected_rows_expectation")
    assert "DATEADD(day, -2" in fresh["kwargs"]["unexpected_rows_query"]


def test_backtest_plans_one_query_per_table_and_evaluates():
    checks = profile_checks("DIM_CUSTOMER", LINES, DOCS, [], driving_table="CRM_CUSTOMER")
    queries, slots = plan(checks, LINES, {"CRM_CUSTOMER": "DB.LANDING.CRM_CUSTOMER"}, "CRM_CUSTOMER")
    sql = queries["CRM_CUSTOMER"]
    assert sql.startswith("SELECT COUNT(*) AS N") and sql.endswith("FROM DB.LANDING.CRM_CUSTOMER")
    assert "REGEXP_LIKE" in sql and "$$^[0-9]{5}$$$" in sql
    assert "'SMB', 'ENT'" in sql and "LENGTH(\"NAME\"::STRING) > 50" in sql
    seg_i = next(i for i, c in enumerate(checks) if c["definition"]["kind"] == "missing_percent")
    seg_slot = next(s for s in slots if s["index"] == seg_i)
    ok = evaluate(checks[seg_i], seg_slot, {"N": 1000, seg_slot["alias"]: 30})
    bad = evaluate(checks[seg_i], seg_slot, {"N": 1000, seg_slot["alias"]: 90})
    assert ok["status"] == "PASS" and bad["status"] == "FAIL"
    vol_i = next(i for i, c in enumerate(checks) if c["definition"]["kind"] == "row_count")
    vol_slot = next(s for s in slots if s["index"] == vol_i)
    assert evaluate(checks[vol_i], vol_slot, {"N": 1000})["status"] == "PASS"
    assert metric_sql({"definition": {"kind": "accepted_values", "values": ["O'Neil"]}}, "X").count("'O''Neil'") == 1
    assert metric_sql({"definition": {"kind": "regex", "pattern": "a$$b"}}, "X") is None
