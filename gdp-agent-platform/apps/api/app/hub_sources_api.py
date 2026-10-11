"""Source Hub (/dashboard/sources): onboard tables from a Snowflake share in one step, and register external
(non-Snowflake) sources in PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG for the ingestion tool to load.

Share onboarding creates an ordinary run and walks it through the existing source procedures (register, access,
landing), landing through SOURCE.LANDING_STAGE as compressed Parquet (services/source/staged.py), so the run then
continues on /runs/<id> exactly like any other. External sources are intake only: the platform stores the answers
(secrets sealed with services/common/secretbox.py) and never calls the ingestion tool.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _config, _ident, _put_config, _snowflake_error, _source_call, _table_ident, current_db
from services.common import secretbox
from services.source import pipelines

router = APIRouter()
CATALOG = "PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG"
ACCOUNT = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")
SHARE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,254}$")


def _form(db: Db) -> dict:
    return pipelines.form_or_default(_config(db, pipelines.FORM_KEY, None))


# ---------------------------------------------------------------- intake form


@router.get("/api/hub/intake-form")
def intake_form(db: Db = Depends(current_db)):
    stored = _config(db, pipelines.FORM_KEY, None)
    form = pipelines.form_or_default(stored)
    return {"form": form, "customized": form is not pipelines.DEFAULT_FORM,
            "problems": pipelines.check_form(stored) if isinstance(stored, dict) else []}


class FormIn(BaseModel):
    form: dict


@router.put("/api/hub/intake-form")
def save_intake_form(body: FormIn, db: Db = Depends(current_db)):
    problems = pipelines.check_form(body.form)
    if problems:
        raise HTTPException(400, "; ".join(problems[:10]))
    _put_config(db, pipelines.FORM_KEY, body.form, "Questions the Source Hub asks for an external pipeline")
    return {"saved": True, "form": body.form}


# ---------------------------------------------------------------- external pipelines


class Answers(BaseModel):
    answers: dict = Field(default_factory=dict)


def _row(r: dict) -> dict:
    for key in ("connection_metadata", "schedule"):
        value = r.get(key)
        if isinstance(value, str):
            try:
                r[key] = json.loads(value)
            except ValueError:
                pass
    return pipelines.mask(r)


SELECT = f"""SELECT SOURCE_ID, SOURCE_NAME, SOURCE_TYPE, CONNECTION_TYPE, CONNECTION_METADATA, SECRET_SEALED, SCHEDULE,
                    TARGET_LANDING_TABLE, SNAPLOGIC_PIPELINE_NAME, DOMAIN_ID, STATUS, NOTES, CREATED_BY,
                    CREATED_AT::VARCHAR AS CREATED_AT, UPDATED_BY, UPDATED_AT::VARCHAR AS UPDATED_AT
               FROM {CATALOG}"""


@router.post("/api/hub/pipelines/validate")
def validate_pipeline(body: Answers, db: Db = Depends(current_db)):
    public, secrets, errors = pipelines.validate(_form(db), body.answers)
    return {"valid": not errors, "errors": errors, "answers": public, "secret_fields": sorted(secrets)}


@router.get("/api/hub/pipelines")
def list_pipelines(status: Optional[str] = None, q: Optional[str] = None, db: Db = Depends(current_db)):
    where, params = [], []
    if status:
        where.append("STATUS = %s")
        params.append(status.upper())
    if q:
        where.append("(SOURCE_NAME ILIKE %s OR SOURCE_TYPE ILIKE %s OR TARGET_LANDING_TABLE ILIKE %s)")
        params += [f"%{q}%"] * 3
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    try:
        found = db.query(f"{SELECT} {clause} ORDER BY CREATED_AT DESC LIMIT 500", tuple(params))
    except Exception:
        return {"pipelines": [], "deployed": False}
    return {"pipelines": [_row(r) for r in found], "deployed": True}


@router.get("/api/hub/pipelines/{source_id}")
def get_pipeline(source_id: str, db: Db = Depends(current_db)):
    found = db.query(f"{SELECT} WHERE SOURCE_ID = %s", (source_id,))
    if not found:
        raise HTTPException(404, "pipeline not found")
    return {"pipeline": _row(found[0])}


def _seal(secrets: dict, source_id: str) -> Optional[bytes]:
    if not secrets:
        return None
    key = secretbox.ops_key()
    if not key:
        raise HTTPException(503, "This host has no encryption key (AIP_SECRET_KEY), so secret answers cannot be stored. "
                                 "Leave the secret fields empty or ask an admin to set the key.")
    return secretbox.seal(json.dumps(secrets), key, secretbox.INGESTION_SECRET, source_id)


@router.post("/api/hub/pipelines")
def register_pipeline(body: Answers, db: Db = Depends(current_db)):
    public, secrets, errors = pipelines.validate(_form(db), body.answers)
    if errors:
        raise HTTPException(422, {"message": "Some answers need attention", "errors": errors})
    source_id = str(uuid.uuid4())
    row = pipelines.catalog_row(source_id, _with_domain(public, body.answers))
    taken = db.query(f"SELECT SOURCE_ID FROM {CATALOG} WHERE SOURCE_NAME = %s AND STATUS <> 'RETIRED'",
                     (row["source_name"],))
    if taken:
        raise HTTPException(409, f"{row['source_name']} is already registered; change it or retire it first")
    sealed = _seal(secrets, source_id)
    try:
        db.execute(
            f"""INSERT INTO {CATALOG} (SOURCE_ID, SOURCE_NAME, SOURCE_TYPE, CONNECTION_TYPE, CONNECTION_METADATA,
                                       SECRET_SEALED, SCHEDULE, TARGET_LANDING_TABLE, SNAPLOGIC_PIPELINE_NAME,
                                       DOMAIN_ID, STATUS, NOTES)
                SELECT %s, %s, %s, %s, PARSE_JSON(%s), %s, PARSE_JSON(%s), %s, %s, %s, %s, %s""",
            (source_id, row["source_name"], row["source_type"], row["connection_type"],
             json.dumps(row["connection_metadata"]), sealed, json.dumps(row["schedule"]) if row["schedule"] else None,
             row["target_landing_table"], row["snaplogic_pipeline_name"], row["domain_id"], row["status"], row["notes"]),
        )
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"source_id": source_id, "pipeline": {**pipelines.mask(row), "has_secret": sealed is not None}}


class PipelineUpdate(BaseModel):
    answers: Optional[dict] = None
    status: Optional[Literal["REGISTERED", "ACTIVE", "PAUSED", "RETIRED"]] = None


@router.patch("/api/hub/pipelines/{source_id}")
def update_pipeline(source_id: str, body: PipelineUpdate, db: Db = Depends(current_db)):
    found = db.query(f"SELECT SOURCE_ID, STATUS, SECRET_SEALED FROM {CATALOG} WHERE SOURCE_ID = %s", (source_id,))
    if not found:
        raise HTTPException(404, "pipeline not found")
    status = body.status or found[0]["status"]
    if body.answers is None:
        db.execute(f"UPDATE {CATALOG} SET STATUS = %s, UPDATED_BY = CURRENT_USER(), UPDATED_AT = CURRENT_TIMESTAMP() "
                   "WHERE SOURCE_ID = %s", (status, source_id))
        return {"source_id": source_id, "status": status}
    public, secrets, errors = pipelines.validate(_form(db), body.answers)
    # a secret left empty on edit keeps the one on record (it is never sent back to the browser)
    errors = {k: v for k, v in errors.items() if not (k in _secret_keys(db) and found[0].get("secret_sealed"))}
    if errors:
        raise HTTPException(422, {"message": "Some answers need attention", "errors": errors})
    row = pipelines.catalog_row(source_id, _with_domain(public, body.answers), status)
    sealed = _seal(secrets, source_id) if secrets else found[0].get("secret_sealed")
    db.execute(
        f"""UPDATE {CATALOG} SET SOURCE_NAME = %s, SOURCE_TYPE = %s, CONNECTION_TYPE = %s,
                   CONNECTION_METADATA = PARSE_JSON(%s), SECRET_SEALED = %s, SCHEDULE = PARSE_JSON(%s),
                   TARGET_LANDING_TABLE = %s, SNAPLOGIC_PIPELINE_NAME = %s, DOMAIN_ID = %s, STATUS = %s, NOTES = %s,
                   UPDATED_BY = CURRENT_USER(), UPDATED_AT = CURRENT_TIMESTAMP()
             WHERE SOURCE_ID = %s""",
        (row["source_name"], row["source_type"], row["connection_type"], json.dumps(row["connection_metadata"]),
         sealed, json.dumps(row["schedule"]) if row["schedule"] else None, row["target_landing_table"],
         row["snaplogic_pipeline_name"], row["domain_id"], status, row["notes"], source_id),
    )
    return {"source_id": source_id, "pipeline": {**pipelines.mask(row), "has_secret": bool(sealed)}}


def _with_domain(public: dict, answers: dict) -> dict:
    """The domain is picked beside the intake questions (not one of them), so it is carried over here."""
    domain_id = str((answers or {}).get("domain_id") or "").strip()
    return {**public, "domain_id": domain_id[:36]} if domain_id else public


def _secret_keys(db: Db) -> set:
    return {f["key"] for f in pipelines.fields(_form(db)) if f.get("secret") or f.get("type") == "password"}


# ---------------------------------------------------------------- Snowflake shares


@router.get("/api/hub/sources/shares")
def list_shares(db: Db = Depends(current_db)):
    """Inbound shares the role can see, and the databases already mounted from a share."""
    shares: list[dict] = []
    try:
        for r in db.query("SHOW SHARES"):
            if str(r.get("kind") or "").upper() == "INBOUND":
                shares.append({"name": r.get("name"), "owner_account": r.get("owner_account"),
                               "database_name": r.get("database_name") or None, "comment": r.get("comment")})
    except Exception:
        pass
    try:
        mounted = [r["database_name"] for r in db.query(
            "SELECT DATABASE_NAME FROM INFORMATION_SCHEMA.DATABASES WHERE TYPE = 'IMPORTED DATABASE' ORDER BY 1")]
    except Exception:
        mounted = []
    return {"shares": shares, "mounted": mounted}


class ShareTarget(BaseModel):
    database: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(min_length=1, max_length=255, alias="schema")


def _share_tables(db: Db, database: str, schema: str) -> list[dict]:
    from services.source.identifiers import quote

    kind = db.query("SELECT TYPE FROM INFORMATION_SCHEMA.DATABASES WHERE DATABASE_NAME = %s", (database,))
    if not kind:
        raise HTTPException(404, f"{database} is not visible to your role. Mount the share first or ask an admin "
                                 "for IMPORTED PRIVILEGES.")
    if kind[0]["type"] != "IMPORTED DATABASE":
        raise HTTPException(400, f"{database} is a regular database, not a mounted share. Use Sources to onboard it.")
    return db.query(f"""SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, BYTES, LAST_ALTERED::VARCHAR AS LAST_ALTERED
                          FROM {quote(database)}.INFORMATION_SCHEMA.TABLES
                         WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME""", (schema,))


@router.post("/api/hub/sources/share/preview")
def preview_share(body: ShareTarget, db: Db = Depends(current_db)):
    database, schema = _ident(body.database, "database"), _ident(body.schema_name, "schema")
    return {"database": database, "schema": schema, "tables": _share_tables(db, database, schema)}


class ShareOnboard(ShareTarget):
    provider_account: Optional[str] = Field(default=None, max_length=255)
    share_name: Optional[str] = Field(default=None, max_length=255)
    mount: bool = False
    tables: list[str] = Field(min_length=1, max_length=500)
    source_system_name: str = Field(min_length=1, max_length=64)
    run_name: Optional[str] = Field(default=None, max_length=256)
    domain_id: Optional[str] = None
    landing_database: Optional[str] = Field(default=None, max_length=255)
    landing_schema: Optional[str] = Field(default=None, max_length=255)


def mount_sql(database: str, provider_account: str, share_name: str) -> str:
    from services.source.identifiers import quote

    assert ACCOUNT.match(provider_account or ""), "provider account must be an account locator or ORG.ACCOUNT name"
    assert SHARE.match(share_name or ""), "share name must be a Snowflake identifier"
    return f"CREATE DATABASE IF NOT EXISTS {quote(database)} FROM SHARE {provider_account}.{share_name}"


@router.post("/api/hub/sources/share/onboard")
def onboard_share(body: ShareOnboard, db: Db = Depends(current_db)):
    """Create a run for the share and take it through register, access and staged landing. Each step's outcome is
    returned; a failure leaves the run where it stopped, to continue on /runs/<id>/source."""
    from services.source.procedures import execute_staged_landing, register_source, validate_source_access

    database, schema = _ident(body.database, "database"), _ident(body.schema_name, "schema")
    tables = sorted({_table_ident(t) for t in body.tables})
    steps: list[dict] = []
    if body.mount:
        if not (body.provider_account and body.share_name):
            raise HTTPException(400, "Mounting needs the provider account and the share name")
        try:
            db.execute(mount_sql(database, body.provider_account, body.share_name))
            steps.append({"step": "mount", "ok": True, "detail": f"{database} mounted from {body.share_name}"})
        except AssertionError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(403, f"Could not mount the share: {str(exc)[:300]}. Mounting needs the IMPORT SHARE "
                                     "privilege; an admin can mount it once and you can pick the database here.") from exc
    available = {t["table_name"] for t in _share_tables(db, database, schema)}
    missing = [t for t in tables if t not in available]
    if missing:
        raise HTTPException(400, f"Not in {database}.{schema}: {', '.join(missing[:10])}")

    payload = {"RUN_NAME": body.run_name or f"Share {database}.{schema}", "DOMAIN_ID": body.domain_id,
               "ENVIRONMENT": "DEV"}
    try:
        created = db.call("CALL CORE.CREATE_RUN(%s)", (json.dumps({k: v for k, v in payload.items() if v}),))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    run_id = (created or {}).get("run_id") or (created or {}).get("RUN_ID")
    steps.append({"step": "run", "ok": bool(run_id), "detail": run_id})
    register = {"SOURCE_SYSTEM_NAME": body.source_system_name, "SOURCE_TYPE": "SNOWFLAKE_SHARE", "DATABASE": database,
                "SCHEMA": schema, "DOMAIN_ID": body.domain_id, "LANDING_DATABASE": body.landing_database,
                "LANDING_SCHEMA": body.landing_schema, "STORAGE_TYPE": "MANAGED"}
    plan = [
        ("register", "CALL SOURCE.REGISTER_SOURCE(%s, %s)", register_source,
         (run_id, json.dumps({k: v for k, v in register.items() if v}))),
        ("access", "CALL SOURCE.VALIDATE_SOURCE_ACCESS(%s, %s)", validate_source_access, (run_id, json.dumps(tables))),
        ("landing", "CALL SOURCE.EXECUTE_STAGED_LANDING(%s)", execute_staged_landing, (run_id,)),
    ]
    result: Any = None
    for name, proc, handler, args in plan:
        try:
            result = _source_call(db, proc, handler, *args)
        except Exception as exc:
            steps.append({"step": name, "ok": False, "detail": str(exc)[:600]})
            return {"run_id": run_id, "ok": False, "steps": steps, "continue_at": f"/runs/{run_id}/source"}
        ok = _step_ok(name, result)
        steps.append({"step": name, "ok": ok, "detail": _step_detail(name, result)})
        if not ok:
            return {"run_id": run_id, "ok": False, "steps": steps, "continue_at": f"/runs/{run_id}/source"}
    return {"run_id": run_id, "ok": True, "steps": steps, "tables": (result or {}).get("tables", []),
            "continue_at": f"/runs/{run_id}/profile"}


def _step_ok(name: str, result: Any) -> bool:
    if not isinstance(result, dict):
        return result is not None
    if name == "access":
        return bool(result.get("passed", True))
    if name == "landing":
        return all(t.get("status") == "COMPLETE" for t in result.get("tables") or []) and bool(result.get("tables"))
    return True


def _step_detail(name: str, result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    if name == "register":
        return f"{result.get('objects_discovered', 0)} objects discovered"
    if name == "access":
        failed = [c for c in result.get("checks") or [] if c.get("status") == "FAILED"]
        return "; ".join(str(c.get("detail")) for c in failed[:3]) or "access confirmed"
    if name == "landing":
        tables = result.get("tables") or []
        bad = [t for t in tables if t.get("status") != "COMPLETE"]
        if bad:
            return "; ".join(f"{t.get('object')}: {t.get('error')}" for t in bad[:3])
        return f"{len(tables)} tables landed through the staged Parquet path"
    return ""
