"""Run QA tests in Snowflake and keep the results (QUALITY.QA_RUN / QA_RESULT), for a run or a domain target table.

Each test is one guarded, read-only SELECT. It runs wrapped in a LIMIT so a failing test returns at most a page of
rows; the outcome is decided from the test's `expected` text:

  "0 rows ..."                 PASS when the query returns no rows (the rows it returns are the failures)
  "<column> = 0 ..."           PASS when that column of the first row is 0 (difference, extra_rows, unresolved)
  side / value / n result      PASS when every value has the same count on the expected and actual side
  anything else                REVIEW: the result is kept for a tester to judge

Tests that read the target model are NOT_RUN until the model is built. Sample values from PII columns are masked.
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.common.sql import clip, insert_rows, rows
from services.qa.guard import check

SAMPLE_ROWS = 20
FETCH_ROWS = 101
GATING = ("CRITICAL", "HIGH")
ZERO = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*0\b")


def plain(value: Any) -> Any:
    """Snowflake values as JSON-friendly Python."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def wrap(sql: str) -> str:
    """The test as a bounded query. A newline before ')' keeps a trailing -- comment from swallowing it."""
    return f"SELECT * FROM (\n{sql}\n) AS QA_TEST LIMIT {FETCH_ROWS}"


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate(expected: Optional[str], columns: Sequence[str], result: List[Dict[str, Any]]) -> Dict[str, Any]:
    """{outcome, detail, measured, failing}: judge one test's result against its expected text."""
    text = (expected or "").strip()
    low = text.lower()
    n = len(result)
    more = "+" if n >= FETCH_ROWS else ""
    if low.startswith("0 rows") or low.startswith("no rows"):
        if n == 0:
            return {"outcome": "PASS", "detail": "No rows returned", "measured": "0", "failing": 0}
        return {"outcome": "FAIL", "detail": f"{min(n, FETCH_ROWS - 1)}{more} failing rows returned",
                "measured": f"{min(n, FETCH_ROWS - 1)}{more} rows", "failing": min(n, FETCH_ROWS - 1)}
    upper = {c.upper(): c for c in columns}
    m = ZERO.match(text)
    if m and m.group(1).upper() in upper:
        if not result:
            return {"outcome": "REVIEW", "detail": "The query returned no rows to compare", "measured": None, "failing": None}
        value = result[0].get(upper[m.group(1).upper()])
        num = _number(value)
        if num is None:
            return {"outcome": "REVIEW", "detail": f"{m.group(1)} is {value!r}", "measured": str(value), "failing": None}
        ok = num == 0
        return {"outcome": "PASS" if ok else "FAIL", "detail": f"{m.group(1)} = {plain(value)}",
                "measured": str(plain(value)), "failing": None if ok else abs(int(num))}
    if {"SIDE", "VALUE", "N"} <= set(upper):
        counts: Dict[Any, Dict[str, float]] = {}
        for r in result:
            side = str(r.get(upper["SIDE"])).lower()
            counts.setdefault(plain(r.get(upper["VALUE"])), {})[side] = _number(r.get(upper["N"])) or 0
        diff = [v for v, c in counts.items() if c.get("expected", 0) != c.get("actual", 0)]
        if n >= FETCH_ROWS:
            return {"outcome": "REVIEW", "detail": "Too many distinct values to compare here; run it in Snowsight",
                    "measured": None, "failing": None}
        return {"outcome": "PASS" if not diff else "FAIL",
                "detail": "Counts match for every value" if not diff else f"{len(diff)} values differ: {', '.join(map(str, diff[:5]))}",
                "measured": f"{len(diff)} values differ", "failing": len(diff) or None}
    return {"outcome": "REVIEW", "detail": f"{n}{more} rows returned; compare with: {text or 'the objective'}",
            "measured": f"{n}{more} rows", "failing": None}


def references(sql: str, fqn: str) -> bool:
    if not fqn:
        return False
    pattern = r"\b" + r"\.".join(f'"?{re.escape(p)}"?' for p in fqn.split(".")) + r"\b"
    return re.search(pattern, sql, re.IGNORECASE) is not None


def summary(results: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    out = {"PASS": 0, "FAIL": 0, "REVIEW": 0, "NOT_RUN": 0, "ERROR": 0}
    for r in results:
        out[r["outcome"]] = out.get(r["outcome"], 0) + 1
    return out


def blocking(results: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Failures that stop a QA sign-off without an override."""
    return [r for r in results if r["outcome"] in ("FAIL", "ERROR") and str(r.get("severity") or "").upper() in GATING]


def _pii(session, run_id: Optional[str]) -> set:
    if not run_id:
        return set()
    try:
        return {str(r["COLUMN_NAME"]).upper() for r in rows(session, """SELECT DISTINCT COLUMN_NAME FROM PROFILE.PROFILE_REGISTRY
                                                                         WHERE RUN_ID = ? AND IS_CURRENT
                                                                           AND COALESCE(PII_CLASSIFICATION, 'NONE') <> 'NONE'""",
                                                                      [run_id])}
    except Exception:
        return set()


def _target_built(session, fqn: str) -> bool:
    if not fqn:
        return False
    try:
        rows(session, f"SELECT 1 FROM {fqn} LIMIT 0")
        return True
    except Exception:
        return False


def hidden_columns(test: Dict[str, Any], columns: Sequence[str], pii: set, keys: Iterable[str] = (),
                   conservative: bool = False) -> set:
    """Result columns to mask in sample rows. A test on a PII column returns its values under aliases
    (source_value, target_value, value, ...), so then every column except the business keys is masked. Without a
    profile (conservative), every column except the business keys is always masked."""
    from services.quality.scan import PII_HINT

    def sensitive(name: Optional[str]) -> bool:
        return bool(name) and (str(name).upper() in pii or PII_HINT.search(str(name)) is not None)

    hidden = {c for c in columns if sensitive(c)}
    source = str(test.get("source") or "")
    if conservative or sensitive(test.get("target_column")) or ("." in source and sensitive(source.rsplit(".", 1)[-1])):
        key_names = {str(k).upper() for k in keys}
        hidden |= {c for c in columns if c.upper() not in key_names}
    return hidden


def execute(session, test: Dict[str, Any], allowed: Sequence[str], pii: set,
            keys: Iterable[str] = (), conservative: bool = False) -> Dict[str, Any]:
    from services.quality.scan import mask

    base = {k: test.get(k) for k in ("test_id", "category", "title", "severity", "origin", "expected", "target_column")}
    ok, problems, sql = check(test.get("sql") or "", allowed)
    if not ok:
        return {**base, "outcome": "ERROR", "detail": "; ".join(problems), "sql": test.get("sql"), "duration_ms": 0}
    started = time.time()
    try:
        df = session.sql(wrap(sql))
        columns = [f.name.strip('"') for f in df.schema.fields]
        fetched = [{c: plain(v) for c, v in zip(columns, r)} for r in df.collect()]
    except Exception as exc:
        return {**base, "outcome": "ERROR", "detail": clip(exc, 600), "sql": sql,
                "duration_ms": int((time.time() - started) * 1000)}
    judged = evaluate(test.get("expected"), columns, fetched)
    hidden = hidden_columns(test, columns, pii, keys, conservative)
    sample = [{c: (mask(v) if c in hidden else v) for c, v in r.items()} for r in fetched[:SAMPLE_ROWS]]
    return {**base, **judged, "rows_returned": len(fetched), "columns": columns, "sample": sample,
            "masked": sorted(hidden), "sql": sql, "duration_ms": int((time.time() - started) * 1000)}


def run_scope(session, run_id: Optional[str] = None, target_table_id: Optional[str] = None,
              suite_id: Optional[str] = None, test_ids: Optional[List[str]] = None, triggered_by: str = "UI",
              trigger_detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run a run's suite or a target table's tests (or the chosen ones) with the caller's role and store every result.

    RUN scope: the generated suite, the run's saved tests and the saved TABLE tests of the run's target, all stored
    with the RUN_ID so the sign-off gate counts them. TABLE scope: the generated suite (when the table has an STTM)
    and the saved TABLE tests (of one suite when suite_id is given, then without the generated tests), stored with
    RUN_ID NULL."""
    from services.qa.procedures import _generated, _saved, _suite, context, table_saved
    from services.qa.scope import table_context

    if not run_id and not target_table_id and suite_id:
        target_table_id = _suite(session, suite_id)["TARGET_TABLE_ID"]
    assert run_id or target_table_id, "choose a run, a target table or a suite to test"
    if run_id:
        scope = "RUN"
        ctx = context(session, run_id)
        tests = _generated(ctx)
        try:
            tests += _saved(session, run_id)
        except Exception:
            pass
        allowed = list(ctx["allowed"])
        table_id = ctx.get("target_table_id")
        table_tests: List[Dict[str, Any]] = []
        if table_id:
            try:
                table_tests = table_saved(session, table_id)
            except Exception:  # before V028
                table_tests = []
        if table_tests:
            # domain tests were checked against the table's allowed tables (sources of its latest STTM)
            try:
                allowed += [a for a in table_context(session, table_id)["allowed"] if a not in allowed]
            except Exception:
                pass
            tests += table_tests
        pii, basis = _pii(session, run_id), "profile"
        suite_id = None
    else:
        scope = "TABLE"
        ctx = table_context(session, target_table_id)
        assert not ctx.get("retired"), "the target table is retired; reactivate it before running its QA tests"
        table_id = target_table_id
        tests = [] if suite_id or not ctx.get("sttm_id") else _generated(ctx)
        tests += table_saved(session, target_table_id, suite_id)
        allowed = list(ctx["allowed"])
        pii, basis = set(ctx.get("pii_columns") or []), ctx.get("pii_basis") or "conservative"
    target = ctx["target"]["fqn"]
    if test_ids:
        wanted = set(test_ids)
        tests = [t for t in tests if t["test_id"] in wanted]
    assert tests, "there are no QA tests to run"
    started = time.time()
    built = _target_built(session, target)
    missing = ("The target model is not built yet; run dbt first." if scope == "RUN"
               else "The target table does not exist (dropped, or not built yet).")
    # business keys under their target and source names stay readable in masked samples
    keys = set(ctx["business_keys"]) | {str(l.get("source_column")).upper() for l in ctx["lines"]
                                        if l.get("source_column")
                                        and str(l.get("target_column") or "").upper() in ctx["business_keys"]}
    results: List[Dict[str, Any]] = []
    for t in tests:
        if not built and references(t.get("sql") or "", target):
            result = {**{k: t.get(k) for k in ("test_id", "category", "title", "severity", "origin", "expected",
                                               "target_column")},
                      "outcome": "NOT_RUN", "detail": missing, "sql": t.get("sql"), "duration_ms": 0}
        else:
            result = execute(session, t, allowed, pii, keys, basis == "conservative")
        result["suite_id"] = t.get("suite_id")
        result["scope"] = t.get("scope") or scope
        results.append(result)
    counts = summary(results)
    qa_run = {"qa_run_id": str(uuid.uuid4()), "run_id": run_id, "sttm_id": ctx.get("sttm_id"), "target": target,
              "target_built": built, "tests": len(results), "passed": counts["PASS"], "failed": counts["FAIL"],
              "review": counts["REVIEW"], "not_run": counts["NOT_RUN"], "errors": counts["ERROR"],
              "duration_ms": int((time.time() - started) * 1000), "triggered_by": triggered_by,
              "blocking": len(blocking(results)), "scope": scope, "target_table_id": table_id,
              "domain_id": ctx.get("domain_id"), "suite_id": suite_id, "trigger_detail": trigger_detail,
              "pii_basis": basis}
    _store(session, qa_run, results)
    _remember(session, run_id, ctx, results)
    return {**qa_run, "results": results}


def run_tests(session, run_id: str, test_ids: Optional[List[str]] = None, triggered_by: str = "UI") -> Dict[str, Any]:
    """Run the run's suite (or the chosen tests) with the caller's role and store every result."""
    return run_scope(session, run_id=run_id, test_ids=test_ids, triggered_by=triggered_by)


def _store(session, qa_run: Dict[str, Any], results: List[Dict[str, Any]]) -> None:
    import json

    run_columns = ["QA_RUN_ID", "RUN_ID", "STTM_ID", "TARGET", "TARGET_BUILT", "DURATION_MS", "TESTS", "PASSED", "FAILED",
                   "REVIEW", "NOT_RUN", "ERRORS", "TRIGGERED_BY"]
    run_exprs = ["?", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "?::BOOLEAN", "?::NUMBER", "?::NUMBER",
                 "?::NUMBER", "?::NUMBER", "?::NUMBER", "?::NUMBER", "?::NUMBER", "?"]
    run_row = [qa_run["qa_run_id"], qa_run["run_id"], qa_run["sttm_id"], qa_run["target"],
               str(qa_run["target_built"]).upper(), qa_run["duration_ms"], qa_run["tests"], qa_run["passed"],
               qa_run["failed"], qa_run["review"], qa_run["not_run"], qa_run["errors"], qa_run["triggered_by"]]
    detail = json.dumps(qa_run["trigger_detail"]) if qa_run.get("trigger_detail") else None
    legacy = qa_run.get("scope", "RUN") == "RUN"
    try:
        insert_rows(session, "QUALITY.QA_RUN",
                    run_columns + ["DOMAIN_ID", "TARGET_TABLE_ID", "SUITE_ID", "SCOPE", "TRIGGER_DETAIL"],
                    run_exprs + ["NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "?", "PARSE_JSON(NULLIF(?, ''))"],
                    [run_row + [qa_run.get("domain_id"), qa_run.get("target_table_id"), qa_run.get("suite_id"),
                                qa_run.get("scope", "RUN"), detail]])
    except Exception:
        if not legacy:
            raise
        insert_rows(session, "QUALITY.QA_RUN", run_columns, run_exprs, [run_row])  # before V028
    result_columns = ["RESULT_ID", "QA_RUN_ID", "RUN_ID", "TEST_ID", "CATEGORY", "TITLE", "SEVERITY", "ORIGIN", "OUTCOME",
                      "ROWS_RETURNED", "MEASURED", "EXPECTED", "DETAIL", "COLUMNS", "SAMPLE_ROWS", "SQL_TEXT", "DURATION_MS"]
    result_exprs = ["?", "?", "NULLIF(?, '')", "?", "?", "?", "?", "?", "?", "NULLIF(?, '')::NUMBER", "NULLIF(?, '')",
                    "NULLIF(?, '')", "NULLIF(?, '')", "PARSE_JSON(NULLIF(?, ''))", "PARSE_JSON(NULLIF(?, ''))", "?",
                    "NULLIF(?, '')::NUMBER"]
    result_rows = [[str(uuid.uuid4()), qa_run["qa_run_id"], qa_run["run_id"], r["test_id"], r.get("category") or "",
                    clip(r.get("title"), 500), r.get("severity") or "", r.get("origin") or "", r["outcome"],
                    r.get("rows_returned"), clip(r.get("measured"), 200), clip(r.get("expected"), 1000),
                    clip(r.get("detail"), 4000), json.dumps(r["columns"]) if r.get("columns") else None,
                    json.dumps(r["sample"]) if r.get("sample") else None, clip(r.get("sql"), 16000), r.get("duration_ms")]
                   for r in results]
    try:
        insert_rows(session, "QUALITY.QA_RESULT", result_columns + ["TARGET_TABLE_ID", "SUITE_ID"],
                    result_exprs + ["NULLIF(?, '')", "NULLIF(?, '')"],
                    [row + [qa_run.get("target_table_id"), r.get("suite_id")] for row, r in zip(result_rows, results)])
    except Exception:
        if not legacy:
            raise
        insert_rows(session, "QUALITY.QA_RESULT", result_columns, result_exprs, result_rows)  # before V028


def _remember(session, run_id: Optional[str], ctx: Dict[str, Any], results: List[Dict[str, Any]]) -> None:
    """A tester's or the AI's saved test that passes is a proven check for this domain: keep it as QA_TEST
    knowledge so later runs, the AI test plan and the copilot can reuse it."""
    domain = ctx.get("domain_id")
    if not domain:
        return
    from services.knowledge.writer import remember

    for r in results:
        if r["outcome"] != "PASS" or r.get("origin") not in ("AI", "USER"):
            continue
        key = f"qa.test.{r['test_id']}"
        content = f"{r.get('title')}. Expected: {r.get('expected') or 'see SQL'}.\n\n{r.get('sql')}"
        try:
            remember(session, domain_id=domain, kind="QA_TEST", key=key, title=r.get("title") or key, content=content,
                     content_json={"test_id": r["test_id"], "sql": r.get("sql"), "expected": r.get("expected"),
                                   "origin": r.get("origin"), "run_id": run_id,
                                   "target_table_id": ctx.get("target_table_id")},
                     tags=["QA", "PASSED"], origin="QA", run_id=run_id, by_domain=False)
        except Exception:
            continue


RESULT_FIELDS = """R.TEST_ID, R.CATEGORY, R.TITLE, R.SEVERITY, R.ORIGIN, R.OUTCOME, R.ROWS_RETURNED,
                    R.MEASURED, R.EXPECTED, R.DETAIL, R.COLUMNS, R.SAMPLE_ROWS, R.SQL_TEXT, R.DURATION_MS"""


def latest(query, run_id: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """(latest QA run, the latest result of every test) through the API's query function; (None, []) before V017
    is deployed. Results span all QA runs on the same STTM: running one test again must not hide an earlier
    failure of another. Deleted saved tests, the run's and the domain table tests, are left out."""
    try:
        found = query("""SELECT QA_RUN_ID, STTM_ID, TARGET, TARGET_BUILT, STARTED_AT::VARCHAR AS STARTED_AT, DURATION_MS, TESTS,
                                PASSED, FAILED, REVIEW, NOT_RUN, ERRORS, TRIGGERED_BY, CREATED_BY
                           FROM QUALITY.QA_RUN WHERE RUN_ID = %s ORDER BY STARTED_AT DESC LIMIT 1""", (run_id,))
    except Exception:
        return None, []
    if not found:
        return None, []
    last = found[0]
    sql = """SELECT """ + RESULT_FIELDS + """
               FROM QUALITY.QA_RESULT R
               JOIN QUALITY.QA_RUN Q ON Q.QA_RUN_ID = R.QA_RUN_ID
              WHERE R.RUN_ID = %s AND EQUAL_NULL(Q.STTM_ID, %s){deleted}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY R.TEST_ID ORDER BY R.CREATED_AT DESC) = 1"""
    deleted = """
                AND R.TEST_ID NOT IN (SELECT TEST_ID FROM CONTRACT.QA_TEST_CASE
                                       WHERE (RUN_ID = %s OR SCOPE = 'TABLE') AND COALESCE(IS_DELETED, FALSE))"""
    try:
        return last, query(sql.format(deleted=deleted), (run_id, last.get("sttm_id"), run_id))
    except Exception:
        pass
    try:  # before V028: no SCOPE column
        return last, query(sql.format(deleted=deleted.replace(" OR SCOPE = 'TABLE'", "")),
                           (run_id, last.get("sttm_id"), run_id))
    except Exception:  # roles without SELECT on CONTRACT.QA_TEST_CASE (VIEWER)
        return last, query(sql.format(deleted=""), (run_id, last.get("sttm_id")))


QA_RUN_FIELDS = """QA_RUN_ID, RUN_ID, STTM_ID, SCOPE, DOMAIN_ID, TARGET_TABLE_ID, SUITE_ID, TARGET, TARGET_BUILT,
                   STARTED_AT::VARCHAR AS STARTED_AT, DURATION_MS, TESTS, PASSED, FAILED, REVIEW, NOT_RUN, ERRORS,
                   TRIGGERED_BY, TRIGGER_DETAIL, CREATED_BY"""


def latest_table(query, target_table_id: str,
                 suite_id: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """(latest TABLE-scope QA run, the latest result of every test across the table's TABLE-scope runs), optionally
    for one suite; deleted table tests are left out. (None, []) before V028."""
    suite = suite_id or ""
    try:
        found = query(f"""SELECT {QA_RUN_FIELDS} FROM QUALITY.QA_RUN
                           WHERE SCOPE = 'TABLE' AND TARGET_TABLE_ID = %s AND (%s = '' OR SUITE_ID = %s)
                           ORDER BY STARTED_AT DESC LIMIT 1""", (target_table_id, suite, suite))
    except Exception:
        return None, []
    if not found:
        return None, []
    sql = """SELECT """ + RESULT_FIELDS + """, R.SUITE_ID, R.QA_RUN_ID, R.CREATED_AT::VARCHAR AS CREATED_AT
               FROM QUALITY.QA_RESULT R
               JOIN QUALITY.QA_RUN Q ON Q.QA_RUN_ID = R.QA_RUN_ID
              WHERE Q.SCOPE = 'TABLE' AND R.TARGET_TABLE_ID = %s AND R.RUN_ID IS NULL AND (%s = '' OR R.SUITE_ID = %s){deleted}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY R.TEST_ID ORDER BY R.CREATED_AT DESC) = 1"""
    deleted = """
                AND R.TEST_ID NOT IN (SELECT TEST_ID FROM CONTRACT.QA_TEST_CASE
                                       WHERE TARGET_TABLE_ID = %s AND COALESCE(IS_DELETED, FALSE))"""
    try:
        return found[0], query(sql.format(deleted=deleted), (target_table_id, suite, suite, target_table_id))
    except Exception:  # roles without SELECT on CONTRACT.QA_TEST_CASE
        return found[0], query(sql.format(deleted=""), (target_table_id, suite, suite))


def history(query, target_table_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Recent QA runs that tested a target table, both table runs and run-lane runs (SCOPE tells them apart)."""
    limit = max(1, min(int(limit or 20), 200))
    try:
        return query(f"""SELECT {QA_RUN_FIELDS} FROM QUALITY.QA_RUN WHERE TARGET_TABLE_ID = %s
                          ORDER BY STARTED_AT DESC LIMIT {limit}""", (target_table_id,))
    except Exception:
        return []
