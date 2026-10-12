"""Modeling hub (pure): a model specification and everything rendered from it.

A specification is a design in the services.modeling.design shape (entities with attributes, business keys, source
columns) plus what the hub adds: the domain it belongs to and how (MAP onto an existing domain, FORK an existing domain
into a new one, or a NEW domain), relationships (foreign keys) between entities, and the target platform (Snowflake
managed tables or Snowflake-managed Iceberg tables). From one specification the hub renders the domain_pack.json, DDL
and dbt staging and mart files that are committed to a feature branch when the model is finalized.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.modeling import design as dsn

MODES = ("MAP", "FORK", "NEW")
PLATFORMS = ("SNOWFLAKE", "ICEBERG")
CARDINALITIES = ("N:1", "1:1", "1:N")
DOMAIN_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
KIND_ROLE = {"DIMENSION": "DIM", "FACT": "FACT", "BRIDGE": "BRIDGE", "REFERENCE": "REFERENCE", "TABLE": "TABLE"}


# ---------------------------------------------------------------- specification


def normalize_spec(raw: Dict[str, Any]) -> Dict[str, Any]:
    raw = raw or {}
    conv = {"naming_case": "upper"}
    design = dsn.normalize(raw.get("design") or {"entities": raw.get("entities") or []}, conv)
    mode = str(raw.get("mode") or "NEW").upper()
    platform = str(raw.get("target_platform") or "SNOWFLAKE").upper()
    rels = []
    for r in raw.get("relationships") or []:
        rel = {k: str(r.get(k) or "").strip().upper() for k in ("from_entity", "from_column", "to_entity", "to_column")}
        if all(rel.values()):
            card = str(r.get("cardinality") or "N:1").upper()
            rel["cardinality"] = card if card in CARDINALITIES else "N:1"
            rels.append(rel)
    return {
        "domain": str(raw.get("domain") or "").strip().upper(),
        "mode": mode if mode in MODES else "NEW",
        "parent_domain": str(raw.get("parent_domain") or "").strip().upper() or None,
        "description": str(raw.get("description") or "")[:1000],
        "target_platform": platform if platform in PLATFORMS else "SNOWFLAKE",
        "target_database": str(raw.get("target_database") or design.get("target_database") or "").strip().upper(),
        "target_schema": str(raw.get("target_schema") or design.get("target_schema") or "").strip().upper(),
        "external_volume": str(raw.get("external_volume") or "").strip() or None,
        "source_tables": [str(t).strip().upper() for t in raw.get("source_tables") or [] if str(t).strip()][:100],
        "design": design,
        "relationships": rels,
    }


def validate_spec(spec: Dict[str, Any], existing_domains: List[str],
                  sources: Optional[Dict[str, Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """Problems with a specification, each {path, severity, message}. ERRORs block finalizing. `sources` are the
    profiled source columns ({table: {column: type}}); without them source columns are not checked."""
    issues: List[Dict[str, str]] = []

    def add(path: str, message: str, severity: str = "ERROR") -> None:
        issues.append({"path": path, "severity": severity, "message": message})

    known = {d.upper() for d in existing_domains}
    domain, mode, parent = spec["domain"], spec["mode"], spec.get("parent_domain")
    if not DOMAIN_NAME.match(domain or ""):
        add("domain", "the domain name is upper case letters, digits and _ (2 to 64 characters)")
    if mode == "MAP":
        if domain not in known:
            add("domain", f"{domain or 'the domain'} is not a registered domain; fork it or create a new domain")
    elif mode == "FORK":
        if not parent or parent not in known:
            add("parent_domain", "a fork needs an existing domain to start from")
        if domain in known:
            add("domain", f"{domain} already exists; a fork needs a new name")
    elif domain in known:
        add("domain", f"{domain} already exists; map to it or fork it instead")
    if spec["target_platform"] == "ICEBERG" and not spec.get("external_volume"):
        add("external_volume", "Iceberg tables need an external volume", "WARN")
    if not spec.get("target_schema"):
        add("target_schema", "no target schema: the domain name is used", "WARN")
    design = spec["design"]
    if sources is None:
        sources = {}
        for e in design.get("entities") or []:
            for a in e.get("attributes") or []:
                for s in a.get("source_columns") or []:
                    table, _, column = s.rpartition(".")
                    sources.setdefault(table, {})[column] = ""
    issues += dsn.validate(design, sources, {"naming_case": "upper"}, [])
    columns = {e["entity_name"]: {a["name"]: a for a in e["attributes"]} for e in design.get("entities") or []}
    for i, r in enumerate(spec.get("relationships") or []):
        path = f"relationships[{i}]"
        src, dst = columns.get(r["from_entity"]), columns.get(r["to_entity"])
        if src is None or dst is None:
            add(path, f"{r['from_entity']} -> {r['to_entity']}: both ends must be entities of the model")
            continue
        if r["from_column"] not in src:
            add(path, f"{r['from_column']} is not a column of {r['from_entity']}")
        if r["to_column"] not in dst:
            add(path, f"{r['to_column']} is not a column of {r['to_entity']}")
            continue
        if not (dst[r["to_column"]].get("is_pk") or r["to_column"] in
                [k.upper() for e in design["entities"] if e["entity_name"] == r["to_entity"] for k in e["business_keys"]]):
            add(path, f"{r['to_entity']}.{r['to_column']} is not a key, so it cannot be referenced", "WARN")
        if r["from_column"] in src and _family(src[r["from_column"]]["datatype"]) != _family(dst[r["to_column"]]["datatype"]):
            add(path, f"{r['from_entity']}.{r['from_column']} and {r['to_entity']}.{r['to_column']} have different types")
    return issues


def _family(datatype: str) -> str:
    t = (datatype or "").upper()
    if re.match(r"^(NUMBER|DECIMAL|NUMERIC|INT|INTEGER|BIGINT|SMALLINT)", t):
        return "NUMBER"
    if re.match(r"^(VARCHAR|STRING|TEXT|CHAR)", t):
        return "TEXT"
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    return t.split("(")[0]


def blocking(issues: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [i for i in issues if i.get("severity") == "ERROR"]


# ---------------------------------------------------------------- types and DDL


def iceberg_type(datatype: str) -> str:
    """The Snowflake-managed Iceberg type for a Snowflake type (semi-structured and geospatial types become STRING
    holding JSON or WKT, TIMESTAMP_TZ becomes TIMESTAMP_LTZ, timestamps get microsecond precision)."""
    t = (datatype or "VARCHAR").upper().replace(" ", "")
    m = re.match(r"^(NUMBER|DECIMAL|NUMERIC)(\((\d+)(,(\d+))?\))?$", t)
    if m:
        precision, scale = int(m.group(3) or 38), int(m.group(5) or 0)
        if scale == 0 and precision <= 9:
            return "INT"
        if scale == 0 and precision <= 18:
            return "BIGINT"
        return f"DECIMAL({min(precision, 38)},{scale})"
    if t in ("INT", "INTEGER", "SMALLINT"):
        return "INT"
    if t == "BIGINT":
        return "BIGINT"
    if t in ("FLOAT", "DOUBLE", "REAL"):
        return "DOUBLE"
    if t.startswith(("VARCHAR", "STRING", "TEXT", "CHAR")):
        return "STRING"
    if t.startswith("TIMESTAMP_TZ") or t.startswith("TIMESTAMP_LTZ"):
        return "TIMESTAMP_LTZ(6)"
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP_NTZ(6)"
    if t.startswith("TIME"):
        return "TIME(6)"
    if t in ("BOOLEAN", "DATE", "BINARY"):
        return t
    return "STRING"


def target_type(datatype: str, platform: str) -> str:
    return iceberg_type(datatype) if platform == "ICEBERG" else (datatype or "VARCHAR").upper()


def _schema(spec: Dict[str, Any]) -> str:
    return spec.get("target_schema") or spec["domain"]


def _qualified(spec: Dict[str, Any], table: str) -> str:
    parts = [p for p in (spec.get("target_database"), _schema(spec), table) if p]
    return ".".join(parts)


def render_ddl(spec: Dict[str, Any]) -> str:
    """CREATE TABLE IF NOT EXISTS for every entity, with primary keys and foreign keys (informational in Snowflake)."""
    platform = spec["target_platform"]
    by_entity: Dict[str, List[Dict[str, str]]] = {}
    for r in spec.get("relationships") or []:
        by_entity.setdefault(r["from_entity"], []).append(r)
    out = [f"-- {spec['domain']} model ({spec['mode']}), target {platform}. Generated by the Modeling hub; review "
           "before running."]
    for e in spec["design"]["entities"]:
        name = _qualified(spec, e["entity_name"])
        cols = [f"    {a['name']} {target_type(a['datatype'], platform)}{'' if a['nullable'] else ' NOT NULL'}"
                for a in e["attributes"]]
        keys = [a["name"] for a in e["attributes"] if a["is_pk"]] or list(e.get("business_keys") or [])
        if keys:
            cols.append(f"    CONSTRAINT PK_{e['entity_name']} PRIMARY KEY ({', '.join(keys)})")
        for r in by_entity.get(e["entity_name"], []):
            cols.append(f"    CONSTRAINT FK_{e['entity_name']}_{r['from_column']} FOREIGN KEY ({r['from_column']}) "
                        f"REFERENCES {_qualified(spec, r['to_entity'])} ({r['to_column']})")
        body = ",\n".join(cols)
        if platform == "ICEBERG":
            volume = spec.get("external_volume") or "<external_volume>"
            location = f"{_schema(spec)}/{e['entity_name']}".lower()
            out.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {name} (\n{body}\n)\n  CATALOG = 'SNOWFLAKE'\n"
                       f"  EXTERNAL_VOLUME = '{volume}'\n  BASE_LOCATION = '{location}'\n"
                       f"  COMMENT = '{_comment(e)}';")
        else:
            out.append(f"CREATE TABLE IF NOT EXISTS {name} (\n{body}\n)\n  COMMENT = '{_comment(e)}';")
    return "\n\n".join(out) + "\n"


def _comment(entity: Dict[str, Any]) -> str:
    return (entity.get("purpose") or entity["entity_name"]).replace("'", "''")[:500]


# ---------------------------------------------------------------- domain pack


def _target(spec: Dict[str, Any], e: Dict[str, Any]) -> Dict[str, Any]:
    keys = list(e.get("business_keys") or []) or [a["name"] for a in e["attributes"] if a["is_pk"]]
    fks = [r for r in spec.get("relationships") or [] if r["from_entity"] == e["entity_name"]]
    return {
        "schema": _schema(spec),
        "table": e["entity_name"],
        "table_type": KIND_ROLE.get(e.get("kind") or "TABLE", "TABLE"),
        "grain": e.get("grain") or "",
        "business_keys": keys,
        "scd_type": "1",
        "description": e.get("purpose") or f"{e['entity_name']} of {spec['domain']}",
        "model_spec": {"role": (e.get("kind") or "TABLE").lower(), "relationships": fks,
                       "target_platform": spec["target_platform"]},
        "columns": [{"name": a["name"], "type": target_type(a["datatype"], spec["target_platform"]),
                     "nullable": a["nullable"], "definition": a.get("rationale") or a.get("transformation_hint") or "",
                     "business_key": a["name"] in keys} for a in e["attributes"]],
    }


def _knowledge(spec: Dict[str, Any], e: Dict[str, Any]) -> Dict[str, Any]:
    domain = spec["domain"].lower()
    return {"key": f"{domain}.model.{e['entity_name'].lower()}", "type": "MODEL_DEFINITION",
            "title": f"{e['entity_name']} ({(e.get('kind') or 'TABLE').lower()})",
            "content": f"{e.get('purpose') or e['entity_name']}. Grain: {e.get('grain') or 'not stated'}.",
            "content_json": {"target_table": e["entity_name"], "role": (e.get("kind") or "TABLE").lower(),
                             "column_count": len(e["attributes"]),
                             "relationships": [r for r in spec.get("relationships") or []
                                               if e["entity_name"] in (r["from_entity"], r["to_entity"])]}}


def render_pack(spec: Dict[str, Any], base_pack: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """domain_pack.json for the model. MAP updates the base domain's pack (same-named targets are replaced, others
    kept); FORK copies the base pack under the new domain name, with knowledge keys re-namespaced; NEW starts empty."""
    domain = spec["domain"]
    if spec["mode"] in ("MAP", "FORK") and base_pack:
        pack = copy.deepcopy(base_pack)
    else:
        pack = {"domain": {"name": domain, "standard": "GENERIC", "owner": "Modeling hub"}, "targets": [], "knowledge": []}
    meta = pack.setdefault("domain", {})
    if spec["mode"] == "FORK":
        parent = str(meta.get("name") or spec.get("parent_domain") or "").upper()
        meta.update(name=domain, forked_from=parent, origin="imported")
        old, new = parent.lower() + ".", domain.lower() + "."
        for k in pack.get("knowledge") or []:
            if str(k.get("key") or "").lower().startswith(old):
                k["key"] = new + str(k["key"])[len(old):]
        for t in pack.get("targets") or []:
            if t.get("schema") == (spec.get("parent_domain") or parent):
                t["schema"] = _schema(spec)
    meta["name"] = domain
    if spec.get("description"):
        meta["description"] = spec["description"]
    meta.setdefault("description", f"{domain} domain")
    if spec.get("target_database"):
        meta["silver_database"] = spec["target_database"]
    meta["silver_schema"] = _schema(spec)
    targets = {str(t.get("table") or "").upper(): t for t in pack.get("targets") or []}
    for e in spec["design"]["entities"]:
        targets[e["entity_name"]] = _target(spec, e)
    pack["targets"] = list(targets.values())
    knowledge = {k["key"]: k for k in pack.get("knowledge") or []}
    for e in spec["design"]["entities"]:
        item = _knowledge(spec, e)
        knowledge[item["key"]] = item
    pack["knowledge"] = list(knowledge.values())
    return pack


def pack_problems(pack: Dict[str, Any]) -> List[str]:
    from services.knowledge.validate import validate_pack

    return validate_pack(pack)


# ---------------------------------------------------------------- dbt


def _snake(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(name or "").lower()).strip("_") or "model"


def render_dbt(spec: Dict[str, Any], project_dir: str = "", expressions: bool = False) -> Dict[str, str]:
    """dbt sources, one staging view per source table and one mart per entity, with key and relationship tests.
    With `expressions`, a derived column's transformation_hint is SQL and wins over its source column (table models,
    where the hint is an expression the modeler wrote); otherwise it is used only for columns without a source."""
    root = (project_dir.strip("/") + "/") if project_dir.strip("/") else ""
    domain = _snake(spec["domain"])
    used: Dict[str, List[str]] = {}
    for e in spec["design"]["entities"]:
        for a in e["attributes"]:
            for s in a.get("source_columns") or []:
                table, _, column = s.rpartition(".")
                if table and column and column not in used.setdefault(table, []):
                    used[table].append(column)
    for t in spec.get("source_tables") or []:
        used.setdefault(t.split(".")[-1], [])
    files: Dict[str, str] = {}
    source_name = f"{domain}_sources"
    db_schema = {}
    for full in spec.get("source_tables") or []:
        parts = full.split(".")
        if len(parts) == 3:
            db_schema = {"database": parts[0], "schema": parts[1]}
            break
    lines = ["version: 2", "", "sources:", f"  - name: {source_name}"]
    if db_schema:
        lines += [f"    database: {db_schema['database']}", f"    schema: {db_schema['schema']}"]
    lines.append("    tables:")
    for table in sorted(used):
        lines.append(f"      - name: {_snake(table)}")
        lines.append(f"        identifier: {table}")
    files[f"{root}models/staging/{domain}/_sources.yml"] = "\n".join(lines) + "\n"
    for table, cols in sorted(used.items()):
        select = ",\n".join(f"    {c.lower()}" for c in cols) if cols else "    *"
        files[f"{root}models/staging/{domain}/stg_{domain}__{_snake(table)}.sql"] = (
            "{{ config(materialized='view') }}\n\n"
            f"select\n{select}\nfrom {{{{ source('{source_name}', '{_snake(table)}') }}}}\n")
    schema = ["version: 2", "", "models:"]
    rels = spec.get("relationships") or []
    for e in spec["design"]["entities"]:
        model = _snake(e["entity_name"])
        tables = sorted({s.rpartition(".")[0] for a in e["attributes"] for s in a.get("source_columns") or []
                         if s.rpartition(".")[0]})
        driving = tables[0] if tables else None
        cols = []
        for a in e["attributes"]:
            src = next((s.rpartition(".")[2] for s in a.get("source_columns") or [] if s.rpartition(".")[0] == driving), None)
            expr = (a.get("transformation_hint") or "").strip() if a.get("derived") else ""
            usable = expr if expr and ";" not in expr else ""
            value = usable if (expressions and usable) else src.lower() if src else (usable or "null")
            cols.append(f"    cast({value} as {target_type(a['datatype'], spec['target_platform']).lower()}) "
                        f"as {a['name'].lower()}")
        source = f"{{{{ ref('stg_{domain}__{_snake(driving)}') }}}}" if driving else "(select 1 as _placeholder)"
        keys = [a["name"] for a in e["attributes"] if a["is_pk"]] or list(e.get("business_keys") or [])
        config = (f"{{{{ config(materialized='incremental', unique_key=[{', '.join(repr(k.lower()) for k in keys)}], "
                  "incremental_strategy='merge') }}\n\n") if keys else "{{ config(materialized='table') }}\n\n"
        joins = f"-- other sources: {', '.join(tables[1:])}; join them in the staging layer or here\n" if len(tables) > 1 else ""
        files[f"{root}models/marts/{domain}/{model}.sql"] = (config + joins + "select\n" + ",\n".join(cols)
                                                             + f"\nfrom {source}\n")
        schema += [f"  - name: {model}", f"    description: \"{(e.get('purpose') or '').replace(chr(34), '')[:300]}\"",
                   "    columns:"]
        for a in e["attributes"]:
            tests = []
            if a["name"] in keys:
                tests += ["not_null"] + (["unique"] if len(keys) == 1 else [])
            elif not a["nullable"]:
                tests.append("not_null")
            for r in rels:
                if r["from_entity"] == e["entity_name"] and r["from_column"] == a["name"]:
                    tests.append({"relationships": {"to": f"ref('{_snake(r['to_entity'])}')",
                                                    "field": r["to_column"].lower()}})
            schema.append(f"      - name: {a['name'].lower()}")
            if tests:
                schema.append("        tests:")
                for t in tests:
                    if isinstance(t, str):
                        schema.append(f"          - {t}")
                    else:
                        rel = t["relationships"]
                        schema += ["          - relationships:", f"              to: {rel['to']}",
                                   f"              field: {rel['field']}"]
    files[f"{root}models/marts/{domain}/_schema.yml"] = "\n".join(schema) + "\n"
    return files


# ---------------------------------------------------------------- git


def branch_name(domain: str, now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    return f"feature/model-{_snake(domain).replace('_', '-')}-{stamp}"


def pack_path(domain: str) -> str:
    return f"domain/{_snake(domain)}/domain_pack.json"


def artifacts(spec: Dict[str, Any], pack: Dict[str, Any], project_dir: str = "") -> Dict[str, str]:
    """{path: content} committed on publish: the domain pack, the DDL and the dbt files."""
    files = {pack_path(spec["domain"]): json.dumps(pack, indent=2) + "\n",
             f"domain/{_snake(spec['domain'])}/model.sql": render_ddl(spec)}
    files.update(render_dbt(spec, project_dir))
    return files


def summary(spec: Dict[str, Any]) -> Tuple[int, int, int]:
    entities = spec["design"]["entities"]
    return len(entities), sum(len(e["attributes"]) for e in entities), len(spec.get("relationships") or [])


def spec_from_pack(pack: Dict[str, Any], mode: str, domain: str, parent: Optional[str] = None) -> Dict[str, Any]:
    """An existing domain's pack as a specification, so the canvas starts from what the domain already models.
    Spokes that name their hub (model_spec.hub_fk) become relationships to the hub."""
    kinds = {v: k for k, v in KIND_ROLE.items()}
    entities, rels = [], []
    targets = pack.get("targets") or []
    for t in targets:
        keys = [str(k).upper() for k in t.get("business_keys") or []]
        entities.append({"entity_name": str(t.get("table") or "").upper(),
                         "kind": kinds.get(str(t.get("table_type") or "").upper(), "TABLE"),
                         "purpose": t.get("description") or "", "grain": t.get("grain") or "", "business_keys": keys,
                         "attributes": [{"name": c.get("name"), "datatype": c.get("type") or "VARCHAR",
                                         "nullable": bool(c.get("nullable", True)),
                                         "is_pk": str(c.get("name") or "").upper() in keys and len(keys) == 1,
                                         "source_columns": [], "derived": True, "rationale": c.get("definition") or ""}
                                        for c in t.get("columns") or []]})
        spec = t.get("model_spec") or {}
        for r in spec.get("relationships") or []:
            rels.append(r)
        if spec.get("hub") and spec.get("hub_fk") and spec["hub"] != t.get("table"):
            rels.append({"from_entity": t.get("table"), "from_column": spec["hub_fk"], "to_entity": spec["hub"],
                         "to_column": spec["hub_fk"], "cardinality": "N:1"})
    meta = pack.get("domain") or {}
    return normalize_spec({"domain": domain, "mode": mode, "parent_domain": parent,
                           "description": meta.get("description"), "target_database": meta.get("silver_database"),
                           "target_schema": meta.get("silver_schema"), "design": {"entities": entities},
                           "relationships": rels})
