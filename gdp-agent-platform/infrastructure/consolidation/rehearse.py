"""P0b: rehearse the whole consolidation on a zero-copy clone, in one Snowflake session (one sign-in).

    python -m infrastructure.consolidation.rehearse --connection <c> --source DEV_AI_PLATFORM [--warehouse DBT_WH]

Never writes to --source: it only reads it (CLONE, COPY FILES ... FROM, LIST). Everything else happens in
<SOURCE>_REHEARSAL and a second clone of that, <SOURCE>_RH_BACKUP_REHEARSAL, used as the backup for the row-by-row
comparison. Steps: clone, copy staged files, V060, freeze, baseline, backup clone, V061, verify data, full deploy,
runtime apply (with the dbt rename test), verify functional, then rollback on the clone and a check that every object
is back under its old name with no row lost. A report is written to consolidation_runs/. The clones are left in place
for review; drop them when done (`DROP DATABASE <SOURCE>_REHEARSAL`, `..._RH_BACKUP_REHEARSAL`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

from infrastructure.consolidation import baseline, common, rollback, runtime, verify
from infrastructure.consolidation.common import IDENT, LEGACY_SCHEMAS, PLATFORM_SCHEMA, ROOT, q, show, try_q


class Shared:
    """The one connection, handed to every script; their close() is a no-op."""

    def __init__(self, con):
        self.con = con

    def cursor(self):
        return self.con.cursor()

    def execute_string(self, *a, **k):
        return self.con.execute_string(*a, **k)

    def close(self):
        pass


def run_script(module, argv: List[str], shared) -> int:
    module.connect = lambda args: shared
    saved = sys.argv
    sys.argv = [module.__name__] + argv
    try:
        module.main()
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv = saved


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--connection", required=True)
    p.add_argument("--source", required=True, help="the platform database to clone (read only)")
    p.add_argument("--warehouse", default="DBT_WH")
    p.add_argument("--role")
    p.add_argument("--out", default=str(ROOT / "consolidation_runs"))
    p.add_argument("--reuse", action="store_true", help="continue on an existing rehearsal clone (skip the clone step)")
    args = p.parse_args()
    src = args.source.upper()
    assert IDENT.match(src), "invalid source database"
    db, backup = f"{src}_REHEARSAL", f"{src}_RH_BACKUP_REHEARSAL"
    report: Dict[str, Any] = {"source": src, "rehearsal": db, "backup": backup, "steps": []}
    started = time.time()

    con = common.connect(argparse.Namespace(connection=args.connection, role=args.role, warehouse=args.warehouse))
    shared = Shared(con)
    cur = con.cursor()

    def step(name: str, fn) -> Any:
        t0 = time.time()
        print(f"\n===== {name}", flush=True)
        try:
            result = fn()
            ok = not (isinstance(result, int) and not isinstance(result, bool) and result != 0) and result is not False
        except Exception as exc:
            result, ok = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}", False
        report["steps"].append({"step": name, "ok": ok, "seconds": round(time.time() - t0, 1),
                                "result": common.jsonable(result) if not isinstance(result, int) else result})
        print(f"----- {name}: {'OK' if ok else 'FAILED'} ({round(time.time() - t0, 1)}s)", flush=True)
        if not ok:
            finish(report, args, started)
            sys.exit(1)
        return result

    who = q(cur, "SELECT CURRENT_USER() AS U, CURRENT_ROLE() AS R, CURRENT_WAREHOUSE() AS W")[0]
    report["session"] = who
    print(f"connected as {who}", flush=True)
    cur.execute(f"USE WAREHOUSE {args.warehouse}")

    def clone():
        if args.reuse:
            assert q(cur, f"SHOW DATABASES LIKE '{db}'"), f"{db} does not exist"
            return "reused"
        assert not q(cur, f"SHOW DATABASES LIKE '{db}'"), f"{db} already exists: drop it or pass --reuse"
        try:
            cur.execute(f"CREATE DATABASE {db} CLONE {src} INCLUDE INTERNAL STAGES "
                        "COMMENT = 'Consolidation rehearsal (P0b): disposable'")
            return "cloned with internal stages"
        except Exception as exc:
            cur.execute(f"CREATE DATABASE {db} CLONE {src} COMMENT = 'Consolidation rehearsal (P0b): disposable'")
            return f"cloned without INCLUDE INTERNAL STAGES ({str(exc)[:200]}); files are copied next"

    step("clone source into rehearsal database", clone)

    def copy_files():
        out = []
        for s in show(cur, "STAGES", src):
            if str(s.get("type", "")).upper() != "INTERNAL" or s.get("schema_name") not in (*LEGACY_SCHEMAS, PLATFORM_SCHEMA):
                continue
            name = f"{s['schema_name']}.{s['name']}"
            before = len(try_q(cur, f"LIST @{db}.{name}"))
            cur.execute(f"COPY FILES INTO @{db}.{name} FROM @{src}.{name}")
            if str(s.get("directory_enabled", "")).upper() in ("Y", "TRUE"):
                cur.execute(f"ALTER STAGE {db}.{name} REFRESH")
            source_n, clone_n = len(q(cur, f"LIST @{src}.{name}")), len(q(cur, f"LIST @{db}.{name}"))
            assert clone_n >= source_n, f"{name}: {clone_n} files in the clone, {source_n} in the source"
            out.append({"stage": name, "source_files": source_n, "clone_files_before_copy": before, "clone_files": clone_n})
        return out

    if not args.reuse:
        step("copy staged files into the clone", copy_files)

    from infrastructure.deploy_snowflake import apply_from_session

    def deploy(**kw):
        log = apply_from_session(shared, db, args.warehouse, rehearsal=True, **kw)
        for line in log:
            print(f"  {line}")
        return log

    step("deploy --through 060 (manifest tables)", lambda: deploy(through="060"))
    base = ["--connection", args.connection, "--database", db, "--out", args.out]
    step("runtime freeze", lambda: run_script(runtime, ["freeze", *base, "--rehearsal", "--execute"], shared))
    step("baseline", lambda: run_script(baseline, [*base, "--rehearsal"], shared))

    def backup_clone():
        assert not q(cur, f"SHOW DATABASES LIKE '{backup}'"), f"{backup} already exists"
        cur.execute(f"CREATE DATABASE {backup} CLONE {db} COMMENT = 'Consolidation rehearsal backup: disposable'")

    step("backup clone of the rehearsal database", backup_clone)
    step("deploy --consolidate --through 061 (V061: the move)", lambda: deploy(consolidate=True, through="061"))
    step("verify data", lambda: run_script(verify, ["data", *base, "--rehearsal", "--backup-database", backup], shared))
    step("deploy --consolidate (procedures, views, search, agent, seed)", lambda: deploy(consolidate=True))
    step("runtime apply", lambda: run_script(runtime, ["apply", *base, "--rehearsal", "--execute",
                                                          "--source-database", src, "--test-dbt-rename"], shared))
    step("verify functional", lambda: run_script(verify, ["functional", *base, "--rehearsal"], shared))
    step("rollback on the rehearsal clone", lambda: run_script(rollback, [*base, "--rehearsal", "--execute"], shared))

    def after_rollback():
        objects = common.baseline_objects(cur, db, common.latest_run(cur, db, "BASELINE")["run_id"])
        problems = []
        for o in objects:
            if o["object_type"] != "TABLE" or o["method"] != "RENAME":
                continue
            found = try_q(cur, f"SELECT COUNT(*) AS N FROM {o['old_fqn']}")
            if not found:
                problems.append(f"{o['old_fqn']}: not back")
            elif int(found[0]["n"]) < int(o["baseline"]["row_count"]):
                problems.append(f"{o['old_fqn']}: {found[0]['n']} rows, baseline {o['baseline']['row_count']}")
            if try_q(cur, f"SELECT 1 FROM {o['new_fqn']} LIMIT 1"):
                problems.append(f"{o['new_fqn']}: still present in CORE")
        assert not problems, problems
        return {"tables_back": sum(1 for o in objects if o["object_type"] == "TABLE" and o["method"] == "RENAME")}

    step("check: everything back under its old name, no row lost", after_rollback)
    finish(report, args, started)


def finish(report, args, started) -> None:
    report["minutes"] = round((time.time() - started) / 60, 1)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{report['rehearsal']}_rehearsal_report.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"\nreport: {path}")
    for s in report["steps"]:
        print(f"  {'OK    ' if s['ok'] else 'FAILED'} {s['step']} ({s['seconds']}s)")


if __name__ == "__main__":
    main()
