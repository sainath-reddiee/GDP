"""Staged share landing (pure SQL builders): Snowflake share -> compressed Parquet on CORE.LANDING_STAGE -> landing.

The share is read once, by one COPY INTO <stage> statement, and every later step works on the platform's own staged
files, so a long load never holds the provider's objects and a failed load can be retried from the files. The landing
table is created with the source's exact column types (CREATE TABLE ... AS SELECT ... LIMIT 0: no rows are read) and
filled by COPY INTO ... MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE, so column order on either side never matters.

The rows go into <table>__LOADING first and are swapped into the landing table only once the COPY succeeded, so a
failed re-run leaves the previous landing as it was and readers never see a half-filled table. The staged files are
removed after the swap, so the provider's data does not stay on the platform's stage.

Not every type survives Parquet: Snowflake refuses to unload TIMESTAMP_TZ and TIMESTAMP_LTZ (100171), writes VARIANT,
OBJECT and ARRAY as JSON text (a VARIANT column would come back as a string), and BINARY, GEOGRAPHY, GEOMETRY, VECTOR
and structured types do not come back as they were. parquet_safe() says whether all of a table's columns round-trip;
a table whose columns do not lands with CREATE TABLE AS SELECT instead (services.source.procedures), which copies
every type exactly. So does a table whose column names differ only by case (MATCH_BY_COLUMN_NAME cannot tell them
apart) or whose columns could not be read.

Used by services.source.procedures.execute_landing(method="STAGED_PARQUET") for MANAGED landing targets; IN_PLACE
copies nothing and ICEBERG keeps the CTAS path.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from services.source.identifiers import quote

METHOD = "STAGED_PARQUET"
STAGE = "CORE.LANDING_STAGE"
FILE_FORMAT = "CORE.PARQUET_SNAPPY"
MAX_FILE_SIZE = 256 * 1024 * 1024
RUN_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
DB = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
LOADING_SUFFIX = "__LOADING"
# INFORMATION_SCHEMA.COLUMNS.DATA_TYPE values that unload to Parquet and load back unchanged
PARQUET_SAFE = frozenset({"TEXT", "VARCHAR", "CHAR", "CHARACTER", "STRING", "NUMBER", "DECIMAL", "NUMERIC", "INT",
                          "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT", "FLOAT", "FLOAT4", "FLOAT8", "DOUBLE",
                          "DOUBLE PRECISION", "REAL", "BOOLEAN", "DATE", "TIME", "TIMESTAMP_NTZ", "DATETIME"})


def stage_prefix(platform_database: str, run_id: str, landing_table: str) -> str:
    """@<db>.CORE.LANDING_STAGE/runs/<run_id>/<landing table>/ (quoted stage path, safe for SQL)."""
    assert DB.match(platform_database or ""), "unsafe platform database name"
    assert RUN_ID.match(run_id or ""), "unsafe run id"
    assert re.match(r"^[A-Z0-9_]{1,255}$", landing_table or ""), "landing tables are named [A-Z0-9_]"
    return f"@{platform_database}.{STAGE}/runs/{run_id}/{landing_table}/"


def parquet_safe(columns: Optional[List[Dict[str, Any]]]) -> Tuple[bool, str]:
    """(True, '') when every column round-trips through Parquet; else (False, why), and the table lands by CTAS."""
    rows = [c for c in columns or [] if c.get("COLUMN_NAME")]
    if not rows:
        return False, "its column types could not be read"
    unsafe = sorted({str(c.get("DATA_TYPE") or "").upper() for c in rows} - PARQUET_SAFE)
    if unsafe:
        return False, f"{', '.join(unsafe)} columns do not round-trip through Parquet"
    names = [str(c["COLUMN_NAME"]).upper() for c in rows]
    if len(set(names)) < len(names):
        return False, "it has column names that differ only by case"
    return True, ""


def loading_name(target_fqn: str) -> str:
    """'"DB"."S"."T"' -> '"DB"."S"."T__LOADING"' (the quoted last part gets the suffix)."""
    assert target_fqn.endswith('"'), "the target must be a quoted name"
    return target_fqn[:-1] + LOADING_SUFFIX + '"'


def landing_steps(platform_database: str, run_id: str, source_fqn: str, target_fqn: str,
                  landing_table: str, comment: str) -> List[Tuple[str, str]]:
    """(step, statement) in order for one table. `source_fqn` and `target_fqn` are already quoted (identifiers.fqn).
    The unload's result has rows_unloaded and the load's rows_loaded (count_from)."""
    location = stage_prefix(platform_database, run_id, landing_table)
    fmt = f"{platform_database}.{FILE_FORMAT}"
    safe_comment = comment.replace("\\", "\\\\").replace("'", "''")
    loading = loading_name(target_fqn)
    return [
        ("clear the stage", f"REMOVE {location}"),
        # one read of the shared table into Snappy Parquet with the column names in the files
        ("unload", f"COPY INTO {location}data_ FROM (SELECT * FROM {source_fqn}) "
                   f"FILE_FORMAT = (TYPE = 'PARQUET' COMPRESSION = 'SNAPPY') HEADER = TRUE OVERWRITE = TRUE "
                   f"MAX_FILE_SIZE = {MAX_FILE_SIZE}"),
        # the source's exact types, no rows read
        ("create", f"CREATE OR REPLACE TABLE {loading} COMMENT = '{safe_comment}' AS SELECT * FROM {source_fqn} LIMIT 0"),
        ("load", f"COPY INTO {loading} FROM {location} FILE_FORMAT = (FORMAT_NAME = '{fmt}') "
                 f"PATTERN = '.*[.]parquet' MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = ABORT_STATEMENT FORCE = TRUE"),
        # only now does the landing table change, in one step, to the fully loaded table
        ("swap", f"CREATE TABLE IF NOT EXISTS {target_fqn} LIKE {loading}"),
        ("swap", f"ALTER TABLE {loading} SWAP WITH {target_fqn}"),
        ("swap", f"DROP TABLE IF EXISTS {loading}"),
        ("clean up the stage", f"REMOVE {location}"),
    ]


def landing_statements(platform_database: str, run_id: str, source_fqn: str, target_fqn: str,
                       landing_table: str, comment: str) -> List[str]:
    """The statements of landing_steps, without their step names."""
    return [sql for _, sql in landing_steps(platform_database, run_id, source_fqn, target_fqn, landing_table, comment)]


def count_from(rows: List[Any], field: str) -> Optional[int]:
    """The sum of a COPY result's `field` (rows_unloaded, rows_loaded), or None when the result does not carry it."""
    total, seen = 0, False
    for row in rows or []:
        if isinstance(row, dict):
            data = row
        elif hasattr(row, "as_dict"):
            data = row.as_dict()
        elif hasattr(row, "asDict"):
            data = row.asDict()
        elif isinstance(row, dict):
            data = row
        else:
            return None
        value = next((v for k, v in data.items() if str(k).lower() == field), None)
        if value is not None:
            total, seen = total + int(value), True
    return total if seen else None


def target_name(database: str, schema: str, table: str) -> str:
    return ".".join(quote(p) for p in (database, schema, table))
