"""On-the-fly Cortex rewrite of a generated dbt file."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from services.common.llm import DEFAULT_MODEL

Rows = Callable[..., List[Dict[str, Any]]]

ENHANCE_SCHEMA = {
    "type": "object",
    "required": ["content", "rationale", "summary"],
    "additionalProperties": False,
    "properties": {
        "content": {"type": "string", "description": "Full rewritten file"},
        "rationale": {"type": "string"},
        "summary": {"type": "string"},
    },
}


def enhance_prompt(path: str, content: str, request: str, context: str = "") -> str:
    return (
        "You are a senior analytics engineer. Rewrite this dbt file for Snowflake. "
        "Keep valid dbt Jinja/SQL or YAML. Do not invent source tables that are not in context. "
        "Return the full file plus a short rationale.\n\n"
        f"FILE: {path}\n\n"
        f"ENGINEER REQUEST:\n{request.strip()[:2000]}\n\n"
        f"CONTEXT:\n{(context or 'Approved STTM generated this file.')[:7000]}\n\n"
        f"CURRENT FILE:\n{content[:8000]}\n"
    )


def parse_complete(details: Any, fallback_model: str) -> Dict[str, Any]:
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except ValueError:
            details = {}
    details = details or {}
    output = details.get("structured_output") or details.get("structuredOutput") or []
    raw = output[0].get("raw_message") if output else details.get("choices", [{}])[0].get("messages", "")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {"content": raw, "rationale": "", "summary": "Rewrote file"}
    if not isinstance(raw, dict):
        raw = {"content": str(raw or ""), "rationale": "", "summary": ""}
    return {
        "content": str(raw.get("content") or ""),
        "rationale": str(raw.get("rationale") or ""),
        "summary": str(raw.get("summary") or ""),
        "model": details.get("model") or fallback_model,
        "usage": details.get("usage") or {},
    }


def enhance_file(
    fetch_rows: Rows,
    path: str,
    content: str,
    request: str,
    model: Optional[str] = None,
    context: str = "",
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    chosen = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    assert 1 <= int(max_tokens) <= 16384
    prompt = enhance_prompt(path, content, request, context)
    rows = fetch_rows(
        "SELECT AI_COMPLETE(model => %s, prompt => %s, "
        "model_parameters => PARSE_JSON(%s), response_format => PARSE_JSON(%s), "
        "show_details => TRUE) AS R",
        (
            chosen,
            prompt,
            json.dumps({"temperature": 0.2, "max_tokens": int(max_tokens)}),
            json.dumps({"type": "json", "schema": ENHANCE_SCHEMA}),
        ),
    )
    details = (rows[0] or {}).get("r") if rows else None
    if details is None and rows:
        details = rows[0].get("R")
    parsed = parse_complete(details, chosen)
    if not parsed["content"].strip():
        raise ValueError("Cortex returned an empty file. Try another model or a narrower request.")
    parsed["file_path"] = path
    parsed["prompt"] = request.strip()[:500]
    return parsed
