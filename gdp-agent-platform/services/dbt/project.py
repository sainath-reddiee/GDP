"""Deterministic dbt project from an approved STTM (pure). No LLM, no web search."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from services.sttm.refine import render_csv

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def snake(name: str) -> str:
    return re.sub(r"[\W]+", "_", (name or "").strip()).strip("_").lower() or "x"


def safe_ident(name: str) -> str:
    value = snake(name)
    assert IDENT.match(value), f"unsafe identifier: {name}"
    return value


def _col(name: str) -> str:
    return safe_ident(name)


def _cast(expr: str, data_type: str) -> str:
    dtype = (data_type or "VARCHAR").split("(")[0].upper()
    if dtype in {"VARCHAR", "STRING", "TEXT"}:
        return f"CAST({expr} AS VARCHAR)"
    if dtype in {"NUMBER", "NUMERIC", "DECIMAL", "INT", "INTEGER", "BIGINT", "FLOAT"}:
        return f"CAST({expr} AS {data_type})"
    if dtype == "DATE":
        return f"CAST({expr} AS DATE)" if "TO_DATE" not in expr.upper() and "CAST(" not in expr.upper() else expr
    if dtype.startswith("TIMESTAMP"):
        return f"CAST({expr} AS TIMESTAMP_NTZ)" if "CAST(" not in expr.upper() else expr
    if dtype == "BOOLEAN":
        return f"CAST({expr} AS BOOLEAN)"
    return f"CAST({expr} AS {data_type})"


def _source_ref(line: Dict[str, Any]) -> str:
    return f"{_col(line['source_column'])}"


def staging_sql(source_name: str, table: str, columns: List[str], landing_ref: str) -> str:
    selects = [f"    {c} as {c}" for c in columns]
    return (
        f"-- staging: rename only; no casts, no business rules\n"
        f"select\n" + ",\n".join(selects) + "\n"
        f"from {landing_ref}\n"
    )


def intermediate_sql(stg_model: str, keys: List[str], order_col: Optional[str]) -> str:
    partition = ", ".join(_col(k) for k in keys) or "1"
    order = _col(order_col) if order_col else "1"
    return (
        f"-- keep the latest row per business key\n"
        f"select *\nfrom {{{{ ref('{stg_model}') }}}}\n"
        f"qualify row_number() over (partition by {partition} order by {order} desc) = 1\n"
    )


def mart_sql(int_model: str, lines: List[Dict[str, Any]], keys: List[str],
             incremental: bool = True, contract_id: str = "") -> str:
    unique = ", ".join(_col(k) for k in keys) or "customer_id"
    config = (
        f"{{{{ config(materialized='incremental', unique_key='{unique}', "
        f"incremental_strategy='merge', on_schema_change='fail') }}}}\n\n"
        if incremental else ""
    )
    if contract_id:
        config += f"-- generated from approved mapping document {contract_id}\n"
    selects = []
    for line in lines:
        target = _col(line["target_column"])
        mapping = (line.get("mapping_type") or "").upper()
        expr = (line.get("transformation") or "").strip()
        if mapping == "UNMAPPED":
            expr = "null"
        elif not expr:
            src = line.get("source_column")
            expr = _col(src) if src else "null"
        if mapping != "UNMAPPED" and "CAST(" not in expr.upper() and line.get("target_datatype"):
            if mapping != "DERIVED" or "CURRENT_TIMESTAMP" not in expr.upper():
                if mapping in {"DIRECT", "TRANSFORM"} or (mapping == "DERIVED" and "MD5" not in expr.upper()):
                    if "CURRENT_TIMESTAMP" not in expr.upper() and not expr.upper().startswith("'"):
                        expr = _cast(expr, line["target_datatype"])
        selects.append(f"    {expr} as {target}")
    body = "select\n" + ",\n".join(selects) + f"\nfrom {{{{ ref('{int_model}') }}}}\n"
    return config + body


def sources_yml(source_name: str, database: str, schema: str, tables: List[Dict[str, str]]) -> str:
    table_blocks = []
    for t in tables:
        table_blocks.append(f"      - name: {safe_ident(t['name'])}\n        identifier: {t['identifier']}")
    return (
        "version: 2\n\n"
        "sources:\n"
        f"  - name: {safe_ident(source_name)}\n"
        f"    database: {database}\n"
        f"    schema: {schema}\n"
        "    tables:\n" + "\n".join(table_blocks) + "\n"
    )


def schema_yml(model: str, lines: List[Dict[str, Any]], grain: str) -> str:
    cols = []
    for line in lines:
        tests = []
        if line.get("uniqueness_rule"):
            tests.append("          - unique")
        if not line.get("nullable_rule"):
            tests.append("          - not_null")
        if line.get("accepted_values"):
            values = ", ".join(json.dumps(v) for v in line["accepted_values"])
            tests.append("          - accepted_values:\n              values: [" + values + "]")
        test_block = ("\n        tests:\n" + "\n".join(tests)) if tests else ""
        desc = (line.get("business_definition") or "").replace("\n", " ")
        cols.append(f"      - name: {_col(line['target_column'])}\n        description: {json.dumps(desc)}{test_block}")
    return (
        "version: 2\n\nmodels:\n"
        f"  - name: {model}\n"
        f"    description: {json.dumps(grain or model)}\n"
        "    columns:\n" + "\n".join(cols) + "\n"
    )


def project_yml(project_name: str) -> str:
    return (
        f"# Skills applied: DBT-ONBOARD-SOURCE, SILVER-MODEL, GDP_DOMAIN_SKILL.\n"
        f"# Compile-only. Do not execute Iceberg DDL, watermark inserts, or gold readiness from those skills.\n"
        f"name: {project_name}\n"
        "version: '1.0.0'\n"
        "config-version: 2\n"
        "profile: gdp_platform\n"
        "model-paths: [\"models\"]\n"
        "macro-paths: [\"macros\"]\n"
        "target-path: \"target\"\n"
        "clean-targets: [\"target\"]\n"
        "models:\n"
        f"  {project_name}:\n"
        "    staging:\n      +materialized: view\n"
        "    intermediate:\n      +materialized: ephemeral\n"
        "    marts:\n      +materialized: incremental\n      +incremental_strategy: merge\n"
        "      +on_schema_change: fail\n"
    )


def build(sttm: Dict[str, Any], macros: List[Dict[str, str]], soda_yaml: str,
          landing_tables: List[Dict[str, str]], source_system: str) -> Dict[str, str]:
    """Return {relative_path: content} for a compile-ready dbt project."""
    design = sttm["table_design"]
    lines = sttm["lines"]
    project = snake(f"gdp_{source_system}")
    src = safe_ident(source_system)
    source_tables = sorted({l["source_table"] for l in lines if l.get("source_table")})
    primary = snake(source_tables[0]) if source_tables else "source"
    stg = f"stg_{src}__{primary}"
    entity = snake((design.get("target_table") or "customer").replace("DIM_", "").replace("FCT_", ""))
    intermediate = f"int_{entity}__deduped"
    mart = snake(design.get("target_table") or "dim_customer")
    keys = [snake(k) for k in (design.get("business_keys") or [])]
    landing_db = landing_tables[0]["database"] if landing_tables else "LANDING"
    landing_schema = landing_tables[0]["schema"] if landing_tables else "LANDING"

    # Staging columns: unique source columns referenced by the STTM, lower-cased.
    stg_cols: List[str] = []
    for line in lines:
        if line.get("source_column"):
            name = _col(line["source_column"])
            if name not in stg_cols:
                stg_cols.append(name)
    if keys:
        for k in keys:
            if k not in stg_cols and any(_col(l.get("source_column") or "") == k for l in lines):
                pass

    landing_idents = [{"name": snake(t["source_table"]), "identifier": t["landing_table"]} for t in landing_tables]
    if not landing_idents and source_tables:
        landing_idents = [{"name": snake(t), "identifier": t} for t in source_tables]

    source_rel = f"{{{{ source('{src}', '{snake(source_tables[0]) if source_tables else primary}') }}}}"
    files: Dict[str, str] = {
        "dbt_project.yml": project_yml(project),
        f"models/staging/_sources.yml": sources_yml(src, landing_db, landing_schema, landing_idents),
        f"models/staging/{stg}.sql": staging_sql(src, primary, stg_cols, source_rel),
        f"models/intermediate/{intermediate}.sql": intermediate_sql(stg, keys, keys[0] if keys else None),
        f"models/marts/gdp/{mart}.sql": mart_sql(intermediate, lines, design.get("business_keys") or [],
                                                 contract_id=str(sttm.get("sttm_id") or "")),
        f"models/marts/gdp/_schema.yml": schema_yml(mart, lines, design.get("grain") or ""),
        "mappings/sttm.json": json.dumps({
            "sttm_id": sttm.get("sttm_id"),
            "table_design": design,
            "lines": [{k: l.get(k) for k in (
                "target_column", "target_datatype", "source_table", "source_column", "source_datatype",
                "mapping_type", "transformation", "business_definition")} for l in lines],
        }, indent=2),
        "mappings/sttm.csv": render_csv([{
            "target_column": l.get("target_column"), "target_datatype": l.get("target_datatype"),
            "mapping_type": l.get("mapping_type"), "source_table": l.get("source_table"),
            "source_column": l.get("source_column"), "source_datatype": l.get("source_datatype"),
            "transformation": l.get("transformation"), "business_definition": l.get("business_definition"),
        } for l in lines]),
        "soda/checks.yml": soda_yaml or "# no soda checks\n",
    }
    for macro in macros:
        files[f"macros/{safe_ident(macro['name'])}.sql"] = macro["sql"]
    return files
