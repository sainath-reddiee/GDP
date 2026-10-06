"""AI mapping copilot: one structured Cortex call that reviews columns and proposes decisions.

Pure helpers only (prompt building, validation). The API runs AI_COMPLETE and never writes decisions —
the reviewer applies suggestions explicitly, so every saved mapping is still a human decision.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

ACTIONS = ("APPROVE", "ALTERNATIVE", "NULL")
MAX_COLUMNS = 60

SCHEMA = {
    "type": "object",
    "properties": {"columns": {"type": "array", "items": {"type": "object", "properties": {
        "source_column_id": {"type": "string"},
        "action": {"type": "string", "enum": list(ACTIONS)},
        "target_column": {"type": "string"},
        "transformation": {"type": "string"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    }, "required": ["source_column_id", "action", "target_column", "confidence", "reason"]}}},
    "required": ["columns"],
}


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return "?"


def build_prompt(sources: List[Dict[str, Any]], candidates: Dict[str, List[Dict[str, Any]]],
                 targets: List[Dict[str, Any]], target_table: str, taken: Dict[str, str],
                 instructions: str = "", notes: Iterable[str] = ()) -> str:
    """sources: profile rows; candidates: source_column_id -> ranked candidates; taken: target_column_id -> source_column_id."""
    target_lines = [
        f"- {t['column_name']} {t.get('data_type') or ''}"
        + (" REQUIRED" if not t.get("nullable") else "")
        + (" BUSINESS_KEY" if t.get("is_business_key") else "")
        + (" (already mapped by the reviewer)" if t["target_column_id"] in taken else "")
        + (f": {t['definition']}" if t.get("definition") else "")
        for t in targets
    ]
    blocks = []
    for s in sources[:MAX_COLUMNS]:
        ranked = candidates.get(s["source_column_id"], [])
        options = "; ".join(f"{c['target_column']} {round(float(c['final_score']) * 100)}%" for c in ranked[:3]) or "none"
        samples = s.get("values") or []
        blocks.append(
            f"- id={s['source_column_id']} {s.get('source_table') or ''}.{s['column_name']} {s.get('data_type') or ''}"
            f" null {_pct(s.get('null_percentage'))}, distinct {_pct(s.get('distinct_percentage'))}"
            + (f", semantic {s['semantic_type']}" if s.get("semantic_type") else "")
            + (f", e.g. {', '.join(map(str, samples[:5]))}" if samples else "")
            + (f"\n  description: {s['description']}" if s.get("description") else "")
            + f"\n  scored candidates: {options}"
        )
    prior = "\n".join(f"- {n}" for n in list(notes)[:12])
    return (
        f"You are a senior data engineer reviewing source-to-target column mappings into {target_table}.\n"
        "For each source column decide one action:\n"
        "  APPROVE: map to the best scored candidate (target_column = that candidate).\n"
        "  ALTERNATIVE: map to a different target column from the target list (target_column = its name).\n"
        "  NULL: no target fits; the column is not loaded (target_column = \"\").\n"
        "Rules: use only target columns from the list; never map two source columns to the same target; prefer "
        "covering REQUIRED and BUSINESS_KEY targets; give a Snowflake SQL expression in transformation only when a "
        "cast or cleanup is needed, written against the source column name; confidence is 0-1; reason is one "
        "sentence grounded in names, types, profile statistics or sample values.\n"
        + (f"\nReviewer instructions: {instructions.strip()}\n" if instructions.strip() else "")
        + (f"\nPrior reviewer decisions in this domain:\n{prior}\n" if prior else "")
        + "\nTarget columns:\n" + "\n".join(target_lines)
        + "\n\nSource columns:\n" + "\n".join(blocks)
    )


def normalize(raw: Dict[str, Any], sources: List[Dict[str, Any]], candidates: Dict[str, List[Dict[str, Any]]],
              targets: List[Dict[str, Any]], taken: Dict[str, str]) -> List[Dict[str, Any]]:
    """Validate model output against real ids; resolve target collisions by confidence."""
    by_name = {t["column_name"].upper(): t for t in targets}
    source_ids = {s["source_column_id"] for s in sources}
    out: Dict[str, Dict[str, Any]] = {}
    for item in raw.get("columns") or []:
        sid = str(item.get("source_column_id") or "")
        action = str(item.get("action") or "").upper()
        if sid not in source_ids or action not in ACTIONS or sid in out:
            continue
        confidence = max(0.0, min(1.0, float(item.get("confidence") or 0)))
        suggestion: Dict[str, Any] = {
            "source_column_id": sid, "action": action, "target_column_id": None, "target_column": None,
            "candidate_id": None, "transformation": (item.get("transformation") or "").strip() or None,
            "confidence": round(confidence, 2), "reason": str(item.get("reason") or "").strip(), "note": None,
        }
        if action != "NULL":
            target = by_name.get(str(item.get("target_column") or "").upper())
            if not target:
                continue
            suggestion["target_column_id"] = target["target_column_id"]
            suggestion["target_column"] = target["column_name"]
            match = next((c for c in candidates.get(sid, []) if c["target_column_id"] == target["target_column_id"]), None)
            if match:
                suggestion["candidate_id"] = match["candidate_id"]
                suggestion["action"] = "MODIFY" if suggestion["transformation"] else "APPROVE"
            else:
                suggestion["action"] = "ALTERNATIVE"
        out[sid] = suggestion

    winners: Dict[str, Dict[str, Any]] = {}
    for s in sorted(out.values(), key=lambda x: -x["confidence"]):
        tid = s["target_column_id"]
        if not tid:
            continue
        holder = taken.get(tid)
        if tid in winners or (holder and holder != s["source_column_id"]):
            s["note"] = f"{s['target_column']} is already claimed by another column; review manually"
            s["action"], s["target_column_id"], s["target_column"], s["candidate_id"] = "NULL", None, None, None
            s["confidence"] = min(s["confidence"], 0.3)
            continue
        winners[tid] = s
    return [out[s["source_column_id"]] for s in sources if s["source_column_id"] in out]


def to_decision(s: Dict[str, Any], justification: Optional[str] = None) -> Dict[str, Any]:
    """Map an accepted suggestion onto the SAVE_MAPPING_DECISIONS contract."""
    why = justification or f"AI copilot: {s['reason']}"
    if s["action"] == "NULL":
        return {"decision": "REJECTED", "source_column_id": s["source_column_id"], "business_justification": why}
    if s["action"] == "MODIFY":
        return {"decision": "MODIFIED", "source_column_id": s["source_column_id"], "candidate_id": s["candidate_id"],
                "transformation": s["transformation"], "business_justification": why}
    if s["action"] == "ALTERNATIVE":
        return {"decision": "ALTERNATIVE_TARGET", "source_column_id": s["source_column_id"],
                "target_column_id": s["target_column_id"], "transformation": s["transformation"],
                "business_justification": why}
    return {"decision": "APPROVED", "source_column_id": s["source_column_id"], "candidate_id": s["candidate_id"],
            "business_justification": why}
