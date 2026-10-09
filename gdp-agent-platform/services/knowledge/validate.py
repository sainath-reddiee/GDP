"""Shape checks for knowledge content (domain packs, learned rules, registry JSON).

Knowledge arrives from hand-written packs, contract conversion, reviewer decisions and LLM output, so any field can
be missing, null or the wrong type. Every consumer reads it through normalize_content(); a malformed item is
repaired when the intent is clear and dropped otherwise, and never crashes a pipeline stage. validate_pack() is the
strict counterpart used at deploy time so a broken pack fails the deploy instead of a customer's run.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

KNOWLEDGE_TYPES = {"BUSINESS_RULE", "MODEL_DEFINITION", "NAMING_STANDARD", "DBT_PATTERN", "SODA_PATTERN",
                   "TRANSFORMATION_RULE", "MAPPING_PATTERN", "EXCEPTION", "GLOSSARY", "STTM_TEMPLATE",
                   "ONBOARDING_GUIDE", "COLUMN_RULE", "QA_TEST"}


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(v).strip() for v in value if v is not None and str(v).strip()]


def _value_map(value: Any) -> Dict[str, str]:
    """Code decodes: {"A": "ACTIVE"}, [["A", "ACTIVE"]] or [{"source": "A", "target": "ACTIVE"}]."""
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if k is not None and v is not None}
    out: Dict[str, str] = {}
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                out[str(item[0])] = str(item[1])
            elif isinstance(item, dict):
                src = item.get("source", item.get("from", item.get("code")))
                tgt = item.get("target", item.get("to", item.get("value")))
                if src is not None and tgt is not None:
                    out[str(src)] = str(tgt)
    return out


def normalize_content(kind: str, content: Any) -> Optional[Dict[str, Any]]:
    """Repaired copy of a knowledge item's CONTENT_JSON, or None when it is unusable for that kind."""
    if not isinstance(content, dict):
        return None
    item = dict(content)
    if _text(item.get("target_column")):
        item["target_column"] = _text(item["target_column"])
    if kind == "GLOSSARY":
        if not item.get("target_column"):
            return None
        item["synonyms"] = _strings(item.get("synonyms"))
    elif kind == "BUSINESS_RULE":
        if "source_values" in item:
            item["source_values"] = _value_map(item.get("source_values"))
        if "expression" in item:
            item["expression"] = _text(item.get("expression"))
    elif kind == "TRANSFORMATION_RULE":
        for key in ("expression", "transformation", "semantic_type", "source_pattern"):
            if key in item:
                item[key] = _text(item.get(key))
        checks = item.get("soda_checks")
        item["soda_checks"] = [c for c in checks if isinstance(c, dict)] if isinstance(checks, list) else []
    elif kind == "MAPPING_PATTERN":
        if not (_text(item.get("source_column")) and _text(item.get("target_column"))):
            return None
    return item


def validate_pack(pack: Dict[str, Any]) -> List[str]:
    """Problems that would make a domain pack misbehave at run time (empty list when the pack is sound)."""
    from services.mapping.features import rule_expression

    problems: List[str] = []
    domain = pack.get("domain") or {}
    if not _text(domain.get("name")):
        problems.append("domain.name is required")
    for t in pack.get("targets") or []:
        if not _text(t.get("table")):
            problems.append("a target is missing its table name")
        for c in t.get("columns") or []:
            if not _text(c.get("name")):
                problems.append(f"{t.get('table')}: a column is missing its name")
    for i, k in enumerate(pack.get("knowledge") or []):
        kind, key = k.get("type"), k.get("key") or f"knowledge[{i}]"
        if kind not in KNOWLEDGE_TYPES:
            problems.append(f"{key}: unknown knowledge type {kind!r}")
            continue
        content = k.get("content_json")
        if content is None:
            continue
        if not isinstance(content, dict):
            problems.append(f"{key}: content_json must be an object")
            continue
        if kind == "GLOSSARY" and content.get("synonyms") is not None and not isinstance(content["synonyms"], list):
            problems.append(f"{key}: synonyms must be a list")
        if kind == "TRANSFORMATION_RULE" and (content.get("expression") or content.get("transformation")):
            expr = rule_expression(content)
            if not expr:
                problems.append(f"{key}: transformation has no usable SQL with a source column")
            elif "{col}" not in expr and not content.get("target_column"):
                problems.append(f"{key}: a pattern rule must reference {{col}}")
        if kind == "BUSINESS_RULE" and "source_values" in content and not isinstance(content["source_values"], dict):
            problems.append(f"{key}: source_values must be an object of code to value")
    return problems
