"""Status of the three lanes built in parallel from the approved STTM, and the gate into code review.

The workflow state machine is a single line (STTM -> Data Quality -> dbt -> Validation -> Code review), but the
work is parallel: generating dbt moves the run past the Data Quality states without approving them, and QA tests
are not a workflow stage at all. So each lane's status comes from what was actually done:

  Data Quality  complete when every check is decided (at least one approved), review while any is proposed
  QA tests      complete when a tester signed off the current STTM's tests, blocked when they rejected them
  Validation    complete once the run is past VALIDATION_PASSED

Code review opens only when all three are complete; the review endpoint enforces the same gate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

POST_STTM = {
    "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
    "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING",
    "VALIDATION_PASSED", "VALIDATION_FAILED", "DBT_REVIEW", "DBT_APPROVED", "COMPLETED",
}
VALIDATED = {"VALIDATION_PASSED", "DBT_REVIEW", "DBT_APPROVED", "COMPLETED"}


def soda_lane(counts: Dict[str, int]) -> Dict[str, Any]:
    proposed = int(counts.get("PROPOSED") or 0)
    approved = int(counts.get("APPROVED") or 0)
    rejected = int(counts.get("REJECTED") or 0)
    if not proposed and not approved and not rejected:
        return {"status": "ACTIVE", "note": "generate checks", "done": False}
    if proposed:
        return {"status": "REVIEW_REQUIRED", "note": f"{proposed} to confirm", "done": False}
    if not approved:
        return {"status": "REVIEW_REQUIRED", "note": "no check approved", "done": False}
    return {"status": "COMPLETE", "note": f"{approved} approved", "done": True}


def qa_lane(signoff: Optional[Dict[str, Any]], tests: int) -> Dict[str, Any]:
    decision = str((signoff or {}).get("decision") or "").upper()
    if decision == "APPROVED":
        return {"status": "COMPLETE", "note": "signed off", "done": True}
    if decision == "REJECTED":
        return {"status": "BLOCKED", "note": "rejected", "done": False}
    return {"status": "REVIEW_REQUIRED", "note": f"{tests} tests to sign off" if tests else "sign off", "done": False}


def lanes(current_state: str, soda_counts: Dict[str, int], qa_signoff: Optional[Dict[str, Any]],
          qa_tests: int) -> Optional[Dict[str, Any]]:
    """None before the STTM is approved (nothing to show yet)."""
    state = str(current_state or "").upper()
    if state not in POST_STTM:
        return None
    soda, qa = soda_lane(soda_counts), qa_lane(qa_signoff, qa_tests)
    validated = state in VALIDATED
    waiting: List[str] = []
    if not soda["done"]:
        waiting.append(f"Data Quality: {soda['note']}")
    if not qa["done"]:
        waiting.append(f"QA tests: {qa['note']}")
    if not validated:
        waiting.append("Validation: not passed yet")
    return {"SODA": soda, "QA": qa, "VALIDATION": {"done": validated},
            "gate": {"ready": not waiting, "waiting_on": waiting}}


def apply(stages: List[Dict[str, Any]], current_state: str, result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Overlay lane statuses on the state machine's rail; Code review waits for every lane."""
    if not result:
        return stages
    out = []
    for s in stages:
        s = dict(s)
        if s.get("stage") == "SODA":
            s.update(status=result["SODA"]["status"], note=result["SODA"]["note"])
        if s.get("stage") == "REVIEW" and str(current_state).upper() == "DBT_REVIEW" and not result["gate"]["ready"]:
            s.update(status="LOCKED", note=f"waiting on {len(result['gate']['waiting_on'])}")
        out.append(s)
        if s.get("stage") == "STTM" and not any(x.get("stage") == "QA" for x in stages):
            out.append({"stage": "QA", "status": result["QA"]["status"], "note": result["QA"]["note"]})
    return out
