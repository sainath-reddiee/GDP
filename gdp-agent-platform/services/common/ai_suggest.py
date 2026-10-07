"""AI suggestions shown next to rule results: one batched Cortex call per table and stage, cached by fingerprint.

The rules decide; the model only suggests. A suggestion is never applied by itself: the reviewer accepts it (it then
becomes a knowledge rule the engine follows next time) or rejects it (kept as negative evidence, never offered
again for that scope). Any failure returns "no suggestions" and never blocks the stage.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from services.common.audit import record_cost
from services.common.llm import complete_json, model_for
from services.common.sql import clip, insert_rows, rows, variant

DECISIONS = ("ACCEPTED", "REJECTED")


def fingerprint(context: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class Spec:
    """One stage's suggestion contract: prompt, output schema, item key and shape check."""

    def __init__(self, stage: str, version: int, instructions: str, item_schema: Dict[str, Any],
                 key: Callable[[Dict[str, Any]], str], required: List[str]):
        self.stage, self.version, self.instructions = stage, version, instructions
        self.item_schema, self.key, self.required = item_schema, key, required

    @property
    def schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"items": {"type": "array", "items": self.item_schema}},
                "required": ["items"]}

    def prompt(self, context: Dict[str, Any], rejected: List[str]) -> str:
        avoid = ("\nDo not suggest these again (a reviewer rejected them): " + "; ".join(rejected[:40])) if rejected else ""
        return (f"{self.instructions}\n\nOnly suggest where you disagree with or add to the RULE RESULT shown. "
                f"Give a short reason grounded in the evidence shown. Return an empty list when the rules are right."
                f"{avoid}\n\nCONTEXT (JSON):\n{clip(json.dumps(context, default=str), 24000)}")

    def valid(self, item: Any) -> bool:
        return isinstance(item, dict) and all(isinstance(item.get(k), str) and item[k].strip() for k in self.required)


def _cached(session, spec: Spec, scope_key: str, fp: str, model: str) -> Optional[Dict[str, Any]]:
    found = rows(session, """SELECT SUGGESTION_ID, ITEMS, MODEL, CREATED_AT::VARCHAR AS CREATED_AT
                               FROM CORE.AI_SUGGESTION
                              WHERE STAGE = ? AND SCOPE_KEY = ? AND FINGERPRINT = ? AND MODEL = ? AND PROMPT_VERSION = ?
                              ORDER BY CREATED_AT DESC LIMIT 1""",
                 [spec.stage, scope_key, fp, model, spec.version])
    if not found:
        return None
    return {"suggestion_id": found[0]["SUGGESTION_ID"], "items": variant(found[0]["ITEMS"]) or [],
            "model": found[0]["MODEL"], "created_at": found[0]["CREATED_AT"], "cached": True}


def decisions(session, stage: str, scope_key: str) -> Dict[str, Dict[str, Any]]:
    """Latest reviewer verdict per item key for a scope (any run, so a rejection holds across runs)."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows(session, """SELECT ITEM_KEY, DECISION, NOTE, DECIDED_BY, DECIDED_AT::VARCHAR AS DECIDED_AT
                                FROM CORE.AI_SUGGESTION_DECISION WHERE STAGE = ? AND SCOPE_KEY = ?
                             QUALIFY ROW_NUMBER() OVER (PARTITION BY ITEM_KEY ORDER BY DECIDED_AT DESC) = 1""",
                  [stage, scope_key]):
        out[r["ITEM_KEY"]] = {"decision": r["DECISION"], "note": r["NOTE"], "decided_by": r["DECIDED_BY"],
                              "decided_at": r["DECIDED_AT"]}
    return out


def suggest(session, spec: Spec, scope_key: str, context: Dict[str, Any], run_id: Optional[str] = None,
            refresh: bool = False, cached_only: bool = False) -> Dict[str, Any]:
    """Cached suggestions for one scope (usually a table) of one stage, with each item's review status.
    `cached_only` never calls the model: pages show earlier answers without paying for new ones."""
    model = model_for(session)
    fp = fingerprint(context)
    verdicts = decisions(session, spec.stage, scope_key)
    result = None if refresh else _cached(session, spec, scope_key, fp, model)
    if result is None and cached_only:
        return {"suggestion_id": None, "items": [], "model": model, "cached": False, "generated": False,
                "error": None}
    if result is None:
        started = time.time()
        rejected = [k for k, v in verdicts.items() if v["decision"] == "REJECTED"]
        try:
            output, usage, used = complete_json(session, spec.prompt(context, rejected), spec.schema, max_tokens=4000)
        except Exception as exc:
            return {"suggestion_id": None, "items": [], "model": model, "cached": False,
                    "error": f"AI suggestions unavailable: {clip(exc, 300)}"}
        try:  # recorded with or without a run (domain reviews have none)
            record_cost(session, run_id, spec.stage, used, usage, int((time.time() - started) * 1000),
                        tool_calls=1)
        except Exception:
            pass
        items = [i for i in (output.get("items") or []) if spec.valid(i)][:60]
        suggestion_id = str(uuid.uuid4())
        insert_rows(session, "CORE.AI_SUGGESTION",
                    ["SUGGESTION_ID", "STAGE", "SCOPE_KEY", "FINGERPRINT", "MODEL", "PROMPT_VERSION", "ITEMS",
                     "RUN_ID"],
                    ["?", "?", "?", "?", "?", "?::NUMBER", "PARSE_JSON(?)", "NULLIF(?, '')"],
                    [[suggestion_id, spec.stage, scope_key, fp, used or model, spec.version, items, run_id]])
        result = {"suggestion_id": suggestion_id, "items": items, "model": used or model, "cached": False}
    for item in result["items"]:
        item["item_key"] = spec.key(item)
        item["review"] = verdicts.get(item["item_key"])
    result["items"] = [i for i in result["items"] if (i.get("review") or {}).get("decision") != "REJECTED"]
    result.setdefault("error", None)
    result["generated"] = True
    return result


def record_decision(session, spec: Spec, suggestion_id: str, scope_key: str, item: Dict[str, Any], decision: str,
                    run_id: Optional[str], domain_id: Optional[str], note: Optional[str] = None) -> Dict[str, Any]:
    decision = str(decision or "").upper()
    assert decision in DECISIONS, f"decision must be one of {DECISIONS}"
    assert spec.valid(item), "the suggestion item is malformed"
    key = spec.key(item)
    insert_rows(session, "CORE.AI_SUGGESTION_DECISION",
                ["DECISION_ID", "SUGGESTION_ID", "STAGE", "SCOPE_KEY", "ITEM_KEY", "DECISION", "ITEM", "RUN_ID",
                 "DOMAIN_ID", "NOTE"],
                ["?", "?", "?", "?", "?", "?", "PARSE_JSON(?)", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')"],
                [[str(uuid.uuid4()), suggestion_id, spec.stage, scope_key, key, decision,
                  {k: v for k, v in item.items() if k not in ("review", "item_key")}, run_id, domain_id,
                  clip(note, 2000)]])
    return {"item_key": key, "decision": decision}
