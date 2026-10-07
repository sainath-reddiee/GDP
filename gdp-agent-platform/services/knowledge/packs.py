"""Domain knowledge packs as data: the same rows whether a pack is seeded at deploy or imported from the UI.

A pack is JSON: {"domain": {...}, "targets": [{table, columns, ...}], "knowledge": [{key, type, ...}]}. Adding a
company's domain needs a pack, not a code change.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.common.standard import GENERIC, normalize_standard, technical_semantic

GDP_DOMAIN_ID = "00000000-0000-4000-a000-000000000001"
GDP_TABLE_ID = "00000000-0000-4000-a000-000000000002"


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256((prefix + "|" + "|".join(parts)).encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32]))



def domain_id(name: str) -> str:
    return GDP_DOMAIN_ID if name == "GDP" else _stable_id("domain", name)


LiveColumns = Dict[Tuple[str, str], List[Dict[str, Any]]]


def merge_columns(contract: List[Dict[str, Any]], live: Optional[List[Dict[str, Any]]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Live silver columns win for names, types, nullability and order; the contract adds definitions and keys."""
    if not live:
        return contract, {"live": False}
    by_name = {c["name"].upper(): c for c in contract}
    merged = []
    for col in live:
        known = by_name.get(col["name"].upper(), {})
        merged.append({**known, "name": col["name"], "type": col["type"], "nullable": col["nullable"]})
    live_names = {c["name"].upper() for c in live}
    drift = {"live": True,
             "missing_in_silver": sorted(n for n in by_name if n not in live_names),
             "not_in_contract": sorted(n for n in live_names if n not in by_name) if contract else []}
    return merged, drift


def _dumps(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def pack_rows(pack: Dict[str, Any], database: str, live: Optional[LiveColumns] = None) -> Dict[str, Any]:
    """Registry rows for one domain pack (shared by deploy seeding and UI import)."""
    live = live or {}
    domains, tables, columns, knowledge, drift = [], [], [], [], []
    meta = pack["domain"]
    name = meta["name"]
    did = domain_id(name)
    is_gdp = name == "GDP"
    config = {k: meta[k] for k in ("silver_database", "silver_schema", "contract", "signals", "source_systems",
                                   "origin") if k in meta}
    standard = normalize_standard(meta.get("standard")) or GENERIC
    config["standard"] = standard
    domains.append((did, name, meta["description"], meta["owner"], True, 1, config))
    target_db = database if is_gdp else meta.get("silver_database") or database
    for i, table in enumerate(pack["targets"]):
        tid = GDP_TABLE_ID if is_gdp and i == 0 else _stable_id("table", target_db, table["schema"], table["table"])
        cols, report = merge_columns(table.get("columns") or [], live.get((table["schema"], table["table"])))
        if not is_gdp:
            drift.append({"domain": name, "table": table["table"], "columns": len(cols), **report})
        tables.append((
            tid, did, target_db, table["schema"], table["table"], table.get("table_type"), table.get("grain"),
            # A target with no live silver table and no contract columns stays as knowledge only: mapping
            # cannot use it, so it is registered inactive until the silver table exists.
            table.get("business_keys") or [], str(table.get("scd_type") or "1"), table.get("description"), 1,
            bool(cols), table.get("model_spec"),
        ))
        for n, col in enumerate(cols, start=1):
            cid = (_stable_id("col", table["table"], col["name"]) if is_gdp
                   else _stable_id("col", table["schema"], table["table"], col["name"]))
            columns.append((
                cid, tid, col["name"], col.get("type") or "TEXT", n, bool(col.get("nullable")), col.get("definition"),
                col.get("semantic_type") or technical_semantic(
                    col["name"], standard, table["table"], hub_fk=(table.get("model_spec") or {}).get("hub_fk")),
                bool(col.get("business_key")), bool(col.get("pii")),
                col.get("accepted_values") or [], 1,
            ))
    for item in pack["knowledge"]:
        knowledge.append((
            _stable_id("k", item["key"]), did, item["type"], item["title"], item["content"],
            item.get("content_json"), [name], item["key"], "ACTIVE", 1, True,
        ))
    return {"domains": domains, "tables": tables, "columns": columns, "knowledge": knowledge, "drift": drift}


def merge(execute: Callable[..., Any], database: str, data: Dict[str, Any]) -> None:
    """Idempotent MERGE of pack rows; `execute(sql, params)` uses %s binds (connector cursor or API Db)."""
    for row in data["domains"]:
        execute(
            f"""MERGE INTO {database}.KNOWLEDGE.DOMAIN_REGISTRY t
                USING (SELECT %s AS DOMAIN_ID, %s AS DOMAIN_NAME, %s AS DESCRIPTION, %s AS OWNER,
                              %s AS ACTIVE_FLAG, %s AS VERSION, PARSE_JSON(%s) AS CONFIG) s
                   ON t.DOMAIN_ID = s.DOMAIN_ID
                WHEN MATCHED THEN UPDATE SET DOMAIN_NAME = s.DOMAIN_NAME, DESCRIPTION = s.DESCRIPTION,
                     OWNER = s.OWNER, ACTIVE_FLAG = s.ACTIVE_FLAG, VERSION = s.VERSION, CONFIG = s.CONFIG,
                     UPDATED_AT = CURRENT_TIMESTAMP()
                WHEN NOT MATCHED THEN INSERT (DOMAIN_ID, DOMAIN_NAME, DESCRIPTION, OWNER, ACTIVE_FLAG, VERSION, CONFIG)
                     VALUES (s.DOMAIN_ID, s.DOMAIN_NAME, s.DESCRIPTION, s.OWNER, s.ACTIVE_FLAG, s.VERSION, s.CONFIG)""",
            (*row[:6], json.dumps(row[6] or {})),
        )
    for row in data["tables"]:
        execute(
            f"""MERGE INTO {database}.KNOWLEDGE.TARGET_TABLE_REGISTRY t
                USING (SELECT %s AS TARGET_TABLE_ID, %s AS DOMAIN_ID, %s AS TARGET_DATABASE,
                              %s AS TARGET_SCHEMA, %s AS TARGET_TABLE, %s AS TABLE_TYPE, %s AS GRAIN,
                              PARSE_JSON(%s) AS BUSINESS_KEYS, %s AS SCD_TYPE, %s AS DESCRIPTION,
                              %s AS VERSION, %s AS ACTIVE_FLAG, PARSE_JSON(NULLIF(%s, '')) AS MODEL_SPEC) s
                   ON t.TARGET_TABLE_ID = s.TARGET_TABLE_ID
                WHEN MATCHED THEN UPDATE SET TARGET_DATABASE = s.TARGET_DATABASE, TARGET_SCHEMA = s.TARGET_SCHEMA,
                     TARGET_TABLE = s.TARGET_TABLE, TABLE_TYPE = s.TABLE_TYPE, GRAIN = s.GRAIN,
                     BUSINESS_KEYS = s.BUSINESS_KEYS, SCD_TYPE = s.SCD_TYPE, DESCRIPTION = s.DESCRIPTION,
                     VERSION = s.VERSION, ACTIVE_FLAG = s.ACTIVE_FLAG, MODEL_SPEC = s.MODEL_SPEC
                WHEN NOT MATCHED THEN INSERT (TARGET_TABLE_ID, DOMAIN_ID, TARGET_DATABASE, TARGET_SCHEMA,
                     TARGET_TABLE, TABLE_TYPE, GRAIN, BUSINESS_KEYS, SCD_TYPE, DESCRIPTION, VERSION, ACTIVE_FLAG,
                     MODEL_SPEC)
                     VALUES (s.TARGET_TABLE_ID, s.DOMAIN_ID, s.TARGET_DATABASE, s.TARGET_SCHEMA, s.TARGET_TABLE,
                             s.TABLE_TYPE, s.GRAIN, s.BUSINESS_KEYS, s.SCD_TYPE, s.DESCRIPTION, s.VERSION, s.ACTIVE_FLAG,
                             s.MODEL_SPEC)""",
            (row[0], row[1], row[2], row[3], row[4], row[5], row[6], _dumps(row[7]),
             row[8], row[9], row[10], row[11], _dumps(row[12])),
        )
    for row in data["columns"]:
        execute(
            f"""MERGE INTO {database}.KNOWLEDGE.TARGET_COLUMN_REGISTRY t
                USING (SELECT %s AS TARGET_COLUMN_ID, %s AS TARGET_TABLE_ID, %s AS COLUMN_NAME, %s AS DATA_TYPE,
                              %s AS ORDINAL_POSITION, %s AS NULLABLE, %s AS BUSINESS_DEFINITION,
                              %s AS SEMANTIC_TYPE, %s AS IS_BUSINESS_KEY, %s AS IS_PII,
                              PARSE_JSON(%s) AS ACCEPTED_VALUES, %s AS VERSION) s
                   ON t.TARGET_COLUMN_ID = s.TARGET_COLUMN_ID
                WHEN MATCHED THEN UPDATE SET COLUMN_NAME = s.COLUMN_NAME, DATA_TYPE = s.DATA_TYPE,
                     ORDINAL_POSITION = s.ORDINAL_POSITION, NULLABLE = s.NULLABLE,
                     BUSINESS_DEFINITION = s.BUSINESS_DEFINITION, SEMANTIC_TYPE = s.SEMANTIC_TYPE,
                     IS_BUSINESS_KEY = s.IS_BUSINESS_KEY, IS_PII = s.IS_PII,
                     ACCEPTED_VALUES = s.ACCEPTED_VALUES, VERSION = s.VERSION
                WHEN NOT MATCHED THEN INSERT (TARGET_COLUMN_ID, TARGET_TABLE_ID, COLUMN_NAME, DATA_TYPE,
                     ORDINAL_POSITION, NULLABLE, BUSINESS_DEFINITION, SEMANTIC_TYPE, IS_BUSINESS_KEY,
                     IS_PII, ACCEPTED_VALUES, VERSION)
                     VALUES (s.TARGET_COLUMN_ID, s.TARGET_TABLE_ID, s.COLUMN_NAME, s.DATA_TYPE,
                             s.ORDINAL_POSITION, s.NULLABLE, s.BUSINESS_DEFINITION, s.SEMANTIC_TYPE,
                             s.IS_BUSINESS_KEY, s.IS_PII, s.ACCEPTED_VALUES, s.VERSION)""",
            (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9],
             _dumps(row[10]), row[11]),
        )
    for row in data["knowledge"]:
        execute(
            f"""MERGE INTO {database}.KNOWLEDGE.DOMAIN_KNOWLEDGE t
                USING (SELECT %s AS KNOWLEDGE_ID, %s AS DOMAIN_ID, %s AS KNOWLEDGE_TYPE, %s AS TITLE,
                              %s AS CONTENT, PARSE_JSON(NULLIF(%s, '')) AS CONTENT_JSON,
                              PARSE_JSON(%s) AS TAGS, %s AS SOURCE_REFERENCE, %s AS STATUS,
                              %s AS VERSION, %s AS IS_CURRENT, 'SEED' AS CREATED_BY) s
                   ON t.KNOWLEDGE_ID = s.KNOWLEDGE_ID
                WHEN MATCHED THEN UPDATE SET TITLE = s.TITLE, CONTENT = s.CONTENT, CONTENT_JSON = s.CONTENT_JSON,
                     TAGS = s.TAGS, STATUS = s.STATUS, VERSION = s.VERSION, IS_CURRENT = s.IS_CURRENT,
                     UPDATED_AT = CURRENT_TIMESTAMP()
                WHEN NOT MATCHED THEN INSERT (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT,
                     CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY)
                     VALUES (s.KNOWLEDGE_ID, s.DOMAIN_ID, s.KNOWLEDGE_TYPE, s.TITLE, s.CONTENT,
                             s.CONTENT_JSON, s.TAGS, s.SOURCE_REFERENCE, s.STATUS, s.VERSION,
                             s.IS_CURRENT, s.CREATED_BY)""",
            (row[0], row[1], row[2], row[3], row[4], _dumps(row[5]), _dumps(row[6]),
             row[7], row[8], row[9], row[10]),
        )


# ---------------------------------------------------------------- import, export, AI draft

PROTECTED = {"GDP"}  # the platform's own pack is maintained in the repository, never replaced from the UI


def prepare(pack: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """A pack ready to register: defaults filled, knowledge keys namespaced by domain, content repaired.
    Returns (pack, problems); nothing is registered while problems remain."""
    from services.knowledge.validate import normalize_content, validate_pack

    pack = json.loads(json.dumps(pack or {}))
    meta = pack.setdefault("domain", {})
    name = str(meta.get("name") or "").strip().upper()
    meta["name"] = name
    meta.setdefault("description", f"{name} domain")
    meta.setdefault("owner", "Imported")
    meta["standard"] = normalize_standard(meta.get("standard")) or GENERIC
    meta["origin"] = "imported"  # added through the UI: deletable, never re-seeded by a deploy
    pack.setdefault("targets", [])
    pack.setdefault("knowledge", [])
    problems: List[str] = []
    if name in PROTECTED:
        problems.append(f"{name} is maintained in the repository and cannot be replaced by an import")
    for t in pack["targets"]:
        t["table"] = str(t.get("table") or "").strip()
        t.setdefault("schema", meta.get("silver_schema") or name)
        for c in t.get("columns") or []:
            c["name"] = str(c.get("name") or "").strip()
    prefix = name.lower() + "."
    for i, item in enumerate(pack["knowledge"]):
        key = str(item.get("key") or f"item{i}")
        item["key"] = key if key.lower().startswith(prefix) else prefix + key  # keys are global ids
        item.setdefault("title", key)
        item.setdefault("content", json.dumps(item.get("content_json") or {})[:4000])
        if item.get("content_json") is not None:
            repaired = normalize_content(item.get("type"), item["content_json"])
            if repaired is None and item.get("type") in ("GLOSSARY", "BUSINESS_RULE", "TRANSFORMATION_RULE",
                                                          "MAPPING_PATTERN"):
                problems.append(f"{item['key']}: content_json is not usable for {item.get('type')}")
            elif repaired is not None:
                item["content_json"] = repaired
    return pack, validate_pack(pack) + problems  # validated after repair: fixable shapes are not problems


def import_pack(execute: Callable[..., Any], database: str, pack: Dict[str, Any]) -> Dict[str, Any]:
    pack, problems = prepare(pack)
    assert not problems, "PACK_INVALID: " + "; ".join(problems[:12])
    data = pack_rows(pack, database)
    merge(execute, database, data)
    return {"domain_id": domain_id(pack["domain"]["name"]), "domain_name": pack["domain"]["name"],
            "targets": len(data["tables"]), "columns": len(data["columns"]), "knowledge": len(data["knowledge"]),
            "inactive_targets": [t[4] for t in data["tables"] if not t[11]]}


def export_pack(query: Callable[..., List[Dict[str, Any]]], domain: str) -> Dict[str, Any]:
    """A registered domain back as pack JSON (to review, edit and re-import). `query` uses %s binds."""
    def low(r):
        return {str(k).lower(): v for k, v in r.items()}

    def js(v):
        return json.loads(v) if isinstance(v, str) and v[:1] in "[{" else v

    found = [low(r) for r in query("SELECT * FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain,))]
    assert found, "domain not found"
    d = found[0]
    config = js(d.get("config")) or {}
    pack: Dict[str, Any] = {"domain": {"name": d["domain_name"], "description": d.get("description") or "",
                                       "owner": d.get("owner") or "", **config}, "targets": [], "knowledge": []}
    tables = [low(r) for r in query("SELECT * FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE DOMAIN_ID = %s "
                                    "ORDER BY TARGET_TABLE", (domain,))]
    for t in tables:
        cols = [low(r) for r in query("SELECT * FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY WHERE TARGET_TABLE_ID = %s "
                                      "ORDER BY ORDINAL_POSITION", (t["target_table_id"],))]
        pack["targets"].append({
            "schema": t["target_schema"], "table": t["target_table"], "table_type": t.get("table_type"),
            "grain": t.get("grain"), "business_keys": js(t.get("business_keys")) or [], "scd_type": t.get("scd_type"),
            "description": t.get("description"), "model_spec": js(t.get("model_spec")),
            "columns": [{"name": c["column_name"], "type": c.get("data_type"), "nullable": bool(c.get("nullable")),
                         "definition": c.get("business_definition"), "semantic_type": c.get("semantic_type"),
                         "business_key": bool(c.get("is_business_key")), "pii": bool(c.get("is_pii")),
                         "accepted_values": js(c.get("accepted_values")) or []} for c in cols]})
    for k in [low(r) for r in query("SELECT * FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE DOMAIN_ID = %s AND IS_CURRENT "
                                    "AND STATUS = 'ACTIVE' ORDER BY SOURCE_REFERENCE", (domain,))]:
        pack["knowledge"].append({"key": k.get("source_reference") or k["knowledge_id"], "type": k["knowledge_type"],
                                  "title": k.get("title"), "content": k.get("content"),
                                  "content_json": js(k.get("content_json"))})
    return pack


_TERMS = {"type": "array", "items": {"type": "object", "properties": {"term": {"type": "string"},
                                                                     "weight": {"type": "number"}},
                                     "required": ["term"]}}
DRAFT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"}, "description": {"type": "string"},
        "table_signals": _TERMS, "column_signals": _TERMS,
        "targets": {"type": "array", "items": {"type": "object", "properties": {
            "table": {"type": "string"}, "grain": {"type": "string"}, "description": {"type": "string"},
            "business_keys": {"type": "array", "items": {"type": "string"}},
            "columns": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "type": {"type": "string"}, "nullable": {"type": "boolean"},
                "definition": {"type": "string"}, "business_key": {"type": "boolean"}, "pii": {"type": "boolean"}},
                "required": ["name"]}}}, "required": ["table", "columns"]}},
        "rules": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string"}, "target_column": {"type": "string"}, "title": {"type": "string"},
            "content": {"type": "string"}, "synonyms": {"type": "array", "items": {"type": "string"}},
            "expression": {"type": "string"}}, "required": ["kind", "title", "content"]}},
    },
    "required": ["name", "targets"],
}

DRAFT_PROMPT = """You turn a data contract or model description (any format: markdown, a spreadsheet export, DDL,
prose) into a knowledge pack for a data onboarding platform. Extract only what the document states.
- name: one short upper-case domain name, for example ORDERS or CLAIMS.
- table_signals / column_signals: words that identify this domain in source table and column names, weight 1-3.
- targets: the target tables with their columns, Snowflake types, nullability, definitions, business keys and
  PII flags.
- rules: kind GLOSSARY (target_column plus synonyms other systems use), BUSINESS_RULE (target_column plus the rule
  text) or TRANSFORMATION_RULE (target_column plus a SQL expression using {col} for the source column).
Do not invent tables, columns or rules the document does not support.
DOCUMENT:
"""


def draft_from_answer(answer: Dict[str, Any], standard: Optional[str] = None) -> Dict[str, Any]:
    """Model answer -> pack JSON (pure; the reviewer edits it before import)."""
    name = str(answer.get("name") or "NEW_DOMAIN").strip().upper().replace(" ", "_")

    def weights(items):
        out = {}
        for i in items or []:
            if isinstance(i, dict) and str(i.get("term") or "").strip():
                try:
                    weight = float(i.get("weight") or 1)
                except (TypeError, ValueError):
                    weight = 1.0
                out[str(i["term"]).strip().upper()] = int(min(3, max(1, round(weight))))
        return out

    knowledge = []
    for i, r in enumerate(answer.get("rules") or []):
        if not isinstance(r, dict):
            continue
        kind = str(r.get("kind") or "").upper()
        col = str(r.get("target_column") or "").strip().upper()
        content_json: Dict[str, Any] = {"target_column": col} if col else {}
        if kind == "GLOSSARY":
            content_json["synonyms"] = [str(s) for s in r.get("synonyms") or []]
        elif kind == "TRANSFORMATION_RULE":
            content_json["expression"] = str(r.get("expression") or "")
        else:
            kind = "BUSINESS_RULE"
            content_json["rule"] = str(r.get("content") or "")
        knowledge.append({"key": f"{name.lower()}.{kind.lower()}.{col.lower() or i}", "type": kind,
                          "title": str(r.get("title") or col or kind), "content": str(r.get("content") or ""),
                          "content_json": content_json if col else None})
    targets = []
    for t in answer.get("targets") or []:
        if not isinstance(t, dict) or not str(t.get("table") or "").strip():
            continue
        targets.append({"schema": name, "table": str(t["table"]).strip().upper(), "grain": t.get("grain") or "",
                        "description": t.get("description") or "", "business_keys": t.get("business_keys") or [],
                        "scd_type": "1", "columns": [
                            {"name": str(c["name"]).strip().upper(), "type": c.get("type") or "TEXT",
                             "nullable": c.get("nullable", True), "definition": c.get("definition") or "",
                             "business_key": bool(c.get("business_key")), "pii": bool(c.get("pii"))}
                            for c in t.get("columns") or [] if isinstance(c, dict) and str(c.get("name") or "").strip()]})
    return {"domain": {"name": name, "description": answer.get("description") or f"{name} domain",
                       "owner": "Drafted from a contract", "standard": normalize_standard(standard) or GENERIC,
                       "signals": {"tables": weights(answer.get("table_signals")),
                                   "columns": weights(answer.get("column_signals"))}},
            "targets": targets, "knowledge": knowledge}


def draft_pack(session, text: str, standard: Optional[str] = None) -> Dict[str, Any]:
    """AI extraction of a pack from any contract document. Nothing is registered: the reviewer edits, then imports."""
    from services.common.llm import complete_json

    answer, usage, model = complete_json(session, DRAFT_PROMPT + text[:60000], DRAFT_SCHEMA, max_tokens=12000, stage="KNOWLEDGE")
    pack = draft_from_answer(answer, standard)
    _, problems = prepare(pack)
    return {"pack": pack, "problems": problems, "model": model, "usage": usage}
