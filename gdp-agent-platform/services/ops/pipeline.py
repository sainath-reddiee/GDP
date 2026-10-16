"""What a failed task does, from the Airflow knowledge pack, for every analysis of a failure: the incident context
(services.ops.context, part `pipeline`), the RCA agent's get_failure_context tool and the agent's fix proposals.

  task       the task's operator, the SQL it runs, the tables it reads and writes, its dbt models and SnapLogic pipeline
             (CORE.DAG_TASK, services.ops.catalog)
  upstream   the tasks it waits for and their state in the failed run
  writers    for each table it reads, the tasks (of any DAG) that write it, so a missing or stale table leads to the
             DAG that should have produced it
  dag        schedule, criticality, expected completion and maximum duration
  runbooks   AIRFLOW_RUNBOOK knowledge whose signals appear in the error (error codes, exception names, phrases)

Read only and bounded. Every source is optional: what cannot be read is left out.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from services.ops.redact import redact

RUNBOOK_LIMIT = 3
WRITERS_LIMIT = 8


def _list(value: Any) -> List[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value] if value else []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


def _json(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def table_key(name: str) -> str:
    """SCHEMA.TABLE of a 2- or 3-part name, upper case: how two tasks naming the same table differently still match."""
    parts = str(name or "").upper().split(".")
    return ".".join(parts[-2:])


def signal_hits(signals: List[str], text: str) -> int:
    """How many of a runbook's signals appear in the error: case-insensitive phrases; a bare number (an error code)
    only as a whole number."""
    hits = 0
    for s in signals:
        s = str(s or "").strip()
        if not s:
            continue
        if s.isdigit():
            hits += 1 if re.search(rf"(?<!\d){re.escape(s)}(?!\d)", text) else 0
        else:
            hits += 1 if s.lower() in text.lower() else 0
    return hits


def match_runbooks(items: List[Dict[str, Any]], error_text: str, limit: int = RUNBOOK_LIMIT) -> List[Dict[str, Any]]:
    """Runbooks ranked by the signals the error shows; none without a hit."""
    scored = []
    for item in items:
        signals = _list(_json(item.get("content_json")).get("signals"))
        hits = signal_hits(signals, error_text or "")
        if hits:
            scored.append((hits, str(item.get("source_reference") or ""), item))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s[2] for s in scored[:limit]]


def runbooks(db: Any, error_text: str, limit: int = RUNBOOK_LIMIT) -> List[Dict[str, Any]]:
    if not str(error_text or "").strip():
        return []
    items = db.query("""SELECT SOURCE_REFERENCE, TITLE, CONTENT, CONTENT_JSON FROM CORE.DOMAIN_KNOWLEDGE
                         WHERE KNOWLEDGE_TYPE = 'AIRFLOW_RUNBOOK' AND IS_CURRENT AND STATUS = 'ACTIVE'
                         ORDER BY SOURCE_REFERENCE LIMIT 300""")
    return match_runbooks(items, error_text, limit)


def facts(db: Any, env_id: Optional[str], dag_id: Optional[str], task_id: Optional[str], run_id: Optional[str] = None,
          error_text: str = "") -> Dict[str, Any]:
    """{task, upstream_states, writers, dag, runbooks}; parts that cannot be read are empty."""
    from services.ops import catalog

    out: Dict[str, Any] = {"task": None, "upstream_states": {}, "writers": {}, "dag": {}, "runbooks": []}
    try:
        out["task"] = catalog.task_entry(db, env_id, dag_id, task_id)
    except Exception:
        out["task"] = None
    task = out["task"] or {}
    ups = _list(task.get("upstream"))
    if ups and run_id:
        try:
            marks = ", ".join(["%s"] * len(ups[:30]))
            found = db.query(f"""SELECT TASK_ID, STATE FROM CORE.TASK_RUN WHERE ENV_ID = %s AND DAG_ID = %s AND RUN_ID = %s
                                    AND TASK_ID IN ({marks})
                                 QUALIFY ROW_NUMBER() OVER (PARTITION BY TASK_ID ORDER BY TRY_NUMBER DESC) = 1""",
                             (env_id, dag_id, run_id, *ups[:30]))
            out["upstream_states"] = {r["task_id"]: r.get("state") for r in found}
        except Exception:
            pass
    reads = [r for r in _list(task.get("reads")) if not r.startswith("@")][:20]
    if reads:
        try:
            # matched on SCHEMA.TABLE: one task may name DB.CORE.ORDERS and another CORE.ORDERS (its default database)
            by_key = {table_key(r): r.upper() for r in reads}
            found = db.query("""SELECT T.ENV_ID, T.DAG_ID, T.TASK_ID, UPPER(W.VALUE::VARCHAR) AS OBJ FROM CORE.DAG_TASK T,
                                       LATERAL FLATTEN(INPUT => T.WRITES) W
                                 WHERE ARRAY_CONTAINS((SPLIT_PART(UPPER(W.VALUE::VARCHAR), '.', -2) || '.'
                                                       || SPLIT_PART(UPPER(W.VALUE::VARCHAR), '.', -1))::VARIANT,
                                                      PARSE_JSON(%s)::ARRAY)
                                   AND NOT (T.DAG_ID = %s AND T.TASK_ID = %s)
                                 ORDER BY T.DAG_ID, T.TASK_ID LIMIT 40""", (json.dumps(sorted(by_key)), dag_id, task_id))
            writers: Dict[str, List[str]] = {}
            for r in found:
                names = writers.setdefault(by_key.get(table_key(r["obj"]), r["obj"]), [])
                if len(names) < WRITERS_LIMIT:
                    names.append(f"{r['dag_id']}.{r['task_id']}" + ("" if r.get("env_id") == env_id else f" ({r['env_id']})"))
            out["writers"] = writers
        except Exception:
            pass
    try:
        found = db.query("""SELECT SCHEDULE, CRITICALITY, EXPECTED_BY_CRON, MAX_DURATION_MIN, TIMEZONE FROM CORE.DAG
                             WHERE ENV_ID = %s AND DAG_ID = %s""", (env_id, dag_id))
        out["dag"] = {k: v for k, v in (found[0] if found else {}).items() if v not in (None, "")}
    except Exception:
        pass
    try:
        out["runbooks"] = [{"key": r.get("source_reference"), "title": r.get("title"),
                            "content": redact(str(r.get("content") or ""))[:900]} for r in runbooks(db, error_text)]
    except Exception:
        pass
    return out


def text(found: Dict[str, Any], sql_chars: int = 1200) -> str:
    """The facts as prompt text (redacted, bounded); empty when the catalogue knows nothing of the task."""
    lines: List[str] = []
    task = found.get("task") or {}
    if task:
        head = f"Operator: {task.get('operator') or 'unknown'}."
        states = found.get("upstream_states") or {}
        ups = _list(task.get("upstream"))
        if ups:
            head += " Runs after: " + ", ".join(f"{u} ({states[u]})" if states.get(u) else u for u in ups[:10]) + "."
        lines.append(head)
        if task.get("sql_text"):
            lines.append("SQL it runs (from the DAG source):\n" + redact(str(task["sql_text"]))[:sql_chars])
        if _list(task.get("reads")):
            lines.append("Reads: " + ", ".join(_list(task["reads"])[:20]) + ".")
        if _list(task.get("writes")):
            lines.append("Writes: " + ", ".join(_list(task["writes"])[:20]) + ".")
        if _list(task.get("dbt_selectors")):
            lines.append("dbt selectors: " + " ".join(_list(task["dbt_selectors"])[:20]) + ".")
        if task.get("snaplogic_pipeline"):
            lines.append(f"SnapLogic pipeline: {task['snaplogic_pipeline']}.")
        for obj, names in list((found.get("writers") or {}).items())[:10]:
            lines.append(f"{obj} is written by: {', '.join(names)}.")
    dag = found.get("dag") or {}
    if dag:
        lines.append("DAG: " + "; ".join(f"{k.replace('_', ' ')} {v}" for k, v in dag.items()) + ".")
    for r in found.get("runbooks") or []:
        lines.append(f"[runbook:{r['key']}] {r.get('title')}: {r.get('content')}")
    return "\n".join(lines)
