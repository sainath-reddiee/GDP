"""Knowledge procedures (KNOWLEDGE schema, EXECUTE AS OWNER).

IDENTIFY_DOMAIN   side track: scores domains after profiling, records DOMAIN_RECOMMENDATION.
                  Does not overwrite a DOMAIN_ID already stamped from the onboarding target.
SEARCH_KNOWLEDGE  Cortex Search over current, active knowledge (agent tool and UI)
LOAD_SKILL        current version of a skill, loaded on demand
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from services.common.audit import record_cost, tool_call
from services.common.sql import clip, config_value, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.knowledge import search as ks
from services.knowledge.domain import infer_domain
from services.knowledge.validate import normalize_content
from services.knowledge.terms import entity_tokens, token_set


def _database(session) -> str:
    return scalar(session, "SELECT CURRENT_DATABASE()")


def _domain_terms(session) -> List[Dict[str, Any]]:
    try:
        domains = rows(session, "SELECT DOMAIN_ID, DOMAIN_NAME, CONFIG FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG")
    except Exception:
        domains = rows(session, "SELECT DOMAIN_ID, DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG")
    out = []
    for d in domains:
        terms = set()
        for k in rows(session, "SELECT CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE DOMAIN_ID = ? AND IS_CURRENT "
                               "AND STATUS = 'ACTIVE' AND KNOWLEDGE_TYPE = 'GLOSSARY'", [d["DOMAIN_ID"]]):
            content = normalize_content("GLOSSARY", variant(k["CONTENT_JSON"])) or {}
            for s in (content.get("synonyms") or []) + [content.get("target_column") or ""]:
                terms |= token_set(s)
        for c in rows(session, """SELECT C.COLUMN_NAME, T.TARGET_TABLE FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY C
                                  JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = C.TARGET_TABLE_ID
                                  WHERE T.DOMAIN_ID = ? AND T.ACTIVE_FLAG""", [d["DOMAIN_ID"]]):
            terms |= token_set(c["COLUMN_NAME"]) | entity_tokens(c["TARGET_TABLE"])
        signals = (variant(d.get("CONFIG")) or {}).get("signals")
        out.append({"domain_id": d["DOMAIN_ID"], "name": d["DOMAIN_NAME"], "terms": terms, "signals": signals})
    return out


def _identified_payload(session, run_id: str, stage: Stage) -> Dict[str, Any]:
    recs = rows(session, """SELECT R.DOMAIN_ID, D.DOMAIN_NAME, R.CONFIDENCE, R.STATUS
                              FROM KNOWLEDGE.DOMAIN_RECOMMENDATION R
                              JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
                             WHERE R.RUN_ID = ? ORDER BY R.CONFIDENCE DESC""", [run_id])
    accepted = next((r for r in recs if r["STATUS"] == "ACCEPTED"), recs[0] if recs else None)
    domain = None
    if accepted:
        domain = {"domain_id": accepted["DOMAIN_ID"], "domain_name": accepted["DOMAIN_NAME"],
                  "confidence": accepted["CONFIDENCE"]}
    return {"domain": domain, "state": stage.payload()}


def identify_domain(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    if stage.state != "PROFILING_COMPLETE":
        return _identified_payload(session, run_id, stage)
    stage.require("PROFILING_COMPLETE")
    database = _database(session)
    with tool_call(session, run_id, "get_domain_candidates", {"run_id": run_id}) as call:
        try:
            profile = rows(session, """SELECT TABLE_NAME, COLUMN_NAME, SEMANTIC_TYPE, GENERATED_DESCRIPTION
                                       FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = ? AND IS_CURRENT""", [run_id])
            assert profile, "no current profile for this run"
            started = time.time()
            query = " ".join(sorted({p["TABLE_NAME"] for p in profile}) + [p["COLUMN_NAME"] for p in profile])
            try:
                results = ks.search(session, database, query, limit=10)
            except Exception:
                results = []
            hits: Dict[str, int] = {}
            for r in results:
                hits[r.get("DOMAIN_NAME")] = hits.get(r.get("DOMAIN_NAME"), 0) + 1
            record_cost(session, run_id, "DOMAIN", None, {}, int((time.time() - started) * 1000), search_calls=1)
            vocab = _domain_terms(session)
            # Same rule as the Sources page: when packs define detection signals, a pack without signals (a generic
            # demo pack) does not compete on common words like ID or NAME. A domain already stamped stays eligible.
            if any(d.get("signals") for d in vocab):
                stamped = stage.run.get("DOMAIN_ID")
                vocab = [d for d in vocab if d.get("signals") or d["domain_id"] == stamped]
            ranked = infer_domain(sorted({p["TABLE_NAME"] for p in profile}), [p["COLUMN_NAME"] for p in profile],
                                  vocab, hits)
            assert ranked, "no active domains are registered"
            threshold = float(config_value(session, "DOMAIN_CONFIDENCE_THRESHOLD", 0.3))
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            raise
        top = ranked[0]
        stamped_id = stage.run.get("DOMAIN_ID")
        matched = [{"knowledge_id": r.get("KNOWLEDGE_ID"), "title": r.get("TITLE"), "type": r.get("KNOWLEDGE_TYPE")}
                   for r in results if r.get("DOMAIN_NAME") == top["domain_name"]]
        recommendation = (f"{top['domain_name']} (confidence {top['confidence']:.2f}); signals: "
                          f"{'; '.join(top['evidence']['signals'][:6]) or 'none'}; matched terms: "
                          f"{', '.join(top['evidence']['matched_terms'][:12])}")
        confident = top["confidence"] >= threshold
        if not confident:
            recommendation += (f". Below the {threshold:.2f} threshold, so no pack was applied: pick the domain on "
                               "the Domain page before mapping.")
        call.summary = f"Cortex Search: {len(results)} knowledge items; selected {recommendation}"

    def accepted(d: Dict[str, Any]) -> bool:
        return bool((stamped_id and d["domain_id"] == stamped_id) or (not stamped_id and confident and d is top))

    def record(_event_id: str) -> None:
        insert_rows(session, "KNOWLEDGE.DOMAIN_RECOMMENDATION",
                    ["RECOMMENDATION_ID", "RUN_ID", "DOMAIN_ID", "CONFIDENCE", "EVIDENCE_JSON", "MATCHED_KNOWLEDGE",
                     "RECOMMENDATION", "STATUS", "DECIDED_BY", "DECIDED_AT", "MODEL_VERSION"],
                    ["?", "?", "?", "?::FLOAT", "PARSE_JSON(?)", "PARSE_JSON(?)", "?", "?", "NULLIF(?, '')",
                     "IFF(? = 'ACCEPTED', CURRENT_TIMESTAMP(), NULL)", "'domain-scoring-v2'"],
                    [[str(uuid.uuid4()), run_id, d["domain_id"], d["confidence"], d["evidence"],
                      matched if d is top else [], recommendation if d is top else f"{d['domain_name']} not selected",
                      "ACCEPTED" if accepted(d) else "PROPOSED",
                      "SYSTEM" if accepted(d) else None,
                      "ACCEPTED" if accepted(d) else "PROPOSED"] for d in ranked])
        if not stamped_id and confident:
            session.sql("UPDATE CORE.WORKFLOW_RUN SET DOMAIN_ID = ? WHERE RUN_ID = ?",
                        params=[top["domain_id"], run_id]).collect()

    stage.move("DOMAIN_IDENTIFIED", recommendation, {"domains": ranked[:3]}, in_transaction=record)
    return {"domain": top, "alternatives": ranked[1:], "matched_knowledge": matched, "state": stage.payload()}


def search_knowledge(session, query: str, domain: Optional[str], knowledge_type: Optional[str],
                     limit: Optional[float]) -> Dict[str, Any]:
    database = _database(session)
    with tool_call(session, None, "search_domain_knowledge",
                   {"query": clip(query, 200), "domain": domain, "type": knowledge_type}) as call:
        results = ks.search(session, database, query, domain or None, knowledge_type or None, int(limit or 5))
        call.summary = f"{len(results)} results"
    return {"results": results}


def load_skill(session, skill_name: str) -> Dict[str, Any]:
    raw = (skill_name or "").strip().upper()
    names = list({raw, raw.replace("_", "-"), raw.replace("-", "_")})
    found = rows(session, """SELECT SKILL_NAME, SKILL_TYPE, VERSION, STAGE_PATH, CHECKSUM, DESCRIPTION, CONTENT, CONFIG
                             FROM KNOWLEDGE.SKILL_REGISTRY
                             WHERE IS_CURRENT AND STATUS = 'ACTIVE'
                               AND SKILL_NAME IN (""" + ", ".join(["?"] * len(names)) + ")",
                 names)
    assert found, f"skill {skill_name} not found"
    skill = found[0]
    skill["CONFIG"] = variant(skill["CONFIG"])
    with tool_call(session, None, "load_skill", {"skill": skill["SKILL_NAME"], "version": skill["VERSION"]}) as call:
        call.summary = f"{skill['SKILL_NAME']} v{skill['VERSION']}"
    return {k.lower(): v for k, v in skill.items()}


GENERAL_DOMAIN = "GENERAL"


def ensure_domain(session, name: str, description: str = "") -> str:
    """Domain id by name, created (active) when it does not exist yet."""
    name = (name or "").strip().upper()
    assert name, "domain name is required"
    found = rows(session, "SELECT DOMAIN_ID FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = ?", [name])
    if found:
        return found[0]["DOMAIN_ID"]
    domain_id = str(uuid.uuid4())
    insert_rows(session, "KNOWLEDGE.DOMAIN_REGISTRY",
                ["DOMAIN_ID", "DOMAIN_NAME", "DESCRIPTION", "OWNER", "ACTIVE_FLAG", "VERSION"],
                ["?", "?", "NULLIF(?, '')", "CURRENT_USER()", "TRUE", "1"],
                [[domain_id, name, description]])
    return domain_id


def _target_domain(session, payload: Dict[str, Any], registered_domain: Optional[str]) -> str:
    """Domain for a target: the caller's choice, else where the same table is already registered, else GENERAL.
    Never a fixed client domain, so any company's tables are filed under their own domain."""
    if payload.get("domain_id"):
        found = rows(session, "SELECT DOMAIN_ID FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [payload["domain_id"]])
        assert found, f"unknown domain {payload['domain_id']}"
        return found[0]["DOMAIN_ID"]
    if payload.get("domain_name"):
        return ensure_domain(session, payload["domain_name"])
    if registered_domain:
        return registered_domain
    return ensure_domain(session, GENERAL_DOMAIN, "Targets registered without a domain pack")


def _add_new_target_columns(session, target_id: str, columns: List[Dict[str, Any]]) -> None:
    """Re-registering a target picks up columns added to the table since its last snapshot."""
    known = {str(r["COLUMN_NAME"]) for r in rows(session, "SELECT COLUMN_NAME FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY "
                                                          "WHERE TARGET_TABLE_ID = ?", [target_id])}
    new = [c for c in columns if c["column_name"] not in known]
    if new:
        insert_rows(session, "KNOWLEDGE.TARGET_COLUMN_REGISTRY",
                    ["TARGET_COLUMN_ID", "TARGET_TABLE_ID", "COLUMN_NAME", "DATA_TYPE", "ORDINAL_POSITION",
                     "NULLABLE", "BUSINESS_DEFINITION", "SEMANTIC_TYPE", "IS_BUSINESS_KEY", "IS_PII",
                     "ACCEPTED_VALUES", "VERSION"],
                    ["?", "?", "?", "?", "?::NUMBER", "?::BOOLEAN", "NULLIF(?, '')", "NULLIF(?, '')",
                     "?::BOOLEAN", "FALSE", "PARSE_JSON(?)", "1"],
                    [[str(uuid.uuid4()), target_id, c["column_name"], c["data_type"], c["ordinal_position"],
                      bool(c.get("nullable")), c.get("comment"), None, bool(c.get("business_key")), []] for c in new])


def register_target_table(session, payload_json: str) -> Dict[str, Any]:
    """Store a target snapshot. The caller reads the external table; this procedure only writes platform metadata.

    Owner's rights cannot see every database the signed-in user can see, so the column list arrives in the payload.
    """
    from services.source.identifiers import normalize

    payload = json.loads(payload_json or "{}")
    database, schema, table = normalize(payload.get("database")), normalize(payload.get("schema")), normalize(payload.get("table"))
    columns = payload.get("columns") or []
    assert isinstance(columns, list) and columns, f"TARGET_NOT_VISIBLE: {database}.{schema}.{table}"
    found = rows(session, """SELECT TARGET_TABLE_ID, DOMAIN_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                             WHERE TARGET_DATABASE = ? AND TARGET_SCHEMA = ? AND TARGET_TABLE = ? AND ACTIVE_FLAG
                             ORDER BY CREATED_AT LIMIT 1""", [database, schema, table])
    domain_id = _target_domain(session, payload, found[0]["DOMAIN_ID"] if found else None)
    if found:
        target_id = found[0]["TARGET_TABLE_ID"]
        _add_new_target_columns(session, target_id, columns)
    else:
        target_id = str(uuid.uuid4())
        insert_rows(session, "KNOWLEDGE.TARGET_TABLE_REGISTRY",
                    ["TARGET_TABLE_ID", "DOMAIN_ID", "TARGET_DATABASE", "TARGET_SCHEMA", "TARGET_TABLE",
                     "TABLE_TYPE", "GRAIN", "BUSINESS_KEYS", "SCD_TYPE", "DESCRIPTION", "VERSION", "ACTIVE_FLAG"],
                    ["?", "?", "?", "?", "?", "'TABLE'", "?", "PARSE_JSON(?)", "'1'", "?", "1", "TRUE"],
                    [[target_id, domain_id, database, schema, table,
                      f"One row per business key of {table}, snapshotted from the existing table.",
                      [], f"Existing table {database}.{schema}.{table} selected as the modeling target."]])
        insert_rows(session, "KNOWLEDGE.TARGET_COLUMN_REGISTRY",
                    ["TARGET_COLUMN_ID", "TARGET_TABLE_ID", "COLUMN_NAME", "DATA_TYPE", "ORDINAL_POSITION",
                     "NULLABLE", "BUSINESS_DEFINITION", "SEMANTIC_TYPE", "IS_BUSINESS_KEY", "IS_PII",
                     "ACCEPTED_VALUES", "VERSION"],
                    ["?", "?", "?", "?", "?::NUMBER", "?::BOOLEAN", "NULLIF(?, '')", "NULLIF(?, '')",
                     "?::BOOLEAN", "FALSE", "PARSE_JSON(?)", "1"],
                    [[str(uuid.uuid4()), target_id, c["column_name"], c["data_type"], c["ordinal_position"],
                      bool(c.get("nullable")), c.get("comment"), None, bool(c.get("business_key")), []]
                     for c in columns])
    return {"target_table_id": target_id, "target_model": f"{database}.{schema}.{table}",
            "columns": len(columns), "domain_id": domain_id}


def current_knowledge_version(session, domain_id: Optional[str]) -> str:
    """Stable fingerprint of the current knowledge set used by an artifact."""
    value = scalar(session, """SELECT TO_VARCHAR(MAX(UPDATED_AT), 'YYYYMMDDHH24MISS') || '-' || COUNT(*)
                               FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE IS_CURRENT AND (DOMAIN_ID = ? OR ? = '')""",
                   [domain_id or "", domain_id or ""])
    return value or "none"
