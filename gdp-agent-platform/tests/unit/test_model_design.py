from services.modeling.design import (
    DESIGN_SCHEMA, baseline, build_prompt, conventions, describe_conventions, diff, normalize, spec_for, validate,
)

SOURCES = {"RAW_ORDERS": {"ORDER_ID": "NUMBER", "CUSTOMER_ID": "NUMBER", "TOTAL": "NUMBER(10,2)", "STATUS": "TEXT"},
           "RAW_CUSTOMERS": {"CUSTOMER_ID": "NUMBER", "EMAIL": "TEXT"}}


def attr(name, dtype="NUMBER", src=None, pk=False, derived=False):
    return {"name": name, "datatype": dtype, "nullable": not pk, "is_pk": pk,
            "source_columns": src or [], "derived": derived, "rationale": ""}


def design(*attrs, decision="NEW", target=""):
    return {"decision": decision, "decision_target": target, "reasons": [], "evidence": [],
            "entities": [{"entity_name": "fact_orders", "kind": "FACT", "purpose": "Orders", "grain": "one row per order",
                          "business_keys": ["ORDER_ID"], "attributes": list(attrs)}]}


def test_conventions_are_a_choice():
    none = conventions("NONE")
    assert none["audit_columns"] == [] and none["surrogate_key"] == "none"
    assert "No conventions" in describe_conventions(none)
    gdp = conventions("GDP")
    assert "GDP_INSERTED_TS" in gdp["audit_columns"] and gdp["surrogate_key"] == "sequence"
    company = conventions("COMPANY", company={"key_strategy": "hash", "scd_type": 2})
    assert company["surrogate_key"] == "hash" and company["scd_type"] == 2
    custom = conventions("CUSTOM", {"audit_columns": ["LOAD_TS", " "], "surrogate_key": "weird", "naming_case": "lower"})
    assert custom["audit_columns"] == ["LOAD_TS"] and custom["surrogate_key"] == "none" and custom["naming_case"] == "lower"
    assert conventions("bogus")["preset"] == "NONE"


def test_normalize_cases_names_and_marks_derived():
    d = normalize(design(attr("order id", src=["raw_orders.order_id"], pk=True), attr("order id"), attr("load_ts")),
                  conventions("GDP"))
    e = d["entities"][0]
    assert e["entity_name"] == "FACT_ORDERS"
    assert [a["name"] for a in e["attributes"]] == ["ORDER_ID", "LOAD_TS"]  # duplicate dropped
    assert e["attributes"][0]["source_columns"] == ["RAW_ORDERS.ORDER_ID"]
    assert e["attributes"][1]["derived"] is True  # no source -> derived
    assert d["primary_entity"] == "FACT_ORDERS"
    assert normalize({"decision": "MAYBE"}, conventions("NONE"))["decision"] == "NEW"


def test_validation_grounds_every_column_in_the_profile():
    conv = conventions("NONE")
    good = normalize(design(attr("ORDER_ID", src=["RAW_ORDERS.ORDER_ID"], pk=True),
                            attr("TOTAL", "NUMBER(10,2)", src=["RAW_ORDERS.TOTAL"])), conv)
    assert [i for i in validate(good, SOURCES, conv, []) if i["severity"] == "ERROR"] == []
    bad = normalize(design(attr("ORDER_ID", src=["RAW_ORDERS.ORDER_ID"], pk=True),
                           attr("DISCOUNT", src=["RAW_ORDERS.DISCOUNT"]), attr("X", "VARCHAR2")), conv)
    messages = [i["message"] for i in validate(bad, SOURCES, conv, [])]
    assert any("RAW_ORDERS.DISCOUNT is not a profiled column" in m for m in messages)
    assert any("VARCHAR2 is not a Snowflake type" in m for m in messages)


def test_reuse_must_point_at_a_registered_model_and_conventions_only_warn():
    conv = conventions("GDP")
    d = normalize(design(attr("ORDER_ID", src=["RAW_ORDERS.ORDER_ID"], pk=True), decision="EXTEND_EXISTING",
                         target="DB.SILVER.FACT_ORDERS"), conv)
    issues = validate(d, SOURCES, conv, [])
    assert any(i["severity"] == "ERROR" and "not a registered model" in i["message"] for i in issues)
    assert any(i["severity"] == "WARN" and "audit column GDP_INSERTED_TS" in i["message"] for i in issues)
    assert not [i for i in validate(d, SOURCES, conv, ["DB.SILVER.FACT_ORDERS"]) if i["severity"] == "ERROR"]
    free = validate(d, SOURCES, conventions("NONE"), ["DB.SILVER.FACT_ORDERS"])
    assert not any("audit column" in i["message"] for i in free)


def test_business_key_must_be_a_column():
    conv = conventions("NONE")
    d = normalize(design(attr("TOTAL", src=["RAW_ORDERS.TOTAL"])), conv)
    assert any("business key ORDER_ID is not a column" in i["message"] for i in validate(d, SOURCES, conv, []))


def test_diff_lists_column_changes():
    conv = conventions("NONE")
    a = normalize(design(attr("ORDER_ID", src=["RAW_ORDERS.ORDER_ID"], pk=True), attr("STATUS", "TEXT", ["RAW_ORDERS.STATUS"])), conv)
    b = normalize(design(attr("ORDER_ID", src=["RAW_ORDERS.ORDER_ID"], pk=True), attr("STATUS", "VARCHAR(20)", ["RAW_ORDERS.STATUS"]),
                         attr("TOTAL", "NUMBER(10,2)", ["RAW_ORDERS.TOTAL"])), conv)
    changes = {(c["change"], c.get("column")) for c in diff(a, b)}
    assert ("ADDED", "TOTAL") in changes and ("CHANGED", "STATUS") in changes


def test_prompt_is_grounded_and_spec_carries_lineage():
    ctx = {"domain": "SALES", "skills": "[AI-MODEL-GENERATION v1]",
           "sources": {"RAW_ORDERS": [{"column": "ORDER_ID", "type": "NUMBER", "key": True}]},
           "models": [{"fqn": "DB.SILVER.FACT_ORDERS", "description": "orders", "columns": ["ORDER_ID NUMBER"]}],
           "knowledge": ["[MODEL_DEFINITION] Orders: one row per order"]}
    prompt = build_prompt(ctx, conventions("NONE"), "keep it to one table")
    assert "MODEL:DB.SILVER.FACT_ORDERS" in prompt and "Never invent source columns" in prompt
    assert "No conventions" in prompt and "Developer request: keep it to one table" in prompt
    assert set(DESIGN_SCHEMA["required"]) >= {"decision", "entities"}
    conv = conventions("NONE")
    d = normalize(design(attr("ORDER_ID", src=["RAW_ORDERS.ORDER_ID"], pk=True), attr("LOAD_TS", derived=True)), conv)
    spec = spec_for(d["entities"][0], d, conv)
    assert spec["primary_key"] == ["ORDER_ID"] and spec["derived_columns"] == ["LOAD_TS"]
    assert spec["lineage"] == {"ORDER_ID": ["RAW_ORDERS.ORDER_ID"]}


def test_baseline_turns_the_registered_copy_into_version_one():
    b = baseline([{"name": "ORDER_ID", "type": "NUMBER", "key": True, "sources": ["RAW_ORDERS.ORDER_ID"]},
                  {"name": "Notes", "type": "TEXT"}], "DIM_RAW_ORDERS", {"naming_case": "as_is"})
    attrs = b["entities"][0]["attributes"]
    assert b["entities"][0]["entity_name"] == "DIM_RAW_ORDERS" and attrs[1]["name"] == "Notes" and attrs[1]["derived"]
