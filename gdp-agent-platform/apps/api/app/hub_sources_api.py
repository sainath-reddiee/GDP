"""Source Hub (/dashboard/sources): onboard tables from a Snowflake share in one step, and register external
(non-Snowflake) sources in CORE.INGESTION_PIPELINE_CATALOG for the ingestion tool to load.

Share onboarding creates an ordinary run and walks it through the existing source procedures (register, access,
landing), landing through CORE.LANDING_STAGE as compressed Parquet (services/source/staged.py), so the run then
continues on /runs/<id> exactly like any other. External sources are intake only: the platform stores the answers
(secrets sealed with services/common/secretbox.py) and never calls the ingestion tool. SnapLogic onboarding writes one
row per table to CORE.INGESTION_CONTROL_CONFIG (V046), which the SnapLogic parent and child pipelines read.
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
from services.source import ingestion_control, pipelines

router = APIRouter()
CATALOG = "CORE.INGESTION_PIPELINE_CATALOG"
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


# ---------------------------------------------------------------- SnapLogic ingestion control (V046)


class ControlIn(BaseModel):
    source: dict = Field(default_factory=dict)
    target: dict = Field(default_factory=dict)
    load: dict = Field(default_factory=dict)
    snaplogic: dict = Field(default_factory=dict)
    tables: list[dict] = Field(default_factory=list, max_length=200)
    comments: Optional[str] = Field(default=None, max_length=4000)


CONTROL_SELECT = f"""SELECT C.*, C.CREATED_AT::VARCHAR AS CREATED_AT_TEXT, C.UPDATED_AT::VARCHAR AS UPDATED_AT_TEXT,
                            C.LAST_RUN_TIMESTAMP::VARCHAR AS LAST_RUN_TEXT, P.SOURCE_NAME, P.SOURCE_TYPE, D.DOMAIN_NAME
                       FROM {ingestion_control.TABLE} C
                       LEFT JOIN {CATALOG} P ON P.SOURCE_ID = C.SOURCE_ID
                       LEFT JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = C.DOMAIN_ID"""


def _control(r: dict) -> dict:
    out = {k: v for k, v in r.items() if not k.endswith("_text")}
    for k in ("created_at", "updated_at", "last_run_timestamp"):
        out[k] = r.get({"created_at": "created_at_text", "updated_at": "updated_at_text",
                        "last_run_timestamp": "last_run_text"}[k])
    out["target_previous_load_time"] = str(r["target_previous_load_time"])[:19] if r.get("target_previous_load_time") else None
    for k in ("master_id", "child_id"):
        out[k] = int(out[k]) if out.get(k) is not None else None
    return out


def _history(db: Db, master_id: int, child_id: int, action: str, row: dict) -> None:
    db.execute(f"""INSERT INTO {ingestion_control.HISTORY} (MASTER_ID, CHILD_ID, ACTION, IS_ACTIVE, CHANGED_BY, SNAPSHOT)
                   SELECT %s, %s, %s, %s, CURRENT_USER(), PARSE_JSON(%s)""",
               (int(master_id), int(child_id), action, bool(row.get("is_active")), json.dumps(row, default=str)))


@router.post("/api/hub/ingestion-control/validate")
def validate_control(body: ControlIn):
    header, rows, errors = ingestion_control.validate(body.model_dump())
    return {"valid": not errors, "errors": errors, "rows": rows, "source": header}


@router.post("/api/hub/ingestion-control")
def create_control(body: ControlIn, db: Db = Depends(current_db)):
    """Onboard an external source for SnapLogic: one catalog row for the source and one control row per table under
    one MASTER_ID, with a history row each, all in one transaction. Nothing is sent to SnapLogic; it reads the table."""
    from app.db import transaction

    header, rows, errors = ingestion_control.validate(body.model_dump())
    if errors:
        raise HTTPException(422, {"message": "Some answers need attention", "errors": errors})
    try:
        clash = db.query(f"""SELECT LANDING_TABLE FROM {ingestion_control.TABLE}
                              WHERE IS_ACTIVE AND PARENT_PIPELINE_NAME = %s AND LANDING_DATABASE = %s AND LANDING_SCHEMA = %s
                                AND LANDING_TABLE IN ({", ".join(["%s"] * len(rows))})""",
                         (header["parent_pipeline_name"], header["landing_database"], header["landing_schema"],
                          *[r["landing_table"] for r in rows]))
    except Exception as exc:
        raise HTTPException(409, "The ingestion control table is not deployed yet (migration V046).") from exc
    if clash:
        names = ", ".join(sorted({c["landing_table"] for c in clash}))
        raise HTTPException(409, f"{header['parent_pipeline_name']} already loads {names}; deactivate those rows or choose other landing tables")
    who = (db.query("SELECT CURRENT_USER() AS U") or [{"u": "UNKNOWN"}])[0]["u"]
    source_id = str(uuid.uuid4())
    try:
        with transaction(db):
            schedule = {"frequency": header["load_frequency"], **({"cron": header["job_trigger_time"]} if header["job_trigger_time"] else {})}
            db.execute(f"""INSERT INTO {CATALOG} (SOURCE_ID, SOURCE_NAME, SOURCE_TYPE, CONNECTION_TYPE, CONNECTION_METADATA,
                                                  SCHEDULE, TARGET_LANDING_TABLE, SNAPLOGIC_PIPELINE_NAME, DOMAIN_ID, STATUS, NOTES)
                           SELECT %s, %s, %s, NULL, PARSE_JSON(%s), PARSE_JSON(%s), %s, %s, %s, %s, %s""",
                       (source_id, header["source_name"], header["source_type"],
                        json.dumps({"tables": len(rows), "control": "CORE.INGESTION_CONTROL_CONFIG"}),
                        json.dumps(schedule), f"{header['landing_database']}.{header['landing_schema']}.{rows[0]['landing_table']}",
                        header["parent_pipeline_name"], header["domain_id"],
                        "ACTIVE" if header["is_active"] else "REGISTERED", header["comments"]))
            master_id = int(db.query("SELECT CORE.SEQ_INGESTION_MASTER_ID.NEXTVAL AS N")[0]["n"])
            db.execute(ingestion_control.insert_sql(len(rows)),
                       tuple(ingestion_control.insert_params(master_id, source_id, header, rows, who)))
            children = db.query(f"SELECT CHILD_ID, LANDING_TABLE FROM {ingestion_control.TABLE} WHERE MASTER_ID = %s ORDER BY CHILD_ID",
                                (master_id,))
            by_table = {r["landing_table"]: r for r in rows}
            for c in children:
                _history(db, master_id, int(c["child_id"]), "CREATED", by_table.get(c["landing_table"], {}))
    except HTTPException:
        raise
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"master_id": master_id, "source_id": source_id, "source_name": header["source_name"],
            "parent_pipeline_name": header["parent_pipeline_name"],
            "children": [{"child_id": int(c["child_id"]), "landing_table": c["landing_table"]} for c in children]}


@router.get("/api/hub/ingestion-control")
def list_controls(source_id: Optional[str] = None, q: Optional[str] = None, active: Optional[bool] = None,
                  db: Db = Depends(current_db)):
    where, params = [], []
    if source_id:
        where.append("C.SOURCE_ID = %s")
        params.append(source_id)
    if active is not None:
        where.append("C.IS_ACTIVE = %s")
        params.append(bool(active))
    if q and q.strip():
        like = f"%{q.strip()[:100]}%"
        where.append("(C.PARENT_PIPELINE_NAME ILIKE %s OR C.LANDING_TABLE ILIKE %s OR P.SOURCE_NAME ILIKE %s OR C.SOURCE_TABLE_SQL ILIKE %s)")
        params += [like] * 4
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    try:
        found = db.query(f"{CONTROL_SELECT} {clause} ORDER BY C.MASTER_ID DESC, C.CHILD_ID LIMIT 1000", tuple(params))
    except Exception:
        return {"controls": [], "deployed": False}
    return {"controls": [_control(r) for r in found], "deployed": True}


@router.get("/api/hub/ingestion-control/{master_id}")
def get_control(master_id: int, db: Db = Depends(current_db)):
    found = db.query(f"{CONTROL_SELECT} WHERE C.MASTER_ID = %s ORDER BY C.CHILD_ID", (int(master_id),))
    if not found:
        raise HTTPException(404, "ingestion configuration not found")
    history = db.query(f"""SELECT CHILD_ID, ACTION, IS_ACTIVE, CHANGED_BY, CHANGED_AT::VARCHAR AS CHANGED_AT
                             FROM {ingestion_control.HISTORY} WHERE MASTER_ID = %s ORDER BY CHANGED_AT DESC LIMIT 500""",
                       (int(master_id),))
    return {"master_id": int(master_id), "controls": [_control(r) for r in found],
            "history": [{**h, "child_id": int(h["child_id"])} for h in history]}


class ControlEdit(BaseModel):
    changes: dict = Field(default_factory=dict)


@router.patch("/api/hub/ingestion-control/{master_id}/{child_id}")
def edit_control(master_id: int, child_id: int, body: ControlEdit, db: Db = Depends(current_db)):
    """Edit a table's control row or switch it on or off; the change is validated as the wizard would and kept in the
    history."""
    from app.db import transaction

    found = db.query(f"SELECT * FROM {ingestion_control.TABLE} WHERE MASTER_ID = %s AND CHILD_ID = %s", (int(master_id), int(child_id)))
    if not found:
        raise HTTPException(404, "ingestion configuration not found")
    current = found[0]
    row, errors = ingestion_control.edit_row(current, body.changes)
    if errors:
        raise HTTPException(422, {"message": "Some answers need attention", "errors": errors})
    fields = [c for c in (*ingestion_control.EDITABLE, "is_active") if c in body.changes]
    if not fields:
        raise HTTPException(400, "nothing to change")
    action = ingestion_control.action_for(current, row)
    try:
        with transaction(db):
            db.execute(f"""UPDATE {ingestion_control.TABLE} SET {", ".join(f"{c.upper()} = %s" for c in fields)},
                                  UPDATED_BY = CURRENT_USER(), UPDATED_AT = CURRENT_TIMESTAMP()
                            WHERE MASTER_ID = %s AND CHILD_ID = %s""",
                       (*[row.get(c) for c in fields], int(master_id), int(child_id)))
            _history(db, master_id, child_id, action, {**{k: current.get(k) for k in ingestion_control.COLUMNS}, **{c: row.get(c) for c in fields}})
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"master_id": int(master_id), "child_id": int(child_id), "action": action, "is_active": bool(row.get("is_active")),
            "changed": fields}


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

class ShareMount(BaseModel):
    provider_account: str = Field(min_length=1, max_length=255)
    share_name: str = Field(min_length=1, max_length=255)
    database: str = Field(min_length=1, max_length=255)


@router.post("/api/hub/sources/share/mount")
def mount_share(body: ShareMount, db: Db = Depends(current_db)):
    """Create the database from an inbound share now (IMPORT SHARE), so its schemas and tables can be picked."""
    database = _ident(body.database, "database")
    try:
        db.execute(mount_sql(database, body.provider_account, body.share_name))
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(403, f"Could not mount the share: {str(exc)[:300]}. Mounting needs the IMPORT SHARE privilege; "
                                 "an admin can mount it once and you can pick the database here.") from exc
    return {"database": database, "schemas": _share_schemas(db, database)}


def _share_schemas(db: Db, database: str) -> list[dict]:
    from services.source.identifiers import quote

    return db.query(f"""SELECT TABLE_SCHEMA AS SCHEMA_NAME, COUNT(*) AS TABLES, SUM(ROW_COUNT) AS ROW_COUNT
                          FROM {quote(database)}.INFORMATION_SCHEMA.TABLES
                         WHERE TABLE_SCHEMA <> 'INFORMATION_SCHEMA'
                         GROUP BY 1 ORDER BY 1""")


@router.get("/api/hub/sources/share/schemas")
def share_schemas(database: str, db: Db = Depends(current_db)):
    """The schemas of a mounted share with their table counts, for picking what to onboard."""
    database = _ident(database, "database")
    kind = db.query("SELECT TYPE FROM INFORMATION_SCHEMA.DATABASES WHERE DATABASE_NAME = %s", (database,))
    if not kind:
        raise HTTPException(404, f"{database} is not visible to your role. Mount the share first or ask an admin "
                                 "for IMPORTED PRIVILEGES.")
    try:
        return {"database": database, "schemas": _share_schemas(db, database)}
    except Exception as exc:
        raise _snowflake_error(exc) from exc



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
        ("register", "CALL CORE.REGISTER_SOURCE(%s, %s)", register_source,
         (run_id, json.dumps({k: v for k, v in register.items() if v}))),
        ("access", "CALL CORE.VALIDATE_SOURCE_ACCESS(%s, %s)", validate_source_access, (run_id, json.dumps(tables))),
        ("landing", "CALL CORE.EXECUTE_STAGED_LANDING(%s)", execute_staged_landing, (run_id,)),
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
