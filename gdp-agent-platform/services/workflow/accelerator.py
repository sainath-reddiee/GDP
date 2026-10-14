"""How much a pipeline's runs are accelerated, from what the platform already records (pure).

- generated share: of the mapping decisions people made, the share that accepted the platform's proposal as it was
  (APPROVED) rather than changing it (MODIFIED, ALTERNATIVE_TARGET) or turning it down (REJECTED);
- time per stage: from the run's workflow events, the time spent in each stage until it moved on;
- first-time-right gates: human gates passed without a request for changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

ACCEPTED = "APPROVED"
GATES = {"MAPPING_REVIEW": "Mapping", "STTM_REVIEW": "STTM", "SODA_REVIEW": "Data Quality", "DBT_REVIEW": "Code review"}


def generated_share(decisions: Iterable[str]) -> Optional[float]:
    """Accepted as proposed / all decisions, rounded to a percent; None without decisions."""
    values = [str(d or "").upper() for d in decisions]
    if not values:
        return None
    return round(100.0 * sum(1 for d in values if d == ACCEPTED) / len(values), 1)


def median(values: List[float]) -> Optional[float]:
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def _ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)[:26])
    except (TypeError, ValueError):
        return None


def stage_times(events: List[Dict[str, Any]], stage_of: Dict[str, str], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """[{stage, minutes, automatic}] from ordered events {to_state, created_at, actor_type}: each stage's time is from
    the first event that entered it to the first event that left it (now, for the current stage)."""
    out: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    timeline = [(stage_of.get(str(e.get("to_state")), ""), _ts(e.get("created_at")), str(e.get("actor_type") or "")) for e in events]
    timeline = [t for t in timeline if t[1] is not None]
    for i, (stage, at, actor) in enumerate(timeline):
        if not stage:
            continue
        if stage not in out:
            out[stage] = {"stage": stage, "start": at, "end": None, "human": False}
            order.append(stage)
        nxt = next((t for t in timeline[i + 1:] if t[0] and t[0] != stage), None)
        if nxt and out[stage]["end"] is None:
            out[stage]["end"] = nxt[1]
        if actor == "HUMAN":
            out[stage]["human"] = True
    rows = []
    for s in order:
        r = out[s]
        end = r["end"] or now
        minutes = round((end - r["start"]).total_seconds() / 60, 1) if end and r["start"] and end >= r["start"] else None
        rows.append({"stage": s, "minutes": minutes, "human_gate": r["human"]})
    return rows


def gates(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{gate, passed, first_time_right, sent_back}] for the human gates the run reached."""
    out: Dict[str, Dict[str, Any]] = {}
    for e in events:
        frm, to = str(e.get("from_state") or ""), str(e.get("to_state") or "")
        if frm in GATES:
            g = out.setdefault(frm, {"gate": GATES[frm], "state": frm, "passed": False, "sent_back": 0})
            if to.endswith("_APPROVED") or (frm == "DBT_REVIEW" and to in ("DBT_APPROVED", "COMPLETED")):
                g["passed"] = True
            elif to.endswith("_PENDING"):
                g["sent_back"] += 1
    return [{**g, "first_time_right": g["passed"] and g["sent_back"] == 0} for g in out.values()]
