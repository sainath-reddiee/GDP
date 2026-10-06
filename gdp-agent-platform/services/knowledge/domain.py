"""Domain identification scoring (pure)."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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


# ---------------------------------------------------------------- signal-aware inference (domain packs)

SIGNAL_SHARE, TERM_SHARE, SEARCH_SHARE = 0.5, 0.3, 0.2
SATURATION = 8.0
COLUMN_HIT_CAP = 3


def _matches(keyword: str, name: str) -> bool:
    """Short keywords match whole tokens (LOT never matches PILOT); longer or punctuated ones match substrings."""
    kw, upper = keyword.upper(), name.upper()
    if len(kw) >= 4 or not kw.isalnum():
        return kw in upper
    return kw in {t for t in re.split(r"[^A-Z0-9]+", upper) if t}


def signal_score(tables: Iterable[str], columns: Iterable[str], signals: Dict[str, Dict[str, float]]) -> Tuple[float, List[str]]:
    """Weighted keyword hits on table and column names, saturating to 0..1 so wide tables do not dominate."""
    tables, columns = list(tables), list(columns)
    raw, hits = 0.0, []
    for kw, weight in (signals.get("tables") or {}).items():
        found = [t for t in tables if _matches(kw, t)]
        if found:
            raw += weight * 1.5
            hits.append(f"table {found[0]} ~ {kw}")
    for kw, weight in (signals.get("columns") or {}).items():
        found = [c for c in columns if _matches(kw, c)]
        if found:
            raw += weight * min(len(found), COLUMN_HIT_CAP) / 2
            hits.append(f"{len(found)} column(s) ~ {kw}")
    return round(raw / (raw + SATURATION), 4), hits


def infer_domain(tables: Iterable[str], columns: Iterable[str], domains: List[Dict[str, Any]],
                 search_hits: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """domains: [{"domain_id", "name", "terms": set, "signals": {...}}]. Combines signal hits, glossary/target term
    coverage normalized by the smaller vocabulary (not by every source term), and Cortex Search hit share."""
    from services.knowledge.terms import entity_tokens, token_set

    tables, columns = list(tables), list(columns)
    source_terms: Set[str] = set()
    for c in columns:
        source_terms |= token_set(c)
    for t in tables:
        source_terms |= entity_tokens(t)
    search_hits = search_hits or {}
    total_hits = sum(search_hits.values())
    scored = []
    for d in domains:
        signal, hits = signal_score(tables, columns, d.get("signals") or {})
        matched = sorted(source_terms & d.get("terms", set()))
        base = min(len(source_terms), len(d.get("terms", set()))) or 1
        coverage = min(len(matched) / min(base, 25), 1.0)
        share = search_hits.get(d["name"], 0) / total_hits if total_hits else 0.0
        weights = (SIGNAL_SHARE, TERM_SHARE, SEARCH_SHARE) if d.get("signals") else (0.0, 0.6, 0.4)
        confidence = round(weights[0] * signal + weights[1] * coverage + weights[2] * share, 4)
        scored.append({
            "domain_id": d["domain_id"], "domain_name": d["name"], "confidence": confidence,
            "evidence": {"signals": hits[:12], "signal_score": signal, "matched_terms": matched[:30],
                         "term_coverage": round(coverage, 4), "search_hits": search_hits.get(d["name"], 0)},
        })
    return sorted(scored, key=lambda x: (-x["confidence"], x["domain_name"]))
