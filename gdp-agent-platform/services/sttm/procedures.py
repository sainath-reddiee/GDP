"""CONTRACT.GENERATE_STTM: MAPPING_APPROVED -> STTM_PENDING -> STTM_REVIEW."""

from __future__ import annotations

import uuid
from typing import Any, Dict

from services.common.audit import tool_call
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.knowledge.procedures import current_knowledge_version
from services.knowledge.usage import STAGE_SKILLS, use_skills
from services.mapping.procedures import target_columns, target_table
from services.sttm.assemble import assemble


def generate_sttm(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("MAPPING_APPROVED", "STTM_PENDING")
    stage.walk(["MAPPING_APPROVED", "STTM_PENDING"], "STTM generation started")
    with tool_call(session, run_id, "generate_sttm", {"run_id": run_id}) as call:
        try:
            use_skills(session, STAGE_SKILLS["STTM"])
            run = stage.run
            target = target_table(session, run)
            columns = target_columns(session, target["TARGET_TABLE_ID"])
            source = rows(session, "SELECT SOURCE_SYSTEM_NAME FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = ?",
                          [run["SOURCE_SYSTEM_ID"]])
            source_name = source[0]["SOURCE_SYSTEM_NAME"] if source else "SOURCE"
            decisions = {}
            for d in rows(session, """
                SELECT D.DECISION_ID, D.TARGET_COLUMN_ID, D.TRANSFORMATION, D.BUSINESS_JUSTIFICATION, D.REVIEWER,
                       C.FINAL_SCORE, L.COLUMN_NAME AS SOURCE_COLUMN, L.DATA_TYPE AS SOURCE_DATATYPE,
                       T.SOURCE_TABLE, T.SOURCE_DATABASE, T.SOURCE_SCHEMA
                  FROM MAPPING.MAPPING_DECISION D
                  JOIN SOURCE.LANDING_COLUMN_REGISTRY L ON L.LANDING_COLUMN_ID = D.SOURCE_COLUMN_ID
                  JOIN SOURCE.LANDING_TABLE_REGISTRY T ON T.LANDING_ID = L.LANDING_ID
                  LEFT JOIN MAPPING.MAPPING_CANDIDATE C ON C.CANDIDATE_ID = D.CANDIDATE_ID
                 WHERE D.RUN_ID = ? AND D.IS_CURRENT AND D.DECISION <> 'REJECTED' AND D.TARGET_COLUMN_ID IS NOT NULL
                """, [run_id]):
                decisions[d["TARGET_COLUMN_ID"]] = {
                    "decision_id": d["DECISION_ID"], "transformation": d["TRANSFORMATION"],
                    "business_justification": d["BUSINESS_JUSTIFICATION"], "reviewer": d["REVIEWER"],
                    "confidence": d["FINAL_SCORE"], "source_column": d["SOURCE_COLUMN"],
                    "source_datatype": d["SOURCE_DATATYPE"], "source_table": d["SOURCE_TABLE"],
                    "source_database": d["SOURCE_DATABASE"], "source_schema": d["SOURCE_SCHEMA"],
                    "source_system": source_name,
                }
            contract = assemble(
                {"grain": target["GRAIN"], "scd_type": target["SCD_TYPE"], "business_keys": variant(target["BUSINESS_KEYS"]),
                 "target_database": target["TARGET_DATABASE"], "target_schema": target["TARGET_SCHEMA"],
                 "target_table": target["TARGET_TABLE"], "table_name": target["TARGET_TABLE"]},
                columns, decisions, source_name, run["SOURCE_DATABASE"], run["SOURCE_SCHEMA"],
            )
            if contract["unmapped_required"]:
                raise ValueError("STTM_INCOMPLETE: required columns have no approved mapping: "
                                 + ", ".join(contract["unmapped_required"]))
            version = (scalar(session, "SELECT MAX(STTM_VERSION) FROM CONTRACT.STTM_REGISTRY WHERE RUN_ID = ?",
                              [run_id]) or 0) + 1
            sttm_id = str(uuid.uuid4())
            kv = current_knowledge_version(session, run["DOMAIN_ID"])

            def write(_event_id: str) -> None:
                session.sql("UPDATE CONTRACT.STTM_REGISTRY SET STATUS = 'SUPERSEDED' WHERE RUN_ID = ? AND STATUS IN ('DRAFT','REVIEW')",
                            params=[run_id]).collect()
                insert_rows(session, "CONTRACT.STTM_REGISTRY",
                            ["STTM_ID", "RUN_ID", "DOMAIN_ID", "TARGET_TABLE_ID", "STTM_VERSION", "STATUS",
                             "TABLE_DESIGN", "KNOWLEDGE_VERSION", "SKILL_VERSION", "GENERATION_VERSION", "CREATED_BY"],
                            ["?", "?", "?", "?", "?::NUMBER", "'REVIEW'", "PARSE_JSON(?)", "?", "'STTM_SKILL:1.0.0'",
                             "'sttm-v1'", "CURRENT_USER()"],
                            [[sttm_id, run_id, run["DOMAIN_ID"], target["TARGET_TABLE_ID"], version,
                              contract["table_design"], kv]])
                insert_rows(session, "CONTRACT.STTM_LINE",
                            ["STTM_LINE_ID", "STTM_ID", "DECISION_ID", "SOURCE_SYSTEM", "SOURCE_DATABASE",
                             "SOURCE_SCHEMA", "SOURCE_TABLE", "SOURCE_COLUMN", "SOURCE_DATATYPE",
                             "TARGET_DOMAIN", "TARGET_DATABASE", "TARGET_SCHEMA", "TARGET_TABLE", "TARGET_COLUMN",
                             "TARGET_DATATYPE", "MAPPING_TYPE", "TRANSFORMATION", "BUSINESS_RULE",
                             "BUSINESS_DEFINITION", "NULLABLE_RULE", "UNIQUENESS_RULE", "ACCEPTED_VALUES",
                             "SCD_BEHAVIOR", "MAPPING_CONFIDENCE", "HUMAN_APPROVED", "REVIEWER"],
                            ["?", "?", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')",
                             "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "?", "?", "?", "?", "?", "?",
                             "?", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "?::BOOLEAN", "?::BOOLEAN",
                             "PARSE_JSON(?)", "NULLIF(?, '')", "NULLIF(?, '')::FLOAT", "?::BOOLEAN", "NULLIF(?, '')"],
                            [[str(uuid.uuid4()), sttm_id, l.get("decision_id"), l.get("source_system"),
                              l.get("source_database"), l.get("source_schema"), l.get("source_table"),
                              l.get("source_column"), l.get("source_datatype"),
                              scalar(session, "SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?",
                                     [run["DOMAIN_ID"]]),
                              target["TARGET_DATABASE"], target["TARGET_SCHEMA"], target["TARGET_TABLE"],
                              l["target_column"], l["target_datatype"], l["mapping_type"], l.get("transformation"),
                              l.get("business_rule"), l.get("business_definition"), l["nullable_rule"],
                              l["uniqueness_rule"], l.get("accepted_values") or [], l.get("scd_behavior"),
                              l.get("mapping_confidence"), l["human_approved"], l.get("reviewer")]
                             for l in contract["lines"]])

            call.summary = f"STTM v{version}: {len(contract['lines'])} lines, grain {contract['table_design']['grain']}"
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            stage.fail(exc)
            return {"state": stage.payload()}
    stage.move("STTM_REVIEW", call.summary, {"sttm_id": sttm_id, "version": version}, in_transaction=write)
    return {"sttm_id": sttm_id, "version": version, "table_design": contract["table_design"],
            "lines": contract["lines"], "state": stage.payload()}
