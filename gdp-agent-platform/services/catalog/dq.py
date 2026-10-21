"""Data Quality Store: declarative Soda / Great Expectations checks per table and column, managed without a run (V037).

Checks are kept in the platform's neutral form ({kind, ...}; services/soda/custom.py validates it), which is what the
scanner runs (services/quality/scan.py). SODA_DSL is rendered from it (services/soda/expectations.render_check), so
exports and the Soda CLI kit stay in step. A check can also be created from a one-line SodaCL string for the common
metrics (parse_dsl); anything richer goes through the check editor.

Table scans are stored in CORE.CHECK_RUN / CHECK_RESULT with SCOPE 'TABLE' and RUN_ID NULL, and update the
catalog's LAST_RUN_STATUS / LAST_RUN_AT / LAST_MEASURED. Schedules are Snowflake tasks that call
CORE.RUN_TABLE_SCAN(TABLE_FQN), one per table.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.common.sql import clip, insert_rows, rows, variant
from services.common.platform_schema import fqn

CATALOG = "CORE.DQ_CHECKS_CATALOG"
STATUSES = ("ACTIVE", "PAUSED", "RETIRED")
ORIGINS = ("PROFILE", "RUN", "USER", "REQUIREMENT")  # REQUIREMENT: from a client requirement (dq_requirements)
# five fields of the cron alphabet, then an optional IANA time zone: nothing that can end the quoted SCHEDULE
CRON = re.compile(r"^\s*([0-9*/,?#LW-]+\s+){4}[0-9*/,?#LW-]+(\s+[A-Za-z_]+(?:/[A-Za-z_+-]+)*)?\s*$")

_NUM = r"-?\d+(?:\.\d+)?"
_FN = re.compile(rf"^(?P<fn>[a-z_]+)\((?P<args>[^)]*)\)\s*(?P<op><=|>=|<|>|=|!=)\s*(?P<value>{_NUM})\s*(?P<unit>%|[mhd])?$",
                 re.I)
_BETWEEN = re.compile(rf"^(?P<fn>[a-z_]+)(?:\((?P<args>[^)]*)\))?\s+between\s+(?P<low>{_NUM})\s+and\s+(?P<high>{_NUM})$", re.I)
_ROWS = re.compile(r"^row_count\s*(?P<op><=|>=|<|>|=)\s*(?P<value>\d+)$", re.I)


# ---------------------------------------------------------------- pure: DSL, normalize, render


def parse_dsl(text: str) -> Tuple[Dict[str, Any], List[str]]:
    """One-line SodaCL -> (neutral check {target_column, definition}, problems). Supported:
    row_count > N | row_count between A and B | missing_count(C) = 0 | missing_percent(C) < X% |
    duplicate_count(C[, D]) = 0 | duplicate_percent(C) < X% | freshness(C) < 1d | min|max|avg|sum|stddev(C) op N |
    min|max|avg(C) between A and B."""
    line = " ".join(str(text or "").strip().rstrip(":").split())
    if line.startswith("- "):
        line = line[2:].rstrip(":")
    if not line:
        return {}, ["the check is empty"]
    m = _ROWS.match(line)
    if m:
        value = int(m["value"])
        op = m["op"]
        if op == ">":
            return {"target_column": None, "definition": {"kind": "row_count", "gt": value}}, []
        if op == ">=":
            return {"target_column": None, "definition": {"kind": "row_count", "gt": value - 1}}, []
        return {}, ["row_count supports > N, >= N or between A and B"]
    m = _BETWEEN.match(line)
    if m:
        fn, args = m["fn"].lower(), [a.strip() for a in (m["args"] or "").split(",") if a.strip()]
        low, high = float(m["low"]), float(m["high"])
        if fn == "row_count" and not args:
            return {"target_column": None, "definition": {"kind": "row_count", "min": int(low), "max": int(high)}}, []
        if fn in ("min", "max", "avg", "sum", "stddev") and len(args) == 1:
            return {"target_column": args[0], "definition": {"kind": fn, "threshold": {"between": [low, high]}}}, []
        return {}, [f"{fn} between ... is not supported in one line; use the editor"]
    m = _FN.match(line)
    if not m:
        return {}, ["not a one-line SodaCL check this editor understands; use the structured editor"]
    fn, op, value, unit = m["fn"].lower(), m["op"], float(m["value"]), (m["unit"] or "")
    args = [a.strip() for a in m["args"].split(",") if a.strip()]
    col = args[0] if args else None

    def need(cond: bool, message: str) -> List[str]:
        return [] if cond else [message]

    if fn == "missing_count":
        return ({"target_column": col, "definition": {"kind": "not_null"}},
                need(op == "=" and value == 0 and col, "missing_count supports = 0 on one column"))
    if fn == "missing_percent":
        return ({"target_column": col, "definition": {"kind": "missing_percent", "max_percent": value}},
                need(op in ("<", "<=") and col, "missing_percent supports < X%"))
    if fn == "duplicate_count":
        return ({"target_column": col if len(args) == 1 else None,
                 "definition": {"kind": "unique", **({"columns": args} if len(args) > 1 else {})}},
                need(op == "=" and value == 0 and args, "duplicate_count supports = 0"))
    if fn == "duplicate_percent":
        return ({"target_column": col, "definition": {"kind": "duplicate_percent", "max_percent": value}},
                need(op in ("<", "<=") and col, "duplicate_percent supports < X%"))
    if fn == "freshness":
        amount = f"{value:g}{unit or 'd'}"
        return ({"target_column": col, "definition": {"kind": "freshness", "threshold": amount}},
                need(op in ("<", "<=") and unit in ("", "m", "h", "d") and value.is_integer(),
                     "freshness supports < 30m, 6h or 1d"))
    if fn in ("min", "max", "avg", "sum", "stddev"):
        return ({"target_column": col, "definition": {"kind": fn, "threshold": {"op": op, "value": value}}},
                need(bool(col) and op != "!=", f"{fn} needs a column and a comparison"))
    return {}, [f"{fn} is not supported in one line; use the structured editor"]


def headline(soda: str) -> str:
    """The first line of a rendered check without YAML punctuation: missing_count(EMAIL) = 0."""
    first = next((l for l in str(soda or "").splitlines() if l.strip()), "")
    return first.strip().lstrip("-").strip().rstrip(":").strip()


def normalize(check: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Validated neutral check plus its rendered SodaCL, or problems."""
    from services.soda.custom import validate
    from services.soda.expectations import render_check

    cleaned, problems = validate({"target_column": check.get("target_column"), "severity": check.get("severity") or "WARN",
                                  "requirement": check.get("requirement"), "definition": check.get("definition") or {}})
    if problems:
        return {}, problems
    cleaned["soda_dsl"] = render_check(cleaned)
    cleaned["headline"] = assertion(cleaned)
    return cleaned, []


def assertion(check: Dict[str, Any]) -> str:
    """The full assertion of a check (missing_percent(EMAIL) < 2%) whatever its severity: WARN checks render as a
    Soda warn block whose first line omits the threshold, so the headline comes from the FAIL form."""
    from services.soda.expectations import render_check

    try:
        return headline(render_check({**check, "severity": "FAIL"}))
    except Exception:  # noqa: BLE001  stored definitions from older code; fall back to the rendered DSL
        return headline(check.get("soda_dsl") or "")


def same_check(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    def key(c):
        d = {k: v for k, v in (c.get("definition") or {}).items() if k not in ("backtest", "evidence")}
        return (str(c.get("target_column") or "").upper(), json.dumps(d, sort_keys=True, default=str))
    return key(a) == key(b)


def build_tree(rows_: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Catalog rows -> [{table, domain_id, checks: n, status counts, columns: [{column, checks: [...]}]}], the table's own
    checks under column None (shown as "Table")."""
    tables: Dict[str, Dict[str, Any]] = {}
    for r in rows_:
        t = tables.setdefault(r["table_name"], {"table": r["table_name"], "domain_id": r.get("domain_id"),
                                                "schedule_cron": None, "columns": {}, "counts": {}})
        t["schedule_cron"] = t["schedule_cron"] or r.get("schedule_cron")
        col = r.get("column_name") or None
        t["columns"].setdefault(col, []).append(r)
        status = r.get("last_run_status") or "NEVER_RUN"
        t["counts"][status] = t["counts"].get(status, 0) + 1
    out = []
    for name in sorted(tables):
        t = tables[name]
        cols = sorted(t["columns"].items(), key=lambda kv: (kv[0] is not None, kv[0] or ""))
        evaluated = sum(v for k, v in t["counts"].items() if k in ("PASS", "WARN", "FAIL"))
        passed = t["counts"].get("PASS", 0)
        out.append({"table": name, "domain_id": t["domain_id"], "schedule_cron": t["schedule_cron"],
                    "checks": sum(len(v) for v in t["columns"].values()), "counts": t["counts"],
                    "pass_rate": round(100 * passed / evaluated, 1) if evaluated else None,
                    "columns": [{"column": c, "checks": v} for c, v in cols]})
    return out


def task_name(table_fqn: str) -> str:
    return "DQ_" + hashlib.md5(table_fqn.upper().encode("utf-8")).hexdigest()[:16].upper()


def schedule_sql(database: str, table_fqn: str, cron: str, warehouse: str) -> Tuple[str, List[str]]:
    """(task fqn, statements) for a scheduled table scan. The table name is bound into a string literal, so quotes
    are escaped; the cron is validated."""
    from services.quality.backtest import FQN

    assert re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", database or ""), "unsafe database name"
    assert re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", warehouse or ""), "unsafe warehouse name"
    assert FQN.match(table_fqn or ""), "table must be DATABASE.SCHEMA.TABLE"
    assert CRON.match(cron or ""), "cron needs five fields and an optional time zone, e.g. 0 6 * * * UTC"
    task = fqn(database, task_name(table_fqn))
    literal = table_fqn.replace("'", "''")
    return task, [
        f"CREATE OR REPLACE TASK {task} WAREHOUSE = {warehouse} SCHEDULE = 'USING CRON {cron.strip()}' "
        f"COMMENT = 'Data Quality Store: scheduled scan of {literal}' "
        f"AS CALL {fqn(database, 'RUN_TABLE_SCAN')}('{literal}')",
        f"ALTER TASK {task} RESUME",
    ]


# ---------------------------------------------------------------- Snowpark: catalog writes


def _fqn(value: str) -> str:
    from services.quality.backtest import FQN

    text = str(value or "").strip()
    assert FQN.match(text), "table must be DATABASE.SCHEMA.TABLE"
    return ".".join(p if p.startswith('"') else p.upper() for p in re.findall(r'"(?:[^"]|"")+"|[^.]+', text))


def _domain_for(session, table_fqn: str, given: Optional[str]) -> Optional[str]:
    if given:
        return given
    parts = [p.strip('"') for p in re.findall(r'"(?:[^"]|"")+"|[^.]+', table_fqn)]
    try:
        found = rows(session, """SELECT DOMAIN_ID FROM CORE.TARGET_TABLE_REGISTRY
                                  WHERE UPPER(TARGET_DATABASE) = UPPER(?) AND UPPER(TARGET_SCHEMA) = UPPER(?)
                                    AND UPPER(TARGET_TABLE) = UPPER(?) LIMIT 1""", parts)
        if found:
            return found[0]["DOMAIN_ID"]
        found = rows(session, """SELECT MAX(DOMAIN_ID) AS D FROM CORE.DATA_PROFILES
                                  WHERE UPPER(DATABASE_NAME) = UPPER(?) AND UPPER(SCHEMA_NAME) = UPPER(?)
                                    AND UPPER(TABLE_NAME) = UPPER(?)""", parts)
        return found[0]["D"] if found else None
    except Exception:
        return None


def _existing(session, table_fqn: str) -> List[Dict[str, Any]]:
    return [{"check_id": r["CHECK_ID"], "target_column": r["COLUMN_NAME"], "definition": variant(r["CHECK_DEFINITION"]) or {}}
            for r in rows(session, f"""SELECT CHECK_ID, COLUMN_NAME, CHECK_DEFINITION FROM {CATALOG}
                                        WHERE TABLE_NAME = ? AND STATUS <> 'RETIRED'""", [table_fqn])]


def _lineage(session, check_id: str, requirement: Optional[str], source_run_id: Optional[str]) -> None:
    """Why the check exists and the run it came from (V038 columns; skipped before V038 is applied)."""
    try:
        session.sql(f"""UPDATE {CATALOG} SET REQUIREMENT = NULLIF(?, ''), SOURCE_RUN_ID = COALESCE(NULLIF(?, ''), SOURCE_RUN_ID)
                         WHERE CHECK_ID = ?""", params=[clip(requirement or "", 4000), source_run_id or "", check_id]).collect()
    except Exception:
        pass


def upsert(session, payload_json: str) -> Dict[str, Any]:
    """Create or update one check. The payload carries either `definition` (neutral form) or `dsl` (one-line SodaCL)."""
    payload = json.loads(payload_json or "{}")
    table = _fqn(payload.get("table") or "")
    check = {"target_column": payload.get("column") or payload.get("target_column"), "severity": payload.get("severity"),
             "requirement": payload.get("requirement"), "definition": payload.get("definition")}
    if payload.get("dsl") and not payload.get("definition"):
        parsed, problems = parse_dsl(payload["dsl"])
        assert not problems, "; ".join(problems)
        check.update({k: v for k, v in parsed.items() if v is not None or k == "definition"})
        if parsed.get("target_column"):
            check["target_column"] = parsed["target_column"]
    cleaned, problems = normalize(check)
    assert not problems, "; ".join(problems)
    origin = str(payload.get("origin") or "USER").upper()
    origin = origin if origin in ORIGINS else "USER"
    check_id = payload.get("check_id")
    if not check_id:
        duplicate = next((e for e in _existing(session, table) if same_check(e, cleaned)), None)
        assert not duplicate, "this table already has the same check"
    domain = _domain_for(session, table, payload.get("domain_id"))
    values = [table, cleaned["target_column"] or "", cleaned["check_type"], json.dumps(cleaned["definition"]),
              clip(cleaned["soda_dsl"], 4000), cleaned["severity"], domain or ""]
    if check_id:
        found = rows(session, f"SELECT CHECK_ID FROM {CATALOG} WHERE CHECK_ID = ?", [check_id])
        assert found, "check not found"
        session.sql(f"""UPDATE {CATALOG} SET TABLE_NAME = ?, COLUMN_NAME = NULLIF(?, ''), CHECK_TYPE = ?,
                               CHECK_DEFINITION = PARSE_JSON(?), SODA_DSL = ?, SEVERITY = ?,
                               DOMAIN_ID = COALESCE(NULLIF(?, ''), DOMAIN_ID), UPDATED_AT = CURRENT_TIMESTAMP(),
                               LAST_RUN_STATUS = NULL, LAST_MEASURED = NULL
                         WHERE CHECK_ID = ?""", params=values + [check_id]).collect()
    else:
        check_id = str(uuid.uuid4())
        insert_rows(session, CATALOG,
                    ["CHECK_ID", "TABLE_NAME", "COLUMN_NAME", "CHECK_TYPE", "CHECK_DEFINITION", "SODA_DSL", "SEVERITY",
                     "DOMAIN_ID", "ORIGIN", "STATUS"],
                    ["?", "?", "NULLIF(?, '')", "?", "PARSE_JSON(?)", "?", "?", "NULLIF(?, '')", "?", "'ACTIVE'"],
                    [[check_id] + values + [origin]])
    _lineage(session, check_id, cleaned.get("requirement"), payload.get("source_run_id"))
    return {"check_id": check_id, "table": table, "column": cleaned["target_column"], "check_type": cleaned["check_type"],
            "soda_dsl": cleaned["soda_dsl"], "headline": cleaned["headline"], "definition": cleaned["definition"],
            "severity": cleaned["severity"]}


def set_status(session, check_id: str, status: str) -> Dict[str, Any]:
    status = str(status or "").upper()
    assert status in STATUSES, f"status must be one of {list(STATUSES)}"
    found = rows(session, f"SELECT CHECK_ID FROM {CATALOG} WHERE CHECK_ID = ?", [check_id])
    assert found, "check not found"
    session.sql(f"UPDATE {CATALOG} SET STATUS = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE CHECK_ID = ?",
                params=[status, check_id]).collect()
    return {"check_id": check_id, "status": status}


def _add_many(session, table: str, checks: List[Dict[str, Any]], origin: str, domain: Optional[str],
              source_run_id: Optional[str] = None) -> Dict[str, Any]:
    existing = _existing(session, table)
    added, skipped, problems = 0, 0, []
    for c in checks:
        cleaned, bad = normalize(c)
        if bad:
            problems.append(f"{c.get('target_column') or 'table'}: {'; '.join(bad)}")
            continue
        if any(same_check(e, cleaned) for e in existing):
            skipped += 1
            continue
        upsert(session, json.dumps({"table": table, "column": cleaned["target_column"], "severity": cleaned["severity"],
                                    "requirement": cleaned.get("requirement"), "definition": cleaned["definition"],
                                    "origin": origin, "domain_id": domain, "source_run_id": source_run_id}))
        existing.append(cleaned)
        added += 1
    return {"table": table, "added": added, "skipped": skipped, "problems": problems[:20]}


def profile_suggestions(session, table: str, domain_id: Optional[str] = None) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    """(table, checks, domain) that the table's latest profile shows to be true today
    (services/profiling/insights.suggested_checks)."""
    from services.catalog.profiles import gx_checks
    from services.profiling.insights import suggested_checks
    from services.profiling.procedures import read_document

    fqn = _fqn(table)
    parts = [p.strip('"') for p in re.findall(r'"(?:[^"]|"")+"|[^.]+', fqn)]
    found = rows(session, """SELECT PROFILE_STAGE_PATH FROM CORE.TABLE_PROFILES
                              WHERE UPPER(DATABASE_NAME) = UPPER(?) AND UPPER(SCHEMA_NAME) = UPPER(?)
                                AND UPPER(TABLE_NAME) = UPPER(?) AND SOURCE_FINGERPRINT <> ''
                              ORDER BY PROFILED_AT DESC LIMIT 1""", parts)
    assert found, f"{fqn} has no profile yet; profile it in the Profiling hub first"
    doc = read_document(session, found[0]["PROFILE_STAGE_PATH"])
    assert doc, "the staged profile is missing; re-profile the table"
    checks = [{"target_column": c.get("target_column"), "severity": c.get("severity") or "WARN",
               "requirement": (c.get("definition") or {}).get("evidence"),
               "definition": {k: v for k, v in (c.get("definition") or {}).items() if k != "evidence"}}
              for c in gx_checks(suggested_checks(doc))]
    return fqn, checks, _domain_for(session, fqn, domain_id)


def run_suggestions(session, run_id: str) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    """(table, checks, domain): a run's approved data quality checks, for the run's built target table."""
    from services.quality.scan import target_fqn
    from services.soda.procedures import _current_sttm

    sttm = _current_sttm(session, run_id)
    fqn = target_fqn(session, sttm)
    assert fqn, "the run's target model is not built yet, so its checks have no table to run against"
    stored = rows(session, """SELECT TARGET_COLUMN, CHECK_DEFINITION, SEVERITY, CLIENT_REQUIREMENT
                                FROM CORE.SODA_EXPECTATION_REGISTRY
                               WHERE RUN_ID = ? AND IS_CURRENT AND STATUS = 'APPROVED'""", [run_id])
    assert stored, "the run has no approved data quality checks"
    checks = [{"target_column": r["TARGET_COLUMN"], "severity": r["SEVERITY"], "requirement": r["CLIENT_REQUIREMENT"],
               "definition": {k: v for k, v in (variant(r["CHECK_DEFINITION"]) or {}).items()
                              if k not in ("backtest", "evidence")}} for r in stored]
    return _fqn(fqn), checks, sttm.get("DOMAIN_ID")


def seed_from_profile(session, table: str, domain_id: Optional[str] = None) -> Dict[str, Any]:
    """Straight into the catalog (kept for scripts; the Data Quality Store stages into the suite draft instead)."""
    fqn, checks, domain = profile_suggestions(session, table, domain_id)
    return _add_many(session, fqn, checks, "PROFILE", domain)


def promote_from_run(session, run_id: str) -> Dict[str, Any]:
    """Straight into the catalog (kept for scripts; the Data Quality Store stages into the suite draft instead)."""
    fqn, checks, domain = run_suggestions(session, run_id)
    return _add_many(session, fqn, checks, "RUN", domain, run_id)


# ---------------------------------------------------------------- Snowpark: scans


def run_table(session, table: str, check_ids: Optional[List[str]] = None, triggered_by: str = "UI") -> Dict[str, Any]:
    """Run a table's ACTIVE checks (or the chosen ones) with the caller's role, store the scan, update the catalog."""
    from services.qa.guard import check as guard
    from services.quality.scan import _scan_model, health

    fqn = _fqn(table)
    found = rows(session, f"""SELECT CHECK_ID, COLUMN_NAME, CHECK_TYPE, CHECK_DEFINITION, SEVERITY, DOMAIN_ID
                               FROM {CATALOG} WHERE TABLE_NAME = ? AND STATUS = 'ACTIVE' ORDER BY COLUMN_NAME, CHECK_TYPE""",
                 [fqn])
    if check_ids:
        wanted = set(check_ids)
        found = [r for r in found if r["CHECK_ID"] in wanted]
    assert found, "there are no active checks to run on this table"
    checks = [{"expectation_id": None, "target_table": fqn, "target_column": r["COLUMN_NAME"],
               "check_type": r["CHECK_TYPE"], "definition": variant(r["CHECK_DEFINITION"]) or {},
               "severity": r["SEVERITY"]} for r in found]
    try:
        prior = rows(session, """SELECT ROWS_SCANNED FROM CORE.CHECK_RUN WHERE TABLE_FQN = ? AND ROWS_SCANNED IS NOT NULL
                                  ORDER BY STARTED_AT DESC LIMIT 1""", [fqn])
        previous = float(prior[0]["ROWS_SCANNED"]) if prior else None
    except Exception:
        previous = None
    started = time.time()
    results, scanned = _scan_model(session, None, checks, [], fqn, guard, previous)
    for r, row in zip(results, found):
        r["catalog_check_id"] = row["CHECK_ID"]
    counts = {k: sum(1 for r in results if r["outcome"] == k) for k in ("PASS", "WARN", "FAIL", "NOT_EVALUATED", "ERROR")}
    scan = {"scan_id": str(uuid.uuid4()), "table": fqn, "domain_id": found[0].get("DOMAIN_ID"),
            "duration_ms": int((time.time() - started) * 1000), "checks": len(results), "passed": counts["PASS"],
            "warned": counts["WARN"], "failed": counts["FAIL"], "not_evaluated": counts["NOT_EVALUATED"],
            "errors": counts["ERROR"], "health": health(counts["PASS"], counts["WARN"], counts["FAIL"]),
            "rows_scanned": scanned, "triggered_by": triggered_by}
    _store(session, scan, results)
    for r in results:
        session.sql(f"""UPDATE {CATALOG} SET LAST_RUN_STATUS = ?, LAST_RUN_AT = CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
                               LAST_MEASURED = TRY_TO_DOUBLE(NULLIF(?, '')) WHERE CHECK_ID = ?""",
                    params=[r["outcome"], "" if r.get("measured") is None else str(r.get("measured")),
                            r["catalog_check_id"]]).collect()
    return {**scan, "results": results}


def _store(session, scan: Dict[str, Any], results: List[Dict[str, Any]]) -> None:
    insert_rows(session, "CORE.CHECK_RUN",
                ["SCAN_ID", "TARGET", "MODE", "SCOPE", "TABLE_FQN", "DOMAIN_ID", "DURATION_MS", "CHECKS", "PASSED",
                 "WARNED", "FAILED", "NOT_EVALUATED", "ERRORS", "HEALTH", "ROWS_SCANNED", "TRIGGERED_BY"],
                ["?", "?", "'MODEL'", "'TABLE'", "?", "NULLIF(?, '')", "?::NUMBER", "?::NUMBER", "?::NUMBER", "?::NUMBER",
                 "?::NUMBER", "?::NUMBER", "?::NUMBER", "NULLIF(?, '')::NUMBER", "NULLIF(?, '')::NUMBER", "?"],
                [[scan["scan_id"], scan["table"], scan["table"], scan.get("domain_id"), scan["duration_ms"],
                  scan["checks"], scan["passed"], scan["warned"], scan["failed"], scan["not_evaluated"], scan["errors"],
                  scan["health"], scan.get("rows_scanned"), scan["triggered_by"]]])
    insert_rows(session, "CORE.CHECK_RESULT",
                ["RESULT_ID", "SCAN_ID", "CATALOG_CHECK_ID", "TABLE_FQN", "TARGET_TABLE", "TARGET_COLUMN", "CHECK_TYPE",
                 "KIND", "DIMENSION", "SEVERITY", "OUTCOME", "MEASURED", "THRESHOLD", "FAILED_ROWS", "DETAIL",
                 "SAMPLE_ROWS", "SQL_TEXT", "DURATION_MS"],
                ["?", "?", "?", "?", "?", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')",
                 "NULLIF(?, '')", "?", "NULLIF(?, '')::FLOAT", "NULLIF(?, '')", "NULLIF(?, '')::NUMBER", "NULLIF(?, '')",
                 "PARSE_JSON(NULLIF(?, ''))", "NULLIF(?, '')", "NULLIF(?, '')::NUMBER"],
                [[str(uuid.uuid4()), scan["scan_id"], r["catalog_check_id"], scan["table"], scan["table"],
                  r.get("target_column"), r.get("check_type"), r.get("kind"), r.get("dimension"), r.get("severity"),
                  r["outcome"], r.get("measured"), r.get("threshold"), r.get("failed_rows"),
                  str(r.get("detail") or "")[:4000], json.dumps(r.get("sample")) if r.get("sample") else None,
                  str(r.get("sql") or "")[:16000], r.get("duration_ms")] for r in results])


def run_table_scan(session, table_fqn: str) -> Dict[str, Any]:
    """CORE.RUN_TABLE_SCAN(TABLE_FQN): the scheduled scan (Snowflake task)."""
    out = run_table(session, table_fqn, None, "SCHEDULE")
    out.pop("results", None)
    return out
