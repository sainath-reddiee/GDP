"""Diffs for the diff workbench (pure): files, table model columns and STTM lines."""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional

from services.knowledge.skills import compact


def file_diff(path: str, old: Optional[str], new: Optional[str]) -> Dict[str, Any]:
    """One file in the shape components/diff-view.tsx renders: status, +/- counts and compacted line ops."""
    left, right = (old or "").splitlines(), (new or "").splitlines()
    status = "added" if old is None else "removed" if new is None else "same" if left == right else "changed"
    ops: List[List[Any]] = []
    plus = minus = 0
    if status != "same":
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
            if tag == "equal":
                ops.extend([" ", i1 + k + 1, j1 + k + 1, left[i1 + k]] for k in range(i2 - i1))
                continue
            for k in range(i1, i2):
                ops.append(["-", k + 1, None, left[k]])
                minus += 1
            for k in range(j1, j2):
                ops.append(["+", None, k + 1, right[k]])
                plus += 1
    return {"path": path, "status": status, "added": plus, "removed": minus, "ops": compact(ops)}


def files_diff(old: Dict[str, str], new: Dict[str, str]) -> List[Dict[str, Any]]:
    """Every file of two file sets (old order first, then new files)."""
    paths = list(old) + [p for p in new if p not in old]
    return [file_diff(p, old.get(p), new.get(p)) for p in paths]


def rendered_files(rendered: Dict[str, Any]) -> Dict[str, str]:
    """A table model render ({ddl, dbt: {path: text}}) as one file set."""
    out: Dict[str, str] = {}
    if rendered.get("ddl"):
        out["ddl.sql"] = str(rendered["ddl"])
    for path, text in (rendered.get("dbt") or {}).items():
        out[str(path)] = str(text)
    return out


def _attrs(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    entities = ((spec or {}).get("design") or {}).get("entities") or []
    return {str(a.get("name") or "").upper(): a for a in (entities[0].get("attributes") or [])} if entities else {}


COLUMN_FIELDS = ("datatype", "nullable", "is_pk", "description", "business_definition", "semantic_type")


def columns(old_spec: Dict[str, Any], new_spec: Dict[str, Any]) -> Dict[str, Any]:
    """The table model tab: the entity after the change with each column marked added, changed, removed or same."""
    before, after = _attrs(old_spec), _attrs(new_spec)
    entities = ((new_spec or {}).get("design") or {}).get("entities") or []
    rows = []
    for name, a in after.items():
        b = before.get(name)
        changed = [f for f in COLUMN_FIELDS if b is not None and b.get(f) != a.get(f)]
        rows.append({"name": a.get("name"), "datatype": a.get("datatype"), "nullable": a.get("nullable"),
                     "is_pk": bool(a.get("is_pk")), "description": a.get("description"),
                     "state": "added" if b is None else "changed" if changed else "same", "changed": changed})
    rows += [{"name": b.get("name"), "datatype": b.get("datatype"), "nullable": b.get("nullable"),
              "is_pk": bool(b.get("is_pk")), "description": b.get("description"), "state": "removed", "changed": []}
             for name, b in before.items() if name not in after]
    return {"entity": entities[0].get("entity_name") if entities else "", "columns": rows,
            "counts": {s: sum(1 for r in rows if r["state"] == s) for s in ("added", "changed", "removed")}}


STTM_FIELDS = ("target_datatype", "mapping_type", "source_table", "source_column", "transformation", "business_rule")


def sttm_lines(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The STTM tab: every target column whose mapping is added, changed or removed, before and after."""
    def key(line: Dict[str, Any]) -> str:
        return str(line.get("target_column") or "").upper()

    a, b = {key(l): l for l in old}, {key(l): l for l in new}
    rows = []
    for k in list(a) + [k for k in b if k not in a]:
        o, n = a.get(k), b.get(k)
        if o is not None and n is not None and all(o.get(f) == n.get(f) for f in STTM_FIELDS):
            continue
        rows.append({"target_column": (n or o).get("target_column"),
                     "state": "added" if o is None else "removed" if n is None else "changed",
                     "before": {f: (o or {}).get(f) for f in STTM_FIELDS} if o else None,
                     "after": {f: (n or {}).get(f) for f in STTM_FIELDS} if n else None})
    return {"lines": rows}
