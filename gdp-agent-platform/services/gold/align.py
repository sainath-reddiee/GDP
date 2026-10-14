"""A gold proposal aligned to the domain's reference gold model (pure).

When a person has given the platform the gold model they expect (a data model reference for the GOLD layer, V057), the
reference is the answer key: its tables, columns, types, keys and grain are kept exactly. The proposal only fills what
the reference lacks, and says why for each addition:

- A proposed table matches a reference table by name, else by its stem (FACT_OPPORTUNITY and OPPORTUNITY_FACT), else by
  the columns they share (at least half of the proposal's business columns). A matched table is the reference table;
  proposed columns it does not have are added with a note, so the reviewer keeps or drops them in the Workbench.
- A proposed table with no match is added with a note ("not in your reference"); a reference table no proposal matches
  is kept, with a note that no profiled silver table feeds it yet.
- Mapping lines follow the renames, so mapping and the STTM target the reference names.
- Relationships: the reference's, plus proposed ones whose two ends exist in the aligned model.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

TECH = re.compile(r"^(GDP_[A-Z_]+|[A-Z0-9_]+_(SKEY|HKEY))$")


def _entities(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(((spec or {}).get("design") or {}).get("entities") or [])


def _name(e: Dict[str, Any]) -> str:
    return str(e.get("entity_name") or "").upper()


def _cols(e: Dict[str, Any]) -> List[str]:
    return [str(a.get("name") or "").upper() for a in e.get("attributes") or []]


def _core(name: str) -> str:
    """FACT_OPPORTUNITY, OPPORTUNITY_FACT, F_OPPORTUNITY -> OPPORTUNITY."""
    n = re.sub(r"^(FACT|FCT|F|DIM|D|BRIDGE|BRG)_", "", name.upper())
    return re.sub(r"_(FACT|FCT|DIM|BRIDGE)$", "", n)


def _business(cols: List[str]) -> set:
    return {c for c in cols if not TECH.match(c)}


def _column_in(name: str, ref_cols: List[str]) -> Optional[str]:
    """The reference column a proposed column is (same name, or _F / _FLAG spelled the other way)."""
    n = name.upper()
    if n in ref_cols:
        return n
    for alt in ((n[:-5] + "_F") if n.endswith("_FLAG") else None, (n[:-2] + "_FLAG") if n.endswith("_F") else None):
        if alt and alt in ref_cols:
            return alt
    return None


def match(proposed: List[Dict[str, Any]], reference: List[Dict[str, Any]]) -> Dict[str, Tuple[str, str]]:
    """{proposed table: (reference table, how it matched)}; each reference table is matched at most once."""
    out: Dict[str, Tuple[str, str]] = {}
    taken: set = set()
    ref_by_name = {_name(r): r for r in reference}
    for p in proposed:
        if _name(p) in ref_by_name:
            out[_name(p)] = (_name(p), "same name")
            taken.add(_name(p))
    for p in proposed:
        if _name(p) in out or p.get("conformed_from"):
            continue
        same_core = [r for r in reference if _name(r) not in taken and _core(_name(r)) == _core(_name(p))
                     and str(r.get("table_type") or "").upper() in ("", str(p.get("table_type") or "").upper(), "TABLE")]
        if len(same_core) == 1:
            out[_name(p)] = (_name(same_core[0]), "same subject")
            taken.add(_name(same_core[0]))
    for p in proposed:
        if _name(p) in out or p.get("conformed_from"):
            continue
        mine = _business(_cols(p))
        best, score = None, 0.0
        for r in reference:
            if _name(r) in taken or not mine:
                continue
            shared = len(mine & _business(_cols(r))) / len(mine)
            if shared > score:
                best, score = r, shared
        if best is not None and score >= 0.5:
            out[_name(p)] = (_name(best), f"{round(score * 100)}% of its columns")
            taken.add(_name(best))
    return out


def align(proposal: Dict[str, Any], reference: Dict[str, Any], title: str = "your reference") -> Dict[str, Any]:
    """{spec, lines, notes, alignment} with the reference kept exactly and only what it lacks added from the proposal."""
    spec = copy.deepcopy(proposal["spec"])
    proposed = _entities(spec)
    ref_entities = [copy.deepcopy(e) for e in _entities(reference)]
    pairs = match(proposed, ref_entities)
    ref_by_name = {_name(r): r for r in ref_entities}
    notes = list(proposal.get("notes") or [])
    renames: Dict[str, str] = {}
    column_renames: Dict[Tuple[str, str], str] = {}
    alignment: List[Dict[str, Any]] = []
    out_entities: List[Dict[str, Any]] = []
    used: set = set()

    for p in proposed:
        pname = _name(p)
        if pname not in pairs:
            if not p.get("conformed_from") and _name(p) not in ref_by_name:
                p["description"] = f"{p.get('description') or ''} Not in {title}: proposed from the profiled silver tables.".strip()
                notes.append(f"{pname} is not in {title}; it was proposed from silver (keep it or drop it in the Workbench)")
                alignment.append({"proposed": pname, "reference": None, "how": "added"})
            out_entities.append(p)
            continue
        rname, how = pairs[pname]
        r = ref_by_name[rname]
        used.add(rname)
        renames[pname] = rname
        ref_cols = _cols(r)
        ref_pk = next((str(a["name"]).upper() for a in r.get("attributes") or [] if a.get("is_pk")), None)
        added = []
        for a in p.get("attributes") or []:
            col = str(a.get("name") or "").upper()
            own = col in (f"{pname}_SKEY", f"{pname}_HKEY")
            target = f"{rname}{col[len(pname):]}" if own else _column_in(col, ref_cols)
            if own and col.endswith("_SKEY") and target not in ref_cols and ref_pk:
                target = ref_pk   # the reference names its own surrogate key differently
            if target and target in ref_cols:
                if target != col:
                    column_renames[(pname, col)] = target
                continue
            if own:
                continue
            extra = dict(a, description=f"{a.get('description') or ''} Not in {title}: proposed from silver.".strip())
            r.setdefault("attributes", []).append(extra)
            added.append(col)
        for keep in ("database", "schema"):
            if not r.get(keep) and p.get(keep):
                r[keep] = p[keep]
        if str(r.get("table_type") or "TABLE").upper() == "TABLE" and p.get("table_type"):
            r["table_type"] = p["table_type"]
        r["description"] = r.get("description") or p.get("description")
        if added:
            notes.append(f"{rname}: {len(added)} column(s) from silver are not in {title} ({', '.join(added[:6])}"
                         f"{' ...' if len(added) > 6 else ''}); kept as proposals")
        alignment.append({"proposed": pname, "reference": rname, "how": how, "added_columns": added})
        out_entities.append(r)

    for r in ref_entities:
        if _name(r) not in used and _name(r) not in {_name(e) for e in out_entities}:
            notes.append(f"{_name(r)} is in {title} but no profiled silver table feeds it yet; add its source to the run")
            alignment.append({"proposed": None, "reference": _name(r), "how": "reference only"})
            out_entities.append(r)

    def rename(table: str) -> str:
        return renames.get(str(table or "").upper(), table)

    lines = []
    for ln in proposal.get("lines") or []:
        t = str(ln.get("gold_table") or "").upper()
        c = str(ln.get("gold_column") or "").upper()
        lines.append(dict(ln, gold_table=rename(t), gold_column=column_renames.get((t, c), c)))

    names = {_name(e): set(_cols(e)) for e in out_entities}
    rels = [dict(r) for r in reference.get("relationships") or []]
    seen = {(str(r.get("from_entity")).upper(), str(r.get("from_column")).upper(), str(r.get("to_entity")).upper())
            for r in rels}
    for r in spec.get("relationships") or []:
        fe, te = rename(str(r["from_entity"]).upper()), rename(str(r["to_entity"]).upper())
        fc = column_renames.get((str(r["from_entity"]).upper(), str(r["from_column"]).upper()), str(r["from_column"]).upper())
        tc = column_renames.get((str(r["to_entity"]).upper(), str(r["to_column"]).upper()), str(r["to_column"]).upper())
        if fe in names and te in names and fc in names[fe] and (fe, fc, te) not in seen:
            rels.append(dict(r, from_entity=fe, from_column=fc, to_entity=te, to_column=tc))
            seen.add((fe, fc, te))

    spec["design"]["entities"] = out_entities
    spec["design"]["primary_entity"] = _name(out_entities[0]) if out_entities else None
    spec["relationships"] = rels
    for k in ("physical_defaults", "naming_standard"):
        if reference.get(k):
            spec[k] = reference[k]
    return {"spec": spec, "lines": lines, "notes": notes, "alignment": alignment}
