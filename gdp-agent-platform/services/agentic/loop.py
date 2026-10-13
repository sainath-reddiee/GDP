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
  usage    {usage, model}                      one model call (for cost; not shown)
  done     {}                                  always last

Nothing here talks to Snowflake: the API passes `complete(prompt, schema) -> (output, usage, model)` and
`call(method, path, body, params) -> (status, data)`.
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Tuple

from services.agentic import changeset, plan as rail, prompt
from services.agentic.tools import TOOLS, Call, ToolError, validate

Complete = Callable[[str, Dict[str, Any]], Awaitable[Tuple[Dict[str, Any], Dict[str, Any], str]]]

MAX_STEPS = 8          # model steps per turn
MAX_CALLS = 4          # tool calls per model step
MAX_TOOL_CALLS = 12    # tool calls per turn
MAX_FIELDS = 8         # fields in one question
FIELD_TYPES = ("select", "multiselect", "text", "textarea", "number", "confirm")
# never ask a person to type a credential into the chat
SECRET_FIELD = re.compile(r"pass(word|wd)?|secret|token|api[_ ]?key|private[_ ]?key|credential", re.I)
BUDGET_NOTE = "The tool budget for this turn is used up. Do not call more tools: reply now with what you have, or ask."


def _args(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        raise ToolError("args_json is not valid JSON") from None


def clean_fields(fields: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The question's fields the chat can render safely, and the names that were refused (credentials)."""
    out, refused, seen = [], [], set()
    for f in fields if isinstance(fields, list) else []:
        if not isinstance(f, dict) or not str(f.get("name") or "").strip():
            continue
        name = str(f["name"]).strip()[:64]
        label = str(f.get("label") or name)[:200]
        if SECRET_FIELD.search(name) or SECRET_FIELD.search(label):
            refused.append(name)
            continue
        if name in seen:
            continue
        seen.add(name)
        kind = f.get("type") if f.get("type") in FIELD_TYPES else "text"
        options = [str(o)[:200] for o in (f.get("options") or []) if str(o).strip()][:50]
        if kind in ("select", "multiselect") and not options:
            kind = "text"
        item: Dict[str, Any] = {"name": name, "label": label, "type": kind}
        if options:
            item["options"] = options
        for k in ("default", "help"):
            if f.get(k) not in (None, ""):
                item[k] = str(f[k])[:500]
        if f.get("required") is False:
            item["required"] = False
        out.append(item)
        if len(out) >= MAX_FIELDS:
            break
    return out, refused


async def run_turn(complete: Complete, call: Call, state: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
    """state: {title, jira_key, plan, changes, events, ctx} (the person's new message is already the last event).
    `plan` and `changes` are updated in place; the caller persists them."""
    plan, changes = state["plan"], state.setdefault("changes", [])
    ctx = state.setdefault("ctx", {})
    ctx["changes"] = changes          # draft tools build on the open draft for the same asset
    scratch: List[Dict[str, Any]] = []
    used = 0
    replied = False
    failed_drafts: Dict[str, str] = {}   # drafting tool -> last error, cleared when a later call of it succeeds

    def truthful(text: str) -> str:
        """The model's reply, with every draft that failed this turn stated plainly (it may have written the reply
        expecting success)."""
        if not failed_drafts:
            return text
        lines = "\n".join(f"- {name}: {error}" for name, error in failed_drafts.items())
        return f"{text}\n\n**Not drafted** (these steps failed; nothing was drafted for them):\n{lines}"
    for _ in range(MAX_STEPS):
        try:
            out, usage, model = await complete(prompt.build(state, scratch), prompt.TURN_SCHEMA)
        except Exception as exc:  # the model call itself failed: say so and stop
            yield {"type": "error", "text": f"The model call failed: {str(exc)[:500]}"}
            break
        if not isinstance(out, dict):
            yield {"type": "error", "text": "The model returned something that is not a step; try again."}
            break
        yield {"type": "usage", "usage": usage, "model": model}
        if out.get("thought"):
            yield {"type": "thought", "text": str(out["thought"])[:2000]}
        moved = False
        for u in out.get("plan_updates") or []:
            if isinstance(u, dict):
                moved |= rail.merge(plan, str(u.get("step")), str(u.get("status")), str(u.get("note") or ""), force=True)
        if moved:
            yield {"type": "plan", "plan": plan}

        calls = [c for c in (out.get("tool_calls") or []) if isinstance(c, dict)][:MAX_CALLS]
        if calls and used >= MAX_TOOL_CALLS:
            if any(s.get("note") == BUDGET_NOTE for s in scratch):
                break                      # told once already: stop instead of spending more model calls
            scratch.append({"note": BUDGET_NOTE})
            continue
        for c in calls:
            if used >= MAX_TOOL_CALLS:
                scratch.append({"note": BUDGET_NOTE})
                break
            used += 1
            name = str(c.get("tool") or "")
            tool = TOOLS.get(name)
            before = None
            try:
                if tool is None:
                    raise ToolError(f"there is no tool named {name!r}")
                args = validate(tool, _args(c.get("args_json")))
                if tool.drafts and tool.step != "publish":
                    before = rail.by_key(plan)[tool.step]["status"]
                    if rail.merge(plan, tool.step, "RUNNING"):
                        yield {"type": "plan", "plan": plan}
                result = await tool.run(call, args, ctx)
            except ToolError as exc:
                scratch.append({"tool": name, "error": str(exc)})
                yield {"type": "tool", "tool": name, "ok": False, "error": str(exc)}
                if tool and tool.drafts:
                    failed_drafts[name] = str(exc)[:400]
                if tool and before is not None and rail.merge(plan, tool.step, before, force=True):
                    yield {"type": "plan", "plan": plan}
                continue
            except Exception as exc:  # a bug or an unexpected response: report it, keep the turn alive
                scratch.append({"tool": name, "error": f"unexpected error: {str(exc)[:300]}"})
                yield {"type": "tool", "tool": name, "ok": False, "error": "The tool failed unexpectedly; try another way."}
                if tool and tool.drafts:
                    failed_drafts[name] = "the tool failed unexpectedly"
                if tool and before is not None and rail.merge(plan, tool.step, before, force=True):
                    yield {"type": "plan", "plan": plan}
                continue
            failed_drafts.pop(name, None)
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
            if tool.drafts and before is not None and not result.get("steps") and rail.by_key(plan)[tool.step]["status"] == "RUNNING":
                moved |= rail.merge(plan, tool.step, before, force=True)
            if moved:
                yield {"type": "plan", "plan": plan}

        if calls:
            continue   # the model wrote this step's reply before seeing the results: let it look at them first

        ask = out.get("ask_user") if isinstance(out.get("ask_user"), dict) else {}
        if ask.get("question"):
            fields, refused = clean_fields(ask.get("fields"))
            if refused:
                scratch.append({"note": f"Fields {refused} were removed: never ask for passwords, tokens or keys. "
                                        "Ask for the name of the vault secret instead."})
            if out.get("reply"):
                yield {"type": "reply", "text": truthful(str(out["reply"])[:8000])}
            yield {"type": "ask", "question": str(ask["question"])[:1000], "fields": fields}
            replied = True
            break
        if out.get("reply"):
            yield {"type": "reply", "text": truthful(str(out["reply"])[:8000])}
            replied = True
        break
    if not replied and scratch:
        yield {"type": "reply", "text": truthful("I stopped here to keep this turn short. Say continue and I will pick "
                                                 "up where I left off, or review what is drafted so far.")}
    elif not replied:
        yield {"type": "reply", "text": "I could not work out a next step. Could you rephrase or add detail?"}
    approval = rail.by_key(plan)["approval"]
    if any(c.get("status") == "PROPOSED" for c in changes) and approval["status"] != "RUNNING":
        if rail.merge(plan, "approval", "RUNNING", "waiting for your review", force=True):
            yield {"type": "plan", "plan": plan}
    yield {"type": "done"}
