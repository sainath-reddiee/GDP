"""Mapping procedures (MAPPING schema, EXECUTE AS OWNER).

GENERATE_MAPPING_CANDIDATES  DOMAIN_IDENTIFIED -> MAPPING_PENDING -> MAPPING_REVIEW
SAVE_MAPPING_DECISIONS       records reviewer decisions while the run is in MAPPING_REVIEW
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.common.audit import record_cost, tool_call
from services.common.llm import complete_json
from services.common.sql import clip, config_value, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.knowledge import search as ks
from services.knowledge.procedures import current_knowledge_version, identify_domain
from services.knowledge.validate import normalize_content
from services.common.standard import run_standard
from services.knowledge.usage import assert_safe_transformation, domain_context, use_stage
from services.knowledge.writer import forget, record_usage, remember
from services.mapping import features, scoring
from services.mapping.feedback import pattern as feedback_pattern

DECISIONS = {"APPROVED", "REJECTED", "MODIFIED", "ALTERNATIVE_TARGET"}
EMBED_MODEL_DEFAULT = "snowflake-arctic-embed-l-v2.0"
EMBED_MODELS = {EMBED_MODEL_DEFAULT}


NO_TARGET = ("NO_TARGET_MODEL: this run has no target model. In Sources, open the modeling panel and pick an "
             "existing model or propose a new one before mapping.")


def target_table(session, run: Dict[str, Any]) -> Dict[str, Any]:
    """The run's target model, resolved exactly: DB.SCHEMA.TABLE when the run names one (preferring the run's
    domain), otherwise the table name inside the run's domain. Never a guess: no target means a clear error."""
    model = (run.get("TARGET_MODEL") or "").strip()
    assert model, NO_TARGET
    parts = model.split(".")
    if len(parts) == 3:
        found = rows(session, """SELECT * FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                                 WHERE ACTIVE_FLAG AND TARGET_DATABASE = ? AND TARGET_SCHEMA = ? AND TARGET_TABLE = ?
                                 ORDER BY IFF(DOMAIN_ID = ?, 0, 1), CREATED_AT LIMIT 1""",
                     [parts[0], parts[1], parts[2], run.get("DOMAIN_ID") or ""])
        if not found:  # registered before identifiers kept their case
            found = rows(session, """SELECT * FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                                     WHERE ACTIVE_FLAG AND UPPER(TARGET_DATABASE) = UPPER(?)
                                       AND UPPER(TARGET_SCHEMA) = UPPER(?) AND UPPER(TARGET_TABLE) = UPPER(?)
                                     ORDER BY IFF(DOMAIN_ID = ?, 0, 1), CREATED_AT LIMIT 1""",
                         [parts[0], parts[1], parts[2], run.get("DOMAIN_ID") or ""])
    else:
        assert run.get("DOMAIN_ID"), ("NO_DOMAIN: confirm the knowledge pack on the Domain page before mapping, "
                                      f"so the target {model} can be found.")
        found = rows(session, """SELECT * FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                                 WHERE ACTIVE_FLAG AND DOMAIN_ID = ? AND UPPER(TARGET_TABLE) = UPPER(?)
                                 ORDER BY CREATED_AT LIMIT 1""", [run["DOMAIN_ID"], parts[-1]])
    assert found, (f"TARGET_NOT_REGISTERED: target model {model} is not registered. Register it from Sources "
                   "(modeling panel) and retry.")
    return found[0]


def target_columns(session, target_table_id: str) -> List[Dict[str, Any]]:
    out = []
    for c in rows(session, """SELECT C.*, T.TARGET_TABLE FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY C
                              JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = C.TARGET_TABLE_ID
                              WHERE C.TARGET_TABLE_ID = ? ORDER BY C.ORDINAL_POSITION""", [target_table_id]):
        out.append({"target_column_id": c["TARGET_COLUMN_ID"], "column_name": c["COLUMN_NAME"],
                    "table_name": c["TARGET_TABLE"], "data_type": c["DATA_TYPE"], "nullable": c["NULLABLE"],
                    "semantic_type": c["SEMANTIC_TYPE"], "is_business_key": c["IS_BUSINESS_KEY"],
                    "is_pii": c["IS_PII"], "accepted_values": variant(c["ACCEPTED_VALUES"]) or [],
                    "definition": c["BUSINESS_DEFINITION"]})
    return out


def source_columns(session, run_id: str) -> List[Dict[str, Any]]:
    out = []
    for p in rows(session, """SELECT * FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = ? AND IS_CURRENT
                              ORDER BY TABLE_NAME, COLUMN_NAME""", [run_id]):
        stats = variant(p["STATISTICS_JSON"]) or {}
        patterns = variant(p["PATTERN_JSON"]) or []
        out.append({"source_column_id": p["SOURCE_COLUMN_ID"], "column_name": p["COLUMN_NAME"],
                    "table_name": p["TABLE_NAME"], "data_type": p["DATA_TYPE"], "semantic_type": p["SEMANTIC_TYPE"],
                    "null_percentage": p["NULL_PERCENTAGE"], "distinct_percentage": p["DISTINCT_PERCENTAGE"],
                    "cardinality": p["CARDINALITY"], "max_length": stats.get("max_length"),
                    "date_format": stats.get("date_format"), "values": stats.get("enum_values"),
                    "pattern": patterns[0]["pattern"] if patterns else None,
                    "description": p["GENERATED_DESCRIPTION"], "pii": p["PII_CLASSIFICATION"]})
    return out


def domain_knowledge(session, domain_id: str, run_id: str, target: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Knowledge for one run. Learned evidence (reviewer patterns, past decisions) is scoped to the run's domain and
    target table, so a column name approved for one company's table never steers an unrelated table."""
    knowledge: Dict[str, Any] = {"glossary": {}, "rules": {}, "transforms": [], "history": [], "notes": []}
    target_name = str((target or {}).get("TARGET_TABLE") or "").upper()
    used: List[str] = []
    from services.knowledge.writer import NOT_OPERATIONAL_SQL

    for k in rows(session, f"""SELECT KNOWLEDGE_ID, KNOWLEDGE_TYPE, CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                              WHERE DOMAIN_ID = ? AND IS_CURRENT AND STATUS = 'ACTIVE' AND CONTENT_JSON IS NOT NULL
                                AND {NOT_OPERATIONAL_SQL}""",
                  [domain_id]):
        kind = k["KNOWLEDGE_TYPE"]
        content = normalize_content(kind, variant(k["CONTENT_JSON"]))
        if content is None:
            continue
        if kind == "MAPPING_PATTERN" and target_name and content.get("target_table") \
                and str(content["target_table"]).upper() != target_name:
            continue
        if kind in ("GLOSSARY", "BUSINESS_RULE", "TRANSFORMATION_RULE", "MAPPING_PATTERN"):
            used.append(k["KNOWLEDGE_ID"])
        if kind == "GLOSSARY" and content.get("target_column"):
            knowledge["glossary"][content["target_column"].upper()] = content
        elif kind == "BUSINESS_RULE" and content.get("target_column"):
            knowledge["rules"][content["target_column"].upper()] = content
        elif kind == "TRANSFORMATION_RULE":
            knowledge["transforms"].append(content)
        elif kind == "MAPPING_PATTERN" and content.get("source_column") and content.get("target_column"):
            if target_name and content.get("target_table") and str(content["target_table"]).upper() != target_name:
                continue
            knowledge["history"].append((content["source_column"], content["target_column"], 1.0))
            if content.get("justification") or content.get("overridden"):
                knowledge["notes"].append(
                    f"{content['source_column']} -> {content['target_column']}"
                    + (f" (model proposed {content['proposed_target']})" if content.get("overridden") else "")
                    + (f": {content['justification']}" if content.get("justification") else "")
                )
    record_usage(session, run_id, "MAPPING", used)
    for h in rows(session, """SELECT L.COLUMN_NAME AS SRC, T.COLUMN_NAME AS TGT
                              FROM MAPPING.MAPPING_DECISION D
                              JOIN SOURCE.LANDING_COLUMN_REGISTRY L ON L.LANDING_COLUMN_ID = D.SOURCE_COLUMN_ID
                              JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY T ON T.TARGET_COLUMN_ID = D.TARGET_COLUMN_ID
                              JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY TT ON TT.TARGET_TABLE_ID = T.TARGET_TABLE_ID
                              WHERE D.IS_CURRENT AND D.RUN_ID <> ? AND D.DECISION IN ('APPROVED', 'MODIFIED', 'ALTERNATIVE_TARGET')
                                AND TT.DOMAIN_ID = ? AND (NULLIF(?, '') IS NULL OR TT.TARGET_TABLE_ID = ?)""",
                  [run_id, domain_id or "", (target or {}).get("TARGET_TABLE_ID") or "",
                   (target or {}).get("TARGET_TABLE_ID") or ""]):
        knowledge["history"].append((h["SRC"], h["TGT"], 1.0))
    return knowledge


def _embed_text_source(c: Dict[str, Any]) -> str:
    return f"Column {c['column_name']} of table {c['table_name']}: {c.get('description') or c['semantic_type']}"


def _embed_text_target(t: Dict[str, Any]) -> str:
    return f"Column {t['column_name']} of {t['table_name']}: {t.get('definition') or ''}"


def embeddings(session, kind: str, items: List[Tuple[str, str]], model: str) -> Dict[str, str]:
    """(object_id, text) -> embedding id, computing AI_EMBED only for texts not seen before (reuses the modeler's approach)."""
    out = {}
    for object_id, text in items:
        sha = hashlib.sha256(f"{model}|{text}".encode("utf-8")).hexdigest()
        existing = rows(session, "SELECT EMBEDDING_ID FROM MAPPING.COLUMN_EMBEDDING WHERE OBJECT_KIND = ? AND OBJECT_ID = ? "
                                 "AND TEXT_SHA256 = ? LIMIT 1", [kind, object_id, sha])
        if existing:
            out[object_id] = existing[0]["EMBEDDING_ID"]
            continue
        embedding_id = str(uuid.uuid4())
        assert model in EMBED_MODELS, f"unsupported embed model {model}"
        session.sql(
            "INSERT INTO MAPPING.COLUMN_EMBEDDING (EMBEDDING_ID, OBJECT_KIND, OBJECT_ID, EMBED_MODEL, EMBED_TEXT, "
            f"EMBEDDING, TEXT_SHA256) SELECT ?, ?, ?, ?, ?, AI_EMBED('{model}', ?), ?",
            params=[embedding_id, kind, object_id, model, clip(text), clip(text), sha],
        ).collect()
        out[object_id] = embedding_id
    return out


def cosine_matrix(session, source_ids: Dict[str, str], target_ids: Dict[str, str]) -> Dict[Tuple[str, str], float]:
    result = rows(session, """
        SELECT S.OBJECT_ID AS SID, T.OBJECT_ID AS TID, VECTOR_COSINE_SIMILARITY(S.EMBEDDING, T.EMBEDDING) AS COS
          FROM MAPPING.COLUMN_EMBEDDING S, MAPPING.COLUMN_EMBEDDING T
         WHERE ARRAY_CONTAINS(S.EMBEDDING_ID::VARIANT, PARSE_JSON(?)::ARRAY)
           AND ARRAY_CONTAINS(T.EMBEDDING_ID::VARIANT, PARSE_JSON(?)::ARRAY)""",
                  [json.dumps(list(source_ids.values())), json.dumps(list(target_ids.values()))])
    return {(r["SID"], r["TID"]): float(r["COS"]) for r in result}


ADJUDICATION_SCHEMA = {
    "type": "object",
    "properties": {"columns": {"type": "array", "items": {"type": "object", "properties": {
        "source_column": {"type": "string"}, "preferred_target": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["source_column", "preferred_target", "reason"]}}},
    "required": ["columns"],
}


def _adjudicate(session, run_id: str, ambiguous: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]],
                skill_excerpt: str = "", reviewer_notes: Optional[List[str]] = None) -> Dict[str, Dict]:
    """One LLM call for ambiguous columns only. It explains; it never re-ranks."""
    if not ambiguous:
        return {}
    blocks = []
    for source, ranked in ambiguous:
        options = "; ".join(f"{c['target']['column_name']} (score {c['final_score']}: {c['target'].get('definition')})"
                            for c in ranked)
        blocks.append(f"- {source['column_name']} ({source['data_type']}, {source['semantic_type']}, null "
                      f"{source['null_percentage']}%, distinct {source['distinct_percentage']}%): "
                      f"{source.get('description') or ''}\n  candidates: {options}")
    prior = ""
    if reviewer_notes:
        prior = "Prior reviewer decisions on this domain:\n- " + "\n- ".join(reviewer_notes[:12]) + "\n"
    prompt = ("You review source-to-target column mappings for a data warehouse. For each source column pick the "
              "best candidate target (or NONE) and give a one-sentence reason grounded in the evidence. "
              "Do not invent columns. Ranking is already fixed; you only explain the top candidates.\n"
              + prior
              + (f"\nLoaded skills:\n{skill_excerpt}\n" if skill_excerpt else "")
              + "\n".join(blocks))
    started = time.time()
    try:
        result, usage, model = complete_json(session, prompt, ADJUDICATION_SCHEMA, max_tokens=2000, stage="MAPPING")
    except Exception:
        return {}
    record_cost(session, run_id, "MAPPING", model, usage, int((time.time() - started) * 1000), tool_calls=1)
    return {str(c["source_column"]).upper(): {**c, "model": model} for c in (result.get("columns") or [])
            if isinstance(c, dict) and isinstance(c.get("source_column"), str)
            and isinstance(c.get("preferred_target"), str)}


def _scoring_config(session, domain_id: str) -> Dict[str, Any]:
    found = rows(session, """SELECT CONFIG_ID, WEIGHTS, THRESHOLDS FROM MAPPING.MAPPING_SCORING_CONFIG
                             WHERE ACTIVE_FLAG AND (DOMAIN_ID = ? OR DOMAIN_ID IS NULL)
                             ORDER BY IFF(DOMAIN_ID IS NULL, 1, 0), VERSION DESC LIMIT 1""", [domain_id])
    assert found, "no active mapping scoring configuration"
    cfg = {"config_id": found[0]["CONFIG_ID"], "weights": variant(found[0]["WEIGHTS"]),
           "thresholds": variant(found[0]["THRESHOLDS"])}
    scoring.validate_config(cfg["weights"], cfg["thresholds"])
    return cfg


def generate_mapping_candidates(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    if stage.state == "PROFILING_COMPLETE":
        identify_domain(session, run_id)
        stage = Stage(session, run_id)
    stage.require("DOMAIN_IDENTIFIED", "MAPPING_PENDING")
    stage.walk(["DOMAIN_IDENTIFIED", "MAPPING_PENDING"], "mapping generation started")
    database = scalar(session, "SELECT CURRENT_DATABASE()")
    started = time.time()
    with tool_call(session, run_id, "generate_mapping_candidates", {"run_id": run_id}) as call:
        try:
            guidance = use_stage(session, "MAPPING", run_standard(stage.run), run_id=run_id)
            run = stage.run
            target = target_table(session, run)
            targets = target_columns(session, target["TARGET_TABLE_ID"])
            assert targets, (f"TARGET_EMPTY: {target['TARGET_DATABASE']}.{target['TARGET_SCHEMA']}."
                             f"{target['TARGET_TABLE']} has no registered columns. Register the table again from "
                             "Sources so its columns are captured, or pick another target.")
            try:
                guidance += "\n\n" + domain_context(session, run["DOMAIN_ID"], target["TARGET_TABLE"], 4000,
                                                     run_standard(run))
            except Exception:
                pass
            mappable = features.mappable_targets(targets)
            sources = source_columns(session, run_id)
            assert sources, "no current profile for this run"
            knowledge = domain_knowledge(session, run["DOMAIN_ID"], run_id, target)
            cfg = _scoring_config(session, run["DOMAIN_ID"])
            top_k = int(config_value(session, "MAPPING_TOP_K", 3))
            model = config_value(session, "EMBED_MODEL", EMBED_MODEL_DEFAULT)
            src_emb = embeddings(session, "SOURCE_COLUMN", [(s["source_column_id"], _embed_text_source(s)) for s in sources], model)
            tgt_emb = embeddings(session, "TARGET_COLUMN", [(t["target_column_id"], _embed_text_target(t)) for t in mappable], model)
            cosines = cosine_matrix(session, src_emb, tgt_emb)
            domain_name = scalar(session, "SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?",
                                 [run["DOMAIN_ID"]])

            ranked_by_source: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
            search_calls = 0
            for s in sources:
                cands = []
                for t in mappable:
                    f = features.all_features(s, t, knowledge, cosines.get((s["source_column_id"], t["target_column_id"])))
                    cands.append({"target": t, **f})
                ranked = scoring.rank_candidates(cands, cfg["weights"], cfg["thresholds"], top_k)
                try:
                    hits = ks.search(session, database, f"{s['column_name']} {s.get('description') or ''}",
                                     domain=domain_name, limit=3)
                    search_calls += 1
                except Exception:
                    hits = []
                for c in ranked:
                    c["evidence"]["knowledge"] = [h.get("TITLE") for h in hits]
                ranked_by_source.append((s, ranked))

            ambiguous = [(s, r) for s, r in ranked_by_source if r and r[0]["ambiguous"]]
            llm = _adjudicate(session, run_id, ambiguous, guidance, knowledge["notes"])
            version = (scalar(session, "SELECT MAX(CANDIDATE_VERSION) FROM MAPPING.MAPPING_CANDIDATE WHERE RUN_ID = ?",
                              [run_id]) or 0) + 1
            knowledge_version = current_knowledge_version(session, run["DOMAIN_ID"])
            values = []
            for s, ranked in ranked_by_source:
                verdict = llm.get(s["column_name"].upper())
                for c in ranked:
                    reason = scoring.explain(c)
                    if verdict and c["rank"] == 1:
                        agrees = verdict["preferred_target"].upper() == c["target"]["column_name"].upper()
                        c["evidence"]["llm"] = {"preferred_target": verdict["preferred_target"], "agrees": agrees,
                                                "model": verdict["model"]}
                        if verdict.get("reason"):
                            reason = f"{verdict['reason']} (deterministic evidence: {reason})"
                    sc = c["scores"]
                    values.append([str(uuid.uuid4()), run_id, s["source_column_id"], c["target"]["target_column_id"],
                                   run["DOMAIN_ID"], sc["semantic"], sc["keyword"], sc["datatype"], sc["statistical"],
                                   sc["domain"], sc["context"], sc["historical"], c["final_score"], c["rank"],
                                   c["confidence"], c["recommendation"],
                                   {**c["evidence"], "margin": c["margin"], "weights": cfg["weights"]},
                                   clip(reason), c["transformation"], cfg["config_id"],
                                   verdict["model"] if verdict and c["rank"] == 1 else "hybrid-scoring-v1",
                                   knowledge_version, version])

            def write(_event_id: str) -> None:
                session.sql("UPDATE MAPPING.MAPPING_CANDIDATE SET IS_CURRENT = FALSE WHERE RUN_ID = ? AND IS_CURRENT",
                            params=[run_id]).collect()
                insert_rows(session, "MAPPING.MAPPING_CANDIDATE",
                            ["CANDIDATE_ID", "RUN_ID", "SOURCE_COLUMN_ID", "TARGET_COLUMN_ID", "DOMAIN_ID",
                             "SEMANTIC_SCORE", "KEYWORD_SCORE", "DATATYPE_SCORE", "STATISTICAL_SCORE", "DOMAIN_SCORE",
                             "CONTEXT_SCORE", "HISTORICAL_SCORE", "FINAL_SCORE", "RANK", "CONFIDENCE",
                             "RECOMMENDATION", "EVIDENCE_JSON", "GENERATED_REASON", "TRANSFORMATION",
                             "SCORING_CONFIG_ID", "MODEL_VERSION", "KNOWLEDGE_VERSION", "CANDIDATE_VERSION",
                             "IS_CURRENT"],
                            ["?", "?", "?", "?", "?"] + ["?::FLOAT"] * 8 + ["?::NUMBER", "?::FLOAT", "?",
                             "PARSE_JSON(?)", "?", "NULLIF(?, '')", "?", "?", "?", "?::NUMBER", "TRUE"],
                            values)

            covered = {r[0]["target"]["column_name"] for _, r in ranked_by_source if r and r[0]["recommendation"] != "MANUAL"}
            unmapped = [t["column_name"] for t in mappable if t["column_name"] not in covered]
            summary = {"source_columns": len(sources), "targets": len(mappable), "candidates": len(values),
                       "auto_suggest": sum(1 for _, r in ranked_by_source if r and r[0]["recommendation"] == "AUTO_SUGGEST"),
                       "ambiguous": len(ambiguous), "llm_adjudicated": len(llm), "search_calls": search_calls,
                       "unmapped_targets": unmapped, "candidate_version": version}
            record_cost(session, run_id, "MAPPING", model, {}, int((time.time() - started) * 1000),
                        tool_calls=1, search_calls=search_calls)
            call.summary = (f"{summary['candidates']} candidates for {summary['source_columns']} source columns; "
                            f"{summary['auto_suggest']} auto-suggested, {summary['ambiguous']} need review; "
                            f"Cortex Search {search_calls} calls")
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            stage.fail(exc)
            return {"state": stage.payload()}
    stage.move("MAPPING_REVIEW", call.summary, summary, in_transaction=write)
    return {"summary": summary, "state": stage.payload()}


def mapping_status(session, run_id: str) -> Dict[str, Any]:
    """Progress towards the mapping gate: every proposed source column decided, every required target covered."""
    sources = rows(session, """SELECT DISTINCT C.SOURCE_COLUMN_ID, L.COLUMN_NAME
                               FROM MAPPING.MAPPING_CANDIDATE C
                               JOIN SOURCE.LANDING_COLUMN_REGISTRY L ON L.LANDING_COLUMN_ID = C.SOURCE_COLUMN_ID
                               WHERE C.RUN_ID = ? AND C.IS_CURRENT""", [run_id])
    decisions = rows(session, """SELECT SOURCE_COLUMN_ID, DECISION, TARGET_COLUMN_ID FROM MAPPING.MAPPING_DECISION
                                 WHERE RUN_ID = ? AND IS_CURRENT""", [run_id])
    decided = {d["SOURCE_COLUMN_ID"] for d in decisions}
    mapped_targets = {d["TARGET_COLUMN_ID"] for d in decisions if d["DECISION"] != "REJECTED"}
    run = rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])[0]
    missing_required: List[str] = []
    if sources and run["DOMAIN_ID"]:
        for t in features.mappable_targets(target_columns(session, target_table(session, run)["TARGET_TABLE_ID"])):
            if not t["nullable"] and t["target_column_id"] not in mapped_targets:
                missing_required.append(t["column_name"])
    undecided = [s["COLUMN_NAME"] for s in sources if s["SOURCE_COLUMN_ID"] not in decided]
    return {"source_columns": len(sources), "decided": len(sources) - len(undecided), "undecided": undecided,
            "missing_required_targets": missing_required,
            "complete": bool(sources) and not undecided and not missing_required}


def _store_feedback(session, run: Dict[str, Any], targets: Dict[str, Dict[str, Any]],
                    candidates: Dict[str, Dict[str, Any]], prepared: List[tuple]) -> None:
    """Write each decision into domain knowledge so the next run's score and Cortex prompt see it."""
    if not run.get("DOMAIN_ID"):
        return
    names = {r["LANDING_COLUMN_ID"]: r for r in rows(session, """
        SELECT L.LANDING_COLUMN_ID, L.COLUMN_NAME, T.SOURCE_TABLE
          FROM SOURCE.LANDING_COLUMN_REGISTRY L
          JOIN SOURCE.LANDING_TABLE_REGISTRY T ON T.LANDING_ID = L.LANDING_ID
         WHERE L.LANDING_COLUMN_ID IN (""" + ", ".join("?" for _ in prepared) + ")",
        [p[0] for p in prepared])}
    proposed = {}
    rank1 = rows(session, """
        SELECT C.SOURCE_COLUMN_ID, T.COLUMN_NAME
          FROM MAPPING.MAPPING_CANDIDATE C
          JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY T ON T.TARGET_COLUMN_ID = C.TARGET_COLUMN_ID
         WHERE C.RUN_ID = ? AND C.IS_CURRENT AND C.RANK = 1""", [run["RUN_ID"]])
    for r in rank1:
        proposed[r["SOURCE_COLUMN_ID"]] = r["COLUMN_NAME"]
    target_table_name = (next(iter(targets.values()), {}).get("table_name")
                         or (run.get("TARGET_MODEL") or "").split(".")[-1] or "TARGET")
    for source_id, decision, _candidate, target_id, transformation, justification, _comments in prepared:
        src = names.get(source_id) or {}
        target_name = targets.get(target_id, {}).get("column_name") if target_id else None
        item = feedback_pattern(src.get("COLUMN_NAME") or source_id, src.get("SOURCE_TABLE") or "SOURCE",
                                target_name, target_table_name, decision, transformation, justification,
                                proposed.get(source_id))
        if not item["active"]:
            forget(session, run["DOMAIN_ID"], item["source_reference"])
            continue
        remember(session, domain_id=run["DOMAIN_ID"], kind="MAPPING_PATTERN", key=item["source_reference"],
                 title=item["title"], content=item["content"], content_json=item["content_json"],
                 tags=["FEEDBACK", "MAPPING"], origin="MAPPING", run_id=run["RUN_ID"])


def last_per_source(prepared: List[tuple]) -> List[tuple]:
    """One decision per source column (the first tuple item); a later decision in the same payload wins."""
    latest = {p[0]: p for p in prepared}
    return list(latest.values())


def save_mapping_decisions(session, run_id: str, decisions_json: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("MAPPING_REVIEW")
    decisions = json.loads(decisions_json or "[]")
    if isinstance(decisions, dict):
        decisions = [decisions]
    assert isinstance(decisions, list) and decisions, "provide one or more decisions"
    candidates = {c["CANDIDATE_ID"]: c for c in rows(session, """SELECT CANDIDATE_ID, SOURCE_COLUMN_ID, TARGET_COLUMN_ID,
        RECOMMENDATION, TRANSFORMATION FROM MAPPING.MAPPING_CANDIDATE WHERE RUN_ID = ? AND IS_CURRENT""", [run_id])}
    run = stage.run
    valid_targets = {t["target_column_id"]: t for t in target_columns(session, target_table(session, run)["TARGET_TABLE_ID"])}
    current = {d["SOURCE_COLUMN_ID"]: d for d in rows(session, """SELECT SOURCE_COLUMN_ID, TARGET_COLUMN_ID, DECISION,
        VERSION FROM MAPPING.MAPPING_DECISION WHERE RUN_ID = ? AND IS_CURRENT""", [run_id])}

    prepared = []
    for raw in decisions:
        d = {k.lower(): v for k, v in raw.items()}
        decision = str(d.get("decision", "")).upper()
        assert decision in DECISIONS, f"decision must be one of {sorted(DECISIONS)}"
        candidate = candidates.get(d.get("candidate_id") or "")
        source_id = d.get("source_column_id") or (candidate or {}).get("SOURCE_COLUMN_ID")
        assert source_id, "source_column_id or candidate_id is required"
        assert source_id in {c["SOURCE_COLUMN_ID"] for c in candidates.values()}, "source column has no current candidates"
        if candidate:
            assert candidate["SOURCE_COLUMN_ID"] == source_id, "candidate belongs to a different source column"
        justification = (d.get("business_justification") or "").strip() or None
        transformation = (d.get("transformation") or "").strip() or None
        assert_safe_transformation(transformation)
        target_id = None
        if decision == "APPROVED":
            assert candidate, "APPROVED needs candidate_id"
            target_id, transformation = candidate["TARGET_COLUMN_ID"], transformation or candidate["TRANSFORMATION"]
        elif decision == "MODIFIED":
            assert candidate and transformation, "MODIFIED needs candidate_id and a transformation"
            target_id = candidate["TARGET_COLUMN_ID"]
        elif decision == "ALTERNATIVE_TARGET":
            target_id = d.get("target_column_id")
            assert target_id in valid_targets, "ALTERNATIVE_TARGET needs a target_column_id of the target model"
        needs_reason = decision in ("MODIFIED", "ALTERNATIVE_TARGET") or (
            decision == "APPROVED" and candidate["RECOMMENDATION"] != "AUTO_SUGGEST")
        if needs_reason and not justification:
            raise ValueError(f"BUSINESS_JUSTIFICATION is required for a {decision} decision on a mapping that needs "
                             "business interpretation")
        prepared.append((source_id, decision, candidate, target_id, transformation, justification,
                         (d.get("comments") or "").strip() or None))

    prepared = last_per_source(prepared)
    claimed: Dict[str, str] = {d["TARGET_COLUMN_ID"]: s for s, d in current.items()
                               if d["DECISION"] != "REJECTED" and d["TARGET_COLUMN_ID"]}
    for source_id, decision, _, target_id, *_ in prepared:
        claimed = {t: s for t, s in claimed.items() if s != source_id}
        if target_id:
            assert target_id not in claimed, (f"target {valid_targets[target_id]['column_name']} is already mapped "
                                              "from another source column; reject that decision first")
            claimed[target_id] = source_id

    session.sql("BEGIN TRANSACTION").collect()
    try:
        values = []
        for source_id, decision, candidate, target_id, transformation, justification, comments in prepared:
            session.sql("UPDATE MAPPING.MAPPING_DECISION SET IS_CURRENT = FALSE WHERE RUN_ID = ? AND SOURCE_COLUMN_ID = ? "
                        "AND IS_CURRENT", params=[run_id, source_id]).collect()
            version = (current.get(source_id, {}).get("VERSION") or 0) + 1
            values.append([str(uuid.uuid4()), run_id, (candidate or {}).get("CANDIDATE_ID"), source_id, target_id,
                           decision, clip(transformation), clip(justification), clip(comments), version])
        insert_rows(session, "MAPPING.MAPPING_DECISION",
                    ["DECISION_ID", "RUN_ID", "CANDIDATE_ID", "SOURCE_COLUMN_ID", "TARGET_COLUMN_ID", "DECISION",
                     "TRANSFORMATION", "BUSINESS_JUSTIFICATION", "COMMENTS", "REVIEWER", "VERSION", "IS_CURRENT"],
                    ["?", "?", "NULLIF(?, '')", "?", "NULLIF(?, '')", "?", "NULLIF(?, '')", "NULLIF(?, '')",
                     "NULLIF(?, '')", "CURRENT_USER()", "?::NUMBER", "TRUE"], values)
        _store_feedback(session, run, valid_targets, candidates, prepared)
        session.sql("COMMIT").collect()
    except Exception:
        session.sql("ROLLBACK").collect()
        raise
    with tool_call(session, run_id, "save_mapping_decision", {"decisions": len(prepared)}) as call:
        call.summary = ", ".join(f"{p[1]}" for p in prepared)
    return {"saved": len(prepared), "status": mapping_status(session, run_id)}
