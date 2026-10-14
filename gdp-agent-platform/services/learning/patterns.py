"""Decisions become rules (pure): what reviewers keep deciding the same way, across tables and runs, proposed as a
rule the engine applies next time.

Per-column feedback already exists (services/mapping/feedback.py: "this source column maps to that target"). What a
single decision cannot say is the general habit behind several of them. Three habits are learned:

  TRANSFORMATION    the same transformation shape, with the source column as a placeholder, chosen for the same
                    source and target type families (TRY_TO_DATE({col}, 'MM/DD/YYYY') for text to date) in several
                    tables: a TRANSFORMATION_RULE keyed by the type families (and the source pattern, when every
                    case shared one)
  OVERRIDE          reviewers moved the engine's pick for the same source column to the same other target in several
                    runs: a domain-wide MAPPING_PATTERN, so the corrected target ranks first everywhere
  RENAME            a source suffix consistently becomes another target suffix with the same stem (SENT_F to
                    SENT_FLAG, START_DT to START_DATE): a MAPPING_PATTERN the keyword score reads as a synonym

Each candidate carries its evidence (how many decisions, runs and tables, the share of decisions it explains, examples)
and goes to the knowledge inbox; nothing is applied until a steward approves it, ideally after a replay
(services/learning/replay.py) shows it does not make mapping worse.

An event: {run_id, source_table, source_column, source_type, pattern, date_format, target_table, target_column,
target_type, decision, transformation, proposed_target, proposed_transformation}.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.profiling.profiler import type_family

KEPT = ("APPROVED", "MODIFIED", "ALTERNATIVE_TARGET")


def template(transformation: Optional[str], source_column: Optional[str]) -> Optional[str]:
    """The transformation with the source column as {col} (case-insensitive, quoted or not); None without the column."""
    t = re.sub(r"\s+", " ", str(transformation or "").strip().rstrip(";"))
    col = str(source_column or "").strip()
    if not t or not col:
        return None
    out = re.sub(rf'(?i)(?<![\w"]){re.escape(col)}(?![\w"])|"{re.escape(col)}"', "{col}", t)
    out = re.sub(r"(?i)\b(?:[a-z_][a-z0-9_]*\.)\{col\}", "{col}", out)   # o.{col}, src.{col}
    return out if "{col}" in out and out.strip() != "{col}" else None


def _key(*parts: Any) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _suffixes(source: str, target: str) -> Optional[Tuple[str, str, str]]:
    """(stem, source suffix, target suffix) when the two names share a stem and differ in their last part."""
    s, t = source.upper(), target.upper()
    if s == t or "_" not in s or "_" not in t:
        return None
    s_stem, s_suf = s.rsplit("_", 1)
    t_stem, t_suf = t.rsplit("_", 1)
    if s_stem != t_stem or not s_suf or not t_suf or s_suf == t_suf:
        return None
    return s_stem, f"_{s_suf}", f"_{t_suf}"


def learn(events: Iterable[Dict[str, Any]], *, layer: str = "SILVER", min_count: int = 3, min_runs: int = 2,
          min_share: float = 0.6) -> List[Dict[str, Any]]:
    """Rule candidates [{kind, habit, key, title, content, content_json, evidence}] from reviewer decisions."""
    events = [e for e in events if e.get("source_column")]
    kept = [e for e in events if str(e.get("decision") or "").upper() in KEPT and e.get("target_column")]
    out: List[Dict[str, Any]] = []

    # TRANSFORMATION: the same shape for the same type families, in several tables
    by_families: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for e in kept:
        by_families[(type_family(e.get("source_type") or ""), type_family(e.get("target_type") or ""))].append(e)
    for (sf, tf), group in by_families.items():
        shapes: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for e in group:
            shape = template(e.get("transformation"), e.get("source_column"))
            if shape:
                shapes[shape].append(e)
        for shape, hits in shapes.items():
            tables = {(e.get("source_table") or "").upper() for e in hits}
            share = len(hits) / len(group)
            corrected = [e for e in hits if template(e.get("proposed_transformation"), e.get("source_column")) != shape]
            if len(hits) < min_count or len(tables) < 2 or share < min_share or not corrected:
                continue   # rare, one table's quirk, contested, or the engine already proposes it
            patterns = {e.get("pattern") or e.get("date_format") for e in hits}
            pattern = next(iter(patterns)) if len(patterns) == 1 and None not in patterns else None
            content_json = {"expression": shape, "source_family": sf, "target_family": tf, "learned": True}
            if pattern:
                content_json["source_pattern"] = pattern
            out.append(_candidate(
                "TRANSFORMATION_RULE", "TRANSFORMATION", f"learned.transform.{layer}.{_key(sf, tf, shape, pattern)}",
                f"{sf.title()} to {tf.lower()}: {shape}" + (f" (pattern {pattern})" if pattern else ""),
                f"When a {sf.lower()} source column maps to a {tf.lower()} target"
                + (f" and its values look like {pattern}" if pattern else "")
                + f", reviewers transform it as {shape}.", content_json, hits, share, len(corrected)))

    # OVERRIDE: the engine's pick for a source column moved to the same other target in several runs
    by_source: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in kept:
        by_source[str(e["source_column"]).upper()].append(e)
    for source, group in by_source.items():
        overrides = [e for e in group if e.get("proposed_target")
                     and str(e["proposed_target"]).upper() != str(e["target_column"]).upper()]
        if not overrides:
            continue
        target, _ = Counter(str(e["target_column"]).upper() for e in overrides).most_common(1)[0]
        hits = [e for e in group if str(e["target_column"]).upper() == target]
        runs = {e.get("run_id") for e in overrides if str(e["target_column"]).upper() == target}
        share = len(hits) / len(group)
        if len(runs) < min_runs or share < min_share:
            continue
        proposed = Counter(str(e["proposed_target"]).upper() for e in overrides
                           if str(e["target_column"]).upper() == target).most_common(1)[0][0]
        out.append(_candidate(
            "MAPPING_PATTERN", "OVERRIDE", f"learned.override.{layer}.{source}",
            f"{source} maps to {target} (not {proposed})",
            f"In {len(runs)} runs reviewers moved {source} from the engine's {proposed} to {target}; rank {target} first "
            "for it in every table of the domain.",
            {"source_column": source, "target_column": target, "proposed_target": proposed, "overridden": True,
             "learned": True}, hits, share, len(runs)))

    # RENAME: a source suffix becomes another target suffix with the same stem
    renames: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for e in kept:
        found = _suffixes(str(e["source_column"]), str(e["target_column"]))
        if found:
            renames[(found[1], found[2])].append({**e, "_stem": found[0]})
    for (s_suf, t_suf), hits in renames.items():
        stems = {e["_stem"] for e in hits}
        with_suffix = [e for e in kept if str(e["source_column"]).upper().endswith(s_suf)]
        share = len(hits) / len(with_suffix) if with_suffix else 0.0
        corrected = [e for e in hits if str(e.get("proposed_target") or "").upper() != str(e["target_column"]).upper()]
        if len(stems) < min_count or share < min_share or not corrected:
            continue
        out.append(_candidate(
            "MAPPING_PATTERN", "RENAME", f"learned.rename.{layer}.{s_suf}.{t_suf}",
            f"*{s_suf} source columns map to *{t_suf} targets",
            f"Reviewers map a source column ending {s_suf} to the target with the same stem ending {t_suf} "
            f"({', '.join(sorted(stems)[:4])}...).",
            {"source_suffix": s_suf, "target_suffix": t_suf, "learned": True}, hits, share, len(corrected)))
    return out


def _candidate(kind: str, habit: str, key: str, title: str, content: str, content_json: Dict[str, Any],
               hits: List[Dict[str, Any]], share: float, corrected: int) -> Dict[str, Any]:
    evidence = {"decisions": len(hits), "runs": len({e.get("run_id") for e in hits}),
                "tables": len({(e.get("source_table") or "").upper() for e in hits}), "share": round(share, 2),
                "corrected_engine": corrected,
                "examples": [f"{e.get('source_table')}.{e.get('source_column')} -> {e.get('target_column')}"
                             + (f" ({e['transformation']})" if e.get("transformation") else "") for e in hits[:5]]}
    return {"kind": kind, "habit": habit, "key": key, "title": title[:500], "content": content,
            "content_json": {**content_json, "evidence": evidence}, "evidence": evidence}


def apply(knowledge: Dict[str, Any], items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """A mapping knowledge dict (services/mapping/procedures.domain_knowledge) with items added as if approved
    ({kind, content_json}): what a replay compares against the knowledge in use."""
    out = {**knowledge, "glossary": dict(knowledge.get("glossary") or {}), "rules": dict(knowledge.get("rules") or {}),
           "transforms": list(knowledge.get("transforms") or []), "history": list(knowledge.get("history") or []),
           "renames": list(knowledge.get("renames") or []), "avoid": set(knowledge.get("avoid") or set()),
           "notes": list(knowledge.get("notes") or [])}
    for item in items:
        kind, c = str(item.get("kind") or "").upper(), item.get("content_json") or {}
        if kind == "TRANSFORMATION_RULE":
            out["transforms"].insert(0, c)
        elif kind == "MAPPING_PATTERN" and c.get("source_suffix") and c.get("target_suffix"):
            out["renames"].append((str(c["source_suffix"]).upper(), str(c["target_suffix"]).upper()))
        elif kind == "MAPPING_PATTERN" and c.get("rejected_target") and c.get("source_column"):
            out["avoid"].add((str(c["source_column"]).upper(), str(c["rejected_target"]).upper()))
        elif kind == "MAPPING_PATTERN" and c.get("source_column") and c.get("target_column"):
            out["history"].append((str(c["source_column"]), str(c["target_column"]), 1.0))
        elif kind in ("GLOSSARY", "BUSINESS_RULE") and c.get("target_column"):
            out["glossary" if kind == "GLOSSARY" else "rules"][str(c["target_column"]).upper()] = c
    return out
