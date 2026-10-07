"""PROFILE.RUN_PROFILING: LANDING_COMPLETE -> PROFILING_PENDING -> PROFILING_RUNNING -> PROFILING_COMPLETE.
PROFILE.REFRESH_TABLE_PROFILE: re-profile one table of a run (cache bust), no state change.

Profiles persist per source table, not per run: one JSON document per table on @METADATA.PROFILES_STAGE,
indexed by METADATA.TABLE_PROFILES. A later run of the same source reuses the document while the table's
fingerprint (columns, landed row count, source LAST_ALTERED) is unchanged, so neither the warehouse scan
nor the LLM enrichment repeats. PROFILE.PROFILE_REGISTRY still receives run-scoped rows because mapping,
STTM and dbt join on landing column ids.
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, Sequence, Tuple, Union

from services.common.audit import record_cost, tool_call
from services.common.llm import complete_json
from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.common.stage import Stage
from services.common.rules import ensure_active
from services.knowledge.usage import STAGE_SKILLS, use_skills
from services.profiling import profiler
from services.profiling.insights import quality_dimensions
from services.source.identifiers import fqn, quote
from services.workflow.procedures import _get_run, _is_closed

PATH = ["LANDING_COMPLETE", "PROFILING_PENDING", "PROFILING_RUNNING"]
PROFILE_STAGE = "METADATA.PROFILES_STAGE"
PROFILE_FORMAT = "METADATA.PROFILE_JSON_FORMAT"
DEFAULT_CONCURRENCY = 5
MAX_CONCURRENCY = 16
OPTION_FIELDS = {"force_refresh", "refresh_tables", "concurrency_limit"}

TableKey = Tuple[str, str, str, str]
ForceRefresh = Union[bool, Collection[str]]


@dataclass(frozen=True)
class TableRef:
    """A table to profile: its source identity (the cache key) and the landed copy that is read."""

    source_name: str
    database: str
    schema: str
    table: str
    landing_fqn: str
    row_count: Optional[int] = None
    columns: Tuple[Tuple[str, str], ...] = ()
    last_altered: Optional[str] = None

    @property
    def key(self) -> TableKey:
        return (self.source_name, self.database, self.schema, self.table)

    @property
    def stage_path(self) -> str:
        return profiler.profile_stage_path(*self.key)

    @property
    def approximate(self) -> bool:
        return profiler.is_large(self.row_count)

    @property
    def fingerprint(self) -> str:
        return profiler.source_fingerprint(self.columns, self.row_count, self.last_altered)

    def source(self) -> Dict[str, str]:
        return {"source_name": self.source_name, "database": self.database, "schema": self.schema,
                "table": self.table}


@dataclass
class TableProfile:
    ref: TableRef
    columns: List[Dict[str, Any]]
    model: Optional[str]
    cached: bool
    approximate: bool
    row_count: int
    persisted: bool = False
    persist_error: Optional[str] = None


def _gather(session, statements: Sequence[Optional[str]], limit: int) -> List[List[Dict[str, Any]]]:
    """Run independent statements with up to `limit` in flight as Snowpark async jobs.
    Falls back to sequential execution where async submission is unavailable."""
    results: List[List[Dict[str, Any]]] = [[] for _ in statements]
    pending = [(i, sql) for i, sql in enumerate(statements) if sql]
    for start in range(0, len(pending), limit):
        jobs = []
        for i, sql in pending[start:start + limit]:
            try:
                jobs.append((i, sql, session.sql(sql).collect_nowait()))
            except Exception:
                jobs.append((i, sql, None))
        for i, sql, job in jobs:
            raw = job.result() if job is not None else session.sql(sql).collect()
            results[i] = [r.as_dict() for r in raw]
    return results


def _compute(session, refs: Sequence[TableRef], limit: int) -> Dict[TableKey, Tuple[List[Dict[str, Any]], int]]:
    """Four statements per table (stats, top values, patterns, histograms), `limit` tables at a time."""
    out: Dict[TableKey, Tuple[List[Dict[str, Any]], int]] = {}
    for start in range(0, len(refs), limit):
        chunk = refs[start:start + limit]
        stats = _gather(session, [profiler.stats_sql(r.landing_fqn, r.columns, r.approximate) for r in chunk], limit)
        details: List[Optional[str]] = []
        for ref, found in zip(chunk, stats):
            specs = profiler.histogram_specs(ref.columns, found[0])
            details += [profiler.frequencies_sql(ref.landing_fqn, ref.columns, approximate=ref.approximate),
                        profiler.patterns_sql(ref.landing_fqn, ref.columns, approximate=ref.approximate),
                        profiler.histogram_sql(ref.landing_fqn, specs, approximate=ref.approximate)]
        detail_rows = _gather(session, details, limit * 3)
        for n, (ref, found) in enumerate(zip(chunk, stats)):
            row = found[0]
            row_count = int(row["ROW_COUNT"])
            freq = profiler.group_rows(detail_rows[3 * n], "V")
            patterns = profiler.group_rows(detail_rows[3 * n + 1], "P")
            hist_counts: Dict[int, Dict[int, int]] = {}
            for h in detail_rows[3 * n + 2]:
                hist_counts.setdefault(int(h["C"]), {})[int(h["B"])] = int(h["N"])
            ranges = {i: (low, high) for i, _, low, high in profiler.histogram_specs(ref.columns, row)}
            columns = []
            for i, (name, data_type) in enumerate(ref.columns):
                stats_i = profiler.column_stats(row, i, row_count, ref.approximate)
                histogram = (profiler.build_histogram(*ranges[i], hist_counts.get(i, {})) if i in ranges else None)
                columns.append(profiler.build_profile(
                    name, data_type, stats_i, freq.get(i, []),
                    [{"pattern": p["value"], "count": p["count"]} for p in patterns.get(i, [])],
                    histogram=histogram, approximate=ref.approximate))
            out[ref.key] = (columns, row_count)
    return out


def _enrich(session, run_id: Optional[str], table: str, profiles: List[Dict[str, Any]],
            skill_excerpt: str) -> Optional[str]:
    """One structured AI_COMPLETE call per table. Failures leave the deterministic profile intact."""
    started = time.time()
    try:
        result, usage, model = complete_json(
            session, profiler.enrichment_prompt(table, profiles, skill_excerpt),
            profiler.ENRICHMENT_SCHEMA, max_tokens=2000)
    except Exception:
        return None
    if run_id:
        record_cost(session, run_id, "PROFILING", model, usage, int((time.time() - started) * 1000), tool_calls=1)
    # The model's answer is untrusted: keep only well-formed items, never fail the profile over one.
    by_name = {str(c["column_name"]).upper(): c for c in (result.get("columns") or [])
               if isinstance(c, dict) and isinstance(c.get("column_name"), str)}
    for p in profiles:
        found = by_name.get(p["column_name"].upper())
        if not found:
            continue
        p["description"] = clip(found.get("description"), 4000)
        suggested = (found.get("semantic_type") or "").upper()
        if p["semantic_type"] in profiler.GENERIC_TYPES and suggested and suggested not in profiler.GENERIC_TYPES:
            p["semantic_type"] = suggested
            if (p.get("pii_classification") or "NONE") == "NONE":  # the model may add PII, never clear it
                p["pii_classification"] = profiler.pii_classification(suggested)
    return model


# ---------------------------------------------------------------- persistent cache


def _load_index(session, refs: Sequence[TableRef]) -> Optional[Dict[TableKey, Dict[str, Any]]]:
    """None when METADATA is not deployed yet: profiling still works, it just does not persist."""
    names = sorted({r.source_name for r in refs})
    if not names:
        return {}
    try:
        found = rows(session, "SELECT * FROM METADATA.TABLE_PROFILES "
                              "WHERE ARRAY_CONTAINS(SOURCE_NAME::VARIANT, PARSE_JSON(?)::ARRAY)", [json.dumps(names)])
    except Exception:
        return None
    return {(r["SOURCE_NAME"], r["DATABASE_NAME"], r["SCHEMA_NAME"], r["TABLE_NAME"]): r for r in found}


def read_document(session, stage_path: str) -> Optional[Dict[str, Any]]:
    assert profiler.STAGE_PATH.match(stage_path or ""), f"unsafe profile path: {stage_path}"
    found = rows(session, f"SELECT $1 AS DOC FROM @{PROFILE_STAGE}/{stage_path} (FILE_FORMAT => '{PROFILE_FORMAT}')")
    doc = variant(found[0]["DOC"]) if found else None
    return doc if isinstance(doc, dict) else None


def _write_document(session, stage_path: str, document: Dict[str, Any]) -> None:
    assert profiler.STAGE_PATH.match(stage_path), f"unsafe profile path: {stage_path}"
    parent, filename = stage_path.rsplit("/", 1)
    tmp = tempfile.mkdtemp()
    try:
        local = Path(tmp) / filename
        local.write_text(json.dumps(document, default=str), encoding="utf-8")
        session.file.put(local.as_posix(), f"@{PROFILE_STAGE}/{parent}", auto_compress=False, overwrite=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _upsert_index(session, ref: TableRef, document: Dict[str, Any], run_id: Optional[str]) -> None:
    session.sql(
        """
        MERGE INTO METADATA.TABLE_PROFILES T
        USING (SELECT ? AS SOURCE_NAME, ? AS DATABASE_NAME, ? AS SCHEMA_NAME, ? AS TABLE_NAME,
                      ?::NUMBER AS ROW_COUNT, ?::NUMBER AS COLUMN_COUNT, ? AS PROFILE_STAGE_PATH,
                      ? AS PROFILE_CHECKSUM, ? AS SOURCE_FINGERPRINT, ?::BOOLEAN AS IS_APPROXIMATE,
                      ? AS PROFILER_VERSION, NULLIF(?, '') AS MODEL_VERSION, NULLIF(?, '') AS PROFILED_IN_RUN,
                      ?::FLOAT AS AVG_NULL_PERCENTAGE, ?::NUMBER AS KEY_CANDIDATES, ?::NUMBER AS PII_COLUMNS,
                      PARSE_JSON(?) AS QUALITY_JSON) S
           ON T.SOURCE_NAME = S.SOURCE_NAME AND T.DATABASE_NAME = S.DATABASE_NAME
          AND T.SCHEMA_NAME = S.SCHEMA_NAME AND T.TABLE_NAME = S.TABLE_NAME
        WHEN MATCHED THEN UPDATE SET
             ROW_COUNT = S.ROW_COUNT, COLUMN_COUNT = S.COLUMN_COUNT, PROFILE_STAGE_PATH = S.PROFILE_STAGE_PATH,
             PROFILE_CHECKSUM = S.PROFILE_CHECKSUM, SOURCE_FINGERPRINT = S.SOURCE_FINGERPRINT,
             IS_APPROXIMATE = S.IS_APPROXIMATE, PROFILER_VERSION = S.PROFILER_VERSION,
             MODEL_VERSION = S.MODEL_VERSION, PROFILED_IN_RUN = S.PROFILED_IN_RUN,
             AVG_NULL_PERCENTAGE = S.AVG_NULL_PERCENTAGE, KEY_CANDIDATES = S.KEY_CANDIDATES,
             PII_COLUMNS = S.PII_COLUMNS, QUALITY_JSON = S.QUALITY_JSON, STATUS = 'STAGED_READY_FOR_MODELING',
             STATUS_UPDATED_AT = CURRENT_TIMESTAMP(), ERROR_MESSAGE = NULL,
             PROFILED_BY = CURRENT_USER(), PROFILED_AT = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT
             (SOURCE_NAME, DATABASE_NAME, SCHEMA_NAME, TABLE_NAME, ROW_COUNT, COLUMN_COUNT, PROFILE_STAGE_PATH,
              PROFILE_CHECKSUM, SOURCE_FINGERPRINT, IS_APPROXIMATE, PROFILER_VERSION, MODEL_VERSION,
              PROFILED_IN_RUN, AVG_NULL_PERCENTAGE, KEY_CANDIDATES, PII_COLUMNS, QUALITY_JSON, STATUS, STATUS_UPDATED_AT,
              PROFILED_BY, PROFILED_AT)
        VALUES (S.SOURCE_NAME, S.DATABASE_NAME, S.SCHEMA_NAME, S.TABLE_NAME, S.ROW_COUNT, S.COLUMN_COUNT,
                S.PROFILE_STAGE_PATH, S.PROFILE_CHECKSUM, S.SOURCE_FINGERPRINT, S.IS_APPROXIMATE,
                S.PROFILER_VERSION, S.MODEL_VERSION, S.PROFILED_IN_RUN, S.AVG_NULL_PERCENTAGE, S.KEY_CANDIDATES,
                S.PII_COLUMNS, S.QUALITY_JSON, 'STAGED_READY_FOR_MODELING', CURRENT_TIMESTAMP(), CURRENT_USER(), CURRENT_TIMESTAMP())
        """,
        params=[ref.source_name, ref.database, ref.schema, ref.table, str(document["row_count"]),
                str(document["column_count"]), ref.stage_path, profiler.document_checksum(document),
                document["fingerprint"], "TRUE" if document["approximate"] else "FALSE",
                document["profiler_version"], document.get("model_version") or "", run_id or "",
                *_summary_metrics(document["columns"]), json.dumps(quality_dimensions(document))],
    ).collect()


def _summary_metrics(columns: Sequence[Dict[str, Any]]) -> List[str]:
    nulls = [float((c.get("statistics") or {}).get("null_percentage") or 0) for c in columns]
    return [str(round(sum(nulls) / len(nulls), 4) if nulls else 0.0),
            str(sum(1 for c in columns if c.get("potential_key"))),
            str(sum(1 for c in columns if (c.get("pii_classification") or "NONE") != "NONE"))]


def _ensure_index_rows(session, refs: Sequence[TableRef]) -> None:
    """placeholder rows so a table profiled for the first time shows up as PROFILING."""
    for ref in refs:
        session.sql(
            """
            MERGE INTO METADATA.TABLE_PROFILES T
            USING (SELECT ? AS SOURCE_NAME, ? AS DATABASE_NAME, ? AS SCHEMA_NAME, ? AS TABLE_NAME,
                          ? AS PROFILE_STAGE_PATH) S
               ON T.SOURCE_NAME = S.SOURCE_NAME AND T.DATABASE_NAME = S.DATABASE_NAME
              AND T.SCHEMA_NAME = S.SCHEMA_NAME AND T.TABLE_NAME = S.TABLE_NAME
            WHEN NOT MATCHED THEN INSERT
                 (SOURCE_NAME, DATABASE_NAME, SCHEMA_NAME, TABLE_NAME, PROFILE_STAGE_PATH, PROFILE_CHECKSUM,
                  SOURCE_FINGERPRINT, PROFILER_VERSION, STATUS, STATUS_UPDATED_AT, PROFILED_BY, PROFILED_AT)
            VALUES (S.SOURCE_NAME, S.DATABASE_NAME, S.SCHEMA_NAME, S.TABLE_NAME, S.PROFILE_STAGE_PATH, '', '', '',
                    'PROFILING', CURRENT_TIMESTAMP(), CURRENT_USER(), CURRENT_TIMESTAMP())
            """,
            params=[*ref.key, ref.stage_path],
        ).collect()


def _set_status(session, refs: Sequence[TableRef], status: str, error: Optional[str] = None) -> None:
    for ref in refs:
        session.sql(
            "UPDATE METADATA.TABLE_PROFILES SET STATUS = ?, STATUS_UPDATED_AT = CURRENT_TIMESTAMP(), "
            "ERROR_MESSAGE = NULLIF(?, '') WHERE SOURCE_NAME = ? AND DATABASE_NAME = ? AND SCHEMA_NAME = ? "
            "AND TABLE_NAME = ?",
            params=[status, clip(error, 4000), *ref.key],
        ).collect()


def _forced(force_refresh: ForceRefresh, ref: TableRef) -> bool:
    if isinstance(force_refresh, bool):
        return force_refresh
    return ref.table in force_refresh


def profile_tables(session, tables: List[TableRef], concurrency_limit: int = DEFAULT_CONCURRENCY,
                   force_refresh: ForceRefresh = False, run_id: Optional[str] = None,
                   guidance: str = "") -> Dict[TableKey, TableProfile]:
    """Profile N tables: fresh cached documents are reused, the rest are computed `concurrency_limit`
    tables at a time, enriched, written to @METADATA.PROFILES_STAGE and upserted into the index."""
    ensure_active(session)
    limit = max(1, min(int(concurrency_limit or DEFAULT_CONCURRENCY), MAX_CONCURRENCY))
    index = _load_index(session, tables)
    out: Dict[TableKey, TableProfile] = {}
    misses: List[TableRef] = []
    for ref in tables:
        entry = None if index is None else index.get(ref.key)
        if not _forced(force_refresh, ref) and profiler.cache_is_fresh(entry, ref.fingerprint):
            try:
                doc = read_document(session, entry["PROFILE_STAGE_PATH"])
            except Exception:
                doc = None
            if doc and [c.get("column_name") for c in doc.get("columns", [])] == [n for n, _ in ref.columns]:
                out[ref.key] = TableProfile(ref, doc["columns"], doc.get("model_version"), True,
                                            bool(doc.get("approximate")), int(doc.get("row_count") or 0), True)
                continue
        misses.append(ref)

    computed = _compute(session, misses, limit)
    stamp = datetime.now(timezone.utc).isoformat()
    for ref in misses:
        columns, row_count = computed[ref.key]
        model = _enrich(session, run_id, ref.table, columns, guidance)
        result = TableProfile(ref, columns, model, False, ref.approximate, row_count)
        if index is not None:
            try:
                doc = profiler.profile_document(ref.source(), row_count, columns, ref.fingerprint,
                                                ref.approximate, model, stamp)
                _write_document(session, ref.stage_path, doc)
                _upsert_index(session, ref, doc, run_id)
                result.persisted = True
            except Exception as exc:
                result.persist_error = clip(exc, 300)
        out[ref.key] = result
    return out


# ---------------------------------------------------------------- run-scoped wiring


def _run_tables(session, run_id: str) -> List[Dict[str, Any]]:
    """Latest successful landing per source object of the run, with its columns and cache identity."""
    tables = rows(session, """
        SELECT L.LANDING_ID, L.SOURCE_TABLE, L.SOURCE_DATABASE, L.SOURCE_SCHEMA, L.LANDING_DATABASE,
               L.LANDING_SCHEMA, L.LANDING_TABLE, L.ROW_COUNT,
               COALESCE(S.SOURCE_SYSTEM_NAME, L.SOURCE_SYSTEM_ID) AS SOURCE_SYSTEM_NAME,
               O.LAST_ALTERED
          FROM SOURCE.LANDING_TABLE_REGISTRY L
          LEFT JOIN SOURCE.SOURCE_REGISTRY S ON S.SOURCE_SYSTEM_ID = L.SOURCE_SYSTEM_ID
          LEFT JOIN (SELECT RUN_ID, OBJECT_NAME, MAX(LAST_ALTERED)::VARCHAR AS LAST_ALTERED
                       FROM SOURCE.SOURCE_OBJECT GROUP BY RUN_ID, OBJECT_NAME) O
                 ON O.RUN_ID = L.RUN_ID AND O.OBJECT_NAME = L.SOURCE_TABLE
         WHERE L.RUN_ID = ? AND L.INGESTION_STATUS = 'COMPLETE'
       QUALIFY ROW_NUMBER() OVER (PARTITION BY L.SOURCE_TABLE ORDER BY L.CREATED_AT DESC) = 1
         ORDER BY L.SOURCE_TABLE""", [run_id])
    for t in tables:
        t["COLUMNS"] = rows(session, """
            SELECT LANDING_COLUMN_ID, COLUMN_NAME, DATA_TYPE FROM SOURCE.LANDING_COLUMN_REGISTRY
             WHERE LANDING_ID = ? ORDER BY ORDINAL_POSITION""", [t["LANDING_ID"]])
        t["REF"] = TableRef(
            source_name=t["SOURCE_SYSTEM_NAME"], database=t["SOURCE_DATABASE"], schema=t["SOURCE_SCHEMA"],
            table=t["SOURCE_TABLE"], landing_fqn=fqn(t["LANDING_DATABASE"], t["LANDING_SCHEMA"], t["LANDING_TABLE"]),
            row_count=None if t["ROW_COUNT"] is None else int(t["ROW_COUNT"]),
            columns=tuple((c["COLUMN_NAME"], c["DATA_TYPE"]) for c in t["COLUMNS"]),
            last_altered=t.get("LAST_ALTERED"))
    return tables


def _bind(profile: TableProfile, table: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run-scoped copy of a (possibly cached) table profile, keyed to this run's landing column ids."""
    by_name = {c["column_name"]: c for c in profile.columns}
    bound = []
    for col in table["COLUMNS"]:
        p = copy.deepcopy(by_name[col["COLUMN_NAME"]])
        p["landing_column_id"] = col["LANDING_COLUMN_ID"]
        bound.append(p)
    return bound


def _foreign_keys(session, tables: List[Dict[str, Any]], profiles: Dict[str, List[Dict[str, Any]]]) -> None:
    """A column is a potential FK when it shares a name with another table's potential key and all its
    values exist there. Recomputed per run because it depends on which tables the run selected."""
    keys = {(t["LANDING_ID"], p["column_name"]): t for t in tables for p in profiles[t["LANDING_ID"]] if p["potential_key"]}
    for t in tables:
        for p in profiles[t["LANDING_ID"]]:
            for (other_id, key_col), other in keys.items():
                if other_id == t["LANDING_ID"] or key_col != p["column_name"] or p["potential_key"]:
                    continue
                child = profiler.sampled(t["REF"].landing_fqn, t["REF"].approximate)
                parent = other["REF"].landing_fqn
                q = quote(key_col)
                try:
                    orphans = scalar(session, f"SELECT COUNT(*) FROM (SELECT {q} AS K FROM {child}) C WHERE C.K IS NOT NULL "
                                              f"AND NOT EXISTS (SELECT 1 FROM {parent} P WHERE P.{q} = C.K)")
                except Exception:
                    continue  # in-place sources may be unreadable by the procedure owner; FKs are only a hint
                if orphans == 0:
                    p["potential_foreign_key"] = f"{other['SOURCE_TABLE']}.{key_col}"


def _column_rules(session, run_id: str, source_table: str) -> Dict[str, Dict[str, Any]]:
    """Accepted COLUMN_RULE knowledge for this run's source table (same source system), by column name."""
    try:
        found = rows(session, """SELECT K.CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE K
                                   JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = ?
                                  WHERE K.IS_CURRENT AND K.STATUS = 'ACTIVE' AND K.KNOWLEDGE_TYPE = 'COLUMN_RULE'
                                    AND K.CONTENT_JSON:source_system_id::STRING = R.SOURCE_SYSTEM_ID
                                    AND UPPER(K.CONTENT_JSON:source_table::STRING) = UPPER(?)""",
                     [run_id, source_table])
    except Exception:
        return {}
    out = {}
    for r in found:
        content = variant(r["CONTENT_JSON"]) or {}
        if content.get("column_name"):
            out[str(content["column_name"]).upper()] = content
    return out


def _store(session, run_id: str, table: Dict[str, Any], profiles: List[Dict[str, Any]], model: Optional[str],
           cache: str) -> None:
    profiler.apply_column_rules(profiles, _column_rules(session, run_id, table["SOURCE_TABLE"]))
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
          {**p["statistics"], "potential_foreign_key": p.get("potential_foreign_key"), "family": p["family"],
           "cache": cache},
          p.get("description"), p["semantic_type"], p["potential_key"], bool(p.get("potential_foreign_key")),
          p["pii_classification"], model] for p in profiles],
    )


def parse_options(options_json: Optional[str]) -> Tuple[ForceRefresh, int]:
    raw = json.loads(options_json) if options_json else {}
    assert isinstance(raw, dict), "OPTIONS_JSON must be a JSON object"
    unknown = set(raw) - OPTION_FIELDS
    assert not unknown, f"unknown options: {sorted(unknown)}"
    tables = raw.get("refresh_tables") or []
    assert isinstance(tables, list) and all(isinstance(t, str) for t in tables), \
        "refresh_tables must be a list of source table names"
    limit = int(raw.get("concurrency_limit") or DEFAULT_CONCURRENCY)
    assert 1 <= limit <= MAX_CONCURRENCY, f"concurrency_limit must be 1-{MAX_CONCURRENCY}"
    force: ForceRefresh = True if raw.get("force_refresh") else set(tables)
    return force, limit


def _profile_run(session, run_id: str, tables: List[Dict[str, Any]], force: ForceRefresh,
                 limit: int) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[TableKey, TableProfile]]:
    guidance = use_skills(session, STAGE_SKILLS["PROFILING"])
    results = profile_tables(session, [t["REF"] for t in tables], limit, force, run_id, guidance)
    profiles = {t["LANDING_ID"]: _bind(results[t["REF"].key], t) for t in tables}
    _foreign_keys(session, tables, profiles)
    return profiles, results


def _table_summary(table: Dict[str, Any], profiles: List[Dict[str, Any]], result: TableProfile) -> Dict[str, Any]:
    return {"table": table["SOURCE_TABLE"], "columns": len(profiles),
            "pii_columns": sum(p["pii_classification"] != "NONE" for p in profiles),
            "enriched_by": result.model, "cache": "HIT" if result.cached else "MISS",
            "approximate": result.approximate, "persisted": result.persisted,
            **({"persist_error": result.persist_error} if result.persist_error else {})}


def run_profiling(session, run_id: str, options_json: Optional[str] = None) -> Dict[str, Any]:
    force, limit = parse_options(options_json)
    stage = Stage(session, run_id)
    stage.require(*PATH[:2])
    stage.walk(PATH, "profiling started")
    tables = _run_tables(session, run_id)
    summary: Dict[str, Any] = {"tables": [], "cache_hits": 0, "computed": 0}
    with tool_call(session, run_id, "profile_table", {"tables": [t["SOURCE_TABLE"] for t in tables],
                                                       "concurrency_limit": limit}) as call:
        try:
            assert tables, "no successfully landed tables for this run"
            profiles, results = _profile_run(session, run_id, tables, force, limit)
            for t in tables:
                result = results[t["REF"].key]
                _store(session, run_id, t, profiles[t["LANDING_ID"]], result.model,
                       "HIT" if result.cached else "MISS")
                summary["tables"].append(_table_summary(t, profiles[t["LANDING_ID"]], result))
            summary["cache_hits"] = sum(1 for t in summary["tables"] if t["cache"] == "HIT")
            summary["computed"] = len(summary["tables"]) - summary["cache_hits"]
        except Exception as exc:
            call.status, call.error = "FAILED", clip(exc)
            stage.fail(exc)
            return {"profiled": summary, "state": stage.payload()}
        call.summary = (f"{len(tables)} tables ({summary['cache_hits']} from cache); "
                        + "; ".join(f"{t['table']}: {t['columns']} columns, {t['pii_columns']} PII"
                                    for t in summary["tables"][:20]))
    stage.move("PROFILING_COMPLETE", f"profiled {len(tables)} tables ({summary['cache_hits']} from cache)", summary)
    try:
        from services.knowledge.procedures import identify_domain
        identified = identify_domain(session, run_id)
        return {"profiled": summary, "domain": identified.get("domain"), "state": identified.get("state") or stage.payload()}
    except Exception:
        return {"profiled": summary, "state": stage.payload()}


def refresh_table_profile(session, run_id: str, source_table: str) -> Dict[str, Any]:
    """Bust the cache for one table and re-profile it from the run's landed copy. No state change; the run's
    PROFILE_REGISTRY rows get a new version only if the run was already profiled."""
    run = _get_run(session, run_id)
    assert not _is_closed(run), "run is archived or deleted; restore it first"
    tables = _run_tables(session, run_id)
    target = [t for t in tables if t["SOURCE_TABLE"] == source_table]
    assert target, f"{source_table} has no completed landing in this run"
    with tool_call(session, run_id, "refresh_table_profile", {"table": source_table}) as call:
        profiles, results = _profile_run(session, run_id, tables, {source_table}, DEFAULT_CONCURRENCY)
        table = target[0]
        result = results[table["REF"].key]
        profiled = scalar(session, "SELECT COUNT(*) FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = ? AND SOURCE_TABLE_ID = ?",
                          [run_id, table["LANDING_ID"]])
        if profiled:
            _store(session, run_id, table, profiles[table["LANDING_ID"]], result.model, "REFRESHED")
        call.summary = f"{source_table}: re-profiled, {len(profiles[table['LANDING_ID']])} columns"
    return {**_table_summary(table, profiles[table["LANDING_ID"]], result), "cache": "REFRESHED",
            "registry_updated": bool(profiled)}


def run_profiling_basic(session, run_id: str) -> Dict[str, Any]:
    """One-argument RUN_PROFILING; Snowflake requires the handler arity to match the signature."""
    return run_profiling(session, run_id)


# ---------------------------------------------------------------- in-place source profiling (no landing copy)


def source_table_refs(session, source_id: str,
                      tables: Optional[Sequence[str]] = None) -> Tuple[Dict[str, Any], List[TableRef]]:
    """TableRefs pointing at the source tables themselves, built from INFORMATION_SCHEMA the same way landing
    registers them, so a later run over the same tables hits the same cache entries."""
    from services.source.adapters import adapter_for
    from services.source.identifiers import format_data_type

    found = rows(session, "SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, SOURCE_TYPE, CONFIGURATION_JSON "
                          "FROM SOURCE.SOURCE_REGISTRY WHERE SOURCE_SYSTEM_ID = ? AND ACTIVE_FLAG", [source_id])
    assert found, f"source {source_id} not found"
    source = found[0]
    config = variant(source["CONFIGURATION_JSON"]) or {}
    adapter = adapter_for(source["SOURCE_TYPE"], config["database"], config["schema"])
    run = lambda sql, params: rows(session, sql, params)  # noqa: E731
    objects = {o.name: o for o in adapter.discover_objects(run)}
    columns: Dict[str, List[Tuple[str, str]]] = {}
    for c in run(adapter.columns_sql(), [adapter.schema]):
        columns.setdefault(c["TABLE_NAME"], []).append(
            (c["COLUMN_NAME"], format_data_type(c["DATA_TYPE"], c["CHARACTER_MAXIMUM_LENGTH"],
                                                c["NUMERIC_PRECISION"], c["NUMERIC_SCALE"])))
    wanted = list(tables) if tables else sorted(objects)
    missing = [t for t in wanted if t not in objects]
    assert not missing, f"not in {adapter.database}.{adapter.schema}: {missing[:10]}"
    refs = [TableRef(source["SOURCE_SYSTEM_NAME"], adapter.database, adapter.schema, t, adapter.object_fqn(t),
                     objects[t].row_count, tuple(columns.get(t, ())), objects[t].last_altered) for t in wanted]
    return {"source_system_id": source["SOURCE_SYSTEM_ID"], "source_system_name": source["SOURCE_SYSTEM_NAME"],
            "database": adapter.database, "schema": adapter.schema}, refs


def profile_source_tables(session, source_id: str, payload_json: str) -> Dict[str, Any]:
    """Profile selected tables of a registered source in place: read-only queries against the source, results
    written only to @METADATA.PROFILES_STAGE and METADATA.TABLE_PROFILES. Nothing is copied."""
    payload = json.loads(payload_json or "{}")
    tables = payload.get("tables") or []
    assert isinstance(tables, list) and tables and all(isinstance(t, str) for t in tables), \
        "tables must be a non-empty list of table names"
    assert len(tables) <= 500, "profile at most 500 tables per request"
    force = bool(payload.get("force_refresh"))
    limit = max(1, min(int(payload.get("concurrency_limit") or DEFAULT_CONCURRENCY), MAX_CONCURRENCY))
    source, refs = source_table_refs(session, source_id, sorted(set(tables)))
    _ensure_index_rows(session, refs)
    _set_status(session, refs, "PROFILING")
    try:
        guidance = use_skills(session, STAGE_SKILLS["PROFILING"])
    except Exception:
        guidance = ""

    out: Dict[str, Any] = {**source, "profiled": [], "cached": [], "failed": []}

    def settle(chunk: List[TableRef]) -> None:
        results = profile_tables(session, chunk, limit, force, None, guidance)
        for ref in chunk:
            r = results[ref.key]
            if r.cached:
                _set_status(session, [ref], "STAGED_READY_FOR_MODELING")
                out["cached"].append(ref.table)
            elif r.persisted:
                out["profiled"].append(ref.table)
            else:
                _set_status(session, [ref], "FAILED", r.persist_error or "profile could not be stored")
                out["failed"].append({"table": ref.table, "error": r.persist_error})

    for start in range(0, len(refs), limit):
        chunk = refs[start:start + limit]
        try:
            settle(chunk)
        except Exception:
            for ref in chunk:
                try:
                    settle([ref])
                except Exception as exc:
                    _set_status(session, [ref], "FAILED", f"{type(exc).__name__}: {exc}")
                    out["failed"].append({"table": ref.table, "error": clip(exc, 400)})
    return out
