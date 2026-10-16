"""Roll the consolidation back: rename every moved object back to its old schema and name, from the manifest.

    python -m infrastructure.consolidation.rollback --connection <c> --database <DB> [--execute]

The move was a rename of the same objects, so the rollback is a rename too and keeps every row written since the
cut-over. Order: suspend the CORE task twins, drop the compatibility views that sit on the old names (views only, no
data), rename tables, views, policies, stages, file formats and sequences back, restore the references V061 changed
(sequence defaults, the landing stage's file format, the case policy body, the profile alias view, the CORE future
grant), resume the old search services, re-grant USAGE on the old procedures, resume the old tasks that were running
before the freeze (not on a rehearsal clone), and remove the V061 row from CORE.SCHEMA_MIGRATION so a later forward
run applies it again. Then deploy the previous release with --force. Nothing is dropped except the compatibility views.
Prints every statement; runs only with --execute.
"""

from __future__ import annotations

import sys

from infrastructure.consolidation.common import (LEDGER, REHEARSAL_SUFFIX, baseline_objects, connect, database_name,
                                                 finish_run, latest_run, parser, q, show, start_run, try_q,
                                                 write_json)
from infrastructure.consolidation.generate import NO_COMPAT_VIEW, SEQUENCE_DEFAULTS
from infrastructure.consolidation.runtime import Plan

REVERSE = ("VIEW", "TABLE", "ROW ACCESS POLICY", "STAGE", "FILE FORMAT", "SEQUENCE")


def main() -> None:
    p = parser(__doc__)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--rehearsal", action="store_true")
    args = p.parse_args()
    db = database_name(args)
    rehearsal = args.rehearsal or db.endswith(REHEARSAL_SUFFIX)
    con = connect(args)
    cur = con.cursor()
    base = latest_run(cur, db, "BASELINE")
    assert base, "no completed baseline: nothing to roll back to"
    objects = baseline_objects(cur, db, base["run_id"])
    plan = Plan(cur, args.execute)

    for o in objects:
        if o["object_type"] == "TASK":
            plan.run(f"ALTER TASK IF EXISTS {o['new_fqn']} SUSPEND", "suspend the CORE twin (kept, holds no data)")

    for o in objects:
        if o["object_type"] in ("TABLE", "VIEW") and o["method"] == "RENAME":
            schema, name = o["old_fqn"].split(".")[1:3]
            if (schema, name) in NO_COMPAT_VIEW:
                continue
            view = try_q(cur, f"SHOW VIEWS LIKE '{name}' IN SCHEMA {db}.{schema}")
            if view and str(view[0].get("comment") or "").startswith("Compatibility view"):
                plan.run(f"DROP VIEW IF EXISTS {o['old_fqn']}", "free the old name (compatibility view, no data)")

    for kind in REVERSE:
        for o in objects:
            if o["object_type"] == kind and o["method"] == "RENAME":
                plan.run(f"ALTER {kind} IF EXISTS {o['new_fqn']} RENAME TO {o['old_fqn']}", "move back")

    for table, column, seq in SEQUENCE_DEFAULTS:
        schema = "CASES" if table == "CASE_RECORD" else "PLATFORM_CONFIG"
        plan.run(f"ALTER TABLE {db}.{schema}.{table} ALTER COLUMN {column} SET DEFAULT {db}.{schema}.{seq}.NEXTVAL",
                 "sequence default back to the old name")
    plan.run(f"ALTER STAGE {db}.SOURCE.LANDING_STAGE SET FILE_FORMAT = (FORMAT_NAME = '{db}.SOURCE.PARQUET_SNAPPY')",
             "landing stage file format back")
    plan.run(f"""ALTER ROW ACCESS POLICY {db}.CASES.DOMAIN_SCOPE SET BODY ->
    ROW_DOMAIN IS NULL
    OR IS_DATABASE_ROLE_IN_SESSION('PLATFORM_ADMIN')
    OR ROW_DOMAIN IN (SELECT DOMAIN_ID FROM {db}.KNOWLEDGE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL')
    OR ROW_DOMAIN IN (SELECT DOMAIN_ID FROM {db}.KNOWLEDGE.DOMAIN_MEMBER WHERE UPPER(USER_NAME) = UPPER(CURRENT_USER()))
    OR ROW_DOMAIN NOT IN (SELECT DOMAIN_ID FROM {db}.KNOWLEDGE.DOMAIN_MEMBER)""", "case policy body back (V034)")
    plan.run(f"CREATE OR REPLACE VIEW {db}.GOVERNANCE.PERSISTENT_PROFILES COPY GRANTS "
             f"COMMENT = 'Alias of GOVERNANCE.DATA_PROFILES' AS SELECT * FROM {db}.GOVERNANCE.DATA_PROFILES",
             "profile alias view back (V036)")
    plan.run(f"GRANT SELECT ON FUTURE TABLES IN SCHEMA {db}.CORE TO DATABASE ROLE {db}.VIEWER", "V002 future grant back")

    for o in objects:
        b = o["baseline"] or {}
        if o["object_type"] == "CORTEX SEARCH SERVICE":
            plan.run(f"ALTER CORTEX SEARCH SERVICE {o['old_fqn']} RESUME INDEXING", "old service back")
            plan.run(f"ALTER CORTEX SEARCH SERVICE {o['old_fqn']} RESUME SERVING", "old service back")
        elif o["object_type"] == "PROCEDURE":
            for g in b.get("grants") or []:
                if g["privilege"] == "USAGE" and g["granted_to"] in ("DATABASE_ROLE", "ROLE"):
                    to = "DATABASE ROLE" if g["granted_to"] == "DATABASE_ROLE" else "ROLE"
                    plan.run(f"GRANT USAGE ON PROCEDURE {o['old_fqn']} TO {to} {g['grantee']}", "old procedure usage back")
        elif o["object_type"] == "TASK" and b.get("state_before_freeze") == "started" and not rehearsal:
            plan.run(f"ALTER TASK {o['old_fqn']} RESUME", "old task was running before the freeze")

    found = q(cur, f"SELECT VERSION, DESCRIPTION, CHECKSUM, APPLIED_BY, APPLIED_AT::VARCHAR AS APPLIED_AT "
                   f"FROM {db}.{LEDGER}.SCHEMA_MIGRATION WHERE VERSION = '061'")
    if found:
        plan.run(f"DELETE FROM {db}.{LEDGER}.SCHEMA_MIGRATION WHERE VERSION = '061'",
                 f"ledger: V061 applies again on a later forward run (row kept in the run details: {found[0]})")
    left = [t for t in show(cur, "TASKS", db) if t.get("state") == "started"]
    print(f"running tasks now: {len(left)}")
    if not args.execute:
        print("dry run: nothing executed (pass --execute). Afterwards: deploy the previous release with --force, "
              "then `verify data` against the same baseline.")
        con.close()
        return
    run_id = start_run(cur, db, "ROLLBACK", rehearsal, {"baseline": base["run_id"]})
    finish_run(cur, db, run_id, not plan.failed, {"executed": plan.done, "failed": plan.failed, "ledger_row": found})
    if not plan.failed:
        cur.execute(f"UPDATE {db}.{LEDGER}.CONSOLIDATION_OBJECT SET STATUS = 'ROLLED_BACK', UPDATED_AT = "
                    "CURRENT_TIMESTAMP() WHERE RUN_ID = %s AND METHOD = 'RENAME'", (base["run_id"],))
    path = write_json(args, "rollback", {"run_id": run_id, "executed": plan.done, "failed": plan.failed})
    print(f"rollback {run_id}: {len(plan.done)} statement(s), {len(plan.failed)} failed; written {path}")
    con.close()
    sys.exit(1 if plan.failed else 0)


if __name__ == "__main__":
    main()
