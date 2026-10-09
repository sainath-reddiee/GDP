"""SodaCL expectations from the STTM, a client brief, and learned patterns (pure)."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from services.soda.extract import requirement_from_row
from services.common.rules import ends_with_hint
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
        if semantic == "AUDIT_TIMESTAMP" or ends_with_hint(col, "hints.updated_columns"):
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


def _threshold_text(threshold: Any) -> str:
    """{"op": "<", "value": 5} -> "< 5"; {"between": [1, 9]} -> "between 1 and 9"."""
    t = threshold or {}
    if "between" in t:
        return f"between {t['between'][0]} and {t['between'][1]}"
    return f"{t.get('op', '>')} {t.get('value', 0)}"


def _negate(condition: str) -> str:
    op, _, value = condition.partition(" ")
    flipped = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "=": "!=", "!=": "="}.get(op, op)
    return f"{flipped} {value}"


def _yq(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _yaml_check(c: Dict[str, Any]) -> List[str]:
    """One SodaCL v3 check. FAIL checks put the threshold on the check line; WARN checks use an alert
    configuration (`warn: when ...`) because Soda does not allow both on the same check."""
    d = c.get("definition") or {}
    kind = d.get("kind")
    col = c.get("target_column")
    name = (c.get("requirement") or "")[:80]
    warn = str(c.get("severity") or "").upper() == "WARN"
    config: List[str] = []
    metric, fail_line, warn_when = "", "", ""
    if kind == "row_count":
        if d.get("min") is not None and d.get("max") is not None:
            metric, fail_line = "row_count", f"row_count between {d['min']} and {d['max']}"
            warn_when = f"not between {d['min']} and {d['max']}"
        else:
            metric, fail_line, warn_when = "row_count", f"row_count > {d.get('gt', 0)}", f"<= {d.get('gt', 0)}"
    elif kind == "not_null" and col:
        metric, fail_line, warn_when = f"missing_count({col})", f"missing_count({col}) = 0", "> 0"
        if d.get("missing_values"):
            config.append(f"missing values: [{', '.join(repr(v) for v in d['missing_values'])}]")
    elif kind == "missing_percent" and col:
        limit = f"{d.get('max_percent', 0):g}%"
        metric, fail_line, warn_when = f"missing_percent({col})", f"missing_percent({col}) < {limit}", f"> {limit}"
    elif kind == "unique":
        joined = ", ".join(d.get("columns") or ([col] if col else []))
        metric, fail_line, warn_when = f"duplicate_count({joined})", f"duplicate_count({joined}) = 0", "> 0"
    elif kind == "accepted_values" and col:
        metric, fail_line, warn_when = f"invalid_count({col})", f"invalid_count({col}) = 0", "> 0"
        config.append(f"valid values: [{', '.join(repr(v) for v in d.get('values', []))}]")
    elif kind == "regex" and col:
        if d.get("max_invalid_percent"):
            limit = f"{d['max_invalid_percent']:g}%"
            metric, fail_line, warn_when = f"invalid_percent({col})", f"invalid_percent({col}) < {limit}", f"> {limit}"
        else:
            metric, fail_line, warn_when = f"invalid_count({col})", f"invalid_count({col}) = 0", "> 0"
        config.append(f"valid regex: {_yq(d.get('pattern'))}")
    elif kind == "format" and col:
        metric, fail_line, warn_when = f"invalid_count({col})", f"invalid_count({col}) = 0", "> 0"
        config.append(f"valid format: {d.get('format')}")
    elif kind == "range" and col:
        metric, fail_line, warn_when = f"invalid_count({col})", f"invalid_count({col}) = 0", "> 0"
        if d.get("min") is not None:
            config.append(f"valid min: {d['min']}")
        if d.get("max") is not None:
            config.append(f"valid max: {d['max']}")
    elif kind == "max_length" and col:
        metric, fail_line, warn_when = f"invalid_count({col})", f"invalid_count({col}) = 0", "> 0"
        config.append(f"valid max length: {d.get('max')}")
    elif kind == "freshness":
        target = col or "LOADED_AT"
        threshold = d.get("threshold") or "1d"
        metric, fail_line, warn_when = f"freshness({target})", f"freshness({target}) < {threshold}", f"> {threshold}"
    elif kind == "schema":
        body = ["  - schema:", "      fail:"]
        required = ", ".join(d.get("required") or [])
        if required:
            body.append(f"        when required column missing: [{required}]")
        forbidden = ", ".join(d.get("forbidden") or [])
        if forbidden:
            body.append(f"        when forbidden column present: [{forbidden}]")
        types = d.get("types") or {}
        if types:
            body.append("        when wrong column type:")
            for name_, typ in list(types.items())[:12]:
                body.append(f"          {name_}: {typ}")
        if name:
            body.append(f"      name: {name}")
        return body
    elif kind == "failed_rows":
        body = ["  - failed rows:"]
        if name:
            body.append(f"      name: {name}")
        body.append(f"      samples limit: {int(d.get('samples_limit', 20))}")
        if d.get("query"):
            body.append("      fail query: |")
            body += [f"        {line}" for line in str(d["query"]).strip().splitlines()]
        else:
            body.append(f"      fail condition: {d.get('condition')}")
        limit = d.get("max_failed")
        if warn:
            body.append(f"      warn: when > {limit or 0}")
        elif limit:
            body.append(f"      fail: when > {limit}")
        return body
    elif kind == "metric":
        metric_name = re.sub(r"[^A-Za-z0-9_]", "_", str(d.get("name") or "custom_metric")).strip("_").lower() or "custom_metric"
        cond = _threshold_text(d.get("threshold"))
        body = [f"  - {metric_name}:" if warn else f"  - {metric_name} {cond}:"]
        if name:
            body.append(f"      name: {name}")
        if d.get("query"):
            body.append(f"      {metric_name} query: |")
            body += [f"        {line}" for line in str(d["query"]).strip().splitlines()]
        else:
            body.append(f"      {metric_name} expression: {d.get('expression')}")
        if warn:
            body.append(f"      warn: when not {cond}" if cond.startswith("between") else f"      warn: when {_negate(cond)}")
        return body
    elif kind in ("avg", "min", "max", "sum", "stddev") and col:
        cond = _threshold_text(d.get("threshold"))
        metric, fail_line = f"{kind}({col})", f"{kind}({col}) {cond}"
        warn_when = f"not {cond}" if cond.startswith("between") else _negate(cond)
    elif kind == "duplicate_percent" and col:
        limit = f"{float(d.get('max_percent', 0)):g}%"
        metric, fail_line, warn_when = f"duplicate_percent({col})", f"duplicate_percent({col}) < {limit}", f"> {limit}"
    elif kind == "change_over_time":
        low, high = float(d.get("max_decrease_percent", 20)), float(d.get("max_increase_percent", 50))
        cond = f"between -{low:g} and +{high:g}"
        body = [f"  - change for row_count {cond}:" if not warn else "  - change for row_count:"]
        if name:
            body.append(f"      name: {name}")
        if warn:
            body.append(f"      warn: when not {cond}")
        return body
    elif kind == "reference" and col:
        ref_t = d.get("reference_table") or "REF"
        ref_c = d.get("reference_column") or col
        body = [f"  - values in ({col}) must exist in {ref_t} ({ref_c}):"]
        if name:
            body.append(f"      name: {name}")
        return body
    else:
        base = kind or "row_count"
        metric = f"{base}({col})" if col else base
        threshold = d.get("threshold") or ("= 0" if col else "> 0")
        fail_line, warn_when = f"{metric} {threshold}", "> 0"
    if name:
        config.append(f"name: {name}")
    if warn:
        return [f"  - {metric}:"] + [f"      {line}" for line in config] + [f"      warn: when {warn_when}"]
    if not config:
        return [f"  - {fail_line}"]
    return [f"  - {fail_line}:"] + [f"      {line}" for line in config]
