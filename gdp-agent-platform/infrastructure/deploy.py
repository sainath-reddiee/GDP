"""Deploy the platform to one Snowflake environment with one command, as the role you were given (never SYSADMIN).

    python infrastructure/deploy.py --env client-prod                # check, show the plan, confirm, deploy, grant, verify
    python infrastructure/deploy.py --env client-prod --check        # privileges only: what is missing and who grants it
    python infrastructure/deploy.py --env client-prod --plan         # what would change (read only)
    python infrastructure/deploy.py --env client-prod --admin-sql    # write the one-time SQL for the account admins
    python infrastructure/deploy.py --env client-prod --yes          # no confirmation prompt (CI)

The environment file is infrastructure/environments/<env>.toml (start from example.toml). Steps, in order:

  1. preflight  connect as the configured role; check the warehouse, the database (owned, or creatable), Cortex, the
                roles in [access], and the optional features. Stops before changing anything when a required
                privilege is missing, and writes the SQL the account admins must run once (--admin-sql).
  2. plan       pending migrations and the deploy steps whose inputs changed since the last deploy.
  3. deploy     infrastructure/deploy_snowflake.py, which applies everything in dependency order: database and
                ledger, schema and tables (baseline or migrations, which also create the platform's database roles and
                their grants), workflow seed, code package, procedures, GitHub and Oracle refreshes, knowledge seed,
                views, Cortex Search services, agents. Each step is skipped when nothing in it changed.
  4. access     GRANT DATABASE ROLE <db>.<role> TO ROLE <account role> for every line of [access]; the deploy role
                can do this itself because it owns the database roles it created. Warehouse usage too, when it owns
                the warehouse. Nothing is revoked.
  5. verify     the migration ledger is current, procedures, Cortex Search services and the agent exist.

Account-level work is never done here: creating account roles, Cortex access, EXECUTE TASK, integrations. Those are
one-time grants by your account admins; --admin-sql writes exactly the statements this environment still needs, each
under the role that must run it. Database roles are not in that list: the deploy creates them as the database owner.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ENV_DIR = ROOT / "infrastructure" / "environments"
LOG_DIR = ROOT / "deploy-logs"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,254}$")
DATABASE_ROLES = ("PLATFORM_ADMIN", "DATA_ENGINEER", "DATA_STEWARD", "DOMAIN_OWNER", "QA_ENGINEER", "REVIEWER",
                  "VIEWER", "OPS_SERVICE", "OPS_VIEWER", "SERVICE_AGENT")
CONNECT_KEYS = ("account", "user", "authenticator", "private_key_file", "host")   # never a password in the file
REQUIRED, OPTIONAL = "required", "optional"


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- the environment file

def load_env(name: str) -> Dict[str, Any]:
    """The environment as a dict, validated. `name` is a file name in infrastructure/environments or a path."""
    import tomllib

    path = Path(name)
    if not path.suffix:
        path = ENV_DIR / f"{name}.toml"
    if not path.exists():
        raise ConfigError(f"no environment file {path}; copy infrastructure/environments/example.toml")
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return validate_env(raw, path.stem)


def validate_env(raw: Dict[str, Any], name: str) -> Dict[str, Any]:
    sf = dict(raw.get("snowflake") or {})
    problems: List[str] = []
    if "password" in {k.lower() for k in sf}:
        problems.append("[snowflake] must not hold a password: use connections.toml, SSO (externalbrowser) or a key file")
    if not sf.get("connection") and not (sf.get("account") and sf.get("user")):
        problems.append("[snowflake] needs connection (a name in ~/.snowflake/connections.toml) or account and user")
    for key in ("role", "warehouse", "database"):
        if not IDENT.match(str(sf.get(key) or "")):
            problems.append(f"[snowflake] {key} must be a Snowflake identifier")
    access = {}
    for db_role, roles in (raw.get("access") or {}).items():
        if db_role.upper() not in DATABASE_ROLES:
            problems.append(f"[access] {db_role} is not a platform database role ({', '.join(DATABASE_ROLES)})")
            continue
        roles = [roles] if isinstance(roles, str) else list(roles or [])
        bad = [r for r in roles if not IDENT.match(str(r))]
        if bad:
            problems.append(f"[access] {db_role}: {bad[0]} is not a Snowflake identifier")
        access[db_role.upper()] = sorted({str(r).upper() for r in roles})
    role = str(sf.get("role") or "").upper()
    if role and role not in access.get("PLATFORM_ADMIN", []):   # the deploy role administers the platform
        access["PLATFORM_ADMIN"] = sorted(set(access.get("PLATFORM_ADMIN", [])) | {role})
    features = dict(raw.get("features") or {})
    integration = str(features.get("github_integration") or "")
    if integration and not IDENT.match(integration):
        problems.append("[features] github_integration must be a Snowflake identifier")
    if problems:
        raise ConfigError("; ".join(problems))
    return {"name": name, "role": role, "warehouse": str(sf["warehouse"]).upper(), "database": str(sf["database"]).upper(),
            "connection": sf.get("connection"), "connect": {k: sf[k] for k in CONNECT_KEYS if sf.get(k)},
            "access": access, "model": str(features.get("llm_model") or "claude-sonnet-4-5"),
            "pr_sync_task": bool(features.get("pr_sync_task")), "github_integration": integration.upper() or None,
            "actual_cost": bool(features.get("actual_cost_from_account_usage"))}


# ---------------------------------------------------------------- reading Snowflake

def _rows(cur, sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    cur.execute(sql, params) if params else cur.execute(sql)
    names = [d[0].lower() for d in (cur.description or [])]
    return [dict(zip(names, r)) for r in cur.fetchall() or []]


def _try(cur, sql: str) -> Tuple[bool, str]:
    try:
        cur.execute(sql)
        cur.fetchall()
        return True, ""
    except Exception as exc:   # Snowflake puts the reason on the line after "SQL compilation error:"
        return False, " ".join(line.strip() for line in str(exc).splitlines() if line.strip())[:300]


def role_grants(cur, role: str, limit: int = 50) -> Dict[str, Any]:
    """{roles: every role `role` inherits (itself included), account: privileges on the account, of any of them}."""
    seen, todo, account = set(), [role.upper()], set()
    while todo and len(seen) < limit:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            grants = _rows(cur, f'SHOW GRANTS TO ROLE "{current}"')
        except Exception:
            continue
        for g in grants:
            on, privilege, name = str(g.get("granted_on") or "").upper(), str(g.get("privilege") or "").upper(), \
                str(g.get("name") or "").upper().strip('"')
            if on == "ROLE" and privilege == "USAGE":
                todo.append(name)
            elif on == "ACCOUNT":
                account.add(privilege)
    return {"roles": seen, "account": account}


def _show_one(cur, kind: str, name: str) -> Optional[Dict[str, Any]]:
    try:
        found = _rows(cur, f"SHOW {kind} LIKE '{name}'")
    except Exception:
        return None
    return next((r for r in found if str(r.get("name") or "").upper() == name.upper()), None)


# ---------------------------------------------------------------- preflight

@dataclass
class Check:
    name: str
    status: str                      # ok | missing | warn | info
    detail: str
    level: str = REQUIRED            # a missing required check stops the deploy
    who: str = ""                    # the role that must fix it
    fix: List[str] = field(default_factory=list)


def preflight(cur, env: Dict[str, Any]) -> List[Check]:
    role, wh, db = env["role"], env["warehouse"], env["database"]
    checks: List[Check] = []
    # sign-in uses the user's default role; switching here lets a wrong role be explained instead of failing the sign-in
    ok, err = _try(cur, f'USE ROLE "{role}"')
    found = _rows(cur, "SELECT CURRENT_USER() AS U, CURRENT_ROLE() AS R, CURRENT_ACCOUNT() AS A, "
                       "CURRENT_AVAILABLE_ROLES() AS AVAILABLE")[0]
    current = str(found.get("r") or "").upper()
    if not ok or current != role:
        names = [n.strip() for n in str(found.get("available") or "").strip("[]").replace('"', "").split(",") if n.strip()]
        available = ",".join(names[:15]) + (f" and {len(names) - 15} more" if len(names) > 15 else "")
        checks.append(Check("role", "missing", f"cannot use role {role} ({err or 'not the session role'}). Roles this user "
                            f"has: {available or 'unknown'}. Use an account role here, not a database role such as "
                            f"{db}.DATA_ENGINEER", who="your role administrator",
                            fix=[f"GRANT ROLE {role} TO USER {found.get('u')};"]))
        return checks
    _try(cur, "USE SECONDARY ROLES NONE")   # everything the deploy creates is owned by, and checked against, this role
    checks.append(Check("role", "ok", f"connected to {found.get('a')} as {found.get('u')} with role {role}"))
    grants = role_grants(cur, role)

    ok, err = _try(cur, f"USE WAREHOUSE {wh}")
    checks.append(Check("warehouse", "ok", f"{wh} is usable") if ok else Check(
        "warehouse", "missing", f"cannot use {wh} ({err})", who="the warehouse owner",
        fix=[f"GRANT USAGE ON WAREHOUSE {wh} TO ROLE {role};"]))

    database = _show_one(cur, "DATABASES", db)
    if database:
        owner = str(database.get("owner") or "").upper()
        if owner in grants["roles"]:
            checks.append(Check("database", "ok", f"{db} exists and {role} owns it (through {owner})"
                                if owner != role else f"{db} exists and {role} owns it"))
        else:
            held = {str(g.get("privilege") or "").upper() for g in _safe_rows(cur, f"SHOW GRANTS ON DATABASE {db}")
                    if str(g.get("grantee_name") or "").upper() in grants["roles"]}
            need = {"USAGE", "CREATE SCHEMA", "CREATE DATABASE ROLE"}
            if need <= held:
                checks.append(Check("database", "warn", f"{db} is owned by {owner}; {role} can create schemas and database "
                                    "roles in it, but objects another role created will not be changeable by the deploy"))
            else:
                checks.append(Check("database", "missing", f"{db} exists, owned by {owner}; {role} does not own it",
                                    who=f"{owner} (the database owner)",
                                    fix=[f"GRANT OWNERSHIP ON DATABASE {db} TO ROLE {role} COPY CURRENT GRANTS;"]))
    elif "CREATE DATABASE" in grants["account"]:
        checks.append(Check("database", "ok", f"{db} does not exist yet; {role} may create it and will own it"))
    else:
        checks.append(Check("database", "missing", f"{db} does not exist and {role} cannot create databases",
                            who="SYSADMIN (or any role with CREATE DATABASE)",
                            fix=[f"CREATE DATABASE IF NOT EXISTS {db};",
                                 f"GRANT OWNERSHIP ON DATABASE {db} TO ROLE {role} COPY CURRENT GRANTS;"]))

    checks.append(cortex_check(cur, role, env["model"]))

    for db_role, roles in sorted(env["access"].items()):
        for account_role in roles:
            if account_role == role:
                continue
            if _show_one(cur, "ROLES", account_role):
                checks.append(Check(f"access {account_role}", "ok", f"{account_role} exists; it will get {db}.{db_role}",
                                    level=OPTIONAL))
            else:
                checks.append(Check(f"access {account_role}", "missing", f"account role {account_role} does not exist "
                                    "(or is not visible)", level=OPTIONAL, who="your role administrator",
                                    fix=[f"CREATE ROLE IF NOT EXISTS {account_role};",
                                         f"-- then grant {account_role} to the people (or service user) who need {db_role}"]))

    if env["pr_sync_task"]:
        checks.append(Check("tasks", "ok", "EXECUTE TASK granted: the pull request sync task can run", level=OPTIONAL)
                      if "EXECUTE TASK" in grants["account"] else Check(
                          "tasks", "missing", "the pull request sync task needs EXECUTE TASK on the account",
                          level=OPTIONAL, who="ACCOUNTADMIN", fix=[f"GRANT EXECUTE TASK ON ACCOUNT TO ROLE {role};"]))
    if env["github_integration"]:
        name = env["github_integration"]
        checks.append(Check("github", "ok", f"integration {name} is usable", level=OPTIONAL)
                      if _show_one(cur, "INTEGRATIONS", name) else Check(
                          "github", "missing", f"integration {name} does not exist or {role} has no USAGE on it",
                          level=OPTIONAL, who="ACCOUNTADMIN",
                          fix=[f"-- create the GitHub external access integration {name} (see docs), then:",
                               f"GRANT USAGE ON INTEGRATION {name} TO ROLE {role};"]))
    if env["actual_cost"]:
        ok, _ = _try(cur, "SELECT 1 FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_QUERY_USAGE_HISTORY LIMIT 0")
        checks.append(Check("actual cost", "ok", "Cortex usage history is readable (actual credits)", level=OPTIONAL)
                      if ok else Check("actual cost", "missing", "SNOWFLAKE.ACCOUNT_USAGE is not readable; AI cost stays "
                                       "an estimate", level=OPTIONAL, who="ACCOUNTADMIN",
                                       fix=[f"GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER TO ROLE {role};"]))
    return checks


MODEL = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,62}$")
NOT_ALLOWED = re.compile(r"(?i)unknown function|does not exist or not authorized|insufficient privileges|not authorized")


def cortex_check(cur, role: str, model: str) -> Check:
    """One AI_COMPLETE call of one token (a negligible cost): the only test that proves this role can use this model in
    this account. COUNT_TOKENS cannot stand in for it: it does not know every model AI_COMPLETE serves. A privilege
    error and a model or region error need different admins' fixes."""
    assert MODEL.match(model), f"unsafe model name {model}"
    ok, err = _try(cur, f"SELECT AI_COMPLETE(model => '{model}', prompt => 'Reply with OK', "
                        "model_parameters => {'temperature': 0, 'max_tokens': 1}) AS R")
    if ok:
        return Check("cortex", "ok", f"{role} can call {model} through Cortex")
    if NOT_ALLOWED.search(err):
        return Check("cortex", "missing", f"Cortex functions are not available to {role} ({err})", who="ACCOUNTADMIN",
                     fix=[f"GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE {role};"])
    region = _safe_rows(cur, "SHOW PARAMETERS LIKE 'CORTEX_ENABLED_CROSS_REGION' IN ACCOUNT")
    value = str((region[0] if region else {}).get("value") or "unknown")
    return Check("cortex", "missing", f"{model} could not be called ({err}); CORTEX_ENABLED_CROSS_REGION = {value}. "
                 "Allow cross-region inference, or set llm_model to a model offered in this region",
                 who="ACCOUNTADMIN", fix=["ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';"]
                 if value.upper() in ("DISABLED", "FALSE", "UNKNOWN") else
                 [f"-- {model} is not available even with cross-region inference ({value}); choose another llm_model"])


def _safe_rows(cur, sql: str) -> List[Dict[str, Any]]:
    try:
        return _rows(cur, sql)
    except Exception:
        return []


def blocking(checks: Iterable[Check]) -> List[Check]:
    return [c for c in checks if c.status == "missing" and c.level == REQUIRED]


def admin_sql(checks: Iterable[Check], env: Dict[str, Any]) -> str:
    """The statements the account admins must run once, grouped by the role that runs them."""
    by_who: Dict[str, List[Check]] = {}
    for c in checks:
        if c.fix and c.who:
            by_who.setdefault(c.who, []).append(c)
    lines = [f"-- One-time setup for the AI platform in environment '{env['name']}' (database {env['database']}, "
             f"deploy role {env['role']}).",
             f"-- Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by infrastructure/deploy.py --admin-sql.",
             "-- Database roles are NOT created here: the deploy creates them itself, as the owner of the database.",
             "-- Run each block as the role named above it, then run the deploy again.", ""]
    if not by_who:
        lines.append("-- Nothing to do: every privilege the deploy needs is in place.")
    for who, items in by_who.items():
        lines.append(f"-- ===== run as {who} =====")
        for c in items:
            lines.append(f"-- {c.name}: {c.detail}" + ("" if c.level == REQUIRED else " (optional)"))
            lines.extend(c.fix)
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- plan

def plan(cur, env: Dict[str, Any]) -> List[str]:
    """What the deploy would do (read only): pending migrations and the steps whose inputs changed."""
    from infrastructure import deploy_snowflake as ds
    from infrastructure.seed_knowledge import seed_inputs_digest
    from services.common.platform_schema import PLATFORM_SCHEMA

    db, wh = env["database"], env["warehouse"]
    if not ds.database_exists(cur, db):
        return [f"{db} does not exist: a new environment (database, baseline schema, every step)"]
    applied = dict((r["version"], r["checksum"]) for r in _safe_rows(cur, f"SELECT VERSION, CHECKSUM FROM {db}.CORE.SCHEMA_MIGRATION"))
    state = dict((r["object_key"], r["checksum"]) for r in _safe_rows(cur, f"SELECT OBJECT_KEY, CHECKSUM FROM {db}.CORE.DEPLOY_STATE"))
    if not applied:
        return [f"{db} exists but has no platform ledger: the baseline creates the schema, then every step runs"]
    out = []
    for version, name, path in ds.list_migrations():
        if version not in applied:
            out.append(f"migration V{version} {name}")
        elif applied[version] != ds.checksum(path.read_text(encoding="utf-8")):
            out.append(f"STOP: V{version} {name} changed after it was applied (checksum mismatch)")
    zip_name, _ = ds.build_services_zip()
    variables = {"database": db, "schema": PLATFORM_SCHEMA, "services_import": f"@{db}.{PLATFORM_SCHEMA}.CODE_STAGE/{zip_name}",
                 "warehouse": wh, "agent_skills": ds.agent_skills_yaml(db)}
    if state.get("package/services") != zip_name:
        out.append(f"code package {zip_name}")
    for path in sorted(ds.PROCEDURES_DIR.glob("*.sql")):
        if state.get(f"procedures/{path.name}") != ds.checksum(ds.render(path.read_text(encoding="utf-8"), variables)):
            out.append(f"procedures {path.name}")
    if state.get("seed/platform") != seed_inputs_digest():
        out.append("knowledge seed (domain packs, skills, platform config)")
    for path in sorted(ds.VIEWS_DIR.glob("*.sql")) + sorted(ds.SEARCH_DIR.glob("*.sql")) + sorted(ds.AGENTS_DIR.glob("*.sql")):
        if state.get(f"{path.parent.name}/{path.name}") != ds.checksum(ds.render(path.read_text(encoding="utf-8"), variables)):
            out.append(f"{path.parent.name} {path.name}")
    return out or ["nothing changed since the last deploy"]


# ---------------------------------------------------------------- access and verify

def access_statements(env: Dict[str, Any], owns_warehouse: bool) -> List[str]:
    db, out = env["database"], []
    for db_role, roles in sorted(env["access"].items()):
        for account_role in roles:
            out.append(f"GRANT DATABASE ROLE {db}.{db_role} TO ROLE {account_role}")
    if owns_warehouse:
        for account_role in sorted({r for roles in env["access"].values() for r in roles} - {env["role"]}):
            out.append(f"GRANT USAGE ON WAREHOUSE {env['warehouse']} TO ROLE {account_role}")
    return out


def grant_access(cur, env: Dict[str, Any]) -> List[str]:
    wh = _show_one(cur, "WAREHOUSES", env["warehouse"])
    owns = bool(wh) and str(wh.get("owner") or "").upper() in role_grants(cur, env["role"])["roles"]
    log = []
    for sql in access_statements(env, owns):
        try:
            cur.execute(sql)
            log.append(f"access: {sql}")
        except Exception as exc:
            log.append(f"access: FAILED {sql} ({str(exc).splitlines()[0][:200]})")
    if not owns:
        log.append(f"access: {env['warehouse']} is not owned by {env['role']}; its owner grants USAGE to the access roles")
    return log


def verify(cur, env: Dict[str, Any]) -> List[str]:
    from infrastructure import deploy_snowflake as ds

    db = env["database"]
    latest = ds.list_migrations()[-1][0]
    applied = _safe_rows(cur, f"SELECT MAX(VERSION) AS V FROM {db}.CORE.SCHEMA_MIGRATION")
    have = str((applied[0] if applied else {}).get("v") or "none")
    out = [f"verify: migrations at V{have} (repository V{latest})" + ("" if have == latest else "  <-- NOT CURRENT")]
    out.append(f"verify: {len(_safe_rows(cur, f'SHOW PROCEDURES IN SCHEMA {db}.CORE'))} procedures")
    searches = _safe_rows(cur, f"SHOW CORTEX SEARCH SERVICES IN SCHEMA {db}.CORE")
    out.append(f"verify: {len(searches)} Cortex Search services"
               + "".join(f"; {s.get('name')} {s.get('indexing_state') or s.get('serving_state') or ''}".rstrip() for s in searches))
    agents = _safe_rows(cur, f"SHOW AGENTS IN SCHEMA {db}.CORE")
    out.append(f"verify: {len(agents)} agent(s)")
    roles = _safe_rows(cur, f"SHOW DATABASE ROLES IN DATABASE {db}")
    missing = sorted(set(DATABASE_ROLES) - {str(r.get("name") or "").upper() for r in roles})
    out.append("verify: all platform database roles exist" if not missing else f"verify: database roles missing: {missing}")
    return out


# ---------------------------------------------------------------- the command

def connect(env: Dict[str, Any]):
    import snowflake.connector

    sys.path.insert(0, str(ROOT / "apps" / "api"))
    try:   # the same in-memory SSO token cache as apps/api
        import app.db  # noqa: F401
    except Exception:
        pass
    # no role at sign-in: the preflight switches to it (USE ROLE), so a wrong or missing role is explained, not refused
    args = {"login_timeout": 300, "client_session_keep_alive": True, "client_store_temporary_credential": True,
            **env["connect"]}
    if env["connection"]:
        args["connection_name"] = env["connection"]
    return snowflake.connector.connect(**args)


class Log:
    def __init__(self, env_name: str, write: bool):
        self.lines: List[str] = []
        self.path = LOG_DIR / f"{env_name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.log" if write else None

    def __call__(self, line: str = "") -> None:
        print(line, flush=True)
        self.lines.append(line)

    def save(self) -> Optional[Path]:
        if not self.path:
            return None
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        return self.path


def print_checks(log: Log, checks: List[Check]) -> None:
    mark = {"ok": "ok     ", "missing": "MISSING", "warn": "warn   ", "info": "info   "}
    for c in checks:
        suffix = "" if c.level == REQUIRED or c.status in ("ok", "info") else " (optional)"
        log(f"  {mark[c.status]} {c.name}: {c.detail}{suffix}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", required=True, help="environment file name in infrastructure/environments (or a path)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="preflight only")
    mode.add_argument("--plan", action="store_true", help="preflight and the plan, change nothing")
    mode.add_argument("--admin-sql", nargs="?", const="", metavar="FILE",
                      help="write the one-time SQL for the account admins (default deploy-logs/<env>-admin.sql)")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--force", action="store_true", help="apply every step, even unchanged ones")
    args = parser.parse_args(argv)
    try:
        env = load_env(args.env)
    except ConfigError as exc:
        parser.error(str(exc))
    writes = not (args.check or args.plan or args.admin_sql is not None)
    log = Log(env["name"], write=writes)
    log(f"environment {env['name']}: database {env['database']}, role {env['role']}, warehouse {env['warehouse']}")
    try:
        con = connect(env)
    except Exception as exc:
        log(f"could not connect: {str(exc).splitlines()[0][:400]}")
        log("Check the [snowflake] connection settings and complete the sign-in in the browser window. Nothing was changed.")
        return 1
    try:
        cur = con.cursor()
        log("1. preflight")
        checks = preflight(cur, env)
        print_checks(log, checks)
        if args.admin_sql is not None:
            target = Path(args.admin_sql) if args.admin_sql else LOG_DIR / f"{env['name']}-admin.sql"
            target.parent.mkdir(exist_ok=True)
            target.write_text(admin_sql(checks, env), encoding="utf-8")
            log(f"admin SQL written to {target}")
            return 0
        stop = blocking(checks)
        if stop:
            log(f"stopped: {len(stop)} required privilege(s) missing. Run with --admin-sql and give the file to your admins.")
            return 2
        if args.check:
            return 0
        log("2. plan")
        steps = plan(cur, env)
        for line in steps:
            log(f"  {line}")
        if any(s.startswith("STOP:") for s in steps):
            log("stopped: an applied migration was changed; add a new migration instead")
            return 3
        if args.plan:
            return 0
        if not args.yes:
            answer = input(f"Deploy to {env['database']} as {env['role']}? Type the database name to continue: ")
            if answer.strip().upper() != env["database"]:
                log("not confirmed: nothing was changed")
                return 1
        log("3. deploy")
        from infrastructure.deploy_snowflake import apply_from_session

        started = time.time()
        for line in apply_from_session(con, env["database"], env["warehouse"], force=args.force):
            log(f"  {line}")
        log(f"  deploy took {time.time() - started:.0f}s")
        log("4. access")
        for line in grant_access(cur, env):
            log(f"  {line}")
        log("5. verify")
        for line in verify(cur, env):
            log(f"  {line}")
        optional = [c for c in checks if c.status == "missing"]
        if optional:
            log(f"note: {len(optional)} optional item(s) still need an admin (features stay off until then): "
                + ", ".join(c.name for c in optional))
        return 0
    except Exception as exc:
        log(f"FAILED: {str(exc).splitlines()[0][:500]}")
        log("The deploy is safe to run again: finished steps are skipped and the failed one is retried.")
        return 1
    finally:
        con.close()
        saved = log.save()
        if saved:
            print(f"log written to {saved}")


if __name__ == "__main__":
    sys.exit(main())
