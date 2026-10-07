"""Load everything the dbt-onboard-source engine needs for a run (STTM, target DDL, landed source types, joins).

`query(sql, params)` returns rows as dicts with UPPER-case keys and uses `?` placeholders, so the same loader
serves the Snowpark procedure and the API-side overlay.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from services.source.er_graph import infer_joins

Query = Callable[[str, list], List[Dict[str, Any]]]
SAFE = re.compile(r"^[A-Za-z0-9_$]+$")


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _type(row: Dict[str, Any]) -> str:
    dtype = str(row.get("DATA_TYPE") or "")
    if dtype == "NUMBER" and row.get("NUMERIC_PRECISION") is not None:
        return f"NUMBER({row['NUMERIC_PRECISION']},{row.get('NUMERIC_SCALE') or 0})"
    return dtype


def source_key_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "src").lower()).strip("_") or "src"


def load_inputs(query: Query, run_id: str, plan: Dict[str, Any], sttm: Dict[str, Any],
                lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    run = (query("""SELECT R.RUN_NAME, R.TARGET_MODEL, R.DOMAIN_ID, D.DOMAIN_NAME, S.SOURCE_SYSTEM_NAME
                      FROM CORE.WORKFLOW_RUN R
                      LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
                      LEFT JOIN SOURCE.SOURCE_REGISTRY S ON S.SOURCE_SYSTEM_ID = R.SOURCE_SYSTEM_ID
                     WHERE R.RUN_ID = ?""", [run_id]) or [{}])[0]
    design = sttm.get("table_design") or {}
    target = design.get("target_table") or str(run.get("TARGET_MODEL") or "target").split(".")[-1]
    landed = query("""SELECT SOURCE_TABLE, LANDING_TABLE, LANDING_DATABASE, LANDING_SCHEMA
                        FROM SOURCE.LANDING_TABLE_REGISTRY
                       WHERE RUN_ID = ? AND INGESTION_STATUS = 'COMPLETE'
                     QUALIFY ROW_NUMBER() OVER (PARTITION BY SOURCE_TABLE ORDER BY CREATED_AT DESC) = 1""", [run_id])
    sources = []
    from services.source.identifiers import quote

    for row in landed:
        db, schema, table = row.get("LANDING_DATABASE"), row.get("LANDING_SCHEMA"), row.get("LANDING_TABLE")
        columns: Dict[str, str] = {}
        names: Dict[str, str] = {}
        if db and schema and table:
            try:
                for col in query(f"""SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE
                                      FROM {quote(str(db))}.INFORMATION_SCHEMA.COLUMNS
                                     WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION""",
                                 [schema, table]):
                    columns[str(col["COLUMN_NAME"]).upper()] = _type(col)
                    names[str(col["COLUMN_NAME"]).upper()] = str(col["COLUMN_NAME"])
            except Exception:
                columns, names = {}, {}
        # columns is keyed upper case for matching; names maps back to the exact stored spelling for SQL.
        sources.append({"name": str(row["SOURCE_TABLE"]).upper(), "identifier": table, "database": db,
                        "schema": schema, "columns": columns, "names": names})
    target_columns: List[Dict[str, Any]] = []
    try:
        target_columns = [{
            "column_name": r["COLUMN_NAME"], "data_type": r["DATA_TYPE"], "nullable": r.get("NULLABLE"),
            "is_business_key": r.get("IS_BUSINESS_KEY"), "is_pii": r.get("IS_PII"),
            "business_definition": r.get("BUSINESS_DEFINITION"), "accepted_values": _json(r.get("ACCEPTED_VALUES")) or [],
        } for r in query("""SELECT C.COLUMN_NAME, C.DATA_TYPE, C.NULLABLE, C.IS_BUSINESS_KEY, C.IS_PII,
                                   C.BUSINESS_DEFINITION, C.ACCEPTED_VALUES
                              FROM KNOWLEDGE.TARGET_TABLE_REGISTRY T
                              JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY C ON C.TARGET_TABLE_ID = T.TARGET_TABLE_ID
                             WHERE T.ACTIVE_FLAG AND T.DOMAIN_ID = ? AND UPPER(T.TARGET_TABLE) = UPPER(?)
                             ORDER BY C.ORDINAL_POSITION""", [run.get("DOMAIN_ID"), target])]
    except Exception:
        target_columns = []
    model_spec: Dict[str, Any] = {}
    try:
        found = query("""SELECT MODEL_SPEC FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                          WHERE ACTIVE_FLAG AND DOMAIN_ID = ? AND UPPER(TARGET_TABLE) = UPPER(?)
                          ORDER BY TARGET_TABLE LIMIT 1""", [run.get("DOMAIN_ID"), target])
        model_spec = _json(found[0].get("MODEL_SPEC")) if found else {}
    except Exception:
        model_spec = {}
    tables = {s["name"]: [{"column_name": c, "data_type": t.split("(")[0]} for c, t in s["columns"].items()]
              for s in sources}
    system = str(run.get("SOURCE_SYSTEM_NAME") or "SOURCE")
    return {
        "domain": plan.get("domain_folder") or run.get("DOMAIN_NAME") or "gdp",
        "target": target,
        "prefix": plan.get("prefix") if plan.get("prefix") is not None else "GDP",
        "source_key": plan.get("source_key") or source_key_of(system),
        "source_system": system.upper(),
        "business_keys": design.get("business_keys") or [],
        "grain": design.get("grain") or "",
        "sources": sources,
        "target_columns": target_columns,
        "lines": lines,
        "joins": _planned_joins(design) or (infer_joins(tables) if len(tables) > 1 else []),
        "primary": (design.get("join_graph") or {}).get("driving_table") or design.get("driving_table"),
        "model_spec": model_spec or {},
    }


def _planned_joins(design: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Joins the reviewer saw in the STTM; dbt follows them instead of re-inferring from column names."""
    from services.sttm.join_graph import to_dbt_joins

    graph = design.get("join_graph") or {}
    return to_dbt_joins(graph) if graph.get("joins") else []


def upper_rows(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{str(k).upper(): v for k, v in r.items()} for r in raw]


def db_query(db) -> Query:
    """Adapt the API's pyformat Db to the loader's `?` placeholders."""
    return lambda sql, params: upper_rows(db.query(sql.replace("?", "%s"), tuple(params)))


def session_query(session, rows_fn) -> Query:
    return lambda sql, params: rows_fn(session, sql, params)


def assemble(inputs: Dict[str, Any], sttm: Dict[str, Any], soda_yaml: str,
             skeleton: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Engine output + STTM exports + Soda checks + the generation report, overlaid on the skeleton."""
    from services.dbt.onboard import generate
    from services.sttm.refine import render_csv

    out = generate(inputs, skeleton or {})
    lines = inputs.get("lines") or []
    files = dict(out["files"])
    files["mappings/sttm.json"] = json.dumps({
        "sttm_id": sttm.get("sttm_id"), "table_design": sttm.get("table_design"),
        "lines": [{k: l.get(k) for k in ("target_column", "target_datatype", "source_table", "source_column",
                                         "mapping_type", "transformation", "business_definition")} for l in lines],
    }, indent=2, default=str)
    files["mappings/sttm.csv"] = render_csv([{k: l.get(k) for k in (
        "target_column", "target_datatype", "mapping_type", "source_table", "source_column", "source_datatype",
        "transformation", "business_definition")} for l in lines])
    files["soda/checks.yml"] = soda_yaml or "# no soda checks\n"
    report = {**out["report"], "sttm_id": sttm.get("sttm_id"), "skeleton_files": len(skeleton or {})}
    files["release/generation-report.json"] = json.dumps(report, indent=2, default=str)
    originals = {p: (skeleton or {})[p] for p, s in report["files"].items() if s == "patched"}
    if originals:
        files["release/skeleton-base.json"] = json.dumps(originals)
    merged = dict(skeleton or {})
    merged.update(files)
    return {"files": merged, "generated": files, "report": report}
