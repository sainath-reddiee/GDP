"""A mastered domain's own Gold run: the conformed dimension (DIM_COMPANY) from the post-MDM golden record, child
dimensions from related post-MDM tables, the same table runs, rules and gold dbt as a consumer's facts."""

import json
from pathlib import Path

from services.gold import dbt as gold_dbt
from services.gold import rules, star
from services.gold.propose import propose_mastered
from services.gold.run_codegen import gold_lines
from services.modeling import spec_v2

ROOT = Path(__file__).resolve().parents[2]
COMPANY = {k: v for k, v in json.loads((ROOT / "domain/company/mdm.json").read_text(encoding="utf-8")).items() if k != "knowledge"}


def col(name, data_type="VARCHAR", distinct=None):
    return {"name": name, "data_type": data_type, "nullable": True, "distinct_count": distinct}


GOLDEN = {"database": "DEV_GDP_SILVER_DB", "schema": "COMPANY_MDM", "table": "COMPANY_CORE", "columns": [
    col("CBRE_ENTERPRISE_COMPANY_ID"), col("MDM_ENTITY_ID"), col("COMPANY_NAME"), col("LEGAL_NAME"), col("EMPLOYEE_COUNT", "NUMBER(38,0)"),
    col("MDM_POST_MDM_LUD", "TIMESTAMP_NTZ"), col("MDM_UPDATED_TS", "TIMESTAMP_NTZ"), col("GDP_IS_ACTIVE", "BOOLEAN"),
    col("GDP_INSERTED_TS", "TIMESTAMP_NTZ")]}
INDUSTRY = {"database": "DEV_GDP_SILVER_DB", "schema": "COMPANY_MDM", "table": "COMPANY_INDUSTRY", "columns": [
    col("CBRE_ENTERPRISE_COMPANY_ID"), col("INDUSTRY_CODE"), col("INDUSTRY_NAME"), col("GDP_IS_ACTIVE", "BOOLEAN")]}
XREF = {"database": "DEV_GDP_SILVER_DB", "schema": "COMPANY_MDM", "table": "COMPANY_CROSS_REFERENCE", "columns": [
    col("SOURCE_UNIQUE_ID"), col("CBRE_ENTERPRISE_COMPANY_ID")]}
MERGE = {"database": "DEV_GDP_SILVER_DB", "schema": "COMPANY_MDM", "table": "COMPANY_MERGE", "columns": [col("WINNER_ID")]}


def _ent(spec, name):
    return next(e for e in spec["design"]["entities"] if e["entity_name"] == name)


def test_the_conformed_dimension_and_its_children_from_the_golden_record():
    out = propose_mastered("company", [GOLDEN, INDUSTRY, XREF, MERGE], COMPANY)
    spec = out["spec"]
    assert [e["entity_name"] for e in spec["design"]["entities"]] == ["DIM_COMPANY", "DIM_COMPANY_INDUSTRY"]
    dim = _ent(spec, "DIM_COMPANY")
    cols = [a["name"] for a in dim["attributes"]]
    assert cols[0] == "DIM_COMPANY_SKEY" and dim["attributes"][0]["is_pk"] and dim["business_keys"] == ["CBRE_ENTERPRISE_COMPANY_ID"]
    assert {"CBRE_ENTERPRISE_COMPANY_ID", "COMPANY_NAME", "LEGAL_NAME", "EMPLOYEE_COUNT"} <= set(cols)
    for dropped in ("MDM_ENTITY_ID", "MDM_POST_MDM_LUD", "MDM_UPDATED_TS"):
        assert dropped not in cols, dropped
    assert cols.count("GDP_INSERTED_TS") == 1 and cols.count("GDP_IS_ACTIVE") == 1   # gold's own audit, once
    assert cols[-6:] == ["DIM_COMPANY_HKEY", "GDP_IS_ACTIVE", "GDP_INSERTED_TS", "GDP_INSERTED_BY", "GDP_UPDATED_TS", "GDP_UPDATED_BY"]
    assert dim["gold_source"] == {"mastered": True, "relation": "{{ source('company_mdm_source', 'company_core') }}",
                                  "dedupe": {"partition": ["CBRE_ENTERPRISE_COMPANY_ID"],
                                             "order": ["MDM_POST_MDM_LUD DESC NULLS LAST", "MDM_UPDATED_TS DESC NULLS LAST"]},
                                  "active": "GDP_IS_ACTIVE"}
    child = _ent(spec, "DIM_COMPANY_INDUSTRY")
    assert child["business_keys"] == ["CBRE_ENTERPRISE_COMPANY_ID", "INDUSTRY_CODE"]
    assert {"from_entity": "DIM_COMPANY_INDUSTRY", "from_column": "DIM_COMPANY_SKEY", "to_entity": "DIM_COMPANY"}.items() <= spec["relationships"][0].items()
    assert spec["physical_defaults"]["database"] == "DEV_GDP_GOLD_COMPANY_DB"
    assert any("COMPANY_CROSS_REFERENCE is the cross-reference" in n for n in out["notes"])
    assert any("COMPANY_MERGE does not carry" in n for n in out["notes"])
    # the model store keeps how the dimension reads its post-MDM table
    assert spec_v2.normalize_v2(spec, "m")["design"]["entities"][0]["gold_source"]["mastered"] is True


def test_without_the_golden_record_it_asks_for_it():
    out = propose_mastered("COMPANY", [INDUSTRY], COMPANY)
    assert any("Select the golden record (COMPANY_CORE)" in n for n in out["notes"])


def test_mastered_dimensions_are_built_by_table_runs_dimension_first():
    out = propose_mastered("COMPANY", [GOLDEN, INDUSTRY], COMPANY)
    layout = star.plan(out["spec"], out["lines"], [GOLDEN, INDUSTRY])
    assert [t["entity"] for t in layout["tables"]] == ["DIM_COMPANY", "DIM_COMPANY_INDUSTRY"]
    assert layout["tables"][0]["source_table"] == "DEV_GDP_SILVER_DB.COMPANY_MDM.COMPANY_CORE"


def test_child_key_rule_and_generated_dbt():
    spec = spec_v2.normalize_v2(propose_mastered("COMPANY", [GOLDEN, INDUSTRY], COMPANY)["spec"], "m")
    child = _ent(spec, "DIM_COMPANY_INDUSTRY")
    by_rule = rules.rules(child, ["CBRE_ENTERPRISE_COMPANY_ID", "INDUSTRY_CODE", "INDUSTRY_NAME"], [], {"COMPANY": COMPANY},
                          spec["relationships"], spec["design"]["entities"])
    assert by_rule["DIM_COMPANY_SKEY"]["source_column"] == "CBRE_ENTERPRISE_COMPANY_ID"
    assert by_rule["DIM_COMPANY_SKEY"]["kind"] == "DIMENSION_KEY"

    src = {"source_database": "DEV_GDP_SILVER_DB", "source_schema": "COMPANY_MDM"}
    sttm = [dict(src, source_table="COMPANY_INDUSTRY", target_column=c, source_column=s, mapping_type="DIRECT")
            for c, s in (("DIM_COMPANY_SKEY", "CBRE_ENTERPRISE_COMPANY_ID"), ("CBRE_ENTERPRISE_COMPANY_ID", "CBRE_ENTERPRISE_COMPANY_ID"),
                         ("INDUSTRY_CODE", "INDUSTRY_CODE"), ("INDUSTRY_NAME", "INDUSTRY_NAME"))]
    lines, _ = gold_lines(child, sttm, spec, by_rule, None)
    assert {l["gold_table"] for l in lines} == {"DIM_COMPANY_INDUSTRY"}       # the parent is built by its own run
    out = gold_dbt.generate(spec, lines, mastered={"COMPANY": COMPANY}, consumes=[], only=["DIM_COMPANY_INDUSTRY"])
    sql = out["files"]["models/gold/company/dim_company_industry.sql"]
    assert "from {{ source('company_mdm_source', 'company_industry') }}" in sql
    assert "left join {{ ref('dim_company') }} dim_company on dim_company.cbre_enterprise_company_id = src.cbre_enterprise_company_id" in sql
    assert "unique_key=['cbre_enterprise_company_id', 'industry_code']" in sql and "src.gdp_is_active as gdp_is_active" in sql

    dim = _ent(spec, "DIM_COMPANY")
    dlines, _ = gold_lines(dim, [dict(src, source_table="COMPANY_CORE", target_column=c, source_column=c, mapping_type="DIRECT")
                                 for c in ("CBRE_ENTERPRISE_COMPANY_ID", "COMPANY_NAME", "LEGAL_NAME", "EMPLOYEE_COUNT")], spec, {}, None)
    dsql = gold_dbt.generate(spec, dlines, mastered={"COMPANY": COMPANY}, consumes=[], only=["DIM_COMPANY"])["files"]["models/gold/company/dim_company.sql"]
    assert "from {{ source('company_mdm_source', 'company_core') }}" in dsql
    assert ("qualify row_number() over (partition by cbre_enterprise_company_id order by mdm_post_mdm_lud desc nulls last, "
            "mdm_updated_ts desc nulls last) = 1") in dsql
    assert "unique_key=['cbre_enterprise_company_id']" in dsql and "seq_dim_company_skey" in dsql
