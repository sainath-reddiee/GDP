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
