"""Gold proposal: a consumer hub becomes a fact with mastered keys resolved through the cross-reference, a
hub-to-company link becomes a bridge, descriptors become dimensions, and gold rules (types, HKEY, audit) hold."""

import json
from pathlib import Path

from services.gold.propose import gold_location, gold_type, propose, stem
from services.modeling import spec_v2

ROOT = Path(__file__).resolve().parents[2]
COMPANY = {k: v for k, v in json.loads((ROOT / "domain/company/mdm.json").read_text(encoding="utf-8")).items() if k != "knowledge"}
PROPERTY = {k: v for k, v in json.loads((ROOT / "domain/property/mdm.json").read_text(encoding="utf-8")).items() if k != "knowledge"}
MASTERED = {"COMPANY": COMPANY, "PROPERTY": PROPERTY}
CONSUMES = [{"domain": "COMPANY", "silver_column": "COMPANY_CORE_SKEY", "gold_column": "DIM_COMPANY_SKEY"},
            {"domain": "PROPERTY", "silver_column": "PROPERTY_CORE_SKEY", "gold_column": "DIM_PROPERTY_SKEY"}]


def col(name, data_type="VARCHAR", distinct=None, nullable=True):
    return {"name": name, "data_type": data_type, "nullable": nullable, "distinct_count": distinct}


HUB = {"database": "DEV_GDP_SILVER_DB", "schema": "OPPORTUNITY", "table": "OPPORTUNITY_CORE", "layer": "SILVER_PRE_MDM", "columns": [
    col("OPPORTUNITY_CORE_SKEY", "NUMBER(38,0)"), col("SOURCE_UNIQUE_ID"), col("REF_GDP_SOURCE_SYSTEM_SKEY", "NUMBER(38,0)"),
    col("COMPANY_CORE_SKEY", "NUMBER(38,0)"), col("LANDLORD_COMPANY_CORE_SKEY", "NUMBER(38,0)"), col("PROPERTY_CORE_SKEY", "NUMBER(38,0)"),
    col("OPPORTUNITY_NAME"), col("STAGE", distinct=8), col("DESCRIPTION", distinct=90000), col("DEAL_AMOUNT", "NUMBER(18,2)"),
    col("CLOSE_DATE", "DATE"), col("WIN_RATE", "FLOAT"), col("IS_WON", "BOOLEAN"), col("SENT_F", "VARCHAR"),
    col("OPPORTUNITY_CORE_HKEY"), col("GDP_IS_ACTIVE", "BOOLEAN"), col("GDP_INSERTED_TS", "TIMESTAMP_NTZ")]}
ROLE = {"database": "DEV_GDP_SILVER_DB", "schema": "OPPORTUNITY", "table": "OPPORTUNITY_COMPANY_ROLE", "layer": "SILVER_PRE_MDM", "columns": [
    col("OPPORTUNITY_COMPANY_ROLE_SKEY", "NUMBER(38,0)"), col("OPPORTUNITY_CORE_SKEY", "NUMBER(38,0)"), col("COMPANY_CORE_SKEY", "NUMBER(38,0)"),
    col("SOURCE_UNIQUE_ID"), col("REF_GDP_SOURCE_SYSTEM_SKEY", "NUMBER(38,0)"), col("ROLE_TYPE", distinct=4)]}


def _ent(spec, name):
    return next(e for e in spec["design"]["entities"] if e["entity_name"] == name)


def _cols(e):
    return [a["name"] for a in e["attributes"]]


def test_hub_becomes_a_fact_with_mastered_keys_and_gold_columns():
    out = propose("opportunity", [HUB], consumes=CONSUMES, mastered=MASTERED)
    spec = out["spec"]
    fact = _ent(spec, "FACT_OPPORTUNITY")
    cols = _cols(fact)
    assert cols[0] == "FACT_OPPORTUNITY_SKEY" and fact["attributes"][0]["is_pk"]
    assert cols[-6:] == ["FACT_OPPORTUNITY_HKEY", "GDP_IS_ACTIVE", "GDP_INSERTED_TS", "GDP_INSERTED_BY", "GDP_UPDATED_TS", "GDP_UPDATED_BY"]
    for kept in ("SOURCE_UNIQUE_ID", "DIM_DATA_SOURCE_SKEY", "DIM_COMPANY_SKEY", "LANDLORD_DIM_COMPANY_SKEY", "DIM_PROPERTY_SKEY",
                 "OPPORTUNITY_NAME", "DESCRIPTION", "DEAL_AMOUNT", "CLOSE_DATE", "WIN_RATE", "IS_WON", "SENT_FLAG", "DIM_OPPORTUNITY_STAGE_SKEY"):
        assert kept in cols, kept
    for dropped in ("OPPORTUNITY_CORE_SKEY", "REF_GDP_SOURCE_SYSTEM_SKEY", "COMPANY_CORE_SKEY", "OPPORTUNITY_CORE_HKEY", "STAGE", "SENT_F"):
        assert dropped not in cols, dropped
    assert cols.count("GDP_IS_ACTIVE") == 1                    # silver audit dropped, gold audit injected once
    assert fact["business_keys"] == ["SOURCE_UNIQUE_ID", "DIM_DATA_SOURCE_SKEY"]
    types = {a["name"]: a["datatype"] for a in fact["attributes"]}
    assert types["DEAL_AMOUNT"] == "NUMBER(38,5)" and types["WIN_RATE"] == "NUMBER(10,5)" and types["CLOSE_DATE"] == "DATE"
    assert types["IS_WON"] == "BOOLEAN" and types["DIM_COMPANY_SKEY"] == "LONG" and types["OPPORTUNITY_NAME"] == "STRING"
    assert spec["physical_defaults"]["database"] == "DEV_GDP_GOLD_OPPORTUNITY_DB" and spec["physical_defaults"]["platform"] == "ICEBERG"


def test_mastered_keys_resolve_through_the_full_cross_reference_to_conformed_dimensions():
    out = propose("OPPORTUNITY", [HUB], consumes=CONSUMES, mastered=MASTERED)
    spec = out["spec"]
    dim = _ent(spec, "DIM_COMPANY")
    assert dim["conformed_from"] == "COMPANY" and dim["database"] == "DEV_GDP_GOLD_COMPANY_DB"
    rels = {(r["from_column"], r["to_entity"]) for r in spec["relationships"]}
    assert {("DIM_COMPANY_SKEY", "DIM_COMPANY"), ("LANDLORD_DIM_COMPANY_SKEY", "DIM_COMPANY"),
            ("DIM_PROPERTY_SKEY", "DIM_PROPERTY"), ("DIM_OPPORTUNITY_STAGE_SKEY", "DIM_OPPORTUNITY_STAGE")} <= rels
    resolve = [l for l in out["lines"] if l["gold_column"] == "DIM_PROPERTY_SKEY"][0]
    assert resolve["kind"] == "RESOLVE" and resolve["source_column"] == "PROPERTY_CORE_SKEY"
    assert "PROPERTY_CROSS_REFERENCE on SOURCE_SYSTEM_ID" in resolve["expression"] and "never the STAGE delta" in resolve["note"]
    # descriptor with many values stays an attribute; with few it becomes a dimension
    assert "DIM_OPPORTUNITY_DESCRIPTION" not in [e["entity_name"] for e in spec["design"]["entities"]]
    assert _ent(spec, "DIM_OPPORTUNITY_STAGE")["business_keys"] == ["STAGE"]


def test_many_to_many_link_becomes_a_bridge_with_two_n_to_one_relationships():
    out = propose("OPPORTUNITY", [HUB, ROLE], consumes=CONSUMES, mastered=MASTERED)
    spec = out["spec"]
    names = [e["entity_name"] for e in spec["design"]["entities"]]
    assert names[:2] == ["FACT_OPPORTUNITY", "FACT_OPPORTUNITY_COMPANY_ROLE"]
    bridge = _ent(spec, "FACT_OPPORTUNITY_COMPANY_ROLE")
    assert bridge["table_type"] == "BRIDGE" and "ROLE_TYPE" in _cols(bridge) and not any(c.endswith("_ROLE_TYPE_SKEY") for c in _cols(bridge))
    links = [(r["from_column"], r["to_entity"], r["cardinality"]) for r in spec["relationships"] if r["from_entity"] == "FACT_OPPORTUNITY_COMPANY_ROLE"]
    assert ("FACT_OPPORTUNITY_SKEY", "FACT_OPPORTUNITY", "N:1") in links and ("DIM_COMPANY_SKEY", "DIM_COMPANY", "N:1") in links
    no_role = {**ROLE, "columns": [c for c in ROLE["columns"] if c["name"] != "ROLE_TYPE"]}
    names = [e["entity_name"] for e in propose("OPPORTUNITY", [HUB, no_role], consumes=CONSUMES, mastered=MASTERED)["spec"]["design"]["entities"]]
    assert "BRIDGE_OPPORTUNITY_COMPANY" in names


def test_the_proposal_is_a_valid_gold_model():
    out = propose("OPPORTUNITY", [HUB, ROLE], consumes=CONSUMES, mastered=MASTERED)
    spec = spec_v2.normalize_v2(out["spec"], "m-gold")
    assert spec["layer"] == "GOLD"
    errors = [i for i in spec_v2.validate(spec, []) if i["severity"] == "ERROR"]
    assert not errors, errors
    assert spec_v2.normalize_v2(out["spec"], "m")["design"]["entities"][-1]["conformed_from"] in ("COMPANY", "PROPERTY")
    every = {(e["entity_name"], a["name"]) for e in out["spec"]["design"]["entities"] if not e.get("conformed_from") for a in e["attributes"]}
    lined = {(l["gold_table"], l["gold_column"]) for l in out["lines"]}
    assert every <= lined, every - lined                      # every generated gold column says where it comes from


def test_unknown_mastered_domain_is_noted_not_invented():
    out = propose("OPPORTUNITY", [HUB], consumes=CONSUMES, mastered={"COMPANY": COMPANY})
    assert any("PROPERTY is consumed but has no mastered contract" in n for n in out["notes"])
    assert "DIM_PROPERTY" not in [e["entity_name"] for e in out["spec"]["design"]["entities"]]


def test_types_and_names():
    assert stem("OPPORTUNITY_CORE") == "OPPORTUNITY" and stem("CORE") == "CORE"
    assert gold_type("PARTY_ID", "NUMBER(38,0)") == "STRING"         # source ids are strings in gold
    assert gold_type("UNIT_COUNT", "NUMBER(10,0)") == "NUMBER(38,0)"
    assert gold_type("LATITUDE", "FLOAT") == "NUMBER(15,8)"
    assert gold_type("UPDATED_TS", "TIMESTAMP_LTZ") == "TIMESTAMP_NTZ"
    assert gold_type("START_DATE", "TIMESTAMP_NTZ") == "TIMESTAMP_NTZ"
    assert gold_location("lease", "qa")["iceberg"]["external_volume"] == "GDP_SF_QA_GOLD"


def test_keys_and_ids_are_never_descriptors():
    from services.gold.propose import is_descriptor

    assert is_descriptor("STAGE", "VARCHAR") and is_descriptor("LEASE_TYPE", "TEXT")
    for n in ("SOURCE_UNIQUE_ID", "STATUS_ID", "STAGE_SKEY", "TYPE_KEY", "GDP_SOURCE", "OPPORTUNITY_CORE_HKEY"):
        assert not is_descriptor(n, "VARCHAR"), n
    assert not is_descriptor("STATUS_CODE_COUNT", "NUMBER(38,0)")
