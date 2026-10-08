"""Profile Oracle tables where they live: the statistics are computed by Oracle (sampled above a size limit)
and only aggregates leave the database. The result goes through the platform's own profile builder, so value-based
PII, date formats, keys, quality scores and AI review work exactly as for Snowflake tables."""

from __future__ import annotations

import datetime as dt
import decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.source.oracle.connect import cursor

SAMPLE_TARGET_ROWS = 100_000
TOP_VALUES = 10
TOP_PATTERNS = 5
PLACEHOLDERS = ("", "N/A", "NA", "NULL", "NONE", "<NULL>", ".")
UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
LOWER = "abcdefghijklmnopqrstuvwxyz"


def q(name: str) -> str:
    """Oracle identifier, exact spelling (quoted)."""
    assert name and '"' not in name and "\x00" not in name, f"unsafe identifier {name!r}"
    return '"' + name + '"'


def table_ref(owner: str, table: str) -> str:
    return f"{q(owner.upper())}.{q(table)}"


def value_sql(c: Dict[str, Any]) -> str:
    """The column's value expression without its alias (XMLSERIALIZE(...), SYS_EXTRACT_UTC(...) or the quoted name)."""
    expr = c.get("expr")
    if expr and " AS " in expr:
        return expr.rsplit(" AS ", 1)[0]
    return q(c["column_name"])


def profileable(columns: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Columns that can be profiled: readable, and not LONG / LONG RAW (Oracle allows no function on those)."""
    return [c for c in columns if not c.get("skip_reason")
            and str(c.get("data_type") or "").upper() not in ("LONG", "LONG RAW")]


def sample_clause(estimated_rows: Optional[int], target: int = SAMPLE_TARGET_ROWS) -> Tuple[str, bool]:
    """SAMPLE(p) so about `target` rows are read; nothing for small or unknown tables. (sql, approximate)."""
    if not estimated_rows or estimated_rows <= target * 2:
        return "", False
    pct = max(0.000001, min(99.999999, 100.0 * target / estimated_rows))
    return f" SAMPLE ({pct:.6f})", True


def stats_sql(owner: str, table: str, columns: Sequence[Dict[str, Any]], sample: str) -> str:
    """One pass; aliases match services.profiling.profiler.column_stats (N, V, D, MIN, MAX, LMIN, LMAX, LAVG, AVG)."""
    src = table_ref(owner, table)
    parts = ["COUNT(*) AS ROW_COUNT"]
    placeholders = ", ".join(f"'{p}'" for p in PLACEHOLDERS if p)
    for i, c in enumerate(columns):
        col, family, lob = value_sql(c), c["family"], c.get("lob")
        parts.append(f"COUNT({col}) AS N{i}")
        if lob:  # LOBs cannot be compared, grouped or counted distinct; length is still useful
            if family == "TEXT":
                parts += [f"COUNT({col}) AS V{i}", f"MIN(DBMS_LOB.GETLENGTH({col})) AS LMIN{i}",
                          f"MAX(DBMS_LOB.GETLENGTH({col})) AS LMAX{i}", f"AVG(DBMS_LOB.GETLENGTH({col})) AS LAVG{i}"]
            else:
                parts.append(f"COUNT({col}) AS V{i}")
            continue
        if family == "TEXT":
            parts.append(f"COUNT(CASE WHEN TRIM({col}) IS NOT NULL AND UPPER(TRIM({col})) NOT IN ({placeholders}) "
                         f"THEN 1 END) AS V{i}")
            parts += [f"MIN(SUBSTR({col}, 1, 200)) AS MIN{i}", f"MAX(SUBSTR({col}, 1, 200)) AS MAX{i}",
                      f"MIN(LENGTH({col})) AS LMIN{i}", f"MAX(LENGTH({col})) AS LMAX{i}", f"AVG(LENGTH({col})) AS LAVG{i}"]
        else:
            parts += [f"COUNT({col}) AS V{i}", f"MIN({col}) AS MIN{i}", f"MAX({col}) AS MAX{i}"]
            if family == "NUMBER":
                parts.append(f"AVG({col}) AS AVG{i}")
        parts.append(f"APPROX_COUNT_DISTINCT({col}) AS D{i}" if sample else f"COUNT(DISTINCT {col}) AS D{i}")
    return f"SELECT {', '.join(parts)} FROM {src}{sample}"


def _value_expr(c: Dict[str, Any]) -> str:
    col = value_sql(c)
    if c["family"] == "TIMESTAMP":
        return f"TO_CHAR({col}, 'YYYY-MM-DD HH24:MI:SS')"
    if c["family"] == "TEXT":
        return f"SUBSTR({col}, 1, 200)"
    return f"TO_CHAR({col})"


def frequencies_sql(owner: str, table: str, columns: Sequence[Dict[str, Any]], sample: str) -> Optional[str]:
    """Top values per column (C = column position, V = value, N = count), UNION ALL of ranked subqueries."""
    src = table_ref(owner, table)
    branches = []
    for i, c in enumerate(columns):
        if c.get("lob"):
            continue
        expr = _value_expr(c)
        branches.append(f"SELECT {i} AS C, V, N FROM (SELECT {expr} AS V, COUNT(*) AS N FROM {src}{sample} "
                        f"WHERE {value_sql(c)} IS NOT NULL GROUP BY {expr} ORDER BY N DESC "
                        f"FETCH FIRST {TOP_VALUES} ROWS ONLY)")
    return " UNION ALL ".join(branches) or None


def patterns_sql(owner: str, table: str, columns: Sequence[Dict[str, Any]], sample: str) -> Optional[str]:
    """Value shapes like the Snowflake profiler: letters A/a, digits 9 (P = shape, N = count)."""
    src = table_ref(owner, table)
    branches = []
    for i, c in enumerate(columns):
        if c.get("lob") or c["family"] != "TEXT":
            continue
        col = value_sql(c)
        shape = f"TRANSLATE(SUBSTR({col}, 1, 60), '{UPPER}{LOWER}0123456789', '{'A' * 26}{'a' * 26}{'9' * 10}')"
        branches.append(f"SELECT {i} AS C, P, N FROM (SELECT {shape} AS P, COUNT(*) AS N FROM {src}{sample} "
                        f"WHERE {col} IS NOT NULL GROUP BY {shape} ORDER BY N DESC FETCH FIRST {TOP_PATTERNS} ROWS ONLY)")
    return " UNION ALL ".join(branches) or None


def plain(value: Any) -> Any:
    """Oracle driver values -> JSON-safe values for the profile document."""
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 2 ** 53 else float(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat(sep=" ")[:19] if isinstance(value, dt.datetime) else value.isoformat()
    if isinstance(value, bytes):
        return value.hex()[:64]
    return value


def _rows(cur, sql: Optional[str]) -> List[Dict[str, Any]]:
    if not sql:
        return []
    cur.execute(sql)
    names = [d[0].upper() for d in cur.description]
    return [{k: plain(v) for k, v in zip(names, row)} for row in cur.fetchall()]


def profile_table(conn, owner: str, table: str, columns: Sequence[Dict[str, Any]],
                  estimated_rows: Optional[int]) -> Tuple[List[Dict[str, Any]], int, bool]:
    """(profile columns, row count, approximate) built with the platform profiler."""
    from services.profiling import profiler

    columns = profileable(columns)
    assert columns, f"{table} has no columns that can be profiled"
    sample, approximate = sample_clause(estimated_rows)
    cur = cursor(conn, 5000)
    stats_row = _rows(cur, stats_sql(owner, table, columns, sample))[0]
    sampled_rows = int(stats_row.get("ROW_COUNT") or 0)
    row_count = int(estimated_rows) if approximate and estimated_rows else sampled_rows
    freqs = profiler.group_rows(_rows(cur, frequencies_sql(owner, table, columns, sample)), "V")
    pats = profiler.group_rows(_rows(cur, patterns_sql(owner, table, columns, sample)), "P")
    out = []
    for i, c in enumerate(columns):
        stats = profiler.column_stats(stats_row, i, sampled_rows, approximate)
        if approximate and sampled_rows:  # scale counts from the sample to the table
            factor = row_count / sampled_rows
            for k in ("row_count", "null_count", "physical_null_count", "placeholder_null_count", "duplicate_count"):
                if stats.get(k) is not None:
                    stats[k] = int(round(stats[k] * factor))
        built = profiler.build_profile(c["column_name"], c["snowflake_type"], stats, freqs.get(i, []),
                                       [{"pattern": p["value"], "count": p["count"]} for p in pats.get(i, [])],
                                       approximate=approximate)
        built["source_type"] = c["oracle_type"]
        built["constraints"] = c.get("constraints") or []
        if c.get("comment"):
            built.setdefault("description", c["comment"])
        if "PK" in built["constraints"] or "UNIQUE" in built["constraints"]:
            built["potential_key"] = True
        out.append(built)
    return out, row_count, approximate
