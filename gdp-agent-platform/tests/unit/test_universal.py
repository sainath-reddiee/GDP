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
