"""GOVERNANCE.DATA_PROFILES: every profiling pass of every table, one row per column, so profiles can be searched
across runs by domain, table, run and tags, and a column's history compared pass to pass."""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Sequence

from services.common.sql import clip, insert_rows

TABLE = "GOVERNANCE.DATA_PROFILES"
TOP_VALUES = 10
COLUMNS = ["PROFILE_ID", "SOURCE_NAME", "DATABASE_NAME", "SCHEMA_NAME", "TABLE_NAME", "COLUMN_NAME", "ORDINAL",
           "RUN_ID", "DOMAIN_ID", "DATA_TYPE", "FAMILY", "SEMANTIC_TYPE", "PII_CLASSIFICATION", "ROW_COUNT",
           "NULL_COUNT", "NULL_PCT", "DISTINCT_COUNT", "CARDINALITY", "CARDINALITY_RATIO", "MIN_VALUE", "MAX_VALUE",
           "POTENTIAL_KEY", "TOP_VALUES", "HISTOGRAM", "DESCRIPTION", "IS_APPROXIMATE", "PROFILER_VERSION"]
# The run's domain, else the registered source's domain; NULL when neither is known.
DOMAIN_SQL = ("COALESCE((SELECT MAX(DOMAIN_ID) FROM CORE.WORKFLOW_RUN WHERE RUN_ID = NULLIF(?, '')), "
              "(SELECT MAX(DOMAIN_ID) FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_NAME = ?))")
EXPRS = ["?", "?", "?", "?", "?", "?", "?::NUMBER", "NULLIF(?, '')", DOMAIN_SQL, "NULLIF(?, '')", "NULLIF(?, '')",
         "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')::NUMBER", "NULLIF(?, '')::NUMBER", "NULLIF(?, '')::FLOAT",
         "NULLIF(?, '')::NUMBER", "NULLIF(?, '')", "NULLIF(?, '')::FLOAT", "NULLIF(?, '')", "NULLIF(?, '')",
         "?::BOOLEAN", "PARSE_JSON(NULLIF(?, ''))", "PARSE_JSON(NULLIF(?, ''))", "NULLIF(?, '')", "?::BOOLEAN",
         "NULLIF(?, '')"]


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def column_metrics(column: Dict[str, Any], row_count: Optional[int]) -> Dict[str, Any]:
    """The catalog's view of one profiled column (a column of profiler.profile_document)."""
    stats = column.get("statistics") or {}
    nulls = _num(stats.get("null_count"))
    if nulls is None:
        nulls = _num(stats.get("physical_null_count"))
    rows = _num(stats.get("row_count"))
    rows = rows if rows is not None else _num(row_count)
    null_pct = _num(stats.get("null_percentage"))
    if null_pct is None and rows:
        null_pct = round(100.0 * (nulls or 0) / rows, 4)
    distinct = _num(stats.get("distinct_count"))
    non_null = (rows or 0) - (nulls or 0)
    ratio = round(distinct / non_null, 6) if distinct is not None and non_null > 0 else None
    pii = (column.get("pii_classification") or "NONE").upper()
    return {
        "column_name": column.get("column_name"),
        "data_type": column.get("data_type"),
        "family": column.get("family"),
        "semantic_type": column.get("semantic_type"),
        "pii_classification": pii,
        "row_count": None if rows is None else int(rows),
        "null_count": None if nulls is None else int(nulls),
        "null_pct": null_pct,
        "distinct_count": None if distinct is None else int(distinct),
        "cardinality": column.get("cardinality"),
        "cardinality_ratio": ratio,
        # the profiler already drops min/max of PII columns; never store them here even if a document has them
        "min_value": None if pii != "NONE" else clip(stats.get("min"), 1024) or None,
        "max_value": None if pii != "NONE" else clip(stats.get("max"), 1024) or None,
        "potential_key": bool(column.get("potential_key")),
        "top_values": (stats.get("frequency_distribution") or column.get("sample_values") or [])[:TOP_VALUES],
        "histogram": stats.get("histogram") if pii == "NONE" else None,
        "description": clip(column.get("description"), 4000) or None,
    }


def column_rows(profile_id: str, key: Sequence[str], document: Dict[str, Any], run_id: Optional[str]) -> List[List[Any]]:
    """Insert values for every column of a profile document. `key` is (source, database, schema, table)."""
    source, database, schema, table = key
    out = []
    for i, column in enumerate(document.get("columns") or []):
        m = column_metrics(column, document.get("row_count"))
        if not m["column_name"]:
            continue
        out.append([profile_id, source, database, schema, table, m["column_name"], i + 1, run_id or "",
                    run_id or "", source, m["data_type"], m["family"], m["semantic_type"], m["pii_classification"],
                    m["row_count"], m["null_count"], m["null_pct"], m["distinct_count"], m["cardinality"],
                    m["cardinality_ratio"], m["min_value"], m["max_value"], m["potential_key"],
                    m["top_values"] or "", m["histogram"] or "", m["description"],
                    bool(document.get("approximate")), document.get("profiler_version")])
    return out


def record(session, key: Sequence[str], document: Dict[str, Any], run_id: Optional[str]) -> Optional[str]:
    """Copy one profiling pass into the catalog. Returns the PROFILE_ID, or None when nothing was written
    (V036 not deployed, or any other error): the profile itself is already stored, so this never raises."""
    profile_id = str(uuid.uuid4())
    try:
        values = column_rows(profile_id, key, document, run_id)
        if not values:
            return None
        insert_rows(session, TABLE, COLUMNS, EXPRS, values)
        return profile_id
    except Exception:
        return None


# ---------------------------------------------------------------- hub queries (API side, %s binds)

SORTS = {"recent": "PROFILED_AT DESC", "table": "TABLE_NAME", "nulls": "AVG_NULL_PCT DESC NULLS LAST",
         "rows": "ROW_COUNT DESC NULLS LAST", "drift": "NULL_DRIFT DESC NULLS LAST"}


def search_sql(domain: Optional[str] = None, table: Optional[str] = None, run_id: Optional[str] = None,
               tag_keys: Optional[List[str]] = None, q: Optional[str] = None, bookmarked_by: Optional[str] = None,
               sort: str = "recent", limit: int = 50, offset: int = 0) -> tuple[str, tuple]:
    """Latest pass per table with table-level rollups, filtered. Tags are resolved to table keys by the caller
    (CORE.TAG_ASSIGNMENT), so the filter here is a plain key list."""
    where, params = [], []
    if domain:
        where.append("(L.DOMAIN_ID = %s OR UPPER(D.DOMAIN_NAME) = UPPER(%s))")
        params += [domain, domain]
    if table:
        where.append("L.TABLE_NAME ILIKE %s")
        params.append(f"%{table}%")
    if run_id:
        where.append("L.RUN_ID = %s")
        params.append(run_id)
    if q:
        where.append("(L.TABLE_NAME ILIKE %s OR L.SCHEMA_NAME ILIKE %s OR L.DATABASE_NAME ILIKE %s)")
        params += [f"%{q}%"] * 3
    if tag_keys is not None:
        where.append("ARRAY_CONTAINS(L.TABLE_FQN::VARIANT, PARSE_JSON(%s)::ARRAY)")
        params.append(_json_list(tag_keys))
    if bookmarked_by:
        where.append("EXISTS (SELECT 1 FROM GOVERNANCE.PROFILE_BOOKMARK B WHERE B.USER_NAME = %s AND B.TABLE_FQN = L.TABLE_FQN)")
        params.append(bookmarked_by)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = SORTS.get(sort, SORTS["recent"])
    sql = f"""
        WITH P AS (
            SELECT PROFILE_ID, SOURCE_NAME, DATABASE_NAME, SCHEMA_NAME, TABLE_NAME,
                   UPPER(DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TABLE_NAME) AS TABLE_FQN,
                   MAX(RUN_ID) AS RUN_ID, MAX(DOMAIN_ID) AS DOMAIN_ID, MAX(ROW_COUNT) AS ROW_COUNT,
                   COUNT(*) AS COLUMN_COUNT, ROUND(AVG(NULL_PCT), 2) AS AVG_NULL_PCT,
                   COUNT_IF(POTENTIAL_KEY) AS KEY_CANDIDATES, COUNT_IF(PII_CLASSIFICATION <> 'NONE') AS PII_COLUMNS,
                   MAX(PROFILED_AT) AS PROFILED_AT, MAX(PROFILED_BY) AS PROFILED_BY,
                   ARRAY_AGG(DISTINCT SEMANTIC_TYPE) AS SEMANTIC_TYPES
              FROM GOVERNANCE.DATA_PROFILES
             GROUP BY PROFILE_ID, SOURCE_NAME, DATABASE_NAME, SCHEMA_NAME, TABLE_NAME
        ), L AS (
            SELECT P.*, COUNT(*) OVER (PARTITION BY TABLE_FQN) AS PASSES,
                   -- change in the average null rate since the previous pass (null with one pass)
                   ROUND(ABS(AVG_NULL_PCT - LEAD(AVG_NULL_PCT) OVER (PARTITION BY TABLE_FQN ORDER BY PROFILED_AT DESC)), 2)
                       AS NULL_DRIFT
              FROM P
           QUALIFY ROW_NUMBER() OVER (PARTITION BY TABLE_FQN ORDER BY PROFILED_AT DESC) = 1
        )
        SELECT L.PROFILE_ID, L.SOURCE_NAME, L.DATABASE_NAME, L.SCHEMA_NAME, L.TABLE_NAME, L.TABLE_FQN, L.RUN_ID,
               L.DOMAIN_ID, D.DOMAIN_NAME, L.ROW_COUNT, L.COLUMN_COUNT, L.AVG_NULL_PCT, L.KEY_CANDIDATES,
               L.PII_COLUMNS, L.PROFILED_AT::VARCHAR AS PROFILED_AT, L.PROFILED_BY, L.SEMANTIC_TYPES, L.PASSES,
               L.NULL_DRIFT, COUNT(*) OVER () AS TOTAL
          FROM L LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = L.DOMAIN_ID
          {clause}
         ORDER BY {order}
         LIMIT {int(max(1, min(limit, 200)))} OFFSET {int(max(0, offset))}"""
    return sql, tuple(params)


def _json_list(values: List[str]) -> str:
    import json

    return json.dumps([str(v).upper() for v in values])


def drift_between(previous: List[Dict[str, Any]], current: List[Dict[str, Any]],
                  null_jump: float = 10.0, distinct_jump: float = 0.5) -> Dict[str, Any]:
    """Column drift between two passes of the same table (rows keyed by lower-case column names, as the API returns
    them). Flags added, removed and retyped columns, null-rate jumps of `null_jump` points and distinct-count changes
    above `distinct_jump` (relative)."""
    before = {r["column_name"]: r for r in previous}
    after = {r["column_name"]: r for r in current}
    columns: Dict[str, List[str]] = {}

    def flag(name: str, reason: str) -> None:
        columns.setdefault(name, []).append(reason)

    for name, now in after.items():
        was = before.get(name)
        if was is None:
            flag(name, "added")
            continue
        if (was.get("data_type") or "") != (now.get("data_type") or ""):
            flag(name, f"type {was.get('data_type')} -> {now.get('data_type')}")
        a, b = _num(was.get("null_pct")), _num(now.get("null_pct"))
        if a is not None and b is not None and abs(b - a) >= null_jump:
            flag(name, f"nulls {a:.1f}% -> {b:.1f}%")
        a, b = _num(was.get("distinct_count")), _num(now.get("distinct_count"))
        if a and b is not None and abs(b - a) / a > distinct_jump:
            flag(name, f"distinct {int(a)} -> {int(b)}")
    for name in before:
        if name not in after:
            flag(name, "removed")
    return {"changed": bool(columns), "columns": columns,
            "drifted_columns": len(columns)}


# ---------------------------------------------------------------- expectations from a profile

_CHECK = re.compile(r"^(?P<fn>[a-z_]+)\((?P<col>[^)]*)\)\s*(?P<op><|=|>)\s*(?P<value>[0-9.]+)(?P<unit>%|d|h)?$")


def gx_checks(checks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """insights.suggested_checks (SodaCL strings) -> the neutral check shape services.quality.gx renders."""
    out: List[Dict[str, Any]] = []
    for c in checks or []:
        text = str(c.get("check") or "").strip()
        if text.startswith("row_count"):
            out.append({"check_type": "ROW_COUNT", "target_column": None, "severity": "FAIL", "origin": "PROFILE",
                        "definition": {"kind": "row_count", "gt": 0, "evidence": c.get("reason")}})
            continue
        m = _CHECK.match(text)
        if not m:
            continue
        fn, col, value, unit = m["fn"], m["col"].strip(), m["value"], m["unit"]
        base = {"target_column": col, "severity": "WARN", "origin": "PROFILE"}
        evidence = {"evidence": c.get("reason")}
        if fn == "duplicate_count":
            out.append({**base, "check_type": "UNIQUE", "definition": {"kind": "unique", **evidence}})
        elif fn == "missing_count":
            out.append({**base, "check_type": "NOT_NULL", "definition": {"kind": "not_null", **evidence}})
        elif fn == "invalid_count" and c.get("valid_values"):
            out.append({**base, "check_type": "ACCEPTED_VALUES",
                        "definition": {"kind": "accepted_values", "values": c["valid_values"], **evidence}})
        elif fn == "invalid_percent" and c.get("valid_regex"):
            out.append({**base, "check_type": "CUSTOM",
                        "definition": {"kind": "regex", "pattern": c["valid_regex"], "max_invalid_percent": float(value),
                                       **evidence}})
        elif fn == "freshness":
            out.append({**base, "check_type": "FRESHNESS",
                        "definition": {"kind": "freshness", "threshold": f"{value}{unit or 'd'}", **evidence}})
    return out
