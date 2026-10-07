"""Rule thresholds and name hints as data, not code.

Every value has a default equal to the platform's long-standing behaviour. A platform admin can override any key
globally (CORE.PLATFORM_CONFIG key RULES) and a domain can override it again (DOMAIN_REGISTRY.CONFIG.rules), so a
company whose columns are named differently tunes the hints instead of the code.

Stage code enters `using(rules_for(...))` once; pure functions read `rule(key)`. Outside a stage (unit tests) the
defaults apply.
"""

from __future__ import annotations

import contextvars
import json
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional

DEFAULTS: Dict[str, Any] = {
    # profiling
    "profile.enum_max_distinct": 20,
    "profile.value_pii_share": 0.8,
    # data quality checks proposed from profiles
    "quality.enum_max": 20,
    "quality.regex_coverage": 0.95,
    "quality.missing_soft_max": 20.0,
    "quality.volume_tolerance": 0.1,
    # relationships, joins and mapping
    "relationships.min_confidence": 0.6,
    "relationships.min_value_overlap": 0.9,
    "joins.ambiguity_margin": 0.1,
    "joins.inner_min_confidence": 0.9,
    "mapping.ambiguity_margin": 0.08,
    # domain detection
    "domain.inferred_min_confidence": 0.3,
    "domain.inherited_min_confidence": 0.2,
    # confidence bands the UI shows (read from the API, never hardcoded in pages)
    "ui.confident": 0.8,
    "ui.weak": 0.45,
    "ui.model_match_strong": 0.5,
    "ui.join_strong": 0.85,
    # name hints: second-line evidence after the values themselves
    "hints.freshness_columns": ["UPDATED", "MODIFIED", "LOAD", "INGEST", "CREATED", "EVENT", "TS", "DATE", "TIME"],
    "hints.identifier_suffixes": ["_ID", "_KEY", "_CODE", "_NO", "_NUM", "_NBR", "ID", "_LID", "_REF"],
    "hints.updated_columns": ["UPDATED_TS", "UPDATED_AT", "LAST_MODIFIED", "MODIFIED_AT", "LOADED_AT", "LOAD_TS",
                              "_LOAD_DATE"],
    "hints.abbreviations": {},  # extra {ABBR: [WORD, ...]} merged over the built-in list
}

_ACTIVE: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("gdp_rules", default=None)


def merged(*layers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """DEFAULTS overlaid with each layer; unknown keys and values of the wrong kind are ignored."""
    out = dict(DEFAULTS)
    for layer in layers:
        for key, value in (layer or {}).items():
            if key not in DEFAULTS or value is None:
                continue
            default = DEFAULTS[key]
            if isinstance(default, bool) or not isinstance(default, (int, float)):
                if isinstance(value, type(default)):
                    out[key] = value
            else:
                try:
                    out[key] = int(float(value)) if isinstance(default, int) else float(value)
                except (TypeError, ValueError):
                    continue
    return out


def active() -> Dict[str, Any]:
    return _ACTIVE.get() or DEFAULTS


def rule(key: str) -> Any:
    return active().get(key, DEFAULTS[key])


def activate(rules: Dict[str, Any]) -> None:
    """Rules for the rest of this call (a stage procedure, or one API request's worker context)."""
    _ACTIVE.set(rules)


def ensure_active(session) -> None:
    """Catalog work outside a run (profiling a schema): platform rules, unless a stage already set them."""
    if _ACTIVE.get() is None:
        try:
            activate(rules_for(session))
        except Exception:
            pass


@contextmanager
def using(rules: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    token = _ACTIVE.set(rules)
    try:
        yield rules
    finally:
        _ACTIVE.reset(token)


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def load_rules(query: Callable[[str, List[Any]], List[Dict[str, Any]]], domain_id: Optional[str] = None
               ) -> Dict[str, Any]:
    """Defaults <- global RULES config <- the domain's rules. `query(sql, params)` returns dict rows with
    upper-case keys (Snowpark) or lower-case keys (API connector); both are read."""
    def first(found: List[Dict[str, Any]], key: str) -> Any:
        if not found:
            return None
        row = found[0]
        return row.get(key) if key in row else row.get(key.lower())

    platform: Dict[str, Any] = {}
    domain: Dict[str, Any] = {}
    try:
        platform = _as_dict(first(query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG "
                                        "WHERE CONFIG_KEY = 'RULES' AND IS_CURRENT", []), "CONFIG_VALUE"))
    except Exception:
        platform = {}
    if domain_id:
        try:
            config = _as_dict(first(query("SELECT CONFIG FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?",
                                          [domain_id]), "CONFIG"))
            domain = _as_dict(config.get("rules"))
        except Exception:
            domain = {}
    return merged(platform, domain)


def rules_for(session, domain_id: Optional[str] = None) -> Dict[str, Any]:
    """Rules for a stage running inside Snowflake."""
    from services.common.sql import rows

    return load_rules(lambda sql, params: rows(session, sql, params), domain_id)


def ui_bands(rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    r = rules or active()
    return {k.split(".", 1)[1]: r[k] for k in DEFAULTS if k.startswith("ui.")}


def ends_with_hint(name: str, key: str) -> bool:
    """Name ends with one of the configured suffix hints (case-insensitive)."""
    upper = str(name or "").upper()
    return any(upper.endswith(str(h).upper()) for h in rule(key) or [])


def contains_hint(name: str, key: str) -> bool:
    upper = str(name or "").upper()
    return any(str(h).upper() in upper for h in rule(key) or [])
