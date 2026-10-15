"""Reference inputs (V057): give the platform anything about a domain and a layer, so the engine decides better.

POST /api/domains/{id}/references             files (any type, a zip of a dbt project too), with the layer and, if the
                                               person knows better than the detector, the kind (and the table for rows)
POST /api/domains/{id}/references/schema      a live Snowflake schema read as a data model or a post-MDM model
GET  /api/domains/{id}/references?layer=       every input of the layer: what it is, how it was read, what it changed
GET  /api/domains/{id}/references/{rid}        one input with what it was read as
POST /api/domains/{id}/references/{rid}/kind   correct its layer or kind; it is read again
POST /api/domains/{id}/references/{rid}/withdraw   the engine stops using it (kept for history)

What each kind becomes (services/reference): a DATA_MODEL is the layer's reference model (Gold proposals align to it,
outputs are scored against it); an MDM_SCHEMA is also that; an STTM sheet becomes reference mapping lines (mapping ranks
them first, the STTM pre-fills their transformations); SAMPLE_ROWS become a table's examples (review); DBT_CODE is kept
with its files listed (conventions are learned from it); a DOCUMENT is read for knowledge (review). Nothing changes what
a run does without a person: models go through the Model Workbench, knowledge through the review inbox, mappings and
STTMs through their gates.
"""

from __future__ import annotations

import io
import json
import re
import threading
import uuid
import zipfile
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.db import Db
from app.domain_sources_api import _approved_entities, _put, _get, _start_reading
from app.main import _snowflake_error, current_db, domain_detail
from app.source_invoke import invoke_source
from services.knowledge import document_files
from services.reference import detect, sttm_import

router = APIRouter()
LAYERS = ("SILVER", "MDM", "GOLD", "ANY")
MAX_FILES, MAX_BYTES = 10, 50 * 1024 * 1024
_reading: set = set()
_lock = threading.Lock()


def _js(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _deployed(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if "does not exist" in str(exc).lower() or "invalid identifier" in str(exc).lower():
        return HTTPException(503, "Reference inputs need migration V057. An admin applies it in Admin, Deploy.")
    return _snowflake_error(exc)


def _row(db: Db, domain_id: str, rid: str) -> Dict[str, Any]:
    found = db.query("""SELECT REFERENCE_ID, DOMAIN_ID, LAYER, KIND, TITLE, ORIGIN, SOURCE_POINTER, STATUS, NOTE, PARSED, APPLIED,
                               CREATED_BY, TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT
                          FROM CORE.REFERENCE_INPUT WHERE REFERENCE_ID = %s AND DOMAIN_ID = %s""", (rid, domain_id))
    if not found:
        raise HTTPException(404, "reference not found")
    r = found[0]
    r["parsed"], r["applied"] = _js(r.get("parsed")), _js(r.get("applied"))
    return r


def _set(db: Db, rid: str, **fields: Any) -> None:
    sets, params = [], []
    for k, v in fields.items():
        if isinstance(v, (dict, list)):
            sets.append(f"{k.upper()} = PARSE_JSON(%s)"); params.append(json.dumps(v, default=str))
        else:
            sets.append(f"{k.upper()} = %s"); params.append(v)
    db.execute(f"UPDATE CORE.REFERENCE_INPUT SET {', '.join(sets)}, UPDATED_AT = CURRENT_TIMESTAMP() WHERE REFERENCE_ID = %s",
               (*params, rid))


def _sheets(name: str, data: bytes) -> List[tuple]:
    if name.lower().endswith((".xlsx", ".xlsm")):
        from services.agentic.attachments import xlsx_rows

        return xlsx_rows(data, max_rows=20000)
    import csv

    text = data.decode("utf-8-sig", errors="ignore")
    try:
        dialect = csv.Sniffer().sniff(text.split("\n", 1)[0], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [(re.sub(r"\.\w+$", "", name.rsplit("/", 1)[-1]), [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)])]


def _model_layer(layer: str) -> str:
    return "GOLD" if layer == "GOLD" else "SILVER"


def _with_mdm(db: Db, domain_id: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """A post-MDM schema read as the MASTERED contract it implies, with the questions for the domain owner."""
    from services.mdm.derive import derive

    name = (db.query("SELECT DOMAIN_NAME FROM CORE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,)) or [{}])[0].get("domain_name")
    out = derive(str(name or ""), parsed.get("spec") or {})
    return {**parsed, "mdm_contract": out["contract"], "mdm_questions": out["questions"], "mdm_found": out["found"]}


def _read(db: Db, domain_id: str, rid: str, name: str, data: bytes, table: Optional[str]) -> None:
    """Read one kept input as its kind, and record what it was read as and what it changed."""
    r = _row(db, domain_id, rid)
    kind, layer = r["kind"], r["layer"]
    _set(db, rid, status="READING", note=f"Reading it as {kind.replace('_', ' ').lower()}")
    if kind in ("DATA_MODEL", "MDM_SCHEMA"):
        from services.modeling.model_files import ModelFileError, parse_file

        try:
            out = parse_file(name, data)
        except ModelFileError as exc:
            _set(db, rid, status="FAILED", note=str(exc)[:3900]); return
        if kind == "MDM_SCHEMA":
            out = _with_mdm(db, domain_id, out)
        _set(db, rid, status="READY", parsed=out,
             note=f"{out['format']}: {out['entities']} table(s), {out['columns']} column(s), {out['relationships']} relationship(s)"
                  + (f". {'; '.join(out['notes'][:3])}" if out["notes"] else ""))
    elif kind == "STTM":
        out = sttm_import.parse(_sheets(name, data), (table or "").upper() or None)
        if not out["lines"]:
            _set(db, rid, status="FAILED", note=("; ".join(out["notes"]) or "No mapping rows were found")[:3900]); return
        _set(db, rid, status="READY", parsed=out,
             note=f"{len(out['lines'])} mapping line(s) for {', '.join(out['targets'][:6])}")
    elif kind == "SAMPLE_ROWS":
        from services.knowledge import examples

        target = (table or re.sub(r"\.\w+$", "", name.rsplit("/", 1)[-1])).strip().upper()
        try:
            header, rows = examples.read_rows(name, data)
            checked = examples.check(target, header, rows, _approved_entities(db, domain_id, _model_layer(layer)).get(target))
        except examples.ExampleError as exc:
            _set(db, rid, status="FAILED", note=str(exc)[:3900]); return
        written = invoke_source(db, lambda session: examples.write(session, domain_id=domain_id, checked=checked, file_name=name,
                                                                   layer=_model_layer(layer) if layer != "ANY" else "ANY"))
        _set(db, rid, status="READY", parsed={k: checked[k] for k in ("table", "columns", "row_count", "warnings")},
             applied={"knowledge_id": written, "unchanged": written is None},
             note=f"{checked['row_count']} example row(s) of {target}, waiting for review in the knowledge inbox")
    elif kind == "DBT_CODE":
        files: Dict[str, str] = {}
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    if n.lower().endswith((".sql", ".yml", ".yaml", ".md")) and not n.endswith("/") and len(files) < 2000:
                        files[n] = z.read(n)[:200_000].decode("utf-8", errors="ignore")
        else:
            files[name] = data[:400_000].decode("utf-8", errors="ignore")
        models = [p for p in files if p.lower().endswith(".sql") and "/models/" in f"/{p.lower()}"]
        from services.reference import code_conventions

        from services.gold.conventions import learn as learn_gold

        learned = code_conventions.learn(files)
        gold_conventions, gold_notes = learn_gold(code_conventions.as_chunks(files))
        _set(db, rid, status="READY", parsed={"files": sorted(files)[:2000], "models": len(models), "conventions": learned,
                                              "gold_conventions": gold_conventions, "gold_notes": gold_notes,
                                              "overrides": code_conventions.to_overrides(learned, _model_layer(layer))},
             note=f"{len(files)} file(s), {len(models)} model(s); learned: {', '.join(sorted(learned)) or 'nothing yet'}")
    else:   # DOCUMENT: read for knowledge, proposed for this layer (the document reader and the review inbox)
        source_id = str(uuid.uuid4())
        path = document_files.stage_path(domain_id, source_id, name)
        file_kind, _note = document_files.classify(name, data)
        _put(db, path, data)
        db.execute("""INSERT INTO CORE.CONFLUENCE_SOURCE (SOURCE_ID, DOMAIN_ID, KIND, TITLE, FILE_NAME, STAGE_PATH, FILE_SIZE,
                                                     CONTENT_TYPE, FILE_SHA256, FILE_KIND, READ_STATUS, LAYER)
                      SELECT %s, %s, 'UPLOAD', %s, %s, %s, %s, %s, %s, %s, 'QUEUED', %s""",
                   (source_id, domain_id, name[:1024], name, path, len(data), document_files.content_type(name),
                    document_files.sha256(data), file_kind, _model_layer(layer) if layer != "ANY" else "ANY"))
        _start_reading(db, domain_id, source_id, data)
        _set(db, rid, status="READY", applied={"document_source_id": source_id},
             note="Being read for knowledge; what it states is proposed for this layer in the knowledge inbox")


def _start(db: Db, domain_id: str, rid: str, name: str, data: bytes, table: Optional[str]) -> None:
    with _lock:
        if rid in _reading:
            return
        _reading.add(rid)

    def run() -> None:
        try:
            _read(db, domain_id, rid, name, data, table)
        except Exception as exc:
            try:
                _set(db, rid, status="FAILED", note=str(getattr(exc, "detail", exc))[:3900])
            except Exception:
                pass
        finally:
            with _lock:
                _reading.discard(rid)

    threading.Thread(target=run, name=f"reference-{rid[:8]}", daemon=True).start()


@router.post("/api/domains/{domain_id}/references")
async def add_references(domain_id: str, files_in: List[UploadFile] = File(..., alias="files"), layer: str = Form("ANY"),
                         kind: Optional[str] = Form(None), table: Optional[str] = Form(None), db: Db = Depends(current_db)):
    layer = layer.upper()
    if layer not in LAYERS:
        raise HTTPException(400, f"layer must be one of {', '.join(LAYERS)}")
    if kind and kind.upper() not in detect.KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(detect.KINDS)}")
    files = []
    for f in files_in[:MAX_FILES]:
        files.append(((f.filename or "reference")[:512], await f.read(MAX_BYTES + 1)))

    def store() -> List[Dict[str, Any]]:
        domain_detail(domain_id, db)
        out = []
        for name, data in files:
            if len(data) > MAX_BYTES or not data:
                out.append({"file_name": name, "error": "empty" if not data else "larger than 50 MB"}); continue
            digest = document_files.sha256(data)
            dup = db.query("""SELECT REFERENCE_ID FROM CORE.REFERENCE_INPUT WHERE DOMAIN_ID = %s AND FILE_SHA256 = %s
                               AND LAYER = %s AND STATUS <> 'WITHDRAWN'""", (domain_id, digest, layer))
            if dup:
                out.append({"file_name": name, "reference_id": dup[0]["reference_id"], "duplicate": True}); continue
            found = detect.detect(name, data)
            k = (kind or found["kind"]).upper()
            rid = str(uuid.uuid4())
            path = f"{domain_id}/references/{rid}/{re.sub(r'[^A-Za-z0-9._-]+', '_', name)[:200]}"
            _put(db, path, data)
            db.execute("""INSERT INTO CORE.REFERENCE_INPUT (REFERENCE_ID, DOMAIN_ID, LAYER, KIND, TITLE, ORIGIN, SOURCE_POINTER,
                                                                 FILE_SHA256, STATUS, NOTE, PARSED)
                          SELECT %s, %s, %s, %s, %s, 'UPLOAD', %s, %s, 'QUEUED', %s, PARSE_JSON(%s)""",
                       (rid, domain_id, layer, k, name, path, digest, f"Detected as {k.replace('_', ' ').lower()}: {found['why']}",
                        json.dumps({"detected": found, "table": table})))
            _start(db, domain_id, rid, name, data, table)
            out.append({"file_name": name, "reference_id": rid, "kind": k, "detected": found})
        return out

    try:
        return {"references": await run_in_threadpool(store), "skipped": max(0, len(files_in) - MAX_FILES)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _deployed(exc) from exc


class SchemaIn(BaseModel):
    database: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(alias="schema", min_length=1, max_length=255)
    tables: Optional[List[str]] = Field(default=None, max_length=500)
    layer: Literal["SILVER", "MDM", "GOLD", "ANY"] = "ANY"
    kind: Literal["DATA_MODEL", "MDM_SCHEMA"] = "DATA_MODEL"


@router.post("/api/domains/{domain_id}/references/schema")
def add_schema_reference(domain_id: str, body: SchemaIn, db: Db = Depends(current_db)):
    """A live schema (read only: INFORMATION_SCHEMA, keys) as the layer's reference model."""
    from services.modeling import importers

    domain_detail(domain_id, db)
    ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
    if not ident.match(body.database) or not ident.match(body.schema_name):
        raise HTTPException(400, "database and schema must be plain identifiers")
    try:
        spec = invoke_source(db, importers.from_schema, body.database.upper(), body.schema_name.upper(), body.tables or None)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from None
    entities = (spec.get("design") or {}).get("entities") or []
    parsed = {"spec": spec, "format": "Live schema", "notes": [], "entities": len(entities),
              "columns": sum(len(e.get("attributes") or []) for e in entities), "relationships": len(spec.get("relationships") or [])}
    if body.kind == "MDM_SCHEMA":
        parsed = _with_mdm(db, domain_id, parsed)
    rid = str(uuid.uuid4())
    pointer = f"{body.database.upper()}.{body.schema_name.upper()}"
    try:
        db.execute("""INSERT INTO CORE.REFERENCE_INPUT (REFERENCE_ID, DOMAIN_ID, LAYER, KIND, TITLE, ORIGIN, SOURCE_POINTER,
                                                             STATUS, NOTE, PARSED)
                      SELECT %s, %s, %s, %s, %s, 'SCHEMA', %s, 'READY', %s, PARSE_JSON(%s)""",
                   (rid, domain_id, body.layer, body.kind, pointer, pointer,
                    f"Live schema: {parsed['entities']} table(s), {parsed['columns']} column(s)", json.dumps(parsed, default=str)))
    except Exception as exc:
        raise _deployed(exc) from exc
    return _row(db, domain_id, rid)


@router.get("/api/domains/{domain_id}/references")
def list_references(domain_id: str, layer: Optional[str] = None, db: Db = Depends(current_db)):
    where, params = ["DOMAIN_ID = %s"], [domain_id]
    if layer and layer.upper() in LAYERS:
        where.append("COALESCE(LAYER, 'ANY') IN ('ANY', %s)"); params.append(layer.upper())
    try:
        rows = db.query(f"""SELECT REFERENCE_ID, LAYER, KIND, TITLE, ORIGIN, SOURCE_POINTER, STATUS, NOTE, APPLIED,
                                   PARSED:entities::NUMBER AS ENTITIES, ARRAY_SIZE(PARSED:lines) AS LINES,
                                   PARSED:detected:why::STRING AS DETECTED_WHY, PARSED:mdm_found AS MDM_FOUND,
                                   PARSED:mdm_questions AS MDM_QUESTIONS, PARSED:mdm_contract IS NOT NULL AND
                                   NOT IS_NULL_VALUE(PARSED:mdm_contract) AS MDM_DERIVED, CREATED_BY,
                                   TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT
                              FROM CORE.REFERENCE_INPUT WHERE {' AND '.join(where)} ORDER BY CREATED_AT DESC LIMIT 200""",
                        tuple(params))
        scores = db.query(f"""SELECT LAYER, KIND, STAGE, SCORE, RUN_ID, TO_VARCHAR(SCORED_AT, 'YYYY-MM-DD HH24:MI') AS SCORED_AT
                                FROM CORE.REFERENCE_SCORE WHERE DOMAIN_ID = %s ORDER BY SCORED_AT DESC LIMIT 50""", (domain_id,))
    except Exception as exc:
        if "does not exist" in str(exc).lower():
            return {"references": [], "scores": [], "deployed": False}
        raise _snowflake_error(exc) from exc
    for r in rows:
        r["applied"] = _js(r.get("applied"))
        r["mdm_found"], r["mdm_questions"] = _js(r.get("mdm_found")), _js(r.get("mdm_questions"))
    for s in scores:
        s["score"] = _js(s.get("score"))
    return {"references": rows, "scores": scores, "deployed": True, "kinds": list(detect.KINDS)}


@router.get("/api/domains/{domain_id}/references/{rid}")
def get_reference(domain_id: str, rid: str, db: Db = Depends(current_db)):
    return _row(db, domain_id, rid)


class KindIn(BaseModel):
    kind: Literal["DATA_MODEL", "MDM_SCHEMA", "STTM", "DBT_CODE", "SAMPLE_ROWS", "DOCUMENT"]
    layer: Literal["SILVER", "MDM", "GOLD", "ANY"]
    table: Optional[str] = Field(default=None, max_length=255)


@router.post("/api/domains/{domain_id}/references/{rid}/kind")
def set_reference_kind(domain_id: str, rid: str, body: KindIn, db: Db = Depends(current_db)):
    """The person knows better than the detector: read it again as this kind, for this layer."""
    r = _row(db, domain_id, rid)
    if r["origin"] != "UPLOAD":
        raise HTTPException(400, "only an uploaded file can be read again as another kind")
    _set(db, rid, kind=body.kind, layer=body.layer, status="QUEUED", note=f"Read again as {body.kind.replace('_', ' ').lower()}")
    data = _get(db, r["source_pointer"])
    _start(db, domain_id, rid, r["title"] or "reference", data, body.table)
    return _row(db, domain_id, rid)


@router.post("/api/domains/{domain_id}/references/{rid}/withdraw")
def withdraw_reference(domain_id: str, rid: str, db: Db = Depends(current_db)):
    _row(db, domain_id, rid)
    _set(db, rid, status="WITHDRAWN", note="Withdrawn: the engine no longer uses it")
    return _row(db, domain_id, rid)


class UseModelIn(BaseModel):
    replace: bool = False


@router.post("/api/domains/{domain_id}/references/{rid}/use-as-model")
def use_reference_as_model(domain_id: str, rid: str, body: UseModelIn, db: Db = Depends(current_db)):
    """A data model reference as the layer's domain model: version 1 of a new model, or merged into the open draft
    (same-named tables replaced with their ids kept; with `replace`, tables the reference lacks are retired). It is a
    draft like any other: reviewed and approved in the Model Workbench (four eyes)."""
    from services.modeling import domain_model, importers

    r = _row(db, domain_id, rid)
    if r["kind"] != "DATA_MODEL" or r["status"] != "READY" or not (r.get("parsed") or {}).get("spec"):
        raise HTTPException(409, "only a data model reference that has been read can become the domain model")
    layer = r["layer"]
    if layer not in ("SILVER", "GOLD"):
        raise HTTPException(400, "give the reference a layer first (Silver or Gold): the MDM layer has no domain model")
    spec, user = r["parsed"]["spec"], str(db.user or "").upper()
    model_id = domain_model.model_id_for(domain_id, layer)
    note = f"From reference {r.get('title') or rid}"

    def work(session):
        from services.common.sql import rows

        exists = rows(session, "SELECT OPEN_VERSION FROM CORE.DOMAIN_MODEL WHERE MODEL_ID = ?", [model_id])
        if not exists:
            return domain_model.create_model(session, domain_id, json.dumps(
                {"layer": layer, "spec": spec, "origin": "REFERENCE", "change_note": note}), user)
        if not exists[0].get("OPEN_VERSION"):
            domain_model.open_draft(session, model_id, user, None, note)
        draft = domain_model.get_model(session, model_id)["current"]
        merged = importers.merge(draft["spec"], spec, model_id, replace=body.replace)
        return domain_model.save_draft(session, model_id, json.dumps({"spec": merged, "etag": draft["etag"], "change_note": note}), user)

    try:
        made = invoke_source(db, work)
    except HTTPException:
        raise
    except AssertionError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    applied = {**(r.get("applied") or {}), "model_id": model_id, "version": made.get("version")}
    _set(db, rid, applied=applied, note=f"{r.get('note') or ''} Draft v{made.get('version')} of the {layer.lower()} model.".strip()[:3900])
    return {"model_id": model_id, "version": made.get("version"), "layer": layer}


class UseMdmIn(BaseModel):
    contract: Optional[Dict[str, Any]] = None   # the derived contract as the owner corrected it


@router.post("/api/domains/{domain_id}/references/{rid}/use-as-mdm")
def use_reference_as_mdm(domain_id: str, rid: str, body: UseMdmIn, db: Db = Depends(current_db)):
    """The MASTERED contract derived from a post-MDM schema (or the owner's correction of it) as the domain's MDM
    contract: validated, versioned as a domain change, and governed like any domain edit. A repository domain keeps its
    contract in domain/<name>/mdm.json, so it gets the JSON to commit instead."""
    from app.domains_api import _baseline, _domain_caches_changed, _domain_snapshot
    from services.mdm import contract as mdm

    r = _row(db, domain_id, rid)
    derived = (r.get("parsed") or {}).get("mdm_contract")
    contract = body.contract or derived
    if r["kind"] != "MDM_SCHEMA" or r["status"] != "READY" or not contract:
        raise HTTPException(409, "only a post-MDM schema reference with a derived contract can become the MDM contract")
    found = db.query("SELECT DOMAIN_NAME, CONFIG:mdm AS MDM FROM CORE.DOMAIN_REGISTRY "
                     "WHERE DOMAIN_ID = %s", (domain_id,))
    if not found:
        raise HTTPException(404, "domain not found")
    name, current = found[0]["domain_name"], _js(found[0].get("mdm")) or {}
    contract = {**contract, "role": "MASTERED"}
    problems = mdm.validate(name, contract)
    if problems:
        raise HTTPException(400, "; ".join(problems))
    role = str(current.get("role") or "").upper()
    if role == "CONSUMER":
        raise HTTPException(409, f"{name} consumes mastered domains; unlink it on the Lineage tab before it is mastered itself")
    if role == "MASTERED":
        raise HTTPException(409, f"{name}'s contract comes from the repository: commit this as domain/{name.lower()}/mdm.json "
                                 f"(it is re-registered on deploy). {json.dumps(contract)[:3000]}")
    try:
        _baseline(db, domain_id)
        db.execute("UPDATE CORE.DOMAIN_REGISTRY SET CONFIG = OBJECT_INSERT(COALESCE(CONFIG, OBJECT_CONSTRUCT()), 'mdm', PARSE_JSON(%s), TRUE), "
                   "UPDATED_AT = CURRENT_TIMESTAMP() WHERE DOMAIN_ID = %s", (json.dumps(contract), domain_id))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    _domain_caches_changed()
    _domain_snapshot(db, domain_id, "EDIT", f"Mastered by Reltio: contract from {r.get('title') or 'a post-MDM schema'}")
    _set(db, rid, applied={**(r.get("applied") or {}), "mdm_contract": True})
    return {"domain_id": domain_id, "role": "MASTERED", "contract": contract}
