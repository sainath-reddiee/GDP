"""Functional test cases for a target model, written as plain SQL a tester can run (pure).

Every test traces to the STTM: one query, an objective and the expected result. Unless a test says otherwise,
the expectation is that the query returns no rows (failing rows are listed with a LIMIT for triage) or the
counts it returns match. Categories, in suite order:

  RECONCILIATION   row and key counts, source driving table vs target
  GRAIN            duplicate and missing business keys on the target
  COMPLETENESS     STTM-required columns populated
  SOURCE_TO_TARGET DIRECT columns moved unchanged (by business key, or as a set when no key is mapped)
  TRANSFORMATION   the STTM expression applied to the source equals the target value
  LOOKUP           unresolved lookups / surrogate keys and spoke-to-hub orphans
  VALUES           accepted values and constants
  JOINS            join fan-out against the driving table
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
ALIAS_PREFIX = re.compile(r"\b[a-z][a-z0-9_]{0,5}\.(?=[\"A-Za-z_])")
CATEGORIES = ("RECONCILIATION", "GRAIN", "COMPLETENESS", "SOURCE_TO_TARGET", "TRANSFORMATION", "LOOKUP", "VALUES", "JOINS")
CODES = {"RECONCILIATION": "RC", "GRAIN": "GR", "COMPLETENESS": "CP", "SOURCE_TO_TARGET": "ST",
         "TRANSFORMATION": "TX", "LOOKUP": "LK", "VALUES": "VL", "JOINS": "JN"}
FAILING_ROWS = 100
NON_DETERMINISTIC = re.compile(r"\b(CURRENT_\w+|SYSDATE|GETDATE|LOCALTIMESTAMP|UUID_STRING|RANDOM|UNIFORM|SEQ\d)\b", re.I)
SQL_WORDS = {
    "AS", "CASE", "WHEN", "THEN", "ELSE", "END", "NULL", "AND", "OR", "NOT", "IN", "IS", "LIKE", "ILIKE", "TRUE",
    "FALSE", "BETWEEN", "DATE", "VARCHAR", "STRING", "TEXT", "CHAR", "NUMBER", "NUMERIC", "INT", "INTEGER", "BIGINT",
    "FLOAT", "DOUBLE", "DECIMAL", "BOOLEAN", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "TIME",
    "DISTINCT", "OVER", "PARTITION", "BY", "ORDER", "ASC", "DESC", "DAY", "MONTH", "YEAR", "HOUR", "MINUTE", "SECOND",
    "WEEK", "QUARTER", "FROM", "FOR", "BOTH", "LEADING", "TRAILING", "ESCAPE", "IGNORE", "RESPECT", "NULLS", "FIRST",
    "LAST", "ROWS", "RANGE", "UNBOUNDED", "PRECEDING", "FOLLOWING", "CURRENT", "ROW", "WITHIN", "GROUP",
}


def unknown_columns(expr: str, columns: Sequence[str]) -> List[str]:
    """Identifiers in an STTM expression that are not columns of its source table (functions and keywords skipped)."""
    if not columns:
        return []
    known = {c.upper() for c in columns}
    text = re.sub(r"'(?:[^']|'')*'", " ", expr)
    out = []
    for m in re.finditer(r'"([^"]+)"|\b([A-Za-z_][A-Za-z0-9_$]*)\b(\s*\()?', text):
        name, is_call = (m.group(1) or m.group(2) or ""), bool(m.group(3))
        upper = name.upper()
        if is_call or upper in SQL_WORDS or upper in known or NON_DETERMINISTIC.match(upper) or upper in out:
            continue
        out.append(upper)
    return out


def ident(name: str) -> str:
    return name if SAFE.match(name or "") and name == name.upper() else '"' + str(name).replace('"', '""') + '"'


def lit(value: Any) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def source_expression(line: Dict[str, Any]) -> Optional[str]:
    """STTM expression rewritten to run against the bare source table (aliases dropped, {col} filled)."""
    expr = str(line.get("transformation") or "").strip()
    if not expr:
        return None
    if line.get("source_column"):
        expr = expr.replace("{col}", ident(str(line["source_column"])))
    return ALIAS_PREFIX.sub("", expr)


def _keys(lines: Sequence[Dict[str, Any]], business_keys: Sequence[str], driving: str) -> List[Tuple[str, str]]:
    """(source column, target column) pairs for business keys mapped DIRECT from the driving table."""
    by_target = {str(l["target_column"]).upper(): l for l in lines}
    out = []
    for key in business_keys:
        line = by_target.get(str(key).upper())
        if (line and str(line.get("source_table") or "").upper() == driving and line.get("source_column")
                and not line.get("transformation")):
            out.append((str(line["source_column"]), str(key).upper()))  # real source spelling for SQL
    return out if len(out) == len(business_keys) else []


class Suite:
    def __init__(self) -> None:
        self.tests: List[Dict[str, Any]] = []
        self.counters: Dict[str, int] = {}

    def add(self, category: str, title: str, objective: str, sql: str, expected: str, severity: str = "HIGH",
            target_column: Optional[str] = None, source: Optional[str] = None, warning: Optional[str] = None) -> None:
        n = self.counters[category] = self.counters.get(category, 0) + 1
        self.tests.append({"test_id": f"QA-{CODES[category]}-{n:03d}", "category": category, "title": title,
                           "objective": objective, "sql": sql.strip() + ";", "expected": expected, "severity": severity,
                           "target_column": target_column, "source": source, "origin": "GENERATED",
                           "warning": warning})


def build_suite(target: Dict[str, Any], sources: Dict[str, str], lines: List[Dict[str, Any]],
                business_keys: Sequence[str] = (), graph: Optional[Dict[str, Any]] = None,
                spec: Optional[Dict[str, Any]] = None,
                columns: Optional[Dict[str, Sequence[str]]] = None) -> List[Dict[str, Any]]:
    """target: {"fqn": "DB.SCHEMA.TABLE", "name": "TABLE"}; sources: {SOURCE_TABLE: "DB.SCHEMA.TABLE"}."""
    suite = Suite()
    tgt, name = target["fqn"], target.get("name") or target["fqn"].split(".")[-1]
    graph = graph or {}
    spec = spec or {}
    columns = {k.upper(): list(v) for k, v in (columns or {}).items()}

    def real(table: str, column: str) -> str:
        """Join keys arrive upper-cased from the join planner; SQL needs the column's stored spelling."""
        return next((c for c in columns.get(str(table).upper(), []) if c.upper() == column.upper()), column)
    mapped = [l for l in lines if str(l.get("mapping_type") or "").upper() != "UNMAPPED"]
    counts: Dict[str, int] = {}
    for l in mapped:
        if l.get("source_table"):
            t = str(l["source_table"]).upper()
            counts[t] = counts.get(t, 0) + 1
    driving = str(graph.get("driving_table") or (max(counts, key=counts.get) if counts else "")).upper()
    src = sources.get(driving)
    keys = _keys(lines, business_keys, driving) if src else []
    tkeys = [ident(k) for k in business_keys]

    # RECONCILIATION
    if src:
        suite.add("RECONCILIATION", "Row count: source vs target",
                  f"Every row of the driving source {driving} lands in {name} once.",
                  f"SELECT\n    (SELECT COUNT(*) FROM {src}) AS source_rows,\n    (SELECT COUNT(*) FROM {tgt}) AS target_rows,\n"
                  "    source_rows - target_rows AS difference",
                  "difference = 0, or explained by documented filters", "HIGH", source=driving)
        if keys:
            sk = ", ".join(ident(s) for s, _ in keys)
            suite.add("RECONCILIATION", "Distinct business keys: source vs target",
                      "No business key is lost or invented by the load.",
                      f"SELECT\n    (SELECT COUNT(*) FROM (SELECT DISTINCT {sk} FROM {src})) AS source_keys,\n"
                      f"    (SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(tkeys)} FROM {tgt})) AS target_keys,\n"
                      "    source_keys - target_keys AS difference",
                      "difference = 0", "HIGH", source=driving)
            suite.add("RECONCILIATION", "Keys in source missing from target",
                      "List source business keys that did not reach the target.",
                      f"SELECT {sk} FROM {src}\nMINUS\nSELECT {', '.join(tkeys)} FROM {tgt}\nLIMIT {FAILING_ROWS}",
                      "0 rows", "HIGH", source=driving)

    # GRAIN
    if tkeys:
        joined = ", ".join(tkeys)
        suite.add("GRAIN", f"No duplicate business keys ({', '.join(business_keys)})",
                  f"{name} holds one row per business key.",
                  f"SELECT {joined}, COUNT(*) AS rows_per_key\nFROM {tgt}\nGROUP BY {joined}\nHAVING COUNT(*) > 1\n"
                  f"ORDER BY rows_per_key DESC\nLIMIT {FAILING_ROWS}", "0 rows", "CRITICAL")
        suite.add("GRAIN", "No missing business keys", "Every row carries its business key.",
                  f"SELECT COUNT(*) AS rows_without_key\nFROM {tgt}\nWHERE " + " OR ".join(f"{k} IS NULL" for k in tkeys),
                  "rows_without_key = 0", "CRITICAL")

    # COMPLETENESS
    required = [l for l in mapped if not l.get("nullable_rule") and str(l["target_column"]).upper()
                not in {k.upper() for k in business_keys}]
    if required:
        metrics = ",\n".join(f"    COUNT_IF({ident(str(l['target_column']))} IS NULL) AS {ident(str(l['target_column']))}_nulls"
                             if SAFE.match(str(l["target_column"])) else
                             f"    COUNT_IF({ident(str(l['target_column']))} IS NULL)" for l in required)
        suite.add("COMPLETENESS", f"Required columns populated ({len(required)})",
                  "Columns the STTM marks as required are never null.",
                  f"SELECT\n{metrics}\nFROM {tgt}", "every *_nulls value = 0", "HIGH")

    # SOURCE_TO_TARGET and TRANSFORMATION
    direct = [l for l in mapped if l.get("source_column") and not l.get("transformation")
              and str(l.get("source_table") or "").upper() == driving
              and str(l["target_column"]).upper() not in {k.upper() for k in business_keys}]
    if src and direct and keys:
        on = " AND ".join(f"t.{tk} = s.{ident(sc)}" for (sc, _), tk in zip(keys, tkeys))
        sel_keys = ", ".join(f"s.{ident(sc)}" for sc, _ in keys)
        for l in direct:
            sc, tc = ident(str(l["source_column"])), ident(str(l["target_column"]))
            suite.add("SOURCE_TO_TARGET", f"{l['target_column']} equals {driving}.{l['source_column']}",
                      "DIRECT mapping: the value is moved without change.",
                      f"SELECT {sel_keys}, s.{sc} AS source_value, t.{tc} AS target_value\nFROM {src} AS s\n"
                      f"JOIN {tgt} AS t ON {on}\nWHERE NOT EQUAL_NULL(s.{sc}, t.{tc})\nLIMIT {FAILING_ROWS}",
                      "0 rows", "MEDIUM", str(l["target_column"]), f"{driving}.{l['source_column']}")
    elif src and direct:
        scols = ", ".join(ident(str(l["source_column"])) for l in direct)
        tcols = ", ".join(ident(str(l["target_column"])) for l in direct)
        suite.add("SOURCE_TO_TARGET", f"DIRECT columns match as a set ({len(direct)})",
                  "No business key is mapped directly, so the DIRECT columns are compared as row sets.",
                  f"SELECT {scols} FROM {src}\nMINUS\nSELECT {tcols} FROM {tgt}\nLIMIT {FAILING_ROWS}",
                  "0 rows (repeat with the two SELECTs swapped for the reverse direction)", "MEDIUM", source=driving)

    for l in mapped:
        expr = source_expression(l)
        if not expr or str(l.get("mapping_type") or "").upper() == "CONSTANT":
            continue
        tc = ident(str(l["target_column"]))
        table = str(l.get("source_table") or "").upper() or driving
        where = sources.get(table)
        rule = f"Rule: {str(l.get('transformation'))[:300]}"
        if NON_DETERMINISTIC.search(expr) or not re.search(r"[A-Za-z_]", re.sub(r"'(?:[^']|'')*'", "", expr)):
            literal = not re.search(r"[A-Za-z_]", re.sub(r"'(?:[^']|'')*'", "", expr))
            sql = (f"SELECT COUNT(*) AS wrong\nFROM {tgt}\nWHERE NOT EQUAL_NULL({tc}, {expr})" if literal
                   else f"SELECT COUNT(*) AS missing\nFROM {tgt}\nWHERE {tc} IS NULL")
            suite.add("TRANSFORMATION", f"{l['target_column']} follows the STTM rule",
                      rule + ("" if literal else " (load-time value: checked for presence only)"),
                      sql, "wrong = 0" if literal else "missing = 0", "MEDIUM", str(l["target_column"]))
            continue
        if not where:
            continue
        missing = unknown_columns(expr, columns.get(table, []))
        warning = (f"The STTM rule references {', '.join(missing)}, not a column of {table}; fix the STTM before "
                   "running this test.") if missing else None
        if keys and table == driving:
            sk = ", ".join(ident(s_) for s_, _ in keys)
            on = " AND ".join(f"a.{tk} = e.{ident(s_)}" for (s_, _), tk in zip(keys, tkeys))
            sql = (f"WITH expected AS (\n    SELECT {sk}, {expr} AS expected_value\n    FROM {where}\n),\n"
                   f"actual AS (\n    SELECT {', '.join(tkeys)}, {tc} AS actual_value\n    FROM {tgt}\n)\n"
                   f"SELECT e.*, a.actual_value\nFROM expected AS e\nJOIN actual AS a ON {on}\n"
                   f"WHERE NOT EQUAL_NULL(e.expected_value, a.actual_value)\nLIMIT {FAILING_ROWS}")
            expected = "0 rows"
        else:
            sql = (f"-- No direct key path from {table} to {name}; compare the value distributions.\n"
                   f"SELECT 'expected' AS side, {expr} AS value, COUNT(*) AS n FROM {where} GROUP BY 2\n"
                   f"UNION ALL\nSELECT 'actual', {tc}, COUNT(*) FROM {tgt} GROUP BY 2\nORDER BY value, side")
            expected = "matching counts per value on both sides"
        suite.add("TRANSFORMATION", f"{l['target_column']} follows the STTM rule", rule, sql, expected, "HIGH",
                  str(l["target_column"]), f"{table}.{l.get('source_column') or ''}".strip("."), warning)

    # LOOKUP
    for l in mapped:
        col = str(l["target_column"]).upper()
        kind = str(l.get("mapping_type") or "").upper()
        if kind == "LOOKUP" or (col.endswith("_SKEY") and col != str(spec.get("hub_fk") or "").upper()):
            suite.add("LOOKUP", f"{col} resolves for every row",
                      "A lookup that finds nothing leaves the surrogate key empty; those rows need a reference fix.",
                      f"SELECT COUNT(*) AS unresolved\nFROM {tgt}\nWHERE {ident(col)} IS NULL",
                      "unresolved = 0, or only rows whose source code is blank", "MEDIUM", col)
    fk, hub = spec.get("hub_fk"), spec.get("hub")
    if spec.get("role") == "spoke" and fk and hub and "." in tgt:
        hub_fqn = ".".join(tgt.split(".")[:2] + [ident(str(hub).upper())])
        suite.add("LOOKUP", f"Every {fk} exists in {hub}",
                  f"{name} is a spoke of {hub}; orphans break the hub relationship.",
                  f"SELECT t.{ident(str(fk).upper())}, COUNT(*) AS orphan_rows\nFROM {tgt} AS t\n"
                  f"WHERE t.{ident(str(fk).upper())} IS NOT NULL\n  AND NOT EXISTS (SELECT 1 FROM {hub_fqn} AS h "
                  f"WHERE h.{ident(str(fk).upper())} = t.{ident(str(fk).upper())})\nGROUP BY 1\nLIMIT {FAILING_ROWS}",
                  "0 rows", "CRITICAL", str(fk).upper())

    # VALUES
    for l in mapped:
        col = ident(str(l["target_column"]))
        values = l.get("accepted_values") or []
        if values:
            suite.add("VALUES", f"{l['target_column']} only holds accepted values",
                      f"STTM accepted values: {', '.join(map(str, values))[:200]}",
                      f"SELECT {col}, COUNT(*) AS n\nFROM {tgt}\nWHERE {col} IS NOT NULL AND {col}::STRING NOT IN "
                      f"({', '.join(lit(v) for v in values)})\nGROUP BY 1\nORDER BY n DESC",
                      "0 rows", "HIGH", str(l["target_column"]))
        if str(l.get("mapping_type") or "").upper() == "CONSTANT" and l.get("default_value") is not None:
            suite.add("VALUES", f"{l['target_column']} is the constant {l['default_value']}",
                      "CONSTANT mapping.", f"SELECT COUNT(*) AS wrong\nFROM {tgt}\nWHERE NOT EQUAL_NULL({col}::STRING, "
                      f"{lit(l['default_value'])})", "wrong = 0", "LOW", str(l["target_column"]))

    # JOINS
    for j in graph.get("joins") or []:
        left, right = str(j.get("left_table") or "").upper(), str(j.get("right_table") or "").upper()
        if left not in sources or right not in sources or not j.get("keys"):
            continue
        cond = " AND ".join(f"l.{ident(real(left, a.strip()))} = r.{ident(real(right, (b or a).strip()))}"
                            for a, _, b in (str(k).partition("=") for k in j["keys"]))
        suite.add("JOINS", f"{left} to {right} does not fan out",
                  f"{j.get('join_type', 'LEFT')} join on {', '.join(j['keys'])} ({j.get('cardinality', '?')}); "
                  "joined rows must equal driving rows unless the plan says 1:N.",
                  f"SELECT\n    (SELECT COUNT(*) FROM {sources[left]}) AS left_rows,\n"
                  f"    (SELECT COUNT(*) FROM {sources[left]} AS l {j.get('join_type', 'LEFT')} JOIN {sources[right]} AS r ON {cond}) AS joined_rows,\n"
                  "    joined_rows - left_rows AS extra_rows",
                  "extra_rows = 0 (N:1 or 1:1)", "HIGH", source=f"{left} > {right}")
    return suite.tests


def script(target_name: str, tests: List[Dict[str, Any]]) -> str:
    """All tests as one .sql file a tester can open in Snowsight."""
    out = [f"-- QA test suite for {target_name}", "-- Each block: objective, expected result, query.", ""]
    for t in tests:
        out += [f"-- {t['test_id']} [{t['category']}] {t['title']}",
                f"-- Objective: {t['objective']}",
                f"-- Expected: {t['expected']}",
                t["sql"], ""]
    return "\n".join(out)
