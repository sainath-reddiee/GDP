"""Incident detection (pure): from captured DAG runs and task runs, which incidents to open or update, and which
successes resolve them; plus the error fingerprint, the severity and a small cron helper for expected-by times.

Kinds:
  FAILED             a DAG run failed (DAG level; skipped by incidents.py when a task of that run already has one)
  RETRIES_EXHAUSTED  a task ended 'failed' (Airflow's final state once retries are used up); 'up_for_retry' counts only
                     for CRITICAL DAGs when the setting alert_on_retry_for_critical is on
  LATE               no successful DAG run by the DAG's expected-by cron time (read in the DAG's timezone, else UTC)
  LONG_RUNNING       a DAG run still running past the DAG's max duration
  UPSTREAM           set by incidents.py for a child grouped under an upstream DAG's open incident

A candidate is a plain dict: kind, env_id, dag_id, task_id, map_index, run_id, at (ISO time it was observed),
error_excerpt, fingerprint, severity, title and occurrence_key (what makes two sightings the same occurrence, so a run
read twice by push and poll counts once).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from services.ops.normalize import ts
from services.ops.redact import redact

KINDS = ("FAILED", "RETRIES_EXHAUSTED", "LATE", "LONG_RUNNING", "UPSTREAM")
DAG_KINDS = {"FAILED", "LATE", "LONG_RUNNING"}
SEVERITIES = ("P1", "P2", "P3", "P4")
STATUSES = ("OPEN", "ACK", "MITIGATED", "RESOLVED", "MUTED")
CRITICALITY_BASE = {"CRITICAL": 2, "HIGH": 3, "MEDIUM": 4, "LOW": 4}
KIND_SHIFT = {"FAILED": 0, "RETRIES_EXHAUSTED": 0, "LATE": 0, "LONG_RUNNING": 1, "UPSTREAM": 1}
_PROD = re.compile(r"(^|[^a-z0-9])(prod|prd|production)([^a-z0-9]|$)")
LOW_PRIORITY_RUNS = {"backfill", "manual"}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "reopen_hours": 24, "alert_on_retry_for_critical": False, "transition_on_resolve": False, "done_status": "Done",
    "rate_limit_per_10min": 10, "public_base_url": None,
}


# ---------------------------------------------------------------- fingerprint

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?|\d{2}:\d{2}:\d{2}(\.\d+)?")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b|\b(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}\b")
_QUOTED = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`")
_PATH = re.compile(r"(?:[A-Za-z]:\\|\\\\|(?<![\w:])/|\b(?:s3|gs|hdfs|file)://)[^\s,;:)'\"]+")
_NUMBER = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:[.,]\d+)*")
_EXCEPTION = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Failure|Timeout|Warning)\b.*")


def last_exception_line(text: Optional[str]) -> str:
    """The line that names the exception (the last one of the traceback), else the last non-empty line."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for line in reversed(lines):
        # Airflow log prefixes: "[2024-01-01, 00:00:00 UTC] {taskinstance.py:1234} ERROR - ..."
        body = re.sub(r"^\[[^\]]*\]\s*(\{[^}]*\}\s*)?([A-Z]+\s*-\s*)?", "", line)
        if _EXCEPTION.match(body):
            return body
    return re.sub(r"^\[[^\]]*\]\s*(\{[^}]*\}\s*)?([A-Z]+\s*-\s*)?", "", lines[-1]) if lines else ""


def signature(text: Optional[str]) -> str:
    """The error's stable shape: the last exception line with times, ids, hex, paths, quoted literals and numbers
    replaced by placeholders, lower case, single spaces."""
    line = last_exception_line(text)
    for pattern, repl in ((_TIMESTAMP, "<ts>"), (_UUID, "<id>"), (_QUOTED, "<str>"), (_PATH, "<path>"), (_HEX, "<hex>"),
                          (_NUMBER, "<n>")):
        line = pattern.sub(repl, line)
    return " ".join(line.lower().split())[:300]


def fingerprint(env_id: str, dag_id: str, task_id: Optional[str], error_excerpt: Optional[str]) -> str:
    raw = "\x1f".join([env_id or "", dag_id or "", task_id or "", signature(error_excerpt)])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- severity

def is_prod(*names: Optional[str]) -> bool:
    return any(_PROD.search(str(n).lower()) for n in names if n)


def severity(criticality: Optional[str], kind: str, env_names: Iterable[Optional[str]] = (), run_type: Optional[str] = None,
             external_trigger: Optional[bool] = None, override: Optional[str] = None) -> str:
    """P1 (worst) to P4: the DAG's criticality times the kind, one level up for a production environment, one level
    down for backfill or manual runs. A routing rule's override wins."""
    if override and str(override).upper() in SEVERITIES:
        return str(override).upper()
    level = CRITICALITY_BASE.get(str(criticality or "").upper(), 4) + KIND_SHIFT.get(kind, 0)
    if is_prod(*env_names):
        level -= 1
    if str(run_type or "").lower() in LOW_PRIORITY_RUNS or (external_trigger and str(run_type or "").lower() != "scheduled"):
        level += 1
    return f"P{max(1, min(4, level))}"


def raise_level(current: str, other: str) -> str:
    """The more severe of two severities."""
    return min(current, other) if current in SEVERITIES and other in SEVERITIES else (current or other)


# ---------------------------------------------------------------- cron (expected-by)

_ALIASES = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
            "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}
_NAMES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11,
          "dec": 12, "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _field(text: str, low: int, high: int) -> Tuple[Set[int], bool]:
    """(allowed values, restricted) for one cron field."""
    out: Set[int] = set()
    text = text.strip().lower()
    for part in text.split(","):
        step, stepped = 1, "/" in part
        if stepped:
            part, step_text = part.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError("cron step must be positive")
        if part in ("*", "?"):
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(_NAMES.get(a, a)), int(_NAMES.get(b, b))
        else:
            start = int(_NAMES.get(part, part))
            end = high if stepped else start
        if start < low or end > high or start > end:
            raise ValueError(f"cron value out of range: {part}")
        out.update(range(start, end + 1, step))
    return out, text not in ("*", "?")


def parse_cron(expr: str) -> Dict[str, Any]:
    text = _ALIASES.get((expr or "").strip().lower(), (expr or "").strip())
    parts = text.split()
    if len(parts) == 6:      # a leading seconds field is ignored
        parts = parts[1:]
    if len(parts) != 5:
        raise ValueError("a cron expression has 5 fields")
    minute, _ = _field(parts[0], 0, 59)
    hour, _ = _field(parts[1], 0, 23)
    dom, dom_r = _field(parts[2], 1, 31)
    month, _ = _field(parts[3], 1, 12)
    dow, dow_r = _field(parts[4], 0, 7)
    if 7 in dow:
        dow = (dow - {7}) | {0}
    return {"minute": sorted(minute), "hour": sorted(hour), "dom": dom, "dom_r": dom_r, "month": month, "dow": dow,
            "dow_r": dow_r}


def _day_matches(spec: Dict[str, Any], day: datetime) -> bool:
    if day.month not in spec["month"]:
        return False
    in_dom = day.day in spec["dom"]
    in_dow = (day.isoweekday() % 7) in spec["dow"]
    if spec["dom_r"] and spec["dow_r"]:
        return in_dom or in_dow     # cron: either restriction matches
    return in_dom and in_dow


def prev_fire(expr: str, now: datetime, tz: Optional[str] = None) -> Optional[datetime]:
    """The latest time at or before `now` matching the cron expression, read in timezone `tz` (UTC when empty or
    unknown). Returned in UTC. Uses croniter when it is installed."""
    zone = _zone(tz)
    local = now.astimezone(zone)
    try:
        from croniter import croniter  # type: ignore

        found = croniter(_ALIASES.get(expr.strip().lower(), expr), local + timedelta(seconds=1)).get_prev(datetime)
        return found.astimezone(timezone.utc)
    except ImportError:
        pass
    spec = parse_cron(expr)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    for back in range(0, 370):
        candidate_day = day - timedelta(days=back)
        if not _day_matches(spec, candidate_day):
            continue
        for h in reversed(spec["hour"]):
            for m in reversed(spec["minute"]):
                at = candidate_day.replace(hour=h, minute=m)
                if at <= local:
                    return at.astimezone(timezone.utc)
    return None


def _zone(tz: Optional[str]):
    if tz:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(tz)
        except Exception:
            pass
    return timezone.utc


def _dt(value: Any) -> Optional[datetime]:
    iso = ts(value)
    return datetime.fromisoformat(iso) if iso else None


# ---------------------------------------------------------------- candidates

def _title(kind: str, dag_id: str, task_id: Optional[str], retrying: bool = False) -> str:
    if kind == "FAILED":
        return f"DAG {dag_id} failed"
    if kind == "RETRIES_EXHAUSTED":
        return f"Task {task_id} in {dag_id} " + ("failed and is retrying" if retrying else "failed after its retries")
    if kind == "LATE":
        return f"DAG {dag_id} is late: no successful run by its expected time"
    if kind == "LONG_RUNNING":
        return f"DAG {dag_id} is running longer than its maximum duration"
    return f"{dag_id}: failed downstream of an open incident"


def _candidate(kind: str, dag: Dict[str, Any], env: Dict[str, Any], task_id: Optional[str], run: Dict[str, Any],
               error: Optional[str], at: Optional[str], occurrence_key: str, retrying: bool = False) -> Dict[str, Any]:
    env_id, dag_id = dag["env_id"], dag["dag_id"]
    excerpt = redact(error)[:4000] if error else None
    sig_source = excerpt if kind in ("RETRIES_EXHAUSTED", "UPSTREAM") else kind.lower()
    return {
        "kind": kind, "env_id": env_id, "dag_id": dag_id, "task_id": task_id, "map_index": run.get("map_index"),
        "run_id": run.get("run_id"), "at": ts(at) or ts(datetime.now(timezone.utc)), "error_excerpt": excerpt,
        "fingerprint": fingerprint(env_id, dag_id, task_id, sig_source),
        "severity": severity(dag.get("criticality"), kind, (env_id, env.get("name")), run.get("run_type"),
                             run.get("external_trigger")),
        "title": _title(kind, dag_id, task_id, retrying), "occurrence_key": occurrence_key,
        "run_type": run.get("run_type"), "criticality": dag.get("criticality"),
    }


def from_task(task: Dict[str, Any], dag: Dict[str, Any], env: Dict[str, Any], settings: Dict[str, Any],
              run: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """A task row (lower-case OPS.TASK_RUN columns) -> RETRIES_EXHAUSTED candidate or None."""
    state = str(task.get("state") or "").lower()
    retrying = False
    if state == "failed":
        pass
    elif state == "up_for_retry" and str(dag.get("criticality") or "").upper() == "CRITICAL" \
            and settings.get("alert_on_retry_for_critical"):
        retrying = True
    else:
        return None
    meta = {**(run or {}), "run_id": task.get("run_id"), "map_index": task.get("map_index")}
    key = f"task:{task.get('run_id')}:{task.get('task_id')}:{task.get('map_index', -1)}:{task.get('try_number', 0)}"
    return _candidate("RETRIES_EXHAUSTED", dag, env, task.get("task_id"), meta, task.get("error_excerpt"),
                      task.get("ended_at") or task.get("updated_at"), key, retrying)


def from_run(run: Dict[str, Any], dag: Dict[str, Any], env: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A DAG run row -> FAILED candidate or None."""
    if str(run.get("state") or "").lower() != "failed":
        return None
    return _candidate("FAILED", dag, env, None, run, None, run.get("ended_at") or run.get("updated_at"),
                      f"run:{run.get('run_id')}")


def late(dag: Dict[str, Any], env: Dict[str, Any], last_success_at: Any, now: datetime) -> Optional[Dict[str, Any]]:
    """LATE when the DAG's latest expected-by time has passed and no run succeeded since the expected-by time before
    it. Paused and inactive DAGs are never late."""
    expr = (dag.get("expected_by_cron") or "").strip()
    if not expr or dag.get("is_paused") or dag.get("is_active") is False:
        return None
    try:
        due = prev_fire(expr, now, dag.get("timezone"))
        if due is None:
            return None
        before = prev_fire(expr, due - timedelta(seconds=1), dag.get("timezone"))
    except (ValueError, KeyError):
        return None
    success = _dt(last_success_at)
    if success is not None and (before is None or success > before):
        return None
    return _candidate("LATE", dag, env, None, {"run_id": None}, None, due.isoformat(), f"late:{due.isoformat()}")


def long_running(run: Dict[str, Any], dag: Dict[str, Any], env: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
    limit = dag.get("max_duration_min")
    if not limit or str(run.get("state") or "").lower() != "running":
        return None
    started = _dt(run.get("started_at"))
    if started is None or now - started <= timedelta(minutes=int(limit)):
        return None
    return _candidate("LONG_RUNNING", dag, env, None, run, None, now.isoformat(), f"long:{run.get('run_id')}")


def candidates(runs: Iterable[Dict[str, Any]], tasks: Iterable[Dict[str, Any]], dags: Dict[Tuple[str, str], Dict[str, Any]],
               envs: Dict[str, Dict[str, Any]], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Failure candidates from changed runs and tasks. Task failures come first, so a DAG run that failed because of
    them is recognised as the same failure by incidents.py."""
    run_meta = {(r["env_id"], r["dag_id"], r["run_id"]): r for r in runs}
    out: List[Dict[str, Any]] = []
    for t in tasks:
        dag = dags.get((t["env_id"], t["dag_id"])) or {"env_id": t["env_id"], "dag_id": t["dag_id"]}
        found = from_task(t, dag, envs.get(t["env_id"], {}), settings, run_meta.get((t["env_id"], t["dag_id"], t["run_id"])))
        if found:
            out.append(found)
    failed_runs = {(c["env_id"], c["dag_id"], c["run_id"]) for c in out}
    for r in run_meta.values():
        dag = dags.get((r["env_id"], r["dag_id"])) or {"env_id": r["env_id"], "dag_id": r["dag_id"]}
        found = from_run(r, dag, envs.get(r["env_id"], {}))
        if found and (r["env_id"], r["dag_id"], r["run_id"]) not in failed_runs:
            out.append(found)
    return out


def successes(runs: Iterable[Dict[str, Any]], tasks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Success signals that may resolve incidents: {env_id, dag_id, task_id (None for a DAG run), run_id, map_index,
    at}. A finished run (any final state) also ends LONG_RUNNING."""
    out: List[Dict[str, Any]] = []
    for t in tasks:
        if str(t.get("state") or "").lower() == "success":
            out.append({"env_id": t["env_id"], "dag_id": t["dag_id"], "task_id": t["task_id"], "run_id": t.get("run_id"),
                        "map_index": t.get("map_index"), "at": ts(t.get("ended_at") or t.get("updated_at")), "finished": True})
    for r in runs:
        state = str(r.get("state") or "").lower()
        if state == "success":
            out.append({"env_id": r["env_id"], "dag_id": r["dag_id"], "task_id": None, "run_id": r.get("run_id"),
                        "map_index": None, "at": ts(r.get("ended_at") or r.get("updated_at")), "finished": True})
        elif state in ("failed",):
            out.append({"env_id": r["env_id"], "dag_id": r["dag_id"], "task_id": None, "run_id": r.get("run_id"),
                        "map_index": None, "at": ts(r.get("ended_at") or r.get("updated_at")), "finished": True,
                        "only_long_running": True})
    return out


def resolves(incident: Dict[str, Any], signal: Dict[str, Any]) -> bool:
    """Does this success resolve the incident? Same environment and DAG; a task-level incident needs a success of the
    same task (the same map index when it is the same run), a DAG-level one a successful DAG run. The success must be
    newer than the incident's last sighting."""
    if incident.get("env_id") != signal.get("env_id") or incident.get("dag_id") != signal.get("dag_id"):
        return False
    if str(incident.get("status") or "").upper() not in ("OPEN", "ACK", "MITIGATED", "MUTED"):
        return False
    kind = str(incident.get("kind") or "").upper()
    if kind == "LONG_RUNNING":   # ends when that run finishes, whatever the clock of the SLA check said
        return signal.get("task_id") is None and signal.get("run_id") == incident.get("run_id")
    if signal.get("only_long_running"):
        return False
    at, seen = _dt(signal.get("at")), _dt(incident.get("last_seen"))
    if at is None or (seen is not None and at <= seen):
        return False
    if incident.get("task_id"):
        if signal.get("task_id") != incident.get("task_id"):
            return False
        if signal.get("run_id") == incident.get("run_id") and incident.get("map_index") is not None \
                and signal.get("map_index") is not None and int(signal["map_index"]) != int(incident["map_index"]):
            return False
        return True
    if signal.get("task_id") is not None:
        return False
    return kind in DAG_KINDS or kind == "UPSTREAM"
