"""CODEGEN.GENERATE_DBT: SODA_APPROVED -> DBT_PENDING -> DBT_GENERATING -> VALIDATION_PENDING."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

from services.common.audit import tool_call
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.dbt.project import build
from services.knowledge.procedures import current_knowledge_version, load_skill
from services.knowledge.usage import STAGE_SKILLS, use_skills
from services.soda.expectations import render_yaml
from services.soda.procedures import _current_sttm, _lines


def _landing_tables(session, run_id: str) -> List[Dict[str, str]]:
    return [{
        "source_table": r["SOURCE_TABLE"],
        "landing_table": r["LANDING_TABLE"],
        "database": r["LANDING_DATABASE"],
        "schema": r["LANDING_SCHEMA"],
    } for r in rows(session, """
        SELECT SOURCE_TABLE, LANDING_TABLE, LANDING_DATABASE, LANDING_SCHEMA
          FROM SOURCE.LANDING_TABLE_REGISTRY
         WHERE RUN_ID = ? AND INGESTION_STATUS = 'COMPLETE'
       QUALIFY ROW_NUMBER() OVER (PARTITION BY SOURCE_TABLE ORDER BY CREATED_AT DESC) = 1
    """, [run_id])]


def _macros(session) -> List[Dict[str, str]]:
    skill = load_skill(session, "GDP_DOMAIN_SKILL")
    config = skill.get("config") or {}
    return [{"name": m["name"], "sql": m["sql"]} for m in config.get("macros", []) if m.get("sql")]


def _put_files(session, stage_root: str, files: Dict[str, str]) -> None:
    tmp = tempfile.mkdtemp()
    try:
        for rel, content in files.items():
            local = Path(tmp) / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
        for rel in files:
            posix = rel.replace("\\", "/")
            parent = posix.rsplit("/", 1)[0] if "/" in posix else ""
            remote = f"@{stage_root}/{parent}" if parent else f"@{stage_root}"
            session.file.put((Path(tmp) / rel).as_posix(), remote, auto_compress=False, overwrite=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def generate_dbt(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("SODA_APPROVED", "DBT_PENDING")
    stage.walk(["SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING"], "dbt generation started")
    with tool_call(session, run_id, "generate_dbt", {"run_id": run_id}) as call:
        try:
            use_skills(session, STAGE_SKILLS["DBT"])
            sttm = _current_sttm(session, run_id)
            design = variant(sttm["TABLE_DESIGN"]) or {}
            lines = [{
                "target_column": r["TARGET_COLUMN"], "target_datatype": r["TARGET_DATATYPE"],
                "source_column": r["SOURCE_COLUMN"], "source_table": r["SOURCE_TABLE"],
                "mapping_type": r["MAPPING_TYPE"], "transformation": r["TRANSFORMATION"],
                "nullable_rule": r["NULLABLE_RULE"], "uniqueness_rule": r["UNIQUENESS_RULE"],
                "accepted_values": variant(r["ACCEPTED_VALUES"]) or [],
                "business_definition": r["BUSINESS_DEFINITION"],
            } for r in rows(session, "SELECT * FROM CONTRACT.STTM_LINE WHERE STTM_ID = ? ORDER BY TARGET_COLUMN",
                            [sttm["STTM_ID"]])]
            soda_rows = rows(session, """SELECT TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION,
                                         SEVERITY, ORIGIN, CLIENT_REQUIREMENT
                                         FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                                         WHERE RUN_ID = ? AND IS_CURRENT AND STATUS IN ('PROPOSED', 'APPROVED')""",
                             [run_id])
            checks = [{
                "target_table": r["TARGET_TABLE"], "target_column": r["TARGET_COLUMN"],
                "check_type": r["CHECK_TYPE"], "definition": variant(r["CHECK_DEFINITION"]) or {},
                "severity": r["SEVERITY"], "origin": r["ORIGIN"], "requirement": r["CLIENT_REQUIREMENT"],
            } for r in soda_rows]
            soda_yaml = render_yaml((design.get("target_table") or "dim_customer").lower(), checks)
            source_name = scalar(session, """SELECT S.SOURCE_SYSTEM_NAME FROM SOURCE.SOURCE_REGISTRY S
                                             JOIN CORE.WORKFLOW_RUN R ON R.SOURCE_SYSTEM_ID = S.SOURCE_SYSTEM_ID
                                             WHERE R.RUN_ID = ?""", [run_id]) or "SOURCE"
            files = build({"sttm_id": sttm["STTM_ID"], "table_design": design, "lines": lines}, _macros(session), soda_yaml,
                          _landing_tables(session, run_id), source_name)
            version = (scalar(session, "SELECT MAX(GENERATION_VERSION) FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE RUN_ID = ?",
                              [run_id]) or 0) + 1
            generation_id = str(uuid.uuid4())
            kv = current_knowledge_version(session, stage.run["DOMAIN_ID"])
            stage_path = f"CODEGEN.DBT_STAGE/{run_id}/v{version}"
            _put_files(session, stage_path, files)

            def write(_event_id: str) -> None:
                session.sql("UPDATE CODEGEN.DBT_GENERATION_REGISTRY SET GENERATION_STATUS = 'SUPERSEDED' "
                            "WHERE RUN_ID = ? AND GENERATION_STATUS IN ('GENERATING', 'GENERATED')",
                            params=[run_id]).collect()
                insert_rows(session, "CODEGEN.DBT_GENERATION_REGISTRY",
                            ["GENERATION_ID", "RUN_ID", "DOMAIN", "TARGET_MODEL", "STTM_ID", "STTM_VERSION",
                             "FILES_GENERATED", "SKILL_VERSION", "KNOWLEDGE_VERSION", "MODEL_VERSION",
                             "GENERATION_VERSION", "GENERATION_STATUS", "STAGE_PATH", "CREATED_BY"],
                            ["?", "?", "?", "?", "?", "?::NUMBER", "?::NUMBER", "'GDP_DOMAIN_SKILL:1.0.0'",
                             "?", "'dbt-deterministic-v1'", "?::NUMBER", "'GENERATED'", "?", "CURRENT_USER()"],
                            [[generation_id, run_id,
                              scalar(session, "SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?",
                                     [stage.run["DOMAIN_ID"]]) or "GDP",
                              stage.run.get("TARGET_MODEL") or design.get("target_table"),
                              sttm["STTM_ID"], sttm["STTM_VERSION"], len(files), kv, version, f"@{stage_path}"]])
                insert_rows(session, "CODEGEN.GENERATED_ARTIFACT",
                            ["ARTIFACT_ID", "GENERATION_ID", "RUN_ID", "ARTIFACT_TYPE", "FILE_PATH", "CONTENT",
                             "CONTENT_SHA256"],
                            ["?", "?", "?", "?", "?", "?", "?"],
                            [[str(uuid.uuid4()), generation_id, run_id, _artifact_type(path), path, content,
                              hashlib.sha256(content.encode("utf-8")).hexdigest()]
                             for path, content in files.items()])

            call.summary = f"dbt v{version}: {len(files)} files on @{stage_path}"
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            stage.fail(exc)
            return {"state": stage.payload()}
    stage.move("VALIDATION_PENDING", call.summary,
               {"generation_id": generation_id, "files": list(files)}, in_transaction=write)
    return {"generation_id": generation_id, "version": version, "files": list(files), "state": stage.payload()}


def _artifact_type(path: str) -> str:
    if path.endswith("dbt_project.yml"):
        return "DBT_PROJECT"
    if path.startswith("macros/"):
        return "DBT_MACRO"
    if path.endswith("_sources.yml"):
        return "DBT_SOURCES_YML"
    if path.endswith(".yml"):
        return "DBT_SCHEMA_YML"
    if path.startswith("soda/"):
        return "SODA_CHECKS"
    if path.startswith("mappings/"):
        return "STTM_EXPORT"
    return "DBT_MODEL"
