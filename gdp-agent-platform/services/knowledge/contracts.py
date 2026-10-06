"""Parse GDP domain contract markdown (snowflake/skills/gdp/dbt-onboard-source/references/<domain>-contract.md).

A contract holds, per silver target: the canonical column table, the HKEY column list, the reference CTEs and
LEFT JOINs that resolve REF_*_SKEY columns, and the final SELECT order; plus required mapping keys and a type
cast table for the domain. The parser is deliberately literal: it reads the fenced blocks and tables the skill
tells engineers to paste, so the generated dbt code uses exactly what the contract says.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

CANONICAL = re.compile(r"^##\s+(?:\d+\.\s+)?(?:([A-Z][A-Z0-9_]+)\s+[—-]\s+)?Canonical Column Contract", re.M)
SUBSECTION = re.compile(r"^#{2,3}\s+(?:([A-Z][A-Z0-9_]+)\s+[—-]\s+)?(Reference CTE Block|Reference JOIN Block|HKEY Block|"
                        r"Final SELECT Block)", re.M)
HEADING = re.compile(r"^#{1,3}\s+.*$", re.M)
FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.S)
TARGET_HUB = re.compile(r"Target (?:hub(?: model)?|table):\s*`([A-Z0-9_]+)\.([A-Z0-9_]+)\.([A-Z0-9_]+)`")
CTE = re.compile(r"(?m)^([a-z_][a-z0-9_]*)\s+as\s*\(")
JOIN = re.compile(r"left\s+join\s+([a-z_][a-z0-9_]*)\s+as\s+([a-z_][a-z0-9_]*)\s+on\s+(.*?)(?=\bleft\s+join\b|\Z)",
                  re.S | re.I)
BRACES = re.compile(r"\{([^}]+)\}")


def _section(text: str, start: int) -> str:
    """Text from `start` to the next heading at the same or higher level."""
    nxt = HEADING.search(text, text.find("\n", start) + 1)
    return text[start:nxt.start()] if nxt else text[start:]


def _expand(cell: str) -> List[str]:
    """'STREET_NUMBER_{1,2,3}, CITY' -> ['STREET_NUMBER_1', 'STREET_NUMBER_2', 'STREET_NUMBER_3', 'CITY']."""
    out: List[str] = []
    for part in re.split(r",\s*(?![^{]*\})", cell):
        part = part.strip().strip("`")
        if not part:
            continue
        m = BRACES.search(part)
        if m:
            out += [part[:m.start()] + v.strip() + part[m.end():] for v in m.group(1).split(",")]
        else:
            out.append(part)
    return [c for c in out if re.match(r"^[A-Z][A-Z0-9_]*$", c)]


def _columns(section: str) -> List[Dict[str, Any]]:
    cols: List[Dict[str, Any]] = []
    for line in section.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[1].lower() in ("column", "target column"):
            continue
        names = _expand(cells[1])
        data_type = None if cells[2].startswith("(") else cells[2].strip("`")
        nullable = not cells[3].upper().startswith("NO")
        notes = (cells[4] if len(cells) > 4 else "") or (cells[3].split("(", 1)[1].rstrip(")") if "(" in cells[3] else "")
        for name in names:
            cols.append({"name": name, "type": data_type, "nullable": nullable, "notes": notes.strip() or None})
    seen, unique = set(), []
    for c in cols:
        if c["name"] not in seen:
            seen.add(c["name"])
            unique.append(c)
    return unique


def _fence(section: str) -> Optional[str]:
    m = FENCE.search(section)
    return m.group(1).strip() if m else None


def _ctes(sql: str) -> Dict[str, str]:
    """name -> full 'name as (...)' text, split on top-level parentheses."""
    out: Dict[str, str] = {}
    for m in CTE.finditer(sql):
        depth, i = 0, m.end() - 1
        while i < len(sql):
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            if depth == 0:
                break
            i += 1
        out[m.group(1)] = sql[m.start():i + 1]
    return out


def cte_output_column(cte_sql: str) -> Optional[str]:
    """First selected column alias of a reference CTE: the SKEY a REF_*_SKEY target takes."""
    m = re.search(r"select\s+(?:distinct\s+)?(.*?)(?:,|\n\s*from)", cte_sql, re.S | re.I)
    if not m:
        return None
    expr = m.group(1).strip()
    alias = re.search(r"\bas\s+([a-z_][a-z0-9_]*)\s*$", expr, re.I)
    return (alias.group(1) if alias else expr.split(".")[-1]).lower()


def _joins(sql: str) -> List[Dict[str, str]]:
    return [{"cte": m.group(1).lower(), "alias": m.group(2).lower(), "condition": " ".join(m.group(3).split())}
            for m in JOIN.finditer(sql)]


def _casts(text: str) -> List[Dict[str, str]]:
    m = re.search(r"^##\s+Type Cast Reference.*$", text, re.M)
    if not m:
        return []
    out = []
    for line in _section(text, m.start()).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) < 4 or "---" in line or cells[0].lower().startswith("target"):
            continue
        cast = cells[3].strip("`")
        for name in re.split(r"\s*/\s*|,\s*", cells[0]):
            name = name.strip().strip("`")
            if re.match(r"^[A-Z][A-Z0-9_]*$", name):
                out.append({"target_column": name, "target_type": cells[1].strip("`"), "source_type": cells[2],
                            "cast": cast})
    return out


def _required(text: str) -> List[str]:
    m = re.search(r"^#{2,3}\s+(?:\d+\.\s+)?Required Mapping Keys.*$", text, re.M)
    if not m:
        return []
    return [line.lstrip("- ").strip() for line in _section(text, m.start()).splitlines() if line.startswith("- ")]


def named_section(text: str, title_prefix: str) -> Optional[str]:
    m = re.search(rf"^##\s+{re.escape(title_prefix)}.*$", text, re.M)
    return _section(text, m.start()).strip() if m else None


def parse_contract(text: str) -> Dict[str, Any]:
    hub = TARGET_HUB.search(text)
    database, schema, hub_table = (hub.groups() if hub else (None, None, None))
    targets: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def target(name: str) -> Dict[str, Any]:
        if name not in targets:
            targets[name] = {"table": name, "columns": [], "hkey_columns": [], "reference_ctes": {},
                             "reference_joins": [], "final_select": []}
            order.append(name)
        return targets[name]

    for m in CANONICAL.finditer(text):
        name = m.group(1) or hub_table
        if name:
            target(name)["columns"] = _columns(_section(text, m.start()))
    for m in SUBSECTION.finditer(text):
        name = m.group(1) or hub_table
        if not name:
            continue
        block = _fence(_section(text, m.start())) or ""
        t = target(name)
        kind = m.group(2)
        if kind == "HKEY Block":
            t["hkey_columns"] = [c.upper() for c in re.findall(r"'([A-Za-z0-9_]+)'", block)]
        elif kind == "Reference CTE Block":
            t["reference_ctes"] = _ctes(block)
        elif kind == "Reference JOIN Block":
            t["reference_joins"] = _joins(block)
        elif kind == "Final SELECT Block":
            t["final_select"] = [l.strip().rstrip(",") for l in block.splitlines() if l.strip()]
    return {"database": database, "schema": schema, "hub": hub_table, "targets": [targets[n] for n in order],
            "required_mapping": _required(text), "casts": _casts(text)}


def ref_lookup(target_column: str, spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """For a REF_*_SKEY target column, the contract CTE that serves it:
    {"expr": "<alias>.<skey>", "joins": [...in dependency order], "ctes": [...], "attributes": [o.<attr> names read]}.

    Match order: a CTE whose output SKEY is the column without REF_ (ref_status -> OPPORTUNITY_STATUS_SKEY serves
    REF_OPPORTUNITY_STATUS_SKEY) or equals it; else the longest `ref_*` CTE name prefixing the column (ref_country
    serves REF_COUNTRY_OF_REGISTRATION_SKEY). Joins referencing another join's alias pull it in (country_mapping
    before ref_country)."""
    col = target_column.lower()
    ctes: Dict[str, str] = spec.get("reference_ctes") or {}
    joins: List[Dict[str, str]] = spec.get("reference_joins") or []
    usable = {n: cte_output_column(sql) for n, sql in ctes.items() if n != "ref_source_system"}
    exact = [n for n, out in usable.items() if out and out in (col, col[4:] if col.startswith("ref_") else col)]
    prefixed = [n for n in usable if n.startswith("ref_") and col.startswith(n)]
    cte = exact[0] if exact else (max(prefixed, key=len) if prefixed else None)
    if not cte:
        return None
    join = next((j for j in joins if j["cte"] == cte), None)
    column = usable[cte]
    if not join or not column:
        return None
    needed, queue = [], [join]
    while queue:
        j = queue.pop(0)
        if j in needed:
            continue
        needed.insert(0, j)
        for other in joins:
            if other is not j and re.search(rf"\b{re.escape(other['alias'])}\.", j["condition"]):
                queue.append(other)
    attributes = sorted({a.lower() for j in needed for a in re.findall(r"\bo\.([a-z_][a-z0-9_]*)", j["condition"], re.I)})
    return {"expr": f"{join['alias']}.{column}", "joins": needed, "ctes": [j["cte"] for j in needed],
            "attributes": attributes}
