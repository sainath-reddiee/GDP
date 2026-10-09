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
    if kind == "range" and d.get("min") is None and d.get("max") is None:
        problems.append("a range needs a min, a max or both")
    if kind in ("missing_percent", "duplicate_percent"):
        try:
            if not 0 <= float(d.get("max_percent", -1)) <= 100:
                raise ValueError
        except (TypeError, ValueError):
            problems.append("max_percent must be between 0 and 100")
    if kind == "max_length" and not isinstance(d.get("max"), int):
        problems.append("max length must be a whole number")
    if kind == "freshness" and not re.match(r"^\d+(\.\d+)?\s*[mhd]$", str(d.get("threshold") or "")):
        problems.append("freshness threshold looks like 30m, 6h or 1d")
    if kind == "reference" and not FQN.match(str(d.get("reference_table") or "")):
        problems.append("reference table must be DATABASE.SCHEMA.TABLE")
    if kind == "schema" and not (d.get("required") or d.get("forbidden") or d.get("types")):
        problems.append("a schema check needs required, forbidden or typed columns")
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
