"""Profiling and STTM hubs (/dashboard/profiling, /dashboard/sttm): tenant-wide catalogs over every run.

Profiles: GOVERNANCE.DATA_PROFILES (column history, V036) with tables profiled before V036 filled in from
METADATA.TABLE_PROFILES; the table detail (staged document, scorecard, live drift) is the existing /api/catalog/profile.
STTM: GOVERNANCE.STTM_CATALOG versions, edited inline (each edit a new version), compared and exported to dbt.
"""

from __future__ import annotations

import io
import zipfile
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.db import Db
from app.main import (CatalogProfileRequest, _json, _snowflake_error, _store_rows, _table_ident, _tags_for,
                      catalog_profile_tables, catalog_table_profile, current_db)
from app.source_invoke import invoke_source
from services.catalog import profiles as cat_profiles
from services.catalog import sttm as cat_sttm

router = APIRouter()


def _fqn(value: str) -> tuple[str, str, str]:
    parts = [p.strip().strip('"') for p in str(value or "").split(".")]
    if len(parts) != 3 or not all(parts):
        raise HTTPException(400, "use DATABASE.SCHEMA.TABLE")
    return parts[0], parts[1], parts[2]


# ---------------------------------------------------------------- profiles


def _tag_keys(db: Db, tag: str) -> list[str]:
    try:
        return [r["entity_key"] for r in db.query(
            "SELECT ENTITY_KEY FROM CORE.TAG_ASSIGNMENT WHERE ENTITY_TYPE = 'PROFILE' AND TAG = %s", (tag.lower(),))]
    except Exception:
        return []


def _legacy_rows(db: Db, seen: set, domain: Optional[str], table: Optional[str], q: Optional[str]) -> list[dict]:
    """Tables profiled before V036 (or whose catalog copy failed), from METADATA.TABLE_PROFILES."""
    out = []
    for r in _store_rows(db):
        if not r.get("source_fingerprint") or str(r.get("source_name") or "").upper().startswith("ORACLE_"):
            continue
        fqn = f"{r['database_name']}.{r['schema_name']}.{r['table_name']}".upper()
        if fqn in seen:
            continue
        text = f"{r['database_name']} {r['schema_name']} {r['table_name']}".lower()
        if (table and table.lower() not in str(r["table_name"]).lower()) or (q and q.lower() not in text) or domain:
            continue
        out.append({"profile_id": None, "source_name": r["source_name"], "database_name": r["database_name"],
                    "schema_name": r["schema_name"], "table_name": r["table_name"], "table_fqn": fqn,
                    "run_id": r.get("profiled_in_run"), "domain_id": None, "domain_name": None,
                    "row_count": r.get("row_count"), "column_count": r.get("column_count"),
                    "avg_null_pct": r.get("avg_null_percentage"), "key_candidates": r.get("key_candidates"),
                    "pii_columns": r.get("pii_columns"), "profiled_at": r.get("profiled_at"),
                    "profiled_by": r.get("profiled_by"), "semantic_types": [], "passes": 1, "catalog": False})
    return out


@router.get("/api/hub/profiles")
def search_profiles(domain: Optional[str] = None, table: Optional[str] = None, run_id: Optional[str] = None,
                    tag: Optional[str] = None, q: Optional[str] = None, bookmarked: bool = False,
                    sort: str = "recent", limit: int = 60, offset: int = 0, db: Db = Depends(current_db)):
    tag_keys = _tag_keys(db, tag) if tag else None
    sql, params = cat_profiles.search_sql(domain, table, run_id, tag_keys, q, db.user if bookmarked else None,
                                          sort, limit, offset)
    deployed = True
    try:
        rows = db.query(sql, params)
    except Exception:
        rows, deployed = [], False
    total = int(rows[0]["total"]) if rows else 0
    for r in rows:
        r.pop("total", None)
        r["semantic_types"] = [t for t in (_json(r.get("semantic_types")) or []) if t]
        r["catalog"] = True
    if not (run_id or tag or bookmarked) and offset == 0:
        legacy = _legacy_rows(db, {r["table_fqn"] for r in rows}, domain, table, q)
        rows += legacy
        total += len(legacy)
    keys = [r["table_fqn"] for r in rows]
    tags = _tags_for(db, "PROFILE", keys)
    marks = _bookmarks(db)
    for r in rows:
        r["tags"] = tags.get(r["table_fqn"], [])
        r["bookmarked"] = r["table_fqn"] in marks
    return {"profiles": rows, "total": total, "deployed": deployed, "facets": _facets(db)}


def _bookmarks(db: Db) -> set:
    try:
        return {r["table_fqn"] for r in db.query("SELECT TABLE_FQN FROM GOVERNANCE.PROFILE_BOOKMARK WHERE USER_NAME = %s",
                                                 (db.user,))}
    except Exception:
        return set()


def _facets(db: Db) -> dict:
    out: dict = {"domains": [], "tags": [], "runs": []}
    try:
        out["domains"] = db.query("SELECT DOMAIN_ID, DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG "
                                  "ORDER BY DOMAIN_NAME")
    except Exception:
        pass
    try:
        out["tags"] = [r["tag"] for r in db.query("SELECT DISTINCT TAG FROM CORE.TAG_ASSIGNMENT "
                                                  "WHERE ENTITY_TYPE = 'PROFILE' ORDER BY TAG LIMIT 200")]
    except Exception:
        pass
    try:
        out["runs"] = db.query("""SELECT P.RUN_ID, MAX(R.RUN_NAME) AS RUN_NAME, MAX(P.PROFILED_AT)::VARCHAR AS PROFILED_AT
                                    FROM GOVERNANCE.DATA_PROFILES P LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = P.RUN_ID
                                   WHERE P.RUN_ID IS NOT NULL GROUP BY P.RUN_ID ORDER BY 3 DESC LIMIT 50""")
    except Exception:
        pass
    return out


COLUMN_SELECT = """SELECT COLUMN_NAME, ORDINAL, DATA_TYPE, FAMILY, SEMANTIC_TYPE, PII_CLASSIFICATION, ROW_COUNT,
                          NULL_COUNT, NULL_PCT, DISTINCT_COUNT, CARDINALITY, CARDINALITY_RATIO, MIN_VALUE, MAX_VALUE,
                          POTENTIAL_KEY, TOP_VALUES, HISTOGRAM, DESCRIPTION
                     FROM GOVERNANCE.DATA_PROFILES WHERE PROFILE_ID = %s ORDER BY ORDINAL"""


@router.get("/api/hub/profiles/columns")
def profile_columns(fqn: str, profile_id: Optional[str] = None, db: Db = Depends(current_db)):
    """Columns of one pass (the newest by default), the table's pass history and drift against the previous pass."""
    database, schema, table = _fqn(fqn)
    try:
        passes = db.query("""SELECT PROFILE_ID, MAX(RUN_ID) AS RUN_ID, MAX(ROW_COUNT) AS ROW_COUNT, COUNT(*) AS COLUMNS,
                                    MAX(PROFILED_AT)::VARCHAR AS PROFILED_AT, MAX(PROFILED_BY) AS PROFILED_BY
                               FROM GOVERNANCE.DATA_PROFILES
                              WHERE UPPER(DATABASE_NAME) = UPPER(%s) AND UPPER(SCHEMA_NAME) = UPPER(%s)
                                AND UPPER(TABLE_NAME) = UPPER(%s)
                              GROUP BY PROFILE_ID ORDER BY MAX(PROFILED_AT) DESC LIMIT 50""", (database, schema, table))
    except Exception:
        passes = []
    if not passes:
        return {"fqn": fqn.upper(), "passes": [], "columns": [], "drift": None, "catalog": False}
    ids = [p["profile_id"] for p in passes]
    current = profile_id if profile_id in ids else ids[0]
    columns = db.query(COLUMN_SELECT, (current,))
    for c in columns:
        c["top_values"] = _json(c.get("top_values")) or []
        c["histogram"] = _json(c.get("histogram")) or None
    position = ids.index(current)
    drift = None
    if position + 1 < len(ids):
        previous = db.query(COLUMN_SELECT, (ids[position + 1],))
        drift = cat_profiles.drift_between(previous, columns)
    return {"fqn": fqn.upper(), "profile_id": current, "passes": passes, "columns": columns, "drift": drift,
            "catalog": True}


class Bookmark(BaseModel):
    table_fqn: str = Field(min_length=5, max_length=800)
    note: Optional[str] = Field(default=None, max_length=500)


@router.post("/api/hub/profiles/bookmarks")
def add_bookmark(body: Bookmark, db: Db = Depends(current_db)):
    key = ".".join(_fqn(body.table_fqn)).upper()
    db.execute("""MERGE INTO GOVERNANCE.PROFILE_BOOKMARK T USING (SELECT %s AS U, %s AS K, %s AS N) S
                    ON T.USER_NAME = S.U AND T.TABLE_FQN = S.K
                  WHEN NOT MATCHED THEN INSERT (USER_NAME, TABLE_FQN, NOTE) VALUES (S.U, S.K, S.N)""",
               (db.user, key, body.note))
    return {"bookmarked": True, "table_fqn": key}


@router.delete("/api/hub/profiles/bookmarks")
def remove_bookmark(fqn: str, db: Db = Depends(current_db)):
    key = ".".join(_fqn(fqn)).upper()
    db.execute("DELETE FROM GOVERNANCE.PROFILE_BOOKMARK WHERE USER_NAME = %s AND TABLE_FQN = %s", (db.user, key))
    return {"bookmarked": False, "table_fqn": key}


class Reprofile(BaseModel):
    database: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(min_length=1, max_length=255, alias="schema")
    tables: list[str] = Field(min_length=1, max_length=100)


@router.post("/api/hub/profiles/reprofile")
def reprofile(body: Reprofile, db: Db = Depends(current_db)):
    """Ad-hoc re-profiling: the catalog profiling path with the cache bypassed."""
    return catalog_profile_tables(CatalogProfileRequest(database=body.database, schema=body.schema_name,
                                                        tables=body.tables, force_refresh=True), db)


class TableRef(BaseModel):
    database: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(min_length=1, max_length=255, alias="schema")
    table: str = Field(min_length=1, max_length=255)


@router.post("/api/hub/profiles/expectations")
def expectations(body: TableRef, db: Db = Depends(current_db)):
    """Soda checks and a Great Expectations suite derived from what the profile shows to be true today."""
    from services.quality.gx import render_suite

    detail = catalog_table_profile(body.database, body.schema_name, body.table, db)
    checks = detail.get("checks") or []
    return {"table": _table_ident(body.table), "checks": checks, "soda_yaml": detail.get("checks_yaml") or "",
            "gx_suite": render_suite(_table_ident(body.table), cat_profiles.gx_checks(checks))}


# ---------------------------------------------------------------- STTM catalog


@router.get("/api/hub/sttm")
def search_sttm(q: Optional[str] = None, source: Optional[str] = None, target: Optional[str] = None,
                domain: Optional[str] = None, status: Optional[str] = None, limit: int = 50, offset: int = 0,
                db: Db = Depends(current_db)):
    sql, params = cat_sttm.search_sql(q, source, target, domain, status, limit, offset)
    try:
        rows = db.query(sql, params)
    except Exception:
        return {"mappings": [], "total": 0, "deployed": False}
    total = int(rows[0]["total"]) if rows else 0
    for r in rows:
        r.pop("total", None)
        r["source_entities"] = _json(r.get("source_entities")) or []
    return {"mappings": rows, "total": total, "deployed": True}


def _version(db: Db, catalog_id: str, version: Optional[int] = None) -> dict:
    sql = """SELECT CATALOG_ID, VERSION, STTM_ID, RUN_ID, DOMAIN_ID, DOMAIN_NAME, SOURCE_SYSTEM, SOURCE_ENTITIES,
                    TARGET_TABLE_ID, TARGET_ENTITY, STATUS, ORIGIN, CHANGE_NOTE, TABLE_DESIGN, LINES, LINE_COUNT,
                    CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT
               FROM GOVERNANCE.STTM_CATALOG WHERE CATALOG_ID = %s"""
    params: tuple = (catalog_id,)
    if version is not None:
        sql += " AND VERSION = %s"
        params += (int(version),)
    found = db.query(sql + " ORDER BY VERSION DESC LIMIT 1", params)
    if not found:
        raise HTTPException(404, "mapping version not found")
    r = found[0]
    for key in ("source_entities", "lines", "table_design"):
        r[key] = _json(r.get(key)) or ([] if key != "table_design" else {})
    return r


@router.get("/api/hub/sttm/{catalog_id}")
def get_sttm(catalog_id: str, version: Optional[int] = None, db: Db = Depends(current_db)):
    entry = _version(db, catalog_id, version)
    versions = db.query("""SELECT VERSION, ORIGIN, STATUS, CHANGE_NOTE, LINE_COUNT, STTM_ID, RUN_ID, CREATED_BY,
                                  CREATED_AT::VARCHAR AS CREATED_AT
                             FROM GOVERNANCE.STTM_CATALOG WHERE CATALOG_ID = %s ORDER BY VERSION DESC""", (catalog_id,))
    return {"mapping": entry, "versions": versions}


class DiffIn(BaseModel):
    catalog_id: str
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)


@router.post("/api/hub/sttm/diff")
def diff_sttm(body: DiffIn, db: Db = Depends(current_db)):
    a, b = _version(db, body.catalog_id, body.from_version), _version(db, body.catalog_id, body.to_version)
    return {"from": body.from_version, "to": body.to_version, **cat_sttm.diff(a["lines"], b["lines"])}


class EditIn(BaseModel):
    base_version: int = Field(ge=1)
    edits: list[dict] = Field(min_length=1, max_length=500)
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/hub/sttm/{catalog_id}/edit")
def edit_sttm(catalog_id: str, body: EditIn, db: Db = Depends(current_db)):
    """Inline business-transformation edits become a new catalog version. The run's own STTM is not changed: the
    catalog version is what the hub re-exports to dbt."""
    latest = _version(db, catalog_id)
    if latest["version"] != body.base_version:
        raise HTTPException(409, f"Version {latest['version']} was saved since you opened v{body.base_version}; "
                                 "reload and apply your change again")
    try:
        lines, touched = cat_sttm.apply_edits(latest["lines"], body.edits)
    except (AssertionError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    note = body.note or f"Edited {', '.join(touched[:8])}"
    try:
        written = invoke_source(db, cat_sttm.write_version, catalog_id, lines, "CATALOG_EDIT", note,
                                **{"run_id": latest.get("run_id"), "domain_id": latest.get("domain_id"),
                                   "domain_name": latest.get("domain_name"), "source_system": latest.get("source_system"),
                                   "target_table_id": latest.get("target_table_id"),
                                   "target_entity": latest["target_entity"], "status": "DRAFT",
                                   "table_design": latest.get("table_design")})
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {**written, "edited": touched, "diff": cat_sttm.diff(latest["lines"], lines)}


@router.get("/api/hub/sttm/{catalog_id}/export")
def export_sttm(catalog_id: str, version: Optional[int] = None, format: str = "zip", db: Db = Depends(current_db)):
    entry = _version(db, catalog_id, version)
    landing = []
    if entry.get("run_id"):
        try:
            landing = [{"source_table": r["source_table"], "landing_table": r["landing_table"],
                        "database": r["landing_database"], "schema": r["landing_schema"]}
                       for r in db.query("""SELECT SOURCE_TABLE, LANDING_TABLE, LANDING_DATABASE, LANDING_SCHEMA
                                              FROM SOURCE.LANDING_TABLE_REGISTRY
                                             WHERE RUN_ID = %s AND INGESTION_STATUS = 'COMPLETE'""", (entry["run_id"],))]
        except Exception:
            landing = []
    files = cat_sttm.export_files(entry, landing)
    if format == "json":
        return {"files": files}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in sorted(files.items()):
            zf.writestr(path, content)
    buffer.seek(0)
    name = f"sttm_{entry['target_entity'].split('.')[-1].lower()}_v{entry['version']}.zip"
    return StreamingResponse(buffer, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})
