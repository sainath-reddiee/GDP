"""One conversational turn of the copilot.

A turn alternates model steps and tool calls until the model asks the person something, replies that it is done, or
the step budget runs out. It yields events the API stores and streams to the chat:

  thought  {text}                              the model's short reasoning for this step
  plan     {plan}                              the whole rail after a change
  tool     {tool, args, ok, summary|error}     one tool call and its outcome
  change   {change}                            a drafted change (kept for review, never applied here)
  ask      {question, fields}                  a question for the person; the turn ends
  reply    {text}                              the model's message to the person
  error    {text}                              the turn stopped on an error
  done     {}                                  always last

Nothing here talks to Snowflake: the API passes `complete(prompt, schema) -> (output, usage, model)` and
`call(method, path, body, params) -> (status, data)`.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Tuple

from services.agentic import changeset, plan as rail, prompt
from services.agentic.tools import TOOLS, Call, ToolError, validate

Complete = Callable[[str, Dict[str, Any]], Awaitable[Tuple[Dict[str, Any], Dict[str, Any], str]]]

MAX_STEPS = 8          # model steps per turn
MAX_CALLS = 4          # tool calls per model step
MAX_TOOL_CALLS = 12    # tool calls per turn


def _args(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        raise ToolError("args_json is not valid JSON") from None


async def run_turn(complete: Complete, call: Call, state: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
    """state: {title, jira_key, plan, changes, events} (the person's new message is already the last event).
    `plan` and `changes` are updated in place; the caller persists them."""
    plan, changes = state["plan"], state.setdefault("changes", [])
    scratch: List[Dict[str, Any]] = []
    used = 0
    replied = False
    for _ in range(MAX_STEPS):
        try:
            out, usage, model = await complete(prompt.build(state, scratch), prompt.TURN_SCHEMA)
        except Exception as exc:  # the model call itself failed: say so and stop
            yield {"type": "error", "text": f"The model call failed: {str(exc)[:500]}"}
            break
        yield {"type": "usage", "usage": usage, "model": model}
        if out.get("thought"):
            yield {"type": "thought", "text": str(out["thought"])[:2000]}
        moved = False
        for u in out.get("plan_updates") or []:
            moved |= rail.merge(plan, str(u.get("step")), str(u.get("status")), str(u.get("note") or ""), force=True)
        if moved:
            yield {"type": "plan", "plan": plan}

        calls = (out.get("tool_calls") or [])[:MAX_CALLS]
        for c in calls:
            if used >= MAX_TOOL_CALLS:
                break
            used += 1
            name = str(c.get("tool") or "")
            tool = TOOLS.get(name)
            try:
                if tool is None:
                    raise ToolError(f"there is no tool named {name!r}")
                args = validate(tool, _args(c.get("args_json")))
                if rail.merge(plan, tool.step, "RUNNING"):
                    yield {"type": "plan", "plan": plan}
                result = await tool.run(call, args, state.get("ctx"))
            except ToolError as exc:
                scratch.append({"tool": name, "error": str(exc)})
                yield {"type": "tool", "tool": name, "ok": False, "error": str(exc)}
                continue
            except Exception as exc:  # a bug or an unexpected response: report it, keep the turn alive
                scratch.append({"tool": name, "error": f"unexpected error: {str(exc)[:300]}"})
                yield {"type": "tool", "tool": name, "ok": False, "error": "unexpected error in the tool"}
                continue
            if result.get("jira_key") and not state.get("jira_key"):
                state["jira_key"] = result["jira_key"]
            scratch.append({"tool": name, "args": args, "result": result.get("summary")})
            yield {"type": "tool", "tool": name, "ok": True, "summary": result.get("summary")}
            if result.get("change"):
                saved = changeset.upsert(changes, result["change"])
                yield {"type": "change", "change": saved}
            moved = False
            for key, status, note in result.get("steps") or []:
                moved |= rail.merge(plan, key, status, note)
            if moved:
                yield {"type": "plan", "plan": plan}

        ask = out.get("ask_user") or {}
        if ask.get("question"):
            if out.get("reply"):
                yield {"type": "reply", "text": str(out["reply"])[:8000]}
            yield {"type": "ask", "question": str(ask["question"])[:1000],
                   "fields": [f for f in (ask.get("fields") or []) if isinstance(f, dict) and f.get("name")][:8],
                   "ask_id": f"ask-{len(state.get('events') or [])}"}
            replied = True
            break
        if out.get("reply") and (out.get("done") or not calls):
            yield {"type": "reply", "text": str(out["reply"])[:8000]}
            replied = True
            break
        if out.get("done") or not calls:
            break
    if not replied and scratch:
        yield {"type": "reply", "text": "I stopped here to keep this turn short. Tell me to continue, or review what is drafted so far."}
    if any(c.get("status") == "PROPOSED" for c in changes) and rail.merge(plan, "approval", "RUNNING", "waiting for your review"):
        yield {"type": "plan", "plan": plan}
    yield {"type": "done"}
