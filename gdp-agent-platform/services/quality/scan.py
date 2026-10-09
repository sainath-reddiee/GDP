"""Run a run's data quality checks inside Snowflake and keep every result.

Each check (the neutral model in services/soda: definition.kind + parameters) becomes SQL against the built model:
one aggregate query carries every check that is a count of violating rows or a column metric, and checks that need
their own statement (multi-column duplicates, references, schema, custom SQL) run separately. Failing column checks
also collect up to 20 sample rows, with PII columns masked. When the model is not built yet, the scan falls back to
the source backtest (services/quality/backtest.py) so the reviewer still sees today's data.

Results go to QUALITY.CHECK_RUN (one row per scan) and QUALITY.CHECK_RESULT (one row per check), so a run's quality
can be reviewed and trended at any time.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.quality.backtest import FQN, _lit, _pattern
from services.quality.gx import FORMAT_REGEX

SAMPLE_ROWS = 20
PII_HINT = re.compile(r"(EMAIL|PHONE|MOBILE|SSN|SOCIAL|BIRTH|DOB|PASSPORT|ADDRESS|STREET|FIRST_NAME|LAST_NAME|FULL_NAME|"
                      r"CARD|IBAN|ACCOUNT_NUMBER|TAX_ID|NATIONAL_ID)", re.IGNORECASE)
PII_SEMANTICS = {"EMAIL", "PHONE", "PERSON_NAME", "NAME", "ADDRESS", "SSN", "DATE_OF_BIRTH", "CREDIT_CARD", "IBAN"}
UNSAFE_CONDITION = re.compile(r";|--|/\*|\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|CREATE|ALTER|GRANT|REVOKE|CALL|"
                              r"EXECUTE|COPY|PUT|GET|REMOVE|USE|SET|BEGIN|COMMIT|ROLLBACK)\b|SYSTEM\$", re.IGNORECASE)
METRIC_KINDS = {"avg": "AVG", "min": "MIN", "max": "MAX", "sum": "SUM", "stddev": "STDDEV"}
DIMENSIONS = {
    "row_count": "completeness", "not_null": "completeness", "missing_percent": "completeness",
    "unique": "uniqueness", "duplicate_percent": "uniqueness",
    "accepted_values": "validity", "regex": "validity", "format": "validity", "range": "validity",
    "max_length": "validity", "freshness": "timeliness", "schema": "schema", "reference": "consistency",
    "failed_rows": "consistency", "metric": "consistency", "change_over_time": "consistency",
    "avg": "accuracy", "min": "accuracy", "max": "accuracy", "sum": "accuracy", "stddev": "accuracy",
}


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def dimension(kind: Optional[str]) -> str:
    return DIMENSIONS.get(str(kind or ""), "consistency")


# ---------------------------------------------------------------- thresholds


def threshold_ok(value: Optional[float], threshold: Optional[Dict[str, Any]]) -> Optional[bool]:
    """{"op": "<", "value": 5} or {"between": [a, b]}; None when there is nothing to compare."""
    if value is None or not threshold:
        return None
    if "between" in threshold:
        low, high = threshold["between"]
        return float(low) <= value <= float(high)
    op, limit = str(threshold.get("op") or "="), float(threshold.get("value") or 0)
    return {"<": value < limit, "<=": value <= limit, ">": value > limit, ">=": value >= limit,
            "=": value == limit, "!=": value != limit}.get(op, False)


def describe_threshold(threshold: Optional[Dict[str, Any]]) -> str:
    if not threshold:
        return ""
    if "between" in threshold:
        return f"between {threshold['between'][0]} and {threshold['between'][1]}"
    return f"{threshold.get('op', '=')} {threshold.get('value')}"


def freshness_hours(threshold: Any) -> float:
    found = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([mhd]?)\s*$", str(threshold or "1d"))
    if not found:
        return 24.0
    n, unit = float(found.group(1)), found.group(2) or "h"
    return n / 60 if unit == "m" else n * 24 if unit == "d" else n


def safe_condition(condition: str) -> str:
    text = str(condition or "").strip()
    assert text and not UNSAFE_CONDITION.search(text), "the condition must be a plain SQL boolean expression"
    return text


# ---------------------------------------------------------------- checks to SQL


def violation(check: Dict[str, Any]) -> Optional[str]:
    """WHERE predicate matching the rows that break a column check (also used to fetch failing samples)."""
    d = check.get("definition") or {}
    kind = d.get("kind")
    col = check.get("target_column")
    if kind == "failed_rows" and d.get("condition"):
        return f"({safe_condition(d['condition'])})"
    if not col:
        return None
    c = q(col)
    text = f"TRIM({c}::STRING)"
    if kind in ("not_null", "missing_percent"):
        missing = d.get("missing_values") or []
        extra = f" OR {text} IN ({', '.join(_lit(v) for v in missing)})" if missing else ""
        return f"({c} IS NULL{extra})"
    if kind == "accepted_values" and d.get("values"):
        return f"({c} IS NOT NULL AND {text} NOT IN ({', '.join(_lit(v) for v in d['values'])}))"
    if kind in ("regex", "format"):
        raw = d.get("pattern") if kind == "regex" else FORMAT_REGEX.get(str(d.get("format") or "").lower())
        quoted = _pattern(raw) if raw else None
        return f"({c} IS NOT NULL AND NOT REGEXP_LIKE({text}, {quoted}))" if quoted else None
    if kind == "range":
        parts = []
        if d.get("min") is not None:
            parts.append(f"TRY_TO_DOUBLE({c}::STRING) < {float(d['min'])}")
        if d.get("max") is not None:
            parts.append(f"TRY_TO_DOUBLE({c}::STRING) > {float(d['max'])}")
        return f"({' OR '.join(parts)})" if parts else None
    if kind == "max_length" and d.get("max") is not None:
        return f"(LENGTH({c}::STRING) > {int(d['max'])})"
    return None


def plan(checks: List[Dict[str, Any]], fqn: str) -> Tuple[str, Dict[int, Dict[str, Any]], Dict[int, str]]:
    """(aggregate SQL, {check index: how to read it}, {check index: standalone SQL})."""
    assert FQN.match(fqn), f"unsafe table name {fqn}"
    parts = ["COUNT(*) AS N"]
    reads: Dict[int, Dict[str, Any]] = {}
    standalone: Dict[int, str] = {}
    for i, check in enumerate(checks):
        d = check.get("definition") or {}
        kind = d.get("kind")
        col = check.get("target_column")
        alias = f"M{i}"
        try:
            if kind == "row_count" or (kind == "change_over_time" and d.get("metric", "row_count") == "row_count"):
                reads[i] = {"alias": "N", "measure": "value"}
            elif kind == "unique":
                cols = d.get("columns") or ([col] if col else [])
                if len(cols) == 1:
                    parts.append(f"COUNT({q(cols[0])}) - COUNT(DISTINCT {q(cols[0])}) AS {alias}")
                    reads[i] = {"alias": alias, "measure": "count"}
                elif cols:
                    keys = ", ".join(q(c) for c in cols)
                    standalone[i] = (f"SELECT COALESCE(SUM(C - 1), 0) AS V FROM (SELECT COUNT(*) AS C FROM {fqn} "
                                     f"GROUP BY {keys} HAVING COUNT(*) > 1)")
            elif kind == "duplicate_percent" and col:
                parts.append(f"COUNT({q(col)}) - COUNT(DISTINCT {q(col)}) AS {alias}")
                reads[i] = {"alias": alias, "measure": "percent"}
            elif kind in METRIC_KINDS and col:
                parts.append(f"{METRIC_KINDS[kind]}(TRY_TO_DOUBLE({q(col)}::STRING)) AS {alias}")
                reads[i] = {"alias": alias, "measure": "value"}
            elif kind == "freshness":
                target = col or "LOADED_AT"
                parts.append(f"DATEDIFF('minute', MAX({q(target)})::TIMESTAMP_NTZ, "
                             f"CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ) / 60.0 AS {alias}")
                reads[i] = {"alias": alias, "measure": "value"}
            elif kind == "metric" and d.get("expression"):
                expression = safe_condition(d["expression"])
                parts.append(f"({expression}) AS {alias}")
                reads[i] = {"alias": alias, "measure": "value"}
            elif kind in ("metric", "failed_rows") and d.get("query"):
                standalone[i] = "QUERY"  # validated and wrapped by the caller (needs the allow list)
            elif kind == "reference" and col and d.get("reference_table"):
                ref = str(d["reference_table"])
                assert FQN.match(ref), f"reference table must be DATABASE.SCHEMA.TABLE, got {ref}"
                ref_col = q(d.get("reference_column") or col)
                standalone[i] = (f"SELECT COUNT(*) AS V FROM {fqn} T WHERE T.{q(col)} IS NOT NULL AND NOT EXISTS "
                                 f"(SELECT 1 FROM {ref} R WHERE R.{ref_col} = T.{q(col)})")
            elif kind == "schema":
                standalone[i] = "SCHEMA"
            else:
                predicate = violation(check)
                if predicate:
                    parts.append(f"COUNT_IF({predicate}) AS {alias}")
                    reads[i] = {"alias": alias, "measure": "percent" if kind == "missing_percent" else "count"}
        except AssertionError as exc:
            reads[i] = {"error": str(exc)}
    return f"SELECT {', '.join(parts)} FROM {fqn}", reads, standalone


def evaluate(check: Dict[str, Any], value: Optional[float], rows_total: int,
             previous: Optional[float] = None) -> Dict[str, Any]:
    """Outcome for one check: PASS, WARN (a WARN-severity check that failed), FAIL, or NOT_EVALUATED."""
    d = check.get("definition") or {}
    kind = d.get("kind")
    severity = str(check.get("severity") or "FAIL").upper()
    if value is None:
        return {"outcome": "NOT_EVALUATED", "measured": None, "detail": "no value"}
    threshold: Optional[Dict[str, Any]] = d.get("threshold")
    percent = None
    if kind == "row_count":
        if d.get("min") is not None and d.get("max") is not None:
            threshold = {"between": [d["min"], d["max"]]}
        else:
            threshold = threshold or {"op": ">", "value": d.get("gt", 0)}
        detail = f"{int(value):,} rows"
    elif kind == "change_over_time":
        if previous in (None, 0):
            return {"outcome": "NOT_EVALUATED", "measured": value, "detail": "no earlier scan to compare with"}
        change = round(100.0 * (value - previous) / previous, 2)
        low, high = -float(d.get("max_decrease_percent", 20)), float(d.get("max_increase_percent", 50))
        ok = low <= change <= high
        return {"outcome": "PASS" if ok else ("WARN" if severity == "WARN" else "FAIL"), "measured": change,
                "threshold": f"between {low:g}% and +{high:g}%",
                "detail": f"row count {int(previous):,} → {int(value):,} ({change:+g}%)"}
    elif kind == "freshness":
        hours = freshness_hours(d.get("threshold"))
        threshold = {"op": "<=", "value": hours}
        detail = f"newest row is {value:.1f} h old (limit {hours:g} h)"
    elif kind in METRIC_KINDS or kind == "metric":
        detail = f"{kind if kind != 'metric' else 'value'} = {value:g}"
    else:
        violations = int(value)
        percent = round(100.0 * violations / rows_total, 4) if rows_total else 0.0
        if kind in ("missing_percent", "duplicate_percent") or d.get("max_invalid_percent") is not None:
            limit = float(d.get("max_percent", d.get("max_invalid_percent", 0)) or 0)
            threshold = {"op": "<=", "value": limit}
            value_for = percent
            ok = threshold_ok(value_for, threshold)
            detail = f"{violations:,} of {rows_total:,} rows ({percent:g}%), limit {limit:g}%"
            return {"outcome": "PASS" if ok else ("WARN" if severity == "WARN" else "FAIL"), "measured": percent,
                    "failed_rows": violations, "threshold": f"<= {limit:g}%", "detail": detail}
        threshold = threshold or {"op": "<=", "value": d.get("max_failed", 0)}
        detail = f"{violations:,} of {rows_total:,} rows fail ({percent:g}%)"
        ok = threshold_ok(float(violations), threshold)
        return {"outcome": "PASS" if ok else ("WARN" if severity == "WARN" else "FAIL"), "measured": violations,
                "failed_rows": violations, "percent": percent, "threshold": describe_threshold(threshold), "detail": detail}
    ok = threshold_ok(float(value), threshold)
    if ok is None:
        return {"outcome": "NOT_EVALUATED", "measured": value, "detail": "no threshold set"}
    return {"outcome": "PASS" if ok else ("WARN" if severity == "WARN" else "FAIL"), "measured": value,
            "threshold": describe_threshold(threshold), "detail": detail}


def schema_result(check: Dict[str, Any], actual: Dict[str, str]) -> Dict[str, Any]:
    d = check.get("definition") or {}
    have = {k.upper(): str(v).upper() for k, v in actual.items()}
    missing = [c for c in d.get("required") or [] if c.upper() not in have]
    forbidden = [c for c in d.get("forbidden") or [] if c.upper() in have]
    wrong = [f"{c}: {have[c.upper()]} (expected {t})" for c, t in (d.get("types") or {}).items()
             if c.upper() in have and type_family(t) and type_family(have[c.upper()]) != type_family(t)]
    problems = ([f"missing {', '.join(missing)}"] if missing else []) + ([f"forbidden present {', '.join(forbidden)}"] if forbidden else []) \
        + ([f"wrong type {'; '.join(wrong[:5])}"] if wrong else [])
    severity = str(check.get("severity") or "FAIL").upper()
    return {"outcome": "PASS" if not problems else ("WARN" if severity == "WARN" else "FAIL"),
            "measured": len(missing) + len(forbidden) + len(wrong),
            "detail": "; ".join(problems) or f"all {len(d.get('required') or [])} required columns present"}


FAMILIES = {
    "NUMBER": "NUMBER", "NUMERIC": "NUMBER", "DECIMAL": "NUMBER", "INT": "NUMBER", "INTEGER": "NUMBER", "BIGINT": "NUMBER",
    "SMALLINT": "NUMBER", "TINYINT": "NUMBER", "BYTEINT": "NUMBER", "FIXED": "NUMBER",
    "FLOAT": "FLOAT", "FLOAT4": "FLOAT", "FLOAT8": "FLOAT", "DOUBLE": "FLOAT", "REAL": "FLOAT",
    "VARCHAR": "TEXT", "TEXT": "TEXT", "STRING": "TEXT", "CHAR": "TEXT", "CHARACTER": "TEXT",
    "DATE": "DATE", "TIME": "TIME", "BOOLEAN": "BOOLEAN", "BINARY": "BINARY", "VARBINARY": "BINARY",
    "VARIANT": "SEMI", "OBJECT": "SEMI", "ARRAY": "SEMI",
}


def type_family(t: Any) -> str:
    base = re.split(r"[(\s]", str(t or "").strip().upper())[0]
    if base.startswith("TIMESTAMP") or base == "DATETIME":
        return "TIMESTAMP"
    return FAMILIES.get(base, base)


def mask(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    return text[:1] + "•" * min(6, max(1, len(text) - 1))


def pii_columns(lines: List[Dict[str, Any]], columns: List[str]) -> set:
    flagged = {str(l["target_column"]).upper() for l in lines
               if str(l.get("semantic_type") or "").upper() in PII_SEMANTICS}
    return flagged | {c.upper() for c in columns if PII_HINT.search(c)}


# ---------------------------------------------------------------- running a scan


def _rows(session, sql: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
    from services.common.sql import rows

    return rows(session, sql, params)


def target_fqn(session, sttm: Dict[str, Any]) -> Optional[str]:
    """The built model, when the STTM's registered target exists in Snowflake."""
    from services.common.sql import variant
    from services.source.identifiers import sql_ident

    design = variant(sttm.get("TABLE_DESIGN")) or {}
    found = _rows(session, """SELECT TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                               WHERE TARGET_TABLE_ID = ?""", [sttm.get("TARGET_TABLE_ID")]) if sttm.get("TARGET_TABLE_ID") else []
    if not found:
        return None
    db, schema, table = found[0]["TARGET_DATABASE"], found[0]["TARGET_SCHEMA"], design.get("target_table") or found[0]["TARGET_TABLE"]
    exists = _rows(session, f"SELECT 1 FROM {sql_ident(db)}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? "
                            "AND TABLE_NAME = ? LIMIT 1", [str(schema).upper(), str(table).upper()])
    return f"{sql_ident(db)}.{sql_ident(schema)}.{sql_ident(table)}" if exists else None


def _store(session, scan: Dict[str, Any], results: List[Dict[str, Any]]) -> None:
    # insert_rows binds None as '' so NULLIF(...) keeps empty values NULL (Snowpark would bind None as 'None')
    from services.common.sql import insert_rows

    insert_rows(session, "QUALITY.CHECK_RUN",
                ["SCAN_ID", "RUN_ID", "STTM_ID", "TARGET", "MODE", "DURATION_MS", "CHECKS", "PASSED", "WARNED", "FAILED",
                 "NOT_EVALUATED", "ERRORS", "HEALTH", "ROWS_SCANNED", "TRIGGERED_BY"],
                ["?", "?", "NULLIF(?, '')", "?", "?", "?::NUMBER", "?::NUMBER", "?::NUMBER", "?::NUMBER", "?::NUMBER",
                 "?::NUMBER", "?::NUMBER", "NULLIF(?, '')::NUMBER", "NULLIF(?, '')::NUMBER", "?"],
                [[scan["scan_id"], scan["run_id"], scan.get("sttm_id"), scan["target"], scan["mode"], scan["duration_ms"],
                  scan["checks"], scan["passed"], scan["warned"], scan["failed"], scan["not_evaluated"], scan["errors"],
                  scan["health"], scan.get("rows_scanned"), scan.get("triggered_by") or "UI"]])
    insert_rows(session, "QUALITY.CHECK_RESULT",
                ["RESULT_ID", "SCAN_ID", "RUN_ID", "EXPECTATION_ID", "TARGET_TABLE", "TARGET_COLUMN", "CHECK_TYPE", "KIND",
                 "DIMENSION", "SEVERITY", "OUTCOME", "MEASURED", "THRESHOLD", "FAILED_ROWS", "DETAIL", "SAMPLE_ROWS", "SQL_TEXT",
                 "DURATION_MS"],
                ["?", "?", "?", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')",
                 "NULLIF(?, '')", "NULLIF(?, '')", "?", "NULLIF(?, '')::FLOAT", "NULLIF(?, '')", "NULLIF(?, '')::NUMBER",
                 "NULLIF(?, '')", "PARSE_JSON(NULLIF(?, ''))", "NULLIF(?, '')", "NULLIF(?, '')::NUMBER"],
                [[str(uuid.uuid4()), scan["scan_id"], scan["run_id"], r.get("expectation_id"), r.get("target_table"),
                  r.get("target_column"), r.get("check_type"), r.get("kind"), r.get("dimension"), r.get("severity"),
                  r["outcome"], r.get("measured"), r.get("threshold"), r.get("failed_rows"),
                  str(r.get("detail") or "")[:4000], json.dumps(r.get("sample")) if r.get("sample") else None,
                  str(r.get("sql") or "")[:16000], r.get("duration_ms")] for r in results])


def health(passed: int, warned: int, failed: int) -> Optional[int]:
    """0-100: passes count fully, warnings half; checks that were not evaluated do not count."""
    total = passed + warned + failed
    return round(100 * (passed + 0.5 * warned) / total) if total else None


def run_scan(session, run_id: str, triggered_by: str = "UI") -> Dict[str, Any]:
    """Execute the run's current (non-rejected) checks and record the results."""
    from services.common.sql import variant
    from services.qa.guard import check as guard
    from services.soda.procedures import _current_sttm, _lines

    started = time.time()
    sttm = _current_sttm(session, run_id)
    lines = _lines(session, sttm["STTM_ID"])
    stored = _rows(session, """SELECT EXPECTATION_ID, TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION, SEVERITY
                                 FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                WHERE RUN_ID = ? AND IS_CURRENT AND STATUS <> 'REJECTED' ORDER BY CHECK_TYPE""", [run_id])
    checks = [{"expectation_id": r["EXPECTATION_ID"], "target_table": r["TARGET_TABLE"],
               "target_column": r["TARGET_COLUMN"], "check_type": r["CHECK_TYPE"],
               "definition": variant(r["CHECK_DEFINITION"]) or {}, "severity": r["SEVERITY"]} for r in stored]
    assert checks, "there are no data quality checks to run; generate them first"
    fqn = target_fqn(session, sttm)
    scan = {"scan_id": str(uuid.uuid4()), "run_id": run_id, "sttm_id": sttm["STTM_ID"],
            "target": fqn or "source tables", "mode": "MODEL" if fqn else "SOURCE", "triggered_by": triggered_by}
    results: List[Dict[str, Any]] = []
    if fqn:
        results, scan["rows_scanned"] = _scan_model(session, run_id, checks, lines, fqn, guard)
    else:
        from services.soda.procedures import backtest_soda

        outcome = backtest_soda(session, run_id)
        by_id = {o["expectation_id"]: o for o in outcome["results"]}
        for c in checks:
            o = by_id.get(c["expectation_id"], {})
            status = o.get("status", "NOT_EVALUATED")
            outcome_ = "PASS" if status == "PASS" else "NOT_EVALUATED" if status == "NOT_EVALUATED" else \
                ("WARN" if str(c["severity"]).upper() == "WARN" else "FAIL")
            results.append({**_base(c), "outcome": outcome_, "measured": o.get("observed"),
                            "failed_rows": o.get("observed") if status == "FAIL" else 0,
                            "detail": f"source data: {o.get('detail') or 'not evaluated'}"})
    counts = {k: sum(1 for r in results if r["outcome"] == k) for k in ("PASS", "WARN", "FAIL", "NOT_EVALUATED", "ERROR")}
    scan.update(checks=len(results), passed=counts["PASS"], warned=counts["WARN"], failed=counts["FAIL"],
                not_evaluated=counts["NOT_EVALUATED"], errors=counts["ERROR"], health=health(counts["PASS"], counts["WARN"], counts["FAIL"]),
                duration_ms=int((time.time() - started) * 1000))
    _store(session, scan, results)
    _remember_failures(session, run_id, sttm, results)
    return {**scan, "results": results}


def _base(c: Dict[str, Any]) -> Dict[str, Any]:
    kind = (c.get("definition") or {}).get("kind")
    return {"expectation_id": c["expectation_id"], "target_table": c.get("target_table"),
            "target_column": c.get("target_column"), "check_type": c.get("check_type"), "kind": kind,
            "dimension": dimension(kind), "severity": c.get("severity")}


def _scan_model(session, run_id: str, checks: List[Dict[str, Any]], lines: List[Dict[str, Any]], fqn: str,
                guard) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    from services.source.identifiers import sql_ident

    aggregate, reads, standalone = plan(checks, fqn)
    t0 = time.time()
    try:
        row = (_rows(session, aggregate) or [{}])[0]
        agg_error = None
    except Exception as exc:
        row, agg_error = {}, str(exc)[:300]
    agg_ms = int((time.time() - t0) * 1000)
    total = int(row.get("N") or 0)
    db, schema, table = [p.strip('"') for p in re.findall(r'"(?:[^"]|"")+"|[^.]+', fqn)]
    actual = {r["COLUMN_NAME"]: r["DATA_TYPE"] for r in _rows(
        session, f"SELECT COLUMN_NAME, DATA_TYPE FROM {sql_ident(db)}.INFORMATION_SCHEMA.COLUMNS "
                 "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", [schema, table])}
    masked = pii_columns(lines, list(actual))
    allowed = [fqn.replace('"', "")] + [str(c["definition"].get("reference_table")) for c in checks
                                        if (c.get("definition") or {}).get("reference_table")]
    out = []
    for i, c in enumerate(checks):
        base = _base(c)
        d = c.get("definition") or {}
        started = time.time()
        sql_text = aggregate
        try:
            if i in reads and "error" in reads[i]:
                out.append({**base, "outcome": "ERROR", "detail": reads[i]["error"]})
                continue
            if i in reads:
                if agg_error:
                    out.append({**base, "outcome": "ERROR", "detail": agg_error, "sql": aggregate})
                    continue
                raw = row.get(reads[i]["alias"])
                value = None if raw is None else float(raw)
                previous = None
                if d.get("kind") == "change_over_time":
                    prior = _rows(session, """SELECT ROWS_SCANNED FROM QUALITY.CHECK_RUN
                                               WHERE RUN_ID = ? AND MODE = 'MODEL' AND ROWS_SCANNED IS NOT NULL
                                               ORDER BY STARTED_AT DESC LIMIT 1""", [run_id])
                    previous = float(prior[0]["ROWS_SCANNED"]) if prior else None
                result = evaluate(c, value, total, previous)
            elif i in standalone:
                kind = d.get("kind")
                if standalone[i] == "SCHEMA":
                    result, sql_text = schema_result(c, actual), "INFORMATION_SCHEMA.COLUMNS"
                elif standalone[i] == "QUERY":
                    ok, problems, cleaned = guard(d["query"], allowed)
                    assert ok, "; ".join(problems)
                    if kind == "failed_rows":
                        sql_text = f"SELECT COUNT(*) AS V FROM ({cleaned})"
                        value = float((_rows(session, sql_text) or [{"V": 0}])[0]["V"] or 0)
                        result = evaluate({**c, "definition": {**d, "kind": "failed_rows_count"}}, value, total)
                    else:
                        sql_text = cleaned
                        first = (_rows(session, cleaned) or [{}])[0]
                        value = next(iter(first.values()), None)
                        result = evaluate(c, None if value is None else float(value), total)
                else:
                    sql_text = standalone[i]
                    value = float((_rows(session, sql_text) or [{"V": 0}])[0]["V"] or 0)
                    result = evaluate({**c, "definition": {**d, "kind": "violations"}}, value, total)
            else:
                out.append({**base, "outcome": "NOT_EVALUATED",
                            "detail": f"{d.get('kind')} cannot be evaluated in SQL; run it with the Soda CLI"})
                continue
            entry = {**base, **result, "sql": sql_text,
                     "duration_ms": agg_ms if i in reads else int((time.time() - started) * 1000)}
            predicate = violation(c)
            if entry["outcome"] in ("FAIL", "WARN") and predicate and entry.get("failed_rows"):
                sample_sql = f"SELECT * FROM {fqn} WHERE {predicate} LIMIT {SAMPLE_ROWS}"
                try:
                    sample = _rows(session, sample_sql)
                    entry["sample"] = [{k: (mask(v) if k.upper() in masked else (v if isinstance(v, (int, float, bool)) or v is None else str(v)[:200]))
                                        for k, v in r.items()} for r in sample]
                    entry["sample_sql"] = sample_sql
                except Exception:
                    pass
            out.append(entry)
        except Exception as exc:
            out.append({**base, "outcome": "ERROR", "detail": str(exc)[:400], "sql": sql_text})
    return out, (total if not agg_error else None)


def _remember_failures(session, run_id: str, sttm: Dict[str, Any], results: List[Dict[str, Any]]) -> None:
    """A check that failed in this scan and the one before is a recurring issue: keep it as EXCEPTION knowledge so
    later runs, the copilot and reviewers know about it."""
    failing = [r for r in results if r["outcome"] == "FAIL" and r.get("expectation_id")]
    if not failing or not sttm.get("DOMAIN_ID"):
        return
    try:
        before = {r["EXPECTATION_ID"] for r in _rows(session, """
            SELECT EXPECTATION_ID FROM QUALITY.CHECK_RESULT
             WHERE RUN_ID = ? AND OUTCOME = 'FAIL'
               AND SCAN_ID = (SELECT SCAN_ID FROM QUALITY.CHECK_RUN WHERE RUN_ID = ?
                              ORDER BY STARTED_AT DESC LIMIT 1 OFFSET 1)""", [run_id, run_id])}
    except Exception:
        return
    for r in failing:
        if r["expectation_id"] not in before:
            continue
        key = f"quality.recurring.{run_id[:8]}.{r['expectation_id'][:8]}"
        title = f"Recurring data quality failure: {r.get('target_table')}{'.' + r['target_column'] if r.get('target_column') else ''} ({r.get('kind')})"
        try:
            session.sql("""MERGE INTO KNOWLEDGE.DOMAIN_KNOWLEDGE K
                           USING (SELECT ? AS SOURCE_REFERENCE) S ON K.SOURCE_REFERENCE = S.SOURCE_REFERENCE AND K.IS_CURRENT
                           WHEN MATCHED THEN UPDATE SET CONTENT = ?, UPDATED_AT = CURRENT_TIMESTAMP()
                           WHEN NOT MATCHED THEN INSERT (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, TAGS,
                                                         SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY)
                                VALUES (UUID_STRING(), ?, 'EXCEPTION', ?, ?, PARSE_JSON('["QUALITY_SCAN"]'), ?, 'ACTIVE',
                                        1, TRUE, CURRENT_USER())""",
                        params=[key, f"{title}. Latest scan: {r.get('detail')}", sttm["DOMAIN_ID"], title,
                                f"{title}. Latest scan: {r.get('detail')}", key]).collect()
        except Exception:
            continue
