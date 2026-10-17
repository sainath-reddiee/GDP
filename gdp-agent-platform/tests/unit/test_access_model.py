"""AODS account roles instead of database roles: the SQL translation (every repository file), the role administrator's
one-time SQL, managed access and narrow future grants, the deploy's checks, and the run-time admin check."""

import re

import pytest

from infrastructure import deploy
from infrastructure import deploy_snowflake as ds
from services.common import access

V = {"database": "AI_PLATFORM", "schema": "CORE", "services_import": "@AI_PLATFORM.CORE.CODE_STAGE/x.zip", "warehouse": "WH"}


def repository_sql():
    files = [ds.BASELINE_FILE] + [p for _, _, p in ds.list_migrations() if p.parent == ds.MIGRATIONS_DIR]
    files += sorted(ds.PROCEDURES_DIR.glob("*.sql")) + sorted(ds.VIEWS_DIR.glob("*.sql"))
    files += sorted(ds.SEARCH_DIR.glob("*.sql")) + sorted(ds.AGENTS_DIR.glob("*.sql"))
    variables = {**V, "agent_skills": ds.agent_skills_yaml("AI_PLATFORM")}
    return [(f.name, ds.render(f.read_text(encoding="utf-8"), variables)) for f in files]


def test_every_repository_file_translates_to_account_roles_only():
    grants = 0
    for name, sql in repository_sql():
        out = access.to_account_roles(sql, "AI_PLATFORM", "AODS")
        code = re.sub(r"--[^\n]*", "", out)
        assert "CREATE DATABASE ROLE" not in code.upper(), name
        assert set(re.findall(r"TO ROLE AODS_(\w+)", out)) <= set(access.ROLES), name
        grants += len(re.findall(r"TO ROLE AODS_", out))
    assert grants > 800


def test_translation_rewrites_each_form():
    sql = "\n".join([
        "CREATE DATABASE ROLE IF NOT EXISTS AI_PLATFORM.VIEWER COMMENT = 'x';",
        "GRANT DATABASE ROLE AI_PLATFORM.VIEWER TO DATABASE ROLE AI_PLATFORM.DATA_ENGINEER;",
        "GRANT SELECT ON TABLE AI_PLATFORM.CORE.T TO DATABASE ROLE AI_PLATFORM.VIEWER;",
        "REVOKE INSERT ON TABLE AI_PLATFORM.CORE.T FROM DATABASE ROLE AI_PLATFORM.VIEWER;",
        "CREATE ROW ACCESS POLICY P AS (U VARCHAR) RETURNS BOOLEAN -> "
        "U = CURRENT_USER() OR IS_DATABASE_ROLE_IN_SESSION('PLATFORM_ADMIN');",
        "CREATE SCHEMA IF NOT EXISTS AI_PLATFORM.CORE COMMENT = 'c';",
        "CREATE SCHEMA IF NOT EXISTS AI_PLATFORM.LANDING WITH MANAGED ACCESS;",
        "CREATE SCHEMA IF NOT EXISTS AI_PLATFORM.OTHER;"])
    out = access.to_account_roles(sql, "AI_PLATFORM", "aods_prod")
    assert "CREATE DATABASE ROLE" not in out and "GRANT DATABASE ROLE" not in out
    assert "TO ROLE AODS_PROD_VIEWER" in out and "FROM ROLE AODS_PROD_VIEWER" in out
    assert "IS_ROLE_IN_SESSION('AODS_PROD_PLATFORM_ADMIN')" in out
    assert "AI_PLATFORM.CORE WITH MANAGED ACCESS COMMENT" in out
    assert out.count("WITH MANAGED ACCESS") == 2   # LANDING already had it; OTHER is not a platform schema
    with pytest.raises(ValueError, match="still named"):
        access.to_account_roles("GRANT USAGE ON SCHEMA X TO DATABASE ROLE OTHERDB.VIEWER;", "AI_PLATFORM", "AODS")
    assert access.to_account_roles("-- database role notes\nSELECT 'DATABASE ROLE';", "AI_PLATFORM", "AODS")


def test_role_administrator_sql_creates_and_links_the_roles():
    hierarchy = deploy.role_hierarchy()
    assert ("VIEWER", "DATA_ENGINEER") in hierarchy and ("DATA_ENGINEER", "PLATFORM_ADMIN") in hierarchy
    sql = access.setup_sql("AODS", hierarchy, "AODS_PLATFORM_ADMIN")
    assert sql[0] == "CREATE ROLE IF NOT EXISTS AODS_PLATFORM_ADMIN;"
    assert len([s for s in sql if s.startswith("CREATE ROLE")]) == 10
    assert "GRANT ROLE AODS_VIEWER TO ROLE AODS_DATA_ENGINEER;" in sql
    assert not any("DATABASE ROLE" in s for s in sql)
    assert not any(s.startswith("GRANT ROLE AODS_PLATFORM_ADMIN TO ROLE AODS_PLATFORM_ADMIN") for s in sql)
    assert "GRANT ROLE AODS_PLATFORM_ADMIN TO ROLE PLATFORM_ADMIN;" in access.setup_sql("AODS", hierarchy, "PLATFORM_ADMIN")
    sysadmin = access.setup_sql("AODS", hierarchy, "SYSADMIN")
    assert len([s for s in sysadmin if s.startswith("GRANT ROLE AODS_PLATFORM_ADMIN TO ROLE SYSADMIN")]) == 1


def test_future_grants_are_narrow():
    grants = access.future_grants("AI_PLATFORM", "AODS")
    assert "GRANT ALL PRIVILEGES ON FUTURE TABLES IN SCHEMA AI_PLATFORM.CORE TO ROLE AODS_PLATFORM_ADMIN" in grants
    assert "GRANT SELECT ON FUTURE VIEWS IN SCHEMA AI_PLATFORM.CORE TO ROLE AODS_VIEWER" in grants
    assert not any("FUTURE TABLES" in g and "AODS_PLATFORM_ADMIN" not in g for g in grants)   # tables stay explicit
    assert not any("IN DATABASE" in g for g in grants)   # database-level future grants need MANAGE GRANTS


def account_env(role="AODS_PLATFORM_ADMIN", prefix=None):
    raw = {"snowflake": {"connection": "c", "role": role, "warehouse": "wh", "database": "ai_platform"},
           "access": {"model": "account_roles", **({"role_prefix": prefix} if prefix else {})}}
    return deploy.validate_env(raw, "client")


def test_env_with_account_roles():
    e = account_env()
    assert e["accounts"] and e["access_model"] == {"model": "account_roles", "prefix": "AODS"} and e["access"] == {}
    with pytest.raises(deploy.ConfigError, match="lists account roles only with"):
        deploy.validate_env({"snowflake": {"connection": "c", "role": "R", "warehouse": "W", "database": "D"},
                             "access": {"model": "account_roles", "VIEWER": ["X"]}}, "c")
    with pytest.raises(deploy.ConfigError, match="role_prefix"):
        account_env(prefix="bad-prefix")
    assert account_env(prefix="aods_prod")["access_model"]["prefix"] == "AODS_PROD"


def test_access_step_defines_future_grants_and_never_database_roles():
    statements = deploy.access_statements(account_env(), owns_warehouse=True)
    assert "GRANT SELECT ON FUTURE VIEWS IN SCHEMA AI_PLATFORM.CORE TO ROLE AODS_VIEWER" in statements
    assert not any(s.endswith("TO ROLE AODS_PLATFORM_ADMIN") for s in statements)   # the deploy role owns everything
    assert "GRANT USAGE ON WAREHOUSE WH TO ROLE AODS_DATA_ENGINEER" in statements
    assert not any("DATABASE ROLE" in s for s in statements)
    other = deploy.access_statements(account_env(role="PLATFORM_ADMIN"), owns_warehouse=False)
    assert "GRANT ALL PRIVILEGES ON FUTURE TABLES IN SCHEMA AI_PLATFORM.CORE TO ROLE AODS_PLATFORM_ADMIN" in other
    assert not any("WAREHOUSE" in s for s in other)


class Cursor:
    def __init__(self, existing_roles, owner="AODS_PLATFORM_ADMIN"):
        self.roles, self.owner, self.description, self._rows = set(existing_roles), owner, [], []

    def execute(self, sql, params=None):
        m = re.match(r"SHOW ROLES LIKE '(\w+)'", sql)
        if m:
            self.description, self._rows = [("name",)], ([(m.group(1),)] if m.group(1) in self.roles else [])
        elif sql.startswith("SHOW WAREHOUSES"):
            self.description, self._rows = [("name",), ("owner",)], [("WH", self.owner)]
        else:
            self.description, self._rows = [("x",)], []

    def fetchall(self):
        return self._rows


def test_preflight_requires_every_aods_role_and_hands_the_lead_the_sql():
    grants = {"roles": {"AODS_PLATFORM_ADMIN"}, "account": set()}
    roles = deploy.account_role_checks(Cursor({"AODS_VIEWER"}), account_env(), grants)[0]
    assert roles.status == "missing" and roles.level == deploy.REQUIRED and roles.who == "your role administrator"
    assert "CREATE ROLE IF NOT EXISTS AODS_DATA_ENGINEER;" in roles.fix
    assert "GRANT ROLE AODS_VIEWER TO ROLE AODS_DATA_ENGINEER;" in roles.fix
    ready = deploy.account_role_checks(Cursor(access.account_roles("AODS")), account_env(), grants)
    assert ready[0].status == "ok" and len(ready) == 1   # admin role held, warehouse owned
    not_admin = deploy.account_role_checks(Cursor(access.account_roles("AODS"), owner="SYSADMIN"),
                                           account_env(role="DEPLOYER"), {"roles": {"DEPLOYER"}, "account": set()})
    assert [c.name for c in not_admin] == ["account roles", "admin role", "warehouse for users"]
    assert "GRANT USAGE ON WAREHOUSE WH TO ROLE AODS_VIEWER;" in not_admin[2].fix


def test_setup_defaults_to_aods_account_roles(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "ENV_DIR", tmp_path)
    monkeypatch.setattr(deploy, "known_connections", lambda: ["acme"])
    answers = iter(["1", "", "", "", "", ""])
    text = deploy.setup_env("acme", ask=lambda prompt: next(answers)).read_text()
    assert 'model = "account_roles"' in text and 'role_prefix = "AODS"' in text and 'role = "AODS_PLATFORM_ADMIN"' in text
    e = deploy.load_env("acme")
    assert e["accounts"] and e["role"] == "AODS_PLATFORM_ADMIN"


def test_runtime_admin_check_follows_the_recorded_model():
    assert access.admin_check_sql(None) == "IS_DATABASE_ROLE_IN_SESSION('PLATFORM_ADMIN')"
    assert access.admin_check_sql({"model": "account_roles", "prefix": "aods"}) == "IS_ROLE_IN_SESSION('AODS_PLATFORM_ADMIN')"
    assert access.normalise({"model": "nonsense"})["model"] == "database_roles"
    access._LOADED.clear()
    model = access.load(lambda sql: [{"V": '{"model": "account_roles", "prefix": "AODS_PROD"}'}], "t1")
    assert access.admin_check_sql(model) == "IS_ROLE_IN_SESSION('AODS_PROD_PLATFORM_ADMIN')"

    def boom(sql):
        raise RuntimeError("no table")
    assert access.load(boom, "t2")["model"] == "database_roles"


def test_deploy_records_the_model_and_makes_schemas_managed():
    class Cur:
        def __init__(self, stored=None):
            self.stored, self.sql = stored, []

        def execute(self, sql, params=None):
            self.sql.append(sql)

        def fetchone(self):
            return (self.stored,) if self.stored else None

    cur = Cur()
    assert ds.record_access_model(cur, "DB", {"model": "database_roles", "prefix": "AODS"}) == []
    assert not any("INSERT" in s for s in cur.sql)   # the default needs no row
    cur = Cur()
    assert ds.record_access_model(cur, "DB", {"model": "account_roles", "prefix": "AODS"})
    assert any("INSERT INTO DB.CORE.PLATFORM_CONFIG" in s for s in cur.sql)
    same = Cur('{"model": "account_roles", "prefix": "AODS"}')
    assert ds.record_access_model(same, "DB", {"model": "account_roles", "prefix": "AODS"}) == []
    managed = Cur()
    ds.managed_access(managed, "DB")
    assert "ALTER SCHEMA IF EXISTS DB.CORE ENABLE MANAGED ACCESS" in managed.sql
    assert "CREATE SCHEMA IF NOT EXISTS DB.CORE WITH MANAGED ACCESS" in ds.bootstrap_sql("DB", managed=True)
