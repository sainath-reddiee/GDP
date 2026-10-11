"""What changed between two versions of a domain model, and how much it matters (pure).

Entities, columns and relationships are matched by id first, then by name, so a rename is reported as a rename. Every
change has a class:

  ADDITIVE        safe for everything downstream: a new entity, a new nullable column, a wider type, a new relationship
  NON_FUNCTIONAL  documentation and classification only: descriptions, definitions, PII, tags, subject areas
  BREAKING        can break mappings, STTM, dbt, checks or tests that use the model: removing or retiring, renaming,
                  narrowing a type, nullable to not null, primary key, business key, grain, SCD or table type changes

Approving a version whose diff has a BREAKING change needs an explicit acknowledgement (services/modeling/domain_model).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

ADDITIVE, NON_FUNCTIONAL, BREAKING = "ADDITIVE", "NON_FUNCTIONAL", "BREAKING"
RANK = {NON_FUNCTIONAL: 0, ADDITIVE: 1, BREAKING: 2}

DOC_FIELDS = ("description", "business_definition", "semantic_type", "pii", "tags", "accepted_values", "rationale")
ENTITY_DOC = ("description", "purpose", "subject_area")
ENTITY_BREAKING = ("table_type", "scd_type", "grain", "business_keys")


def _type_parts(datatype: str) -> Tuple[str, List[int]]:
    t = (datatype or "").upper().replace(" ", "")
    m = re.match(r"^([A-Z_]+)(?:\(([\d,]+)\))?$", t)
    if not m:
        return t, []
    return m.group(1), [int(x) for x in (m.group(2) or "").split(",") if x]


TEXT = {"VARCHAR", "STRING", "TEXT", "CHAR", "CHARACTER", "NCHAR", "NVARCHAR"}
NUMBER = {"NUMBER", "DECIMAL", "NUMERIC"}
INTS = {"INT": 9, "INTEGER": 38, "SMALLINT": 4, "TINYINT": 2, "BYTEINT": 2, "BIGINT": 18}


def type_change(old: str, new: str) -> str:
    """WIDEN, NARROW or CHANGE (a different family) for two Snowflake types; SAME when equivalent."""
    if (old or "").upper().replace(" ", "") == (new or "").upper().replace(" ", ""):
        return "SAME"
    (ob, oa), (nb, na) = _type_parts(old), _type_parts(new)
    if ob in TEXT and nb in TEXT:
        ol = oa[0] if oa else 16777216
        nl = na[0] if na else 16777216
        return "SAME" if ol == nl else ("WIDEN" if nl > ol else "NARROW")
    onum = ob in NUMBER or ob in INTS
    nnum = nb in NUMBER or nb in INTS
    if onum and nnum:
        op, os_ = (oa + [38, 0])[:2] if ob in NUMBER else (INTS[ob], 0)
        np_, ns = (na + [38, 0])[:2] if nb in NUMBER else (INTS[nb], 0)
        if len(oa) == 1 and ob in NUMBER:
            os_ = 0
        if len(na) == 1 and nb in NUMBER:
            ns = 0
        if (np_, ns) == (op, os_):
            return "SAME"
        return "WIDEN" if np_ - ns >= op - os_ and ns >= os_ else "NARROW"
    if ob.startswith("TIMESTAMP") and nb.startswith("TIMESTAMP"):
        return "CHANGE" if ob != nb else ("WIDEN" if (na or [9])[0] >= (oa or [9])[0] else "NARROW")
    return "CHANGE"


def _match(old: List[Dict[str, Any]], new: List[Dict[str, Any]], key: str) -> List[Tuple[Optional[Dict], Optional[Dict]]]:
    """Pairs (old, new) matched by id, then by name; unmatched sides are None."""
    pairs: List[Tuple[Optional[Dict], Optional[Dict]]] = []
    new_by_id = {n.get("id"): n for n in new if n.get("id")}
    used = set()
    rest_old = []
    for o in old:
        n = new_by_id.get(o.get("id"))
        if n is not None and id(n) not in used:
            pairs.append((o, n))
            used.add(id(n))
        else:
            rest_old.append(o)
    new_by_name = {str(n.get(key) or "").upper(): n for n in new if id(n) not in used}
    for o in rest_old:
        n = new_by_name.get(str(o.get(key) or "").upper())
        if n is not None and id(n) not in used:
            pairs.append((o, n))
            used.add(id(n))
        else:
            pairs.append((o, None))
    pairs += [(None, n) for n in new if id(n) not in used]
    return pairs


def _change(path: str, kind: str, cls: str, message: str, before: Any = None, after: Any = None,
            entity: str = "", column: str = "") -> Dict[str, Any]:
    return {"path": path, "kind": kind, "class": cls, "message": message, "before": before, "after": after,
            "entity": entity, "column": column}


def _columns(ename: str, old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for o, n in _match(old.get("attributes") or [], new.get("attributes") or [], "name"):
        if o is None:
            cls = ADDITIVE if n.get("nullable", True) or n.get("default") else BREAKING
            why = "" if cls == ADDITIVE else " (not null without a default: existing loads must supply it)"
            out.append(_change(f"{ename}.{n['name']}", "COLUMN_ADDED", cls, f"column {n['name']} added{why}",
                               after=n.get("datatype"), entity=ename, column=n["name"]))
            continue
        if n is None:
            out.append(_change(f"{ename}.{o['name']}", "COLUMN_REMOVED", BREAKING, f"column {o['name']} removed",
                               before=o.get("datatype"), entity=ename, column=o["name"]))
            continue
        path = f"{ename}.{n['name']}"
        if o["name"] != n["name"]:
            out.append(_change(path, "COLUMN_RENAMED", BREAKING, f"column {o['name']} renamed to {n['name']}",
                               o["name"], n["name"], ename, n["name"]))
        if o.get("status", "ACTIVE") != n.get("status", "ACTIVE"):
            retired = n.get("status") == "RETIRED"
            out.append(_change(path, "COLUMN_RETIRED" if retired else "COLUMN_RESTORED", BREAKING if retired else ADDITIVE,
                               f"column {n['name']} {'retired' if retired else 'restored'}", o.get("status"),
                               n.get("status"), ename, n["name"]))
        t = type_change(o.get("datatype", ""), n.get("datatype", ""))
        if t != "SAME":
            out.append(_change(path, "TYPE_CHANGED", ADDITIVE if t == "WIDEN" else BREAKING,
                               f"{n['name']} type {o.get('datatype')} to {n.get('datatype')} ({t.lower()})",
                               o.get("datatype"), n.get("datatype"), ename, n["name"]))
        if bool(o.get("nullable", True)) != bool(n.get("nullable", True)):
            tighter = not n.get("nullable", True)
            out.append(_change(path, "NULLABILITY_CHANGED", BREAKING if tighter else ADDITIVE,
                               f"{n['name']} is now {'NOT NULL' if tighter else 'nullable'}",
                               o.get("nullable"), n.get("nullable"), ename, n["name"]))
        if bool(o.get("is_pk")) != bool(n.get("is_pk")):
            out.append(_change(path, "PRIMARY_KEY_CHANGED", BREAKING,
                               f"{n['name']} {'added to' if n.get('is_pk') else 'removed from'} the primary key",
                               o.get("is_pk"), n.get("is_pk"), ename, n["name"]))
        for f in ("is_unique", "default", "check"):
            if (o.get(f) or None) != (n.get(f) or None):
                cls = BREAKING if f in ("is_unique", "check") and n.get(f) else ADDITIVE
                out.append(_change(path, f"{f.upper()}_CHANGED", cls, f"{n['name']} {f.replace('_', ' ')} changed",
                                   o.get(f), n.get(f), ename, n["name"]))
        for f in DOC_FIELDS:
            if (o.get(f) or None) != (n.get(f) or None):
                out.append(_change(path, f"{f.upper()}_CHANGED", NON_FUNCTIONAL,
                                   f"{n['name']} {f.replace('_', ' ')} changed", o.get(f), n.get(f), ename, n["name"]))
    return out


def diff(old: Optional[Dict[str, Any]], new: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """{changes: [...], counts: {ADDITIVE, NON_FUNCTIONAL, BREAKING}, breaking: bool, entities_touched: [...]}.
    Both sides are normalized v2 specifications (services/modeling/spec_v2.normalize_v2); None is an empty model."""
    old_entities = ((old or {}).get("design") or {}).get("entities") or []
    new_entities = ((new or {}).get("design") or {}).get("entities") or []
    changes: List[Dict[str, Any]] = []
    for o, n in _match(old_entities, new_entities, "entity_name"):
        if o is None:
            changes.append(_change(n["entity_name"], "ENTITY_ADDED", ADDITIVE, f"entity {n['entity_name']} added",
                                   after=len(n.get("attributes") or []), entity=n["entity_name"]))
            continue
        if n is None:
            changes.append(_change(o["entity_name"], "ENTITY_REMOVED", BREAKING, f"entity {o['entity_name']} removed",
                                   before=len(o.get("attributes") or []), entity=o["entity_name"]))
            continue
        name = n["entity_name"]
        if o["entity_name"] != name:
            changes.append(_change(name, "ENTITY_RENAMED", BREAKING, f"entity {o['entity_name']} renamed to {name}",
                                   o["entity_name"], name, name))
        if o.get("status", "ACTIVE") != n.get("status", "ACTIVE"):
            retired = n.get("status") == "RETIRED"
            changes.append(_change(name, "ENTITY_RETIRED" if retired else "ENTITY_RESTORED",
                                   BREAKING if retired else ADDITIVE, f"entity {name} {'retired' if retired else 'restored'}",
                                   o.get("status"), n.get("status"), name))
        for f in ENTITY_BREAKING:
            if (o.get(f) or None) != (n.get(f) or None):
                changes.append(_change(name, f"{f.upper()}_CHANGED", BREAKING, f"{name} {f.replace('_', ' ')} changed",
                                       o.get(f), n.get(f), name))
        if (o.get("database"), o.get("schema")) != (n.get("database"), n.get("schema")):
            changes.append(_change(name, "LOCATION_CHANGED", BREAKING, f"{name} moves to another database or schema",
                                   [o.get("database"), o.get("schema")], [n.get("database"), n.get("schema")], name))
        for f in ("unique_constraints", "checks"):
            if (o.get(f) or []) != (n.get(f) or []):
                changes.append(_change(name, f"{f.upper()}_CHANGED", BREAKING if n.get(f) else ADDITIVE,
                                       f"{name} {f.replace('_', ' ')} changed", o.get(f), n.get(f), name))
        if (o.get("physical") or {}) != (n.get("physical") or {}):
            changes.append(_change(name, "PHYSICAL_CHANGED", NON_FUNCTIONAL, f"{name} physical options changed",
                                   o.get("physical"), n.get("physical"), name))
        if (o.get("model_spec") or None) != (n.get("model_spec") or None):
            changes.append(_change(name, "MODEL_SPEC_CHANGED", BREAKING, f"{name} hub or spoke settings changed",
                                   o.get("model_spec"), n.get("model_spec"), name))
        for f in ENTITY_DOC:
            if (o.get(f) or None) != (n.get(f) or None):
                changes.append(_change(name, f"{f.upper()}_CHANGED", NON_FUNCTIONAL, f"{name} {f.replace('_', ' ')} changed",
                                       o.get(f), n.get(f), name))
        changes += _columns(name, o, n)
    old_rels = (old or {}).get("relationships") or []
    new_rels = (new or {}).get("relationships") or []
    for o, n in _match(old_rels, new_rels, "name"):
        r = n or o
        path = f"rel:{r['id']}"
        label = f"{r['from_entity']}.{r['from_column']} -> {r['to_entity']}.{r['to_column']}"
        if o is None:
            changes.append(_change(path, "RELATIONSHIP_ADDED", ADDITIVE, f"relationship {label} added",
                                   entity=r["from_entity"], column=r["from_column"]))
        elif n is None:
            changes.append(_change(path, "RELATIONSHIP_REMOVED", BREAKING, f"relationship {label} removed",
                                   entity=r["from_entity"], column=r["from_column"]))
        else:
            fields = [f for f in ("from_entity", "from_column", "to_entity", "to_column", "cardinality", "from_optional",
                                  "to_optional", "enforce") if o.get(f) != n.get(f)]
            if fields:
                changes.append(_change(path, "RELATIONSHIP_CHANGED",
                                       BREAKING if set(fields) & {"from_entity", "from_column", "to_entity", "to_column",
                                                                   "cardinality"} else NON_FUNCTIONAL,
                                       f"relationship {label}: {', '.join(fields)} changed",
                                       {f: o.get(f) for f in fields}, {f: n.get(f) for f in fields},
                                       r["from_entity"], r["from_column"]))
    if (old or {}).get("subject_areas") != (new or {}).get("subject_areas") and old is not None:
        changes.append(_change("model", "SUBJECT_AREAS_CHANGED", NON_FUNCTIONAL, "subject areas changed"))
    if old is not None and ((old or {}).get("physical_defaults") or {}) != ((new or {}).get("physical_defaults") or {}):
        changes.append(_change("model", "PHYSICAL_DEFAULTS_CHANGED", BREAKING, "default platform, database or schema changed",
                               (old or {}).get("physical_defaults"), (new or {}).get("physical_defaults")))
    counts = {ADDITIVE: 0, NON_FUNCTIONAL: 0, BREAKING: 0}
    for c in changes:
        counts[c["class"]] += 1
    return {"changes": changes, "counts": counts, "breaking": counts[BREAKING] > 0,
            "entities_touched": sorted({c["entity"] for c in changes if c["entity"]})}


def breaking_paths(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """The entity and column of every breaking change (what impact analysis looks up downstream)."""
    out, seen = [], set()
    for c in result["changes"]:
        if c["class"] != BREAKING or not c["entity"]:
            continue
        entity = c["before"] if c["kind"] == "ENTITY_RENAMED" else c["entity"]
        column = c["before"] if c["kind"] == "COLUMN_RENAMED" else c["column"]
        key = (entity, column)
        if key not in seen:
            seen.add(key)
            out.append({"entity": entity, "column": column, "kind": c["kind"]})
    return out
