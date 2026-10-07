"""Onboarding intent: existing-target reuse vs profile-and-suggest."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional


def intent_key(run_id: str) -> str:
    return f"onboarding.intent.{run_id}"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if len(t) > 1}


def proposed_model_name(tables: List[str]) -> str:
    """Suggested name for a new model from its main source table (editable before registering)."""
    primary = (tables or ["SOURCE"])[0]
    entity = re.sub(r"^(crm_|src_|stg_|raw_)", "", primary, flags=re.I)
    entity = re.sub(r"[^A-Za-z0-9]+", "_", entity).strip("_").upper() or "SOURCE"
    return f"DIM_{entity}" if not entity.startswith(("DIM_", "FCT_")) else entity


def suggest_models(
    profile_columns: Iterable[Dict[str, Any]],
    targets: Iterable[Dict[str, Any]],
    source_tables: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Score existing targets against profiled columns; also propose a new model name."""
    by_table: Dict[str, List[str]] = defaultdict(list)
    for row in profile_columns:
        table = str(row.get("table_name") or row.get("TABLE_NAME") or "").strip()
        col = str(row.get("column_name") or row.get("COLUMN_NAME") or "").strip()
        if table and col:
            by_table[table].append(col)
    for name in source_tables or []:
        by_table.setdefault(str(name), [])
    source_cols = {c for cols in by_table.values() for c in cols}
    source_tokens = set()
    for table, cols in by_table.items():
        source_tokens |= _tokens(table)
        for col in cols:
            source_tokens |= _tokens(col)

    scored: List[Dict[str, Any]] = []
    for target in targets:
        tname = str(target.get("target_table") or target.get("TARGET_TABLE") or "")
        tcols = [str(c) for c in (target.get("columns") or []) if c]
        overlap = {_norm(c) for c in source_cols} & {_norm(c) for c in tcols}
        token_hit = source_tokens & _tokens(tname)
        table_hit = any(
            _norm(src) == _norm(tname) or _norm(src) in _norm(tname) or _norm(tname) in _norm(src)
            for src in by_table
        )
        score = (len(overlap) / max(len(tcols), 1)) * 0.55
        if token_hit:
            score += 0.2
        if table_hit:
            score += 0.35
        if overlap or token_hit:
            scored.append({
                "kind": "existing",
                "target_table": tname,
                "fqn": target.get("fqn") or ".".join(
                    p for p in (
                        target.get("target_database"),
                        target.get("target_schema"),
                        tname,
                    ) if p
                ),
                "domain_name": target.get("domain_name") or target.get("DOMAIN_NAME"),
                "score": round(min(score, 1.0), 3),
                "overlap_columns": sorted(overlap)[:12],
                "reason": (
                    f"{len(overlap)} column name(s) overlap"
                    + (f"; name tokens match {', '.join(sorted(token_hit)[:4])}" if token_hit else "")
                ),
            })
    scored.sort(key=lambda item: item["score"], reverse=True)

    primary = next(iter(by_table), "SOURCE")
    proposed = proposed_model_name([primary])
    return scored[:5] + [{
        "kind": "proposed",
        "target_table": proposed,
        "fqn": proposed,
        "domain_name": None,
        "score": 0.4,
        "overlap_columns": [],
        "reason": f"New model suggested from source table {primary}. Profile first, then register if you keep it.",
    }]
