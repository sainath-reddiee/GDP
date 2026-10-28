"""Human-in-the-loop AI evolution for Domain Models, Table Models, and Domain Knowledge.

Given an existing v2 model specification (`DmSpec`), existing domain knowledge items, and natural-language human
instructions, evolves the entities, attributes, relationships, and domain knowledge while preserving existing entity
and column UUIDs so `model_diff.diff` and `spec_v2.validate` work seamlessly. Nothing overwrites an approved model:
changes return to the workbench draft for human review.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, List, Optional

from services.modeling import importers, model_diff, spec_v2

EVOLVE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string"},
                    "entity_type": {"type": "string"},
                    "entity_grain": {"type": "string"},
                    "name": {"type": "string"},
                    "datatype": {"type": "string"},
                    "nullable": {"type": "boolean"},
                    "is_pk": {"type": "boolean"},
                    "pii": {"type": "boolean"},
                    "description": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["entity_name", "name", "datatype"],
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_entity": {"type": "string"},
                    "from_column": {"type": "string"},
                    "to_entity": {"type": "string"},
                    "to_column": {"type": "string"},
                },
                "required": ["from_entity", "from_column", "to_entity", "to_column"],
            },
        },
        "knowledge": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "target_column": {"type": "string"},
                },
                "required": ["type", "title", "content"],
            },
        },
    },
    "required": ["rationale", "columns"],
}

EVOLVE_PROMPT = """You are an expert Enterprise Data Architect co-designing a governed Snowflake domain data model and
its enterprise knowledge base with a human data modeler.

You are given:
1. CURRENT_MODEL: the current domain model (entities, attributes, keys, types, relationships).
2. EXISTING_KNOWLEDGE: current domain glossary, business rules, and transformation rules.
3. HUMAN_INSTRUCTIONS: what the human wants to add, refine, restructure, or evolve.

Rules:
- Follow HUMAN_INSTRUCTIONS faithfully.
- In `columns`, list ONLY the newly added or modified columns (at most 8 columns total; all unmentioned existing
  entities and columns are preserved automatically).
- Each item in `columns` specifies `entity_name` (uppercase), `name` (uppercase column name), `datatype` (VARCHAR,
  NUMBER(38,0), NUMBER(18,2), FLOAT, BOOLEAN, DATE, TIMESTAMP_NTZ), `nullable`, `is_pk`, `pii`, and `description`.
  For a brand-new entity, include its primary key column (`is_pk: true, nullable: false`) and set `entity_type`
  (DIMENSION, FACT, BRIDGE, TABLE) and `entity_grain`.
- In `relationships`, include any new foreign-key relationships connecting entities (at most 4).
- In `knowledge`, extract any domain knowledge implied by the human's instructions (at most 4 items):
  - `BUSINESS_RULE`: business validations, invariants, or domain policies
  - `GLOSSARY`: business terms and definitions
  - `TRANSFORMATION_RULE`: SQL expressions or standardization rules
  - `MODEL_DEFINITION`: entity grain or modeling rationale
- In `rationale`, write ONE short, concise sentence (under 140 characters) summarizing the recommendation.
"""


def _compact_model_for_prompt(spec: Dict[str, Any]) -> Dict[str, Any]:
    entities = []
    for e in (spec.get("design") or {}).get("entities") or []:
        entities.append({
            "entity_name": e.get("entity_name"),
            "table_type": e.get("table_type"),
            "grain": e.get("grain"),
            "columns": [a.get("name") for a in (e.get("attributes") or [])[:20]],
        })
    return {
        "domain": spec.get("domain"),
        "entities": entities[:15],
        "relationships": [
            f"{r.get('from_entity')}.{r.get('from_column')}->{r.get('to_entity')}.{r.get('to_column')}"
            for r in (spec.get("relationships") or [])[:20]
        ],
    }


def _merge_ai_entities(base: Dict[str, Any], ai_answer: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    """Merge AI-evolved entities and relationships into `base` while preserving all existing entity & attribute UUIDs."""
    out = copy.deepcopy(base)
    by_name = {e["entity_name"].upper(): e for e in out["design"]["entities"]}

    incoming_entities = list(ai_answer.get("entities") or [])
    if not incoming_entities and ai_answer.get("columns"):
        grouped: Dict[str, Dict[str, Any]] = {}
        for col in ai_answer.get("columns") or []:
            if not isinstance(col, dict):
                continue
            ename = str(col.get("entity_name") or "").strip().upper()
            cname = str(col.get("name") or "").strip().upper()
            if not ename or not cname:
                continue
            ent = grouped.setdefault(
                ename,
                {
                    "entity_name": ename,
                    "table_type": str(col.get("entity_type") or "TABLE").strip().upper(),
                    "grain": str(col.get("entity_grain") or "").strip(),
                    "business_keys": [],
                    "attributes": [],
                },
            )
            if col.get("entity_type") and ent["table_type"] == "TABLE":
                ent["table_type"] = str(col["entity_type"]).strip().upper()
            if col.get("entity_grain") and not ent["grain"]:
                ent["grain"] = str(col["entity_grain"]).strip()
            if col.get("is_pk") and cname not in ent["business_keys"]:
                ent["business_keys"].append(cname)
            ent["attributes"].append(
                {
                    "name": cname,
                    "datatype": str(col.get("datatype") or "VARCHAR").strip().upper(),
                    "nullable": col.get("nullable", not bool(col.get("is_pk"))),
                    "is_pk": bool(col.get("is_pk")),
                    "pii": bool(col.get("pii")),
                    "description": str(col.get("description") or "").strip(),
                    "status": str(col.get("status") or "ACTIVE").strip().upper(),
                }
            )
        incoming_entities = list(grouped.values())

    if not incoming_entities:
        return out

    incoming_raw = {
        "domain": out.get("domain"),
        "description": out.get("description"),
        "design": {"entities": incoming_entities},
        "relationships": ai_answer.get("relationships") or [],
    }
    normalized = spec_v2.normalize_v2(incoming_raw, model_id)

    for inc_e in normalized["design"]["entities"]:
        ename = inc_e["entity_name"].upper()
        old_e = by_name.get(ename)
        if old_e:
            inc_by_col = {a["name"].upper(): a for a in inc_e["attributes"]}
            merged_attrs = []
            seen_cols = set()
            # Keep existing attributes in their original order, applying any AI modifications in-place
            for old_a in old_e["attributes"]:
                cname = old_a["name"].upper()
                seen_cols.add(cname)
                inc_a = inc_by_col.get(cname)
                if inc_a:
                    inc_a["id"] = old_a["id"]
                    for k in ("business_definition", "description", "semantic_type", "accepted_values", "tags", "classification"):
                        if not inc_a.get(k) and old_a.get(k):
                            inc_a[k] = old_a[k]
                    if not (inc_a.get("pii") or {}).get("flag") and (old_a.get("pii") or {}).get("flag"):
                        inc_a["pii"] = old_a["pii"]
                    merged_attrs.append(inc_a)
                else:
                    merged_attrs.append(old_a)
            # Append newly added attributes
            for inc_a in inc_e["attributes"]:
                cname = inc_a["name"].upper()
                if cname not in seen_cols:
                    seen_cols.add(cname)
                    merged_attrs.append(inc_a)
            inc_e["id"] = old_e["id"]
            inc_e["attributes"] = merged_attrs
            for k in ("table_type", "scd_type", "business_keys", "database", "schema", "model_spec", "physical", "unique_constraints", "checks", "subject_area", "grain", "description"):
                if (not inc_e.get(k) or (k in ("table_type", "scd_type") and inc_e[k] in ("TABLE", 1))) and old_e.get(k):
                    inc_e[k] = old_e[k]
            idx = out["design"]["entities"].index(old_e)
            out["design"]["entities"][idx] = inc_e
        else:
            out["design"]["entities"].append(inc_e)
            by_name[ename] = inc_e

    existing_rels = {(r["from_entity"], r["from_column"], r["to_entity"], r["to_column"]): r for r in out.get("relationships") or []}
    for r in normalized.get("relationships") or []:
        key = (r["from_entity"], r["from_column"], r["to_entity"], r["to_column"])
        if key not in existing_rels:
            out.setdefault("relationships", []).append(r)
            existing_rels[key] = r

    return spec_v2.normalize_v2(out, model_id)


def _fallback_evolve(base: Dict[str, Any], instructions: str, model_id: str) -> Dict[str, Any]:
    """Deterministic fallback when Cortex LLM is unavailable or when user pastes DDL / structured instructions."""
    text = (instructions or "").strip()
    out = copy.deepcopy(base)
    knowledge: List[Dict[str, Any]] = []
    notes: List[str] = []

    # 1. Check if user pasted CREATE TABLE DDL
    if re.search(r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE", text, re.I):
        parsed = importers.parse_ddl(text)
        if parsed.get("design", {}).get("entities"):
            out = importers.merge(out, parsed, model_id, replace=False)
            notes.append(f"Merged {len(parsed['design']['entities'])} table(s) from DDL")

    # 2. Check for "add table/entity <NAME>"
    for m in re.finditer(r"(?:add|create|new)\s+(?:a\s+)?(?:fact|dimension|bridge|reference|hub|spoke)?\s*(?:table|entity)\s+(?:called\s+|named\s+)?([A-Za-z_][A-Za-z0-9_]*)", text, re.I):
        tname = m.group(1).upper()
        if tname in ("WITH", "TO", "FOR", "IN", "THE", "AND", "OR"):
            continue
        if not any(e["entity_name"].upper() == tname for e in out["design"]["entities"]):
            pk_name = f"{tname}_ID"
            kind = "FACT" if re.search(r"\bfact\b", m.group(0), re.I) else "DIMENSION" if re.search(r"\bdim", m.group(0), re.I) else "TABLE"
            attrs = [
                {"name": pk_name, "datatype": "VARCHAR", "nullable": False, "is_pk": True, "description": f"Primary key of {tname}"},
            ]
            # Extract columns mentioned after "with columns ..." or "columns: ..."
            col_match = re.search(rf"{re.escape(m.group(1))}[^.;\n]*?(?:with\s+(?:columns?|fields?|attributes?)|columns?:)\s*([^.;\n]+)", text, re.I)
            if col_match:
                for raw_col in re.split(r",|\band\b", col_match.group(1)):
                    clean = re.sub(r"\([^)]*\)", "", raw_col).strip()
                    tokens = [tok for tok in re.split(r"\s+", clean) if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tok)]
                    if not tokens:
                        continue
                    cname = tokens[0].upper()
                    if cname in ("PK", "PRIMARY", "KEY", "NOT", "NULL", "PII", pk_name):
                        continue
                    dtype = "DATE" if cname.endswith(("_DATE", "_DT")) else "TIMESTAMP_NTZ" if cname.endswith(("_TS", "_AT")) else "NUMBER(18,2)" if cname.endswith(("_AMT", "_AMOUNT", "_RENT", "_PRICE", "_COST", "_VALUE")) else "VARCHAR"
                    if len(tokens) > 1 and tokens[1].upper() in ("VARCHAR", "NUMBER", "DATE", "BOOLEAN", "FLOAT", "INTEGER", "TIMESTAMP_NTZ"):
                        dtype = tokens[1].upper()
                    attrs.append({
                        "name": cname,
                        "datatype": dtype,
                        "nullable": True,
                        "is_pk": False,
                        "description": f"{cname.replace('_', ' ').title()}",
                    })
            attrs.append({"name": "LOAD_TS", "datatype": "TIMESTAMP_NTZ", "nullable": False, "is_pk": False, "description": "Record load timestamp"})
            out["design"]["entities"].append({
                "entity_name": tname,
                "table_type": kind,
                "kind": kind if kind in spec_v2.V1_KINDS else "TABLE",
                "scd_type": 1,
                "grain": f"One row per {tname.lower().replace('_', ' ')}",
                "description": f"Added from human instructions: {tname}",
                "business_keys": [pk_name],
                "attributes": attrs,
            })
            notes.append(f"Added entity {tname}")
            knowledge.append({
                "type": "MODEL_DEFINITION",
                "title": f"{tname} entity definition",
                "content": f"{tname} ({kind}): One row per {tname.lower().replace('_', ' ')}.",
                "target_column": pk_name,
            })

    # 3. Check for "add column(s) <COL> to <ENTITY>" or general column additions
    if out["design"]["entities"]:
        target_ent = out["design"]["entities"][0]
        for e in out["design"]["entities"]:
            if re.search(rf"\b{re.escape(e['entity_name'])}\b", text, re.I):
                target_ent = e
                break
        existing_col_names = {a["name"].upper() for a in target_ent["attributes"]}

        for cm in re.finditer(r"(?:add|include)\s+(?:a\s+)?(?:column|field|attribute)s?\s+([A-Za-z0-9_,\s]+?)(?:\s+to\s+([A-Za-z_][A-Za-z0-9_]*))?(?:[.;\n]|$)", text, re.I):
            ent = target_ent
            if cm.group(2):
                found = next((x for x in out["design"]["entities"] if x["entity_name"].upper() == cm.group(2).upper()), None)
                if found:
                    ent = found
            ent_cols = {a["name"].upper() for a in ent["attributes"]}
            for piece in re.split(r",|\band\b", cm.group(1)):
                tokens = [t for t in re.split(r"\s+", piece.strip()) if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", t)]
                if not tokens:
                    continue
                cname = tokens[0].upper()
                if cname in ("A", "AN", "THE", "COLUMN", "COLUMNS", "FIELD", "FIELDS", "ATTRIBUTE", "ATTRIBUTES", "TO", "IN", "WITH") or cname in ent_cols:
                    continue
                dtype = "DATE" if cname.endswith(("_DATE", "_DT")) else "TIMESTAMP_NTZ" if cname.endswith(("_TS", "_AT")) else "NUMBER(18,2)" if cname.endswith(("_AMT", "_AMOUNT", "_RENT", "_PRICE", "_COST", "_RATE")) else "BOOLEAN" if cname.startswith(("IS_", "HAS_")) or cname.endswith("_FLAG") else "VARCHAR"
                ent["attributes"].append({
                    "name": cname,
                    "datatype": dtype,
                    "nullable": True,
                    "is_pk": False,
                    "description": f"{cname.replace('_', ' ').title()}",
                    "business_definition": f"{cname.replace('_', ' ').title()}",
                })
                ent_cols.add(cname)
                notes.append(f"Added column {ent['entity_name']}.{cname} ({dtype})")

        # 4. Check for SCD2 / audit columns request
        if re.search(r"\b(?:scd\s*2|scd2|audit\s+columns?|effective\s+dates?)\b", text, re.I):
            for std_col, std_type, std_desc in [
                ("EFFECTIVE_START_TS", "TIMESTAMP_NTZ", "SCD2 row effective start timestamp"),
                ("EFFECTIVE_END_TS", "TIMESTAMP_NTZ", "SCD2 row effective end timestamp"),
                ("IS_CURRENT", "BOOLEAN", "True when row is the current active version"),
                ("RECORD_UPDATED_TS", "TIMESTAMP_NTZ", "Audit timestamp of last update"),
            ]:
                if std_col not in existing_col_names:
                    target_ent["attributes"].append({
                        "name": std_col,
                        "datatype": std_type,
                        "nullable": True,
                        "is_pk": False,
                        "description": std_desc,
                    })
                    existing_col_names.add(std_col)
            if re.search(r"\b(?:scd\s*2|scd2)\b", text, re.I):
                target_ent["scd_type"] = 2
            notes.append(f"Applied audit / SCD2 columns on {target_ent['entity_name']}")

        # 5. Check for PII tagging instructions
        if re.search(r"\bpii\b|\bsensitive\b|\bpersonal\b", text, re.I):
            pii_cols = []
            for e in out["design"]["entities"]:
                for a in e["attributes"]:
                    cname = a["name"].upper()
                    if cname.endswith(("_SKEY", "_HKEY", "_ID", "_CODE", "_TYPE", "_SCORE")):
                        continue
                    if re.search(rf"\b{re.escape(cname)}\b", text, re.I) or any(
                        k in cname for k in ("EMAIL", "PHONE", "SSN", "TAX_ID", "PASSPORT", "DOB", "BIRTH_DATE", "FIRST_NAME", "LAST_NAME", "FULL_NAME", "CONTACT_NAME", "TENANT_NAME", "OWNER_NAME")
                    ):
                        a["pii"] = {"flag": True, "classification": "DIRECT"}
                        pii_cols.append(f"{e['entity_name']}.{cname}")
            if pii_cols:
                notes.append(
                    f"Marked {pii_cols[0]} as PII" if len(pii_cols) == 1 else f"Marked {len(pii_cols)} personal/contact column(s) as PII"
                )

    # 6. Check for business rule / glossary instructions
    if re.search(r"\b(?:rule|must|shall|validate|glossary|synonym|definition)\b", text, re.I):
        knowledge.append({
            "type": "BUSINESS_RULE",
            "title": text[:80].strip(),
            "content": text,
            "target_column": "",
        })

    normalized = spec_v2.normalize_v2(out, model_id)
    unique_notes = list(dict.fromkeys(notes))
    rationale = "; ".join(unique_notes[:3]) if unique_notes else "Recommendation ready for your model."
    return {
        "spec": normalized,
        "rationale": rationale,
        "knowledge_proposals": knowledge,
        "model": "deterministic-fallback",
        "usage": None,
    }


def _normalize_knowledge_proposals(domain_name: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        ktype = str(item.get("type") or "BUSINESS_RULE").strip().upper()
        if ktype not in ("GLOSSARY", "BUSINESS_RULE", "TRANSFORMATION_RULE", "MODEL_DEFINITION", "MAPPING_PATTERN"):
            ktype = "BUSINESS_RULE"
        title = str(item.get("title") or "").strip()[:500]
        content = str(item.get("content") or "").strip()[:8000]
        if not title or not content:
            continue
        col = str(item.get("target_column") or "").strip().upper()
        cjson: Dict[str, Any] = {}
        if col:
            cjson["target_column"] = col
        if item.get("synonyms"):
            cjson["synonyms"] = [str(s) for s in item.get("synonyms") or [] if str(s).strip()]
        if item.get("expression"):
            cjson["expression"] = str(item.get("expression") or "").strip()
        out.append({
            "key": f"{domain_name.lower()}.{ktype.lower()}.{col.lower() or idx}",
            "knowledge_type": ktype,
            "title": title,
            "content": content,
            "content_json": cjson or None,
            "target_column": col or None,
        })
    return out


def evolve_domain_model(
    session: Any,
    current_spec: Dict[str, Any],
    instructions: str,
    model_id: str,
    existing_knowledge: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Evolve a v2 Domain Model (`DmSpec`) and its Domain Knowledge from natural-language human instructions."""
    from services.common.llm import model_for
    from services.common.sql import rows

    base = spec_v2.normalize_v2(current_spec, model_id)
    domain_name = str(base.get("domain") or "DOMAIN").strip().upper()
    clean_instructions = (instructions or "").strip()
    if not clean_instructions:
        raise AssertionError("Provide instructions describing how to evolve the model or knowledge.")

    json_hint = (
        '\nReturn ONLY valid JSON (no markdown fences) matching this shape:\n'
        '{"rationale": "...", "columns": [{"entity_name": "...", "entity_type": "DIMENSION|FACT|TABLE", '
        '"entity_grain": "...", "name": "...", "datatype": "VARCHAR|NUMBER(18,2)|DATE|TIMESTAMP_NTZ|BOOLEAN", '
        '"nullable": true, "is_pk": false, "pii": false, "description": "..."}], '
        '"relationships": [{"from_entity": "...", "from_column": "...", "to_entity": "...", "to_column": "..."}], '
        '"knowledge": [{"type": "BUSINESS_RULE|GLOSSARY|TRANSFORMATION_RULE", "title": "...", "content": "...", "target_column": "..."}]}\n'
    )
    prompt_payload = (
        f"{EVOLVE_PROMPT}{json_hint}\n"
        f"CURRENT_MODEL:\n{json.dumps(_compact_model_for_prompt(base))}\n\n"
        f"EXISTING_KNOWLEDGE:\n{json.dumps((existing_knowledge or [])[:15])}\n\n"
        f"HUMAN_INSTRUCTIONS:\n{clean_instructions[:8000]}\n"
    )

    try:
        if session is None:
            raise RuntimeError("No Snowflake session for Cortex LLM")
        llm_model = model_for(session, "MODELING")
        raw_rows = rows(session, "SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?) AS R", [llm_model, prompt_payload])
        raw_text = str((raw_rows[0] if raw_rows else {}).get("R") or "").strip()
        match = re.search(r"\{.*\}", raw_text, re.S)
        if not match:
            raise ValueError("No JSON object in Cortex response")
        answer = json.loads(match.group(0))
        evolved = _merge_ai_entities(base, answer, model_id)
        knowledge_proposals = _normalize_knowledge_proposals(domain_name, answer.get("knowledge") or [])
        rationale = str(answer.get("rationale") or "Evolved model and domain knowledge from your instructions.").strip()
        usage = None
    except Exception:
        fallback = _fallback_evolve(base, clean_instructions, model_id)
        evolved = fallback["spec"]
        knowledge_proposals = _normalize_knowledge_proposals(domain_name, fallback["knowledge_proposals"])
        rationale = fallback["rationale"]
        llm_model = fallback["model"]
        usage = fallback["usage"]

    changes = model_diff.diff(base, evolved)
    issues = spec_v2.validate(evolved)
    return {
        "spec": evolved,
        "issues": issues,
        "counts": changes["counts"],
        "changes": changes["changes"][:50],
        "breaking": changes["breaking"],
        "rationale": rationale,
        "knowledge_proposals": knowledge_proposals,
        "model": llm_model,
        "usage": usage,
    }
