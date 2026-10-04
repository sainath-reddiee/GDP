"""CONTRACT.GENERATE_SODA / IMPORT_CLIENT_EXPECTATIONS / SAVE_SODA_DECISIONS."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from services.common.audit import tool_call
from services.common.llm import complete_json
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.knowledge.usage import STAGE_SKILLS, use_skills
from services.soda.expectations import from_client, from_sttm, merge_checks, render_yaml, without_rejected
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
        "semantic_type": r.get("SEMANTIC_TYPE"),
        "range_rule": variant(r.get("RANGE_RULE")),
    } for r in rows(session, """
        SELECT L.TARGET_COLUMN, L.TARGET_DATATYPE, L.NULLABLE_RULE, L.UNIQUENESS_RULE,
               L.ACCEPTED_VALUES, L.BUSINESS_DEFINITION, L.RANGE_RULE, C.SEMANTIC_TYPE
          FROM CONTRACT.STTM_LINE L
          JOIN CONTRACT.STTM_REGISTRY S ON S.STTM_ID = L.STTM_ID
          LEFT JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY C
            ON C.TARGET_TABLE_ID = S.TARGET_TABLE_ID AND UPPER(C.COLUMN_NAME) = UPPER(L.TARGET_COLUMN)
         WHERE L.STTM_ID = ?
         ORDER BY L.TARGET_COLUMN
    """, [sttm_id])]


def _rejected(session, domain_id: str) -> List[Dict[str, Any]]:
    found = rows(session, """SELECT CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                             WHERE IS_CURRENT AND KNOWLEDGE_TYPE = 'SODA_PATTERN'
                               AND (DOMAIN_ID = ? OR SOURCE_REFERENCE LIKE 'SODA.%')""", [domain_id])
    out = []
    for row in found:
        payload = variant(row["CONTENT_JSON"]) or {}
        if str(payload.get("decision") or "").upper() == "REJECTED":
            out.append(payload)
    return out


def _knowledge(session, domain_id: str) -> List[str]:
    found = rows(session, """SELECT TITLE, CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                             WHERE IS_CURRENT AND KNOWLEDGE_TYPE IN ('SODA_PATTERN', 'BUSINESS_RULE', 'EXCEPTION')
                               AND (DOMAIN_ID = ? OR SOURCE_REFERENCE LIKE 'SODA.%' OR SOURCE_REFERENCE LIKE 'soda.brief.%')
                             ORDER BY UPDATED_AT DESC NULLS LAST LIMIT 12""", [domain_id])
    return [f"{r['TITLE']}: {r['CONTENT']}" for r in found]


def _briefs(session, run_id: str) -> List[str]:
    found = rows(session, """SELECT CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                             WHERE IS_CURRENT AND SOURCE_REFERENCE = ?""", [f"soda.brief.{run_id}"])
    return [r["CONTENT"] for r in found if r.get("CONTENT")]


def _store_brief(session, run_id: str, domain_id: str, brief: str, filename: str) -> None:
    ref = f"soda.brief.{run_id}"
    session.sql("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, STATUS = 'RETIRED'
                   WHERE SOURCE_REFERENCE = ? AND IS_CURRENT""", params=[ref]).collect()
    version = (scalar(session, "SELECT MAX(VERSION) FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = ?",
                      [ref]) or 0) + 1
    insert_rows(session, "KNOWLEDGE.DOMAIN_KNOWLEDGE",
                ["KNOWLEDGE_ID", "DOMAIN_ID", "KNOWLEDGE_TYPE", "TITLE", "CONTENT", "CONTENT_JSON",
                 "TAGS", "SOURCE_REFERENCE", "STATUS", "VERSION", "IS_CURRENT", "CREATED_BY"],
                ["?", "?", "'BUSINESS_RULE'", "?", "?", "PARSE_JSON(?)", "PARSE_JSON(?)", "?",
                 "'ACTIVE'", "?::NUMBER", "TRUE", "CURRENT_USER()"],
                [[str(uuid.uuid4()), domain_id, f"Client Soda brief {filename or run_id}"[:500],
                  clip(brief, 8000), {"run_id": run_id, "filename": filename},
                  ["CLIENT", "SODA", "BRIEF"], ref, version]])


def _extract(session, brief: str, table: str, columns: List[str], knowledge: List[str]) -> List[Dict[str, Any]]:
    try:
        use_skills(session, ["SODA_SKILL"])
    except Exception:
        pass
    output, _, _ = complete_json(session, extract_prompt(brief, table, columns, knowledge), EXTRACT_SCHEMA)
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
    stage.require("STTM_APPROVED", "SODA_PENDING")
    stage.walk(["STTM_APPROVED", "SODA_PENDING"], "Soda generation started")
    version = 0
    with tool_call(session, run_id, "generate_soda", {"run_id": run_id}) as call:
        try:
            try:
                use_skills(session, STAGE_SKILLS["SODA"])
            except Exception:
                pass
            sttm = _current_sttm(session, run_id)
            design = variant(sttm["TABLE_DESIGN"]) or {}
            table = design.get("target_table") or "DIM_CUSTOMER"
            lines = _lines(session, sttm["STTM_ID"])
            columns = [l["target_column"] for l in lines]
            knowledge = _knowledge(session, sttm["DOMAIN_ID"])
            checks = from_sttm(table, lines, design.get("business_keys") or [])
            extracted: List[Dict[str, Any]] = []
            for brief in _briefs(session, run_id):
                try:
                    extracted.extend(_extract(session, brief, table, columns, knowledge))
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
            checks = without_rejected(merge_checks(checks, extracted, imported),
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
    stage.move("SODA_REVIEW", call.summary, {"count": len(checks)}, in_transaction=write)
    return {"version": version, "count": len(checks), "yaml": yaml_text, "checks": checks, "state": stage.payload()}


def import_client_expectations(session, run_id: str, rows_json: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW")
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
    table = design.get("target_table") or "DIM_CUSTOMER"
    columns = [l["target_column"] for l in _lines(session, sttm["STTM_ID"])]
    imported: List[Dict[str, Any]] = []
    if parsed.get("brief"):
        _store_brief(session, run_id, sttm["DOMAIN_ID"], parsed["brief"], parsed.get("filename") or "")
        imported = _extract(session, parsed["brief"], table, columns, _knowledge(session, sttm["DOMAIN_ID"]))
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


def _store_feedback(session, domain_id: str, item: Dict[str, Any]) -> None:
    if not domain_id:
        return
    session.sql("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, STATUS = 'RETIRED'
                   WHERE DOMAIN_ID = ? AND SOURCE_REFERENCE = ? AND IS_CURRENT""",
                params=[domain_id, item["source_reference"]]).collect()
    version = (scalar(session, """SELECT MAX(VERSION) FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                                  WHERE DOMAIN_ID = ? AND SOURCE_REFERENCE = ?""",
                      [domain_id, item["source_reference"]]) or 0) + 1
    insert_rows(session, "KNOWLEDGE.DOMAIN_KNOWLEDGE",
                ["KNOWLEDGE_ID", "DOMAIN_ID", "KNOWLEDGE_TYPE", "TITLE", "CONTENT", "CONTENT_JSON",
                 "TAGS", "SOURCE_REFERENCE", "STATUS", "VERSION", "IS_CURRENT", "CREATED_BY"],
                ["?", "?", "'SODA_PATTERN'", "?", "?", "PARSE_JSON(?)", "PARSE_JSON(?)", "?",
                 "'ACTIVE'", "?::NUMBER", "TRUE", "CURRENT_USER()"],
                [[str(uuid.uuid4()), domain_id, item["title"], item["content"], item["content_json"],
                  ["FEEDBACK", "SODA"], item["source_reference"], version]])


def save_soda_decisions(session, run_id: str, decisions_json: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("SODA_REVIEW")
    decisions = json.loads(decisions_json or "[]")
    if isinstance(decisions, dict):
        decisions = [decisions]
    assert isinstance(decisions, list) and decisions, "provide at least one Soda decision"
    sttm = _current_sttm(session, run_id)
    for raw in decisions:
        expectation_id = raw.get("expectation_id")
        decision = str(raw.get("decision") or "").upper()
        assert expectation_id and decision in {"APPROVED", "REJECTED", "MODIFIED"}, \
            "each decision needs expectation_id and APPROVED | REJECTED | MODIFIED"
        found = rows(session, """SELECT * FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                 WHERE EXPECTATION_ID = ? AND RUN_ID = ? AND IS_CURRENT""",
                     [expectation_id, run_id])
        assert found, f"unknown expectation {expectation_id}"
        row = found[0]
        definition = variant(raw.get("definition")) or variant(row["CHECK_DEFINITION"]) or {}
        requirement = raw.get("requirement") or row["CLIENT_REQUIREMENT"]
        session.sql("""UPDATE CONTRACT.SODA_EXPECTATION_REGISTRY
                          SET STATUS = ?, CHECK_DEFINITION = PARSE_JSON(?), CLIENT_REQUIREMENT = NULLIF(?, ''),
                              REVIEWED_BY = CURRENT_USER(), REVIEWED_AT = CURRENT_TIMESTAMP()
                        WHERE EXPECTATION_ID = ?""",
                    params=[decision if decision != "MODIFIED" else "APPROVED",
                            json.dumps(definition), clip(requirement), expectation_id]).collect()
        _store_feedback(session, sttm["DOMAIN_ID"], feedback_pattern(
            row["TARGET_TABLE"], row["TARGET_COLUMN"], row["CHECK_TYPE"], decision,
            requirement, raw.get("justification"), definition))
    remaining = rows(session, """SELECT EXPECTATION_ID FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                 WHERE RUN_ID = ? AND IS_CURRENT AND STATUS = 'PROPOSED'""", [run_id])
    return {"decided": len(decisions), "remaining": [r["EXPECTATION_ID"] for r in remaining],
            "complete": not remaining}
