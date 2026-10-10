"""Per-stage AI suggestions: what the model is shown, what it may suggest, and what accepting does.

Accepting always stores a rule the deterministic engine reads next time, so the same source never needs the model
again for that decision:
  PROFILING -> COLUMN_RULE knowledge for that exact source column (semantic type, PII, date format)
  DOMAIN    -> the run's knowledge pack
  STTM      -> TRANSFORMATION_RULE (through the existing apply path)
  SODA      -> a client requirement on the run plus SODA_PATTERN knowledge
  DBT       -> the target registry's business-key flag or system semantic type
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from services.common.ai_suggest import Spec, record_decision, suggest
from services.common.sql import rows, variant

_STR = {"type": "string"}
_OPT = {"type": ["string", "null"]}

SPECS: Dict[str, Spec] = {
    "PROFILING": Spec(
        "PROFILING", 1,
        "You review column profiles of one source table. RULE RESULT per column is semantic_type, "
        "pii_classification and date_format, decided from names and value shapes. Suggest corrections: a better "
        "semantic type, PII the rules missed (EMAIL, PHONE, NAME, SSN, CARD, IP_ADDRESS, DATE_OF_BIRTH, ADDRESS, "
        "NATIONAL_ID) or a date format in Snowflake syntax for text dates.",
        {"type": "object", "properties": {"column": _STR, "semantic_type": _OPT, "pii_classification": _OPT,
                                          "date_format": _OPT, "reason": _STR}, "required": ["column", "reason"]},
        lambda i: str(i.get("column")), ["column", "reason"]),
    "DOMAIN": Spec(
        "DOMAIN", 1,
        "You pick the business domain (knowledge pack) for a set of source tables. RULE RESULT is the ranked "
        "candidates with confidence. Suggest one domain_name from AVAILABLE_DOMAINS only, when the evidence "
        "points elsewhere or the rules were not confident.",
        {"type": "object", "properties": {"domain_name": _STR, "reason": _STR}, "required": ["domain_name", "reason"]},
        lambda i: str(i.get("domain_name")).upper(), ["domain_name", "reason"]),
    "STTM": Spec(
        "STTM", 1,
        "You review source-to-target transformations. RULE RESULT per line is the current transformation (null "
        "means a straight copy). Suggest a Snowflake SQL expression over the named source column where a cast, "
        "date parse (use the profile's date format), trim, code decode or null handling is needed. Use only the "
        "source column of that line, written exactly as given.",
        {"type": "object", "properties": {"target_column": _STR, "transformation": _STR, "reason": _STR},
         "required": ["target_column", "transformation", "reason"]},
        lambda i: f"{str(i.get('target_column')).upper()}|{i.get('transformation')}",
        ["target_column", "transformation", "reason"]),
    "SODA": Spec(
        "SODA", 1,
        "You propose data quality checks for a target table. RULE RESULT is the checks already proposed. Add only "
        "checks the profile evidence supports (value sets, ranges, formats, uniqueness, freshness) that are "
        "missing. check_type is one of MISSING, DUPLICATE, VALID_VALUES, RANGE, FORMAT, FRESHNESS, ROW_COUNT.",
        {"type": "object", "properties": {
            "target_column": _OPT, "check_type": _STR, "severity": _STR, "requirement": _STR, "reason": _STR,
            "valid_values": {"type": "array", "items": _STR}, "valid_regex": _OPT, "valid_format": _OPT,
            "valid_min": {"type": ["number", "null"]}, "valid_max": {"type": ["number", "null"]},
            "freshness": _OPT}, "required": ["check_type", "severity", "requirement", "reason"]},
        lambda i: f"{i.get('target_column') or '*'}|{str(i.get('check_type')).upper()}|{i.get('requirement')}",
        ["check_type", "severity", "requirement", "reason"]),
    "DBT": Spec(
        "DBT", 1,
        "You review the column roles the dbt generator will use. RULE RESULT per target column is its role: "
        "BUSINESS_KEY (identifies a record), SURROGATE_KEY, AUDIT_TIMESTAMP, RECORD_SOURCE or ATTRIBUTE. Wrong "
        "business keys silently merge or duplicate records, so suggest a change only with clear evidence "
        "(uniqueness in the profile, names, grain).",
        {"type": "object", "properties": {"target_column": _STR, "role": _STR, "reason": _STR},
         "required": ["target_column", "role", "reason"]},
        lambda i: f"{str(i.get('target_column')).upper()}|{str(i.get('role')).upper()}",
        ["target_column", "role", "reason"]),
}

DBT_ROLES = {"BUSINESS_KEY", "SURROGATE_KEY", "AUDIT_TIMESTAMP", "RECORD_SOURCE", "ATTRIBUTE"}


def _run(session, run_id: str) -> Dict[str, Any]:
    found = rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    assert found, "run not found"
    return found[0]


def _profiles(session, run_id: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for p in rows(session, """SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, SEMANTIC_TYPE, PII_CLASSIFICATION,
                                     CARDINALITY, NULL_PERCENTAGE, DISTINCT_PERCENTAGE, POTENTIAL_KEY_FLAG,
                                     PATTERN_JSON, SAMPLE_VALUES, STATISTICS_JSON
                                FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = ? AND IS_CURRENT
                               ORDER BY TABLE_NAME, COLUMN_NAME""", [run_id]):
        stats = variant(p["STATISTICS_JSON"]) or {}
        pii = p["PII_CLASSIFICATION"] or "NONE"
        out.setdefault(p["TABLE_NAME"], []).append({
            "column": p["COLUMN_NAME"], "data_type": p["DATA_TYPE"], "semantic_type": p["SEMANTIC_TYPE"],
            "pii_classification": pii, "date_format": stats.get("date_format"), "cardinality": p["CARDINALITY"],
            "null_pct": p["NULL_PERCENTAGE"], "distinct_pct": p["DISTINCT_PERCENTAGE"],
            "unique": bool(p["POTENTIAL_KEY_FLAG"]),
            "patterns": [x.get("pattern") for x in (variant(p["PATTERN_JSON"]) or [])[:2]],
            # masked already for PII; never shown for PII columns
            "samples": [] if pii != "NONE" else [x.get("value") for x in (variant(p["SAMPLE_VALUES"]) or [])[:5]],
            "min": None if pii != "NONE" else stats.get("min"), "max": None if pii != "NONE" else stats.get("max"),
        })
    return out


def _current_sttm(session, run_id: str) -> Optional[Dict[str, Any]]:
    found = rows(session, """SELECT * FROM CONTRACT.STTM_REGISTRY WHERE RUN_ID = ?
                              ORDER BY STTM_VERSION DESC LIMIT 1""", [run_id])
    return found[0] if found else None


def _sttm_lines(session, sttm_id: str) -> List[Dict[str, Any]]:
    return rows(session, """SELECT STTM_LINE_ID, TARGET_COLUMN, TARGET_DATATYPE, SOURCE_TABLE, SOURCE_COLUMN,
                                   SOURCE_DATATYPE, MAPPING_TYPE, TRANSFORMATION, NULLABLE_RULE
                              FROM CONTRACT.STTM_LINE WHERE STTM_ID = ? ORDER BY TARGET_COLUMN""", [sttm_id])


def _target(session, run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from services.mapping.procedures import target_table

    try:
        return target_table(session, run)
    except Exception:
        return None


def _target_scope(target: Dict[str, Any], run_id: str) -> str:
    """Scope of a target table; a run without a resolved target keeps its own scope rather than a shared
    target.None.None.None that would mix verdicts across unrelated runs."""
    if not target.get("TARGET_TABLE"):
        return f"run.{run_id}"
    return f"target.{target.get('TARGET_DATABASE')}.{target.get('TARGET_SCHEMA')}.{target.get('TARGET_TABLE')}"


def contexts(session, stage: str, run_id: str) -> List[Tuple[str, Dict[str, Any]]]:
    """(scope_key, context) pairs: one batched call per scope. Scope keys name what the facts are about
    (a source table, a target table), so a verdict carries over to other runs on the same objects."""
    run = _run(session, run_id)
    if stage == "PROFILING":
        out = []
        for table, cols in _profiles(session, run_id).items():
            scope = f"{run.get('SOURCE_SYSTEM_ID') or 'source'}.{table}"
            out.append((scope, {"table": table, "columns": cols}))
        return out
    if stage == "DOMAIN":
        recs = rows(session, """SELECT D.DOMAIN_NAME, R.CONFIDENCE FROM KNOWLEDGE.DOMAIN_RECOMMENDATION R
                                  JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
                                 WHERE R.RUN_ID = ? ORDER BY R.CONFIDENCE DESC LIMIT 5""", [run_id])
        available = [r["DOMAIN_NAME"] for r in rows(session, "SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY "
                                                              "WHERE ACTIVE_FLAG ORDER BY DOMAIN_NAME")]
        tables = {t: [c["column"] for c in cols][:40] for t, cols in _profiles(session, run_id).items()}
        return [(f"run.{run_id}", {"tables": tables, "rule_result": [
            {"domain": r["DOMAIN_NAME"], "confidence": r["CONFIDENCE"]} for r in recs],
            "available_domains": available})]
    if stage in ("STTM", "SODA", "DBT"):
        sttm = _current_sttm(session, run_id)
        assert sttm, "generate the STTM first"
        lines = _sttm_lines(session, sttm["STTM_ID"])
        profiles = {(t.upper(), c["column"].upper()): c for t, cols in _profiles(session, run_id).items() for c in cols}
        target = _target(session, run) or {}
        scope = _target_scope(target, run_id)
        def evidence(line):
            return profiles.get((str(line["SOURCE_TABLE"] or "").upper(), str(line["SOURCE_COLUMN"] or "").upper()))
        if stage == "STTM":
            return [(scope, {"lines": [{
                "target_column": l["TARGET_COLUMN"], "target_type": l["TARGET_DATATYPE"],
                "source": f"{l['SOURCE_TABLE']}.{l['SOURCE_COLUMN']}" if l["SOURCE_COLUMN"] else None,
                "source_column": l["SOURCE_COLUMN"], "source_type": l["SOURCE_DATATYPE"],
                "rule_result": l["TRANSFORMATION"], "profile": evidence(l)} for l in lines if l["SOURCE_COLUMN"]]})]
        if stage == "SODA":
            existing = [{"column": r["TARGET_COLUMN"], "check_type": r["CHECK_TYPE"],
                         "definition": variant(r["CHECK_DEFINITION"])}
                        for r in rows(session, """SELECT TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION
                                                    FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                                   WHERE RUN_ID = ? AND IS_CURRENT""", [run_id])]
            return [(scope, {"columns": [{"target_column": l["TARGET_COLUMN"], "type": l["TARGET_DATATYPE"],
                                          "nullable": l["NULLABLE_RULE"], "profile": evidence(l)} for l in lines],
                             "rule_result": existing})]
        registry = {r["COLUMN_NAME"].upper(): r for r in rows(
            session, """SELECT COLUMN_NAME, IS_BUSINESS_KEY, SEMANTIC_TYPE FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY
                         WHERE TARGET_TABLE_ID = ?""", [target.get("TARGET_TABLE_ID")])} if target else {}
        def role(name):
            r = registry.get(name.upper()) or {}
            if r.get("IS_BUSINESS_KEY"):
                return "BUSINESS_KEY"
            return r.get("SEMANTIC_TYPE") if r.get("SEMANTIC_TYPE") in DBT_ROLES else "ATTRIBUTE"
        design = variant(sttm.get("TABLE_DESIGN")) or {}
        return [(scope, {"grain": design.get("grain"), "business_keys_in_sttm": design.get("business_keys"),
                         "columns": [{"target_column": l["TARGET_COLUMN"], "rule_result": role(l["TARGET_COLUMN"]),
                                      "source": l["SOURCE_COLUMN"], "profile": evidence(l)} for l in lines]})]
    raise AssertionError(f"no suggestions for stage {stage}")


def run_suggestions(session, stage: str, run_id: str, refresh: bool = False, cached_only: bool = False
                    ) -> Dict[str, Any]:
    spec = SPECS[stage]
    scopes = []
    for scope_key, context in contexts(session, stage, run_id):
        result = suggest(session, spec, scope_key, context, run_id, refresh, cached_only)
        scopes.append({"scope_key": scope_key, **result})
    return {"stage": stage, "scopes": scopes,
            "count": sum(len(s["items"]) for s in scopes if s.get("items"))}


def _knowledge(session, domain_id: str, kind: str, title: str, content: Dict[str, Any], ref: str,
               tags: List[str], origin: str = "SUGGESTION", run_id: Optional[str] = None) -> None:
    from services.knowledge.writer import remember

    remember(session, domain_id=domain_id, kind=kind, key=ref, title=title, content=json.dumps(content),
             content_json=content, tags=tags, origin=origin, run_id=run_id or content.get("run_id"))


def _domain_for(session, run: Dict[str, Any]) -> str:
    from services.knowledge.procedures import GENERAL_DOMAIN, ensure_domain

    return run.get("DOMAIN_ID") or ensure_domain(session, GENERAL_DOMAIN)


def _apply(session, stage: str, run: Dict[str, Any], scope_key: str, item: Dict[str, Any]) -> Dict[str, Any]:
    run_id = run["RUN_ID"]
    if stage == "PROFILING":
        table = scope_key.split(".", 1)[1]
        fields = {k: item.get(k) for k in ("semantic_type", "pii_classification", "date_format") if item.get(k)}
        assert fields, "nothing to apply"
        content = {"source_system_id": run.get("SOURCE_SYSTEM_ID"), "source_table": table,
                   "column_name": item["column"], **fields}
        _knowledge(session, _domain_for(session, run), "COLUMN_RULE", f"{table}.{item['column']}", content,
                   f"column.{scope_key}.{item['column']}".upper(), ["AI", "PROFILING", "ACCEPTED"], "PROFILING", run_id)
        sets, params = [], []
        if fields.get("semantic_type"):
            sets.append("SEMANTIC_TYPE = ?"); params.append(fields["semantic_type"].upper())
        if fields.get("pii_classification"):
            sets.append("PII_CLASSIFICATION = ?"); params.append(fields["pii_classification"].upper())
        if fields.get("date_format"):
            sets.append("STATISTICS_JSON = OBJECT_INSERT(COALESCE(STATISTICS_JSON, OBJECT_CONSTRUCT()), 'date_format', ?::VARIANT, TRUE)")
            params.append(fields["date_format"])
        session.sql(f"UPDATE PROFILE.PROFILE_REGISTRY SET {', '.join(sets)} WHERE RUN_ID = ? AND IS_CURRENT "
                    "AND TABLE_NAME = ? AND COLUMN_NAME = ?", params=params + [run_id, table, item["column"]]).collect()
        return {"applied": "column rule stored; this run's profile updated"}
    if stage == "DOMAIN":
        found = rows(session, "SELECT DOMAIN_ID FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = UPPER(?) "
                              "AND ACTIVE_FLAG", [item["domain_name"]])
        assert found, f"unknown domain {item['domain_name']}"
        assert run["CURRENT_STATE"] in ("PROFILING_COMPLETE", "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "FAILED"), \
            "the pack can only be changed before mapping starts"
        session.sql("UPDATE CORE.WORKFLOW_RUN SET DOMAIN_ID = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE RUN_ID = ?",
                    params=[found[0]["DOMAIN_ID"], run_id]).collect()
        return {"applied": f"run uses the {item['domain_name']} pack"}
    if stage == "STTM":
        from services.sttm.procedures import apply_transformation

        apply_transformation(session, run_id, json.dumps({
            "target_column": item["target_column"], "transformation": item["transformation"],
            "rationale": f"AI suggestion accepted: {item['reason']}"}))
        return {"applied": "transformation applied and stored as a rule"}
    if stage == "SODA":
        from services.soda.procedures import import_client_expectations

        row = {k: v for k, v in item.items() if k not in ("reason", "item_key", "review")}
        row["rationale"] = item["reason"]
        import_client_expectations(session, run_id, json.dumps({"rows": [row]}))
        _knowledge(session, _domain_for(session, run), "SODA_PATTERN",
                   f"{item.get('target_column') or 'table'} {item['check_type']}", {**row, "decision": "ACCEPTED"},
                   f"soda.ai.{scope_key}.{item.get('target_column') or '*'}.{item['check_type']}".upper(),
                   ["AI", "SODA", "ACCEPTED"], "SODA", run_id)
        return {"applied": "check added to the run and stored as a pattern"}
    if stage == "DBT":
        role = str(item["role"]).upper()
        assert role in DBT_ROLES, f"role must be one of {sorted(DBT_ROLES)}"
        target = _target(session, run)
        assert target, "the run has no target model"
        is_key = role == "BUSINESS_KEY"
        semantic = role if role in ("SURROGATE_KEY", "AUDIT_TIMESTAMP", "RECORD_SOURCE") else None
        session.sql("""UPDATE KNOWLEDGE.TARGET_COLUMN_REGISTRY
                          SET IS_BUSINESS_KEY = ?::BOOLEAN,
                              SEMANTIC_TYPE = IFF(?::BOOLEAN, NULLIF(?, ''), IFF(SEMANTIC_TYPE IN
                                  ('SURROGATE_KEY', 'AUDIT_TIMESTAMP', 'RECORD_SOURCE'), NULL, SEMANTIC_TYPE))
                        WHERE TARGET_TABLE_ID = ? AND UPPER(COLUMN_NAME) = UPPER(?)""",
                    params=[is_key, bool(semantic), semantic or "", target["TARGET_TABLE_ID"],
                            item["target_column"]]).collect()
        return {"applied": f"{item['target_column']} is now {role.lower().replace('_', ' ')} in the target registry"}
    raise AssertionError(f"no accept action for {stage}")


def decide(session, stage: str, run_id: str, payload_json: str) -> Dict[str, Any]:
    """Accept or reject one suggested item. Accept applies it as a rule; reject keeps it from coming back."""
    payload = json.loads(payload_json or "{}")
    spec = SPECS[stage]
    run = _run(session, run_id)
    item = payload.get("item") or {}
    decision = str(payload.get("decision") or "").upper()
    result: Dict[str, Any] = {}
    if decision == "ACCEPTED":
        result = _apply(session, stage, run, payload["scope_key"], item)
    recorded = record_decision(session, spec, payload.get("suggestion_id") or "", payload["scope_key"], item,
                               decision, run_id, run.get("DOMAIN_ID"), payload.get("note"))
    return {**recorded, **result}
