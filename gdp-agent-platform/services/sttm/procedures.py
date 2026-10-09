"""CONTRACT.GENERATE_STTM / REFINE_TRANSFORMATION / APPLY_TRANSFORMATION / EXPORT_STTM_CSV."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.common.audit import record_cost, tool_call
from services.common.llm import complete_json
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.knowledge.procedures import current_knowledge_version
from services.common.standard import run_standard
from services.knowledge.usage import assert_safe_transformation, domain_context, use_stage
from services.mapping.procedures import target_columns, target_table
from services.sttm.assemble import assemble
from services.sttm.join_graph import apply_overrides, build_join_graph, join_logic_by_table
from services.sttm.refine import REFINE_SCHEMA, refine_prompt, render_csv, reusable_expression


def generate_sttm(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("MAPPING_APPROVED", "STTM_PENDING")
    stage.walk(["MAPPING_APPROVED", "STTM_PENDING"], "STTM generation started")
    with tool_call(session, run_id, "generate_sttm", {"run_id": run_id}) as call:
        try:
            run = stage.run
            use_stage(session, "STTM", run_standard(run), run_id)
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
            graph = plan_joins(session, run_id, contract["lines"])
            contract["table_design"].update(join_graph=graph, driving_table=graph["driving_table"],
                                            join_paths=[j["condition"] for j in graph["joins"]])
            join_logic = join_logic_by_table(graph)
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
                             "SCD_BEHAVIOR", "MAPPING_CONFIDENCE", "HUMAN_APPROVED", "REVIEWER", "JOIN_LOGIC"],
                            ["?", "?", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')",
                             "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "?", "?", "?", "?", "?", "?",
                             "?", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "?::BOOLEAN", "?::BOOLEAN",
                             "PARSE_JSON(?)", "NULLIF(?, '')", "NULLIF(?, '')::FLOAT", "?::BOOLEAN", "NULLIF(?, '')",
                             "NULLIF(?, '')"],
                            [[str(uuid.uuid4()), sttm_id, l.get("decision_id"), l.get("source_system"),
                              l.get("source_database"), l.get("source_schema"), l.get("source_table"),
                              l.get("source_column"), l.get("source_datatype"),
                              scalar(session, "SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?",
                                     [run["DOMAIN_ID"]]),
                              target["TARGET_DATABASE"], target["TARGET_SCHEMA"], target["TARGET_TABLE"],
                              l["target_column"], l["target_datatype"], l["mapping_type"], l.get("transformation"),
                              l.get("business_rule"), l.get("business_definition"), l["nullable_rule"],
                              l["uniqueness_rule"], l.get("accepted_values") or [], l.get("scd_behavior"),
                              l.get("mapping_confidence"), l["human_approved"], l.get("reviewer"),
                              join_logic.get(str(l.get("source_table") or "").upper())]
                             for l in contract["lines"]])

            call.summary = f"STTM v{version}: {len(contract['lines'])} lines, grain {contract['table_design']['grain']}"
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            stage.fail(exc)
            return {"state": stage.payload()}
    stage.move("STTM_REVIEW", call.summary, {"sttm_id": sttm_id, "version": version}, in_transaction=write)
    return {"sttm_id": sttm_id, "version": version, "table_design": contract["table_design"],
            "lines": contract["lines"], "state": stage.payload()}


def _current_sttm(session, run_id: str) -> Dict[str, Any]:
    found = rows(session, """SELECT * FROM CONTRACT.STTM_REGISTRY
                             WHERE RUN_ID = ? AND STATUS IN ('REVIEW', 'APPROVED', 'DRAFT')
                             ORDER BY STTM_VERSION DESC LIMIT 1""", [run_id])
    assert found, "no current STTM for this run"
    return found[0]


def _profile_for(session, run_id: str, table: Optional[str], column: Optional[str]) -> Dict[str, Any]:
    if not column:
        return {}
    table_name = table or ""
    found = rows(session, """
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, SEMANTIC_TYPE, NULL_PERCENTAGE, DISTINCT_PERCENTAGE,
               CARDINALITY, GENERATED_DESCRIPTION, SAMPLE_VALUES, PATTERN_JSON, STATISTICS_JSON
          FROM PROFILE.PROFILE_REGISTRY
         WHERE RUN_ID = ? AND IS_CURRENT AND UPPER(COLUMN_NAME) = UPPER(?)
           AND (? = '' OR UPPER(TABLE_NAME) = UPPER(?))
         ORDER BY IS_CURRENT DESC LIMIT 1
    """, [run_id, column, table_name, table_name])
    if not found:
        return {}
    p = found[0]
    samples = variant(p["SAMPLE_VALUES"]) or []
    if isinstance(samples, dict):
        samples = samples.get("values") or list(samples.values())
    return {
        "semantic_type": p["SEMANTIC_TYPE"], "null_percentage": p["NULL_PERCENTAGE"],
        "distinct_percentage": p["DISTINCT_PERCENTAGE"], "cardinality": p["CARDINALITY"],
        "description": p["GENERATED_DESCRIPTION"], "samples": samples[:8] if isinstance(samples, list) else samples,
        "patterns": variant(p["PATTERN_JSON"]) or [],
    }


def _prior_rules(session, domain_id: Optional[str], target_column: Optional[str]) -> List[str]:
    if not domain_id:
        return []
    found = rows(session, """SELECT TITLE, CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                             WHERE DOMAIN_ID = ? AND IS_CURRENT AND STATUS = 'ACTIVE'
                               AND KNOWLEDGE_TYPE = 'TRANSFORMATION_RULE'
                             ORDER BY UPDATED_AT DESC NULLS LAST LIMIT 8""", [domain_id])
    out = []
    for r in found:
        if target_column and target_column.upper() in (r.get("TITLE") or "").upper():
            out.insert(0, f"{r['TITLE']}: {r['CONTENT']}")
        else:
            out.append(f"{r['TITLE']}: {r['CONTENT']}")
    return out[:8]


def _line_context(session, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    line_id = payload.get("sttm_line_id")
    line = None
    if line_id:
        found = rows(session, """SELECT L.*, S.DOMAIN_ID FROM CONTRACT.STTM_LINE L
                                 JOIN CONTRACT.STTM_REGISTRY S ON S.STTM_ID = L.STTM_ID
                                 WHERE L.STTM_LINE_ID = ? AND S.RUN_ID = ?""", [line_id, run_id])
        assert found, "STTM line not found on this run"
        line = found[0]
    context = {
        "sttm_line_id": line_id,
        "target_column": (line or {}).get("TARGET_COLUMN") or payload.get("target_column"),
        "target_datatype": (line or {}).get("TARGET_DATATYPE") or payload.get("target_datatype"),
        "source_table": (line or {}).get("SOURCE_TABLE") or payload.get("source_table"),
        "source_column": (line or {}).get("SOURCE_COLUMN") or payload.get("source_column"),
        "source_datatype": (line or {}).get("SOURCE_DATATYPE") or payload.get("source_datatype"),
        "current_transformation": (line or {}).get("TRANSFORMATION") or payload.get("current_transformation"),
        "business_definition": (line or {}).get("BUSINESS_DEFINITION") or payload.get("business_definition"),
        "decision_id": (line or {}).get("DECISION_ID"),
        "mapping_type": (line or {}).get("MAPPING_TYPE"),
        "domain_id": (line or {}).get("DOMAIN_ID"),
    }
    assert context["target_column"], "target_column or sttm_line_id is required"
    run = rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    context["standard"] = run_standard(run[0] if run else {})
    context["domain_id"] = context["domain_id"] or (run[0]["DOMAIN_ID"] if run else None)
    context["profile"] = _profile_for(session, run_id, context["source_table"], context["source_column"])
    context["prior_rules"] = _prior_rules(session, context["domain_id"], context["target_column"])
    try:
        target = rows(session, "SELECT TARGET_MODEL FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
        model = ((target[0]["TARGET_MODEL"] if target else "") or "").split(".")[-1] or None
        context["domain_rules"] = domain_context(session, context["domain_id"], model, 3000, context["standard"])
    except Exception:
        context["domain_rules"] = ""
    return context


def refine_transformation(session, run_id: str, payload_json: str) -> Dict[str, Any]:
    payload = json.loads(payload_json or "{}")
    prompt = (payload.get("prompt") or "").strip()
    assert prompt, "describe the transformation in natural language"
    context = _line_context(session, run_id, payload)
    with tool_call(session, run_id, "refine_transformation",
                   {"target": context["target_column"], "prompt": clip(prompt, 500)}) as call:
        try:
            try:
                use_stage(session, "STTM", context["standard"], run_id)
            except Exception:
                pass
            output, usage, model = complete_json(session, refine_prompt(context, prompt), REFINE_SCHEMA, max_tokens=1500, stage="STTM")
            record_cost(session, run_id, "STTM", model, usage, 0, tool_calls=1)
            sql = (output.get("transformation") or "").strip()
            assert_safe_transformation(sql)
            assert sql, "model returned an empty transformation"
            call.summary = f"{context['target_column']}: {clip(sql, 200)}"
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            raise
    return {
        "target_column": context["target_column"],
        "sttm_line_id": context.get("sttm_line_id"),
        "transformation": sql,
        "rationale": output.get("rationale"),
        "dbt_notes": output.get("dbt_notes"),
        "soda_checks": output.get("soda_checks") or [],
        "context": {k: context[k] for k in ("source_table", "source_column", "source_datatype",
                                            "target_datatype", "current_transformation", "profile")},
    }


def apply_transformation(session, run_id: str, payload_json: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require("STTM_REVIEW", "STTM_PENDING")
    payload = json.loads(payload_json or "{}")
    sql = (payload.get("transformation") or "").strip()
    assert_safe_transformation(sql)
    assert sql, "transformation is required"
    context = _line_context(session, run_id, payload)
    assert context.get("sttm_line_id"), "apply needs an STTM line"
    mapping_type = context.get("mapping_type") or "TRANSFORM"
    if mapping_type == "DIRECT":
        mapping_type = "TRANSFORM"
    prompt = clip(payload.get("prompt"), 2000)
    rationale = clip(payload.get("rationale"), 2000)
    soda_checks = payload.get("soda_checks") or []
    expression = reusable_expression(sql, context.get("source_column"))
    content = (
        f"{context.get('source_table')}.{context.get('source_column')} -> {context['target_column']}: {sql}. "
        f"{rationale or prompt}"
    )
    model_row = rows(session, "SELECT TARGET_MODEL FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    target_table = str((model_row[0]["TARGET_MODEL"] if model_row else "") or "").split(".")[-1].strip('"').upper()
    # one rule per domain + target table + column: the same column name on another table is a different rule
    ref = f"transform.{target_table}.{str(context['target_column']).upper()}" if target_table         else f"transform.{str(context['target_column']).upper()}"
    with tool_call(session, run_id, "apply_transformation",
                   {"target": context["target_column"], "sql": clip(sql, 400)}) as call:
        session.sql("""UPDATE CONTRACT.STTM_LINE SET TRANSFORMATION = ?, MAPPING_TYPE = ?
                       WHERE STTM_LINE_ID = ?""",
                    params=[sql, mapping_type, context["sttm_line_id"]]).collect()
        if context.get("decision_id"):
            session.sql("UPDATE MAPPING.MAPPING_DECISION SET TRANSFORMATION = ? WHERE DECISION_ID = ?",
                        params=[sql, context["decision_id"]]).collect()
        if context.get("domain_id"):
            session.sql("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, STATUS = 'RETIRED'
                           WHERE DOMAIN_ID = ? AND SOURCE_REFERENCE = ? AND IS_CURRENT""",
                        params=[context["domain_id"], ref]).collect()
            version = (scalar(session, """SELECT MAX(VERSION) FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                                          WHERE DOMAIN_ID = ? AND SOURCE_REFERENCE = ?""",
                              [context["domain_id"], ref]) or 0) + 1
            insert_rows(session, "KNOWLEDGE.DOMAIN_KNOWLEDGE",
                        ["KNOWLEDGE_ID", "DOMAIN_ID", "KNOWLEDGE_TYPE", "TITLE", "CONTENT", "CONTENT_JSON",
                         "TAGS", "SOURCE_REFERENCE", "STATUS", "VERSION", "IS_CURRENT", "CREATED_BY"],
                        ["?", "?", "'TRANSFORMATION_RULE'", "?", "?", "PARSE_JSON(?)", "PARSE_JSON(?)", "?",
                         "'ACTIVE'", "?::NUMBER", "TRUE", "CURRENT_USER()"],
                        [[str(uuid.uuid4()), context["domain_id"],
                          f"Transform {context['target_column']}"[:500], clip(content, 8000),
                          {"target_column": context["target_column"], "target_table": target_table or None,
                           "source_column": context.get("source_column"),
                           "source_table": context.get("source_table"), "expression": expression, "sql": sql,
                           "prompt": prompt, "rationale": rationale, "soda_checks": soda_checks,
                           "dbt_notes": payload.get("dbt_notes"), "run_id": run_id,
                           "profile": context.get("profile")},
                          ["TRANSFORM", "STTM", "SODA", "DBT"], ref, version]])
        call.summary = f"applied {context['target_column']}"
    return {"target_column": context["target_column"], "transformation": sql, "mapping_type": mapping_type,
            "state": stage.payload()}


def _put_text(session, stage_root: str, filename: str, content: str) -> None:
    tmp = tempfile.mkdtemp()
    try:
        path = Path(tmp) / filename
        path.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
        session.file.put(path.as_posix(), f"@{stage_root}", auto_compress=False, overwrite=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def export_sttm_csv(session, run_id: str) -> Dict[str, Any]:
    sttm = _current_sttm(session, run_id)
    rules = {}
    for k in rows(session, """SELECT TITLE, CONTENT, CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                              WHERE IS_CURRENT AND KNOWLEDGE_TYPE = 'TRANSFORMATION_RULE'
                                AND (DOMAIN_ID = ? OR SOURCE_REFERENCE LIKE 'transform.%')""",
                  [sttm["DOMAIN_ID"]]):
        content = variant(k["CONTENT_JSON"]) or {}
        target = (content.get("target_column") or "").upper()
        if target:
            rules[target] = content
    lines = []
    for r in rows(session, "SELECT * FROM CONTRACT.STTM_LINE WHERE STTM_ID = ? ORDER BY TARGET_COLUMN",
                  [sttm["STTM_ID"]]):
        extra = rules.get((r["TARGET_COLUMN"] or "").upper()) or {}
        lines.append({
            "target_column": r["TARGET_COLUMN"], "target_datatype": r["TARGET_DATATYPE"],
            "mapping_type": r["MAPPING_TYPE"], "source_table": r["SOURCE_TABLE"],
            "source_column": r["SOURCE_COLUMN"], "source_datatype": r["SOURCE_DATATYPE"],
            "transformation": r["TRANSFORMATION"], "prompt": extra.get("prompt"),
            "rationale": extra.get("rationale") or r["BUSINESS_RULE"],
            "business_definition": r["BUSINESS_DEFINITION"],
            "soda_checks": extra.get("soda_checks") or [],
        })
    csv_text = render_csv(lines)
    version = sttm["STTM_VERSION"]
    stage_root = f"CODEGEN.DBT_STAGE/{run_id}/sttm"
    filename = f"sttm_v{version}.csv"
    _put_text(session, stage_root, filename, csv_text)
    stage_path = f"@{stage_root}/{filename}"
    if sttm.get("DOMAIN_ID"):
        ref = f"sttm.csv.{run_id}"
        session.sql("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, STATUS = 'RETIRED'
                       WHERE SOURCE_REFERENCE = ? AND IS_CURRENT""", params=[ref]).collect()
        kv = (scalar(session, "SELECT MAX(VERSION) FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = ?",
                     [ref]) or 0) + 1
        insert_rows(session, "KNOWLEDGE.DOMAIN_KNOWLEDGE",
                    ["KNOWLEDGE_ID", "DOMAIN_ID", "KNOWLEDGE_TYPE", "TITLE", "CONTENT", "CONTENT_JSON",
                     "TAGS", "SOURCE_REFERENCE", "STATUS", "VERSION", "IS_CURRENT", "CREATED_BY"],
                    ["?", "?", "'STTM_TEMPLATE'", "?", "?", "PARSE_JSON(?)", "PARSE_JSON(?)", "?",
                     "'ACTIVE'", "?::NUMBER", "TRUE", "CURRENT_USER()"],
                    [[str(uuid.uuid4()), sttm["DOMAIN_ID"], f"STTM CSV v{version}"[:500],
                      clip(f"Stored at {stage_path}\n{csv_text}", 8000),
                      {"stage_path": stage_path, "run_id": run_id, "sttm_id": sttm["STTM_ID"],
                       "sttm_version": version, "rows": len(lines)},
                      ["STTM", "CSV", "DBT", "SODA"], ref, kv]])
    with tool_call(session, run_id, "export_sttm_csv", {"path": stage_path, "rows": len(lines)}) as call:
        call.summary = f"{len(lines)} lines -> {stage_path}"
    return {"stage_path": stage_path, "csv": csv_text, "rows": len(lines), "sttm_version": version}


# ---------------------------------------------------------------- multi-table join planning


def _run_columns(session, run_id: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for c in rows(session, """
        SELECT T.SOURCE_TABLE, C.COLUMN_NAME, C.DATA_TYPE
          FROM SOURCE.LANDING_TABLE_REGISTRY T
          JOIN SOURCE.LANDING_COLUMN_REGISTRY C ON C.LANDING_ID = T.LANDING_ID
         WHERE T.RUN_ID = ? AND T.INGESTION_STATUS = 'COMPLETE'
       QUALIFY DENSE_RANK() OVER (PARTITION BY T.SOURCE_TABLE ORDER BY T.CREATED_AT DESC) = 1
         ORDER BY T.SOURCE_TABLE, C.ORDINAL_POSITION """, [run_id]):
        out.setdefault(str(c["SOURCE_TABLE"]).upper(), []).append(
            {"column_name": str(c["COLUMN_NAME"]).upper(), "data_type": c["DATA_TYPE"]})
    return out


def _value_relationships(session, run_id: str, docs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Joins proven by value overlap between the run's landed tables; failures leave the name-based plan."""
    from services.profiling.insights import overlap_candidates, overlap_relationships, overlap_sql
    from services.source.identifiers import quote

    exact: Dict[Tuple[str, str], str] = {}
    tables: Dict[str, str] = {}
    try:
        for r in rows(session, """
            SELECT T.SOURCE_TABLE, T.LANDING_DATABASE, T.LANDING_SCHEMA, T.LANDING_TABLE, C.COLUMN_NAME
              FROM SOURCE.LANDING_TABLE_REGISTRY T
              JOIN SOURCE.LANDING_COLUMN_REGISTRY C ON C.LANDING_ID = T.LANDING_ID
             WHERE T.RUN_ID = ? AND T.INGESTION_STATUS = 'COMPLETE'
           QUALIFY DENSE_RANK() OVER (PARTITION BY T.SOURCE_TABLE ORDER BY T.CREATED_AT DESC) = 1""", [run_id]):
            key = str(r["SOURCE_TABLE"]).upper()
            tables[key] = ".".join(quote(str(p)) for p in (r["LANDING_DATABASE"], r["LANDING_SCHEMA"], r["LANDING_TABLE"]))
            exact[(key, str(r["COLUMN_NAME"]).upper())] = str(r["COLUMN_NAME"])
        pairs = [p for p in overlap_candidates(docs) if p["child"] in tables and p["parent"] in tables]
        sql = overlap_sql(pairs, lambda t, c: (tables[t], quote(exact.get((t, c), c))))
        return overlap_relationships(pairs, rows(session, sql)) if sql else []
    except Exception:
        return []


def _profile_docs(session, run_id: str) -> Dict[str, Dict[str, Any]]:
    """The run's current column profiles in the shape services.profiling.insights reads."""
    docs: Dict[str, Dict[str, Any]] = {}
    for p in rows(session, """SELECT TABLE_NAME, COLUMN_NAME, POTENTIAL_KEY_FLAG, PII_CLASSIFICATION, STATISTICS_JSON
                              FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = ? AND IS_CURRENT """, [run_id]):
        stats = variant(p["STATISTICS_JSON"]) or {}
        docs.setdefault(str(p["TABLE_NAME"]).upper(), {"columns": []})["columns"].append({
            "column_name": str(p["COLUMN_NAME"]).upper(), "family": stats.get("family"),
            "potential_key": bool(p["POTENTIAL_KEY_FLAG"]), "pii_classification": p["PII_CLASSIFICATION"] or "NONE",
            "statistics": stats,
        })
    return docs


def plan_joins(session, run_id: str, lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Join graph for the run's source tables from profile evidence (keys, ranges, overlap) and name matches.
    A reviewer-edited graph from the previous STTM version is kept when it still covers the same tables."""
    from services.profiling.insights import infer_relationships
    from services.source.er_graph import infer_joins

    columns = _run_columns(session, run_id)
    tables = sorted(columns)
    prior = rows(session, """SELECT TABLE_DESIGN:join_graph AS G FROM CONTRACT.STTM_REGISTRY
                              WHERE RUN_ID = ? AND TABLE_DESIGN:join_graph:edited::BOOLEAN
                              ORDER BY STTM_VERSION DESC LIMIT 1""", [run_id])
    if prior:
        graph = variant(prior[0]["G"]) or {}
        planned = {graph.get("driving_table"), *[j["right_table"] for j in graph.get("joins") or []]}
        if planned <= set(tables) | {None}:
            return graph
    docs = _profile_docs(session, run_id)
    relationships = infer_relationships(docs)
    relationships += _value_relationships(session, run_id, docs)
    relationships += [{**j, "source": "name"} for j in infer_joins(columns)] if len(columns) > 1 else []
    complete = {(t, c["column_name"]): float((c.get("statistics") or {}).get("null_percentage") or 0) == 0
                for t, d in docs.items() for c in d["columns"]}
    counts: Dict[str, int] = {}
    for line in lines:
        table = str(line.get("source_table") or "").upper()
        if table:
            counts[table] = counts.get(table, 0) + 1
    return build_join_graph(tables, relationships, counts, complete)


def update_join_graph(session, run_id: str, payload_json: str) -> Dict[str, Any]:
    """Reviewer edits to the join plan of the STTM under review: driving table and joins (keys, LEFT or INNER)."""
    from services.workflow.procedures import _get_run, _is_closed, _record_run_event

    payload = json.loads(payload_json or "{}")
    run = _get_run(session, run_id)
    assert not _is_closed(run), "run is archived or deleted; restore it first"
    sttm = _current_sttm(session, run_id)
    assert sttm["STATUS"] in ("REVIEW", "DRAFT"), "the STTM is approved; reopen it before changing joins"
    design = variant(sttm["TABLE_DESIGN"]) or {}
    columns = {t: [c["column_name"] for c in cs] for t, cs in _run_columns(session, run_id).items()}
    graph = apply_overrides(design.get("join_graph") or {"joins": []}, payload.get("driving_table"),
                            payload.get("joins") or [], columns)
    design.update(join_graph=graph, driving_table=graph["driving_table"],
                  join_paths=[j["condition"] for j in graph["joins"]])
    logic = join_logic_by_table(graph)
    session.sql("UPDATE CONTRACT.STTM_REGISTRY SET TABLE_DESIGN = PARSE_JSON(?) WHERE STTM_ID = ?",
                params=[json.dumps(design), sttm["STTM_ID"]]).collect()
    for table, text in logic.items():
        session.sql("UPDATE CONTRACT.STTM_LINE SET JOIN_LOGIC = ? WHERE STTM_ID = ? AND UPPER(SOURCE_TABLE) = ?",
                    params=[text, sttm["STTM_ID"], table]).collect()
    _record_run_event(session, run, "HUMAN", "join plan edited",
                      {"driving_table": graph["driving_table"], "joins": len(graph["joins"]),
                       "unreachable": graph["unreachable"]})
    return {"sttm_id": sttm["STTM_ID"], "join_graph": graph}
