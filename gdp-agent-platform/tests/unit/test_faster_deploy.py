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


class RoleCursor:
    """A Snowflake cursor for the direct role check: SHOW GRANTS ON ACCOUNT, RESULT_SCAN of it, IS_ROLE_IN_SESSION.
    The deploy role SYSADMIN holds A and C; B is someone else's."""

    ACCOUNT = [("EXECUTE TASK", "ROLE", "C"), ("CREATE DATABASE", "ROLE", "B"), ("MONITOR USAGE", "ROLE", "SYSADMIN")]
    HELD = {"SYSADMIN", "A", "C"}

    def __init__(self, fail_show=False):
        self.sql, self.description, self._rows, self.fail_show = [], [], [], fail_show

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if sql.startswith("SHOW GRANTS ON ACCOUNT"):
            if self.fail_show:
                raise RuntimeError("Insufficient privileges to operate on account")
            self.description, self._rows = [], []
        elif "RESULT_SCAN" in sql:
            self.description = [("privilege",), ("grantee_name",)]
            self._rows = [(p, g) for p, to, g in self.ACCOUNT if to == "ROLE"]
        elif "IS_ROLE_IN_SESSION" in sql:
            self.description = [(f"R{i}",) for i in range(len(params))]
            self._rows = [tuple(name in self.HELD for name in params)]
        elif sql.startswith('SHOW GRANTS TO ROLE "SYSADMIN"'):   # the fallback walk
            self.description = [("privilege",), ("granted_on",), ("name",)]
            self._rows = [("USAGE", "ROLE", "A")]
        else:
            self.description, self._rows = [("x",)], []

    def fetchall(self):
        return self._rows


def test_the_role_hierarchy_is_asked_of_snowflake_in_a_few_queries():
    from infrastructure import deploy

    deploy._ROLE_GRANTS.clear()
    deploy.NOTES.clear()
    cur = RoleCursor()
    found = deploy.role_grants(cur, "SYSADMIN")
    assert found["account"] == {"EXECUTE TASK", "MONITOR USAGE"}          # B's CREATE DATABASE is not ours
    assert "A" in found["roles"] and "SYSADMIN" in found["roles"] and "B" not in found["roles"]
    assert len(cur.sql) == 4 and not deploy.NOTES                         # SHOW, RESULT_SCAN, one batch, A on demand
    assert deploy.role_grants(cur, "SYSADMIN") is found and len(cur.sql) == 4   # once per deploy


def test_without_show_grants_on_account_it_reads_role_by_role_and_says_so():
    from infrastructure import deploy

    deploy._ROLE_GRANTS.clear()
    deploy.NOTES.clear()
    found = deploy.role_grants(RoleCursor(fail_show=True), "SYSADMIN")
    assert "SYSADMIN" in found["roles"] and deploy.NOTES and deploy.NOTES[0].startswith("role hierarchy read role by role")
