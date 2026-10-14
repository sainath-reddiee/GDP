"""A domain's own dbt project as a reference: what code generation takes from it (key strategy, staging
materialization, the gold history hook and data-source dimension), how it overlays the conventions, and the code score."""

import json

from services.common.standard import conventions_for
from services.gold.conventions import learn as learn_gold
from services.reference import code_conventions, score

HUB = """{{ config(materialized='incremental') }}
select coalesce(t.x_skey, seq_x_skey.nextval) as x_skey from {{ ref('stg_x') }}"""
FACT = """{{ config(materialized='incremental', post_hook=["{{ m_insert_history_gold(this) }}"]) }}
select coalesce(t.fact_a_skey, seq_fact_a_skey.nextval) as fact_a_skey from {{ ref('dim_data_source') }}"""
DIM_SOURCE = "select dim_data_source_skey, data_source_name from {{ source('util', 'data_source') }}"
PROJECT = {"p/dbt_project.yml": "name: gdp",
           "p/models/silver/opp/stg_a.sql": "{{ config(materialized='view') }} select 1",
           "p/models/silver/opp/stg_b.sql": "{{ config(materialized='view') }} select 1",
           "p/models/silver/opp/opportunity_core.sql": HUB,
           "p/models/gold/opp/fact_a.sql": FACT,
           "p/models/gold/shared/dim_data_source.sql": DIM_SOURCE}


def test_overrides_only_what_the_evidence_decides():
    learned = code_conventions.learn(PROJECT)
    assert code_conventions.to_overrides(learned, "SILVER") == {"key_strategy": "sequence", "staging_materialization": "view"}
    assert code_conventions.to_overrides(learned, "GOLD") == {"key_strategy": "sequence"}
    assert code_conventions.to_overrides({"surrogate_keys": [{"value": "sequence", "count": 1}]}) == {}   # one model is not a convention
    assert code_conventions.to_overrides({"hash_functions": [{"value": "md5", "count": 3}]}) == {"key_strategy": "hash"}


def test_gold_conventions_are_learned_from_an_uploaded_project():
    conventions, notes = learn_gold(code_conventions.as_chunks(PROJECT))
    assert conventions["history_hook"] == "{{ m_insert_history_gold(this) }}"
    assert conventions["data_source"]["relation"] == "{{ ref('dim_data_source') }}" and conventions["data_source"]["name"] == "DATA_SOURCE_NAME"
    assert notes == []


def _query(parsed, own=None):
    def query(sql, params):
        if "REFERENCE_INPUT" in sql:
            return [{"REFERENCE_ID": "r1", "TITLE": "gdp-dbt.zip", "PARSED": json.dumps(parsed)}] if parsed else []
        if "DOMAIN_REGISTRY" in sql:
            return [{"V": json.dumps(own)}] if own else []
        return []
    return query


def test_the_project_overlays_the_conventions_and_the_domain_still_wins():
    parsed = {"conventions": {"hash_functions": [{"value": "sha2", "count": 4}], "materializations": [{"value": "view", "count": 3}]}}
    conv = conventions_for(_query(parsed), "GDP", "SILVER", "d1")
    assert conv["key_strategy"] == "hash" and conv["staging_materialization"] == "view"
    assert conv["learned_from_project"] == ["key_strategy", "staging_materialization"]
    mine = conventions_for(_query(parsed, {"key_strategy": "sequence"}), "GDP", "SILVER", "d1")
    assert mine["key_strategy"] == "sequence" and mine["learned_from_project"] == ["staging_materialization"]
    plain = conventions_for(_query(None), "GDP", "SILVER", "d1")
    assert plain["key_strategy"] == "sequence" and "learned_from_project" not in plain


def test_code_score_of_a_run_is_the_share_of_its_models_named_as_the_project_names_them():
    out = score.code(["models/gold/opp/fact_a.sql", "models/gold/opp/dim_new.sql", "release/report.json"], PROJECT, of="generated")
    assert (out["matched"], out["total"], out["pct"], out["extra"]) == (1, 2, 50.0, ["dim_new"])
