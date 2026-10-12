"""The plan rail: eight fixed steps every copilot session moves through (pure)."""

from __future__ import annotations

from typing import Any, Dict, List

STEPS: List[Dict[str, Any]] = [
    {"no": 1, "key": "intake", "title": "Jira ingestion and scoping"},
    {"no": 2, "key": "context", "title": "Domain pack and code context"},
    {"no": 3, "key": "model", "title": "Data model and DDL update"},
    {"no": 4, "key": "sttm", "title": "STTM and transform generation"},
    {"no": 5, "key": "quality", "title": "Soda DQ and QA SQL generation"},
    {"no": 6, "key": "dryrun", "title": "Snowflake dry run"},
    {"no": 7, "key": "approval", "title": "Human approval gate"},
    {"no": 8, "key": "publish", "title": "Git commit and Jira sync"},
]
KEYS = [s["key"] for s in STEPS]
STATUSES = ("PENDING", "RUNNING", "DONE", "BLOCKED", "SKIPPED")
_RANK = {"PENDING": 0, "SKIPPED": 1, "RUNNING": 2, "BLOCKED": 3, "DONE": 4}


def initial() -> List[Dict[str, Any]]:
    return [{"no": s["no"], "key": s["key"], "title": s["title"], "status": "PENDING", "note": ""} for s in STEPS]


def merge(plan: List[Dict[str, Any]], key: str, status: str, note: str = "", *, force: bool = False) -> bool:
    """Move one step to `status`. A finished step never goes back to running on its own (a later draft for the same
    step keeps it DONE); `force` lets the model reopen or skip a step explicitly. True when something changed."""
    if key not in KEYS or status not in STATUSES:
        return False
    step = next(s for s in plan if s["key"] == key)
    if not force and step["status"] == "DONE" and status == "RUNNING":
        return False
    if step["status"] == status and (not note or note == step.get("note")):
        return False
    step["status"] = status
    if note:
        step["note"] = note[:2000]
    return True


def by_key(plan: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {s["key"]: s for s in plan}
