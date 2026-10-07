"""Cortex / account models the engineer can pick for AI_COMPLETE."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

Execute = Callable[[str], List[Dict[str, Any]]]

CORTEX_INFERENCE = [
    {"name": "claude-sonnet-4-5", "family": "claude", "source": "cortex"},
    {"name": "claude-opus-4-5", "family": "claude", "source": "cortex"},
    {"name": "claude-haiku-4-5", "family": "claude", "source": "cortex"},
    {"name": "claude-4-sonnet", "family": "claude", "source": "cortex"},
    {"name": "claude-4-opus", "family": "claude", "source": "cortex"},
    {"name": "claude-3-7-sonnet", "family": "claude", "source": "cortex"},
    {"name": "claude-3-5-sonnet", "family": "claude", "source": "cortex"},
    {"name": "openai-gpt-5", "family": "openai", "source": "cortex"},
    {"name": "openai-gpt-5-mini", "family": "openai", "source": "cortex"},
    {"name": "llama4-maverick", "family": "llama", "source": "cortex"},
    {"name": "llama4-scout", "family": "llama", "source": "cortex"},
    {"name": "deepseek-r1", "family": "deepseek", "source": "cortex"},
    {"name": "snowflake-arctic", "family": "snowflake", "source": "cortex"},
    {"name": "llama3.1-70b", "family": "llama", "source": "cortex"},
    {"name": "llama3.1-8b", "family": "llama", "source": "cortex"},
    {"name": "llama3.3-70b", "family": "llama", "source": "cortex"},
    {"name": "snowflake-llama-3.3-70b", "family": "llama", "source": "cortex"},
    {"name": "mistral-large2", "family": "mistral", "source": "cortex"},
    {"name": "mistral-7b", "family": "mistral", "source": "cortex"},
    {"name": "openai-gpt-4.1", "family": "openai", "source": "cortex"},
    {"name": "openai-o4-mini", "family": "openai", "source": "cortex"},
]


def _family(name: str) -> str:
    lower = name.lower()
    if "claude" in lower:
        return "claude"
    if "llama" in lower:
        return "llama"
    if "mistral" in lower:
        return "mistral"
    if "openai" in lower or "gpt" in lower:
        return "openai"
    if "deepseek" in lower:
        return "deepseek"
    if "arctic" in lower or lower.startswith("snowflake"):
        return "snowflake"
    return "other"


def parse_show_models(raw: List[Dict[str, Any]], source: str) -> List[Dict[str, str]]:
    out = []
    for row in raw or []:
        item = {str(k).lower(): v for k, v in (row or {}).items()}
        name = str(item.get("name") or item.get("model_name") or item.get("inference_profile_name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "family": _family(name),
            "source": source,
            "kind": str(item.get("type") or item.get("category") or "MODEL"),
        })
    return out


def discover_models(execute: Execute, default: str = "claude-sonnet-4-5") -> Dict[str, Any]:
    warnings: List[str] = []
    found: List[Dict[str, str]] = []
    for sql, source in (
        ("SHOW MODELS IN ACCOUNT", "account"),
        ("SHOW INFERENCE PROFILES IN ACCOUNT", "inference_profile"),
    ):
        try:
            found.extend(parse_show_models(execute(sql) or [], source))
        except Exception as exc:
            warnings.append(f"{sql}: {exc}")
    try:
        rows = execute(
            "SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'LLM_MODEL' AND IS_CURRENT"
        ) or []
        if rows:
            value = rows[0].get("config_value") or rows[0].get("CONFIG_VALUE")
            if isinstance(value, str) and value.startswith('"'):
                value = value.strip('"')
            if value:
                default = str(value)
    except Exception as exc:
        warnings.append(f"PLATFORM_CONFIG LLM_MODEL: {exc}")

    by_name = {m["name"]: m for m in CORTEX_INFERENCE}
    for item in found:
        by_name[item["name"]] = item
    if default not in by_name:
        by_name[default] = {"name": default, "family": _family(default), "source": "config", "kind": "MODEL"}
    models = sorted(by_name.values(), key=lambda m: (m["family"], m["name"]))
    return {"default": default, "models": models, "warnings": warnings}


def parse_allowlist(raw: List[Dict[str, Any]]) -> Optional[List[str]]:
    """CORTEX_MODELS_ALLOWLIST from SHOW PARAMETERS: None when every model is allowed ('All' or unset)."""
    for row in raw or []:
        item = {str(k).lower(): v for k, v in (row or {}).items()}
        if str(item.get("key") or "").upper() != "CORTEX_MODELS_ALLOWLIST":
            continue
        value = str(item.get("value") or "").strip()
        if not value or value.lower() == "all":
            return None
        if value.lower() == "none":
            return []
        return [v.strip().lower() for v in value.split(",") if v.strip()]
    return None


def account_models(execute: Execute, default: str = "") -> Dict[str, Any]:
    """discover_models plus whether the account allows each model (CORTEX_MODELS_ALLOWLIST)."""
    data = discover_models(execute, default or "claude-sonnet-4-5")
    allowlist: Optional[List[str]] = None
    try:
        allowlist = parse_allowlist(execute("SHOW PARAMETERS LIKE 'CORTEX_MODELS_ALLOWLIST' IN ACCOUNT") or [])
    except Exception as exc:
        data["warnings"].append(f"CORTEX_MODELS_ALLOWLIST: {exc}")
    for m in data["models"]:
        m["available"] = allowlist is None or m["name"].lower() in allowlist
    data["allowlist"] = allowlist
    return data
