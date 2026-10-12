"""In-process calls to this API as the signed-in person (the copilot's tools).

The request goes through the whole app, middleware included, with the caller's own session and work role headers,
so privilege checks, change requests, store queueing and four-eyes apply exactly as for a click in the UI. This is the
mechanism governance.approve_request uses to replay an approved call, without the replay token: nothing is pre-approved.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Tuple

FORBIDDEN = ("/api/copilot/", "/api/governance/", "/api/admin/", "/api/auth/")
FORWARDED = ("x-aip-session", "x-aip-role")


def caller(request_headers: Mapping[str, str]):
    """A `call(method, path, body=None, params=None) -> (status, data)` bound to the caller's headers."""
    import httpx

    headers = {h: request_headers[h] for h in FORWARDED if request_headers.get(h)}
    headers["content-type"] = "application/json"

    async def call(method: str, path: str, body: Any = None, params: Optional[dict] = None) -> Tuple[int, Any]:
        if not path.startswith("/api/") or path.startswith(FORBIDDEN) or ".." in path:
            return 400, {"detail": f"the copilot cannot call {path}"}
        from app.main import app as api_app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://copilot",
                                     timeout=600) as client:
            response = await client.request(method.upper(), path, headers=headers, params=params,
                                            content=json.dumps(body, default=str) if body is not None else None)
        try:
            data = response.json()
        except ValueError:
            data = {"detail": response.text[:2000]}
        return response.status_code, data

    return call
