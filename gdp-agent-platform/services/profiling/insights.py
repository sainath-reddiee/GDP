"""Insights derived purely from staged profile documents: quality scorecard, suggested checks,
schema drift against the live source, and cross-table relationship inference. No data is read."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

WEIGHTS = {"completeness": 0.35, "uniqueness": 0.25, "validity": 0.25, "freshness": 0.15}
GRADES = ((90, "A"), (75, "B"), (60, "C"), (40, "D"))
MIN_RELATIONSHIP_CONFIDENCE = 0.6
FRESHNESS_COLUMN = re.compile(r"(UPDATED|MODIFIED|LOAD|INGEST|CREATED|EVENT|TS|DATE|TIME)", re.I)


def _cols(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(doc.get("columns") or [])


def _stats(col: Dict[str, Any]) -> Dict[str, Any]:
    return col.get("statistics") or {}


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- quality scorecard


def quality_dimensions(doc: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Completeness, uniqueness and validity (0-100) from a profile; these do not change until re-profiled."""
    cols = _cols(doc)
    if not cols:
        return {"completeness": None, "uniqueness": None, "validity": None}
    nulls = [_num(_stats(c).get("null_percentage")) or 0.0 for c in cols]
    completeness = 100.0 - sum(nulls) / len(nulls)
    if any(c.get("potential_key") for c in cols):
        uniqueness = 100.0
    else:
        uniqueness = max((_num(_stats(c).get("distinct_percentage")) or 0.0) for c in cols)
    conformance = []
    for c in cols:
        if c.get("family") != "TEXT":
            continue
        if _stats(c).get("enum_values"):
            conformance.append(100.0)
            continue
        patterns = c.get("patterns") or []
        total = sum(int(p.get("count") or 0) for p in patterns)
        if total:
            conformance.append(100.0 * int(patterns[0].get("count") or 0) / total)
    validity = sum(conformance) / len(conformance) if conformance else 100.0
    return {k: round(v, 1) for k, v in
            {"completeness": completeness, "uniqueness": uniqueness, "validity": validity}.items()}


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def freshness_score(last_altered: Optional[str], now: Optional[datetime] = None) -> Tuple[Optional[float], Optional[float]]:
    """(score, age in days) from the source table's last change; None when unknown (views)."""
    ts = _parse_ts(last_altered)
    if ts is None:
        return None, None
    age = max(((now or datetime.now(timezone.utc)) - ts).total_seconds() / 86400, 0.0)
    for limit, score in ((1, 100.0), (7, 85.0), (30, 65.0), (90, 40.0)):
        if age <= limit:
            return score, round(age, 1)
    return 20.0, round(age, 1)


def grade(score: Optional[float]) -> str:
    if score is None:
        return "—"
    for floor, letter in GRADES:
        if score >= floor:
            return letter
    return "F"


def scorecard(dimensions: Dict[str, Optional[float]], last_altered: Optional[str] = None,
              now: Optional[datetime] = None) -> Dict[str, Any]:
    fresh, age = freshness_score(last_altered, now)
    dims = {**dimensions, "freshness": fresh}
    present = {k: v for k, v in dims.items() if v is not None}
    weight = sum(WEIGHTS[k] for k in present)
    overall = round(sum(WEIGHTS[k] * v for k, v in present.items()) / weight, 1) if weight else None
    return {"overall": overall, "grade": grade(overall), "dimensions": dims, "age_days": age}


def _regex(shape: str) -> str:
    """Character-class shape (A, a, 9) back to an anchored regex."""
    out = []
    for ch in shape:
        out.append({"A": "[A-Z]", "a": "[a-z]", "9": "[0-9]"}.get(ch, re.escape(ch)))
    return "^" + "".join(out) + "$"


IDENTIFIER_NAME = re.compile(r"(^ID$|_ID$|_KEY$|_CODE$|_NO$|_NUM$|_NBR$|ID$)", re.I)


def _is_identifier(col: Dict[str, Any]) -> bool:
    return col.get("semantic_type") == "IDENTIFIER" or bool(IDENTIFIER_NAME.search(col["column_name"]))


def suggested_checks(doc: Dict[str, Any], limit: int = 20, max_not_null: int = 6) -> List[Dict[str, Any]]:
    """SodaCL checks a reviewer can adopt, derived from what the profile shows to be true today.
    Order: volume, identifier keys, accepted values, formats, freshness, then not-null on complete columns.
    Uniqueness is only proposed for identifier-like keys: on small tables many columns are unique by chance."""
    checks: List[Dict[str, Any]] = [{"check": "row_count > 0", "column": None, "reason": "table must not be empty"}]
    cols = _cols(doc)
    keyed = set()
    for c in cols:
        if c.get("potential_key") and _is_identifier(c):
            name = c["column_name"]
            keyed.add(name)
            checks.append({"check": f"duplicate_count({name}) = 0", "column": name,
                           "reason": "identifier unique in the profiled data (key candidate)"})
            checks.append({"check": f"missing_count({name}) = 0", "column": name, "reason": "key candidate has no nulls"})
    for c in cols:
        name, s = c["column_name"], _stats(c)
        enum = s.get("enum_values")
        if enum and c.get("pii_classification", "NONE") == "NONE" and len(enum) <= 20:
            checks.append({"check": f"invalid_count({name}) = 0", "column": name, "valid_values": [str(v) for v in enum],
                           "reason": f"{len(enum)} distinct values observed"})
            continue
        patterns = c.get("patterns") or []
        total = sum(int(p.get("count") or 0) for p in patterns)
        if c.get("family") == "TEXT" and total and int(patterns[0]["count"]) / total >= 0.95 and len(patterns[0]["pattern"]) <= 40:
            checks.append({"check": f"invalid_percent({name}) < 5%", "column": name,
                           "valid_regex": _regex(patterns[0]["pattern"]),
                           "reason": f"{round(100 * int(patterns[0]['count']) / total)}% match pattern {patterns[0]['pattern']}"})
    stamps = [c for c in cols if c.get("family") in ("TIMESTAMP", "DATE") and FRESHNESS_COLUMN.search(c["column_name"])]
    if stamps:
        checks.append({"check": f"freshness({stamps[0]['column_name']}) < 1d", "column": stamps[0]["column_name"],
                       "reason": "latest change column; adjust the threshold to the load schedule"})
    complete = [c for c in cols if c["column_name"] not in keyed and (_num(_stats(c).get("null_percentage")) or 0) == 0
                and (_stats(c).get("row_count") or 0) > 0]
    for c in complete[:max_not_null]:
        checks.append({"check": f"missing_count({c['column_name']}) = 0", "column": c["column_name"],
                       "reason": "no nulls in the profiled data"})
    return checks[:limit]


def checks_yaml(table: str, checks: Sequence[Dict[str, Any]]) -> str:
    lines = [f"checks for {table}:"]
    for c in checks:
        if c.get("valid_values") is not None:
            values = ", ".join("'" + v.replace("'", "''") + "'" for v in c["valid_values"])
            lines += [f"  - {c['check']}:", f"      valid values: [{values}]"]
        elif c.get("valid_regex"):
            lines += [f"  - {c['check']}:", f"      valid regex: '{c['valid_regex']}'"]
        else:
            lines.append(f"  - {c['check']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- schema drift


def drift(doc: Dict[str, Any], current_columns: Iterable[Tuple[str, str]], current_rows: Optional[int]) -> Dict[str, Any]:
    """What changed in the live table since it was profiled."""
    before = {c["column_name"]: c.get("data_type") for c in _cols(doc)}
    after = dict(current_columns)
    retyped = [{"column": n, "from": before[n], "to": after[n]} for n in before if n in after and before[n] != after[n]]
    rows_before = doc.get("row_count")
    rows: Dict[str, Any] = {"before": rows_before, "after": current_rows, "delta": None, "pct": None}
    if rows_before is not None and current_rows is not None:
        rows["delta"] = int(current_rows) - int(rows_before)
        rows["pct"] = round(100.0 * rows["delta"] / rows_before, 2) if rows_before else None
    out = {
        "added": sorted(n for n in after if n not in before),
        "removed": sorted(n for n in before if n not in after),
        "retyped": retyped,
        "row_count": rows,
        "profiled_at": doc.get("profiled_at"),
    }
    out["schema_changed"] = bool(out["added"] or out["removed"] or out["retyped"])
    out["changed"] = out["schema_changed"] or bool(rows["delta"])
    return out


# ---------------------------------------------------------------- relationships


def _stem(table: str) -> str:
    name = table.upper().split("_")[-1]
    if name.endswith("IES"):
        return name[:-3] + "Y"
    return re.sub(r"(ES|S)$", "", name) if len(name) > 3 else name


def _name_match(child_col: str, parent_table: str, parent_col: str) -> float:
    c, p = child_col.upper(), parent_col.upper()
    if c == p and p != "ID":
        return 0.5
    stem = _stem(parent_table)
    if p == "ID" and c in (f"{stem}_ID", f"{stem}ID"):
        return 0.45
    if p.endswith("_ID") and c.endswith(p) and len(p) >= 5:
        return 0.35
    return 0.0


def _range_within(child: Dict[str, Any], parent: Dict[str, Any]) -> bool:
    cmin, cmax = _stats(child).get("min"), _stats(child).get("max")
    pmin, pmax = _stats(parent).get("min"), _stats(parent).get("max")
    if None in (cmin, cmax, pmin, pmax):
        return False
    nums = [_num(v) for v in (cmin, cmax, pmin, pmax)]
    if None not in nums:
        return nums[2] <= nums[0] and nums[1] <= nums[3]
    return str(pmin) <= str(cmin) and str(cmax) <= str(pmax)


def _values(col: Dict[str, Any]) -> set:
    if col.get("pii_classification", "NONE") != "NONE":
        return set()
    return {str(v.get("value")) for v in (_stats(col).get("frequency_distribution") or []) if v.get("value") is not None}


def infer_relationships(docs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Child.column -> parent.key candidates scored on name, type, value range, distinct counts and value overlap.
    Output matches the ER component's join shape: keys are 'CHILD_COL=PARENT_COL' (or the shared name)."""
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for parent, pdoc in docs.items():
        keys = [c for c in _cols(pdoc) if c.get("potential_key")]
        for child, cdoc in docs.items():
            if child == parent:
                continue
            for k in keys:
                for c in _cols(cdoc):
                    name_score = _name_match(c["column_name"], parent, k["column_name"])
                    if not name_score:
                        continue
                    score, evidence = name_score, [f"name {c['column_name']} ~ {parent}.{k['column_name']}"]
                    if c.get("family") and c.get("family") == k.get("family"):
                        score += 0.15
                        evidence.append(f"same type family ({c['family'].lower()})")
                    if _range_within(c, k):
                        score += 0.15
                        evidence.append("value range inside the parent key range")
                    cd, kd = _stats(c).get("distinct_count"), _stats(k).get("distinct_count")
                    if cd is not None and kd is not None and cd <= kd:
                        score += 0.1
                        evidence.append(f"{cd} distinct values <= {kd} parent keys")
                    overlap = _values(c) & _values(k)
                    if overlap:
                        score += 0.1
                        evidence.append(f"{len(overlap)} shared sample values")
                    score = round(min(score, 1.0), 2)
                    if score < MIN_RELATIONSHIP_CONFIDENCE:
                        continue
                    pair = (child, parent)
                    if pair in best and best[pair]["confidence"] >= score:
                        continue
                    key = c["column_name"] if c["column_name"] == k["column_name"] else f"{c['column_name']}={k['column_name']}"
                    best[pair] = {"left": child, "right": parent, "keys": [key],
                                  "cardinality": "1:1" if c.get("potential_key") else "N:1",
                                  "confidence": score, "evidence": evidence, "source": "profile"}
    # A 1:1 link is found from both sides; keep one direction.
    for (child, parent), join in list(best.items()):
        reverse = best.get((parent, child))
        if reverse and join["cardinality"] == "1:1" and reverse["cardinality"] == "1:1" and child > parent:
            del best[(child, parent)]
    return sorted(best.values(), key=lambda j: (-j["confidence"], j["left"], j["right"]))
