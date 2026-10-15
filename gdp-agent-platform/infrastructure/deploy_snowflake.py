"""Deploy the platform metadata layer to Snowflake.

    python infrastructure/deploy_snowflake.py --connection <name> [--database AI_PLATFORM] [--dry-run]

Steps (idempotent):
  1. bootstrap   CREATE DATABASE/SCHEMA CORE IF NOT EXISTS + CORE.SCHEMA_MIGRATION + CORE.DEPLOY_STATE
  2. migrations  snowflake/database/migrations/V###__*.sql, each applied once; checksum must not change
  3. seed        workflow graph JSON -> CORE.WORKFLOW_STATE / CORE.WORKFLOW_TRANSITION (MERGE)
  4. package     services/ -> content-addressed zip -> PUT @CORE.CODE_STAGE
  5. procedures  snowflake/procedures/*.sql (CREATE OR REPLACE PROCEDURE + grants)
  6. demo        optional, --demo-source <DB>: snowflake/demo/*.sql (demo CRM source system)

Every platform object lives in <database>.PLATFORM_SCHEMA (services/common/platform_schema.py); SQL templates name it
{{schema}}. The migration ledger (SCHEMA_MIGRATION, DEPLOY_STATE) is pinned to CORE on purpose: it must never follow a
change of the constant, or every migration would look unapplied. V061 (the consolidation into CORE) is gated: it is
applied only with --consolidate after a completed baseline (infrastructure/consolidation/README.md), unless the old
schemas hold no rows at all. --rehearsal (a database whose name ends in _REHEARSAL) never touches objects outside that
database: no Oracle procedure refresh and no publisher task.

Only what changed is applied. CORE.DEPLOY_STATE keeps a checksum of every step's rendered SQL (procedures, views,
Cortex Search, agents, demo files), of the code package, of the seed inputs and of the publisher refreshes; a step
whose checksum matches the last successful apply is skipped. A procedure file changes when its SQL or the code
package changes. When a migration is applied, views, Cortex Search, agents and the seed run again so they pick up the
new tables. --force (or "Re-apply everything" in Admin) applies every step regardless.
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

from infrastructure.seed_knowledge import _stage_dir, list_skills, seed_inputs_digest, seed_platform  # noqa: E402
from services.common.platform_schema import LEGACY_SCHEMAS, PLATFORM_SCHEMA  # noqa: E402
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
LEDGER_SCHEMA = "CORE"          # SCHEMA_MIGRATION and DEPLOY_STATE; never derived from PLATFORM_SCHEMA
CONSOLIDATION_MIGRATION = "061"
REHEARSAL_SUFFIX = "_REHEARSAL"
AGENT_CREATE = re.compile(
    r"CREATE\s+OR\s+REPLACE\s+AGENT\s+(?P<name>[A-Za-z0-9_$.\"]+)\s+(?P<props>.*?)\s*FROM\s+SPECIFICATION\s*"
    r"(?P<spec>\$\$.*?\$\$)\s*;", re.S | re.I)


def agent_skills_yaml(database: str) -> str:
    """Stage skills attached to the supervisor. Each path is the folder that contains SKILL.md."""
    lines = ["skills:"]
    for skill in list_skills():
        path = f"@{database}.{PLATFORM_SCHEMA}.SKILL_STAGE/{skill['folder']}/{_stage_dir(skill)}"
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
CREATE TABLE IF NOT EXISTS {database}.CORE.DEPLOY_STATE (
    OBJECT_KEY   VARCHAR(512) NOT NULL,
    CHECKSUM     VARCHAR(64)  NOT NULL,
    APPLIED_BY   VARCHAR(256) NOT NULL,
    APPLIED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_DEPLOY_STATE PRIMARY KEY (OBJECT_KEY)
);
"""


class DeployState:
    """Checksums of the last successful apply of each deploy step (CORE.DEPLOY_STATE). `changed` says whether a
    step must run; `record` stores its checksum once it succeeded, so a failed step runs again next time."""

    def __init__(self, cur, database: str, force: bool = False):
        self.cur, self.database, self.force = cur, database, force
        self.skipped: Dict[str, int] = {}
        try:
            cur.execute(f"SELECT OBJECT_KEY, CHECKSUM FROM {database}.CORE.DEPLOY_STATE")
            self.applied = dict(cur.fetchall())
        except Exception:
            self.applied = {}

    def changed(self, key: str, digest: str) -> bool:
        if self.force or self.applied.get(key) != digest:
            return True
        group = key.split("/", 1)[0]
        self.skipped[group] = self.skipped.get(group, 0) + 1
        return False

    def record(self, key: str, digest: str) -> None:
        self.cur.execute(
            f"""MERGE INTO {self.database}.CORE.DEPLOY_STATE t
                USING (SELECT %s AS OBJECT_KEY, %s AS CHECKSUM) s ON t.OBJECT_KEY = s.OBJECT_KEY
                WHEN MATCHED THEN UPDATE SET CHECKSUM = s.CHECKSUM, APPLIED_BY = CURRENT_USER(),
                     APPLIED_AT = CURRENT_TIMESTAMP()
                WHEN NOT MATCHED THEN INSERT (OBJECT_KEY, CHECKSUM, APPLIED_BY)
                     VALUES (s.OBJECT_KEY, s.CHECKSUM, CURRENT_USER())""",
            (key, digest),
        )
        self.applied[key] = digest

    def summary(self) -> List[str]:
        return [f"{group}: {n} unchanged (skipped)" for group, n in sorted(self.skipped.items())]


def seed_digest(state_rows, transition_rows) -> str:
    return checksum(repr((state_rows, transition_rows)))


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
        "schema": PLATFORM_SCHEMA,
        "services_import": f"@{database}.{PLATFORM_SCHEMA}.CODE_STAGE/{zip_name}",
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
                                  state_rows, transition_rows, demo_files, force=args.force,
                                  consolidate=args.consolidate, rehearsal=args.rehearsal, through=args.through)
        for line in log:
            print(line)
    finally:
        con.close()


def refresh_github_publisher(cur, database: str, services_import: str, warehouse: str = "",
                             state: "DeployState | None" = None) -> str:
    """Re-point CORE.PUBLISH_DBT_PR at the new code package once GitHub publishing has been set up, and keep the
    task that syncs domain model pull requests (Model Workbench) running. Skipped when neither the package, the
    publisher setup nor the warehouse changed since the last refresh."""
    import json as _json
    from services.dbt.publish import procedure_sql, sync_task_sql

    cur.execute(f"SELECT CONFIG_VALUE FROM {database}.CORE.PLATFORM_CONFIG "
                f"WHERE CONFIG_KEY = 'GITHUB_PUBLISH' AND IS_CURRENT")
    row = cur.fetchone()
    if not row:
        return "github publisher: not set up (skipped)"
    config = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
    digest = checksum(_json.dumps([config, services_import, warehouse], sort_keys=True, default=str))
    if state and not state.changed("github_publisher", digest):
        return "github publisher: unchanged (skipped)"
    try:
        cur.execute(procedure_sql(database, services_import, config["external_access_integration"], config["secret"]))
    except Exception as exc:
        return f"github publisher: skipped ({exc})"
    if not warehouse:
        if state:
            state.record("github_publisher", digest)
        return "github publisher: refreshed"
    try:
        for statement in sync_task_sql(database, warehouse):
            cur.execute(statement)
    except Exception as exc:
        return f"github publisher: refreshed; model pull request sync task skipped ({exc})"
    if state:
        state.record("github_publisher", digest)
    return "github publisher: refreshed; model pull request sync every 30 minutes"


def refresh_oracle_sources(cur, database: str, services_import: str, state: "DeployState | None" = None) -> str:
    """Re-point each Oracle source's procedure (Snowflake runtime, already set up) at the new code package. Skipped
    when neither the package nor the sources changed since the last refresh."""
    import json as _json
    from services.source.oracle.procedures import procedure_sql

    try:
        cur.execute(f"SELECT CONFIGURATION_JSON FROM {database}.CORE.SOURCE_REGISTRY "
                    "WHERE CONNECTION_TYPE = 'oracle' AND ACTIVE_FLAG")
        configs = [_json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in cur.fetchall()]
    except Exception as exc:
        return f"oracle sources: skipped ({exc})"
    digest = checksum(_json.dumps([configs, services_import], sort_keys=True, default=str))
    if state and not state.changed("oracle_sources", digest):
        return "oracle sources: unchanged (skipped)"
    done, failed = 0, []
    for cfg in configs:
        if cfg.get("runtime", "snowflake") != "snowflake" or not cfg.get("oracle_procedure"):
            continue
        try:
            cur.execute(procedure_sql(cfg["database"], cfg["schema"], services_import,
                                      cfg["external_access_integration"]))
            done += 1
        except Exception as exc:
            failed.append(f"{cfg.get('schema')}: {str(exc)[:120]}")
    if state and not failed:
        state.record("oracle_sources", digest)
    return f"oracle sources: {done} procedure(s) refreshed" + (f"; failed {failed}" if failed else "")


def consolidation_gate(cur, database: str, consolidate: bool) -> str:
    """V061 moves every platform object into CORE. Allowed when the old schemas hold no rows (a new environment), or
    with --consolidate after a completed baseline of this database. Raises otherwise, before anything moves."""
    legacy = ", ".join(f"'{s}'" for s in LEGACY_SCHEMAS)
    cur.execute(f"SELECT COALESCE(SUM(ROW_COUNT), 0) FROM {database}.INFORMATION_SCHEMA.TABLES "
                f"WHERE TABLE_SCHEMA IN ({legacy}) AND TABLE_TYPE = 'BASE TABLE'")
    held = int(cur.fetchone()[0] or 0)
    if held == 0:
        return f"V{CONSOLIDATION_MIGRATION}: the old schemas hold no rows (new environment); no baseline needed"
    assert consolidate, (
        f"V{CONSOLIDATION_MIGRATION} moves every platform object into {PLATFORM_SCHEMA} and the old schemas hold {held} "
        "rows. It is applied only by the consolidation runbook (infrastructure/consolidation/README.md): baseline, "
        "then deploy --consolidate. Nothing was moved.")
    cur.execute(f"SELECT COUNT(*) FROM {database}.{LEDGER_SCHEMA}.CONSOLIDATION_RUN "
                "WHERE PHASE = 'BASELINE' AND STATUS = 'COMPLETED' AND DATABASE_NAME = %s", (database,))
    assert int(cur.fetchone()[0] or 0) > 0, (
        f"V{CONSOLIDATION_MIGRATION} needs a completed baseline of {database} "
        "(python -m infrastructure.consolidation.baseline). Nothing was moved.")
    return f"V{CONSOLIDATION_MIGRATION}: completed baseline found; --consolidate given"


def agent_statements(cur, sql: str) -> str:
    """Update an existing agent in place (ALTER ... MODIFY LIVE VERSION SET SPECIFICATION) instead of CREATE OR
    REPLACE, which would start it again at VERSION$1 and unlink its monitoring history. New agents are created."""
    m = AGENT_CREATE.search(sql)
    if not m:
        return sql
    name = m.group("name")
    parts = name.replace('"', "").split(".")
    assert len(parts) == 3, f"agent name must be DATABASE.SCHEMA.NAME: {name}"
    cur.execute(f"SHOW AGENTS LIKE '{parts[2]}' IN SCHEMA {parts[0]}.{parts[1]}")
    if not cur.fetchall():
        return sql
    props = " ".join(m.group("props").split())
    alter = (f"ALTER AGENT {name} SET {props};\n" if props else "") + \
        f"ALTER AGENT {name} MODIFY LIVE VERSION SET SPECIFICATION = {m.group('spec')};"
    return sql[:m.start()] + alter + sql[m.end():]


def apply_to_connection(con, database: str, warehouse: str, variables: Dict[str, str],
                        zip_name: str, zip_bytes: bytes, migrations, procedures, extra_sql,
                        graph_version, state_rows, transition_rows, demo_files, force: bool = False,
                        consolidate: bool = False, rehearsal: bool = False,
                        through: str | None = None) -> List[str]:
    """Apply migrations, seed, package, procedures and extras on an open connection; steps that did not change
    since their last successful apply are skipped unless `force`. `rehearsal` keeps every change inside `database`
    (which must end in _REHEARSAL): no Oracle procedure refresh, no publisher task. `through` applies migrations up to
    and including that version and stops (the consolidation runbook checks the data between V060, V061 and the rest)."""
    log: List[str] = []
    if rehearsal:
        assert database.endswith(REHEARSAL_SUFFIX), f"--rehearsal needs a database named *{REHEARSAL_SUFFIX}"
        log.append(f"rehearsal: changes stay inside {database}")
    cur = con.cursor()
    cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE()")
    user, role, current_wh = cur.fetchone()
    log.append(f"connected as {user} role={role} warehouse={current_wh}")
    if warehouse:
        cur.execute(f"USE WAREHOUSE {warehouse}")
    assert current_wh or warehouse, "no warehouse in session; pass --warehouse"

    con.execute_string(bootstrap_sql(database))
    state = DeployState(cur, database, force)
    if force:
        log.append("force: every step is applied")

    cur.execute(f"SELECT VERSION, CHECKSUM FROM {database}.CORE.SCHEMA_MIGRATION")
    applied = dict(cur.fetchall())
    migrated = False
    for version, name, path in migrations:
        if through and version > through:
            break
        raw = path.read_text(encoding="utf-8")
        digest = checksum(raw)
        if version in applied:
            assert applied[version] == digest, \
                f"V{version} was modified after being applied (checksum mismatch); add a new migration instead"
            state.skipped["migrations"] = state.skipped.get("migrations", 0) + 1
            continue
        if version == CONSOLIDATION_MIGRATION:
            log.append(consolidation_gate(cur, database, consolidate))
        con.execute_string(render(raw, variables))
        cur.execute(
            f"INSERT INTO {database}.CORE.SCHEMA_MIGRATION (VERSION, DESCRIPTION, CHECKSUM, APPLIED_BY) "
            f"SELECT %s, %s, %s, CURRENT_USER()",
            (version, name, digest),
        )
        log.append(f"migration V{version} {name}: applied")
        migrated = True
    if through:
        log.append(f"--through {through}: stopped after the migrations")
        return log
    if migrated:
        log.append("migrations applied: views, Cortex Search, agents and the seed run again")

    graph_digest = seed_digest(state_rows, transition_rows)
    if migrated or state.changed("seed/workflow_graph", graph_digest):
        _seed_graph(cur, database, graph_version, state_rows, transition_rows)
        state.record("seed/workflow_graph", graph_digest)
        log.append(f"seed graph {graph_version}: {len(state_rows)} states, {len(transition_rows)} transitions")

    if state.changed("package/services", zip_name):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / zip_name
            local.write_bytes(zip_bytes)
            cur.execute(
                f"PUT 'file://{local.as_posix()}' @{database}.CORE.CODE_STAGE "
                f"AUTO_COMPRESS = FALSE OVERWRITE = FALSE"
            )
        state.record("package/services", zip_name)
        log.append(f"package {zip_name} uploaded")

    for path in procedures:
        sql = render(path.read_text(encoding="utf-8"), variables)
        key, digest = f"procedures/{path.name}", checksum(sql)
        if not state.changed(key, digest):
            continue
        con.execute_string(sql)
        state.record(key, digest)
        log.append(f"procedures {path.name}: applied")
    log.append(refresh_github_publisher(cur, database, variables["services_import"],
                                        "" if rehearsal else (variables.get("warehouse") or warehouse), state))
    if rehearsal:
        log.append("oracle sources: skipped (rehearsal; their procedures live outside this database)")
    else:
        log.append(refresh_oracle_sources(cur, database, variables["services_import"], state))

    inputs = seed_inputs_digest()
    if migrated or state.changed("seed/platform", inputs):
        log.extend(seed_platform(cur, database) or [])
        state.record("seed/platform", inputs)
        log.append("seed domain packs, skills, platform config")

    for path in extra_sql:
        sql = render(path.read_text(encoding="utf-8"), variables)
        key, digest = f"{path.parent.name}/{path.name}", checksum(sql)
        if not migrated and not state.changed(key, digest):
            continue
        try:
            con.execute_string(agent_statements(cur, sql) if path.parent.name == "agents" else sql)
            state.record(key, digest)
            log.append(f"{path.parent.name} {path.name}: applied")
        except Exception as exc:
            if path.parent.name == "agents":
                raise
            log.append(f"{path.parent.name} {path.name}: skipped ({exc})")

    for path in demo_files:
        sql = render(path.read_text(encoding="utf-8"), variables)
        key, digest = f"demo/{variables['demo_database']}/{path.name}", checksum(sql)
        if not state.changed(key, digest):
            continue
        con.execute_string(sql)
        state.record(key, digest)
        log.append(f"demo {path.name}: applied to {variables['demo_database']}")
    log.extend(state.summary())
    return log


def _seed_graph(cur, database: str, graph_version, state_rows, transition_rows) -> None:
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


def apply_from_session(con, database: str, warehouse: str, demo_source: str | None = None,
                       force: bool = False, consolidate: bool = False, rehearsal: bool = False,
                       through: str | None = None) -> List[str]:
    # The Admin deploy never passes consolidate, so it cannot apply V061 on a database that holds data.
    """Deploy using an already-authenticated Snowflake connection (no new SSO); only changed steps unless `force`."""
    zip_name, zip_bytes = build_services_zip()
    variables = {
        "database": database.upper(),
        "schema": PLATFORM_SCHEMA,
        "services_import": f"@{database.upper()}.{PLATFORM_SCHEMA}.CODE_STAGE/{zip_name}",
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
        graph_version, state_rows, transition_rows, demo_files, force=force,
        consolidate=consolidate, rehearsal=rehearsal, through=through,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--connection", help="name in ~/.snowflake/connections.toml")
    parser.add_argument("--database", default="AI_PLATFORM")
    parser.add_argument("--role")
    parser.add_argument("--warehouse")
    parser.add_argument("--dry-run", action="store_true", help="render SQL without connecting")
    parser.add_argument("--demo-source", help="also create the demo CRM source in this (separate) database")
    parser.add_argument("--force", action="store_true",
                        help="apply every step, even the ones that did not change since the last deploy")
    parser.add_argument("--consolidate", action="store_true",
                        help="allow V061 (move every platform object into CORE) after a completed baseline")
    parser.add_argument("--through", help="apply migrations up to and including this version (e.g. 060), then stop")
    parser.add_argument("--rehearsal", action="store_true",
                        help="the database is a *_REHEARSAL clone; change nothing outside it")
    args = parser.parse_args()
    if not args.dry_run and not args.connection:
        parser.error("--connection is required unless --dry-run")
    deploy(args)


if __name__ == "__main__":
    main()
