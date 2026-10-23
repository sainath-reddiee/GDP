"""The one-command client deploy (infrastructure/deploy.py): the environment file, the preflight (role, warehouse,
database ownership, Cortex, access roles, optional features), the admin SQL handoff, the plan, the access grants and
the command's stop points. Never needs SYSADMIN; never creates account roles or database roles by hand."""

import pytest

from infrastructure import deploy
from infrastructure import deploy_snowflake as ds

ENV_RAW = {"snowflake": {"connection": "client", "role": "platform_admin", "warehouse": "ai_wh", "database": "ai_platform"},
           "access": {"model": "database_roles", "DATA_ENGINEER": ["DE_ROLE"], "viewer": "ANALYST", "OPS_SERVICE": []},
           "features": {"pr_sync_task": True}}


def env(**over):
    raw = {k: dict(v) if isinstance(v, dict) else v for k, v in ENV_RAW.items()}
    for section, values in over.items():
        raw[section] = {**raw.get(section, {}), **values}
    return deploy.validate_env(raw, "client")


class Cursor:
    """Answers by the first rule whose prefix starts the SQL: (description names, rows) or an exception."""

    def __init__(self, rules=None):
        self.rules = rules or {}
        self.sql, self.description, self._rows = [], [], []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "RESULT_SCAN(LAST_QUERY_ID())" in sql and "granted_on" in sql:
            # as Snowflake does: the previous SHOW's rows, only the role and account grants
            names = [d[0] for d in self.description]
            at = names.index("granted_on") if "granted_on" in names else None
            self._rows = [r for r in self._rows if at is None or r[at] in ("ROLE", "ACCOUNT")]
            return
        for prefix, answer in self.rules.items():
            if sql.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                names, rows = answer
                self.description = [(n,) for n in names]
                self._rows = list(rows)
                return
        self.description, self._rows = [("x",)], []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def healthy(**over):
    rules = {
        "SELECT CURRENT_USER()": (["U", "R", "A"], [("ANA", "PLATFORM_ADMIN", "ACME")]),
        'SHOW GRANTS TO ROLE "PLATFORM_ADMIN"': (["privilege", "granted_on", "name"], [("USAGE", "ROLE", "PA_BASE")]),
        'SHOW GRANTS TO ROLE "PA_BASE"': (["privilege", "granted_on", "name"],
                                         [("CREATE DATABASE", "ACCOUNT", "ACME"), ("EXECUTE TASK", "ACCOUNT", "ACME")]),
        "SHOW DATABASES LIKE": (["created_on", "name", "owner"], []),
        "SHOW ROLES LIKE 'DE_ROLE'": (["name"], [("DE_ROLE",)]),
        "SHOW ROLES LIKE 'ANALYST'": (["name"], [("ANALYST",)]),
        "SHOW PARAMETERS": (["key", "value"], [("CORTEX_ENABLED_CROSS_REGION", "ANY_REGION")]),
    }
    rules.update(over)
    return Cursor(rules)


# ---------------------------------------------------------------- the environment file


def test_env_is_normalised_and_the_deploy_role_administers_the_platform():
    e = env()
    assert (e["role"], e["warehouse"], e["database"]) == ("PLATFORM_ADMIN", "AI_WH", "AI_PLATFORM")
    assert e["access"] == {"DATA_ENGINEER": ["DE_ROLE"], "VIEWER": ["ANALYST"], "OPS_SERVICE": [],
                           "PLATFORM_ADMIN": ["PLATFORM_ADMIN"]}
    assert e["connection"] == "client" and e["pr_sync_task"] is True and e["github_integration"] is None


@pytest.mark.parametrize("over, message", [
    ({"snowflake": {"password": "x"}}, "must not hold a password"),
    ({"snowflake": {"role": "bad role"}}, "role must be"),
    ({"snowflake": {"connection": None}}, "needs connection"),
    ({"access": {"SYSADMIN": ["X"]}}, "not a platform database role"),
    ({"access": {"VIEWER": ["bad-role"]}}, "not a Snowflake identifier"),
])
def test_env_refuses_unsafe_or_unknown_settings(over, message):
    with pytest.raises(deploy.ConfigError, match=message):
        env(**over)


def test_example_file_is_valid(monkeypatch):
    monkeypatch.setattr(deploy, "known_connections", lambda: None)
    e = deploy.load_env(str(deploy.ENV_DIR / "example.toml"))
    assert e["role"] == "AODS_PLATFORM_ADMIN" and e["accounts"] and e["access_model"]["prefix"] == "AODS"


# ---------------------------------------------------------------- preflight


def by_name(checks):
    return {c.name: c for c in checks}


def test_preflight_passes_a_role_that_can_create_the_database():
    checks = by_name(deploy.preflight(healthy(), env()))
    assert checks["role"].status == "ok" and checks["warehouse"].status == "ok"
    assert checks["database"].status == "ok" and "may create it" in checks["database"].detail
    assert checks["cortex"].status == "ok" and checks["tasks"].status == "ok"
    assert checks["access DE_ROLE"].status == "ok" and not deploy.blocking(checks.values())


def test_preflight_explains_each_missing_privilege_and_who_grants_it():
    cur = healthy(**{
        'SHOW GRANTS TO ROLE "PA_BASE"': (["privilege", "granted_on", "name"], []),
        "USE WAREHOUSE": RuntimeError("Object does not exist or not authorized."),
        "SELECT AI_COMPLETE": RuntimeError("Unknown function AI_COMPLETE"),
        "SHOW ROLES LIKE 'ANALYST'": (["name"], []),
    })
    checks = by_name(deploy.preflight(cur, env()))
    assert checks["warehouse"].fix == ["GRANT USAGE ON WAREHOUSE AI_WH TO ROLE PLATFORM_ADMIN;"]
    assert checks["database"].who.startswith("SYSADMIN")
    assert "GRANT OWNERSHIP ON DATABASE AI_PLATFORM TO ROLE PLATFORM_ADMIN COPY CURRENT GRANTS;" in checks["database"].fix
    assert checks["cortex"].fix == ["GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE PLATFORM_ADMIN;"]
    assert checks["tasks"].level == deploy.OPTIONAL and checks["access ANALYST"].level == deploy.OPTIONAL
    assert {c.name for c in deploy.blocking(checks.values())} == {"warehouse", "database", "cortex"}


def test_an_existing_database_must_be_owned_by_the_deploy_role():
    owned = healthy(**{"SHOW DATABASES LIKE": (["created_on", "name", "owner"], [("t", "AI_PLATFORM", "PA_BASE")])})
    assert by_name(deploy.preflight(owned, env()))["database"].status == "ok"   # owned through an inherited role
    other = healthy(**{"SHOW DATABASES LIKE": (["created_on", "name", "owner"], [("t", "AI_PLATFORM", "SYSADMIN")]),
                       "SHOW GRANTS ON DATABASE": (["privilege", "grantee_name"], [("USAGE", "PLATFORM_ADMIN")])})
    check = by_name(deploy.preflight(other, env()))["database"]
    assert check.status == "missing" and check.who.startswith("SYSADMIN (the database owner)")
    granted = healthy(**{"SHOW DATABASES LIKE": (["created_on", "name", "owner"], [("t", "AI_PLATFORM", "SYSADMIN")]),
                         "SHOW GRANTS ON DATABASE": (["privilege", "grantee_name"], [
                             ("USAGE", "PLATFORM_ADMIN"), ("CREATE SCHEMA", "PLATFORM_ADMIN"),
                             ("CREATE DATABASE ROLE", "PA_BASE")])})
    assert by_name(deploy.preflight(granted, env()))["database"].status == "warn"


def test_cortex_check_tells_privilege_from_model_or_region_problems():
    assert deploy.cortex_check(healthy(), "PLATFORM_ADMIN", "claude-sonnet-4-5").status == "ok"
    denied = healthy(**{"SELECT AI_COMPLETE": RuntimeError("SQL compilation error: Unknown function AI_COMPLETE")})
    check = deploy.cortex_check(denied, "PLATFORM_ADMIN", "claude-sonnet-4-5")
    assert check.fix == ["GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE PLATFORM_ADMIN;"]
    region = healthy(**{"SELECT AI_COMPLETE": RuntimeError("400 model claude-sonnet-4-5 is unavailable in this region"),
                        "SHOW PARAMETERS": (["key", "value"], [("CORTEX_ENABLED_CROSS_REGION", "DISABLED")])})
    check = deploy.cortex_check(region, "PLATFORM_ADMIN", "claude-sonnet-4-5")
    assert check.status == "missing" and "CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION'" in check.fix[0]
    anywhere = healthy(**{"SELECT AI_COMPLETE": RuntimeError("unknown model")})
    assert "choose another llm_model" in deploy.cortex_check(anywhere, "PLATFORM_ADMIN", "claude-sonnet-4-5").fix[0]
    with pytest.raises(AssertionError):
        deploy.cortex_check(healthy(), "PLATFORM_ADMIN", "x'); DROP TABLE T; --")


def test_a_failed_sign_in_changes_nothing_and_says_why(command, monkeypatch):
    applied, _ = command

    def refuse(e):
        raise RuntimeError("Unable to receive the OAuth message within a given timeout")
    monkeypatch.setattr(deploy, "connect", refuse)
    assert deploy.main(["--env", "client", "--yes"]) == 1 and applied == []


def test_preflight_switches_role_after_sign_in_and_explains_a_wrong_one():
    cur = healthy()
    deploy.preflight(cur, env())
    assert cur.sql[0] == 'USE ROLE "PLATFORM_ADMIN"' and "USE SECONDARY ROLES NONE" in cur.sql
    wrong = healthy(**{"USE ROLE": RuntimeError("Role 'PLATFORM_ADMIN' specified does not exist or not authorized."),
                       "SELECT CURRENT_USER()": (["U", "R", "A", "AVAILABLE"],
                                                 [("ANA", "PUBLIC", "ACME", '["PUBLIC","ANALYST"]')])})
    checks = deploy.preflight(wrong, env())
    assert len(checks) == 1 and checks[0].status == "missing" and 'GRANT ROLE PLATFORM_ADMIN TO USER "ANA";' in checks[0].fix
    assert "Roles this user has: PUBLIC,ANALYST" in checks[0].detail and "not a database role" in checks[0].detail
    assert not any(s.startswith("USE WAREHOUSE") for s in wrong.sql)   # nothing else runs under the wrong role


def test_admin_sql_groups_statements_by_who_runs_them_and_never_creates_database_roles():
    cur = healthy(**{'SHOW GRANTS TO ROLE "PA_BASE"': (["privilege", "granted_on", "name"], []),
                     "SELECT AI_COMPLETE": RuntimeError("Unknown function AI_COMPLETE"),
                     "SHOW ROLES LIKE 'ANALYST'": (["name"], [])})
    sql = deploy.admin_sql(deploy.preflight(cur, env()), env())
    assert "-- ===== run as ACCOUNTADMIN =====" in sql and "GRANT EXECUTE TASK ON ACCOUNT TO ROLE PLATFORM_ADMIN;" in sql
    assert "-- ===== run as your role administrator =====" in sql and "CREATE ROLE IF NOT EXISTS ANALYST;" in sql
    assert "CREATE DATABASE IF NOT EXISTS AI_PLATFORM;" in sql
    assert "CREATE DATABASE ROLE" not in sql and "SYSADMIN TO" not in sql
    assert "Nothing to do" in deploy.admin_sql(deploy.preflight(healthy(), env()), env())


# ---------------------------------------------------------------- access, plan, bootstrap


def test_access_grants_every_mapping_and_warehouse_usage_only_when_owned():
    statements = deploy.access_statements(env(), owns_warehouse=False)
    assert "GRANT DATABASE ROLE AI_PLATFORM.DATA_ENGINEER TO ROLE DE_ROLE" in statements
    assert "GRANT DATABASE ROLE AI_PLATFORM.PLATFORM_ADMIN TO ROLE PLATFORM_ADMIN" in statements
    assert not any("WAREHOUSE" in s for s in statements) and not any("REVOKE" in s for s in statements)
    owned = deploy.access_statements(env(), owns_warehouse=True)
    assert "GRANT USAGE ON WAREHOUSE AI_WH TO ROLE ANALYST" in owned
    assert "GRANT USAGE ON WAREHOUSE AI_WH TO ROLE PLATFORM_ADMIN" not in owned


def test_plan_lists_pending_work_and_stops_on_an_edited_migration(monkeypatch):
    migrations = ds.list_migrations()
    first, last = migrations[0], migrations[-1]
    applied = [(v, ds.checksum(p.read_text(encoding="utf-8"))) for v, _, p in migrations[:-1]]
    cur = Cursor({"SHOW DATABASES LIKE": (["created_on", "name"], [("t", "AI_PLATFORM")]),
                  "SELECT VERSION, CHECKSUM": (["VERSION", "CHECKSUM"], applied),
                  "SELECT OBJECT_KEY": (["OBJECT_KEY", "CHECKSUM"], [])})
    steps = deploy.plan(cur, env())
    assert any(s.startswith(f"migration V{last[0]} {last[1]}") for s in steps) and any(s.startswith("code package") for s in steps)
    edited = Cursor({**cur.rules, "SELECT VERSION, CHECKSUM": (["VERSION", "CHECKSUM"], [(first[0], "changed")])})
    assert any(s.startswith(f"STOP: V{first[0]}") for s in deploy.plan(edited, env()))
    new = Cursor({"SHOW DATABASES LIKE": (["created_on", "name"], [])})
    assert "a new environment" in deploy.plan(new, env())[0]


def test_bootstrap_skips_create_database_when_it_exists():
    assert "CREATE DATABASE" in ds.bootstrap_sql("AI_PLATFORM")
    assert "CREATE DATABASE" not in ds.bootstrap_sql("AI_PLATFORM", create_database=False)
    cur = Cursor({"SHOW DATABASES LIKE": (["created_on", "name"], [("t", "AI_PLATFORMX"), ("t", "AI_PLATFORM")])})
    assert ds.database_exists(cur, "AI_PLATFORM")
    assert not ds.database_exists(Cursor({"SHOW DATABASES LIKE": (["created_on", "name"], [("t", "AI_PLATFORMX")])}),
                                  "AI_PLATFORM")


# ---------------------------------------------------------------- the command


class Con:
    def __init__(self, cur):
        self.cur, self.closed = cur, False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


@pytest.fixture()
def command(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "load_env", lambda name: env())
    monkeypatch.setattr(deploy, "LOG_DIR", tmp_path)
    applied = []
    monkeypatch.setattr(ds, "apply_from_session", lambda con, db, wh, force=False, access=None: applied.append((db, wh)) or ["deployed"])
    monkeypatch.setattr(deploy, "plan", lambda cur, e: ["migration V068 x"])
    monkeypatch.setattr(deploy, "verify", lambda cur, e: ["verify: ok"])
    return applied, tmp_path


def test_command_stops_before_changing_anything_when_privileges_are_missing(command, monkeypatch):
    applied, tmp = command
    cur = healthy(**{"USE WAREHOUSE": RuntimeError("not authorized")})
    monkeypatch.setattr(deploy, "connect", lambda e: Con(cur))
    assert deploy.main(["--env", "client", "--yes"]) == 2 and applied == []
    assert deploy.main(["--env", "client", "--admin-sql"]) == 0
    assert "GRANT USAGE ON WAREHOUSE AI_WH" in (tmp / "client-admin.sql").read_text()


def test_command_needs_the_database_name_typed_to_deploy(command, monkeypatch):
    applied, tmp = command
    monkeypatch.setattr(deploy, "connect", lambda e: Con(healthy()))
    monkeypatch.setattr("builtins.input", lambda prompt: "wrong")
    assert deploy.main(["--env", "client"]) == 1 and applied == []
    monkeypatch.setattr("builtins.input", lambda prompt: "ai_platform")
    assert deploy.main(["--env", "client"]) == 0 and applied == [("AI_PLATFORM", "AI_WH")]
    logs = list(tmp.glob("client-*.log"))
    text = logs[-1].read_text()
    assert "3. deploy" in text and "GRANT DATABASE ROLE AI_PLATFORM.DATA_ENGINEER TO ROLE DE_ROLE" in text


def test_check_and_plan_change_nothing(command, monkeypatch):
    applied, tmp = command
    cur = healthy()
    monkeypatch.setattr(deploy, "connect", lambda e: Con(cur))
    assert deploy.main(["--env", "client", "--check"]) == 0
    assert deploy.main(["--env", "client", "--plan"]) == 0
    assert applied == [] and not any(s.startswith(("GRANT", "CREATE")) for s in cur.sql)
    assert not list(tmp.glob("*.log"))


# ---------------------------------------------------------------- guided setup


def answers(*values):
    it = iter(values)
    return lambda prompt: next(it)


def test_setup_with_a_saved_connection_writes_a_valid_file(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "ENV_DIR", tmp_path)
    monkeypatch.setattr(deploy, "known_connections", lambda: ["default_connection_name", "acme"])
    path = deploy.setup_env("acme-prod", ask=answers("2", "2", "", "", "", "de_role, analyst"))
    text = path.read_text()
    assert 'connection = "acme"' in text and 'DATA_ENGINEER = ["DE_ROLE", "ANALYST"]' in text
    e = deploy.load_env("acme-prod")
    assert (e["role"], e["warehouse"], e["database"]) == ("PLATFORM_ADMIN", "AI_PLATFORM_WH", "AI_PLATFORM")


def test_setup_with_an_account_and_sso_or_key_never_asks_for_a_password(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "ENV_DIR", tmp_path)
    monkeypatch.setattr(deploy, "known_connections", lambda: [])
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return {0: "abc-xy123", 1: "ana@client.com", 2: "1", 3: "2", 4: "my_role", 5: "", 6: "", 7: ""}[len(prompts) - 1]
    text = deploy.setup_env("client", ask=ask).read_text()
    assert 'account = "abc-xy123"' in text and 'authenticator = "externalbrowser"' in text and 'role = "MY_ROLE"' in text
    assert not any("password" in p.lower() for p in prompts)
    key = deploy.setup_env("client2", ask=answers("abc-xy123", "ana", "2", r"C:\keys\d.p8", "2", "", "", "", "")).read_text()
    assert 'authenticator = "snowflake_jwt"' in key and 'private_key_file = "C:/keys/d.p8"' in key
    assert deploy.load_env("client2")["connect"]["authenticator"] == "snowflake_jwt"


def test_setup_asks_again_for_an_invalid_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "ENV_DIR", tmp_path)
    monkeypatch.setattr(deploy, "known_connections", lambda: [])
    text = deploy.setup_env("c", ask=answers("bad account!", "abc-1", "u", "1", "2", "bad role", "OK_ROLE", "", "", "")).read_text()
    assert 'account = "abc-1"' in text and 'role = "OK_ROLE"' in text


def test_an_unknown_connection_name_is_caught_before_sign_in(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "ENV_DIR", tmp_path)
    (tmp_path / "x.toml").write_text('[snowflake]\nconnection = "client-prod"\nrole = "R"\nwarehouse = "W"\ndatabase = "D"\n'
                                     '[access]\nmodel = "database_roles"\n')
    monkeypatch.setattr(deploy, "known_connections", lambda: ["anblicks"])
    with pytest.raises(deploy.ConfigError, match="known: anblicks.*--setup"):
        deploy.load_env("x")
    monkeypatch.setattr(deploy, "known_connections", lambda: None)   # unreadable file: let the connector decide
    assert deploy.load_env("x")["connection"] == "client-prod"
