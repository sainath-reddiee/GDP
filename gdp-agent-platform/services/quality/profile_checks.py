"""Dataset-aware checks for a target model, derived from the staged profiles of the source columns it is built from.

Each check uses the neutral shape of CONTRACT.SODA_EXPECTATION_REGISTRY (check_type plus a `definition` with a `kind`)
and carries the evidence it was derived from, so a reviewer sees why a threshold was chosen. Rules:

- Evidence only flows through a mapping that preserves values: DIRECT lines carry every rule; trivially wrapped
  lines (TRIM, CAST, NULLIF...) carry completeness, length and numeric bounds; anything else carries nothing.
- Profile-derived checks are WARN unless they protect the load itself (truncation into a shorter target column,
  hub/spoke referential integrity). The STTM and the client stay the authority for FAIL rules.
- A rule the STTM already states is not duplicated; the STTM check gets the profile evidence instead, including
  values the source holds that the STTM list does not allow.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

ENUM_MAX = 20
REGEX_COVERAGE = 0.95
MISSING_SOFT_MAX = 20.0
VOLUME_TOLERANCE = 0.1
WRAPPERS = re.compile(r"\b(TRIM|LTRIM|RTRIM|UPPER|LOWER|INITCAP|NULLIF|CAST|TRY_CAST|TO_VARCHAR|TO_CHAR|TO_NUMBER|"
                      r"TRY_TO_NUMBER|TO_DECIMAL|TRY_TO_DECIMAL|TO_DATE|TRY_TO_DATE|TO_TIMESTAMP|TRY_TO_TIMESTAMP|"
                      r"TO_TIMESTAMP_NTZ|TRY_TO_TIMESTAMP_NTZ|AS|VARCHAR|STRING|TEXT|NUMBER|DATE|TIMESTAMP_NTZ|"
                      r"TIMESTAMP|INTEGER|INT|FLOAT|DECIMAL|BOOLEAN)\b", re.I)
VARCHAR_LEN = re.compile(r"^(?:VARCHAR|STRING|TEXT|CHAR|CHARACTER)\s*\(\s*(\d+)\s*\)", re.I)


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def carry(line: Dict[str, Any]) -> str:
    """How much profile evidence survives the mapping: 'all', 'shape' or 'none'."""
    if not line.get("source_column"):
        return "none"
    kind = str(line.get("mapping_type") or "").upper()
    expr = str(line.get("transformation") or "").strip()
    if not expr or kind == "DIRECT" and expr.upper() in {"", line["source_column"].upper()}:
        return "all"
    residue = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\.", " ", expr)
    residue = re.sub(rf'"?\b{re.escape(line["source_column"])}\b"?', " ", residue, flags=re.I)
    residue = WRAPPERS.sub(" ", residue)
    residue = re.sub(r"[\s(),:'\"0-9]|''", "", residue)
    return "shape" if not residue else "none"


def _regex(shape: str) -> str:
    """Profile shape (A upper, a lower, 9 digit, anything else literal) to an anchored regex with run lengths."""
    classes = {"A": "[A-Z]", "a": "[a-z]", "9": "[0-9]"}
    out: List[str] = []
    for run in re.finditer(r"(.)\1*", shape):
        token = classes.get(run.group(1), re.escape(run.group(1)))
        size = len(run.group(0))
        out.append(token if size == 1 else f"{token}{{{size}}}")
    return "^" + "".join(out) + "$"


def _top_pattern(col: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    patterns = col.get("patterns") or []
    total = sum(int(p.get("count") or 0) for p in patterns)
    if not total:
        return None
    best = patterns[0]
    share = int(best.get("count") or 0) / total
    return (str(best.get("pattern") or ""), share) if best.get("pattern") else None


def _target_length(datatype: Optional[str]) -> Optional[int]:
    found = VARCHAR_LEN.match(str(datatype or "").strip())
    return int(found.group(1)) if found else None


def _check(table: str, column: Optional[str], check_type: str, definition: Dict[str, Any], severity: str,
           requirement: str, evidence: str) -> Dict[str, Any]:
    return {"target_table": table, "target_column": column, "check_type": check_type,
            "definition": {**definition, "evidence": evidence}, "severity": severity, "origin": "PROFILE",
            "requirement": requirement}


def _stated(existing: Iterable[Dict[str, Any]]) -> Dict[Tuple[Optional[str], str], Dict[str, Any]]:
    """Rules already present, keyed by (column, intent)."""
    intent = {"not_null": "completeness", "missing_percent": "completeness", "accepted_values": "values",
              "regex": "pattern", "format": "pattern", "range": "range", "unique": "uniqueness",
              "max_length": "length", "row_count": "volume", "reference": "reference"}
    out = {}
    for c in existing:
        kind = (c.get("definition") or {}).get("kind")
        if kind == "row_count" and (c.get("definition") or {}).get("min") is None:
            continue  # "not empty" is not a volume expectation
        if kind in intent:
            col = c.get("target_column")
            out[(str(col).upper() if col else None, intent[kind])] = c
    return out


def profile_checks(target_table: str, lines: List[Dict[str, Any]], docs: Dict[str, Dict[str, Any]],
                   existing: List[Dict[str, Any]] = (), spec: Optional[Dict[str, Any]] = None,
                   driving_table: Optional[str] = None) -> List[Dict[str, Any]]:
    """docs: {SOURCE_TABLE: {"row_count": n, "columns": [profile column]}} in the profiler's document shape.
    Returns new checks; STTM checks in `existing` are annotated in place with the profile evidence."""
    stated = _stated(existing)
    out: List[Dict[str, Any]] = []
    columns = {(t.upper(), c["column_name"].upper()): c for t, d in docs.items() for c in d.get("columns") or []}
    for line in lines:
        target = str(line["target_column"]).upper()
        src_t, src_c = str(line.get("source_table") or "").upper(), str(line.get("source_column") or "").upper()
        col = columns.get((src_t, src_c))
        level = carry(line)
        if not col or level == "none":
            continue
        s = col.get("statistics") or {}
        rows = int(s.get("row_count") or docs.get(src_t, {}).get("row_count") or 0)
        null_pct = _num(s.get("null_percentage"))
        where = f"{src_t}.{src_c}"
        pii = (col.get("pii_classification") or "NONE") != "NONE"

        # completeness
        if rows and null_pct is not None and (target, "completeness") not in stated:
            if null_pct == 0:
                out.append(_check(target_table, target, "NOT_NULL", {"kind": "not_null"}, "WARN",
                                  f"{target} had no missing values in the profiled source.",
                                  f"0 nulls in {rows:,} rows of {where}"))
            elif null_pct <= MISSING_SOFT_MAX:
                limit = min(100.0, math.ceil(null_pct * 1.5 + 1))
                out.append(_check(target_table, target, "NOT_NULL", {"kind": "missing_percent", "max_percent": limit},
                                  "WARN", f"{target} missing share should stay under {limit:g}%.",
                                  f"{null_pct:g}% nulls in {rows:,} rows of {where}; threshold 1.5x observed + 1"))
        elif (target, "completeness") in stated and null_pct is not None:
            _annotate(stated[(target, "completeness")], f"source {where}: {null_pct:g}% nulls in {rows:,} rows"
                      + (" (will fail as profiled)" if null_pct > 0 else ""))

        # target length (truncation protection)
        length = _target_length(line.get("target_datatype"))
        max_len = s.get("max_length")
        if length and max_len is not None and (target, "length") not in stated:
            over = int(max_len) > length
            out.append(_check(target_table, target, "CUSTOM", {"kind": "max_length", "max": length}, "FAIL",
                              f"{target} values must fit VARCHAR({length}).",
                              f"longest source value {int(max_len)} characters in {where}"
                              + ("; EXCEEDS the target length, fix the STTM or the type" if over else "")))

        # numeric bounds
        numeric = s.get("numeric_range") or {}
        low = _num(numeric.get("min"))
        if not pii and low is not None and low >= 0 and (target, "range") not in stated:
            out.append(_check(target_table, target, "RANGE", {"kind": "range", "min": 0}, "WARN",
                              f"{target} should not be negative.",
                              f"observed range {numeric.get('min')} to {numeric.get('max')} in {where}"))
        elif (target, "range") in stated and numeric:
            _annotate(stated[(target, "range")], f"source {where} range {numeric.get('min')} to {numeric.get('max')}")

        if level != "all" or pii:
            continue

        # accepted values
        enum = [str(v) for v in s.get("enum_values") or []]
        if (target, "values") in stated:
            rule = stated[(target, "values")]
            allowed = {str(v).upper() for v in (rule.get("definition") or {}).get("values") or []}
            extra = [v for v in enum if v.upper() not in allowed]
            _annotate(rule, f"source {where} holds {len(enum)} values"
                      + (f"; NOT in the STTM list: {', '.join(extra[:8])}" if extra else "; all within the list"))
        elif 1 < len(enum) <= ENUM_MAX and col.get("cardinality") in ("LOW", "CONSTANT"):
            out.append(_check(target_table, target, "ACCEPTED_VALUES", {"kind": "accepted_values", "values": enum},
                              "WARN", f"{target} should stay within the {len(enum)} observed codes.",
                              f"{len(enum)} distinct values in {rows:,} rows of {where}"))
            continue

        # dominant shape
        top = _top_pattern(col)
        if (col.get("family") == "TEXT" and top and top[1] >= REGEX_COVERAGE and len(top[0]) <= 40
                and (target, "pattern") not in stated and not enum):
            out.append(_check(target_table, target, "CUSTOM",
                              {"kind": "regex", "pattern": _regex(top[0]), "max_invalid_percent": 5}, "WARN",
                              f"{target} should keep the shape {top[0]}.",
                              f"{round(top[1] * 100)}% of {where} match {top[0]}"))

    # volume against the driving source table
    driving = (driving_table or "").upper()
    source_rows = int((docs.get(driving) or {}).get("row_count") or 0)
    if source_rows and (None, "volume") not in stated:
        low_n, high_n = math.floor(source_rows * (1 - VOLUME_TOLERANCE)), math.ceil(source_rows * (1 + VOLUME_TOLERANCE))
        out.append(_check(target_table, None, "ROW_COUNT", {"kind": "row_count", "min": low_n, "max": high_n}, "WARN",
                          f"Row count should stay within {int(VOLUME_TOLERANCE * 100)}% of the driving source.",
                          f"{driving} has {source_rows:,} rows; adjust when the model filters or aggregates"))

    # spoke to hub referential integrity from the domain contract
    spec = spec or {}
    fk, hub = spec.get("hub_fk"), spec.get("hub")
    mapped = {str(l["target_column"]).upper() for l in lines}
    if spec.get("role") == "spoke" and fk and hub and str(fk).upper() in mapped:
        out.append(_check(target_table, str(fk).upper(), "REFERENCE",
                          {"kind": "reference", "reference_table": str(hub).lower(), "reference_column": str(fk).upper()},
                          "FAIL", f"Every {fk} must resolve to a {hub} row.",
                          f"domain contract: {target_table} is a spoke of {hub}"))
    return out


def _annotate(check: Dict[str, Any], note: str) -> None:
    definition = check.setdefault("definition", {})
    prior = definition.get("evidence")
    definition["evidence"] = f"{prior}; {note}" if prior else note
