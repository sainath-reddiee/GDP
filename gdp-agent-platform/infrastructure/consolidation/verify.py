"""P6: prove that nothing was lost, against the baseline.

    python -m infrastructure.consolidation.verify data       --connection <c> --database <DB> [--backup-database <B>]
    python -m infrastructure.consolidation.verify functional --connection <c> --database <DB>

data        right after `deploy_snowflake.py --consolidate --through 061` (before the seed writes anything): every moved
            object exists in CORE as the SAME object (table id, created_on), with the same rows (count and full-row
            HASH_AGG), columns, keys, policies and grants; Time Travel still reaches the baseline; every staged file is
            there (path, size, md5); sequences never go back. --backup-database also compares each table row by row
            (HASH(*) set difference, both directions) with the zero-copy backup clone. CORE tables must be unchanged.
functional  after the full deploy and `runtime apply`: procedures, Cortex Search services, the agent, task twins,
            compatibility views and their grants, old services suspended, no USAGE left on old procedures.

Exit code 0 only when every check passed. Results go to CORE.CONSOLIDATION_CHECK and a JSON file.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Dict, List

from infrastructure.consolidation.baseline import files_digest, table_capture
from infrastructure.consolidation.common import (LEDGER, PLATFORM_SCHEMA, ROOT, baseline_objects,
                                                 connect, database_name, finish_run, grants_on, jsonable, latest_run,
                                                 map_fqn, parser, q, show, start_run, try_q, write_json)
from infrastructure.consolidation.generate import NO_COMPAT_VIEW

PROC = re.compile(r"CREATE\s+OR\s+REPLACE\s+PROCEDURE\s+\{\{database\}\}\.\{\{schema\}\}\.([A-Z_][A-Z0-9_]*)\s*\(", re.I)


class Checks:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def add(self, obj: str, name: str, expected: Any, actual: Any, passed: bool) -> None:
        self.rows.append({"object": obj, "check": name, "expected": jsonable(expected), "actual": jsonable(actual),
                          "passed": bool(passed)})

    def eq(self, obj: str, name: str, expected: Any, actual: Any) -> None:
        self.add(obj, name, expected, actual, expected == actual)

    @property
    def failed(self) -> List[Dict[str, Any]]:
        return [r for r in self.rows if not r["passed"]]


def split(fqn: str):
    db, schema, name = fqn.split(".", 2)
    return db, schema.strip('"'), name.strip('"')


def mapped_grants(grants):
    return sorted((g["privilege"], g["granted_to"], g["grantee"]) for g in grants or [] if g["privilege"] != "OWNERSHIP") + \
        sorted(("OWNERSHIP", g["granted_to"], g["grantee"]) for g in grants or [] if g["privilege"] == "OWNERSHIP")


def norm_columns(columns, db: str):
    return [{**c, "column_default": map_fqn(str(c["column_default"]), db) if c.get("column_default") else None}
            for c in columns or []]


def norm_fks(fks, moved_pairs):
    out = []
    for fk in fks or []:
        left, target = fk.split("->")
        schema, table, col = target.split(".")
        if (schema, table) in moved_pairs:
            schema, table = PLATFORM_SCHEMA, moved_pairs[(schema, table)]
        out.append(f"{left}->{schema}.{table}.{col}")
    return sorted(out)


def data(args, cur, db: str, objects, checks: Checks) -> None:
    tables = {(r["schema_name"], r["name"]): r for r in show(cur, "TABLES", db)}
    cols: Dict[Any, List[Dict[str, Any]]] = {}
    for c in q(cur, f"SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
                    f"CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE FROM {db}.INFORMATION_SCHEMA.COLUMNS "
                    f"WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION", (PLATFORM_SCHEMA,)):
        cols.setdefault((c["table_schema"], c["table_name"]), []).append(c)
    moved_pairs = {split(o["old_fqn"])[1:]: split(o["new_fqn"])[2] for o in objects if o["object_type"] == "TABLE"}
    stages = {(r["schema_name"], r["name"]) for r in show(cur, "STAGES", db)}
    sequences = {(r["schema_name"], r["name"]): r for r in show(cur, "SEQUENCES", db)}
    formats = {(r["schema_name"], r["name"]): r for r in show(cur, "FILE FORMATS", db)}
    for o in objects:
        kind, old, new, b = o["object_type"], o["old_fqn"], o["new_fqn"], o["baseline"] or {}
        _, schema, name = split(new)
        _, old_schema, old_name = split(old)
        if kind == "TABLE":
            shown = tables.get((schema, name))
            checks.add(old, "EXISTS_IN_CORE", new, bool(shown), bool(shown))
            if not shown:
                continue
            if o["method"] != "KEEP":
                still = tables.get((old_schema, old_name))
                checks.add(old, "GONE_FROM_OLD_SCHEMA", None, bool(still), not still)
            now = table_capture(cur, db, schema, name, shown, cols.get((schema, name), []))
            checks.eq(old, "ROW_COUNT", b["row_count"], now["row_count"])
            checks.eq(old, "HASH", b["hash"], now["hash"])
            if b.get("table_id"):
                checks.eq(old, "OBJECT_ID", b["table_id"], now["table_id"])
            checks.eq(old, "CREATED_ON", b.get("created_on"), now["created_on"])
            checks.eq(old, "COLUMNS", norm_columns(b["columns"], db), norm_columns(now["columns"], db))
            checks.eq(old, "PRIMARY_KEY", b["primary_key"], now["primary_key"])
            checks.eq(old, "FOREIGN_KEYS", norm_fks(b["foreign_keys"], moved_pairs),
                      norm_fks(now["foreign_keys"], moved_pairs))
            for k in ("retention_time", "cluster_by", "change_tracking", "comment", "owner"):
                checks.eq(old, k.upper(), b.get(k), now.get(k))
            checks.eq(old, "POLICIES", b["policies"], now["policies"])
            checks.eq(old, "GRANTS", mapped_grants(b["grants"]), mapped_grants(now["grants"]))
            tt = try_q(cur, f"SELECT COUNT(*) AS N FROM {new} AT(TIMESTAMP => %s::TIMESTAMP_LTZ)", (b["captured_at"],))
            checks.eq(old, "TIME_TRAVEL_ROW_COUNT", b["row_count"], int(tt[0]["n"]) if tt else None)
            if args.backup_database:
                backup = f"{args.backup_database.upper()}.{old_schema}.{old_name}"
                for label, a, z in (("MISSING_VS_BACKUP", backup, new), ("EXTRA_VS_BACKUP", new, backup)):
                    diff = try_q(cur, f"SELECT COUNT(*) AS N FROM (SELECT HASH(*) FROM {a} MINUS SELECT HASH(*) FROM {z})")
                    checks.eq(old, label, 0, int(diff[0]["n"]) if diff else None)
        elif kind == "STAGE":
            present = (schema, name) in stages
            checks.add(old, "EXISTS_IN_CORE", new, present, present)
            if present:
                _, summary = files_digest(q(cur, f"LIST @{new}"))
                checks.eq(old, "FILES", {k: b[k] for k in ("files", "bytes", "digest")}, summary)
                checks.eq(old, "GRANTS", mapped_grants(b["grants"]), mapped_grants(grants_on(cur, "STAGE", new)))
        elif kind == "SEQUENCE":
            now = sequences.get((schema, name))
            checks.add(old, "EXISTS_IN_CORE", new, bool(now), bool(now))
            if now:
                nxt = int(now.get("next_value") or 0)
                checks.add(old, "NEXT_VALUE_NOT_BEHIND", b["next_value"], nxt, nxt >= int(b["next_value"]))
                checks.eq(old, "GRANTS", mapped_grants(b["grants"]), mapped_grants(grants_on(cur, "SEQUENCE", new)))
        elif kind == "FILE FORMAT":
            now = formats.get((schema, name))
            checks.add(old, "EXISTS_IN_CORE", new, bool(now), bool(now))
            if now:
                checks.eq(old, "FORMAT_OPTIONS", b.get("format_options"), now.get("format_options"))
                checks.eq(old, "GRANTS", mapped_grants(b["grants"]), mapped_grants(grants_on(cur, "FILE FORMAT", new)))
        elif kind == "ROW ACCESS POLICY":
            refs = try_q(cur, f"SELECT REF_SCHEMA_NAME, REF_ENTITY_NAME, REF_COLUMN_NAME FROM TABLE("
                              f"{db}.INFORMATION_SCHEMA.POLICY_REFERENCES(POLICY_NAME => %s))", (new,))
            got = sorted(f"{x['ref_schema_name']}.{x['ref_entity_name']}.{x['ref_column_name']}" for x in refs)
            want = sorted(f"{PLATFORM_SCHEMA}.{moved_pairs.get(tuple(r.split('.')[:2]), r.split('.')[1])}."
                          f"{r.split('.')[2]}" if tuple(r.split(".")[:2]) in moved_pairs else r for r in b["references"])
            checks.eq(old, "ATTACHED_TO", want, got)
            body = try_q(cur, f"DESCRIBE ROW ACCESS POLICY {new}")
            text = str(body[0].get("body")) if body else ""
            checks.add(old, "BODY_NAMES_NO_OLD_SCHEMA", "no legacy reference", text, map_fqn(text, db) == text)
        elif kind == "VIEW":
            found = try_q(cur, f"SELECT COUNT(*) AS N FROM {new}")
            checks.add(old, "VIEW_READABLE_IN_CORE", new, bool(found), bool(found))


def expected_procedures() -> List[str]:
    names = set()
    for path in sorted((ROOT / "snowflake" / "procedures").glob("*.sql")):
        names.update(m.group(1).upper() for m in PROC.finditer(path.read_text(encoding="utf-8")))
    return sorted(names)


def functional(args, cur, db: str, objects, checks: Checks) -> None:
    procs = {r["name"] for r in show(cur, "PROCEDURES", db) if r.get("schema_name") == PLATFORM_SCHEMA}
    for name in expected_procedures():
        checks.add(f"{db}.{PLATFORM_SCHEMA}.{name}", "PROCEDURE_EXISTS", True, name in procs, name in procs)
    services = {r["name"]: r for r in show(cur, "CORTEX SEARCH SERVICES", db)
                if r.get("schema_name") == PLATFORM_SCHEMA}
    for name in ("KNOWLEDGE_SEARCH", "CODE_SEARCH"):
        s = services.get(name, {})
        checks.add(f"{db}.{PLATFORM_SCHEMA}.{name}", "SEARCH_SERVICE_ACTIVE", "ACTIVE",
                   [s.get("indexing_state"), s.get("serving_state")], s.get("serving_state") == "ACTIVE")
    tasks = {f"{t['database_name']}.{t['schema_name']}.{t['name']}": t for t in show(cur, "TASKS", db)}
    for o in objects:
        kind, old, new, b = o["object_type"], o["old_fqn"], o["new_fqn"], o["baseline"] or {}
        if kind == "AGENT":
            desc = try_q(cur, f"DESCRIBE AGENT {new}")
            spec = json.dumps(jsonable(desc[0])) if desc else ""
            checks.add(new, "AGENT_SPEC_NAMES_NO_OLD_SCHEMA", True, bool(spec), bool(spec) and map_fqn(spec, db) == spec)
            versions = sorted(str(v.get("name")) for v in try_q(cur, f"SHOW VERSIONS IN AGENT {new}"))
            checks.add(new, "AGENT_VERSIONS_KEPT", b.get("versions"), versions, set(b.get("versions") or []) <= set(versions))
        elif kind == "TASK":
            twin, legacy = tasks.get(new), tasks.get(old)
            checks.add(old, "TASK_TWIN_IN_CORE", new, bool(twin), bool(twin))
            if twin:
                body = str(twin.get("definition") or "")
                checks.add(old, "TASK_BODY_NAMES_NO_OLD_SCHEMA", True, body, map_fqn(body, db) == body)
                checks.eq(old, "TASK_SCHEDULE", b.get("schedule"), twin.get("schedule"))
            checks.add(old, "OLD_TASK_SUSPENDED", "suspended", legacy and legacy.get("state"),
                       not legacy or legacy.get("state") == "suspended")
        elif kind in ("GIT REPOSITORY", "SECRET", "DBT PROJECT"):
            present = bool(try_q(cur, f"DESCRIBE {kind} {old}"))
            checks.add(old, "KEPT_IN_PLACE", True, present, present)
        elif kind in ("TABLE", "VIEW") and o["method"] == "RENAME":
            _, schema, name = split(old)
            if (schema, name) in NO_COMPAT_VIEW:
                continue
            readable = try_q(cur, f"SELECT COUNT(*) AS N FROM {old}")
            checks.add(old, "COMPAT_VIEW_READABLE", True, bool(readable), bool(readable))
            want = sorted(g["grantee"] for g in b.get("grants") or [] if g["privilege"] == "SELECT")
            got = sorted(g["grantee"] for g in grants_on(cur, "VIEW", old) if g["privilege"] == "SELECT")
            checks.eq(old, "COMPAT_VIEW_SELECT_GRANTS", want, got)
        elif kind == "CORTEX SEARCH SERVICE":
            found = [r for r in show(cur, "CORTEX SEARCH SERVICES", db)
                     if f"{r['database_name']}.{r['schema_name']}.{r['name']}" == old]
            state = found[0].get("serving_state") if found else None
            checks.add(old, "OLD_SERVICE_SUSPENDED", "SUSPENDED", state, state in (None, "SUSPENDED"))
        elif kind == "PROCEDURE":
            left = [g for g in grants_on(cur, "PROCEDURE", old) if g["privilege"] == "USAGE"]
            checks.add(old, "OLD_PROCEDURE_NO_USAGE", 0, len(left), not left)


def main() -> None:
    p = parser(__doc__)
    p.add_argument("phase", choices=("data", "functional"))
    p.add_argument("--backup-database", help="the zero-copy backup clone taken in P4")
    p.add_argument("--rehearsal", action="store_true")
    args = p.parse_args()
    db = database_name(args)
    con = connect(args)
    cur = con.cursor()
    base = latest_run(cur, db, "BASELINE")
    assert base, "no completed baseline"
    objects = baseline_objects(cur, db, base["run_id"])
    run_id = start_run(cur, db, "VERIFY", args.rehearsal, {"phase": args.phase, "baseline": base["run_id"]})
    checks = Checks()
    (data if args.phase == "data" else functional)(args, cur, db, objects, checks)
    for c in checks.rows:
        cur.execute(f"INSERT INTO {db}.{LEDGER}.CONSOLIDATION_CHECK (CHECK_RUN_ID, OLD_FQN, CHECK_NAME, EXPECTED, ACTUAL, "
                    "PASSED) SELECT %s, %s, %s, PARSE_JSON(%s), PARSE_JSON(%s), %s",
                    (run_id, c["object"], c["check"], json.dumps(c["expected"]), json.dumps(c["actual"]), c["passed"]))
    failed = checks.failed
    finish_run(cur, db, run_id, not failed, {"phase": args.phase, "checks": len(checks.rows), "failed": len(failed)})
    path = write_json(args, f"verify_{args.phase}", {"run_id": run_id, "checks": checks.rows})
    print(f"verify {args.phase} {run_id}: {len(checks.rows)} checks, {len(failed)} failed")
    for c in failed[:200]:
        print(f"  FAIL {c['object']} {c['check']}: expected {str(c['expected'])[:160]} got {str(c['actual'])[:160]}")
    print(f"written: {path}")
    con.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
