"""Gold Pipeline: Gold runs on the run engine (V054), Silver (read in place) to Gold.

A Gold run is a run with PIPELINE = 'GOLD'. It goes through the same stages, gates and approvals as a Silver run:
Silver Selection (register and validate the silver tables, read in place, no copy), Profiling, Modeling (the gold
model and the field mapping, then the transformations: the STTM), QA tests, Data Quality, Gold Code Generation,
Validation and Code review. What differs is here and in services/gold:

GET  /api/gold/inputs?domain_id=        silver tables a domain's Gold run can start from: a consumer domain's silver
                                        schemas (its registered hubs suggested), with row counts, read from the catalog
POST /api/gold/runs                     start a Gold run on chosen silver tables (RUN.CREATE): created, registered in
                                        place, access validated and profiled, like a catalog run
GET  /api/runs/{id}/gold                the run's gold modeling: the domain's MDM role, its GOLD model (approved and
                                        open versions, the approved tables to map into) and the table the run uses
POST /api/runs/{id}/gold/propose        propose the gold model from the run's profile (facts, dimensions, bridges;
                                        services/gold/propose.py) into a draft of the domain's GOLD model, reviewed and
                                        approved in the Model Workbench (MODEL.EDIT)
POST /api/runs/{id}/gold/target         use one table of the approved gold model as the run's target (MODEL.EDIT)
POST /api/runs/{id}/gold/star-schema    build the domain's star schema: one table run per fact and bridge of the
                                        approved gold model (child runs, PARENT_RUN_ID), each on the silver table that
                                        feeds it, registered in place, profiled (cache) and pinned to its table
                                        (RUN.CREATE)
Nothing here writes to silver, MDM or gold data.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import (MODEL_APPLY_STATES, AccessRequest, CreateRun, _quote_ident, _snowflake_error, create_run,
                      current_db, pin_run_model, prepare_source, run_profiling)
from app.source_invoke import invoke_source
from services.gold import propose as gold

router = APIRouter()
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _js(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _env(database: Optional[str]) -> str:
    m = re.match(r"^([A-Z]+)_", str(database or "").upper())
    return m.group(1) if m else "DEV"


def _contracts(db: Db) -> Dict[str, Dict[str, Any]]:
    rows = db.query("""SELECT DOMAIN_ID, DOMAIN_NAME, CONFIG:mdm AS MDM, CONFIG:silver_database::STRING AS SILVER_DATABASE,
                              CONFIG:silver_schema::STRING AS SILVER_SCHEMA
                         FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG""")
    return {r["domain_name"]: {**r, "mdm": _js(r.get("mdm")) or {}} for r in rows}


def _domain(db: Db, domain_id: str) -> Dict[str, Any]:
    found = next((c for c in _contracts(db).values() if c["domain_id"] == domain_id), None)
    if not found:
        raise HTTPException(404, "domain not found")
    return found


def _mastered(reg: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {n: r["mdm"] for n, r in reg.items() if str(r["mdm"].get("role") or "").upper() == "MASTERED"}


def _require_consumer(d: Dict[str, Any]) -> None:
    role = str(d["mdm"].get("role") or "").upper()
    if role == "MASTERED":
        raise HTTPException(400, f"{d['domain_name']} is mastered by Reltio; its gold (the conformed dimension) exists in "
                                 "gdp-dbt and is referenced by consumers, not regenerated")
    if role != "CONSUMER":
        raise HTTPException(400, f"{d['domain_name']} is not linked to MDM: on the domain's Lineage tab, mark which mastered "
                                 "domains (Company, Property) it consumes, then start the Gold run")


# ---------------------------------------------------------------- starting a Gold run

@router.get("/api/gold/inputs")
def gold_inputs(domain_id: str, db: Db = Depends(current_db)):
    d = _domain(db, domain_id)
    role = str(d["mdm"].get("role") or "").upper() or None
    hubs = db.query("""SELECT TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                        WHERE DOMAIN_ID = %s AND ACTIVE_FLAG AND COALESCE(LAYER, 'SILVER') = 'SILVER'""", (domain_id,))
    schemas: Dict[tuple, set] = {}
    for h in hubs:
        schemas.setdefault((h["target_database"].upper(), h["target_schema"].upper()), set()).add(h["target_table"].upper())
    if d.get("silver_database") and d.get("silver_schema"):
        schemas.setdefault((d["silver_database"].upper(), d["silver_schema"].upper()), set())
    out = []
    for (database, schema), suggested in sorted(schemas.items()):
        item: Dict[str, Any] = {"database": database, "schema": schema, "tables": []}
        try:
            for t in db.query(f"""SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, LAST_ALTERED::VARCHAR AS LAST_ALTERED
                                    FROM {_quote_ident(database)}.INFORMATION_SCHEMA.TABLES
                                   WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME LIMIT 500""", (schema,)):
                item["tables"].append({"table": t["table_name"], "type": t["table_type"], "row_count": t.get("row_count"),
                                       "last_altered": t.get("last_altered"), "suggested": t["table_name"].upper() in suggested})
            item["missing"] = sorted(suggested - {t["table"].upper() for t in item["tables"]})
        except Exception as exc:
            item["error"] = str(exc).splitlines()[0][:200]
        out.append(item)
    return {"domain_id": domain_id, "domain_name": d["domain_name"], "role": role, "consumes": d["mdm"].get("consumes") or [],
            "schemas": out}


class GoldRunIn(BaseModel):
    domain_id: str = Field(min_length=1, max_length=36)
    database: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(alias="schema", min_length=1, max_length=255)
    tables: List[str] = Field(min_length=1, max_length=50)
    run_name: Optional[str] = Field(default=None, max_length=256)


@router.post("/api/gold/runs")
def start_gold_run(body: GoldRunIn, db: Db = Depends(current_db)):
    """Create a Gold run on silver tables read in place, validate access and profile them (the run then waits on
    Modeling: the gold model and its target table)."""
    d = _domain(db, body.domain_id)
    _require_consumer(d)
    names = [body.database, body.schema_name, *body.tables]
    if not all(IDENT.match(x) for x in names):
        raise HTTPException(400, "database, schema and table names must be plain identifiers")
    database, schema = body.database.upper(), body.schema_name.upper()
    tables = sorted({t.upper() for t in body.tables})
    run_name = body.run_name or f"{d['domain_name']} gold {time.strftime('%Y-%m-%d %H:%M')}"
    intent = {
        "path": "profile_suggest", "pipeline": "GOLD", "run_name": run_name, "model_existing": False, "targets": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "domain_id": body.domain_id,
        "source": {"origin": "snowflake", "database": database, "schema": schema,
                   "source_system_name": f"SILVER_{database}_{schema}"[:120], "source_type": "SNOWFLAKE_DATABASE",
                   "tables": tables},
        "target": {"storage_type": "IN_PLACE"},
    }
    created = create_run(CreateRun(run_name=run_name, domain_id=body.domain_id, intent=intent, pipeline="GOLD"), db)
    run_id = created.get("run_id")
    if created.get("registration_error"):
        return {"run_id": run_id, "stage": "SOURCE", "error": created["registration_error"]}
    prepared = prepare_source(run_id, AccessRequest(selected=tables), db)
    if not prepared.get("passed"):
        return {"run_id": run_id, "stage": "SOURCE", "error": "access check failed; open the run to see which table"}
    try:
        profiled = run_profiling(run_id, None, db)
    except HTTPException as exc:
        return {"run_id": run_id, "stage": "PROFILING", "error": exc.detail}
    return {"run_id": run_id, "stage": "MODELING", "state": (profiled or {}).get("state")}


# ---------------------------------------------------------------- the Gold run's modeling

def _run(db: Db, run_id: str) -> Dict[str, Any]:
    found = db.query("""SELECT RUN_ID, RUN_NAME, DOMAIN_ID, COALESCE(PIPELINE, 'SILVER') AS PIPELINE, CURRENT_STATE, TARGET_MODEL,
                               MODEL_ID, MODEL_VERSION, MODEL_ENTITY, PARENT_RUN_ID, SOURCE_DATABASE, SOURCE_SCHEMA
                          FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s""", (run_id,))
    if not found:
        raise HTTPException(404, "run not found")
    if str(found[0]["pipeline"]).upper() != "GOLD":
        raise HTTPException(400, "this is a Silver run; gold modeling is for Gold runs")
    return found[0]


def _gold_model(db: Db, domain_id: str) -> Optional[Dict[str, Any]]:
    from services.modeling.domain_model import model_id_for

    found = db.query("SELECT MODEL_ID, APPROVED_VERSION, OPEN_VERSION FROM GOVERNANCE.DOMAIN_MODEL WHERE MODEL_ID = %s",
                     (model_id_for(domain_id, "GOLD"),))
    return found[0] if found else None


def _approved_tables(db: Db, model: Dict[str, Any]) -> List[Dict[str, Any]]:
    from services.modeling import spec_v2

    if not model or not model.get("approved_version"):
        return []
    found = db.query("SELECT SPEC FROM GOVERNANCE.MODEL_DESIGNS WHERE MODEL_ID = %s AND VERSION = %s",
                     (model["model_id"], int(model["approved_version"])))
    if not found:
        return []
    spec = spec_v2.active(spec_v2.normalize_v2(_js(found[0]["spec"]) or {}, model["model_id"]))
    phys = spec.get("physical_defaults") or {}
    return [{"entity": e["entity_name"], "table_type": e["table_type"], "grain": e.get("grain"),
             "description": e.get("description"), "columns": len(e.get("attributes") or []),
             "fqn": f"{e.get('database') or phys.get('database')}.{e.get('schema') or phys.get('schema')}.{e['entity_name']}"}
            for e in spec["design"]["entities"] if not e.get("conformed_from")]


def _profiled_inputs(db: Db, run_id: str) -> List[Dict[str, Any]]:
    """The run's silver tables with their profiled columns (distinct counts decide which descriptors become
    dimensions)."""
    rows = db.query("""SELECT L.LANDING_DATABASE, L.LANDING_SCHEMA, L.LANDING_TABLE, P.COLUMN_NAME, P.DATA_TYPE, P.DISTINCT_COUNT,
                              P.NULL_COUNT, P.ROW_COUNT
                         FROM PROFILE.PROFILE_REGISTRY P
                         JOIN SOURCE.LANDING_TABLE_REGISTRY L ON L.LANDING_ID = P.SOURCE_TABLE_ID
                        WHERE P.RUN_ID = %s AND P.IS_CURRENT
                        ORDER BY L.LANDING_TABLE, P.COLUMN_NAME""", (run_id,))
    tables: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        key = (r["landing_database"], r["landing_schema"], r["landing_table"])
        t = tables.setdefault(key, {"database": key[0], "schema": key[1], "table": key[2], "layer": "SILVER_PRE_MDM",
                                    "row_count": r.get("row_count"), "columns": []})
        t["columns"].append({"name": r["column_name"], "data_type": str(r.get("data_type") or "VARCHAR").upper(),
                             "nullable": bool(r.get("null_count")), "distinct_count": r.get("distinct_count")})
    return list(tables.values())


def _children(db: Db, run_id: str) -> List[Dict[str, Any]]:
    """The Gold run's table runs, with the state each is in."""
    rows = db.query("""SELECT R.RUN_ID, R.RUN_NAME, R.MODEL_ENTITY, R.TARGET_MODEL, R.CURRENT_STATE, R.CURRENT_STAGE, R.STATUS,
                              T.TABLE_TYPE
                         FROM CORE.WORKFLOW_RUN R
                         LEFT JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.ACTIVE_FLAG AND COALESCE(T.LAYER, 'SILVER') = 'GOLD'
                              AND UPPER(T.TARGET_DATABASE || '.' || T.TARGET_SCHEMA || '.' || T.TARGET_TABLE) = UPPER(R.TARGET_MODEL)
                        WHERE R.PARENT_RUN_ID = %s AND R.DELETED_AT IS NULL ORDER BY R.CREATED_AT""", (run_id,))
    return [{"run_id": r["run_id"], "run_name": r["run_name"], "entity": r.get("model_entity"), "fqn": r.get("target_model"),
             "table_type": r.get("table_type"), "current_state": r["current_state"], "current_stage": r.get("current_stage"),
             "status": r.get("status")} for r in rows]


@router.get("/api/runs/{run_id}/gold")
def run_gold(run_id: str, db: Db = Depends(current_db)):
    run = _run(db, run_id)
    d = _domain(db, run["domain_id"])
    model = _gold_model(db, run["domain_id"])
    return {"run_id": run_id, "domain_id": run["domain_id"], "domain_name": d["domain_name"],
            "parent_run_id": run.get("parent_run_id"), "children": [] if run.get("parent_run_id") else _children(db, run_id),
            "role": str(d["mdm"].get("role") or "").upper() or None, "consumes": d["mdm"].get("consumes") or [],
            "model": model, "tables": _approved_tables(db, model) if model else [],
            "target": {"model_id": run.get("model_id"), "version": run.get("model_version"), "entity": run.get("model_entity"),
                       "fqn": run.get("target_model")} if run.get("model_entity") else None,
            "inputs": [{k: t[k] for k in ("database", "schema", "table", "row_count")} | {"columns": len(t["columns"])}
                       for t in _profiled_inputs(db, run_id)],
            "can_choose_target": run["current_state"] in MODEL_APPLY_STATES}


def _reference_model(db: Db, domain_id: str) -> Optional[Dict[str, Any]]:
    """The domain's gold reference model (a DATA_MODEL reference input for the GOLD layer, V057), if one was given."""
    from services.reference import store

    ref = store.latest(lambda sql, params: db.query(sql.replace("?", "%s"), tuple(params)), domain_id, "GOLD", "DATA_MODEL")
    return ref if ref and (ref.get("parsed") or {}).get("spec") else None


def _proposal(db: Db, d: Dict[str, Any], reg: Dict[str, Any], inputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The gold proposal for the profiled silver tables, aligned to the domain's gold reference model when there is
    one (its tables, columns and keys kept exactly; only what it lacks proposed)."""
    out = gold.propose(d["domain_name"], inputs, consumes=d["mdm"].get("consumes") or [], mastered=_mastered(reg),
                       env=_env(d.get("silver_database") or inputs[0]["database"]))
    ref = _reference_model(db, d["domain_id"])
    if ref:
        from services.gold import align
        from services.reference import score

        measured = score.model(out["spec"], ref["parsed"]["spec"])   # the engine's own proposal, before alignment
        out = align.align(out, ref["parsed"]["spec"], f"your reference model ({ref.get('title') or 'gold'})")
        out["reference"] = {"reference_id": ref["reference_id"], "title": ref.get("title")}
        out["score"] = measured
    return out


def _record_model_score(db: Db, domain_id: str, run_id: Optional[str], out: Dict[str, Any], stage: str) -> None:
    from services.reference import store

    if out.get("score") and out.get("reference"):
        store.record(lambda sql, params: db.execute(sql.replace("?", "%s"), tuple(params)), domain_id=domain_id,
                     layer="GOLD", kind="DATA_MODEL", reference_id=out["reference"]["reference_id"], run_id=run_id,
                     stage=stage, score=out["score"])


@router.post("/api/runs/{run_id}/gold/propose")
def propose_gold(run_id: str, db: Db = Depends(current_db)):
    """The run's profiled silver tables as a proposed gold model, saved as a draft of the domain's GOLD model: a new
    model, or merged into the open draft (tables a reviewer added are kept), aligned to the gold reference model."""
    from services.modeling import domain_model, importers

    run = _run(db, run_id)
    reg = _contracts(db)
    d = next(c for c in reg.values() if c["domain_id"] == run["domain_id"])
    _require_consumer(d)
    inputs = _profiled_inputs(db, run_id)
    if not inputs:
        raise HTTPException(409, "profile the run's silver tables first")
    out = _proposal(db, d, reg, inputs)
    spec = out["spec"]
    user = str(db.user or "").upper()
    model_id = domain_model.model_id_for(run["domain_id"], "GOLD")
    note = f"Proposed by Gold run {run['run_name']}"

    def work(session):
        from services.common.sql import rows

        exists = rows(session, "SELECT OPEN_VERSION, APPROVED_VERSION FROM GOVERNANCE.DOMAIN_MODEL WHERE MODEL_ID = ?", [model_id])
        if not exists:
            return domain_model.create_model(session, run["domain_id"], json.dumps(
                {"layer": "GOLD", "spec": spec, "origin": "GOLD_RUN", "change_note": note}), user)
        if not exists[0].get("OPEN_VERSION"):
            domain_model.open_draft(session, model_id, user, None, note)
        draft = domain_model.get_model(session, model_id)["current"]
        merged = importers.merge(draft["spec"], spec, model_id)
        return domain_model.save_draft(session, model_id, json.dumps({"spec": merged, "etag": draft["etag"], "change_note": note}), user)

    try:
        made = invoke_source(db, work)
    except HTTPException:
        raise
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    _record_model_score(db, run["domain_id"], run_id, out, "MODEL")
    return {"model_id": model_id, "version": made.get("version"), "notes": out["notes"],
            "reference": out.get("reference"), "alignment": out.get("alignment") or [], "score": out.get("score"),
            "tables": [{"entity": e["entity_name"], "table_type": e.get("table_type"), "conformed": bool(e.get("conformed_from"))}
                       for e in spec["design"]["entities"]]}


@router.post("/api/domains/{domain_id}/references/rescore")
def rescore_gold_reference(domain_id: str, db: Db = Depends(current_db)):
    """Replay the gold proposal on the latest profiled Gold run and measure it against the gold reference model,
    writing nothing but the score: after a change to skills, knowledge, rules or conventions, did output get closer?"""
    reg = _contracts(db)
    d = next((c for c in reg.values() if c["domain_id"] == domain_id), None)
    if not d:
        raise HTTPException(404, "domain not found")
    _require_consumer(d)
    if not _reference_model(db, domain_id):
        raise HTTPException(409, "give the Gold layer a data model reference first (References tab)")
    runs = db.query("""SELECT RUN_ID FROM CORE.WORKFLOW_RUN WHERE DOMAIN_ID = %s AND PIPELINE = 'GOLD' AND PARENT_RUN_ID IS NULL
                         AND DELETED_AT IS NULL ORDER BY CREATED_AT DESC LIMIT 5""", (domain_id,))
    for r in runs:
        inputs = _profiled_inputs(db, r["run_id"])
        if inputs:
            out = _proposal(db, d, reg, inputs)
            _record_model_score(db, domain_id, r["run_id"], out, "RESCORE")
            return {"run_id": r["run_id"], "score": out.get("score"), "reference": out.get("reference")}
    raise HTTPException(409, "profile a Gold run's silver tables first: the proposal is replayed on its profile")


class TargetIn(BaseModel):
    entity: str = Field(min_length=1, max_length=255)


@router.post("/api/runs/{run_id}/gold/target")
def choose_gold_target(run_id: str, body: TargetIn, db: Db = Depends(current_db)):
    run = _run(db, run_id)
    model = _gold_model(db, run["domain_id"])
    if not model or not model.get("approved_version"):
        raise HTTPException(409, "the domain's gold model is not approved yet; approve it in the Model Workbench (four eyes)")
    tables = {t["entity"]: t for t in _approved_tables(db, model)}
    entity = body.entity.upper()
    if entity not in tables:
        raise HTTPException(400, f"{entity} is not a table of the approved gold model")
    if run["current_state"] not in MODEL_APPLY_STATES:
        raise HTTPException(409, f"the run is at {run['current_state']}; reopen mapping to change its gold table")
    try:
        return pin_run_model(db, run_id, model["model_id"], int(model["approved_version"]), entity)
    except HTTPException:
        raise
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@router.post("/api/runs/{run_id}/gold/star-schema")
def build_star_schema(run_id: str, db: Db = Depends(current_db)):
    """One table run per fact and bridge of the approved gold model, each on its silver table (read in place),
    profiled from the cache and pinned to its table; tables that already have a run are left as they are."""
    from services.gold import star

    run = _run(db, run_id)
    if run.get("parent_run_id"):
        raise HTTPException(400, "this is a table run; build the star schema from its Gold run")
    reg = _contracts(db)
    d = next(c for c in reg.values() if c["domain_id"] == run["domain_id"])
    _require_consumer(d)
    model = _gold_model(db, run["domain_id"])
    if not model or not model.get("approved_version"):
        raise HTTPException(409, "the domain's gold model is not approved yet; approve it in the Model Workbench (four eyes)")
    inputs = _profiled_inputs(db, run_id)
    if not inputs:
        raise HTTPException(409, "profile the Gold run's silver tables first")
    from services.modeling import spec_v2

    found = db.query("SELECT SPEC FROM GOVERNANCE.MODEL_DESIGNS WHERE MODEL_ID = %s AND VERSION = %s",
                     (model["model_id"], int(model["approved_version"])))
    spec = spec_v2.active(spec_v2.normalize_v2(_js(found[0]["spec"]) or {}, model["model_id"]))
    proposal = _proposal(db, d, reg, inputs)
    layout = star.plan(spec, proposal["lines"], inputs)
    have = {str(c.get("entity") or "").upper() for c in _children(db, run_id)}
    created, errors = [], []
    for t in layout["tables"]:
        if t["entity"] in have:
            continue
        database, schema, table = t["source_table"].split(".")
        intent = {"path": "map_existing", "pipeline": "GOLD", "run_name": f"{run['run_name']}: {t['entity']}",
                  "model_existing": True, "targets": [], "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "domain_id": run["domain_id"],
                  "source": {"origin": "snowflake", "database": database, "schema": schema,
                             "source_system_name": f"SILVER_{database}_{schema}"[:120], "source_type": "SNOWFLAKE_DATABASE",
                             "tables": [table]},
                  "target": {"storage_type": "IN_PLACE"}}
        try:
            child = create_run(CreateRun(run_name=intent["run_name"], domain_id=run["domain_id"], intent=intent,
                                         pipeline="GOLD", parent_run_id=run_id), db)
            child_id = child.get("run_id")
            if child.get("registration_error"):
                raise HTTPException(409, child["registration_error"])
            if not prepare_source(child_id, AccessRequest(selected=[table]), db).get("passed"):
                raise HTTPException(409, f"access check failed on {t['source_table']}")
            run_profiling(child_id, None, db)
            pin_run_model(db, child_id, model["model_id"], int(model["approved_version"]), t["entity"])
            created.append({"run_id": child_id, **t})
        except HTTPException as exc:
            errors.append({**t, "error": str(exc.detail)})
        except Exception as exc:
            errors.append({**t, "error": str(_snowflake_error(exc).detail)})
    return {"created": created, "errors": errors, "unsourced": layout["unsourced"], "dimensions": layout["dimensions"],
            "children": _children(db, run_id)}


class StarPublishIn(BaseModel):
    base_branch: Optional[str] = Field(default=None, max_length=200)
    cut_branch: Optional[str] = Field(default=None, max_length=200)
    draft: bool = False


@router.post("/api/runs/{run_id}/gold/publish")
def publish_star_schema(run_id: str, body: StarPublishIn, db: Db = Depends(current_db)):
    """The Gold run's star schema (every table run's latest code) as one branch and one pull request."""
    from app.main import _publish_dbt

    run = _run(db, run_id)
    if run.get("parent_run_id"):
        raise HTTPException(400, "this is a table run; publish the star schema from its Gold run")
    children = _children(db, run_id)
    if not children:
        raise HTTPException(409, "build the star schema first")
    return _publish_dbt(db, run_id, {"star_schema": True, **body.model_dump(exclude_none=True)})

