"""Drive the Phase 1 slice through the running API (uses the API's Snowflake session)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8001"


def call(method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        API + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:2000]
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


def main() -> None:
    name = f"live-p1-{int(time.time())}"
    run = call("POST", "/api/runs", {"run_name": name, "target_model": "GDP.DIM_CUSTOMER", "environment": "TEST"})
    run_id = run["run_id"]
    print("created", run_id, run["current_state"])

    src = call("POST", f"/api/runs/{run_id}/source", {
        "source_system_name": "LIVE_CRM", "source_type": "SNOWFLAKE_DATABASE",
        "database": "DEV_AIP_DEMO_SOURCE", "schema": "CRM",
    })
    print("source", src.get("state", src).get("current_state"))

    acc = call("POST", f"/api/runs/{run_id}/access", {"selected": ["CRM_CUSTOMER"]})
    print("access", acc.get("state", acc).get("current_state"))

    land = call("POST", f"/api/runs/{run_id}/landing")
    print("landing", land.get("state", land).get("current_state"))

    prof = call("POST", f"/api/runs/{run_id}/profile")
    print("profile", prof.get("state", prof).get("current_state"), prof.get("profiled"))

    dom = call("POST", f"/api/runs/{run_id}/domain")
    print("domain", dom.get("state", dom).get("current_state"), (dom.get("domain") or {}).get("domain_name"))

    mp = call("POST", f"/api/runs/{run_id}/mapping")
    print("mapping", mp.get("state", mp).get("current_state"), mp.get("summary"))

    board = call("GET", f"/api/runs/{run_id}/mapping")
    print("status before decisions", board["status"])
    required = ["CUSTOMER_ID", "CUSTOMER_NAME", "CUSTOMER_STATUS"]
    used: set[str] = set()
    decisions = []
    cands = sorted(board["candidates"], key=lambda c: (-float(c["final_score"]), c["rank"]))
    concat = "TRIM(INITCAP(TRIM(first_nm)) || ' ' || INITCAP(TRIM(last_nm)))"
    for target in required:
        for c in cands:
            if c["target_column"] == target and c["source_column_id"] not in used:
                decisions.append({
                    "candidate_id": c["candidate_id"], "source_column_id": c["source_column_id"],
                    "decision": "MODIFIED" if target == "CUSTOMER_NAME" else "APPROVED",
                    "transformation": concat if target == "CUSTOMER_NAME" else c.get("transformation"),
                    "business_justification": "live: cover required DIM_CUSTOMER columns",
                })
                used.add(c["source_column_id"])
                break
    for c in cands:
        if c["source_column_id"] not in used:
            decisions.append({"source_column_id": c["source_column_id"], "decision": "REJECTED"})
            used.add(c["source_column_id"])
    saved = call("POST", f"/api/runs/{run_id}/mapping/decisions", {"decisions": decisions})
    print("saved", saved)

    rev = call("POST", f"/api/runs/{run_id}/review", {
        "to_state": "MAPPING_APPROVED", "decision": "APPROVE",
        "business_justification": "live: mappings complete for GDP customer",
    })
    print("mapping approved", rev.get("state", rev).get("current_state"))

    sttm = call("POST", f"/api/runs/{run_id}/sttm")
    print("sttm", sttm.get("state", sttm).get("current_state"), sttm.get("version"))
    call("POST", f"/api/runs/{run_id}/review", {
        "to_state": "STTM_APPROVED", "decision": "APPROVE",
        "business_justification": "live: STTM matches DIM_CUSTOMER",
    })

    soda = call("POST", f"/api/runs/{run_id}/soda")
    print("soda", soda.get("state", soda).get("current_state"), soda.get("count"))
    call("POST", f"/api/runs/{run_id}/review", {
        "to_state": "SODA_APPROVED", "decision": "APPROVE",
        "business_justification": "live: Soda covers grain and required columns",
    })

    dbt = call("POST", f"/api/runs/{run_id}/dbt")
    print("dbt", dbt.get("state", dbt).get("current_state"), dbt.get("files"))
    val = call("POST", f"/api/runs/{run_id}/validation")
    print("validation", val.get("status"), val.get("state", val).get("current_state"), val.get("errors"))

    done = call("POST", f"/api/runs/{run_id}/review", {
        "to_state": "DBT_APPROVED", "decision": "APPROVE",
        "business_justification": "live: generated project reviewed",
    })
    print("final", done.get("state", done).get("current_state"), "run", run_id)


if __name__ == "__main__":
    main()
