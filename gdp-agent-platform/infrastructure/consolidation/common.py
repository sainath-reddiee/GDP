"""Shared helpers for the consolidation runbook (freeze, baseline, runtime, verify, rollback).

Every script takes --connection (a name in ~/.snowflake/connections.toml) and --database, never a credential.
Results are written to CORE.CONSOLIDATION_RUN / CONSOLIDATION_OBJECT / CONSOLIDATION_CHECK (V060) and, as JSON, to
--out (default consolidation_runs/ next to the repository, git-ignored).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.common.platform_schema import (LEGACY_SCHEMAS, MOVED, PLATFORM_SCHEMA, RENAME,  # noqa: E402,F401
                                             RUNTIME_PREFIXES, moved)

IDENT = re.compile(r"^[A-Z][A-Z0-9_$]{0,254}$")
LEDGER = "CORE"
REHEARSAL_SUFFIX = "_REHEARSAL"
# Object kinds listed with SHOW ... IN DATABASE; anything in a legacy schema that is not in MOVED (or a known runtime
# prefix) is reported as unknown and stops the baseline.
SHOW_KINDS = ("TABLES", "VIEWS", "STAGES", "FILE FORMATS", "SEQUENCES", "PROCEDURES", "USER FUNCTIONS", "TASKS",
              "SECRETS", "NETWORK RULES", "GIT REPOSITORIES", "DBT PROJECTS", "CORTEX SEARCH SERVICES", "AGENTS",
              "STREAMLITS", "NOTEBOOKS", "STREAMS", "PIPES", "ALERTS", "ROW ACCESS POLICIES", "MASKING POLICIES",
              "TAGS", "EXTERNAL TABLES", "DYNAMIC TABLES", "EVENT TABLES", "SEMANTIC VIEWS")


def parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--connection", required=True, help="name in ~/.snowflake/connections.toml")
    p.add_argument("--database", required=True)
    p.add_argument("--role")
    p.add_argument("--warehouse")
    p.add_argument("--out", default=str(ROOT / "consolidation_runs"))
    return p


def connect(args):
    import snowflake.connector

    sys.path.insert(0, str(ROOT / "apps" / "api"))
    try:
        import app.db  # noqa: F401  (same in-memory token cache as the API and the deploy script)
    except Exception:
        pass
    kwargs = {"connection_name": args.connection, "login_timeout": 300, "client_session_keep_alive": True,
              "client_store_temporary_credential": True}
    if args.role:
        kwargs["role"] = args.role
    if args.warehouse:
        kwargs["warehouse"] = args.warehouse
    return snowflake.connector.connect(**kwargs)


def database_name(args) -> str:
    db = args.database.upper()
    assert IDENT.match(db), f"invalid database identifier: {args.database}"
    return db


def q(cur, sql: str, params: Optional[Iterable[Any]] = None) -> List[Dict[str, Any]]:
    """Rows as dicts with lower-case keys."""
    cur.execute(sql, tuple(params) if params is not None else None)
    if not cur.description:
        return []
    cols = [c[0].lower() for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def try_q(cur, sql: str, params=None) -> List[Dict[str, Any]]:
    try:
        return q(cur, sql, params)
    except Exception:
        return []


def quote(name: str) -> str:
    return name if IDENT.match(name) else '"' + name.replace('"', '""') + '"'


def fq(db: str, schema: str, name: str) -> str:
    return f"{db}.{quote(schema)}.{quote(name)}"


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def start_run(cur, db: str, phase: str, rehearsal: bool, details: Optional[dict] = None) -> str:
    run_id = str(uuid.uuid4())
    cur.execute(f"INSERT INTO {db}.{LEDGER}.CONSOLIDATION_RUN (RUN_ID, DATABASE_NAME, PHASE, STATUS, REHEARSAL, DETAILS) "
                "SELECT %s, %s, %s, 'RUNNING', %s, PARSE_JSON(%s)",
                (run_id, db, phase, rehearsal, json.dumps(jsonable(details or {}))))
    return run_id


def finish_run(cur, db: str, run_id: str, ok: bool, details: Optional[dict] = None) -> None:
    cur.execute(f"UPDATE {db}.{LEDGER}.CONSOLIDATION_RUN SET STATUS = %s, FINISHED_AT = CURRENT_TIMESTAMP(), "
                "DETAILS = PARSE_JSON(%s) WHERE RUN_ID = %s",
                ("COMPLETED" if ok else "FAILED", json.dumps(jsonable(details or {})), run_id))


def latest_run(cur, db: str, phase: str) -> Optional[Dict[str, Any]]:
    found = q(cur, f"SELECT RUN_ID, DETAILS, STARTED_AT, REHEARSAL FROM {db}.{LEDGER}.CONSOLIDATION_RUN "
                   "WHERE PHASE = %s AND STATUS = 'COMPLETED' AND DATABASE_NAME = %s ORDER BY STARTED_AT DESC LIMIT 1",
              (phase, db))
    if not found:
        return None
    row = found[0]
    row["details"] = json.loads(row["details"]) if isinstance(row["details"], str) else row["details"]
    return row


def baseline_objects(cur, db: str, run_id: str) -> List[Dict[str, Any]]:
    out = q(cur, f"SELECT OBJECT_TYPE, OLD_FQN, NEW_FQN, METHOD, BASELINE, AFTER, STATUS "
                 f"FROM {db}.{LEDGER}.CONSOLIDATION_OBJECT WHERE RUN_ID = %s ORDER BY OBJECT_TYPE, OLD_FQN", (run_id,))
    for row in out:
        for k in ("baseline", "after"):
            if isinstance(row.get(k), str):
                row[k] = json.loads(row[k])
    return out


def write_json(args, name: str, payload: Any) -> Path:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"{args.database.upper()}_{name}_{stamp}.json"
    path.write_text(json.dumps(jsonable(payload), indent=1, sort_keys=True), encoding="utf-8")
    return path


def show(cur, kind: str, db: str) -> List[Dict[str, Any]]:
    """SHOW <kind> IN DATABASE, empty when the kind is not available in this account."""
    return try_q(cur, f"SHOW {kind} IN DATABASE {db}")


def grants_on(cur, object_type: str, name: str) -> List[Dict[str, str]]:
    rows_ = try_q(cur, f"SHOW GRANTS ON {object_type} {name}")
    return sorted(({"privilege": r.get("privilege"), "granted_to": r.get("granted_to"),
                    "grantee": r.get("grantee_name"), "grant_option": str(r.get("grant_option"))}
                   for r in rows_), key=lambda g: (g["privilege"], g["granted_to"], g["grantee"]))


def map_fqn(text: str, db: str) -> str:
    """Rewrite every legacy reference in a SQL text to PLATFORM_SCHEMA (used for task bodies and comparisons)."""
    from infrastructure.consolidation.codemod import rewrite

    return rewrite(text, template=False)
