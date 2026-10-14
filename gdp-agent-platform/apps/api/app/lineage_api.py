"""End-to-end lineage, Source to Gold (services/lineage/trace.py).

GET /api/lineage/trace?object=&column=&direction=up|down|both&depth=   the path through every layer
GET /api/lineage/objects?q=                                             objects that have lineage, to pick from
Edges are read from where the platform holds them: landed tables (SOURCE.LANDING_TABLE_REGISTRY), approved STTM lines
(CONTRACT.STTM_LINE, the latest approved version per target), the mastered-domain contracts and consumer hubs
(KNOWLEDGE.DOMAIN_REGISTRY CONFIG.mdm, KNOWLEDGE.TARGET_COLUMN_REGISTRY), and gold generation (GOVERNANCE.LINEAGE_EDGE).
Read only; any source that is not deployed is skipped.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from app.db import Db
from app.main import current_db
from services.lineage import trace as lt

router = APIRouter()


def _soft(db: Db, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    try:
        return db.query(sql, params)
    except Exception:
        return []


def _js(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _env(silver_database: Optional[str]) -> str:
    m = re.match(r"^([A-Z]+)_", str(silver_database or "").upper())
    return m.group(1) if m else "DEV"


def edges(db: Db) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in _soft(db, """SELECT SOURCE_DATABASE, SOURCE_SCHEMA, SOURCE_TABLE, LANDING_DATABASE, LANDING_SCHEMA, LANDING_TABLE
                            FROM SOURCE.LANDING_TABLE_REGISTRY WHERE INGESTION_STATUS = 'COMPLETE'
                           QUALIFY ROW_NUMBER() OVER (PARTITION BY LANDING_DATABASE, LANDING_SCHEMA, LANDING_TABLE ORDER BY CREATED_AT DESC) = 1"""):
        out.append(lt.edge("SOURCE", f"{r['source_database']}.{r['source_schema']}.{r['source_table']}", None,
                           "BRONZE", f"{r['landing_database']}.{r['landing_schema']}.{r['landing_table']}", None, "INGEST", "landed", "SILVER_RUN"))
    for r in _soft(db, """SELECT L.SOURCE_DATABASE, L.SOURCE_SCHEMA, L.SOURCE_TABLE, L.SOURCE_COLUMN, L.TARGET_DATABASE, L.TARGET_SCHEMA,
                                 L.TARGET_TABLE, L.TARGET_COLUMN, L.TRANSFORMATION
                            FROM CONTRACT.STTM_LINE L JOIN CONTRACT.STTM_REGISTRY S ON S.STTM_ID = L.STTM_ID
                           WHERE S.STATUS = 'APPROVED' AND L.SOURCE_TABLE IS NOT NULL
                         QUALIFY DENSE_RANK() OVER (PARTITION BY S.TARGET_TABLE_ID ORDER BY S.CREATED_AT DESC) = 1
                           LIMIT 20000"""):
        src = ".".join(str(r.get(k) or "") for k in ("source_database", "source_schema", "source_table")).strip(".")
        tgt = ".".join(str(r.get(k) or "") for k in ("target_database", "target_schema", "target_table")).strip(".")
        out.append(lt.edge("BRONZE", src, r.get("source_column"), "SILVER_PRE_MDM", tgt, r.get("target_column"), "MAP",
                           r.get("transformation"), "SILVER_RUN"))
    reg = _soft(db, """SELECT DOMAIN_ID, DOMAIN_NAME, CONFIG:mdm AS MDM, CONFIG:silver_database::STRING AS SILVER_DATABASE
                         FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG AND CONFIG:mdm IS NOT NULL""")
    mastered = {}
    for r in reg:
        m = _js(r.get("mdm")) or {}
        if str(m.get("role") or "").upper() == "MASTERED":
            mastered[r["domain_name"]] = m
            try:
                out += lt.mdm_edges(r["domain_name"], m, _env(r.get("silver_database")))
            except (KeyError, TypeError):
                continue
    for r in reg:
        m = _js(r.get("mdm")) or {}
        if str(m.get("role") or "").upper() != "CONSUMER":
            continue
        cols = _soft(db, """SELECT T.TARGET_DATABASE, T.TARGET_SCHEMA, T.TARGET_TABLE, C.COLUMN_NAME
                              FROM KNOWLEDGE.TARGET_TABLE_REGISTRY T JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY C ON C.TARGET_TABLE_ID = T.TARGET_TABLE_ID
                             WHERE T.DOMAIN_ID = %s AND T.ACTIVE_FLAG""", (r["domain_id"],))
        tables: Dict[str, List[str]] = {}
        for c in cols:
            tables.setdefault(f"{c['target_database']}.{c['target_schema']}.{c['target_table']}", []).append(c["column_name"])
        out += lt.consumer_edges(r["domain_name"], m.get("consumes") or [], [{"object": o, "columns": v} for o, v in tables.items()],
                                 mastered, _env(r.get("silver_database")))
    for r in _soft(db, """SELECT FROM_LAYER, FROM_OBJECT, FROM_COLUMN, TO_LAYER, TO_OBJECT, TO_COLUMN, KIND, EXPRESSION, PRODUCER
                            FROM GOVERNANCE.LINEAGE_EDGE WHERE IS_CURRENT LIMIT 50000"""):
        out.append(lt.edge(r["from_layer"], r["from_object"], r.get("from_column"), r["to_layer"], r["to_object"], r.get("to_column"),
                           r["kind"], r.get("expression"), r.get("producer") or ""))
    return out


@router.get("/api/lineage/trace")
def lineage_trace(object: str = Query(min_length=3, max_length=512), column: Optional[str] = Query(default=None, max_length=256),
                  direction: str = Query(default="both", pattern="^(up|down|both)$"), depth: int = Query(default=8, ge=1, le=12),
                  db: Db = Depends(current_db)):
    result = lt.trace(edges(db), object, column, depth, direction)
    result.pop("starts", None)
    return result


@router.get("/api/lineage/objects")
def lineage_objects(q: str = Query(default="", max_length=256), db: Db = Depends(current_db)):
    seen: Dict[str, Dict[str, Any]] = {}
    for e in edges(db):
        for side in ("from", "to"):
            o = e[f"{side}_object"]
            n = seen.setdefault(o, {"object": o, "layer": e[f"{side}_layer"], "columns": set()})
            if e[f"{side}_column"]:
                n["columns"].add(e[f"{side}_column"])
    needle = q.strip().upper()
    found = [{**n, "columns": sorted(n["columns"])} for o, n in seen.items() if not needle or needle in o]
    order = {l: i for i, l in enumerate(lt.LAYERS)}
    found.sort(key=lambda n: (order.get(n["layer"], 99), n["object"]))
    return {"objects": found[:200], "total": len(found)}
