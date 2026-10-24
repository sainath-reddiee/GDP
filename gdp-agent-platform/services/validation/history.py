"""A run's history at a glance, for the Run history tab: its validation attempts (scored as the gate scored them, or
from the checks they recorded before the gate existed) and the people who decided its gates. Pure."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from services.validation.checks import score as score_of


def _body(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        out = json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def attempt(row: Dict[str, Any]) -> Dict[str, Any]:
    """One SUMMARY row of CORE.VALIDATION_RUN as an attempt: its status, score and the gate's reasons."""
    body = _body(row.get("result_json"))
    gate = body.get("gate") if isinstance(body.get("gate"), dict) else {}
    value: Optional[float] = gate.get("score") if gate else None
    if value is None:
        exact = score_of([r for r in body.get("results") or [] if isinstance(r, dict)])
        value = None if exact is None else round(exact, 1)
    return {"status": row.get("status"), "score": value, "min_score": gate.get("min_score") if gate else None,
            "reasons": list(gate.get("reasons") or []) if gate else [], "errors": row.get("error_count") or 0,
            "at": row.get("completed_at") or row.get("started_at")}


def summarize(run_ids: Iterable[str], summaries: List[Dict[str, Any]], reviews: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{run_id: {attempts, first_score, last_score, last_status, last_at, reasons, failed_before, decisions}} for every
    run asked for (a run without a validation or a decision gets zeros and empty lists). `failed_before` says an
    attempt before the latest failed: with a passing latest attempt, the run recovered."""
    out: Dict[str, Dict[str, Any]] = {rid: {"attempts": 0, "first_score": None, "last_score": None, "last_status": None,
                                            "last_at": None, "reasons": [], "failed_before": False, "decisions": []} for rid in run_ids}
    by_run: Dict[str, List[Dict[str, Any]]] = {}
    for row in summaries:
        by_run.setdefault(row.get("run_id"), []).append(attempt(row))
    for rid, tries in by_run.items():
        if rid not in out:
            continue
        tries.sort(key=lambda a: str(a["at"] or ""))
        scored = [a for a in tries if a["score"] is not None]
        last = tries[-1]
        out[rid].update({"attempts": len(tries), "first_score": scored[0]["score"] if scored else None,
                         "last_score": last["score"], "last_status": last["status"], "last_at": last["at"],
                         "reasons": last["reasons"], "failed_before": any(a["status"] == "FAILED" for a in tries[:-1])})
    for r in sorted(reviews, key=lambda r: str(r.get("reviewed_at") or "")):
        rid = r.get("run_id")
        if rid in out:
            out[rid]["decisions"].append({"stage": r.get("stage"), "decision": r.get("decision"),
                                          "reviewer": r.get("reviewer"), "at": r.get("reviewed_at")})
    return out
