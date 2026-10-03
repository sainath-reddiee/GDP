"""Domain identification scoring (pure)."""

from __future__ import annotations

from typing import Any, Dict, List, Set

TERM_WEIGHT = 0.6
SEARCH_WEIGHT = 0.4


def score_domains(source_terms: Set[str], domains: List[Dict[str, Any]],
                  search_hits: Dict[str, int]) -> List[Dict[str, Any]]:
    """domains: [{"domain_id", "name", "terms": set}]. Returns domains sorted by confidence with evidence."""
    total_hits = sum(search_hits.values()) or 1
    scored = []
    for d in domains:
        matched = sorted(source_terms & d["terms"])
        coverage = len(matched) / len(source_terms) if source_terms else 0.0
        hits = search_hits.get(d["name"], 0)
        confidence = round(TERM_WEIGHT * coverage + SEARCH_WEIGHT * hits / total_hits, 4)
        scored.append({
            "domain_id": d["domain_id"], "domain_name": d["name"], "confidence": confidence,
            "evidence": {"matched_terms": matched, "term_coverage": round(coverage, 4),
                         "search_hits": hits, "source_terms": sorted(source_terms)},
        })
    return sorted(scored, key=lambda x: (-x["confidence"], x["domain_name"]))
