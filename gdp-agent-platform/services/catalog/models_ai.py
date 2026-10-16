"""Modeling hub proposal (Snowpark handler): a first model for chosen source tables, grounded in their profiles, the
domain's registered models and knowledge, and the modeling skills bound to the MODELING stage (AI-DATA-MODELING,
AI-MODEL-GENERATION, SEMANTIC-COLUMN-CLUSTERING, MODEL-JSON-TO-DDL, loaded through services.knowledge.usage).

Without AI the proposal is the profiled tables as entities (columns as-is, keys from the profile) with foreign keys
inferred from the profiles (services.profiling.insights.infer_relationships). With AI the design prompt of
services.modeling.design is reused, so hub and run designs follow the same rules.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from services.catalog.models import normalize_spec
from services.common.sql import rows


def _split(fqn: str) -> Tuple[str, str, str]:
    parts = [p.strip().strip('"') for p in str(fqn or "").split(".")]
    assert len(parts) == 3 and all(parts), f"{fqn}: use DATABASE.SCHEMA.TABLE"
    return parts[0], parts[1], parts[2]


def _docs(session, tables: List[str]) -> Dict[str, Dict[str, Any]]:
    from services.profiling.procedures import read_document

    out: Dict[str, Dict[str, Any]] = {}
    for fqn in tables:
        database, schema, table = _split(fqn)
        found = rows(session, """SELECT PROFILE_STAGE_PATH FROM CORE.TABLE_PROFILES
                                  WHERE UPPER(DATABASE_NAME) = UPPER(?) AND UPPER(SCHEMA_NAME) = UPPER(?)
                                    AND UPPER(TABLE_NAME) = UPPER(?) AND SOURCE_FINGERPRINT <> ''
                                  ORDER BY PROFILED_AT DESC LIMIT 1""", [database, schema, table])
        if not found:
            continue
        try:
            doc = read_document(session, found[0]["PROFILE_STAGE_PATH"])
        except Exception:
            doc = None
        if doc:
            out[table.upper()] = doc
    return out


def baseline(docs: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(design, relationships): every profiled table as an entity, foreign keys from the profiles."""
    from services.profiling.insights import infer_relationships

    entities = []
    for table, doc in sorted(docs.items()):
        cols = doc.get("columns") or []
        keys = [c["column_name"] for c in cols if c.get("potential_key")][:1]
        entities.append({"entity_name": table, "kind": "TABLE", "purpose": f"{table} as profiled",
                         "grain": f"one row per {keys[0]}" if keys else "", "business_keys": keys,
                         "attributes": [{"name": c["column_name"], "datatype": c.get("data_type") or "VARCHAR",
                                         "nullable": float((c.get("statistics") or {}).get("null_percentage") or 0) > 0,
                                         "is_pk": c["column_name"] in keys,
                                         "source_columns": [f"{table}.{c['column_name']}"],
                                         "rationale": c.get("description") or ""} for c in cols]})
    rels = []
    for j in infer_relationships(docs):
        key = j["keys"][0]
        child_col, _, parent_col = key.partition("=")
        rels.append({"from_entity": j["left"], "from_column": child_col, "to_entity": j["right"],
                     "to_column": parent_col or child_col, "cardinality": j.get("cardinality") or "N:1",
                     "confidence": j.get("confidence")})
    return {"decision": "NEW", "entities": entities}, rels


def _domain_context(session, domain: Optional[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not domain:
        return [], []
    found = rows(session, "SELECT DOMAIN_ID FROM CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = UPPER(?) LIMIT 1",
                 [domain])
    if not found:
        return [], []
    domain_id = found[0]["DOMAIN_ID"]
    models = []
    for t in rows(session, """SELECT TARGET_TABLE_ID, TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE, DESCRIPTION
                                FROM CORE.TARGET_TABLE_REGISTRY WHERE DOMAIN_ID = ? AND ACTIVE_FLAG AND COALESCE(LAYER, 'SILVER') = 'SILVER'
                               ORDER BY TARGET_TABLE LIMIT 40""", [domain_id]):
        cols = [f"{c['COLUMN_NAME']} {c['DATA_TYPE']}" for c in rows(
            session, "SELECT COLUMN_NAME, DATA_TYPE FROM CORE.TARGET_COLUMN_REGISTRY WHERE TARGET_TABLE_ID = ? "
                     "ORDER BY ORDINAL_POSITION", [t["TARGET_TABLE_ID"]])]
        models.append({"fqn": f"{t['TARGET_DATABASE']}.{t['TARGET_SCHEMA']}.{t['TARGET_TABLE']}".upper(),
                       "description": t["DESCRIPTION"], "columns": cols})
    knowledge = [f"[{k['KNOWLEDGE_TYPE']}] {k['TITLE']}: {str(k['CONTENT'] or '')[:300]}" for k in rows(
        session, """SELECT KNOWLEDGE_TYPE, TITLE, CONTENT FROM CORE.DOMAIN_KNOWLEDGE
                     WHERE DOMAIN_ID = ? AND IS_CURRENT AND COALESCE(STATUS, 'ACTIVE') = 'ACTIVE'
                       AND KNOWLEDGE_TYPE IN ('MODEL_DEFINITION', 'NAMING_STANDARD', 'GLOSSARY', 'MAPPING_PATTERN')
                     ORDER BY IFF(KNOWLEDGE_TYPE = 'MODEL_DEFINITION', 0, 1) LIMIT 40""", [domain_id])]
    return models, knowledge


def propose(session, payload_json: str) -> Dict[str, Any]:
    from services.knowledge.usage import use_stage
    from services.modeling import design as dsn

    payload = json.loads(payload_json or "{}")
    tables = [str(t) for t in payload.get("source_tables") or []][:30]
    assert tables, "pick at least one profiled source table"
    docs = _docs(session, tables)
    assert docs, "none of the chosen tables has a profile yet; profile them first"
    mode = str(payload.get("mode") or "NEW").upper()
    context_domain = payload.get("domain") if mode == "MAP" else payload.get("parent_domain") if mode == "FORK" else None
    models, knowledge = _domain_context(session, context_domain)
    try:
        skills = use_stage(session, "MODELING", excerpt=1500)
    except Exception:
        skills = ""
    design, rels = baseline(docs)
    model_used, usage = None, None
    if payload.get("use_ai"):
        from services.common.llm import complete_json

        sources = {t: [{"column": c["column_name"], "type": c.get("data_type"), "semantic": c.get("semantic_type"),
                        "key": bool(c.get("potential_key")), "pii": (c.get("pii_classification") or "NONE") != "NONE",
                        "nulls": (c.get("statistics") or {}).get("null_percentage")} for c in doc.get("columns") or []]
                   for t, doc in docs.items()}
        ctx = {"domain": context_domain or payload.get("domain"), "sources": sources, "models": models,
               "knowledge": knowledge, "skills": skills}
        conv = dsn.conventions(str(payload.get("preset") or "NONE"), payload.get("custom"))
        started = time.time()
        raw, usage, model_used = complete_json(session, dsn.build_prompt(ctx, conv, payload.get("instructions") or ""),
                                               dsn.DESIGN_SCHEMA, max_tokens=8000, stage="MODELING")
        design = dsn.normalize(raw, conv)
        usage = {**(usage or {}), "elapsed_ms": int((time.time() - started) * 1000)}
        names = {e["entity_name"] for e in design["entities"]}
        rels = [r for r in rels if r["from_entity"] in names and r["to_entity"] in names]
    spec = normalize_spec({**payload, "mode": mode, "design": design, "relationships": rels,
                           "source_tables": [t.upper() for t in tables]})
    return {"spec": spec, "model": model_used, "usage": usage,
            "grounding": {"profiled_tables": sorted(docs), "missing_profiles": [t for t in tables if _split(t)[2].upper() not in docs],
                          "models": len(models), "knowledge": len(knowledge), "skills": bool(skills)}}
