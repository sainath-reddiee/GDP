"""Server-sent-events proxy to the Cortex Agent REST API.

POST /api/v2/databases/{db}/schemas/{schema}/agents/{name}:run, authenticated with the signed-in
user's programmatic access token so the agent's tools run under that user's privileges.
Thinking events are dropped; the UI only sees status, tool, text and error events.
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

from app.db import DATABASE, Db

AGENT_NAME = os.environ.get("AIP_AGENT_NAME", "DATA_ENGINEERING_SUPERVISOR")
AGENT_SCHEMA = os.environ.get("AIP_AGENT_SCHEMA", "CORE")
HIDDEN_EVENT_PREFIX = "response.thinking"


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _event_name(block: str) -> str:
    for line in block.splitlines():
        if line.startswith("event:"):
            return line[len("event:"):].strip()
    return "message"


async def stream_agent(db: Db, run_id: str | None, message: str) -> AsyncIterator[str]:
    if not AGENT_NAME:
        yield sse("response.status", {"status": "not_configured",
                                      "message": "No Cortex Agent is deployed yet (AIP_AGENT_NAME is unset)."})
        yield sse("done", {})
        return
    if not db.token:
        yield sse("response.status", {"status": "not_configured",
                                      "message": "The agent needs a per-user token; sign in with AIP_AUTH=pat."})
        yield sse("done", {})
        return

    text = f"[run_id: {run_id}]\n{message}" if run_id else message
    url = f"https://{db.host}/api/v2/databases/{DATABASE}/schemas/{AGENT_SCHEMA}/agents/{AGENT_NAME}:run"
    headers = {
        "Authorization": f"Bearer {db.token}",
        "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": text}]}], "stream": True}

    async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as client:
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", "replace")[:2000]
                yield sse("error", {"status": resp.status_code, "message": detail})
                return
            block: list[str] = []
            async for line in resp.aiter_lines():
                if line:
                    block.append(line)
                    continue
                if block:
                    raw = "\n".join(block)
                    block = []
                    if not _event_name(raw).startswith(HIDDEN_EVENT_PREFIX):
                        yield raw + "\n\n"
    yield sse("done", {})
