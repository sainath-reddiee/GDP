"""The deploy applies only what changed since its last successful apply (CORE.DEPLOY_STATE)."""

from pathlib import Path

import pytest

import infrastructure.deploy_snowflake as deploy
import infrastructure.seed_knowledge as seed


class FakeCursor:
    def __init__(self, con):
        self.con, self._one, self._all = con, None, []

    def execute(self, sql, params=None):
        self.con.statements.append(sql)
        self._one, self._all = None, []
        if sql.startswith("SELECT CURRENT_USER()"):
            self._one = ("DEPLOYER", "SYSADMIN", "WH")
        elif "FROM DB.CORE.DEPLOY_STATE" in sql:
            self._all = list(self.con.state.items())
        elif "FROM DB.CORE.SCHEMA_MIGRATION" in sql:
            self._all = list(self.con.migrations.items())
        elif sql.lstrip().startswith("MERGE INTO DB.CORE.DEPLOY_STATE"):
            self.con.state[params[0]] = params[1]
        elif sql.startswith("INSERT INTO DB.CORE.SCHEMA_MIGRATION"):
            self.con.migrations[params[0]] = params[2]

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class FakeCon:
    def __init__(self):
        self.state, self.migrations, self.statements, self.scripts = {}, {}, [], []

    def cursor(self):
        return FakeCursor(self)

    def execute_string(self, sql):
        if "CORE.DEPLOY_STATE (" not in sql:  # the bootstrap (CREATE ... IF NOT EXISTS) runs every time
            self.scripts.append(sql)


@pytest.fixture()
def files(tmp_path, monkeypatch):
    (tmp_path / "procedures").mkdir()
    (tmp_path / "views").mkdir()
    (tmp_path / "migrations").mkdir()
    (tmp_path / "procedures" / "a.sql").write_text("CREATE OR REPLACE PROCEDURE {{database}}.CORE.A() IMPORTS = ('{{services_import}}');")
    (tmp_path / "procedures" / "b.sql").write_text("CREATE OR REPLACE PROCEDURE {{database}}.CORE.B();")
    (tmp_path / "views" / "v.sql").write_text("CREATE OR REPLACE VIEW {{database}}.CORE.V AS SELECT 1;")
    (tmp_path / "migrations" / "V001__first.sql").write_text("CREATE TABLE IF NOT EXISTS {{database}}.CORE.T (X INT);")
    seeded = []
    monkeypatch.setattr(deploy, "seed_platform", lambda cur, db: seeded.append(db) or ["seeded"])
    monkeypatch.setattr(deploy, "seed_inputs_digest", lambda: "seed-1")
    return tmp_path, seeded


def _apply(con, root: Path, zip_name="services-1.zip", force=False):
    variables = {"database": "DB", "services_import": f"@DB.CORE.CODE_STAGE/{zip_name}", "warehouse": "WH"}
    migrations = [(m.name[1:4], m.name[6:-4], m) for m in sorted((root / "migrations").glob("*.sql"))]
    return deploy.apply_to_connection(
        con, "DB", "WH", variables, zip_name, b"zip", migrations, sorted((root / "procedures").glob("*.sql")),
        sorted((root / "views").glob("*.sql")), "1.0", [("CREATED", "SOURCE", "PENDING", 1, 1, True, None, "1.0")],
        [("CREATED", "FAILED", "SYSTEM", None, True, "1.0")], [], force=force)


def test_first_deploy_applies_everything_then_a_repeat_skips_everything(files):
    root, seeded = files
    con = FakeCon()
    first = _apply(con, root)
    assert "procedures a.sql: applied" in first and "procedures b.sql: applied" in first
    assert "views v.sql: applied" in first and "migration V001 first: applied" in first
    assert seeded == ["DB"] and any(s.startswith("PUT ") for s in con.statements)

    con.scripts.clear(), con.statements.clear(), seeded.clear()
    second = _apply(con, root)
    assert con.scripts == [] and seeded == []
    assert not any(s.startswith("PUT ") or "WORKFLOW_STATE" in s for s in con.statements)
    assert "procedures: 2 unchanged (skipped)" in second and "views: 1 unchanged (skipped)" in second
    assert "migrations: 1 unchanged (skipped)" in second


def test_only_the_changed_file_is_applied(files):
    root, _ = files
    con = FakeCon()
    _apply(con, root)
    (root / "procedures" / "b.sql").write_text("CREATE OR REPLACE PROCEDURE {{database}}.CORE.B(X INT);")
    con.scripts.clear()
    log = _apply(con, root)
    assert "procedures b.sql: applied" in log and "procedures a.sql: applied" not in log
    assert con.scripts == ["CREATE OR REPLACE PROCEDURE DB.CORE.B(X INT);"]


def test_a_new_code_package_reapplies_the_procedures_that_import_it(files):
    root, _ = files
    con = FakeCon()
    _apply(con, root)
    con.scripts.clear()
    log = _apply(con, root, zip_name="services-2.zip")
    assert "package services-2.zip uploaded" in log
    assert "procedures a.sql: applied" in log and "procedures b.sql: applied" not in log


def test_a_new_migration_reruns_views_and_the_seed(files):
    root, seeded = files
    con = FakeCon()
    _apply(con, root)
    seeded.clear()
    (root / "migrations" / "V002__second.sql").write_text("ALTER TABLE {{database}}.CORE.T ADD COLUMN Y INT;")
    log = _apply(con, root)
    assert "migration V002 second: applied" in log and "views v.sql: applied" in log
    assert seeded == ["DB"] and "procedures a.sql: applied" not in log


def test_force_applies_everything(files):
    root, seeded = files
    con = FakeCon()
    _apply(con, root)
    seeded.clear()
    log = _apply(con, root, force=True)
    assert log[1] == "force: every step is applied"
    assert {"procedures a.sql: applied", "procedures b.sql: applied", "views v.sql: applied"} <= set(log)
    assert seeded == ["DB"]


def test_a_failed_view_is_retried_next_deploy(files):
    root, _ = files
    con = FakeCon()
    calls = []

    def flaky(sql):
        calls.append(sql)
        if "VIEW" in sql and len([c for c in calls if "VIEW" in c]) == 1:
            raise RuntimeError("object does not exist")
    con.execute_string = flaky
    first = _apply(con, root)
    assert any(line.startswith("views v.sql: skipped") for line in first)
    assert "views/v.sql" not in con.state
    assert "views v.sql: applied" in _apply(con, root)


def test_seed_inputs_digest_follows_the_repository_files(tmp_path, monkeypatch):
    (tmp_path / "domain" / "x").mkdir(parents=True)
    (tmp_path / "skills").mkdir()
    pack = tmp_path / "domain" / "x" / "domain_pack.json"
    pack.write_text('{"domain": {"name": "X"}}')
    monkeypatch.setattr(seed, "ROOT", tmp_path)
    monkeypatch.setattr(seed, "DOMAIN_DIR", tmp_path / "domain")
    monkeypatch.setattr(seed, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(seed, "SEED_CODE", [])
    before = seed.seed_inputs_digest()
    assert seed.seed_inputs_digest() == before
    pack.write_text('{"domain": {"name": "X", "description": "changed"}}')
    assert seed.seed_inputs_digest() != before


def test_seed_leaves_governed_domains_tables_alone():
    data = {"domains": [("d1", "COMPANY"), ("d2", "NEW")],
            "tables": [("t1", "d1", "SILVER"), ("t2", "d2", "SILVER")],
            "columns": [("c1", "t1", "ID"), ("c2", "t2", "ID")],
            "knowledge": [("k1", "d1")], "drift": [{"domain": "COMPANY"}, {"domain": "NEW"}]}
    log = seed.drop_governed(data, {"d1": 3})
    assert [t[0] for t in data["tables"]] == ["t2"] and [c[0] for c in data["columns"]] == ["c2"]
    assert data["knowledge"] == [("k1", "d1")] and data["drift"] == [{"domain": "NEW"}]
    assert "COMPANY" in log[0] and "approved model v3" in log[0]
    assert seed.drop_governed(data, {}) == []
