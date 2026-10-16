"""The safe-action catalogue: the only things an agent-proposed fix may do.

Each action is a generic, reviewed fix procedure (CORE.FIX_<ACTION>(FAILURE_ID, INPUT)) with a strict input shape. An
action may only touch objects inside the fix scope (CONTROL_PREFERENCE.AGENT_FIX_SCOPE: comma-separated DB.SCHEMA.NAME
patterns with * wildcards), never anything else, and every input is checked against Snowflake twice: when the agent
proposes it (validate_plan) and again when it runs (the procedure handlers below). There is no free SQL anywhere.
"""

from __future__ import annotations

import fnmatch
import json
import re
from typing import Any, Dict, List, Optional, Tuple

_IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
_FQN = re.compile(rf"^({_IDENT})\.({_IDENT})\.({_IDENT})$")
_COLUMN = re.compile(rf"^{_IDENT}$")
_TYPE = re.compile(r"^(NUMBER(\(\d{1,2}(,\s*\d{1,2})?\))?|DECIMAL(\(\d{1,2}(,\s*\d{1,2})?\))?|INTEGER|INT|BIGINT|FLOAT|DOUBLE|"
                   r"BOOLEAN|DATE|TIMESTAMP_(NTZ|LTZ|TZ)(\(\d\))?|TIME|VARCHAR(\(\d{1,8}\))?|STRING|TEXT|VARIANT)$", re.I)

ACTIONS: Dict[str, Dict[str, Any]] = {
    "ADD_COLUMN": {
        "procedure": "CORE.FIX_ADD_COLUMN", "risk": "schema",
        "doc": "Add a missing column to a table, optionally filled from another column of the same table",
        "input": {"table": "DB.SCHEMA.TABLE", "column": "NAME", "data_type": "NUMBER(12,2) | VARCHAR(100) | ...",
                  "fill_from": "an existing column of the same table (optional)"},
    },
    "CREATE_TABLE_FROM": {
        "procedure": "CORE.FIX_CREATE_TABLE_FROM", "risk": "schema",
        "doc": "Create a missing table as a copy of an existing table (CREATE TABLE ... AS SELECT)",
        "input": {"table": "DB.SCHEMA.MISSING_TABLE", "source_table": "DB.SCHEMA.EXISTING_TABLE"},
    },
    "CREATE_STAGE": {
        "procedure": "CORE.FIX_CREATE_STAGE", "risk": "schema",
        "doc": "Create a missing internal stage",
        "input": {"stage": "DB.SCHEMA.STAGE"},
    },
    "DEDUP_ON_KEY": {
        "procedure": "CORE.FIX_DEDUP_ON_KEY", "risk": "data",
        "doc": "Keep one row per key in a table (removes duplicate rows; changes data). With keep_latest_by, the row with "
               "the latest value of that column is kept; otherwise the choice is repeatable (ordered by every column)",
        "input": {"table": "DB.SCHEMA.TABLE", "key_columns": ["COLUMN", "..."],
                  "keep_latest_by": "a timestamp or sequence column of the table (optional)"},
    },
    "RERUN_ONLY": {
        "procedure": "CORE.FIX_RERUN_ONLY", "risk": "none",
        "doc": "Change nothing: the failure was transient, clearing the task to run again is the fix",
        "input": {},
    },
}


def catalogue_text() -> str:
    return "\n".join(f"- {name}: {a['doc']}. input {json.dumps(a['input'])}" for name, a in ACTIONS.items())


# ---------------------------------------------------------------- scope and names

def scope_patterns(scope: Optional[str]) -> List[str]:
    return [p.strip().upper() for p in str(scope or "").split(",") if p.strip()]


def in_scope(fqn: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatchcase(fqn.upper(), p) for p in patterns)


def qualify(name: Any, database: str, default_schema: Optional[str] = None) -> str:
    """DB.SCHEMA.NAME (upper case) from a 2- or 3-part name; ValueError for anything else (quotes, spaces, paths)."""
    text = str(name or "").strip().strip('"').upper()
    parts = text.split(".")
    if len(parts) == 2:
        parts = [database.upper()] + parts
    elif len(parts) == 1 and default_schema:
        parts = [database.upper(), default_schema.upper(), parts[0]]
    fqn = ".".join(parts)
    if not _FQN.match(fqn):
        raise ValueError(f"not a plain object name: {name!r}")
    return fqn


def _scoped(name: Any, database: str, patterns: List[str]) -> str:
    fqn = qualify(name, database)
    if not in_scope(fqn, patterns):
        raise ValueError(f"{fqn} is outside the agent fix scope")
    d, s, t = fqn.split(".")
    if d == database.upper() and s == "CORE" and not t.startswith("LAB_"):
        raise ValueError(f"{fqn} is a platform object: an agent never changes the platform's own tables")
    return fqn


# ---------------------------------------------------------------- what exists in Snowflake

def columns_of(db: Any, fqn: str) -> Optional[Dict[str, str]]:
    """{COLUMN: DATA_TYPE} of a table, or None when it does not exist."""
    d, s, t = fqn.split(".")
    try:
        rows = db.query(f"SELECT COLUMN_NAME, DATA_TYPE FROM {d}.INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION", (s, t))
    except Exception:
        return None
    return {str(r["column_name"]).upper(): str(r["data_type"]) for r in rows} or None


def table_comment(db: Any, fqn: str) -> Optional[str]:
    """The table's own documentation (its COMMENT), which often says what a missing column should hold."""
    d, s, t = fqn.split(".")
    try:
        found = db.query(f"SELECT COMMENT FROM {d}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (s, t))
    except Exception:
        return None
    return (str(found[0].get("comment") or "").strip() or None) if found else None


def stage_exists(db: Any, fqn: str) -> bool:
    d, s, t = fqn.split(".")
    try:
        return bool(db.query(f"SELECT 1 AS X FROM {d}.INFORMATION_SCHEMA.STAGES WHERE STAGE_SCHEMA = %s AND STAGE_NAME = %s",
                             (s, t)))
    except Exception:
        return False


def tables_in_scope(db: Any, patterns: List[str], database: str, limit: int = 40) -> List[str]:
    """Existing tables the scope covers (context for the proposer: a missing table is often a copy of one of these)."""
    out: List[str] = []
    schemas = sorted({p.split(".")[1] for p in patterns if p.count(".") == 2 and p.split(".")[0] == database.upper()
                      and "*" not in p.split(".")[1]})
    for schema in schemas:
        try:
            rows = db.query(f"SELECT TABLE_NAME FROM {database}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s "
                            "AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME", (schema,))
        except Exception:
            continue
        for r in rows:
            fqn = f"{database.upper()}.{schema}.{str(r['table_name']).upper()}"
            if in_scope(fqn, patterns):
                out.append(fqn)
    return out[:limit]


# ---------------------------------------------------------------- validation (proposal time and run time)

def check_step(db: Any, action: str, given: Dict[str, Any], database: str, patterns: List[str],
               running: bool = False) -> Dict[str, Any]:
    """The step's normalised input, or ValueError: unknown action, a name outside the scope, or a state in Snowflake
    that does not need this action (the column already exists, the table is not missing, ...). `running` (the fix
    procedure, not the proposal): an ADD_COLUMN whose column was added by an earlier attempt that failed while filling
    it may finish the fill instead of being refused."""
    action = str(action or "").upper()
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action}; allowed: {', '.join(ACTIONS)}")
    given = given if isinstance(given, dict) else {}
    if action == "ADD_COLUMN":
        table = _scoped(given.get("table"), database, patterns)
        column = str(given.get("column") or "").strip().upper()
        data_type = str(given.get("data_type") or "").strip().upper()
        if not _COLUMN.match(column):
            raise ValueError(f"not a column name: {given.get('column')!r}")
        if not _TYPE.match(data_type):
            raise ValueError(f"data type {given.get('data_type')!r} is not allowed")
        cols = columns_of(db, table)
        if cols is None:
            raise ValueError(f"{table} does not exist")
        fill = str(given.get("fill_from") or "").strip().upper() or None
        if fill and (fill not in cols or not _COLUMN.match(fill)):
            raise ValueError(f"{table}.{fill} does not exist (fill_from)")
        if column in cols:
            if running and fill and _unfilled(db, table, column, fill):
                return {"table": table, "column": column, "data_type": data_type, "fill_from": fill, "exists": True}
            raise ValueError(f"{table}.{column} already exists")
        return {"table": table, "column": column, "data_type": data_type, "fill_from": fill}
    if action == "CREATE_TABLE_FROM":
        table = _scoped(given.get("table"), database, patterns)
        source = _scoped(given.get("source_table"), database, patterns)
        if columns_of(db, table) is not None:
            raise ValueError(f"{table} already exists")
        if columns_of(db, source) is None:
            raise ValueError(f"{source} does not exist (source_table)")
        return {"table": table, "source_table": source}
    if action == "CREATE_STAGE":
        stage = _scoped(given.get("stage"), database, patterns)
        if stage_exists(db, stage):
            raise ValueError(f"{stage} already exists")
        return {"stage": stage}
    if action == "DEDUP_ON_KEY":
        table = _scoped(given.get("table"), database, patterns)
        keys = [str(k).strip().upper() for k in (given.get("key_columns") or []) if str(k).strip()]
        cols = columns_of(db, table)
        if cols is None:
            raise ValueError(f"{table} does not exist")
        if not keys or any(not _COLUMN.match(k) or k not in cols for k in keys):
            raise ValueError(f"key columns must be existing columns of {table}")
        latest = str(given.get("keep_latest_by") or "").strip().upper() or None
        if latest and (not _COLUMN.match(latest) or latest not in cols):
            raise ValueError(f"keep_latest_by must be an existing column of {table}")
        return {"table": table, "key_columns": keys, "keep_latest_by": latest,
                "order_columns": [c for c, t in cols.items() if c not in keys
                                  and str(t).upper() not in ("GEOGRAPHY", "GEOMETRY", "VECTOR")]}
    return {}


def validate_plan(db: Any, steps: Any, database: str, scope: Optional[str]) -> List[Dict[str, Any]]:
    """The plan as skill steps [{step_no, action (CORE.FIX_*), input (JSON), note}], or ValueError."""
    patterns = scope_patterns(scope)
    if not patterns:
        raise ValueError("no agent fix scope is set (CONTROL_PREFERENCE.AGENT_FIX_SCOPE)")
    if not isinstance(steps, list) or not steps or len(steps) > 5:
        raise ValueError("a proposed fix has one to five steps")
    out = []
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError(f"step {i} is not an object")
        action = str(step.get("action") or "").upper()
        clean = check_step(db, action, step.get("input") or {}, database, patterns)
        out.append({"step_no": i, "action": ACTIONS[action]["procedure"], "input": json.dumps(clean),
                    "note": f"{action}: {str(step.get('note') or ACTIONS[action]['doc'])[:200]}"})
    return out


def risk_of(steps: List[Dict[str, Any]]) -> str:
    kinds = {next((n for n, a in ACTIONS.items() if a["procedure"] == s["action"]), "") for s in steps}
    return "data" if any(ACTIONS.get(k, {}).get("risk") == "data" for k in kinds) else \
        ("schema" if any(ACTIONS.get(k, {}).get("risk") == "schema" for k in kinds) else "none")


# ---------------------------------------------------------------- the procedures (run time: checked again)

def _context(session: Any) -> Tuple[Any, str, List[str]]:
    from services.ops.sqlio import as_db
    from services.resolve.engine import database_of

    db = as_db(session)
    database = database_of(db)
    found = db.query("SELECT AGENT_FIX_SCOPE FROM CORE.CONTROL_PREFERENCE WHERE IS_ACTIVE ORDER BY UPDATED_AT DESC LIMIT 1")
    return db, database, scope_patterns(found[0].get("agent_fix_scope") if found else None)


def _run(session: Any, action: str, failure_id: str, given: str) -> Dict[str, Any]:
    db, database, patterns = _context(session)
    try:
        payload = json.loads(given or "{}")
    except ValueError:
        raise ValueError("the step input is not JSON")
    clean = check_step(db, action, payload, database, patterns, running=True)
    if action == "ADD_COLUMN":
        if not clean.get("exists"):
            db.execute(f"ALTER TABLE {clean['table']} ADD COLUMN {clean['column']} {clean['data_type']}")
        if clean.get("fill_from"):
            db.execute(f"UPDATE {clean['table']} SET {clean['column']} = {clean['fill_from']} WHERE {clean['column']} IS NULL")
    elif action == "CREATE_TABLE_FROM":
        db.execute(f"CREATE TABLE {clean['table']} AS SELECT * FROM {clean['source_table']}")
    elif action == "CREATE_STAGE":
        db.execute(f"CREATE STAGE {clean['stage']}")
    elif action == "DEDUP_ON_KEY":
        db.execute(dedup_sql(clean))
    return {"action": action, "done": clean, "failure": failure_id}


def _unfilled(db: Any, table: str, column: str, fill: str) -> bool:
    """True when rows still wait for the fill (the column is NULL where the source column is not)."""
    try:
        found = db.query(f"SELECT COUNT(*) AS N FROM {table} WHERE {column} IS NULL AND {fill} IS NOT NULL")
    except Exception:
        return False
    return bool(found) and int(found[0].get("n") or 0) > 0


def dedup_sql(clean: Dict[str, Any]) -> str:
    """One row per key, in place: INSERT OVERWRITE keeps the table itself (its streams, grants, comments, clustering and
    defaults), unlike CREATE OR REPLACE. The kept row is chosen the same way every time: the latest keep_latest_by,
    then every other column."""
    keys = ", ".join(clean["key_columns"])
    order = [f"{clean['keep_latest_by']} DESC NULLS LAST"] if clean.get("keep_latest_by") else []
    order += [c for c in clean.get("order_columns") or [] if c != clean.get("keep_latest_by")]
    return (f"INSERT OVERWRITE INTO {clean['table']} SELECT * FROM {clean['table']} "
            f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {keys} ORDER BY {', '.join(order) or keys}) = 1")


def add_column_entry(session: Any, failure_id: str, given: str) -> Dict[str, Any]:
    return _run(session, "ADD_COLUMN", failure_id, given)


def create_table_from_entry(session: Any, failure_id: str, given: str) -> Dict[str, Any]:
    return _run(session, "CREATE_TABLE_FROM", failure_id, given)


def create_stage_entry(session: Any, failure_id: str, given: str) -> Dict[str, Any]:
    return _run(session, "CREATE_STAGE", failure_id, given)


def dedup_on_key_entry(session: Any, failure_id: str, given: str) -> Dict[str, Any]:
    return _run(session, "DEDUP_ON_KEY", failure_id, given)


def rerun_only_entry(session: Any, failure_id: str, given: str = "{}") -> Dict[str, Any]:
    return {"action": "RERUN_ONLY", "done": {}, "failure": failure_id}


def describe_entry(session: Any, name: str) -> Dict[str, Any]:
    """CALL CORE.SP_DESCRIBE_OBJECT(name): the RCA agent's tool. Columns of a table inside the fix scope (read only)."""
    db, database, patterns = _context(session)
    try:
        fqn = _scoped(name, database, patterns)
    except ValueError as exc:
        return {"error": str(exc)}
    cols = columns_of(db, fqn)
    if cols is None:
        return {"object": fqn, "exists": stage_exists(db, fqn) and "stage" or False}
    return {"object": fqn, "exists": True, "columns": cols, "comment": table_comment(db, fqn)}
