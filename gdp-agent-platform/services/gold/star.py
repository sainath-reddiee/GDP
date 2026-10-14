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


SKIP = ("release/branch.json", "release/skills.json", "release/workspace.json")


def _model_blocks(body: str) -> List[str]:
    """The `  - name: x` entries of a dbt schema file's models list, each with its lines."""
    blocks: List[str] = []
    for line in body.strip("\n").split("\n"):
        if line.startswith("  - name:"):
            blocks.append(line)
        elif blocks:
            blocks[-1] += "\n" + line
    return blocks


def merge_files(children: List[Dict[str, Any]]) -> Dict[str, Any]:
    """{"files", "conflicts"}: every table run's generated files as one pull request (pure). Models, DDL and macros
    keep their paths; a dimension two tables add is the same file and is kept once; the schema YAML of the domain's
    gold folder lists every table's models; each table's Soda checks, STTM export and generation report get their
    own path. children: [{"entity", "files": {path: text}}]."""
    out: Dict[str, str] = {}
    conflicts: List[str] = []
    schema: Dict[str, List[str]] = {}
    for child in children:
        entity = str(child.get("entity") or "table").lower()
        for path, text in (child.get("files") or {}).items():
            if path in SKIP or text is None:
                continue
            if path == "soda/checks.yml":
                path = f"soda/gold/{entity}.yml"
            elif path == "mappings/sttm.json":
                path = f"mappings/gold/{entity}.sttm.json"
            elif path == "release/gold-generation-report.json":
                path = f"release/gold/{entity}.report.json"
            elif path.startswith("models/") and path.endswith(".yml") and "\nmodels:" in f"\n{text}":
                blocks = schema.setdefault(path, [])
                for block in _model_blocks(text.split("models:", 1)[1]):
                    name = block.split("\n", 1)[0]
                    if not any(b.split("\n", 1)[0] == name for b in blocks):   # a shared dimension is listed once
                        blocks.append(block)
                continue
            if path in out and out[path] != text:
                if path != "dbt_project.yml":
                    conflicts.append(path)
                continue
            out[path] = text
    for path, bodies in schema.items():
        out[path] = "version: 2\n\nmodels:\n" + "\n".join(bodies) + "\n"
    return {"files": out, "conflicts": sorted(set(conflicts))}
