"""CONTRACT.GENERATE_SODA / IMPORT_CLIENT_EXPECTATIONS / SAVE_SODA_DECISIONS."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from services.common.audit import record_cost, tool_call
from services.common.llm import complete_json
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.knowledge.usage import use_skills, use_stage
from services.knowledge.validate import normalize_content
from services.sttm.assemble import sttm_target_name
from services.quality.backtest import evaluate as evaluate_check, plan as backtest_plan
from services.quality.gx import render_suite
from services.quality.profile_checks import profile_checks
from services.soda.expectations import from_client, from_sttm, merge_checks, render_yaml, without_rejected
from services.soda.decisions import DecisionPayloadError, parse_decision_payload, stored_status
from services.soda.extract import EXTRACT_SCHEMA, extract_prompt, parse_client_document, requirement_from_row
from services.soda.feedback import pattern as feedback_pattern


def _current_sttm(session, run_id: str) -> Dict[str, Any]:
    found = rows(session, """SELECT * FROM CONTRACT.STTM_REGISTRY
                             WHERE RUN_ID = ? AND STATUS IN ('REVIEW', 'APPROVED')
                             ORDER BY STTM_VERSION DESC LIMIT 1""", [run_id])
    assert found, "no current STTM for this run"
    return found[0]


def _lines(session, sttm_id: str) -> List[Dict[str, Any]]:
    return [{
        "target_column": r["TARGET_COLUMN"], "target_datatype": r["TARGET_DATATYPE"],
        "nullable_rule": r["NULLABLE_RULE"], "uniqueness_rule": r["UNIQUENESS_RULE"],
        "accepted_values": variant(r["ACCEPTED_VALUES"]) or [],
        "business_definition": r["BUSINESS_DEFINITION"],
        "transformation": r.get("TRANSFORMATION"),
        "semantic_type": r.get("SEMANTIC_TYPE"),
        "range_rule": variant(r.get("RANGE_RULE")),
        "source_table": r.get("SOURCE_TABLE"), "source_column": r.get("SOURCE_COLUMN"),
        "mapping_type": r.get("MAPPING_TYPE"),
    } for r in rows(session, """
        SELECT L.TARGET_COLUMN, L.TARGET_DATATYPE, L.NULLABLE_RULE, L.UNIQUENESS_RULE,
               L.ACCEPTED_VALUES, L.BUSINESS_DEFINITION, L.TRANSFORMATION, L.RANGE_RULE, C.SEMANTIC_TYPE,
               L.SOURCE_TABLE, L.SOURCE_COLUMN, L.MAPPING_TYPE
          FROM CONTRACT.STTM_LINE L
          JOIN CONTRACT.STTM_REGISTRY S ON S.STTM_ID = L.STTM_ID
          LEFT JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY C
            ON C.TARGET_TABLE_ID = S.TARGET_TABLE_ID AND UPPER(C.COLUMN_NAME) = UPPER(L.TARGET_COLUMN)
         WHERE L.STTM_ID = ?
         ORDER BY L.TARGET_COLUMN
    """, [sttm_id])]


def _profile_docs(session, run_id: str) -> Dict[str, Dict[str, Any]]:
    """Current column profiles of the run's source tables, in the profiler's document shape."""
    docs: Dict[str, Dict[str, Any]] = {}
    for p in rows(session, """SELECT TABLE_NAME, COLUMN_NAME, ROW_COUNT, CARDINALITY, POTENTIAL_KEY_FLAG,
                                     PII_CLASSIFICATION, PATTERN_JSON, STATISTICS_JSON
                                FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = ? AND IS_CURRENT""", [run_id]):
        stats = variant(p["STATISTICS_JSON"]) or {}
        table = str(p["TABLE_NAME"]).upper()
        doc = docs.setdefault(table, {"row_count": p["ROW_COUNT"], "columns": []})
        doc["row_count"] = doc["row_count"] or p["ROW_COUNT"]
        doc["columns"].append({
            "column_name": str(p["COLUMN_NAME"]).upper(), "family": stats.get("family"),
            "cardinality": p["CARDINALITY"], "potential_key": bool(p["POTENTIAL_KEY_FLAG"]),
            "pii_classification": p["PII_CLASSIFICATION"] or "NONE",
            "patterns": variant(p["PATTERN_JSON"]) or [], "statistics": {"row_count": p["ROW_COUNT"], **stats},
        })
    return docs


def _model_spec(session, sttm: Dict[str, Any]) -> Dict[str, Any]:
    try:
        found = rows(session, "SELECT MODEL_SPEC FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE TARGET_TABLE_ID = ?",
                     [sttm.get("TARGET_TABLE_ID")])
        return (variant(found[0]["MODEL_SPEC"]) or {}) if found else {}
    except Exception:
        return {}


def _driving_table(design: Dict[str, Any], lines: List[Dict[str, Any]]) -> str | None:
    driving = (design.get("join_graph") or {}).get("driving_table")
    if driving:
        return str(driving).upper()
    counts: Dict[str, int] = {}
    for line in lines:
        if line.get("source_table"):
            key = str(line["source_table"]).upper()
            counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _rejected(session, domain_id: str) -> List[Dict[str, Any]]:
    found = rows(session, """SELECT CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                             WHERE IS_CURRENT AND KNOWLEDGE_TYPE = 'SODA_PATTERN'
                               AND DOMAIN_ID = ?""", [domain_id])
    out = []
    for row in found:
        payload = variant(row["CONTENT_JSON"]) or {}
        if str(payload.get("decision") or "").upper() == "REJECTED":
            out.append(payload)
    return out


def _knowledge(session, domain_id: str, run_id: Optional[str] = None) -> List[str]:
    found = rows(session, """SELECT KNOWLEDGE_ID, TITLE, CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                             WHERE IS_CURRENT AND KNOWLEDGE_TYPE IN
                                   ('SODA_PATTERN', 'BUSINESS_RULE', 'EXCEPTION', 'TRANSFORMATION_RULE', 'STTM_TEMPLATE')
                               AND DOMAIN_ID = ?
                             ORDER BY UPDATED_AT DESC NULLS LAST LIMIT 16""", [domain_id])
    if run_id:
        from services.knowledge.writer import record_usage

        record_usage(session, run_id, "SODA", [r["KNOWLEDGE_ID"] for r in found])
    return [f"{r['TITLE']}: {r['CONTENT']}" for r in found]


def _transform_checks(session, domain_id: str, table: str) -> List[Dict[str, Any]]:
    found = rows(session, """SELECT CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                             WHERE IS_CURRENT AND STATUS = 'ACTIVE' AND KNOWLEDGE_TYPE = 'TRANSFORMATION_RULE'
                               AND DOMAIN_ID = ?""", [domain_id])
    out = []
    for row in found:
        content = normalize_content("TRANSFORMATION_RULE", variant(row["CONTENT_JSON"])) or {}
        if content.get("target_table") and str(content["target_table"]).upper() != str(table).upper():
            continue  # a rule learned on another target table
        target = content.get("target_column")
        for raw in content.get("soda_checks") or []:
            item = dict(raw)
            item.setdefault("target_column", target)
            item["origin"] = "TRANSFORM"
            out.append(requirement_from_row(table, item))
    return out


def _briefs(session, run_id: str) -> List[str]:
    found = rows(session, """SELECT CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                             WHERE IS_CURRENT AND SOURCE_REFERENCE = ?""", [f"soda.brief.{run_id}"])
    return [r["CONTENT"] for r in found if r.get("CONTENT")]


def _store_brief(session, run_id: str, domain_id: str, brief: str, filename: str) -> None:
    from services.knowledge.writer import remember

    remember(session, domain_id=domain_id, kind="BUSINESS_RULE", key=f"soda.brief.{run_id}",
             title=f"Client Soda brief {filename or run_id}", content=brief,
             content_json={"run_id": run_id, "filename": filename}, tags=["CLIENT", "SODA", "BRIEF"],
             origin="SODA", run_id=run_id, by_domain=False)


def _extract(session, brief: str, table: str, columns: List[str], knowledge: List[str],
             run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        use_skills(session, ["SODA_SKILL"], run_id=run_id)
    except Exception:
        pass
    started = time.time()
    output, usage, model = complete_json(session, extract_prompt(brief, table, columns, knowledge), EXTRACT_SCHEMA, stage="SODA")
    if run_id:
        record_cost(session, run_id, "SODA", model, usage, int((time.time() - started) * 1000), tool_calls=1)
    return [requirement_from_row(table, {**r, "origin": "AI"}) for r in output.get("requirements") or []]


def _store(session, run_id: str, sttm: Dict[str, Any], checks: List[Dict[str, Any]]) -> int:
    version = (scalar(session, "SELECT MAX(VERSION) FROM CONTRACT.SODA_EXPECTATION_REGISTRY WHERE RUN_ID = ?",
                      [run_id]) or 0) + 1
    session.sql("UPDATE CONTRACT.SODA_EXPECTATION_REGISTRY SET IS_CURRENT = FALSE, STATUS = 'SUPERSEDED' "
                "WHERE RUN_ID = ? AND IS_CURRENT", params=[run_id]).collect()
    insert_rows(session, "CONTRACT.SODA_EXPECTATION_REGISTRY",
                ["EXPECTATION_ID", "RUN_ID", "STTM_ID", "DOMAIN_ID", "TARGET_TABLE", "TARGET_COLUMN", "CHECK_TYPE",
                 "CHECK_DEFINITION", "SEVERITY", "ORIGIN", "CLIENT_REQUIREMENT", "STATUS", "VERSION", "IS_CURRENT",
                 "CREATED_BY"],
                ["?", "?", "?", "?", "?", "NULLIF(?, '')", "?", "PARSE_JSON(?)", "?", "?", "NULLIF(?, '')",
                 "'PROPOSED'", "?::NUMBER", "TRUE", "CURRENT_USER()"],
                [[str(uuid.uuid4()), run_id, sttm["STTM_ID"], sttm["DOMAIN_ID"], c["target_table"],
                  c.get("target_column"), c["check_type"], c["definition"], c["severity"], c["origin"],
                  clip(c.get("requirement")), version] for c in checks])
    return version


def generate_soda(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
                  "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING",
                  "VALIDATION_PASSED", "VALIDATION_FAILED", "DBT_REVIEW")
    if stage.state in ("STTM_APPROVED", "SODA_PENDING"):
        stage.walk(["STTM_APPROVED", "SODA_PENDING"], "Soda generation started")
    version = 0
    with tool_call(session, run_id, "generate_soda", {"run_id": run_id}) as call:
        try:
            try:
                use_stage(session, "SODA", run_id=run_id)
            except Exception:
                pass
            sttm = _current_sttm(session, run_id)
            design = variant(sttm["TABLE_DESIGN"]) or {}
            table = sttm_target_name(session, sttm)
            lines = _lines(session, sttm["STTM_ID"])
            columns = [l["target_column"] for l in lines]
            knowledge = _knowledge(session, sttm["DOMAIN_ID"], run_id)
            checks = from_sttm(table, lines, design.get("business_keys") or [])
            stored = _transform_checks(session, sttm["DOMAIN_ID"], table)
            extracted: List[Dict[str, Any]] = []
            for brief in _briefs(session, run_id):
                try:
                    extracted.extend(_extract(session, brief, table, columns, knowledge, run_id))
                except Exception:
                    continue
            existing_client = rows(session, """SELECT TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION,
                                               SEVERITY, CLIENT_REQUIREMENT, ORIGIN FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                               WHERE RUN_ID = ? AND ORIGIN IN ('CLIENT', 'AI') AND STATUS <> 'REJECTED'
                                               QUALIFY ROW_NUMBER() OVER (PARTITION BY CHECK_TYPE, TARGET_COLUMN
                                                                          ORDER BY VERSION DESC) = 1""", [run_id])
            imported = [{"target_table": r["TARGET_TABLE"], "target_column": r["TARGET_COLUMN"],
                         "check_type": r["CHECK_TYPE"], "definition": variant(r["CHECK_DEFINITION"]),
                         "severity": r["SEVERITY"], "origin": r["ORIGIN"],
                         "requirement": r["CLIENT_REQUIREMENT"]} for r in existing_client]
            try:
                profiled = profile_checks(table, lines, _profile_docs(session, run_id), checks,
                                          _model_spec(session, sttm), _driving_table(design, lines))
            except Exception:
                profiled = []
            checks = without_rejected(merge_checks(checks, stored, extracted, imported, profiled),
                                     _rejected(session, sttm["DOMAIN_ID"]))
            yaml_text = render_yaml(table.lower(), checks)

            def write(_event_id: str) -> None:
                nonlocal version
                version = _store(session, run_id, sttm, checks)

            call.summary = f"{len(checks)} Soda expectations"
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            stage.fail(exc)
            return {"state": stage.payload()}
    if stage.state == "SODA_PENDING":
        stage.move("SODA_REVIEW", call.summary, {"count": len(checks)}, in_transaction=write)
    else:
        write("")
    return {"version": version, "count": len(checks), "yaml": yaml_text, "checks": checks,
            "gx_suite": render_suite(table, checks), "state": stage.payload()}


def backtest_soda(session, run_id: str) -> Dict[str, Any]:
    """Evaluate the run's current checks against today's source data (caller's role) and keep the result on each
    expectation, so a reviewer accepts thresholds that the data actually meets."""
    sttm = _current_sttm(session, run_id)
    design = variant(sttm["TABLE_DESIGN"]) or {}
    lines = _lines(session, sttm["STTM_ID"])
    stored = rows(session, """SELECT EXPECTATION_ID, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION, SEVERITY
                                FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                               WHERE RUN_ID = ? AND IS_CURRENT AND STATUS <> 'REJECTED'""", [run_id])
    checks = [{"expectation_id": r["EXPECTATION_ID"], "target_column": r["TARGET_COLUMN"],
               "check_type": r["CHECK_TYPE"], "definition": variant(r["CHECK_DEFINITION"]) or {},
               "severity": r["SEVERITY"]} for r in stored]
    landed = rows(session, """SELECT SOURCE_TABLE, LANDING_DATABASE, LANDING_SCHEMA, LANDING_TABLE
                                FROM SOURCE.LANDING_TABLE_REGISTRY
                               WHERE RUN_ID = ? AND INGESTION_STATUS = 'COMPLETE'
                             QUALIFY ROW_NUMBER() OVER (PARTITION BY SOURCE_TABLE ORDER BY CREATED_AT DESC) = 1""",
                  [run_id])
    from services.source.identifiers import sql_ident

    sources = {str(r["SOURCE_TABLE"]).upper(): ".".join(sql_ident(str(r[k])) for k in ("LANDING_DATABASE", "LANDING_SCHEMA", "LANDING_TABLE"))
               for r in landed}
    queries, slots = backtest_plan(checks, lines, sources, _driving_table(design, lines))
    results: Dict[str, Any] = {}
    for table, sql in queries.items():
        try:
            found = rows(session, sql)
            results[table] = found[0] if found else None
        except Exception as exc:
            results[table] = None
            for slot in slots:
                if slot.get("table") == table:
                    slot["reason"] = f"query failed: {clip(exc, 200)}"
    outcome = []
    for slot in slots:
        check = checks[slot["index"]]
        result = {**evaluate_check(check, slot, results.get(slot.get("table"))),
                  "expectation_id": check["expectation_id"]}
        outcome.append(result)
        session.sql("""UPDATE CONTRACT.SODA_EXPECTATION_REGISTRY
                          SET CHECK_DEFINITION = OBJECT_INSERT(CHECK_DEFINITION, 'backtest', PARSE_JSON(?), TRUE)
                        WHERE EXPECTATION_ID = ? AND RUN_ID = ?""",
                    params=[json.dumps({k: result.get(k) for k in ("status", "observed", "percent", "detail")}),
                            check["expectation_id"], run_id]).collect()
    summary = {s: sum(1 for r in outcome if r["status"] == s) for s in ("PASS", "FAIL", "NOT_EVALUATED")}
    return {"results": outcome, "summary": summary, "queries": list(queries.values())}


def import_client_expectations(session, run_id: str, rows_json: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
                  "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "DBT_REVIEW")
    parsed = json.loads(rows_json or "[]")
    if isinstance(parsed, str):
        parsed = parse_client_document(parsed)
    elif isinstance(parsed, dict) and parsed.get("text"):
        parsed = parse_client_document(parsed["text"], parsed.get("filename") or "")
    elif isinstance(parsed, dict) and not (parsed.get("brief") or parsed.get("rows")):
        parsed = parse_client_document(json.dumps(parsed))
    elif isinstance(parsed, list):
        parsed = {"rows": parsed}
    assert isinstance(parsed, dict) and (parsed.get("brief") or parsed.get("rows")), \
        "provide a client brief or a JSON/CSV array of requirement rows"
    sttm = _current_sttm(session, run_id)
    design = variant(sttm["TABLE_DESIGN"]) or {}
    table = sttm_target_name(session, sttm)
    columns = [l["target_column"] for l in _lines(session, sttm["STTM_ID"])]
    imported: List[Dict[str, Any]] = []
    if parsed.get("brief"):
        _store_brief(session, run_id, sttm["DOMAIN_ID"], parsed["brief"], parsed.get("filename") or "")
        imported = _extract(session, parsed["brief"], table, columns, _knowledge(session, sttm["DOMAIN_ID"], run_id), run_id)
    if parsed.get("rows"):
        imported = merge_checks(imported, from_client(table, parsed["rows"]))
    assert imported, "no Soda requirements could be extracted from the client brief"
    version = (scalar(session, "SELECT MAX(VERSION) FROM CONTRACT.SODA_EXPECTATION_REGISTRY WHERE RUN_ID = ?",
                      [run_id]) or 0) + 1
    insert_rows(session, "CONTRACT.SODA_EXPECTATION_REGISTRY",
                ["EXPECTATION_ID", "RUN_ID", "STTM_ID", "DOMAIN_ID", "TARGET_TABLE", "TARGET_COLUMN", "CHECK_TYPE",
                 "CHECK_DEFINITION", "SEVERITY", "ORIGIN", "CLIENT_REQUIREMENT", "STATUS", "VERSION", "IS_CURRENT",
                 "CREATED_BY"],
                ["?", "?", "?", "?", "?", "NULLIF(?, '')", "?", "PARSE_JSON(?)", "?", "?", "NULLIF(?, '')",
                 "'PROPOSED'", "?::NUMBER", "TRUE", "CURRENT_USER()"],
                [[str(uuid.uuid4()), run_id, sttm["STTM_ID"], sttm["DOMAIN_ID"], c["target_table"],
                  c.get("target_column"), c["check_type"], c["definition"], c["severity"], c.get("origin") or "CLIENT",
                  clip(c.get("requirement")), version] for c in imported])
    return {"imported": len(imported), "version": version, "yaml": render_yaml(table.lower(), imported)}


def _store_feedback(session, domain_id: str, item: Dict[str, Any], run_id: Optional[str] = None) -> None:
    if not domain_id:
        return
    from services.knowledge.writer import remember

    active = item.get("active", True)
    # a rejection stays current (generation reads it to avoid proposing the check again) but is not ACTIVE, so
    # knowledge search and the copilot never present it as a rule; DRAFT, not RETIRED, because restoring a domain
    # re-activates its RETIRED rows
    remember(session, domain_id=domain_id, kind="SODA_PATTERN", key=item["source_reference"], title=item["title"],
             content=item["content"], content_json=item["content_json"],
             tags=["FEEDBACK", "SODA"] + ([] if active else ["REJECTED"]), origin="SODA",
             run_id=run_id or (item.get("content_json") or {}).get("run_id"), status=None if active else "DRAFT")


def store_approved_set(session, run_id: str) -> None:
    """When the data quality gate is approved, keep the whole approved check set as one SODA_PATTERN knowledge
    item for the target table, so the next run on this model starts from what was agreed."""
    found = rows(session, """SELECT TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION, SEVERITY,
                                    CLIENT_REQUIREMENT, DOMAIN_ID
                               FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                              WHERE RUN_ID = ? AND IS_CURRENT AND STATUS = 'APPROVED'""", [run_id])
    if not found or not found[0]["DOMAIN_ID"]:
        return
    table = found[0]["TARGET_TABLE"]
    checks = [{"target_column": r["TARGET_COLUMN"], "check_type": r["CHECK_TYPE"],
               "definition": variant(r["CHECK_DEFINITION"]) or {}, "severity": r["SEVERITY"],
               "requirement": r["CLIENT_REQUIREMENT"]} for r in found]
    summary = "; ".join(f"{c['check_type'].lower()} on {c['target_column'] or 'table'}" for c in checks[:40])
    _store_feedback(session, found[0]["DOMAIN_ID"], {
        "source_reference": f"soda.checkset.{table}".upper(),
        "title": f"Approved data quality checks for {table}"[:500],
        "content": f"{len(checks)} approved checks for {table}: {summary}.",
        "content_json": {"target_table": table, "decision": "APPROVED", "run_id": run_id, "checks": checks},
        "active": True,
    })


def save_soda_decisions(session, run_id: str, decisions_json: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("SODA_REVIEW", "STTM_APPROVED", "SODA_PENDING", "SODA_APPROVED",
                  "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING",
                  "VALIDATION_PASSED", "VALIDATION_FAILED", "DBT_REVIEW")
    try:
        decisions = parse_decision_payload(decisions_json)
    except DecisionPayloadError as exc:
        raise AssertionError(str(exc)) from exc
    sttm = _current_sttm(session, run_id)
    applied = 0
    skipped: List[Dict[str, str]] = []
    for raw in decisions:
        expectation_id = raw["expectation_id"]
        decision = raw["decision"]
        found = rows(session, """SELECT * FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                 WHERE EXPECTATION_ID = ? AND RUN_ID = ? AND IS_CURRENT""",
                     [expectation_id, run_id])
        if not found:
            raise AssertionError(f"unknown expectation {expectation_id}")
        row = found[0]
        target = stored_status(decision)
        if row["STATUS"] == target and decision != "MODIFIED":
            skipped.append({"expectation_id": expectation_id, "reason": "already_decided"})
            continue
        definition = variant(raw.get("definition")) or variant(row["CHECK_DEFINITION"]) or {}
        requirement = raw.get("requirement") or row["CLIENT_REQUIREMENT"]
        severity = (raw.get("severity") if decision == "MODIFIED" else None) or row["SEVERITY"]
        session.sql("""UPDATE CONTRACT.SODA_EXPECTATION_REGISTRY
                          SET STATUS = ?, CHECK_DEFINITION = PARSE_JSON(?), CLIENT_REQUIREMENT = NULLIF(?, ''), SEVERITY = ?,
                              REVIEWED_BY = CURRENT_USER(), REVIEWED_AT = CURRENT_TIMESTAMP()
                        WHERE EXPECTATION_ID = ? AND RUN_ID = ? AND IS_CURRENT""",
                    params=[target, json.dumps(definition), clip(requirement), severity, expectation_id, run_id]).collect()
        _store_feedback(session, sttm["DOMAIN_ID"], feedback_pattern(
            row["TARGET_TABLE"], row["TARGET_COLUMN"], row["CHECK_TYPE"], decision,
            requirement, raw.get("justification"), definition))
        applied += 1
    remaining = rows(session, """SELECT EXPECTATION_ID FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                 WHERE RUN_ID = ? AND IS_CURRENT AND STATUS = 'PROPOSED'""", [run_id])
    return {"decided": applied, "skipped": skipped,
            "remaining": [r["EXPECTATION_ID"] for r in remaining], "complete": not remaining}
