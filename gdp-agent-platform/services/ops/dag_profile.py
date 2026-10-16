"""DAG profiles (part of the Airflow knowledge pack): one AIRFLOW_DAG knowledge item per captured DAG, so the RCA agent,
Ask and the grounded diagnosis find what a DAG is for and how it normally behaves through knowledge search.

A profile holds the schedule, owners, criticality and SLA, the usual duration, the upstream and downstream DAGs
(CORE.DAG_DEPENDENCY), every task with what it reads and writes (CORE.DAG_TASK, services.ops.catalog) and the failures
that keep coming back with the known fix that handles each. Written through services.knowledge.writer.remember with
origin OPS (ACTIVE at once) under the reference ops.dag.<env>.<dag>, in the DAG's domain, else GENERAL. Volatile
numbers are rounded (durations to buckets, counts to words), so a new version is written only when something a reader
would care about changed.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from services.ops.redact import redact
from services.ops.sqlio import as_db, as_session

KIND = "AIRFLOW_DAG"
MAX_TASKS = 60


def reference(env_id: str, dag_id: str) -> str:
    return f"ops.dag.{env_id}.{dag_id}"


def duration_text(seconds: Any) -> Optional[str]:
    """A rounded duration: under a minute, whole minutes under 10, 5-minute steps under an hour, 15 above."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if value < 60:
        return "under a minute"
    minutes = value / 60
    step = 1 if minutes < 10 else 5 if minutes < 60 else 15
    rounded = max(step, int(round(minutes / step)) * step)
    return f"{rounded} min" if rounded < 60 else f"{rounded / 60:.2g} h".replace(".0 h", " h")


def times_text(count: Any) -> str:
    n = int(count or 0)
    return "once" if n <= 1 else "a few times" if n < 5 else "often"


def _list(value: Any) -> List[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value] if value else []
    return list(value or [])


def task_order(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tasks in run order (upstream first), ties by id; a cycle or unknown edge falls back to the id order."""
    by_id = {t["task_id"]: t for t in tasks}
    depth: Dict[str, int] = {}

    def level(task_id: str, seen: frozenset) -> int:
        if task_id in depth:
            return depth[task_id]
        if task_id in seen:
            return 0
        ups = [u for u in _list(by_id[task_id].get("upstream")) if u in by_id]
        depth[task_id] = 1 + max((level(u, seen | {task_id}) for u in ups), default=-1)
        return depth[task_id]

    for t in tasks:
        level(t["task_id"], frozenset())
    return sorted(tasks, key=lambda t: (depth.get(t["task_id"], 0), t["task_id"]))


def task_line(t: Dict[str, Any]) -> str:
    parts = [f"- {t['task_id']}" + (f" ({t['operator']})" if t.get("operator") else "")]
    ups = _list(t.get("upstream"))
    if ups:
        parts.append("after " + ", ".join(ups[:5]))
    if _list(t.get("reads")):
        parts.append("reads " + ", ".join(_list(t["reads"])[:8]))
    if _list(t.get("writes")):
        parts.append("writes " + ", ".join(_list(t["writes"])[:8]))
    if _list(t.get("dbt_selectors")):
        parts.append("dbt " + " ".join(_list(t["dbt_selectors"])[:8]))
    if t.get("snaplogic_pipeline"):
        parts.append(f"SnapLogic pipeline {t['snaplogic_pipeline']}")
    return parts[0] + (": " + "; ".join(parts[1:]) if len(parts) > 1 else "")


def load(db: Any, env_id: str, dag_id: str) -> Optional[Dict[str, Any]]:
    """Everything a profile says, read from the platform's tables."""
    found = db.query("""SELECT DAG_ID, ENV_ID, DOMAIN_ID, OWNERS, TAGS, SCHEDULE, DESCRIPTION, CRITICALITY, EXPECTED_BY_CRON,
                               MAX_DURATION_MIN, TIMEZONE, FILELOC, IS_PAUSED FROM CORE.DAG
                         WHERE ENV_ID = %s AND DAG_ID = %s""", (env_id, dag_id))
    if not found:
        return None
    out: Dict[str, Any] = {"dag": found[0]}
    stats = db.query("""SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY DURATION_S) AS P50_S,
                               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY DURATION_S) AS P95_S
                          FROM CORE.DAG_RUN WHERE ENV_ID = %s AND DAG_ID = %s AND STATE = 'success'
                           AND COALESCE(STARTED_AT, LOGICAL_DATE) >= DATEADD(day, -30, CURRENT_TIMESTAMP())""",
                     (env_id, dag_id))
    out["stats"] = stats[0] if stats else {}
    out["tasks"] = db.query("""SELECT TASK_ID, OPERATOR, UPSTREAM, READS, WRITES, DBT_SELECTORS, SNAPLOGIC_PIPELINE
                                 FROM CORE.DAG_TASK WHERE ENV_ID = %s AND DAG_ID = %s ORDER BY TASK_ID""", (env_id, dag_id))
    deps = db.query("""SELECT UPSTREAM_DAG_ID, DOWNSTREAM_DAG_ID FROM CORE.DAG_DEPENDENCY
                        WHERE ENV_ID = %s AND (UPSTREAM_DAG_ID = %s OR DOWNSTREAM_DAG_ID = %s)""", (env_id, dag_id, dag_id))
    out["upstream"] = sorted({d["upstream_dag_id"] for d in deps if d["downstream_dag_id"] == dag_id})
    out["downstream"] = sorted({d["downstream_dag_id"] for d in deps if d["upstream_dag_id"] == dag_id})
    try:
        out["failures"] = db.query("""
            SELECT F.TASK_ID, F.EXCEPTION_CLASS, MIN(F.EXCEPTION_TEXT) AS EXCEPTION_TEXT, COUNT(*) AS N,
                   MAX(K.TITLE) AS FIX_TITLE, MAX(K.STATUS) AS FIX_STATUS
              FROM CORE.FAILURE_EVENT F LEFT JOIN CORE.KNOWN_ISSUE K ON K.ISSUE_ID = F.ISSUE_ID
             WHERE F.ENV_ID = %s AND F.DAG_ID = %s AND F.CREATED_AT >= DATEADD(day, -30, CURRENT_TIMESTAMP())
             GROUP BY F.TASK_ID, F.EXCEPTION_CLASS, F.ERROR_SIGNATURE
             ORDER BY N DESC, F.TASK_ID LIMIT 8""", (env_id, dag_id))
    except Exception:
        out["failures"] = []
    return out


def entry(profile: Dict[str, Any]) -> Dict[str, Any]:
    """{title, content, content_json, tags} of one profile; every string redacted."""
    dag = profile["dag"]
    dag_id, env_id = dag["dag_id"], dag["env_id"]
    owners = _list(dag.get("owners"))
    head = [f"Airflow DAG {dag_id} (environment {env_id})."]
    if dag.get("description"):
        head.append(str(dag["description"])[:400])
    facts = []
    for label, value in (("Schedule", dag.get("schedule")), ("Owners", ", ".join(map(str, owners))),
                         ("Criticality", dag.get("criticality")), ("Expected done by (cron)", dag.get("expected_by_cron")),
                         ("Maximum duration", f"{dag['max_duration_min']} min" if dag.get("max_duration_min") else None),
                         ("Time zone", dag.get("timezone")), ("Paused", "yes" if dag.get("is_paused") else None)):
        if value:
            facts.append(f"{label}: {value}.")
    stats = profile.get("stats") or {}
    p50, p95 = duration_text(stats.get("p50_s")), duration_text(stats.get("p95_s"))
    if p50:
        facts.append(f"Usual duration (successful runs, 30 days): about {p50}" + (f", slow runs {p95}" if p95 and p95 != p50 else "") + ".")
    lines = [" ".join(head), " ".join(facts)]
    if profile.get("upstream"):
        lines.append("Waits for DAGs: " + ", ".join(profile["upstream"][:20]) + ".")
    if profile.get("downstream"):
        lines.append("DAGs that wait for it: " + ", ".join(profile["downstream"][:20]) + ".")
    tasks = task_order(list(profile.get("tasks") or []))
    if tasks:
        lines.append(f"Tasks ({len(tasks)}), in run order:")
        lines += [task_line(t) for t in tasks[:MAX_TASKS]]
        if len(tasks) > MAX_TASKS:
            lines.append(f"- and {len(tasks) - MAX_TASKS} more")
    failures = list(profile.get("failures") or [])
    if failures:
        lines.append("Failures in the last 30 days:")
        for f in failures:
            fix = (f" Known fix: {f['fix_title']} ({str(f.get('fix_status') or '').lower()})." if f.get("fix_title")
                   else " No known fix yet.")
            text = " ".join(str(f.get("exception_text") or "").split())[:200]
            lines.append(f"- {f.get('task_id') or 'DAG'}: {f.get('exception_class') or 'Error'}: {text} "
                         f"({times_text(f.get('n'))}).{fix}")
    content = redact("\n".join(line for line in lines if line.strip()))
    tables = sorted({x for t in tasks for x in _list(t.get("reads")) + _list(t.get("writes"))})
    content_json = {"env_id": env_id, "dag_id": dag_id, "schedule": dag.get("schedule"), "owners": owners,
                    "criticality": dag.get("criticality"), "expected_by_cron": dag.get("expected_by_cron"),
                    "max_duration_min": dag.get("max_duration_min"), "usual_duration": p50, "slow_duration": p95,
                    "upstream_dags": profile.get("upstream") or [], "downstream_dags": profile.get("downstream") or [],
                    "tasks": [{"task_id": t["task_id"], "operator": t.get("operator"), "upstream": _list(t.get("upstream")),
                               "reads": _list(t.get("reads")), "writes": _list(t.get("writes")),
                               "dbt_selectors": _list(t.get("dbt_selectors")),
                               "snaplogic_pipeline": t.get("snaplogic_pipeline")} for t in tasks[:MAX_TASKS]],
                    "tables": tables[:200],
                    "recurring_failures": [{"task_id": f.get("task_id"), "exception_class": f.get("exception_class"),
                                            "times": times_text(f.get("n")), "known_fix": f.get("fix_title")}
                                           for f in failures]}
    tags = ["OPS", "AIRFLOW", str(dag_id)[:100]] + [str(t)[:100] for t in _list(dag.get("tags"))[:8]]
    return {"title": redact(f"DAG {dag_id} ({env_id})")[:500], "content": content, "content_json": content_json,
            "tags": list(dict.fromkeys(tags))}


def remember_profile(source: Any, env_id: str, dag_id: str, remember: Optional[Any] = None) -> Optional[str]:
    """The KNOWLEDGE_ID written, or None (unknown DAG, or nothing changed). Never raises."""
    try:
        db, session = as_db(source), as_session(source)
        profile = load(db, env_id, dag_id)
        if not profile:
            return None
        item = entry(profile)
        from services.ops.resolution import domain_for

        if remember is None:
            from services.knowledge.writer import remember
        return remember(session, domain_id=domain_for(db, session, {"env_id": env_id, "dag_id": dag_id}), kind=KIND,
                        key=reference(env_id, dag_id), title=item["title"], content=item["content"],
                        content_json=item["content_json"], tags=item["tags"], origin="OPS",
                        change_note="Profile from the Airflow catalogue")
    except Exception:
        return None
