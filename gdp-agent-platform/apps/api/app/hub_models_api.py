"""Modeling hub (/dashboard/modeling): design a model by mapping onto, forking or creating a domain, see it as an ER
diagram, render its domain pack, DDL and dbt files, and publish it to a feature branch with a draft pull request.

Designs are versioned in GOVERNANCE.MODEL_DESIGNS (V036). Finalizing renders and stores the files; publishing calls
CODEGEN.PUBLISH_DBT_PR with a model_branch request (services/dbt/publish.py publish_model_branch), which reads the
files back from the table, so only finalized content is ever pushed. The GitHub token never leaves Snowflake.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _dbt_repos, _github_publisher_ready, _json, _record_cost, _snowflake_error, current_db
from app.source_invoke import invoke_source
from services.catalog import models as cat_models

router = APIRouter()


def _domains(db: Db) -> list[dict]:
    try:
        return db.query("SELECT DOMAIN_ID, DOMAIN_NAME, DESCRIPTION FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG "
                        "ORDER BY DOMAIN_NAME")
    except Exception:
        return []


def _domain_names(db: Db) -> list[str]:
    return [str(d["domain_name"]).upper() for d in _domains(db)]


def _source_index(db: Db, tables: list[str]) -> Optional[dict]:
    """{TABLE: {COLUMN: type}} of the chosen source tables from the profile catalog; None when unknown."""
    if not tables:
        return None
    names = sorted({t.split(".")[-1].strip('"').upper() for t in tables})
    try:
        found = db.query("""SELECT UPPER(TABLE_NAME) AS T, UPPER(COLUMN_NAME) AS C, MAX(DATA_TYPE) AS D
                              FROM GOVERNANCE.DATA_PROFILES
                             WHERE ARRAY_CONTAINS(UPPER(TABLE_NAME)::VARIANT, PARSE_JSON(%s)::ARRAY) GROUP BY 1, 2""",
                         (json.dumps(names),))
    except Exception:
        return None
    if not found:
        return None
    out: dict = {}
    for r in found:
        out.setdefault(r["t"], {})[r["c"]] = r["d"]
    return out


@router.get("/api/hub/models/context")
def context(db: Db = Depends(current_db)):
    try:
        designs = db.query("""SELECT DESIGN_ID, VERSION, DOMAIN_NAME, MODE, PARENT_DOMAIN, STATUS, BRANCH, PR_URL,
                                     CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT
                                FROM GOVERNANCE.MODEL_DESIGNS
                             QUALIFY ROW_NUMBER() OVER (PARTITION BY DESIGN_ID ORDER BY VERSION DESC) = 1
                               ORDER BY CREATED_AT DESC LIMIT 50""")
        deployed = True
    except Exception:
        designs, deployed = [], False
    repos = [{k: r.get(k) for k in ("repo_id", "name", "git_url", "provider", "branch", "dbt_project_dir")}
             for r in _dbt_repos(db, None)]
    return {"domains": _domains(db), "designs": designs, "repos": repos, "deployed": deployed,
            "publisher_ready": _github_publisher_ready(db), "modes": list(cat_models.MODES),
            "platforms": list(cat_models.PLATFORMS)}


@router.get("/api/hub/models/domains/{domain_id}")
def domain_start(domain_id: str, mode: Literal["MAP", "FORK"] = "MAP", new_name: Optional[str] = None,
                 db: Db = Depends(current_db)):
    """An existing domain as a starting specification (MAP keeps its name; FORK takes new_name)."""
    from services.knowledge.packs import export_pack

    try:
        pack = export_pack(lambda sql, params: db.query(sql, params), domain_id)
    except AssertionError as exc:
        raise HTTPException(404, str(exc)) from exc
    parent = str(pack["domain"]["name"]).upper()
    name = parent if mode == "MAP" else (new_name or f"{parent}_EXT").upper()
    return {"spec": cat_models.spec_from_pack(pack, mode, name, parent), "base_pack": pack}


class ProposeIn(BaseModel):
    mode: Literal["MAP", "FORK", "NEW"] = "NEW"
    domain: str = Field(default="", max_length=64)
    parent_domain: Optional[str] = Field(default=None, max_length=64)
    source_tables: list[str] = Field(min_length=1, max_length=30)
    instructions: Optional[str] = Field(default=None, max_length=2000)
    use_ai: bool = False
    target_platform: Literal["SNOWFLAKE", "ICEBERG"] = "SNOWFLAKE"
    target_database: Optional[str] = Field(default=None, max_length=255)
    target_schema: Optional[str] = Field(default=None, max_length=255)


@router.post("/api/hub/models/propose")
def propose(body: ProposeIn, db: Db = Depends(current_db)):
    from services.catalog.models_ai import propose as propose_handler

    started = time.time()
    try:
        result = invoke_source(db, propose_handler, json.dumps(body.model_dump()))
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    if result.get("model"):
        _record_cost(db, None, "MODELING", result.get("model"), result.pop("usage", None), started)
    spec = result["spec"]
    issues = cat_models.validate_spec(spec, _domain_names(db), _source_index(db, spec["source_tables"]))
    return {**result, "issues": issues}


class SpecIn(BaseModel):
    spec: dict
    base_pack: Optional[dict] = None


def _checked(db: Db, raw: dict) -> tuple[dict, list]:
    spec = cat_models.normalize_spec(raw)
    return spec, cat_models.validate_spec(spec, _domain_names(db), _source_index(db, spec["source_tables"]))


def _base_pack(db: Db, spec: dict, given: Optional[dict]) -> Optional[dict]:
    if spec["mode"] == "NEW":
        return None
    if given:
        return given
    from services.knowledge.packs import export_pack

    name = spec["domain"] if spec["mode"] == "MAP" else spec.get("parent_domain")
    match = next((d for d in _domains(db) if str(d["domain_name"]).upper() == str(name or "").upper()), None)
    if not match:
        return None
    try:
        return export_pack(lambda sql, params: db.query(sql, params), match["domain_id"])
    except Exception:
        return None


@router.post("/api/hub/models/validate")
def validate(body: SpecIn, db: Db = Depends(current_db)):
    spec, issues = _checked(db, body.spec)
    entities, columns, rels = cat_models.summary(spec)
    return {"spec": spec, "issues": issues, "blocking": len(cat_models.blocking(issues)),
            "summary": {"entities": entities, "columns": columns, "relationships": rels}}


@router.post("/api/hub/models/render")
def render(body: SpecIn, project_dir: str = "", db: Db = Depends(current_db)):
    spec, issues = _checked(db, body.spec)
    pack = cat_models.render_pack(spec, _base_pack(db, spec, body.base_pack))
    return {"spec": spec, "issues": issues, "ddl": cat_models.render_ddl(spec), "domain_pack": pack,
            "pack_problems": cat_models.pack_problems(pack), "dbt": cat_models.render_dbt(spec, project_dir),
            "branch": cat_models.branch_name(spec["domain"])}


class SaveIn(SpecIn):
    design_id: Optional[str] = None


def _next_version(db: Db, design_id: str) -> int:
    found = db.query("SELECT COALESCE(MAX(VERSION), 0) AS V FROM GOVERNANCE.MODEL_DESIGNS WHERE DESIGN_ID = %s",
                     (design_id,))
    return int(found[0]["v"] or 0) + 1 if found else 1


def _insert(db: Db, design_id: str, version: int, spec: dict, issues: list, status: str,
            pack: Optional[dict] = None, files: Optional[dict] = None, repo_id: Optional[str] = None) -> None:
    db.execute(
        """INSERT INTO GOVERNANCE.MODEL_DESIGNS (DESIGN_ID, VERSION, DOMAIN_NAME, MODE, PARENT_DOMAIN, STATUS, SPEC,
                                                ISSUES, DOMAIN_PACK, ARTIFACTS, REPO_ID, SOURCE_TABLES)
           SELECT %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), PARSE_JSON(%s), PARSE_JSON(%s), PARSE_JSON(%s), %s,
                  PARSE_JSON(%s)::ARRAY""",
        (design_id, version, spec["domain"], spec["mode"], spec.get("parent_domain"), status, json.dumps(spec),
         json.dumps(issues), json.dumps(pack) if pack else None, json.dumps(files) if files else None, repo_id,
         json.dumps(spec.get("source_tables") or [])),
    )


@router.post("/api/hub/models")
def save(body: SaveIn, db: Db = Depends(current_db)):
    """Save the specification as a new DRAFT version (a new design when design_id is empty)."""
    spec, issues = _checked(db, body.spec)
    if not spec["domain"]:
        raise HTTPException(400, "name the domain first")
    design_id = body.design_id or str(uuid.uuid4())
    version = _next_version(db, design_id)
    try:
        _insert(db, design_id, version, spec, issues, "DRAFT")
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"design_id": design_id, "version": version, "issues": issues}


@router.get("/api/hub/models/{design_id}")
def get_design(design_id: str, version: Optional[int] = None, db: Db = Depends(current_db)):
    sql = """SELECT DESIGN_ID, VERSION, DOMAIN_NAME, MODE, PARENT_DOMAIN, STATUS, SPEC, ISSUES, DOMAIN_PACK, ARTIFACTS,
                    REPO_ID, BRANCH, PR_URL, PUBLISH_RESULT, CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT
               FROM GOVERNANCE.MODEL_DESIGNS WHERE DESIGN_ID = %s"""
    params: tuple = (design_id,)
    if version:
        sql += " AND VERSION = %s"
        params += (version,)
    found = db.query(sql + " ORDER BY VERSION DESC LIMIT 1", params)
    if not found:
        raise HTTPException(404, "model design not found")
    row = found[0]
    for key in ("spec", "issues", "domain_pack", "artifacts", "publish_result"):
        row[key] = _json(row.get(key))
    versions = db.query("""SELECT VERSION, STATUS, BRANCH, PR_URL, CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT
                             FROM GOVERNANCE.MODEL_DESIGNS WHERE DESIGN_ID = %s ORDER BY VERSION DESC""", (design_id,))
    return {"design": row, "versions": versions}


class FinalizeIn(SpecIn):
    repo_id: Optional[str] = None


@router.post("/api/hub/models/{design_id}/finalize")
def finalize(design_id: str, body: FinalizeIn, db: Db = Depends(current_db)):
    """Freeze a version: no blocking issues, a valid domain pack, files rendered and stored for publishing."""
    spec, issues = _checked(db, body.spec)
    blocking = cat_models.blocking(issues)
    if blocking:
        raise HTTPException(422, {"message": "Fix the blocking issues before finalizing", "issues": blocking})
    pack = cat_models.render_pack(spec, _base_pack(db, spec, body.base_pack))
    problems = cat_models.pack_problems(pack)
    if problems:
        raise HTTPException(422, {"message": "The rendered domain pack is invalid", "issues": problems})
    repo = next((r for r in _dbt_repos(db, None) if r["repo_id"] == body.repo_id), None) if body.repo_id else None
    files = cat_models.artifacts(spec, pack, (repo or {}).get("dbt_project_dir") or "")
    version = _next_version(db, design_id)
    try:
        _insert(db, design_id, version, spec, issues, "FINALIZED", pack, files, body.repo_id)
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"design_id": design_id, "version": version, "files": sorted(files), "branch": cat_models.branch_name(spec["domain"])}


class PublishIn(BaseModel):
    version: int = Field(ge=1)
    repo_id: str
    base: Optional[str] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, max_length=200)


@router.post("/api/hub/models/{design_id}/publish")
def publish(design_id: str, body: PublishIn, db: Db = Depends(current_db)):
    """Cut feature/model-<domain>-<timestamp>, commit the finalized files and open a draft pull request."""
    found = db.query("SELECT DOMAIN_NAME, STATUS FROM GOVERNANCE.MODEL_DESIGNS WHERE DESIGN_ID = %s AND VERSION = %s",
                     (design_id, body.version))
    if not found:
        raise HTTPException(404, "model design version not found")
    if found[0]["status"] not in ("FINALIZED", "PUBLISH_FAILED"):
        raise HTTPException(409, "finalize the design before publishing it")
    repo = next((r for r in _dbt_repos(db, None) if r["repo_id"] == body.repo_id), None)
    if not repo:
        raise HTTPException(409, "That repository is not connected for dbt. Connect one in Admin, Integrations.")
    if (repo.get("provider") or "") != "GITHUB":
        raise HTTPException(409, f"{repo['name']} is not on GitHub; publishing models opens GitHub pull requests only.")
    if not _github_publisher_ready(db):
        return {"status": "NOT_CONFIGURED",
                "detail": "GitHub publishing is not set up. An admin can set it up in the GitHub publishing card."}
    head = cat_models.branch_name(found[0]["domain_name"])
    request = {"design_id": design_id, "version": body.version, "repo_id": body.repo_id, "head": head,
               "base": body.base or repo.get("branch"), "title": body.title}
    try:
        result = db.call("CALL CODEGEN.PUBLISH_DBT_PR(%s, %s)", ("", json.dumps({"model_branch": request})))
    except Exception as exc:
        return {"status": "FAILED", "detail": str(exc)[:1000], "head_branch": head}
    return {**(result or {}), "head_branch": head}
