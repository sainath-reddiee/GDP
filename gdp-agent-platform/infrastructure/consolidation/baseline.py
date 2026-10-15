"""P1 and P2: live inventory and baseline of every platform object, before the move. Read-only apart from writing the
manifest (CORE.CONSOLIDATION_RUN / CONSOLIDATION_OBJECT, V060).

    python -m infrastructure.consolidation.baseline --connection <name> --database <DB> [--rehearsal]

Run it after `runtime freeze` (writes stopped), so the row counts and hashes are the ones the move must preserve. The
baseline is COMPLETED only when every object in the old schemas is known (in MOVED, a known runtime prefix, or a
runtime kind: secret, git repository, dbt project) and every capture succeeded; the deploy gate for V061 needs it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any, Dict, List, Tuple

from infrastructure.consolidation.common import (LEDGER, LEGACY_SCHEMAS, MOVED, PLATFORM_SCHEMA, REHEARSAL_SUFFIX,
                                                 RUNTIME_PREFIXES, SHOW_KINDS, connect, database_name, finish_run,
                                                 fq, grants_on, jsonable, latest_run, parser, q, show, start_run,
                                                 try_q, write_json)

RUNTIME_KINDS = {"SECRETS": "SECRET", "GIT REPOSITORIES": "GIT REPOSITORY", "DBT PROJECTS": "DBT PROJECT"}
# Written by every deploy (the V060 and V061 rows themselves), so not part of the "CORE did not change" proof.
LEDGER_TABLES = ("SCHEMA_MIGRATION", "DEPLOY_STATE")


def files_digest(listing: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """LIST rows -> (files relative to the stage, summary with a digest over path, size and md5)."""
    files = sorted(({"path": str(r.get("name", "")).split("/", 1)[-1], "size": int(r.get("size") or 0),
                     "md5": r.get("md5")} for r in listing), key=lambda f: f["path"])
    blob = "\n".join(f"{f['path']}|{f['size']}|{f['md5']}" for f in files)
    return files, {"files": len(files), "bytes": sum(f["size"] for f in files),
                   "digest": hashlib.sha256(blob.encode("utf-8")).hexdigest()}


def table_capture(cur, db: str, schema: str, name: str, shown: Dict[str, Any], columns: List[Dict[str, Any]]):
    fqn = fq(db, schema, name)
    stats = q(cur, f"SELECT COUNT(*) AS N, HASH_AGG(*) AS H, CURRENT_TIMESTAMP()::VARCHAR AS AT FROM {fqn}")[0]
    ident = try_q(cur, f"SELECT ID FROM {db}.INFORMATION_SCHEMA.TABLE_STORAGE_METRICS WHERE TABLE_SCHEMA = %s "
                       "AND TABLE_NAME = %s AND TABLE_DROPPED IS NULL", (schema, name))
    policies = try_q(cur, f"SELECT POLICY_NAME, POLICY_KIND, REF_COLUMN_NAME, POLICY_STATUS FROM TABLE("
                          f"{db}.INFORMATION_SCHEMA.POLICY_REFERENCES(REF_ENTITY_NAME => %s, REF_ENTITY_DOMAIN => 'table'))",
                     (fqn,))
    pks = try_q(cur, f"SHOW PRIMARY KEYS IN TABLE {fqn}")
    fks = try_q(cur, f"SHOW IMPORTED KEYS IN TABLE {fqn}")
    return {
        "row_count": int(stats["n"]), "hash": str(stats["h"]), "captured_at": stats["at"],
        "table_id": str(ident[0]["id"]) if ident else None,
        "created_on": str(shown.get("created_on")), "kind": shown.get("kind"),
        "retention_time": shown.get("retention_time"), "cluster_by": shown.get("cluster_by"),
        "change_tracking": shown.get("change_tracking"), "comment": shown.get("comment"), "owner": shown.get("owner"),
        "columns": [{k: c.get(k) for k in ("column_name", "data_type", "is_nullable", "column_default",
                                            "character_maximum_length", "numeric_precision", "numeric_scale")}
                    for c in columns],
        "primary_key": sorted(str(r.get("column_name")) for r in pks),
        "foreign_keys": sorted(f"{r.get('fk_column_name')}->{r.get('pk_schema_name')}.{r.get('pk_table_name')}."
                               f"{r.get('pk_column_name')}" for r in fks),
        "policies": sorted(({"policy": p["policy_name"], "kind": p["policy_kind"], "column": p["ref_column_name"]}
                            for p in policies), key=lambda p: (p["kind"], p["policy"])),
        "grants": grants_on(cur, "TABLE", fqn),
    }


def main() -> None:
    p = parser(__doc__)
    p.add_argument("--rehearsal", action="store_true")
    p.add_argument("--skip-freeze-check", action="store_true", help="only for a rehearsal clone (its tasks are suspended)")
    args = p.parse_args()
    db = database_name(args)
    if args.rehearsal:
        assert db.endswith(REHEARSAL_SUFFIX), f"--rehearsal needs a *{REHEARSAL_SUFFIX} database"
    con = connect(args)
    cur = con.cursor()
    assert q(cur, f"SHOW TABLES LIKE 'CONSOLIDATION_OBJECT' IN SCHEMA {db}.{LEDGER}"), \
        "V060 is not applied: deploy_snowflake.py --through 060 first"
    freeze = latest_run(cur, db, "FREEZE")
    if not freeze and not args.skip_freeze_check:
        sys.exit("No completed freeze: run `python -m infrastructure.consolidation.runtime freeze` first.")
    run_id = start_run(cur, db, "BASELINE", args.rehearsal, {"freeze_run": freeze and freeze["run_id"]})
    errors: List[str] = []
    unknown: List[str] = []
    objects: List[Dict[str, Any]] = []

    listed: Dict[str, List[Dict[str, Any]]] = {kind: show(cur, kind, db) for kind in SHOW_KINDS}
    in_scope = set(LEGACY_SCHEMAS) | {PLATFORM_SCHEMA}

    def known(kind: str, schema: str, name: str) -> bool:
        if (schema, name) in MOVED or kind in RUNTIME_KINDS:
            return True
        return any(schema == s and name.startswith(pfx) for (s, pfx) in RUNTIME_PREFIXES)

    for kind, rows_ in listed.items():
        for r in rows_:
            schema, name = str(r.get("schema_name") or ""), str(r.get("name") or "")
            if schema in LEGACY_SCHEMAS and not known(kind, schema, name):
                unknown.append(f"{kind[:-1] if kind.endswith('S') else kind} {schema}.{name}")

    shown_tables = {(r["schema_name"], r["name"]): r for r in listed["TABLES"] if r.get("schema_name") in in_scope}
    legacy_list = ", ".join(f"'{s}'" for s in sorted(in_scope))
    cols: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for c in q(cur, f"SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
                    f"CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE FROM {db}.INFORMATION_SCHEMA.COLUMNS "
                    f"WHERE TABLE_SCHEMA IN ({legacy_list}) ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"):
        cols.setdefault((c["table_schema"], c["table_name"]), []).append(c)

    def add(obj_type: str, old: str, new: str, method: str, baseline: Dict[str, Any]) -> None:
        objects.append({"type": obj_type, "old": old, "new": new, "method": method, "baseline": baseline})

    # tables: every moved table, plus CORE tables (KEEP) so the check proves V061 did not touch them
    for (schema, name), shown in sorted(shown_tables.items()):
        moving = MOVED.get((schema, name))
        if schema == PLATFORM_SCHEMA and name not in LEDGER_TABLES and not str(name).startswith("CONSOLIDATION_"):
            target, method = name, "KEEP"
        elif moving and moving[0] == "TABLE":
            target, method = moving[1], moving[2]
        else:
            continue
        try:
            add("TABLE", fq(db, schema, name), fq(db, PLATFORM_SCHEMA, target), method,
                table_capture(cur, db, schema, name, shown, cols.get((schema, name), [])))
        except Exception as exc:
            errors.append(f"TABLE {schema}.{name}: {str(exc)[:300]}")

    for r in listed["VIEWS"]:
        key = (r.get("schema_name"), r.get("name"))
        if key in MOVED:
            t, new, method = MOVED[key]
            add("VIEW", fq(db, *key), fq(db, PLATFORM_SCHEMA, new), method,
                {"text": r.get("text"), "grants": grants_on(cur, "VIEW", fq(db, *key))})

    for r in listed["STAGES"]:
        key = (r.get("schema_name"), r.get("name"))
        if key not in MOVED and key[0] != PLATFORM_SCHEMA:
            continue
        fqn = fq(db, *key)
        try:
            files, summary = files_digest(q(cur, f"LIST @{fqn}"))
        except Exception as exc:
            errors.append(f"STAGE {fqn}: {str(exc)[:300]}")
            continue
        new = fq(db, PLATFORM_SCHEMA, MOVED[key][1]) if key in MOVED else fqn
        add("STAGE", fqn, new, MOVED[key][2] if key in MOVED else "KEEP",
            {**summary, "type": r.get("type"), "directory_enabled": r.get("directory_enabled"),
             "grants": grants_on(cur, "STAGE", fqn)})
        write_json(args, f"stage_{key[0]}_{key[1]}_files", files)

    for r in listed["SEQUENCES"]:
        key = (r.get("schema_name"), r.get("name"))
        if key in MOVED:
            add("SEQUENCE", fq(db, *key), fq(db, PLATFORM_SCHEMA, MOVED[key][1]), MOVED[key][2],
                {"next_value": int(r.get("next_value") or 0), "interval": r.get("interval"),
                 "grants": grants_on(cur, "SEQUENCE", fq(db, *key))})

    for r in listed["FILE FORMATS"]:
        key = (r.get("schema_name"), r.get("name"))
        if key in MOVED:
            add("FILE FORMAT", fq(db, *key), fq(db, PLATFORM_SCHEMA, MOVED[key][1]), MOVED[key][2],
                {"type": r.get("type"), "format_options": r.get("format_options"),
                 "grants": grants_on(cur, "FILE FORMAT", fq(db, *key))})

    for r in listed["ROW ACCESS POLICIES"]:
        key = (r.get("schema_name"), r.get("name"))
        if key not in MOVED and key[0] != PLATFORM_SCHEMA:
            continue
        fqn = fq(db, *key)
        body = try_q(cur, f"DESCRIBE ROW ACCESS POLICY {fqn}")
        refs = try_q(cur, f"SELECT REF_SCHEMA_NAME, REF_ENTITY_NAME, REF_COLUMN_NAME FROM TABLE("
                          f"{db}.INFORMATION_SCHEMA.POLICY_REFERENCES(POLICY_NAME => %s))", (fqn,))
        add("ROW ACCESS POLICY", fqn, fq(db, PLATFORM_SCHEMA, MOVED[key][1]) if key in MOVED else fqn,
            MOVED[key][2] if key in MOVED else "KEEP",
            {"body": body[0].get("body") if body else None, "signature": body[0].get("signature") if body else None,
             "references": sorted(f"{x['ref_schema_name']}.{x['ref_entity_name']}.{x['ref_column_name']}" for x in refs),
             "grants": grants_on(cur, "ROW ACCESS POLICY", fqn)})

    # procedures in the old schemas: recreated by the deploy; their grants are revoked by `runtime apply`
    for r in listed["PROCEDURES"]:
        schema = r.get("schema_name")
        if schema not in LEGACY_SCHEMAS:
            continue
        signature = str(r.get("arguments") or "").split(" RETURN ")[0]
        fqn_sig = f"{db}.{schema}.{signature}"
        add("PROCEDURE", fqn_sig, f"{db}.{PLATFORM_SCHEMA}.{signature}", "RECREATE",
            {"grants": grants_on(cur, "PROCEDURE", fqn_sig)})

    # runtime objects
    states = {t["name"]: t["state"] for t in (freeze or {}).get("details", {}).get("tasks", [])} if freeze else {}
    for r in listed["TASKS"]:
        schema, name = r.get("schema_name"), r.get("name")
        if schema in LEGACY_SCHEMAS:
            fqn = fq(db, schema, name)
            add("TASK", fqn, fq(db, PLATFORM_SCHEMA, name), "RUNTIME",
                {k: r.get(k) for k in ("state", "schedule", "warehouse", "definition", "predecessors", "comment",
                                         "allow_overlapping_execution", "condition")}
                | {"state_before_freeze": states.get(fqn, r.get("state")), "grants": grants_on(cur, "TASK", fqn)})
    for r in listed["SECRETS"]:
        schema = r.get("schema_name")
        if schema in LEGACY_SCHEMAS:
            fqn = fq(db, schema, r.get("name"))
            add("SECRET", fqn, fqn, "KEEP", {"secret_type": r.get("secret_type"), "comment": r.get("comment"),
                                              "owner": r.get("owner"), "grants": grants_on(cur, "SECRET", fqn)})
    for r in listed["GIT REPOSITORIES"]:
        schema = r.get("schema_name")
        if schema in LEGACY_SCHEMAS:
            fqn = fq(db, schema, r.get("name"))
            add("GIT REPOSITORY", fqn, fqn, "KEEP",
                {k: r.get(k) for k in ("origin", "api_integration", "git_credentials", "comment")}
                | {"grants": grants_on(cur, "GIT REPOSITORY", fqn)})
    for r in listed["DBT PROJECTS"]:
        schema = r.get("schema_name")
        if schema in LEGACY_SCHEMAS:
            fqn = fq(db, schema, r.get("name"))
            versions = try_q(cur, f"SHOW VERSIONS IN DBT PROJECT {fqn}")
            add("DBT PROJECT", fqn, fqn, "KEEP",
                {"versions": sorted(str(v.get("name") or v.get("version")) for v in versions),
                 "grants": grants_on(cur, "DBT PROJECT", fqn)})
    for r in listed["NETWORK RULES"]:
        key = (r.get("schema_name"), r.get("name"))
        if key[0] in LEGACY_SCHEMAS:
            fqn = fq(db, *key)
            desc = try_q(cur, f"DESCRIBE NETWORK RULE {fqn}")
            add("NETWORK RULE", fqn, fq(db, PLATFORM_SCHEMA, key[1]), "RUNTIME",
                {"describe": desc[0] if desc else {}})
    for r in listed["CORTEX SEARCH SERVICES"]:
        key = (r.get("schema_name"), r.get("name"))
        if key in MOVED:
            add("CORTEX SEARCH SERVICE", fq(db, *key), fq(db, PLATFORM_SCHEMA, key[1]), "RECREATE",
                {k: r.get(k) for k in ("indexing_state", "serving_state", "definition")}
                | {"grants": grants_on(cur, "CORTEX SEARCH SERVICE", fq(db, *key))})
    for r in listed["AGENTS"]:
        if r.get("schema_name") == PLATFORM_SCHEMA:
            fqn = fq(db, PLATFORM_SCHEMA, r.get("name"))
            desc = try_q(cur, f"DESCRIBE AGENT {fqn}")
            versions = try_q(cur, f"SHOW VERSIONS IN AGENT {fqn}")
            add("AGENT", fqn, fqn, "KEEP", {"describe": jsonable(desc[0]) if desc else {},
                                             "versions": sorted(str(v.get("name")) for v in versions),
                                             "grants": grants_on(cur, "AGENT", fqn)})

    # schema-level grants and future grants (section 6 of the plan)
    schemas = {}
    for schema in sorted(in_scope):
        schemas[schema] = {"grants": grants_on(cur, "SCHEMA", f"{db}.{schema}"),
                           "future": [{"privilege": g.get("privilege"), "grant_on": g.get("grant_on"),
                                       "grantee": g.get("grantee_name")}
                                      for g in try_q(cur, f"SHOW FUTURE GRANTS IN SCHEMA {db}.{schema}")]}

    stored = scan_stored_names(cur, db, cols)
    missing = sorted(f"{t} {s}.{n}" for (s, n), (t, _, m) in MOVED.items()
                     if m == "RENAME" and not any(o["old"] == fq(db, s, n) for o in objects))
    for o in objects:
        cur.execute(f"INSERT INTO {db}.{LEDGER}.CONSOLIDATION_OBJECT (RUN_ID, OBJECT_TYPE, OLD_FQN, NEW_FQN, METHOD, "
                    "BASELINE, STATUS) SELECT %s, %s, %s, %s, %s, PARSE_JSON(%s), 'BASELINED'",
                    (run_id, o["type"], o["old"], o["new"], o["method"], json.dumps(jsonable(o["baseline"]))))
    ok = not errors and not unknown
    details = {"objects": len(objects), "unknown": unknown, "errors": errors, "missing": missing,
               "stored_names": stored, "schemas": schemas, "freeze_run": freeze and freeze["run_id"]}
    finish_run(cur, db, run_id, ok, details)
    path = write_json(args, "baseline", {"run_id": run_id, **details, "objects": objects})
    by_type: Dict[str, int] = {}
    for o in objects:
        by_type[o["type"]] = by_type.get(o["type"], 0) + 1
    print(f"baseline {run_id}: {'COMPLETED' if ok else 'FAILED'}; {by_type}")
    for line in unknown:
        print(f"  unknown object (decide before moving): {line}")
    for line in errors:
        print(f"  error: {line}")
    for line in missing:
        print(f"  not present (nothing to move): {line}")
    for line in stored:
        print(f"  stored legacy names: {line}")
    print(f"written: {path}")
    con.close()
    sys.exit(0 if ok else 1)


def scan_stored_names(cur, db: str, cols) -> List[str]:
    """Columns whose rows hold a legacy schema-qualified name (informational: read-time resolution, rows unchanged)."""
    out: List[str] = []
    # SQL string literal holding the regex  .*(^|[^A-Z0-9_])(SOURCE|...)\.[A-Z][A-Z0-9_]+.*  (backslash doubled for SQL)
    regex = "'.*(^|[^A-Z0-9_])(" + "|".join(LEGACY_SCHEMAS) + ")" + "\\\\" + ".[A-Z][A-Z0-9_]+.*'"
    for (schema, table), columns in sorted(cols.items()):
        if (schema, table) not in MOVED and schema != PLATFORM_SCHEMA:
            continue
        text_cols = [(c["column_name"], c["data_type"]) for c in columns
                     if c["data_type"] in ("TEXT", "VARIANT", "ARRAY", "OBJECT")]
        if not text_cols:
            continue
        exprs = ", ".join(
            f"COUNT_IF(REGEXP_LIKE({quote_col(c) if kind == 'TEXT' else 'TO_JSON(' + quote_col(c) + ')'}, {regex}, 's')) "
            f"AS {quote_col(c)}" for c, kind in text_cols)
        try:
            counts = q(cur, f"SELECT {exprs} FROM {fq(db, schema, table)}")[0]
        except Exception as exc:
            out.append(f"{schema}.{table}: scan failed ({str(exc)[:120]})")
            continue
        for c, n in counts.items():
            if n:
                out.append(f"{schema}.{table}.{c.upper()}: {n} row(s)")
    return out


def quote_col(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


if __name__ == "__main__":
    main()
