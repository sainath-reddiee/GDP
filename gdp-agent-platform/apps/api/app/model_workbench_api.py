"""Model Workbench API: one canonical, versioned model per domain (services/modeling/domain_model.py).

  GET  /api/hub/domain-models                              every domain with its model state
  POST /api/hub/domain-models                              create a model for a domain (version 1 draft from its
                                                           registry), or fork a model into a new domain
  POST /api/hub/domain-models/baseline                     approved baselines for domains without a model
  GET  /api/hub/domain-models/{id}?version=                header, versions and one version with its specification
  POST /api/hub/domain-models/{id}/draft                   open the next version (or roll back to an older one)
  PUT  /api/hub/domain-models/{id}/draft                   save the draft (etag, refused when stale)
  POST /api/hub/domain-models/{id}/draft/validate          check a specification without saving
  POST /api/hub/domain-models/{id}/submit | withdraw       send for review / take back
  POST /api/hub/domain-models/{id}/versions/{v}/approve    four eyes; then applied to the registry
  POST /api/hub/domain-models/{id}/versions/{v}/reject
  POST /api/hub/domain-models/{id}/versions/{v}/apply      retry putting an approved version into the registry
  GET  /api/hub/domain-models/{id}/diff?from=&to=          classified changes (ADDITIVE, NON_FUNCTIONAL, BREAKING)
  GET|POST /api/hub/domain-models/{id}/versions/{v}/comments, POST .../comments/{cid}/resolve
  PUT  /api/hub/domain-models/{id}/layout | standard       diagram positions, naming standard
  GET  /api/hub/domain-models/{id}/export?format=ddl|pack|csv|dbt&version=

The review is the governance here, so these routes are never turned into change requests (NO_QUEUE_ROUTES in
services/governance/policy.py): a caller without the privilege is refused instead.
"""

from __future__ import annotations

import io
import json
import zipfile
from functools import partial
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _snowflake_error, _source_call, current_db
from app.source_invoke import invoke_source

router = APIRouter()


def _svc():
    from services.modeling import domain_model

    return domain_model


def _user(db: Db) -> str:
    return str(db.user or "").upper()


def _call(db: Db, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a workbench service on the caller's session. A Conflict (stale draft, second open version) and a breaking
    change without acknowledgement are 409; something missing is 404; any other rule is 409 with its message."""
    try:
        return invoke_source(db, partial(fn, **kwargs) if kwargs else fn, *args)
    except HTTPException:
        raise
    except AssertionError as exc:
        message = str(exc) or "request rejected"
        lower = message.lower()
        raise HTTPException(404 if "not found" in lower else 409, message) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


def _missing(exc: Exception) -> bool:
    text = str(exc).lower()
    return "does not exist" in text or "invalid identifier" in text


@router.get("/api/hub/domain-models")
def list_models(db: Db = Depends(current_db)):
    try:
        return {"models": invoke_source(db, _svc().list_models), "deployed": True}
    except Exception as exc:
        if _missing(exc):
            return {"models": [], "deployed": False}
        raise _snowflake_error(exc) from exc


class CreateIn(BaseModel):
    domain_id: Optional[str] = Field(default=None, max_length=36)
    fork_of: Optional[str] = Field(default=None, max_length=36)
    version: Optional[int] = Field(default=None, ge=1)
    domain: Optional[str] = Field(default=None, max_length=64)
    naming_standard: Optional[dict] = None
    change_note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/hub/domain-models")
def create_model(body: CreateIn, db: Db = Depends(current_db)):
    svc = _svc()
    if body.fork_of:
        if not body.domain:
            raise HTTPException(400, "name the new domain")
        return _call(db, svc.fork_model, body.fork_of, json.dumps({"version": body.version, "domain": body.domain}), _user(db))
    if not body.domain_id:
        raise HTTPException(400, "choose the domain to model")
    return _call(db, svc.create_model, body.domain_id,
                 json.dumps({"naming_standard": body.naming_standard, "change_note": body.change_note}), _user(db))


class BaselineIn(BaseModel):
    domain_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/hub/domain-models/baseline")
def baseline(body: BaselineIn, db: Db = Depends(current_db)):
    """An approved version 1 equal to today's registry, for one domain or every domain without a model."""
    svc = _svc()
    if body.domain_id:
        made = _call(db, svc.baseline, body.domain_id, _user(db))
        if not made:
            raise HTTPException(409, "the domain already has a model or registers no tables")
        return made
    return {"log": _call(db, svc.backfill, _user(db))}


@router.get("/api/hub/domain-models/{model_id}")
def get_model(model_id: str, version: Optional[int] = None, db: Db = Depends(current_db)):
    from services.modeling import spec_v2

    out = _call(db, _svc().get_model, model_id, version)
    if out.get("current"):
        out["current"]["issues"] = spec_v2.validate(out["current"]["spec"])
        out["current"]["summary"] = spec_v2.summary(out["current"]["spec"])
    return out


class DraftIn(BaseModel):
    from_version: Optional[int] = Field(default=None, ge=1)
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/hub/domain-models/{model_id}/draft")
def open_draft(model_id: str, body: DraftIn, db: Db = Depends(current_db)):
    return _call(db, _svc().open_draft, model_id, _user(db), body.from_version, body.note or "")


class SaveIn(BaseModel):
    spec: dict
    etag: Optional[str] = Field(default=None, max_length=64)
    change_note: Optional[str] = Field(default=None, max_length=2000)


@router.put("/api/hub/domain-models/{model_id}/draft")
def save_draft(model_id: str, body: SaveIn, if_match: Optional[str] = Header(default=None), db: Db = Depends(current_db)):
    etag = body.etag or (if_match or "").strip('"')
    return _call(db, _svc().save_draft, model_id,
                 json.dumps({"spec": body.spec, "etag": etag, "change_note": body.change_note}, default=str), _user(db))


class ValidateIn(BaseModel):
    spec: dict


@router.post("/api/hub/domain-models/{model_id}/draft/validate")
def validate_draft(model_id: str, body: ValidateIn):
    from services.modeling import spec_v2

    spec = spec_v2.normalize_v2(body.spec, model_id)
    return {"spec": spec, "issues": spec_v2.validate(spec), "summary": spec_v2.summary(spec)}


class NoteIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/hub/domain-models/{model_id}/submit")
def submit(model_id: str, body: NoteIn, db: Db = Depends(current_db)):
    return _call(db, _svc().submit, model_id, _user(db), body.note or "")


@router.post("/api/hub/domain-models/{model_id}/withdraw")
def withdraw(model_id: str, db: Db = Depends(current_db)):
    return _call(db, _svc().withdraw, model_id, _user(db))


def _apply(db: Db, model_id: str, version: int) -> dict:
    from services.modeling.registry_sync import apply_model_version

    try:
        return {"applied": True, **_source_call(db, "CALL MODELING.APPLY_DOMAIN_MODEL(%s, %s)", apply_model_version,
                                                model_id, version)}
    except AssertionError as exc:
        return {"applied": False, "error": str(exc)}
    except Exception as exc:
        return {"applied": False, "error": str(exc)[:500]}


class ApproveIn(BaseModel):
    acknowledge_breaking: bool = False
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/hub/domain-models/{model_id}/versions/{version}/approve")
def approve(model_id: str, version: int, body: ApproveIn, db: Db = Depends(current_db)):
    """The version becomes the domain's approved model, then the registry is updated from it. If the registry update
    fails the approval stands and /apply retries it."""
    result = _call(db, _svc().approve, model_id, version, _user(db), body.acknowledge_breaking)
    return {**result, "registry": _apply(db, model_id, version)}


@router.post("/api/hub/domain-models/{model_id}/versions/{version}/apply")
def apply(model_id: str, version: int, db: Db = Depends(current_db)):
    out = _apply(db, model_id, version)
    if not out["applied"]:
        raise HTTPException(409, out["error"])
    return out


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/api/hub/domain-models/{model_id}/versions/{version}/reject")
def reject(model_id: str, version: int, body: RejectIn, db: Db = Depends(current_db)):
    return _call(db, _svc().reject, model_id, version, _user(db), body.reason)


@router.get("/api/hub/domain-models/{model_id}/diff")
def diff(model_id: str, to: int, from_version: Optional[int] = Query(default=None, alias="from", ge=1),
         db: Db = Depends(current_db)):
    """Changes from one version to another; without `from`, from the approved version (or an empty model)."""
    svc = _svc()
    if from_version is None:
        header = _call(db, svc.get_model, model_id, to)["model"]
        approved = header.get("approved_version")
        from_version = int(approved) if approved and int(approved) != int(to) else None
    return _call(db, svc.diff_versions, model_id, from_version, to)


@router.get("/api/hub/domain-models/{model_id}/versions/{version}/comments")
def list_comments(model_id: str, version: int, db: Db = Depends(current_db)):
    return {"comments": _call(db, _svc().comments, model_id, version)}


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    path: str = Field(default="model", max_length=600)
    kind: Literal["COMMENT", "SUGGESTION", "BLOCKER"] = "COMMENT"
    parent_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/hub/domain-models/{model_id}/versions/{version}/comments")
def add_comment(model_id: str, version: int, body: CommentIn, db: Db = Depends(current_db)):
    return _call(db, _svc().add_comment, model_id, version, body.model_dump_json(), _user(db))


class ResolveIn(BaseModel):
    resolved: bool = True


@router.post("/api/hub/domain-models/{model_id}/comments/{comment_id}/resolve")
def resolve_comment(model_id: str, comment_id: str, body: ResolveIn, db: Db = Depends(current_db)):
    return _call(db, _svc().resolve_comment, model_id, comment_id, _user(db), body.resolved)


@router.put("/api/hub/domain-models/{model_id}/layout")
def set_layout(model_id: str, body: dict, db: Db = Depends(current_db)):
    return _call(db, _svc().set_layout, model_id, json.dumps(body.get("positions", body)), _user(db))


@router.put("/api/hub/domain-models/{model_id}/standard")
def set_standard(model_id: str, body: dict, db: Db = Depends(current_db)):
    return _call(db, _svc().set_standard, model_id, json.dumps(body), _user(db))


@router.get("/api/hub/domain-models/{model_id}/export")
def export(model_id: str, format: Literal["ddl", "pack", "csv", "dbt"] = "ddl", version: Optional[int] = None,
           db: Db = Depends(current_db)):
    from services.modeling import render_v2

    out = _call(db, _svc().get_model, model_id, version)
    current = out.get("current")
    if not current:
        raise HTTPException(404, "the model has no version yet")
    spec, v = current["spec"], current["version"]
    stem = f"{spec['domain'].lower()}_model_v{v}"
    if format == "ddl":
        return PlainTextResponse(render_v2.render_ddl(spec), media_type="text/plain",
                                 headers={"Content-Disposition": f'attachment; filename="{stem}.sql"'})
    if format == "csv":
        return PlainTextResponse(render_v2.data_dictionary(spec), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{stem}_dictionary.csv"'})
    if format == "pack":
        return PlainTextResponse(json.dumps(render_v2.render_pack(spec), indent=2) + "\n", media_type="application/json",
                                 headers={"Content-Disposition": f'attachment; filename="{stem}_domain_pack.json"'})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in render_v2.render_dbt(spec).items():
            z.writestr(path, content)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{stem}_dbt.zip"'})
