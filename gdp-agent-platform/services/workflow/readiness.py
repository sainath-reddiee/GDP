"""Ready to build: which runs the Forward Engineering Lead can build end to end, domain by domain (pure).

Intake asks, Enable onboards the source and starts its run, Design profiles, models and maps it. A run is ready to build
when its design is done: the domain's model is approved, the field mapping is approved and the transformations (STTM)
are approved. From there autopilot (services/workflow/autopilot.py) generates the code and the data quality checks in
parallel and validates the code with the review and standards agents, until the code review gate a person owns. Data
quality approval runs alongside and is shown, never waited for.

The unit of building is a leaf run: a run mapped to one table, or each table run of a run mapped to several (the parent
only fans out). Each leaf gets a status:

  ENABLE      its source is still being onboarded (the Data OS Administrator)
  DESIGN      profiled, modelled and mapped by the Data Designer and the gate approvers (with who acts next)
  MODEL       its design is done but the domain's model is not approved yet
  READY       ready to build end to end
  BUILDING    code is being generated or validated
  FLAGGED     validation failed the gate: improve and validate again
  REVIEW      validated; waiting on QA and code review
  BUILT       code approved, completed or deployed
  ATTENTION   the run failed
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from services.onboarding.requests import DONE_STATES, RUN_STATES

STATUSES = ("READY", "FLAGGED", "BUILDING", "REVIEW", "MODEL", "DESIGN", "ENABLE", "ATTENTION", "BUILT")
_ORDER = list(RUN_STATES) + sorted(DONE_STATES)
_MAPPING_DONE = _ORDER.index("MAPPING_APPROVED")
_STTM_DONE = _ORDER.index("STTM_APPROVED")
_DQ_DONE = _ORDER.index("SODA_APPROVED")
_DESIGN_FROM = _ORDER.index("LANDING_COMPLETE")
BUILDING = {"DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING"}
REVIEWING = {"VALIDATION_PASSED", "DBT_REVIEW"}
# states the code can be built from: the STTM is approved and no generation has started
READY_STATES = {"STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING"}


def _up(v: Any) -> str:
    return str(v or "").strip().upper()


def _at(state: str) -> int:
    return _ORDER.index(state) if state in _ORDER else -1


def leaf(run: Dict[str, Any], model_approved: bool) -> Dict[str, Any]:
    """One buildable run: its status, its design checks and percent, and who acts next."""
    state = _up(run.get("current_state"))
    at = _at(state)
    if state == "FAILED":
        frm = _at(_up(run.get("failed_from_state")))
        at = frm
    design = {"model": bool(model_approved), "mapping": at >= _MAPPING_DONE, "sttm": at >= _STTM_DONE,
              "dq": at >= _DQ_DONE}
    done = sum(design[k] for k in ("model", "mapping", "sttm"))
    waiting = None
    if state == "FAILED":
        status = "ATTENTION"
        waiting = f"The run failed: {(run.get('failure_reason') or 'see the run console')[:200]}"
    elif state in DONE_STATES:
        status = "BUILT"
    elif state == "VALIDATION_FAILED":
        status = "FLAGGED"
        waiting = "Validation flagged the code: improve the skill or the mapping, then validate again"
    elif state in REVIEWING:
        status = "REVIEW"
        waiting = RUN_STATES[state][2]
    elif state in BUILDING:
        status = "BUILDING"
        waiting = RUN_STATES[state][2]
    elif state in READY_STATES:
        status = "READY" if model_approved else "MODEL"
        waiting = None if model_approved else "Data Designer: approve the domain model"
    elif at >= _DESIGN_FROM:
        status = "DESIGN"
        waiting = RUN_STATES.get(state, ("", "", None))[2]
    else:
        status = "ENABLE"
        waiting = RUN_STATES.get(state, ("", "", "Data OS Administrator: onboard the source"))[2]
    return {"run_id": run.get("run_id"), "run_name": run.get("run_name"), "parent_run_id": run.get("parent_run_id"),
            "target_model": run.get("target_model"), "source_system_name": run.get("source_system_name"),
            "current_state": state, "status": status, "design": design, "percent": round(100 * done / 3),
            "waiting": waiting, "updated_at": run.get("updated_at")}


def board(runs: Iterable[Dict[str, Any]], domains: Dict[str, Dict[str, Any]],
          history: Optional[Dict[str, Dict[str, Any]]] = None,
          requests: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The Ready to build board. `runs`: live Silver runs (not archived, deleted or cancelled), parents and their table
    runs; `domains`: DOMAIN_ID -> {domain_name, approved_version}; `history`: run id -> its validation summary
    (services.validation.history.summarize); `requests`: run id -> {request_id, title} of the request it serves.
    Returns {domains: [...], totals: {status: count}}, domains with something ready first."""
    history, requests = history or {}, requests or {}
    runs = [r for r in runs if _up(r.get("current_state")) != "CANCELLED"]
    parents = {r["parent_run_id"] for r in runs if r.get("parent_run_id")}
    by_id = {r["run_id"]: r for r in runs}
    out: Dict[str, Dict[str, Any]] = {}
    totals = {s: 0 for s in STATUSES}
    for r in runs:
        if r["run_id"] in parents:
            continue   # a run mapped to several tables is built table by table
        parent = by_id.get(r.get("parent_run_id") or "")
        did = r.get("domain_id") or (parent or {}).get("domain_id")
        d = domains.get(did or "") or {}
        approved = bool(d.get("approved_version"))
        item = leaf({**r, "source_system_name": r.get("source_system_name") or (parent or {}).get("source_system_name")}, approved)
        item["parent_name"] = (parent or {}).get("run_name")
        h = history.get(r["run_id"]) or {}
        item["score"] = h.get("last_score")
        item["attempts"] = h.get("attempts", 0)
        link = requests.get(r["run_id"]) or requests.get(r.get("parent_run_id") or "")
        item["request"] = {"request_id": link["request_id"], "title": link["title"]} if link else None
        # runs of a domain that is retired or gone are one group, apart from runs without a domain
        key = did if d else ("inactive" if did else "none")
        group = out.setdefault(key, {"domain_id": did if d else None,
                                     "domain_name": d.get("domain_name") or ("Inactive domain" if did else "No domain"),
                                     "model_approved": approved, "approved_version": d.get("approved_version"),
                                     "runs": [], "counts": {s: 0 for s in STATUSES}})
        group["runs"].append(item)
        group["counts"][item["status"]] += 1
        totals[item["status"]] += 1
    for g in out.values():
        g["runs"].sort(key=lambda x: (STATUSES.index(x["status"]), -x["percent"], str(x.get("run_name") or "")))
        live = [x for x in g["runs"] if x["status"] not in ("BUILT", "ATTENTION")]
        g["percent"] = round(sum(x["percent"] for x in live) / len(live)) if live else 100
    ordered = sorted(out.values(), key=lambda g: (-g["counts"]["READY"], -g["counts"]["FLAGGED"], g["domain_name"]))
    return {"domains": ordered, "totals": totals}
