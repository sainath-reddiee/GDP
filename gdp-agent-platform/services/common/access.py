"""Who may do what in Snowflake: database roles (the default) or account roles with a prefix (AODS_* by default).

  database_roles  the platform creates ten database roles inside its database (<db>.DATA_ENGINEER, ...) and grants
                  them to account roles. Needs nobody but the database owner.
  account_roles   no database roles at all. A role administrator creates ten account roles <PREFIX>_DATA_ENGINEER, ...
                  and links them (role_hierarchy). The deploy grants every privilege straight to those account roles:
                  the owner of an object may grant it to any role. Platform schemas are managed access, so the
                  schema owner (the deploy role) can also define future grants without MANAGE GRANTS.

The SQL in the repository is written for database roles. For account_roles the deploy translates it as it renders
(to_account_roles): the migration files and their checksums never change, so one environment can use either model.
The model in force is kept in PLATFORM_CONFIG (key ACCESS_MODEL) so code that checks for the admin role at run time
(row policies, services.governance.domains, copilot) follows it.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

KEY = "ACCESS_MODEL"
DATABASE_ROLES_MODEL, ACCOUNT_ROLES_MODEL = "database_roles", "account_roles"
MODELS = (DATABASE_ROLES_MODEL, ACCOUNT_ROLES_MODEL)
DEFAULT_PREFIX = "AODS"
ROLES = ("PLATFORM_ADMIN", "DATA_ENGINEER", "DATA_STEWARD", "DOMAIN_OWNER", "QA_ENGINEER", "REVIEWER", "VIEWER",
         "OPS_SERVICE", "OPS_VIEWER", "SERVICE_AGENT")
PREFIX = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")
# schemas the platform creates; managed access in account_roles (future grants by the schema owner)
PLATFORM_SCHEMAS = ("CORE", "LANDING", "GDP_SILVER")
# future grants in account_roles (narrow on purpose: tables are granted one by one, some hold secrets)
FUTURE_ADMIN = ("TABLES", "VIEWS", "PROCEDURES", "FUNCTIONS", "STAGES", "SEQUENCES", "FILE FORMATS")
FUTURE_READ_VIEWS_TO = "VIEWER"


def normalise(model: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """{model, prefix} with defaults; anything unknown falls back to database roles."""
    model = model if isinstance(model, dict) else {}
    kind = str(model.get("model") or DATABASE_ROLES_MODEL).lower()
    prefix = str(model.get("prefix") or DEFAULT_PREFIX).upper()
    if kind not in MODELS or not PREFIX.match(prefix):
        return {"model": DATABASE_ROLES_MODEL, "prefix": DEFAULT_PREFIX}
    return {"model": kind, "prefix": prefix}


def role_name(prefix: str, role: str) -> str:
    return f"{prefix.upper()}_{role.upper()}"


def account_roles(prefix: str) -> List[str]:
    return [role_name(prefix, r) for r in ROLES]


# ---------------------------------------------------------------- the admin check at run time

def admin_check_sql(model: Optional[Dict[str, Any]]) -> str:
    """The SQL expression that is TRUE when the session holds the platform admin role."""
    m = normalise(model)
    if m["model"] == ACCOUNT_ROLES_MODEL:
        return f"IS_ROLE_IN_SESSION('{role_name(m['prefix'], 'PLATFORM_ADMIN')}')"
    return "IS_DATABASE_ROLE_IN_SESSION('PLATFORM_ADMIN')"


_LOADED: Dict[str, Dict[str, str]] = {}


def load(query: Callable[[str], List[Dict[str, Any]]], cache_key: str = "") -> Dict[str, str]:
    """The model in force, read once per process (and cache key) from PLATFORM_CONFIG; database roles when unset."""
    if cache_key in _LOADED:
        return _LOADED[cache_key]
    import json

    try:
        found = query("SELECT CONFIG_VALUE AS V FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'ACCESS_MODEL' AND IS_CURRENT "
                      "ORDER BY VERSION DESC LIMIT 1")
        value = (found[0].get("v", found[0].get("V")) if found else None)   # Db rows are lower case, Snowpark upper
        value = json.loads(value) if isinstance(value, str) else value
    except Exception:
        value = None
    _LOADED[cache_key] = normalise(value)
    return _LOADED[cache_key]


# ---------------------------------------------------------------- translating the repository's SQL (deploy)

def _db(database: str) -> str:
    return re.escape(database.upper())


def to_account_roles(sql: str, database: str, prefix: str) -> str:
    """Rendered SQL written for database roles, rewritten for account roles <prefix>_*: no CREATE DATABASE ROLE, no
    role-to-role grants (the role administrator links the account roles), every grant to <prefix>_<ROLE>, the admin
    check on the account role, and platform schemas created with managed access."""
    db, p = _db(database), prefix.upper()
    flags = re.I
    sql = re.sub(rf"CREATE\s+DATABASE\s+ROLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{db}\.\w+[^;]*;", "", sql, flags=flags)
    sql = re.sub(rf"(?:GRANT|REVOKE)\s+DATABASE\s+ROLE\s+{db}\.\w+\s+(?:TO|FROM)\s+(?:DATABASE\s+)?ROLE\s+[\w.\"]+\s*;", "",
                 sql, flags=flags)
    sql = re.sub(rf"\b(TO|FROM)\s+DATABASE\s+ROLE\s+{db}\.(\w+)", lambda m: f"{m.group(1)} ROLE {p}_{m.group(2).upper()}",
                 sql, flags=flags)
    sql = re.sub(r"IS_DATABASE_ROLE_IN_SESSION\(\s*'(\w+)'\s*\)",
                 lambda m: f"IS_ROLE_IN_SESSION('{p}_{m.group(1).upper()}')", sql, flags=flags)
    schemas = "|".join(PLATFORM_SCHEMAS)
    sql = re.sub(rf"(CREATE\s+(?:OR\s+REPLACE\s+)?(?:TRANSIENT\s+)?SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?{db}\.(?:{schemas}))\b"
                 r"(?!\s+WITH\s+MANAGED)", r"\1 WITH MANAGED ACCESS", sql, flags=flags)
    code = re.sub(r"'(?:[^']|'')*'", "''", re.sub(r"--[^\n]*", "", sql))   # comments and text do not count
    left = re.search(r"DATABASE\s+ROLE", code, flags=flags)
    if left:
        start = max(0, left.start() - 80)
        raise ValueError(f"a database role is still named after translation: ...{code[start:left.end() + 80]}...")
    return sql


def role_hierarchy(sql_template: str) -> List[Tuple[str, str]]:
    """[(granted role, grantee role)] from the repository's GRANT DATABASE ROLE a TO DATABASE ROLE b lines."""
    pairs = re.findall(r"GRANT\s+DATABASE\s+ROLE\s+\{\{database\}\}\.(\w+)\s+TO\s+DATABASE\s+ROLE\s+\{\{database\}\}\.(\w+)",
                       sql_template, flags=re.I)
    return sorted({(a.upper(), b.upper()) for a, b in pairs})


def setup_sql(prefix: str, hierarchy: List[Tuple[str, str]], deploy_role: str) -> List[str]:
    """What the role administrator runs once: the account roles, their links, and the admin role for the deployer."""
    out = [f"CREATE ROLE IF NOT EXISTS {r};" for r in account_roles(prefix)]
    out += [f"GRANT ROLE {role_name(prefix, a)} TO ROLE {role_name(prefix, b)};" for a, b in hierarchy]
    admin = role_name(prefix, "PLATFORM_ADMIN")
    if deploy_role.upper() not in (admin, "SYSADMIN"):
        out.append(f"GRANT ROLE {admin} TO ROLE {deploy_role.upper()};")
    out.append(f"GRANT ROLE {admin} TO ROLE SYSADMIN;   -- recommended: custom roles roll up to SYSADMIN"
               + (" (also makes it the deploy role's)" if deploy_role.upper() == "SYSADMIN" else ""))
    return out


def future_grants(database: str, prefix: str) -> List[str]:
    """Schema-level future grants (managed access schemas, so the schema owner may define them): everything for the
    admin role, SELECT on future views for readers. Tables stay granted one by one."""
    db, admin, viewer = database.upper(), role_name(prefix, "PLATFORM_ADMIN"), role_name(prefix, FUTURE_READ_VIEWS_TO)
    out = []
    for schema in PLATFORM_SCHEMAS:
        for kind in FUTURE_ADMIN:
            out.append(f"GRANT ALL PRIVILEGES ON FUTURE {kind} IN SCHEMA {db}.{schema} TO ROLE {admin}")
    out.append(f"GRANT SELECT ON FUTURE VIEWS IN SCHEMA {db}.CORE TO ROLE {viewer}")
    return out
