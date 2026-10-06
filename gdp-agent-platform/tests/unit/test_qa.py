from services.qa.guard import check
from services.qa.tests import build_suite, script, source_expression

TARGET = {"fqn": "SILVER.CRM.DIM_CUSTOMER", "name": "DIM_CUSTOMER"}
SOURCES = {"CUSTOMERS": "LAND.CRM.CUSTOMERS", "ORDERS": "LAND.CRM.ORDERS"}


def line(target, src=None, col=None, kind="DIRECT", rule=None, nullable=True, values=None, default=None):
    return {"target_column": target, "source_table": src, "source_column": col, "mapping_type": kind,
            "transformation": rule, "nullable_rule": nullable, "accepted_values": values or [], "default_value": default}


LINES = [
    line("CUSTOMER_ID", "CUSTOMERS", "CUST_ID", nullable=False),
    line("CUSTOMER_NAME", "CUSTOMERS", "NAME", kind="TRANSFORM", rule="INITCAP(TRIM(o.NAME))", nullable=False),
    line("EMAIL", "CUSTOMERS", "EMAIL"),
    line("STATUS", "CUSTOMERS", "STATUS", values=["ACTIVE", "INACTIVE"]),
    line("RECORD_SOURCE", kind="CONSTANT", default="CRM"),
    line("LAST_ORDER_DATE", "ORDERS", "ORDER_DATE", kind="TRANSFORM", rule="MAX(ORDER_DATE)"),
    line("REF_COUNTRY_SKEY", "CUSTOMERS", "COUNTRY", kind="LOOKUP"),
    line("NOTES", kind="UNMAPPED"),
]
GRAPH = {"driving_table": "CUSTOMERS", "joins": [
    {"left_table": "CUSTOMERS", "right_table": "ORDERS", "join_type": "LEFT", "keys": ["CUST_ID=CUSTOMER_ID"],
     "cardinality": "1:N"}]}


def suite():
    return build_suite(TARGET, SOURCES, LINES, ["CUSTOMER_ID"], GRAPH, {})


def test_suite_covers_every_category_with_traceable_ids():
    tests = suite()
    cats = {t["category"] for t in tests}
    assert cats == {"RECONCILIATION", "GRAIN", "COMPLETENESS", "SOURCE_TO_TARGET", "TRANSFORMATION", "LOOKUP", "VALUES", "JOINS"}
    ids = [t["test_id"] for t in tests]
    assert len(ids) == len(set(ids)) and ids[0] == "QA-RC-001"
    assert all(t["sql"].endswith(";") and t["expected"] for t in tests)


def test_key_based_comparisons_use_the_mapped_source_key():
    tests = {t["title"]: t for t in suite()}
    missing = tests["Keys in source missing from target"]["sql"]
    assert "SELECT CUST_ID FROM LAND.CRM.CUSTOMERS\nMINUS\nSELECT CUSTOMER_ID FROM SILVER.CRM.DIM_CUSTOMER" in missing
    email = tests["EMAIL equals CUSTOMERS.EMAIL"]["sql"]
    assert "JOIN SILVER.CRM.DIM_CUSTOMER AS t ON t.CUSTOMER_ID = s.CUST_ID" in email
    assert "NOT EQUAL_NULL(s.EMAIL, t.EMAIL)" in email
    rule = tests["CUSTOMER_NAME follows the STTM rule"]["sql"]
    assert "INITCAP(TRIM(NAME)) AS expected_value" in rule and "a.CUSTOMER_ID = e.CUST_ID" in rule
    other = tests["LAST_ORDER_DATE follows the STTM rule"]
    assert "compare the value distributions" in other["sql"] and "LAND.CRM.ORDERS" in other["sql"]


def test_grain_completeness_values_lookup_and_join_checks():
    tests = {t["title"]: t for t in suite()}
    assert "HAVING COUNT(*) > 1" in tests["No duplicate business keys (CUSTOMER_ID)"]["sql"]
    assert "COUNT_IF(CUSTOMER_NAME IS NULL) AS CUSTOMER_NAME_nulls" in tests["Required columns populated (1)"]["sql"]
    assert "NOT IN ('ACTIVE', 'INACTIVE')" in tests["STATUS only holds accepted values"]["sql"]
    assert "'CRM'" in tests["RECORD_SOURCE is the constant CRM"]["sql"]
    assert "REF_COUNTRY_SKEY IS NULL" in tests["REF_COUNTRY_SKEY resolves for every row"]["sql"]
    join = tests["CUSTOMERS to ORDERS does not fan out"]["sql"]
    assert "LEFT JOIN LAND.CRM.ORDERS AS r ON l.CUST_ID = r.CUSTOMER_ID" in join
    assert not any("NOTES" in (t.get("target_column") or "") for t in tests.values())


def test_spoke_hub_orphans_and_set_comparison_without_keys():
    spec = {"role": "spoke", "hub": "COMPANY_CORE", "hub_fk": "COMPANY_CORE_SKEY"}
    tests = build_suite({"fqn": "SILVER.COMPANY.COMPANY_ADDRESS"}, {"MTA": "B.MTA.PS"},
                        [line("COMPANY_CORE_SKEY", kind="DERIVED"), line("CITY", "MTA", "CITY")], [], {}, spec)
    orphan = next(t for t in tests if t["category"] == "LOOKUP")
    assert "NOT EXISTS (SELECT 1 FROM SILVER.COMPANY.COMPANY_CORE AS h" in orphan["sql"] and orphan["severity"] == "CRITICAL"
    st = next(t for t in tests if t["category"] == "SOURCE_TO_TARGET")
    assert "MINUS" in st["sql"] and "as row sets" in st["objective"]


def test_source_expression_and_script():
    assert source_expression(line("A", "T", "X", rule="UPPER({col})")) == "UPPER(X)"
    assert source_expression(line("A", "T", "X", rule="o.X || src.Y")) == "X || Y"
    text = script("DIM_CUSTOMER", suite())
    assert text.startswith("-- QA test suite for DIM_CUSTOMER") and "-- Expected: 0 rows" in text


def test_guard_allows_reads_of_run_tables_only():
    allowed = ["SILVER.CRM.DIM_CUSTOMER", "LAND.CRM.CUSTOMERS"]
    ok, problems, sql = check("WITH x AS (SELECT * FROM SILVER.CRM.DIM_CUSTOMER) SELECT EXTRACT(YEAR FROM d) FROM x;", allowed)
    assert ok, problems
    assert not sql.endswith(";")
    assert check('select * from "LAND"."CRM"."CUSTOMERS" c join silver.crm.dim_customer t on t.id = c.id', allowed)[0]
    for bad, reason in [
        ("DELETE FROM SILVER.CRM.DIM_CUSTOMER", "start with SELECT"),
        ("SELECT 1; DROP TABLE X", "one statement"),
        ("SELECT * FROM OTHER.DB.SECRETS", "not one of this run"),
        ("SELECT * FROM DIM_CUSTOMER", "fully qualified"),
        ("SELECT * FROM TABLE(GENERATOR(ROWCOUNT => 10))", "table functions"),
        ("SELECT SYSTEM$WHITELIST()", "not allowed"),
    ]:
        ok, problems, _ = check(bad, allowed)
        assert not ok and any(reason in p for p in problems), (bad, problems)
    assert check("SELECT 'drop table' AS note FROM SILVER.CRM.DIM_CUSTOMER", allowed)[0]


def test_derived_rules_and_unknown_columns_are_handled():
    lines = [line("CUSTOMER_ID", "CUSTOMERS", "CUST_ID", nullable=False),
             line("LOADED_AT", kind="DERIVED", rule="CURRENT_TIMESTAMP()"),
             line("RECORD_SOURCE", kind="DERIVED", rule="'CRM'"),
             line("CUSTOMER_KEY", kind="DERIVED", rule="MD5(CUST_ID)"),
             line("FULL_NAME", "CUSTOMERS", "NAME", kind="TRANSFORM", rule="FIRST_NM || ' ' || LAST_NM")]
    tests = {t["title"]: t for t in build_suite(TARGET, SOURCES, lines, ["CUSTOMER_ID"], GRAPH, {},
                                                 {"CUSTOMERS": ["CUST_ID", "NAME", "EMAIL"]})}
    assert "LOADED_AT IS NULL" in tests["LOADED_AT follows the STTM rule"]["sql"]
    assert "NOT EQUAL_NULL(RECORD_SOURCE, 'CRM')" in tests["RECORD_SOURCE follows the STTM rule"]["sql"]
    key = tests["CUSTOMER_KEY follows the STTM rule"]
    assert "MD5(CUST_ID) AS expected_value" in key["sql"] and "LAND.CRM.CUSTOMERS" in key["sql"] and not key["warning"]
    assert "FIRST_NM, LAST_NM" in tests["FULL_NAME follows the STTM rule"]["warning"]
    assert all("SOURCE_TABLE" not in t["sql"] for t in tests.values())
