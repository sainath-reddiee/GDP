"""Oracle column types -> Arrow types (Parquet) -> Snowflake landing types, with no silent precision loss.

  NUMBER(p,0) p<=18        int64              NUMBER(p,0)
  NUMBER(p,0) p>18         decimal128(38,0)   NUMBER(38,0)
  NUMBER(p,s) s>0          decimal128(p,s)    NUMBER(p,s)
  NUMBER / FLOAT (free)    decimal128(38,10)  NUMBER(38,10)   values that do not fit fall back to text
  BINARY_FLOAT/DOUBLE      float64            FLOAT
  DATE, TIMESTAMP          timestamp[us]      TIMESTAMP_NTZ   Oracle DATE carries a time of day
  TIMESTAMP WITH (LOCAL) TZ timestamp[us,UTC] TIMESTAMP_TZ
  CHAR/VARCHAR2/CLOB/...   string             VARCHAR
  RAW / BLOB               binary             BINARY
  INTERVAL, ROWID, XMLTYPE string             VARCHAR
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

FREE_NUMBER_SCALE = 10
LOB_TYPES = {"CLOB", "NCLOB", "BLOB", "LONG", "LONG RAW", "BFILE"}


@dataclass(frozen=True)
class ColumnType:
    oracle: str          # data type as Oracle reports it, e.g. NUMBER(12,2)
    arrow: str           # int64 | float64 | decimal:p,s | timestamp | timestamptz | string | binary | bool
    snowflake: str       # landing type, e.g. NUMBER(12,2)
    family: str          # profiler family: NUMBER | TEXT | TIMESTAMP | DATE | BOOLEAN | OTHER
    lob: bool = False
    free_number: bool = False


def oracle_type_name(data_type: str, precision: Optional[int], scale: Optional[int], length: Optional[int]) -> str:
    """ALL_TAB_COLUMNS parts -> display type: NUMBER(12,2), VARCHAR2(40), TIMESTAMP(6) WITH TIME ZONE."""
    t = str(data_type or "").upper()
    if t == "NUMBER":
        if precision is None:
            return "NUMBER" if scale in (None, 0) or scale == -127 else f"NUMBER(*,{scale})"
        return f"NUMBER({int(precision)},{int(scale or 0)})"
    if t in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "RAW") and length:
        return f"{t}({int(length)})"
    return t


def map_type(data_type: str, precision: Optional[int] = None, scale: Optional[int] = None) -> ColumnType:
    t = re.sub(r"\(\d+\)", "", str(data_type or "").upper()).strip()  # TIMESTAMP(6) -> TIMESTAMP
    name = oracle_type_name(data_type, precision, scale, None) if t == "NUMBER" else str(data_type or "").upper()
    if t == "NUMBER":
        if precision is None:
            if scale == 0:
                return ColumnType(name, "decimal:38,0", "NUMBER(38,0)", "NUMBER")
            return ColumnType(name, f"decimal:38,{FREE_NUMBER_SCALE}", f"NUMBER(38,{FREE_NUMBER_SCALE})", "NUMBER",
                              free_number=True)
        p, s = int(precision), int(scale or 0)
        if s <= 0:
            return (ColumnType(name, "int64", f"NUMBER({p},0)", "NUMBER") if p <= 18
                    else ColumnType(name, "decimal:38,0", "NUMBER(38,0)", "NUMBER"))
        return ColumnType(name, f"decimal:{min(p, 38)},{min(s, 37)}", f"NUMBER({min(p, 38)},{min(s, 37)})", "NUMBER")
    if t == "FLOAT":
        return ColumnType(name, f"decimal:38,{FREE_NUMBER_SCALE}", f"NUMBER(38,{FREE_NUMBER_SCALE})", "NUMBER",
                          free_number=True)
    if t in ("BINARY_FLOAT", "BINARY_DOUBLE"):
        return ColumnType(name, "float64", "FLOAT", "NUMBER")
    if t == "DATE" or (t.startswith("TIMESTAMP") and "ZONE" not in t):
        return ColumnType(name, "timestamp", "TIMESTAMP_NTZ", "TIMESTAMP")
    if t.startswith("TIMESTAMP"):
        return ColumnType(name, "timestamptz", "TIMESTAMP_TZ", "TIMESTAMP")
    if t in ("RAW", "BLOB", "LONG RAW", "BFILE"):
        return ColumnType(name, "binary", "BINARY", "OTHER", lob=t in LOB_TYPES)
    if t == "BOOLEAN":
        return ColumnType(name, "bool", "BOOLEAN", "BOOLEAN")
    if t in ("CHAR", "NCHAR", "VARCHAR2", "NVARCHAR2", "VARCHAR", "CLOB", "NCLOB", "LONG"):
        return ColumnType(name, "string", "VARCHAR", "TEXT", lob=t in LOB_TYPES)
    return ColumnType(name, "string", "VARCHAR", "TEXT")  # INTERVAL, ROWID, UROWID, XMLTYPE, JSON, user types


def arrow_type(spec: ColumnType):
    """The pyarrow type for a column (imported lazily: the procedure and the API host both have pyarrow)."""
    import pyarrow as pa

    kind = spec.arrow
    if kind.startswith("decimal:"):
        p, s = (int(x) for x in kind.split(":", 1)[1].split(","))
        return pa.decimal128(p, s)
    return {"int64": pa.int64(), "float64": pa.float64(), "timestamp": pa.timestamp("us"),
            "timestamptz": pa.timestamp("us", tz="UTC"), "string": pa.string(), "binary": pa.binary(),
            "bool": pa.bool_()}[kind]


def describe(columns: Dict[str, ColumnType]) -> Dict[str, Any]:
    return {name: {"oracle": c.oracle, "snowflake": c.snowflake, "family": c.family} for name, c in columns.items()}
