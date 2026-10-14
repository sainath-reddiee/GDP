"""Reference inputs: what a file is, and a person's STTM sheet read as mapping lines."""

import io
import zipfile

from services.reference import detect, sttm_import


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, t in files.items():
            z.writestr(n, t)
    return buf.getvalue()


def test_detects_each_kind():
    k = lambda name, text: detect.detect(name, text.encode() if isinstance(text, str) else text)["kind"]   # noqa: E731
    assert k("model.sql", "CREATE TABLE FACT_X (A NUMBER);") == "DATA_MODEL"
    assert k("mdm.sql", "CREATE TABLE COMPANY_CROSS_REFERENCE (SOURCE_UNIQUE_ID STRING, CBRE_ENTERPRISE_COMPANY_ID STRING);") == "MDM_SCHEMA"
    assert k("fact.sql", "select * from {{ ref('opportunity_core') }}") == "DBT_CODE"
    assert k("project.zip", _zip({"proj/dbt_project.yml": "name: x", "proj/models/a.sql": "select 1"})) == "DBT_CODE"
    assert k("sttm.csv", "Target Table,Target Column,Source Table,Source Column,Transformation\nF,A,S,B,UPPER(B)\n") == "STTM"
    assert k("dictionary.csv", "Table Name,Column Name,Data Type,Nullable\nDIM_X,ID,NUMBER,N\n") == "DATA_MODEL"
    assert k("rows.csv", "OPPORTUNITY_ID,STAGE,AMOUNT\n1,Won,10\n") == "SAMPLE_ROWS"
    assert k("brief.pdf", b"%PDF-1.7") == "DOCUMENT"
    assert k("erwin.xml", "<?xml version='1.0'?><ERwin><Entity name='X'/></ERwin>") == "DATA_MODEL"
    assert k("spec.json", '{"design": {"entities": []}}') == "DATA_MODEL"
    assert k("notes.txt", "just some notes") == "DOCUMENT"


def test_sttm_sheet_as_mapping_lines():
    rows = [["Mapping for the opportunity fact"],
            ["Target Table", "Target Column", "Source Table", "Source Column", "Transformation Logic", "Description"],
            ["FACT_OPPORTUNITY", "OPPORTUNITY_NAME", "OPPORTUNITY_CORE", "OPPORTUNITY_NAME", "", "Name"],
            ["FACT_OPPORTUNITY", "DEAL_AMOUNT_USD", "OPPORTUNITY_CORE", "DEAL_AMOUNT", "DEAL_AMOUNT * FX_RATE", ""],
            ["FACT_OPPORTUNITY", "DIM_COMPANY_SKEY", "", "", "", "resolved through the cross-reference"],
            ["", "", "", "", "", ""]]
    out = sttm_import.parse([("Sheet1", rows)])
    by = sttm_import.for_target(out["lines"], "DEV.GOLD.FACT_OPPORTUNITY")
    assert out["targets"] == ["FACT_OPPORTUNITY"] and len(out["lines"]) == 3
    assert by["OPPORTUNITY_NAME"]["mapping_type"] == "DIRECT" and by["OPPORTUNITY_NAME"]["source_column"] == "OPPORTUNITY_NAME"
    assert by["DEAL_AMOUNT_USD"]["mapping_type"] == "TRANSFORM" and by["DEAL_AMOUNT_USD"]["transformation"] == "DEAL_AMOUNT * FX_RATE"
    assert by["DIM_COMPANY_SKEY"]["mapping_type"] == "UNMAPPED"
    # a sheet without a target table column takes the sheet's name
    out = sttm_import.parse([("FACT_X", [["Target Column", "Source Column"], ["A", "B"]])])
    assert out["lines"][0]["target_table"] == "FACT_X"
    assert sttm_import.parse([("S", [["a", "b"]])])["notes"]


def test_code_conventions_from_a_dbt_project():
    from services.reference import code_conventions

    fact = """{{ config(materialized='incremental', incremental_strategy='merge', post_hook=["{{ m_insert_history_gold(this) }}"]) }}
select coalesce(t.fact_a_skey, {{ this.database }}.{{ this.schema }}.seq_fact_a_skey.nextval) as fact_a_skey,
       sha2(concat_ws('||', a, b), 256) as fact_a_hkey
from {{ ref('opportunity_core') }} s left join {{ ref('dim_data_source') }} d on d.x = s.x
{{ gdp_audit_columns() }}"""
    files = {"proj/dbt_project.yml": "name: gdp", "proj/models/gold/opportunity/fact_a.sql": fact,
             "proj/models/gold/opportunity/dim_b.sql": "{{ config(materialized='table') }} select 1",
             "proj/macros/m_insert_history_gold.sql": "{% macro m_insert_history_gold(x) %}{% endmacro %}"}
    out = code_conventions.learn(files)
    assert out["models"] == 2 and out["layer_folders"][0] == {"value": "gold", "count": 2}
    assert {p["value"] for p in out["name_prefixes"]} == {"fact_", "dim_"}
    assert out["history_hook"][0]["value"] == "m_insert_history_gold"
    assert out["surrogate_keys"][0]["value"] == "sequence" and out["hash_functions"][0]["value"] == "sha2"
    assert out["data_source"] and any(m["value"] == "gdp_audit_columns" for m in out["macros"])
    assert out["macro_files"] == 1


def test_sttm_transformation_becomes_an_expression_only_when_it_is_one():
    assert sttm_import.as_expression("UPPER(TRIM(STAGE))", "STAGE") == "UPPER(TRIM(STAGE))"
    assert sttm_import.as_expression("CASE WHEN X = 'Y' THEN 1 ELSE 0 END", "X") == "CASE WHEN X = 'Y' THEN 1 ELSE 0 END"
    assert sttm_import.as_expression("FIRST_NAME || ' ' || LAST_NAME", "FIRST_NAME") == "FIRST_NAME || ' ' || LAST_NAME"
    assert sttm_import.as_expression("'N/A'", None) == "'N/A'"
    for not_sql in ("STAGE", "Direct", "take the latest value for the account", "lookup in the CRM; see note #4", ""):
        assert sttm_import.as_expression(not_sql, "STAGE") is None, not_sql


def test_reference_sttm_rules_for_a_run_and_mapping_promotes_them():
    from services.mapping.procedures import promote

    lines = sttm_import.parse([("FACT_OPPORTUNITY", [
        ["Target Table", "Target Column", "Source Table", "Source Column", "Transformation"],
        ["FACT_OPPORTUNITY", "OPPORTUNITY_STAGE", "OPPORTUNITY_CORE", "STAGE", "UPPER(TRIM(STAGE))"],
        ["FACT_OPPORTUNITY", "DEAL_VALUE", "OPPORTUNITY_CORE", "DEAL_AMOUNT", "Direct"],
        ["FACT_OPPORTUNITY", "OWNER", "ACCOUNT", "OWNER_NAME", ""],          # another source table
        ["FACT_OPPORTUNITY", "NEXT_STEP", "", "", ""],                       # unmapped
        ["DIM_OTHER", "X", "OPPORTUNITY_CORE", "CLOSE_DATE", ""],             # another target
    ])], None)["lines"]
    sources = [{"column_name": n, "table_name": "DEV_GDP_SILVER_DB.OPPORTUNITY.OPPORTUNITY_CORE"}
               for n in ("STAGE", "DEAL_AMOUNT", "OWNER_NAME", "CLOSE_DATE")]
    rules = sttm_import.rules_for(lines, "DEV_GDP_GOLD_OPPORTUNITY_DB.OPPORTUNITY.FACT_OPPORTUNITY", sources, "your STTM (q3.xlsx)")
    assert set(rules) == {"STAGE", "DEAL_AMOUNT"}
    assert rules["STAGE"]["target_column"] == "OPPORTUNITY_STAGE" and rules["STAGE"]["transformation"] == "UPPER(TRIM(STAGE))"
    assert rules["DEAL_AMOUNT"]["target_column"] == "DEAL_VALUE" and rules["DEAL_AMOUNT"]["transformation"] is None

    def cand(name, score):
        return {"target": {"column_name": name}, "final_score": score, "evidence": {}, "transformation": None}
    cands = [cand("STAGE_NAME", 0.9), cand("OPPORTUNITY_STAGE", 0.4), cand("DEAL_VALUE", 0.2)]
    out = promote(cands, sorted(cands, key=lambda c: -c["final_score"])[:2], rules["STAGE"], 3)
    assert out[0]["target"]["column_name"] == "OPPORTUNITY_STAGE" and out[0]["final_score"] >= 0.97
    assert out[0]["transformation"] == "UPPER(TRIM(STAGE))" and out[0]["evidence"]["reference_sttm"]["reference"] == "your STTM (q3.xlsx)"
    assert "gold_rule" not in out[0]["evidence"] and out[0]["recommendation"] == "AUTO_SUGGEST"
    unsafe = dict(rules["STAGE"], transformation="DROP TABLE X")
    again = promote([cand("OPPORTUNITY_STAGE", 0.4)], [], unsafe, 3)
    assert again[0]["transformation"] is None                   # an unsafe expression is never carried
