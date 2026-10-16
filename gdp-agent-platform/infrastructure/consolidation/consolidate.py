"""The one-time consolidation of an existing environment, in one Snowflake session (one sign-in).

    python -m infrastructure.consolidation.consolidate --connection <c> --database DEV_AI_PLATFORM [--warehouse DBT_WH]

Stop the API and the worker first. Steps, each stopping the run on failure (README.md, "Existing environments"):
V060, freeze (suspend running tasks), baseline, safety net (Time Travel retention raised, zero-copy backup clone with
internal stages, files of non-directory stages copied), V061 (the move), data verification against the backup, full
deploy, runtime apply, functional verification, and finally the CORE task twins of the tasks that were running are
resumed. Nothing is dropped. If a step fails after the move, run `python -m infrastructure.consolidation.rollback`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from infrastructure.consolidation import baseline, common, runtime, verify
from infrastructure.consolidation.common import IDENT, LEGACY_SCHEMAS, PLATFORM_SCHEMA, ROOT, q, show, try_q
from infrastructure.consolidation.rehearse import Shared, run_script


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--connection", required=True)
    p.add_argument("--database", required=True)
    p.add_argument("--warehouse", default="DBT_WH")
    p.add_argument("--role")
    p.add_argument("--retention-days", type=int, default=14)
    p.add_argument("--out", default=str(ROOT / "consolidation_runs"))
    p.add_argument("--no-compat-views", action="store_true", help="leave nothing under the old schema names")
    p.add_argument("--start-at", choices=("deploy",), help="continue after a data verification that was reviewed")
    args = p.parse_args()
    db = args.database.upper()
    assert IDENT.match(db) and not db.endswith(common.REHEARSAL_SUFFIX), "use rehearse.py for *_REHEARSAL clones"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup = f"{db}_PRECONSOLIDATION_{stamp}"
    report: Dict[str, Any] = {"database": db, "backup": backup, "steps": []}
    started = time.time()
    con = common.connect(argparse.Namespace(connection=args.connection, role=args.role, warehouse=args.warehouse))
    shared, cur = Shared(con), con.cursor()
    cur.execute(f"USE WAREHOUSE {args.warehouse}")
    report["session"] = q(cur, "SELECT CURRENT_USER() AS U, CURRENT_ROLE() AS R")[0]
    print(f"connected as {report['session']}; database {db}", flush=True)

    def finish(ok: bool) -> None:
        report["ok"], report["minutes"] = ok, round((time.time() - started) / 60, 1)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{db}_consolidation_report.json"
        path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print(f"\nreport: {path}")
        for s in report["steps"]:
            print(f"  {'OK    ' if s['ok'] else 'FAILED'} {s['step']} ({s['seconds']}s)")

    def step(name: str, fn) -> Any:
        t0 = time.time()
        print(f"\n===== {name}", flush=True)
        try:
            result = fn()
            ok = not (isinstance(result, int) and not isinstance(result, bool) and result != 0) and result is not False
        except Exception as exc:
            result, ok = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}", False
        report["steps"].append({"step": name, "ok": ok, "seconds": round(time.time() - t0, 1),
                                "result": result if isinstance(result, int) else common.jsonable(result)})
        print(f"----- {name}: {'OK' if ok else 'FAILED'} ({round(time.time() - t0, 1)}s)", flush=True)
        if not ok:
            print(f"\nSTOPPED at: {name}. Nothing after it ran. If V061 already ran, rollback.py restores every name.")
            finish(False)
            sys.exit(1)
        return result

    from infrastructure.deploy_snowflake import apply_from_session

    def deploy(**kw):
        log = apply_from_session(shared, db, args.warehouse, **kw)
        for line in log:
            print(f"  {line}")
        return log

    base = ["--connection", args.connection, "--database", db, "--out", args.out]
    compat = ["--no-compat-views"] if args.no_compat_views else []
    if args.start_at == "deploy":
        report["resumed_at"] = "deploy"
        return finish_from_deploy(step, deploy, base, compat, shared, finish, con)
    step("deploy --through 060 (manifest tables)", lambda: deploy(through="060"))
    step("freeze: suspend running tasks", lambda: run_script(runtime, ["freeze", *base, "--execute"], shared))
    step("baseline", lambda: run_script(baseline, base, shared))

    def safety_net():
        out: Dict[str, Any] = {}
        before = try_q(cur, f"SHOW PARAMETERS LIKE 'DATA_RETENTION_TIME_IN_DAYS' IN DATABASE {db}")
        out["retention_before"] = before[0].get("value") if before else None
        try:
            cur.execute(f"ALTER DATABASE {db} SET DATA_RETENTION_TIME_IN_DAYS = {int(args.retention_days)}")
            out["retention_now"] = args.retention_days
        except Exception as exc:
            out["retention_now"] = f"unchanged ({str(exc)[:200]})"
        assert not q(cur, f"SHOW DATABASES LIKE '{backup}'"), f"{backup} already exists"
        for s in show(cur, "STAGES", db):
            if str(s.get("directory_enabled", "")).upper() in ("Y", "TRUE"):
                cur.execute(f"ALTER STAGE {db}.{s['schema_name']}.{s['name']} REFRESH")
        cur.execute(f"CREATE DATABASE {backup} CLONE {db} INCLUDE INTERNAL STAGES "
                    "COMMENT = 'Backup taken before the schema consolidation (V061); keep until sign-off'")
        stages = []
        for s in show(cur, "STAGES", db):
            if str(s.get("type", "")).upper() != "INTERNAL" or s.get("schema_name") not in (*LEGACY_SCHEMAS, PLATFORM_SCHEMA):
                continue
            name = f"{s['schema_name']}.{s['name']}"
            cur.execute(f"COPY FILES INTO @{backup}.{name} FROM @{db}.{name}")
            n_src, n_bak = len(q(cur, f"LIST @{db}.{name}")), len(q(cur, f"LIST @{backup}.{name}"))
            assert n_bak >= n_src, f"{name}: backup has {n_bak} files, source {n_src}"
            stages.append({"stage": name, "files": n_src, "backup_files": n_bak})
        out["stages"] = stages
        return out

    step("safety net: retention, backup clone, stage files", safety_net)
    step("V061: move every platform object into CORE", lambda: deploy(consolidate=True, through="061"))
    step("verify data (rows, hashes, ids, files, grants vs backup)",
         lambda: run_script(verify, ["data", *base, "--backup-database", backup], shared))
    finish_from_deploy(step, deploy, base, compat, shared, finish, con)


def finish_from_deploy(step, deploy, base, compat, shared, finish, con) -> None:
    step("deploy (procedures, views, Cortex Search, agent, seed)", lambda: deploy(consolidate=True))
    step("runtime apply (task twins, network rule, compatibility views)",
         lambda: run_script(runtime, ["apply", *base, "--execute", *compat], shared))
    step("verify functional", lambda: run_script(verify, ["functional", *base, *compat], shared))
    step("resume the tasks that were running", lambda: run_script(runtime, ["resume", *base, "--execute"], shared))
    finish(True)
    con.close()


if __name__ == "__main__":
    main()
