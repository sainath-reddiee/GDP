from services.source.model_match import match_columns, recommend, score_targets

SRC = {"ASSESSMENT": ["ASSESSMENT_LID", "TAXARN", "OWNER_NAME", "GEOMETRY", "TAX_YEAR", "LAND_VALUE", "NOTES"]}
TARGETS = [
    {"fqn": "DB.SILVER.PROPERTY_ASSESSMENT", "target_table": "PROPERTY_ASSESSMENT", "domain_name": "PROPERTY",
     "columns": ["ASSESSMENT_ID", "TAX_ARN", "OWNER_NAME", "TAX_YEAR", "LAND_VALUE", "INSERTED_TS"]},
    {"fqn": "DB.SILVER.COMPANY_CORE", "target_table": "COMPANY_CORE", "domain_name": "COMPANY", "columns": ["COMPANY_ID", "NAME"]},
]


def test_columns_match_by_name_glossary_and_similarity():
    pairs, unmatched, missing = match_columns(SRC["ASSESSMENT"], TARGETS[0]["columns"], {"TAXARN": "TAXARN"})
    how = {s: (t, k) for s, t, k in pairs}
    assert how["OWNER_NAME"] == ("OWNER_NAME", "name")
    assert how["ASSESSMENT_LID"][0] == "ASSESSMENT_ID"
    assert "NOTES" in unmatched and "INSERTED_TS" in missing


def test_history_and_lineage_outrank_name_overlap():
    plain = score_targets(SRC, TARGETS, {}, [], {}, None)
    assert plain[0]["target_table"] == "PROPERTY_ASSESSMENT" and all(m["target_table"] != "COMPANY_CORE" for m in plain)
    remembered = score_targets(SRC, TARGETS, {}, [{"source_table": "ASSESSMENT", "target_fqn": "DB.SILVER.PROPERTY_ASSESSMENT",
                                                  "run_name": "Lightbox v1"}], {}, "PROPERTY")
    top = remembered[0]
    assert top["score"] > plain[0]["score"] and "Already mapped from ASSESSMENT in run Lightbox v1" in top["evidence"]
    assert any("PROPERTY domain" in e for e in top["evidence"])
    designed = score_targets(SRC, TARGETS, {}, [], {"DB.SILVER.PROPERTY_ASSESSMENT": {"ASSESSMENT"}}, None)
    assert any("Approved model design" in e for e in designed[0]["evidence"])


def test_recommendation():
    strong = score_targets(SRC, TARGETS, {}, [{"source_table": "ASSESSMENT", "target_fqn": "DB.SILVER.PROPERTY_ASSESSMENT"}],
                           {"DB.SILVER.PROPERTY_ASSESSMENT": {"ASSESSMENT"}}, "PROPERTY")
    rec = recommend(strong, "DIM_ASSESSMENT")
    assert rec["action"] in ("MAP_EXISTING", "EXTEND_EXISTING") and rec["target_table"] == "PROPERTY_ASSESSMENT"
    assert recommend([], "DIM_ASSESSMENT")["action"] == "NEW"
    weak = [{"score": 0.4, "fqn": "X", "target_table": "X", "coverage_target": 0.2, "evidence": ["e"],
             "coverage_source": 0.1, "unmatched_source_columns": []}]
    assert recommend(weak, "DIM_A")["action"] == "REVIEW"
