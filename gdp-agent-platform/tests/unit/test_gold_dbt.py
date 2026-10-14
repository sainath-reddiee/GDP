"""Gold code generation: the dbt models render (Jinja) and parse as Snowflake SQL in both full and incremental
runs, resolve mastered keys through the full cross-reference, keep skeys stable, and the YAML and DDL are valid."""

import json

import jinja2
import pytest
import sqlglot
import yaml

from services.gold import dbt
from services.gold.propose import propose
from tests.unit.test_gold_propose import CONSUMES, HUB, MASTERED, ROLE


def _render(sql: str, incremental: bool) -> str:
    class This:
        database, schema = "DEV_GDP_GOLD_OPPORTUNITY_DB", "OPPORTUNITY"

        def __str__(self):
            return "DEV_GDP_GOLD_OPPORTUNITY_DB.OPPORTUNITY.THE_MODEL"

    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    return env.from_string(sql).render(
        config=lambda **kw: "", ref=lambda n: f"DEV.MODELS.{n.upper()}", source=lambda s, t: f"DEV.{s.upper()}.{t.upper()}",
        is_incremental=lambda: incremental, this=This())


@pytest.fixture(scope="module")
def generated():
    out = propose("OPPORTUNITY", [HUB, ROLE], consumes=CONSUMES, mastered=MASTERED)
    return dbt.generate(out["spec"], out["lines"], mastered=MASTERED, consumes=CONSUMES), out


def test_files_for_every_own_table_but_not_the_conformed_dimensions(generated):
    gen, out = generated
    files = gen["files"]
    for t in ("fact_opportunity", "fact_opportunity_company_role", "dim_opportunity_stage"):
        assert f"models/gold/opportunity/{t}.sql" in files and f"ddl/gold/opportunity/{t}.sql" in files
    assert not any("dim_company" in f or "dim_property" in f for f in files)
    assert "models/gold/opportunity/_opportunity__gold.yml" in files
    assert gen["report"]["conformed"] == ["DIM_COMPANY", "DIM_PROPERTY"]


@pytest.mark.parametrize("incremental", [False, True])
def test_models_render_and_parse_as_snowflake_sql(generated, incremental):
    gen, _ = generated
    for path, text in gen["files"].items():
        if path.startswith("models/") and path.endswith(".sql"):
            sql = _render(text, incremental)
            sqlglot.parse_one(sql, read="snowflake")          # raises on a syntax error


def test_fact_resolves_mastered_keys_and_keeps_skeys_stable(generated):
    gen, _ = generated
    sql = gen["files"]["models/gold/opportunity/fact_opportunity.sql"]
    assert "unique_key=['source_unique_id', 'dim_data_source_skey']" in sql and "full_refresh=false" in sql
    assert "{{ source('company_mdm_source', 'company_cross_reference') }}" in sql
    assert "{{ source('property_mdm_source', 'property_cross_reference') }}" in sql
    assert "{{ ref('dim_company') }}" in sql and "{{ ref('dim_property') }}" in sql
    assert "nvl(dim_company_r.dim_company_skey, -1) as dim_company_skey" in sql
    assert "nvl(landlord_dim_company_r.dim_company_skey, -1) as landlord_dim_company_skey" in sql
    assert "on src.landlord_company_core_skey = landlord_dim_company_r.company_core_skey" in sql
    assert sql.count("dim_company_resolution as (") == 1      # one resolution per mastered domain, joined per role
    inc = _render(sql, True)
    assert "COALESCE(t.fact_opportunity_skey, DEV_GDP_GOLD_OPPORTUNITY_DB.OPPORTUNITY.seq_fact_opportunity_skey.nextval)".lower() in inc.lower()
    assert "t.fact_opportunity_hkey <> s.fact_opportunity_hkey" in inc
    full = _render(sql, False)
    assert "left join" in full.lower() and "{{ this }}" not in full and "t.fact_opportunity_skey is null" not in full


def test_hkey_covers_business_columns_not_keys_or_audit(generated):
    gen, _ = generated
    sql = gen["files"]["models/gold/opportunity/fact_opportunity.sql"]
    hk = sql[sql.index("sha2("): sql.index(", 256)")]
    assert "deal_amount" in hk and "dim_company_skey" in hk
    assert "fact_opportunity_skey" not in hk and "gdp_inserted_ts" not in hk and "gdp_is_active" not in hk


def test_ddl_follows_the_gold_template(generated):
    gen, _ = generated
    ddl = gen["files"]["ddl/gold/opportunity/fact_opportunity.sql"]
    assert "CREATE SEQUENCE IF NOT EXISTS DEV_GDP_GOLD_OPPORTUNITY_DB.OPPORTUNITY.SEQ_FACT_OPPORTUNITY_SKEY" in ddl
    assert "CREATE ICEBERG TABLE IF NOT EXISTS DEV_GDP_GOLD_OPPORTUNITY_DB.OPPORTUNITY.FACT_OPPORTUNITY" in ddl
    assert "CONSTRAINT PK_FACT_OPPORTUNITY PRIMARY KEY (FACT_OPPORTUNITY_SKEY)" in ddl
    assert "CONSTRAINT UK_FACT_OPPORTUNITY UNIQUE (SOURCE_UNIQUE_ID, DIM_DATA_SOURCE_SKEY)" in ddl
    assert "FOREIGN KEY (DIM_COMPANY_SKEY) REFERENCES DEV_GDP_GOLD_COMPANY_DB.COMPANY.DIM_COMPANY (DIM_COMPANY_SKEY)" in ddl
    assert "EXTERNAL_VOLUME = 'GDP_SF_DEV_GOLD'" in ddl and "BASE_LOCATION = 'gdp-sf-dev-gold/opportunity/fact_opportunity'" in ddl
    body = ddl[ddl.index("(\n"):]
    assert body.index("FACT_OPPORTUNITY_HKEY") < body.index("GDP_IS_ACTIVE") < body.index("GDP_UPDATED_BY")
    assert "IS_WON" in ddl and "BOOLEAN" in ddl and "DEFAULT FALSE" in ddl


def test_schema_yml_tests_keys_and_relationships_ignoring_unresolved(generated):
    gen, _ = generated
    doc = yaml.safe_load(gen["files"]["models/gold/opportunity/_opportunity__gold.yml"])
    fact = next(m for m in doc["models"] if m["name"] == "fact_opportunity")
    cols = {c["name"]: c for c in fact["columns"]}
    assert cols["fact_opportunity_skey"]["tests"] == ["unique", "not_null"]
    rel = next(t["relationships"] for t in cols["dim_company_skey"]["tests"] if isinstance(t, dict))
    assert rel["to"] == "ref('dim_company')" and rel["field"] == "dim_company_skey" and rel["config"]["where"] == "dim_company_skey <> -1"


def test_report_names_every_assumption(generated):
    gen, _ = generated
    report = json.loads(gen["files"]["release/gold-generation-report.json"])
    joined = " ".join(report["assumptions"])
    assert "DIM_DATA_SOURCE_SKEY" in joined and "history hook" in joined and "dbt_project.yml" in joined
    assert "parent" not in joined                               # the bridge's parent key is resolved, nothing to flag
    assert report["conventions"].startswith("none found")


def test_known_conventions_are_used():
    out = propose("OPPORTUNITY", [HUB], consumes=CONSUMES, mastered=MASTERED)
    conv = {"history_hook": "{{ m_insert_history_gold(this) }}",
            "data_source": {"relation": "{{ ref('dim_data_source') }}", "skey": "DIM_DATA_SOURCE_SKEY", "name": "DATA_SOURCE_NAME"}}
    gen = dbt.generate(out["spec"], out["lines"], mastered=MASTERED, consumes=CONSUMES, conventions=conv)
    sql = gen["files"]["models/gold/opportunity/fact_opportunity.sql"]
    assert "post_hook=[\"{{ m_insert_history_gold(this) }}\"]" in sql
    assert not any("history hook" in a or "DIM_DATA_SOURCE_SKEY" in a for a in gen["report"]["assumptions"])


def test_lineage_edges_from_silver_and_mdm_to_gold(generated):
    gen, _ = generated
    edges = gen["lineage"]
    into = [e for e in edges if e["to_column"] == "DIM_COMPANY_SKEY" and e["to_object"].endswith(".FACT_OPPORTUNITY")]
    hub = next(e for e in into if e["from_layer"] == "SILVER_PRE_MDM")
    assert hub["kind"] == "RESOLVE" and hub["from_column"] == "COMPANY_CORE_SKEY"
    assert hub["from_object"] == "DEV_GDP_SILVER_DB.OPPORTUNITY.OPPORTUNITY_CORE"
    dim = next(e for e in into if e["from_layer"] == "GOLD")
    assert dim["from_object"] == "DEV_GDP_GOLD_COMPANY_DB.COMPANY.DIM_COMPANY" and dim["from_column"] == "DIM_COMPANY_SKEY"
    assert any(e["to_column"] == "DEAL_AMOUNT" and e["kind"] == "BUILD" for e in edges)


def test_bridge_reaches_its_parent_fact_through_the_parent_merge_key(generated):
    gen, _ = generated
    sql = gen["files"]["models/gold/opportunity/fact_opportunity_company_role.sql"]
    assert "from {{ ref('opportunity_core') }} p" in sql
    assert "join {{ ref('fact_opportunity') }} f on f.source_unique_id = p.source_unique_id and f.dim_data_source_skey = pds.dim_data_source_skey" in sql
    assert "nvl(parent_fact_opportunity.fact_opportunity_skey, -1) as fact_opportunity_skey" in sql
    assert "nvl(dim_company_r.dim_company_skey, -1) as dim_company_skey" in sql


def test_generation_follows_the_approved_model_not_the_proposal():
    import copy

    out = propose("OPPORTUNITY", [HUB], consumes=CONSUMES, mastered=MASTERED)
    spec = copy.deepcopy(out["spec"])
    fact = spec["design"]["entities"][0]
    fact["attributes"] = [a for a in fact["attributes"] if a["name"] != "DESCRIPTION"]          # removed in review
    fact["attributes"].insert(3, {"name": "REGION_NOTE", "datatype": "STRING", "nullable": True, "is_pk": False})   # added in review
    gen = dbt.generate(spec, out["lines"], mastered=MASTERED, consumes=CONSUMES)
    sql = gen["files"]["models/gold/opportunity/fact_opportunity.sql"]
    assert "src.description" not in sql and "null::STRING as region_note" in sql
    assert any("REGION_NOTE: added in review" in a for a in gen["report"]["assumptions"])
    sqlglot.parse_one(_render(sql, True), read="snowflake")


def test_conventions_learned_from_indexed_code():
    from services.gold.conventions import learn

    chunks = [
        {"path": "models/silver/company/company_core.sql", "text": "{{ m_insert_history_gold(this) }}"},          # not gold: ignored
        {"path": "models/gold/company/dim_company.sql", "kind": "model", "name": "dim_company",
         "text": "{{ config(post_hook=[\"{{ m_insert_history_gold(this) }}\"]) }} select ..."},
        {"path": "models/gold/shared/dim_data_source.sql", "kind": "model", "name": "dim_data_source",
         "columns": ["DIM_DATA_SOURCE_SKEY", "DATA_SOURCE_NAME"], "text": "select ..."},
    ]
    conv, notes = learn(chunks)
    assert conv["history_hook"] == "{{ m_insert_history_gold(this) }}" and conv["history_hook_from"].endswith("dim_company.sql")
    assert conv["data_source"]["name"] == "DATA_SOURCE_NAME" and not notes
    conv, notes = learn([{"path": "models/gold/x/dim_company.sql", "text": "{{ m_insert_history_gold(this, 'dim_company_hist') }}"}])
    assert "history_hook" not in conv and "model-specific" in notes[0]
    assert learn([]) == ({}, [])
