"""Ops worker: background jobs for Airflow capture, run apart from the API as `python -m app.worker` (from apps/api,
on the API host or in its own container with the same environment).

Jobs, each guarded by an OPS.JOB_LEASE so exactly one holder runs it at a time across processes and replicas:
  poll:<env_id>  incremental capture of one MWAA environment, every POLL_SECONDS (services.ops.store.poll_env)
  retention      purges raw OPS.EVENT rows after 30 days (every 6 hours)
  outbox, escalate, sla  hooks filled in by the incidents work (PR O2); no-ops for now
The worker uses system_db(): the dev session in dev mode, else the key-pair service user (AIP_SERVICE_USER,
AIP_SERVICE_KEY_PATH). AWS credentials come from the host's default chain (role, AWS_PROFILE). A heartbeat row
(JOB_NAME 'worker') shows when a worker last looped. SIGINT or SIGTERM stops it after the current job.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.db import SnowflakeSessionError, system_db  # noqa: E402
from services.ops import lease  # noqa: E402
from services.ops.mwaa import MwaaError  # noqa: E402
from services.ops.redact import redact  # noqa: E402

log = logging.getLogger("gdp.ops.worker")
TICK_SECONDS = 5.0
RETENTION_EVERY = 6 * 3600
EVENT_RETENTION_DAYS = 30
HOLDER = f"{socket.gethostname()[:60]}:{os.getpid()}"
_manual: Dict[str, threading.Thread] = {}
_manual_lock = threading.Lock()


def poll_job(env_id: str) -> str:
    return f"poll:{env_id}"


def load_env(db: Any, env_id: str) -> Optional[Dict[str, Any]]:
    found = db.query("""SELECT ENV_ID, NAME, MWAA_ENV, REGION, ENABLED, POLL_SECONDS, CURSOR_VALUE
                          FROM OPS.AIRFLOW_ENV WHERE ENV_ID = %s""", (env_id,))
    return found[0] if found else None


def due_envs(db: Any) -> List[Dict[str, Any]]:
    """Enabled environments whose last attempt is older than their poll interval (database clock, so every worker
    agrees)."""
    return db.query("""SELECT ENV_ID, NAME, MWAA_ENV, REGION, POLL_SECONDS, CURSOR_VALUE FROM OPS.AIRFLOW_ENV
                        WHERE ENABLED AND (LAST_ATTEMPT_AT IS NULL
                              OR DATEDIFF(second, LAST_ATTEMPT_AT, CURRENT_TIMESTAMP()) >= COALESCE(POLL_SECONDS, 300))
                        ORDER BY LAST_ATTEMPT_AT NULLS FIRST""")


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, MwaaError):
        return str(exc)
    return redact(f"{type(exc).__name__}: {exc}")[:1000]


def run_poll(db: Any, env_id: str, holder: str = HOLDER, poller: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Poll one environment under its lease. {ran: False} when another holder is polling it right now."""
    from services.ops.store import poll_env

    poller = poller or poll_env
    with lease.held(db, poll_job(env_id), holder) as got:
        if not got:
            return {"ran": False, "detail": "another worker is polling this environment right now"}
        env = load_env(db, env_id)
        if env is None:
            return {"ran": False, "detail": "environment not found"}
        db.execute("UPDATE OPS.AIRFLOW_ENV SET LAST_ATTEMPT_AT = CURRENT_TIMESTAMP() WHERE ENV_ID = %s", (env_id,))
        try:
            summary = poller(db, env)
        except Exception as exc:
            message = _error_text(exc)
            try:
                db.execute("UPDATE OPS.AIRFLOW_ENV SET LAST_ERROR = %s WHERE ENV_ID = %s", (message[:2000], env_id))
            except Exception:
                pass
            log.warning("poll %s failed: %s", env_id, message)
            return {"ran": True, "ok": False, "error": message}
        return {"ran": True, "ok": True, **summary}


def poll_now(env_id: str, db_factory: Callable[[], Any] = system_db) -> Dict[str, Any]:
    """Start one poll in a background thread for the API ("Poll now"). The lease makes it safe next to a running
    worker: whoever holds it polls, the other skips."""
    with _manual_lock:
        running = _manual.get(env_id)
        if running and running.is_alive():
            return {"started": False, "detail": "A poll of this environment is already running from this API host."}
        try:
            db = db_factory()
        except SnowflakeSessionError as exc:
            return {"started": False, "detail": str(exc)}

        def work() -> None:
            try:
                run_poll(db, env_id, holder=f"api:{HOLDER}")
            except Exception as exc:  # never kill the API process
                log.warning("manual poll %s failed: %s", env_id, _error_text(exc))

        thread = threading.Thread(target=work, name=f"ops-poll:{env_id}", daemon=True)
        _manual[env_id] = thread
        thread.start()
    return {"started": True, "detail": "Polling started. New runs show up when it finishes (usually under a minute)."}


# ---------------------------------------------------------------- hooks for PR O2

def outbox(db: Any) -> None:
    """Send queued Teams notifications (PR O2)."""


def escalate(db: Any) -> None:
    """Escalate unacknowledged incidents (PR O2)."""


def sla(db: Any) -> None:
    """LATE and LONG_RUNNING detection (PR O2)."""


def retention(db: Any) -> None:
    from services.ops.store import purge_events

    purge_events(db, EVENT_RETENTION_DAYS)


# ---------------------------------------------------------------- loop

class Worker:
    def __init__(self, db_factory: Callable[[], Any] = system_db, tick: float = TICK_SECONDS):
        self.db_factory = db_factory
        self.tick = tick
        self.stop = threading.Event()
        self._last: Dict[str, float] = {}

    def _every(self, name: str, seconds: float) -> bool:
        now = time.monotonic()
        if now - self._last.get(name, -1e18) >= seconds:
            self._last[name] = now
            return True
        return False

    def _guarded(self, db: Any, job: str, fn: Callable[[Any], None]) -> None:
        with lease.held(db, job, HOLDER) as got:
            if got:
                fn(db)

    def once(self) -> None:
        db = self.db_factory()
        db.execute("""MERGE INTO OPS.JOB_LEASE T USING (SELECT 'worker' AS JOB_NAME) S ON T.JOB_NAME = S.JOB_NAME
                      WHEN MATCHED THEN UPDATE SET LAST_RUN_AT = CURRENT_TIMESTAMP(), CURSOR_VALUE = %s
                      WHEN NOT MATCHED THEN INSERT (JOB_NAME, LAST_RUN_AT, CURSOR_VALUE) VALUES ('worker', CURRENT_TIMESTAMP(), %s)""",
                   (HOLDER, HOLDER))
        for env in due_envs(db):
            if self.stop.is_set():
                return
            result = run_poll(db, env["env_id"])
            if result.get("ran"):
                log.info("poll %s: %s", env["env_id"], "ok" if result.get("ok") else result.get("error"))
        for name, fn in (("outbox", outbox), ("escalate", escalate), ("sla", sla)):
            if self.stop.is_set():
                return
            self._guarded(db, name, fn)
        if self._every("retention", RETENTION_EVERY):
            self._guarded(db, "retention", retention)

    def run(self) -> None:
        log.info("ops worker %s started", HOLDER)
        while not self.stop.is_set():
            try:
                self.once()
            except SnowflakeSessionError as exc:
                log.error("no Snowflake session: %s", exc)
                self.stop.wait(30)
            except Exception as exc:
                log.error("worker loop failed: %s", _error_text(exc))
            self.stop.wait(self.tick)
        log.info("ops worker %s stopped", HOLDER)


def main() -> None:
    logging.basicConfig(level=os.environ.get("AIP_WORKER_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = Worker()

    def stop(signum, _frame) -> None:
        log.info("signal %s: stopping after the current job", signum)
        worker.stop.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    worker.run()


if __name__ == "__main__":
    main()
