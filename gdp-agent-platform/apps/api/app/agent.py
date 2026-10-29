"""Server-sent-events proxy to the Cortex Agent REST API.

POST /api/v2/databases/{db}/schemas/{schema}/agents/{name}:run, authenticated with the signed-in
user's programmatic access token so the agent's tools run under that user's privileges.
Thinking events are dropped; the UI only sees status, tool, text and error events.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Callable

import httpx

from app.db import DATABASE, Db

log = logging.getLogger("gdp.agent")

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


def _auth_headers(db: Any) -> dict[str, str]:
    if hasattr(db, "rest_headers"):
        return db.rest_headers()
    token = getattr(db, "token", None)
    if token:
        return {
            "Authorization": f"Bearer {token}",
            "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
        }
    conn = getattr(db, "conn", None)
    sess_token = getattr(getattr(conn, "rest", None), "token", None)
    if sess_token:
        return {"Authorization": f'Snowflake Token="{sess_token}"'}
    return {}


async def stream_agent(db: Db, run_id: str | None, message: str) -> AsyncIterator[str]:
    if not AGENT_NAME:
        yield sse("response.status", {"status": "not_configured",
                                      "message": "No Cortex Agent is deployed yet (AIP_AGENT_NAME is unset)."})
        yield sse("done", {})
        return
    auth = _auth_headers(db)
    if not auth:
        yield sse("response.status", {"status": "not_configured",
                                      "message": "No Snowflake REST token available on current session."})
        yield sse("done", {})
        return

    text = f"[run_id: {run_id}]\n{message}" if run_id else message
    url = f"https://{db.host}/api/v2/databases/{DATABASE}/schemas/{AGENT_SCHEMA}/agents/{AGENT_NAME}:run"
    headers = {
        **auth,
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


def run_supervisor(db: Any, run_id: str | None, message: str, timeout_ms: int = 90_000) -> str | None:
    """Invoke DATA_ENGINEERING_SUPERVISOR synchronously and return its text response (or None if unavailable)."""
    if not AGENT_NAME or not _auth_headers(db):
        return None
    from services.resolve import agent as cortex_agent

    text = f"[run_id: {run_id}]\n{message}" if run_id else message
    try:
        return cortex_agent.run(DATABASE, AGENT_SCHEMA, AGENT_NAME, text, timeout_ms=timeout_ms, db=db)
    except Exception as exc:
        log.warning("DATA_ENGINEERING_SUPERVISOR call skipped/failed: %s", exc)
        return None


def invoke_supervisor_stage(
    db: Any,
    run_id: str,
    stage_tool: str,
    fallback_fn: Callable[[], Any],
    expected_states: tuple[str, ...] = (),
) -> Any:
    """Consult DATA_ENGINEERING_SUPERVISOR for a workflow stage, then execute the stage procedure once."""
    prompt = (
        f"For workflow run {run_id}, call get_workflow_state with RUN_ID='{run_id}' and load the required stage skill(s) "
        f"for {stage_tool}, then give one short sentence of supervisory guidance. Do not call {stage_tool} directly "
        f"because the platform will execute the stage procedure next."
    )
    agent_reply = run_supervisor(db, run_id, prompt)
    res = fallback_fn()
    if isinstance(res, dict) and agent_reply:
        return {**res, "agent": AGENT_NAME, "supervisor_note": agent_reply[:1000]}
    return res

