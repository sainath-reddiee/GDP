"""Ops worker: background jobs for Airflow capture, run apart from the API as `python -m app.worker` (from apps/api,
on the API host or in its own container with the same environment).

Jobs, each guarded by an OPS.JOB_LEASE so exactly one holder runs it at a time across processes and replicas:
  poll:<env_id>  incremental capture of one MWAA environment, every POLL_SECONDS (services.ops.store.poll_env)
  detect         incidents for runs and task runs loaded since its cursor (every tick, after the polls)
  outbox         Teams cards and Jira bot actions from OPS.NOTIFICATION, with retries (every 30 s)
  escalate       unacknowledged incidents and ended mutes (every 60 s)
  sla            LATE and LONG_RUNNING detection (every 120 s)
  jira_sync      incidents whose ticket is Done in Jira become MITIGATED (every 5 min)
  diagnose       AI diagnosis of new OPEN incidents (settings ai_auto, ai_severities; at most 5 per run; every 60 s)
  digest         the weekly reliability card per team, Mondays from 09:00 UTC (setting weekly_digest; every 15 min)
  retention      purges raw OPS.EVENT rows after 30 days (every 6 hours)
The worker uses worker_db(): its own non-shared connection (the named dev connection in dev mode, else the key-pair
service user from AIP_SERVICE_USER, AIP_SERVICE_KEY_PATH), so incident writes run in transactions. AWS credentials come from the host's default chain (role, AWS_PROFILE). A heartbeat row
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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.db import SnowflakeSessionError, system_db, worker_db  # noqa: E402
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


# ---------------------------------------------------------------- incidents (PR O2)

DETECT_OVERLAP = timedelta(minutes=2)
INTERVALS = {"outbox": 30, "escalate": 60, "sla": 120, "jira_sync": 300, "diagnose": 60, "digest": 900}


def _store(db: Any):
    from services.ops.incidents import SqlStore

    return SqlStore(db)


def _bot(store: Any):
    from services.ops.tickets import bot_client

    return bot_client(store.jira_config())


EXACT = "|exact"   # cursor suffix: the last read was cut at its limit, resume exactly there (no overlap)


def detect_window(cursor: Optional[str]) -> Tuple[Optional[str], bool]:
    """(since, inclusive) for the next detect read. Normally the cursor minus a small overlap (rows committed late);
    after a read cut at its limit, exactly from the cursor, rows at it included, so a backlog larger than the overlap
    window still moves forward."""
    if not cursor:
        return None, False
    exact = cursor.endswith(EXACT)
    stamp = cursor[:-len(EXACT)] if exact else cursor
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None, False
    return (stamp, True) if exact else ((moment - DETECT_OVERLAP).isoformat(), False)


def detect(db: Any) -> Dict[str, Any]:
    """Incidents for everything loaded since the cursor (minus a small overlap; applying a run twice changes
    nothing). The cursor is the newest LOADED_AT processed, kept on the 'detect' lease row; when a read was cut at its
    limit it stops at the last row read of the cut list, so nothing is skipped."""
    from services.ops.incidents import process

    store = _store(db)
    found = db.query("SELECT CURSOR_VALUE FROM OPS.JOB_LEASE WHERE JOB_NAME = 'detect'")
    cursor = found[0].get("cursor_value") if found else None
    since, inclusive = detect_window(cursor)
    runs, tasks, newest, truncated = store.changed_since(since, inclusive=inclusive)
    summary = process(store, runs, tasks)   # never raises for one bad candidate: the cursor moves on
    value = (newest + EXACT) if newest and truncated else newest
    if value and value != cursor:
        db.execute("UPDATE OPS.JOB_LEASE SET CURSOR_VALUE = %s WHERE JOB_NAME = 'detect'", (value,))
    return summary


def detect_rows(db: Any, runs: List[Dict[str, Any]], tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Incidents for rows just stored (the push ingest): cheap and idempotent, the detect job sees them again."""
    from services.ops.incidents import process

    return process(_store(db), runs, tasks)


def outbox(db: Any) -> None:
    """Send queued Teams cards and Jira bot actions."""
    from services.ops.notify import run_outbox
    from services.ops.tickets import handle

    store = _store(db)
    settings = store.settings()
    bot = _bot(store)
    counts = run_outbox(db, settings, jira=lambda _db, row: handle(store, row, bot, settings))
    if any(counts.values()):
        log.info("outbox: %s", counts)


def escalate(db: Any) -> None:
    """Escalate unacknowledged incidents and reopen incidents whose mute ended."""
    from services.ops.incidents import escalate as run

    result = run(_store(db))
    if any(result.values()):
        log.info("escalate: %s", result)


def sla(db: Any) -> None:
    """LATE and LONG_RUNNING detection."""
    from services.ops.incidents import sla_check

    sla_check(_store(db))


def jira_sync(db: Any) -> None:
    from services.ops.tickets import sync_done

    store = _store(db)
    changed = sync_done(store, _bot(store))
    if changed:
        log.info("jira_sync: %s incidents mitigated", changed)


def diagnose(db: Any) -> None:
    """AI diagnosis of new incidents (PR O3)."""
    from services.ops.diagnose import auto_diagnose

    result = auto_diagnose(db)
    if any(result.values()):
        log.info("diagnose: %s", result)


def digest(db: Any) -> None:
    """The weekly reliability digest per team (idempotent per ISO week)."""
    from services.ops.reliability import run_digest

    result = run_digest(db)
    if result.get("queued"):
        log.info("digest: %s", result)


def retention(db: Any) -> None:
    from services.ops.store import purge_events

    purge_events(db, EVENT_RETENTION_DAYS)


# ---------------------------------------------------------------- loop

class Worker:
    def __init__(self, db_factory: Callable[[], Any] = worker_db, tick: float = TICK_SECONDS):
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

    def _guarded(self, db: Any, job: str, fn: Callable[[Any], Any]) -> None:
        with lease.held(db, job, HOLDER) as got:
            if got:
                fn(db)

    def _safe(self, db: Any, job: str, fn: Callable[[Any], Any]) -> None:
        """One job under its lease; a failure (for example V031 not applied yet) is logged and the loop goes on."""
        try:
            self._guarded(db, job, fn)
        except SnowflakeSessionError:
            raise
        except Exception as exc:
            log.warning("%s failed: %s", job, _error_text(exc))

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
        self._safe(db, "detect", detect)
        for name, fn in (("outbox", outbox), ("escalate", escalate), ("sla", sla), ("jira_sync", jira_sync),
                         ("diagnose", diagnose), ("digest", digest)):
            if self.stop.is_set():
                return
            if self._every(name, INTERVALS[name]):
                self._safe(db, name, fn)
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
