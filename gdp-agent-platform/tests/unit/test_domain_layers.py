"""Domains by layer: what each layer's stages read (coverage and gaps), each layer's readiness, conventions per
domain and layer, and domain skills that keep their layer."""

from services.common.standard import conventions
from services.knowledge import playbook
from services.knowledge.skills import in_pipeline, with_domain


def test_coverage_shows_what_each_layer_reads_and_its_gaps():
    counts = {"GLOSSARY": {"ANY": 5}, "MODEL_DEFINITION": {"SILVER": 3}, "EXCEPTION": {"ANY": 9}}
    silver = {c["type"]: c for c in playbook.coverage(counts, "SILVER")}
    gold = {c["type"]: c for c in playbook.coverage(counts, "GOLD")}
    assert not silver["GLOSSARY"]["gap"] and not silver["MODEL_DEFINITION"]["gap"] and silver["QA_TEST"]["gap"]
    assert gold["MODEL_DEFINITION"]["gap"]   # silver definitions do not describe gold tables
    assert not gold["EXCEPTION"]["gap"] and "GLOSSARY" not in gold


def test_gold_readiness():
    facts = {"role": "CONSUMER", "consumes": [{"domain": "COMPANY", "gold_column": "DIM_COMPANY_SKEY"},
                                              {"domain": "PROPERTY", "gold_column": "DIM_PROPERTY_SKEY"}],
             "silver_model": 2, "gold_model": 1, "gold_tables": [{"entity": "FACT_X", "columns": ["DIM_COMPANY_SKEY"]}],
             "gold_definitions": 1}
    items = {i["id"]: i for i in playbook.layer_readiness("GOLD", facts)}
    assert items["mdm"]["met"] and items["gold"]["met"] and items["definitions"]["met"]
    assert not items["keys"]["met"] and "PROPERTY" in items["keys"]["detail"]
    assert not {i["id"]: i for i in playbook.layer_readiness("GOLD", {"role": None})}["mdm"]["met"]
    mdm = {i["id"]: i for i in playbook.layer_readiness("MDM", {"contract_problems": [], "exceptions": 4, "consumers": ["OPPORTUNITY"]})}
    assert all(i["met"] for i in mdm.values())
    assert playbook.layer_readiness("SILVER", {}) == []


def test_domain_conventions_per_layer():
    silver = conventions("GDP", None, "SILVER", None, {"branch_prefix": "feat/company-"})
    gold = conventions("GDP", None, "GOLD", {"pr_title": "Gold {name}"}, {"branch_prefix": "feat/company-gold-"})
    assert silver["branch_prefix"] == "feat/company-" and silver["model_dir"] == "models/silver/{domain}"
    assert gold["branch_prefix"] == "feat/company-gold-" and gold["model_dir"] == "models/gold/{domain}"
    assert gold["pr_title"] == "Gold {name}"
    assert conventions("GDP", None, "SILVER", None, {"branch_prefix": 3})["branch_prefix"] == "feat/gdp-"   # wrong type ignored


def test_domain_skills_keep_their_layer():
    rows = [{"stage": "DBT", "skill_name": "MY-GOLD", "pipeline": "GOLD"}, {"stage": "DBT", "skill_name": "SHARED"}]
    assert with_domain(["GOLD-MODEL"], rows, "DBT", "GOLD") == ["GOLD-MODEL", "MY-GOLD", "SHARED"]
    assert with_domain(["GDP-DBT-ONBOARD-SOURCE"], rows, "DBT", "SILVER") == ["GDP-DBT-ONBOARD-SOURCE", "SHARED"]
    assert in_pipeline("ANY", "GOLD") and not in_pipeline("GOLD", "SILVER")
