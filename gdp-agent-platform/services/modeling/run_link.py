"""Runs and the domain model: a run's model design is a proposal to the domain's canonical model, never a direct
write to the registry (Model Workbench, PR 3).

  propose        the run's design (CORE.MODEL_DESIGN, v1 shape) merged into the approved domain model; no change
                 means the run can use the approved model now (the API pins it), otherwise a CORE.MODEL_CHANGE
                 is recorded for the modelers
  merge_change   a proposal merged into the model's open draft (a draft is opened when there is none)
  on_approved    when a version with merged proposals is approved: those proposals are APPROVED (their runs are
                 pinned by the API) and proposals made against an older version become STALE
  pin_run        CORE.PIN_RUN_MODEL (owner's rights): the run's target becomes the approved entity, its design
                 version is marked APPROVED, and the run records which model version it uses
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from services.common.sql import clip, insert_rows, rows, variant
from services.modeling import domain_model, importers, model_diff, spec_v2

CHANGES = "CORE.MODEL_CHANGE"


def _run(session, run_id: str) -> Dict[str, Any]:
    found = rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    assert found, "run not found"
    return found[0]


def _model_for(session, domain_id: str) -> Optional[Dict[str, Any]]:
    found = rows(session, "SELECT MODEL_ID, APPROVED_VERSION, DOMAIN_NAME FROM CORE.DOMAIN_MODEL WHERE DOMAIN_ID = ? "
                         "AND COALESCE(LAYER, 'SILVER') = 'SILVER'",
                 [domain_id])
    return found[0] if found else None


def domain_model_summary(session, domain_id: str) -> Optional[Dict[str, Any]]:
    """The domain's approved silver model as a run can use it as it is: its version, its hub (the entity a run is
    pinned to) and every active entity with its role and column count, hub first. None without an approved model."""
    model = _model_for(session, domain_id) if domain_id else None
    if not model or not model.get("APPROVED_VERSION"):
        return None
    version = int(model["APPROVED_VERSION"])
    found = rows(session, "SELECT SPEC FROM CORE.MODEL_DESIGNS WHERE MODEL_ID = ? AND VERSION = ?", [model["MODEL_ID"], version])
    if not found:
        return None
    spec = spec_v2.normalize_v2(variant(found[0]["SPEC"]) or {}, model["MODEL_ID"])
    entities = []
    for e in spec["design"]["entities"]:
        if e.get("status") == "RETIRED":
            continue
        role = str(e.get("table_type") or "TABLE").upper()
        entities.append({"entity": e["entity_name"], "role": role,
                         "columns": sum(1 for a in e.get("attributes") or [] if a.get("status") != "RETIRED"),
                         "fqn": ".".join(p for p in (*spec_v2.placement(spec, e), e["entity_name"]) if p)})
    entities.sort(key=lambda t: ({"HUB": 0, "SPOKE": 1}.get(t["role"], 2), t["entity"]))
    usable = [t for t in entities if t["columns"]]
    hub = next((t for t in usable if t["role"] == "HUB"), usable[0] if usable else None)
    return {"model_id": model["MODEL_ID"], "version": version, "domain_name": model.get("DOMAIN_NAME"),
            "hub": hub["entity"] if hub else None, "entities": entities}


def withdraw_run_proposals(session, run_id: str, user: str) -> int:
    """The run's undecided proposals, withdrawn: the run uses the domain model as it is."""
    found = rows(session, f"SELECT CHANGE_ID FROM {CHANGES} WHERE RUN_ID = ? AND STATUS = 'PROPOSED'", [run_id])
    if found:
        session.sql(f"""UPDATE {CHANGES} SET STATUS = 'WITHDRAWN', DECIDED_BY = ?, DECIDED_AT = CURRENT_TIMESTAMP(),
                               NOTE = 'The run uses the domain model as it is' WHERE RUN_ID = ? AND STATUS = 'PROPOSED'""",
                    params=[user.upper(), run_id]).collect()
    return len(found)


def run_entities(design: Dict[str, Any]) -> Dict[str, Any]:
    """A run design (services/modeling/design.py shape) as a raw v2 specification of its entities."""
    db, schema = str(design.get("target_database") or "").upper(), str(design.get("target_schema") or "").upper()
    entities = []
    for e in design.get("entities") or []:
        entities.append({**e, "table_type": e.get("kind") or "TABLE", "description": e.get("purpose") or "",
                         "database": db or None, "schema": schema or None})
    return {"target_database": db, "target_schema": schema, "design": {"entities": entities}}


def propose(session, run_id: str, design_version: int, user: str) -> Dict[str, Any]:
    run = _run(session, run_id)
    assert run.get("DOMAIN_ID"), "identify the run's domain first; its model belongs to that domain"
    found = rows(session, "SELECT DESIGN_JSON, ISSUES FROM CORE.MODEL_DESIGN WHERE RUN_ID = ? AND VERSION = ?",
                 [run_id, int(design_version)])
    assert found, f"design version {design_version} not found"
    design, issues = variant(found[0]["DESIGN_JSON"]) or {}, variant(found[0]["ISSUES"]) or []
    errors = [i for i in issues if i.get("severity") == "ERROR"]
    assert not errors, f"fix {len(errors)} problem(s) before proposing: {errors[0]['message']}"
    assert design.get("entities"), "the design has no entities"
    primary = str(design.get("primary_entity") or design["entities"][0]["entity_name"]).upper()
    # a design that reuses or extends a registered model names it; the run maps to that model while its additions wait
    existing = design.get("decision_target") if design.get("decision") in ("REUSE_EXISTING", "EXTEND_EXISTING") else None
    return propose_entities(session, run["DOMAIN_ID"], run_entities(design), primary, user, run_id=run_id,
                            design_version=design_version, label=f"run {run.get('RUN_NAME') or run_id}",
                            existing_target=existing)


def existing_entity(spec: Dict[str, Any], target: Optional[str]) -> Optional[Dict[str, Any]]:
    """The approved model's active entity a design reuses or extends: its DB.SCHEMA.TABLE, or the table name alone
    when exactly one entity has it."""
    target = str(target or "").strip().upper()
    if not target:
        return None
    active = [e for e in (spec.get("design") or {}).get("entities") or [] if e.get("status") != "RETIRED"]
    for e in active:
        if ".".join(p for p in (*spec_v2.placement(spec, e), e["entity_name"]) if p).upper() == target:
            return e
    named = [e for e in active if str(e["entity_name"]).upper() == target.split(".")[-1]]
    return named[0] if len(named) == 1 else None


def propose_entities(session, domain_id: str, incoming: Dict[str, Any], primary: str, user: str, *,
                     run_id: Optional[str] = None, design_version: int = 0, table_model_id: Optional[str] = None,
                     table_model_version: Optional[int] = None, label: str = "",
                     existing_target: Optional[str] = None) -> Dict[str, Any]:
    """Raw v2 entities (`incoming`) proposed to the domain's model, from a run's design or from a table model of the
    Model Store. No change means the proposer can use the approved model now (pinned); otherwise a MODEL_CHANGE is
    recorded for the modelers (an earlier undecided proposal from the same run or table model is withdrawn). When the
    design reuses or extends an entity of the approved model (`existing_target`), `use_existing` names it so the run
    maps to it now; the additions stay a proposal, and the run moves to the new version once it is approved."""
    model = _model_for(session, domain_id)
    if not model:
        domain_model.baseline(session, domain_id, user)
        model = _model_for(session, domain_id)
    source = {"run_id": run_id, "design_version": design_version, "table_model_id": table_model_id,
              "table_model_version": table_model_version}
    if not model:  # the domain registers nothing yet: the proposal becomes the model's first draft
        domain = rows(session, "SELECT DOMAIN_NAME FROM CORE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [domain_id])
        spec = spec_v2.normalize_v2({**incoming, "domain": domain[0]["DOMAIN_NAME"] if domain else ""},
                                    domain_model.model_id_for(domain_id))
        created = domain_model.create_model(session, domain_id, json.dumps({"spec": spec, "origin": "RUN",
                                            "change_note": f"First draft, proposed by {label or 'a run'}"}), user)
        change_id = _record(session, created["model_id"], None, primary, spec, model_diff.diff(None, spec), "MERGED", 1,
                            **source)
        return {"model_id": created["model_id"], "change_id": change_id, "status": "MERGED", "merged_into": 1, "pinned": False}
    model_id, approved = model["MODEL_ID"], model.get("APPROVED_VERSION")
    base = domain_model._spec(session, model_id, int(approved)) if approved else None
    proposed = importers.merge(base, incoming, model_id) if base else spec_v2.normalize_v2(
        {**incoming, "domain": model["DOMAIN_NAME"]}, model_id)
    changes = model_diff.diff(base, proposed)
    if not changes["changes"] and approved:
        entity = next((e for e in proposed["design"]["entities"] if e["entity_name"] == primary), None)
        return {"model_id": model_id, "pinned": True, "version": int(approved), "entity": primary,
                "target_model": ".".join(p for p in (*spec_v2.placement(proposed, entity), primary) if p) if entity else None}
    if table_model_id:
        session.sql(f"""UPDATE {CHANGES} SET STATUS = 'WITHDRAWN', DECIDED_BY = ?, DECIDED_AT = CURRENT_TIMESTAMP()
                         WHERE TABLE_MODEL_ID = ? AND STATUS = 'PROPOSED'""", params=[user.upper(), table_model_id]).collect()
    else:
        session.sql(f"""UPDATE {CHANGES} SET STATUS = 'WITHDRAWN', DECIDED_BY = ?, DECIDED_AT = CURRENT_TIMESTAMP()
                         WHERE RUN_ID = ? AND STATUS = 'PROPOSED'""", params=[user.upper(), run_id]).collect()
    change_id = _record(session, model_id, approved, primary, proposed, changes, "PROPOSED", None, **source)
    out = {"model_id": model_id, "change_id": change_id, "status": "PROPOSED", "pinned": False,
           "counts": changes["counts"], "breaking": changes["breaking"]}
    current = existing_entity(base, existing_target) if base and existing_target else None
    if current:
        out["use_existing"] = {"version": int(approved), "entity": current["entity_name"],
                               "target_model": ".".join(p for p in (*spec_v2.placement(base, current), current["entity_name"]) if p)}
    return out


def _record(session, model_id: str, base: Optional[int], primary: str, spec: Dict[str, Any], changes: Dict[str, Any],
            status: str, merged_into: Optional[int], *, run_id: Optional[str] = None, design_version: int = 0,
            table_model_id: Optional[str] = None, table_model_version: Optional[int] = None) -> str:
    touched = set(changes.get("entities_touched") or []) | {primary}
    entities = [e for e in spec["design"]["entities"] if e["entity_name"] in touched]
    change_id = str(uuid.uuid4())
    columns = ["CHANGE_ID", "MODEL_ID", "BASE_VERSION", "RUN_ID", "RUN_DESIGN_VERSION", "TARGET_ENTITY",
               "PROPOSED_ENTITIES", "DIFF", "STATUS", "MERGED_INTO_VERSION"]
    exprs = ["?", "?", "NULLIF(?, '')::NUMBER", "NULLIF(?, '')", "NULLIF(?, '')::NUMBER", "?", "PARSE_JSON(?)",
             "PARSE_JSON(?)", "?", "NULLIF(?, '')::NUMBER"]
    values = [change_id, model_id, base if base else "", run_id or "", int(design_version) if design_version else "",
              primary, entities, {"counts": changes["counts"], "breaking": changes["breaking"],
                                  "changes": changes["changes"][:200]},
              status, merged_into if merged_into else ""]
    if table_model_id:  # V044
        columns += ["SOURCE", "TABLE_MODEL_ID", "TABLE_MODEL_VERSION"]
        exprs += ["'TABLE_MODEL'", "?", "NULLIF(?, '')::NUMBER"]
        values += [table_model_id, int(table_model_version) if table_model_version else ""]
    insert_rows(session, CHANGES, columns, exprs, [values])
    return change_id


def list_changes(session, model_id: str) -> List[Dict[str, Any]]:
    """Proposals to the model: from runs, and from approved Profiling Store annotations (SOURCE 'ANNOTATION', V041)."""
    sql = f"""SELECT C.CHANGE_ID, C.BASE_VERSION, C.RUN_ID, C.RUN_DESIGN_VERSION, C.TARGET_ENTITY, C.DIFF,
                     C.STATUS, C.MERGED_INTO_VERSION, C.NOTE, C.CREATED_BY, C.CREATED_AT, C.DECIDED_BY,
                     R.RUN_NAME, R.CURRENT_STATE{{extra}}
                FROM {CHANGES} C LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = C.RUN_ID
               WHERE C.MODEL_ID = ? ORDER BY C.CREATED_AT DESC LIMIT 200"""
    found = None
    for extra in (", C.SOURCE, C.ANNOTATION_ID, C.PATCH, C.TABLE_MODEL_ID, C.TABLE_MODEL_VERSION",
                  ", C.SOURCE, C.ANNOTATION_ID, C.PATCH", ""):  # V044, V041, before
        try:
            found = rows(session, sql.format(extra=extra), [model_id])
            break
        except Exception:
            if not extra:
                raise
    out = []
    for r in found:
        c = {str(k).lower(): v for k, v in r.items()}
        c["diff"] = variant(c.get("diff")) or {}
        c["patch"] = variant(c.get("patch"))
        c["source"] = c.get("source") or ("TABLE_MODEL" if c.get("table_model_id") else "RUN")
        c["created_at"] = str(c["created_at"])[:19] if c.get("created_at") else None
        out.append(c)
    return out


def merge_change(session, model_id: str, change_id: str, user: str) -> Dict[str, Any]:
    """Merge a proposal into the model's open draft (opening one when there is none)."""
    try:
        found = rows(session, f"SELECT STATUS, PROPOSED_ENTITIES, PATCH FROM {CHANGES} WHERE CHANGE_ID = ? AND MODEL_ID = ?",
                     [change_id, model_id])
    except Exception:  # before V041
        found = rows(session, f"SELECT STATUS, PROPOSED_ENTITIES FROM {CHANGES} WHERE CHANGE_ID = ? AND MODEL_ID = ?",
                     [change_id, model_id])
    assert found, "proposal not found"
    assert found[0]["STATUS"] in ("PROPOSED", "STALE"), f"the proposal is {str(found[0]['STATUS']).lower()}"
    detail = domain_model.get_model(session, model_id)
    current = detail.get("open")
    if current and current["status"] == "IN_REVIEW":
        raise AssertionError(f"version {current['version']} is in review; withdraw it or decide it before merging")
    if not current:
        domain_model.open_draft(session, model_id, user, note="Merged proposals")
        detail = domain_model.get_model(session, model_id)
        current = detail["open"]
    draft = domain_model._spec(session, model_id, current["version"])
    patch = variant(found[0].get("PATCH"))
    if patch:  # an annotation sets a few fields of one column; the rest of the draft is left as it is
        from services.catalog.profile_ops import apply_patch

        merged = apply_patch(draft, patch["entity"], patch["column"], patch.get("set") or {})
    else:
        entities = variant(found[0]["PROPOSED_ENTITIES"]) or []
        merged = importers.merge(draft, {"design": {"entities": entities}}, model_id)
    saved = domain_model.save_draft(session, model_id, json.dumps({"etag": current["etag"], "spec": merged}, default=str), user)
    session.sql(f"""UPDATE {CHANGES} SET STATUS = 'MERGED', MERGED_INTO_VERSION = ?, DECIDED_BY = ?,
                           DECIDED_AT = CURRENT_TIMESTAMP() WHERE CHANGE_ID = ?""",
                params=[int(current["version"]), user.upper(), change_id]).collect()
    return {"change_id": change_id, "status": "MERGED", "version": current["version"], "etag": saved["etag"],
            "counts": saved["counts"]}


def reject_change(session, model_id: str, change_id: str, user: str, note: str) -> Dict[str, Any]:
    found = rows(session, f"SELECT STATUS, TARGET_ENTITY FROM {CHANGES} WHERE CHANGE_ID = ? AND MODEL_ID = ?",
                 [change_id, model_id])
    assert found, "proposal not found"
    assert found[0]["STATUS"] in ("PROPOSED", "STALE"), f"the proposal is {str(found[0]['STATUS']).lower()}"
    assert len((note or "").strip()) >= 3, "say why the proposal is rejected"
    session.sql(f"""UPDATE {CHANGES} SET STATUS = 'REJECTED', NOTE = ?, DECIDED_BY = ?, DECIDED_AT = CURRENT_TIMESTAMP()
                     WHERE CHANGE_ID = ?""", params=[clip(note, 2000), user.upper(), change_id]).collect()
    from services.knowledge.signals import change_rejection, keep

    model = rows(session, "SELECT DOMAIN_ID, DOMAIN_NAME FROM CORE.DOMAIN_MODEL WHERE MODEL_ID = ?", [model_id])
    if model:
        try:
            source = rows(session, f"SELECT SOURCE FROM {CHANGES} WHERE CHANGE_ID = ?", [change_id])[0].get("SOURCE")
        except Exception:  # before V041
            source = None
        keep(session, model[0]["DOMAIN_ID"], change_rejection(model[0]["DOMAIN_NAME"], found[0].get("TARGET_ENTITY"),
                                                              source or "RUN", note), "MODELING")
    return {"change_id": change_id, "status": "REJECTED"}


def on_approved(session, model_id: str, version: int) -> List[Dict[str, Any]]:
    """Proposals merged into an approved version are APPROVED (returned, so their runs get pinned); proposals made
    against an older version go STALE (the run can propose again on top of the new model)."""
    merged = rows(session, f"""SELECT CHANGE_ID, RUN_ID, RUN_DESIGN_VERSION, TARGET_ENTITY FROM {CHANGES}
                                WHERE MODEL_ID = ? AND STATUS = 'MERGED' AND MERGED_INTO_VERSION = ?""",
                  [model_id, int(version)])
    if merged:
        session.sql(f"UPDATE {CHANGES} SET STATUS = 'APPROVED' WHERE MODEL_ID = ? AND STATUS = 'MERGED' AND MERGED_INTO_VERSION = ?",
                    params=[model_id, int(version)]).collect()
    session.sql(f"""UPDATE {CHANGES} SET STATUS = 'STALE' WHERE MODEL_ID = ? AND STATUS = 'PROPOSED'
                     AND COALESCE(BASE_VERSION, 0) < ?""", params=[model_id, int(version)]).collect()
    return [{"change_id": m["CHANGE_ID"], "run_id": m["RUN_ID"], "design_version": int(m["RUN_DESIGN_VERSION"] or 0),
             "entity": m["TARGET_ENTITY"]} for m in merged if m.get("RUN_ID")]


def pending_for_run(session, run_id: str) -> Optional[Dict[str, Any]]:
    """The run's newest proposal that is not decided yet (PROPOSED or MERGED into an unapproved draft).
    `maps_existing`: the run already maps to the approved version the proposal builds on, so mapping need not wait."""
    found = rows(session, f"""SELECT CHANGE_ID, MODEL_ID, STATUS, MERGED_INTO_VERSION, BASE_VERSION, CREATED_AT FROM {CHANGES}
                               WHERE RUN_ID = ? AND STATUS IN ('PROPOSED', 'MERGED', 'STALE')
                               ORDER BY CREATED_AT DESC LIMIT 1""", [run_id])
    if not found:
        return None
    r = {str(k).lower(): v for k, v in found[0].items()}
    r["created_at"] = str(r["created_at"])[:19] if r.get("created_at") else None
    run = rows(session, "SELECT MODEL_ID, MODEL_VERSION, TARGET_MODEL FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    pinned = run[0] if run else {}
    r["maps_existing"] = bool(pinned.get("TARGET_MODEL") and pinned.get("MODEL_ID") == r.get("model_id")
                              and r.get("base_version") is not None and pinned.get("MODEL_VERSION") is not None
                              and int(pinned["MODEL_VERSION"]) == int(r["base_version"]))
    return r


def pin_run(session, run_id: str, model_id: str, version: int, entity: str, design_version: int = 0) -> Dict[str, Any]:
    """CORE.PIN_RUN_MODEL (owner's rights): point the run at the approved model's entity."""
    found = rows(session, """SELECT D.SPEC, D.STATUS FROM CORE.MODEL_DESIGNS D WHERE D.MODEL_ID = ? AND D.VERSION = ?""",
                 [model_id, int(version)])
    assert found and found[0]["STATUS"] in ("APPROVED", "PUBLISHED"), "the run can only use an approved model version"
    spec = spec_v2.normalize_v2(variant(found[0]["SPEC"]) or {}, model_id)
    ent = next((e for e in spec["design"]["entities"] if e["entity_name"] == str(entity).upper()), None)
    assert ent and ent.get("status") != "RETIRED", f"{entity} is not an active entity of model version {version}"
    fqn = ".".join(p for p in (*spec_v2.placement(spec, ent), ent["entity_name"]) if p)
    session.sql("""UPDATE CORE.WORKFLOW_RUN SET MODEL_ID = ?, MODEL_VERSION = ?, MODEL_ENTITY = ?, TARGET_MODEL = ?,
                          UPDATED_AT = CURRENT_TIMESTAMP() WHERE RUN_ID = ?""",
                params=[model_id, int(version), ent["entity_name"], fqn, run_id]).collect()
    if design_version:
        session.sql("UPDATE CORE.MODEL_DESIGN SET STATUS = 'SUPERSEDED' WHERE RUN_ID = ? AND STATUS = 'APPROVED'",
                    params=[run_id]).collect()
        session.sql("""UPDATE CORE.MODEL_DESIGN SET STATUS = 'APPROVED', APPROVED_BY = CURRENT_USER(),
                              APPROVED_AT = CURRENT_TIMESTAMP() WHERE RUN_ID = ? AND VERSION = ?""",
                    params=[run_id, int(design_version)]).collect()
    out = {"run_id": run_id, "model_id": model_id, "model_version": int(version), "entity": ent["entity_name"],
           "target_model": fqn}
    targets = record_targets(session, run_id, model_id, int(version), spec, ent["entity_name"])
    if targets is not None:
        out["targets"] = targets
    return out


ROLE_ORDER = {"HUB": 0, "SPOKE": 1}


def target_set(session, spec: Dict[str, Any], pinned: str) -> List[Dict[str, Any]]:
    """Every active entity of an approved model version as the run's targets: hub first, then the pinned entity, then
    by name. An entity without columns, or without an active registry row, is listed but not included."""
    out = []
    for e in spec["design"]["entities"]:
        if e.get("status") == "RETIRED":
            continue
        db, schema = spec_v2.placement(spec, e)
        found = rows(session, """SELECT TARGET_TABLE_ID, ACTIVE_FLAG FROM CORE.TARGET_TABLE_REGISTRY
                                  WHERE UPPER(TARGET_DATABASE) = UPPER(?) AND UPPER(TARGET_SCHEMA) = UPPER(?)
                                    AND UPPER(TARGET_TABLE) = UPPER(?) ORDER BY IFF(ACTIVE_FLAG, 0, 1), CREATED_AT LIMIT 1""",
                     [db or "", schema or "", e["entity_name"]])
        reg = found[0] if found else {}
        live = [a for a in e.get("attributes") or [] if a.get("status") != "RETIRED"]
        reason = ("no columns in the approved model" if not live
                  else "not registered as a target table" if not reg
                  else "the target table is not active in the registry" if not reg.get("ACTIVE_FLAG") else None)
        role = str(e.get("table_type") or "TABLE").upper()
        out.append({"entity": e["entity_name"], "target_fqn": ".".join(p for p in (db, schema, e["entity_name"]) if p),
                    "target_table_id": reg.get("TARGET_TABLE_ID"), "role": role if role in ROLE_ORDER else "TABLE",
                    "included": reason is None, "excluded_reason": reason})
    out.sort(key=lambda t: (ROLE_ORDER.get(t["role"], 2), t["entity"] != pinned, t["entity"]))
    for i, t in enumerate(out, 1):
        t["ordinal"] = i
    return out


def record_targets(session, run_id: str, model_id: str, version: int, spec: Dict[str, Any],
                   pinned: str) -> Optional[List[Dict[str, Any]]]:
    """CORE.RUN_TARGET (V074): the target set of a silver run with no parent run. A gold model, and a child run that
    loads one entity of its parent's set, keep the single pinned entity (None). The previous set stays, not current."""
    if str(spec.get("layer") or "SILVER").upper() != "SILVER":
        return None
    try:
        run = rows(session, "SELECT PARENT_RUN_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
        if run and run[0].get("PARENT_RUN_ID"):
            return None
        targets = target_set(session, spec, pinned)
        session.sql("UPDATE CORE.RUN_TARGET SET IS_CURRENT = FALSE WHERE RUN_ID = ? AND IS_CURRENT",
                    params=[run_id]).collect()
    except Exception as exc:
        if "RUN_TARGET" in str(exc).upper() and "DOES NOT EXIST" in str(exc).upper():
            return None  # before V074: the run maps to the pinned entity alone
        raise
    insert_rows(session, "CORE.RUN_TARGET",
                ["RUN_TARGET_ID", "RUN_ID", "MODEL_ID", "MODEL_VERSION", "ENTITY_NAME", "TARGET_FQN", "TARGET_TABLE_ID",
                 "ROLE", "ORDINAL", "INCLUDED", "EXCLUDED_REASON", "IS_CURRENT"],
                ["?", "?", "?", "?", "?", "?", "NULLIF(?, '')", "?", "?", "?", "NULLIF(?, '')", "TRUE"],
                [[str(uuid.uuid4()), run_id, model_id, int(version), t["entity"], t["target_fqn"],
                  t["target_table_id"] or "", t["role"], t["ordinal"], t["included"], t["excluded_reason"] or ""]
                 for t in targets])
    return targets
