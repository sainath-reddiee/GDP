"""End-to-end lineage (pure): Source, Silver pre-MDM, MDM, Silver post-MDM, Gold, at entity and column level.

Edges come from where the platform already holds the truth, so nothing drifts:
  INGEST       a source object landed as a table (CORE.LANDING_TABLE_REGISTRY)
  MAP          an approved STTM line: source column to silver target column (CORE.STTM_LINE)
  MDM_MATCH    a pre-MDM record matched to the cross-reference on its source key (mastered-domain contract)
  MDM_GOLDEN   the cross-reference's mastered id to the golden record (contract)
  BUILD        the golden record to the gold dimension, or a silver column to a gold column (gold generation)
  RESOLVE      a key resolved to another key: a consumer hub's pre-MDM key, or a mastered gold key
Objects are DATABASE.SCHEMA.TABLE; the trace matches on the full name or on SCHEMA.TABLE, so the same model in another
environment still connects.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Tuple

LAYERS = ("SOURCE", "BRONZE", "SILVER_PRE_MDM", "MDM", "SILVER_POST_MDM", "GOLD")


def _fq(section: Dict[str, Any], env: str, table: Optional[str] = None) -> str:
    return f"{str(section['database']).replace('{ENV}', env.upper())}.{section['schema']}.{table or section['table']}".upper()


def edge(from_layer: str, from_object: str, from_column: Optional[str], to_layer: str, to_object: str, to_column: Optional[str],
         kind: str, expression: Optional[str] = None, producer: str = "") -> Dict[str, Any]:
    return {"from_layer": from_layer, "from_object": str(from_object).upper(), "from_column": (from_column or None) and str(from_column).upper(),
            "to_layer": to_layer, "to_object": str(to_object).upper(), "to_column": (to_column or None) and str(to_column).upper(),
            "kind": kind, "expression": expression, "producer": producer}


def mdm_edges(name: str, c: Dict[str, Any], env: str = "DEV") -> List[Dict[str, Any]]:
    """A mastered domain's path from pre-MDM silver to its gold dimension."""
    pre, x, golden, gold = c["pre_mdm"], c["cross_reference"], c["golden"], c["gold"]
    p, xr, gn, dm = _fq(pre, env), _fq(x, env), _fq(golden, env), _fq(gold, env, gold["dimension"])
    who = f"MDM_CONTRACT:{name}"
    return [
        edge("SILVER_PRE_MDM", p, pre["source_key"], "MDM", xr, x["source_key"], "MDM_MATCH",
             f"{pre['source_key']} + upper source-system name = {x['source_key']} + {x['system_name']} (active, latest)", who),
        # within the cross-reference, a source record's row carries the mastered id Reltio matched it to
        edge("MDM", xr, x["source_key"], "MDM", xr, x["mastered_id"], "MDM_MATCH",
             f"Reltio's match: the source record's {x['mastered_id']} ({x.get('cardinality', 'N:1')})", who),
        edge("MDM", xr, x["mastered_id"], "SILVER_POST_MDM", gn, golden["mastered_id"], "MDM_GOLDEN",
             "the golden record of the mastered id (latest version)", who),
        edge("SILVER_POST_MDM", gn, golden["mastered_id"], "GOLD", dm, gold["mastered_id"], "BUILD", "one gold row per mastered id", who),
        edge("SILVER_PRE_MDM", p, pre["skey"], "GOLD", dm, gold["skey"], "RESOLVE",
             f"{pre['skey']} -> {x['table']} -> {gold['dimension']}.{gold['skey']} (-1 when unresolved)", who),
    ]


def consumer_edges(name: str, consumes: Iterable[Dict[str, Any]], tables: Iterable[Dict[str, Any]],
                   mastered: Dict[str, Dict[str, Any]], env: str = "DEV") -> List[Dict[str, Any]]:
    """A consumer hub's pre-MDM keys, each a lookup of the mastered domain's pre-MDM core skey.
    tables: [{object: DB.SCHEMA.TABLE, columns: [names]}]"""
    out = []
    for c in consumes:
        m = mastered.get(str(c["domain"]).upper())
        if not m:
            continue
        key = str(c["silver_column"]).upper()
        core = _fq(m["pre_mdm"], env)
        for t in tables:
            for col in t.get("columns") or []:
                col = str(col).upper()
                if col == key or col.endswith("_" + key):
                    out.append(edge("SILVER_PRE_MDM", core, m["pre_mdm"]["skey"], "SILVER_PRE_MDM", t["object"], col, "RESOLVE",
                                    f"lookup of the source record in {m['pre_mdm']['table']} (-1 when no match)", f"MDM_CONTRACT:{name}"))
    return out


def _keys(obj: str) -> Tuple[str, str]:
    parts = str(obj).upper().split(".")
    return ".".join(parts), ".".join(parts[-2:])


def trace(edges: List[Dict[str, Any]], obj: str, column: Optional[str] = None, depth: int = 8,
          direction: str = "both") -> Dict[str, Any]:
    """Every edge reachable upstream and downstream of an object (and column, when given), and the objects on the way,
    each with its layer and the columns the path touches."""
    full, short = _keys(obj)
    col = column.upper() if column else None

    def same(a: str, b_full: str, b_short: str) -> bool:
        af, ash = _keys(a)
        return af == b_full or ash == b_short

    picked: Dict[int, Dict[str, Any]] = {}
    starts = [(full, short, col)]
    for d in (("up", "down") if direction == "both" else (direction,)):
        queue = deque([(full, short, col, 0)])
        seen = set()
        while queue:
            f, s, c, n = queue.popleft()
            if (f, c, d) in seen or n >= depth:
                continue
            seen.add((f, c, d))
            for i, e in enumerate(edges):
                here, there = (("to_object", "to_column"), ("from_object", "from_column")) if d == "up" else (("from_object", "from_column"), ("to_object", "to_column"))
                if not same(e[here[0]], f, s):
                    continue
                if c and e[here[1]] and e[here[1]] != c:
                    continue
                picked[i] = e
                nf, ns = _keys(e[there[0]])
                queue.append((nf, ns, e[there[1]], n + 1))
    nodes: Dict[str, Dict[str, Any]] = {}
    for e in picked.values():
        for side in ("from", "to"):
            o = e[f"{side}_object"]
            node = nodes.setdefault(o, {"object": o, "layer": e[f"{side}_layer"], "columns": set()})
            if e[f"{side}_column"]:
                node["columns"].add(e[f"{side}_column"])
    start = next((n for n in nodes.values() if same(n["object"], full, short)), None)
    order = {l: i for i, l in enumerate(LAYERS)}
    out_nodes = sorted(({**n, "columns": sorted(n["columns"])} for n in nodes.values()),
                       key=lambda n: (order.get(n["layer"], 99), n["object"]))
    return {"start": {"object": start["object"] if start else full, "column": col}, "nodes": out_nodes,
            "edges": list(picked.values()), "layers": [l for l in LAYERS if any(n["layer"] == l for n in out_nodes)], "starts": starts}
