"""Accuracy against references: the engine's own proposal, mapping and code measured against what a person gave."""

from services.gold.propose import propose
from services.reference import score
from tests.unit.test_gold_align import REFERENCE
from tests.unit.test_gold_propose import CONSUMES, HUB, MASTERED


def test_model_score_counts_reference_columns_the_proposal_produced():
    out = score.model(propose("OPPORTUNITY", [HUB], consumes=CONSUMES, mastered=MASTERED)["spec"], REFERENCE)
    # OPPORTUNITY_FACT: 9 columns, the proposal has 8 (its key, SENT_F as SENT_FLAG, not NEXT_STEP); the owner dim: 0 of 2
    assert out["total"] == 11 and out["matched"] == 8 and out["pct"] == round(100 * 8 / 11, 1)
    assert "OPPORTUNITY_FACT.NEXT_STEP" in out["missing"] and "DIM_OPPORTUNITY_OWNER" in out["missing"]
    assert "DIM_OPPORTUNITY_STAGE" in out["extra"] and "DIM_COMPANY" not in out["extra"]   # conformed dims are not extra
    fact = out["details"]["tables"][0]
    assert fact == {"reference": "OPPORTUNITY_FACT", "proposed": "FACT_OPPORTUNITY", "how": "same subject", "matched": 8, "columns": 9}
    assert 0 < out["details"]["types_agree_pct"] < 100          # e.g. SENT_F BOOLEAN vs STRING disagree


def test_a_proposal_equal_to_the_reference_scores_100():
    assert score.model(REFERENCE, REFERENCE)["pct"] == 100.0
    assert score.model({"design": {"entities": []}}, REFERENCE)["pct"] == 0.0


def test_mapping_score_is_the_engine_first_choice_against_the_sttm():
    lines = {"OPPORTUNITY_STAGE": {"source_column": "STAGE"}, "DEAL_VALUE": {"source_column": "DEAL_AMOUNT"},
             "OWNER": {"source_column": "OWNER_NAME"}, "NEXT_STEP": {"source_column": None, "mapping_type": "UNMAPPED"}}
    out = score.mapping([("STAGE", "OPPORTUNITY_STAGE"), ("DEAL_AMOUNT", "AMOUNT"), ("CLOSE_DATE", "CLOSE_DATE")], lines,
                        ["STAGE", "DEAL_AMOUNT", "CLOSE_DATE"])
    assert (out["matched"], out["total"], out["pct"]) == (1, 2, 50.0)      # OWNER_NAME is not a source of this run
    assert out["missing"] == ["DEAL_AMOUNT -> DEAL_VALUE (engine: AMOUNT)"] and out["extra"] == ["CLOSE_DATE"]


def test_code_score_compares_model_names():
    out = score.code(["models/gold/fact_opportunity.sql", "models/gold/dim_stage.sql"],
                     ["proj/models/gold/fact_opportunity.sql", "proj/models/gold/dim_owner.sql", "proj/macros/x.sql"])
    assert (out["matched"], out["total"], out["missing"], out["extra"]) == (1, 2, ["dim_owner"], ["dim_stage"])
