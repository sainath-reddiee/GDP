"""Deploy the platform metadata layer to Snowflake.

    python infrastructure/deploy_snowflake.py --connection <name> [--database AI_PLATFORM] [--dry-run]

Steps (idempotent):
  1. bootstrap   CREATE DATABASE/SCHEMA CORE IF NOT EXISTS + CORE.SCHEMA_MIGRATION
  2. migrations  snowflake/database/migrations/V###__*.sql, each applied once; checksum must not change
  3. seed        workflow graph JSON -> CORE.WORKFLOW_STATE / CORE.WORKFLOW_TRANSITION (MERGE)
  4. package     services/ -> content-addressed zip -> PUT @CORE.CODE_STAGE
  5. procedures  snowflake/procedures/*.sql (CREATE OR REPLACE PROCEDURE + grants)
  6. demo        optional, --demo-source <DB>: snowflake/demo/*.sql (demo CRM source system)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from infrastructure.seed_knowledge import list_skills, seed_platform  # noqa: E402
from services.workflow.graph import load_graph  # noqa: E402

MIGRATIONS_DIR = ROOT / "snowflake" / "database" / "migrations"
PROCEDURES_DIR = ROOT / "snowflake" / "procedures"
DEMO_DIR = ROOT / "snowflake" / "demo"
VIEWS_DIR = ROOT / "snowflake" / "views"
SEARCH_DIR = ROOT / "snowflake" / "cortex_search"
AGENTS_DIR = ROOT / "snowflake" / "agents"
GRAPH_FILE = ROOT / "snowflake" / "database" / "seed" / "workflow_graph.json"
SERVICES_DIR = ROOT / "services"

IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]{0,254}$")
MIGRATION_NAME = re.compile(r"^V(\d{3})__([a-z0-9_]+)\.sql$")
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)


def agent_skills_yaml(database: str) -> str:
    """Stage skills attached to the supervisor. Each path is the folder that contains SKILL.md."""
    lines = ["skills:"]
    for skill in list_skills():
        path = f"@{database}.KNOWLEDGE.SKILL_STAGE/{skill['folder']}/{skill['version']}"
        lines.append(f"  - name: {skill['source_name']}")
        lines.append("    source:")
        lines.append("      type: STAGE")
        lines.append(f"      path: \"{path}\"")
    return "\n".join(lines)


def render(sql: str, variables: Dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        assert key in variables, f"unknown placeholder {{{{{key}}}}}"
        return variables[key]

    rendered = PLACEHOLDER.sub(sub, sql)
    assert "{{" not in rendered and "}}" not in rendered, "unrendered placeholder left in SQL"
    return rendered


def checksum(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def list_migrations() -> List[Tuple[str, str, Path]]:
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = MIGRATION_NAME.match(path.name)
        assert m, f"migration file name must match V###__name.sql: {path.name}"
        found.append((m.group(1), m.group(2), path))
    versions = [v for v, _, _ in found]
    assert len(versions) == len(set(versions)), "duplicate migration version"
    return found


def build_services_zip() -> Tuple[str, bytes]:
    """Deterministic zip of services/ (sorted entries, fixed timestamps) named by content hash."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SERVICES_DIR.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes().replace(b"\r\n", b"\n"))
    data = buf.getvalue()
    return f"services-{hashlib.sha256(data).hexdigest()[:16]}.zip", data


def bootstrap_sql(database: str) -> str:
    return f"""
CREATE DATABASE IF NOT EXISTS {database} COMMENT = 'AI-assisted data engineering platform metadata';
CREATE SCHEMA IF NOT EXISTS {database}.CORE COMMENT = 'Workflow state machine, runs, reviews';
CREATE TABLE IF NOT EXISTS {database}.CORE.SCHEMA_MIGRATION (
    VERSION      VARCHAR(8)   NOT NULL,
    DESCRIPTION  VARCHAR(256) NOT NULL,
    CHECKSUM     VARCHAR(64)  NOT NULL,
    APPLIED_BY   VARCHAR(256) NOT NULL,
    APPLIED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_SCHEMA_MIGRATION PRIMARY KEY (VERSION)
);
"""


def seed_rows(database: str):
    graph = load_graph(GRAPH_FILE)
    states = [(s.state, s.stage, s.kind, s.ordinal, s.phase, s.enabled, s.retry_to, graph.version)
              for s in graph.states.values()]
    transitions = [(t.from_state, t.to_state, t.actor, t.guard, t.enabled, graph.version)
                   for t in graph.transitions]
    return graph.version, states, transitions


def merge_statement(table: str, columns: List[str], keys: List[str], n_rows: int) -> str:
    row = "(" + ", ".join(["%s"] * len(columns)) + ")"
    values = ",\n".join([row] * n_rows)
    aliases = ", ".join(f"column{i + 1} AS {c}" for i, c in enumerate(columns))
    on = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    updates = ", ".join(f"{c} = s.{c}" for c in columns if c not in keys)
    inserts = ", ".join(columns)
    insert_values = ", ".join(f"s.{c}" for c in columns)
    return (
        f"MERGE INTO {table} t USING (SELECT {aliases} FROM VALUES\n{values}) s ON {on}\n"
        f"WHEN MATCHED THEN UPDATE SET {updates}, UPDATED_AT = CURRENT_TIMESTAMP()\n"
        f"WHEN NOT MATCHED THEN INSERT ({inserts}) VALUES ({insert_values})"
    )


STATE_COLUMNS = ["STATE", "STAGE", "KIND", "ORDINAL", "PHASE", "ENABLED", "RETRY_TO", "GRAPH_VERSION"]
TRANSITION_COLUMNS = ["FROM_STATE", "TO_STATE", "ACTOR", "GUARD", "ENABLED", "GRAPH_VERSION"]


def deploy(args) -> None:
    database = args.database.upper()
    assert IDENTIFIER.match(database), f"invalid database identifier: {args.database}"
    zip_name, zip_bytes = build_services_zip()
    warehouse = (args.warehouse or "DBT_WH").upper()
    variables = {
        "database": database,
        "services_import": f"@{database}.CORE.CODE_STAGE/{zip_name}",
        "warehouse": warehouse,
        "agent_skills": agent_skills_yaml(database),
    }
    migrations = list_migrations()
    procedures = sorted(PROCEDURES_DIR.glob("*.sql"))
    extra_sql = sorted(VIEWS_DIR.glob("*.sql")) + sorted(SEARCH_DIR.glob("*.sql")) + sorted(AGENTS_DIR.glob("*.sql"))
    graph_version, state_rows, transition_rows = seed_rows(database)
    demo_files = sorted(DEMO_DIR.glob("*.sql")) if args.demo_source else []
    if args.demo_source:
        variables["demo_database"] = args.demo_source.upper()
        assert IDENTIFIER.match(variables["demo_database"]), f"invalid database identifier: {args.demo_source}"
        assert variables["demo_database"] != database, "demo source must be a separate database"

    if args.dry_run:
        print(f"-- bootstrap\n{bootstrap_sql(database)}")
        for version, name, path in migrations:
            print(f"-- migration V{version} {name} checksum={checksum(path.read_text(encoding='utf-8'))}")
            print(render(path.read_text(encoding="utf-8"), variables))
        print(f"-- seed graph {graph_version}: {len(state_rows)} states, {len(transition_rows)} transitions")
        print(f"-- package {zip_name} ({len(zip_bytes)} bytes)")
        for path in procedures:
            print(f"-- procedures {path.name}")
            print(render(path.read_text(encoding="utf-8"), variables))
        for path in extra_sql:
            print(f"-- {path.parent.name} {path.name}")
            print(render(path.read_text(encoding="utf-8"), variables))
        print("-- seed domain pack, skills, platform config")
        for path in demo_files:
            print(f"-- demo {path.name}")
            print(render(path.read_text(encoding="utf-8"), variables))
        return

    import snowflake.connector

    # Same in-memory token cache as apps/api (Windows CredWrite 1783).
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    try:
        import app.db  # noqa: F401
    except Exception:
        pass

    connect_args = {
        "connection_name": args.connection,
        "login_timeout": 300,
        "client_session_keep_alive": True,
        "client_store_temporary_credential": True,
    }
    if args.role:
        connect_args["role"] = args.role
    if args.warehouse:
        connect_args["warehouse"] = args.warehouse
    con = snowflake.connector.connect(**connect_args)
    try:
        log = apply_to_connection(con, database, warehouse, variables, zip_name, zip_bytes,
                                  migrations, procedures, extra_sql, graph_version,
                                  state_rows, transition_rows, demo_files)
        for line in log:
            print(line)
    finally:
        con.close()


def refresh_github_publisher(cur, database: str, services_import: str) -> str:
    """Re-point CODEGEN.PUBLISH_DBT_PR at the new code package once GitHub publishing has been set up."""
    import json as _json
    from services.dbt.publish import procedure_sql

    cur.execute(f"SELECT CONFIG_VALUE FROM {database}.CORE.PLATFORM_CONFIG "
                f"WHERE CONFIG_KEY = 'GITHUB_PUBLISH' AND IS_CURRENT")
    row = cur.fetchone()
    if not row:
        return "github publisher: not set up (skipped)"
    config = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
    try:
        cur.execute(procedure_sql(database, services_import, config["external_access_integration"], config["secret"]))
        return "github publisher: refreshed"
    except Exception as exc:
        return f"github publisher: skipped ({exc})"


def apply_to_connection(con, database: str, warehouse: str, variables: Dict[str, str],
                        zip_name: str, zip_bytes: bytes, migrations, procedures, extra_sql,
                        graph_version, state_rows, transition_rows, demo_files) -> List[str]:
    """Apply migrations, seed, package, procedures and extras on an open connection."""
    log: List[str] = []
    cur = con.cursor()
    cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE()")
    user, role, current_wh = cur.fetchone()
    log.append(f"connected as {user} role={role} warehouse={current_wh}")
    if warehouse:
        cur.execute(f"USE WAREHOUSE {warehouse}")
    assert current_wh or warehouse, "no warehouse in session; pass --warehouse"

    con.execute_string(bootstrap_sql(database))

    cur.execute(f"SELECT VERSION, CHECKSUM FROM {database}.CORE.SCHEMA_MIGRATION")
    applied = dict(cur.fetchall())
    for version, name, path in migrations:
        raw = path.read_text(encoding="utf-8")
        digest = checksum(raw)
        if version in applied:
            assert applied[version] == digest, \
                f"V{version} was modified after being applied (checksum mismatch); add a new migration instead"
            log.append(f"migration V{version} {name}: already applied")
            continue
        con.execute_string(render(raw, variables))
        cur.execute(
            f"INSERT INTO {database}.CORE.SCHEMA_MIGRATION (VERSION, DESCRIPTION, CHECKSUM, APPLIED_BY) "
            f"SELECT %s, %s, %s, CURRENT_USER()",
            (version, name, digest),
        )
        log.append(f"migration V{version} {name}: applied")

    cur.execute(
        merge_statement(f"{database}.CORE.WORKFLOW_STATE", STATE_COLUMNS, ["STATE"], len(state_rows)),
        [v for row in state_rows for v in row],
    )
    cur.execute(
        merge_statement(f"{database}.CORE.WORKFLOW_TRANSITION", TRANSITION_COLUMNS,
                        ["FROM_STATE", "TO_STATE"], len(transition_rows)),
        [v for row in transition_rows for v in row],
    )
    cur.execute(
        f"UPDATE {database}.CORE.WORKFLOW_STATE SET ENABLED = FALSE, GRAPH_VERSION = %s, "
        f"UPDATED_AT = CURRENT_TIMESTAMP() WHERE GRAPH_VERSION <> %s",
        (graph_version, graph_version),
    )
    cur.execute(
        f"UPDATE {database}.CORE.WORKFLOW_TRANSITION SET ENABLED = FALSE, GRAPH_VERSION = %s, "
        f"UPDATED_AT = CURRENT_TIMESTAMP() WHERE GRAPH_VERSION <> %s",
        (graph_version, graph_version),
    )
    log.append(f"seed graph {graph_version}: {len(state_rows)} states, {len(transition_rows)} transitions")

    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / zip_name
        local.write_bytes(zip_bytes)
        cur.execute(
            f"PUT 'file://{local.as_posix()}' @{database}.CORE.CODE_STAGE "
            f"AUTO_COMPRESS = FALSE OVERWRITE = FALSE"
        )
    log.append(f"package {zip_name} uploaded")

    for path in procedures:
        con.execute_string(render(path.read_text(encoding="utf-8"), variables))
        log.append(f"procedures {path.name}: applied")
    log.append(refresh_github_publisher(cur, database, variables["services_import"]))

    seed_platform(cur, database)
    log.append("seed domain pack, skills, platform config")

    for path in extra_sql:
        try:
            con.execute_string(render(path.read_text(encoding="utf-8"), variables))
            log.append(f"{path.parent.name} {path.name}: applied")
        except Exception as exc:
            if path.parent.name == "agents":
                raise
            log.append(f"{path.parent.name} {path.name}: skipped ({exc})")

    for path in demo_files:
        con.execute_string(render(path.read_text(encoding="utf-8"), variables))
        log.append(f"demo {path.name}: applied to {variables['demo_database']}")
    return log


def apply_from_session(con, database: str, warehouse: str, demo_source: str | None = None) -> List[str]:
    """Deploy using an already-authenticated Snowflake connection (no new SSO)."""
    zip_name, zip_bytes = build_services_zip()
    variables = {
        "database": database.upper(),
        "services_import": f"@{database.upper()}.CORE.CODE_STAGE/{zip_name}",
        "warehouse": warehouse.upper(),
        "agent_skills": agent_skills_yaml(database.upper()),
    }
    demo_files = []
    if demo_source:
        variables["demo_database"] = demo_source.upper()
        demo_files = sorted(DEMO_DIR.glob("*.sql"))
    graph_version, state_rows, transition_rows = seed_rows(database)
    return apply_to_connection(
        con, database.upper(), warehouse.upper(), variables, zip_name, zip_bytes,
        list_migrations(), sorted(PROCEDURES_DIR.glob("*.sql")),
        sorted(VIEWS_DIR.glob("*.sql")) + sorted(SEARCH_DIR.glob("*.sql")) + sorted(AGENTS_DIR.glob("*.sql")),
        graph_version, state_rows, transition_rows, demo_files,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--connection", help="name in ~/.snowflake/connections.toml")
    parser.add_argument("--database", default="AI_PLATFORM")
    parser.add_argument("--role")
    parser.add_argument("--warehouse")
    parser.add_argument("--dry-run", action="store_true", help="render SQL without connecting")
    parser.add_argument("--demo-source", help="also create the demo CRM source in this (separate) database")
    args = parser.parse_args()
    if not args.dry_run and not args.connection:
        parser.error("--connection is required unless --dry-run")
    deploy(args)


if __name__ == "__main__":
    main()
