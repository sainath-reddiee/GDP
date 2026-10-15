"""Normalize Data Quality review payloads before they hit Snowflake."""

from __future__ import annotations

import json
from typing import Any, Dict, List

MAX_BATCH = 500
ALLOWED = frozenset({"APPROVED", "REJECTED", "MODIFIED"})


class DecisionPayloadError(ValueError):
    pass


def parse_decision_payload(raw_json: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(raw_json or "[]")
    except json.JSONDecodeError as exc:
        raise DecisionPayloadError("decisions must be JSON") from exc
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        raise DecisionPayloadError("provide at least one Data Quality decision")
    if len(data) > MAX_BATCH:
        raise DecisionPayloadError(f"at most {MAX_BATCH} decisions per request")

    seen: Dict[str, Dict[str, Any]] = {}
    for i, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise DecisionPayloadError(f"decision {i} must be an object")
        expectation_id = str(raw.get("expectation_id") or "").strip()
        decision = str(raw.get("decision") or "").upper().strip()
        if not expectation_id:
            raise DecisionPayloadError(f"decision {i} needs expectation_id")
        if decision not in ALLOWED:
            raise DecisionPayloadError(f"decision {i} needs APPROVED | REJECTED | MODIFIED")
        justification = raw.get("justification")
        if justification is not None:
            justification = str(justification).strip() or None
        requirement = raw.get("requirement")
        if requirement is not None:
            requirement = str(requirement).strip() or None
        seen[expectation_id] = {
            "expectation_id": expectation_id,
            "decision": decision,
            "justification": justification,
            "requirement": requirement,
            "definition": raw.get("definition"),
            "severity": str(raw.get("severity") or "").upper() if str(raw.get("severity") or "").upper() in ("FAIL", "WARN") else None,
        }
    return list(seen.values())


def stored_status(decision: str) -> str:
    return "APPROVED" if decision == "MODIFIED" else decision


# ---------------------------------------------------------------- regenerating keeps reviewed decisions

REVIEWED = ("APPROVED", "REJECTED")


def _definition(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _same(a: Any, b: Any) -> bool:
    return json.dumps(_definition(a), sort_keys=True, default=str) == json.dumps(_definition(b), sort_keys=True, default=str)


def check_key(table: Any, column: Any, check_type: Any, definition: Any) -> tuple:
    kind = (_definition(definition) or {}).get("kind") if isinstance(_definition(definition), dict) else None
    return (str(table or "").upper(), str(column or "").upper(), str(check_type or "").upper(), str(kind or ""))


def carry_decisions(checks: List[Dict[str, Any]], prior: List[Dict[str, Any]]) -> Dict[str, int]:
    """Regenerated checks take over the reviewer's decision on the check they replace (same table, column, check
    type and kind), so regenerating never sends reviewed work back to PROPOSED. `prior` are the run's current rows
    (CORE.SODA_EXPECTATION_REGISTRY, upper-case keys); `checks` are updated in place with status, definition,
    severity, requirement, reviewer and generated_definition (what the generator proposed, kept for the next time).

      the generator proposes what it proposed when the reviewer decided   the decision, the reviewer's definition
                                                                          (modified or not) and severity are kept
      it proposes something else, and the check was REJECTED              still REJECTED
      it proposes something else, and the check was APPROVED              back to PROPOSED for a new look, with the
                                                                          reviewer's definition when they had
                                                                          modified it (the new proposal otherwise)
    A row decided before generated definitions were kept counts as unmodified when its definition equals the new
    proposal, and as modified otherwise. Returns counts {kept, rejected, review_again}."""
    counts = {"kept": 0, "rejected": 0, "review_again": 0}
    pool: Dict[tuple, List[Dict[str, Any]]] = {}
    for p in prior:
        if str(p.get("STATUS") or "").upper() in REVIEWED and p.get("REVIEWED_BY"):
            pool.setdefault(check_key(p.get("TARGET_TABLE"), p.get("TARGET_COLUMN"), p.get("CHECK_TYPE"),
                                      p.get("CHECK_DEFINITION")), []).append(p)
    per_key: Dict[tuple, int] = {}
    for c in checks:
        k = check_key(c.get("target_table"), c.get("target_column"), c.get("check_type"), c.get("definition"))
        per_key[k] = per_key.get(k, 0) + 1
    for c in checks:
        generated = c.get("definition")
        c["generated_definition"] = generated
        k = check_key(c.get("target_table"), c.get("target_column"), c.get("check_type"), generated)
        candidates = pool.get(k) or []
        match = next((p for p in candidates if _same(p.get("GENERATED_DEFINITION") or p.get("CHECK_DEFINITION"), generated)), None)
        if match is None and len(candidates) == 1 and per_key[k] == 1:
            match = candidates[0]
        if match is None:
            continue
        candidates.remove(match)
        status = str(match["STATUS"]).upper()
        decided = _definition(match.get("CHECK_DEFINITION"))
        before = _definition(match.get("GENERATED_DEFINITION"))
        same_input = _same(before, generated) if before is not None else _same(decided, generated)
        c["reviewed_by"], c["reviewed_at"] = match.get("REVIEWED_BY"), match.get("REVIEWED_AT")
        c["requirement"] = match.get("CLIENT_REQUIREMENT") or c.get("requirement")
        if same_input:
            c.update(status=status, definition=decided, severity=match.get("SEVERITY") or c.get("severity"))
            counts["kept"] += 1
        elif status == "REJECTED":
            c["status"] = "REJECTED"
            counts["rejected"] += 1
        else:
            modified = before is None or not _same(decided, before)
            c.update(status="PROPOSED", definition=decided if modified else generated,
                     severity=match.get("SEVERITY") or c.get("severity"))
            c["reviewed_by"] = c["reviewed_at"] = None
            counts["review_again"] += 1
    return counts
