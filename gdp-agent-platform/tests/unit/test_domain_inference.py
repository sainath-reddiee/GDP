from infrastructure.seed_knowledge import GDP_DOMAIN_ID, GDP_TABLE_ID, domain_id, domain_rows, load_domain_packs, merge_columns
from services.knowledge.domain import _matches, infer_domain
from services.knowledge.terms import entity_tokens, token_set


def domains():
    out = []
    for pack in load_domain_packs():
        terms = set()
        for t in pack["targets"]:
            terms |= entity_tokens(t["table"])
            for c in t.get("columns") or []:
                terms |= token_set(c["name"])
        out.append({"domain_id": domain_id(pack["domain"]["name"]), "name": pack["domain"]["name"], "terms": terms,
                    "signals": pack["domain"].get("signals")})
    return out


def top(tables, columns):
    ranked = infer_domain(tables, columns, domains())
    return ranked[0]["domain_name"], ranked[0]["confidence"], ranked


def test_company_sources_are_recognized():
    name, conf, _ = top(["PS_CUSTOMER", "PS_CUST_ADDRESS", "PS_VENDOR"],
                        ["CUST_ID", "NAME1", "EFF_STATUS", "CUST_STATUS", "COUNTRY", "DUNS_NUMBER", "NAICS_CODE",
                         "ADDRESS1", "CITY", "POSTAL"])
    assert name == "COMPANY" and conf >= 0.4


def test_opportunity_sources_are_recognized():
    name, conf, _ = top(["OPPORTUNITY"],
                        ["ID", "NAME", "STAGENAME", "STAGE_NAME", "CLOSEDATE", "PROBABILITY", "DEAL_TYPE__C",
                         "RFP_DUE_DATE__C", "SOP_AMOUNT__C", "WIN_LOSS_REASON__C"])
    assert name == "OPPORTUNITY" and conf >= 0.4


def test_property_sources_are_recognized():
    name, _, _ = top(["DIM_PROPERTY_BUILDING", "FACT_AAR"],
                     ["BUILDING_ID", "PARCEL_NUMBER", "APN", "YEAR_BUILT", "BUILDING_SQFT", "LOT_SIZE_ACRES", "ZONING"])
    assert name == "PROPERTY"


def test_unrelated_tables_get_low_confidence_and_short_keywords_match_whole_tokens():
    _, conf, _ = top(["AIRFLOW_DAG_RUNS"], ["DAG_ID", "RUN_ID", "START_TS", "END_TS", "STATE"])
    assert conf < 0.3
    assert _matches("LOT", "LOT_SIZE") and not _matches("LOT", "PILOT_FLAG")
    assert _matches("__C", "STAGE__C") and _matches("DUNS", "COMPANY_DUNS_NO")


def test_seed_rows_keep_gdp_ids_and_are_unique():
    rows = domain_rows("AI")
    assert rows["domains"][0][0] == GDP_DOMAIN_ID and rows["tables"][0][0] == GDP_TABLE_ID
    assert {d[1] for d in rows["domains"]} >= {"GDP", "COMPANY", "OPPORTUNITY", "PROPERTY"}
    ids = [t[0] for t in rows["tables"]]
    assert len(ids) == len(set(ids)) and len({c[0] for c in rows["columns"]}) == len(rows["columns"])
    spec = next(t[12] for t in rows["tables"] if t[4] == "COMPANY_ADDRESS")
    assert spec["role"] == "spoke" and spec["hub_fk"] == "COMPANY_CORE_SKEY"


def test_live_columns_win_and_drift_is_reported():
    cols, drift = merge_columns(
        [{"name": "A", "type": "TEXT", "nullable": True, "definition": "a"}, {"name": "B", "type": "TEXT", "nullable": True}],
        [{"name": "A", "type": "VARCHAR(10)", "nullable": False}, {"name": "C", "type": "NUMBER(38,0)", "nullable": True}])
    assert [c["name"] for c in cols] == ["A", "C"] and cols[0]["definition"] == "a" and cols[0]["type"] == "VARCHAR(10)"
    assert drift == {"live": True, "missing_in_silver": ["B"], "not_in_contract": ["C"]}
    assert merge_columns([{"name": "A"}], None) == ([{"name": "A"}], {"live": False})
