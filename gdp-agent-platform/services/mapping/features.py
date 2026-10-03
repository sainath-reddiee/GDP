"""Hybrid mapping features (pure). Each returns a score in [0, 1] plus evidence.

Inputs are plain dicts:
  source: {column_name, table_name, data_type, semantic_type, null_percentage, distinct_percentage, cardinality,
           max_length, date_format, values (masked distinct values for low-cardinality columns), description}
  target: {column_name, table_name, data_type, nullable, semantic_type, is_business_key, accepted_values, definition}
  knowledge: {glossary: {TARGET_COLUMN: {synonyms}}, rules: {TARGET_COLUMN: {source_values|expression}},
              transforms: [{semantic_type|source_pattern, expression, macro}], history: [(SOURCE_COLUMN, TARGET_COLUMN, weight)]}
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

from services.knowledge.terms import entity_tokens, jaccard, token_set
from services.profiling.profiler import type_family

COSINE_FLOOR, COSINE_SPAN = 0.25, 0.55
SYSTEM_DERIVED = {"SURROGATE_KEY", "RECORD_SOURCE", "AUDIT_TIMESTAMP"}

CASTS = {  # (source family, target family) -> score; same family is 1.0
    ("TIMESTAMP", "DATE"): 0.85, ("DATE", "TIMESTAMP"): 0.8, ("NUMBER", "TEXT"): 0.5,
    ("DATE", "TEXT"): 0.3, ("TIMESTAMP", "TEXT"): 0.3, ("BOOLEAN", "TEXT"): 0.4,
}


def _length(data_type: str) -> Optional[int]:
    m = re.search(r"\((\d+)", data_type or "")
    return int(m.group(1)) if m and type_family(data_type) == "TEXT" else None


def _precision(data_type: str) -> Optional[Tuple[int, int]]:
    m = re.search(r"\((\d+)\s*,\s*(\d+)\)", data_type or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def semantic_score(cosine: Optional[float]) -> float:
    if cosine is None:
        return 0.0
    return round(max(0.0, min(1.0, (cosine - COSINE_FLOOR) / COSINE_SPAN)), 4)


def keyword_score(source: Dict[str, Any], target: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    src, tgt = token_set(source["column_name"]), token_set(target["column_name"])
    entity = entity_tokens(target["table_name"]) & entity_tokens(source["table_name"])
    direct = jaccard(src, tgt)
    with_entity = jaccard(src | entity, tgt)
    ratio = difflib.SequenceMatcher(None, source["column_name"].upper(), target["column_name"].upper()).ratio()
    score = max(direct, with_entity, 0.8 * ratio)
    return round(score, 4), {"source_tokens": sorted(src), "target_tokens": sorted(tgt),
                             "entity_tokens": sorted(entity), "jaccard": round(max(direct, with_entity), 4),
                             "sequence_ratio": round(ratio, 4)}


def datatype_score(source: Dict[str, Any], target: Dict[str, Any], transformation: Optional[str]) -> Tuple[float, Dict]:
    sf, tf = type_family(source["data_type"]), type_family(target["data_type"])
    if sf == tf:
        score = 1.0
    elif sf == "TEXT" and tf in ("DATE", "TIMESTAMP") and (source.get("date_format") or transformation):
        score = 0.8
    elif sf == "TEXT" and tf in ("NUMBER", "DATE", "TIMESTAMP", "BOOLEAN"):
        score = 0.3
    else:
        score = CASTS.get((sf, tf), 0.0)
    notes = [f"{sf} -> {tf}"]
    t_len, s_max = _length(target["data_type"]), source.get("max_length")
    if t_len and s_max and s_max > t_len:
        score *= 0.5
        notes.append(f"source values up to {s_max} chars exceed target length {t_len}")
    sp, tp = _precision(source["data_type"]), _precision(target["data_type"])
    if sp and tp and (sp[0] - sp[1] > tp[0] - tp[1] or sp[1] > tp[1]):
        score *= 0.7
        notes.append("precision/scale narrower in target")
    return round(score, 4), {"families": f"{sf}->{tf}", "notes": notes}


def _value_fit(source: Dict[str, Any], target: Dict[str, Any], knowledge: Dict[str, Any]) -> Optional[float]:
    accepted = [str(v).upper() for v in (target.get("accepted_values") or [])]
    values = source.get("values")
    if not accepted or not values:
        return None
    decode = {k.upper(): str(v).upper() for k, v in
              (knowledge.get("rules", {}).get(target["column_name"], {}).get("source_values") or {}).items()}
    hits = sum(1 for v in values if str(v).strip().upper() in accepted or decode.get(str(v).strip().upper()) in accepted)
    return hits / len(values)


def statistical_score(source: Dict[str, Any], target: Dict[str, Any], knowledge: Dict[str, Any]) -> Tuple[float, Dict]:
    parts: Dict[str, float] = {}
    null_ratio = (source.get("null_percentage") or 0) / 100
    if target.get("is_business_key"):
        parts["uniqueness"] = round((source.get("distinct_percentage") or 0) / 100 * (1 - null_ratio), 4)
    elif not target.get("nullable", True):
        parts["completeness"] = round(1 - null_ratio, 4)
    if target.get("semantic_type"):
        same = source.get("semantic_type") == target["semantic_type"]
        related = {source.get("semantic_type"), target["semantic_type"]} <= {"DATE", "DATE_OF_BIRTH", "TIMESTAMP"}
        parts["semantic_type"] = 1.0 if same else 0.5 if related else 0.0
    fit = _value_fit(source, target, knowledge)
    if fit is not None:
        parts["value_fit"] = round(fit, 4)
    score = sum(parts.values()) / len(parts) if parts else 0.5
    return round(score, 4), parts


def domain_score(source: Dict[str, Any], target: Dict[str, Any], knowledge: Dict[str, Any]) -> Tuple[float, Dict]:
    name = source["column_name"].upper()
    synonyms = {s.upper() for s in knowledge.get("glossary", {}).get(target["column_name"], {}).get("synonyms", [])}
    if name in synonyms:
        return 1.0, {"glossary": f"{name} is a listed synonym of {target['column_name']}"}
    rule = knowledge.get("rules", {}).get(target["column_name"], {})
    if rule.get("source_values") and _value_fit(source, target, knowledge):
        return 0.8, {"business_rule": f"source values decode to {target['column_name']} accepted values"}
    return 0.0, {}


def context_score(source: Dict[str, Any], target: Dict[str, Any]) -> Tuple[float, Dict]:
    s, t = entity_tokens(source["table_name"]), entity_tokens(target["table_name"])
    score = len(s & t) / len(t) if t else 0.0
    return round(score, 4), {"source_entity": sorted(s), "target_entity": sorted(t)}


def historical_score(source: Dict[str, Any], target: Dict[str, Any], knowledge: Dict[str, Any]) -> Tuple[float, Dict]:
    best, evidence = 0.0, {}
    src = token_set(source["column_name"])
    for hist_source, hist_target, weight in knowledge.get("history", []):
        if hist_target.upper() != target["column_name"].upper():
            continue
        if hist_source.upper() == source["column_name"].upper():
            score = 1.0 * weight
        else:
            similarity = jaccard(src, token_set(hist_source))
            score = (0.8 * weight) if similarity >= 0.99 else 0.0
        if score > best:
            best, evidence = score, {"previous_mapping": f"{hist_source} -> {hist_target}"}
    return round(best, 4), evidence


def propose_transformation(source: Dict[str, Any], target: Dict[str, Any], knowledge: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Deterministic transformation from domain rules. Returns (expression, rule title) or (None, None) for direct."""
    col = source["column_name"].lower()
    rule = knowledge.get("rules", {}).get(target["column_name"], {})
    if rule.get("source_values"):
        whens = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in sorted(rule["source_values"].items()))
        return f"CASE UPPER(TRIM({col})) {whens} ELSE NULL END", "business rule decode"
    if rule.get("expression"):
        return rule["expression"].format(col=col), "business rule"
    for t in knowledge.get("transforms", []):
        if t.get("target_column") and t["target_column"].upper() == target["column_name"].upper():
            return t["expression"].format(col=col), "transformation rule"
    for t in knowledge.get("transforms", []):
        if t.get("source_pattern") and source.get("pattern") == t["source_pattern"] \
                and type_family(target["data_type"]) in ("DATE", "TIMESTAMP"):
            return t["expression"].format(col=col), "transformation rule"
    for t in knowledge.get("transforms", []):
        if t.get("semantic_type") and t["semantic_type"] == target.get("semantic_type"):
            return t["expression"].format(col=col), "transformation rule"
    sf, tf = type_family(source["data_type"]), type_family(target["data_type"])
    if sf == "TIMESTAMP" and tf == "DATE":
        return f"CAST({col} AS DATE)", "type conversion"
    if sf == "TEXT" and tf == "DATE" and source.get("date_format"):
        return f"TRY_TO_DATE({col}, '{source['date_format']}')", "type conversion"
    return None, None


def all_features(source: Dict[str, Any], target: Dict[str, Any], knowledge: Dict[str, Any],
                 cosine: Optional[float]) -> Dict[str, Any]:
    transformation, rule = propose_transformation(source, target, knowledge)
    kw, kw_ev = keyword_score(source, target)
    dt, dt_ev = datatype_score(source, target, transformation)
    st, st_ev = statistical_score(source, target, knowledge)
    dm, dm_ev = domain_score(source, target, knowledge)
    cx, cx_ev = context_score(source, target)
    hs, hs_ev = historical_score(source, target, knowledge)
    return {
        "scores": {"semantic": semantic_score(cosine), "keyword": kw, "datatype": dt, "statistical": st,
                   "domain": dm, "context": cx, "historical": hs},
        "evidence": {"semantic": {"cosine": None if cosine is None else round(cosine, 4)}, "keyword": kw_ev,
                     "datatype": dt_ev, "statistical": st_ev, "domain": dm_ev, "context": cx_ev,
                     "historical": hs_ev, "transformation_rule": rule},
        "transformation": transformation,
    }


def mappable_targets(targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Targets a source column can map to; system-derived columns are filled by the STTM, not by sources."""
    return [t for t in targets if t.get("semantic_type") not in SYSTEM_DERIVED]
