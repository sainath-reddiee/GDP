"""Drafted changes (pure): one per asset, replaced by a revision, ordered for publishing.

A change is {change_key, kind, title, operations: [{method, path, body}], preview, status}. Its operations are the
exact API calls that apply it; Approve and Publish replays them as the user, so the API decides what is allowed and
what waits for approval. The agent never runs them during a conversation.
"""

from __future__ import annotations

from typing import Any, Dict, List

KINDS = ("INGESTION", "MODEL", "STTM", "DBT", "QA", "DQ", "GIT", "JIRA")
# Publishing order: a source before the model built on it, the model before its mapping, checks after both,
# then pull requests, and Jira last so its comment can report what happened.
ORDER = {k: i for i, k in enumerate(KINDS)}
OPEN = ("PROPOSED",)
ALLOWED_METHODS = ("POST", "PUT", "PATCH")


def make(change_key: str, kind: str, title: str, operations: List[Dict[str, Any]],
         preview: Dict[str, Any] | None = None) -> Dict[str, Any]:
    assert kind in KINDS, f"unknown change kind {kind}"
    assert operations, "a change needs at least one operation"
    for op in operations:
        assert op.get("method") in ALLOWED_METHODS, f"unsupported method {op.get('method')}"
        path = str(op.get("path") or "")
        assert path.startswith("/api/") and ".." not in path, f"not an API path: {path}"
        assert not path.startswith(("/api/copilot/", "/api/governance/", "/api/admin/", "/api/auth/")), \
            f"the copilot cannot call {path}"
    return {"change_key": change_key[:512], "kind": kind, "title": title[:512], "operations": operations,
            "preview": preview or {}, "status": "PROPOSED"}


def upsert(changes: List[Dict[str, Any]], change: Dict[str, Any]) -> Dict[str, Any]:
    """Add a change, or replace the open draft for the same asset (keeping its id and position)."""
    for existing in changes:
        if existing["change_key"] == change["change_key"] and existing.get("status") in OPEN:
            keep = {k: existing[k] for k in ("change_id", "seq") if k in existing}
            existing.clear()
            existing.update({**change, **keep})
            return existing
    change = dict(change)
    change["seq"] = max([c.get("seq", 0) for c in changes] or [0]) + 1
    changes.append(change)
    return change


def ordered(changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Open changes in publishing order (kind first, then the order they were drafted)."""
    return sorted([c for c in changes if c.get("status") in OPEN], key=lambda c: (ORDER.get(c["kind"], 99), c.get("seq", 0)))


def summary(changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What the model sees of the drafts so far."""
    return [{"change_key": c["change_key"], "kind": c["kind"], "title": c["title"], "status": c.get("status")}
            for c in changes if c.get("status") != "DISCARDED"]


def tabs(changes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """The diff workbench: every open change's preview, grouped into model, sttm, dbt and quality tabs."""
    out: Dict[str, List[Dict[str, Any]]] = {"model": [], "sttm": [], "dbt": [], "quality": []}
    for c in changes:
        if c.get("status") == "DISCARDED":
            continue
        p = c.get("preview") or {}
        base = {"change_id": c.get("change_id"), "title": c["title"], "kind": c["kind"]}
        if p.get("model"):
            out["model"].append({**base, **p["model"]})
        if p.get("sttm"):
            out["sttm"].append({**base, **p["sttm"]})
        if p.get("files"):
            tab = "quality" if c["kind"] in ("QA", "DQ") else "dbt"
            out[tab].append({**base, "files": p["files"]})
    return out
