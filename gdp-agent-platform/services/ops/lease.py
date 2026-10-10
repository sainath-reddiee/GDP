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


@contextmanager
def held(db: Any, job: str, holder: str, seconds: int = LEASE_SECONDS, renew_every: float = RENEW_EVERY) -> Iterator[bool]:
    """`with held(db, job, me) as got:` runs the body either way; `got` says whether this holder has the lease. While
    it does, a timer renews the lease; the lease is released at the end."""
    got = acquire(db, job, holder, seconds)
    stop = threading.Event()
    timer: Optional[threading.Thread] = None
    if got:
        def keep() -> None:
            while not stop.wait(renew_every):
                try:
                    if not renew(db, job, holder, seconds):
                        return
                except Exception:
                    pass

        timer = threading.Thread(target=keep, name=f"lease:{job}", daemon=True)
        timer.start()
    try:
        yield got
    finally:
        stop.set()
        if got:
            try:
                release(db, job, holder)
            except Exception:
                pass  # it expires on its own
