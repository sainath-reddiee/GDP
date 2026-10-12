"""Approve and Publish: replay drafted changes through the API as the person who approves them.

Each change's operations run in order with `call` (an in-process request carrying the person's own session), so
the API decides, exactly as for a click in a store: the call applies, it becomes a change request for an approver
(HTTP 202, the change is then PENDING_APPROVAL with its request id), or it is refused. An operation path or body may
refer to an earlier operation's result of the same change, such as "/api/qa-packs/{op0.pack_id}/submit".

Changes run in dependency order (changeset.ORDER). A failure stops the run, so nothing that depends on it is
applied; a change waiting for approval does not stop the others, since each is governed on its own.

Events:  start {change_id}   result {change_id, status, request_id?, detail?, results}   stopped {change_id}   done {}
"""

from __future__ import annotations

import re
from typing import Any, AsyncIterator, Dict, List

from services.agentic import changeset

REF = re.compile(r"\{op(\d+)\.([A-Za-z0-9_]+)\}")


class Unresolved(Exception):
    pass


def resolve(value: Any, results: List[Dict[str, Any]]) -> Any:
    """Fill {opN.field} references from earlier results (strings, and strings inside lists and objects)."""
    if isinstance(value, str):
        def sub(m: "re.Match[str]") -> str:
            i, key = int(m.group(1)), m.group(2)
            if i >= len(results) or results[i].get(key) in (None, ""):
                raise Unresolved(f"step {i + 1} returned no {key}")
            return str(results[i][key])
        return REF.sub(sub, value)
    if isinstance(value, list):
        return [resolve(v, results) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, results) for k, v in value.items()}
    return value


def _detail(data: Any) -> str:
    if isinstance(data, dict):
        d = data.get("detail")
        return d if isinstance(d, str) else str(d or data)[:800]
    return str(data)[:800]


async def run(call, changes: List[Dict[str, Any]], only: List[str] | None = None) -> AsyncIterator[Dict[str, Any]]:
    """Publish the open changes (or only the ids in `only`); each change dict is updated in place."""
    todo = [c for c in changeset.ordered(changes) if not only or c.get("change_id") in only]
    for change in todo:
        yield {"type": "start", "change_id": change.get("change_id")}
        results: List[Dict[str, Any]] = []
        status, detail, request_id = "APPLIED", "", None
        for op in change.get("operations") or []:
            try:
                path, body = resolve(op["path"], results), resolve(op.get("body"), results)
            except Unresolved as exc:
                status, detail = "FAILED", str(exc)
                break
            code, data = await call(op["method"], path, body)
            results.append(data if isinstance(data, dict) else {"value": data})
            if code == 202 and isinstance(data, dict) and data.get("pending_approval"):
                status, request_id = "PENDING_APPROVAL", data.get("request_id")
                detail = f"Sent for approval to {data.get('approver_role') or 'an approver'}."
                break
            if code >= 400:
                status, detail = "FAILED", f"{op['method']} {path}: {_detail(data)}"
                break
        change["status"], change["request_id"] = status, request_id
        change["result"] = {"detail": detail, "results": results[-5:]}
        yield {"type": "result", "change_id": change.get("change_id"), "status": status, "request_id": request_id,
               "detail": detail, "title": change.get("title"), "kind": change.get("kind")}
        if status == "FAILED":
            yield {"type": "stopped", "change_id": change.get("change_id")}
            break
    yield {"type": "done"}
