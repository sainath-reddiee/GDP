"""Table Model Store: one persistent, versioned model per modeled table (V044), reopened and edited in the Modeling
Store at any time.

  CORE.TABLE_MODEL          one row per (source system, table): origin (the run that designed it, or ADHOC),
                                  layer, table type, latest version, diagram layout, the domain model entity it feeds
  CORE.TABLE_MODEL_VERSION  every save is a new immutable version: a single-entity v2 specification, its issues,
                                  the change summary against the previous version and the DDL and dbt rendered from it

A table model is created by a run (services/modeling/table_model_sync.register_from_run, on every saved design) or ad
hoc from a landed source table. Its specification is the domain model's v2 shape with exactly one entity, so the
Model Workbench renderers (render_v2), diff (model_diff) and editors are reused as they are.

A table model never writes the governed domain model: `propose` sends its entity to the domain's model as a
CORE.MODEL_CHANGE (services/modeling/run_link.propose_entities), merged and approved in the Model Workbench.
`publish` pushes the table's DDL and dbt files to a branch feature/model-<table>-v<version> (services/dbt/publish.py
publish_table_model), recorded in CORE.GIT_PR as kind TABLE_MODEL.

Every function takes the caller's user name explicitly, so the rules are testable and the audit columns are right
whichever session runs the SQL.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.modeling import model_diff, render_v2, spec_v2
from services.modeling.domain_model import Conflict

MODELS = "CORE.TABLE_MODEL"
VERSIONS = "CORE.TABLE_MODEL_VERSION"
LAYERS = ("LANDING", "BRONZE", "SILVER", "GOLD")
TABLE_TYPES = ("ICEBERG", "STANDARD", "DYNAMIC_TABLE")
ORIGINS = ("RUN", "ADHOC", "IMPORT")
# column classification, kept in the attribute's semantic_type
CLASSIFICATIONS = ("BUSINESS_KEY", "SURROGATE_KEY", "ATTRIBUTE", "AUDIT_TIMESTAMP", "MEASURE", "RECORD_SOURCE")


def table_model_id(source_name: str, table_name: str) -> str:
    """md5('table:' || SOURCE_NAME || '|' || TABLE_NAME) as a UUID: the same table of the same source is one model,
    whichever run (or person) creates it."""
    h = hashlib.md5(f"table:{str(source_name or '').upper()}|{str(table_name or '').upper()}".encode("utf-8")).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _low(r: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in r.items()}


def _ts(value: Any) -> Optional[str]:
    return str(value)[:19] if value else None


def _name(value: Any) -> str:
    return re.sub(r"[^A-Z0-9_$]+", "_", str(value or "").strip().upper()).strip("_")


# ---------------------------------------------------------------- specification (pure)


def platform_for(table_type: str) -> str:
    return "ICEBERG" if str(table_type or "").upper() == "ICEBERG" else "SNOWFLAKE"


def single(raw: Dict[str, Any], model_id: str, *, domain: str = "", table_type: str = "STANDARD",
           table_name: str = "") -> Dict[str, Any]:
    """Any specification (v1 run design, v2, or a bare entity) as a single-entity v2 specification: the first active
    entity (or the one named `table_name`) is kept, relationships are dropped (the other ends live in the domain
    model), the platform follows the table type and the domain is the one the table belongs to."""
    raw = dict(raw or {})
    if "entity_name" in raw and "design" not in raw and "entities" not in raw:
        raw = {"design": {"entities": [raw]}}
    spec = spec_v2.normalize_v2({**raw, "relationships": []}, model_id)
    entities = spec["design"]["entities"]
    pick = next((e for e in entities if table_name and e["entity_name"] == _name(table_name)), None) or \
        next((e for e in entities if e.get("status") != "RETIRED"), None) or (entities[0] if entities else None)
    spec["design"]["entities"] = [pick] if pick else []
    spec["design"]["primary_entity"] = pick["entity_name"] if pick else ""
    platform = platform_for(table_type)
    spec["physical_defaults"]["platform"] = spec["target_platform"] = platform
    spec["domain"] = _name(domain) or spec.get("domain") or "TABLE_MODEL"
    return spec


def validate(spec: Dict[str, Any]) -> List[Dict[str, str]]:
    issues = spec_v2.validate(spec)
    if len(spec["design"]["entities"]) != 1:
        issues.insert(0, {"path": "model", "severity": "ERROR", "message": "a table model has exactly one table"})
    for e in spec["design"]["entities"]:
        for a in e["attributes"]:
            kind = a.get("semantic_type")
            if kind and kind not in CLASSIFICATIONS and kind not in ("DERIVED_KEY",):
                issues.append({"path": f"{e['entity_name']}.{a['name']}", "severity": "WARN",
                               "message": f"{kind} is not a table model classification"})
    return issues


def render(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The DDL and the dbt files (sources, staging, mart, schema tests) of a table model specification."""
    try:
        ddl = render_v2.render_ddl(spec)
    except Exception as exc:  # an unfinished draft still renders what it can
        ddl = f"-- the DDL cannot be rendered yet: {clip(exc, 300)}\n"
    try:
        dbt = render_v2.render_dbt(spec, expressions=True)
    except Exception as exc:
        dbt = {"README.md": f"The dbt files cannot be rendered yet: {clip(exc, 300)}\n"}
    return {"ddl": ddl, "dbt": dbt}


def files(spec: Dict[str, Any], layer: str = "SILVER") -> Dict[str, str]:
    """The repository files of a table model pull request: its DDL beside the dbt project files."""
    entity = spec["design"]["entities"][0]
    table = render_v2._snake(entity["entity_name"])
    out = {f"models/ddl/{str(layer or 'SILVER').lower()}/{table}.sql": render_v2.render_ddl(spec)}
    out.update(render_v2.render_dbt(spec, expressions=True))
    return out


# ---------------------------------------------------------------- reads


HEADER_FIELDS = """M.TABLE_MODEL_ID, M.TABLE_NAME, M.SOURCE_ID, M.SOURCE_NAME, M.SOURCE_TABLES, M.DOMAIN_ID, M.ORIGIN,
                   M.ORIGIN_RUN_ID, M.LAST_RUN_ID, M.MODEL_LAYER, M.TABLE_TYPE, M.CURRENT_VERSION, M.ETAG,
                   M.DIAGRAM_LAYOUT, M.LINKED_MODEL_ID, M.LINKED_ENTITY, M.LAST_CHANGE_ID, M.CREATED_BY, M.CREATED_AT,
                   M.UPDATED_BY, M.UPDATED_AT, D.DOMAIN_NAME, R.RUN_NAME AS ORIGIN_RUN_NAME"""
HEADER_FROM = f"""{MODELS} M
                  LEFT JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = M.DOMAIN_ID
                  LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = M.ORIGIN_RUN_ID"""


def _header_row(r: Dict[str, Any]) -> Dict[str, Any]:
    h = _low(r)
    h["source_tables"] = variant(h.get("source_tables")) or []
    h["diagram_layout"] = variant(h.get("diagram_layout")) or {}
    h["current_version"] = int(h["current_version"]) if h.get("current_version") is not None else None
    for k in ("created_at", "updated_at"):
        h[k] = _ts(h.get(k))
    return h


def _header(session, model_id: str) -> Dict[str, Any]:
    found = rows(session, f"SELECT {HEADER_FIELDS} FROM {HEADER_FROM} WHERE M.TABLE_MODEL_ID = ?", [model_id])
    assert found, "table model not found"
    return _header_row(found[0])


VERSION_FIELDS = """VERSION, STATUS, CHECKSUM, ISSUES, CHANGE_NOTE, CHANGE_SUMMARY, ORIGIN, ORIGIN_RUN_ID,
                    RUN_DESIGN_VERSION, REPO_ID, BRANCH, PR_URL, PR_NUMBER, PR_STATE, MERGED_SHA, PR_SYNCED_AT,
                    CREATED_BY, CREATED_AT"""


def _version_meta(r: Dict[str, Any]) -> Dict[str, Any]:
    v = _low(r)
    v["version"] = int(v["version"])
    v["issues"] = variant(v.get("issues")) or []
    summary = variant(v.get("change_summary")) or {}
    v["change_summary"] = {"counts": summary.get("counts") or {}, "breaking": bool(summary.get("breaking"))} if summary else None
    for k in ("created_at", "pr_synced_at"):
        v[k] = _ts(v.get(k))
    for k in ("pr_number", "run_design_version"):
        v[k] = int(v[k]) if v.get(k) is not None else None
    return v


def _versions(session, model_id: str) -> List[Dict[str, Any]]:
    return [_version_meta(r) for r in rows(session, f"SELECT {VERSION_FIELDS} FROM {VERSIONS} WHERE TABLE_MODEL_ID = ? "
                                                    "ORDER BY VERSION DESC", [model_id])]


def _spec(session, model_id: str, version: int, header: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    found = rows(session, f"SELECT SPEC FROM {VERSIONS} WHERE TABLE_MODEL_ID = ? AND VERSION = ?", [model_id, int(version)])
    assert found, f"version {version} not found"
    h = header or _header(session, model_id)
    return single(variant(found[0]["SPEC"]) or {}, model_id, domain=h.get("domain_name") or h.get("source_name") or "",
                  table_type=h.get("table_type") or "STANDARD")


def list_models(session, domain_id: str = "", source_name: str = "", origin_run_id: str = "", layer: str = "",
                search: str = "") -> List[Dict[str, Any]]:
    """Every table model with its latest version's summary (columns, keys, status, pull request). `search` matches
    the table, the source system, the source tables and any column name."""
    where, params = [], []
    if domain_id:
        where.append("M.DOMAIN_ID = ?")
        params.append(domain_id)
    if source_name:
        where.append("UPPER(M.SOURCE_NAME) = UPPER(?)")
        params.append(source_name)
    if origin_run_id:
        where.append("(M.ORIGIN_RUN_ID = ? OR M.LAST_RUN_ID = ?)")
        params += [origin_run_id, origin_run_id]
    if layer:
        where.append("M.MODEL_LAYER = ?")
        params.append(layer.upper())
    if search and search.strip():
        like = f"%{search.strip()[:100]}%"
        where.append(f"""(M.TABLE_NAME ILIKE ? OR M.SOURCE_NAME ILIKE ? OR ARRAY_TO_STRING(M.SOURCE_TABLES, ',') ILIKE ?
                          OR EXISTS (SELECT 1 FROM {VERSIONS} S, LATERAL FLATTEN(INPUT => S.SPEC:design:entities) E,
                                                LATERAL FLATTEN(INPUT => E.VALUE:attributes) A
                                      WHERE S.TABLE_MODEL_ID = M.TABLE_MODEL_ID AND S.VERSION = M.CURRENT_VERSION
                                        AND A.VALUE:name::VARCHAR ILIKE ?))""")
        params += [like, like, like, like]
    found = rows(session, f"""SELECT {HEADER_FIELDS}, V.STATUS, V.SPEC, V.ISSUES, V.BRANCH, V.PR_URL, V.PR_STATE,
                                     V.CREATED_AT AS VERSION_AT
                                FROM {HEADER_FROM}
                                LEFT JOIN {VERSIONS} V ON V.TABLE_MODEL_ID = M.TABLE_MODEL_ID AND V.VERSION = M.CURRENT_VERSION
                               {"WHERE " + " AND ".join(where) if where else ""}
                               ORDER BY M.UPDATED_AT DESC NULLS LAST, M.TABLE_NAME LIMIT 1000""", params)
    out = []
    for r in found:
        r = _low(r)
        spec = variant(r.pop("spec", None)) or {}
        issues = variant(r.pop("issues", None)) or []
        version_at = r.pop("version_at", None)
        h = _header_row(r)
        entity = ((spec.get("design") or {}).get("entities") or [{}])[0] or {}
        attrs = [a for a in entity.get("attributes") or [] if a.get("status") != "RETIRED"]
        h.update(status=r.get("status"), branch=r.get("branch"), pr_url=r.get("pr_url"), pr_state=r.get("pr_state"),
                 version_at=_ts(version_at), columns=len(attrs),
                 primary_keys=[a.get("name") for a in attrs if a.get("is_pk")],
                 errors=len([i for i in issues if i.get("severity") == "ERROR"]))
        h.pop("diagram_layout", None)
        h.pop("etag", None)
        out.append(h)
    return out


def get_model(session, model_id: str, version: Optional[int] = None) -> Dict[str, Any]:
    """The header, every version (newest first, without specifications) and one version (the asked one, else the
    latest) with its specification, issues and rendered DDL and dbt."""
    header = _header(session, model_id)
    versions = _versions(session, model_id)
    pick = int(version or header.get("current_version") or 0)
    current = None
    if pick:
        meta = next((v for v in versions if v["version"] == pick), None)
        assert meta, f"version {pick} not found"
        spec = _spec(session, model_id, pick, header)
        current = {**meta, "spec": spec, "issues": validate(spec), "summary": spec_v2.summary(spec), **render(spec)}
    return {"model": header, "versions": versions, "current": current}


def diff_versions(session, model_id: str, from_version: Optional[int], to_version: int) -> Dict[str, Any]:
    header = _header(session, model_id)
    old = _spec(session, model_id, int(from_version), header) if from_version else None
    return {"from": from_version, "to": int(to_version),
            **model_diff.diff(old, _spec(session, model_id, int(to_version), header))}


# ---------------------------------------------------------------- writes


def _insert_version(session, model_id: str, version: int, spec: Dict[str, Any], previous: Optional[Dict[str, Any]],
                    origin: str, user: str, note: str = "", run_id: str = "", design_version: int = 0) -> Dict[str, Any]:
    issues = validate(spec)
    changes = model_diff.diff(previous, spec)
    rendered = render(spec)
    insert_rows(session, VERSIONS,
                ["TABLE_MODEL_ID", "VERSION", "STATUS", "SPEC", "CHECKSUM", "ISSUES", "CHANGE_NOTE", "CHANGE_SUMMARY",
                 "GENERATED_DDL", "GENERATED_DBT", "ORIGIN", "ORIGIN_RUN_ID", "RUN_DESIGN_VERSION", "CREATED_BY"],
                ["?", "?::NUMBER", "'DRAFT'", "PARSE_JSON(?)", "?", "PARSE_JSON(?)", "NULLIF(?, '')", "PARSE_JSON(?)",
                 "?", "PARSE_JSON(?)", "?", "NULLIF(?, '')", "NULLIF(?, '')::NUMBER", "?"],
                [[model_id, int(version), spec, spec_v2.checksum(spec), issues, clip(note, 2000),
                  {"counts": changes["counts"], "breaking": changes["breaking"], "changes": changes["changes"][:200]},
                  rendered["ddl"], rendered["dbt"], origin, run_id, int(design_version) if design_version else "",
                  user.upper()]])
    return {"issues": issues, "counts": changes["counts"], "breaking": changes["breaking"]}


def create(session, payload: Dict[str, Any], user: str) -> Dict[str, Any]:
    """A new table model with its version 1. `payload`: table_name, source_name, and optionally source_id,
    source_tables, domain_id, origin (ADHOC by default), origin_run_id, run_design_version, model_layer, table_type,
    spec (any shape `single` reads) and change_note. Refused when the source already has a model of that table."""
    table = _name(payload.get("table_name") or "")
    source = str(payload.get("source_name") or "").strip().upper()
    assert spec_v2.NAME.match(table or "-"), "name the table (letters, digits, _ and $)"
    assert source, "name the source system"
    model_id = table_model_id(source, table)
    assert not scalar(session, f"SELECT COUNT(*) FROM {MODELS} WHERE TABLE_MODEL_ID = ?", [model_id]), \
        f"{source} already has a model of {table}; open it instead"
    layer = str(payload.get("model_layer") or "SILVER").upper()
    table_type = str(payload.get("table_type") or "STANDARD").upper()
    origin = str(payload.get("origin") or "ADHOC").upper()
    assert layer in LAYERS, f"layer must be one of {list(LAYERS)}"
    assert table_type in TABLE_TYPES, f"table type must be one of {list(TABLE_TYPES)}"
    assert origin in ORIGINS, f"origin must be one of {list(ORIGINS)}"
    domain_name = ""
    if payload.get("domain_id"):
        found = rows(session, "SELECT DOMAIN_NAME FROM CORE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [payload["domain_id"]])
        domain_name = found[0]["DOMAIN_NAME"] if found else ""
    raw = payload.get("spec") or {"design": {"entities": [{"entity_name": table, "attributes": payload.get("columns") or []}]}}
    spec = single(raw, model_id, domain=domain_name or source, table_type=table_type, table_name=table)
    assert spec["design"]["entities"], "the table model needs its table"
    spec["design"]["entities"][0]["entity_name"] = table
    spec["design"]["primary_entity"] = table
    etag = uuid.uuid4().hex
    insert_rows(session, MODELS,
                ["TABLE_MODEL_ID", "TABLE_NAME", "SOURCE_ID", "SOURCE_NAME", "SOURCE_TABLES", "DOMAIN_ID", "ORIGIN",
                 "ORIGIN_RUN_ID", "LAST_RUN_ID", "MODEL_LAYER", "TABLE_TYPE", "CURRENT_VERSION", "ETAG", "CREATED_BY",
                 "UPDATED_BY"],
                ["?", "?", "NULLIF(?, '')", "?", "PARSE_JSON(?)::ARRAY", "NULLIF(?, '')", "?", "NULLIF(?, '')",
                 "NULLIF(?, '')", "?", "?", "1", "?", "?", "?"],
                [[model_id, table, payload.get("source_id") or "", source,
                  [str(t).upper() for t in payload.get("source_tables") or []][:100], payload.get("domain_id") or "",
                  origin, payload.get("origin_run_id") or "", payload.get("origin_run_id") or "", layer, table_type, etag,
                  user.upper(), user.upper()]])
    out = _insert_version(session, model_id, 1, spec, None, "RUN" if origin == "RUN" else origin, user,
                          payload.get("change_note") or ("Designed in a run" if origin == "RUN" else "Model created"),
                          payload.get("origin_run_id") or "", int(payload.get("run_design_version") or 0))
    return {"table_model_id": model_id, "version": 1, "etag": etag, **out}


def save(session, model_id: str, payload: Dict[str, Any], user: str) -> Dict[str, Any]:
    """A new version from the editor. `etag` must be the one the caller read (a stale etag is a Conflict, so two
    modelers never overwrite each other); layer and table type may change with it. Nothing changes when the
    specification, layer and table type are all the same as the latest version's."""
    header = _header(session, model_id)
    if str(payload.get("etag") or "") != str(header.get("etag") or ""):
        raise Conflict(f"CONFLICT: {header.get('updated_by') or 'someone'} saved this table model since you opened it; "
                       "reload to see their changes")
    layer = str(payload.get("model_layer") or header.get("model_layer") or "SILVER").upper()
    table_type = str(payload.get("table_type") or header.get("table_type") or "STANDARD").upper()
    assert layer in LAYERS, f"layer must be one of {list(LAYERS)}"
    assert table_type in TABLE_TYPES, f"table type must be one of {list(TABLE_TYPES)}"
    spec = single(payload.get("spec") or {}, model_id, domain=header.get("domain_name") or header["source_name"],
                  table_type=table_type)
    assert spec["design"]["entities"], "the table model needs its table"
    current = int(header.get("current_version") or 0)
    previous = _spec(session, model_id, current, header) if current else None
    if previous is not None and spec_v2.checksum(spec) == spec_v2.checksum(previous) and \
            layer == header.get("model_layer") and table_type == header.get("table_type"):
        return {"table_model_id": model_id, "version": current, "etag": header.get("etag"), "unchanged": True,
                "issues": validate(spec)}
    return _next_version(session, header, spec, previous, "USER", user, payload.get("change_note") or "",
                         layer=layer, table_type=table_type)


def _next_version(session, header: Dict[str, Any], spec: Dict[str, Any], previous: Optional[Dict[str, Any]],
                  origin: str, user: str, note: str, *, layer: Optional[str] = None, table_type: Optional[str] = None,
                  run_id: str = "", design_version: int = 0, source_tables: Optional[List[str]] = None) -> Dict[str, Any]:
    model_id = header["table_model_id"]
    version = int(scalar(session, f"SELECT COALESCE(MAX(VERSION), 0) FROM {VERSIONS} WHERE TABLE_MODEL_ID = ?",
                         [model_id]) or 0) + 1
    out = _insert_version(session, model_id, version, spec, previous, origin, user, note, run_id, design_version)
    etag = uuid.uuid4().hex
    session.sql(f"""UPDATE {MODELS} SET CURRENT_VERSION = ?, ETAG = ?, MODEL_LAYER = ?, TABLE_TYPE = ?,
                           LAST_RUN_ID = COALESCE(NULLIF(?, ''), LAST_RUN_ID),
                           SOURCE_TABLES = COALESCE(PARSE_JSON(NULLIF(?, ''))::ARRAY, SOURCE_TABLES),
                           UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP()
                     WHERE TABLE_MODEL_ID = ? AND COALESCE(ETAG, '') = ?""",
                params=[version, etag, layer or header.get("model_layer") or "SILVER",
                        table_type or header.get("table_type") or "STANDARD", run_id or "",
                        json.dumps(source_tables) if source_tables else "", user.upper(), model_id,
                        header.get("etag") or ""]).collect()
    now = scalar(session, f"SELECT ETAG FROM {MODELS} WHERE TABLE_MODEL_ID = ?", [model_id])
    if now != etag:
        session.sql(f"DELETE FROM {VERSIONS} WHERE TABLE_MODEL_ID = ? AND VERSION = ?", params=[model_id, version]).collect()
        raise Conflict("CONFLICT: the table model was saved by someone else at the same moment; reload to see their changes")
    return {"table_model_id": model_id, "version": version, "etag": etag, **out}


def set_layout(session, model_id: str, layout: Dict[str, Any], user: str) -> Dict[str, Any]:
    """Diagram positions {entity_id: {x, y}}: cosmetic, saved without a new version."""
    assert isinstance(layout, dict), "the layout is an object"
    clean = {str(k)[:64]: {"x": round(float(v.get("x", 0)), 1), "y": round(float(v.get("y", 0)), 1)}
             for k, v in list(layout.items())[:200] if isinstance(v, dict)}
    _header(session, model_id)
    session.sql(f"UPDATE {MODELS} SET DIAGRAM_LAYOUT = PARSE_JSON(?), UPDATED_AT = CURRENT_TIMESTAMP() WHERE TABLE_MODEL_ID = ?",
                params=[json.dumps(clean), model_id]).collect()
    return {"table_model_id": model_id, "positions": len(clean)}


def link(session, model_id: str, domain_id: str, user: str) -> Dict[str, Any]:
    """Set the domain the table belongs to (its proposals go to that domain's model)."""
    header = _header(session, model_id)
    found = rows(session, "SELECT DOMAIN_NAME FROM CORE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [domain_id])
    assert found, "domain not found"
    session.sql(f"UPDATE {MODELS} SET DOMAIN_ID = ?, UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE TABLE_MODEL_ID = ?",
                params=[domain_id, user.upper(), model_id]).collect()
    return {"table_model_id": model_id, "domain_id": domain_id, "domain_name": found[0]["DOMAIN_NAME"],
            "previous": header.get("domain_id")}


def propose(session, model_id: str, version: int, user: str) -> Dict[str, Any]:
    """Send a version's table to its domain's governed model as a proposal (or, when the approved model already has
    it exactly, link to it). The domain model changes only when a modeler merges and approves the proposal."""
    from services.modeling import run_link

    header = _header(session, model_id)
    assert header.get("domain_id"), "choose the domain this table belongs to first"
    spec = _spec(session, model_id, int(version), header)
    problems = spec_v2.blocking(validate(spec))
    assert not problems, f"fix {len(problems)} problem(s) before proposing: {problems[0]['path']}: {problems[0]['message']}"
    entity = spec["design"]["entities"][0]
    db, schema = spec_v2.placement(spec, entity)
    incoming = {"target_database": spec["physical_defaults"]["database"], "target_schema": spec["physical_defaults"]["schema"],
                "design": {"entities": [{**entity, "id": None, "database": db or None, "schema": schema or None}]}}
    for a in incoming["design"]["entities"][0]["attributes"]:
        a.pop("id", None)
    result = run_link.propose_entities(session, header["domain_id"], incoming, entity["entity_name"], user,
                                       table_model_id=model_id, table_model_version=int(version),
                                       label=f"table model {header['source_name']}.{header['table_name']}")
    session.sql(f"""UPDATE {MODELS} SET LINKED_MODEL_ID = ?, LINKED_ENTITY = ?, LAST_CHANGE_ID = NULLIF(?, ''),
                           UPDATED_AT = CURRENT_TIMESTAMP() WHERE TABLE_MODEL_ID = ?""",
                params=[result.get("model_id"), entity["entity_name"], result.get("change_id") or "", model_id]).collect()
    return result


def linked_entities(session, header: Dict[str, Any]) -> Dict[str, Any]:
    """The approved domain model's entities related to this table (for the diagram): the table itself as the domain
    knows it, the entities it references or is referenced by, and those relationships."""
    model = rows(session, "SELECT MODEL_ID, APPROVED_VERSION FROM CORE.DOMAIN_MODEL WHERE DOMAIN_ID = ? "
                         "AND COALESCE(LAYER, 'SILVER') = 'SILVER'",
                 [header.get("domain_id") or ""]) if header.get("domain_id") else []
    if not model or not model[0].get("APPROVED_VERSION"):
        return {"entities": [], "relationships": [], "model_id": None}
    from services.modeling import domain_model

    spec = spec_v2.active(domain_model._spec(session, model[0]["MODEL_ID"], int(model[0]["APPROVED_VERSION"])))
    name = header["table_name"]
    rels = [r for r in spec.get("relationships") or [] if name in (r["from_entity"], r["to_entity"])]
    names = {r["from_entity"] for r in rels} | {r["to_entity"] for r in rels}
    names.discard(name)
    return {"model_id": model[0]["MODEL_ID"], "version": int(model[0]["APPROVED_VERSION"]),
            "entities": [e for e in spec["design"]["entities"] if e["entity_name"] in names][:30],
            "relationships": rels[:60],
            "in_domain_model": any(e["entity_name"] == name for e in spec["design"]["entities"])}


# ---------------------------------------------------------------- source tables (ad hoc models)


def source_tables(session, search: str = "") -> List[Dict[str, Any]]:
    """Landed source tables a model can be created from (the latest landing of each), with whether one exists."""
    where, params = ["L.INGESTION_STATUS = 'COMPLETE'"], []
    if search and search.strip():
        like = f"%{search.strip()[:100]}%"
        where.append("(L.SOURCE_TABLE ILIKE ? OR S.SOURCE_SYSTEM_NAME ILIKE ?)")
        params += [like, like]
    found = rows(session, f"""SELECT L.LANDING_ID, L.RUN_ID, L.SOURCE_SYSTEM_ID, S.SOURCE_SYSTEM_NAME, S.DOMAIN_ID,
                                     L.SOURCE_DATABASE, L.SOURCE_SCHEMA, L.SOURCE_TABLE, L.ROW_COUNT,
                                     L.INGESTED_AT::VARCHAR AS INGESTED_AT
                                FROM CORE.LANDING_TABLE_REGISTRY L
                                LEFT JOIN CORE.SOURCE_REGISTRY S ON S.SOURCE_SYSTEM_ID = L.SOURCE_SYSTEM_ID
                               WHERE {' AND '.join(where)}
                             QUALIFY ROW_NUMBER() OVER (PARTITION BY L.SOURCE_SYSTEM_ID, L.SOURCE_DATABASE, L.SOURCE_SCHEMA,
                                                                     L.SOURCE_TABLE ORDER BY L.CREATED_AT DESC) = 1
                               ORDER BY S.SOURCE_SYSTEM_NAME, L.SOURCE_TABLE LIMIT 500""", params)
    existing = {r["TABLE_MODEL_ID"] for r in rows(session, f"SELECT TABLE_MODEL_ID FROM {MODELS}")}
    out = []
    for r in found:
        t = _low(r)
        t["source_name"] = str(t.get("source_system_name") or t.get("source_database") or "SOURCE").upper()
        t["table_model_id"] = table_model_id(t["source_name"], t["source_table"])
        t["exists"] = t["table_model_id"] in existing
        out.append(t)
    return out


def create_from_source(session, landing_id: str, payload: Dict[str, Any], user: str) -> Dict[str, Any]:
    """An ad hoc table model of a landed source table: one column per landed column (type, nullability, comment)."""
    found = rows(session, """SELECT L.LANDING_ID, L.RUN_ID, L.SOURCE_SYSTEM_ID, S.SOURCE_SYSTEM_NAME, S.DOMAIN_ID,
                                    L.SOURCE_DATABASE, L.SOURCE_SCHEMA, L.SOURCE_TABLE
                               FROM CORE.LANDING_TABLE_REGISTRY L
                               LEFT JOIN CORE.SOURCE_REGISTRY S ON S.SOURCE_SYSTEM_ID = L.SOURCE_SYSTEM_ID
                              WHERE L.LANDING_ID = ?""", [landing_id])
    assert found, "source table not found"
    t = _low(found[0])
    columns = [{"name": c["COLUMN_NAME"], "datatype": c["DATA_TYPE"], "nullable": c["NULLABLE"] is not False,
                "description": c.get("SOURCE_COMMENT") or "",
                "source_columns": [f"{t['source_table']}.{c['COLUMN_NAME']}".upper()]}
               for c in rows(session, """SELECT COLUMN_NAME, DATA_TYPE, NULLABLE, SOURCE_COMMENT
                                           FROM CORE.LANDING_COLUMN_REGISTRY WHERE LANDING_ID = ?
                                          ORDER BY ORDINAL_POSITION""", [landing_id])]
    table = _name(payload.get("table_name") or t["source_table"])
    fqn = ".".join(str(p).upper() for p in (t["source_database"], t["source_schema"], t["source_table"]) if p)
    return create(session, {"table_name": table, "source_name": t.get("source_system_name") or t["source_database"],
                            "source_id": t.get("source_system_id"), "source_tables": [fqn],
                            "domain_id": payload.get("domain_id") or t.get("domain_id"), "origin": "ADHOC",
                            "model_layer": payload.get("model_layer") or "SILVER",
                            "table_type": payload.get("table_type") or "STANDARD",
                            "columns": columns, "change_note": f"Created from source table {fqn}"}, user)


# ---------------------------------------------------------------- AI suggestions


def _suggest_spec():
    from services.common.ai_suggest import Spec

    s, o = {"type": "string"}, {"type": ["string", "null"]}
    return Spec(
        "TABLE_MODEL", 1,
        "You review the columns of one table model. RULE RESULT per column is its data type, classification "
        f"({', '.join(CLASSIFICATIONS)}), primary key flag and description. Suggest a classification where the name, "
        "type, profile or key flags clearly point elsewhere, and a short business description (one sentence) for "
        "columns that have none.",
        {"type": "object", "properties": {"column": s, "classification": o, "description": o, "reason": s},
         "required": ["column", "reason"]},
        lambda i: f"{str(i.get('column')).upper()}|{i.get('classification') or ''}|{(i.get('description') or '')[:60]}",
        ["column", "reason"])


def suggest(session, model_id: str, spec_raw: Optional[Dict[str, Any]], refresh: bool = False) -> Dict[str, Any]:
    """Column classifications and descriptions suggested by the model for the editor's specification (cached per
    content; nothing is applied until the modeler accepts it in the editor and saves)."""
    from services.common.ai_suggest import suggest as ai_suggest

    header = _header(session, model_id)
    spec = single(spec_raw or {}, model_id, domain=header.get("domain_name") or header["source_name"],
                  table_type=header.get("table_type") or "STANDARD") if spec_raw else \
        _spec(session, model_id, int(header["current_version"]), header)
    entity = spec["design"]["entities"][0]
    context = {"table": entity["entity_name"], "source": header["source_name"], "domain": header.get("domain_name"),
               "grain": entity.get("grain"), "description": entity.get("description"),
               "columns": [{"column": a["name"], "datatype": a["datatype"], "classification": a.get("semantic_type"),
                            "is_pk": a.get("is_pk"), "nullable": a.get("nullable"), "description": a.get("description"),
                            "source_columns": a.get("source_columns")}
                           for a in entity["attributes"] if a.get("status") != "RETIRED"][:300]}
    out = ai_suggest(session, _suggest_spec(), f"table_model:{model_id}", context, None, refresh)
    names = {a["name"] for a in entity["attributes"]}
    out["items"] = [dict(i, column=str(i["column"]).upper(),
                         classification=(str(i.get("classification") or "").upper() or None)
                         if str(i.get("classification") or "").upper() in CLASSIFICATIONS else None)
                    for i in out.get("items") or [] if str(i.get("column") or "").upper() in names]
    return out
