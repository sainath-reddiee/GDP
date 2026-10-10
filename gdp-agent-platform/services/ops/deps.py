"""DAG to DAG dependencies for storm grouping (OPS.DAG_DEPENDENCY), read by the poller at most every 30 minutes per
environment.

Sources, each stored with its KIND:
  DATASET     Airflow 2 datasets (/datasets: producing_tasks -> consuming_dags) and Airflow 3 assets (/assets:
              producing_tasks -> scheduled_dags or consuming_dags)
  SENSOR      an ExternalTaskSensor in B waits for A: A is upstream of B
  MARKER      an ExternalTaskMarker in A clears B with it: A is upstream of B
  TRIGGER     a TriggerDagRunOperator in A starts B: A is upstream of B
  CODE_GRAPH  B runs dbt models whose upstream models are run by A (only for DAGs mapped to an indexed repository)
  MANUAL      set by hand; never changed or removed here
The other DAG of a sensor, marker or trigger comes from the task definition when Airflow exposes it (external_dag_id,
trigger_dag_id, also inside params), else from the task instance's resolved extra link (it points at the other DAG).
Everything is optional: a source that fails or is not exposed adds nothing, and the poll goes on.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import parse_qs, unquote, urlparse

REFRESH_MINUTES = 30
MAX_TASK_DAGS = 150        # /dags/{id}/tasks calls per refresh; the rest are read on the next refreshes (rotating)
MAX_LINK_LOOKUPS = 30
STALE_TASK_DAYS = 7        # sensor, marker and trigger edges not seen again for this long are dropped
AUTO_KINDS = ("DATASET", "SENSOR", "MARKER", "TRIGGER", "CODE_GRAPH")
PRIORITY = {"MANUAL": 0, "SENSOR": 1, "TRIGGER": 2, "MARKER": 3, "DATASET": 4, "CODE_GRAPH": 5}
SENSOR_CLASSES = {"ExternalTaskSensor", "ExternalTaskSensorAsync", "ExternalDagSensor"}
MARKER_CLASSES = {"ExternalTaskMarker"}
TRIGGER_CLASSES = {"TriggerDagRunOperator"}
_OTHER_KEYS = {"SENSOR": ("external_dag_id",), "MARKER": ("external_dag_id",), "TRIGGER": ("trigger_dag_id",)}
_DAG_IN_PATH = re.compile(r"/dags/([^/?#]+)")


def edge(env_id: str, upstream: Any, downstream: Any, kind: str) -> Optional[Dict[str, Any]]:
    up, down = str(upstream or "").strip(), str(downstream or "").strip()
    if not up or not down or up == down or len(up) > 250 or len(down) > 250:
        return None
    return {"env_id": env_id, "upstream_dag_id": up, "downstream_dag_id": down, "kind": kind}


def _dag_ids(items: Any) -> Set[str]:
    out: Set[str] = set()
    for item in items or []:
        if isinstance(item, dict) and item.get("dag_id"):
            out.add(str(item["dag_id"]))
        elif isinstance(item, str) and item:
            out.add(item)
    return out


def dataset_edges(env_id: str, datasets: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Producer DAG -> consumer DAG for every dataset (v1) or asset (v2)."""
    out: List[Dict[str, Any]] = []
    for d in datasets or []:
        if not isinstance(d, dict):
            continue
        producers = _dag_ids(d.get("producing_tasks"))
        consumers = _dag_ids(d.get("consuming_dags")) | _dag_ids(d.get("scheduled_dags")) | _dag_ids(d.get("consuming_tasks"))
        for up in sorted(producers):
            for down in sorted(consumers):
                found = edge(env_id, up, down, "DATASET")
                if found:
                    out.append(found)
    return out


def task_class(task: Dict[str, Any]) -> str:
    """The operator class name of a task definition: class_ref.class_name (v1 and v2), else operator_name (v2)."""
    ref = task.get("class_ref") or {}
    name = ref.get("class_name") if isinstance(ref, dict) else None
    return str(name or task.get("operator_name") or task.get("operator") or "")


def task_kind(task: Dict[str, Any]) -> Optional[str]:
    name = task_class(task)
    if name in SENSOR_CLASSES:
        return "SENSOR"
    if name in MARKER_CLASSES:
        return "MARKER"
    if name in TRIGGER_CLASSES:
        return "TRIGGER"
    return None


def _value(found: Any) -> Optional[str]:
    if isinstance(found, dict):
        found = found.get("value", found.get("__var"))
    if isinstance(found, str) and found.strip() and "{{" not in found:
        return found.strip()
    return None


def other_dag(task: Dict[str, Any], kind: str) -> Optional[str]:
    """The other DAG's id when the task definition exposes it (top level, `params`, or `extra`)."""
    for key in _OTHER_KEYS.get(kind, ()):
        for holder in (task, task.get("params") or {}, task.get("extra") or {}):
            if isinstance(holder, dict) and key in holder:
                found = _value(holder.get(key))
                if found:
                    return found
    return None


def task_edges(env_id: str, dag_id: str, tasks: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """(edges, unresolved [(task_id, kind)]) for the sensor, marker and trigger tasks of one DAG."""
    edges: List[Dict[str, Any]] = []
    unresolved: List[Tuple[str, str]] = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        kind = task_kind(task)
        if not kind:
            continue
        other = other_dag(task, kind)
        if not other:
            unresolved.append((str(task.get("task_id") or ""), kind))
            continue
        found = edge(env_id, other, dag_id, kind) if kind == "SENSOR" else edge(env_id, dag_id, other, kind)
        if found:
            edges.append(found)
    return edges, unresolved


def dag_from_url(url: Any, exclude: Optional[str] = None) -> Optional[str]:
    """The DAG an Airflow UI link points at: /dags/<id>/... (Airflow 2.3+ and 3) or ?dag_id=<id> (older views)."""
    if not isinstance(url, str) or not url:
        return None
    parsed = urlparse(url)
    candidates = [unquote(m) for m in _DAG_IN_PATH.findall(parsed.path or "")]
    candidates += parse_qs(parsed.query or "").get("dag_id", [])
    for c in candidates:
        if c and c != exclude:
            return c
    return None


def link_edges(env_id: str, dag_id: str, kind: str, links: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for url in (links or {}).values():
        other = dag_from_url(url, exclude=dag_id)
        if not other:
            continue
        found = edge(env_id, other, dag_id, kind) if kind == "SENSOR" else edge(env_id, dag_id, other, kind)
        if found:
            out.append(found)
    return out


def code_graph_edges(env_id: str, dag_models: Dict[str, Iterable[str]], graph: Any, depth: int = 2) -> List[Dict[str, Any]]:
    """A -> B when a model B runs depends (within `depth` steps in the code graph) on a model only A runs."""
    from services.code.graph import key

    owner: Dict[str, Set[str]] = {}
    for dag_id, models in dag_models.items():
        for m in models:
            owner.setdefault(key(m), set()).add(dag_id)
    out: List[Dict[str, Any]] = []
    for dag_id, models in dag_models.items():
        for m in models:
            for up in graph.uses(m, depth):
                for other in owner.get(key(up["name"]), set()):
                    if other != dag_id and dag_id not in owner.get(key(up["name"]), set()):
                        found = edge(env_id, other, dag_id, "CODE_GRAPH")
                        if found:
                            out.append(found)
    return out


def merge_edges(edges: Iterable[Optional[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """One edge per (env, upstream, downstream): the most specific kind wins (a sensor beats a dataset)."""
    best: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for e in edges:
        if not e:
            continue
        k = (e["env_id"], e["upstream_dag_id"], e["downstream_dag_id"])
        if k not in best or PRIORITY.get(e["kind"], 9) < PRIORITY.get(best[k]["kind"], 9):
            best[k] = e
    return sorted(best.values(), key=lambda e: (e["upstream_dag_id"], e["downstream_dag_id"]))


# ---------------------------------------------------------------- storage

def upsert(db: Any, edges: List[Dict[str, Any]]) -> int:
    """MERGE the edges; a MANUAL row is never overwritten."""
    if not edges:
        return 0
    db.execute("""
        MERGE INTO OPS.DAG_DEPENDENCY T USING (
            SELECT F.VALUE:env_id::VARCHAR AS ENV_ID, F.VALUE:upstream_dag_id::VARCHAR AS UPSTREAM_DAG_ID,
                   F.VALUE:downstream_dag_id::VARCHAR AS DOWNSTREAM_DAG_ID, F.VALUE:kind::VARCHAR AS KIND
              FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))) F) S
        ON T.ENV_ID = S.ENV_ID AND T.UPSTREAM_DAG_ID = S.UPSTREAM_DAG_ID AND T.DOWNSTREAM_DAG_ID = S.DOWNSTREAM_DAG_ID
        WHEN MATCHED AND COALESCE(T.KIND, '') <> 'MANUAL' THEN UPDATE SET KIND = S.KIND, UPDATED_AT = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (ENV_ID, UPSTREAM_DAG_ID, DOWNSTREAM_DAG_ID, KIND)
             VALUES (S.ENV_ID, S.UPSTREAM_DAG_ID, S.DOWNSTREAM_DAG_ID, S.KIND)""", (json.dumps(edges),))
    return len(edges)


def prune(db: Any, env_id: str, kinds: List[str], before: Optional[str] = None, days: Optional[int] = None) -> None:
    """Drop automatic edges of `kinds` not seen again: older than the refresh start `before`, or than `days`."""
    if not kinds:
        return
    marks = ", ".join(["%s"] * len(kinds))
    if before:
        db.execute(f"""DELETE FROM OPS.DAG_DEPENDENCY WHERE ENV_ID = %s AND KIND IN ({marks})
                         AND UPDATED_AT < TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)""", (env_id, *kinds, before))
    elif days:
        db.execute(f"""DELETE FROM OPS.DAG_DEPENDENCY WHERE ENV_ID = %s AND KIND IN ({marks})
                         AND UPDATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP())""", (env_id, *kinds, -abs(int(days))))


def claim_refresh(db: Any, env_id: str, minutes: int = REFRESH_MINUTES) -> Tuple[bool, int]:
    """(due, rotation offset): True for the one caller that may refresh now (a compare-and-set on the deps:<env> row
    of OPS.JOB_LEASE, so two pollers never both refresh)."""
    from services.ops import lease

    job = f"deps:{env_id}"
    lease.ensure(db, job)
    got = db.execute_count("""UPDATE OPS.JOB_LEASE SET LAST_RUN_AT = CURRENT_TIMESTAMP()
                               WHERE JOB_NAME = %s AND (LAST_RUN_AT IS NULL
                                     OR LAST_RUN_AT < DATEADD(minute, %s, CURRENT_TIMESTAMP()))""", (job, -abs(int(minutes))))
    if got < 1:
        return False, 0
    found = db.query("SELECT CURSOR_VALUE FROM OPS.JOB_LEASE WHERE JOB_NAME = %s", (job,))
    try:
        offset = int((found[0].get("cursor_value") if found else 0) or 0)
    except (TypeError, ValueError):
        offset = 0
    return True, max(0, offset)


def _db_now(db: Any) -> Optional[str]:
    from services.ops.normalize import ts

    try:
        found = db.query("SELECT CURRENT_TIMESTAMP() AS NOW")
        return ts(found[0].get("now")) if found else None
    except Exception:
        return None


def _latest_task_runs(db: Any, env_id: str, pairs: List[Tuple[str, str]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    if not pairs:
        return {}
    found = db.query("""
        SELECT T.DAG_ID, T.TASK_ID, T.RUN_ID, T.MAP_INDEX FROM OPS.TASK_RUN T
          JOIN TABLE(FLATTEN(INPUT => PARSE_JSON(%s))) F ON T.DAG_ID = F.VALUE[0]::VARCHAR AND T.TASK_ID = F.VALUE[1]::VARCHAR
         WHERE T.ENV_ID = %s
        QUALIFY ROW_NUMBER() OVER (PARTITION BY T.DAG_ID, T.TASK_ID ORDER BY COALESCE(T.STARTED_AT, T.UPDATED_AT) DESC NULLS LAST) = 1""",
                     (json.dumps([list(p) for p in pairs]), env_id))
    return {(r["dag_id"], r["task_id"]): r for r in found}


def dag_models(db: Any, env_id: str) -> Tuple[Dict[str, Set[str]], List[str]]:
    """({dag_id: dbt models it runs}, repo ids) for DAGs mapped to an indexed repository: Cosmos task names seen in
    the last 14 days, plus dbt commands in the DAG file as indexed."""
    from services.ops import code_map

    dags = db.query("""SELECT DAG_ID, FILELOC, REPO_ID, REPO_PATH FROM OPS.DAG
                        WHERE ENV_ID = %s AND REPO_ID IS NOT NULL AND COALESCE(IS_ACTIVE, TRUE)""", (env_id,))
    if not dags:
        return {}, []
    out: Dict[str, Set[str]] = {d["dag_id"]: set() for d in dags}
    tasks = db.query("""SELECT DISTINCT DAG_ID, TASK_ID, OPERATOR FROM OPS.TASK_RUN
                         WHERE ENV_ID = %s AND ARRAY_CONTAINS(DAG_ID::VARIANT, PARSE_JSON(%s))
                           AND COALESCE(STARTED_AT, UPDATED_AT) >= DATEADD(day, -14, CURRENT_TIMESTAMP())
                         LIMIT 20000""", (env_id, json.dumps(sorted(out))))
    for t in tasks:
        for m in code_map.task_models(t.get("task_id"), t.get("operator"), None):
            name = code_map.selector_model(m)
            if name:
                out[t["dag_id"]].add(name)
    for d in dags:
        path = code_map.candidate_repo_path(d.get("fileloc"), d.get("repo_path"))
        if not path:
            continue
        chunks = db.query("SELECT TEXT FROM CODE.CODE_CHUNK WHERE REPO_ID = %s AND PATH = %s ORDER BY START_LINE LIMIT 200",
                          (d["repo_id"], path))
        source = "\n".join(str(c.get("text") or "") for c in chunks)
        for selector in code_map.source_task_models(source):
            name = code_map.selector_model(selector)
            if name:
                out[d["dag_id"]].add(name)
    return {k: v for k, v in out.items() if v}, sorted({d["repo_id"] for d in dags if d.get("repo_id")})


def refresh(db: Any, mw: Any, env_id: str, offset: int = 0) -> Dict[str, Any]:
    """Read every source once and store the edges. Returns counts per kind and what could not be read."""
    started = _db_now(db)
    edges: List[Optional[Dict[str, Any]]] = []
    problems: List[str] = []
    complete: List[str] = []

    try:
        datasets, done = mw.datasets()
        edges += dataset_edges(env_id, datasets)
        if done:
            complete.append("DATASET")
    except Exception as exc:
        problems.append(f"datasets: {type(exc).__name__}")

    dag_ids = [r["dag_id"] for r in db.query("""SELECT DAG_ID FROM OPS.DAG WHERE ENV_ID = %s AND COALESCE(IS_ACTIVE, TRUE)
                                                 ORDER BY DAG_ID""", (env_id,))]
    if offset >= len(dag_ids):
        offset = 0
    batch = (dag_ids[offset:] + dag_ids[:offset])[:MAX_TASK_DAGS]
    next_offset = (offset + len(batch)) % max(1, len(dag_ids)) if dag_ids else 0
    unresolved: List[Tuple[str, str, str]] = []
    for dag_id in batch:
        try:
            found, missing = task_edges(env_id, dag_id, mw.dag_tasks(dag_id))
        except Exception as exc:
            problems.append(f"tasks of {dag_id}: {type(exc).__name__}")
            continue
        edges += found
        unresolved += [(dag_id, t, k) for t, k in missing if t]
    if unresolved:
        latest = _latest_task_runs(db, env_id, [(d, t) for d, t, _ in unresolved[:MAX_LINK_LOOKUPS]])
        for dag_id, task_id, kind in unresolved[:MAX_LINK_LOOKUPS]:
            run = latest.get((dag_id, task_id))
            if not run:
                continue
            try:
                edges += link_edges(env_id, dag_id, kind, mw.task_links(dag_id, run["run_id"], task_id, int(run.get("map_index") or -1)))
            except Exception:
                continue

    try:
        models, repo_ids = dag_models(db, env_id)
        if models and repo_ids:
            from services.code import graph as code_graph
            from services.ops.sqlio import to_pyformat

            rows = lambda sql, params: db.query(to_pyformat(sql, bool(params)), tuple(params))  # noqa: E731
            edges += code_graph_edges(env_id, models, code_graph.load(rows, repo_ids))
            complete.append("CODE_GRAPH")
    except Exception as exc:
        problems.append(f"code graph: {type(exc).__name__}")

    merged = merge_edges(edges)
    upsert(db, merged)
    if started:
        prune(db, env_id, complete, before=started)
    prune(db, env_id, ["SENSOR", "MARKER", "TRIGGER"], days=STALE_TASK_DAYS)
    db.execute("UPDATE OPS.JOB_LEASE SET CURSOR_VALUE = %s WHERE JOB_NAME = %s", (str(next_offset), f"deps:{env_id}"))
    counts: Dict[str, int] = {}
    for e in merged:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    return {"edges": len(merged), "by_kind": counts, "dags_read": len(batch), "problems": problems[:20]}


def refresh_if_due(db: Any, mw: Any, env_id: str, minutes: int = REFRESH_MINUTES) -> Optional[Dict[str, Any]]:
    """Refresh when 30 minutes have passed since the last one. Never raises: dependencies are an optional input."""
    try:
        due, offset = claim_refresh(db, env_id, minutes)
        if not due:
            return None
        return refresh(db, mw, env_id, offset)
    except Exception as exc:
        return {"edges": 0, "problems": [f"{type(exc).__name__}"]}
