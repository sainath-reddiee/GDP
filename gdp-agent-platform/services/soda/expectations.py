"""SodaCL expectations from the STTM, a client brief, and learned patterns (pure)."""

from __future__ import annotations

from typing import Any, Dict, List

from services.soda.extract import requirement_from_row
from services.sttm.refine import from_transform

FORMATS = {
    "EMAIL": "email", "PHONE": "phone number", "UUID": "uuid",
    "CREDIT_CARD": "credit card number", "IPV4": "ipv4",
}


def from_sttm(target_table: str, lines: List[Dict[str, Any]], grain_keys: List[str]) -> List[Dict[str, Any]]:
    required = [line["target_column"] for line in lines if not line.get("nullable_rule")]
    types = {line["target_column"]: line.get("target_datatype") for line in lines if line.get("target_datatype")}
    checks = [
        {"target_table": target_table, "target_column": None, "check_type": "ROW_COUNT",
         "definition": {"kind": "row_count", "gt": 0}, "severity": "FAIL", "origin": "STTM",
         "requirement": "The mart must not be empty after the load."},
        {"target_table": target_table, "target_column": None, "check_type": "SCHEMA",
         "definition": {"kind": "schema", "required": required, "types": types},
         "severity": "FAIL", "origin": "STTM",
         "requirement": "Required STTM columns must be present on the dataset."},
    ]
    if grain_keys:
        checks.append({"target_table": target_table, "target_column": grain_keys[0] if len(grain_keys) == 1 else None,
                       "check_type": "UNIQUE",
                       "definition": {"kind": "unique", "columns": grain_keys}, "severity": "FAIL",
                       "origin": "STTM", "requirement": "Business key of the dimension must be unique."})
        for key in grain_keys:
            checks.append(_not_null(target_table, key, "STTM", "Business key cannot be missing."))
    for line in lines:
        col = line["target_column"]
        if not line.get("nullable_rule") and col not in grain_keys:
            checks.append(_not_null(target_table, col, "STTM", f"{col} is required by the STTM."))
        if line.get("accepted_values"):
            checks.append({"target_table": target_table, "target_column": col, "check_type": "ACCEPTED_VALUES",
                           "definition": {"kind": "accepted_values", "values": list(line["accepted_values"])},
                           "severity": "FAIL", "origin": "STTM",
                           "requirement": f"{col} must be one of {line['accepted_values']}."})
        semantic = (line.get("semantic_type") or "").upper()
        definition = (line.get("business_definition") or "")
        fmt = FORMATS.get(semantic) or _infer_format(col, definition)
        if fmt:
            checks.append({"target_table": target_table, "target_column": col, "check_type": "CUSTOM",
                           "definition": {"kind": "format", "format": fmt}, "severity": "WARN",
                           "origin": "DOMAIN_RULE",
                           "requirement": f"{col} should match Soda valid format '{fmt}'."})
        rng = line.get("range_rule") or {}
        if isinstance(rng, dict) and any(rng.get(k) is not None for k in ("min", "max", "valid_min", "valid_max")):
            checks.append({"target_table": target_table, "target_column": col, "check_type": "RANGE",
                           "definition": {"kind": "range",
                                          "min": rng.get("min", rng.get("valid_min")),
                                          "max": rng.get("max", rng.get("valid_max"))},
                           "severity": "FAIL", "origin": "STTM",
                           "requirement": f"{col} must stay within the STTM range."})
        if semantic == "AUDIT_TIMESTAMP" or col.upper() in {"LOADED_AT", "UPDATED_AT", "EFFECTIVE_FROM"}:
            checks.append({"target_table": target_table, "target_column": col, "check_type": "FRESHNESS",
                           "definition": {"kind": "freshness", "threshold": "1d"}, "severity": "WARN",
                           "origin": "DOMAIN_RULE",
                           "requirement": f"{col} should be fresher than one day."})
        checks.extend(from_transform(target_table, line))
    return checks


def without_rejected(checks: List[Dict[str, Any]], rejected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop previously rejected checks unless a new client row reintroduces them."""
    skip = {(r.get("check_type"), r.get("target_column")) for r in rejected}
    return [c for c in checks
            if c.get("origin") == "CLIENT" or (c.get("check_type"), c.get("target_column")) not in skip]


def render_check(check: Dict[str, Any]) -> str:
    return "\n".join(_yaml_check(check))


def _infer_format(column: str, definition: str) -> str | None:
    text = f"{column} {definition}".upper()
    if "EMAIL" in text:
        return "email"
    if "PHONE" in text or "TEL" in text:
        return "phone number"
    if "UUID" in text:
        return "uuid"
    return None


def from_client(target_table: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [requirement_from_row(target_table, r) for r in rows]


def merge_checks(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for group in groups:
        for check in group:
            key = (check.get("check_type"), check.get("target_column"),
                   check.get("definition", {}).get("kind"),
                   str(check.get("definition", {}).get("values") or ""),
                   str(check.get("definition", {}).get("format") or ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(check)
    return out


def _not_null(table: str, column: str, origin: str, requirement: str) -> Dict[str, Any]:
    return {"target_table": table, "target_column": column, "check_type": "NOT_NULL",
            "definition": {"kind": "not_null"}, "severity": "FAIL", "origin": origin, "requirement": requirement}


def render_yaml(model: str, checks: List[Dict[str, Any]]) -> str:
    lines = [f"checks for {model}:"]
    for c in checks:
        if str(c.get("status") or "").upper() == "REJECTED":
            continue
        lines.extend(_yaml_check(c))
    return "\n".join(lines) + "\n"


def _yaml_check(c: Dict[str, Any]) -> List[str]:
    d = c.get("definition") or {}
    kind = d.get("kind")
    col = c.get("target_column")
    name = (c.get("requirement") or "")[:80]
    warn = str(c.get("severity") or "").upper() == "WARN"
    body: List[str] = []
    if kind == "row_count":
        if d.get("min") is not None and d.get("max") is not None:
            body.append(f"  - row_count between {d['min']} and {d['max']}")
        else:
            body.append(f"  - row_count > {d.get('gt', 0)}")
    elif kind == "not_null" and col:
        body.append(f"  - missing_count({col}) = 0")
        if d.get("missing_values"):
            values = ", ".join(repr(v) for v in d["missing_values"])
            body.append(f"      missing values: [{values}]")
    elif kind == "unique":
        cols = d.get("columns") or ([col] if col else [])
        joined = ", ".join(cols)
        body.append(f"  - duplicate_count({joined}) = 0")
    elif kind == "accepted_values" and col:
        values = ", ".join(repr(v) for v in d.get("values", []))
        body.append(f"  - invalid_count({col}) = 0:")
        body.append(f"      valid values: [{values}]")
    elif kind == "regex" and col:
        body.append(f"  - invalid_count({col}) = 0:")
        body.append(f"      valid regex: {d.get('pattern')}")
    elif kind == "format" and col:
        body.append(f"  - invalid_count({col}) = 0:")
        body.append(f"      valid format: {d.get('format')}")
    elif kind == "range" and col:
        body.append(f"  - invalid_count({col}) = 0:")
        if d.get("min") is not None:
            body.append(f"      valid min: {d['min']}")
        if d.get("max") is not None:
            body.append(f"      valid max: {d['max']}")
    elif kind == "freshness":
        body.append(f"  - freshness({col or 'LOADED_AT'}) < {d.get('threshold') or '1d'}")
    elif kind == "schema":
        required = ", ".join(d.get("required") or [])
        body.append("  - schema:")
        body.append("      fail:")
        if required:
            body.append(f"        when required column missing: [{required}]")
        types = d.get("types") or {}
        if types:
            body.append("        when wrong column type:")
            for name_, typ in list(types.items())[:12]:
                body.append(f"          {name_}: {typ}")
    elif kind == "reference" and col:
        ref_t = d.get("reference_table") or "REF"
        ref_c = d.get("reference_column") or col
        body.append(f"  - values in ({col}) must exist in {ref_t} ({ref_c})")
    else:
        metric = kind or "row_count"
        ident = f"  - {metric}({col}) = 0" if col else f"  - {metric} > 0"
        if d.get("threshold"):
            ident = f"  - {metric}({col}) {d['threshold']}" if col else f"  - {metric} {d['threshold']}"
        body.append(ident)
    if name:
        last = body[-1]
        if last.endswith(":"):
            body.append(f"      name: {name}")
        else:
            body[-1] = last + ":"
            body.append(f"      name: {name}")
    if warn and kind not in ("freshness", "schema"):
        body.append("    warn: when > 0")
    return body
