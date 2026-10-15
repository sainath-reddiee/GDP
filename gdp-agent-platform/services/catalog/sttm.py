"""CORE.STTM_CATALOG: every version of every source-to-target mapping, decoupled from run state.

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

TABLE = "CORE.STTM_CATALOG"
ORIGINS = ("GENERATED", "TRANSFORM_EDIT", "JOIN_EDIT", "CATALOG_EDIT", "BACKFILL")
LINE_FIELDS = ("source_system", "source_table", "source_column", "source_datatype", "target_column", "target_datatype",
               "mapping_type", "transformation", "business_rule", "business_definition", "join_logic", "filter_rule")
# every field of a line except the target column (its key) and join_logic (derived from the join plan)
EDITABLE = ("source_system", "source_table", "source_column", "source_datatype", "target_datatype", "mapping_type",
            "transformation", "business_rule", "business_definition", "filter_rule")
TEXT_FIELDS = ("business_rule", "business_definition")
NAME_FIELDS = ("source_system", "source_table", "source_column")
DESIGN_FIELDS = ("grain", "business_keys", "dedup", "scd_type", "incremental_strategy", "filters")
SCD_TYPES = ("0", "1", "2")
STRATEGIES = ("merge", "append", "delete+insert", "insert_overwrite", "full_refresh")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,255}$")
DATATYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_ ]{0,60}(\(\s*\d+(\s*,\s*\d+)?\s*\))?$")
MAPPING_TYPES = ("DIRECT", "TRANSFORM", "DERIVED", "LOOKUP", "CONSTANT", "UNMAPPED")
COMPARED = ("source_system", "source_table", "source_column", "source_datatype", "target_datatype", "mapping_type",
            "transformation", "business_rule", "business_definition", "join_logic", "filter_rule")
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


def _set_field(line: Dict[str, Any], field: str, value: Any) -> None:
    """One validated field change on a line."""
    if field == "mapping_type":
        v = str(value or "").upper()
        assert v in MAPPING_TYPES, f"mapping_type must be one of {list(MAPPING_TYPES)}"
        line[field] = v
    elif field in ("transformation", "filter_rule"):
        check_expression(value)
        line[field] = (value or "").strip() or None
    elif field in NAME_FIELDS:
        v = str(value or "").strip()
        assert not v or NAME.match(v), f"{field} must be a plain Snowflake name, not {v!r}"
        line[field] = v.upper() or None
    elif field in ("source_datatype", "target_datatype"):
        v = str(value or "").strip()
        assert not v or DATATYPE.match(v), f"{field} {v!r} is not a data type"
        line[field] = v.upper() or None
    elif field in TEXT_FIELDS:
        line[field] = (str(value).strip()[:4000] or None) if value is not None else None
    else:
        raise AssertionError(f"cannot edit {field}; editable fields are {list(EDITABLE)}")


def apply_edits(lines: List[Dict[str, Any]], edits: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """New line set with `edits` applied (each {target_column, field: value, ...}); returns (lines, edited columns).
    Any EDITABLE field of any line can change, approved or not; an unknown target column is an error."""
    out = [_line(l) for l in lines]
    index = {_key(l): l for l in out}
    touched: List[str] = []
    for edit in edits or []:
        key = str(edit.get("target_column") or "").upper()
        assert key in index, f"{edit.get('target_column')} is not a target column of this mapping"
        line = index[key]
        unknown = set(k for k in edit if k != "target_column") - set(EDITABLE)
        assert not unknown, f"cannot edit {sorted(unknown)}; editable fields are {list(EDITABLE)}"
        for field, value in edit.items():
            if field != "target_column":
                _set_field(line, field, value)
        touched.append(line["target_column"])
    return out, touched


def add_lines(lines: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Lines for new target columns; each needs a target column name, a target data type and a mapping type."""
    out = [_line(l) for l in lines]
    taken = {_key(l) for l in out}
    added: List[str] = []
    for raw in new or []:
        name = str(raw.get("target_column") or "").strip().upper()
        assert NAME.match(name), f"target column {raw.get('target_column')!r} must be a plain Snowflake name"
        assert name not in taken, f"{name} is already a target column of this mapping"
        line = {k: None for k in LINE_FIELDS}
        line.update(target_column=name, target_datatype="VARCHAR", mapping_type="DIRECT" if raw.get("source_column") else "UNMAPPED")
        for field, value in raw.items():
            if field in EDITABLE:
                _set_field(line, field, value)
        out.append(line)
        taken.add(name)
        added.append(name)
    return out, added


def remove_lines(lines: List[Dict[str, Any]], columns: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    drop = {str(c).upper() for c in columns or []}
    missing = drop - {_key(l) for l in lines}
    assert not missing, f"{sorted(missing)} are not target columns of this mapping"
    out = [_line(l) for l in lines if _key(l) not in drop]
    assert out, "a mapping keeps at least one target column"
    return out, sorted(drop)


def apply_design(design: Dict[str, Any], patch: Dict[str, Any], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Grain, business keys, dedup, SCD type, incremental strategy and row filters ({table, condition, reason})."""
    out = dict(design or {})
    unknown = set(patch or {}) - set(DESIGN_FIELDS)
    assert not unknown, f"cannot edit {sorted(unknown)}; design fields are {list(DESIGN_FIELDS)}"
    targets = {_key(l) for l in lines}
    for field, value in (patch or {}).items():
        if field in ("grain", "dedup"):
            out[field] = (str(value or "").strip()[:1000]) or None
        elif field == "business_keys":
            keys = [str(k).strip().upper() for k in value or [] if str(k).strip()]
            missing = set(keys) - targets
            assert not missing, f"business keys {sorted(missing)} are not target columns"
            out[field] = keys
        elif field == "scd_type":
            v = str(value or "1")
            assert v in SCD_TYPES, f"scd_type must be one of {list(SCD_TYPES)}"
            out[field] = v
        elif field == "incremental_strategy":
            v = str(value or "merge").lower()
            assert v in STRATEGIES, f"incremental_strategy must be one of {list(STRATEGIES)}"
            out[field] = v
        elif field == "filters":
            filters = []
            for f in value or []:
                condition = str(f.get("condition") or "").strip()
                assert condition, "a filter needs a condition"
                check_expression(condition)
                table = str(f.get("table") or "").strip().upper()
                assert not table or NAME.match(table), f"filter table {table!r} must be a plain Snowflake name"
                filters.append({"table": table or None, "condition": condition,
                                "reason": (str(f.get("reason") or "").strip()[:1000]) or None})
            out[field] = filters
    return out


def apply_joins(design: Dict[str, Any], joins: Dict[str, Any], columns: Dict[str, List[str]],
                lines: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """The run join-plan editor's rules (services.sttm.join_graph.apply_overrides) on a catalog version; returns the
    new design and the lines with their JOIN_LOGIC recomputed."""
    from services.sttm.join_graph import apply_overrides, join_logic_by_table

    out = dict(design or {})
    graph = apply_overrides(out.get("join_graph") or {"joins": []}, joins.get("driving_table"), joins.get("joins") or [],
                            columns)
    out.update(join_graph=graph, driving_table=graph["driving_table"], join_paths=[j["condition"] for j in graph["joins"]])
    return out, with_join_logic(lines, graph, join_logic_by_table)


def with_join_logic(lines: List[Dict[str, Any]], graph: Dict[str, Any], logic_fn=None) -> List[Dict[str, Any]]:
    if logic_fn is None:
        from services.sttm.join_graph import join_logic_by_table as logic_fn
    logic = logic_fn(graph) if graph.get("driving_table") else {}
    return [{**_line(l), "join_logic": logic.get(str(l.get("source_table") or "").upper())} for l in lines]


def columns_from_lines(lines: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for l in lines:
        if l.get("source_table") and l.get("source_column"):
            cols = out.setdefault(str(l["source_table"]).upper(), [])
            if str(l["source_column"]).upper() not in cols:
                cols.append(str(l["source_column"]).upper())
    return out


def plan_edit(entry: Dict[str, Any], payload: Dict[str, Any], columns: Dict[str, List[str]]) -> Dict[str, Any]:
    """Every change of one store edit applied to the latest version, validated together: line edits, added and
    removed lines, design (grain, keys, dedup, SCD, strategy, filters) and the join plan. Pure; returns the new
    lines, design, a summary and the diff."""
    lines = [_line(l) for l in entry.get("lines") or []]
    design = dict(entry.get("table_design") or {})
    lines, edited = apply_edits(lines, payload.get("edits") or [])
    lines, added = add_lines(lines, payload.get("add_lines") or [])
    lines, removed = remove_lines(lines, payload.get("remove_lines") or []) if payload.get("remove_lines") else (lines, [])
    if payload.get("design"):
        design = apply_design(design, payload["design"], lines)
    joined = bool(payload.get("joins"))
    if joined:
        merged = {t: list(c) for t, c in columns_from_lines(lines).items()}
        for t, cs in (columns or {}).items():
            merged.setdefault(t.upper(), [])
            merged[t.upper()] += [c.upper() for c in cs if c.upper() not in merged[t.upper()]]
        design, lines = apply_joins(design, payload["joins"], merged, lines)
    elif design.get("join_graph"):
        lines = with_join_logic(lines, design["join_graph"])
    assert edited or added or removed or payload.get("design") or joined, "nothing to change"
    parts = [f"edited {', '.join(edited[:6])}" if edited else "", f"added {', '.join(added[:6])}" if added else "",
             f"removed {', '.join(removed[:6])}" if removed else "",
             "design" if payload.get("design") else "", "join plan" if joined else ""]
    summary = "; ".join(p for p in parts if p)
    return {"lines": lines, "design": design, "summary": summary, "edited": edited, "added": added, "removed": removed,
            "diff": diff(entry.get("lines") or [], lines)}


def _norm(value: Any) -> Any:
    if value in (None, "", []):
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, default=str)
    return value.strip() if isinstance(value, str) else value


def carry_store_edits(baseline: Dict[str, Any], latest: Dict[str, Any], lines: List[Dict[str, Any]],
                      design: Dict[str, Any]) -> Dict[str, Any]:
    """Re-apply to a regenerated STTM the changes made in the STTM Store since the run's last generation. Pure.

    `baseline` is the catalog version the last generation wrote and `latest` the run's newest version (lines and
    table_design each); `lines` and `design` are what generation just assembled and are updated in place. A field
    changed in the store takes the store's value while the regenerated value still equals the baseline (the new
    mapping did not touch it); when both changed, the regenerated value stays and the field is reported as a
    conflict. Lines added in the store are added back unless the mapping now produces that column; lines removed in
    the store are removed. Returns {applied, added, removed, conflicts}."""
    report: Dict[str, List[Any]] = {"applied": [], "added": [], "removed": [], "conflicts": []}
    base = {_key(l): l for l in baseline.get("lines") or []}
    late = {_key(l): l for l in latest.get("lines") or []}
    index = {_key(l): l for l in lines}
    for col in sorted(set(base) & set(late)):
        line = index.get(col)
        if line is None:
            continue
        for field in EDITABLE:
            before, after, now = base[col].get(field), late[col].get(field), line.get(field)
            if _norm(before) == _norm(after) or _norm(now) == _norm(after):
                continue
            if _norm(now) == _norm(before):
                line[field] = after
                line["human_approved"] = True
                report["applied"].append(f"{col}.{field}")
            else:
                report["conflicts"].append({"target_column": col, "field": field, "store": after, "regenerated": now})
    for col in sorted(set(late) - set(base)):
        if col in index:
            report["conflicts"].append({"target_column": col, "field": "line", "store": "added in the STTM Store",
                                        "regenerated": "mapped by the run"})
            continue
        added = {**{k: None for k in LINE_FIELDS}, **_line(late[col])}
        added.update(target_column=late[col].get("target_column") or col,
                     target_datatype=added.get("target_datatype") or "VARCHAR",
                     mapping_type=added.get("mapping_type") or "UNMAPPED", nullable_rule=True, uniqueness_rule=False,
                     accepted_values=[], human_approved=True, required=False)
        lines.append(added)
        report["added"].append(added["target_column"])
    for col in sorted(set(base) - set(late)):
        if col in index:
            lines.remove(index[col])
            report["removed"].append(col)
    base_design, late_design = baseline.get("table_design") or {}, latest.get("table_design") or {}
    for field in DESIGN_FIELDS:
        before, after, now = base_design.get(field), late_design.get(field), design.get(field)
        if _norm(before) == _norm(after) or _norm(now) == _norm(after):
            continue
        if _norm(now) == _norm(before):
            design[field] = after
            report["applied"].append(f"design.{field}")
        else:
            report["conflicts"].append({"target_column": None, "field": f"design.{field}", "store": after, "regenerated": now})
    return report


def store_edits_for_run(session, run_id: str, target_table_id: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """(baseline, latest) catalog versions of this run's mapping when the STTM Store changed it after the last
    generation, else None. Never raises (before V036 there is no catalog)."""
    try:
        found = rows(session, f"""SELECT VERSION, ORIGIN, LINES, TABLE_DESIGN FROM {TABLE}
                                   WHERE RUN_ID = ? AND TARGET_TABLE_ID = ? ORDER BY VERSION DESC LIMIT 50""",
                     [run_id, target_table_id])
    except Exception:
        return None
    versions = [{"version": int(r["VERSION"]), "origin": r["ORIGIN"], "lines": variant(r["LINES"]) or [],
                 "table_design": variant(r["TABLE_DESIGN"]) or {}} for r in found]
    baseline = next((v for v in versions if v["origin"] in ("GENERATED", "BACKFILL")), None)
    if not baseline or not versions:
        return None
    edited = [v for v in versions if v["version"] > baseline["version"] and v["origin"] == "CATALOG_EDIT"]
    return (baseline, versions[0]) if edited else None


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
                                                     BUSINESS_RULE, BUSINESS_DEFINITION, JOIN_LOGIC, FILTER_RULE
                                                FROM CORE.STTM_LINE WHERE STTM_ID = ? ORDER BY TARGET_COLUMN""",
                                       [sttm_id])]


def snapshot(session, sttm_id: str, origin: str = "GENERATED", note: str = "") -> Optional[Dict[str, Any]]:
    """Copy the current state of a run's STTM into the catalog as a new version. Never raises: the run step that
    called it has already succeeded, and a missing catalog (V036 not deployed) must not undo it."""
    try:
        assert origin in ORIGINS
        reg = rows(session, """SELECT S.STTM_ID, S.RUN_ID, S.DOMAIN_ID, S.TARGET_TABLE_ID, S.STATUS, S.TABLE_DESIGN,
                                      D.DOMAIN_NAME, T.TARGET_DATABASE, T.TARGET_SCHEMA, T.TARGET_TABLE
                                 FROM CORE.STTM_REGISTRY S
                                 LEFT JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = S.DOMAIN_ID
                                 LEFT JOIN CORE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = S.TARGET_TABLE_ID
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
        found = rows(session, """SELECT STTM_ID FROM CORE.STTM_REGISTRY
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


# ---------------------------------------------------------------- Snowpark: store edits (applied after approval)

LINE_COLUMNS = {"source_system": "SOURCE_SYSTEM", "source_table": "SOURCE_TABLE", "source_column": "SOURCE_COLUMN",
                "source_datatype": "SOURCE_DATATYPE", "target_datatype": "TARGET_DATATYPE", "mapping_type": "MAPPING_TYPE",
                "transformation": "TRANSFORMATION", "business_rule": "BUSINESS_RULE",
                "business_definition": "BUSINESS_DEFINITION", "join_logic": "JOIN_LOGIC", "filter_rule": "FILTER_RULE"}


def _latest(session, cid: str) -> Dict[str, Any]:
    found = rows(session, f"""SELECT CATALOG_ID, VERSION, STTM_ID, RUN_ID, DOMAIN_ID, DOMAIN_NAME, SOURCE_SYSTEM,
                                     TARGET_TABLE_ID, TARGET_ENTITY, TABLE_DESIGN, LINES
                                FROM {TABLE} WHERE CATALOG_ID = ? ORDER BY VERSION DESC LIMIT 1""", [cid])
    assert found, "mapping not found"
    r = {k.lower(): v for k, v in found[0].items()}
    r["table_design"] = variant(r.get("table_design")) or {}
    r["lines"] = variant(r.get("lines")) or []
    r["version"] = int(r["version"])
    return r


def edit_version(session, cid: str, payload_json: str, approved: bool = False) -> Dict[str, Any]:
    """Apply one store edit: a new catalog version (APPROVED when it came through an approval, else REVIEW) and the
    same change written back to the run that produced the mapping, so the next dbt generation uses it."""
    payload = json.loads(payload_json or "{}")
    latest = _latest(session, cid)
    base = payload.get("base_version")
    assert base is None or int(base) == latest["version"], \
        f"version {latest['version']} was saved since you opened v{base}; reload and apply your change again"
    columns: Dict[str, List[str]] = {}
    if latest.get("run_id") and payload.get("joins"):
        try:
            from services.sttm.procedures import _run_columns

            columns = {t: [c["column_name"] for c in cs] for t, cs in _run_columns(session, latest["run_id"]).items()}
        except Exception:
            columns = {}
    plan = plan_edit(latest, payload, columns)
    note = (str(payload.get("note") or "").strip() or plan["summary"])[:2000]
    written = write_version(session, cid, plan["lines"], "CATALOG_EDIT", note, sttm_id=latest.get("sttm_id"),
                            run_id=latest.get("run_id"), domain_id=latest.get("domain_id"),
                            domain_name=latest.get("domain_name"), source_system=latest.get("source_system"),
                            target_table_id=latest.get("target_table_id"), target_entity=latest.get("target_entity") or "",
                            status="APPROVED" if approved else "REVIEW", table_design=plan["design"])
    synced = sync_to_run(session, latest.get("sttm_id"), latest.get("run_id"), plan, written["version"], note)
    learned = learn_from_edit(session, latest, plan, note)
    return {**written, "status": "APPROVED" if approved else "REVIEW", "summary": plan["summary"],
            "diff": plan["diff"], "run_updated": synced, "learned": learned}


def edit_knowledge(entry: Dict[str, Any], diff: Dict[str, Any], note: str,
                   lines: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The knowledge items a store edit teaches (pure): a changed transformation as the target column's
    TRANSFORMATION_RULE (the same key the run's transform apply writes, so the newest decision wins), and a changed
    business rule as the column's BUSINESS_RULE. Cleared values teach nothing."""
    from services.sttm.refine import reusable_expression

    table = str(entry.get("target_entity") or "").split(".")[-1].strip('"').upper()
    full = {_key(l): l for l in lines or []}  # the edited version's lines: the diff holds only changed fields
    out: List[Dict[str, Any]] = []
    for change in diff.get("changed") or []:
        column = str(change.get("target_column") or "").upper()
        after = change.get("to") or {}
        line = {**(change.get("from") or {}), **full.get(str(change.get("target_column") or "").upper(), {}), **after}
        if not column:
            continue
        if "transformation" in change.get("fields", []) and after.get("transformation"):
            sql = str(after["transformation"])
            source = line.get("source_column")
            out.append({"kind": "TRANSFORMATION_RULE",
                        "key": f"transform.{table}.{column}" if table else f"transform.{column}",
                        "title": f"Transform {column}",
                        "content": f"{line.get('source_table')}.{source} -> {column}: {sql}. {note}".strip(),
                        "content_json": {"target_column": column, "target_table": table or None,
                                         "source_column": source, "source_table": line.get("source_table"),
                                         "expression": reusable_expression(sql, source), "sql": sql,
                                         "rationale": note, "catalog_id": entry.get("catalog_id")},
                        "tags": ["TRANSFORM", "STTM", "STORE_EDIT"]})
        if "business_rule" in change.get("fields", []) and after.get("business_rule"):
            out.append({"kind": "BUSINESS_RULE", "key": f"sttm.rule.{table}.{column}",
                        "title": f"{column} rule", "content": str(after["business_rule"]),
                        "content_json": {"target_column": column, "target_table": table or None,
                                         "rule": str(after["business_rule"]), "catalog_id": entry.get("catalog_id")},
                        "tags": ["STTM", "STORE_EDIT"]})
    return out


def learn_from_edit(session, entry: Dict[str, Any], plan: Dict[str, Any], note: str) -> int:
    """Store edit -> domain knowledge, so the next mapping, STTM and Soda runs use what the reviewer settled. Through
    the learning policy (services/knowledge/writer.remember); never fails the edit, which is already saved."""
    if not entry.get("domain_id"):
        return 0
    from services.knowledge.writer import remember

    learned = 0
    for item in edit_knowledge(entry, plan.get("diff") or {}, note, plan.get("lines")):
        try:
            remember(session, domain_id=entry["domain_id"], kind=item["kind"], key=item["key"], title=item["title"],
                     content=item["content"], content_json=item["content_json"], tags=item["tags"], origin="STTM",
                     run_id=entry.get("run_id"))
            learned += 1
        except Exception:
            continue
    return learned


def sync_to_run(session, sttm_id: Optional[str], run_id: Optional[str], plan: Dict[str, Any], version: int,
                note: str) -> bool:
    """Write the catalog edit back to the run's STTM (lines, design, joins, filters) and note on the run that dbt
    should be regenerated. Archived or deleted runs, and mappings without a run, are left alone."""
    if not sttm_id:
        return False
    reg = rows(session, """SELECT S.STTM_ID, R.RUN_ID, COALESCE(R.IS_ARCHIVED, FALSE) AS ARCHIVED, R.DELETED_AT
                             FROM CORE.STTM_REGISTRY S JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = S.RUN_ID
                            WHERE S.STTM_ID = ?""", [sttm_id])
    if not reg or reg[0].get("ARCHIVED") or reg[0].get("DELETED_AT"):
        return False
    existing = rows(session, """SELECT STTM_LINE_ID, TARGET_COLUMN, TARGET_DOMAIN, TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE
                                  FROM CORE.STTM_LINE WHERE STTM_ID = ?""", [sttm_id])
    by_col = {str(r["TARGET_COLUMN"]).upper(): r for r in existing}
    target = existing[0] if existing else None
    wanted = {_key(l): l for l in plan["lines"]}
    for col in sorted(set(by_col) - set(wanted)):
        session.sql("DELETE FROM CORE.STTM_LINE WHERE STTM_LINE_ID = ?", params=[by_col[col]["STTM_LINE_ID"]]).collect()
    for col, line in wanted.items():
        values = [line.get(f) for f in LINE_COLUMNS]
        if col in by_col:
            sets = ", ".join(f"{c} = ?" for c in LINE_COLUMNS.values())
            session.sql(f"UPDATE CORE.STTM_LINE SET {sets}, HUMAN_APPROVED = TRUE, REVIEWER = CURRENT_USER() "
                        f"WHERE STTM_LINE_ID = ?", params=[*values, by_col[col]["STTM_LINE_ID"]]).collect()
        elif target is not None:
            cols = ", ".join(LINE_COLUMNS.values())
            session.sql(f"""INSERT INTO CORE.STTM_LINE (STTM_LINE_ID, STTM_ID, TARGET_DOMAIN, TARGET_DATABASE,
                                   TARGET_SCHEMA, TARGET_TABLE, TARGET_COLUMN, {cols}, HUMAN_APPROVED, REVIEWER)
                            SELECT ?, ?, ?, ?, ?, ?, ?, {", ".join(["?"] * len(LINE_COLUMNS))}, TRUE, CURRENT_USER()""",
                        params=[str(uuid.uuid4()), sttm_id, target["TARGET_DOMAIN"], target["TARGET_DATABASE"],
                                target["TARGET_SCHEMA"], target["TARGET_TABLE"], col,
                                *[v if v is not None or f not in ("target_datatype", "mapping_type")
                                  else ("VARCHAR" if f == "target_datatype" else "UNMAPPED")
                                  for f, v in zip(LINE_COLUMNS, values)]]).collect()
    session.sql("UPDATE CORE.STTM_REGISTRY SET TABLE_DESIGN = PARSE_JSON(?) WHERE STTM_ID = ?",
                params=[json.dumps(plan["design"], default=str), sttm_id]).collect()
    try:
        from services.workflow.procedures import _get_run, _record_run_event

        _record_run_event(session, _get_run(session, run_id or reg[0]["RUN_ID"]), "HUMAN",
                          "STTM changed in the STTM Store; regenerate dbt",
                          {"catalog_version": version, "summary": note[:500]})
    except Exception:
        pass
    return True
