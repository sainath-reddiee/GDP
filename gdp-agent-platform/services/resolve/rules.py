"""The resolution rules, pure (no database): what a failure is, which guardrail applies, whether a search hit counts,
what happens next, who may approve, which procedures a fix may call, and which status changes are allowed.

Slide 3 of the design, in order: auto-resolve on? -> scope mode -> strong hit? -> approval needed? A miss never invents
a fix, and only PUBLISHED fixes are ever run.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.ops.detect import last_exception_line, signature as normalised

STATUSES = ("PENDING", "IN_PROGRESS", "OUT_OF_SCOPE", "AWAITING_APPROVAL", "REJECTED", "RESOLVED", "TICKETED")
FLOW_MODES = ("AUTO", "APPROVAL_ALWAYS", "TICKET_ONLY", "DISABLED")
FIX_TYPES = ("PROCEDURE", "SKILL")
ISSUE_STATUSES = ("DRAFT", "PUBLISHED", "RETIRED")
OUTCOMES = ("SUCCESS", "FAILED", "TICKETED")

# Slide 4, FAILURE_EVENT.STATUS: every allowed move. Anything else is refused.
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "PENDING": ("IN_PROGRESS", "OUT_OF_SCOPE"),   # OUT_OF_SCOPE: set aside before a claim (engine.set_aside)
    "IN_PROGRESS": ("OUT_OF_SCOPE", "AWAITING_APPROVAL", "RESOLVED", "TICKETED"),
    "AWAITING_APPROVAL": ("RESOLVED", "REJECTED", "TICKETED"),   # approved fix ran, approver said no, approved fix failed
    "OUT_OF_SCOPE": (), "REJECTED": (), "RESOLVED": (), "TICKETED": (),
}

# A fix procedure: CORE.FIX_<NAME>, optionally with the database. Fixes are written and reviewed like code; the prefix
# keeps the resolver from ever calling a platform procedure (or anything else) by name.
FIX_PROCEDURE = re.compile(r"^(?:(?P<db>[A-Za-z_][A-Za-z0-9_$]*)\.)?CORE\.(?P<name>FIX_[A-Za-z0-9_]{1,200})$", re.I)
_CLASS = re.compile(r"^(?P<cls>[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Failure|Timeout|Warning))\b[:\s]*(?P<msg>.*)$")
_QUERY_ID = re.compile(r"(?i)(?:query[ _-]?id|sfqid|query_id)[\"'\s:=]+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
_DBT_NODE = re.compile(r"\b((?:model|seed|snapshot|test|source)\.[A-Za-z0-9_]+\.[A-Za-z0-9_.]+)\b")
_DBT_MODEL = re.compile(r"(?i)\b(?:in|database error in|compilation error in)\s+model\s+([A-Za-z0-9_]+)")
_TOKEN = re.compile(r"[a-z0-9_<>]+")


# ---------------------------------------------------------------- what a failure is

def parse_failure(excerpt: Optional[str]) -> Dict[str, Optional[str]]:
    """{exception_class, exception_text, query_id, dbt_node_id} from a (redacted) error excerpt."""
    text = excerpt or ""
    line = last_exception_line(text)
    m = _CLASS.match(line)
    cls = m.group("cls").split(".")[-1] if m else None
    msg = (m.group("msg") if m else line).strip()
    # a multi-line message (Snowflake: "SQL compilation error: ...\ninvalid identifier 'X'") continues on the next lines
    rows = text.splitlines()
    at = max((i for i, r in enumerate(rows) if line and line in r), default=-1)
    for extra in rows[at + 1:at + 4] if at >= 0 else []:
        if not extra.strip() or re.match(r"^\s*(\[|\d{4}-\d\d-\d\dT|t? ?(INFO|ERROR|WARNING|DEBUG)\b|Traceback|During handling|The above)", extra):
            break
        msg = f"{msg} {extra.strip()}"
    q = _QUERY_ID.search(text)
    node = _DBT_NODE.search(text)
    model = None if node else _DBT_MODEL.search(text)
    return {"exception_class": cls, "exception_text": msg[:4000] or None,
            "query_id": q.group(1).lower() if q else None,
            "dbt_node_id": node.group(1) if node else (f"model.{model.group(1)}" if model else None)}


def error_signature(dag_id: str, task_id: Optional[str], exception_class: Optional[str], excerpt: Optional[str]) -> str:
    """sha1 of the DAG, the task, the exception class and the normalised error text (times, ids, numbers, paths and
    literals replaced). The same failure gives the same signature run after run; a KNOWN_ISSUE stores it too."""
    raw = "\x1f".join([dag_id or "", task_id or "", (exception_class or "").lower(), normalised(excerpt)])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def dedup_key(env_id: str, dag_id: str, task_id: Optional[str], run_id: Optional[str], map_index: Any, try_number: Any) -> str:
    """One row per try: the push and the poll, or the poll overlap, ingest it once."""
    full = f"{env_id}|{dag_id}|{task_id or ''}|{run_id or ''}|{map_index if map_index is not None else -1}|{try_number or 0}"
    return full if len(full) <= 600 else f"{env_id[:40]}|{dag_id[:60]}|" + hashlib.sha1(full.encode("utf-8")).hexdigest()


def match_text(dag_id: str, task_id: Optional[str], exception_class: Optional[str], excerpt: Optional[str]) -> str:
    """What is searched for a failure, and what a fix written from it starts with."""
    parts = [f"dag {dag_id}", f"task {task_id}" if task_id else "", exception_class or "", normalised(excerpt)]
    return " | ".join(p for p in parts if p)[:2000]


# ---------------------------------------------------------------- guardrails

def preference(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """CONTROL_PREFERENCE (lower-case columns) with defaults; nothing runs when the row is missing."""
    r = row or {}
    def num(key: str, default: float) -> float:
        try:
            return float(r.get(key)) if r.get(key) is not None else default
        except (TypeError, ValueError):
            return default
    mode = str(r.get("rca_mode") or "AGENT").upper()
    return {"auto_resolve_enabled": bool(r.get("auto_resolve_enabled")),
            "search_score_min": min(1.0, max(0.0, num("search_score_min", 0.75))),
            "max_parallel_tasks": int(min(50, max(1, num("max_parallel_tasks", 5)))),
            "airflow_clear_enabled": r.get("airflow_clear_enabled") is not False,
            "default_approval_required": r.get("default_approval_required") is not False,
            "graph_hops": int(min(6, max(1, num("graph_hops", 3)))),
            "notify_target": r.get("notify_target"),
            "rca_mode": mode if mode in ("AGENT", "GROUNDED") else "AGENT",
            # agent-proposed fixes (V063): off unless enabled, and only on objects the scope names
            "agent_proposals_enabled": bool(r.get("agent_proposals_enabled")),
            "agent_fix_scope": (str(r.get("agent_fix_scope")).strip() or None) if r.get("agent_fix_scope") else None,
            # permanent fixes (V066): drafting only proposes a code change; a pull request needs two people
            "permanent_fix_enabled": r.get("permanent_fix_enabled") is not False,
            "permanent_fix_min_runs": int(min(50, max(2, num("permanent_fix_min_runs", 3))))}


def _most_specific(rows: Iterable[Dict[str, Any]], dag_id: str, task_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The row for exactly this task, else the row for the whole DAG (task null), else None."""
    task_row = dag_row = None
    for r in rows:
        if str(r.get("dag_id") or "") != dag_id:
            continue
        t = r.get("task_id")
        if t and task_id and str(t) == task_id:
            task_row = r
        elif not t and dag_row is None:
            dag_row = r
    return task_row or dag_row


def flow_mode(scopes: Iterable[Dict[str, Any]], dag_id: str, task_id: Optional[str]) -> Optional[str]:
    """The PIPELINE_SCOPE mode for the failure, or None when no row covers it (out of scope: never claimed)."""
    row = _most_specific(scopes, dag_id, task_id)
    if row is None:
        return None
    if row.get("enabled") is False:
        return "DISABLED"
    mode = str(row.get("flow_mode") or "AUTO").upper()
    return mode if mode in FLOW_MODES else "DISABLED"


def approver_for(routes: Iterable[Dict[str, Any]], dag_id: str, task_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The APPROVAL_ROUTE row (task row wins over the DAG row), or None: any holder of OPS.FIX_APPROVE decides."""
    return _most_specific(routes, dag_id, task_id)


# ---------------------------------------------------------------- the search

def token_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Jaccard similarity of the normalised word sets (0..1): the score when the search service gives none."""
    x, y = set(_TOKEN.findall((a or "").lower())), set(_TOKEN.findall((b or "").lower()))
    if not x or not y:
        return 0.0
    return round(len(x & y) / len(x | y), 4)


def issue_applies(issue: Dict[str, Any], dag_id: str, task_id: Optional[str], exception_class: Optional[str]) -> bool:
    """A fix scoped to a DAG, task or exception class applies only to failures that match it (blank = any)."""
    for key, value in (("dag_id", dag_id), ("task_id", task_id), ("exception_class", exception_class)):
        want = str(issue.get(key) or "").strip()
        if want and want.lower() != str(value or "").lower():
            return False
    return True


def score_hit(issue: Dict[str, Any], failure: Dict[str, Any], service_score: Optional[float]) -> float:
    """1.0 for the same error signature; else the search service's similarity when it gives one; else word overlap."""
    if issue.get("error_signature") and issue.get("error_signature") == failure.get("error_signature"):
        return 1.0
    if service_score is not None:
        try:
            return max(0.0, min(1.0, float(service_score)))
        except (TypeError, ValueError):
            pass
    return token_similarity(issue.get("match_text"),
                            match_text(failure.get("dag_id"), failure.get("task_id"), failure.get("exception_class"),
                                       failure.get("exception_text")))


def best_hit(candidates: List[Tuple[Dict[str, Any], Optional[float]]], failure: Dict[str, Any],
             score_min: float) -> Tuple[Optional[Dict[str, Any]], float]:
    """(issue, score) of the best PUBLISHED fix that applies and scores at least `score_min`, else (None, best score)."""
    best, best_score = None, 0.0
    for issue, service_score in candidates:
        if str(issue.get("status") or "").upper() != "PUBLISHED":
            continue
        if not issue_applies(issue, failure.get("dag_id"), failure.get("task_id"), failure.get("exception_class")):
            continue
        s = score_hit(issue, failure, service_score)
        if s > best_score:
            best, best_score = issue, s
    if best is None or best_score < score_min:
        return None, best_score
    return best, best_score


# ---------------------------------------------------------------- the decision (slide 3)

def decide(prefs: Dict[str, Any], mode: Optional[str], hit: Optional[Dict[str, Any]]) -> str:
    """What happens to a claimed failure: OUT_OF_SCOPE | TICKET | RUN | APPROVAL. (Whether to claim at all is
    auto_resolve_enabled plus a scope row, checked before the claim.)"""
    if mode in (None, "DISABLED"):
        return "OUT_OF_SCOPE"
    if mode == "TICKET_ONLY" or hit is None:
        return "TICKET"
    if mode == "APPROVAL_ALWAYS":
        return "APPROVAL"
    flag = hit.get("approval_required")
    needs = prefs.get("default_approval_required", True) if flag is None else bool(flag)
    return "APPROVAL" if needs else "RUN"


def can_move(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(str(current or "").upper(), ())


# ---------------------------------------------------------------- what a fix may call

def fix_procedure(value: Optional[str], database: Optional[str] = None) -> str:
    """The fully qualified fix procedure, or ValueError. Only CORE.FIX_* in the platform database may be called."""
    m = FIX_PROCEDURE.match(str(value or "").strip())
    if not m:
        raise ValueError("a fix procedure must be named CORE.FIX_<NAME> (letters, digits and underscores)")
    db = m.group("db")
    if db and database and db.upper() != database.upper():
        raise ValueError(f"a fix procedure must live in the platform database {database}")
    name = m.group("name").upper()
    return f"{(db or database or '').upper() + '.' if (db or database) else ''}CORE.{name}"


def skill_steps(steps: Any, database: Optional[str] = None) -> List[Dict[str, Any]]:
    """The skill's steps in order, each {step_no, action (a checked fix procedure), input (JSON text or None), note}.
    ValueError for an empty list, a duplicate step number or an action that is not a fix procedure."""
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except ValueError:
            raise ValueError("skill steps must be a JSON list")
    if not isinstance(steps, list) or not steps:
        raise ValueError("a skill needs at least one step")
    out, seen = [], set()
    for i, s in enumerate(steps, start=1):
        if not isinstance(s, dict):
            raise ValueError(f"step {i} must be an object")
        no = int(s.get("step_no") or i)
        if no in seen:
            raise ValueError(f"step number {no} is used twice")
        seen.add(no)
        given = s.get("input")
        out.append({"step_no": no, "action": fix_procedure(s.get("action"), database),
                    "input": None if given in (None, "", {}) else (given if isinstance(given, str) else json.dumps(given)),
                    "note": str(s.get("note") or "")[:500]})
    return sorted(out, key=lambda s: s["step_no"])


def publish_check(issue: Dict[str, Any], actor: str, skill: Optional[Dict[str, Any]] = None,
                  database: Optional[str] = None) -> None:
    """ValueError when a DRAFT cannot be published: incomplete fix, or the reviewer wrote it themselves (four eyes)."""
    if str(issue.get("status") or "").upper() != "DRAFT":
        raise ValueError("only a draft can be published")
    if not str(issue.get("match_text") or "").strip():
        raise ValueError("the fix needs match text")
    if issue.get("created_by") and str(issue["created_by"]).lower() == str(actor or "").lower():
        raise ValueError("a fix is published by a reviewer other than its author")
    kind = str(issue.get("fix_type") or "").upper()
    if kind == "PROCEDURE":
        fix_procedure(issue.get("procedure_fqn"), database)
    elif kind == "SKILL":
        if not skill:
            raise ValueError("the skill this fix runs was not found")
        skill_steps(skill.get("steps"), database)
    else:
        raise ValueError("fix type must be PROCEDURE or SKILL")
