"""GOVERNANCE.STTM_CATALOG: every version of every source-to-target mapping, decoupled from run state.

A catalog entry (CATALOG_ID) is one mapping: a source system feeding one target table. Each STTM generation,
transformation edit and join edit in a run, and each edit made in the STTM hub, adds a version holding the full line
set, so versions can be reopened, compared and re-exported to dbt long after the run is archived.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.common.sql import rows, scalar, variant

TABLE = "GOVERNANCE.STTM_CATALOG"
ORIGINS = ("GENERATED", "TRANSFORM_EDIT", "JOIN_EDIT", "CATALOG_EDIT", "BACKFILL")
LINE_FIELDS = ("source_system", "source_table", "source_column", "source_datatype", "target_column", "target_datatype",
               "mapping_type", "transformation", "business_rule", "business_definition", "join_logic")
EDITABLE = ("transformation", "mapping_type", "business_rule", "business_definition", "source_table", "source_column")
MAPPING_TYPES = ("DIRECT", "TRANSFORM", "DERIVED", "LOOKUP", "CONSTANT", "UNMAPPED")
COMPARED = ("source_table", "source_column", "target_datatype", "mapping_type", "transformation", "business_rule",
            "business_definition", "join_logic")
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def catalog_id(source_system: Optional[str], target_table_id: str) -> str:
    """Stable id of one mapping; the same rule as the V036 backfill (md5 of source system and target table id)."""
    digest = hashlib.md5(f"{source_system or ''}|{target_table_id}".encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest))


def _line(raw: Dict[str, Any]) -> Dict[str, Any]:
    line = {k.lower(): v for k, v in raw.items()}
    return {k: line.get(k) for k in LINE_FIELDS}


def _key(line: Dict[str, Any]) -> str:
    return str(line.get("target_column") or line.get("TARGET_COLUMN") or "").upper()


# ---------------------------------------------------------------- pure: diff and edits


def diff(old_lines: List[Dict[str, Any]], new_lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Line-level changes between two versions, keyed on target column."""
    a = {_key(l): _line(l) for l in old_lines or []}
    b = {_key(l): _line(l) for l in new_lines or []}
    added = [b[k] for k in sorted(set(b) - set(a))]
    removed = [a[k] for k in sorted(set(a) - set(b))]
    changed = []
    for k in sorted(set(a) & set(b)):
        fields = [f for f in COMPARED if (a[k].get(f) or None) != (b[k].get(f) or None)]
        if fields:
            changed.append({"target_column": b[k]["target_column"], "fields": fields,
                            "from": {f: a[k].get(f) for f in fields}, "to": {f: b[k].get(f) for f in fields}})
    return {"added": added, "removed": removed, "changed": changed,
            "summary": f"{len(added)} added, {len(removed)} removed, {len(changed)} changed"}


def check_expression(expression: Optional[str]) -> None:
    """A transformation is one SQL expression: no statements (the mapping-validation rule) and no ';' outside
    string literals, so an edit can never smuggle a second statement into generated dbt."""
    from services.knowledge.usage import assert_safe_transformation

    text = (expression or "").strip()
    if not text:
        return
    assert len(text) <= 4000, "a transformation is at most 4000 characters"
    assert ";" not in STRING_LITERAL.sub("''", text), "a transformation is one expression, without ';'"
    assert_safe_transformation(text)


def apply_edits(lines: List[Dict[str, Any]], edits: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """New line set with `edits` applied (each {target_column, field: value, ...}); returns (lines, edited columns).
    Only EDITABLE fields change; an unknown target column is an error."""
    out = [_line(l) for l in lines]
    index = {_key(l): l for l in out}
    touched: List[str] = []
    for edit in edits or []:
        key = str(edit.get("target_column") or "").upper()
        assert key in index, f"{edit.get('target_column')} is not a target column of this mapping"
        line = index[key]
        unknown = set(k for k in edit if k != "target_column") - set(EDITABLE)
        assert not unknown, f"cannot edit {sorted(unknown)}; editable fields are {list(EDITABLE)}"
        if "mapping_type" in edit:
            value = str(edit["mapping_type"] or "").upper()
            assert value in MAPPING_TYPES, f"mapping_type must be one of {list(MAPPING_TYPES)}"
            line["mapping_type"] = value
        if "transformation" in edit:
            check_expression(edit["transformation"])
            line["transformation"] = (edit["transformation"] or "").strip() or None
        for field in ("business_rule", "business_definition", "source_table", "source_column"):
            if field in edit:
                line[field] = (str(edit[field]).strip()[:4000] or None) if edit[field] is not None else None
        touched.append(line["target_column"])
    return out, touched


def export_files(entry: Dict[str, Any], landing_tables: Optional[List[Dict[str, str]]] = None) -> Dict[str, str]:
    """A catalog version as a compile-ready dbt project (services/dbt/project.build)."""
    from services.dbt.project import build

    design = dict(entry.get("table_design") or {})
    target = str(entry.get("target_entity") or "").split(".")
    design.setdefault("target_table", target[-1] if target else "target")
    lines = [_line(l) for l in entry.get("lines") or []]
    for line in lines:
        line["target_datatype"] = line.get("target_datatype") or "VARCHAR"
    sttm = {"sttm_id": entry.get("sttm_id") or f"{entry.get('catalog_id')}:v{entry.get('version')}",
            "table_design": design, "lines": lines}
    return build(sttm, [], "", landing_tables or [], entry.get("source_system") or "source")


# ---------------------------------------------------------------- Snowpark writers (run pipeline side)


def _lines_for(session, sttm_id: str) -> List[Dict[str, Any]]:
    return [_line(r) for r in rows(session, """SELECT SOURCE_SYSTEM, SOURCE_TABLE, SOURCE_COLUMN, SOURCE_DATATYPE,
                                                     TARGET_COLUMN, TARGET_DATATYPE, MAPPING_TYPE, TRANSFORMATION,
                                                     BUSINESS_RULE, BUSINESS_DEFINITION, JOIN_LOGIC
                                                FROM CONTRACT.STTM_LINE WHERE STTM_ID = ? ORDER BY TARGET_COLUMN""",
                                       [sttm_id])]


def snapshot(session, sttm_id: str, origin: str = "GENERATED", note: str = "") -> Optional[Dict[str, Any]]:
    """Copy the current state of a run's STTM into the catalog as a new version. Never raises: the run step that
    called it has already succeeded, and a missing catalog (V036 not deployed) must not undo it."""
    try:
        assert origin in ORIGINS
        reg = rows(session, """SELECT S.STTM_ID, S.RUN_ID, S.DOMAIN_ID, S.TARGET_TABLE_ID, S.STATUS, S.TABLE_DESIGN,
                                      D.DOMAIN_NAME, T.TARGET_DATABASE, T.TARGET_SCHEMA, T.TARGET_TABLE
                                 FROM CONTRACT.STTM_REGISTRY S
                                 LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = S.DOMAIN_ID
                                 LEFT JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = S.TARGET_TABLE_ID
                                WHERE S.STTM_ID = ?""", [sttm_id])
        if not reg:
            return None
        r = reg[0]
        lines = _lines_for(session, sttm_id)
        source_system = next((l["source_system"] for l in lines if l.get("source_system")), None)
        target = ".".join(str(p) for p in (r.get("TARGET_DATABASE"), r.get("TARGET_SCHEMA"), r.get("TARGET_TABLE")) if p)
        return write_version(session, catalog_id(source_system, r["TARGET_TABLE_ID"]), lines, origin, note,
                             sttm_id=sttm_id, run_id=r["RUN_ID"], domain_id=r["DOMAIN_ID"],
                             domain_name=r.get("DOMAIN_NAME"), source_system=source_system,
                             target_table_id=r["TARGET_TABLE_ID"], target_entity=target or r["TARGET_TABLE_ID"],
                             status=r["STATUS"], table_design=variant(r.get("TABLE_DESIGN")))
    except Exception:
        return None


def snapshot_run(session, run_id: str, origin: str, note: str = "") -> Optional[Dict[str, Any]]:
    """snapshot() of the run's current STTM (newest REVIEW, APPROVED or DRAFT version). Never raises."""
    try:
        found = rows(session, """SELECT STTM_ID FROM CONTRACT.STTM_REGISTRY
                                 WHERE RUN_ID = ? AND STATUS IN ('REVIEW', 'APPROVED', 'DRAFT')
                                 ORDER BY STTM_VERSION DESC LIMIT 1""", [run_id])
    except Exception:
        return None
    return snapshot(session, found[0]["STTM_ID"], origin, note) if found else None


def write_version(session, cid: str, lines: List[Dict[str, Any]], origin: str, note: str = "", *,
                  sttm_id: Optional[str] = None, run_id: Optional[str] = None, domain_id: Optional[str] = None,
                  domain_name: Optional[str] = None, source_system: Optional[str] = None,
                  target_table_id: Optional[str] = None, target_entity: str = "", status: str = "DRAFT",
                  table_design: Any = None) -> Dict[str, Any]:
    version = int(scalar(session, f"SELECT COALESCE(MAX(VERSION), 0) FROM {TABLE} WHERE CATALOG_ID = ?", [cid]) or 0) + 1
    sources = sorted({l["source_table"] for l in lines if l.get("source_table")})
    session.sql(
        f"""INSERT INTO {TABLE} (CATALOG_ID, VERSION, STTM_ID, RUN_ID, DOMAIN_ID, DOMAIN_NAME, SOURCE_SYSTEM,
                                 SOURCE_ENTITIES, TARGET_TABLE_ID, TARGET_ENTITY, STATUS, ORIGIN, CHANGE_NOTE,
                                 TABLE_DESIGN, LINES, LINE_COUNT)
            SELECT ?, ?::NUMBER, NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''),
                   PARSE_JSON(?)::ARRAY, NULLIF(?, ''), ?, ?, ?, NULLIF(?, ''), PARSE_JSON(NULLIF(?, '')),
                   PARSE_JSON(?)::ARRAY, ?::NUMBER""",
        params=[cid, str(version), sttm_id or "", run_id or "", domain_id or "", domain_name or "",
                source_system or "", json.dumps(sources), target_table_id or "", target_entity,
                status if status in ("DRAFT", "REVIEW", "APPROVED", "SUPERSEDED") else "DRAFT", origin, note[:2000],
                json.dumps(table_design) if table_design else "", json.dumps(lines, default=str), str(len(lines))],
    ).collect()
    return {"catalog_id": cid, "version": version, "lines": len(lines)}


# ---------------------------------------------------------------- hub queries (API side, %s binds)


def search_sql(q: Optional[str] = None, source: Optional[str] = None, target: Optional[str] = None,
               domain: Optional[str] = None, status: Optional[str] = None, limit: int = 50,
               offset: int = 0) -> Tuple[str, tuple]:
    """Latest version per mapping, filtered by free text, source entity, target entity, domain and status."""
    where, params = [], []
    if q:
        where.append("(TARGET_ENTITY ILIKE %s OR SOURCE_SYSTEM ILIKE %s OR ARRAY_TO_STRING(SOURCE_ENTITIES, ',') ILIKE %s)")
        params += [f"%{q}%"] * 3
    if source:
        where.append("(SOURCE_SYSTEM ILIKE %s OR ARRAY_TO_STRING(SOURCE_ENTITIES, ',') ILIKE %s)")
        params += [f"%{source}%"] * 2
    if target:
        where.append("TARGET_ENTITY ILIKE %s")
        params.append(f"%{target}%")
    if domain:
        where.append("(DOMAIN_ID = %s OR UPPER(DOMAIN_NAME) = UPPER(%s))")
        params += [domain, domain]
    if status:
        where.append("STATUS = %s")
        params.append(status.upper())
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        WITH L AS (
            SELECT *, COUNT(*) OVER (PARTITION BY CATALOG_ID) AS VERSIONS
              FROM {TABLE}
           QUALIFY ROW_NUMBER() OVER (PARTITION BY CATALOG_ID ORDER BY VERSION DESC) = 1
        )
        SELECT CATALOG_ID, VERSION, VERSIONS, STTM_ID, RUN_ID, DOMAIN_ID, DOMAIN_NAME, SOURCE_SYSTEM, SOURCE_ENTITIES,
               TARGET_ENTITY, STATUS, ORIGIN, LINE_COUNT, CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT,
               COUNT(*) OVER () AS TOTAL
          FROM L {clause}
         ORDER BY CREATED_AT DESC
         LIMIT {int(max(1, min(limit, 200)))} OFFSET {int(max(0, offset))}"""
    return sql, tuple(params)
