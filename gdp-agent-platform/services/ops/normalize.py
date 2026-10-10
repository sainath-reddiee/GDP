"""Airflow REST payloads (v1 for Airflow 2, v2 for Airflow 3) and listener push events mapped to OPS rows (pure).

Rows are plain dicts with lower-case OPS column names. Times are ISO 8601 strings in UTC, so they serialize to JSON and
load with TO_TIMESTAMP_LTZ. UPDATED_AT is the state's own time in Airflow (the newest of its timestamps), which decides
which of two writes for the same key wins.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from services.ops.redact import redact

TERMINAL = {"success", "failed", "upstream_failed", "skipped", "removed"}
FAILED = {"failed", "upstream_failed"}
ACTIVE = {"running", "queued", "scheduled", "up_for_retry", "up_for_reschedule", "deferred", "restarting"}
EXCERPT_MAX = 4000
MANUAL_TRIGGERS = {"rest_api", "ui", "cli", "test", "operator"}


def ts(value: Any) -> Optional[str]:
    """Any Airflow timestamp (ISO string with Z or an offset, datetime, epoch seconds) as an ISO string in UTC."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            text = str(value).strip().replace(" ", "T", 1)
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def duration(start: Optional[str], end: Optional[str], given: Any = None) -> Optional[float]:
    if isinstance(given, (int, float)) and given >= 0:
        return round(float(given), 3)
    if start and end:
        seconds = (_dt(end) - _dt(start)).total_seconds()
        return round(seconds, 3) if seconds >= 0 else None
    return None


def newest(*values: Optional[str]) -> Optional[str]:
    found = [v for v in values if v]
    return max(found, key=lambda v: _dt(v)) if found else None


def state_rank(state: Optional[str]) -> int:
    """Tie breaker when two writes carry the same time: a finished state beats a running one beats anything else."""
    s = (state or "").lower()
    return 2 if s in TERMINAL else 1 if s in ACTIVE else 0


def newer(existing: Optional[Dict[str, Any]], incoming: Dict[str, Any]) -> bool:
    """Should `incoming` replace `existing` for the same key? Mirrors the MERGE condition in services.ops.store."""
    if not existing:
        return True
    old, new = _dt(ts(existing.get("updated_at"))), _dt(ts(incoming.get("updated_at")))
    if old is None:
        return True
    if new is None:
        return False
    if new != old:
        return new > old
    return state_rank(incoming.get("state")) >= state_rank(existing.get("state"))


def _state(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip().lower()
    if text.startswith("dagrunstate.") or text.startswith("taskinstancestate."):
        text = text.split(".", 1)[1]
    return text or None


def _schedule(payload: Dict[str, Any]) -> Optional[str]:
    """v1 schedule_interval ({__type: CronExpression|TimeDelta|RelativeDelta, ...}) or v2 timetable_summary."""
    interval = payload.get("schedule_interval")
    if isinstance(interval, dict):
        kind = interval.get("__type")
        if kind == "CronExpression":
            return str(interval.get("value"))[:500]
        if kind == "TimeDelta":
            seconds = int(interval.get("days") or 0) * 86400 + int(interval.get("seconds") or 0)
            return f"every {seconds}s"
        if interval.get("value"):
            return str(interval["value"])[:500]
    for value in (payload.get("timetable_summary"), interval if isinstance(interval, str) else None,
                  payload.get("timetable_description")):
        if value:
            return str(value)[:500]
    return None


def _tags(values: Iterable[Any]) -> List[str]:
    out = []
    for tag in values or []:
        name = tag.get("name") if isinstance(tag, dict) else tag
        if name and str(name) not in out:
            out.append(str(name))
    return out


def dag_row(env_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """One DAG from GET /dags (v1 or v2)."""
    if "is_stale" in payload and "is_active" not in payload:
        active = not bool(payload.get("is_stale"))
    else:
        active = bool(payload.get("is_active", True))
    owners = payload.get("owners") or []
    return {
        "env_id": env_id, "dag_id": str(payload["dag_id"]),
        "fileloc": payload.get("fileloc") or payload.get("relative_fileloc"),
        "owners": [str(o) for o in (owners if isinstance(owners, list) else [owners])],
        "tags": _tags(payload.get("tags") or []),
        "schedule": _schedule(payload),
        "is_paused": bool(payload.get("is_paused")) if payload.get("is_paused") is not None else None,
        "is_active": active,
        "description": (str(payload.get("description"))[:4000] if payload.get("description") else None),
    }


def dag_run_row(env_id: str, payload: Dict[str, Any], source: str = "POLL") -> Dict[str, Any]:
    """One DAG run from GET /dags/{dag}/dagRuns (v1 or v2)."""
    start, end = ts(payload.get("start_date")), ts(payload.get("end_date"))
    logical = ts(payload.get("logical_date") or payload.get("execution_date") or payload.get("run_after"))
    run_type = payload.get("run_type")
    if "external_trigger" in payload:
        external = bool(payload.get("external_trigger"))
    else:
        triggered_by = str(payload.get("triggered_by") or "").lower()
        external = triggered_by in MANUAL_TRIGGERS or str(run_type or "").lower() == "manual"
    state = _state(payload.get("state"))
    return {
        "env_id": env_id, "dag_id": str(payload["dag_id"]), "run_id": str(payload.get("dag_run_id") or payload.get("run_id")),
        "run_type": str(run_type) if run_type else None, "state": state, "logical_date": logical,
        "started_at": start, "ended_at": end if state in TERMINAL else None, "duration_s": duration(start, end if state in TERMINAL else None),
        "external_trigger": external, "note": (str(payload["note"])[:4000] if payload.get("note") else None),
        "source": source,
        "updated_at": newest(ts(payload.get("updated_at")), ts(payload.get("last_scheduling_decision")), start, end,
                             ts(payload.get("queued_at"))),
    }


def task_row(env_id: str, payload: Dict[str, Any], source: str = "POLL") -> Dict[str, Any]:
    """One task instance from GET /dags/{dag}/dagRuns/{run}/taskInstances (v1 or v2)."""
    start, end = ts(payload.get("start_date")), ts(payload.get("end_date"))
    map_index = payload.get("map_index")
    state = _state(payload.get("state"))
    run_id = str(payload.get("dag_run_id") or payload.get("run_id"))
    try_number = int(payload.get("try_number") or 0)
    row = {
        "env_id": env_id, "dag_id": str(payload["dag_id"]), "run_id": run_id, "task_id": str(payload["task_id"]),
        "map_index": int(map_index) if map_index is not None else -1, "try_number": try_number,
        "state": state, "operator": payload.get("operator") or payload.get("operator_name"),
        "started_at": start, "ended_at": end, "duration_s": duration(start, end, payload.get("duration")),
        "hostname": payload.get("hostname") or None,
        "log_ref": f"{payload['dag_id']}/{run_id}/{payload['task_id']}/{try_number}/{int(map_index) if map_index is not None else -1}",
        "error_excerpt": excerpt(payload.get("error")) if payload.get("error") else None,
        "source": source,
        "updated_at": newest(ts(payload.get("updated_at")), start, end, ts(payload.get("queued_when")),
                             ts(payload.get("scheduled_when"))),
    }
    return row


def push_row(env_id: str, body: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """A listener plugin event: ('dag_run' | 'task_instance', row). Raises ValueError on a malformed event."""
    kind = str(body.get("kind") or "")
    if not body.get("dag_id") or not (body.get("run_id") or body.get("dag_run_id")):
        raise ValueError("dag_id and run_id are required")
    if kind == "dag_run":
        row = dag_run_row(env_id, {**body, "start_date": body.get("start"), "end_date": body.get("end"),
                                   "dag_run_id": body.get("run_id") or body.get("dag_run_id")}, "PUSH")
    elif kind == "task_instance":
        if not body.get("task_id"):
            raise ValueError("task_id is required")
        row = task_row(env_id, {**body, "start_date": body.get("start"), "end_date": body.get("end"),
                                "dag_run_id": body.get("run_id") or body.get("dag_run_id")}, "PUSH")
    else:
        raise ValueError("kind must be dag_run or task_instance")
    event_time = ts(body.get("updated_at") or body.get("event_at"))
    row["updated_at"] = newest(row.get("updated_at"), event_time)
    return kind, row


def excerpt(text: Any, limit: int = EXCERPT_MAX) -> Optional[str]:
    """The useful end of an error: the last traceback (or ERROR lines) of a log, redacted and capped."""
    if not text:
        return None
    body = str(text)
    marker = body.rfind("Traceback (most recent call last)")
    if marker >= 0:
        body = body[marker:]
    else:
        lines = [ln for ln in body.splitlines() if " ERROR " in ln or "Error" in ln or "Exception" in ln]
        if lines:
            body = "\n".join(lines[-30:])
    body = redact(body.strip())
    return body[-limit:] if len(body) > limit else body
