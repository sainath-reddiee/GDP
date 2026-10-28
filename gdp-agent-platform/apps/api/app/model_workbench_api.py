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

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
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
def list_models(layer: str = "SILVER", db: Db = Depends(current_db)):
    """The domains' models of one layer: SILVER (what Silver runs map into) or GOLD (the star schemas Gold runs build)."""
    layer = "GOLD" if str(layer).upper() == "GOLD" else "SILVER"
    try:
        return {"models": invoke_source(db, _svc().list_models, layer), "deployed": True, "layer": layer}
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


@router.get("/api/hub/domain-models/publishing")
def publishing(db: Db = Depends(current_db)):
    """Where approved versions can be published: the GitHub repositories connected for dbt, and publisher readiness."""
    from app.main import _dbt_repos, _github_publisher_ready

    repos = [{k: r.get(k) for k in ("repo_id", "name", "git_url", "provider", "branch")} for r in _dbt_repos(db, None)
             if (r.get("provider") or "") in ("GITHUB", "AZURE_DEVOPS")]
    return {"repos": repos, "publisher_ready": _github_publisher_ready(db)}


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
        return {"applied": True, **_source_call(db, "CALL CORE.APPLY_DOMAIN_MODEL(%s, %s)", apply_model_version,
                                                model_id, version)}
    except AssertionError as exc:
        return {"applied": False, "error": str(exc)}
    except Exception as exc:
        return {"applied": False, "error": str(exc)[:500]}


class ApproveIn(BaseModel):
    acknowledge_breaking: bool = False
    note: Optional[str] = Field(default=None, max_length=2000)


def _pin_merged(db: Db, model_id: str, version: int) -> list:
    """Runs whose proposals this version carries now use it (or are told to reopen mapping)."""
    from app.main import pin_run_model
    from services.modeling.run_link import on_approved

    try:
        merged = invoke_source(db, on_approved, model_id, version)
    except Exception:
        return []
    out = []
    for m in merged:
        try:
            out.append(pin_run_model(db, m["run_id"], model_id, version, m["entity"], m["design_version"]))
        except Exception as exc:
            out.append({"run_id": m["run_id"], "pinned": False, "detail": str(exc)[:300]})
    return out


@router.post("/api/hub/domain-models/{model_id}/versions/{version}/approve")
def approve(model_id: str, version: int, body: ApproveIn, db: Db = Depends(current_db)):
    """The version becomes the domain's approved model, then the registry is updated from it and the runs whose
    proposals it carries are pointed at it. If the registry update fails the approval stands and /apply retries it."""
    result = _call(db, _svc().approve, model_id, version, _user(db), body.acknowledge_breaking)
    registry = _apply(db, model_id, version)
    return {**result, "registry": registry, "runs": _pin_merged(db, model_id, version) if registry.get("applied") else []}


@router.get("/api/hub/domain-models/{model_id}/changes")
def list_changes(model_id: str, db: Db = Depends(current_db)):
    from services.modeling import run_link

    try:
        return {"changes": invoke_source(db, run_link.list_changes, model_id)}
    except Exception as exc:
        if _missing(exc):
            return {"changes": []}
        raise _snowflake_error(exc) from exc


@router.post("/api/hub/domain-models/{model_id}/changes/{change_id}/merge")
def merge_change(model_id: str, change_id: str, db: Db = Depends(current_db)):
    """Merge a run's proposal into the open draft (opening one when there is none)."""
    from services.modeling import run_link

    return _call(db, run_link.merge_change, model_id, change_id, _user(db))


class ChangeRejectIn(BaseModel):
    note: str = Field(min_length=3, max_length=2000)


@router.post("/api/hub/domain-models/{model_id}/changes/{change_id}/reject")
def reject_change(model_id: str, change_id: str, body: ChangeRejectIn, db: Db = Depends(current_db)):
    from services.modeling import run_link

    return _call(db, run_link.reject_change, model_id, change_id, _user(db), body.note)


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


class PublishIn(BaseModel):
    repo_id: str = Field(min_length=1, max_length=36)
    base: Optional[str] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, max_length=200)


@router.post("/api/hub/domain-models/{model_id}/versions/{version}/publish")
def publish(model_id: str, version: int, body: PublishIn, db: Db = Depends(current_db)):
    """Open a pull request with an approved version's files (feature/model-<domain>-v<version>). The version becomes
    PUBLISHED when the pull request is merged."""
    import re

    from app.main import _dbt_repos, _github_publisher_ready

    out = _call(db, _svc().get_model, model_id, version)
    current = out.get("current") or {}
    if current.get("status") != "APPROVED":
        raise HTTPException(409, "Only an approved version is published; approve it first.")
    repo = next((r for r in _dbt_repos(db, None) if r["repo_id"] == body.repo_id), None)
    if not repo or (repo.get("provider") or "") not in ("GITHUB", "AZURE_DEVOPS"):
        raise HTTPException(409, "Choose a GitHub or Azure Repos repository connected in Admin, Integrations.")
    if not _github_publisher_ready(db):
        return {"status": "NOT_CONFIGURED", "detail": "GitHub publishing is not set up. An admin can set it up in the GitHub publishing card."}
    slug = re.sub(r"[^a-z0-9]+", "-", out["model"]["domain_name"].lower()).strip("-") or "model"
    head = f"feature/model-{slug}-v{int(version)}"
    request = {"model_id": model_id, "version": int(version), "repo_id": body.repo_id, "head": head,
               "base": body.base or repo.get("branch"), "title": body.title}
    try:
        result = db.call("CALL CORE.PUBLISH_DBT_PR(%s, %s)", ("", json.dumps({"domain_model": request})))
    except Exception as exc:
        return {"status": "FAILED", "detail": str(exc)[:1000], "head_branch": head}
    return {**(result or {}), "head_branch": head}


class SyncIn(BaseModel):
    force: bool = False


@router.post("/api/hub/domain-models/{model_id}/pr-sync")
def pr_sync(model_id: str, body: SyncIn, db: Db = Depends(current_db)):
    """Read the model's open pull requests from GitHub (merged makes the version PUBLISHED). Throttled to once a
    minute per version unless `force`."""
    from app.main import _github_publisher_ready

    if not _github_publisher_ready(db):
        return {"checked": 0, "merged": [], "closed": [], "errors": [], "detail": "GitHub publishing is not set up"}
    try:
        return db.call("CALL CORE.PUBLISH_DBT_PR(%s, %s)",
                       ("", json.dumps({"sync_model_prs": {"model_id": model_id, "force": body.force}}))) or {}
    except Exception as exc:
        raise _snowflake_error(exc) from exc


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


@router.get("/api/hub/domain-models/{model_id}/impact")
def impact(model_id: str, to: int, from_version: Optional[int] = Query(default=None, alias="from", ge=1),
           db: Db = Depends(current_db)):
    """What already uses the entities and columns a version changes in a breaking way."""
    from services.modeling import impact as impact_svc

    if from_version is None:
        approved = _call(db, _svc().get_model, model_id, to)["model"].get("approved_version")
        from_version = int(approved) if approved and int(approved) != int(to) else None
    return _call(db, impact_svc.for_versions, model_id, from_version, to)


class ImportIn(BaseModel):
    kind: Literal["ddl", "pack", "schema"]
    text: Optional[str] = Field(default=None, max_length=2_000_000)
    pack: Optional[dict] = None
    database: Optional[str] = Field(default=None, max_length=255)
    schema_name: Optional[str] = Field(default=None, alias="schema", max_length=255)
    tables: list[str] = Field(default_factory=list, max_length=500)
    replace: bool = False
    spec: dict


@router.post("/api/hub/domain-models/{model_id}/import")
def import_model(model_id: str, body: ImportIn, db: Db = Depends(current_db)):
    """The draft (`spec`, as the editor holds it) merged with DDL, a domain pack or a live schema. Nothing is saved:
    the editor shows the result as unsaved changes to review and save."""
    from services.modeling import importers, spec_v2

    try:
        if body.kind == "ddl":
            incoming = importers.parse_ddl(body.text or "")
        elif body.kind == "pack":
            if not body.pack:
                raise HTTPException(400, "attach a domain_pack.json")
            incoming = spec_v2.spec_from_pack(body.pack, model_id)
        else:
            if not (body.database and body.schema_name):
                raise HTTPException(400, "name the database and schema to read")
            incoming = invoke_source(db, importers.from_schema, body.database, body.schema_name, body.tables)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    base = spec_v2.normalize_v2(body.spec, model_id)
    merged = importers.merge(base, incoming, model_id, body.replace)
    from services.modeling import model_diff

    changes = model_diff.diff(base, merged)
    return {"spec": merged, "issues": spec_v2.validate(merged), "counts": changes["counts"],
            "entities": len(incoming.get("design", {}).get("entities") or [])}


@router.post("/api/hub/domain-models/{model_id}/import-file")
async def import_model_file(model_id: str, file: UploadFile = File(...), spec: str = Form(...), replace: bool = Form(False),
                            db: Db = Depends(current_db)):
    """A data model file merged into the draft (`spec`, as the editor holds it): an Erwin model saved as XML, DDL,
    a data dictionary or Erwin column report (.xlsx or .csv), or a domain_pack.json. Nothing is saved: the editor
    shows the result as unsaved changes to review and save."""
    from services.modeling import importers, model_diff, model_files, spec_v2

    data = await file.read(model_files.MAX_BYTES + 1)
    try:
        current = json.loads(spec)
    except ValueError:
        raise HTTPException(400, "spec is not JSON") from None
    try:
        parsed = model_files.parse_file(file.filename or "model", data, model_id)
    except model_files.ModelFileError as exc:
        raise HTTPException(400, str(exc)) from None
    if not parsed["entities"]:
        raise HTTPException(400, f"No tables were found in {file.filename}.")
    base = spec_v2.normalize_v2(current, model_id)
    merged = importers.merge(base, parsed["spec"], model_id, replace)
    changes = model_diff.diff(base, merged)
    return {"spec": merged, "issues": spec_v2.validate(merged), "counts": changes["counts"], "entities": parsed["entities"],
            "columns": parsed["columns"], "relationships": parsed["relationships"], "format": parsed["format"],
            "notes": parsed["notes"][:50], "file": file.filename}


class EvolveIn(BaseModel):
    spec: Optional[dict] = None
    instructions: str = Field(min_length=3, max_length=16000)
    save_knowledge: bool = False


def _save_knowledge_proposals(db: Db, domain_id: Optional[str], domain_name: str, proposals: list[dict]) -> int:
    if not domain_id or not proposals:
        return 0
    saved = 0
    try:
        import uuid
        from services.knowledge.manage import new_key, prepare_item
        from services.knowledge.writer import lineage_id

        for prop in proposals:
            item, problems = prepare_item(
                prop.get("knowledge_type") or "BUSINESS_RULE",
                prop.get("title") or "",
                prop.get("content") or "",
                prop.get("content_json"),
            )
            if problems:
                continue
            exists = db.query(
                "SELECT KNOWLEDGE_ID FROM CORE.DOMAIN_KNOWLEDGE WHERE DOMAIN_ID = %s AND UPPER(TITLE) = UPPER(%s) AND IS_CURRENT = TRUE LIMIT 1",
                (domain_id, item["title"]),
            )
            if exists:
                continue
            kid = str(uuid.uuid4())
            key = new_key(domain_name or "DOMAIN", item["knowledge_type"], item["title"])
            db.execute(
                """INSERT INTO CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT,
                          CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID,
                          ORIGIN, CHANGE_NOTE, LAYER)
                   SELECT %s, %s, %s, %s, %s, PARSE_JSON(NULLIF(%s, '')), PARSE_JSON(%s), %s, 'ACTIVE', 1, TRUE,
                          CURRENT_USER(), %s, 'COPILOT', 'Evolved with AI in Model Workbench', 'ANY'""",
                (
                    kid,
                    domain_id,
                    item["knowledge_type"],
                    item["title"],
                    item["content"],
                    json.dumps(item["content_json"]) if item["content_json"] is not None else "",
                    json.dumps(["COPILOT", "MODEL_WORKBENCH"]),
                    key,
                    lineage_id(domain_id, key),
                ),
            )
            saved += 1
    except Exception:
        pass
    return saved


@router.post("/api/hub/domain-models/{model_id}/evolve")
def evolve_model(model_id: str, body: EvolveIn, db: Db = Depends(current_db)):
    """Human-in-the-loop AI recommendation & evolution of a domain model (`spec`) and its domain knowledge."""
    import time
    from services.modeling import render_v2
    from services.modeling.ai_evolve import evolve_domain_model

    base_spec = body.spec
    if not base_spec or not (base_spec.get("design") or {}).get("entities"):
        loaded = _call(db, _svc().get_model, model_id, None)
        base_spec = (loaded.get("current") or {}).get("spec") or {}

    domain_id: Optional[str] = None
    domain_name: str = str(base_spec.get("domain") or "").strip().upper()
    existing_knowledge: list[dict] = []
    try:
        rows = db.query("SELECT DOMAIN_ID, DOMAIN_NAME FROM CORE.DOMAIN_MODEL WHERE MODEL_ID = %s LIMIT 1", (model_id,))
        if rows:
            domain_id = rows[0].get("domain_id") or rows[0].get("DOMAIN_ID")
            domain_name = str(rows[0].get("domain_name") or rows[0].get("DOMAIN_NAME") or domain_name).strip().upper()
        if domain_id:
            k_rows = db.query(
                "SELECT KNOWLEDGE_TYPE, TITLE, CONTENT FROM CORE.DOMAIN_KNOWLEDGE "
                "WHERE DOMAIN_ID = %s AND IS_CURRENT = TRUE AND STATUS = 'ACTIVE' ORDER BY UPDATED_AT DESC LIMIT 25",
                (domain_id,),
            )
            existing_knowledge = [
                {
                    "type": r.get("knowledge_type") or r.get("KNOWLEDGE_TYPE"),
                    "title": r.get("title") or r.get("TITLE"),
                    "content": r.get("content") or r.get("CONTENT"),
                }
                for r in k_rows
            ]
    except Exception:
        pass

    started = time.time()
    try:
        result = invoke_source(db, evolve_domain_model, base_spec, body.instructions, model_id, existing_knowledge)
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc

    try:
        from app.main import _record_cost
        _record_cost(db, None, "MODELING", result.get("model"), result.pop("usage", None), started)
    except Exception:
        result.pop("usage", None)

    saved_knowledge = (
        _save_knowledge_proposals(db, domain_id, domain_name, result.get("knowledge_proposals") or [])
        if body.save_knowledge
        else 0
    )

    ddl_preview = ""
    dbt_preview = ""
    try:
        ddl_preview = render_v2.render_ddl(result["spec"])
        dbt_files = render_v2.render_dbt(result["spec"])
        dbt_preview = "\n\n".join(f"# {path}\n{content}" for path, content in list(dbt_files.items())[:4])
    except Exception:
        pass

    return {
        "spec": result["spec"],
        "issues": result["issues"],
        "counts": result["counts"],
        "changes": result["changes"],
        "breaking": result["breaking"],
        "rationale": result["rationale"],
        "knowledge_proposals": result["knowledge_proposals"],
        "knowledge_saved": saved_knowledge,
        "ddl_preview": ddl_preview,
        "dbt_preview": dbt_preview,
    }


class EvolveWorkflowIn(BaseModel):
    spec: dict
    instructions: Optional[str] = Field(default=None, max_length=16000)
    rationale: Optional[str] = Field(default=None, max_length=4000)
    knowledge_proposals: list[dict] = Field(default_factory=list)
    save_knowledge: bool = True
    create_run: bool = True


@router.post("/api/hub/domain-models/{model_id}/evolve/workflow")
def apply_evolve_workflow(model_id: str, body: EvolveWorkflowIn, db: Db = Depends(current_db)):
    """Accept AI model & knowledge recommendations, save the evolved draft, sync to the domain target registry,
    and create/link the Design -> Build -> Validate -> Publish workflow run."""
    import uuid
    from services.modeling import spec_v2
    from services.modeling.registry_sync import apply_spec

    user = _user(db)
    svc = _svc()
    note = (body.rationale or body.instructions or "Evolved with AI").strip()[:1000]

    # 1. Ensure an editable draft is open on CORE.DOMAIN_MODEL and save the evolved spec
    detail = _call(db, svc.get_model, model_id, None)
    header = detail["model"]
    domain_id = header["domain_id"]
    domain_name = str(header["domain_name"] or "DOMAIN").strip().upper()
    open_v = detail.get("open")

    if not open_v:
        opened = _call(db, svc.open_draft, model_id, user, None, note)
        draft_version = opened["version"]
        etag = opened["etag"]
    else:
        draft_version = open_v["version"]
        if open_v["status"] == "IN_REVIEW":
            db.execute(
                "UPDATE CORE.MODEL_DESIGNS SET STATUS = 'DRAFT', UPDATED_AT = CURRENT_TIMESTAMP() WHERE MODEL_ID = %s AND VERSION = %s",
                (model_id, draft_version),
            )
        row = db.query("SELECT ETAG FROM CORE.MODEL_DESIGNS WHERE MODEL_ID = %s AND VERSION = %s", (model_id, draft_version))
        etag = (row[0].get("etag") or row[0].get("ETAG")) if row else open_v.get("etag") or ""

    normalized = spec_v2.normalize_v2(body.spec, model_id)
    saved = _call(
        db,
        svc.save_draft,
        model_id,
        json.dumps({"spec": normalized, "etag": etag, "change_note": note}, default=str),
        user,
    )

    # 2. Save extracted Domain Knowledge items to CORE.DOMAIN_KNOWLEDGE
    knowledge_saved = (
        _save_knowledge_proposals(db, domain_id, domain_name, body.knowledge_proposals)
        if body.save_knowledge
        else 0
    )

    # 3. Sync evolved entities & columns into CORE.TARGET_TABLE_REGISTRY / TARGET_COLUMN_REGISTRY so Build & Mapping see them
    registry_applied = False
    try:
        invoke_source(db, apply_spec, domain_id, normalized)
        registry_applied = True
    except Exception:
        pass

    # 4. Create or link a Design -> Build -> Validate -> Publish workflow run for this evolved model
    run_id: Optional[str] = None
    run_name: Optional[str] = None
    target_model: Optional[str] = None
    entities = (normalized.get("design") or {}).get("entities") or []
    active_entities = [e for e in entities if e.get("status") != "RETIRED"] or entities
    if active_entities:
        first_ent = active_entities[0]
        phys = normalized.get("physical_defaults") or {}
        t_db = first_ent.get("database") or phys.get("database") or "DEV_GDP_SILVER_DB"
        t_sch = first_ent.get("schema") or phys.get("schema") or domain_name
        target_model = f"{t_db}.{t_sch}.{first_ent['entity_name']}"

    if body.create_run and domain_id:
        try:
            src_rows = db.query(
                """SELECT SOURCE_SYSTEM_ID FROM CORE.WORKFLOW_RUN
                    WHERE DOMAIN_ID = %s AND SOURCE_SYSTEM_ID IS NOT NULL AND DELETED_AT IS NULL
                    ORDER BY CREATED_AT DESC LIMIT 1""",
                (domain_id,),
            )
            source_system_id = (src_rows[0].get("source_system_id") or src_rows[0].get("SOURCE_SYSTEM_ID")) if src_rows else None
            run_name = f"{domain_name} AI Model Evolution v{draft_version}"
            payload = {
                "RUN_NAME": run_name,
                "DOMAIN_ID": domain_id,
                "TARGET_MODEL": target_model,
                "ENVIRONMENT": "DEV",
            }
            if source_system_id:
                payload["SOURCE_SYSTEM_ID"] = source_system_id
            created = db.call("CALL CORE.CREATE_RUN(%s)", (json.dumps(payload),))
            run_id = (created or {}).get("run_id") or (created or {}).get("RUN_ID")
            if run_id and source_system_id:
                # Position the workflow at DOMAIN_IDENTIFIED (Stage 2: Design -> Build -> Validate -> Publish)
                db.execute(
                    "UPDATE CORE.WORKFLOW_RUN SET CURRENT_STATE = 'DOMAIN_IDENTIFIED', SOURCE_SYSTEM_ID = %s, TARGET_MODEL = %s WHERE RUN_ID = %s",
                    (source_system_id, target_model, run_id),
                )
        except Exception:
            pass

    run_qs = f"&run={run_id}" if run_id else ""
    workflow_stages = [
        {
            "key": "design",
            "step": 1,
            "label": "Design",
            "detail": f"Draft v{draft_version} saved ({len(active_entities)} entities) · Profile & map fields",
            "status": "ACTIVE",
            "href": f"/os/design?view=selected{run_qs}" if run_id else f"/os/design/modeling/{model_id}?tab=entities",
        },
        {
            "key": "build",
            "step": 2,
            "label": "Build",
            "detail": "Generate Snowflake DDL, dbt models & DQ checks",
            "status": "NEXT",
            "href": f"/os/build?view=selected{run_qs}" if run_id else f"/os/design/modeling/{model_id}?tab=publish",
        },
        {
            "key": "validate",
            "step": 3,
            "label": "Validate",
            "detail": f"{len([i for i in saved.get('issues', []) if i.get('severity') == 'ERROR'])} error(s) · Run validation & standards gate",
            "status": "NEXT",
            "href": f"/os/review?view=selected{run_qs}" if run_id else f"/os/design/modeling/{model_id}?tab=standards",
        },
        {
            "key": "publish",
            "step": 4,
            "label": "Publish",
            "detail": "Approve model, open Git PR & release",
            "status": "NEXT",
            "href": f"/os/release?view=selected{run_qs}" if run_id else f"/os/design/modeling/{model_id}?tab=publish",
        },
    ]

    return {
        "model_id": model_id,
        "domain_id": domain_id,
        "domain_name": domain_name,
        "version": draft_version,
        "etag": saved.get("etag", etag),
        "spec": normalized,
        "issues": saved.get("issues", []),
        "counts": saved.get("counts", {}),
        "breaking": saved.get("breaking", False),
        "registry_applied": registry_applied,
        "knowledge_saved": knowledge_saved,
        "run_id": run_id,
        "run_name": run_name,
        "target_model": target_model,
        "workflow_stages": workflow_stages,
    }



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
