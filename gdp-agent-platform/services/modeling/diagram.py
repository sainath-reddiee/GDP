"""A domain's data model as the domain page draws it (pure): the entities and relationships of one version, the saved
diagram positions, what a draft changes against the approved version, and what each table already has downstream
(knowledge, STTM, a table model)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from services.modeling import model_diff, spec_v2

ADDED = {"ENTITY_ADDED", "ENTITY_RESTORED", "COLUMN_ADDED", "COLUMN_RESTORED"}
REMOVED = {"ENTITY_REMOVED", "ENTITY_RETIRED", "COLUMN_REMOVED", "COLUMN_RETIRED"}


def highlight(approved: Optional[Dict[str, Any]], shown: Dict[str, Any]) -> Dict[str, Any]:
    """Marks for the canvas: entities and ENTITY.COLUMN added, changed or removed, plus the change messages."""
    if not approved:
        return {"entities": {}, "columns": {}, "changes": [], "removed_entities": []}
    result = model_diff.diff(approved, shown)
    entities: Dict[str, str] = {}
    columns: Dict[str, str] = {}
    removed: List[str] = []
    names = {e["entity_name"] for e in shown["design"]["entities"]}
    for c in result["changes"]:
        path, kind = str(c.get("path") or ""), str(c.get("kind") or "")
        mark = "added" if kind in ADDED else "removed" if kind in REMOVED else "changed"
        if "." in path:
            entity = path.split(".", 1)[0]
            columns[path] = mark
            entities.setdefault(entity, "changed")
        elif path in names:
            entities[path] = mark
        elif path:
            removed.append(path)
    return {"entities": entities, "columns": columns, "removed_entities": sorted(set(removed)),
            "changes": [{"path": c.get("path"), "kind": c.get("kind"), "class": c.get("class"), "message": c.get("message")}
                        for c in result["changes"]][:200],
            "counts": result.get("counts") or {}}


def build(spec: Dict[str, Any], *, model_id: str, layout: Optional[Dict[str, Any]] = None,
          approved: Optional[Dict[str, Any]] = None, knowledge: Optional[Dict[str, int]] = None,
          sttm: Iterable[str] = (), table_models: Iterable[str] = ()) -> Dict[str, Any]:
    """Canvas-ready entities and relationships of `spec` (retired ones left out), positions by entity name, the
    changes against `approved` (when `spec` is a draft) and per-table coverage."""
    live = spec_v2.active(spec)
    by_id = {e["id"]: e["entity_name"] for e in spec["design"]["entities"]}
    positions = {by_id[k]: v for k, v in (layout or {}).items() if k in by_id and isinstance(v, dict)}
    knowledge = {str(k).upper(): int(v or 0) for k, v in (knowledge or {}).items()}
    sttm_set = {str(t).upper().rsplit(".", 1)[-1] for t in sttm if t}
    tm_set = {str(t).upper() for t in table_models if t}
    entities, coverage = [], {}
    for e in live["design"]["entities"]:
        name = e["entity_name"]
        entities.append({"entity_name": name, "kind": e.get("table_type") or e.get("kind") or "TABLE",
                         "purpose": e.get("description") or e.get("purpose") or "", "grain": e.get("grain") or "",
                         "business_keys": e.get("business_keys") or [], "subject_area": e.get("subject_area"),
                         "scd_type": e.get("scd_type"),
                         "attributes": [{k: a.get(k) for k in ("name", "datatype", "nullable", "is_pk", "source_columns",
                                                                 "derived", "description", "business_key")}
                                        for a in e["attributes"]]})
        coverage[name] = {"knowledge": knowledge.get(name, 0), "sttm": name in sttm_set, "table_model": name in tm_set}
    rels = [{k: r.get(k) for k in ("from_entity", "from_column", "to_entity", "to_column", "cardinality", "from_optional")}
            for r in live.get("relationships") or []]
    return {"model_id": model_id, "entities": entities, "relationships": rels, "positions": positions,
            "coverage": coverage, "highlight": highlight(spec_v2.active(approved) if approved else None, live),
            "subject_areas": sorted({e["subject_area"] for e in entities if e.get("subject_area")}),
            "summary": {"tables": len(entities), "columns": sum(len(e["attributes"]) for e in entities),
                        "relationships": len(rels)}}
