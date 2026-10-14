"""MDM contracts: Company and Property resolve exactly as the gdp-post-mdm-data-model skill's canonical SQL says."""

import json
import re
from pathlib import Path

import pytest

from infrastructure.seed_knowledge import load_domain_packs, with_mdm
from services.knowledge.packs import pack_rows
from services.mdm import contract as mdm

ROOT = Path(__file__).resolve().parents[2]
COMPANY = json.loads((ROOT / "domain/company/mdm.json").read_text(encoding="utf-8"))
PROPERTY = json.loads((ROOT / "domain/property/mdm.json").read_text(encoding="utf-8"))


def flat(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.lower()).strip()


def test_company_resolution_follows_canonical_pattern_a():
    sql = flat(mdm.resolution_sql(COMPANY, "PROD"))
    for clause in (
        "from prod_gdp_gold_company_db.company.dim_company where gdp_is_active",
        "qualify row_number() over (partition by cbre_enterprise_company_id order by dim_company_skey) = 1",
        "from prod_gdp_silver_db.company_mdm.company_cross_reference",
        "where gdp_is_active and source_system_name not in ('reltio', 'reltiogrouping')",
        "partition by source_unique_id, source_system_name order by gdp_updated_ts desc nulls last, cbre_enterprise_company_id) = 1",
        "from prod_gdp_silver_db.company.company_core p join prod_gdp_silver_db.shared.ref_gdp_source_system r",
        "on p.ref_gdp_source_system_skey = r.gdp_source_system_skey",
        "left join xref x on p.source_unique_id = x.source_unique_id and upper(r.gdp_source_system_name) = x.ssn",
        "nvl(d.dim_company_skey, -1) dim_company_skey",
        "where p.gdp_is_active",
        "partition by p.company_core_skey order by d.dim_company_skey nulls last) = 1",
    ):
        assert clause in sql, clause
    assert "cross_reference_entity_id" not in sql     # never the STAGE delta


def test_property_resolution_keys_on_source_system_id_and_delete_date():
    sql = flat(mdm.resolution_sql(PROPERTY))
    for clause in (
        "from dev_gdp_silver_db.property_mdm.property_cross_reference where gdp_is_active and mdm_delete_date is null",
        "partition by source_system_id, source_system_name order by mdm_post_mdm_lud desc nulls last, mdm_updated_ts desc nulls last) = 1",
        "left join xref x on p.source_unique_id = x.source_system_id and upper(r.gdp_source_system_name) = x.ssn",
        "from dev_gdp_gold_property_db.property.dim_property where gdp_is_active",
        "nvl(d.dim_property_skey, -1) dim_property_skey",
    ):
        assert clause in sql, clause
    assert "not in (" not in sql                       # property has no Reltio-internal exclusion in the canonical SQL


def test_coverage_sql_counts_matched_per_source():
    sql = flat(mdm.coverage_sql(COMPANY))
    assert "count(x.cbre_enterprise_company_id) matched" in sql and "group by 1 order by 2 desc" in sql
    assert "upper(r.gdp_source_system_name) = x.ssn" in sql


def test_dbt_resolution_uses_refs_and_the_full_xref_source():
    r = mdm.dbt_resolution(COMPANY, input_alias="h", input_skey="COMPANY_CORE_SKEY")
    ctes = flat(r["ctes"])
    assert "{{ ref('dim_company') }}" in ctes and "{{ ref('company_core') }}" in ctes
    assert "{{ source('company_mdm_source', 'company_cross_reference') }}" in ctes
    assert "{{ source('silver_shared', 'ref_gdp_source_system') }}" in ctes
    assert r["join"] == "left join dim_company_resolution dim_company_r on h.company_core_skey = dim_company_r.company_core_skey"
    assert r["column"] == "nvl(dim_company_r.dim_company_skey, -1) as dim_company_skey"
    p = mdm.dbt_resolution(PROPERTY, input_alias="f", input_skey="PROPERTY_CORE_SKEY", out_column="DIM_PROPERTY_SKEY")
    assert "c.source_unique_id = x.source_system_id" in flat(p["ctes"])


def test_validate():
    assert mdm.validate("COMPANY", COMPANY) == []
    assert mdm.validate("PROPERTY", PROPERTY) == []
    assert mdm.validate("X", None) == []
    assert "role must be" in mdm.validate("X", {"role": "OTHER"})[0]
    broken = {**COMPANY, "cross_reference": {**COMPANY["cross_reference"], "source_key": "", "table": "bad name"}}
    problems = mdm.validate("COMPANY", broken)
    assert any("cross_reference.source_key is missing" in p for p in problems)
    assert any("not an identifier" in p for p in problems)
    assert mdm.validate("OPP", {"role": "CONSUMER", "consumes": [{"domain": "COMPANY", "silver_column": "COMPANY_CORE_SKEY",
                                                                    "gold_column": "DIM_COMPANY_SKEY"}]}, ["COMPANY"]) == []
    assert "not a mastered domain" in mdm.validate("OPP", {"role": "CONSUMER", "consumes": [
        {"domain": "LEASE", "silver_column": "A", "gold_column": "B"}]}, ["COMPANY"])[0]
    assert "lists the mastered domains" in mdm.validate("OPP", {"role": "CONSUMER"})[0]


def test_chain_is_pre_mdm_to_gold():
    hops = mdm.chain("COMPANY", COMPANY)
    assert [h["layer"] for h in hops] == ["SILVER_PRE_MDM", "MDM", "SILVER_POST_MDM", "GOLD"]
    assert hops[1]["object"] == "COMPANY_MDM.COMPANY_CROSS_REFERENCE" and hops[-1]["object"] == "COMPANY.DIM_COMPANY"


def test_packs_carry_the_contracts_and_their_knowledge():
    packs = {p["domain"]["name"]: p for p in load_domain_packs()}
    assert packs["COMPANY"]["domain"]["mdm"]["role"] == "MASTERED"
    assert packs["PROPERTY"]["domain"]["mdm"]["cross_reference"]["source_key"] == "SOURCE_SYSTEM_ID"
    assert packs["OPPORTUNITY"]["domain"]["mdm"]["consumes"][0]["domain"] == "COMPANY"
    assert "knowledge" not in packs["COMPANY"]["domain"]["mdm"]
    keys = [k["key"] for k in packs["COMPANY"]["knowledge"]]
    assert "company.mdm.rule.full_xref" in keys and len(keys) == len(set(keys))
    rows = pack_rows(packs["COMPANY"], "DEV_AI_PLATFORM")
    assert rows["domains"][0][6]["mdm"]["gold"]["dimension"] == "DIM_COMPANY"   # stored in the domain's CONFIG


def test_with_mdm_does_not_duplicate_knowledge():
    pack = {"domain": {"name": "X"}, "targets": [], "knowledge": [{"key": "a", "type": "EXCEPTION", "title": "t", "content": "c"}]}
    out = with_mdm(pack, {"role": "CONSUMER", "consumes": [], "knowledge": [{"key": "a"}, {"key": "b", "type": "EXCEPTION", "title": "t", "content": "c"}]})
    assert [k["key"] for k in out["knowledge"]] == ["a", "b"] and pack["knowledge"] == [pack["knowledge"][0]]
    assert out["domain"]["mdm"] == {"role": "CONSUMER", "consumes": []}


@pytest.mark.parametrize("contract", [COMPANY, PROPERTY])
def test_knowledge_items_are_valid_exceptions(contract):
    from services.knowledge.validate import KNOWLEDGE_TYPES

    for k in contract["knowledge"]:
        assert k["type"] in KNOWLEDGE_TYPES and k["content_json"]["target_table"]


def test_mdm_domains_load_the_post_mdm_skill_while_modeling_and_generating():
    from services.knowledge.skills import MDM_SKILL, mdm_rows, with_domain

    rows = mdm_rows("CONSUMER")
    assert {r["stage"] for r in rows} == {"MAPPING", "STTM", "DBT"}
    assert with_domain(["AI-SCHEMA-MAPPING"], rows, "MAPPING") == ["AI-SCHEMA-MAPPING", MDM_SKILL]
    assert with_domain(["SODA_SKILL"], rows, "SODA") == ["SODA_SKILL"]
    assert mdm_rows(None) == [] and mdm_rows("MASTERED", "GENERIC") == [] and len(mdm_rows("mastered")) == 3
