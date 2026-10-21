"""Data quality checks from a client requirement, for any table in the Data Quality Store.

A structured requirement (CSV or JSON rows: column, check type, values, threshold) maps straight to checks; a
free-text brief goes through the same Cortex extractor a Silver run uses (services/soda/extract), grounded on the
table's real columns and what the domain already knows. Either way the checks land in the table's suite draft, so
they are reviewed, backtested and approved (four eyes) like any other change, and nothing runs until then.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.common.sql import rows

FRESHNESS_UNITS = {"m": "m", "min": "m", "mins": "m", "minute": "m", "minutes": "m", "h": "h", "hr": "h", "hrs": "h",
                   "hour": "h", "hours": "h", "d": "d", "day": "d", "days": "d"}


def _severity(value: Any) -> str:
    return "FAIL" if str(value or "").upper() in ("FAIL", "ERROR", "HIGH", "CRITICAL", "BLOCKER") else "WARN"


def _freshness(value: Any) -> Optional[str]:
    """'1d', '24 hours', '30 minutes' -> '1d', '24h', '30m'; None when it is not a duration."""
    m = re.match(r"^\s*<?\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)\s*$", str(value or ""))
    unit = FRESHNESS_UNITS.get(m.group(2).lower()) if m else None
    return f"{m.group(1)}{unit}" if m and unit else None


def _number(value: Any) -> Optional[float]:
    try:
        n = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    return int(n) if n.is_integer() else n


def to_check(req: Dict[str, Any], columns: List[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(raw check for the suite, None) or (None, why it was left out). `req` is services/soda/extract
    requirement_from_row output; `columns` are the table's columns (upper case)."""
    d = dict(req.get("definition") or {})
    kind = str(d.get("kind") or "").lower()
    col = (req.get("target_column") or "").strip().strip('"') or None
    known = {c.upper(): c for c in columns}
    label = col or "table"
    if col:
        if col.upper() not in known:
            return None, f"{col}: not a column of this table"
        col = known[col.upper()]
    out: Dict[str, Any]
    if kind == "not_null":
        out = {"kind": "not_null"}
        if d.get("missing_values"):
            out["missing_values"] = [str(v) for v in d["missing_values"]]
    elif kind == "unique":
        out = {"kind": "unique"}
    elif kind == "accepted_values":
        values = [str(v) for v in d.get("values") or [] if str(v).strip()]
        if not values:
            return None, f"{label}: accepted values were not listed"
        out = {"kind": "accepted_values", "values": values}
    elif kind == "regex":
        out = {"kind": "regex", "pattern": str(d.get("pattern") or "")}
    elif kind == "format":
        out = {"kind": "format", "format": str(d.get("format") or "").lower()}
    elif kind == "range":
        out = {"kind": "range", "min": _number(d.get("min")), "max": _number(d.get("max"))}
        out = {k: v for k, v in out.items() if v is not None or k == "kind"}
    elif kind == "freshness":
        threshold = _freshness(d.get("threshold"))
        if not threshold:
            return None, f"{label}: freshness needs a duration such as 1d or 6h"
        out = {"kind": "freshness", "threshold": threshold}
    elif kind == "row_count":
        gt = _number(d.get("threshold"))
        out = {"kind": "row_count", "gt": int(gt) if isinstance(gt, (int, float)) and gt >= 0 else 0}
        col = None
    elif kind == "reference":
        out = {"kind": "reference", "reference_table": str(d.get("reference_table") or "").upper(),
               "reference_column": str(d.get("reference_column") or col or "").upper()}
    else:
        return None, f"{label}: '{req.get('check_type') or kind}' is not a standard check; add it by hand in SodaCL"
    return {"target_column": col, "severity": _severity(req.get("severity")),
            "requirement": (str(req.get("requirement") or "").strip() or None), "definition": out}, None


def _parts(fqn: str) -> List[str]:
    return [p.strip('"') for p in re.findall(r'"(?:[^"]|"")+"|[^.]+', fqn)]


def table_columns(session, fqn: str) -> List[str]:
    """The table's columns as the caller's role sees them."""
    database, schema, table = _parts(fqn)
    found = rows(session, f"""SELECT COLUMN_NAME FROM "{database.replace('"', '""')}".INFORMATION_SCHEMA.COLUMNS
                              WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION""", [schema, table])
    names = [str(r["COLUMN_NAME"]) for r in found]
    assert names, f"{fqn} has no columns this role can read"
    return names


def generate(session, table: str, text: str, filename: str, user: str, domain_id: Optional[str] = None,
             complete: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """Stage the checks a client requirement asks for into the table's suite draft.
    Returns stage_many's {added, skipped, problems} plus {source, proposed, left_out, model}."""
    from services.catalog import dq, dq_suite
    from services.soda.extract import parse_client_document, requirement_from_row

    fqn = dq._fqn(table)
    columns = table_columns(session, fqn)
    domain = dq._domain_for(session, fqn, domain_id)
    doc = parse_client_document(text, filename)
    model = None
    if doc.get("rows"):
        source = "rows"
        reqs = [requirement_from_row(fqn, r) for r in doc["rows"] if isinstance(r, dict)]
    else:
        source = "brief"
        reqs, model = _extract(session, fqn, doc.get("brief") or "", columns, domain, complete)
    raws, left_out = [], []
    for req in reqs:
        raw, why = to_check(req, list(columns))
        if raw:
            raws.append(raw)
        else:
            left_out.append(why)
    assert raws or left_out, "the requirement did not ask for any checks"
    staged = dq_suite.stage_many(session, fqn, raws, "REQUIREMENT", user, domain) if raws else \
        {"added": 0, "skipped": 0, "problems": []}
    return {**staged, "table": fqn, "source": source, "proposed": len(reqs), "left_out": left_out[:30], "model": model}


def _extract(session, fqn: str, brief: str, columns: List[str], domain: Optional[str],
             complete: Optional[Callable[..., Any]]) -> Tuple[List[Dict[str, Any]], str]:
    from services.common.audit import record_cost, tool_call
    from services.soda.extract import EXTRACT_SCHEMA, extract_prompt, requirement_from_row

    if complete is None:
        from services.common.llm import complete_json as complete
    knowledge: List[str] = []
    skills = ""
    try:
        from services.soda.procedures import _knowledge
        knowledge = _knowledge(session, domain, None, fqn.split(".")[-1], columns) if domain else []
    except Exception:
        knowledge = []
    try:
        from services.knowledge.usage import use_skills
        skills = use_skills(session, ["SODA_SKILL"])
    except Exception:
        skills = ""
    started = time.time()
    with tool_call(session, None, "dq_from_requirement", {"table": fqn, "brief_chars": len(brief)}) as call:
        output, usage, model = complete(session, extract_prompt(brief, fqn, columns, knowledge, skills), EXTRACT_SCHEMA,
                                        stage="SODA")
        record_cost(session, None, "SODA", model, usage, int((time.time() - started) * 1000), tool_calls=1)
        found = [requirement_from_row(fqn, {**r, "origin": "AI"}) for r in output.get("requirements") or []]
        call.summary = f"{len(found)} requirements"
    return found, model

