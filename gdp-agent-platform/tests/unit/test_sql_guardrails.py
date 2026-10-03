"""Phase 1 non-goals enforced on every SQL file in the repo."""

import re
from pathlib import Path

import pytest

SNOWFLAKE_DIR = Path(__file__).resolve().parents[2] / "snowflake"
SQL_FILES = sorted(SNOWFLAKE_DIR.rglob("*.sql"))

FORBIDDEN_EVERYWHERE = {
    "task objects (Phase 2)": r"\b(CREATE|ALTER|EXECUTE|DROP)\s+(OR\s+REPLACE\s+)?TASK\b",
    "drop statements": r"\bDROP\s+(TABLE|DATABASE|SCHEMA|STAGE|VIEW)\b",
    "truncate": r"\bTRUNCATE\b",
    "replace of data objects": r"\bCREATE\s+OR\s+REPLACE\s+(TRANSIENT\s+)?(TABLE|DATABASE|SCHEMA|STAGE)\b",
    "account-level privilege": r"\bACCOUNTADMIN\b",
}
FORBIDDEN_IN_MIGRATIONS = {
    "delete in migrations": r"\bDELETE\s+FROM\b",
}


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def test_sql_files_found():
    assert SQL_FILES, "no SQL files found"


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_no_forbidden_statements(path):
    sql = _strip_comments(path.read_text(encoding="utf-8")).upper()
    rules = dict(FORBIDDEN_EVERYWHERE)
    if "migrations" in path.parts:
        rules.update(FORBIDDEN_IN_MIGRATIONS)
    for label, pattern in rules.items():
        assert not re.search(pattern, sql), f"{path.name}: {label}"


@pytest.mark.parametrize("path", sorted((SNOWFLAKE_DIR / "database" / "migrations").glob("*.sql")),
                         ids=lambda p: p.name)
def test_migration_tables_are_idempotent(path):
    sql = _strip_comments(path.read_text(encoding="utf-8")).upper()
    for stmt in re.findall(r"CREATE\s+(?:DATABASE\s+ROLE|TABLE|SCHEMA|STAGE)\s+(?!IF\s+NOT\s+EXISTS)\S+", sql):
        pytest.fail(f"{path.name}: CREATE without IF NOT EXISTS: {stmt}")
