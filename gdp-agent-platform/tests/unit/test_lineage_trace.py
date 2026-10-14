"""End-to-end lineage: a gold fact column traces back through the cross-reference and the silver mapping to the source,
and a source column traces forward to gold."""

from services.gold import dbt
from services.gold.propose import propose
from services.lineage import trace as lt
from tests.unit.test_gold_propose import COMPANY, CONSUMES, HUB, MASTERED

LANDING = "DEV_AI_PLATFORM.LANDING.CRM__ACCOUNTS"


def _edges():
    out = propose("OPPORTUNITY", [HUB], consumes=CONSUMES, mastered=MASTERED)
    gold = dbt.generate(out["spec"], out["lines"], mastered=MASTERED, consumes=CONSUMES)["lineage"]
    edges = [lt.edge(**{k: v for k, v in e.items() if k in ("from_layer", "from_object", "from_column", "to_layer", "to_object", "to_column", "kind", "expression")})
             for e in gold]
    edges += lt.mdm_edges("COMPANY", COMPANY) + lt.mdm_edges("PROPERTY", MASTERED["PROPERTY"])
    edges += lt.consumer_edges("OPPORTUNITY", CONSUMES, [{"object": "DEV_GDP_SILVER_DB.OPPORTUNITY.OPPORTUNITY_CORE",
                                                          "columns": [c["name"] for c in HUB["columns"]]}], MASTERED)
    # pipeline 1: source landed, then mapped into the silver hubs
    edges += [lt.edge("SOURCE", "CRM.SALES.ACCOUNTS", None, "BRONZE", LANDING, None, "INGEST"),
              lt.edge("BRONZE", LANDING, "ACCOUNT_ID", "SILVER_PRE_MDM", "DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE", "SOURCE_UNIQUE_ID", "MAP"),
              lt.edge("BRONZE", LANDING, "AMOUNT", "SILVER_PRE_MDM", "DEV_GDP_SILVER_DB.OPPORTUNITY.OPPORTUNITY_CORE", "DEAL_AMOUNT", "MAP")]
    return edges


def test_gold_key_traces_back_through_mdm_to_the_source():
    t = lt.trace(_edges(), "DEV_GDP_GOLD_OPPORTUNITY_DB.OPPORTUNITY.FACT_OPPORTUNITY", "DIM_COMPANY_SKEY", direction="up")
    objects = {n["object"] for n in t["nodes"]}
    assert "DEV_GDP_SILVER_DB.OPPORTUNITY.OPPORTUNITY_CORE" in objects                   # the hub's pre-MDM key
    assert "DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE" in objects                           # the company it was looked up in
    assert "DEV_GDP_GOLD_COMPANY_DB.COMPANY.DIM_COMPANY" in objects                       # the conformed dimension
    kinds = {e["kind"] for e in t["edges"]}
    assert {"RESOLVE"} <= kinds
    assert t["layers"][0] in ("SILVER_PRE_MDM", "BRONZE", "SOURCE") and t["layers"][-1] == "GOLD"


def test_source_column_traces_forward_to_gold():
    t = lt.trace(_edges(), LANDING, "AMOUNT", direction="down")
    assert ("DEV_GDP_GOLD_OPPORTUNITY_DB.OPPORTUNITY.FACT_OPPORTUNITY", "DEAL_AMOUNT") in {(e["to_object"], e["to_column"]) for e in t["edges"]}
    assert "DIM_COMPANY_SKEY" not in {e["to_column"] for e in t["edges"]}           # only this column's path


def test_mastered_chain_reaches_the_golden_record():
    t = lt.trace(_edges(), "COMPANY.COMPANY_CORE", "SOURCE_UNIQUE_ID", direction="down")   # SCHEMA.TABLE matches any environment
    layers = [n["layer"] for n in t["nodes"]]
    assert layers.index("MDM") < layers.index("SILVER_POST_MDM") < layers.index("GOLD")
    assert any(e["kind"] == "MDM_MATCH" and e["to_column"] == "SOURCE_UNIQUE_ID" for e in t["edges"])
    assert any(e["kind"] == "MDM_GOLDEN" for e in t["edges"])


def test_entity_level_trace_and_depth_limit():
    full = lt.trace(_edges(), "CRM.SALES.ACCOUNTS")
    assert any(e["kind"] == "INGEST" for e in full["edges"])
    short = lt.trace(_edges(), "CRM.SALES.ACCOUNTS", depth=1)
    assert len(short["edges"]) == 1 and short["edges"][0]["kind"] == "INGEST"
    assert lt.trace([], "X.Y.Z")["nodes"] == []
