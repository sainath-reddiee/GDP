"""Live proof of the New source flow on DEV, over HTTP against a running API, each step as the persona that owns it
(header X-AIP-Persona narrows the signed-in user to that persona's roles, as "View as" does in the app).

    python tests/integration/run_request_journey.py                     # API at http://127.0.0.1:8001 (AUTH_MODE=dev)
    API=https://... SESSION=<x-aip-session> python tests/integration/run_request_journey.py

  1. GDP Product Owner      files a New source request for the demo source in the domain
  2. Data OS Administrator  registers the source, validates access, lands the tables; the request links the run
  3. Data Designer          profiles, identifies the domain, generates and approves the mapping and the STTM
  4. Forward Eng. Lead      Build end to end (autopilot): checks (approved on behalf of the DQ approver), code,
                            validation, commit and pull request; stops at code review
  5. check                  the PR record, and the request at REVIEW waiting on the Data Developer

Not run in CI: it needs the API with a Snowflake session, the demo source (DEV_AIP_DEMO_SOURCE.CRM) and, for step 4's
pull request, GitHub publishing set up (Admin, Integrations). Each step prints a checkpoint; the script stops at the
first one that does not hold. Settings: API, SESSION, DOMAIN (default GDP), TARGET_MODEL (default GDP.DIM_CUSTOMER),
SOURCE_DB, SOURCE_SCHEMA, SOURCE_TABLES (comma separated), AUTOPILOT_WAIT (seconds, default 900).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("API", "http://127.0.0.1:8001").rstrip("/")
SESSION = os.environ.get("SESSION", "")
DOMAIN = os.environ.get("DOMAIN", "GDP").upper()
TARGET = os.environ.get("TARGET_MODEL", "GDP.DIM_CUSTOMER")
SOURCE_DB = os.environ.get("SOURCE_DB", "DEV_AIP_DEMO_SOURCE")
SOURCE_SCHEMA = os.environ.get("SOURCE_SCHEMA", "CRM")
TABLES = [t.strip() for t in os.environ.get("SOURCE_TABLES", "CRM_CUSTOMER").split(",") if t.strip()]
WAIT = int(os.environ.get("AUTOPILOT_WAIT", "900"))


def call(persona: str, method: str, path: str, body=None):
    headers = {"Content-Type": "application/json", "X-AIP-Persona": persona}
    if SESSION:
        headers["x-aip-session"] = SESSION
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"FAIL  {persona}: {method} {path} -> {exc.code}: {exc.read().decode()[:1500]}") from exc


def check(ok: bool, what: str, detail: str = "") -> None:
    print(("ok    " if ok else "FAIL  ") + what + (f"  ({detail})" if detail else ""))
    if not ok:
        sys.exit(1)


def state(result) -> str:
    return (result.get("state") or result).get("current_state") if isinstance(result, dict) else str(result)


def request(rid: str) -> dict:
    return call("product_owner", "GET", f"/api/onboarding-requests/{rid}")


def main() -> None:
    stamp = int(time.time())
    source_name = f"JOURNEY_CRM_{stamp}"
    domains = call("product_owner", "GET", "/api/domains")
    domains = domains.get("domains", domains) if isinstance(domains, dict) else domains
    domain = next((d for d in domains if str(d.get("domain_name")).upper() == DOMAIN), None)
    check(bool(domain), f"domain {DOMAIN} exists")

    # 1. the Product Owner files the request
    r = call("product_owner", "POST", "/api/onboarding-requests", {
        "request_type": "SOURCE", "intent": "NEW", "title": f"Journey {stamp}: {DOMAIN} from {source_name}",
        "business_outcome": "Live journey test", "domain_id": domain["domain_id"], "priority": "LOW",
        "sources": [{"source_name": source_name, "note": "journey test"}]})
    rid, item = r["request_id"], r["items"][0]["item_id"]
    check(r["stage"] == "ENABLE" and r["waiting_on"] == "SOURCE.CONNECT", "1. filed; waits on the Data OS Administrator",
          f"{r['stage']} / {r['waiting_on']}")

    # 2. the Data OS Administrator registers and lands the source, and links the run to the request
    run = call("admin", "POST", "/api/runs", {"run_name": f"journey-{stamp}", "target_model": TARGET, "environment": "DEV",
                                              "domain_id": domain["domain_id"]})
    run_id = run["run_id"]
    print(f"      run {run_id}")
    call("admin", "POST", f"/api/runs/{run_id}/source", {"source_system_name": source_name, "source_type": "SNOWFLAKE_DATABASE",
                                                       "database": SOURCE_DB, "schema": SOURCE_SCHEMA})
    call("admin", "POST", f"/api/runs/{run_id}/access", {"selected": TABLES})
    landed = call("admin", "POST", f"/api/runs/{run_id}/landing")
    check(state(landed) == "LANDING_COMPLETE", "2. source registered and landed", state(landed))
    r = request(rid)
    if not r["items"][0].get("run_id"):
        r = call("admin", "POST", f"/api/onboarding-requests/{rid}/sources/{item}/run", {"run_id": run_id})
    check(r["stage"] == "DESIGN" and r["waiting_on"] == "PROFILE.RUN", "   request: source onboarded, waits on the Data Designer",
          f"{r['stage']} / {r['waiting_on']}")
    check(any(e["kind"] == "SOURCE_ONBOARDED" for e in r.get("events", [])), "   the requester's history says Source onboarded")

    # 3. the Data Designer: profile, domain, mapping, STTM, with their approvals
    check(state(call("designer", "POST", f"/api/runs/{run_id}/profile")) == "PROFILING_COMPLETE", "3. profiled")
    call("designer", "POST", f"/api/runs/{run_id}/domain")
    mapped = call("designer", "POST", f"/api/runs/{run_id}/mapping")
    print(f"      mapping {state(mapped)}")
    board = call("designer", "GET", f"/api/runs/{run_id}/mapping")
    decisions, used = [], set()
    for c in sorted(board.get("candidates", []), key=lambda c: (-float(c.get("final_score") or 0), c.get("rank") or 9)):
        if c["source_column_id"] in used:
            continue
        used.add(c["source_column_id"])
        decisions.append({"candidate_id": c["candidate_id"], "source_column_id": c["source_column_id"],
                          "decision": "APPROVED" if int(c.get("rank") or 9) == 1 else "REJECTED",
                          "business_justification": "journey: best candidate"})
    call("designer", "POST", f"/api/runs/{run_id}/mapping/decisions", {"decisions": decisions})
    call("designer", "POST", f"/api/runs/{run_id}/review", {"to_state": "MAPPING_APPROVED", "decision": "APPROVE",
                                                          "business_justification": "journey: mapping approved"})
    call("designer", "POST", f"/api/runs/{run_id}/sttm")
    approved = call("designer", "POST", f"/api/runs/{run_id}/review", {"to_state": "STTM_APPROVED", "decision": "APPROVE",
                                                                     "business_justification": "journey: STTM approved"})
    check(state(approved) == "STTM_APPROVED", "   mapping and STTM approved", state(approved))
    r = request(rid)
    check(r["stage"] == "BUILD" and r["waiting_on"] == "RUN.OPERATE", "   request: design stored, waits on the Forward Engineering Lead",
          f"{r['stage']} / {r['waiting_on']}")

    # 4. the Forward Engineering Lead builds end to end
    job = call("forward_lead", "POST", f"/api/runs/{run_id}/autopilot")
    deadline = time.time() + WAIT
    while job.get("status") == "RUNNING" and time.time() < deadline:
        time.sleep(10)
        job = call("forward_lead", "GET", f"/api/runs/{run_id}/autopilot")
    for s in job.get("steps", []):
        print(f"      {s['action']:<14} {s.get('seconds', '')}s" + (f"  error: {s['error']}" if s.get("error") else ""))
    stop = job.get("stop") or {}
    actions = [s["action"] for s in job.get("steps", [])]
    check("approve_soda" in actions, "4. checks approved on behalf of the DQ approver")
    check("publish_pr" in actions, "   code committed and the pull request opened")
    check(stop.get("where") == "code review", "   stopped at code review", stop.get("label", ""))

    # 5. the PR record and the request
    pub = (call("forward_lead", "GET", f"/api/runs/{run_id}/dbt") or {}).get("publication") or {}
    check(pub.get("status") == "PUBLISHED", "5. pull request recorded", pub.get("pr_url") or pub.get("detail") or "")
    r = request(rid)
    check(r["stage"] == "REVIEW" and r["waiting_on"] == "REVIEW.APPROVE", "   request: in review, waits on the Data Developer",
          f"{r['stage']} / {r['waiting_on']}")
    print(f"\nDone. Request {rid}, run {run_id}, PR {pub.get('pr_url')}")


if __name__ == "__main__":
    main()
