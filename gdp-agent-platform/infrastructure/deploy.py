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

def env_path(name: str) -> Path:
    path = Path(name)
    return path if path.suffix else ENV_DIR / f"{name}.toml"


def load_env(name: str) -> Dict[str, Any]:
    """The environment as a dict, validated. `name` is a file name in infrastructure/environments or a path."""
    import tomllib

    path = env_path(name)
    if not path.exists():
        raise ConfigError(f"no environment file {path}; run with --setup to create it")
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    env = validate_env(raw, path.stem)
    if env["connection"]:
        known = known_connections()
        if known is not None and env["connection"] not in known:
            raise ConfigError(f"connection '{env['connection']}' is not in your Snowflake connections file "
                              f"(known: {', '.join(known) or 'none'}); run with --setup to choose one or enter the account")
    return env


def connections_file() -> Path:
    import os

    home = Path(os.environ.get("SNOWFLAKE_HOME") or Path.home() / ".snowflake")
    return home / "connections.toml"


def known_connections() -> Optional[List[str]]:
    """Connection names in ~/.snowflake/connections.toml, or None when the file cannot be read."""
    import tomllib

    path = connections_file()
    if not path.exists():
        return []
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        return None
    return [k for k, v in data.items() if isinstance(v, dict)]


def _ask(prompt: str, default: str = "", check=None, ask=input) -> str:
    while True:
        value = ask(f"{prompt}{f' [{default}]' if default else ''}: ").strip() or default
        if value and (check is None or check(value)):
            return value
        print("  not valid, try again")


def setup_env(name: str, ask=input) -> Path:
    """Ask for the few settings an environment needs and write infrastructure/environments/<name>.toml."""
    path = env_path(name)
    print(f"Setting up environment '{path.stem}' ({path}). Press Enter to keep a [default].")
    known = known_connections() or []
    lines = ["# Written by infrastructure/deploy.py --setup. Not committed. Never put a password here.", "", "[snowflake]"]
    choice = ""
    if known:
        print("Saved Snowflake connections: " + ", ".join(f"{i + 1}) {k}" for i, k in enumerate(known))
              + f", {len(known) + 1}) enter the account myself")
        choice = _ask("Choose", str(len(known) + 1), lambda v: v.isdigit() and 1 <= int(v) <= len(known) + 1, ask)
    if choice and int(choice) <= len(known):
        lines.append(f'connection = "{known[int(choice) - 1]}"')
    else:
        account = _ask("Account identifier (Snowsight: your name > Account > View account details)", "",
                       lambda v: re.fullmatch(r"[A-Za-z0-9_.\-]{3,255}", v) is not None, ask)
        user = _ask("Your Snowflake user name", "", lambda v: '"' not in v and len(v) < 256, ask)
        auth = _ask("Sign-in: 1) browser SSO  2) key pair", "1", lambda v: v in ("1", "2"), ask)
        lines += [f'account = "{account}"', f'user = "{user}"']
        if auth == "2":
            key = _ask("Path to the private key file (.p8)", "", lambda v: '"' not in v, ask)
            lines += ['authenticator = "snowflake_jwt"', f'private_key_file = "{key.replace(chr(92), "/")}"']
        else:
            lines.append('authenticator = "externalbrowser"')
    from services.common import access as access_model

    ident = lambda v: IDENT.match(v) is not None   # noqa: E731
    model = _ask("Access: 1) account roles with a prefix, no database roles (AODS_*)  2) database roles", "1",
                 lambda v: v in ("1", "2"), ask)
    if model == "1":
        prefix = _ask("Role prefix (AODS gives AODS_DATA_ENGINEER, ...; use AODS_PROD if environments share an account)",
                      access_model.DEFAULT_PREFIX, lambda v: access_model.PREFIX.match(v.upper()) is not None, ask).upper()
        default_role = access_model.role_name(prefix, "PLATFORM_ADMIN")
    role = _ask("Deploy role (an account role you have)", default_role if model == "1" else "PLATFORM_ADMIN", ident,
                ask).upper()
    warehouse = _ask("Warehouse", "AI_PLATFORM_WH", ident, ask).upper()
    database = _ask("Platform database", "AI_PLATFORM", ident, ask).upper()
    lines += [f'role = "{role}"', f'warehouse = "{warehouse}"', f'database = "{database}"', "", "[access]"]
    if model == "1":
        lines += ['model = "account_roles"', f'role_prefix = "{prefix}"']
    else:
        engineers = ask("Account roles of the data engineers, comma separated (Enter to skip for now): ").strip()
        roles = [r.strip().upper() for r in engineers.split(",") if r.strip() and ident(r.strip())]
        lines += ['model = "database_roles"',
                  f"DATA_ENGINEER = [{', '.join(repr(r).replace(chr(39), chr(34)) for r in roles)}]",
                  "VIEWER = []", "OPS_SERVICE = []"]
    lines += ["", "[features]", 'llm_model = "claude-sonnet-4-5"', "pr_sync_task = false",
              "actual_cost_from_account_usage = false", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Written {path}. Edit [access] there any time.")
    return path


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
    from services.common import access as access_model

    access = {}
    section = dict(raw.get("access") or {})
    if "model" not in section:   # never a silent default: the two models create different objects
        problems.append('[access] must say model = "account_roles" (AODS_* account roles, no database roles) or '
                        'model = "database_roles"; run with --setup to rewrite the file')
    kind = str(section.pop("model", access_model.DATABASE_ROLES_MODEL)).lower()
    prefix = str(section.pop("role_prefix", access_model.DEFAULT_PREFIX)).upper()
    if kind not in access_model.MODELS:
        problems.append(f"[access] model must be one of {', '.join(access_model.MODELS)}")
    if not access_model.PREFIX.match(prefix):
        problems.append("[access] role_prefix must be letters, digits or _ (for example AODS or AODS_PROD)")
    if kind == access_model.ACCOUNT_ROLES_MODEL and section:
        problems.append("[access] lists account roles only with model = \"database_roles\"; with account roles, people "
                        f"get the {prefix}_* roles from your role administrator")
        section = {}
    for db_role, roles in section.items():
        if db_role.upper() not in DATABASE_ROLES:
            problems.append(f"[access] {db_role} is not a platform database role ({', '.join(DATABASE_ROLES)})")
            continue
        roles = [roles] if isinstance(roles, str) else list(roles or [])
        bad = [r for r in roles if not IDENT.match(str(r))]
        if bad:
            problems.append(f"[access] {db_role}: {bad[0]} is not a Snowflake identifier")
        access[db_role.upper()] = sorted({str(r).upper() for r in roles})
    role = str(sf.get("role") or "").upper()
    accounts = kind == access_model.ACCOUNT_ROLES_MODEL
    if role and not accounts and role not in access.get("PLATFORM_ADMIN", []):   # the deploy role administers the platform
        access["PLATFORM_ADMIN"] = sorted(set(access.get("PLATFORM_ADMIN", [])) | {role})
    features = dict(raw.get("features") or {})
    integration = str(features.get("github_integration") or "")
    if integration and not IDENT.match(integration):
        problems.append("[features] github_integration must be a Snowflake identifier")
    environment = None
    if raw.get("environment"):   # this deployment's environment bindings (seeded once; Admin, Environments after that)
        from services.common.environments import validate as validate_bindings

        section = dict(raw["environment"])
        environment = {"name": str(section.get("name") or "").upper(), "description": str(section.get("description") or ""),
                       "bindings": dict(section.get("bindings") or {})}
        _, bad = validate_bindings({"current": environment["name"], "environments": [environment]})
        problems.extend(f"[environment] {p}" for p in bad)
    snaplogic = None
    if raw.get("snaplogic"):   # SnapLogic's config tables (seeded once into Admin, SNAPLOGIC_SYNC; granted by their owner)
        from services.source.snaplogic_config import validate_setting

        section = dict(raw["snaplogic"])
        value, bad = validate_setting({"enabled": section.get("enabled") is True,
                                       "table_prefix": section.get("table_prefix") or None})
        problems.extend(f"[snaplogic] {p}" for p in bad)
        writers = section.get("writer_roles")
        writers = [writers] if isinstance(writers, str) else list(writers or [])
        if not writers:   # the roles that onboard sources (SOURCE.CONNECT): engineers and admins
            writers = ([f"{prefix}_DATA_ENGINEER", f"{prefix}_PLATFORM_ADMIN"] if kind == access_model.ACCOUNT_ROLES_MODEL
                       else access.get("DATA_ENGINEER", []) + access.get("PLATFORM_ADMIN", []))
        bad_roles = [r for r in writers if not IDENT.match(str(r))]
        if bad_roles:
            problems.append(f"[snaplogic] writer_roles: {bad_roles[0]} is not a Snowflake identifier")
        snaplogic = {"enabled": value["enabled"], "table_prefix": value["table_prefix"],
                     "writer_roles": sorted({str(r).upper() for r in writers})}
    if problems:
        raise ConfigError("; ".join(problems))
    return {"name": name, "role": role, "warehouse": str(sf["warehouse"]).upper(), "database": str(sf["database"]).upper(),
            "connection": sf.get("connection"), "connect": {k: sf[k] for k in CONNECT_KEYS if sf.get(k)},
            "access": access, "access_model": {"model": kind, "prefix": prefix}, "accounts": accounts,
            "model": str(features.get("llm_model") or "claude-sonnet-4-5"),
            "pr_sync_task": bool(features.get("pr_sync_task")), "github_integration": integration.upper() or None,
            "actual_cost": bool(features.get("actual_cost_from_account_usage")), "environment": environment,
            "snaplogic": snaplogic}


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
                            fix=role_fix(env, str(found.get("u") or "<your_user>"))))
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
            need = {"USAGE", "CREATE SCHEMA"} | (set() if env["accounts"] else {"CREATE DATABASE ROLE"})
            if need <= held:
                checks.append(Check("database", "warn", f"{db} is owned by {owner}; {role} can create its own schemas in it"
                                    + ("" if env["accounts"] else " and database roles")
                                    + ", but objects another role created will not be changeable by the deploy"))
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
    if env["accounts"]:
        checks.extend(account_role_checks(cur, env, grants))

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
    if env.get("snaplogic"):
        checks.extend(snaplogic_checks(cur, env))
    if env["actual_cost"]:
        ok, _ = _try(cur, "SELECT 1 FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_QUERY_USAGE_HISTORY LIMIT 0")
        checks.append(Check("actual cost", "ok", "Cortex usage history is readable (actual credits)", level=OPTIONAL)
                      if ok else Check("actual cost", "missing", "SNOWFLAKE.ACCOUNT_USAGE is not readable; AI cost stays "
                                       "an estimate", level=OPTIONAL, who="ACCOUNTADMIN",
                                       fix=[f"GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER TO ROLE {role};"]))
    return checks


def snaplogic_checks(cur, env: Dict[str, Any]) -> List[Check]:
    """SnapLogic's config tables live outside the platform database, so the roles that onboard sources need their own
    grants there (from the owner of those tables). Optional: without them, onboarding works and the sync stays off."""
    from services.source.snaplogic_config import tables

    snap = env["snaplogic"]
    names = tables(snap)
    database, schema = snap["table_prefix"].split(".")[:2]
    out: List[Check] = []
    for label, table in (("master", names["master"]), ("detail", names["detail"])):
        rows = _safe_rows(cur, f"SHOW GRANTS ON TABLE {table}")
        if not rows:
            out.append(Check(f"snaplogic {label}", "missing", f"{table} does not exist or {env['role']} cannot see its grants",
                             level=OPTIONAL, who=f"the owner of {database}.{schema}", fix=snaplogic_grants(snap, table)))
            continue
        held: Dict[str, set] = {}
        for r in rows:
            held.setdefault(str(r.get("grantee_name") or "").upper(), set()).add(str(r.get("privilege") or "").upper())
        lacking = [w for w in snap["writer_roles"] if not {"SELECT", "INSERT", "UPDATE"} <= held.get(w, set()) | (
            {"SELECT", "INSERT", "UPDATE"} if "OWNERSHIP" in held.get(w, set()) else set())]
        out.append(Check(f"snaplogic {label}", "ok", f"{table}: {', '.join(snap['writer_roles'])} can write it", level=OPTIONAL)
                   if not lacking else Check(
                       f"snaplogic {label}", "missing", f"{table}: {', '.join(lacking)} cannot write it yet",
                       level=OPTIONAL, who=f"the owner of {database}.{schema}", fix=snaplogic_grants({**snap, "writer_roles": lacking}, table)))
    return out


def snaplogic_grants(snap: Dict[str, Any], table: str) -> List[str]:
    database, schema = snap["table_prefix"].split(".")[:2]
    lines = []
    for role in snap["writer_roles"]:
        lines += [f"GRANT USAGE ON DATABASE {database} TO ROLE {role};",
                  f"GRANT USAGE ON SCHEMA {database}.{schema} TO ROLE {role};",
                  f"GRANT SELECT, INSERT, UPDATE ON TABLE {table} TO ROLE {role};"]
    return lines


def role_fix(env: Dict[str, Any], user: str) -> List[str]:
    """What the role administrator runs when the deploy role cannot be used. With account roles the deploy role is
    usually one of the platform's own roles, which may not exist yet: the whole role set comes first (CREATE ROLE IF
    NOT EXISTS is harmless when they do), then the deploy role for this user."""
    lines = []
    if env["accounts"]:
        from services.common import access as access_model

        lines += access_model.setup_sql(env["access_model"]["prefix"], role_hierarchy(), env["role"])
    return lines + [f'GRANT ROLE {env["role"]} TO USER "{user}";']


def role_hierarchy() -> List[Tuple[str, str]]:
    from infrastructure.deploy_snowflake import BASELINE_FILE
    from services.common.access import role_hierarchy as parse

    return parse(BASELINE_FILE.read_text(encoding="utf-8"))


def account_role_checks(cur, env: Dict[str, Any], grants: Dict[str, Any]) -> List[Check]:
    """Account roles: every <PREFIX>_* role must exist before the deploy grants to it (a grant to a missing role fails).
    The role administrator creates them and links them once; the deploy never needs to."""
    from services.common import access as access_model

    prefix, role = env["access_model"]["prefix"], env["role"]
    names = access_model.account_roles(prefix)
    missing = [n for n in names if not _show_one(cur, "ROLES", n)]
    out = [Check("account roles", "ok", f"all {len(names)} {prefix}_* roles exist") if not missing else Check(
        "account roles", "missing", f"{len(missing)} of the {len(names)} {prefix}_* roles do not exist (or are not "
        f"visible): {', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}", who="your role administrator",
        fix=access_model.setup_sql(prefix, role_hierarchy(), role)
        + [f"-- then give people their role, for example: GRANT ROLE {prefix}_DATA_ENGINEER TO USER <user>;"])]
    admin = access_model.role_name(prefix, "PLATFORM_ADMIN")
    if not missing and admin not in grants["roles"]:
        out.append(Check("admin role", "warn", f"{role} does not hold {admin}: it can deploy (it owns everything), but in "
                         f"the app it is not the platform admin", level=OPTIONAL, who="your role administrator",
                         fix=[f"GRANT ROLE {admin} TO ROLE {role};"]))
    wh = _show_one(cur, "WAREHOUSES", env["warehouse"])
    if not (wh and str(wh.get("owner") or "").upper() in grants["roles"]):
        out.append(Check("warehouse for users", "info", f"people with the {prefix}_* roles need USAGE on {env['warehouse']}; "
                         f"{role} does not own it, so its owner grants that", level=OPTIONAL, who="the warehouse owner",
                         fix=[f"GRANT USAGE ON WAREHOUSE {env['warehouse']} TO ROLE {n};" for n in names if n != role]))
    return out


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
             ("-- No database roles are used: people get the " + env["access_model"]["prefix"] + "_* account roles below."
              if env["accounts"] else
              "-- Database roles are NOT created here: the deploy creates them itself, as the owner of the database."),
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
    from services.common import access as access_model

    zip_name, _ = ds.build_services_zip()

    def sql_of(text: str) -> str:   # the same SQL the deploy applies, so unchanged files are not listed
        out = ds.render(text, variables)
        return access_model.to_account_roles(out, db, env["access_model"]["prefix"]) if env["accounts"] else out

    variables = {"database": db, "schema": PLATFORM_SCHEMA, "services_import": f"@{db}.{PLATFORM_SCHEMA}.CODE_STAGE/{zip_name}",
                 "warehouse": wh, "agent_skills": ds.agent_skills_yaml(db)}
    if state.get("package/services") != zip_name:
        out.append(f"code package {zip_name}")
    for path in sorted(ds.PROCEDURES_DIR.glob("*.sql")):
        if state.get(f"procedures/{path.name}") != ds.checksum(sql_of(path.read_text(encoding="utf-8"))):
            out.append(f"procedures {path.name}")
    if state.get("seed/platform") != seed_inputs_digest():
        out.append("knowledge seed (domain packs, skills, platform config)")
    for path in sorted(ds.VIEWS_DIR.glob("*.sql")) + sorted(ds.SEARCH_DIR.glob("*.sql")) + sorted(ds.AGENTS_DIR.glob("*.sql")):
        if state.get(f"{path.parent.name}/{path.name}") != ds.checksum(sql_of(path.read_text(encoding="utf-8"))):
            out.append(f"{path.parent.name} {path.name}")
    return out or ["nothing changed since the last deploy"]


def access_label(env: Dict[str, Any]) -> str:
    if env["accounts"]:
        return f"account roles {env['access_model']['prefix']}_* (no database roles)"
    return f"database roles in {env['database']} (granted to the account roles in [access])"


def render_environment(env: Dict[str, Any]) -> str:
    """Every statement a new environment runs, in deploy order, exactly as this environment's access model applies it
    (account roles: translated to <PREFIX>_*). For review before deploying; needs no connection. An existing
    environment runs only the parts its plan lists."""
    from infrastructure import deploy_snowflake as ds
    from services.common import access as access_model
    from services.common.platform_schema import PLATFORM_SCHEMA

    db, wh, prefix = env["database"], env["warehouse"], env["access_model"]["prefix"]
    zip_name, _ = ds.build_services_zip()
    variables = {"database": db, "schema": PLATFORM_SCHEMA, "services_import": f"@{db}.{PLATFORM_SCHEMA}.CODE_STAGE/{zip_name}",
                 "warehouse": wh, "agent_skills": ds.agent_skills_yaml(db)}

    def sql_of(text: str) -> str:
        out = ds.render(text, variables)
        if not env["accounts"]:
            return out
        out = access_model.to_account_roles(out, db, prefix)
        # comments written for the database-role model would mislead a reviewer of this environment's SQL
        return "\n".join(line for line in out.splitlines()
                         if not (line.lstrip().startswith("--") and re.search(r"(?i)database\s+roles?", line)))

    model = (f"account roles {prefix}_* (no database roles; managed access schemas)" if env["accounts"]
             else "database roles inside the platform database")
    parts = [f"-- Rendered by infrastructure/deploy.py --render for environment '{env['name']}': database {db}, "
             f"deploy role {env['role']}, warehouse {wh}.",
             f"-- Access model: {model}.",
             "-- This is exactly what a new environment runs, in order. Nothing here was run.", "",
             "-- ===== 1. database and migration ledger =====",
             ds.bootstrap_sql(db, create_database=True, managed=env["accounts"]),
             f"-- ===== 2. schema and tables (baseline {ds.BASELINE_FILE.name}) =====",
             sql_of(ds.BASELINE_FILE.read_text(encoding="utf-8"))]
    for version, name, path in ds.list_migrations():
        if version > ds.BASELINE_COVERS:
            parts += [f"-- ===== migration V{version} {name} =====", sql_of(path.read_text(encoding="utf-8"))]
    parts.append(f"-- ===== 3. workflow seed and code package {zip_name} (data and a file upload, not SQL) =====")
    for title, files in (("4. procedures", sorted(ds.PROCEDURES_DIR.glob("*.sql"))),
                         ("5. views", sorted(ds.VIEWS_DIR.glob("*.sql"))),
                         ("6. Cortex Search services", sorted(ds.SEARCH_DIR.glob("*.sql"))),
                         ("7. agents", sorted(ds.AGENTS_DIR.glob("*.sql")))):
        for path in files:
            parts += [f"-- ===== {title}: {path.name} =====", sql_of(path.read_text(encoding="utf-8"))]
    access = access_statements(env, owns_warehouse=False)
    parts += ["-- ===== 8. access =====", *[f"{s};" for s in access]]
    if not env["accounts"]:
        parts.append("-- (database roles are granted to the account roles listed in [access])")
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------- access and verify

def access_statements(env: Dict[str, Any], owns_warehouse: bool) -> List[str]:
    from services.common import access as access_model

    db, out = env["database"], []
    if env["accounts"]:
        prefix = env["access_model"]["prefix"]
        admin = access_model.role_name(prefix, "PLATFORM_ADMIN")
        # the owner already has everything: future grants to itself are skipped
        out += [g for g in access_model.future_grants(db, prefix) if not (env["role"] == admin and g.endswith(f"TO ROLE {admin}"))]
        if owns_warehouse:
            out += [f"GRANT USAGE ON WAREHOUSE {env['warehouse']} TO ROLE {r}" for r in access_model.account_roles(prefix)
                    if r != env["role"]]
        return out
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
    if env["accounts"]:
        from services.common import access as access_model

        names = access_model.account_roles(env["access_model"]["prefix"])
        missing = [n for n in names if not _show_one(cur, "ROLES", n)]
        out.append(f"verify: all {len(names)} account roles exist" if not missing else f"verify: account roles missing: {missing}")
        core = next((r for r in _safe_rows(cur, f"SHOW SCHEMAS LIKE 'CORE' IN DATABASE {db}")
                     if str(r.get("name")).upper() == "CORE"), None)
        managed = "MANAGED ACCESS" in str((core or {}).get("options") or "").upper()
        out.append("verify: CORE is a managed access schema" if managed else "verify: CORE is NOT managed access  <-- check")
        roles = _safe_rows(cur, f"SHOW DATABASE ROLES IN DATABASE {db}")
        out.append("verify: no database roles in the database" if not roles
                   else f"verify: {len(roles)} database role(s) exist from an earlier model (unused)")
        return out
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


class TimedConnection:
    """The deploy's connection with every statement timed. A statement over `slow` seconds is reported when it ends,
    and while one runs past a minute a heartbeat names it, so a long deploy says what it is waiting on instead of
    going quiet until the step ends."""

    def __init__(self, con, echo, slow: float = 10.0, beat: float = 60.0):
        import threading

        self.con, self.echo, self.slow, self.beat = con, echo, slow, beat
        self.running: Optional[Tuple[str, float]] = None
        self.statements, self.slowest = 0, (0.0, "")
        self._stop = threading.Event()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    @staticmethod
    def head(sql: str) -> str:
        return " ".join(str(sql).split())[:110]

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.beat):
            if self.running and time.time() - self.running[1] >= self.beat:
                self.echo(f"  ... still running after {time.time() - self.running[1]:.0f}s: {self.running[0]}")

    def timed(self, sql: str, run):
        self.running, started = (self.head(sql), time.time()), time.time()
        try:
            return run()
        finally:
            took = time.time() - started
            self.running, self.statements = None, self.statements + 1
            if took > self.slowest[0]:
                self.slowest = (took, self.head(sql))
            if took >= self.slow:
                self.echo(f"  ... {took:.0f}s: {self.head(sql)}")

    def cursor(self, *a, **kw):
        return TimedCursor(self.con.cursor(*a, **kw), self)

    def execute_string(self, sql_text: str, remove_comments: bool = False, **kw):
        """The connector's execute_string, one statement at a time so each is timed."""
        import io
        from snowflake.connector.util_text import split_statements

        out = []
        for statement, _ in split_statements(io.StringIO(sql_text), remove_comments=remove_comments):
            cur = self.cursor()
            cur.execute(statement, **kw)
            out.append(cur)
        return out

    def close(self) -> None:
        self._stop.set()
        self.con.close()

    def __getattr__(self, name):
        return getattr(self.con, name)


class TimedCursor:
    def __init__(self, cur, timer: TimedConnection):
        self._cur, self._timer = cur, timer

    def execute(self, sql, *a, **kw):
        self._timer.timed(sql, lambda: self._cur.execute(sql, *a, **kw))
        return self

    def __iter__(self):
        return iter(self._cur)

    def __getattr__(self, name):
        return getattr(self._cur, name)


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
    mode.add_argument("--render", nargs="?", const="", metavar="FILE",
                      help="write the exact SQL a new environment runs, translated for its access model, to review "
                           "(no connection; default deploy-logs/<env>-render.sql)")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--force", action="store_true", help="apply every step, even unchanged ones")
    parser.add_argument("--setup", action="store_true", help="create or redo the environment file by answering questions")
    args = parser.parse_args(argv)
    interactive = sys.stdin.isatty()
    if args.setup or (not env_path(args.env).exists() and interactive):
        setup_env(args.env)
        if args.setup and not (args.check or args.plan or args.admin_sql is not None):
            print(f"Next: python infrastructure/deploy.py --env {args.env} --check")
            return 0
    try:
        env = load_env(args.env)
    except ConfigError as exc:
        parser.error(str(exc))
    if args.render is not None:
        target = Path(args.render) if args.render else LOG_DIR / f"{env['name']}-render.sql"
        target.parent.mkdir(exist_ok=True)
        text = render_environment(env)
        target.write_text(text, encoding="utf-8")
        print(f"rendered SQL for {env['name']} written to {target} ({text.count(chr(10)):,} lines); nothing was run")
        return 0
    writes = not (args.check or args.plan or args.admin_sql is not None)
    log = Log(env["name"], write=writes)
    log(f"environment {env['name']}: database {env['database']}, role {env['role']}, warehouse {env['warehouse']}")
    log(f"access model: {access_label(env)}")
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
            answer = input(f"Deploy to {env['database']} as {env['role']} with {access_label(env)}? "
                           "Type the database name to continue: ")
            if answer.strip().upper() != env["database"]:
                log("not confirmed: nothing was changed")
                return 1
        log("3. deploy")
        from infrastructure.deploy_snowflake import apply_from_session

        started = time.time()
        timed = TimedConnection(con, log)
        for line in apply_from_session(timed, env["database"], env["warehouse"], force=args.force,
                                       access=env["access_model"],
                                       **({"environment": {**(env.get("environment") or {}), "snaplogic": env.get("snaplogic")}}
                                          if env.get("environment") or env.get("snaplogic") else {})):
            log(f"  {line}")
        timed._stop.set()
        log(f"  deploy took {time.time() - started:.0f}s ({timed.statements} statements; slowest "
            f"{timed.slowest[0]:.0f}s: {timed.slowest[1] or 'none'})")
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
