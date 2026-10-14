"""Domain model specification, version 2 (pure).

The Model Workbench keeps one canonical, versioned model per domain (GOVERNANCE.DOMAIN_MODEL, versions in
GOVERNANCE.MODEL_DESIGNS). Version 2 of the specification is a superset of the Modeling hub's version 1
(services/catalog/models.py normalize_spec), so every v1 renderer keeps working, and it carries everything the domain
registry knows, so a model can round-trip through KNOWLEDGE.TARGET_TABLE_REGISTRY / TARGET_COLUMN_REGISTRY and a
domain_pack.json without losing anything:

  model         domain, description, subject_areas, naming_standard, physical_defaults (platform, database, schema,
                iceberg), relationships, design.entities
  entity        id (the registry's TARGET_TABLE_ID once registered), entity_name, table_type (DIMENSION, FACT, HUB,
                SPOKE, ...; unknown types are kept), kind (the v1 subset), scd_type, grain, business_keys, description,
                subject_area, status (ACTIVE | RETIRED), database/schema overrides, model_spec (hub/spoke settings,
                passed through untouched), unique_constraints, checks, physical (cluster_by, transient, retention_days)
  attribute     id (TARGET_COLUMN_ID once registered), name, datatype, nullable, is_pk, description,
                business_definition, semantic_type, pii {flag, classification}, accepted_values, default, is_unique,
                check, status, tags, and the v1 lineage fields (source_columns, derived, transformation_hint, rationale)
  relationship  id, name, from/to entity and column, cardinality, from_optional, to_optional, enforce (RELY | NONE)

Ids are what make a rename a rename (and not a drop plus an add) in services/modeling/model_diff.py.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

SPEC_VERSION = 2
LAYERS = ("SILVER", "GOLD")
TABLE_TYPES = ("DIMENSION", "FACT", "BRIDGE", "REFERENCE", "TABLE", "HUB", "SPOKE", "LINK", "SAT")
V1_KINDS = ("DIMENSION", "FACT", "BRIDGE", "REFERENCE", "TABLE")
STATUSES = ("ACTIVE", "RETIRED")
CARDINALITIES = ("N:1", "1:1", "1:N")
ENFORCE = ("RELY", "NONE")
PLATFORMS = ("SNOWFLAKE", "ICEBERG")
MAX_IDENTIFIER = 255
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_NS = uuid.UUID("0b8f2c9e-6a41-4d7a-9e35-7c1d2f60ab42")

# Snowflake reserved keywords (ANSI and Snowflake-specific) that cannot be used unquoted as identifiers.
RESERVED = frozenset("""
ACCOUNT ALL ALTER AND ANY AS BETWEEN BY CASE CAST CHECK COLUMN CONNECT CONNECTION CONSTRAINT CREATE CROSS CURRENT
CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER DATABASE DELETE DISTINCT DROP ELSE EXISTS FALSE FOLLOWING
FOR FROM FULL GRANT GROUP GSCLUSTER HAVING ILIKE IN INCREMENT INNER INSERT INTERSECT INTO IS ISSUE JOIN LATERAL LEFT
LIKE LOCALTIME LOCALTIMESTAMP MINUS NATURAL NOT NULL OF ON OR ORDER ORGANIZATION QUALIFY REGEXP REVOKE RIGHT RLIKE ROW
ROWS SAMPLE SCHEMA SELECT SET SOME START TABLE TABLESAMPLE THEN TO TRIGGER TRUE TRY_CAST UNION UNIQUE UPDATE USING
VALUES VIEW WHEN WHENEVER WHERE WINDOW WITH
""".split())


def stable_id(*parts: Any) -> str:
    """A deterministic id for something that has no registry id yet (same inputs, same id)."""
    return str(uuid.uuid5(_NS, "|".join(str(p or "").upper() for p in parts)))


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def _list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, list) else [loaded]
        except ValueError:
            return [v.strip() for v in value.split(",") if v.strip()]
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _pii(raw: Dict[str, Any]) -> Dict[str, Any]:
    pii = raw.get("pii")
    if isinstance(pii, dict):
        flag = _bool(pii.get("flag"))
        classification = _upper(pii.get("classification")) or None
    else:
        flag = _bool(pii) or _bool(raw.get("is_pii"))
        classification = _upper(raw.get("pii_classification")) or None
    if classification in ("NONE", ""):
        classification = None
    return {"flag": flag or bool(classification), "classification": classification}


def _attribute(raw: Dict[str, Any], entity_id: str, keys: List[str]) -> Dict[str, Any]:
    name = _upper(raw.get("name"))
    sources = [_upper(s) for s in _list(raw.get("source_columns")) if _upper(s)]
    definition = _text(raw.get("business_definition") or raw.get("definition"), 4000)
    status = _upper(raw.get("status")) or "ACTIVE"
    return {
        "id": str(raw.get("id") or stable_id(entity_id, name)),
        "name": name,
        "datatype": _upper(raw.get("datatype") or raw.get("type") or raw.get("data_type")) or "VARCHAR",
        "nullable": _bool(raw.get("nullable"), True),
        "is_pk": _bool(raw.get("is_pk")),
        "description": _text(raw.get("description"), 4000),
        "business_definition": definition,
        "semantic_type": _upper(raw.get("semantic_type")) or None,
        "pii": _pii(raw),
        "accepted_values": [str(v) for v in _list(raw.get("accepted_values"))][:500],
        "default": _text(raw.get("default"), 500) or None,
        "is_unique": _bool(raw.get("is_unique")),
        "check": _text(raw.get("check"), 1000) or None,
        "status": status if status in STATUSES else "ACTIVE",
        "tags": sorted({_text(t, 60).lower() for t in _list(raw.get("tags")) if _text(t, 60)})[:20],
        "business_key": name in keys or _bool(raw.get("business_key")),
        # v1 lineage fields, kept for the renderers and for runs
        "source_columns": sources,
        "derived": _bool(raw.get("derived")) or not sources,
        "transformation_hint": _text(raw.get("transformation_hint"), 500),
        "rationale": _text(raw.get("rationale"), 500) or definition[:500],
    }


def _entity(raw: Dict[str, Any], model_key: str) -> Dict[str, Any]:
    name = _upper(raw.get("entity_name") or raw.get("table") or raw.get("name"))
    entity_id = str(raw.get("id") or stable_id(model_key, name))
    table_type = _upper(raw.get("table_type") or raw.get("kind")) or "TABLE"
    keys = [_upper(k) for k in _list(raw.get("business_keys")) if _upper(k)]
    attrs, seen = [], set()
    for a in _list(raw.get("attributes") if raw.get("attributes") is not None else raw.get("columns")):
        if not isinstance(a, dict):
            continue
        attr = _attribute(a, entity_id, keys)
        if not attr["name"] or attr["name"] in seen:
            continue
        seen.add(attr["name"])
        attrs.append(attr)
    if not keys:
        keys = [a["name"] for a in attrs if a["business_key"]]
    physical = raw.get("physical") if isinstance(raw.get("physical"), dict) else {}
    status = _upper(raw.get("status")) or "ACTIVE"
    return {
        "id": entity_id,
        "entity_name": name,
        "table_type": table_type,
        "kind": table_type if table_type in V1_KINDS else "TABLE",
        "scd_type": _text(raw.get("scd_type"), 16) or "1",
        "grain": _text(raw.get("grain"), 1024),
        "business_keys": keys,
        "description": _text(raw.get("description") or raw.get("purpose"), 4000),
        "purpose": _text(raw.get("purpose") or raw.get("description"), 500),
        "subject_area": _text(raw.get("subject_area"), 80) or None,
        "status": status if status in STATUSES else "ACTIVE",
        "database": _upper(raw.get("database") or raw.get("target_database")) or None,
        "schema": _upper(raw.get("schema") or raw.get("target_schema")) or None,
        "model_spec": copy.deepcopy(raw["model_spec"]) if isinstance(raw.get("model_spec"), dict) else None,
        "unique_constraints": [[_upper(c) for c in _list(u) if _upper(c)] for u in _list(raw.get("unique_constraints"))
                               if _list(u)],
        "checks": [{"name": _upper(c.get("name")) or f"CK_{name}_{i + 1}", "expression": _text(c.get("expression"), 1000)}
                   for i, c in enumerate(_list(raw.get("checks"))) if isinstance(c, dict) and _text(c.get("expression"), 1)],
        # gold: a conformed dimension owned by another domain's gold model (referenced, never generated here)
        "conformed_from": _upper(raw.get("conformed_from")) or None,
        # gold: how a mastered domain's own dimension reads its post-MDM table (relation, latest version, active column)
        "gold_source": copy.deepcopy(raw["gold_source"]) if isinstance(raw.get("gold_source"), dict) else None,
        "physical": {"cluster_by": [_upper(c) for c in _list(physical.get("cluster_by")) if _upper(c)][:4],
                     "transient": _bool(physical.get("transient")),
                     "retention_days": int(physical["retention_days"]) if str(physical.get("retention_days") or "").isdigit() else None},
        "attributes": attrs,
    }


def _relationship(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rel = {k: _upper(raw.get(k)) for k in ("from_entity", "from_column", "to_entity", "to_column")}
    if not all(rel.values()):
        return None
    card = _upper(raw.get("cardinality")) or "N:1"
    enforce = _upper(raw.get("enforce")) or "NONE"
    rel.update(id=str(raw.get("id") or stable_id("rel", rel["from_entity"], rel["from_column"], rel["to_entity"])),
               name=_upper(raw.get("name")) or f"FK_{rel['from_entity']}_{rel['from_column']}"[:MAX_IDENTIFIER],
               cardinality=card if card in CARDINALITIES else "N:1",
               from_optional=_bool(raw.get("from_optional"), True), to_optional=_bool(raw.get("to_optional")),
               enforce=enforce if enforce in ENFORCE else "NONE")
    return rel


def normalize_v2(raw: Dict[str, Any], model_key: str = "") -> Dict[str, Any]:
    """Any v1 or v2 specification as a v2 one. Never adds or drops meaning: unknown table types and model_spec are
    kept, missing ids are assigned deterministically from the model key and names."""
    raw = copy.deepcopy(raw or {})
    design = raw.get("design") if isinstance(raw.get("design"), dict) else {"entities": raw.get("entities") or []}
    key = model_key or _upper(raw.get("domain"))
    entities, seen = [], set()
    for e in _list(design.get("entities")):
        if not isinstance(e, dict):
            continue
        ent = _entity(e, key)
        if ent["entity_name"] and ent["entity_name"] not in seen:
            seen.add(ent["entity_name"])
            entities.append(ent)
    phys = raw.get("physical_defaults") if isinstance(raw.get("physical_defaults"), dict) else {}
    platform = _upper(phys.get("platform") or raw.get("target_platform")) or "SNOWFLAKE"
    iceberg = phys.get("iceberg") if isinstance(phys.get("iceberg"), dict) else {}
    physical_defaults = {
        "platform": platform if platform in PLATFORMS else "SNOWFLAKE",
        "database": _upper(phys.get("database") or raw.get("target_database") or design.get("target_database")),
        "schema": _upper(phys.get("schema") or raw.get("target_schema") or design.get("target_schema")),
        "iceberg": {"external_volume": _text(iceberg.get("external_volume") or raw.get("external_volume"), 255) or None,
                    "catalog": _text(iceberg.get("catalog"), 255) or "SNOWFLAKE",
                    "base_location": _text(iceberg.get("base_location"), 500) or None},
    }
    rels, rel_seen = [], set()
    for r in _list(raw.get("relationships")):
        rel = _relationship(r) if isinstance(r, dict) else None
        if rel and rel["id"] not in rel_seen:
            rel_seen.add(rel["id"])
            rels.append(rel)
    areas = []
    for a in _list(raw.get("subject_areas")):
        name = _text(a.get("name") if isinstance(a, dict) else a, 80)
        if name and name not in {x["name"] for x in areas}:
            areas.append({"name": name, "description": _text(a.get("description") if isinstance(a, dict) else "", 500),
                          "color": _text(a.get("color") if isinstance(a, dict) else "", 16) or None})
    primary = _upper(design.get("primary_entity"))
    if entities and primary not in seen:
        primary = entities[0]["entity_name"]
    return {
        "spec_version": SPEC_VERSION,
        "domain": _upper(raw.get("domain")),
        "layer": _upper(raw.get("layer")) if _upper(raw.get("layer")) in LAYERS else "SILVER",
        "mode": _upper(raw.get("mode")) or "MAP",
        "parent_domain": _upper(raw.get("parent_domain")) or None,
        "description": _text(raw.get("description"), 4000),
        "subject_areas": areas,
        "naming_standard": raw.get("naming_standard") if isinstance(raw.get("naming_standard"), dict) else {"preset": "NONE"},
        "physical_defaults": physical_defaults,
        # v1 aliases, read by services/catalog/models.py renderers
        "target_platform": physical_defaults["platform"],
        "target_database": physical_defaults["database"],
        "target_schema": physical_defaults["schema"],
        "external_volume": physical_defaults["iceberg"]["external_volume"],
        "source_tables": [_upper(t) for t in _list(raw.get("source_tables")) if _upper(t)][:100],
        "design": {"entities": entities, "primary_entity": primary, "decision": _upper(design.get("decision")) or "NEW"},
        "relationships": rels,
    }


def active(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The specification without retired entities and columns (what is rendered and registered as live)."""
    out = copy.deepcopy(spec)
    out["design"]["entities"] = [dict(e, attributes=[a for a in e["attributes"] if a.get("status") != "RETIRED"])
                                 for e in out["design"]["entities"] if e.get("status") != "RETIRED"]
    live = {(e["entity_name"], a["name"]) for e in out["design"]["entities"] for a in e["attributes"]}
    out["relationships"] = [r for r in out.get("relationships") or []
                            if (r["from_entity"], r["from_column"]) in live and (r["to_entity"], r["to_column"]) in live]
    return out


def canonical(spec: Dict[str, Any]) -> str:
    """Stable JSON of a specification (sorted keys), for hashing and comparing."""
    return json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)


def checksum(spec: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical(spec).encode("utf-8")).hexdigest()


def summary(spec: Dict[str, Any]) -> Dict[str, int]:
    ents = [e for e in spec["design"]["entities"] if e.get("status") != "RETIRED"]
    return {"entities": len(ents), "columns": sum(len([a for a in e["attributes"] if a.get("status") != "RETIRED"])
                                                   for e in ents),
            "relationships": len(spec.get("relationships") or []),
            "retired": len(spec["design"]["entities"]) - len(ents)}


# ---------------------------------------------------------------- validation


def _family(datatype: str) -> str:
    t = (datatype or "").upper()
    if re.match(r"^(NUMBER|DECIMAL|NUMERIC|INT|INTEGER|BIGINT|SMALLINT|TINYINT|BYTEINT)", t):
        return "NUMBER"
    if re.match(r"^(VARCHAR|STRING|TEXT|CHAR|CHARACTER|NCHAR|NVARCHAR)", t):
        return "TEXT"
    if t.startswith("TIMESTAMP") or t == "DATETIME":
        return "TIMESTAMP"
    if t in ("FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION", "REAL"):
        return "FLOAT"
    return t.split("(")[0]


def validate(spec: Dict[str, Any], existing_domains: Iterable[str] = (), check_names: bool = True) -> List[Dict[str, str]]:
    """Problems with a v2 specification, each {path, severity, message}; ERRORs block submitting and approving.
    Paths use names (ENTITY, ENTITY.COLUMN, rel:<id>, model) so the workbench can point at the field."""
    from services.modeling.design import TYPE

    iceberg = (spec.get("physical_defaults") or {}).get("platform") == "ICEBERG"

    issues: List[Dict[str, str]] = []

    def add(path: str, message: str, severity: str = "ERROR") -> None:
        issues.append({"path": path, "severity": severity, "message": message})

    def ident(path: str, name: str, what: str) -> None:
        if not name:
            add(path, f"the {what} needs a name")
        elif len(name) > MAX_IDENTIFIER:
            add(path, f"{name[:40]}... is longer than {MAX_IDENTIFIER} characters")
        elif not NAME.match(name):
            add(path, f"{name} is not a valid {what} name (letters, digits, _ and $, not starting with a digit)")
        elif name.upper() in RESERVED:
            add(path, f"{name} is a Snowflake reserved word; pick another {what} name")

    entities = spec["design"]["entities"]
    if not [e for e in entities if e.get("status") != "RETIRED"]:
        add("model", "the model has no active entities")
    phys = spec.get("physical_defaults") or {}
    if phys.get("platform") == "ICEBERG" and not (phys.get("iceberg") or {}).get("external_volume"):
        add("model", "Iceberg tables need an external volume (physical defaults)")
    if not phys.get("schema"):
        add("model", "no default target schema: the domain name is used", "WARN")
    by_name = {}
    lowered: Dict[str, List[str]] = {}
    for e in entities:
        lowered.setdefault(e["entity_name"].lower(), []).append(e["entity_name"])
        by_name[e["entity_name"]] = e
    for names in lowered.values():
        if len(names) > 1:
            add(names[0], f"{' and '.join(names)} differ only by case")
    standard = spec.get("naming_standard") or {}
    prefix = str(standard.get("table_prefix") or "").upper()
    audits = [str(a).upper() for a in standard.get("audit_columns") or []]
    for e in entities:
        path = e["entity_name"]
        if check_names:
            ident(path, e["entity_name"], "table")
        retired = e.get("status") == "RETIRED"
        live = [a for a in e["attributes"] if a.get("status") != "RETIRED"]
        cols = {a["name"]: a for a in e["attributes"]}
        if not retired and not live:
            add(path, f"{path} has no columns")
        if prefix and not path.startswith(prefix):
            add(path, f"{path} should start with {prefix} (naming standard)", "WARN")
        names_seen: Dict[str, List[str]] = {}
        for a in e["attributes"]:
            names_seen.setdefault(a["name"].lower(), []).append(a["name"])
            apath = f"{path}.{a['name']}"
            if check_names:
                ident(apath, a["name"], "column")
            # Iceberg tables also take LONG (the gold-model skill's key type)
            if not TYPE.match(a["datatype"]) and not (iceberg and a["datatype"].upper() == "LONG"):
                add(apath, f"{a['datatype']} is not a Snowflake type")
            if a.get("is_pk") and a.get("nullable"):
                add(apath, f"{a['name']} is part of the primary key, so it cannot be nullable")
            if a.get("default") and ";" in a["default"]:
                add(apath, "a default is one expression (no ;)")
            if a.get("check") and ";" in a["check"]:
                add(apath, "a check is one expression (no ;)")
        for names in names_seen.values():
            if len(names) > 1:
                add(path, f"columns {' and '.join(names)} differ only by case")
        live_names = {a["name"] for a in live}
        for k in e.get("business_keys") or []:
            if k not in cols:
                add(path, f"business key {k} is not a column of {path}")
            elif k not in live_names and not retired:
                add(f"{path}.{k}", f"{k} is retired but still a business key")
        for i, u in enumerate(e.get("unique_constraints") or []):
            for c in u:
                if c not in live_names:
                    add(path, f"unique constraint {i + 1} names {c}, which is not an active column")
        for c in (e.get("physical") or {}).get("cluster_by") or []:
            if c not in live_names:
                add(path, f"cluster key {c} is not an active column", "WARN")
        if not retired and live and not any(a.get("is_pk") for a in live) and not e.get("business_keys"):
            add(path, f"{path} has no primary or business key", "WARN")
        for audit in audits:
            if not retired and audit not in live_names:
                add(path, f"{path} is missing the audit column {audit} (naming standard)", "WARN")
        if (e.get("database") or e.get("schema")) and phys.get("platform") == "ICEBERG" and not (
                phys.get("iceberg") or {}).get("external_volume"):
            add(path, "Iceberg tables need an external volume")
    for r in spec.get("relationships") or []:
        path = f"rel:{r['id']}"
        src, dst = by_name.get(r["from_entity"]), by_name.get(r["to_entity"])
        if src is None or dst is None:
            add(path, f"{r['from_entity']} -> {r['to_entity']}: both ends must be entities of the model")
            continue
        scol = next((a for a in src["attributes"] if a["name"] == r["from_column"]), None)
        dcol = next((a for a in dst["attributes"] if a["name"] == r["to_column"]), None)
        if scol is None:
            add(path, f"{r['from_column']} is not a column of {r['from_entity']}")
        if dcol is None:
            add(path, f"{r['to_column']} is not a column of {r['to_entity']}")
        if scol is None or dcol is None:
            continue
        if "RETIRED" in (scol.get("status"), dcol.get("status"), src.get("status"), dst.get("status")):
            add(path, f"{r['name']} uses a retired entity or column; remove the relationship or restore it")
        unique_sets = [set(u) for u in dst.get("unique_constraints") or []]
        is_key = dcol.get("is_pk") or dcol.get("is_unique") or {r["to_column"]} in unique_sets or \
            dst.get("business_keys") == [r["to_column"]]
        if not is_key:
            add(path, f"{r['to_entity']}.{r['to_column']} is not a primary or unique key, so it cannot be referenced",
                "WARN")
        if _family(scol["datatype"]) != _family(dcol["datatype"]):
            add(path, f"{r['from_entity']}.{r['from_column']} ({scol['datatype']}) and {r['to_entity']}.{r['to_column']} "
                      f"({dcol['datatype']}) have different types")
        if not r.get("from_optional") and scol.get("nullable"):
            add(path, f"{r['name']} is mandatory but {r['from_entity']}.{r['from_column']} is nullable", "WARN")
    return issues


def blocking(issues: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [i for i in issues if i.get("severity") == "ERROR"]


# ---------------------------------------------------------------- registry and pack round trip


def _js(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def spec_from_registry(domain: Dict[str, Any], tables: List[Dict[str, Any]],
                       columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The domain's registered targets as a v2 specification, losslessly (the same rule as the V039 backfill).
    Rows are registry rows with lower-case keys; inactive tables become RETIRED entities."""
    by_table: Dict[str, List[Dict[str, Any]]] = {}
    for c in sorted(columns, key=lambda c: (str(c.get("target_table_id")), int(c.get("ordinal_position") or 0))):
        by_table.setdefault(str(c["target_table_id"]), []).append(c)
    dbs = [str(t.get("target_database") or "").upper() for t in tables if t.get("active_flag", True)]
    schemas = [str(t.get("target_schema") or "").upper() for t in tables if t.get("active_flag", True)]
    db = max(set(dbs), key=dbs.count) if dbs else ""
    schema = max(set(schemas), key=schemas.count) if schemas else ""
    entities = []
    rels: List[Dict[str, Any]] = []
    for t in sorted(tables, key=lambda t: str(t.get("target_table") or "").upper()):
        keys = [str(k).upper() for k in _js(t.get("business_keys")) or []]
        spec = _js(t.get("model_spec"))
        spec = dict(spec) if isinstance(spec, dict) else None
        wb = (spec or {}).pop("workbench", None) or {}
        if spec is not None and wb:  # added by registry_sync for older readers; not part of the entity's own settings
            for k in wb.get("added") or []:
                spec.pop(k, None)
            spec.update(wb.get("replaced") or {})
        pk = [str(k).upper() for k in (wb.get("primary_key") or (spec or {}).get("primary_key") or [])]
        extra = wb.get("columns") or {}
        attrs = []
        for c in by_table.get(str(t["target_table_id"]), []):
            more = extra.get(c["column_name"]) or {}
            attrs.append({"id": c["target_column_id"], "name": c["column_name"], "datatype": c.get("data_type"),
                          "nullable": c.get("nullable") if c.get("nullable") is not None else True,
                          "is_pk": str(c["column_name"]).upper() in pk,
                          "business_definition": None if more.get("definition_from_description") else c.get("business_definition"),
                          "semantic_type": c.get("semantic_type"),
                          "pii": {"flag": bool(c.get("is_pii")), "classification": more.get("pii_classification")},
                          "accepted_values": _js(c.get("accepted_values")) or [],
                          "business_key": bool(c.get("is_business_key")),
                          **{k: more[k] for k in ("description", "default", "is_unique", "check", "tags") if k in more}})
        tdb, tschema = str(t.get("target_database") or "").upper(), str(t.get("target_schema") or "").upper()
        entities.append({"id": wb.get("entity_id") or t["target_table_id"], "entity_name": t["target_table"],
                         "table_type": t.get("table_type"),
                         "scd_type": t.get("scd_type"), "grain": t.get("grain"), "business_keys": keys,
                         "description": t.get("description"), "model_spec": spec or None,
                         "status": "ACTIVE" if t.get("active_flag", True) else "RETIRED",
                         "database": tdb if tdb != db else None, "schema": tschema if tschema != schema else None,
                         "unique_constraints": wb.get("unique_constraints") or [], "checks": wb.get("checks") or [],
                         "physical": wb.get("physical") or {}, "subject_area": wb.get("subject_area"),
                         "attributes": attrs})
        rels += [r for r in wb.get("relationships") or [] if isinstance(r, dict)]
    for e in entities:
        spec = e.get("model_spec") or {}
        for r in spec.get("relationships") or []:
            if isinstance(r, dict):
                rels.append(r)
        if spec.get("hub") and spec.get("hub_fk") and str(spec["hub"]).upper() != e["entity_name"].upper():
            rels.append({"from_entity": e["entity_name"], "from_column": spec["hub_fk"], "to_entity": spec["hub"],
                         "to_column": spec["hub_fk"], "cardinality": "N:1"})
    name = str(domain.get("domain_name") or "").upper()
    return normalize_v2({"domain": name, "mode": "MAP", "description": domain.get("description"),
                         "target_database": db, "target_schema": schema,
                         "design": {"entities": entities}, "relationships": rels},
                        model_key=str(domain.get("domain_id") or name))


def pack_targets(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """domain_pack.json targets for every active entity, keeping table_type, SCD, model_spec, PII, accepted values
    and semantic types (the v1 renderer flattened them)."""
    phys = spec.get("physical_defaults") or {}
    out = []
    for e in active(spec)["design"]["entities"]:
        fks = [r for r in spec.get("relationships") or [] if r["from_entity"] == e["entity_name"]]
        model_spec = copy.deepcopy(e.get("model_spec") or {})
        if fks and "relationships" not in model_spec and not model_spec.get("hub_fk"):
            model_spec["relationships"] = [{k: r[k] for k in ("from_entity", "from_column", "to_entity", "to_column",
                                                              "cardinality")} for r in fks]
        out.append({
            "schema": e.get("schema") or phys.get("schema") or spec["domain"],
            **({"database": e["database"]} if e.get("database") else {}),
            "table": e["entity_name"], "table_type": e["table_type"], "grain": e.get("grain") or "",
            "business_keys": e.get("business_keys") or [], "scd_type": e.get("scd_type") or "1",
            "description": e.get("description") or e.get("purpose") or "",
            **({"model_spec": model_spec} if model_spec else {}),
            "columns": [{k: v for k, v in {
                "name": a["name"], "type": a["datatype"], "nullable": a["nullable"],
                "definition": a.get("business_definition") or a.get("description") or "",
                "semantic_type": a.get("semantic_type"), "business_key": a["name"] in (e.get("business_keys") or []),
                "pii": bool((a.get("pii") or {}).get("flag")), "accepted_values": a.get("accepted_values") or None,
            }.items() if v not in (None, "") or k in ("nullable", "business_key", "pii")} for a in e["attributes"]],
        })
    return out


def spec_from_pack(pack: Dict[str, Any], model_key: str = "") -> Dict[str, Any]:
    """A domain_pack.json as a v2 specification (lossless for everything pack_targets writes)."""
    meta = pack.get("domain") or {}
    entities = []
    for t in pack.get("targets") or []:
        keys = [str(k).upper() for k in t.get("business_keys") or []]
        entities.append({"entity_name": t.get("table"), "table_type": t.get("table_type"), "scd_type": t.get("scd_type"),
                         "grain": t.get("grain"), "business_keys": keys, "description": t.get("description"),
                         "model_spec": t.get("model_spec"), "schema": t.get("schema"), "database": t.get("database"),
                         "attributes": [{"name": c.get("name"), "datatype": c.get("type"), "nullable": c.get("nullable", True),
                                         "business_definition": c.get("definition"), "semantic_type": c.get("semantic_type"),
                                         "pii": bool(c.get("pii")), "accepted_values": c.get("accepted_values") or [],
                                         "business_key": bool(c.get("business_key")),
                                         "is_pk": str(c.get("name") or "").upper() in keys and len(keys) == 1}
                                        for c in t.get("columns") or []]})
    rels = []
    for t in pack.get("targets") or []:
        ms = t.get("model_spec") or {}
        rels += [r for r in ms.get("relationships") or [] if isinstance(r, dict)]
        if ms.get("hub") and ms.get("hub_fk") and str(ms["hub"]).upper() != str(t.get("table") or "").upper():
            rels.append({"from_entity": t.get("table"), "from_column": ms["hub_fk"], "to_entity": ms["hub"],
                         "to_column": ms["hub_fk"], "cardinality": "N:1"})
    schema = meta.get("silver_schema")
    if not schema:
        schemas = [str(t.get("schema") or "").upper() for t in pack.get("targets") or [] if t.get("schema")]
        schema = max(set(schemas), key=schemas.count) if schemas else ""
    spec = normalize_v2({"domain": meta.get("name"), "description": meta.get("description"),
                         "target_database": meta.get("silver_database"), "target_schema": schema,
                         "design": {"entities": entities}, "relationships": rels}, model_key or str(meta.get("name") or ""))
    for e in spec["design"]["entities"]:  # a table in the default schema needs no override
        if e.get("schema") == spec["physical_defaults"]["schema"]:
            e["schema"] = None
    return spec


def placement(spec: Dict[str, Any], entity: Dict[str, Any]) -> Tuple[str, str]:
    """(database, schema) an entity is created in."""
    phys = spec.get("physical_defaults") or {}
    return (entity.get("database") or phys.get("database") or "",
            entity.get("schema") or phys.get("schema") or spec.get("domain") or "")
