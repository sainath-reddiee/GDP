"""Runs and the Table Model Store: every saved run design (CORE.MODEL_DESIGN) registers its tables as table models
(services/modeling/table_model.py), so the model a run designed can be reopened, edited and published from the
Modeling Store after the run has moved on.

One table model per designed entity and source system. A design that changes nothing for a table adds no version.
Otherwise the table gets a new version (origin RUN):
  - while nobody edited the table in the store since the run last wrote it, the run's design is taken, keeping the
    descriptions and classifications modelers added for columns the design leaves blank (carry_over);
  - once someone did, the run's design is merged into their version field by field (merge_run_spec): what they
    changed stays, what they did not touch follows the run, columns they added or removed stay added or removed.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

from services.common.sql import rows, variant
from services.modeling import spec_v2, table_model
from services.modeling.run_link import run_entities

KEEP = ("description", "business_definition", "semantic_type")


def _source(session, run: Dict[str, Any]) -> Dict[str, Any]:
    found = rows(session, "SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME FROM CORE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = ?",
                 [run.get("SOURCE_SYSTEM_ID") or ""]) if run.get("SOURCE_SYSTEM_ID") else []
    name = (found[0]["SOURCE_SYSTEM_NAME"] if found else None) or run.get("SOURCE_DATABASE") or "RUN"
    return {"source_id": run.get("SOURCE_SYSTEM_ID"), "source_name": str(name).upper()}


def source_tables(entity: Dict[str, Any], run: Dict[str, Any]) -> List[str]:
    """The source tables an entity's columns come from (TABLE.COLUMN prefixes), qualified with the run's source
    database and schema when the run has them."""
    tables = sorted({str(s).rpartition(".")[0].upper() for a in entity.get("attributes") or []
                     for s in a.get("source_columns") or [] if "." in str(s)})
    prefix = ".".join(str(p).upper() for p in (run.get("SOURCE_DATABASE"), run.get("SOURCE_SCHEMA")) if p)
    return [f"{prefix}.{t}" if prefix and t.count(".") == 0 else t for t in tables]


def carry_over(new: Dict[str, Any], old: Dict[str, Any]) -> Dict[str, Any]:
    """`new` with the store-side descriptions and classifications of `old` where `new` has none (pure)."""
    out = copy.deepcopy(new)
    if not old or not old["design"]["entities"] or not out["design"]["entities"]:
        return out
    before = old["design"]["entities"][0]
    entity = out["design"]["entities"][0]
    for k in ("description", "grain"):
        if not entity.get(k) and before.get(k):
            entity[k] = before[k]
    old_cols = {a["name"]: a for a in before["attributes"]}
    for a in entity["attributes"]:
        prev = old_cols.get(a["name"])
        if prev:
            a["id"] = prev["id"]
            for k in KEEP:
                if not a.get(k) and prev.get(k):
                    a[k] = prev[k]
    return out


ENTITY_FIELDS = ("description", "purpose", "grain", "business_keys", "table_type", "kind", "scd_type")
ATTRIBUTE_FIELDS = ("datatype", "nullable", "is_pk", "description", "business_definition", "semantic_type", "is_unique",
                    "business_key", "transformation_hint", "derived", "source_columns", "default", "accepted_values",
                    "pii", "check", "tags", "status")


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _three_way(base: Optional[Dict[str, Any]], ours: Dict[str, Any], theirs: Dict[str, Any], fields) -> Dict[str, Any]:
    """`ours` with each field the store did not change (still equal to `base`) taken from `theirs`. Without a base the
    store owns every field it has filled in; empty ones are filled from `theirs`."""
    out = copy.deepcopy(ours)
    for f in fields:
        if f not in theirs:
            continue
        unchanged = _same(ours.get(f), base.get(f)) if base is not None else ours.get(f) in (None, "", [], {})
        if unchanged:
            out[f] = copy.deepcopy(theirs[f])
    return out


def merge_run_spec(base: Optional[Dict[str, Any]], ours: Dict[str, Any], theirs: Dict[str, Any]) -> Dict[str, Any]:
    """A run's new design (`theirs`) merged into the table model as the store left it (`ours`), against the version
    the run wrote last (`base`, None when the model was created in the store). Pure; single-entity specifications."""
    out = copy.deepcopy(ours)
    if not out["design"]["entities"] or not theirs["design"]["entities"]:
        return out
    b_ent = (base or {}).get("design", {}).get("entities", [None])[0] if base and base["design"]["entities"] else None
    o_ent, t_ent = out["design"]["entities"][0], theirs["design"]["entities"][0]
    merged = _three_way(b_ent, o_ent, t_ent, ENTITY_FIELDS)
    b_cols = {a["name"]: a for a in (b_ent or {}).get("attributes", [])}
    t_cols = {a["name"]: a for a in t_ent["attributes"]}
    attributes = []
    for a in o_ent["attributes"]:
        name = a["name"]
        if name in t_cols:
            attributes.append(_three_way(b_cols.get(name), a, t_cols[name], ATTRIBUTE_FIELDS))
        elif name in b_cols and _same({f: a.get(f) for f in ATTRIBUTE_FIELDS}, {f: b_cols[name].get(f) for f in ATTRIBUTE_FIELDS}):
            continue  # the run dropped a column nobody edited in the store
        else:
            attributes.append(a)  # added in the store, or edited there: kept
    kept = {a["name"] for a in o_ent["attributes"]}
    for name, a in t_cols.items():
        if name not in kept and name not in b_cols:  # new in the run (a column in the base was removed in the store)
            attributes.append(copy.deepcopy(a))
    merged["attributes"] = attributes
    out["design"]["entities"] = [merged]
    if base is not None and _same(out.get("physical_defaults"), base.get("physical_defaults")):
        out["physical_defaults"] = copy.deepcopy(theirs.get("physical_defaults") or out.get("physical_defaults"))
    return out


def register_from_run(session, run_id: str, design_version: int, user: str) -> List[Dict[str, Any]]:
    """Register every entity of a run's design version as a table model (created, new version, or unchanged)."""
    run_rows = rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    assert run_rows, "run not found"
    run = run_rows[0]
    found = rows(session, "SELECT DESIGN_JSON FROM CORE.MODEL_DESIGN WHERE RUN_ID = ? AND VERSION = ?",
                 [run_id, int(design_version)])
    assert found, f"design version {design_version} not found"
    design = variant(found[0]["DESIGN_JSON"]) or {}
    raw = run_entities(design)
    source = _source(session, run)
    out = []
    for e in raw["design"]["entities"]:
        name = str(e.get("entity_name") or "").upper()
        if not name:
            continue
        model_id = table_model.table_model_id(source["source_name"], name)
        tables = source_tables(e, run)
        entity_raw = {"target_database": raw["target_database"], "target_schema": raw["target_schema"],
                      "design": {"entities": [e]}}
        existing = rows(session, f"SELECT TABLE_MODEL_ID FROM {table_model.MODELS} WHERE TABLE_MODEL_ID = ?", [model_id])
        if not existing:
            table_model.create(session, {"table_name": name, **source, "source_tables": tables,
                                                "domain_id": run.get("DOMAIN_ID"), "origin": "RUN",
                                                "origin_run_id": run_id, "run_design_version": int(design_version),
                                                "spec": entity_raw,
                                                "change_note": f"Designed in run {run.get('RUN_NAME') or run_id} "
                                                               f"(design v{design_version})"}, user)
            out.append({"table_model_id": model_id, "table_name": name, "status": "CREATED", "version": 1})
            continue
        header = table_model._header(session, model_id)
        current = int(header.get("current_version") or 0)
        previous = table_model._spec(session, model_id, current, header) if current else None
        spec = table_model.single(entity_raw, model_id, domain=header.get("domain_name") or header["source_name"],
                                  table_type=header.get("table_type") or "STANDARD")
        versions = table_model._versions(session, model_id)
        latest_origin = versions[0]["origin"] if versions else None
        if previous is not None and latest_origin not in (None, "RUN"):
            last_run = next((v["version"] for v in versions if v["origin"] == "RUN"), None)
            base = table_model._spec(session, model_id, last_run, header) if last_run else None
            spec = merge_run_spec(base, previous, spec)  # edited in the store since: their edits win
        else:
            spec = carry_over(spec, previous)
        if previous is not None and spec_v2.checksum(spec) == spec_v2.checksum(previous):
            out.append({"table_model_id": model_id, "table_name": name, "status": "UNCHANGED", "version": current})
            continue
        made = table_model._next_version(session, header, spec, previous, "RUN", user,
                                         f"From run {run.get('RUN_NAME') or run_id} (design v{design_version})",
                                         run_id=run_id, design_version=int(design_version), source_tables=tables)
        out.append({"table_model_id": model_id, "table_name": name, "status": "VERSIONED", "version": made["version"]})
    return out
