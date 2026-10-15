"""Runtime objects of the consolidation (what V061 and the deploy cannot do in SQL files).

    python -m infrastructure.consolidation.runtime freeze --connection <c> --database <DB> [--rehearsal] [--execute]
    python -m infrastructure.consolidation.runtime apply  --connection <c> --database <DB> [--rehearsal] [--execute]
    python -m infrastructure.consolidation.runtime resume --connection <c> --database <DB> --execute

freeze  P3: record the state of every task in the platform database and suspend the running ones.
apply   P5, after `deploy_snowflake.py --consolidate`: tasks get a twin in CORE (clone, then the body re-pointed at CORE;
        Snowflake cannot rename a task), the network rule is re-created in CORE, a read-only compatibility view
        takes each moved table's old name with exactly the SELECT grants the table had, the old Cortex Search services are suspended and USAGE on the old procedures
        is revoked. Nothing is dropped: old tasks stay suspended and the old rule stays. Existing secrets, git
        repository clones and dbt projects stay where they are (rows store their names); new ones go to CORE.
resume  P7: resume the CORE twins of the tasks that were running before the freeze. Never on a rehearsal clone.

Every command prints its statements; nothing runs without --execute. The external access integration (account level)
is never altered here: `apply` prints the statement for its owner.
"""

from __future__ import annotations

import sys
from typing import List

from infrastructure.consolidation.common import (LEDGER, PLATFORM_SCHEMA, REHEARSAL_SUFFIX,
                                                 baseline_objects, connect, database_name, finish_run, latest_run,
                                                 map_fqn, parser, q, show, start_run, try_q, write_json)
from infrastructure.consolidation.generate import NO_COMPAT_VIEW

# Read-only views on the old names for the 30-day transition; only `runtime apply` creates them (never a deploy, so a
# new environment has none), `rollback` and the cleanup remove them. Row access policies of the tables still apply.
COMPAT_COMMENT = "Compatibility view (read-only) of"


class Plan:
    def __init__(self, cur, execute: bool):
        self.cur, self.execute, self.done, self.failed = cur, execute, [], []

    def run(self, sql: str, why: str) -> bool:
        print(f"-- {why}\n{sql};")
        if not self.execute:
            return True
        try:
            self.cur.execute(sql)
            self.done.append(sql)
            return True
        except Exception as exc:
            self.failed.append({"sql": sql, "error": str(exc)[:400]})
            print(f"   FAILED: {str(exc)[:300]}")
            return False


def freeze(args, cur, db: str) -> int:
    tasks = [{"name": f"{t['database_name']}.{t['schema_name']}.{t['name']}", "state": t["state"],
              "schedule": t.get("schedule")} for t in show(cur, "TASKS", db)]
    plan = Plan(cur, args.execute)
    for t in tasks:
        if t["state"] == "started":
            plan.run(f"ALTER TASK {t['name']} SUSPEND", "freeze: suspend a running platform task")
    print("-- also stop the API and the worker, and announce the window (not done by this script)")
    if not args.execute:
        print(f"dry run: {len(tasks)} task(s), {sum(t['state'] == 'started' for t in tasks)} running")
        return 0
    run_id = start_run(cur, db, "FREEZE", args.rehearsal, {"tasks": tasks})
    finish_run(cur, db, run_id, not plan.failed, {"tasks": tasks, "failed": plan.failed})
    print(f"freeze {run_id}: {len(tasks)} task(s) recorded, {len(plan.done)} suspended, {len(plan.failed)} failed")
    return 1 if plan.failed else 0


def task_body(definition: str, source_db: str, db: str) -> str:
    body = map_fqn(definition or "", db)
    return body.replace(f"{source_db}.", f"{db}.") if source_db and source_db != db else body


def apply(args, cur, db: str) -> int:
    base = latest_run(cur, db, "BASELINE")
    assert base, "no completed baseline"
    objects = baseline_objects(cur, db, base["run_id"])
    plan = Plan(cur, args.execute)
    notes: List[str] = []
    existing_tasks = {f"{t['database_name']}.{t['schema_name']}.{t['name']}" for t in show(cur, "TASKS", db)}

    for o in objects:
        b, kind = o["baseline"] or {}, o["object_type"]
        if kind == "TASK":
            if o["new_fqn"] not in existing_tasks:
                plan.run(f"CREATE TASK IF NOT EXISTS {o['new_fqn']} CLONE {o['old_fqn']}",
                         "task twin in CORE (renaming a task is not supported); clones are created suspended")
            plan.run(f"ALTER TASK {o['new_fqn']} MODIFY AS {task_body(b.get('definition'), args.source_database, db)}",
                     "point the twin's body at CORE")
            plan.run(f"ALTER TASK {o['old_fqn']} SUSPEND", "keep the old task suspended (not dropped)")
        elif kind == "NETWORK RULE":
            d = b.get("describe") or {}
            values = ", ".join(f"'{v.strip()}'" for v in str(d.get("value_list") or "").split(",") if v.strip())
            plan.run(f"CREATE NETWORK RULE IF NOT EXISTS {o['new_fqn']} TYPE = {d.get('type')} MODE = {d.get('mode')} "
                     f"VALUE_LIST = ({values})", "network rule in CORE (no RENAME for network rules); the old one stays")
            notes.append(f"Owner of the external access integration: replace {o['old_fqn']} by {o['new_fqn']} in "
                         "ALLOWED_NETWORK_RULES (ALTER EXTERNAL ACCESS INTEGRATION <name> SET ALLOWED_NETWORK_RULES = "
                         "(...)). Not done by this script; the old rule keeps working meanwhile.")
        elif kind in ("TABLE", "VIEW") and o["method"] == "RENAME":
            schema, name = o["old_fqn"].split(".")[1:3]
            if (schema, name) in NO_COMPAT_VIEW:
                continue
            plan.run(f"CREATE OR REPLACE VIEW {o['old_fqn']} COPY GRANTS COMMENT = '{COMPAT_COMMENT} {o['new_fqn']}' "
                     f"AS SELECT * FROM {o['new_fqn']}", "compatibility view (read-only, transition only) on the old name")
            for g in b.get("grants") or []:
                if g["privilege"] == "SELECT" and g["granted_to"] in ("DATABASE_ROLE", "ROLE"):
                    to = "DATABASE ROLE" if g["granted_to"] == "DATABASE_ROLE" else "ROLE"
                    plan.run(f"GRANT SELECT ON VIEW {o['old_fqn']} TO {to} {g['grantee']}",
                             "compatibility view: the same readers as the table had")
        elif kind == "CORTEX SEARCH SERVICE":
            plan.run(f"ALTER CORTEX SEARCH SERVICE {o['old_fqn']} SUSPEND INDEXING", "old service: stop refreshing")
            plan.run(f"ALTER CORTEX SEARCH SERVICE {o['old_fqn']} SUSPEND SERVING", "old service: stop serving")
        elif kind == "PROCEDURE":
            for g in b.get("grants") or []:
                if g["privilege"] == "USAGE" and g["granted_to"] in ("DATABASE_ROLE", "ROLE"):
                    to = "DATABASE ROLE" if g["granted_to"] == "DATABASE_ROLE" else "ROLE"
                    plan.run(f"REVOKE USAGE ON PROCEDURE {o['old_fqn']} FROM {to} {g['grantee']}",
                             "old procedure: nothing calls the pre-V061 copy by mistake (kept for rollback)")
        elif kind in ("SECRET", "DBT PROJECT", "GIT REPOSITORY"):
            notes.append(f"{kind} {o['old_fqn']} stays where it is; the rows that name it stay valid.")

    if args.test_dbt_rename:
        assert db.endswith(REHEARSAL_SUFFIX), "--test-dbt-rename only on a rehearsal clone"
        notes.extend(test_dbt_rename(cur, db, objects, plan))

    for n in notes:
        print(f"note: {n}")
    if not args.execute:
        print("dry run: nothing executed (pass --execute)")
        return 0
    run_id = start_run(cur, db, "RUNTIME", args.rehearsal, {})
    finish_run(cur, db, run_id, not plan.failed, {"executed": plan.done, "failed": plan.failed, "notes": notes})
    write_json(args, "runtime", {"run_id": run_id, "executed": plan.done, "failed": plan.failed, "notes": notes})
    print(f"runtime {run_id}: {len(plan.done)} statement(s), {len(plan.failed)} failed")
    return 1 if plan.failed else 0


def test_dbt_rename(cur, db: str, objects, plan: Plan) -> List[str]:
    """Rehearsal only: does a dbt project keep its versions when renamed across schemas? (docs do not say)."""
    projects = [o for o in objects if o["object_type"] == "DBT PROJECT"]
    if not projects:
        return ["dbt rename test: no dbt project in the old schemas"]
    o = projects[0]
    before = sorted(str(v.get("name") or v.get("version")) for v in try_q(cur, f"SHOW VERSIONS IN DBT PROJECT {o['old_fqn']}"))
    target = o["old_fqn"].rsplit(".", 2)[0] + f".{PLATFORM_SCHEMA}." + o["old_fqn"].rsplit(".", 1)[1]
    if not plan.run(f"ALTER DBT PROJECT {o['old_fqn']} RENAME TO {target}", "rehearsal: dbt project rename test"):
        return [f"dbt rename test: rename failed for {o['old_fqn']}"]
    if not plan.execute:
        return ["dbt rename test: dry run"]
    after = sorted(str(v.get("name") or v.get("version")) for v in try_q(cur, f"SHOW VERSIONS IN DBT PROJECT {target}"))
    return [f"dbt rename test {o['old_fqn']} -> {target}: versions before {before}, after {after}, "
            f"{'KEPT' if before == after else 'CHANGED'}"]


def resume(args, cur, db: str) -> int:
    assert not db.endswith(REHEARSAL_SUFFIX) and not args.rehearsal, "never resume tasks on a rehearsal clone"
    base = latest_run(cur, db, "BASELINE")
    assert base, "no completed baseline"
    plan = Plan(cur, args.execute)
    for o in baseline_objects(cur, db, base["run_id"]):
        if o["object_type"] == "TASK" and (o["baseline"] or {}).get("state_before_freeze") == "started":
            plan.run(f"ALTER TASK {o['new_fqn']} RESUME", "resume: the task was running before the freeze")
    started = [t for t in show(cur, "TASKS", db) if t["schema_name"] == PLATFORM_SCHEMA and t["state"] == "started"]
    print(f"{len(plan.done)} resumed, {len(plan.failed)} failed; CORE tasks running now: {len(started)}")
    return 1 if plan.failed else 0


def main() -> None:
    p = parser(__doc__)
    p.add_argument("command", choices=("freeze", "apply", "resume"))
    p.add_argument("--execute", action="store_true", help="run the statements (default: print them)")
    p.add_argument("--rehearsal", action="store_true")
    p.add_argument("--source-database", default="",
                   help="rehearsal: the database the clone was made from (task bodies name it)")
    p.add_argument("--test-dbt-rename", action="store_true", help="rehearsal: rename one dbt project and compare versions")
    args = p.parse_args()
    db = database_name(args)
    if args.rehearsal:
        assert db.endswith(REHEARSAL_SUFFIX), f"--rehearsal needs a *{REHEARSAL_SUFFIX} database"
    con = connect(args)
    try:
        cur = con.cursor()
        assert q(cur, f"SHOW TABLES LIKE 'CONSOLIDATION_RUN' IN SCHEMA {db}.{LEDGER}"), "V060 is not applied"
        code = {"freeze": freeze, "apply": apply, "resume": resume}[args.command](args, cur, db)
    finally:
        con.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
