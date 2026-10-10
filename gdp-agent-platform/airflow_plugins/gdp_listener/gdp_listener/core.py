"""GDP push for Airflow: payloads, signing and a fire-and-forget sender. No Airflow import, so it is unit-testable.

Every function here is defensive: the listener calls it inside Airflow's scheduler and task processes, and nothing may
ever raise into Airflow, block a task or write a secret to a log. The module logs nothing.
"""

from __future__ import annotations

import atexit
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

TIMEOUT_SECONDS = 3.0
MAX_BODY_BYTES = 60 * 1024
ERROR_MAX = 2000
_pending: list = []
_pending_lock = threading.Lock()


def _drain() -> None:
    """At interpreter exit, give events still in flight up to one timeout in total (a task process often ends right
    after its success hook). Processes that exit hard skip this; the poller captures those runs."""
    deadline = time.time() + TIMEOUT_SECONDS
    with _pending_lock:
        threads = list(_pending)
    for thread in threads:
        thread.join(max(0.0, deadline - time.time()))


atexit.register(_drain)


def config() -> Optional[Dict[str, str]]:
    """{url, env_id, secret} from GDP_INGEST_URL, GDP_ENV_ID and GDP_PUSH_SECRET, or from the Airflow configuration
    section [gdp] (ingest_url, env_id, push_secret), which MWAA exposes as AIRFLOW__GDP__* variables. None when any is
    missing, so an unconfigured plugin does nothing."""
    env = os.environ
    url = env.get("GDP_INGEST_URL") or env.get("AIRFLOW__GDP__INGEST_URL") or ""
    env_id = env.get("GDP_ENV_ID") or env.get("AIRFLOW__GDP__ENV_ID") or ""
    secret = env.get("GDP_PUSH_SECRET") or env.get("AIRFLOW__GDP__PUSH_SECRET") or ""
    if not (url.strip() and env_id.strip() and secret.strip()):
        return None
    return {"url": url.strip(), "env_id": env_id.strip(), "secret": secret.strip()}


def sign(secret: str, timestamp: str, event_id: str, raw: bytes) -> str:
    """Hex HMAC-SHA256 of f"{timestamp}.{event id}.{raw body}"; identical to services.ops.signing.sign on the platform
    side."""
    message = str(timestamp).encode("ascii") + b"." + str(event_id).encode("utf-8") + b"." + raw
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def headers(env_id: str, secret: str, raw: bytes, now: Optional[float] = None, event_id: Optional[str] = None) -> Dict[str, str]:
    stamp = str(int(time.time() if now is None else now))
    event = event_id or uuid.uuid4().hex
    return {"Content-Type": "application/json", "X-GDP-Env": env_id, "X-GDP-Timestamp": stamp,
            "X-GDP-Event-Id": event, "X-GDP-Signature": sign(secret, stamp, event, raw)}


def failed_state(task_instance: Any) -> str:
    """The state to report from on_task_instance_failed: the task instance's own state (up_for_retry while retries
    remain, failed at the last try), 'failed' only when it has none."""
    try:
        return _state(getattr(task_instance, "state", None)) or "failed"
    except Exception:
        return "failed"


def iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _state(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).lower()
    return text.split(".", 1)[1] if text.startswith(("dagrunstate.", "taskinstancestate.")) else text


def _first(obj: Any, *names: str) -> Any:
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            value = None
        if value is not None:
            return value
    return None


def dag_run_payload(dag_run: Any, state: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Compact event for a DAG run (Airflow 2.7+ DagRun or Airflow 3 DagRun)."""
    run_type = _first(dag_run, "run_type")
    triggered_by = _first(dag_run, "triggered_by")
    external = _first(dag_run, "external_trigger")
    if external is None:
        external = str(getattr(triggered_by, "value", triggered_by) or "").lower() in ("rest_api", "ui", "cli", "operator")
    return {
        "kind": "dag_run", "dag_id": _first(dag_run, "dag_id"), "run_id": _first(dag_run, "run_id"),
        "run_type": _state(run_type), "state": state,
        "logical_date": iso(_first(dag_run, "logical_date", "execution_date", "run_after")),
        "start": iso(_first(dag_run, "start_date")), "end": iso(_first(dag_run, "end_date")) if state != "running" else None,
        "external_trigger": bool(external),
        "updated_at": iso(now or datetime.now(timezone.utc)),
    }


def task_payload(task_instance: Any, state: str, error: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Compact event for a task instance (Airflow 2.7+ TaskInstance or Airflow 3 RuntimeTaskInstance)."""
    task = _first(task_instance, "task")
    operator = _first(task_instance, "operator") or (type(task).__name__ if task is not None else None)
    map_index = _first(task_instance, "map_index")
    end = _first(task_instance, "end_date")
    stamp = now or datetime.now(timezone.utc)
    body = {
        "kind": "task_instance", "dag_id": _first(task_instance, "dag_id"), "run_id": _first(task_instance, "run_id"),
        "task_id": _first(task_instance, "task_id"), "map_index": int(map_index) if map_index is not None else -1,
        "try_number": int(_first(task_instance, "try_number") or 0), "state": state, "operator": operator,
        "start": iso(_first(task_instance, "start_date")),
        "end": iso(end or (stamp if state != "running" else None)),
        "duration": _first(task_instance, "duration"), "hostname": _first(task_instance, "hostname"),
        "updated_at": iso(stamp),
    }
    if error is not None:
        text = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
        body["error"] = text[-ERROR_MAX:]
    return body


def encode(payload: Dict[str, Any]) -> bytes:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    if len(raw) > MAX_BODY_BYTES and payload.get("error"):
        payload = {**payload, "error": str(payload["error"])[-1000:]}
        raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    return raw


def post(url: str, raw: bytes, request_headers: Dict[str, str], timeout: float = TIMEOUT_SECONDS) -> int:
    request = urllib.request.Request(url, data=raw, headers=request_headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (the URL is the operator's config)
        return int(response.status)


def emit(payload: Dict[str, Any], cfg: Optional[Dict[str, str]] = None, background: bool = True) -> bool:
    """Sign and send one event without waiting (a daemon thread). Returns False when not configured. Never raises."""
    try:
        cfg = cfg or config()
        if not cfg:
            return False
        raw = encode(payload)
        request_headers = headers(cfg["env_id"], cfg["secret"], raw)

        def send() -> None:
            try:
                post(cfg["url"], raw, request_headers)
            except Exception:
                pass  # the poller captures the run anyway

        if background:
            thread = threading.Thread(target=send, name="gdp-listener", daemon=True)
            with _pending_lock:
                _pending[:] = [t for t in _pending if t.is_alive()]
                _pending.append(thread)
            thread.start()
        else:
            send()
        return True
    except Exception:
        return False


def safe(fn, *args: Any, **kwargs: Any) -> None:
    """Call fn and swallow anything it raises."""
    try:
        fn(*args, **kwargs)
    except Exception:
        pass
