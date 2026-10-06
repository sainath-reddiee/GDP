"""Pure unit tests for STTM, Soda, dbt generation and validation."""

from services.dbt.project import build, snake
from services.knowledge.search import search_request
from services.knowledge.usage import STAGE_SKILLS, assert_safe_transformation
from services.mapping.features import SYSTEM_DERIVED, propose_transformation
from services.mapping.feedback import pattern as feedback_pattern
from services.soda.expectations import from_client, from_sttm, merge_checks, render_yaml, without_rejected
from services.soda.extract import parse_client_document, requirement_from_row
from services.soda.feedback import pattern as soda_feedback
from services.sttm.assemble import assemble
from services.sttm.refine import case_literals, from_transform, refine_prompt, render_csv, reusable_expression
from services.validation.checks import run as validate, summary
from infrastructure.seed_knowledge import DEFAULT_WEIGHTS, DEFAULT_THRESHOLDS, list_skills, load_domain_pack
from services.mapping.scoring import validate_config


def _target():
    return {
        "grain": "one customer", "scd_type": "1", "business_keys": ["CUSTOMER_ID"],
        "target_database": "DEV_AI_PLATFORM", "target_schema": "GDP_SILVER",
        "target_table": "DIM_CUSTOMER", "table_name": "DIM_CUSTOMER",
    }


def _columns():
    return [
        {"target_column_id": "k", "column_name": "CUSTOMER_KEY", "data_type": "VARCHAR(32)",
         "nullable": False, "semantic_type": "SURROGATE_KEY", "is_business_key": False, "definition": "sk"},
        {"target_column_id": "id", "column_name": "CUSTOMER_ID", "data_type": "VARCHAR(20)",
         "nullable": False, "semantic_type": "IDENTIFIER", "is_business_key": True, "definition": "bk"},
        {"target_column_id": "nm", "column_name": "CUSTOMER_NAME", "data_type": "VARCHAR(200)",
         "nullable": False, "semantic_type": "PERSON_NAME", "is_business_key": False, "definition": "name"},
        {"target_column_id": "src", "column_name": "RECORD_SOURCE", "data_type": "VARCHAR(50)",
         "nullable": False, "semantic_type": "RECORD_SOURCE", "is_business_key": False, "definition": "src"},
        {"target_column_id": "at", "column_name": "LOADED_AT", "data_type": "TIMESTAMP_NTZ",
         "nullable": False, "semantic_type": "AUDIT_TIMESTAMP", "is_business_key": False, "definition": "ts"},
    ]


def test_assemble_blocks_unmapped_required():
    result = assemble(_target(), _columns(), {}, "CRM", "SRC", "CRM")
    assert "CUSTOMER_ID" in result["unmapped_required"]
    assert "CUSTOMER_NAME" in result["unmapped_required"]
    assert "CUSTOMER_KEY" not in result["unmapped_required"]
    derived = {l["target_column"]: l for l in result["lines"]}
    assert derived["CUSTOMER_KEY"]["mapping_type"] == "DERIVED"
    assert "MD5" in derived["CUSTOMER_KEY"]["transformation"]
    assert derived["RECORD_SOURCE"]["transformation"] == "'CRM'"


def test_assemble_from_approved_decisions():
    decisions = {
        "id": {"decision_id": "d1", "source_column": "CUST_ID", "source_table": "CRM_CUSTOMER",
               "source_datatype": "VARCHAR", "transformation": None, "confidence": 0.9, "reviewer": "U"},
        "nm": {"decision_id": "d2", "source_column": "FIRST_NM", "source_table": "CRM_CUSTOMER",
               "source_datatype": "VARCHAR",
               "transformation": "TRIM(INITCAP(TRIM(first_nm)) || ' ' || INITCAP(TRIM(last_nm)))",
               "confidence": 0.8, "reviewer": "U"},
    }
    result = assemble(_target(), _columns(), decisions, "CRM", "SRC", "CRM")
    assert result["unmapped_required"] == []
    name = next(l for l in result["lines"] if l["target_column"] == "CUSTOMER_NAME")
    assert name["mapping_type"] == "TRANSFORM"


def test_soda_from_sttm_includes_grain_and_accepted_values():
    lines = [
        {"target_column": "CUSTOMER_ID", "nullable_rule": False, "accepted_values": [], "target_datatype": "VARCHAR"},
        {"target_column": "CUSTOMER_STATUS", "nullable_rule": False, "accepted_values": ["ACTIVE", "INACTIVE"],
         "target_datatype": "VARCHAR", "business_definition": "status"},
        {"target_column": "EMAIL_ADDRESS", "nullable_rule": True, "accepted_values": [],
         "target_datatype": "VARCHAR", "business_definition": "customer email", "semantic_type": "EMAIL"},
        {"target_column": "LOADED_AT", "nullable_rule": False, "accepted_values": [],
         "target_datatype": "TIMESTAMP_NTZ", "semantic_type": "AUDIT_TIMESTAMP"},
    ]
    checks = from_sttm("DIM_CUSTOMER", lines, ["CUSTOMER_ID"])
    kinds = {c["check_type"] for c in checks}
    assert {"ROW_COUNT", "UNIQUE", "NOT_NULL", "ACCEPTED_VALUES", "SCHEMA", "FRESHNESS", "CUSTOM"} <= kinds
    yaml = render_yaml("dim_customer", checks)
    assert "checks for dim_customer" in yaml
    assert "row_count" in yaml
    assert "missing_count(CUSTOMER_ID) = 0" in yaml
    assert "duplicate_count(CUSTOMER_ID) = 0" in yaml
    assert "valid format: email" in yaml
    assert "freshness(LOADED_AT) < 1d" in yaml
    assert "when required column missing" in yaml


def test_soda_parses_client_brief_and_csv():
    csv_doc = parse_client_document(
        "attribute,check_type,severity,requirement\nEMAIL_ADDRESS,CUSTOM,WARN,Email must be valid\n",
        "rules.csv",
    )
    assert csv_doc["rows"][0]["attribute"] == "EMAIL_ADDRESS"
    text_doc = parse_client_document("Customers must have a unique id and a valid email.")
    assert "unique id" in text_doc["brief"]
    check = requirement_from_row("DIM_CUSTOMER", {
        "attribute": "EMAIL_ADDRESS", "check_type": "email", "severity": "WARN",
        "valid_format": "email", "requirement": "Email must be valid",
    })
    assert check["definition"]["kind"] == "format"
    packed = merge_checks(from_client("DIM_CUSTOMER", csv_doc["rows"]), [check])
    assert any(c["target_column"] == "EMAIL_ADDRESS" for c in packed)


def test_soda_feedback_becomes_a_pattern():
    kept = soda_feedback("DIM_CUSTOMER", "EMAIL_ADDRESS", "CUSTOM", "APPROVED",
                         "valid email", "client SLA", {"kind": "format", "format": "email"})
    assert kept["active"] and kept["source_reference"].startswith("SODA.")
    dropped = soda_feedback("DIM_CUSTOMER", "PHONE", "CUSTOM", "REJECTED", None, "out of scope", {})
    assert dropped["active"] is False
    assert "must not be generated" in dropped["content"]
    kept_set = without_rejected(
        [{"check_type": "CUSTOM", "target_column": "PHONE", "origin": "AI"},
         {"check_type": "CUSTOM", "target_column": "PHONE", "origin": "CLIENT"}],
        [{"check_type": "CUSTOM", "target_column": "PHONE"}],
    )
    assert [c["origin"] for c in kept_set] == ["CLIENT"]


def test_dbt_project_and_validation_pass():
    sttm = assemble(_target(), _columns(), {
        "id": {"source_column": "CUST_ID", "source_table": "CRM_CUSTOMER", "source_datatype": "VARCHAR"},
        "nm": {"source_column": "FIRST_NM", "source_table": "CRM_CUSTOMER", "source_datatype": "VARCHAR",
               "transformation": "INITCAP(TRIM(first_nm))"},
    }, "CRM", "DEV_AI_PLATFORM", "LANDING")
    files = build(sttm, [{"name": "initcap_trim", "sql": "{% macro initcap_trim(c) %}INITCAP(TRIM({{ c }})){% endmacro %}"}],
                  render_yaml("dim_customer", from_sttm("DIM_CUSTOMER", sttm["lines"], ["CUSTOMER_ID"])),
                  [{"source_table": "CRM_CUSTOMER", "landing_table": "CRM__CRM_CUSTOMER",
                    "database": "DEV_AI_PLATFORM", "schema": "LANDING"}],
                  "CRM")
    assert "dbt_project.yml" in files
    assert any(p.startswith("models/marts/") and p.endswith(".sql") for p in files)
    assert "macros/initcap_trim.sql" in files
    assert "mappings/sttm.json" in files
    assert "mappings/sttm.csv" in files
    assert "CUSTOMER_ID" in files["mappings/sttm.json"]
    assert "CUSTOMER_ID" in files["mappings/sttm.csv"]
    results = validate(files, [{**l, "required": l["mapping_type"] != "UNMAPPED" and not l["nullable_rule"]}
                               for l in sttm["lines"]], files["soda/checks.yml"])
    status, errors, _ = summary(results)
    assert status == "PASSED" and errors == 0, results
    assert snake("DIM_CUSTOMER") == "dim_customer"


def test_search_request_filters_active():
    req = search_request("customer", domain="GDP", limit=5)
    assert req["query"] == "customer"
    assert req["filter"]["@and"][0] == {"@eq": {"STATUS": "ACTIVE"}}
    assert {"@eq": {"DOMAIN_NAME": "GDP"}} in req["filter"]["@and"]


def test_seed_pack_and_skills():
    pack = load_domain_pack()
    assert pack["domain"]["name"] == "GDP"
    assert any(c["name"] == "CUSTOMER_ID" for t in pack["targets"] for c in t["columns"])
    skills = list_skills()
    names = {s["name"] for s in skills}
    assert {"GDP_DOMAIN_SKILL", "MAPPING_SKILL", "STTM_SKILL", "SODA_SKILL", "VALIDATION_SKILL"} <= names
    assert {"SILVER-MODEL", "GDP-DBT-ONBOARD-SOURCE", "AI-DATA-MODELING", "COLUMN-PROFILING",
            "AI-SCHEMA-MAPPING", "DEV-DATAREADINESS-CHECK"} <= names
    silver = next(s for s in skills if s["name"] == "SILVER-MODEL")
    assert silver["source_name"] == "silver-model"
    assert "column-classification.md" in silver["content"]
    assert silver["description"] and not silver["description"].startswith(">")
    validate_config(DEFAULT_WEIGHTS, DEFAULT_THRESHOLDS)


def test_full_name_transform_preferred():
    expr, rule = propose_transformation(
        {"column_name": "FIRST_NM", "data_type": "VARCHAR", "pattern": None},
        {"column_name": "CUSTOMER_NAME", "data_type": "VARCHAR(200)", "semantic_type": "PERSON_NAME"},
        {"rules": {}, "transforms": [
            {"target_column": "CUSTOMER_NAME",
             "expression": "TRIM(INITCAP(TRIM(first_nm)) || ' ' || INITCAP(TRIM(last_nm)))"},
            {"semantic_type": "PERSON_NAME", "expression": "INITCAP(TRIM({col}))"},
        ]},
    )
    assert "last_nm" in expr and rule == "transformation rule"


def test_reviewer_feedback_becomes_a_pattern():
    item = feedback_pattern("CUST_ID", "CRM_CUSTOMER", "CUSTOMER_ID", "DIM_CUSTOMER",
                            "MODIFIED", "TRIM(cust_id)", "enterprise key", "CUSTOMER_NAME")
    assert item["active"] and item["content_json"]["overridden"]
    assert item["source_reference"] == "feedback.CUST_ID"
    rejected = feedback_pattern("PHONE_NO", "CRM_CUSTOMER", None, "DIM_CUSTOMER", "REJECTED", None, None, "PHONE")
    assert rejected["active"] is False


def test_mapping_skill_blocks_destructive_sql():
    assert_safe_transformation("INITCAP(TRIM(first_nm))")
    try:
        assert_safe_transformation("DELETE FROM dim_customer")
    except ValueError as exc:
        assert "MAPPING_VALIDATION" in str(exc)
    else:
        raise AssertionError("destructive SQL was accepted")
    assert "AI-SCHEMA-MAPPING" in STAGE_SKILLS["MAPPING"]
    assert "SILVER-MODEL" in STAGE_SKILLS["DBT"]


def test_system_derived_not_mappable():
    assert SYSTEM_DERIVED == {"SURROGATE_KEY", "RECORD_SOURCE", "AUDIT_TIMESTAMP"}


def test_refine_prompt_includes_profile_and_instruction():
    text = refine_prompt({
        "source_column": "end_date", "source_table": "EXTRACTION_DETAILS", "source_datatype": "VARCHAR",
        "target_column": "BIRTH_DATE", "target_datatype": "DATE",
        "current_transformation": "CAST(end_date AS DATE)",
        "business_definition": "date of birth",
        "profile": {"null_percentage": 2, "distinct_percentage": 40, "samples": ["2020-01-01", "n/a"],
                    "semantic_type": "DATE", "description": "extraction end"},
        "prior_rules": ["Transform BIRTH_DATE: TRY_TO_DATE({col}, 'YYYY-MM-DD')"],
    }, "treat n/a as null and parse YYYY-MM-DD")
    assert "end_date" in text and "BIRTH_DATE" in text
    assert "n/a" in text and "YYYY-MM-DD" in text


def test_transform_expression_feeds_soda_and_csv():
    expr = "CASE UPPER(TRIM(extraction_status)) WHEN 'Y' THEN 'ACTIVE' WHEN 'N' THEN 'INACTIVE' ELSE NULL END"
    assert case_literals(expr) == ["ACTIVE", "INACTIVE"]
    assert reusable_expression("TRY_TO_DATE(end_date, 'YYYY-MM-DD')", "end_date") == "TRY_TO_DATE({col}, 'YYYY-MM-DD')"
    checks = from_transform("DIM_CUSTOMER", {"target_column": "CUSTOMER_STATUS", "transformation": expr})
    assert any(c["check_type"] == "ACCEPTED_VALUES" for c in checks)
    dates = from_transform("DIM_CUSTOMER", {
        "target_column": "BIRTH_DATE", "transformation": "TRY_TO_DATE(end_date, 'YYYY-MM-DD')",
    })
    assert any(c["definition"].get("format") == "date iso 8601" for c in dates)
    csv_text = render_csv([{
        "target_column": "CUSTOMER_STATUS", "mapping_type": "TRANSFORM", "transformation": expr,
        "soda_checks": checks, "prompt": "map Y/N",
    }])
    assert "CUSTOMER_STATUS" in csv_text and "map Y/N" in csv_text
