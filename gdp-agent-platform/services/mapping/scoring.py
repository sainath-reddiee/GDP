"""Weighted hybrid score, ranking and recommendation (pure). Weights and thresholds come from configuration."""

from __future__ import annotations

from typing import Any, Dict, List

COMPONENTS = ("semantic", "keyword", "datatype", "statistical", "domain", "context", "historical")
AMBIGUITY_MARGIN = 0.08


def validate_config(weights: Dict[str, float], thresholds: Dict[str, float]) -> None:
    assert set(weights) == set(COMPONENTS), f"weights must define exactly {COMPONENTS}"
    assert all(0 <= w <= 1 for w in weights.values()), "weights must be between 0 and 1"
    assert abs(sum(weights.values()) - 1.0) < 1e-6, "weights must sum to 1"
    assert 0 < thresholds["human_review"] < thresholds["auto_suggest"] <= 1, "need 0 < human_review < auto_suggest <= 1"


def final_score(scores: Dict[str, float], weights: Dict[str, float]) -> float:
    return round(sum(weights[c] * scores.get(c, 0.0) for c in COMPONENTS), 4)


def recommendation(score: float, thresholds: Dict[str, float]) -> str:
    if score >= thresholds["auto_suggest"]:
        return "AUTO_SUGGEST"
    if score >= thresholds["human_review"]:
        return "HUMAN_REVIEW"
    return "MANUAL"


def rank_candidates(candidates: List[Dict[str, Any]], weights: Dict[str, float], thresholds: Dict[str, float],
                    top_k: int) -> List[Dict[str, Any]]:
    """candidates: one source column against all targets, each with "scores". Returns top_k ranked with confidence."""
    for c in candidates:
        c["final_score"] = final_score(c["scores"], weights)
    ranked = sorted(candidates, key=lambda c: (-c["final_score"], c["target"]["column_name"]))[:top_k]
    for i, c in enumerate(ranked):
        c["rank"] = i + 1
        runner_up = ranked[1]["final_score"] if len(ranked) > 1 else 0.0
        margin = c["final_score"] - runner_up if i == 0 else 0.0
        c["margin"] = round(margin, 4)
        c["confidence"] = round(min(1.0, c["final_score"] * (0.85 + min(margin, 0.3) / 2)), 4) if i == 0 \
            else round(c["final_score"] * 0.85, 4)
        c["recommendation"] = recommendation(c["final_score"], thresholds) if i == 0 else "MANUAL"
        c["ambiguous"] = i == 0 and (c["recommendation"] != "AUTO_SUGGEST" or margin < AMBIGUITY_MARGIN)
    return ranked


def explain(candidate: Dict[str, Any]) -> str:
    """Deterministic reason built from the strongest evidence."""
    s, e = candidate["scores"], candidate["evidence"]
    parts = []
    if s["domain"] >= 0.8:
        parts.append(next(iter(e["domain"].values())))
    if s["keyword"] >= 0.6:
        parts.append(f"name tokens {e['keyword']['source_tokens']} match {e['keyword']['target_tokens']}")
    if s["historical"] > 0:
        parts.append(f"previously approved {e['historical']['previous_mapping']}")
    if s["semantic"] >= 0.5:
        parts.append(f"descriptions are semantically similar (cosine {e['semantic']['cosine']})")
    if s["datatype"] >= 0.8:
        parts.append(f"compatible types {e['datatype']['families']}")
    if e.get("transformation_rule"):
        parts.append(f"transformation from {e['transformation_rule']}")
    return "; ".join(parts) or "weak evidence; needs manual review"
