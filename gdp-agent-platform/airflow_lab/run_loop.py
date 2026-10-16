"""Run the Airflow failure-resolution lab end to end, N passes, and check every DAG ends where it should.

    python airflow_lab/run_loop.py --passes 2          # DEV_AI_PLATFORM by default
    python airflow_lab/run_loop.py --teardown          # remove every lab object again

Needs: the WSL Airflow serving http://localhost:8080 with airflow_lab/dags loaded and the `lab_snowflake` connection;
the platform worker running against the same database with the local Airflow environment `local-lab` (KIND AIRFLOW)
enabled; one Snowflake sign-in (role SYSADMIN, or LAB_ROLE). Everything lives in the platform's one schema, CORE: the
lab only creates and resets CORE.LAB_* objects, the FIX_LAB_* procedures and the lab DAGs' scopes.

Each pass:
  1. reset the lab (airflow_lab/reset.sql) and switch automatic resolution off, so the failures queue up;
  2. trigger every lab DAG; wait for Airflow and for the worker to queue each failure;
  3. publish the known fixes a reviewer would have written after seeing these failures once (only those missing:
     on the second pass they already exist), author LAB_AUTHOR, published by LAB_REVIEWER (four eyes);
  4. switch automatic resolution on; wait for every failure to reach its state;
  5. decide the duplicate-keys approval as LAB_APPROVER (approve on pass 1, reject on pass 2);
  6. learning loop: the invalid-column failure was a miss (RCA + ticket); publish the fix written from its incident and
     trigger the DAG again, which is then fixed automatically;
  7. wait for the platform to clear the fixed tasks and for Airflow's reruns to succeed; check everything.
Results are printed as a table and written to airflow_lab/last_run.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))
LAB = Path(__file__).resolve().parent
ENV_ID = "local-lab"
DAGS = ["lab_invalid_identifier", "lab_invalid_column", "lab_duplicate_keys", "lab_multi_step", "lab_flaky",
        "lab_fix_breaks", "lab_ticket_only", "lab_unscoped"]
FIXES = {   # what a reviewer publishes after the first failure of each DAG (lab_invalid_column is learned later)
    "lab_invalid_identifier": {"fix_type": "PROCEDURE", "procedure_fqn": "CORE.FIX_LAB_CREATE_ORDERS_STG", "approval_required": False},
    "lab_duplicate_keys": {"fix_type": "PROCEDURE", "procedure_fqn": "CORE.FIX_LAB_DEDUP_ORDERS_IN", "approval_required": True},
    "lab_multi_step": {"fix_type": "SKILL", "skill": "lab_export_setup", "approval_required": False},
    "lab_fix_breaks": {"fix_type": "PROCEDURE", "procedure_fqn": "CORE.FIX_LAB_BROKEN", "approval_required": False},
}
LEARNED = {"lab_invalid_column": {"fix_type": "PROCEDURE", "procedure_fqn": "CORE.FIX_LAB_ADD_AMOUNT_USD", "approval_required": False}}
SKILL_STEPS = [{"step_no": 1, "action": "CORE.FIX_LAB_CREATE_EXPORT_STAGE", "note": "the export stage"},
               {"step_no": 2, "action": "CORE.FIX_LAB_CREATE_EXPORT_TABLE", "note": "the export table"}]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


class ConnDb:
    """The platform's Db interface over a connector connection (%s binds, lower-case keys)."""

    def __init__(self, con, user: str = "LAB"):
        self.con, self.user = con, user

    def query(self, sql, params=()):
        cur = self.con.cursor()
        try:
            cur.execute(sql, tuple(params) if params else None)
            if cur.description is None:
                return []
            cols = [c[0].lower() for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()

    def execute(self, sql, params=()):
        self.query(sql, params)

    def execute_count(self, sql, params=()):
        cur = self.con.cursor()
        try:
            cur.execute(sql, tuple(params) if params else None)
            return int(cur.rowcount or 0)
        finally:
            cur.close()


def airflow_password() -> str:
    """The local Airflow's generated simple-auth password (a local test credential; never printed)."""
    out = subprocess.run(["wsl", "-e", "bash", "-lc", "cat ~/airflow/simple_auth_manager_passwords.json.generated"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["admin"]


def wait(what: str, check, timeout: int = 900, every: int = 15):
    start = time.time()
    while True:
        value = check()
        if value:
            log(f"{what}: done in {round(time.time() - start)} s")
            return value
        if time.time() - start > timeout:
            raise TimeoutError(f"{what}: not done after {timeout} s")
        time.sleep(every)


class Lab:
    def __init__(self, db: ConnDb, af, database: str):
        self.db, self.af, self.database = db, af, database
        self.results = []

    # ------------------------------------------------------------ Snowflake side
    def sql_file(self, name: str) -> None:
        text = (LAB / name).read_text(encoding="utf-8").replace("{{database}}", self.database)
        for _ in self.db.con.execute_string(text, remove_comments=True):
            pass

    def auto(self, on: bool) -> None:
        self.db.execute("UPDATE CORE.CONTROL_PREFERENCE SET AUTO_RESOLVE_ENABLED = %s, AIRFLOW_CLEAR_ENABLED = TRUE, "
                        "UPDATED_BY = 'airflow_lab', UPDATED_AT = CURRENT_TIMESTAMP() WHERE IS_ACTIVE", (on,))

    def failures(self, run_ids):
        if not run_ids:
            return {}
        marks = ", ".join(["%s"] * len(run_ids))
        rows = self.db.query(f"""SELECT FAILURE_ID, DAG_ID, RUN_ID, STATUS, INCIDENT_ID, ISSUE_ID, SEARCH_SCORE, CLEAR_STATE,
                                        CLEAR_DETAIL, DETAIL, RCA:mode::VARCHAR AS RCA_MODE, RCA:cause::VARCHAR AS CAUSE
                                   FROM CORE.FAILURE_EVENT WHERE ENV_ID = %s AND RUN_ID IN ({marks})
                                  ORDER BY CREATED_AT""", (ENV_ID, *run_ids))
        return {r["dag_id"]: r for r in rows}   # the latest failure per DAG among these runs

    def published(self, dag_id: str):
        found = self.db.query("SELECT ISSUE_ID FROM CORE.KNOWN_ISSUE WHERE DAG_ID = %s AND STATUS = 'PUBLISHED' LIMIT 1", (dag_id,))
        return found[0]["issue_id"] if found else None

    def publish_from(self, dag_id: str, failure, spec) -> str:
        from services.resolve import publish

        draft = publish.draft_from_incident(self.db, failure["incident_id"])
        body = {**draft, "title": f"Lab: {dag_id} known fix", "fix_type": spec["fix_type"],
                "approval_required": spec["approval_required"], "owner": "airflow_lab",
                "procedure_fqn": spec.get("procedure_fqn"), "notes": "Published by the Airflow lab loop."}
        if spec["fix_type"] == "SKILL":
            found = self.db.query("SELECT SKILL_ID FROM CORE.FIX_SKILL WHERE SKILL_NAME = %s", (spec["skill"],))
            skill = found[0]["skill_id"] if found else publish.save_skill(
                self.db, {"skill_name": spec["skill"], "description": "Lab: create the export stage, then the export table",
                          "steps": SKILL_STEPS}, "LAB_AUTHOR")["skill_id"]
            body["skill_id"] = skill
        created = publish.create(self.db, body, "LAB_AUTHOR")
        out = publish.publish(self.db, created["issue_id"], "LAB_REVIEWER")
        log(f"published {out['issue_id']} for {dag_id} (search {out.get('search_refresh')})")
        return out["issue_id"]

    # ------------------------------------------------------------ Airflow side
    def trigger(self, dag_id: str, tag: str) -> str:
        self.af.invoke("PATCH", f"/dags/{dag_id}", body={"is_paused": False})
        run_id = f"{tag}__{dag_id}__{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
        self.af.invoke("POST", f"/dags/{dag_id}/dagRuns", body={"dag_run_id": run_id, "logical_date": None, "conf": {}})
        return run_id

    def run_state(self, dag_id: str, run_id: str) -> str:
        return str((self.af.dag_run(dag_id, run_id) or {}).get("state") or "")

    # ------------------------------------------------------------ one pass
    def one_pass(self, n: int) -> None:
        tag = f"lab{n}_{datetime.now(timezone.utc).strftime('%m%d%H%M')}"
        log(f"===== pass {n} ({tag})")
        self.sql_file("reset.sql")
        self.auto(False)
        runs = {d: self.trigger(d, tag) for d in DAGS}
        wait("Airflow finished every lab run",
             lambda: all(self.run_state(d, r) in ("success", "failed") for d, r in runs.items()), timeout=900)
        failing = [d for d in DAGS if d != "lab_flaky"]
        wait("the worker queued every failure", lambda: set(failing) <= set(self.failures(list(runs.values()))), timeout=600)
        queued = self.failures(list(runs.values()))
        for dag_id, spec in FIXES.items():
            if not self.published(dag_id):
                self.publish_from(dag_id, queued[dag_id], spec)
        self.auto(True)
        expect = {"lab_invalid_identifier": "RESOLVED", "lab_duplicate_keys": "AWAITING_APPROVAL", "lab_multi_step": "RESOLVED",
                  "lab_fix_breaks": "TICKETED", "lab_ticket_only": "TICKETED", "lab_unscoped": "OUT_OF_SCOPE",
                  "lab_invalid_column": "RESOLVED" if self.published("lab_invalid_column") else "TICKETED"}
        wait("every failure reached its state",
             lambda: all(self.failures(list(runs.values())).get(d, {}).get("status") == s for d, s in expect.items()), timeout=900)

        from services.resolve import engine
        dup = self.failures(list(runs.values()))["lab_duplicate_keys"]
        approval = self.db.query("SELECT APPROVAL_ID FROM CORE.APPROVAL_REQUEST WHERE FAILURE_ID = %s AND STATUS = 'OPEN'",
                                 (dup["failure_id"],))[0]["approval_id"]
        approve = n % 2 == 1
        engine.decide_approval(self.db, approval, "LAB_APPROVER", approve, "lab: looks right" if approve else "lab: not during month end", False)
        log(f"duplicate-keys fix {'approved' if approve else 'rejected'} as LAB_APPROVER")
        wait("the worker acted on the approval",
             lambda: self.failures(list(runs.values()))["lab_duplicate_keys"]["status"] == ("RESOLVED" if approve else "REJECTED"), timeout=600)

        relearned = None
        if expect["lab_invalid_column"] == "TICKETED":
            col = self.failures(list(runs.values()))["lab_invalid_column"]
            self.publish_from("lab_invalid_column", col, LEARNED["lab_invalid_column"])
            relearned = self.trigger("lab_invalid_column", tag + "_learned")
            wait("the learned fix fixed the next failure",
                 lambda: self.failures([relearned]).get("lab_invalid_column", {}).get("status") == "RESOLVED", timeout=900)

        fixed = [d for d in ("lab_invalid_identifier", "lab_multi_step") + (("lab_duplicate_keys",) if approve else ())]
        fixed_runs = {d: runs[d] for d in fixed}
        fixed_runs["lab_invalid_column"] = relearned or runs["lab_invalid_column"]
        wait("the platform cleared the fixed tasks and Airflow's reruns succeeded",
             lambda: all(self.run_state(d, r) == "success" for d, r in fixed_runs.items()), timeout=900)

        final = {**self.failures(list(runs.values())), **({"lab_invalid_column": self.failures([relearned])["lab_invalid_column"]}
                                                       if relearned else {})}
        want = {**expect, "lab_duplicate_keys": "RESOLVED" if approve else "REJECTED", "lab_invalid_column": "RESOLVED"}
        for d in DAGS:
            f = final.get(d)
            airflow = self.run_state(d, fixed_runs.get(d, runs[d]))
            if d == "lab_flaky":
                ok = f is None and airflow == "success"
                self.results.append({"pass": n, "dag": d, "want": "not queued; Airflow retry succeeds", "got": f and f["status"],
                                     "airflow": airflow, "ok": ok})
                continue
            ok = bool(f) and f["status"] == want[d]
            if want[d] == "RESOLVED":
                ok = ok and f["clear_state"] == "DONE" and airflow == "success"
            self.results.append({"pass": n, "dag": d, "want": want[d], "got": f and f["status"], "airflow": airflow,
                                 "clear": f and f["clear_state"], "rca": f and f["rca_mode"], "score": f and f["search_score"],
                                 "detail": f and (f["detail"] or "")[:120], "ok": ok})
        log(f"pass {n}: {sum(r['ok'] for r in self.results if r['pass'] == n)}/{len(DAGS)} DAGs as expected")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--database", default="DEV_AI_PLATFORM")
    p.add_argument("--teardown", action="store_true", help="remove the lab objects and stop")
    p.add_argument("--passes", type=int, default=2)
    p.add_argument("--airflow", default="http://localhost:8080")
    args = p.parse_args()

    import snowflake.connector
    from services.ops.airflow_rest import AirflowRest

    try:
        import app.db  # noqa: F401  (the platform's in-memory token cache; the Windows store refuses the token)
    except Exception:
        pass

    af = AirflowRest(args.airflow, "admin", airflow_password(), env_id=ENV_ID)
    log(f"Airflow {af.version()['version']} at {args.airflow}")
    log("Connecting to Snowflake: complete the sign-in in your browser...")
    import os

    con = snowflake.connector.connect(connection_name="anblicksorg-anblicksorg_aws", role=os.environ.get("LAB_ROLE", "SYSADMIN"),
                                      warehouse="DBT_WH",
                                      database=args.database.upper(), schema="CORE", login_timeout=600,
                                      client_session_keep_alive=True, client_store_temporary_credential=True)
    lab = Lab(ConnDb(con), af, args.database.upper())
    if args.teardown:
        lab.auto(False)
        lab.sql_file("teardown.sql")
        log("lab objects removed")
        con.close()
        return
    lab.sql_file("setup_lab.sql")
    error = None
    try:
        for n in range(1, args.passes + 1):
            lab.one_pass(n)
    except Exception as exc:   # report what ran so far
        error = f"{type(exc).__name__}: {exc}"
        log(f"stopped: {error}")
    finally:
        lab.auto(False)
        (LAB / "last_run.json").write_text(json.dumps({"results": lab.results, "error": error}, indent=2, default=str), encoding="utf-8")
        print(f"\n{'pass':<5}{'dag':<24}{'want':<36}{'got':<19}{'airflow':<9}{'ok'}")
        for r in lab.results:
            print(f"{r['pass']:<5}{r['dag']:<24}{str(r['want']):<36}{str(r['got']):<19}{str(r['airflow']):<9}{'PASS' if r['ok'] else 'FAIL'}")
        ok = sum(r["ok"] for r in lab.results)
        print(f"\nSUMMARY {ok}/{len(lab.results)} DAG outcomes as expected" + (f"; stopped: {error}" if error else ""))
        con.close()


if __name__ == "__main__":
    main()
