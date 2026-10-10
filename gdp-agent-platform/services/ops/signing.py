"""Signed push from the Airflow listener plugin (pure).

Headers: X-GDP-Env, X-GDP-Timestamp (unix seconds), X-GDP-Event-Id, X-GDP-Signature = hex HMAC-SHA256 of
f"{timestamp}.{event id}.{raw body}" keyed with the environment's push secret. The event id is signed so a captured
request cannot be replayed under a fresh event id inside the clock skew window. The plugin
(airflow_plugins/gdp_listener) has its own copy of `sign` so it can ship without this package; tests keep the two
identical.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Optional, Tuple

HEADER_NAMES = ["X-GDP-Env", "X-GDP-Timestamp", "X-GDP-Event-Id", "X-GDP-Signature"]
MAX_SKEW_SECONDS = 300
MAX_BODY_BYTES = 64 * 1024
EVENT_ID = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")
ENV_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")


def sign(secret: str, timestamp: str, event_id: str, raw: bytes) -> str:
    message = str(timestamp).encode("ascii") + b"." + str(event_id).encode("utf-8") + b"." + raw
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def check_headers(env_id: Optional[str], timestamp: Optional[str], event_id: Optional[str], signature: Optional[str],
                  now: Optional[float] = None, max_skew: int = MAX_SKEW_SECONDS) -> Tuple[bool, str]:
    """Shape and clock checks that need no secret: (ok, reason)."""
    if not (env_id and timestamp and event_id and signature):
        return False, "missing signature headers"
    if not ENV_ID.match(env_id):
        return False, "invalid environment id"
    if not EVENT_ID.match(event_id):
        return False, "invalid event id"
    if not re.fullmatch(r"[0-9a-fA-F]{64}", signature):
        return False, "invalid signature format"
    try:
        stamp = int(timestamp)
    except ValueError:
        return False, "invalid timestamp"
    if abs((time.time() if now is None else now) - stamp) > max_skew:
        return False, "timestamp outside the allowed clock skew"
    return True, ""


def verify(secret: str, timestamp: str, event_id: str, raw: bytes, signature: str) -> bool:
    """Constant-time comparison of the expected and the given signature."""
    return hmac.compare_digest(sign(secret, timestamp, event_id, raw), (signature or "").lower())
