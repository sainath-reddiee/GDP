"""Run the real API against a consolidated rehearsal clone and call every read endpoint.

    python -m infrastructure.consolidation.app_smoke --database DEV_AI_PLATFORM_APP_REHEARSAL [--connection <c>]
        [--root <checkout of the code to run>] [--compare <earlier result json>]

Every GET route without path parameters is called in-process (FastAPI TestClient, dev auth, the real Snowflake
database). A route fails the smoke when it answers 5xx, or when its body shows a missing object: "does not exist",
"invalid identifier", "not authorized", or a store reporting itself not deployed ("deployed": false, "ready": false).
Other 4xx answers (a required query parameter, a feature not configured) are listed but do not fail. With --compare
(the result of the same smoke for the code before V061 on the pre-move backup clone), only routes that newly fail
count: a feature that is not configured in this environment fails both runs the same way. Only *_REHEARSAL databases
are accepted, so this never runs against a real environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MISSING = re.compile(r"does not exist|invalid identifier|not authorized|\"deployed\":\s*false|\"ready\":\s*false", re.I)
# Read endpoints that reach outside Snowflake or stream for minutes; not about the schema move.
SKIP = re.compile(r"/stream|/events$|/export|\.csv$|\.zip$|/download|/oauth|/login|/logout|/health/deep")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--database", required=True)
    p.add_argument("--connection")
    p.add_argument("--warehouse", default="DBT_WH")
    p.add_argument("--out", default=str(ROOT / "consolidation_runs"))
    p.add_argument("--root", default=str(ROOT), help="repository checkout whose apps/api and services to run")
    p.add_argument("--compare", help="result JSON of the reference run")
    args = p.parse_args()
    db = args.database.upper()
    assert db.endswith("_REHEARSAL"), "the smoke test only runs against a *_REHEARSAL database"
    os.environ.update({"AIP_DATABASE": db, "AIP_AUTH": "dev", "AIP_WAREHOUSE": args.warehouse})
    if args.connection:
        os.environ["AIP_SNOWFLAKE_CONNECTION"] = args.connection
    code = Path(args.root).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(code))
    sys.path.insert(0, str(code / "apps" / "api"))
    from fastapi.testclient import TestClient

    from app import db as app_db
    from app.main import app

    for attempt in range(6):   # sign in once before any route (each route would otherwise hit the cool-down)
        try:
            who = app_db.dev_db()
            print(f"signed in as {who.user} ({who.role}) on {db}", flush=True)
            break
        except Exception as exc:
            print(f"sign-in attempt {attempt + 1} failed: {str(exc)[:200]}", flush=True)
            time.sleep(25)
    else:
        sys.exit("could not sign in to Snowflake; nothing was tested")
    client = TestClient(app)
    routes = sorted({r.path for r in app.routes if "GET" in getattr(r, "methods", set())
                     and "{" not in r.path and r.path.startswith("/api") and not SKIP.search(r.path)})
    results, failed = [], []
    for path in routes:
        t0 = time.time()
        try:
            res = client.get(path)
            status, body = res.status_code, res.text[:4000]
        except Exception as exc:
            status, body = 599, f"{type(exc).__name__}: {exc}"[:4000]
        problem = status >= 500 or bool(MISSING.search(body))
        row = {"path": path, "status": status, "ms": int((time.time() - t0) * 1000), "problem": problem,
               "body": body[:600] if (problem or status >= 400) else ""}
        results.append(row)
        if problem:
            failed.append(row)
        print(f"{'FAIL' if problem else ('warn' if status >= 400 else 'ok  ')} {status} {path}", flush=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{db}_app_smoke{'_ref' if code != ROOT else ''}.json"
    path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\n{len(routes)} read endpoints: {len(routes) - len(failed)} passed, {len(failed)} failed; written {path}")
    for row in failed:
        print(f"  FAIL {row['status']} {row['path']}: {row['body'][:300]}")
    if args.compare:
        before = {r["path"]: r for r in json.loads(Path(args.compare).read_text(encoding="utf-8"))}
        new = [r for r in failed if r["path"] in before and not before[r["path"]]["problem"]]
        print(f"compared with {args.compare}: {len(new)} route(s) fail now that passed before")
        for row in new:
            print(f"  NEW FAIL {row['status']} {row['path']}: {row['body'][:300]}")
        sys.exit(1 if new else 0)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
