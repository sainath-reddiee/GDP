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
    return propose_entities(session, run["DOMAIN_ID"], run_entities(design), primary, user, run_id=run_id,
                            design_version=design_version, label=f"run {run.get('RUN_NAME') or run_id}")


def propose_entities(session, domain_id: str, incoming: Dict[str, Any], primary: str, user: str, *,
                     run_id: Optional[str] = None, design_version: int = 0, table_model_id: Optional[str] = None,
                     table_model_version: Optional[int] = None, label: str = "") -> Dict[str, Any]:
    """Raw v2 entities (`incoming`) proposed to the domain's model, from a run's design or from a table model of the
    Model Store. No change means the proposer can use the approved model now (pinned); otherwise a MODEL_CHANGE is
    recorded for the modelers (an earlier undecided proposal from the same run or table model is withdrawn)."""
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
    return {"model_id": model_id, "change_id": change_id, "status": "PROPOSED", "pinned": False,
            "counts": changes["counts"], "breaking": changes["breaking"]}


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
    """The run's newest proposal that is not decided yet (PROPOSED or MERGED into an unapproved draft)."""
    found = rows(session, f"""SELECT CHANGE_ID, MODEL_ID, STATUS, MERGED_INTO_VERSION, CREATED_AT FROM {CHANGES}
                               WHERE RUN_ID = ? AND STATUS IN ('PROPOSED', 'MERGED', 'STALE')
                               ORDER BY CREATED_AT DESC LIMIT 1""", [run_id])
    if not found:
        return None
    r = {str(k).lower(): v for k, v in found[0].items()}
    r["created_at"] = str(r["created_at"])[:19] if r.get("created_at") else None
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
    return {"run_id": run_id, "model_id": model_id, "model_version": int(version), "entity": ent["entity_name"],
            "target_model": fqn}
