"""Turn a reviewer decision into a knowledge pattern the next mapping run can see.

The pattern is evidence for the historical score and for Cortex Search. It never
approves a mapping by itself. A rejection of what the model proposed is kept too, as
an anti-pattern ("this source column is not that target"), so the same wrong
candidate is demoted next time instead of being proposed again with full confidence.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def pattern(source_column: str, source_table: str, target_column: Optional[str], target_table: str,
            decision: str, transformation: Optional[str], justification: Optional[str],
            proposed_target: Optional[str], comments: Optional[str] = None) -> Dict[str, Any]:
    decision = decision.upper()
    overridden = bool(proposed_target and target_column and proposed_target.upper() != target_column.upper())
    rejected_target = proposed_target if decision == "REJECTED" else None
    title = f"Reviewer {decision.lower()} {source_column}"
    if target_column:
        title += f" as {target_column}"
    elif rejected_target:
        title += f" (not {rejected_target})"
    bits = [f"{source_table}.{source_column}"]
    if decision == "REJECTED":
        bits.append(f"must not be mapped to {target_table}.{rejected_target}" if rejected_target
                    else "was rejected and must not be mapped without a new review")
    else:
        bits.append(f"maps to {target_table}.{target_column}")
    if transformation:
        bits.append(f"transformation {transformation}")
    if overridden:
        bits.append(f"the model had proposed {proposed_target}")
    if justification:
        bits.append(f"justification: {justification}")
    if comments:
        bits.append(f"reviewer comment: {comments}")
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
            "comments": comments,
            "proposed_target": proposed_target,
            "rejected_target": rejected_target,
            "overridden": overridden,
        },
        # a rejection of a proposal is worth keeping (as an anti-pattern); one with nothing proposed retires the old
        # pattern for the column
        "active": decision != "REJECTED" or bool(rejected_target),
    }


def anti_pattern(content: Dict[str, Any]) -> Optional[tuple]:
    """(source column, rejected target) when a stored pattern records a rejected proposal. Pure."""
    if str(content.get("decision") or "").upper() == "REJECTED" and content.get("rejected_target") \
            and content.get("source_column"):
        return str(content["source_column"]), str(content["rejected_target"])
    return None
