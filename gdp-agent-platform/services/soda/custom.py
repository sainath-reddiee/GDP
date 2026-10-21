"""Checks added or edited by a person: validate the definition for its kind before it is stored."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from services.qa.guard import FORBIDDEN
from services.quality.scan import UNSAFE_CONDITION

COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,254}$")
FQN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*$")
CHECK_TYPE = {
    "row_count": "ROW_COUNT", "not_null": "NOT_NULL", "missing_percent": "NOT_NULL", "unique": "UNIQUE",
    "duplicate_percent": "DUPLICATE", "accepted_values": "ACCEPTED_VALUES", "regex": "CUSTOM", "format": "CUSTOM",
    "range": "RANGE", "max_length": "CUSTOM", "freshness": "FRESHNESS", "schema": "SCHEMA",
    "reference": "REFERENCE", "failed_rows": "CUSTOM", "metric": "CUSTOM", "avg": "CUSTOM", "min": "CUSTOM",
    "max": "CUSTOM", "sum": "CUSTOM", "stddev": "CUSTOM", "change_over_time": "ROW_COUNT",
}
NEEDS_COLUMN = {"not_null", "missing_percent", "duplicate_percent", "accepted_values", "regex", "format", "range",
                "max_length", "reference", "avg", "min", "max", "sum", "stddev"}
FORMATS = {"email", "phone number", "uuid", "credit card number", "ipv4", "date eu", "date us", "date iso 8601"}
FRESHNESS = re.compile(r"^(\d+)\s*([mhd])$")   # whole units only: Soda, the GX suite and the kit read 30m, 6h, 1d
COLUMN_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_ ]*(\(\s*\d+(\s*,\s*\d+)?\s*\))?$")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _columns(values: Any, what: str, problems: List[str]) -> List[str]:
    """Plain column names, as written (a scan matches them to the table's columns whatever their case)."""
    out = []
    for v in values or []:
        text = str(v or "").strip()
        if not COLUMN.match(text):
            problems.append(f"{what}: {text or '(empty)'} is not a plain column name")
            continue
        out.append(text)
    return out


def _threshold(t: Any) -> Optional[str]:
    if not isinstance(t, dict):
        return "a threshold is required ({op, value} or {between: [low, high]})"
    if "between" in t:
        b = t["between"]
        if not (isinstance(b, list) and len(b) == 2 and all(isinstance(x, (int, float)) for x in b) and b[0] <= b[1]):
            return "between needs two numbers, low first"
        return None
    if t.get("op") not in ("<", "<=", ">", ">=", "=", "!="):
        return "threshold op must be one of < <= > >= = !="
    if not isinstance(t.get("value"), (int, float)):
        return "threshold value must be a number"
    return None


def validate(check: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """(cleaned check, problems)."""
    problems: List[str] = []
    d = dict(check.get("definition") or {})
    kind = str(d.get("kind") or "").strip()
    col = (check.get("target_column") or "").strip() or None
    if kind not in CHECK_TYPE:
        return {}, [f"unknown check kind {kind!r}"]
    if kind in NEEDS_COLUMN and not col:
        problems.append(f"{kind} needs a column")
    if col and not COLUMN.match(col):
        problems.append("column must be a plain column name")
    severity = str(check.get("severity") or "FAIL").upper()
    if severity not in ("FAIL", "WARN"):
        problems.append("severity must be FAIL or WARN")
    if kind == "accepted_values" and not (isinstance(d.get("values"), list) and d["values"]):
        problems.append("accepted values need at least one value")
    if kind == "regex":
        try:
            re.compile(str(d.get("pattern") or ""))
            if not d.get("pattern"):
                problems.append("a regex pattern is required")
        except re.error as exc:
            problems.append(f"the regex does not compile: {exc}")
    if kind == "format" and str(d.get("format") or "").lower() not in FORMATS:
        problems.append(f"format must be one of {sorted(FORMATS)}")
    if kind == "range":
        if d.get("min") is None and d.get("max") is None:
            problems.append("a range needs a min, a max or both")
        elif any(d.get(k) is not None and not _number(d.get(k)) for k in ("min", "max")):
            problems.append("range min and max must be numbers")
        elif d.get("min") is not None and d.get("max") is not None and d["min"] > d["max"]:
            problems.append("range min must not be above max")
    for key in ("max_percent", "max_invalid_percent"):
        required = key == "max_percent" and kind in ("missing_percent", "duplicate_percent")
        if required or d.get(key) not in (None, ""):
            try:
                d[key] = float(d.get(key, -1))
                if not 0 <= d[key] <= 100:
                    raise ValueError
            except (TypeError, ValueError):
                problems.append(f"{key} must be a number between 0 and 100")
    if kind == "max_length" and not isinstance(d.get("max"), int):
        problems.append("max length must be a whole number")
    if kind == "freshness":
        found = FRESHNESS.match(str(d.get("threshold") or "").strip())
        if found:
            d["threshold"] = f"{int(found.group(1))}{found.group(2)}"
        else:
            problems.append("freshness threshold is a whole number of minutes, hours or days: 30m, 6h or 1d")
    if kind == "reference":
        if not FQN.match(str(d.get("reference_table") or "")):
            problems.append("reference table must be DATABASE.SCHEMA.TABLE")
        if d.get("reference_column"):
            found = _columns([d["reference_column"]], "reference column", problems)
            d["reference_column"] = found[0] if found else d["reference_column"]
    if kind == "unique" and d.get("columns"):
        d["columns"] = _columns(d["columns"], "unique columns", problems)
    if kind == "schema":
        if not (d.get("required") or d.get("forbidden") or d.get("types")):
            problems.append("a schema check needs required, forbidden or typed columns")
        for key in ("required", "forbidden"):
            if d.get(key):
                d[key] = _columns(d[key], f"schema {key}", problems)
        if d.get("types"):
            types = d["types"] if isinstance(d["types"], dict) else {}
            if not types:
                problems.append("schema types must be an object of column -> type")
            cleaned = {}
            for name, typ in types.items():
                found = _columns([name], "schema types", problems)
                if not COLUMN_TYPE.match(str(typ or "").strip()):
                    problems.append(f"schema types: {typ!r} is not a column type")
                elif found:
                    cleaned[found[0]] = str(typ).strip().upper()   # a type, not a name: case does not matter
            d["types"] = cleaned
    if kind == "failed_rows":
        if bool(d.get("condition")) == bool(d.get("query")):
            problems.append("give either a fail condition or a fail query")
        if d.get("condition") and UNSAFE_CONDITION.search(str(d["condition"])):
            problems.append("the condition must be a plain SQL boolean expression")
    if kind == "metric":
        if bool(d.get("expression")) == bool(d.get("query")):
            problems.append("give either an expression or a query")
        if d.get("expression") and UNSAFE_CONDITION.search(str(d["expression"])):
            problems.append("the expression must be a plain SQL aggregate")
        if not re.match(r"^[A-Za-z][A-Za-z0-9_ ]{0,60}$", str(d.get("name") or "custom metric")):
            problems.append("metric name: letters, digits, spaces and underscores")
    if kind in ("metric", "avg", "min", "max", "sum", "stddev"):
        bad = _threshold(d.get("threshold"))
        if bad:
            problems.append(bad)
    for key in ("query",):
        if d.get(key):
            text = str(d[key]).strip().rstrip(";")
            if not re.match(r"^\s*(SELECT|WITH)\b", text, re.IGNORECASE) or FORBIDDEN.search(text) or ";" in text:
                problems.append("the query must be one read-only SELECT")
            d[key] = text
    d["kind"] = kind
    requirement = str(check.get("requirement") or "").strip()[:4000]
    return {"target_column": col, "check_type": CHECK_TYPE[kind], "definition": d, "severity": severity,
            "requirement": requirement or None}, problems
