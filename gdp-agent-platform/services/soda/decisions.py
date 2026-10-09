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
