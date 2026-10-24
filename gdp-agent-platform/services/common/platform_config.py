"""Platform settings an admin can change from the UI, with validation.

Each setting is a CORE.PLATFORM_CONFIG key (versioned; the newest current row wins). Rules and thresholds have their
own editor (services/common/rules.py); this covers the rest: the Cortex model, cost rates, catalog display lists and
modeling-standard overrides.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from services.common.standard import GOLD, PIPELINE_PRESETS, PRESETS
from services.ops.datadog import DEFAULT_SETTING as DATADOG_DEFAULTS, validate_setting as validate_datadog
from services.knowledge.scout import DEFAULTS as SCOUT_DEFAULTS, validate as validate_scout
from services.source.snaplogic_config import DEFAULT_SETTING as SNAPLOGIC_DEFAULTS, validate_setting as validate_snaplogic

KNOWN_MODELS = [
    "claude-sonnet-4-5", "claude-4-sonnet", "claude-3-7-sonnet", "claude-4-opus", "openai-gpt-4.1",
    "llama4-maverick", "snowflake-llama-3.3-70b", "llama3.1-70b", "mistral-large2",
]
MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,62}$")
CATALOG_KEYS = ("hidden_target_tables", "hidden_target_databases", "hidden_target_schemas", "hidden_target_ids",
                "hidden_domain_names", "strip_tokens")
SETTINGS = ("LLM_MODEL", "LLM_MODEL_BY_STAGE", "RATE_CARD", "CREDIT_PRICE_USD", "CREDITS_PER_MILLION_TOKENS",
            "CATALOG_DISPLAY", "MODELING_STANDARD.GDP", "MODELING_STANDARD.GENERIC",
            "MODELING_STANDARD.GDP.GOLD", "MODELING_STANDARD.GENERIC.GOLD", "SKILL_SCOUT",
            "SNAPLOGIC_SYNC", "DATADOG")
# What the Admin page lists; CATALOG_DISPLAY is still read by the app but no longer edited there, and the skill
# scout has its own editor on the Proposed skills page.
ADMIN_SETTINGS = tuple(k for k in SETTINGS if k not in ("CATALOG_DISPLAY", "SKILL_SCOUT"))
DEFAULTS: Dict[str, Any] = {
    "LLM_MODEL": "claude-sonnet-4-5",
    "LLM_MODEL_BY_STAGE": {},
    "RATE_CARD": {},
    "CREDIT_PRICE_USD": None,
    "CREDITS_PER_MILLION_TOKENS": {"default": 0},
    "CATALOG_DISPLAY": {"hidden_target_tables": [], "hidden_target_databases": [], "hidden_target_schemas": [],
                        "hidden_target_ids": [], "hidden_domain_names": ["GDP"], "strip_tokens": ["GDP"]},
    "MODELING_STANDARD.GDP": {},
    "MODELING_STANDARD.GENERIC": {},
    "MODELING_STANDARD.GDP.GOLD": {},
    "MODELING_STANDARD.GENERIC.GOLD": {},
    "SKILL_SCOUT": SCOUT_DEFAULTS,
    # onboarding writes SnapLogic's config tables too (services/source/snaplogic_config); off until an admin turns it on
    "SNAPLOGIC_SYNC": SNAPLOGIC_DEFAULTS,
    # Datadog logs and monitors for Operations (services/ops/datadog); the keys stay in the host's environment
    "DATADOG": DATADOG_DEFAULTS,
}


def preset_for(key: str) -> dict:
    """The conventions a MODELING_STANDARD.<STD>[.GOLD] setting overrides: the standard's, with the Gold Pipeline's
    gold overlay for a .GOLD key."""
    parts = key.split(".")
    preset = dict(PRESETS[parts[1]])
    if len(parts) > 2 and parts[2] == GOLD:
        preset.update(PIPELINE_PRESETS.get(parts[1], {}).get(GOLD, {}))
    return preset


def validate(key: str, value: Any) -> Tuple[Any, List[str]]:
    """(cleaned value, problems). Nothing is saved while problems remain."""
    if key not in SETTINGS:
        return None, [f"unknown setting {key}"]
    if key == "LLM_MODEL":
        name = str(value or "").strip().lower()
        return name, [] if MODEL_NAME.match(name) else ["model must be a Cortex model name, for example claude-sonnet-4-5"]
    if key == "LLM_MODEL_BY_STAGE":
        from services.common.llm import STAGES

        if not isinstance(value, dict):
            return None, ["stage models must be an object of stage -> model"]
        out, problems = {}, []
        for stage, model in value.items():
            stage = str(stage).strip().upper()
            model = str(model or "").strip().lower()
            if stage not in STAGES:
                problems.append(f"unknown stage {stage}")
            elif not model:
                continue  # inherit the default
            elif not MODEL_NAME.match(model):
                problems.append(f"{stage}: {model} is not a model name")
            else:
                out[stage] = model
        return out, problems
    if key == "RATE_CARD":
        if not isinstance(value, dict):
            return None, ["rate card must be an object of model -> {input, output}"]
        out, problems = {}, []
        for model, rates in value.items():
            model = str(model).strip().lower()
            if not MODEL_NAME.match(model):
                problems.append(f"{model}: not a model name")
                continue
            if not isinstance(rates, dict):
                problems.append(f"{model}: rates must be {{input, output}}")
                continue
            clean = {}
            for side in ("input", "output"):
                raw = rates.get(side)
                if raw in (None, ""):
                    continue
                try:
                    number = float(raw)
                except (TypeError, ValueError):
                    problems.append(f"{model} {side}: must be a number")
                    continue
                if number < 0:
                    problems.append(f"{model} {side}: cannot be negative")
                clean[side] = number
            if clean:
                out[model] = clean
        return out, problems
    if key == "CREDIT_PRICE_USD":
        if value in (None, ""):
            return None, []
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None, ["credit price must be a number"]
        return price, [] if price >= 0 else ["credit price cannot be negative"]
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
    if key == "SKILL_SCOUT":
        return validate_scout(value)
    if key == "SNAPLOGIC_SYNC":
        return validate_snaplogic(value)
    if key == "DATADOG":
        return validate_datadog(value)
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
    preset = preset_for(key)
    if not isinstance(value, dict):
        return None, ["overrides must be an object"]
    problems = [f"unknown convention {k}" for k in value if k not in preset]
    problems += [f"{k} must be a {type(preset[k]).__name__}" for k, v in value.items()
                 if k in preset and v is not None and not isinstance(v, type(preset[k]))]
    return {k: v for k, v in value.items() if v is not None}, problems
