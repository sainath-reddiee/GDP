"""Dry-run proposed checks against the source data before a reviewer accepts them.

Each column check is evaluated on the source column its STTM line reads (before the transformation), so the
result says whether today's data would pass. One aggregate query per source table; identifiers are validated
and literals quoted, and only read-only SELECTs are produced.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from services.quality.gx import FORMAT_REGEX
from services.quality.profile_checks import carry

SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_PART = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
FQN = re.compile(rf"^{_PART}\.{_PART}\.{_PART}$")  # DB.SCHEMA.TABLE, each part bare or quoted


def _lit(value: Any) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _pattern(value: str) -> Optional[str]:
    """Regex as a single-quoted literal. A $$...$$ literal breaks on the common end anchor: '^...$' + '$$' is '$$$'."""
    return _lit(value) if value else None


def metric_sql(check: Dict[str, Any], column: str) -> Optional[str]:
    """Aggregate over the source table counting rows that violate the check; None when not evaluable."""
    d = check.get("definition") or {}
    kind = d.get("kind")
    c = '"' + str(column).replace('"', '""') + '"'  # exact stored spelling, always quoted
    text = f"TRIM({c}::STRING)"
    if kind in ("not_null", "missing_percent"):
        return f"COUNT_IF({c} IS NULL)"
    if kind == "accepted_values":
        values = d.get("values") or []
        if not values:
            return None
        return f"COUNT_IF({c} IS NOT NULL AND {text} NOT IN ({', '.join(_lit(v) for v in values)}))"
    if kind in ("regex", "format"):
        raw = d.get("pattern") if kind == "regex" else FORMAT_REGEX.get(str(d.get("format") or "").lower())
        quoted = _pattern(raw) if raw else None
        return f"COUNT_IF({c} IS NOT NULL AND NOT REGEXP_LIKE({text}, {quoted}))" if quoted else None
    if kind == "range":
        parts = []
        if d.get("min") is not None:
            parts.append(f"TRY_TO_DOUBLE({c}::STRING) < {float(d['min'])}")
        if d.get("max") is not None:
            parts.append(f"TRY_TO_DOUBLE({c}::STRING) > {float(d['max'])}")
        return f"COUNT_IF({' OR '.join(parts)})" if parts else None
    if kind == "max_length":
        return f"COUNT_IF(LENGTH({c}::STRING) > {int(d['max'])})" if d.get("max") is not None else None
    if kind == "unique":
        cols = d.get("columns") or [check.get("target_column")]
        return f"COUNT({c}) - COUNT(DISTINCT {c})" if len(cols) == 1 else None
    return None


def plan(checks: List[Dict[str, Any]], lines: List[Dict[str, Any]], sources: Dict[str, str],
         driving_table: Optional[str] = None) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """sources: {SOURCE_TABLE: 'DB.SCHEMA.TABLE'}. Returns ({table: sql}, [slot]) where each slot points a check at
    a metric alias of one table query. Checks that cannot be evaluated get a slot with a reason."""
    by_target = {str(l["target_column"]).upper(): l for l in lines}
    metrics: Dict[str, List[str]] = {}
    slots: List[Dict[str, Any]] = []
    for i, check in enumerate(checks):
        kind = (check.get("definition") or {}).get("kind")
        if kind == "row_count":
            table = (driving_table or "").upper()
            if table in sources:
                metrics.setdefault(table, [])
                slots.append({"index": i, "table": table, "alias": "N", "kind": kind})
            else:
                slots.append({"index": i, "reason": "no driving source table to compare"})
            continue
        if not check.get("target_column"):
            slots.append({"index": i, "reason": "table-level check; evaluate after the model is built"})
            continue
        line = by_target.get(str(check.get("target_column") or "").upper())
        level = carry(line) if line else "none"
        if level == "none" or (level == "shape" and kind in ("accepted_values", "regex", "format", "unique")):
            slots.append({"index": i, "reason": "derived column; evaluate after the model is built"})
            continue
        table, column = str(line.get("source_table") or "").upper(), str(line.get("source_column") or "")
        if table not in sources or not column:
            slots.append({"index": i, "reason": "source table not landed"})
            continue
        sql = metric_sql(check, column)
        if not sql:
            slots.append({"index": i, "reason": f"{kind} is checked on the built model only"})
            continue
        alias = f"M{len(metrics.setdefault(table, []))}"
        metrics[table].append(f"{sql} AS {alias}")
        slots.append({"index": i, "table": table, "alias": alias, "kind": kind, "column": column})
    queries = {}
    for table, parts in metrics.items():
        fqn = sources[table]
        if not FQN.match(fqn):
            continue
        queries[table] = "SELECT " + ", ".join(["COUNT(*) AS N"] + parts) + f" FROM {fqn}"
    return queries, slots


def evaluate(check: Dict[str, Any], slot: Dict[str, Any], row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Pass/fail for one check from its table's aggregate row."""
    base = {"index": slot["index"], "check_type": check.get("check_type"), "target_column": check.get("target_column")}
    if slot.get("reason") or row is None:
        return {**base, "status": "NOT_EVALUATED", "detail": slot.get("reason") or "query failed"}
    d = check.get("definition") or {}
    total = int(row.get("N") or 0)
    observed = int(row.get(slot["alias"]) or 0)
    kind = slot["kind"]
    if kind == "row_count":
        low = d.get("min") if d.get("min") is not None else int(d.get("gt", 0)) + 1
        high = d.get("max")
        ok = total >= int(low) and (high is None or total <= int(high))
        return {**base, "status": "PASS" if ok else "FAIL", "observed": total,
                "detail": f"{total:,} source rows; expected {low:,} to {high:,}" if high is not None
                else f"{total:,} source rows; expected at least {low:,}"}
    percent = round(100.0 * observed / total, 4) if total else 0.0
    if kind == "missing_percent":
        ok = percent <= float(d.get("max_percent") or 0)
    elif kind == "regex" and d.get("max_invalid_percent") is not None:
        ok = percent <= float(d["max_invalid_percent"])
    else:
        ok = observed == 0
    return {**base, "status": "PASS" if ok else "FAIL", "observed": observed, "percent": percent,
            "detail": f"{observed:,} of {total:,} source rows violate ({percent:g}%) on {slot['table']}.{slot['column']}"}
