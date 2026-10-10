"""Job leases in OPS.JOB_LEASE: one holder per background job across worker processes and API replicas.

acquire is a compare-and-set: an UPDATE that only matches a free, expired or already-own lease, and execute_count tells
whether this holder got it. A crashed holder's lease expires after LEASE_SECONDS and another one takes over, resuming
from the stored cursor. Renewal runs on a timer while the job works.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

LEASE_SECONDS = 60
RENEW_EVERY = 20.0


def ensure(db: Any, job: str) -> None:
    db.execute("MERGE INTO OPS.JOB_LEASE T USING (SELECT %s AS JOB_NAME) S ON T.JOB_NAME = S.JOB_NAME "
               "WHEN NOT MATCHED THEN INSERT (JOB_NAME) VALUES (S.JOB_NAME)", (job,))


def acquire(db: Any, job: str, holder: str, seconds: int = LEASE_SECONDS) -> bool:
    ensure(db, job)
    count = db.execute_count(
        "UPDATE OPS.JOB_LEASE SET HOLDER = %s, LEASE_UNTIL = DATEADD(second, %s, CURRENT_TIMESTAMP()) "
        "WHERE JOB_NAME = %s AND (HOLDER IS NULL OR HOLDER = %s OR LEASE_UNTIL IS NULL OR LEASE_UNTIL < CURRENT_TIMESTAMP())",
        (holder, int(seconds), job, holder))
    if count > 1:
        # two creators raced in ensure() (Snowflake does not enforce keys): every duplicate is ours now, keep one
        db.execute("DELETE FROM OPS.JOB_LEASE WHERE JOB_NAME = %s AND HOLDER = %s", (job, holder))
        db.execute("INSERT INTO OPS.JOB_LEASE (JOB_NAME, HOLDER, LEASE_UNTIL) "
                   "SELECT %s, %s, DATEADD(second, %s, CURRENT_TIMESTAMP())", (job, holder, int(seconds)))
        return True
    return count == 1


def renew(db: Any, job: str, holder: str, seconds: int = LEASE_SECONDS) -> bool:
    return db.execute_count("UPDATE OPS.JOB_LEASE SET LEASE_UNTIL = DATEADD(second, %s, CURRENT_TIMESTAMP()) "
                            "WHERE JOB_NAME = %s AND HOLDER = %s", (int(seconds), job, holder)) >= 1


def release(db: Any, job: str, holder: str, cursor: Optional[str] = None) -> None:
    db.execute("UPDATE OPS.JOB_LEASE SET HOLDER = NULL, LEASE_UNTIL = NULL, LAST_RUN_AT = CURRENT_TIMESTAMP(), "
               "CURSOR_VALUE = COALESCE(%s, CURSOR_VALUE) WHERE JOB_NAME = %s AND HOLDER = %s", (cursor, job, holder))


class Lease:
    """What `held` yields: true when this holder got the lease. `lost` is set when a renewal fails (another holder took
    over, or the renewal could not be made); the job stops at its next check (still_held)."""

    def __init__(self, got: bool):
        self.got = bool(got)
        self.lost = threading.Event()

    def __bool__(self) -> bool:
        return self.got

    def held(self) -> bool:
        return self.got and not self.lost.is_set()


_current = threading.local()


def still_held() -> bool:
    """False when the lease of the job running in this thread was lost; True outside a lease."""
    current: Optional[Lease] = getattr(_current, "lease", None)
    return current is None or current.held()


@contextmanager
def held(db: Any, job: str, holder: str, seconds: int = LEASE_SECONDS, renew_every: float = RENEW_EVERY) -> Iterator[Lease]:
    """`with held(db, job, me) as got:` runs the body either way; `got` says whether this holder has the lease. While
    it does, a timer renews the lease; when a renewal fails (returns False or raises) the lease counts as lost and
    still_held() turns False, so the body stops at its next check. The lease is released at the end."""
    handle = Lease(acquire(db, job, holder, seconds))
    stop = threading.Event()
    timer: Optional[threading.Thread] = None
    if handle:
        def keep() -> None:
            while not stop.wait(renew_every):
                try:
                    ok = renew(db, job, holder, seconds)
                except Exception:
                    ok = False   # cannot prove we still hold it: stop rather than risk two holders
                if not ok:
                    handle.lost.set()
                    return

        timer = threading.Thread(target=keep, name=f"lease:{job}", daemon=True)
        timer.start()
    previous = getattr(_current, "lease", None)
    if handle:
        _current.lease = handle
    try:
        yield handle
    finally:
        _current.lease = previous
        stop.set()
        if handle:
            try:
                release(db, job, holder)
            except Exception:
                pass  # it expires on its own
