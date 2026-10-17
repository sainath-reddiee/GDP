"""Signed preview tokens: a publish must follow a dry run of exactly the same content by the same person.

The dry run returns a token bound to what it showed (ids, branch, a hash of the content) and to the person; the real
publish checks it. A token lasts TOKEN_SECONDS; a replay of an approved change request may use it a little longer.
`purpose` keeps tokens of one flow from being accepted by another.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

TOKEN_SECONDS = 600
REPLAY_SECONDS = 3600
_FALLBACK_KEY = "aip-preview-token"


class TokenError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message, self.status = message, status


def _key(purpose: str, key: Optional[str]) -> bytes:
    if key is None:
        from services.ops.notify import secret_key

        key = secret_key() or _FALLBACK_KEY
    return f"{purpose}:{key}".encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make(bound: Dict[str, Any], user: str, purpose: str, now: Optional[float] = None, key: Optional[str] = None) -> str:
    payload = {**bound, "u": str(user or "").upper(), "x": int((now or time.time()) + TOKEN_SECONDS)}
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{hmac.new(_key(purpose, key), body.encode('ascii'), hashlib.sha256).hexdigest()}"


def check(token: Optional[str], bound: Dict[str, Any], user: str, purpose: str, now: Optional[float] = None,
          replay: bool = False, key: Optional[str] = None) -> None:
    """TokenError unless the token was made for this content, by this person (unless replayed), and has not expired."""
    if not token or "." not in token:
        raise TokenError("Run a dry run first: publishing needs the preview_token it returns.", 422)
    body, _, sig = token.rpartition(".")
    if not hmac.compare_digest(sig, hmac.new(_key(purpose, key), body.encode("ascii"), hashlib.sha256).hexdigest()):
        raise TokenError("The preview token is not valid. Run the dry run again.", 422)
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        raise TokenError("The preview token is not valid. Run the dry run again.", 422) from None
    limit = int(payload.get("x") or 0) + (REPLAY_SECONDS - TOKEN_SECONDS if replay else 0)
    if (now or time.time()) > limit:
        raise TokenError("The preview expired (it is valid for 10 minutes). Run the dry run again.", 409)
    if any(payload.get(k) != v for k, v in bound.items()):
        raise TokenError("The preview token belongs to other content or another branch. Run the dry run again.", 409)
    if not replay and str(payload.get("u") or "") != str(user or "").upper():
        raise TokenError("The preview was made by someone else. Run the dry run yourself.", 409)
