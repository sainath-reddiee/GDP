"""One way to write knowledge, so every item is versioned the same way and carries where it came from.

remember() keeps one lineage per domain + reference:
  - identical content to the version in use writes nothing (re-running a stage does not churn versions)
  - "auto" policy: the new version becomes current and ACTIVE, the old one SUPERSEDED
  - "review" policy: the new version waits as PROPOSED (not current); the version in use stays until a steward
    approves it in the Knowledge inbox
  - an explicit status (a rejection kept as DRAFT so generation stops proposing it) is written as given
forget() retires the version in use (a reviewer rejected what it said).

The policy per knowledge type lives in platform config KNOWLEDGE_LEARNING_POLICY; people's own edits (origin USER)
are never queued.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, Iterable, List, Optional

from services.common.sql import clip, config_value, rows, scalar

# what is learned from automatic steps (no person decided it) waits for review by default
DEFAULT_POLICY: Dict[str, str] = {"QA_TEST": "review", "EXCEPTION": "review"}
REVIEW_ORIGINS = {"COPILOT"}
NEVER_QUEUED = {"USER", "SEED", "PACK_IMPORT"}
# run records kept in DOMAIN_KNOWLEDGE (dbt branch plan, client Soda brief, STTM CSV export): written as ACTIVE so
# their readers find them, and kept out of the rule queries that feed prompts. Constant SQL, never user input.
OPERATIONAL_STATUS = "ACTIVE"
NOT_OPERATIONAL_SQL = ("COALESCE(SOURCE_REFERENCE, '') NOT LIKE 'dbt.branch.%' "
                       "AND COALESCE(SOURCE_REFERENCE, '') NOT LIKE 'soda.brief.%' "
                       "AND COALESCE(SOURCE_REFERENCE, '') NOT LIKE 'sttm.csv.%'")


def lineage_id(domain_id: Optional[str], key: str) -> str:
    """Same value as the V022 backfill: MD5(domain || '|' || reference)."""
    return hashlib.md5(f"{domain_id or ''}|{key}".encode("utf-8")).hexdigest()


def mode_for(policy: Optional[Dict[str, Any]], kind: str, origin: str) -> str:
    """'auto' or 'review' for one write."""
    origin = (origin or "").upper()
    if origin in NEVER_QUEUED:
        return "auto"
    merged = {**DEFAULT_POLICY, **{str(k).upper(): str(v).lower() for k, v in (policy or {}).items()}}
    if origin in REVIEW_ORIGINS and merged.get(f"ORIGIN:{origin}", "review") == "review":
        return "review"
    return "review" if merged.get(str(kind).upper()) == "review" else "auto"


def _same(current: Dict[str, Any], content: str, content_json: Any) -> bool:
    if str(current.get("CONTENT") or "") != str(content or ""):
        return False
    cj = current.get("CONTENT_JSON")
    try:
        cj = json.loads(cj) if isinstance(cj, str) else cj
    except ValueError:
        pass
    return json.dumps(cj, sort_keys=True, default=str) == json.dumps(content_json, sort_keys=True, default=str)


def remember(session, *, domain_id: Optional[str], kind: str, key: str, title: str, content: str,
             content_json: Any = None, tags: Iterable[str] = (), origin: str, run_id: Optional[str] = None,
             confidence: Optional[float] = None, status: Optional[str] = None, change_note: Optional[str] = None,
             by_domain: bool = True) -> Optional[str]:
    """Write one version. Returns the new KNOWLEDGE_ID, or None when nothing changed. by_domain=False keys the lineage
    on the reference alone (references that already contain a run id)."""
    from services.common.sql import insert_rows

    content = clip(content, 16000)
    where, params = ("DOMAIN_ID = ? AND SOURCE_REFERENCE = ?", [domain_id, key]) if by_domain else ("SOURCE_REFERENCE = ?", [key])
    current = rows(session, f"""SELECT KNOWLEDGE_ID, CONTENT, CONTENT_JSON, STATUS FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                                 WHERE {where} AND IS_CURRENT ORDER BY VERSION DESC LIMIT 1""", params)
    if current and _same(current[0], content, content_json) and (status or "ACTIVE") == current[0].get("STATUS"):
        return None
    mode = "auto" if status else mode_for(config_value(session, "KNOWLEDGE_LEARNING_POLICY", {}), kind, origin)
    version = (scalar(session, f"SELECT MAX(VERSION) FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE {where}", params) or 0) + 1
    if mode == "review":
        # one proposal at a time per lineage: a newer proposal replaces the one still waiting
        session.sql(f"""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET STATUS = 'SUPERSEDED', UPDATED_AT = CURRENT_TIMESTAMP()
                        WHERE {where} AND STATUS = 'PROPOSED'""", params=params).collect()
        new_status, is_current = "PROPOSED", False
    else:
        session.sql(f"""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE
                           SET IS_CURRENT = FALSE, UPDATED_AT = CURRENT_TIMESTAMP(),
                               STATUS = IFF(STATUS IN ('ACTIVE', 'RETIRED'), 'SUPERSEDED', STATUS)
                         WHERE {where} AND IS_CURRENT""", params=params).collect()
        new_status, is_current = (status or "ACTIVE"), True
    knowledge_id = str(uuid.uuid4())
    insert_rows(session, "KNOWLEDGE.DOMAIN_KNOWLEDGE",
                ["KNOWLEDGE_ID", "DOMAIN_ID", "KNOWLEDGE_TYPE", "TITLE", "CONTENT", "CONTENT_JSON", "TAGS",
                 "SOURCE_REFERENCE", "STATUS", "VERSION", "IS_CURRENT", "CREATED_BY", "LINEAGE_ID", "ORIGIN",
                 "SOURCE_RUN_ID", "CONFIDENCE", "CHANGE_NOTE"],
                ["?", "?", "?", "?", "?", "PARSE_JSON(?)", "PARSE_JSON(?)", "?", "?", "?::NUMBER", "?::BOOLEAN",
                 "CURRENT_USER()", "?", "?", "NULLIF(?, '')", "TRY_TO_DOUBLE(NULLIF(?, ''))", "NULLIF(?, '')"],
                [[knowledge_id, domain_id, kind, clip(title, 500), content, content_json, list(tags), key, new_status,
                  version, is_current, lineage_id(domain_id if by_domain else None, key), origin.upper(), run_id or "",
                  "" if confidence is None else str(confidence), clip(change_note, 2000) if change_note else ""]])
    return knowledge_id


def forget(session, domain_id: Optional[str], key: str) -> None:
    """A reviewer rejected what the current version says: it stops being used (kept for history)."""
    session.sql("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, STATUS = 'RETIRED', UPDATED_AT = CURRENT_TIMESTAMP()
                   WHERE DOMAIN_ID = ? AND SOURCE_REFERENCE = ? AND IS_CURRENT""", params=[domain_id, key]).collect()


def record_usage(session, run_id: Optional[str], stage: str, knowledge_ids: List[str]) -> None:
    """Which knowledge a stage put into its prompt or rules, for usage analytics. Never fails the stage."""
    ids = [i for i in dict.fromkeys(knowledge_ids) if i]
    if not ids:
        return
    try:
        session.sql("""INSERT INTO KNOWLEDGE.KNOWLEDGE_USAGE (KNOWLEDGE_ID, LINEAGE_ID, RUN_ID, STAGE)
                       SELECT K.KNOWLEDGE_ID, K.LINEAGE_ID, NULLIF(?, ''), ? FROM KNOWLEDGE.DOMAIN_KNOWLEDGE K
                        WHERE ARRAY_CONTAINS(K.KNOWLEDGE_ID::VARIANT, PARSE_JSON(?))
                          AND NOT EXISTS (SELECT 1 FROM KNOWLEDGE.KNOWLEDGE_USAGE U
                                           WHERE U.KNOWLEDGE_ID = K.KNOWLEDGE_ID AND U.STAGE = ?
                                             AND COALESCE(U.RUN_ID, '') = ?)""",
                    params=[run_id or "", stage, json.dumps(ids[:500]), stage, run_id or ""]).collect()
    except Exception:
        pass
