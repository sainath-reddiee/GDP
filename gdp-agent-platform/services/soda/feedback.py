"""Reviewer Soda decisions become domain knowledge for the next run."""

from __future__ import annotations

from typing import Any, Dict, Optional


def pattern(target_table: str, target_column: Optional[str], check_type: str, decision: str,
            requirement: Optional[str], justification: Optional[str],
            definition: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    decision = decision.upper()
    column = target_column or "TABLE"
    title = f"Soda {decision.lower()} {check_type} on {target_table}.{column}"
    bits = [f"{target_table}.{column} {check_type}"]
    if decision == "REJECTED":
        bits.append("was rejected and must not be generated again without a new brief")
    else:
        bits.append("is an approved quality rule")
    if requirement:
        bits.append(f"requirement: {requirement}")
    if justification:
        bits.append(f"justification: {justification}")
    return {
        "source_reference": f"soda.{target_table}.{column}.{check_type}".upper(),
        "title": title[:500],
        "content": ". ".join(bits) + ".",
        "content_json": {
            "target_table": target_table,
            "target_column": target_column,
            "check_type": check_type,
            "decision": decision,
            "requirement": requirement,
            "justification": justification,
            "definition": definition or {},
        },
        "active": decision != "REJECTED",
    }
