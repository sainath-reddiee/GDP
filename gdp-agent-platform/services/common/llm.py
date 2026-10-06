"""Structured Cortex completions. Prompts and schemas are bound, never interpolated into SQL."""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from services.common.sql import config_value, rows, variant

DEFAULT_MODEL = "claude-sonnet-4-5"


def model_for(session) -> str:
    return config_value(session, "LLM_MODEL", DEFAULT_MODEL)


def complete_json(session, prompt: str, schema: Dict[str, Any], max_tokens: int = 4096,
                  model: str | None = None) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """AI_COMPLETE with a JSON-schema response format. Returns (object, usage, model)."""
    model = model or model_for(session)
    assert 1 <= int(max_tokens) <= 16384, "max_tokens out of range"
    result = rows(
        session,
        "SELECT AI_COMPLETE(model => ?, prompt => ?, "
        f"model_parameters => {{'temperature': 0, 'max_tokens': {int(max_tokens)}}}, "
        "response_format => PARSE_JSON(?), show_details => TRUE) AS R",
        [model, prompt, json.dumps({"type": "json", "schema": schema})],
    )
    details = variant(result[0]["R"])
    if not details or not details.get("structured_output"):
        raise AssertionError(f"Cortex ({model}) returned no structured answer; try again or rephrase the request")
    output = details["structured_output"][0]["raw_message"]
    if isinstance(output, str):
        output = json.loads(output)
    return output, details.get("usage", {}), details.get("model", model)
