"""Relationship-aware join planning for a multi-table STTM (pure).

Input: the run's source tables, candidate relationships (profile-inferred and name-based), and how many target
columns each table feeds. Output, stored in STTM_REGISTRY.TABLE_DESIGN.join_graph:

    {"driving_table": "STG_ACCOUNTS",
     "joins": [{"left_table", "right_table", "join_type": "LEFT" | "INNER", "keys": ["L_COL=R_COL"],
                "condition": "STG_ACCOUNTS.ACCOUNT_ID = STG_SUBSCRIPTIONS.ACC_FK", "cardinality": "N:1" | "1:1" | "1:N",
                "confidence", "reasoning", "fan_out", "alternatives": [...], "source": "profile" | "name" | "manual",
                "status": "SUGGESTED" | "CONFIRMED"}],
     "unreachable": [...], "ambiguities": [...]}

The plan is a maximum-confidence spanning tree rooted at the driving table, so every joined table has exactly one
path and the generated SQL never joins a table twice.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

AMBIGUITY_MARGIN = 0.1
INNER_MIN_CONFIDENCE = 0.9
JOIN_TYPES = ("LEFT", "INNER")

Edge = Dict[str, Any]


def _pairs(keys: Sequence[str]) -> List[Tuple[str, str]]:
    out = []
    for k in keys:
        left, _, right = str(k).partition("=")
        out.append((left.strip(), (right or left).strip()))
    return out


def _flip_cardinality(card: str) -> str:
    return {"N:1": "1:N", "1:N": "N:1"}.get(card, card)


def candidate_edges(relationships: Iterable[Edge]) -> List[Edge]:
    """Normalize relationships from insights.infer_relationships / er_graph.infer_joins into undirected candidates.
    Name-based joins without key evidence get a lower score than profile-backed ones."""
    out: List[Edge] = []
    for r in relationships:
        left, right = str(r["left"]).upper(), str(r["right"]).upper()
        if left == right or not r.get("keys"):
            continue
        source = r.get("source") or "name"
        confidence = float(r.get("confidence") or 0.5)
        if source != "profile":
            confidence = min(confidence, 0.7)
        out.append({"left": left, "right": right, "keys": [str(k).upper() for k in r["keys"]],
                    "cardinality": r.get("cardinality") or "N:N", "confidence": round(confidence, 2),
                    "evidence": list(r.get("evidence") or []), "source": source})
    best: Dict[Tuple[str, str, Tuple[str, ...]], Edge] = {}
    for e in out:
        sig = (e["left"], e["right"], tuple(e["keys"]))
        if sig not in best or best[sig]["confidence"] < e["confidence"]:
            best[sig] = e
    return sorted(best.values(), key=lambda e: -e["confidence"])


def choose_driving(tables: Sequence[str], mapped_counts: Dict[str, int], edges: Sequence[Edge]) -> str:
    """The table that feeds most target columns; ties go to the table that others reference as a parent (N:1)."""
    tables = [t.upper() for t in tables]
    parents = {t: 0 for t in tables}
    for e in edges:
        if e["cardinality"] == "N:1" and e["right"] in parents:
            parents[e["right"]] += 1
    return max(tables, key=lambda t: (mapped_counts.get(t, 0), parents.get(t, 0), -tables.index(t)))


def _orient(edge: Edge, from_table: str) -> Edge:
    """Express a candidate as from_table (already in the plan) -> other table."""
    if edge["left"] == from_table:
        return {**edge}
    keys = [f"{r}={l}" for l, r in _pairs(edge["keys"])]
    return {**edge, "left": edge["right"], "right": edge["left"], "keys": keys,
            "cardinality": _flip_cardinality(edge["cardinality"])}


def _join_type(edge: Edge, complete_keys: Dict[Tuple[str, str], bool]) -> str:
    if edge["cardinality"] != "1:1" or edge["confidence"] < INNER_MIN_CONFIDENCE:
        return "LEFT"
    for lcol, rcol in _pairs(edge["keys"]):
        if not complete_keys.get((edge["left"], lcol)) or not complete_keys.get((edge["right"], rcol)):
            return "LEFT"
    return "INNER"


def condition(edge: Edge) -> str:
    return " AND ".join(f"{edge['left']}.{l} = {edge['right']}.{r}" for l, r in _pairs(edge["keys"]))


def _reasoning(edge: Edge) -> str:
    parts = list(edge.get("evidence") or [])
    if edge["cardinality"] == "N:1":
        parts.append(f"each {edge['left']} row finds at most one {edge['right']} row")
    elif edge["cardinality"] == "1:N":
        parts.append(f"one {edge['left']} row matches many {edge['right']} rows (fan-out)")
    elif edge["cardinality"] == "1:1":
        parts.append("one-to-one")
    if edge["source"] == "name":
        parts.append("matched on column names only; confirm it")
    return "; ".join(parts) or "matched on key columns"


def build_join_graph(tables: Sequence[str], relationships: Iterable[Edge], mapped_counts: Dict[str, int],
                     complete_keys: Optional[Dict[Tuple[str, str], bool]] = None,
                     driving: Optional[str] = None) -> Dict[str, Any]:
    tables = [t.upper() for t in tables]
    complete_keys = complete_keys or {}
    edges = [e for e in candidate_edges(relationships) if e["left"] in tables and e["right"] in tables]
    if not tables:
        return {"driving_table": None, "joins": [], "unreachable": [], "ambiguities": []}
    root = (driving or "").upper() if (driving or "").upper() in tables else choose_driving(tables, mapped_counts, edges)
    in_tree, joins, ambiguities = {root}, [], []
    while True:
        frontier = [_orient(e, e["left"] if e["left"] in in_tree else e["right"]) for e in edges
                    if (e["left"] in in_tree) != (e["right"] in in_tree)]
        if not frontier:
            break
        # prefer confident edges, then lookups (N:1) over fan-outs, then tables that feed more columns
        frontier.sort(key=lambda e: (-e["confidence"], e["cardinality"] == "1:N", -mapped_counts.get(e["right"], 0)))
        best = frontier[0]
        rivals = [e for e in frontier[1:] if e["right"] == best["right"]]
        alternatives = [{"left_table": e["left"], "keys": e["keys"], "condition": condition(e),
                         "cardinality": e["cardinality"], "confidence": e["confidence"], "source": e["source"]}
                        for e in rivals[:3]]
        if rivals and best["confidence"] - rivals[0]["confidence"] < AMBIGUITY_MARGIN:
            ambiguities.append({"table": best["right"], "options": 1 + len(rivals),
                                "message": f"{best['right']} can join on more than one key with similar confidence; confirm the join"})
        joins.append({
            "left_table": best["left"], "right_table": best["right"], "join_type": _join_type(best, complete_keys),
            "keys": best["keys"], "condition": condition(best), "cardinality": best["cardinality"],
            "confidence": best["confidence"], "reasoning": _reasoning(best), "fan_out": best["cardinality"] == "1:N",
            "alternatives": alternatives, "source": best["source"], "status": "SUGGESTED",
        })
        in_tree.add(best["right"])
    unreachable = [t for t in tables if t not in in_tree]
    return {"driving_table": root, "joins": joins, "unreachable": unreachable, "ambiguities": ambiguities}


def apply_overrides(graph: Dict[str, Any], driving: Optional[str], overrides: Sequence[Dict[str, Any]],
                    columns: Dict[str, Iterable[str]]) -> Dict[str, Any]:
    """Reviewer edits: replace or add joins (left_table, right_table, keys, join_type), optionally a new driving
    table. Every key column must exist; every joined table must be reachable from the driving table."""
    cols = {t.upper(): {c.upper() for c in cs} for t, cs in columns.items()}
    joins = {j["right_table"]: dict(j) for j in graph.get("joins") or []}
    for o in overrides:
        left, right = str(o["left_table"]).upper(), str(o["right_table"]).upper()
        assert left in cols and right in cols, f"unknown table in join {left} -> {right}"
        assert left != right, "a table cannot join itself"
        if o.get("remove"):
            joins.pop(right, None)
            continue
        join_type = str(o.get("join_type") or "LEFT").upper()
        assert join_type in JOIN_TYPES, f"join_type must be one of {JOIN_TYPES}"
        keys = [str(k).upper() for k in o.get("keys") or []]
        assert keys, f"join {left} -> {right} needs at least one key"
        for lcol, rcol in _pairs(keys):
            assert lcol in cols[left], f"{left}.{lcol} does not exist"
            assert rcol in cols[right], f"{right}.{rcol} does not exist"
        edge = {"left_table": left, "right_table": right, "join_type": join_type, "keys": keys,
                "cardinality": o.get("cardinality") or joins.get(right, {}).get("cardinality") or "N:1",
                "confidence": 1.0, "reasoning": "set by reviewer", "fan_out": o.get("cardinality") == "1:N",
                "alternatives": [], "source": "manual", "status": "CONFIRMED"}
        edge["condition"] = condition({"left": left, "right": right, "keys": keys})
        joins[right] = edge
    root = (driving or graph.get("driving_table") or "").upper()
    assert root in cols, f"driving table {root} is not a source table of this run"
    joins.pop(root, None)
    reached, ordered = {root}, []
    pending = list(joins.values())
    while pending:
        nxt = next((j for j in pending if j["left_table"] in reached), None)
        if nxt is None:
            break
        ordered.append(nxt)
        reached.add(nxt["right_table"])
        pending.remove(nxt)
    unreachable = sorted(set(cols) - reached)
    return {**graph, "driving_table": root, "joins": ordered, "unreachable": unreachable,
            "ambiguities": [a for a in graph.get("ambiguities") or [] if a["table"] not in {o["right_table"].upper() for o in overrides}],
            "edited": True}


def join_logic_by_table(graph: Dict[str, Any]) -> Dict[str, str]:
    """For each table, the chain of joins from the driving table (STTM_LINE.JOIN_LOGIC)."""
    parent = {j["right_table"]: j for j in graph.get("joins") or []}
    out: Dict[str, str] = {}
    for table in [graph.get("driving_table"), *parent]:
        if not table:
            continue
        chain, cur = [], table
        while cur in parent:
            j = parent[cur]
            chain.append(f"{j['join_type']} JOIN {j['right_table']} ON {j['condition']}")
            cur = j["left_table"]
        out[table] = f"FROM {graph['driving_table']}" + ("".join(f" {c}" for c in reversed(chain)) if chain else "")
    return out


def to_dbt_joins(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The planned joins in the shape services.dbt.onboard expects (left/right/keys/cardinality/join_type)."""
    return [{"left": j["left_table"], "right": j["right_table"], "keys": j["keys"], "cardinality": j["cardinality"],
             "join_type": j["join_type"], "confidence": j["confidence"]} for j in graph.get("joins") or []]


def preview_sql(graph: Dict[str, Any], lines: Sequence[Dict[str, Any]], target_table: str) -> str:
    """Readable SELECT showing how the confirmed joins and mappings assemble the target."""
    driving = graph.get("driving_table")
    if not driving:
        return f"-- {target_table}: no source tables mapped yet\n"
    aliases = {driving: "o", **{j["right_table"]: f"j{i + 1}" for i, j in enumerate(graph.get("joins") or [])}}
    selects = []
    for line in lines:
        target = line.get("target_column")
        table = str(line.get("source_table") or "").upper()
        col = line.get("source_column")
        if line.get("transformation"):
            expr = str(line["transformation"])
        elif table and col:
            expr = f"{aliases.get(table, table.lower())}.{col}"
        else:
            expr = "NULL"
        note = "  -- no join path" if table and table not in aliases else ""
        selects.append(f"    {expr} AS {target}{note}")
    body = ",\n".join(selects) or "    *"
    joins = "".join(
        f"\n{j['join_type']} JOIN {j['right_table']} AS {aliases[j['right_table']]}\n    ON "
        + " AND ".join(f"{aliases[j['left_table']]}.{l} = {aliases[j['right_table']]}.{r}" for l, r in _pairs(j["keys"]))
        + (f"  -- fan-out: one row per {j['right_table']} row" if j.get("fan_out") else "")
        for j in graph.get("joins") or [])
    return f"-- {target_table}\nSELECT\n{body}\nFROM {driving} AS o{joins}\n"
