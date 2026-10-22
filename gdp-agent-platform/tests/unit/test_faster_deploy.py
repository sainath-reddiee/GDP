"""A faster deploy: migrations of grants or data only leave views, search services, agents and the seed alone, and a
procedures file runs its CREATE statements in parallel, then its grants. Snowflake is a fake."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infrastructure.deploy import TimedConnection  # noqa: E402
from infrastructure.deploy_snowflake import changes_shape  # noqa: E402

MIGRATIONS = ROOT / "snowflake" / "database" / "migrations"


def test_only_migrations_that_change_tables_rerun_what_is_built_on_them():
    assert changes_shape("CREATE TABLE IF NOT EXISTS {{database}}.CORE.X (A INT);")
    assert changes_shape("ALTER TABLE {{database}}.CORE.X ADD COLUMN IF NOT EXISTS B INT;")
    assert changes_shape("create or replace view v as select 1;")
    assert not changes_shape("-- CREATE TABLE in a comment only\nGRANT SELECT ON TABLE T TO DATABASE ROLE R;")
    assert not changes_shape("UPDATE {{database}}.CORE.PLATFORM_CONFIG SET CONFIG_VALUE = 1;")
    assert not changes_shape((MIGRATIONS / "V072__platform_config_writes.sql").read_text(encoding="utf-8"))
    assert not changes_shape((MIGRATIONS / "V073__source_onboarding_caller_writes.sql").read_text(encoding="utf-8"))
    assert changes_shape((MIGRATIONS / "V071__aods_snaplogic_config_in_core.sql").read_text(encoding="utf-8"))


class FakeCursor:
    def __init__(self, con):
        self.con, self.sfqid = con, None

    def execute_async(self, sql):
        self.sfqid = f"q{len(self.con.log)}"
        self.con.log.append(("async", " ".join(sql.split())[:40]))

    def execute(self, sql, *a, **kw):
        self.con.log.append(("sync", " ".join(sql.split())[:40]))
        return self


class FakeCon:
    def __init__(self):
        self.log = []

    def cursor(self, *a, **kw):
        return FakeCursor(self)

    def get_query_status_throw_if_error(self, qid):
        return "done"

    def is_still_running(self, status):
        return False


SQL = """
DROP PROCEDURE IF EXISTS DB.CORE.OLD(VARCHAR);
CREATE OR REPLACE PROCEDURE DB.CORE.A(X VARCHAR) RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python') HANDLER = 'm.a';
GRANT USAGE ON PROCEDURE DB.CORE.A(VARCHAR) TO DATABASE ROLE DB.DATA_ENGINEER;
CREATE OR REPLACE PROCEDURE DB.CORE.B(X VARCHAR) RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python') HANDLER = 'm.b';
GRANT USAGE ON PROCEDURE DB.CORE.B(VARCHAR) TO DATABASE ROLE DB.DATA_ENGINEER;
"""


def test_procedures_run_drops_then_creates_in_parallel_then_grants():
    con = FakeCon()
    timed = TimedConnection(con, lambda line: None)
    timed.execute_parallel(SQL)
    kinds = [(mode, text.split()[0]) for mode, text in con.log]
    assert kinds == [("sync", "DROP"), ("async", "CREATE"), ("async", "CREATE"), ("async", "GRANT"), ("async", "GRANT")]
    assert timed.statements == 5


def test_a_file_with_other_statements_runs_in_order():
    con = FakeCon()
    timed = TimedConnection(con, lambda line: None)
    timed.execute_parallel("CREATE STAGE IF NOT EXISTS S; " + SQL)
    assert all(mode == "sync" for mode, _ in con.log) and con.log[0][1].startswith("CREATE STAGE")
