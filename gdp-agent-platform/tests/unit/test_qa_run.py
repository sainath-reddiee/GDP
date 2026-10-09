import decimal

from services.qa.procedures import ALL_CATEGORIES, ask_prompt, plan_prompt
from services.qa.run import blocking, evaluate, plain, references, summary, wrap


def test_zero_rows_tests():
    assert evaluate("0 rows", [], [])["outcome"] == "PASS"
    failed = evaluate("0 rows (repeat with the two SELECTs swapped)", ["ID"], [{"ID": 1}, {"ID": 2}])
    assert failed["outcome"] == "FAIL" and failed["failing"] == 2 and failed["detail"] == "2 failing rows returned"
    many = evaluate("0 rows", ["ID"], [{"ID": i} for i in range(101)])
    assert many["measured"] == "100+ rows"


def test_named_column_must_be_zero():
    ok = evaluate("difference = 0, or explained by documented filters", ["SOURCE_ROWS", "TARGET_ROWS", "DIFFERENCE"],
                  [{"SOURCE_ROWS": 10, "TARGET_ROWS": 10, "DIFFERENCE": 0}])
    assert ok["outcome"] == "PASS" and ok["detail"] == "difference = 0"
    bad = evaluate("extra_rows = 0 (N:1 or 1:1)", ["EXTRA_ROWS"], [{"EXTRA_ROWS": -3}])
    assert bad["outcome"] == "FAIL" and bad["failing"] == 3
    assert evaluate("unresolved = 0", ["OTHER"], [{"OTHER": 0}])["outcome"] == "REVIEW"
    assert evaluate("difference = 0", ["DIFFERENCE"], [])["outcome"] == "REVIEW"


def test_side_by_side_distributions():
    cols = ["SIDE", "VALUE", "N"]
    same = [{"SIDE": "expected", "VALUE": "A", "N": 2}, {"SIDE": "actual", "VALUE": "A", "N": 2}]
    assert evaluate("matching counts per value on both sides", cols, same)["outcome"] == "PASS"
    diff = same + [{"SIDE": "expected", "VALUE": "B", "N": 1}]
    out = evaluate("matching counts per value on both sides", cols, diff)
    assert out["outcome"] == "FAIL" and "B" in out["detail"]


def test_free_text_expectation_is_left_to_a_tester():
    assert evaluate("looks reasonable", ["X"], [{"X": 1}])["outcome"] == "REVIEW"


def test_wrap_survives_trailing_comments_and_values_are_plain():
    sql = wrap("SELECT 1 AS X -- note")
    assert sql.endswith("\n) AS QA_TEST LIMIT 101") and "-- note\n)" in sql
    assert plain(decimal.Decimal("3")) == 3 and plain(decimal.Decimal("2.5")) == 2.5


def test_target_reference_and_gate():
    assert references('SELECT * FROM "DB".MART.DIM_X', "DB.MART.DIM_X")
    assert not references("SELECT * FROM DB.MART.DIM_XY", "DB.MART.DIM_X")
    results = [{"outcome": "FAIL", "severity": "CRITICAL"}, {"outcome": "FAIL", "severity": "LOW"},
               {"outcome": "ERROR", "severity": "HIGH"}, {"outcome": "PASS", "severity": "CRITICAL"}]
    assert len(blocking(results)) == 2
    assert summary(results) == {"PASS": 1, "FAIL": 2, "REVIEW": 0, "NOT_RUN": 0, "ERROR": 1}


CTX = {"target": {"fqn": "DB.MART.DIM_ORDERS", "name": "DIM_ORDERS"}, "business_keys": ["ORDER_ID"],
       "sources": {"ORDERS": "DB.RAW.ORDERS"}, "source_columns": {"ORDERS": ["ORDER_ID NUMBER"]},
       "graph": {}, "allowed": ["DB.RAW.ORDERS", "DB.MART.DIM_ORDERS"],
       "lines": [{"target_column": "ORDER_ID", "target_datatype": "NUMBER", "source_table": "ORDERS",
                  "source_column": "ORDER_ID", "mapping_type": "DIRECT", "transformation": None}]}


def test_prompts_carry_domain_knowledge_and_machine_checkable_expectations():
    known = {"rules": ["[BUSINESS_RULE] Totals: order total equals the sum of its lines"],
             "profile": ["ORDERS.ORDER_ID NUMBER nulls=0% distinct=100% key"], "checks": ["ORDER_ID: NOT_NULL {}"]}
    ask = ask_prompt(CTX, "orders without lines", known)
    assert "order total equals the sum" in ask and "Approved data quality checks" in ask and "'0 rows'" in ask
    plan = plan_prompt(CTX, known, ["Row count: source vs target"], "money")
    assert "Write ONE" not in plan and "4 to 8 NEW" in plan and "- Row count: source vs target" in plan
    assert "Tester focus: money" in plan
    assert "BUSINESS_RULE" in ALL_CATEGORIES and "EDGE_CASE" in ALL_CATEGORIES


def test_missing_target_schema_falls_back_to_the_stand_in():
    from services.qa.procedures import missing_target

    t = "DE_DEMO_PROD.SILVER.DIM_CUSTOMERS"
    assert missing_target("SQL compilation error:\nSchema 'DE_DEMO_PROD.SILVER' does not exist or not authorized.", t)
    assert missing_target("Object 'DE_DEMO_PROD.SILVER.DIM_CUSTOMERS' does not exist or not authorized.", t)
    assert not missing_target("Object 'DE_DEMO_PROD.RAW.ORDERS' does not exist", "OTHERDB.SILVER.DIM_CUSTOMERS")
    assert not missing_target("invalid identifier 'X'", t)
    assert not missing_target("Object 'DE_DEMO_PROD.RAW.ORDERS' does not exist or not authorized.", t)
    assert missing_target("Database 'DE_DEMO_PROD' does not exist or not authorized.", t)
