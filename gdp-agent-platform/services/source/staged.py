"""Staged share landing (pure SQL builders): Snowflake share -> compressed Parquet on CORE.LANDING_STAGE -> landing.

The share is read once, by one COPY INTO <stage> statement, and every later step works on the platform's own staged
files, so a long load never holds the provider's objects and a failed load can be retried from the files. The landing
table is created with the source's exact column types (CREATE TABLE ... AS SELECT ... LIMIT 0: no rows are read) and
filled by COPY INTO ... MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE, so column order on either side never matters.

Used by services.source.procedures.execute_landing(method="STAGED_PARQUET") for MANAGED landing targets; IN_PLACE
copies nothing and ICEBERG keeps the CTAS path.
"""

from __future__ import annotations

import re
from typing import List

from services.source.identifiers import quote

METHOD = "STAGED_PARQUET"
STAGE = "CORE.LANDING_STAGE"
FILE_FORMAT = "CORE.PARQUET_SNAPPY"
MAX_FILE_SIZE = 256 * 1024 * 1024
RUN_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
DB = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def stage_prefix(platform_database: str, run_id: str, landing_table: str) -> str:
    """@<db>.CORE.LANDING_STAGE/runs/<run_id>/<landing table>/ (quoted stage path, safe for SQL)."""
    assert DB.match(platform_database or ""), "unsafe platform database name"
    assert RUN_ID.match(run_id or ""), "unsafe run id"
    assert re.match(r"^[A-Z0-9_]{1,255}$", landing_table or ""), "landing tables are named [A-Z0-9_]"
    return f"@{platform_database}.{STAGE}/runs/{run_id}/{landing_table}/"


def landing_statements(platform_database: str, run_id: str, source_fqn: str, target_fqn: str,
                       landing_table: str, comment: str) -> List[str]:
    """The ordered statements for one table. `source_fqn` and `target_fqn` are already quoted (identifiers.fqn)."""
    location = stage_prefix(platform_database, run_id, landing_table)
    fmt = f"{platform_database}.{FILE_FORMAT}"
    safe_comment = comment.replace("\\", "\\\\").replace("'", "''")
    return [
        f"REMOVE {location}",
        # unload: one read of the shared table into Snappy Parquet with the column names in the files
        f"COPY INTO {location}data_ FROM (SELECT * FROM {source_fqn}) "
        f"FILE_FORMAT = (TYPE = 'PARQUET' COMPRESSION = 'SNAPPY') HEADER = TRUE OVERWRITE = TRUE "
        f"MAX_FILE_SIZE = {MAX_FILE_SIZE}",
        # exact source types, no rows read
        f"CREATE OR REPLACE TABLE {target_fqn} COMMENT = '{safe_comment}' AS SELECT * FROM {source_fqn} LIMIT 0",
        f"COPY INTO {target_fqn} FROM {location} FILE_FORMAT = (FORMAT_NAME = '{fmt}') "
        f"PATTERN = '.*[.]parquet' MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = ABORT_STATEMENT FORCE = TRUE",
    ]


def target_name(database: str, schema: str, table: str) -> str:
    return ".".join(quote(p) for p in (database, schema, table))
