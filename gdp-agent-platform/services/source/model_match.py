"""Which target model should these source tables feed? Scored from everything the platform knows (pure).

Evidence, strongest first:
  history   an earlier run already mapped one of these source tables to the target
  lineage   an approved model design (MODEL_DEFINITION) lists one of these tables as a source
  columns   source columns that match target columns by name, by glossary synonym or by close name
  name      table-name tokens shared with the target
  domain    the target belongs to the domain detected for the source

The result is a ranked list with coverage both ways (how much of the target these columns fill, how much of the
source has a place in the target) and a recommendation: map to an existing model, extend one, or design a new one.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

STOP = {"ID", "LID", "KEY", "CODE", "CD", "NAME", "NM", "DATE", "DT", "TS", "FLAG", "FLG", "NUM", "NO", "THE", "OF",
        "TBL", "TABLE", "RAW", "STG", "SRC", "DIM", "FACT", "FCT", "VW", "V"}


def norm(name: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


def tokens(name: Any) -> Set[str]:
    return {t for t in re.split(r"[^A-Z0-9]+", str(name or "").upper()) if t and t not in STOP and len(t) > 1}


def _similar(a: str, b: str) -> bool:
    """Close names: one contains the other (ASSESSMENT_LID ~ ASSESSMENT_ID is handled by tokens) or token overlap."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6


def match_columns(source_cols: Iterable[str], target_cols: Iterable[str],
                  synonyms: Dict[str, str]) -> Tuple[List[Tuple[str, str, str]], List[str], List[str]]:
    """([(source, target, how)], unmatched source, unmatched target). Each target column is used once."""
    targets = {norm(t): t for t in target_cols}
    free = dict(targets)
    pairs: List[Tuple[str, str, str]] = []
    rest: List[str] = []
    for col in source_cols:
        n = norm(col)
        if n in free:
            pairs.append((col, free.pop(n), "name"))
        elif synonyms.get(n) in free:
            pairs.append((col, free.pop(synonyms[n]), "glossary"))
        else:
            rest.append(col)
    unmatched: List[str] = []
    for col in rest:
        hit = next((key for key, t in free.items() if _similar(col, t)), None)
        if hit:
            pairs.append((col, free.pop(hit), "similar"))
        else:
            unmatched.append(col)
    return pairs, unmatched, list(free.values())


def score_targets(source: Dict[str, List[str]], targets: List[Dict[str, Any]], synonyms: Dict[str, str],
                  history: List[Dict[str, Any]], lineage: Dict[str, Set[str]],
                  domain: Optional[str]) -> List[Dict[str, Any]]:
    """Every target with any evidence, best first."""
    tables = {t.upper() for t in source}
    source_cols = [c for cols in source.values() for c in cols]
    source_tokens = set().union(*(tokens(t) for t in source)) if source else set()
    out: List[Dict[str, Any]] = []
    for target in targets:
        fqn = str(target.get("fqn") or "").upper()
        name = str(target.get("target_table") or "")
        cols = [str(c) for c in target.get("columns") or [] if c]
        pairs, unmatched, missing = match_columns(source_cols, cols, synonyms)
        cover_target = len(pairs) / len(cols) if cols else 0.0
        cover_source = len(pairs) / len(source_cols) if source_cols else 0.0
        shared = source_tokens & tokens(name)
        past = [h for h in history if str(h.get("target_fqn") or "").upper() == fqn and str(h.get("source_table") or "").upper() in tables]
        designed = tables & {t.upper() for t in lineage.get(fqn, set())}
        same_domain = bool(domain) and str(target.get("domain_name") or "").upper() == str(domain).upper()
        score = 0.45 * cover_target + 0.15 * cover_source + (0.15 if shared else 0.0)
        score += 0.35 if past else 0.0
        score += 0.35 if designed else 0.0
        score += 0.08 if same_domain else 0.0
        if not (pairs or shared or past or designed):
            continue
        evidence = []
        if past:
            evidence.append(f"Already mapped from {past[0]['source_table']} in run {past[0].get('run_name') or past[0].get('run_id', '')[:8]}")
        if designed:
            evidence.append(f"Approved model design lists {', '.join(sorted(designed))} as a source")
        if pairs:
            how = {}
            for _, _, kind in pairs:
                how[kind] = how.get(kind, 0) + 1
            evidence.append(f"{len(pairs)} of {len(cols)} target columns matched ("
                            + ", ".join(f"{n} by {k}" for k, n in sorted(how.items(), key=lambda x: -x[1])) + ")")
        if shared:
            evidence.append(f"Name shares {', '.join(sorted(shared)[:4])}")
        if same_domain:
            evidence.append(f"Belongs to the detected {domain} domain")
        out.append({
            "kind": "existing", "fqn": target.get("fqn"), "target_table": name, "domain_name": target.get("domain_name"),
            "score": round(min(score, 1.0), 3), "coverage_target": round(cover_target, 3),
            "coverage_source": round(cover_source, 3), "matched": [list(p) for p in pairs[:40]],
            "missing_target_columns": missing[:40], "unmatched_source_columns": unmatched[:40],
            "evidence": evidence, "reason": evidence[0] if evidence else "",
            "overlap_columns": [p[1] for p in pairs[:12]],
        })
    out.sort(key=lambda m: m["score"], reverse=True)
    return out


def recommend(matches: List[Dict[str, Any]], proposed_name: str, strong: float = 0.6,
              weak: float = 0.35) -> Dict[str, Any]:
    """What to do with these tables, in one line plus reasons."""
    best = matches[0] if matches else None
    if best and best["score"] >= strong:
        extend = best["coverage_source"] < 0.6 and len(best["unmatched_source_columns"]) >= 3
        return {"action": "EXTEND_EXISTING" if extend else "MAP_EXISTING", "fqn": best["fqn"],
                "target_table": best["target_table"], "confidence": best["score"],
                "headline": (f"Map to {best['target_table']} and add {len(best['unmatched_source_columns'])} new columns"
                             if extend else f"Map to {best['target_table']}"),
                "why": best["evidence"][:4]}
    if best and best["score"] >= weak:
        return {"action": "REVIEW", "fqn": best["fqn"], "target_table": best["target_table"],
                "confidence": best["score"],
                "headline": f"Possibly {best['target_table']}; check the match or design a new model",
                "why": best["evidence"][:4] + [f"Only {round(best['coverage_target'] * 100)}% of its columns are covered"]}
    return {"action": "NEW", "fqn": None, "target_table": proposed_name, "confidence": best["score"] if best else 0.0,
            "headline": f"Design a new model ({proposed_name})",
            "why": ([f"Closest existing model {best['target_table']} only matches {round(best['score'] * 100)}%",
                     "Design it in Mapping: AI proposes entities, keys and columns from the profiles and domain knowledge"] if best
                    else ["No registered model shares columns, names, history or lineage with these tables"])}
