"""Profiling and STTM hubs (/dashboard/profiling, /dashboard/sttm): tenant-wide catalogs over every run.

Profiles: GOVERNANCE.DATA_PROFILES (column history, V036) with tables profiled before V036 filled in from
METADATA.TABLE_PROFILES; the table detail (staged document, scorecard, live drift) is the existing /api/catalog/profile.
STTM: GOVERNANCE.STTM_CATALOG versions, edited inline (each edit a new version), compared and exported to dbt.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
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
    total = int(rows[0].get("total") or len(rows)) if rows else 0
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
    total = int(rows[0].get("total") or len(rows)) if rows else 0
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


class JoinsIn(BaseModel):
    driving_table: Optional[str] = Field(default=None, max_length=256)
    joins: list[dict] = Field(default_factory=list, max_length=50)


class EditIn(BaseModel):
    """One store edit: any mix of line edits, added and removed lines, design (grain, keys, dedup, SCD, strategy,
    row filters) and the join plan. Applied as a whole after approval."""
    base_version: int = Field(ge=1)
    edits: list[dict] = Field(default_factory=list, max_length=500)
    add_lines: list[dict] = Field(default_factory=list, max_length=200)
    remove_lines: list[str] = Field(default_factory=list, max_length=500)
    design: Optional[dict] = None
    joins: Optional[JoinsIn] = None
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/hub/sttm/{catalog_id}/edit")
def edit_sttm(catalog_id: str, body: EditIn, request: Request, db: Db = Depends(current_db)):
    """A store edit. Governance queues it for the STTM approver (every store edit waits for another person); the
    approved replay lands here, writes a new APPROVED catalog version and writes the change back to the run's STTM
    so the next dbt generation uses it."""
    from app.governance import _valid_replay

    payload = body.model_dump(exclude_none=True)
    if body.joins is not None:
        payload["joins"] = body.joins.model_dump()
    latest = _version(db, catalog_id)
    if latest["version"] != body.base_version:
        raise HTTPException(409, f"Version {latest['version']} was saved since you opened v{body.base_version}; "
                                 "reload and apply your change again")
    try:  # validate before touching Snowflake (the replay validates again against the then-latest version)
        cat_sttm.plan_edit(latest, {k: v for k, v in payload.items() if k != "joins"}, {}) if not body.joins else None
    except (AssertionError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    approved = bool(_valid_replay(request.headers.get("x-aip-replay")))
    try:
        return invoke_source(db, cat_sttm.edit_version, catalog_id, json.dumps(payload, default=str), approved)
    except AssertionError as exc:
        raise HTTPException(409 if "reload" in str(exc) else 400, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@router.get("/api/hub/sttm/{catalog_id}/preview")
def preview_sttm(catalog_id: str, version: Optional[int] = None, db: Db = Depends(current_db)):
    """The model SQL a version assembles: driving table, joins and row filters, and every target column."""
    from services.sttm.join_graph import preview_sql

    entry = _version(db, catalog_id, version)
    design = entry.get("table_design") or {}
    graph = dict(design.get("join_graph") or {})
    if not graph.get("driving_table"):
        tables = sorted({str(l.get("source_table")).upper() for l in entry["lines"] if l.get("source_table")})
        graph = {"driving_table": design.get("driving_table") or (tables[0] if tables else None), "joins": [],
                 "unreachable": [], "ambiguities": []}
    return {"sql": preview_sql(graph, entry["lines"], entry["target_entity"], design.get("filters") or []),
            "join_graph": graph, "filters": design.get("filters") or []}


@router.get("/api/hub/sttm/{catalog_id}/context")
def sttm_context(catalog_id: str, version: Optional[int] = None, db: Db = Depends(current_db)):
    """What a reviewer needs beside the lines: each source column's profile, the source tables and their columns
    (for re-picking a source or a join key), and the run's mapping candidates and decisions."""
    entry = _version(db, catalog_id, version)
    run_id = entry.get("run_id")
    tables = sorted({str(l.get("source_table")).upper() for l in entry["lines"] if l.get("source_table")})
    columns: dict = {}
    if run_id:
        try:
            for r in db.query("""SELECT T.SOURCE_TABLE, C.COLUMN_NAME, C.DATA_TYPE
                                   FROM SOURCE.LANDING_TABLE_REGISTRY T
                                   JOIN SOURCE.LANDING_COLUMN_REGISTRY C ON C.LANDING_ID = T.LANDING_ID
                                  WHERE T.RUN_ID = %s AND T.INGESTION_STATUS = 'COMPLETE'
                                QUALIFY DENSE_RANK() OVER (PARTITION BY T.SOURCE_TABLE ORDER BY T.CREATED_AT DESC) = 1
                                  ORDER BY T.SOURCE_TABLE, C.ORDINAL_POSITION""", (run_id,)):
                columns.setdefault(str(r["source_table"]).upper(), []).append(
                    {"column": str(r["column_name"]).upper(), "data_type": r.get("data_type")})
        except Exception:
            columns = {}
    for t, cs in cat_sttm.columns_from_lines(entry["lines"]).items():
        known = {c["column"] for c in columns.setdefault(t, [])}
        columns[t] += [{"column": c, "data_type": None} for c in cs if c not in known]
    profiles: dict = {}
    if tables:
        try:
            for r in db.query(f"""SELECT UPPER(TABLE_NAME) AS T, UPPER(COLUMN_NAME) AS C, DATA_TYPE, SEMANTIC_TYPE,
                                         PII_CLASSIFICATION, NULL_PCT, DISTINCT_COUNT, CARDINALITY, MIN_VALUE, MAX_VALUE,
                                         POTENTIAL_KEY, TOP_VALUES, ROW_COUNT
                                    FROM GOVERNANCE.DATA_PROFILES
                                   WHERE UPPER(TABLE_NAME) IN ({", ".join(["%s"] * len(tables))})
                                 QUALIFY ROW_NUMBER() OVER (PARTITION BY UPPER(TABLE_NAME), UPPER(COLUMN_NAME)
                                                            ORDER BY PROFILED_AT DESC) = 1""", tuple(tables)):
                r["top_values"] = _json(r.get("top_values"))
                profiles[f"{r['t']}.{r['c']}"] = r
        except Exception:
            profiles = {}
    candidates: list = []
    decisions: list = []
    if run_id:
        try:
            candidates = db.query("""SELECT UPPER(TBL.SOURCE_TABLE) AS SOURCE_TABLE, UPPER(L.COLUMN_NAME) AS SOURCE_COLUMN,
                                            L.DATA_TYPE AS SOURCE_DATATYPE, UPPER(TC.COLUMN_NAME) AS TARGET_COLUMN,
                                            C.FINAL_SCORE, C.CONFIDENCE, C.RANK, C.RECOMMENDATION, C.GENERATED_REASON,
                                            C.TRANSFORMATION
                                       FROM MAPPING.MAPPING_CANDIDATE C
                                       JOIN SOURCE.LANDING_COLUMN_REGISTRY L ON L.LANDING_COLUMN_ID = C.SOURCE_COLUMN_ID
                                       JOIN SOURCE.LANDING_TABLE_REGISTRY TBL ON TBL.LANDING_ID = L.LANDING_ID
                                       JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY TC ON TC.TARGET_COLUMN_ID = C.TARGET_COLUMN_ID
                                      WHERE C.RUN_ID = %s AND C.IS_CURRENT AND C.RANK <= 5
                                      ORDER BY TC.COLUMN_NAME, C.RANK LIMIT 3000""", (run_id,))
        except Exception:
            candidates = []
        try:
            decisions = db.query("""SELECT UPPER(TC.COLUMN_NAME) AS TARGET_COLUMN, D.DECISION, D.TRANSFORMATION,
                                           D.BUSINESS_JUSTIFICATION, D.REVIEWER, D.CREATED_AT::VARCHAR AS DECIDED_AT
                                      FROM MAPPING.MAPPING_DECISION D
                                      LEFT JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY TC ON TC.TARGET_COLUMN_ID = D.TARGET_COLUMN_ID
                                     WHERE D.RUN_ID = %s AND D.IS_CURRENT""", (run_id,))
        except Exception:
            decisions = []
    return {"run_id": run_id, "tables": tables, "columns": columns, "profiles": profiles,
            "candidates": candidates, "decisions": decisions}


class RefineIn(BaseModel):
    target_column: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=3, max_length=2000)
    source_table: Optional[str] = Field(default=None, max_length=256)
    source_column: Optional[str] = Field(default=None, max_length=256)
    source_datatype: Optional[str] = Field(default=None, max_length=256)
    target_datatype: Optional[str] = Field(default=None, max_length=256)
    current_transformation: Optional[str] = Field(default=None, max_length=4000)
    business_definition: Optional[str] = Field(default=None, max_length=4000)


@router.post("/api/hub/sttm/{catalog_id}/refine")
def refine_sttm(catalog_id: str, body: RefineIn, db: Db = Depends(current_db)):
    """AI proposal for one line's transformation (read-only; applying it is a store edit, so it goes for approval).
    Uses the producing run's profile, rules and code context."""
    from services.sttm.procedures import refine_transformation

    entry = _version(db, catalog_id)
    if not entry.get("run_id"):
        raise HTTPException(400, "AI suggestions need the run that produced this mapping; this version has none")
    try:
        return invoke_source(db, refine_transformation, entry["run_id"], json.dumps(body.model_dump()))
    except AssertionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@router.get("/api/hub/sttm/{catalog_id}/export.csv")
def export_sttm_csv(catalog_id: str, version: Optional[int] = None, db: Db = Depends(current_db)):
    from services.sttm.refine import render_csv

    entry = _version(db, catalog_id, version)
    text = render_csv([{f: l.get(f) for f in ("target_column", "target_datatype", "mapping_type", "source_system",
                                               "source_table", "source_column", "source_datatype", "transformation",
                                               "filter_rule", "business_rule", "business_definition", "join_logic")}
                       for l in entry["lines"]])
    name = f"sttm_{entry['target_entity'].split('.')[-1].lower()}_v{entry['version']}.csv"
    return StreamingResponse(io.BytesIO(text.encode("utf-8")), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


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
