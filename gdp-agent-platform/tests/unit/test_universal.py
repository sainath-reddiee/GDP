"""Onboarding an unfamiliar source: shapes, identifiers and rules must never crash a stage."""

from services.knowledge.validate import normalize_content, validate_pack
from services.mapping.features import domain_score, propose_transformation
from services.source.identifiers import apply_col, sql_ident


def test_identifiers_keep_their_exact_spelling():
    assert sql_ident("CUSTOMER_ID") == "CUSTOMER_ID"
    assert sql_ident("customerId") == '"customerId"'
    assert sql_ident("order date") == '"order date"'
    assert sql_ident('we"ird') == '"we""ird"'


def test_rules_with_braces_and_mixed_case_columns_do_not_crash():
    knowledge = {"rules": {}, "transforms": [
        {"target_column": "ZIP", "expression": "REGEXP_SUBSTR({col}, '[0-9]{5}')"},
        {"target_column": "PAYLOAD", "expression": "PARSE_JSON('{\"k\": 1}'):k || {col}"},
    ]}
    zip_expr, _ = propose_transformation({"column_name": "postalCode", "data_type": "TEXT"},
                                         {"column_name": "ZIP", "data_type": "TEXT"}, knowledge)
    assert zip_expr == "REGEXP_SUBSTR(\"postalCode\", '[0-9]{5}')"
    payload, _ = propose_transformation({"column_name": "RAW", "data_type": "TEXT"},
                                        {"column_name": "PAYLOAD", "data_type": "TEXT"}, knowledge)
    assert payload == "PARSE_JSON('{\"k\": 1}'):k || RAW"
    assert apply_col("{col} || '{x}'", "A") == "A || '{x}'"


def test_decode_values_are_escaped_and_accept_any_shape():
    rule = normalize_content("BUSINESS_RULE", {"target_column": "status", "source_values": [["A", "Active"], ["O", "O'Hare"]]})
    assert rule["source_values"] == {"A": "Active", "O": "O'Hare"}
    expr, _ = propose_transformation({"column_name": "STS", "data_type": "TEXT"},
                                     {"column_name": "status", "data_type": "TEXT"}, {"rules": {"STATUS": rule}, "transforms": []})
    assert "THEN 'O''Hare'" in expr


def test_malformed_knowledge_is_repaired_or_dropped():
    glossary = normalize_content("GLOSSARY", {"target_column": "NAME", "synonyms": None})
    assert glossary["synonyms"] == []
    assert normalize_content("GLOSSARY", {"synonyms": ["X"]}) is None
    assert normalize_content("GLOSSARY", "not an object") is None
    rule = normalize_content("TRANSFORMATION_RULE", {"target_column": "A", "soda_checks": ["oops", {"check_type": "NOT_NULL"}]})
    assert rule["soda_checks"] == [{"check_type": "NOT_NULL"}]
    assert domain_score({"column_name": "NM", "data_type": "TEXT"}, {"column_name": "NAME", "data_type": "TEXT"},
                        {"glossary": {"NAME": {"target_column": "NAME", "synonyms": None}}, "rules": {}})[0] >= 0


def test_validate_pack_flags_problems():
    bad = {"domain": {"name": "X"}, "targets": [{"table": "T", "columns": [{"type": "TEXT"}]}],
           "knowledge": [{"key": "k1", "type": "GLOSSARY", "content_json": {"target_column": "A", "synonyms": "A"}},
                         {"key": "k2", "type": "WHATEVER"},
                         {"key": "k3", "type": "TRANSFORMATION_RULE", "content_json": {"expression": "UPPER(x)"}}]}
    problems = " | ".join(validate_pack(bad))
    assert "missing its name" in problems and "synonyms must be a list" in problems
    assert "unknown knowledge type" in problems and "must reference {col}" in problems


def test_domain_keywords_match_whole_tokens_only():
    from services.knowledge.domain import _matches

    assert not _matches("SITE", "WEBSITE_URL") and not _matches("LAND", "ISLAND_CODE")
    assert _matches("BUILDING", "BUILDINGS") and _matches("DUNS", "COMPANY_DUNS_NO")
    assert _matches("EFF_STATUS", "CUST_EFF_STATUS") and not _matches("EFF_STATUS", "EFF_DT_STATUS")
    assert _matches("__C", "STAGE__C") and _matches("LOT", "LOT_SIZE") and not _matches("LOT", "PILOT_FLAG")


def test_dbt_keeps_lowercase_source_names_exact():
    from services.dbt.onboard import generate

    src = {"name": "ORDERS", "identifier": "orders", "database": "RAW", "schema": "shop",
           "columns": {"ORDER_ID": "TEXT", "CUSTOMER_ID": "TEXT", "AMOUNT": "NUMBER(12,2)"},
           "names": {"ORDER_ID": "orderId", "CUSTOMER_ID": "customer_id", "AMOUNT": "AMOUNT"}}
    cust = {"name": "CUSTOMERS", "identifier": "customers", "database": "RAW", "schema": "shop",
            "columns": {"CUSTOMER_ID": "TEXT", "NAME": "TEXT"}, "names": {"CUSTOMER_ID": "customer_id", "NAME": "name"}}
    line = lambda col, dt, scol, table="ORDERS": {"target_column": col, "target_datatype": dt, "source_table": table,  # noqa: E731
                                                  "source_column": scol, "mapping_type": "DIRECT", "transformation": None}
    out = generate({
        "domain": "shop", "target": "ORDERS_DIM", "source_key": "shop", "source_system": "SHOP", "prefix": "",
        "business_keys": ["ORDER_ID"], "grain": "one row per order", "sources": [src, cust],
        "target_columns": [{"column_name": "ORDER_ID", "data_type": "VARCHAR"}, {"column_name": "AMOUNT", "data_type": "NUMBER(12,2)"},
                           {"column_name": "CUSTOMER_NAME", "data_type": "VARCHAR"}],
        "lines": [line("ORDER_ID", "VARCHAR", "orderId"), line("AMOUNT", "NUMBER(12,2)", "AMOUNT"),
                  line("CUSTOMER_NAME", "VARCHAR", "name", "CUSTOMERS")],
        "joins": [{"left": "ORDERS", "right": "CUSTOMERS", "keys": ["CUSTOMER_ID"], "cardinality": "N:1"}],
    })
    text = "\n".join(out["files"].values())
    assert 'o."orderId"' in text and 'o.AMOUNT' in text and '"name"' in text
    assert 'o."customer_id" = j1."customer_id"' in text
    assert "quoting:" in text and "identifier: true" in text


def test_validation_accepts_the_generators_own_layout():
    from services.dbt.onboard import generate
    from services.validation.checks import run

    src = {"name": "ORDERS", "identifier": "ORDERS", "database": "RAW", "schema": "SHOP",
           "columns": {"ORDER_ID": "TEXT", "AMOUNT": "NUMBER(12,2)"}, "names": {}}
    lines = [{"target_column": "ORDER_ID", "target_datatype": "VARCHAR", "source_table": "ORDERS",
              "source_column": "ORDER_ID", "mapping_type": "DIRECT", "transformation": None, "nullable_rule": False},
             {"target_column": "AMOUNT", "target_datatype": "NUMBER(12,2)", "source_table": "ORDERS",
              "source_column": "AMOUNT", "mapping_type": "DIRECT", "transformation": None, "nullable_rule": True}]
    out = generate({"domain": "shop", "target": "ORDERS_DIM", "source_key": "shop", "source_system": "SHOP", "prefix": "",
                    "business_keys": ["ORDER_ID"], "grain": "one row per order", "sources": [src],
                    "target_columns": [{"column_name": "ORDER_ID", "data_type": "VARCHAR"},
                                       {"column_name": "AMOUNT", "data_type": "NUMBER(12,2)"}],
                    "lines": lines, "joins": []})
    files = {**out["files"], "soda/checks.yml": "checks for orders_dim:\n  - row_count > 0\n"}
    results = {r["validation_type"]: r for r in run(files, lines, files["soda/checks.yml"])}
    for kind in ("REQUIRED_COLUMNS", "STTM_CONSISTENCY", "SCHEMA", "NAMING"):
        assert results[kind]["status"] == "PASSED", (kind, results[kind]["findings"])


def test_technical_columns_come_from_the_table_definition_first():
    from services.common.standard import GDP, GENERIC, default_prefix, run_standard, technical_semantic

    # any company: the column definition decides
    assert technical_semantic("ROW_ID", GENERIC, is_identity=True) == "SURROGATE_KEY"
    assert technical_semantic("loaded", GENERIC, column_default="CURRENT_TIMESTAMP()") == "AUDIT_TIMESTAMP"
    assert technical_semantic("ORDERS_HKEY", GENERIC, "ORDERS") is None  # GDP naming is not imposed
    # GDP runs add the GDP conventions
    assert technical_semantic("ORDERS_HKEY", GDP, "ORDERS") == "SURROGATE_KEY"
    assert technical_semantic("GDP_INSERTED_TS", GDP, "ORDERS") == "AUDIT_TIMESTAMP"
    assert technical_semantic("COMPANY_CORE_SKEY", GDP, "COMPANY_ADDRESS", hub_fk="COMPANY_CORE_SKEY") == "DERIVED_KEY"
    assert technical_semantic("COMPANY_CORE_SKEY", GDP, "OPPORTUNITY_CORE") is None  # another hub: mapped
    assert run_standard({}) == GDP and run_standard({"MODELING_STANDARD": "generic"}) == GENERIC
    assert default_prefix(GENERIC) == "" and default_prefix(GDP) == "GDP"


def test_modeling_standard_drives_prefix_branch_and_skills():
    from services.common.standard import GDP, GENERIC, default_prefix, run_standard
    from services.dbt.procedures import merge_branch_plan
    from services.knowledge.usage import stage_skills

    assert run_standard({}) == GDP  # runs from before the choice keep GDP behaviour
    assert run_standard({"MODELING_STANDARD": "generic"}) == GENERIC
    assert default_prefix(GENERIC) == ""
    plan = merge_branch_plan({}, {}, "Orders onboarding", "r1", GENERIC)
    assert plan["prefix"] == "" and plan["cut_branch"].startswith("feat/onboard-")
    assert merge_branch_plan({}, {}, "Orders", "r1")["prefix"] == "GDP"
    assert not [s for s in stage_skills("DBT", GENERIC) if s.startswith("GDP")]
    assert "GDP-DBT-ONBOARD-SOURCE" in stage_skills("DBT", GDP)


def test_generic_standard_ignores_gdp_naming():
    from services.common.standard import GDP, GENERIC, technical_semantic

    assert technical_semantic("CUSTOMER_HKEY", GDP) == "SURROGATE_KEY"
    assert technical_semantic("CUSTOMER_HKEY", GENERIC) is None
    assert technical_semantic("id", GENERIC, is_identity=True) == "SURROGATE_KEY"
    assert technical_semantic("loaded_at", GENERIC, column_default="CURRENT_TIMESTAMP()") == "AUDIT_TIMESTAMP"


def test_dbt_never_guesses_a_row_key():
    import pytest

    from services.dbt.onboard import NoUniqueKey, _unique_key

    cols = [{"target_column": "NAME", "source_column": "name", "source_table": "T"},
            {"target_column": "ORDER_NO", "source_column": "order_no", "source_table": "T"}]
    with pytest.raises(NoUniqueKey):  # profiled, nothing unique: stop instead of collapsing rows
        _unique_key(cols, [], {}, "T", {"T": []})
    picked, reason = _unique_key(cols, [], {}, "T", {"T": ["ORDER_NO"]})
    assert picked[0]["source_column"] == "order_no" and "profiled as unique" in reason
    picked, _ = _unique_key(cols, ["NAME"], {}, "T", {"T": []})
    assert picked[0]["target_column"] == "NAME"


def test_pii_detected_from_values_whatever_the_column_is_called():
    from services.profiling.profiler import build_profile, value_pii

    freq = lambda *vals: [{"value": v, "count": 1} for v in vals]  # noqa: E731
    assert value_pii(freq("123-45-6789", "987-65-4321")) == "SSN"
    assert value_pii(freq("4111 1111 1111 1111", "5500-0000-0000-0004")) == "CARD"
    assert value_pii(freq("a@x.com", "b.c@y.org")) == "EMAIL"
    assert value_pii(freq("10.0.0.1", "192.168.1.20")) == "IP_ADDRESS"
    assert value_pii(freq("+1 (555) 123-4567", "555-987-6543")) == "PHONE"
    assert value_pii(freq("1000234567", "1000234568")) == "NONE"  # plain ids are not phones
    assert value_pii(freq("2024-01-31", "2023-12-01")) == "NONE"
    stats = {"row_count": 2, "physical_null_count": 0, "null_count": 0, "distinct_count": 2,
             "min": "123-45-6789", "max": "987-65-4321"}
    p = build_profile("ref_x", "VARCHAR", stats, freq("123-45-6789", "987-65-4321"), [])
    assert p["pii_classification"] == "SSN"
    assert p["sample_values"][0]["value"] == "***6789" and p["statistics"]["min"] is None


def test_catalog_table_names_keep_their_spelling():
    import pytest
    from fastapi import HTTPException

    from app.main import _table_ident

    assert _table_ident("members") == "members"
    assert _table_ident("Order Items") == "Order Items"
    assert _table_ident('"Bookings"') == "Bookings"
    with pytest.raises(HTTPException):
        _table_ident('bad"name')


def test_day_first_dates_are_read_from_the_values():
    from services.profiling.profiler import date_format

    shape = [{"pattern": "99/99/9999"}]
    assert date_format(shape, ["27/11/2023", "13/03/2023"]) == "DD/MM/YYYY"
    assert date_format(shape, ["11/27/2023", "03/13/2023"]) == "MM/DD/YYYY"
    assert date_format(shape, ["01/02/2023"]) == "MM/DD/YYYY"  # ambiguous keeps the shape's reading
    assert date_format([{"pattern": "99.99.9999"}], ["31.12.2023"]) == "DD.MM.YYYY"


def test_enrichment_never_clears_detected_pii(monkeypatch):
    from services.profiling import procedures

    monkeypatch.setattr(procedures, "complete_json", lambda *a, **k: (
        {"columns": [{"column_name": "tax_ref", "semantic_type": "ACCOUNT_NUMBER", "description": "ref"},
                     {"column_name": "mail", "semantic_type": "EMAIL", "description": "mail"}]}, {}, "m"))
    profiles = [{"column_name": "tax_ref", "semantic_type": "IDENTIFIER", "pii_classification": "SSN"},
                {"column_name": "mail", "semantic_type": "TEXT", "pii_classification": "NONE"}]
    monkeypatch.setattr(procedures.profiler, "enrichment_prompt", lambda *a, **k: "prompt")
    procedures._enrich(None, None, "members", profiles, "")
    assert profiles[0]["pii_classification"] == "SSN"  # model relabelled the type; detected PII stays
    assert profiles[1]["pii_classification"] == "EMAIL"


def test_product_tokens_are_stripped_only_as_whole_tokens():
    from services.source.catalog_display import configure, source_system_name

    assert source_system_name(explicit="GDPR_EVENTS") == "GDPR_EVENTS"
    assert source_system_name(schema="GDP_CRM") == "CRM"
    assert source_system_name(schema="SALES_GDP_RAW") == "SALES_RAW"
    configure({"strip_tokens": ["ACME"]})
    try:
        assert source_system_name(schema="ACME_ERP") == "ERP" and source_system_name(schema="GDP_CRM") == "GDP_CRM"
    finally:
        configure({"strip_tokens": ["GDP"]})
