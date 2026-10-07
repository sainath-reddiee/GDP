"""Platform settings an admin can change from the UI, with validation.

Each setting is a CORE.PLATFORM_CONFIG key (versioned; the newest current row wins). Rules and thresholds have their
own editor (services/common/rules.py); this covers the rest: the Cortex model, cost rates, catalog display lists and
modeling-standard overrides.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from services.common.standard import PRESETS

KNOWN_MODELS = [
    "claude-sonnet-4-5", "claude-4-sonnet", "claude-3-7-sonnet", "claude-4-opus", "openai-gpt-4.1",
    "llama4-maverick", "snowflake-llama-3.3-70b", "llama3.1-70b", "mistral-large2",
]
MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,62}$")
CATALOG_KEYS = ("hidden_target_tables", "hidden_target_databases", "hidden_target_schemas", "hidden_target_ids",
                "hidden_domain_names", "strip_tokens")
SETTINGS = ("LLM_MODEL", "CREDITS_PER_MILLION_TOKENS", "CATALOG_DISPLAY", "MODELING_STANDARD.GDP",
            "MODELING_STANDARD.GENERIC")
DEFAULTS: Dict[str, Any] = {
    "LLM_MODEL": "claude-sonnet-4-5",
    "CREDITS_PER_MILLION_TOKENS": {"default": 0},
    "CATALOG_DISPLAY": {"hidden_target_tables": [], "hidden_target_databases": [], "hidden_target_schemas": [],
                        "hidden_target_ids": [], "hidden_domain_names": ["GDP"], "strip_tokens": ["GDP"]},
    "MODELING_STANDARD.GDP": {},
    "MODELING_STANDARD.GENERIC": {},
}


def validate(key: str, value: Any) -> Tuple[Any, List[str]]:
    """(cleaned value, problems). Nothing is saved while problems remain."""
    if key not in SETTINGS:
        return None, [f"unknown setting {key}"]
    if key == "LLM_MODEL":
        name = str(value or "").strip().lower()
        return name, [] if MODEL_NAME.match(name) else ["model must be a Cortex model name, for example claude-sonnet-4-5"]
    if key == "CREDITS_PER_MILLION_TOKENS":
        if not isinstance(value, dict):
            return None, ["rates must be an object of model -> credits per million tokens"]
        out, problems = {}, []
        for model, rate in value.items():
            try:
                rate = float(rate)
            except (TypeError, ValueError):
                problems.append(f"{model}: rate must be a number")
                continue
            if rate < 0:
                problems.append(f"{model}: rate cannot be negative")
            out[str(model).strip()] = rate
        out.setdefault("default", 0.0)
        return out, problems
    if key == "CATALOG_DISPLAY":
        if not isinstance(value, dict):
            return None, ["catalog display must be an object of lists"]
        unknown = sorted(set(value) - set(CATALOG_KEYS))
        out = {}
        for k in CATALOG_KEYS:
            items = value.get(k, DEFAULTS[key][k])
            if not isinstance(items, list):
                return None, [f"{k} must be a list"]
            out[k] = [str(v).strip() for v in items if str(v).strip()]
        return out, [f"unknown list {u}" for u in unknown]
    preset = PRESETS[key.split(".", 1)[1]]
    if not isinstance(value, dict):
        return None, ["overrides must be an object"]
    problems = [f"unknown convention {k}" for k in value if k not in preset]
    problems += [f"{k} must be a {type(preset[k]).__name__}" for k, v in value.items()
                 if k in preset and v is not None and not isinstance(v, type(preset[k]))]
    return {k: v for k, v in value.items() if v is not None}, problems
