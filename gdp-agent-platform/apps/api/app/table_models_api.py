"""Table Model Store API: one persistent, versioned model per modeled table (services/modeling/table_model.py, V044).

  GET  /api/hub/table-models?domain_id=&source_name=&origin_run_id=&layer=&search=   the catalog
  POST /api/hub/table-models                                 create one ad hoc (from a landed source table or empty)
  GET  /api/hub/table-models/sources?search=                 landed source tables a model can be created from
  GET  /api/hub/table-models/{id}?version=                   header, versions, one version with spec, DDL and dbt
  PUT  /api/hub/table-models/{id}                            save the editor's specification as a new version (etag)
  POST /api/hub/table-models/{id}/render                     validate and render an unsaved specification
  POST /api/hub/table-models/{id}/suggest                    AI column classifications and descriptions
  GET  /api/hub/table-models/{id}/diff?from=&to=             classified changes between versions
  GET  /api/hub/table-models/{id}/related                    the domain model entities this table relates to
  PUT  /api/hub/table-models/{id}/layout | domain            diagram positions, the domain the table belongs to
  POST /api/hub/table-models/{id}/versions/{v}/propose       propose the table to its domain's governed model
  POST /api/hub/table-models/{id}/versions/{v}/publish       push the version's DDL and dbt files to a Git branch
  GET  /api/hub/table-models/{id}/export?format=ddl|dbt&version=

A table model never changes the governed domain model by itself: proposals are reviewed in the Model Workbench.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from functools import partial
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _snowflake_error, current_db
from app.source_invoke import invoke_source

router = APIRouter()


def _svc():
    from services.modeling import table_model

    return table_model


def _user(db: Db) -> str:
    return str(db.user or "").upper()


def _call(db: Db, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """A stale etag or any other rule is 409, something missing is 404."""
    try:
        return invoke_source(db, partial(fn, **kwargs) if kwargs else fn, *args)
    except HTTPException:
        raise
    except AssertionError as exc:
        message = str(exc) or "request rejected"
        raise HTTPException(404 if "not found" in message.lower() else 409, message) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


def _missing(exc: Exception) -> bool:
    text = str(exc).lower()
    return "does not exist" in text or "invalid identifier" in text


@router.get("/api/hub/table-models")
def list_models(domain_id: str = "", source_name: str = "", origin_run_id: str = "", layer: str = "", search: str = "",
                db: Db = Depends(current_db)):
    try:
        found = invoke_source(db, _svc().list_models, domain_id, source_name, origin_run_id, layer, search[:100])
    except Exception as exc:
        if _missing(exc):
            return {"models": [], "deployed": False}
        raise _snowflake_error(exc) from exc
    return {"models": found, "deployed": True}


@router.get("/api/hub/table-models/sources")
def sources(search: str = "", db: Db = Depends(current_db)):
    try:
        return {"tables": invoke_source(db, _svc().source_tables, search[:100])}
    except Exception as exc:
        if _missing(exc):
            return {"tables": []}
        raise _snowflake_error(exc) from exc


class CreateIn(BaseModel):
    landing_id: Optional[str] = Field(default=None, max_length=36)
    table_name: Optional[str] = Field(default=None, max_length=255)
    source_name: Optional[str] = Field(default=None, max_length=256)
    domain_id: Optional[str] = Field(default=None, max_length=36)
    model_layer: Literal["LANDING", "BRONZE", "SILVER", "GOLD"] = "SILVER"
    table_type: Literal["ICEBERG", "STANDARD", "DYNAMIC_TABLE"] = "STANDARD"


@router.post("/api/hub/table-models")
def create(body: CreateIn, db: Db = Depends(current_db)):
    """From a landed source table (its columns become the model's) or an empty table of a named source."""
    svc = _svc()
    payload = body.model_dump(exclude_none=True)
    if body.landing_id:
        return _call(db, svc.create_from_source, body.landing_id, payload, _user(db))
    if not (body.table_name and body.source_name):
        raise HTTPException(400, "choose a source table, or name the table and its source")
    return _call(db, svc.create, {**payload, "origin": "ADHOC"}, _user(db))


@router.get("/api/hub/table-models/{model_id}")
def get_model(model_id: str, version: Optional[int] = None, db: Db = Depends(current_db)):
    return _call(db, _svc().get_model, model_id, version)


class SaveIn(BaseModel):
    spec: dict
    etag: Optional[str] = Field(default=None, max_length=64)
    change_note: Optional[str] = Field(default=None, max_length=2000)
    model_layer: Optional[Literal["LANDING", "BRONZE", "SILVER", "GOLD"]] = None
    table_type: Optional[Literal["ICEBERG", "STANDARD", "DYNAMIC_TABLE"]] = None


@router.put("/api/hub/table-models/{model_id}")
def save(model_id: str, body: SaveIn, if_match: Optional[str] = Header(default=None), db: Db = Depends(current_db)):
    payload = body.model_dump()
    payload["etag"] = body.etag or (if_match or "").strip('"')
    return _call(db, _svc().save, model_id, payload, _user(db))


class RenderIn(BaseModel):
    spec: dict
    table_type: Optional[Literal["ICEBERG", "STANDARD", "DYNAMIC_TABLE"]] = None
    domain: Optional[str] = Field(default=None, max_length=128)


@router.post("/api/hub/table-models/{model_id}/render")
def render(model_id: str, body: RenderIn):
    """The editor's unsaved specification normalized, checked and rendered (nothing is saved)."""
    svc = _svc()
    spec = svc.single(body.spec, model_id, domain=body.domain or "", table_type=body.table_type or "STANDARD")
    from services.modeling import spec_v2

    return {"spec": spec, "issues": svc.validate(spec), "summary": spec_v2.summary(spec), **svc.render(spec)}


class SuggestIn(BaseModel):
    spec: Optional[dict] = None
    refresh: bool = False


@router.post("/api/hub/table-models/{model_id}/suggest")
def suggest(model_id: str, body: SuggestIn, db: Db = Depends(current_db)):
    return _call(db, _svc().suggest, model_id, body.spec, body.refresh)


class TableEvolveIn(BaseModel):
    spec: dict
    instructions: str = Field(min_length=3, max_length=16000)
    domain: Optional[str] = None
    table_type: Optional[str] = "STANDARD"


@router.post("/api/hub/table-models/{model_id}/evolve")
def evolve_table(model_id: str, body: TableEvolveIn, db: Db = Depends(current_db)):
    """Human-in-the-loop AI evolution of a single table model specification."""
    from app.main import _snowflake_error, invoke_source
    from services.modeling import spec_v2
    from services.modeling.ai_evolve import evolve_domain_model

    svc = _svc()
    base = svc.single(body.spec, model_id, domain=body.domain or "", table_type=body.table_type or "STANDARD")
    try:
        res = invoke_source(db, evolve_domain_model, base, body.instructions, model_id, [])
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    evolved = svc.single(res["spec"], model_id, domain=body.domain or "", table_type=body.table_type or "STANDARD")
    return {
        "spec": evolved,
        "issues": svc.validate(evolved),
        "summary": spec_v2.summary(evolved),
        "rationale": res["rationale"],
        "counts": res["counts"],
        **svc.render(evolved),
    }



@router.get("/api/hub/table-models/{model_id}/diff")
def diff(model_id: str, to: int, from_version: Optional[int] = Query(default=None, alias="from", ge=1),
         db: Db = Depends(current_db)):
    """Changes from one version to another; without `from`, from the version before `to`."""
    if from_version is None and to > 1:
        from_version = to - 1
    return _call(db, _svc().diff_versions, model_id, from_version, to)


@router.get("/api/hub/table-models/{model_id}/related")
def related(model_id: str, db: Db = Depends(current_db)):
    svc = _svc()

    def run(session):
        return svc.linked_entities(session, svc._header(session, model_id))

    try:
        return _call(db, run)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise
        return {"entities": [], "relationships": [], "model_id": None}


@router.put("/api/hub/table-models/{model_id}/layout")
def set_layout(model_id: str, body: dict, db: Db = Depends(current_db)):
    return _call(db, _svc().set_layout, model_id, body.get("positions", body), _user(db))


class DomainIn(BaseModel):
    domain_id: str = Field(min_length=1, max_length=36)


@router.put("/api/hub/table-models/{model_id}/domain")
def set_domain(model_id: str, body: DomainIn, db: Db = Depends(current_db)):
    return _call(db, _svc().link, model_id, body.domain_id, _user(db))


@router.post("/api/hub/table-models/{model_id}/versions/{version}/propose")
def propose(model_id: str, version: int, db: Db = Depends(current_db)):
    """Propose the table to its domain's governed model: merged and approved by modelers in the Model Workbench."""
    return _call(db, _svc().propose, model_id, version, _user(db))


class PublishIn(BaseModel):
    repo_id: str = Field(min_length=1, max_length=36)
    base: Optional[str] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, max_length=200)


@router.post("/api/hub/table-models/{model_id}/versions/{version}/publish")
def publish(model_id: str, version: int, body: PublishIn, db: Db = Depends(current_db)):
    """Open a pull request with the version's DDL and dbt files (feature/model-<table>-v<version>). The version becomes
    PUBLISHED when the pull request is merged."""
    from app.main import _dbt_repos, _github_publisher_ready

    out = _call(db, _svc().get_model, model_id, version)
    current = out.get("current") or {}
    errors = [i for i in current.get("issues") or [] if i.get("severity") == "ERROR"]
    if errors:
        raise HTTPException(409, f"Fix {len(errors)} problem(s) before publishing: {errors[0]['message']}")
    repo = next((r for r in _dbt_repos(db, None) if r["repo_id"] == body.repo_id), None)
    if not repo or (repo.get("provider") or "") not in ("GITHUB", "AZURE_DEVOPS"):
        raise HTTPException(409, "Choose a GitHub or Azure Repos repository connected in Admin, Integrations.")
    if not _github_publisher_ready(db):
        return {"status": "NOT_CONFIGURED", "detail": "GitHub publishing is not set up. An admin can set it up in the GitHub publishing card."}
    slug = re.sub(r"[^a-z0-9]+", "-", out["model"]["table_name"].lower()).strip("-") or "table"
    head = f"feature/model-{slug}-v{int(version)}"
    request = {"table_model_id": model_id, "version": int(version), "repo_id": body.repo_id, "head": head,
               "base": body.base or repo.get("branch"), "title": body.title}
    try:
        result = db.call("CALL CORE.PUBLISH_DBT_PR(%s, %s)", ("", json.dumps({"table_model": request})))
    except Exception as exc:
        return {"status": "FAILED", "detail": str(exc)[:1000], "head_branch": head}
    return {**(result or {}), "head_branch": head}


@router.get("/api/hub/table-models/{model_id}/export")
def export(model_id: str, format: Literal["ddl", "dbt"] = "ddl", version: Optional[int] = None,
           db: Db = Depends(current_db)):
    out = _call(db, _svc().get_model, model_id, version)
    current = out.get("current")
    if not current:
        raise HTTPException(404, "the table model has no version yet")
    stem = f"{out['model']['table_name'].lower()}_v{current['version']}"
    if format == "ddl":
        return PlainTextResponse(current["ddl"], media_type="text/plain",
                                 headers={"Content-Disposition": f'attachment; filename="{stem}.sql"'})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in _svc().files(current["spec"], out["model"].get("model_layer") or "SILVER").items():
            z.writestr(path, content)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{stem}_dbt.zip"'})
