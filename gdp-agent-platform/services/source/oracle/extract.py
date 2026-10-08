"""Stream an Oracle table into Parquet files without holding the table in memory.

Rows are fetched in batches (cursor arraysize), converted to Arrow with the types in services.source.oracle.types,
and written to Parquet files of about TARGET_FILE_BYTES. Each finished file is handed to `put(file_path, name)`, which
uploads it to the landing stage (Snowpark put in Snowflake, PUT from the API host). Column names are made safe for
Snowflake (upper case, letters/digits/_ only, unique) so INFER_SCHEMA and COPY MATCH_BY_COLUMN_NAME line up; the
original Oracle names are reported. Lineage columns _SOURCE_SYSTEM, _SOURCE_TABLE and _BATCH_ID go into every file.
"""

from __future__ import annotations

import decimal
import os
import re
import shutil
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence

from services.source.oracle.connect import cursor
from services.source.oracle.profile import q, table_ref, value_sql
from services.source.oracle.types import FREE_NUMBER_SCALE, ColumnType, map_type

TARGET_FILE_BYTES = 128 * 1024 * 1024
LOB_ARRAY_SIZE = 500
MAX_TEXT_BYTES = 16 * 1024 * 1024      # Snowflake VARCHAR limit
MAX_BINARY_BYTES = 8 * 1024 * 1024     # Snowflake BINARY limit
FREE_NUMBER_LIMIT = decimal.Decimal(10) ** (38 - FREE_NUMBER_SCALE)
LINEAGE = ("_SOURCE_SYSTEM", "_SOURCE_TABLE", "_BATCH_ID")


class Cancelled(Exception):
    """Raised between batches when the user stops a load."""


def snowflake_names(names: Sequence[str]) -> Dict[str, str]:
    """Oracle column -> Snowflake-safe unique upper-case name (ORDER ID -> ORDER_ID, 1ST -> C_1ST)."""
    out: Dict[str, str] = {}
    used = set(LINEAGE)
    for name in names:
        base = re.sub(r"[^A-Z0-9_]", "_", str(name).upper()).strip("_") or "COLUMN"
        if not re.match(r"^[A-Z_]", base):
            base = f"C_{base}"
        candidate, n = base, 2
        while candidate in used:
            candidate, n = f"{base}_{n}", n + 1
        used.add(candidate)
        out[name] = candidate
    return out


def column_specs(columns: Sequence[Dict[str, Any]], oversized: Sequence[str] = ()) -> Dict[str, ColumnType]:
    """Mapped types; free NUMBER columns whose values exceed decimal(38,10) are carried as text instead."""
    specs = {}
    for c in columns:
        spec = map_type(c["data_type"], c.get("precision"), c.get("scale"))
        if c["column_name"] in oversized:
            spec = ColumnType(spec.oracle, "string", "VARCHAR", "TEXT")
        specs[c["column_name"]] = spec
    return specs


def oversized_free_numbers(conn, owner: str, table: str, columns: Sequence[Dict[str, Any]]) -> List[str]:
    """Free NUMBER/FLOAT columns holding a value too large for NUMBER(38,10)."""
    free = [c["column_name"] for c in columns if map_type(c["data_type"], c.get("precision"), c.get("scale")).free_number]
    if not free:
        return []
    cur = cursor(conn, 10)
    cur.execute("SELECT " + ", ".join(f"MAX(ABS({q(n)}))" for n in free) + f" FROM {table_ref(owner, table)}")
    maxima = cur.fetchone() or []
    return [n for n, m in zip(free, maxima) if m is not None and decimal.Decimal(str(m)) >= FREE_NUMBER_LIMIT]


def _convert(values: List[Any], spec: ColumnType, notes: Dict[str, int]) -> List[Any]:
    """Driver values -> values pyarrow accepts for the column's Arrow type (with size ceilings)."""
    kind = spec.arrow
    out: List[Any] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        if kind.startswith("decimal:"):
            scale = int(kind.split(",")[1])
            d = v if isinstance(v, decimal.Decimal) else decimal.Decimal(str(v))
            q_ = d.quantize(decimal.Decimal(1).scaleb(-scale), rounding=decimal.ROUND_HALF_EVEN) if scale else \
                d.to_integral_value(rounding=decimal.ROUND_HALF_EVEN)
            if q_ != d:
                notes["rounded"] = notes.get("rounded", 0) + 1
            out.append(q_)
        elif kind == "int64":
            out.append(int(v))
        elif kind == "float64":
            out.append(float(v))
        elif kind == "string":
            s = v if isinstance(v, str) else str(v)
            if len(s.encode("utf-8")) > MAX_TEXT_BYTES:
                s = s.encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", "ignore")
                notes["truncated_text"] = notes.get("truncated_text", 0) + 1
            out.append(s)
        elif kind == "binary":
            b = bytes(v)
            if len(b) > MAX_BINARY_BYTES:
                b = b[:MAX_BINARY_BYTES]
                notes["truncated_binary"] = notes.get("truncated_binary", 0) + 1
            out.append(b)
        else:
            out.append(v)
    return out


def extract_table(conn, owner: str, table: str, columns: Sequence[Dict[str, Any]], *, source_system: str,
                  batch_id: str, put: Callable[[str, str], None], watermark_column: Optional[str] = None,
                  watermark_value: Optional[Any] = None, lookback_minutes: int = 0,
                  progress: Optional[Callable[[Dict[str, Any]], None]] = None,
                  cancelled: Optional[Callable[[], bool]] = None,
                  target_file_bytes: int = TARGET_FILE_BYTES) -> Dict[str, Any]:
    """Stream one table to Parquet parts; returns rows, files, column name map, notes and the new watermark.
    Columns with a skip_reason are left out (reported as `skipped`)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from services.source.oracle.types import arrow_type

    import oracledb

    skipped = [{"column": c["column_name"], "reason": c["skip_reason"]} for c in columns if c.get("skip_reason")]
    columns = [c for c in columns if not c.get("skip_reason")]
    assert columns, f"{table} has no columns that can be extracted"
    oversized = oversized_free_numbers(conn, owner, table, columns)
    specs = column_specs(columns, oversized)
    names = [c["column_name"] for c in columns]
    safe = snowflake_names(names)
    fields = [pa.field(safe[n], arrow_type(specs[n])) for n in names] + [pa.field(x, pa.string()) for x in LINEAGE]
    schema = pa.schema(fields)
    has_lob = any(specs[n].lob for n in names)

    where, binds = "", {}
    wm_family = None
    if watermark_column:
        assert watermark_column in names, f"watermark column {watermark_column} is not a readable column of {table}"
        wm_family = specs[watermark_column].family
        assert wm_family in ("TIMESTAMP", "NUMBER"), "the watermark column must be a date/timestamp or a number"
        if watermark_value is not None:
            wm_col = next(c for c in columns if c["column_name"] == watermark_column)
            if wm_family == "TIMESTAMP":
                bound = "TO_TIMESTAMP(:wm, 'YYYY-MM-DD HH24:MI:SS.FF6')"
                if lookback_minutes:  # re-read a window: late commits with older timestamps are caught (merge dedupes)
                    bound += f" - NUMTODSINTERVAL({int(lookback_minutes)}, 'MINUTE')"
                if str(wm_col.get("data_type") or "").upper() == "DATE":
                    bound = f"CAST({bound} AS DATE)"  # compare DATE to DATE so an index on the column is used
            else:
                bound = "TO_NUMBER(:wm)"
            where, binds = f" WHERE {value_sql(wm_col)} > {bound}", {"wm": str(watermark_value)}
    select = ", ".join(c.get("expr") or q(c["column_name"]) for c in columns)
    sql = f"SELECT {select} FROM {table_ref(owner, table)}{where}"
    cur = cursor(conn, LOB_ARRAY_SIZE if has_lob else 10_000)

    # Exact NUMBERs (Decimal, never float) and LOBs read inline as str/bytes, for this extract only.
    saved = (oracledb.defaults.fetch_decimals, oracledb.defaults.fetch_lobs)
    oracledb.defaults.fetch_decimals, oracledb.defaults.fetch_lobs = True, False
    try:
        cur.execute(sql, binds)
    finally:
        oracledb.defaults.fetch_decimals, oracledb.defaults.fetch_lobs = saved

    tmp = tempfile.mkdtemp(prefix="ora_extract_")
    notes: Dict[str, int] = {}
    files: List[str] = []
    rows_total = 0
    max_wm = None
    writer = None
    part_path = ""
    wm_index = names.index(watermark_column) if watermark_column else None
    lineage = {"_SOURCE_SYSTEM": source_system, "_SOURCE_TABLE": f"{owner.upper()}.{table}", "_BATCH_ID": batch_id}

    def close_part():
        nonlocal writer
        if writer is None:
            return
        writer.close()
        writer = None
        name = f"part-{len(files) + 1:05d}.parquet"
        put(part_path, name)
        files.append(name)
        os.remove(part_path)

    try:
        while True:
            if cancelled and cancelled():
                raise Cancelled(f"load of {table} stopped after {rows_total} rows")
            batch = cur.fetchmany()
            if not batch:
                break
            cols = list(zip(*batch))
            arrays = [pa.array(_convert(list(cols[i]), specs[n], notes), type=arrow_type(specs[n]))
                      for i, n in enumerate(names)]
            arrays += [pa.array([lineage[x]] * len(batch), type=pa.string()) for x in LINEAGE]
            if writer is None:
                part_path = os.path.join(tmp, f"part-{len(files) + 1:05d}.parquet")
                writer = pq.ParquetWriter(part_path, schema, compression="snappy")
            writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
            rows_total += len(batch)
            if wm_index is not None:
                top = max((v for v in cols[wm_index] if v is not None), default=None)
                if top is not None and (max_wm is None or top > max_wm):
                    max_wm = top
            if os.path.getsize(part_path) >= target_file_bytes:
                close_part()
            if progress:
                progress({"rows": rows_total, "files": len(files)})
        close_part()
    finally:
        if writer is not None:
            writer.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return {"rows": rows_total, "files": files, "columns": {n: safe[n] for n in names},
            "types": {safe[n]: specs[n].snowflake for n in names}, "text_numbers": oversized, "notes": notes,
            "skipped": skipped,
            "watermark": watermark_text(max_wm) if max_wm is not None else watermark_value}


def watermark_text(value: Any) -> str:
    """Stored form of a watermark that TO_TIMESTAMP(..., 'YYYY-MM-DD HH24:MI:SS.FF6') / TO_NUMBER read back."""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")
    return str(value)
