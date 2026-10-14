"""A MASTERED MDM contract derived from the post-MDM schema Reltio writes (pure): what a person gives as an MDM_SCHEMA
reference (a live schema such as COMPANY_MDM, or its DDL) becomes the contract the platform resolves mastered keys
with, plus the questions a domain owner answers before it is used.

Found in the schema:
  cross_reference  the *_CROSS_REFERENCE (or *_XREF) table: source key, source-system name, mastered id, MDM entity id,
                   active, deleted and status columns, and the latest-row order
  golden           the golden-record table (<DOMAIN>_CORE, else the table carrying the mastered id), its version order
                   and the related tables
Assumed from the GDP standard (each one is a question): the pre-MDM silver hub, the source-system reference, the
gold dimension and the systems excluded from resolution.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

ENV_PREFIX = re.compile(r"^(DEV|QA|UAT|SIT|STG|STAGE|PRD|PROD)_", re.I)
SOURCE_SYSTEMS = {"database": "{ENV}_GDP_SILVER_DB", "schema": "SHARED", "table": "REF_GDP_SOURCE_SYSTEM",
                  "skey": "GDP_SOURCE_SYSTEM_SKEY", "name": "GDP_SOURCE_SYSTEM_NAME"}
EXCLUDE = ["Reltio", "ReltioGrouping"]


def _envless(database: Optional[str]) -> str:
    d = str(database or "").upper()
    return ENV_PREFIX.sub("{ENV}_", d) if d else "{ENV}_GDP_SILVER_DB"


def _cols(e: Dict[str, Any]) -> List[str]:
    return [str(a.get("name") or "").upper() for a in e.get("attributes") or []]


def _first(cols: List[str], *patterns: str, exclude: Tuple[str, ...] = ()) -> Optional[str]:
    for p in patterns:
        rx = re.compile(p)
        for c in cols:
            if rx.search(c) and c not in exclude:
                return c
    return None


def _where(spec: Dict[str, Any], e: Dict[str, Any]) -> Tuple[str, str]:
    phys = spec.get("physical_defaults") or {}
    db = e.get("database") or spec.get("target_database") or phys.get("database")
    sch = e.get("schema") or spec.get("target_schema") or phys.get("schema")
    return _envless(db), str(sch or "").upper()


def derive(domain: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """{contract, questions, found} from a post-MDM schema specification (importers.from_schema or a parsed file)."""
    d = domain.upper()
    entities = list(((spec or {}).get("design") or {}).get("entities") or [])
    by_name = {str(e.get("entity_name") or "").upper(): e for e in entities}
    questions: List[str] = []
    found: List[str] = []
    xrefs = [n for n in by_name if re.search(r"_(CROSS_REFERENCE|XREF|CROSSWALK)$", n)]
    if not xrefs:
        return {"contract": None, "found": [], "questions": [
            f"No cross-reference table (*_CROSS_REFERENCE) in this schema: is it the post-MDM schema Reltio writes for {d}?"]}
    xname = next((n for n in xrefs if n.startswith(d)), xrefs[0])
    if len(xrefs) > 1:
        questions.append(f"Several cross-reference tables ({', '.join(xrefs)}); {xname} was used")
    x = by_name[xname]
    xc = _cols(x)
    system_name = _first(xc, r"^SOURCE_SYSTEM_NAME$", r"SYSTEM_NAME$", r"^SOURCE_SYSTEM$", r"^(SRC_)?SYSTEM$")
    source_key = _first(xc, r"^SOURCE_UNIQUE_ID$", r"^SOURCE_(RECORD_)?(ID|KEY)$", r"^(SRC|SOURCE)_.*(ID|KEY)$",
                        exclude=tuple(c for c in (system_name,) if c))
    mastered_id = _first(xc, r"ENTERPRISE_.*_ID$", r"^MASTER(ED)?_.*ID$", rf"^GOLDEN_.*ID$", rf"^{d}_(MDM_)?ID$")
    entity_id = _first(xc, r"^MDM_ENTITY_ID$", r"ENTITY_ID$", r"^RELTIO_.*ID$", exclude=tuple(c for c in (mastered_id,) if c))
    active = _first(xc, r"^GDP_IS_ACTIVE$", r"^IS_ACTIVE$", r"ACTIVE(_FLAG|_F)?$")
    deleted = _first(xc, r"^MDM_DELETED?_DATE$", r"DELETED?_(DATE|TS)$")
    status = _first(xc, r"^MDM_STATUS$", r"STATUS$")
    updated = _first(xc, r"^GDP_UPDATED_TS$", r"^MDM_UPDATED_TS$", r"UPDATED_(TS|DATE)$", r"_LUD$")
    if not mastered_id and entity_id:
        mastered_id, entity_id = entity_id, None
        questions.append(f"No enterprise id column in {xname}: {mastered_id} is used as the mastered id")
    for label, value in (("source key", source_key), ("source-system name", system_name), ("mastered id", mastered_id),
                         ("active column", active)):
        if value:
            found.append(f"{label}: {xname}.{value}")
        else:
            questions.append(f"Which column of {xname} is the {label}?")
    if not updated:
        questions.append(f"{xname} has no update timestamp: which column orders a record's latest row?")
    xdb, xsch = _where(spec, x)

    golden_name = next((n for n in (f"{d}_CORE", f"{d}_GOLDEN", f"{d}_MASTER", d) if n in by_name and n != xname), None)
    if not golden_name and mastered_id:
        golden_name = next((n for n, e in by_name.items() if n != xname and mastered_id in _cols(e)
                            and not re.search(r"(STAGE|STG|_DELTA|AUDIT|EVENTS?|MERGE)", n)), None)
    golden: Optional[Dict[str, Any]] = None
    if golden_name:
        g = by_name[golden_name]
        gc = _cols(g)
        gdb, gsch = _where(spec, g)
        order = [f"{c} DESC NULLS LAST" for c in ("MDM_POST_MDM_LUD", "MDM_UPDATED_TS", "GDP_UPDATED_TS") if c in gc][:2]
        golden = {"database": gdb, "schema": gsch, "table": golden_name, "mastered_id": mastered_id,
                  "entity_id": entity_id if entity_id and entity_id in gc else None, "version_order": order,
                  "related": sorted(n for n in by_name if n not in (xname, golden_name) and not re.search(r"(STAGE|STG|_DELTA)$", n))[:20]}
        golden = {k: v for k, v in golden.items() if v not in (None, [])}
        found.append(f"golden record: {golden_name}")
        if mastered_id and mastered_id not in gc:
            questions.append(f"{golden_name} does not carry {mastered_id}: confirm the golden-record table")
    else:
        questions.append("Which table holds the golden record (one row per mastered id)?")

    contract = {
        "role": "MASTERED", "mastered_by": "Reltio", "reference": f"derived from the post-MDM schema {xdb}.{xsch}",
        "pre_mdm": {"database": "{ENV}_GDP_SILVER_DB", "schema": d, "table": f"{d}_CORE", "skey": f"{d}_CORE_SKEY",
                    "source_key": "SOURCE_UNIQUE_ID", "source_system_skey": "REF_GDP_SOURCE_SYSTEM_SKEY",
                    "active_column": "GDP_IS_ACTIVE"},
        "source_systems": dict(SOURCE_SYSTEMS),
        "cross_reference": {k: v for k, v in {
            "database": xdb, "schema": xsch, "table": xname, "source_key": source_key, "system_name": system_name,
            "mastered_id": mastered_id, "entity_id": entity_id, "active_column": active, "deleted_column": deleted,
            "status_column": status, "exclude_systems": list(EXCLUDE),
            "latest_order": [f"{updated} DESC NULLS LAST"] + ([mastered_id] if mastered_id else []) if updated else [],
            "cardinality": "N:1"}.items() if v not in (None, [])},
        "golden": golden or {},
        "gold": {"database": f"{{ENV}}_GDP_GOLD_{d}_DB", "schema": d, "dimension": f"DIM_{d}", "skey": f"DIM_{d}_SKEY",
                 "mastered_id": mastered_id, "sequence": f"SEQ_DIM_{d}_SKEY", "active_column": "GDP_IS_ACTIVE",
                 "dbt_ref": f"dim_{d.lower()}", "unresolved": -1},
    }
    questions += [
        f"The pre-MDM hub is assumed to be {{ENV}}_GDP_SILVER_DB.{d}.{d}_CORE keyed by {d}_CORE_SKEY and SOURCE_UNIQUE_ID",
        f"The gold dimension is assumed to be DIM_{d} in {{ENV}}_GDP_GOLD_{d}_DB.{d}",
        f"Records from {', '.join(EXCLUDE)} are excluded from resolution",
    ]
    return {"contract": contract, "questions": questions, "found": found}
