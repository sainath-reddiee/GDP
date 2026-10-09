"""Structured Cortex completions. Prompts and schemas are bound, never interpolated into SQL."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from services.common.sql import config_value, rows, variant

DEFAULT_MODEL = "claude-sonnet-4-5"
# Stages an admin can give their own model (LLM_MODEL_BY_STAGE); anything else uses LLM_MODEL.
STAGES = ("PROFILING", "MAPPING", "STTM", "SODA", "QA", "DBT", "KNOWLEDGE", "SUGGESTIONS", "COPILOT", "MODELING")


def resolve_model(default: str, by_stage: Any, stage: Optional[str]) -> str:
    """The stage's own model when the admin set one, otherwise the default."""
    if stage and isinstance(by_stage, dict):
        chosen = str(by_stage.get(stage.upper()) or "").strip()
        if chosen:
            return chosen
    return default or DEFAULT_MODEL


def model_for(session, stage: Optional[str] = None) -> str:
    default = config_value(session, "LLM_MODEL", DEFAULT_MODEL)
    return resolve_model(default, config_value(session, "LLM_MODEL_BY_STAGE", {}) if stage else {}, stage)


def complete_json(session, prompt: str, schema: Dict[str, Any], max_tokens: int = 4096,
                  model: str | None = None, stage: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """AI_COMPLETE with a JSON-schema response format. Returns (object, usage, model); usage carries the
    call's query id so its actual credits can be matched in Snowflake's usage views later."""
    model = model or model_for(session, stage)
    assert 1 <= int(max_tokens) <= 16384, "max_tokens out of range"
    result = rows(
        session,
        "SELECT AI_COMPLETE(model => ?, prompt => ?, "
        f"model_parameters => {{'temperature': 0, 'max_tokens': {int(max_tokens)}}}, "
        "response_format => PARSE_JSON(?), show_details => TRUE) AS R",
        [model, prompt, json.dumps({"type": "json", "schema": schema})],
    )
    details = variant(result[0]["R"])
    try:
        query_id = rows(session, "SELECT LAST_QUERY_ID() AS Q")[0]["Q"]
    except Exception:
        query_id = None
    if not details or not details.get("structured_output"):
        raise AssertionError(f"Cortex ({model}) returned no structured answer; try again or rephrase the request")
    output = details["structured_output"][0]["raw_message"]
    if isinstance(output, str):
        output = json.loads(output)
    usage = dict(details.get("usage") or {})
    usage["query_id"] = query_id
    return output, usage, details.get("model", model)
