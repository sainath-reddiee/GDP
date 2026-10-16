"""Run a Cortex Agent from inside a Snowflake procedure and return its final text.

Inside a Python procedure the agent's REST endpoint is reached with _snowflake.send_snow_api_request (no token, no
egress). Outside Snowflake (the API, tests) there is no _snowflake module and AgentUnavailable is raised, so callers
fall back to their grounded path. The response is a stream of events; the text of the final answer is collected from
whichever event shape the account returns.
"""

from __future__ import annotations

import json
from typing import Any, List

TIMEOUT_MS = 120_000


class AgentUnavailable(Exception):
    """The agent cannot be called here, or it failed."""


def _texts(data: Any, out: List[str], delta: bool) -> None:
    if isinstance(data, dict):
        if data.get("type") == "text" and isinstance(data.get("text"), str):
            out.append(data["text"])
            return
        if delta and isinstance(data.get("text"), str):
            out.append(data["text"])
            return
        for key in ("content", "delta"):
            if key in data:
                _texts(data[key], out, delta)
    elif isinstance(data, list):
        for item in data:
            _texts(item, out, delta)


def final_text(content: Any) -> str:
    """The answer's text from the run response: a JSON list of {event, data}, or server-sent-event text."""
    events: List[tuple] = []
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            events = [(str(e.get("event") or ""), e.get("data")) for e in parsed if isinstance(e, dict)]
        elif isinstance(parsed, dict):
            events = [("response", parsed)]
        else:
            name = ""
            for line in content.splitlines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    try:
                        events.append((name, json.loads(line[5:].strip())))
                    except ValueError:
                        continue
    elif isinstance(content, list):
        events = [(str(e.get("event") or ""), e.get("data")) for e in content if isinstance(e, dict)]
    elif isinstance(content, dict):
        events = [("response", content)]
    final = [d for n, d in events if n == "response"]
    out: List[str] = []
    if final:
        _texts(final[-1], out, delta=False)
    if not out:
        for name, data in events:
            if name in ("response.text.delta", "message.delta", "response.text"):
                _texts(data, out, delta=True)
    text = "".join(out).strip()
    if not text:
        raise AgentUnavailable("the agent returned no text")
    return text


def run(database: str, schema: str, name: str, message: str, timeout_ms: int = TIMEOUT_MS) -> str:
    try:
        import _snowflake  # type: ignore  # only inside a Snowflake procedure
    except ImportError as exc:
        raise AgentUnavailable("Cortex Agents run only inside a Snowflake procedure") from exc
    path = f"/api/v2/databases/{database}/schemas/{schema}/agents/{name}:run"
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": message}]}], "stream": False}
    try:
        resp = _snowflake.send_snow_api_request("POST", path, {}, {}, body, None, timeout_ms)
    except Exception as exc:
        raise AgentUnavailable(f"agent call failed: {exc}") from exc
    status = int((resp or {}).get("status") or 0)
    if status >= 400 or status == 0:
        raise AgentUnavailable(f"agent answered {status}: {str((resp or {}).get('content'))[:300]}")
    return final_text((resp or {}).get("content"))


def json_answer(text: str) -> Any:
    """The JSON object in an agent's answer (it may be fenced or surrounded by prose)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AgentUnavailable("the agent's answer has no JSON object")
    try:
        return json.loads(text[start:end + 1])
    except ValueError as exc:
        raise AgentUnavailable(f"the agent's answer is not valid JSON: {exc}") from exc
