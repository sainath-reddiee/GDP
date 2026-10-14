"""Pipeline 2, Silver (post-MDM) to Gold: gold builds of a domain (V053).

GET  /api/gold/builds                          every build, newest first
POST /api/gold/builds                          start one for a domain (MODEL.EDIT)
GET  /api/gold/builds/{id}                     the build, its inputs, the proposal, the mapping lines, the gold model
GET  /api/gold/builds/{id}/candidates          tables it can start from: the domain's silver hubs (consumer domains)
                                               or its post-MDM golden tables (mastered domains), read from the catalog
PUT  /api/gold/builds/{id}/inputs              choose the input tables
POST /api/gold/builds/{id}/propose             read the inputs' columns (and approximate distinct counts of descriptor
                                               columns, read only) and propose the gold model (services/gold/propose.py)
POST /api/gold/builds/{id}/workbench           put the proposal into the domain's GOLD model as a draft, to review and
                                               approve in the Model Workbench (four eyes)
POST /api/gold/builds/{id}/cancel
Nothing here writes to silver, MDM or gold data; generation (dbt) comes after approval.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db, transaction
from app.main import _quote_ident, _snowflake_error, current_db, domain_detail
from app.source_invoke import invoke_source
from services.gold import propose as gold

router = APIRouter()
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
LAYERS = ("SILVER_PRE_MDM", "SILVER_POST_MDM")


def _js(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _deployed(exc: Exception) -> HTTPException:
    if "does not exist" in str(exc).lower() or "invalid identifier" in str(exc).lower():
        return HTTPException(503, "Gold builds need migration V053. An admin applies it in Admin, Deploy.")
    return _snowflake_error(exc)


def _build(db: Db, build_id: str) -> Dict[str, Any]:
    try:
        found = db.query("""SELECT BUILD_ID, DOMAIN_ID, DOMAIN_NAME, NAME, STEP, STATUS, INPUTS, PROPOSAL, MODEL_ID, MODEL_VERSION,
                                   GENERATION, NOTE, CREATED_BY, TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT,
                                   UPDATED_BY, TO_VARCHAR(UPDATED_AT, 'YYYY-MM-DD HH24:MI') AS UPDATED_AT
                              FROM CORE.GOLD_BUILD WHERE BUILD_ID = %s""", (build_id,))
    except Exception as exc:
        raise _deployed(exc) from exc
    if not found:
        raise HTTPException(404, "gold build not found")
    b = found[0]
    for k in ("inputs", "proposal", "generation"):
        b[k] = _js(b.get(k))
    return b


def _contracts(db: Db) -> Dict[str, Dict[str, Any]]:
    rows = db.query("""SELECT DOMAIN_ID, DOMAIN_NAME, CONFIG:mdm AS MDM, CONFIG:silver_database::STRING AS SILVER_DATABASE,
                              CONFIG:silver_schema::STRING AS SILVER_SCHEMA
                         FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG""")
    return {r["domain_name"]: {**r, "mdm": _js(r.get("mdm")) or {}} for r in rows}


def _env(silver_database: Optional[str]) -> str:
    m = re.match(r"^([A-Z]+)_", str(silver_database or "").upper())
    return m.group(1) if m else "DEV"


def _set(db: Db, build_id: str, **fields: Any) -> None:
    sets, params = [], []
    for k, v in fields.items():
        if isinstance(v, (dict, list)):
            sets.append(f"{k.upper()} = PARSE_JSON(%s)")
            params.append(json.dumps(v, default=str))
        else:
            sets.append(f"{k.upper()} = %s")
            params.append(v)
    db.execute(f"UPDATE CORE.GOLD_BUILD SET {', '.join(sets)}, UPDATED_BY = CURRENT_USER(), UPDATED_AT = CURRENT_TIMESTAMP() "
               "WHERE BUILD_ID = %s", (*params, build_id))


@router.get("/api/gold/builds")
def list_builds(domain_id: Optional[str] = None, db: Db = Depends(current_db)):
    where, params = ("WHERE DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    try:
        rows = db.query(f"""SELECT BUILD_ID, DOMAIN_ID, DOMAIN_NAME, NAME, STEP, STATUS, MODEL_ID, MODEL_VERSION,
                                   ARRAY_SIZE(COALESCE(INPUTS, ARRAY_CONSTRUCT())) AS INPUTS,
                                   ARRAY_SIZE(COALESCE(PROPOSAL:spec:design:entities, ARRAY_CONSTRUCT())) AS TABLES,
                                   CREATED_BY, TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT,
                                   TO_VARCHAR(UPDATED_AT, 'YYYY-MM-DD HH24:MI') AS UPDATED_AT
                              FROM CORE.GOLD_BUILD {where} ORDER BY COALESCE(UPDATED_AT, CREATED_AT) DESC LIMIT 200""", params)
    except Exception as exc:
        if "does not exist" in str(exc).lower():
            return {"builds": [], "deployed": False}
        raise _snowflake_error(exc) from exc
    return {"builds": rows, "deployed": True}


class BuildIn(BaseModel):
    domain_id: str = Field(min_length=1, max_length=36)
    name: Optional[str] = Field(default=None, max_length=256)


@router.post("/api/gold/builds")
def create_build(body: BuildIn, db: Db = Depends(current_db)):
    detail = domain_detail(body.domain_id, db)
    name = detail["domain"]["domain_name"]
    build_id = str(uuid.uuid4())
    try:
        db.execute("""INSERT INTO CORE.GOLD_BUILD (BUILD_ID, DOMAIN_ID, DOMAIN_NAME, NAME, INPUTS)
                      SELECT %s, %s, %s, %s, ARRAY_CONSTRUCT()""",
                   (build_id, body.domain_id, name, (body.name or f"{name} gold").strip()[:256]))
    except Exception as exc:
        raise _deployed(exc) from exc
    return get_build(build_id, db)


@router.get("/api/gold/builds/{build_id}")
def get_build(build_id: str, db: Db = Depends(current_db)):
    b = _build(db, build_id)
    lines = db.query("""SELECT GOLD_TABLE, GOLD_COLUMN, KIND, SOURCE_LAYER, SOURCE_TABLE, SOURCE_COLUMN, EXPRESSION, NOTE
                          FROM GOVERNANCE.GOLD_MAPPING_LINE WHERE BUILD_ID = %s ORDER BY POSITION""", (build_id,))
    reg = _contracts(db)
    mdm = (reg.get(b["domain_name"]) or {}).get("mdm") or {}
    model = None
    if b.get("model_id"):
        found = db.query("SELECT MODEL_ID, APPROVED_VERSION, OPEN_VERSION FROM GOVERNANCE.DOMAIN_MODEL WHERE MODEL_ID = %s", (b["model_id"],))
        model = found[0] if found else None
    gen = b.get("generation") or None
    if gen:   # the summary; the files themselves come from /generation
        gen = {k: v for k, v in gen.items() if k != "files"} | {"paths": sorted((gen.get("files") or {}).keys())}
    return {**b, "generation": gen, "lines": lines, "role": str(mdm.get("role") or "").upper() or None,
            "consumes": mdm.get("consumes") or [], "model": model}


@router.get("/api/gold/builds/{build_id}/candidates")
def candidates(build_id: str, db: Db = Depends(current_db)):
    """Input tables a build can start from, with row counts: a consumer domain's silver hubs (its registered target
    tables), or a mastered domain's post-MDM golden and cross-reference tables."""
    b = _build(db, build_id)
    reg = _contracts(db).get(b["domain_name"]) or {}
    mdm = reg.get("mdm") or {}
    out: List[Dict[str, Any]] = []
    if str(mdm.get("role") or "").upper() == "MASTERED":
        env = _env(reg.get("silver_database"))
        for part, layer in (("golden", "SILVER_POST_MDM"), ("cross_reference", "SILVER_POST_MDM")):
            sec = mdm.get(part) or {}
            if sec:
                out.append({"database": str(sec["database"]).replace("{ENV}", env), "schema": sec["schema"], "table": sec["table"],
                            "layer": layer, "role": part})
    else:
        tables = db.query("""SELECT TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                              WHERE DOMAIN_ID = %s AND ACTIVE_FLAG ORDER BY TARGET_TABLE""", (b["domain_id"],))
        out = [{"database": t["target_database"], "schema": t["target_schema"], "table": t["target_table"],
                "layer": "SILVER_PRE_MDM", "role": "hub"} for t in tables]
    # what exists, with row counts (read only); a table the role cannot read is listed as unreadable
    by_loc: Dict[tuple, List[Dict[str, Any]]] = {}
    for t in out:
        by_loc.setdefault((t["database"], t["schema"]), []).append(t)
    for (database, schema), items in by_loc.items():
        try:
            found = {r["table_name"]: r for r in db.query(
                f"SELECT TABLE_NAME, ROW_COUNT, LAST_ALTERED::VARCHAR AS LAST_ALTERED FROM {_quote_ident(database)}.INFORMATION_SCHEMA.TABLES "
                f"WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ({', '.join(['%s'] * len(items))})", (schema, *[i["table"] for i in items]))}
        except Exception as exc:
            found, err = {}, str(exc).splitlines()[0][:200]
            for i in items:
                i["error"] = err
        for i in items:
            meta = found.get(i["table"])
            i["exists"] = bool(meta)
            i["row_count"] = meta.get("row_count") if meta else None
            i["last_altered"] = meta.get("last_altered") if meta else None
    return {"role": str(mdm.get("role") or "").upper() or None, "tables": out}


class InputIn(BaseModel):
    database: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(alias="schema", min_length=1, max_length=255)
    table: str = Field(min_length=1, max_length=255)
    layer: str = Field(default="SILVER_PRE_MDM", max_length=32)


class InputsIn(BaseModel):
    tables: List[InputIn] = Field(max_length=20)


@router.put("/api/gold/builds/{build_id}/inputs")
def set_inputs(build_id: str, body: InputsIn, db: Db = Depends(current_db)):
    b = _build(db, build_id)
    if b["status"] in ("APPROVED", "GENERATED", "CANCELLED"):
        raise HTTPException(409, f"the build is {b['status'].lower()}; start a new one")
    tables = []
    for t in body.tables:
        if not all(IDENT.match(x) for x in (t.database, t.schema_name, t.table)):
            raise HTTPException(400, f"{t.database}.{t.schema_name}.{t.table} is not a table name")
        if t.layer not in LAYERS:
            raise HTTPException(400, f"layer must be one of {', '.join(LAYERS)}")
        tables.append({"database": t.database.upper(), "schema": t.schema_name.upper(), "table": t.table.upper(), "layer": t.layer})
    _set(db, build_id, inputs=tables, step="INPUTS", status="DRAFT")
    return get_build(build_id, db)


def _columns(db: Db, t: Dict[str, Any]) -> Dict[str, Any]:
    cols = db.query(f"""SELECT COLUMN_NAME AS NAME, DATA_TYPE, IS_NULLABLE, COMMENT, NUMERIC_PRECISION, NUMERIC_SCALE
                          FROM {_quote_ident(t['database'])}.INFORMATION_SCHEMA.COLUMNS
                         WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION""", (t["schema"], t["table"]))
    if not cols:
        raise HTTPException(404, f"{t['database']}.{t['schema']}.{t['table']} has no readable columns")
    out = []
    for c in cols:
        dt = str(c["data_type"]).upper()
        if dt in ("NUMBER", "DECIMAL", "NUMERIC") and c.get("numeric_precision") is not None:
            dt = f"NUMBER({int(c['numeric_precision'])},{int(c.get('numeric_scale') or 0)})"
        out.append({"name": c["name"], "data_type": dt, "nullable": str(c.get("is_nullable")).upper() == "YES", "comment": c.get("comment")})
    # approximate distinct counts of descriptor-like text columns: the only read of data (one scan, read only)
    desc = [c for c in out if gold.is_descriptor(c["name"], c["data_type"])]
    if desc:
        try:
            row = db.query("SELECT " + ", ".join(f"APPROX_COUNT_DISTINCT({_quote_ident(c['name'])}) AS D{i}" for i, c in enumerate(desc))
                           + f" FROM {_quote_ident(t['database'])}.{_quote_ident(t['schema'])}.{_quote_ident(t['table'])}")[0]
            for i, c in enumerate(desc):
                c["distinct_count"] = row.get(f"d{i}")
        except Exception:
            pass   # without counts, descriptors stay attributes
    return {**t, "columns": out}


@router.post("/api/gold/builds/{build_id}/propose")
def propose_build(build_id: str, db: Db = Depends(current_db)):
    b = _build(db, build_id)
    if not b.get("inputs"):
        raise HTTPException(400, "choose the input tables first")
    if b["status"] in ("APPROVED", "GENERATED", "CANCELLED"):
        raise HTTPException(409, f"the build is {b['status'].lower()}; start a new one")
    reg = _contracts(db)
    me = reg.get(b["domain_name"]) or {}
    mdm = me.get("mdm") or {}
    role = str(mdm.get("role") or "").upper()
    if role != "CONSUMER":
        raise HTTPException(400, f"{b['domain_name']} is " + ("mastered by Reltio; its gold (the conformed dimension) exists in gdp-dbt "
                             "and is registered, not regenerated" if role == "MASTERED" else
                             "not linked to MDM; mark which mastered domains it consumes on its Lineage tab first"))
    mastered = {n: r["mdm"] for n, r in reg.items() if str(r["mdm"].get("role") or "").upper() == "MASTERED"}
    inputs = [_columns(db, t) for t in b["inputs"]]
    out = gold.propose(b["domain_name"], inputs, consumes=mdm.get("consumes") or [], mastered=mastered, env=_env(me.get("silver_database")))
    try:
        with transaction(db):
            db.execute("DELETE FROM GOVERNANCE.GOLD_MAPPING_LINE WHERE BUILD_ID = %s", (build_id,))
            for i, l in enumerate(out["lines"]):
                db.execute("""INSERT INTO GOVERNANCE.GOLD_MAPPING_LINE (BUILD_ID, POSITION, GOLD_TABLE, GOLD_COLUMN, KIND, SOURCE_LAYER,
                                                                      SOURCE_TABLE, SOURCE_COLUMN, EXPRESSION, NOTE)
                              SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, NULLIF(%s, '')""",
                           (build_id, i, l["gold_table"], l["gold_column"], l["kind"], l.get("source_layer"), l.get("source_table"),
                            l.get("source_column"), l.get("expression"), l.get("note") or ""))
            _set(db, build_id, proposal={"spec": out["spec"], "notes": out["notes"]}, step="MODELING", status="PROPOSED")
    except Exception as exc:
        raise _deployed(exc) from exc
    return get_build(build_id, db)


@router.post("/api/gold/builds/{build_id}/workbench")
def to_workbench(build_id: str, db: Db = Depends(current_db)):
    """The proposal becomes a draft of the domain's GOLD model (created on first use), reviewed and approved in the
    Model Workbench like any model change."""
    from services.modeling import domain_model

    b = _build(db, build_id)
    spec = (b.get("proposal") or {}).get("spec")
    if not spec:
        raise HTTPException(400, "propose the gold model first")
    user = str(db.user or "").upper()
    model_id = domain_model.model_id_for(b["domain_id"], "GOLD")

    def work(session):
        from services.common.sql import rows

        exists = rows(session, "SELECT OPEN_VERSION, APPROVED_VERSION FROM GOVERNANCE.DOMAIN_MODEL WHERE MODEL_ID = ?", [model_id])
        if not exists:
            return domain_model.create_model(session, b["domain_id"], json.dumps(
                {"layer": "GOLD", "spec": spec, "origin": "GOLD_BUILD", "change_note": f"Proposed by gold build {b['name']}"}), user)
        if not exists[0].get("OPEN_VERSION"):
            domain_model.open_draft(session, model_id, user, None, f"Gold build {b['name']}")
        draft = domain_model.get_model(session, model_id)["current"]
        return domain_model.save_draft(session, model_id, json.dumps({"spec": spec, "etag": draft["etag"],
                                                                      "change_note": f"Proposed by gold build {b['name']}"}), user)

    try:
        made = invoke_source(db, work)
    except HTTPException:
        raise
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        raise _deployed(exc) from exc
    _set(db, build_id, model_id=model_id, model_version=int(made.get("version") or 0) or None, step="REVIEW", status="IN_REVIEW")
    return get_build(build_id, db)


@router.post("/api/gold/builds/{build_id}/cancel")
def cancel_build(build_id: str, db: Db = Depends(current_db)):
    _build(db, build_id)
    _set(db, build_id, status="CANCELLED")
    return get_build(build_id, db)


# ---------------------------------------------------------------- gold code generation (after approval)

def _approved_gold(db: Db, b: Dict[str, Any]) -> Dict[str, Any]:
    from services.modeling import spec_v2

    if not b.get("model_id"):
        raise HTTPException(409, "send the proposal to the Model Workbench and have the gold model approved first")
    found = db.query("""SELECT M.APPROVED_VERSION, D.SPEC FROM GOVERNANCE.DOMAIN_MODEL M
                          JOIN GOVERNANCE.MODEL_DESIGNS D ON D.MODEL_ID = M.MODEL_ID AND D.VERSION = M.APPROVED_VERSION
                         WHERE M.MODEL_ID = %s""", (b["model_id"],))
    if not found or not found[0].get("approved_version"):
        raise HTTPException(409, "the gold model is not approved yet; approve it in the Model Workbench (four eyes)")
    spec = spec_v2.active(spec_v2.normalize_v2(_js(found[0]["spec"]) or {}, b["model_id"]))
    return {"version": int(found[0]["approved_version"]), "spec": spec}


def _conventions(db: Db) -> tuple:
    from services.gold.conventions import learn

    try:
        chunks = db.query("""SELECT C.PATH, C.KIND, C.NAME, C.TEXT, C.COLUMNS FROM CODE.CODE_CHUNK C
                              JOIN CODE.REPO R ON R.REPO_ID = C.REPO_ID
                             WHERE R.ENABLED AND (C.TEXT ILIKE '%m_insert_history_gold(%' OR LOWER(C.NAME) = 'dim_data_source')
                             LIMIT 200""")
    except Exception:
        return {}, ["the code index could not be read; plain SQL is generated"]
    for c in chunks:
        c["columns"] = _js(c.get("columns")) or []
    return learn(chunks)


@router.post("/api/gold/builds/{build_id}/generate")
def generate_gold(build_id: str, db: Db = Depends(current_db)):
    """dbt models, Iceberg DDL and a report for the approved gold model; lineage edges silver/MDM to gold."""
    import hashlib

    from services.gold import dbt as gold_dbt

    b = _build(db, build_id)
    if b["status"] == "CANCELLED":
        raise HTTPException(409, "the build is cancelled")
    approved = _approved_gold(db, b)
    reg = _contracts(db)
    me = reg.get(b["domain_name"]) or {}
    mdm = me.get("mdm") or {}
    mastered = {n: r["mdm"] for n, r in reg.items() if str(r["mdm"].get("role") or "").upper() == "MASTERED"}
    lines = db.query("""SELECT GOLD_TABLE, GOLD_COLUMN, KIND, SOURCE_LAYER, SOURCE_TABLE, SOURCE_COLUMN, EXPRESSION, NOTE
                          FROM GOVERNANCE.GOLD_MAPPING_LINE WHERE BUILD_ID = %s ORDER BY POSITION""", (build_id,))
    conventions, notes = _conventions(db)
    try:
        out = gold_dbt.generate(approved["spec"], lines, mastered=mastered, consumes=mdm.get("consumes") or [],
                                conventions=conventions, env=_env(me.get("silver_database")),
                                build={"build_id": build_id, "name": b["name"], "model_id": b["model_id"], "model_version": approved["version"]})
    except (ValueError, KeyError, StopIteration) as exc:
        raise HTTPException(400, f"the approved gold model cannot be generated: {exc}") from None
    out["report"]["notes"] = notes
    version = int((b.get("generation") or {}).get("version") or 0) + 1
    generation = {"version": version, "model_version": approved["version"], "report": out["report"], "files": out["files"]}
    try:
        with transaction(db):
            db.execute("UPDATE GOVERNANCE.LINEAGE_EDGE SET IS_CURRENT = FALSE WHERE PRODUCER = 'GOLD_BUILD' AND PRODUCER_ID = %s", (build_id,))
            for e in out["lineage"]:
                eid = hashlib.md5(json.dumps([e["from_object"], e.get("from_column"), e["to_object"], e["to_column"], build_id, version]).encode()).hexdigest()
                db.execute("""INSERT INTO GOVERNANCE.LINEAGE_EDGE (EDGE_ID, FROM_LAYER, FROM_OBJECT, FROM_COLUMN, TO_LAYER, TO_OBJECT, TO_COLUMN,
                                                                   KIND, EXPRESSION, PRODUCER, PRODUCER_ID, GENERATION, DOMAIN_ID)
                              SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, 'GOLD_BUILD', %s, %s, %s""",
                           (eid, e["from_layer"], e["from_object"], e.get("from_column"), e["to_layer"], e["to_object"], e["to_column"],
                            e["kind"], (e.get("expression") or "")[:8000], build_id, version, b["domain_id"]))
            _set(db, build_id, generation=generation, step="DONE", status="GENERATED")
    except Exception as exc:
        raise _deployed(exc) from exc
    return get_build(build_id, db)


@router.get("/api/gold/builds/{build_id}/generation")
def gold_generation(build_id: str, db: Db = Depends(current_db)):
    b = _build(db, build_id)
    if not b.get("generation"):
        raise HTTPException(404, "nothing generated yet")
    return b["generation"]


@router.get("/api/gold/builds/{build_id}/generation.zip")
def gold_generation_zip(build_id: str, db: Db = Depends(current_db)):
    import io
    import zipfile

    from fastapi.responses import Response

    gen = gold_generation(build_id, db)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in sorted((gen.get("files") or {}).items()):
            z.writestr(path, text)
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"gold_{build_id[:8]}_v{gen.get('version')}")
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})
