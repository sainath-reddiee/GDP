"""A Gold run's star schema as table runs (pure).

The Gold run selects and profiles the silver tables and gets the domain's gold model approved. Then each own FACT and
BRIDGE of the approved model becomes a table run of its own (a child run, PARENT_RUN_ID), reading the silver table
that feeds it; each table run builds its table and the proposed dimensions the table adds. Conformed dimensions are
the mastered domains' and are never built here. Tables the reviewer added without a silver source are reported.
"""

from __future__ import annotations

from typing import Any, Dict, List

BUILT_BY_RUN = ("FACT", "BRIDGE")


def plan(spec: Dict[str, Any], proposal_lines: List[Dict[str, Any]], inputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """{"tables": [{entity, table_type, source_table}], "unsourced": [entity], "dimensions": [entity]} for the
    approved gold spec, the proposal's mapping lines over the run's inputs (gold_table -> source_table), and the
    inputs ({database, schema, table})."""
    available = {f"{t['database']}.{t['schema']}.{t['table']}".upper() for t in inputs}
    source_of: Dict[str, str] = {}
    for line in proposal_lines:
        src = str(line.get("source_table") or "").upper()
        if src in available and line.get("kind") in ("COLUMN", "RESOLVE"):
            source_of.setdefault(str(line["gold_table"]).upper(), src)
    tables, unsourced, dims = [], [], []
    for e in spec["design"]["entities"]:
        if e.get("conformed_from") or e.get("status") == "RETIRED":
            continue
        name = str(e["entity_name"]).upper()
        if e.get("table_type") not in BUILT_BY_RUN:
            dims.append(name)
            continue
        if name in source_of:
            tables.append({"entity": name, "table_type": e["table_type"], "source_table": source_of[name]})
        else:
            unsourced.append(name)
    order = {"FACT": 0, "BRIDGE": 1}
    tables.sort(key=lambda t: (order.get(t["table_type"], 2), t["entity"]))
    return {"tables": tables, "unsourced": sorted(unsourced), "dimensions": sorted(dims)}


def least_advanced(states: List[str], ordinal: Dict[str, int]) -> str:
    """The state of the table run that is furthest behind (what a Gold run's list row shows)."""
    live = [s for s in states if s not in ("CANCELLED",)]
    return min(live or states, key=lambda s: ordinal.get(s, 10_000)) if states else ""
