"""Gold model proposal (pure): from a domain's silver tables and the MDM contracts, a GDP gold star schema and the
mapping lines that say where every gold column comes from.

Rules (gold-model skill + gdp-post-mdm-data-model skill + decisions of 2026-10-14):
- A consumer domain's silver hub (one row per source record) becomes FACT_<STEM>, one row per source record,
  merged on SOURCE_UNIQUE_ID + DIM_DATA_SOURCE_SKEY (kept in gold so a repoint can match it later).
- A pre-MDM key the hub carries for a mastered domain (COMPANY_CORE_SKEY, PROPERTY_CORE_SKEY, or a role prefix such
  as LANDLORD_COMPANY_CORE_SKEY) becomes the mastered gold key (DIM_COMPANY_SKEY, LANDLORD_DIM_COMPANY_SKEY), resolved
  through the full cross-reference (RESOLVE line), with a relationship to the conformed dimension, which is included
  in the model as a referenced entity (conformed_from) and never generated here.
- A child table that links the hub to a mastered domain (it carries the hub's skey and a mastered pre-MDM skey) is a
  many-to-many: a BRIDGE table (FACT_<HUB>_<MASTERED>_ROLE when it has a role column, else BRIDGE_<HUB>_<MASTERED>)
  with two N:1 relationships.
- A low-cardinality descriptor (profile: at most DIM_MAX_DISTINCT distinct values) becomes a proposed dimension
  DIM_<STEM>_<COLUMN> and the fact carries its key, DIM_<STEM>_<COLUMN>_SKEY (GDP: a foreign key is named as the dimension's key).
- Silver technical columns are not gold columns: the hub skey, the silver HKEY, REF_GDP_SOURCE_SYSTEM_SKEY (becomes
  DIM_DATA_SOURCE_SKEY) and the GDP_* audit columns (gold injects its own). Every gold table gets its own
  <TABLE>_SKEY (sequence), <TABLE>_HKEY (hash of the business columns) and the five GDP_* audit columns last.
- Gold lives in <ENV>_GDP_GOLD_<DOMAIN>_DB.<DOMAIN>, Iceberg on the GDP_SF_<ENV>_GOLD volume.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

PREFIX = "GDP"
DIM_MAX_DISTINCT = 50
AUDIT = [("GDP_IS_ACTIVE", "BOOLEAN"), ("GDP_INSERTED_TS", "TIMESTAMP_NTZ"), ("GDP_INSERTED_BY", "STRING"),
         ("GDP_UPDATED_TS", "TIMESTAMP_NTZ"), ("GDP_UPDATED_BY", "STRING")]
SILVER_TECH = re.compile(r"^(GDP_[A-Z_]+|[A-Z0-9_]+_HKEY|MD5_HASH|DBT_[A-Z_]+|_[A-Z_]+)$")
ROLE_COLUMN = re.compile(r"(^|_)(ROLE|ROLE_TYPE|RELATIONSHIP_TYPE|ROLE_NAME|ROLE_CODE)$")
DESCRIPTOR = re.compile(r"(^|_)(STATUS|STAGE|TYPE|CATEGORY|CLASS|SEGMENT|TIER|REGION|SOURCE|CHANNEL|REASON|PRIORITY|KIND|GROUP)(_|$)")
SUFFIXES = ("_CORE", "_HUB", "_MAIN", "_BASE")


# ---------------------------------------------------------------- names and types

def semantic_type(entity: Dict[str, Any], column: str) -> Optional[str]:
    """What a gold column is when nothing maps into it: the table's own surrogate key, its HKEY and the audit columns
    are produced by the gold model itself (SURROGATE_KEY, DERIVED_KEY, AUDIT_TIMESTAMP), so mapping and the STTM do not
    ask for a source. GDP_IS_ACTIVE is carried from silver and is mapped like any column."""
    name, table = column.upper(), str(entity.get("entity_name") or "").upper()
    if name == f"{table}_SKEY" or (name.endswith("_SKEY") and any(a.get("is_pk") and a["name"].upper() == name
                                                                   for a in entity.get("attributes") or [])):
        return "SURROGATE_KEY"
    if name == f"{table}_HKEY":
        return "DERIVED_KEY"
    if name in ("GDP_INSERTED_TS", "GDP_INSERTED_BY", "GDP_UPDATED_TS", "GDP_UPDATED_BY"):
        return "AUDIT_TIMESTAMP"
    return None


def is_descriptor(name: str, data_type: str) -> bool:
    """A text column that names a category (status, stage, type, ...): a dimension candidate. Keys, ids and silver
    technical columns never are."""
    n = name.upper()
    if n.endswith(("_ID", "_SKEY", "_HKEY", "_KEY")) or n == "SOURCE_UNIQUE_ID" or SILVER_TECH.match(n):
        return False
    return bool(DESCRIPTOR.search(n)) and gold_type(n, data_type) == "STRING"


def stem(table: str) -> str:
    t = table.upper()
    for s in SUFFIXES:
        if t.endswith(s) and len(t) > len(s):
            return t[: -len(s)]
    return t


def gold_type(name: str, silver_type: str) -> str:
    """The gold-model skill's data type for a column (data-types.md)."""
    n, t = name.upper(), re.sub(r"\s+", "", (silver_type or "").upper())
    base = t.split("(")[0]
    if n.endswith("_SKEY"):
        return "LONG"
    if n.startswith("IS_") or n.endswith("_FLAG") or base == "BOOLEAN":
        return "BOOLEAN"
    if n.endswith("_ID") or base in ("VARCHAR", "STRING", "TEXT", "CHAR", "CHARACTER", "NVARCHAR", "NCHAR"):
        return "STRING"
    if base == "DATE" or (n.endswith("_DATE") and base not in ("TIMESTAMP_NTZ", "TIMESTAMP", "DATETIME", "TIMESTAMP_LTZ", "TIMESTAMP_TZ")):
        return "DATE"
    if base in ("TIMESTAMP_TZ",):
        return "TIMESTAMP_TZ"
    if base.startswith("TIMESTAMP") or base == "DATETIME" or n.endswith("_TS"):
        return "TIMESTAMP_NTZ"
    if re.search(r"(LATITUDE|LONGITUDE|^LAT$|^LNG$|^LON$|_LAT$|_LNG$|_LON$)", n):
        return "NUMBER(15,8)"
    if re.search(r"(_RATE|_PCT|_PERCENTAGE|_RATIO)$", n):
        return "NUMBER(10,5)"
    if re.search(r"(_COUNT|_QTY|_QUANTITY|_NUMBER_OF_[A-Z_]+|_DAYS|_FLOORS|_UNITS)$", n) or base in ("INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT"):
        return "NUMBER(38,0)"
    if base in ("NUMBER", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL"):
        m = re.match(r"^(NUMBER|NUMERIC|DECIMAL)\((\d+),0\)$", t)
        return "NUMBER(38,0)" if m else "NUMBER(38,5)"
    if base in ("VARIANT", "OBJECT", "ARRAY"):
        return base
    return "STRING"


def gold_location(domain: str, env: str = "DEV") -> Dict[str, Any]:
    d, e = domain.upper(), env.upper()
    return {"platform": "ICEBERG", "database": f"{e}_{PREFIX}_GOLD_{d}_DB", "schema": d,
            "iceberg": {"external_volume": f"{PREFIX}_SF_{e}_GOLD", "catalog": "SNOWFLAKE",
                        "base_location": f"{PREFIX.lower()}-sf-{e.lower()}-gold/{d.lower()}"}}


def _attr(name: str, datatype: str, *, nullable: bool = True, pk: bool = False, description: str = "") -> Dict[str, Any]:
    return {"name": name, "datatype": datatype, "nullable": False if pk else nullable, "is_pk": pk, "description": description}


def _closing(table: str, business: List[str]) -> List[Dict[str, Any]]:
    cols = [_attr(f"{table}_HKEY", "STRING", nullable=False, description=f"Hash of the business columns: {', '.join(business[:8])}"
                  + (" ..." if len(business) > 8 else ""))]
    cols += [_attr(n, t, nullable=False, description="Audit column") for n, t in AUDIT]
    return cols


# ---------------------------------------------------------------- the proposal

def _mastered_keys(columns: Iterable[Dict[str, Any]], consumes: List[Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any], str]]:
    """(silver column, consumed entry, role prefix) for every pre-MDM key of a mastered domain the table carries."""
    out = []
    for c in columns:
        name = str(c["name"]).upper()
        for entry in consumes:
            key = str(entry["silver_column"]).upper()
            if name == key:
                out.append((name, entry, ""))
            elif name.endswith("_" + key):
                out.append((name, entry, name[: -len(key)]))
    return out


def propose(domain: str, inputs: List[Dict[str, Any]], *, consumes: List[Dict[str, Any]],
            mastered: Dict[str, Dict[str, Any]], env: str = "DEV") -> Dict[str, Any]:
    """A gold specification (spec v2, layer GOLD) and its mapping lines for a consumer domain.

    inputs: [{database, schema, table, layer, columns: [{name, data_type, nullable, comment, distinct_count}], row_count}]
    consumes: the domain's MDM contract entries [{domain, silver_column, gold_column}]
    mastered: mastered domain name -> its MDM contract (pre_mdm, gold, ...)
    """
    domain = domain.upper()
    loc = gold_location(domain, env)
    hub_skeys = {f"{str(t['table']).upper()}_SKEY": str(t["table"]).upper() for t in inputs}
    entities: List[Dict[str, Any]] = []
    rels: List[Dict[str, Any]] = []
    lines: List[Dict[str, Any]] = []
    notes: List[str] = []
    conformed: Dict[str, Dict[str, Any]] = {}

    def conform(dom: str) -> Optional[Dict[str, Any]]:
        c = mastered.get(dom)
        if not c:
            notes.append(f"{dom} is consumed but has no mastered contract; its key stays a plain column")
            return None
        g = c["gold"]
        if g["dimension"] not in conformed:
            conformed[g["dimension"]] = {
                "entity_name": g["dimension"], "table_type": "DIMENSION", "conformed_from": dom,
                "database": str(g["database"]).replace("{ENV}", env.upper()), "schema": g["schema"],
                "description": f"Conformed {dom.lower()} dimension, one row per {g['mastered_id']} (owned by the {dom} gold model).",
                "business_keys": [g["mastered_id"]],
                "attributes": [_attr(g["skey"], "LONG", pk=True, description="Surrogate key"),
                               _attr(g["mastered_id"], "STRING", nullable=False, description="Mastered id from Reltio")]}
        return c

    def line(table: str, column: str, kind: str, src: Optional[Dict[str, Any]] = None, src_col: Optional[str] = None,
             expression: Optional[str] = None, note: str = "", layer: Optional[str] = None) -> None:
        lines.append({"gold_table": table, "gold_column": column, "kind": kind,
                      "source_layer": layer or (src or {}).get("layer"),
                      "source_table": f"{src['database']}.{src['schema']}.{src['table']}".upper() if src else None,
                      "source_column": src_col, "expression": expression, "note": note})

    # a child table carrying another input's hub skey and a mastered key is a bridge (many-to-many)
    bridges = {}
    for t in inputs:
        cols = {str(c["name"]).upper() for c in t.get("columns") or []}
        parents = [h for k, h in hub_skeys.items() if k in cols and h != str(t["table"]).upper()]
        if parents and _mastered_keys(t.get("columns") or [], consumes):
            bridges[str(t["table"]).upper()] = parents[0]

    fact_of: Dict[str, str] = {}
    facts: List[Dict[str, Any]] = []
    for t in inputs:
        table = str(t["table"]).upper()
        cols = t.get("columns") or []
        names = {str(c["name"]).upper() for c in cols}
        mk = _mastered_keys(cols, consumes)
        mk_cols = {m[0] for m in mk}
        if table in bridges:
            parent = bridges[table]
            first = mk[0][1]["domain"] if mk else "LINK"
            role = next((c for c in names if ROLE_COLUMN.search(c)), None)
            gname = f"FACT_{stem(parent)}_{first}_ROLE" if role else f"BRIDGE_{stem(parent)}_{first}"
            kind, grain = "BRIDGE", f"one row per {stem(parent).lower()} and {first.lower()}" + (" and role" if role else "")
        else:
            gname, kind, grain = f"FACT_{stem(table)}", "FACT", f"one row per {table} source record"
        fact_of[table] = gname
        skey = f"{gname}_SKEY"
        attrs = [_attr(skey, "LONG", pk=True, description="Surrogate key, from sequence SEQ_" + gname + "_SKEY")]
        line(gname, skey, "KEY", expression=f"SEQ_{gname}_SKEY.NEXTVAL (kept for an existing row)", note="own surrogate key")
        business: List[str] = []
        if "SOURCE_UNIQUE_ID" in names:
            attrs.append(_attr("SOURCE_UNIQUE_ID", "STRING", nullable=False, description="Source record id (merge key with the data source)"))
            line(gname, "SOURCE_UNIQUE_ID", "COLUMN", t, "SOURCE_UNIQUE_ID")
            business.append("SOURCE_UNIQUE_ID")
        if "REF_GDP_SOURCE_SYSTEM_SKEY" in names:
            attrs.append(_attr("DIM_DATA_SOURCE_SKEY", "LONG", nullable=False, description="Data source of the record"))
            line(gname, "DIM_DATA_SOURCE_SKEY", "RESOLVE", t, "REF_GDP_SOURCE_SYSTEM_SKEY",
                 expression="DIM_DATA_SOURCE on the source system name of REF_GDP_SOURCE_SYSTEM",
                 note="confirm the data-source lookup used by existing gold facts")
            business.append("DIM_DATA_SOURCE_SKEY")
        else:
            notes.append(f"{table} has no REF_GDP_SOURCE_SYSTEM_SKEY: the fact's merge key is SOURCE_UNIQUE_ID alone")
        # links to other facts of the build (a bridge's parent)
        for k, parent in hub_skeys.items():
            if k in names and parent != table:
                fk = f"{fact_of.get(parent, 'FACT_' + stem(parent))}_SKEY"
                attrs.append(_attr(fk, "LONG", description=f"The {stem(parent).lower()} this row belongs to"))
                line(gname, fk, "RESOLVE", t, k, expression=f"{fact_of.get(parent, 'FACT_' + stem(parent))} on the parent's source record",
                     note="parent fact key")
                rels.append({"from_entity": gname, "from_column": fk, "to_entity": fact_of.get(parent, "FACT_" + stem(parent)),
                             "to_column": fk, "cardinality": "N:1"})
                business.append(fk)
        # mastered keys become conformed dimension keys
        for silver_col, entry, prefix in mk:
            c = conform(str(entry["domain"]).upper())
            if not c:
                continue
            g = c["gold"]
            gcol = f"{prefix}{entry.get('gold_column') or g['skey']}".upper()
            attrs.append(_attr(gcol, "LONG", nullable=False, description=f"Mastered {entry['domain'].lower()} ({prefix.rstrip('_').lower() or 'primary'}), -1 when unresolved"))
            line(gname, gcol, "RESOLVE", t, silver_col, layer="MDM",
                 expression=(f"{silver_col} -> {c['pre_mdm']['table']} -> {c['cross_reference']['table']} on "
                             f"{c['cross_reference']['source_key']} + source system name -> {g['dimension']}.{g['skey']}, else -1"),
                 note="full cross-reference, active and latest rows, never the STAGE delta")
            rels.append({"from_entity": gname, "from_column": gcol, "to_entity": g["dimension"], "to_column": g["skey"],
                         "cardinality": "N:1"})
            business.append(gcol)
        # descriptors with few values become dimensions; the rest are fact attributes
        for c in cols:
            n = str(c["name"]).upper()
            # handled above (keys), or silver-only (hub skey, silver HKEY, GDP_* audit: gold injects its own)
            if n in mk_cols or n in hub_skeys or n in ("SOURCE_UNIQUE_ID", "REF_GDP_SOURCE_SYSTEM_SKEY") or SILVER_TECH.match(n):
                continue
            distinct = c.get("distinct_count")
            if kind == "FACT" and is_descriptor(n, c.get("data_type", "")) and distinct is not None and 0 < int(distinct) <= DIM_MAX_DISTINCT:
                dname = f"DIM_{stem(table)}_{n}"
                fk = f"{dname}_SKEY"
                if not any(e["entity_name"] == dname for e in entities):
                    entities.append({
                        "entity_name": dname, "table_type": "DIMENSION", "database": loc["database"], "schema": loc["schema"],
                        "grain": f"one row per {n} value", "business_keys": [n],
                        "description": f"{n.replace('_', ' ').title()} of {stem(table).lower()} ({distinct} values when profiled).",
                        "attributes": [_attr(f"{dname}_SKEY", "LONG", pk=True, description="Surrogate key"),
                                       _attr(n, "STRING", nullable=False, description=c.get("comment") or f"{n} value")]
                        + _closing(dname, [n])})
                    line(dname, f"{dname}_SKEY", "KEY", expression=f"SEQ_{dname}_SKEY.NEXTVAL (kept for an existing value)",
                         note="own surrogate key")
                    line(dname, n, "COLUMN", t, n, expression=f"distinct {n}", note="proposed dimension: confirm the values belong in gold")
                    line(dname, f"{dname}_HKEY", "DERIVED", expression=f"SHA2 of {n}")
                    for a, _ in AUDIT:
                        line(dname, a, "AUDIT", expression="set by the gold load")
                attrs.append(_attr(fk, "LONG", description=f"FK to {dname}"))
                line(gname, fk, "RESOLVE", t, n, expression=f"{dname} on {n}", note="proposed dimension key")
                rels.append({"from_entity": gname, "from_column": fk, "to_entity": dname, "to_column": f"{dname}_SKEY", "cardinality": "N:1"})
                business.append(fk)
                continue
            gt = gold_type(n, c.get("data_type", ""))
            name = n[:-2] + "_FLAG" if n.endswith("_F") and n != "ACTIVE_F" else n
            attrs.append(_attr(name, gt, nullable=bool(c.get("nullable", True)), description=c.get("comment") or ""))
            line(gname, name, "COLUMN", t, n, expression=None if name == n and gt == "STRING" else f"cast to {gt}",
                 note="renamed _F to _FLAG" if name != n else "")
            business.append(name)
        attrs += _closing(gname, business)
        line(gname, f"{gname}_HKEY", "DERIVED", expression=f"SHA2 of {', '.join(business[:6])}{' ...' if len(business) > 6 else ''}")
        for a, _ in AUDIT:
            line(gname, a, "AUDIT", expression="set by the gold load")
        facts.append({
            "entity_name": gname, "table_type": kind, "database": loc["database"], "schema": loc["schema"],
            "grain": grain, "business_keys": [k for k in ("SOURCE_UNIQUE_ID", "DIM_DATA_SOURCE_SKEY") if k in business] or business[:2],
            "description": f"{'Many-to-many link' if kind == 'BRIDGE' else 'Fact'} from silver {table} ({grain}).",
            "attributes": attrs})
    entities = facts + entities + list(conformed.values())
    return {"spec": {"domain": domain, "layer": "GOLD", "mode": "NEW", "description": f"Gold model of {domain}",
                     "physical_defaults": loc, "naming_standard": {"preset": "NONE"},
                     "design": {"entities": entities, "primary_entity": entities[0]["entity_name"] if entities else None},
                     "relationships": [dict(r, enforce="NONE") for r in rels]},
            "lines": lines, "notes": notes}
