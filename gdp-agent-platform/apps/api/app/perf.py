"""Request timing and short read caches for the API.

Timing: every response carries a Server-Timing header (total time, and the time and number of Snowflake statements
it ran), visible in the browser's network panel. Requests slower than AIP_SLOW_MS (default 800) are logged to the
"aip.timing" logger with their statement count; AIP_TIMING_LOG=1 logs every request.

Read caches: read_cache() keeps a reader's answer for a few seconds, per session, Snowflake user and role (roles see
different rows) and per "generation". Any successful write (POST, PUT, PATCH, DELETE) starts a new generation, so nobody reads
a cached answer from before their own change; other users' changes show up within the TTL at most.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Tuple

from starlette.requests import Request

log = logging.getLogger("aip.timing")
SLOW_MS = float(os.environ.get("AIP_SLOW_MS", "800"))
LOG_ALL = os.environ.get("AIP_TIMING_LOG") == "1"
WRITES = {"POST", "PUT", "PATCH", "DELETE"}
MAX_ENTRIES = 500

# per request: [statements, seconds in Snowflake]
_stats: contextvars.ContextVar = contextvars.ContextVar("aip_sql_stats", default=None)
_lock = threading.Lock()
_cache: Dict[Tuple[Any, ...], Tuple[float, Any]] = {}
_generation = {"n": 0}


def instrument(db_class) -> None:
    """Count and time the Snowflake statements each request runs (wraps Db.query, execute, execute_count, call)."""
    for name in ("query", "execute", "execute_count", "call", "query_with_id"):
        original = getattr(db_class, name, None)
        if original is None or getattr(original, "_timed", False):
            continue

        def timed(self, *args, _original=original, **kwargs):
            stats = _stats.get()
            if stats is None:
                return _original(self, *args, **kwargs)
            start = time.perf_counter()
            try:
                return _original(self, *args, **kwargs)
            finally:
                with _lock:
                    stats[0] += 1
                    stats[1] += time.perf_counter() - start

        timed._timed = True  # type: ignore[attr-defined]
        setattr(db_class, name, timed)


def server_timing(total_s: float, statements: int, sql_s: float) -> str:
    return f'app;dur={total_s * 1000:.1f}, sf;desc="{statements} statements";dur={sql_s * 1000:.1f}'


async def middleware(request: Request, call_next):
    """Outermost middleware: times the request, then starts a new cache generation after a successful write."""
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    stats = [0, 0.0]
    token = _stats.set(stats)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        _stats.reset(token)
    total = time.perf_counter() - start
    if request.method in WRITES and response.status_code < 400:
        bump()
    response.headers["Server-Timing"] = server_timing(total, stats[0], stats[1])
    if LOG_ALL or total * 1000 >= SLOW_MS:
        log.warning("%s %s %d %.0fms %d statements %.0fms in Snowflake", request.method, request.url.path,
                    response.status_code, total * 1000, stats[0], stats[1] * 1000)
    return response


def bump() -> None:
    with _lock:
        _generation["n"] += 1
        _cache.clear()


def read_cache(db: Any, key: Tuple[Any, ...] | str, ttl: float, loader: Callable[[], Any]) -> Any:
    """The loader's answer for this user and role, reused for `ttl` seconds within the current generation."""
    # per session object as well: a session's user and role never change under it
    full = (id(db), getattr(db, "user", "") or "", getattr(db, "role", "") or "", _generation["n"], key)
    now = time.time()
    with _lock:
        hit = _cache.get(full)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = loader()
    with _lock:
        if full[3] == _generation["n"]:  # a write in the meantime makes this answer stale: do not keep it
            if len(_cache) >= MAX_ENTRIES:
                for stale in sorted(_cache, key=lambda k: _cache[k][0])[: MAX_ENTRIES // 5]:
                    _cache.pop(stale, None)
            _cache[full] = (time.time(), value)
    return value


def cached(name: str, ttl: float):
    """read_cache() for a route or helper that takes `db`: keyed by its other arguments. FastAPI still sees the
    original signature (functools.wraps), and a direct call with positional arguments is keyed the same way."""
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            db = bound.arguments.get("db")
            if db is None:
                return fn(*args, **kwargs)
            key = (name, tuple((k, repr(v)) for k, v in bound.arguments.items() if k != "db"))
            return read_cache(db, key, ttl, lambda: fn(*args, **kwargs))
        return wrapper
    return deco
