"""Turn a reviewer decision into a knowledge pattern the next mapping run can see.

The pattern is evidence for the historical score and for Cortex Search. It never
approves a mapping by itself.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def pattern(source_column: str, source_table: str, target_column: Optional[str], target_table: str,
            decision: str, transformation: Optional[str], justification: Optional[str],
            proposed_target: Optional[str]) -> Dict[str, Any]:
    decision = decision.upper()
    overridden = bool(proposed_target and target_column and proposed_target.upper() != target_column.upper())
    title = f"Reviewer {decision.lower()} {source_column}"
    if target_column:
        title += f" as {target_column}"
    bits = [f"{source_table}.{source_column}"]
    if decision == "REJECTED":
        bits.append("was rejected and must not be mapped without a new review")
    else:
        bits.append(f"maps to {target_table}.{target_column}")
    if transformation:
        bits.append(f"transformation {transformation}")
    if overridden:
        bits.append(f"the model had proposed {proposed_target}")
    if justification:
        bits.append(f"justification: {justification}")
    return {
        # keyed by target and source table too: the same column name in another table is a different fact
        "source_reference": f"feedback.{str(target_table).upper()}.{str(source_table).upper()}.{source_column.upper()}",
        "title": title[:500],
        "content": ". ".join(bits) + ".",
        "content_json": {
            "source_column": source_column,
            "source_table": source_table,
            "target_column": target_column,
            "target_table": target_table,
            "decision": decision,
            "transformation": transformation,
            "justification": justification,
            "proposed_target": proposed_target,
            "overridden": overridden,
        },
        "active": decision != "REJECTED",
    }
