"""Which steps "Validate & land" still has to run for a run, given where it currently is."""

from __future__ import annotations

from typing import List, Optional

ACCESS = "access"
LANDING = "landing"


def prepare_plan(current_state: str, failed_from: Optional[str] = None) -> List[str]:
    """Ordered steps: 'retry:<STATE>' (system retry transition), 'access', 'landing'.

    Resumes from wherever the source stage stopped, so pressing the button again after a
    failure or a half-finished attempt never repeats work that already succeeded.
    """
    if current_state == "SOURCE_REGISTERED":
        return [ACCESS, LANDING]
    if current_state in ("ACCESS_APPROVED", "LANDING_PENDING"):
        return [LANDING]
    if current_state == "LANDING_COMPLETE":
        return []
    if current_state == "FAILED" and failed_from == "ACCESS_VALIDATION":
        return ["retry:SOURCE_REGISTERED", ACCESS, LANDING]
    if current_state == "FAILED" and failed_from == "LANDING_RUNNING":
        return ["retry:LANDING_PENDING", LANDING]
    raise ValueError(f"cannot validate and land from state {current_state}"
                     + (f" (failed in {failed_from})" if failed_from else ""))
