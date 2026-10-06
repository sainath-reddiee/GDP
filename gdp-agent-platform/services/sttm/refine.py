"""Natural-language transform refine, CSV contract, and Soda hints from SQL (pure)."""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional

REFINE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "transformation": {"type": "string"},
        "rationale": {"type": "string"},
        "dbt_notes": {"type": "string"},
        "soda_checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "check_type": {"type": "string"},
                    "severity": {"type": "string"},
                    "requirement": {"type": "string"},
                    "valid_values": {"type": "array", "items": {"type": "string"}},
                    "valid_format": {"type": ["string", "null"]},
                    "valid_regex": {"type": ["string", "null"]},
                    "valid_min": {"type": ["number", "null"]},
                    "valid_max": {"type": ["number", "null"]},
                },
                "required": ["check_type", "severity", "requirement"],
            },
        },
    },
    "required": ["transformation", "rationale"],
}

CSV_COLUMNS = [
    "target_column", "target_datatype", "mapping_type", "source_table", "source_column",
    "source_datatype", "transformation", "prompt", "rationale", "business_definition",
    "soda_checks",
]

THEN_LITERAL = re.compile(r"THEN\s+'([^']*)'", re.IGNORECASE)
DATE_CAST = re.compile(r"\b(TRY_TO_DATE|TO_DATE|CAST\s*\(.+\s+AS\s+DATE)\b", re.IGNORECASE)
EMAIL_HINT = re.compile(r"\bEMAIL\b", re.IGNORECASE)


def refine_prompt(context: Dict[str, Any], instruction: str) -> str:
    profile = context.get("profile") or {}
    samples = profile.get("samples") or []
    if isinstance(samples, list):
        sample_text = ", ".join(str(s) for s in samples[:8]) or "(none)"
    else:
        sample_text = str(samples)[:400]
    rules = context.get("prior_rules") or []
    rule_text = "\n".join(f"- {r}" for r in rules[:6]) or "- none"
    source = context.get("source_column") or "src_col"
    return (
        "You write one Snowflake SQL scalar expression that maps a source column onto a target column.\n"
        "Return only an expression, never a statement. Allowed: CAST, TRY_TO_DATE, TRY_TO_NUMBER, TRIM, "
        "UPPER, LOWER, INITCAP, COALESCE, NULLIF, CASE, IFF, CONCAT, ||, LEFT, RIGHT, SUBSTR, REGEXP_REPLACE, "
        "MD5, TO_VARCHAR, TO_BOOLEAN. Forbidden: DDL, DML, CALL, COPY, GRANT, subqueries that write.\n"
        f"Source column identifier to reference: {source}\n"
        f"Source table: {context.get('source_table') or '—'}  source type: {context.get('source_datatype') or '—'}\n"
        f"Target column: {context.get('target_column')}  target type: {context.get('target_datatype') or '—'}\n"
        f"Business definition: {context.get('business_definition') or '—'}\n"
        f"Current transformation: {context.get('current_transformation') or '(direct / none)'}\n"
        f"Profile null%: {profile.get('null_percentage')}  distinct%: {profile.get('distinct_percentage')}  "
        f"cardinality: {profile.get('cardinality')}  semantic: {profile.get('semantic_type')}\n"
        f"Profile description: {profile.get('description') or '—'}\n"
        f"Sample values: {sample_text}\n"
        f"Patterns: {profile.get('patterns') or '—'}\n"
        f"Prior accepted rules for this pack:\n{rule_text}\n"
        + (f"Domain rules and contract (follow them):\n{context['domain_rules']}\n" if context.get("domain_rules") else "")
        + f"Engineer instruction:\n{(instruction or '').strip()[:2000]}\n"
        "Also propose SodaCL checks implied by the new expression (accepted values, date format, not-null).\n"
        "dbt_notes should say how the mart SQL should use the expression."
    )


def reusable_expression(sql: str, source_column: Optional[str]) -> str:
    """Swap the concrete source column for {col} so later mapping runs can reuse the rule."""
    if not sql or not source_column:
        return sql
    return re.sub(rf"\b{re.escape(source_column)}\b", "{col}", sql, flags=re.IGNORECASE)


def case_literals(expression: Optional[str]) -> List[str]:
    return list(dict.fromkeys(THEN_LITERAL.findall(expression or "")))


def from_transform(target_table: str, line: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic Soda checks implied by a transformation expression."""
    expr = line.get("transformation") or ""
    col = line.get("target_column")
    if not expr or not col:
        return []
    table = target_table
    checks: List[Dict[str, Any]] = []
    values = case_literals(expr)
    if values and not line.get("accepted_values"):
        checks.append({
            "target_table": table, "target_column": col, "check_type": "ACCEPTED_VALUES",
            "definition": {"kind": "accepted_values", "values": values},
            "severity": "FAIL", "origin": "TRANSFORM",
            "requirement": f"{col} is decoded by CASE; values must stay in {values}.",
        })
    if DATE_CAST.search(expr):
        checks.append({
            "target_table": table, "target_column": col, "check_type": "CUSTOM",
            "definition": {"kind": "format", "format": "date iso 8601"},
            "severity": "WARN", "origin": "TRANSFORM",
            "requirement": f"{col} is cast to a date; invalid dates should be flagged.",
        })
    if EMAIL_HINT.search(expr) or EMAIL_HINT.search(str(line.get("business_definition") or "")):
        checks.append({
            "target_table": table, "target_column": col, "check_type": "CUSTOM",
            "definition": {"kind": "format", "format": "email"},
            "severity": "WARN", "origin": "TRANSFORM",
            "requirement": f"{col} should look like an email after the transform.",
        })
    return checks


def render_csv(rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for raw in rows:
        row = {k: raw.get(k) if raw.get(k) is not None else "" for k in CSV_COLUMNS}
        checks = raw.get("soda_checks")
        if isinstance(checks, list):
            row["soda_checks"] = "; ".join(
                str(c.get("requirement") or c.get("check_type") or c) for c in checks
            )
        writer.writerow(row)
    return buf.getvalue()
