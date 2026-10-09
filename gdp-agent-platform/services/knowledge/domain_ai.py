"""AI on the Domains page: review a domain's knowledge pack (accept/reject) and answer questions about it.

The review reuses the shared suggestion service (one cached call per pack version, decisions remembered, cost
recorded). Accepting an item edits the same registries a pack import writes, so the result is what editing the pack
JSON would give. Answers cite only items that exist in the pack shown to the model.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Dict, List, Tuple

from services.common.ai_suggest import Spec, record_decision, suggest
from services.common.sql import clip, rows, scalar, variant

KINDS = ("GLOSSARY", "TABLE_SIGNAL", "COLUMN_SIGNAL", "DEFINITION", "BUSINESS_RULE")
_STR = {"type": "string"}
_OPT = {"type": ["string", "null"]}

SPEC = Spec(
    "DOMAIN_PACK", 1,
    "You review the knowledge pack of one business domain used by a data onboarding platform to detect, map and "
    "model source data. Suggest additions that make detection and mapping more reliable:\n"
    "- GLOSSARY: synonyms other systems use for a target_column (value = comma-separated synonyms)\n"
    "- TABLE_SIGNAL / COLUMN_SIGNAL: a word that identifies this domain in source table / column names (value = word)\n"
    "- DEFINITION: a business definition for a target_table.target_column that has none (value = definition)\n"
    "- BUSINESS_RULE: a rule for a target_column the pack implies but does not state (value = rule text)\n"
    "Only suggest what the pack's own tables, columns and rules support; do not invent new tables or columns.",
    {"type": "object", "properties": {"kind": _STR, "target_table": _OPT, "target_column": _OPT, "value": _STR,
                                      "reason": _STR}, "required": ["kind", "value", "reason"]},
    lambda i: "|".join(str(i.get(k) or "").upper() for k in ("kind", "target_table", "target_column", "value")),
    ["kind", "value", "reason"])


def _query(session) -> Callable[[str, Any], List[Dict[str, Any]]]:
    return lambda sql, params=(): rows(session, sql.replace("%s", "?"), list(params or ()))


def _domain(session, domain_id: str) -> Dict[str, Any]:
    found = rows(session, "SELECT DOMAIN_ID, DOMAIN_NAME, CONFIG FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?",
                 [domain_id])
    assert found, "domain not found"
    return {**found[0], "CONFIG": variant(found[0]["CONFIG"]) or {}}


def pack_context(pack: Dict[str, Any], budget: int = 20000) -> Dict[str, Any]:
    """What the model sees: names, types, definitions, signals and knowledge titles/content (trimmed)."""
    meta = pack.get("domain") or {}
    ctx = {
        "domain": meta.get("name"), "description": meta.get("description"), "signals": meta.get("signals") or {},
        "targets": [{"table": t.get("table"), "grain": t.get("grain"),
                     "columns": [{"name": c.get("name"), "type": c.get("type"), "definition": c.get("definition"),
                                  "business_key": c.get("business_key")} for c in t.get("columns") or []]}
                    for t in pack.get("targets") or []],
        "knowledge": [{"key": k.get("key"), "type": k.get("type"), "title": k.get("title"),
                       "content": clip(k.get("content"), 300)} for k in pack.get("knowledge") or []],
    }
    while len(json.dumps(ctx, default=str)) > budget and ctx["knowledge"]:
        ctx["knowledge"] = ctx["knowledge"][: len(ctx["knowledge"]) // 2]
    return ctx


def review(session, domain_id: str, refresh: bool = False, cached_only: bool = False) -> Dict[str, Any]:
    from services.knowledge.packs import export_pack

    pack = export_pack(_query(session), domain_id)
    result = suggest(session, SPEC, f"domain.{domain_id}", pack_context(pack), None, refresh, cached_only)
    result["items"] = [i for i in result["items"] if str(i.get("kind") or "").upper() in KINDS]
    return {"stage": "DOMAIN_PACK", "scopes": [{"scope_key": f"domain.{domain_id}", **result}],
            "count": len(result["items"])}


def _knowledge_row(session, domain_id: str, kind: str, key: str, title: str, content: str,
                   content_json: Dict[str, Any]) -> None:
    from services.knowledge.writer import remember

    remember(session, domain_id=domain_id, kind=kind, key=key, title=title, content=content, content_json=content_json,
             tags=["AI", "DOMAIN_REVIEW", "ACCEPTED"], origin="DOMAIN_AI")


def apply_item(session, domain_id: str, item: Dict[str, Any]) -> str:
    """Write an accepted item where a pack import would put it. Returns what changed."""
    domain = _domain(session, domain_id)
    name = str(domain["DOMAIN_NAME"])
    kind = str(item.get("kind") or "").upper()
    col = str(item.get("target_column") or "").strip().upper()
    table = str(item.get("target_table") or "").strip().upper()
    value = str(item.get("value") or "").strip()
    assert kind in KINDS and value, "unsupported suggestion"
    if kind == "GLOSSARY":
        assert col, "a glossary suggestion needs a target column"
        new = [s.strip() for s in value.split(",") if s.strip()]
        found = rows(session, """SELECT SOURCE_REFERENCE, TITLE, CONTENT, CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                                  WHERE DOMAIN_ID = ? AND IS_CURRENT AND KNOWLEDGE_TYPE = 'GLOSSARY'
                                    AND UPPER(CONTENT_JSON:target_column::STRING) = ? LIMIT 1""", [domain_id, col])
        content = variant(found[0]["CONTENT_JSON"]) if found else {}
        content = content if isinstance(content, dict) else {}
        merged = list(dict.fromkeys([*(content.get("synonyms") or []), *new]))
        key = found[0]["SOURCE_REFERENCE"] if found else f"{name.lower()}.glossary.{col.lower()}"
        _knowledge_row(session, domain_id, "GLOSSARY", key, found[0]["TITLE"] if found else col,
                       f"{col}: also called {', '.join(merged)}", {**content, "target_column": col, "synonyms": merged})
        return f"{col} synonyms: {', '.join(merged)}"
    if kind == "BUSINESS_RULE":
        key = f"{name.lower()}.business_rule.{(col or 'domain').lower()}.{uuid.uuid4().hex[:6]}"
        _knowledge_row(session, domain_id, "BUSINESS_RULE", key, f"{col or name} rule", value,
                       {"target_column": col or None, "rule": value})
        return f"business rule added{f' for {col}' if col else ''}"
    if kind in ("TABLE_SIGNAL", "COLUMN_SIGNAL"):
        config = domain["CONFIG"]
        signals = config.get("signals") if isinstance(config.get("signals"), dict) else {}
        bucket = "tables" if kind == "TABLE_SIGNAL" else "columns"
        terms = dict(signals.get(bucket) or {})
        terms[value.upper()] = max(int(terms.get(value.upper()) or 0), 2)
        signals[bucket] = terms
        session.sql("""UPDATE KNOWLEDGE.DOMAIN_REGISTRY
                          SET CONFIG = OBJECT_INSERT(COALESCE(CONFIG, OBJECT_CONSTRUCT()), 'signals', PARSE_JSON(?), TRUE),
                              UPDATED_AT = CURRENT_TIMESTAMP()
                        WHERE DOMAIN_ID = ?""", params=[json.dumps(signals), domain_id]).collect()
        return f"{bucket[:-1]} signal {value.upper()} added"
    assert table and col, "a definition needs a target table and column"
    updated = session.sql("""UPDATE KNOWLEDGE.TARGET_COLUMN_REGISTRY C SET BUSINESS_DEFINITION = ?
                              WHERE UPPER(C.COLUMN_NAME) = ? AND C.TARGET_TABLE_ID IN (
                                    SELECT TARGET_TABLE_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                                     WHERE DOMAIN_ID = ? AND UPPER(TARGET_TABLE) = ?)""",
                          params=[value, col, domain_id, table]).collect()
    assert updated and updated[0][0], f"{table}.{col} is not a column of this domain"
    return f"definition set on {table}.{col}"


def decide(session, domain_id: str, payload_json: str) -> Dict[str, Any]:
    payload = json.loads(payload_json or "{}")
    item = payload.get("item") or {}
    decision = str(payload.get("decision") or "").upper()
    applied = apply_item(session, domain_id, item) if decision == "ACCEPTED" else None
    recorded = record_decision(session, SPEC, payload.get("suggestion_id") or "", f"domain.{domain_id}", item,
                               decision, None, domain_id, payload.get("note"))
    domain = _domain(session, domain_id)
    if applied and str(domain["CONFIG"].get("origin") or "repository") == "repository":
        applied += ". This domain ships as a repository pack: add the change to its domain_pack.json too, or the next deploy resets it."
    return {**recorded, "applied": applied}


ASK_SCHEMA = {"type": "object", "properties": {
    "answer": _STR,
    "citations": {"type": "array", "items": {"type": "object", "properties": {
        "kind": _STR, "key": _STR, "title": _STR}, "required": ["kind", "key"]}}},
    "required": ["answer", "citations"]}


def citation_keys(ctx: Dict[str, Any], hits: List[Dict[str, Any]]) -> Dict[str, Tuple[str, str]]:
    """key -> (kind, title) of everything the model was shown; citations outside this set are dropped."""
    keys: Dict[str, Tuple[str, str]] = {}
    for t in ctx["targets"]:
        keys[str(t["table"]).upper()] = ("TARGET", str(t["table"]))
        for c in t["columns"]:
            keys[f"{t['table']}.{c['name']}".upper()] = ("COLUMN", f"{t['table']}.{c['name']}")
    for k in ctx["knowledge"]:
        keys[str(k["key"]).upper()] = ("KNOWLEDGE", str(k.get("title") or k["key"]))
    for h in hits:
        ref = h.get("SOURCE_REFERENCE") or h.get("KNOWLEDGE_ID")
        if ref:
            keys[str(ref).upper()] = ("KNOWLEDGE", str(h.get("TITLE") or ref))
    return keys


def keep_citations(citations: List[Dict[str, Any]], keys: Dict[str, Tuple[str, str]]) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for c in citations or []:
        key = str((c or {}).get("key") or "").strip().upper()
        if key in keys and key not in seen:
            seen.add(key)
            kind, title = keys[key]
            out.append({"kind": kind, "key": key, "title": title})
    return out


def ask(session, domain_id: str, question: str) -> Dict[str, Any]:
    from services.common.llm import complete_json
    from services.knowledge.packs import export_pack
    from services.knowledge.search import search

    question = clip(str(question or "").strip(), 2000)
    assert question, "ask a question"
    pack = export_pack(_query(session), domain_id)
    ctx = pack_context(pack, budget=16000)
    try:
        database = scalar(session, "SELECT CURRENT_DATABASE()")
        hits = search(session, database, question, domain=ctx["domain"], limit=8)
    except Exception:
        hits = []
    prompt = ("Answer the question about this business domain using only the PACK and the KNOWLEDGE HITS below. "
              "Cite each fact with the key it came from: a target table name, TABLE.COLUMN, or a knowledge key. "
              "If the pack does not say, answer that it does not.\n\n"
              f"QUESTION: {question}\n\nPACK:\n{json.dumps(ctx, default=str)}\n\nKNOWLEDGE HITS:\n"
              + json.dumps([{"key": h.get("SOURCE_REFERENCE") or h.get("KNOWLEDGE_ID"), "title": h.get("TITLE"),
                             "content": clip(h.get("CONTENT"), 600)} for h in hits], default=str))
    started = time.time()
    output, usage, model = complete_json(session, prompt, ASK_SCHEMA, max_tokens=2000, stage="KNOWLEDGE")
    return {"answer": str(output.get("answer") or ""),
            "citations": keep_citations(output.get("citations") or [], citation_keys(ctx, hits)),
            "model": model, "usage": usage, "duration_ms": int((time.time() - started) * 1000)}
