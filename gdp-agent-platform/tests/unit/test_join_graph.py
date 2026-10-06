import pytest

from services.sttm import join_graph as jg

RELATIONSHIPS = [
    {"left": "STG_SUBSCRIPTIONS", "right": "STG_ACCOUNTS", "keys": ["ACC_FK=ACCOUNT_ID"], "cardinality": "N:1",
     "confidence": 0.94, "evidence": ["value range inside the parent key range"], "source": "profile"},
    {"left": "STG_SUBSCRIPTIONS", "right": "STG_ACCOUNTS", "keys": ["BILLING_ACC=ACCOUNT_ID"], "cardinality": "N:1",
     "confidence": 0.9, "source": "profile"},
    {"left": "STG_CONTACTS", "right": "STG_ACCOUNTS", "keys": ["ACCOUNT_ID"], "cardinality": "N:1", "confidence": 0.8},
    {"left": "STG_ACCOUNTS", "right": "STG_ACCOUNT_EXT", "keys": ["ACCOUNT_ID"], "cardinality": "1:1",
     "confidence": 0.97, "source": "profile"},
]
TABLES = ["STG_ACCOUNTS", "STG_CONTACTS", "STG_SUBSCRIPTIONS", "STG_ACCOUNT_EXT", "STG_ORPHAN"]


def plan(**kw):
    counts = kw.pop("counts", {"STG_ACCOUNTS": 5, "STG_SUBSCRIPTIONS": 2, "STG_CONTACTS": 1, "STG_ACCOUNT_EXT": 1})
    complete = kw.pop("complete", {("STG_ACCOUNTS", "ACCOUNT_ID"): True, ("STG_ACCOUNT_EXT", "ACCOUNT_ID"): True})
    return jg.build_join_graph(TABLES, RELATIONSHIPS, counts, complete, **kw)


def test_driving_table_and_spanning_tree():
    g = plan()
    assert g["driving_table"] == "STG_ACCOUNTS"
    by_right = {j["right_table"]: j for j in g["joins"]}
    assert set(by_right) == {"STG_SUBSCRIPTIONS", "STG_CONTACTS", "STG_ACCOUNT_EXT"}
    assert g["unreachable"] == ["STG_ORPHAN"]
    sub = by_right["STG_SUBSCRIPTIONS"]
    assert sub["left_table"] == "STG_ACCOUNTS" and sub["keys"] == ["ACCOUNT_ID=ACC_FK"]
    assert sub["condition"] == "STG_ACCOUNTS.ACCOUNT_ID = STG_SUBSCRIPTIONS.ACC_FK"
    assert sub["cardinality"] == "1:N" and sub["fan_out"] and sub["join_type"] == "LEFT"


def test_one_to_one_with_complete_keys_is_inner_and_name_matches_are_capped():
    by_right = {j["right_table"]: j for j in plan()["joins"]}
    assert by_right["STG_ACCOUNT_EXT"]["join_type"] == "INNER"
    contacts = by_right["STG_CONTACTS"]
    assert contacts["confidence"] <= 0.7 and contacts["source"] == "name" and "column names only" in contacts["reasoning"]
    assert plan(complete={})["joins"][0]["join_type"] == "LEFT"


def test_close_alternatives_are_reported_as_ambiguity():
    g = plan()
    sub = next(j for j in g["joins"] if j["right_table"] == "STG_SUBSCRIPTIONS")
    assert sub["alternatives"][0]["keys"] == ["ACCOUNT_ID=BILLING_ACC"]
    assert [a["table"] for a in g["ambiguities"]] == ["STG_SUBSCRIPTIONS"]


def test_child_driving_table_turns_lookups_into_n_to_1():
    g = plan(driving="STG_SUBSCRIPTIONS")
    acc = next(j for j in g["joins"] if j["right_table"] == "STG_ACCOUNTS")
    assert acc["left_table"] == "STG_SUBSCRIPTIONS" and acc["cardinality"] == "N:1" and not acc["fan_out"]


def test_overrides_are_validated_and_confirmed():
    cols = {"STG_ACCOUNTS": ["ACCOUNT_ID"], "STG_SUBSCRIPTIONS": ["ACC_FK", "BILLING_ACC"], "STG_CONTACTS": ["ACCOUNT_ID"],
            "STG_ACCOUNT_EXT": ["ACCOUNT_ID"], "STG_ORPHAN": ["ACCOUNT_REF"]}
    g = plan()
    edited = jg.apply_overrides(g, None, [
        {"left_table": "STG_ACCOUNTS", "right_table": "STG_SUBSCRIPTIONS", "keys": ["ACCOUNT_ID=BILLING_ACC"], "join_type": "INNER"},
        {"left_table": "STG_ACCOUNTS", "right_table": "STG_ORPHAN", "keys": ["ACCOUNT_ID=ACCOUNT_REF"]},
    ], cols)
    sub = next(j for j in edited["joins"] if j["right_table"] == "STG_SUBSCRIPTIONS")
    assert sub["status"] == "CONFIRMED" and sub["join_type"] == "INNER" and sub["source"] == "manual"
    assert edited["unreachable"] == [] and edited["ambiguities"] == []
    with pytest.raises(AssertionError, match="does not exist"):
        jg.apply_overrides(g, None, [{"left_table": "STG_ACCOUNTS", "right_table": "STG_ORPHAN", "keys": ["X=Y"]}], cols)
    with pytest.raises(AssertionError, match="join_type"):
        jg.apply_overrides(g, None, [{"left_table": "STG_ACCOUNTS", "right_table": "STG_ORPHAN",
                                      "keys": ["ACCOUNT_ID=ACCOUNT_REF"], "join_type": "CROSS"}], cols)


def test_join_logic_dbt_shape_and_preview():
    g = plan()
    logic = jg.join_logic_by_table(g)
    assert logic["STG_ACCOUNTS"] == "FROM STG_ACCOUNTS"
    assert logic["STG_SUBSCRIPTIONS"].startswith("FROM STG_ACCOUNTS LEFT JOIN STG_SUBSCRIPTIONS ON")
    assert {"left", "right", "keys", "cardinality", "join_type"} <= set(jg.to_dbt_joins(g)[0])
    sql = jg.preview_sql(g, [
        {"target_column": "COMPANY_ID", "source_table": "STG_ACCOUNTS", "source_column": "ACCOUNT_ID"},
        {"target_column": "COMPANY_NAME", "source_table": "STG_ACCOUNTS", "source_column": "ACC_NAME",
         "transformation": "TRIM(UPPER(o.ACC_NAME))"},
        {"target_column": "LEGACY", "source_table": "STG_ORPHAN", "source_column": "X"},
    ], "DIM_COMPANY")
    assert "o.ACCOUNT_ID AS COMPANY_ID" in sql and "TRIM(UPPER(o.ACC_NAME)) AS COMPANY_NAME" in sql
    assert "FROM STG_ACCOUNTS AS o" in sql and "no join path" in sql and "fan-out" in sql


def test_dbt_join_plan_follows_multi_hop_paths_and_join_types():
    from services.dbt.onboard import _join_plan

    joins = jg.to_dbt_joins(plan(driving="STG_CONTACTS"))
    used = ["STG_CONTACTS", "STG_ACCOUNTS", "STG_ACCOUNT_EXT", "STG_ORPHAN"]
    steps, missing = _join_plan("STG_CONTACTS", {}, used, joins)
    by_table = {s["table"]: s for s in steps}
    assert by_table["STG_ACCOUNTS"]["on"].startswith("o.")
    ext = by_table["STG_ACCOUNT_EXT"]
    assert ext["on"].startswith(by_table["STG_ACCOUNTS"]["alias"] + ".") and ext["join_type"] == "inner"
    assert missing == ["STG_ORPHAN"]
