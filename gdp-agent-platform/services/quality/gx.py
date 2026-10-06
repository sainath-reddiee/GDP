"""Render neutral checks as a Great Expectations (GX Core 1.x) expectation suite.

The suite is a portable JSON artifact generated next to the SodaCL file; nothing here needs the GX runtime.
Rules without a native expectation (freshness, cross-table references) use `unexpected_rows_expectation`,
whose query reads the batch as `{batch}`.
"""

from __future__ import annotations

from typing import Any, Dict, List

FORMAT_REGEX = {
    "email": r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    "phone number": r"^\+?[0-9 ()./-]{7,20}$",
    "uuid": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    "ipv4": r"^((25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])$",
    "date iso 8601": r"^\d{4}-\d{2}-\d{2}",
}
SEVERITY = {"FAIL": "critical", "WARN": "warning"}
UNITS = {"m": "minute", "h": "hour", "d": "day"}


def _freshness_sql(column: str, threshold: str) -> str:
    raw = str(threshold or "1d").strip().lower()
    unit = UNITS.get(raw[-1:], "day")
    amount = raw[:-1] if raw[-1:] in UNITS else raw
    amount = amount if amount.isdigit() else "1"
    return (f"SELECT MAX({column}) AS latest FROM {{batch}} "
            f"HAVING MAX({column}) < DATEADD({unit}, -{amount}, CURRENT_TIMESTAMP())")


def expectations(check: Dict[str, Any]) -> List[Dict[str, Any]]:
    d = check.get("definition") or {}
    kind, col = d.get("kind"), check.get("target_column")
    meta = {"severity": SEVERITY.get(str(check.get("severity") or "").upper(), "warning"),
            "origin": check.get("origin"), "requirement": check.get("requirement")}
    if d.get("evidence"):
        meta["evidence"] = d["evidence"]

    def one(kind_: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return [{"type": kind_, "kwargs": {k: v for k, v in kwargs.items() if v is not None}, "meta": meta}]

    if kind == "row_count":
        low = d.get("min") if d.get("min") is not None else int(d.get("gt", 0)) + 1
        return one("expect_table_row_count_to_be_between", min_value=low, max_value=d.get("max"))
    if kind == "schema":
        out = one("expect_table_columns_to_match_set", column_set=list(d.get("required") or []), exact_match=False)
        return out
    if not col and kind not in ("unique",):
        return []
    if kind == "not_null":
        return one("expect_column_values_to_not_be_null", column=col)
    if kind == "missing_percent":
        return one("expect_column_values_to_not_be_null", column=col,
                   mostly=round(1 - float(d.get("max_percent") or 0) / 100, 4))
    if kind == "unique":
        cols = d.get("columns") or [col]
        if len(cols) == 1:
            return one("expect_column_values_to_be_unique", column=cols[0])
        return one("expect_compound_columns_to_be_unique", column_list=list(cols))
    if kind == "accepted_values":
        return one("expect_column_values_to_be_in_set", column=col, value_set=list(d.get("values") or []))
    if kind == "regex":
        mostly = d.get("max_invalid_percent")
        return one("expect_column_values_to_match_regex", column=col, regex=d.get("pattern"),
                   mostly=None if mostly is None else round(1 - float(mostly) / 100, 4))
    if kind == "format":
        pattern = FORMAT_REGEX.get(str(d.get("format") or "").lower())
        return one("expect_column_values_to_match_regex", column=col, regex=pattern) if pattern else []
    if kind == "range":
        return one("expect_column_values_to_be_between", column=col, min_value=d.get("min"), max_value=d.get("max"))
    if kind == "max_length":
        return one("expect_column_value_lengths_to_be_between", column=col, max_value=d.get("max"))
    if kind == "freshness":
        return one("unexpected_rows_expectation", unexpected_rows_query=_freshness_sql(col, d.get("threshold")),
                   description=f"{col} is fresher than {d.get('threshold') or '1d'}")
    if kind == "reference":
        ref_t, ref_c = d.get("reference_table"), d.get("reference_column") or col
        return one("unexpected_rows_expectation",
                   unexpected_rows_query=(f"SELECT b.* FROM {{batch}} b WHERE b.{col} IS NOT NULL AND NOT EXISTS "
                                          f"(SELECT 1 FROM {ref_t} r WHERE r.{ref_c} = b.{col})"),
                   description=f"{col} resolves to {ref_t}.{ref_c}")
    return []


def render_suite(model: str, checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Expectation suite for accepted and proposed checks; rejected ones are left out."""
    items: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for check in checks:
        if str(check.get("status") or "").upper() == "REJECTED":
            continue
        rendered = expectations(check)
        if rendered:
            items.extend(rendered)
        else:
            skipped.append(f"{check.get('check_type')} {check.get('target_column') or ''}".strip())
    return {"name": f"{model.lower()}_suite", "expectations": items,
            "meta": {"generated_by": "agentic-pipeline", "gx_version": "1.x", "model": model.lower(),
                     "not_expressible": skipped}}
