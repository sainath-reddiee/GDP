"""Walk the knowledge graph upstream from a failed task (CORE.GRAPH_NODE / CORE.GRAPH_EDGE, views over DAGs, code,
lineage and documents). Edges point the way data flows, so "upstream" follows TO -> FROM."""

from __future__ import annotations

import re
from typing import Any, Dict, List

MAX_NODES = 60
_NAME = re.compile(r"[^A-Za-z0-9_]")


def start_nodes(db: Any, failure: Dict[str, Any]) -> List[str]:
    """The failed task (its catalogue node: its upstream tasks and the tables it reads), the failed DAG, and the code
    symbol of its dbt node or task (matched by name)."""
    ids = [f"task:{failure['env_id']}:{failure['dag_id']}:{failure['task_id']}"] if failure.get("task_id") else []
    ids.append(f"dag:{failure['env_id']}:{failure['dag_id']}")
    names = []
    node = str(failure.get("dbt_node_id") or "")
    if node:
        names.append(node.split(".")[-1])
    if failure.get("task_id"):
        names.append(str(failure["task_id"]).split(".")[-1])
    for name in names:
        clean = _NAME.sub("", name)[:200]
        if not clean:
            continue
        found = db.query("""SELECT NODE_ID FROM CORE.GRAPH_NODE WHERE NODE_TYPE = 'CODE_SYMBOL'
                             AND (UPPER(NAME) = UPPER(%s) OR UPPER(NAME) LIKE UPPER(%s)) LIMIT 5""", (clean, f"%.{clean}"))
        ids += [r["node_id"] for r in found]
    return list(dict.fromkeys(ids))


def walk(db: Any, failure: Dict[str, Any], hops: int = 3) -> Dict[str, Any]:
    """{start, nodes: [{node_id, node_type, name, source_uri, depth}], edges: [{from, to, type}]} up to `hops` levels
    upstream, at most MAX_NODES nodes."""
    start = start_nodes(db, failure)
    seen: Dict[str, int] = {n: 0 for n in start}
    frontier, edges = list(start), []
    for depth in range(1, max(1, int(hops)) + 1):
        if not frontier or len(seen) >= MAX_NODES:
            break
        marks = ", ".join(["%s"] * len(frontier))
        found = db.query(f"SELECT FROM_NODE_ID, TO_NODE_ID, EDGE_TYPE FROM CORE.GRAPH_EDGE WHERE TO_NODE_ID IN ({marks}) LIMIT 200",
                         tuple(frontier))
        nxt = []
        for e in found:
            edges.append({"from": e["from_node_id"], "to": e["to_node_id"], "type": e["edge_type"]})
            if e["from_node_id"] not in seen and len(seen) < MAX_NODES:
                seen[e["from_node_id"]] = depth
                nxt.append(e["from_node_id"])
        frontier = nxt
    nodes: List[Dict[str, Any]] = []
    ids = list(seen)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        marks = ", ".join(["%s"] * len(chunk))
        for r in db.query(f"SELECT NODE_ID, NODE_TYPE, NAME, SOURCE_URI FROM CORE.GRAPH_NODE WHERE NODE_ID IN ({marks})",
                          tuple(chunk)):
            nodes.append({**dict(r), "depth": seen.get(r["node_id"], 0)})
    nodes.sort(key=lambda n: (n["depth"], str(n.get("name") or "")))
    return {"start": start, "nodes": nodes, "edges": edges[:300]}


def path_names(graph: Dict[str, Any], limit: int = 8) -> List[str]:
    """Readable upstream path for a ticket: nearest first."""
    out: List[str] = []
    for n in graph.get("nodes") or []:
        if n.get("depth", 0) == 0:
            continue
        label = f"{str(n.get('node_type') or '').lower().replace('_', ' ')} {n.get('name')}"
        if label not in out:
            out.append(label)
        if len(out) >= limit:
            break
    return out


def text(graph: Dict[str, Any], limit: int = 40) -> str:
    lines = []
    for n in (graph.get("nodes") or [])[:limit]:
        lines.append(f"[{n.get('depth')}] {n.get('node_type')} {n.get('name')}" +
                     (f" ({n.get('source_uri')})" if n.get("source_uri") else ""))
    return "\n".join(lines) or "(no upstream nodes found)"

