"""Target model design for a run: AI-proposed from the run's own context, edited by the developer, versioned, and
applied to the target registry and knowledge on approval.

The design is grounded only in what the platform knows: the run's profiled source columns, the domain's registered
target models and its MODEL_DEFINITION / NAMING_STANDARD / GLOSSARY / MAPPING_PATTERN knowledge, and the modeling
skills (AI-MODEL-GENERATION, SEMANTIC-COLUMN-CLUSTERING). Conventions (audit columns, surrogate keys, naming, SCD)
are the developer's choice per design: GDP, the company standard, custom toggles, or none at all.

Every save is a new row in MODELING.MODEL_DESIGN; approval registers the model's entities as targets (columns set
exactly to the design), writes MODEL_SPEC, points the run at the primary entity and stores MODEL_DEFINITION knowledge.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

PRESETS = ("GDP", "COMPANY", "CUSTOM", "NONE")
DECISIONS = ("REUSE_EXISTING", "EXTEND_EXISTING", "NEW")
KINDS = ("DIMENSION", "FACT", "BRIDGE", "REFERENCE", "TABLE")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,254}$")
TYPE = re.compile(
    r"^(NUMBER(\(\d{1,2}(,\s*\d{1,2})?\))?|DECIMAL(\(\d{1,2}(,\s*\d{1,2})?\))?|NUMERIC|INT|INTEGER|BIGINT|SMALLINT|"
    r"FLOAT|DOUBLE|REAL|BOOLEAN|DATE|TIME|TIMESTAMP(_NTZ|_LTZ|_TZ)?(\(\d\))?|VARCHAR(\(\d{1,8}\))?|STRING|TEXT|"
    r"CHAR(\(\d{1,4}\))?|BINARY|VARIANT|OBJECT|ARRAY|GEOGRAPHY)$", re.IGNORECASE)


# ---------------------------------------------------------------- conventions

def conventions(preset: str, custom: Optional[Dict[str, Any]] = None, company: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The rules a design follows. GDP mirrors the GDP standard, COMPANY the organisation's generic standard,
    CUSTOM whatever the developer toggled, NONE nothing at all (free modeling)."""
    preset = (preset or "NONE").upper()
    preset = preset if preset in PRESETS else "NONE"
    if preset == "NONE":
        return {"preset": "NONE", "audit_columns": [], "surrogate_key": "none", "naming_case": "as_is",
                "table_prefix": "", "scd_type": None, "soft_delete": False}
    if preset == "GDP":
        return {"preset": "GDP", "audit_columns": ["GDP_INSERTED_TS", "GDP_UPDATED_TS", "GDP_IS_ACTIVE", "GDP_ROW_HASH"],
                "surrogate_key": "sequence", "key_pattern": "{TABLE}_HKEY", "naming_case": "upper",
                "table_prefix": "", "column_prefix": "", "scd_type": 1, "soft_delete": True}
    if preset == "COMPANY":
        c = company or {}
        return {"preset": "COMPANY", "audit_columns": list(c.get("audit_columns") or ["LOAD_TS", "RECORD_SOURCE"]),
                "surrogate_key": c.get("key_strategy") or "hash", "key_pattern": c.get("key_pattern") or "{TABLE}_SK",
                "naming_case": "upper", "table_prefix": c.get("table_prefix") or "",
                "scd_type": c.get("scd_type") if c.get("scd_type") in (1, 2) else 2, "soft_delete": False}
    c = custom or {}
    key = str(c.get("surrogate_key") or "none").lower()
    case = str(c.get("naming_case") or "upper").lower()
    scd = c.get("scd_type")
    return {"preset": "CUSTOM",
            "audit_columns": [str(a).strip() for a in (c.get("audit_columns") or []) if str(a).strip()][:12],
            "surrogate_key": key if key in ("none", "sequence", "hash") else "none",
            "key_pattern": str(c.get("key_pattern") or "{TABLE}_SK")[:60],
            "naming_case": case if case in ("upper", "lower", "as_is") else "upper",
            "table_prefix": str(c.get("table_prefix") or "")[:20],
            "scd_type": scd if scd in (1, 2) else None, "soft_delete": bool(c.get("soft_delete"))}


def describe_conventions(c: Dict[str, Any]) -> str:
    if c.get("preset") == "NONE":
        return "No conventions: model freely from the source and domain context. Do not add audit or surrogate columns."
    parts = [f"Convention preset: {c['preset']}."]
    if c.get("audit_columns"):
        parts.append(f"Every entity ends with these audit columns: {', '.join(c['audit_columns'])} (derived, no source).")
    if c.get("surrogate_key") != "none":
        parts.append(f"Each entity has a {c['surrogate_key']} surrogate key named {c.get('key_pattern', '{TABLE}_SK')} "
                     "(derived, is_pk true; business keys stay as attributes).")
    else:
        parts.append("No surrogate keys; the business key is the primary key.")
    if c.get("naming_case") in ("upper", "lower"):
        parts.append(f"Names are {c['naming_case']} case with underscores.")
    if c.get("table_prefix"):
        parts.append(f"Table names start with {c['table_prefix']}.")
    if c.get("scd_type"):
        parts.append(f"History: SCD type {c['scd_type']}.")
    if c.get("soft_delete"):
        parts.append("Rows are soft-deleted through an active flag, never removed.")
    return " ".join(parts)


# ---------------------------------------------------------------- AI output contract

DESIGN_SCHEMA = {
    "type": "object",
    "required": ["decision", "decision_target", "reasons", "entities", "evidence"],
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": list(DECISIONS)},
        "decision_target": {"type": "string", "description": "Registered DB.SCHEMA.TABLE reused or extended; empty for NEW"},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["entity_name", "kind", "purpose", "grain", "business_keys", "attributes"],
                "additionalProperties": False,
                "properties": {
                    "entity_name": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "purpose": {"type": "string"},
                    "grain": {"type": "string"},
                    "business_keys": {"type": "array", "items": {"type": "string"}},
                    "attributes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "datatype", "nullable", "is_pk", "source_columns", "derived", "rationale"],
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"}, "datatype": {"type": "string"},
                                "nullable": {"type": "boolean"}, "is_pk": {"type": "boolean"},
                                "source_columns": {"type": "array", "items": {"type": "string"},
                                                   "description": "TABLE.COLUMN of the profiled sources"},
                                "derived": {"type": "boolean", "description": "true when no source column feeds it"},
                                "transformation_hint": {"type": "string"},
                                "rationale": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
        "evidence": {"type": "array", "items": {"type": "string"},
                     "description": "Keys of the context items used, e.g. MODEL:DB.SCH.T, KNOWLEDGE:<title>, PROFILE:T.C"},
    },
}


def build_prompt(ctx: Dict[str, Any], conv: Dict[str, Any], instructions: str = "",
                 previous: Optional[Dict[str, Any]] = None) -> str:
    sources = "\n".join(
        f"- {t}: " + ", ".join(
            f"{c['column']} {c['type']}" + (f" [{c['semantic']}]" if c.get("semantic") else "")
            + (" key" if c.get("key") else "") + (" fk" if c.get("fk") else "") + (" pii" if c.get("pii") else "")
            + (f" nulls={c['nulls']}%" if c.get("nulls") not in (None, "") else "")
            for c in cols)
        for t, cols in ctx["sources"].items())
    models = "\n".join(
        f"- MODEL:{m['fqn']} ({m.get('description') or 'registered'}): " + ", ".join(m["columns"][:60])
        for m in ctx["models"]) or "- none registered in this domain"
    knowledge = "\n".join(f"- KNOWLEDGE:{k}" for k in ctx["knowledge"]) or "- none"
    prior = ""
    if previous:
        prior = ("\n\nCurrent design (revise it; keep what the developer changed unless the request says otherwise):\n"
                 + json.dumps({"decision": previous.get("decision"), "entities": previous.get("entities")})[:6000])
    return (
        "You are a data modeler. Design the target model for the source tables below, using ONLY the context given. "
        "Never invent source columns: every non-derived attribute lists the TABLE.COLUMN it comes from. Prefer reusing "
        "or extending a registered model of this domain when the source clearly belongs to it (decision REUSE_EXISTING "
        "or EXTEND_EXISTING with decision_target), otherwise NEW. Keep the model small and practical: usually one "
        "entity per business concept at a clear grain. Use Snowflake datatypes. List in evidence the MODEL:, "
        "KNOWLEDGE: and PROFILE: keys you relied on.\n\n"
        f"Domain: {ctx.get('domain') or 'GENERAL'}\n"
        f"Conventions: {describe_conventions(conv)}\n\n"
        f"Modeling skills:\n{ctx.get('skills') or '-'}\n\n"
        f"Profiled source tables (TABLE: column type [semantic] flags):\n{sources}\n\n"
        f"Registered models in this domain:\n{models}\n\n"
        f"Domain knowledge:\n{knowledge}"
        + prior
        + (f"\n\nDeveloper request: {instructions.strip()[:2000]}" if (instructions or "").strip() else "")
    )


# ---------------------------------------------------------------- normalize and validate (pure)

def _case(name: str, conv: Dict[str, Any]) -> str:
    name = re.sub(r"[^A-Za-z0-9_$]+", "_", str(name or "").strip()).strip("_") or "COLUMN"
    if conv.get("naming_case") == "upper":
        return name.upper()
    if conv.get("naming_case") == "lower":
        return name.lower()
    return name


def normalize(raw: Dict[str, Any], conv: Dict[str, Any]) -> Dict[str, Any]:
    """Tidy an AI or edited design: names cased per conventions, types upper, empty lists for missing fields,
    duplicate attributes dropped. Never adds or removes meaning."""
    out = {"decision": raw.get("decision") if raw.get("decision") in DECISIONS else "NEW",
           "decision_target": str(raw.get("decision_target") or "").strip().upper(),
           "reasons": [str(r) for r in raw.get("reasons") or []][:10],
           "evidence": [str(e) for e in raw.get("evidence") or []][:40],
           "target_database": str(raw.get("target_database") or "").strip(),
           "target_schema": str(raw.get("target_schema") or "").strip(),
           "primary_entity": str(raw.get("primary_entity") or "").strip(),
           "entities": []}
    for e in raw.get("entities") or []:
        name = _case(e.get("entity_name"), conv)
        seen: set = set()
        attrs = []
        for a in e.get("attributes") or []:
            an = _case(a.get("name"), conv)
            if an.upper() in seen:
                continue
            seen.add(an.upper())
            attrs.append({"name": an, "datatype": str(a.get("datatype") or "VARCHAR").strip().upper(),
                          "nullable": bool(a.get("nullable", True)), "is_pk": bool(a.get("is_pk")),
                          "source_columns": [str(s).strip().upper() for s in a.get("source_columns") or [] if str(s).strip()],
                          "derived": bool(a.get("derived")) or not (a.get("source_columns") or []),
                          "transformation_hint": str(a.get("transformation_hint") or "")[:500],
                          "rationale": str(a.get("rationale") or "")[:500]})
        out["entities"].append({"entity_name": name, "kind": e.get("kind") if e.get("kind") in KINDS else "TABLE",
                                "purpose": str(e.get("purpose") or "")[:500], "grain": str(e.get("grain") or "")[:300],
                                "business_keys": [_case(k, conv) for k in e.get("business_keys") or []],
                                "attributes": attrs})
    if out["entities"] and out["primary_entity"].upper() not in {e["entity_name"].upper() for e in out["entities"]}:
        out["primary_entity"] = out["entities"][0]["entity_name"]
    return out


def validate(design: Dict[str, Any], sources: Dict[str, Dict[str, str]], conv: Dict[str, Any],
             registered: List[str]) -> List[Dict[str, str]]:
    """Grounding and convention problems, each {path, severity, message}. ERRORs block approval."""
    issues: List[Dict[str, str]] = []
    known = {f"{t}.{c}" for t, cols in sources.items() for c in cols}

    def add(path: str, message: str, severity: str = "ERROR") -> None:
        issues.append({"path": path, "severity": severity, "message": message})

    if not design.get("entities"):
        add("entities", "the design has no entities")
    if design.get("decision") in ("REUSE_EXISTING", "EXTEND_EXISTING"):
        if design.get("decision_target", "").upper() not in {r.upper() for r in registered}:
            add("decision", f"{design.get('decision_target') or 'the target'} is not a registered model of this domain")
    names = [e["entity_name"].upper() for e in design.get("entities", [])]
    for dup in {n for n in names if names.count(n) > 1}:
        add("entities", f"entity {dup} appears twice")
    for i, e in enumerate(design.get("entities", [])):
        base = f"entities[{i}]"
        if not NAME.match(e["entity_name"]):
            add(base, f"{e['entity_name']} is not a valid table name")
        if conv.get("table_prefix") and not e["entity_name"].upper().startswith(conv["table_prefix"].upper()):
            add(base, f"{e['entity_name']} should start with {conv['table_prefix']}", "WARN")
        if not e["attributes"]:
            add(base, f"{e['entity_name']} has no columns")
        attr_names = {a["name"].upper() for a in e["attributes"]}
        for k in e.get("business_keys") or []:
            if k.upper() not in attr_names:
                add(base, f"business key {k} is not a column of {e['entity_name']}")
        if not any(a["is_pk"] for a in e["attributes"]) and not e.get("business_keys"):
            add(base, f"{e['entity_name']} has no primary or business key", "WARN")
        for j, a in enumerate(e["attributes"]):
            path = f"{base}.attributes[{j}]"
            if not NAME.match(a["name"]):
                add(path, f"{a['name']} is not a valid column name")
            if not TYPE.match(a["datatype"]):
                add(path, f"{a['datatype']} is not a Snowflake type")
            missing = [s for s in a["source_columns"] if s not in known]
            if missing:
                add(path, f"{a['name']}: source {', '.join(missing)} is not a profiled column")
            if not a["source_columns"] and not a["derived"]:
                add(path, f"{a['name']} has no source column and is not marked derived")
        for audit in conv.get("audit_columns") or []:
            if audit.upper() not in attr_names:
                add(base, f"{e['entity_name']} is missing the audit column {audit}", "WARN")
    return issues


def diff(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Column-level changes between two designs, per entity."""
    def index(d):
        return {e["entity_name"].upper(): {a["name"].upper(): a for a in e["attributes"]} for e in d.get("entities", [])}

    a, b = index(old or {}), index(new or {})
    out = []
    for ent in sorted(set(a) | set(b)):
        if ent not in a:
            out.append({"entity": ent, "change": "ENTITY_ADDED", "columns": sorted(b[ent])})
            continue
        if ent not in b:
            out.append({"entity": ent, "change": "ENTITY_REMOVED", "columns": sorted(a[ent])})
            continue
        for col in sorted(set(a[ent]) | set(b[ent])):
            if col not in a[ent]:
                out.append({"entity": ent, "change": "ADDED", "column": col, "to": b[ent][col]["datatype"]})
            elif col not in b[ent]:
                out.append({"entity": ent, "change": "REMOVED", "column": col, "from": a[ent][col]["datatype"]})
            else:
                fields = [f for f in ("datatype", "nullable", "is_pk", "source_columns") if a[ent][col][f] != b[ent][col][f]]
                if fields:
                    out.append({"entity": ent, "change": "CHANGED", "column": col, "fields": fields,
                                "from": {f: a[ent][col][f] for f in fields}, "to": {f: b[ent][col][f] for f in fields}})
    return out


def baseline(columns: List[Dict[str, Any]], table: str, conv: Dict[str, Any]) -> Dict[str, Any]:
    """The registered 1:1 target as version 1, so the developer can start from it."""
    return normalize({"decision": "NEW", "reasons": ["Source columns as-is (registered when the run was created)."],
                      "entities": [{"entity_name": table, "kind": "TABLE", "purpose": "Source columns as-is",
                                    "grain": "", "business_keys": [c["name"] for c in columns if c.get("key")],
                                    "attributes": [{"name": c["name"], "datatype": c["type"], "nullable": True,
                                                    "is_pk": bool(c.get("key")), "source_columns": c.get("sources") or [],
                                                    "derived": not c.get("sources"), "rationale": c.get("comment") or ""}
                                                   for c in columns]}]}, {"naming_case": "as_is"})


# ---------------------------------------------------------------- Snowflake side

def _rows(session, sql: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
    from services.common.sql import rows

    return rows(session, sql, params)


def _run(session, run_id: str) -> Dict[str, Any]:
    found = _rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    assert found, "run not found"
    return found[0]


def context(session, run_id: str) -> Dict[str, Any]:
    """Everything the designer may use: profiled sources, the domain's registered models and knowledge, skills."""
    from services.knowledge.usage import use_stage

    run = _run(session, run_id)
    sources: Dict[str, List[Dict[str, Any]]] = {}
    for r in _rows(session, """SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, SEMANTIC_TYPE, NULL_PERCENTAGE,
                                      POTENTIAL_KEY_FLAG, POTENTIAL_FOREIGN_KEY_FLAG, PII_CLASSIFICATION
                                 FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = ? AND IS_CURRENT
                                ORDER BY TABLE_NAME, COLUMN_NAME""", [run_id]):
        sources.setdefault(str(r["TABLE_NAME"]).upper(), []).append({
            "column": str(r["COLUMN_NAME"]).upper(), "type": r["DATA_TYPE"], "semantic": r["SEMANTIC_TYPE"],
            "nulls": r["NULL_PERCENTAGE"], "key": bool(r["POTENTIAL_KEY_FLAG"]),
            "fk": bool(r["POTENTIAL_FOREIGN_KEY_FLAG"]), "pii": (r["PII_CLASSIFICATION"] or "NONE") != "NONE"})
    domain_id = run.get("DOMAIN_ID")
    domain = None
    models: List[Dict[str, Any]] = []
    knowledge: List[str] = []
    if domain_id:
        found = _rows(session, "SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [domain_id])
        domain = found[0]["DOMAIN_NAME"] if found else None
        for t in _rows(session, """SELECT TARGET_TABLE_ID, TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE, DESCRIPTION
                                     FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE DOMAIN_ID = ? AND ACTIVE_FLAG
                                    ORDER BY TARGET_TABLE LIMIT 40""", [domain_id]):
            cols = [f"{c['COLUMN_NAME']} {c['DATA_TYPE']}" for c in _rows(
                session, """SELECT COLUMN_NAME, DATA_TYPE FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY
                             WHERE TARGET_TABLE_ID = ? ORDER BY ORDINAL_POSITION""", [t["TARGET_TABLE_ID"]])]
            models.append({"fqn": f"{t['TARGET_DATABASE']}.{t['TARGET_SCHEMA']}.{t['TARGET_TABLE']}".upper(),
                           "description": t["DESCRIPTION"], "columns": cols})
        picked = _rows(
            session, """SELECT KNOWLEDGE_ID, KNOWLEDGE_TYPE, TITLE, CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                         WHERE DOMAIN_ID = ? AND IS_CURRENT AND COALESCE(STATUS, 'ACTIVE') = 'ACTIVE'
                           AND KNOWLEDGE_TYPE IN ('MODEL_DEFINITION', 'NAMING_STANDARD', 'GLOSSARY', 'MAPPING_PATTERN',
                                                  'BUSINESS_RULE')
                         ORDER BY IFF(KNOWLEDGE_TYPE = 'MODEL_DEFINITION', 0, 1), UPDATED_AT DESC NULLS LAST
                         LIMIT 40""", [domain_id])
        knowledge = [f"[{k['KNOWLEDGE_TYPE']}] {k['TITLE']}: {str(k['CONTENT'] or '')[:300]}" for k in picked]
        from services.knowledge.writer import record_usage

        record_usage(session, run_id, "MODELING", [k["KNOWLEDGE_ID"] for k in picked])
    try:
        skills = use_stage(session, "MODELING", run_id=run_id, excerpt=1500)
    except Exception:
        skills = ""
    return {"run": run, "domain_id": domain_id, "domain": domain, "sources": sources, "models": models,
            "knowledge": knowledge, "skills": skills}


def _company(session) -> Dict[str, Any]:
    from services.common.standard import conventions_for

    def query(sql, params):
        return _rows(session, sql, params)

    return conventions_for(query, "GENERIC")


def _source_index(ctx: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    return {t: {c["column"]: c["type"] for c in cols} for t, cols in ctx["sources"].items()}


def _versions(session, run_id: str) -> List[Dict[str, Any]]:
    from services.common.sql import variant

    return [{"design_id": r["DESIGN_ID"], "version": int(r["VERSION"]), "status": r["STATUS"], "origin": r["ORIGIN"],
             "design": variant(r["DESIGN_JSON"]) or {}, "conventions": variant(r["CONVENTIONS"]) or {},
             "issues": variant(r["ISSUES"]) or [], "instructions": r["INSTRUCTIONS"], "model": r["MODEL"],
             "note": r["NOTE"], "created_by": r["CREATED_BY"],
             "created_at": str(r["CREATED_AT"])[:19] if r.get("CREATED_AT") else None,
             "approved_by": r["APPROVED_BY"]}
            for r in _rows(session, "SELECT * FROM MODELING.MODEL_DESIGN WHERE RUN_ID = ? ORDER BY VERSION DESC", [run_id])]


def _store(session, run_id: str, design: Dict[str, Any], conv: Dict[str, Any], issues: List[Dict[str, str]],
           origin: str, instructions: str = "", model: str = "", note: str = "") -> Dict[str, Any]:
    from services.common.sql import insert_rows

    version = int((_rows(session, "SELECT COALESCE(MAX(VERSION), 0) AS V FROM MODELING.MODEL_DESIGN WHERE RUN_ID = ?",
                         [run_id])[0]["V"]) or 0) + 1
    design_id = str(uuid.uuid4())
    insert_rows(session, "MODELING.MODEL_DESIGN",
                ["DESIGN_ID", "RUN_ID", "VERSION", "STATUS", "ORIGIN", "DECISION", "DESIGN_JSON", "CONVENTIONS",
                 "ISSUES", "INSTRUCTIONS", "MODEL", "NOTE"],
                ["?", "?", "?::NUMBER", "'DRAFT'", "?", "?", "PARSE_JSON(?)", "PARSE_JSON(?)", "PARSE_JSON(?)",
                 "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')"],
                [[design_id, run_id, version, origin, design.get("decision"), json.dumps(design), json.dumps(conv),
                  json.dumps(issues), (instructions or "")[:4000], model, (note or "")[:2000]]])
    return {"design_id": design_id, "version": version}


def _registered(ctx: Dict[str, Any]) -> List[str]:
    return [m["fqn"] for m in ctx["models"]]


def get_design(session, run_id: str) -> Dict[str, Any]:
    """Every version (newest first); the registered 1:1 target becomes version 1 the first time it is asked for."""
    versions = _versions(session, run_id)
    if not versions:
        run = _run(session, run_id)
        model = (run.get("TARGET_MODEL") or "").strip()
        if model.count(".") == 2:
            db, schema, table = model.split(".")
            target = _rows(session, """SELECT TARGET_TABLE_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE ACTIVE_FLAG
                                         AND UPPER(TARGET_DATABASE) = UPPER(?) AND UPPER(TARGET_SCHEMA) = UPPER(?)
                                         AND UPPER(TARGET_TABLE) = UPPER(?) LIMIT 1""", [db, schema, table])
            if target:
                cols = [{"name": c["COLUMN_NAME"], "type": c["DATA_TYPE"], "key": bool(c["IS_BUSINESS_KEY"]),
                         "comment": c["BUSINESS_DEFINITION"],
                         "sources": [m.group(1).upper() + "." + m.group(2).upper()] if (
                             m := re.match(r"^From ([A-Za-z0-9_$]+)\.([A-Za-z0-9_$]+)$", str(c["BUSINESS_DEFINITION"] or ""))) else []}
                        for c in _rows(session, """SELECT COLUMN_NAME, DATA_TYPE, IS_BUSINESS_KEY, BUSINESS_DEFINITION
                                                     FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY WHERE TARGET_TABLE_ID = ?
                                                    ORDER BY ORDINAL_POSITION""", [target[0]["TARGET_TABLE_ID"]])]
                if cols:
                    design = baseline(cols, table, {"naming_case": "as_is"})
                    design.update(target_database=db, target_schema=schema, primary_entity=table)
                    conv = conventions("NONE")
                    _store(session, run_id, design, conv, [], "REGISTERED")
                    versions = _versions(session, run_id)
    ctx_run = _run(session, run_id)
    return {"run_id": run_id, "target_model": ctx_run.get("TARGET_MODEL"), "standard": ctx_run.get("MODELING_STANDARD"),
            "versions": versions, "current": versions[0] if versions else None,
            "approved": next((v for v in versions if v["status"] == "APPROVED"), None)}


def design_model(session, run_id: str, payload_json: str) -> Dict[str, Any]:
    """Ask the model for a design grounded in the run's context and store it as a new draft version."""
    from services.common.audit import record_cost, tool_call
    from services.common.llm import complete_json

    payload = json.loads(payload_json or "{}")
    ctx = context(session, run_id)
    assert ctx["sources"], "profile the run's source tables before designing the model"
    conv = conventions(payload.get("preset") or ("GDP" if str(ctx["run"].get("MODELING_STANDARD") or "").upper() == "GDP" else "NONE"),
                       payload.get("custom"), _company(session) if (payload.get("preset") or "").upper() == "COMPANY" else None)
    previous = None
    if payload.get("base_version"):
        previous = next((v["design"] for v in _versions(session, run_id) if v["version"] == int(payload["base_version"])), None)
    started = time.time()
    with tool_call(session, run_id, "model_design", {"preset": conv["preset"], "instructions": str(payload.get("instructions") or "")[:300]}) as call:
        prompt = build_prompt(ctx, conv, payload.get("instructions") or "", previous)
        raw, usage, model = complete_json(session, prompt, DESIGN_SCHEMA, max_tokens=8000, stage="MODELING")
        record_cost(session, run_id, "MODELING", model, usage, int((time.time() - started) * 1000), tool_calls=1)
        design = normalize(raw, conv)
        current = get_design(session, run_id).get("current")
        location = (current or {}).get("design") or {}
        design.update(target_database=payload.get("target_database") or location.get("target_database") or "",
                      target_schema=payload.get("target_schema") or location.get("target_schema") or "")
        issues = validate(design, _source_index(ctx), conv, _registered(ctx))
        call.summary = f"{len(design['entities'])} entities, {sum(1 for i in issues if i['severity'] == 'ERROR')} errors"
    stored = _store(session, run_id, design, conv, issues, "AI", payload.get("instructions") or "", model)
    return {**stored, "design": design, "conventions": conv, "issues": issues, "model": model,
            "grounding": {"source_tables": len(ctx["sources"]), "models": len(ctx["models"]),
                          "knowledge": len(ctx["knowledge"]), "skills": bool(ctx["skills"])}}


def save_design(session, run_id: str, payload_json: str) -> Dict[str, Any]:
    """A developer's edit becomes a new draft version, validated the same way."""
    payload = json.loads(payload_json or "{}")
    ctx = context(session, run_id)
    raw_conv = payload.get("conventions") or {}
    conv = conventions(raw_conv.get("preset") or "NONE", raw_conv,
                       _company(session) if (raw_conv.get("preset") or "").upper() == "COMPANY" else None)
    design = normalize(payload.get("design") or {}, conv)
    issues = validate(design, _source_index(ctx), conv, _registered(ctx))
    stored = _store(session, run_id, design, conv, issues, "USER", note=payload.get("note") or "")
    return {**stored, "design": design, "conventions": conv, "issues": issues}


def validate_design(session, run_id: str, payload_json: str) -> Dict[str, Any]:
    """Check a design (e.g. after switching conventions) without saving it."""
    payload = json.loads(payload_json or "{}")
    ctx = context(session, run_id)
    raw_conv = payload.get("conventions") or {}
    conv = conventions(raw_conv.get("preset") or "NONE", raw_conv,
                       _company(session) if (raw_conv.get("preset") or "").upper() == "COMPANY" else None)
    design = normalize(payload.get("design") or {}, conv)
    return {"design": design, "conventions": conv, "issues": validate(design, _source_index(ctx), conv, _registered(ctx))}


def spec_for(entity: Dict[str, Any], design: Dict[str, Any], conv: Dict[str, Any]) -> Dict[str, Any]:
    """MODEL_SPEC written on the registered target: what later stages (STTM, dbt, DQ, QA) read."""
    keys = [a["name"] for a in entity["attributes"] if a["is_pk"]] or entity.get("business_keys") or []
    return {"origin": "model_design", "kind": entity["kind"], "grain": entity.get("grain"),
            "business_keys": entity.get("business_keys") or [], "primary_key": keys,
            "conventions": conv, "decision": design.get("decision"),
            "derived_columns": [a["name"] for a in entity["attributes"] if a["derived"]],
            "lineage": {a["name"]: a["source_columns"] for a in entity["attributes"] if a["source_columns"]}}


def apply_design(session, run_id: str, version: int) -> Dict[str, Any]:
    """Approve a version: register every entity as a target (columns set exactly to the design), write MODEL_SPEC,
    point the run at the primary entity and keep the design as MODEL_DEFINITION knowledge. Owner's rights."""
    from services.common.sql import insert_rows, variant
    from services.knowledge.procedures import GENERAL_DOMAIN, ensure_domain

    found = _rows(session, "SELECT * FROM MODELING.MODEL_DESIGN WHERE RUN_ID = ? AND VERSION = ?", [run_id, int(version)])
    assert found, f"design version {version} not found"
    row = found[0]
    design, conv, issues = variant(row["DESIGN_JSON"]) or {}, variant(row["CONVENTIONS"]) or {}, variant(row["ISSUES"]) or []
    blocking = [i for i in issues if i.get("severity") == "ERROR"]
    assert not blocking, f"fix {len(blocking)} problem(s) before approving: {blocking[0]['message']}"
    run = _run(session, run_id)
    domain_id = run.get("DOMAIN_ID") or ensure_domain(session, GENERAL_DOMAIN)
    current = (run.get("TARGET_MODEL") or "").split(".")
    database = design.get("target_database") or (current[0] if len(current) == 3 else run.get("SOURCE_DATABASE"))
    schema = design.get("target_schema") or (current[1] if len(current) == 3 else "SILVER")
    assert database and schema, "set the target database and schema of the model"
    registered = []
    for entity in design.get("entities", []):
        table = entity["entity_name"]
        existing = _rows(session, """SELECT TARGET_TABLE_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE ACTIVE_FLAG
                                       AND UPPER(TARGET_DATABASE) = UPPER(?) AND UPPER(TARGET_SCHEMA) = UPPER(?)
                                       AND UPPER(TARGET_TABLE) = UPPER(?) LIMIT 1""", [database, schema, table])
        spec = spec_for(entity, design, conv)
        if existing:
            target_id = existing[0]["TARGET_TABLE_ID"]
            session.sql("""UPDATE KNOWLEDGE.TARGET_TABLE_REGISTRY SET MODEL_SPEC = PARSE_JSON(?), GRAIN = ?,
                                  BUSINESS_KEYS = PARSE_JSON(?), DESCRIPTION = ?, DOMAIN_ID = ?
                            WHERE TARGET_TABLE_ID = ?""",
                        params=[json.dumps(spec), entity.get("grain") or "", json.dumps(entity.get("business_keys") or []),
                                entity.get("purpose") or "", domain_id, target_id]).collect()
            session.sql("DELETE FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY WHERE TARGET_TABLE_ID = ?", params=[target_id]).collect()
        else:
            target_id = str(uuid.uuid4())
            insert_rows(session, "KNOWLEDGE.TARGET_TABLE_REGISTRY",
                        ["TARGET_TABLE_ID", "DOMAIN_ID", "TARGET_DATABASE", "TARGET_SCHEMA", "TARGET_TABLE", "TABLE_TYPE",
                         "GRAIN", "BUSINESS_KEYS", "SCD_TYPE", "DESCRIPTION", "VERSION", "ACTIVE_FLAG", "MODEL_SPEC"],
                        ["?", "?", "?", "?", "?", "'TABLE'", "?", "PARSE_JSON(?)", "?", "?", "1", "TRUE", "PARSE_JSON(?)"],
                        [[target_id, domain_id, database, schema, table, entity.get("grain") or "",
                          entity.get("business_keys") or [], str(conv.get("scd_type") or "1"), entity.get("purpose") or "",
                          spec]])
        insert_rows(session, "KNOWLEDGE.TARGET_COLUMN_REGISTRY",
                    ["TARGET_COLUMN_ID", "TARGET_TABLE_ID", "COLUMN_NAME", "DATA_TYPE", "ORDINAL_POSITION", "NULLABLE",
                     "BUSINESS_DEFINITION", "SEMANTIC_TYPE", "IS_BUSINESS_KEY", "IS_PII", "ACCEPTED_VALUES", "VERSION"],
                    ["?", "?", "?", "?", "?::NUMBER", "?::BOOLEAN", "NULLIF(?, '')", "NULLIF(?, '')", "?::BOOLEAN",
                     "FALSE", "PARSE_JSON(?)", "1"],
                    [[str(uuid.uuid4()), target_id, a["name"], a["datatype"], n + 1, a["nullable"],
                      a["rationale"] or ("From " + ", ".join(a["source_columns"]) if a["source_columns"] else ""),
                      "SURROGATE_KEY" if a["derived"] and a["is_pk"] else ("AUDIT_TIMESTAMP" if a["derived"] and a["name"].upper() in
                                                                           {c.upper() for c in conv.get("audit_columns") or []} else ""),
                      a["name"].upper() in {k.upper() for k in entity.get("business_keys") or []}, []]
                     for n, a in enumerate(entity["attributes"])])
        registered.append({"entity": table, "fqn": f"{database}.{schema}.{table}", "target_table_id": target_id})
    primary = next((r for r in registered if r["entity"].upper() == str(design.get("primary_entity") or "").upper()),
                   registered[0] if registered else None)
    assert primary, "the design has no entities"
    session.sql("""UPDATE MODELING.MODEL_DESIGN SET STATUS = 'SUPERSEDED' WHERE RUN_ID = ? AND STATUS = 'APPROVED'""",
                params=[run_id]).collect()
    session.sql("""UPDATE MODELING.MODEL_DESIGN SET STATUS = 'APPROVED', APPROVED_BY = CURRENT_USER(),
                          APPROVED_AT = CURRENT_TIMESTAMP() WHERE RUN_ID = ? AND VERSION = ?""",
                params=[run_id, int(version)]).collect()
    session.sql("UPDATE CORE.WORKFLOW_RUN SET TARGET_MODEL = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE RUN_ID = ?",
                params=[primary["fqn"], run_id]).collect()
    _remember(session, domain_id, run_id, design, conv, registered, int(version))
    return {"version": int(version), "target_model": primary["fqn"], "registered": registered}


def _remember(session, domain_id: str, run_id: str, design: Dict[str, Any], conv: Dict[str, Any],
              registered: List[Dict[str, Any]], version: int) -> None:
    """Each approved entity is MODEL_DEFINITION knowledge, so later runs reuse it and the copilot can cite it."""
    from services.common.suggestion_stages import _knowledge

    for entity, reg in zip(design.get("entities", []), registered):
        try:
            user_tags = [r["TAG"] for r in _rows(session, """SELECT TAG FROM CORE.TAG_ASSIGNMENT
                                                              WHERE ENTITY_TYPE = 'MODEL' AND ENTITY_KEY = ?""",
                                                  [reg["fqn"].upper()])]
        except Exception:
            user_tags = []
        content = {"target_table": entity["entity_name"].upper(), "fqn": reg["fqn"], "kind": entity["kind"],
                   "purpose": entity.get("purpose"), "grain": entity.get("grain"),
                   "business_keys": entity.get("business_keys"), "conventions": conv, "decision": design.get("decision"),
                   "columns": [{k: a[k] for k in ("name", "datatype", "nullable", "is_pk", "source_columns", "derived")}
                               for a in entity["attributes"]],
                   "run_id": run_id, "design_version": version}
        try:
            _knowledge(session, domain_id, "MODEL_DEFINITION",
                       f"Model {entity['entity_name']}: {entity.get('purpose') or entity['kind'].lower()}",
                       content, f"model.{reg['fqn'].upper()}", ["MODEL_DESIGN", conv.get("preset") or "NONE", *user_tags],
                       "MODELING", run_id)
        except Exception:
            continue
