import pytest

from services.quality.scan import (
    evaluate, freshness_hours, health, mask, pii_columns, plan, schema_result, threshold_ok, type_family, violation,
)
from services.soda.custom import validate
from services.soda.expectations import render_yaml
from services.soda.kit import build_kit, contract_v4

T = '"DB"."MART"."DIM_ORDERS"'


def chk(kind, col=None, severity="FAIL", **d):
    return {"target_column": col, "severity": severity, "definition": {"kind": kind, **d}}


def test_one_aggregate_query_and_separate_statements_where_needed():
    checks = [chk("row_count", gt=0), chk("not_null", "ORDER_ID"), chk("unique", "ORDER_ID", columns=["ORDER_ID"]),
              chk("unique", None, columns=["ORDER_ID", "LINE"]), chk("accepted_values", "STATUS", values=["A", "B"]),
              chk("avg", "TOTAL", threshold={"between": [1, 9]}), chk("freshness", "UPDATED_AT", threshold="1d"),
              chk("schema", required=["ORDER_ID"]), chk("reference", "CUSTOMER_ID", reference_table="DB.MART.DIM_CUSTOMER"),
              chk("failed_rows", condition="TOTAL < 0"), chk("metric", expression="AVG(TOTAL)", threshold={"op": "<", "value": 5})]
    sql, reads, standalone = plan(checks, T)
    assert sql.startswith("SELECT COUNT(*) AS N") and sql.endswith(f"FROM {T}")
    assert 'COUNT_IF(("ORDER_ID" IS NULL)) AS M1' in sql and "AVG(TRY_TO_DOUBLE" in sql and "DATEDIFF('minute'" in sql
    assert "COUNT_IF((TOTAL < 0))" in sql and "(AVG(TOTAL)) AS M10" in sql
    assert reads[0] == {"alias": "N", "measure": "value"}
    assert "GROUP BY \"ORDER_ID\", \"LINE\"" in standalone[3] and standalone[7] == "SCHEMA"
    assert "NOT EXISTS" in standalone[8] and "DB.MART.DIM_CUSTOMER" in standalone[8]


def test_unsafe_sql_is_refused():
    with pytest.raises(AssertionError):
        plan([chk("row_count")], "DIM_ORDERS; DROP TABLE X")
    _, reads, _ = plan([chk("failed_rows", condition="1=1; DELETE FROM X")], T)
    assert "error" in reads[0]
    _, reads, _ = plan([chk("reference", "C", reference_table="x y z")], T)
    assert "error" in reads[0]


def test_outcomes_respect_thresholds_and_severity():
    assert evaluate(chk("row_count", gt=0), 120, 120)["outcome"] == "PASS"
    assert evaluate(chk("row_count", gt=0), 0, 0)["outcome"] == "FAIL"
    assert evaluate(chk("not_null", "X"), 3, 100) == {
        "outcome": "FAIL", "measured": 3, "failed_rows": 3, "percent": 3.0, "threshold": "<= 0",
        "detail": "3 of 100 rows fail (3%)"}
    assert evaluate(chk("not_null", "X", severity="WARN"), 3, 100)["outcome"] == "WARN"
    assert evaluate(chk("missing_percent", "X", max_percent=5), 3, 100)["outcome"] == "PASS"
    assert evaluate(chk("avg", "X", threshold={"between": [1, 9]}), 12.0, 10)["outcome"] == "FAIL"
    assert evaluate(chk("freshness", "U", threshold="6h"), 3.5, 10)["outcome"] == "PASS"
    assert evaluate(chk("freshness", "U", threshold="6h"), 30.0, 10)["outcome"] == "FAIL"
    moved = evaluate(chk("change_over_time", max_decrease_percent=20, max_increase_percent=50), 50, 50, previous=100)
    assert moved["outcome"] == "FAIL" and moved["measured"] == -50.0
    assert evaluate(chk("change_over_time"), 50, 50, previous=None)["outcome"] == "NOT_EVALUATED"
    assert evaluate(chk("metric", expression="AVG(X)"), 4.0, 1)["outcome"] == "NOT_EVALUATED"  # no threshold
    assert threshold_ok(5, {"op": "<", "value": 5}) is False and threshold_ok(5, {"between": [5, 5]}) is True
    assert freshness_hours("30m") == 0.5 and freshness_hours("2d") == 48 and health(8, 2, 0) == 90 and health(0, 0, 0) is None


def test_schema_types_compare_by_family():
    c = chk("schema", required=["A", "B"], forbidden=["SSN"], types={"A": "NUMBER(10,0)", "C": "VARCHAR(20)"})
    ok = schema_result(c, {"A": "NUMBER", "B": "TEXT", "C": "TEXT"})
    assert ok["outcome"] == "PASS"
    bad = schema_result(c, {"A": "TEXT", "SSN": "TEXT"})
    assert bad["outcome"] == "FAIL" and "missing B" in bad["detail"] and "forbidden present SSN" in bad["detail"]
    assert type_family("TIMESTAMP_NTZ(9)") == type_family("timestamp_ltz") == "TIMESTAMP"


def test_samples_mask_pii():
    assert mask("jane@acme.com") == "j••••••" and mask(None) is None
    flagged = pii_columns([{"target_column": "CONTACT", "semantic_type": "EMAIL"}], ["CUSTOMER_EMAIL", "TOTAL"])
    assert flagged == {"CONTACT", "CUSTOMER_EMAIL"}
    assert violation(chk("range", "AGE", min=0, max=120)) == '(TRY_TO_DOUBLE("AGE"::STRING) < 0.0 OR TRY_TO_DOUBLE("AGE"::STRING) > 120.0)'


def test_custom_checks_are_validated_per_kind():
    good, problems = validate({"target_column": "STATUS", "severity": "warn",
                               "definition": {"kind": "accepted_values", "values": ["A"]}})
    assert not problems and good["check_type"] == "ACCEPTED_VALUES" and good["severity"] == "WARN"
    assert validate({"definition": {"kind": "not_null"}})[1] == ["not_null needs a column"]
    assert validate({"definition": {"kind": "failed_rows", "condition": "X > 1", "query": "SELECT 1"}})[1]
    assert validate({"definition": {"kind": "failed_rows", "query": "DELETE FROM T"}})[1]
    assert validate({"target_column": "X", "definition": {"kind": "regex", "pattern": "(["}})[1]
    assert validate({"target_column": "X", "definition": {"kind": "avg", "threshold": {"op": "~", "value": 1}}})[1]
    assert validate({"definition": {"kind": "freshness", "threshold": "soon"}})[1]
    assert validate({"definition": {"kind": "nope"}})[1] == ["unknown check kind 'nope'"]


def test_kit_has_both_soda_flavours_and_no_secrets():
    checks = [{"target_column": None, "severity": "FAIL", "requirement": "Not empty", "status": "APPROVED",
               "definition": {"kind": "row_count", "gt": 0}},
              {"target_column": "ID", "severity": "FAIL", "requirement": "", "status": "REJECTED",
               "definition": {"kind": "not_null"}}]
    files = build_kit("Orders run", "ORG-ACCT", "DB", "MART", "DIM_ORDERS", "WH", "ROLE", checks)
    assert set(files) == {"README.md", "configuration.yml", "checks.yml", "ds_config.yml", "contract.yaml",
                          "sc_config.yml", ".github/workflows/soda-scan.yml"}
    assert "soda scan -d snowflake_dq -c configuration.yml checks.yml" in files["README.md"]
    assert "soda contract verify --data-source ds_config.yml --contract contract.yaml" in files["README.md"]
    assert "${SNOWFLAKE_PASSWORD}" in files["configuration.yml"] and "${env.SNOWFLAKE_PASSWORD}" in files["ds_config.yml"]
    assert "missing_count(ID)" not in files["checks.yml"]  # rejected check left out
    assert "dataset: snowflake_dq/DB/MART/DIM_ORDERS" in files["contract.yaml"]
    assert "must_be_greater_than: 0" in files["contract.yaml"]


def test_new_kinds_render_in_both_flavours():
    checks = [{"target_column": None, "severity": "FAIL", "requirement": "Totals add up",
               "definition": {"kind": "failed_rows", "condition": "TOTAL < 0"}},
              {"target_column": "TOTAL", "severity": "WARN", "requirement": "",
               "definition": {"kind": "avg", "threshold": {"between": [10, 500]}}}]
    v3 = render_yaml("dim_orders", checks)
    assert "- failed rows:" in v3 and "fail condition: TOTAL < 0" in v3
    assert "- avg(TOTAL):" in v3 and "warn: when not between 10 and 500" in v3
    v4 = contract_v4("DB", "MART", "DIM_ORDERS", checks)
    assert "- failed_rows:" in v4 and "expression: 'TOTAL < 0'" in v4
    assert "expression: AVG(TOTAL)" in v4 and "level: warn" in v4
