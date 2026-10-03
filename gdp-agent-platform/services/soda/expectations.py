"""Soda expectations from an approved STTM and optional client rows (pure)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def from_sttm(target_table: str, lines: List[Dict[str, Any]], grain_keys: List[str]) -> List[Dict[str, Any]]:
    checks = [
        {"target_table": target_table, "target_column": None, "check_type": "ROW_COUNT",
         "definition": {"kind": "row_count", "gt": 0}, "severity": "FAIL", "origin": "STTM",
         "requirement": "The mart must not be empty after the load."},
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
        if (line.get("target_datatype") or "").upper().startswith("VARCHAR") and "EMAIL" in (line.get("business_definition") or "").upper():
            checks.append({"target_table": target_table, "target_column": col, "check_type": "CUSTOM",
                           "definition": {"kind": "regex", "pattern": r"^[^@]+@[^@]+\.[^@]+$"},
                           "severity": "WARN", "origin": "DOMAIN_RULE",
                           "requirement": f"{col} should look like an email address."})
    return checks


def from_client(target_table: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Client Excel/CSV rows: attribute, check_type, threshold, severity, requirement."""
    out = []
    for r in rows:
        kind = str(r.get("check_type") or r.get("CHECK_TYPE") or "").upper().replace(" ", "_")
        col = r.get("attribute") or r.get("target_column") or r.get("ATTRIBUTE")
        out.append({
            "target_table": target_table, "target_column": col or None, "check_type": kind or "CUSTOM",
            "definition": {"kind": kind.lower() or "custom", "threshold": r.get("threshold") or r.get("THRESHOLD")},
            "severity": str(r.get("severity") or r.get("SEVERITY") or "WARN").upper(),
            "origin": "CLIENT", "requirement": r.get("requirement") or r.get("REQUIREMENT") or r.get("client_requirement"),
        })
    return out


def _not_null(table: str, column: str, origin: str, requirement: str) -> Dict[str, Any]:
    return {"target_table": table, "target_column": column, "check_type": "NOT_NULL",
            "definition": {"kind": "not_null"}, "severity": "FAIL", "origin": origin, "requirement": requirement}


def render_yaml(model: str, checks: List[Dict[str, Any]]) -> str:
    lines = [f"checks for {model}:"]
    for c in checks:
        d = c["definition"]
        kind = d.get("kind")
        col = c.get("target_column")
        ident = f"  - {kind}"
        if kind == "row_count":
            ident = f"  - row_count > {d.get('gt', 0)}"
        elif kind == "not_null" and col:
            ident = f"  - missing_count({col}) = 0"
        elif kind == "unique" and d.get("columns"):
            cols = ", ".join(d["columns"])
            ident = f"  - duplicate_count({cols}) = 0" if len(d["columns"]) == 1 else f"  - duplicate_count({cols}) = 0"
        elif kind == "accepted_values" and col:
            values = ", ".join(repr(v) for v in d.get("values", []))
            ident = f"  - invalid_count({col}) = 0:\n      valid values: [{values}]"
        elif kind == "regex" and col:
            ident = f"  - invalid_count({col}) = 0:\n      valid regex: {d.get('pattern')}"
        elif kind == "freshness":
            ident = f"  - freshness(<{col or 'LOADED_AT'}>) < {d.get('threshold') or '1d'}"
        name = (c.get("requirement") or "")[:80]
        if name:
            ident += f"\n      name: {name}"
        ident += f"\n      fail: when > 0" if c.get("severity") == "FAIL" and kind not in ("row_count",) else ""
        lines.append(ident)
    return "\n".join(lines) + "\n"
