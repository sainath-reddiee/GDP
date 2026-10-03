"""PROFILE.RUN_PROFILING: LANDING_COMPLETE -> PROFILING_PENDING -> PROFILING_RUNNING -> PROFILING_COMPLETE."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List

from services.common.audit import record_cost, tool_call
from services.common.llm import complete_json
from services.common.sql import clip, insert_rows, rows, scalar
from services.common.stage import Stage
from services.knowledge.usage import STAGE_SKILLS, use_skills
from services.profiling import profiler
from services.source.identifiers import fqn, quote

PATH = ["LANDING_COMPLETE", "PROFILING_PENDING", "PROFILING_RUNNING"]


def _landed_tables(session, run_id: str) -> List[Dict[str, Any]]:
    """Latest successful landing per source object of the run, with its columns."""
    tables = rows(session, """
        SELECT LANDING_ID, SOURCE_TABLE, LANDING_DATABASE, LANDING_SCHEMA, LANDING_TABLE, ROW_COUNT
          FROM SOURCE.LANDING_TABLE_REGISTRY
         WHERE RUN_ID = ? AND INGESTION_STATUS = 'COMPLETE'
       QUALIFY ROW_NUMBER() OVER (PARTITION BY SOURCE_TABLE ORDER BY CREATED_AT DESC) = 1
         ORDER BY SOURCE_TABLE""", [run_id])
    for t in tables:
        t["COLUMNS"] = rows(session, """
            SELECT LANDING_COLUMN_ID, COLUMN_NAME, DATA_TYPE FROM SOURCE.LANDING_COLUMN_REGISTRY
             WHERE LANDING_ID = ? ORDER BY ORDINAL_POSITION""", [t["LANDING_ID"]])
    return tables


def _profile_table(session, table: Dict[str, Any]) -> List[Dict[str, Any]]:
    target = fqn(table["LANDING_DATABASE"], table["LANDING_SCHEMA"], table["LANDING_TABLE"])
    columns = [(c["COLUMN_NAME"], c["DATA_TYPE"]) for c in table["COLUMNS"]]
    stats_row = rows(session, profiler.stats_sql(target, columns))[0]
    row_count = int(stats_row["ROW_COUNT"])
    profiles = []
    for i, col in enumerate(table["COLUMNS"]):
        name, data_type = col["COLUMN_NAME"], col["DATA_TYPE"]
        stats = profiler.column_stats(stats_row, i, row_count)
        family = profiler.type_family(data_type)
        freq, patterns = [], []
        if family != "OTHER":
            freq = [{"value": r["V"], "count": int(r["N"])} for r in rows(session, profiler.frequency_sql(target, name))]
        if family == "TEXT":
            patterns = [{"pattern": r["P"], "count": int(r["N"])} for r in rows(session, profiler.pattern_sql(target, name))]
        profile = profiler.build_profile(name, data_type, stats, freq, patterns)
        profile["landing_column_id"] = col["LANDING_COLUMN_ID"]
        profiles.append(profile)
    return profiles


def _foreign_keys(session, tables: List[Dict[str, Any]], profiles: Dict[str, List[Dict[str, Any]]]) -> None:
    """A column is a potential FK when it shares a name with another table's potential key and all its values exist there."""
    keys = {(t["LANDING_ID"], p["column_name"]): t for t in tables for p in profiles[t["LANDING_ID"]] if p["potential_key"]}
    for t in tables:
        for p in profiles[t["LANDING_ID"]]:
            for (other_id, key_col), other in keys.items():
                if other_id == t["LANDING_ID"] or key_col != p["column_name"] or p["potential_key"]:
                    continue
                child = fqn(t["LANDING_DATABASE"], t["LANDING_SCHEMA"], t["LANDING_TABLE"])
                parent = fqn(other["LANDING_DATABASE"], other["LANDING_SCHEMA"], other["LANDING_TABLE"])
                q = quote(key_col)
                orphans = scalar(session, f"SELECT COUNT(*) FROM {child} C WHERE C.{q} IS NOT NULL AND NOT EXISTS "
                                          f"(SELECT 1 FROM {parent} P WHERE P.{q} = C.{q})")
                if orphans == 0:
                    p["potential_foreign_key"] = f"{other['SOURCE_TABLE']}.{key_col}"


def _enrich(session, run_id: str, table: Dict[str, Any], profiles: List[Dict[str, Any]],
            skill_excerpt: str) -> str | None:
    """One structured AI_COMPLETE call per table. Failures leave the deterministic profile intact."""
    started = time.time()
    try:
        result, usage, model = complete_json(
            session, profiler.enrichment_prompt(table["SOURCE_TABLE"], profiles, skill_excerpt),
            profiler.ENRICHMENT_SCHEMA, max_tokens=2000)
    except Exception:
        return None
    record_cost(session, run_id, "PROFILING", model, usage, int((time.time() - started) * 1000), tool_calls=1)
    by_name = {c["column_name"].upper(): c for c in result.get("columns", [])}
    for p in profiles:
        found = by_name.get(p["column_name"].upper())
        if not found:
            continue
        p["description"] = clip(found.get("description"), 4000)
        suggested = (found.get("semantic_type") or "").upper()
        if p["semantic_type"] in profiler.GENERIC_TYPES and suggested and suggested not in profiler.GENERIC_TYPES:
            p["semantic_type"] = suggested
            p["pii_classification"] = profiler.pii_classification(suggested)
    return model


def _store(session, run_id: str, table: Dict[str, Any], profiles: List[Dict[str, Any]], model: str | None) -> None:
    version = (scalar(session, "SELECT MAX(PROFILE_VERSION) FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = ? "
                               "AND SOURCE_TABLE_ID = ?", [run_id, table["LANDING_ID"]]) or 0) + 1
    session.sql("UPDATE PROFILE.PROFILE_REGISTRY SET IS_CURRENT = FALSE WHERE RUN_ID = ? AND SOURCE_TABLE_ID = ?",
                params=[run_id, table["LANDING_ID"]]).collect()
    insert_rows(
        session, "PROFILE.PROFILE_REGISTRY",
        ["PROFILE_ID", "RUN_ID", "SOURCE_TABLE_ID", "SOURCE_COLUMN_ID", "TABLE_NAME", "COLUMN_NAME", "DATA_TYPE",
         "PROFILE_VERSION", "IS_CURRENT", "ROW_COUNT", "NULL_COUNT", "NULL_PERCENTAGE", "DISTINCT_COUNT",
         "DISTINCT_PERCENTAGE", "CARDINALITY", "MIN_VALUE", "MAX_VALUE", "PATTERN_JSON", "SAMPLE_VALUES",
         "STATISTICS_JSON", "GENERATED_DESCRIPTION", "SEMANTIC_TYPE", "POTENTIAL_KEY_FLAG",
         "POTENTIAL_FOREIGN_KEY_FLAG", "PII_CLASSIFICATION", "PROFILE_STATUS", "MODEL_VERSION"],
        ["?", "?", "?", "?", "?", "?", "?", "?::NUMBER", "TRUE", "?::NUMBER", "?::NUMBER", "?::FLOAT",
         "NULLIF(?, '')::NUMBER", "NULLIF(?, '')::FLOAT", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')",
         "PARSE_JSON(?)", "PARSE_JSON(?)", "PARSE_JSON(?)", "NULLIF(?, '')", "?", "?::BOOLEAN", "?::BOOLEAN", "?",
         "'COMPLETE'", "NULLIF(?, '')"],
        [[str(uuid.uuid4()), run_id, table["LANDING_ID"], p["landing_column_id"], table["SOURCE_TABLE"],
          p["column_name"], p["data_type"], version, p["statistics"]["row_count"], p["statistics"]["null_count"],
          p["statistics"]["null_percentage"], p["statistics"].get("distinct_count"),
          p["statistics"].get("distinct_percentage"), p["cardinality"],
          None if p["pii_classification"] != "NONE" else clip(p["statistics"].get("min"), 1024),
          None if p["pii_classification"] != "NONE" else clip(p["statistics"].get("max"), 1024),
          p["patterns"], p["sample_values"],
          {**p["statistics"], "potential_foreign_key": p.get("potential_foreign_key"), "family": p["family"]},
          p.get("description"), p["semantic_type"], p["potential_key"], bool(p.get("potential_foreign_key")),
          p["pii_classification"], model] for p in profiles],
    )


def run_profiling(session, run_id: str) -> Dict[str, Any]:
    stage = Stage(session, run_id)
    stage.require(*PATH[:2])
    stage.walk(PATH, "profiling started")
    tables = _landed_tables(session, run_id)
    summary: Dict[str, Any] = {"tables": []}
    with tool_call(session, run_id, "profile_table", {"tables": [t["SOURCE_TABLE"] for t in tables]}) as call:
        try:
            assert tables, "no successfully landed tables for this run"
            guidance = use_skills(session, STAGE_SKILLS["PROFILING"])
            profiles = {t["LANDING_ID"]: _profile_table(session, t) for t in tables}
            _foreign_keys(session, tables, profiles)
            for t in tables:
                model = _enrich(session, run_id, t, profiles[t["LANDING_ID"]], guidance)
                _store(session, run_id, t, profiles[t["LANDING_ID"]], model)
                summary["tables"].append({
                    "table": t["SOURCE_TABLE"], "columns": len(profiles[t["LANDING_ID"]]),
                    "pii_columns": sum(p["pii_classification"] != "NONE" for p in profiles[t["LANDING_ID"]]),
                    "enriched_by": model})
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            stage.fail(exc)
            return {"profiled": summary, "state": stage.payload()}
        call.summary = "; ".join(f"{t['table']}: {t['columns']} columns, {t['pii_columns']} PII"
                                 for t in summary["tables"])
    stage.move("PROFILING_COMPLETE", f"profiled {len(tables)} tables", summary)
    return {"profiled": summary, "state": stage.payload()}
