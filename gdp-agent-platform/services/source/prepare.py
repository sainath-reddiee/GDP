"""Which steps "Validate & land" still has to run for a run, given where it currently is."""

from __future__ import annotations

from typing import List, Optional

ACCESS = "access"
LANDING = "landing"
INTERRUPTED = "fail:landing was interrupted (no progress for a while); landing again"
STALE_LANDING_MINUTES = 30


def prepare_plan(current_state: str, failed_from: Optional[str] = None, idle_minutes: Optional[float] = None) -> List[str]:
    """Ordered steps: 'fail:<reason>' (system transition to FAILED), 'retry:<STATE>' (system retry transition),
    'access', 'landing'.

    Resumes from wherever the source stage stopped, so pressing the button again after a
    failure or a half-finished attempt never repeats work that already succeeded. A run left in LANDING_RUNNING by a
    landing that died (API or worker restart, lost connection) is failed and landed again once it has not moved for
    STALE_LANDING_MINUTES (`idle_minutes`); before that, the landing may still be running.
    """
    if current_state == "LANDING_RUNNING":
        if idle_minutes is not None and idle_minutes >= STALE_LANDING_MINUTES:
            return [INTERRUPTED, "retry:LANDING_PENDING", LANDING]
        raise ValueError("landing is still running; if it stopped, try again after "
                         f"{STALE_LANDING_MINUTES} minutes without progress")
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
