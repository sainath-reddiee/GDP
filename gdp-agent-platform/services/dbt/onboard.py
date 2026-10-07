"""dbt-onboard-source as code: deterministic silver-hub generation from an approved STTM.

Implements the GDP `dbt-onboard-source` skill (SKILL.md + references/sttm-mapping-rules.md + assets/):
bronze source YAML -> ephemeral silver staging per source -> silver hub (SCD1, incremental) + domain macros,
with the rulebook's transformation classes, NULLIF(TRIM()) text rule, casts only on type mismatch, compound
SOURCE_UNIQUE_ID, HKEY over business columns, zone/DDL column order, and idempotent patching of an existing
hub model from the cut-from branch. Pure: no Snowflake or LLM calls; `inputs` are loaded by services.dbt.inputs.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.common.standard import GDP, conventions, fill
from services.knowledge.contracts import ref_lookup
from services.common.rules import ends_with_hint, rule

ENGINE = "dbt-onboard-source/engine-v1"
SIMPLE = re.compile(r"^[A-Z_][A-Z0-9_$]*$")
AUDIT_SUFFIXES = ("IS_ACTIVE", "INSERTED_TS", "INSERTED_BY", "UPDATED_TS", "UPDATED_BY", "ROW_HASH")
TODO_HINTS = {
    "STANDARDIZATION": re.compile(r"standardi[sz]|geocod|downstream", re.I),
    "LOV": re.compile(r"\bLOV\b|list of values", re.I),
}


# ----------------------------------------------------------------------------------------------- naming

def snake(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_") or "x"


def real_name(sources: Dict[str, Dict[str, Any]], table: str, column: str) -> str:
    """Exact stored spelling of a source column (matching is case-insensitive, SQL must not be)."""
    names = (sources.get(str(table).upper()) or {}).get("names") or {}
    return names.get(str(column).upper(), str(column))


def quote(col: str) -> str:
    """Bronze column reference: plain when it is a simple UPPER identifier, else double-quoted."""
    value = (col or "").strip().strip('"')
    assert value and '"' not in value, f"unsafe column: {col}"
    return value if SIMPLE.match(value) else f'"{value}"'


# ----------------------------------------------------------------------------------------------- types

def family(data_type: str) -> str:
    t = (data_type or "").upper().split("(")[0].strip()
    if t in {"TEXT", "VARCHAR", "STRING", "CHAR", "CHARACTER", "NVARCHAR", "NCHAR"}:
        return "text"
    if t in {"FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION", "REAL"}:
        return "float"
    if t in {"NUMBER", "NUMERIC", "DECIMAL", "INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT", "FIXED", "LONG"}:
        return "number"
    if t == "DATE":
        return "date"
    if t.startswith("TIMESTAMP") or t == "DATETIME":
        return "timestamp"
    if t == "BOOLEAN":
        return "boolean"
    if t in {"GEOGRAPHY", "GEOMETRY"}:
        return "geo"
    if t in {"VARIANT", "OBJECT", "ARRAY"}:
        return "semi"
    if t in {"BINARY", "VARBINARY"}:
        return "binary"
    return t.lower() or "unknown"


def _precision(data_type: str) -> Tuple[Optional[int], Optional[int]]:
    m = re.search(r"\((\d+)\s*(?:,\s*(\d+))?\)", data_type or "")
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (None, None)


def conform(expr: str, source_type: str, target_type: str) -> Tuple[str, Optional[str]]:
    """Rule 2: cast ONLY where the bronze type differs from the silver target type. Returns (expr, cast_note)."""
    src, tgt = family(source_type), family(target_type)
    if not target_type or not source_type or src == tgt and src != "number":
        return expr, None
    t = target_type.strip()
    if src == "number" and tgt == "number":
        if _precision(source_type) != _precision(target_type) and _precision(target_type)[0] is not None:
            return f"{expr}::{t.lower()}", f"NUMBER precision {source_type} -> {t}"
        return expr, None
    if tgt == "float" and src == "number":
        return f"{expr}::float", "NUMBER -> FLOAT"
    if tgt == "number" and src == "float":
        return f"{expr}::{t.lower()}", "FLOAT -> NUMBER"
    if tgt == "number" and src == "text":
        p, s = _precision(t)
        args = f", {p}, {s}" if p is not None else ""
        return f"try_to_number({expr}{args})", "TEXT -> NUMBER (try_to_number)"
    if tgt == "float" and src == "text":
        return f"try_to_double({expr})", "TEXT -> FLOAT (try_to_double)"
    if tgt == "timestamp" and src == "date":
        return f"{expr}::timestamp_ntz", "DATE -> TIMESTAMP_NTZ"
    if tgt == "timestamp" and src == "text":
        return f"try_to_timestamp_ntz({expr})", "TEXT -> TIMESTAMP (try_to_timestamp_ntz)"
    if tgt == "timestamp" and src == "timestamp":
        return expr, None
    if tgt == "date" and src == "timestamp":
        return f"{expr}::date", "TIMESTAMP -> DATE"
    if tgt == "date" and src == "text":
        return f"try_to_date({expr})", "TEXT -> DATE (try_to_date)"
    if tgt == "boolean" and src == "text":
        return f"try_to_boolean({expr})", "TEXT -> BOOLEAN (try_to_boolean)"
    if tgt == "boolean" and src == "number":
        return f"({expr} <> 0)", "NUMBER -> BOOLEAN"
    if tgt == "text" and src != "text":
        return f"{expr}::varchar", f"{src.upper()} -> VARCHAR"
    if tgt == "geo" and src == "text":
        return f"try_to_geography({expr})", "TEXT -> GEOGRAPHY"
    return expr, None


# ----------------------------------------------------------------------------------------------- classification

def audit_role(column: str, prefix: str) -> Optional[str]:
    """`GDP_INSERTED_TS` -> 'INSERTED_TS' when it is a {PREFIX}_ audit column (or ETL_CREATED_TS)."""
    name = (column or "").upper()
    if name == "ETL_CREATED_TS":
        return "INSERTED_TS"
    pre = f"{prefix.upper()}_" if prefix else ""
    for suffix in AUDIT_SUFFIXES:
        if name == f"{pre}{suffix}" or (not prefix and name.endswith(f"_{suffix}") and name.count("_") <= 2):
            return suffix
    return None


def classify(line: Dict[str, Any], target: str, prefix: str, source_system_col: str) -> str:
    """The rulebook classes (references/sttm-mapping-rules.md)."""
    col = str(line.get("target_column") or "").upper()
    text = str(line.get("transformation") or "")
    mapping = str(line.get("mapping_type") or "").upper()
    if audit_role(col, prefix) or "ETL AUDIT" in text.upper():
        return "AUDIT"
    if "AUTO INCREMENT" in text.upper() or "AUTO_INCREMENT" in text.upper() or col == f"{target.upper()}_SKEY":
        return "SEQUENCE"
    if col == source_system_col or ("SOURCE_SYSTEM" in col and col.endswith("_SKEY")):
        return "SOURCE_SYSTEM_REF"
    if col == "SOURCE_UNIQUE_ID":
        return "COMPOUND_PK"
    if col.endswith("_HKEY"):
        return "HKEY"
    if col.endswith("_CORE_SKEY"):
        return "HUB_FK"
    if col.endswith("_SKEY"):
        return "FK_LOOKUP"
    for name, pattern in TODO_HINTS.items():
        if pattern.search(text):
            return name
    if mapping == "UNMAPPED" or (not line.get("source_column") and not text.strip()):
        return "UNMAPPED"
    if text.strip() and mapping in {"DERIVED", "TRANSFORM", "CONSTANT", ""} and text.strip().upper() != str(
            line.get("source_column") or "").upper():
        return "DERIVED"
    return "PASSTHROUGH"


# ----------------------------------------------------------------------------------------------- plan

def _ordered_targets(lines: List[Dict[str, Any]], target_columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Target columns in DDL order (registry ORDINAL_POSITION), falling back to STTM order."""
    by_name = {str(l.get("target_column") or "").upper(): l for l in lines}
    out: List[Dict[str, Any]] = []
    for col in target_columns or []:
        name = str(col.get("column_name") or "").upper()
        line = dict(by_name.get(name) or {"target_column": name, "mapping_type": "UNMAPPED"})
        line.setdefault("target_datatype", col.get("data_type"))
        if not line.get("target_datatype"):
            line["target_datatype"] = col.get("data_type")
        line["_registry"] = col
        out.append(line)
    seen = {str(o["target_column"]).upper() for o in out}
    out += [dict(l) for l in lines if str(l.get("target_column") or "").upper() not in seen]
    return out


class NoUniqueKey(ValueError):
    """No confirmed unique key for the record: generating anyway would silently collapse rows in dedup."""


def _unique_key(cols: List[Dict[str, Any]], business_keys: List[str], sources: Dict[str, Dict[str, Any]],
                primary: str, key_candidates: Optional[Dict[str, List[str]]] = None
                ) -> Tuple[List[Dict[str, Any]], str]:
    """Columns forming SOURCE_UNIQUE_ID, only from confirmed keys: STTM business keys -> target registry business
    keys -> a column the profiler measured as unique on the driving table. A key-like name alone is used only when
    the run has no profiles at all. Without any of these generation stops instead of guessing."""
    keys = [k.upper() for k in business_keys or []]
    picked = [c for c in cols if str(c["target_column"]).upper() in keys and c.get("source_column")]
    reason = "STTM business keys"
    if not picked:
        picked = [c for c in cols if (c.get("_registry") or {}).get("is_business_key") and c.get("source_column")]
        reason = "target registry business keys"
    on_primary = [c for c in cols if c.get("source_column") and (c.get("source_table") or primary).upper() == primary]
    if not picked and key_candidates is not None:
        unique = {str(k).upper() for k in key_candidates.get(primary, [])}
        measured = [c for c in on_primary if str(c["source_column"]).upper() in unique]
        measured.sort(key=lambda c: (not ends_with_hint(c["target_column"], "hints.identifier_suffixes"),
                                     str(c["target_column"])))
        picked = measured[:1]
        reason = f"profiled as unique: {picked[0]['source_column']} (confirm business keys in the STTM)" if picked else ""
    if not picked and not key_candidates:
        named = [c for c in on_primary if ends_with_hint(c["target_column"], "hints.identifier_suffixes")]
        picked = named[:1]
        reason = f"inferred from {picked[0]['target_column']} (no profiles; confirm business keys in the STTM)" \
            if picked else ""
    if not picked:
        raise NoUniqueKey(
            f"NO_UNIQUE_KEY: no confirmed unique key for {primary}. Mark the business key columns in the STTM "
            "(or the target registry), or re-profile so a unique column is measured, then generate again.")
    return picked, reason


def _source_unique_id(keys: List[Dict[str, Any]], alias: str, sources: Dict[str, Dict[str, Any]]) -> str:
    parts = []
    for i, key in enumerate(keys):
        table = str(key.get("source_table") or "").upper()
        src_type = (sources.get(table) or {}).get("columns", {}).get(str(key["source_column"]).upper(), "TEXT")
        ref = f"{alias}.{quote(real_name(sources, table, key['source_column']))}"
        ref = ref if family(src_type) == "text" else f"{ref}::varchar"
        parts.append(f"upper(trim({ref}))" if i == 0 else f"coalesce(upper(trim({ref})), '')")
    return " || '||' || ".join(parts) if parts else "null"


def _updated_column(columns: Dict[str, str], prefix: str) -> Optional[str]:
    names = list(columns)
    pref = f"{prefix.upper()}_UPDATED_TS" if prefix else ""
    if pref and pref in columns:
        return pref
    for hint in rule("hints.updated_columns"):
        for name in names:
            if name.endswith(hint) and family(columns[name]) in {"timestamp", "date"}:
                return name
    return None


def _join_plan(primary: str, sources: Dict[str, Dict[str, Any]], used: Iterable[str],
               joins: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Join every other used table to the plan through the first edge that reaches a table already joined
    (multi-hop paths allowed). Join type comes from the STTM join plan (default LEFT); cardinality is oriented
    from the joined table's point of view. Tables with no path are reported."""
    plan: List[Dict[str, Any]] = []
    aliases = {primary: "o"}
    remaining = [t for t in used if t != primary]
    progress = True
    while remaining and progress:
        progress = False
        for table in list(remaining):
            edge, anchor = None, None
            for j in joins:
                ends = {j["left"].upper(), j["right"].upper()}
                if table in ends and len(ends) == 2:
                    other = (ends - {table}).pop()
                    if other in aliases:
                        edge, anchor = j, other
                        break
            if edge is None:
                continue
            aliases[table] = f"j{len(aliases)}"
            conds = []
            for key in edge["keys"]:
                left_col, _, right_col = key.partition("=")
                right_col = right_col or left_col
                if edge["left"].upper() != anchor:
                    left_col, right_col = right_col, left_col
                conds.append(f"{aliases[anchor]}.{quote(real_name(sources, anchor, left_col))} = "
                             f"{aliases[table]}.{quote(real_name(sources, table, right_col))}")
            card = edge["cardinality"] if edge["left"].upper() == anchor else {"N:1": "1:N", "1:N": "N:1"}.get(
                edge["cardinality"], edge["cardinality"])
            plan.append({"table": table, "alias": aliases[table], "on": " and ".join(conds), "cardinality": card,
                         "join_type": str(edge.get("join_type") or "LEFT").lower()})
            remaining.remove(table)
            progress = True
    return plan, remaining


# ----------------------------------------------------------------------------------------------- domain contract

def missing_minimum_mapping(required: List[str], mapped: Iterable[str]) -> List[str]:
    """Contract 'Required Mapping Keys': each entry names one column, or 'One of: `a`, `b`' alternatives."""
    have = {m.lower() for m in mapped}
    missing = []
    for entry in required:
        names = [n.lower() for n in re.findall(r"`([A-Za-z0-9_]+)`", entry)]
        if names and not any(n in have for n in names):
            missing.append(" or ".join(names) if len(names) > 1 else names[0])
    return missing


def contract_cast(template: str, alias: str) -> str:
    """'try_to_number(o.col)' / 'o.col::number(12,8)' with the column substituted."""
    return re.sub(r"\bo\.col\b", f"o.{alias}", template)


# ----------------------------------------------------------------------------------------------- render helpers

def _sources_yml(name: str, source_key: str, sources: Dict[str, Dict[str, Any]], system: str) -> str:
    first = next(iter(sources.values()), {})
    lines = [
        "version: 2", "",
        "# Generated by dbt-onboard-source (Agentic pipeline). Bronze declaration for every table this source uses.",
        "sources:",
        f"  - name: {name}",
        f"    database: \"{{{{ var('bronze_db', '{first.get('database', '')}') }}}}\"",
        f"    schema: \"{{{{ var('bronze_schema_{source_key}', '{first.get('schema', '')}') }}}}\"",
        "    tables:",
    ]
    for table, info in sources.items():
        lines += [f"      - name: {snake(table)}",
                  f"        identifier: \"{info['identifier']}\""]
        if not SIMPLE.match(str(info.get("identifier") or "")):
            lines += ["        quoting:", "          identifier: true"]  # keep lower/mixed-case table names exact
        lines += [
                  f"        description: \"{system} {table.lower()} (landed copy)\""]
        if info.get("columns"):
            lines.append("        columns:")
            for col, dtype in info["columns"].items():
                lines += [f"          - name: {col.lower()}", f"            data_type: {dtype.lower()}"]
    return "\n".join(lines) + "\n"


def _has_macro(skeleton: Dict[str, str], name: str) -> bool:
    pattern = re.compile(r"\{%-?\s*macro\s+" + re.escape(name) + r"\s*\(")
    return any(pattern.search(text or "") for path, text in skeleton.items() if path.startswith("macros/"))


def _macros(domain: str, target: str, hkey_cols: List[str], skeleton: Dict[str, str], need_source_system: bool,
            prefix: str) -> Tuple[str, str, List[str]]:
    """Returns (path, content, macro names added). Extends an existing <domain>_utils.sql instead of replacing it."""
    path = f"macros/{domain}_utils.sql"
    existing = skeleton.get(path, "")
    blocks: List[Tuple[str, str]] = []
    if not _has_macro(skeleton, "generate_sha2_hash_key"):
        blocks.append(("generate_sha2_hash_key", (
            "-- SHA2-256 over the business columns; NULLs become '' so the key is stable.\n"
            "{% macro generate_sha2_hash_key(columns) -%}\n"
            "    sha2(concat_ws('||'{% for c in columns %}, coalesce(cast({{ c }} as varchar), ''){% endfor %}), 256)\n"
            "{%- endmacro %}\n")))
    if not _has_macro(skeleton, "m_is_source_active"):
        blocks.append(("m_is_source_active", (
            "-- True when this source branch should load: --vars '{active_source: <key>}' or 'all' (default).\n"
            "{% macro m_is_source_active(source_key) -%}\n"
            "    {{ var('active_source', 'all') in ['all', source_key] }}\n"
            "{%- endmacro %}\n")))
    if need_source_system and not _has_macro(skeleton, f"m_get_source_system_skey_{domain}"):
        p, pl = prefix.upper(), prefix.lower()
        blocks.append((f"m_get_source_system_skey_{domain}", (
            f"{{% macro m_get_source_system_skey_{domain}(source_system_name) -%}}\n"
            f"    select distinct {p}_SOURCE_SYSTEM_SKEY as {pl}_source_system_skey\n"
            f"    from {{{{ source('{domain}_shared_reference', 'ref_{pl}_source_system') }}}}\n"
            f"    where upper({p}_SOURCE_SYSTEM_NAME) = '{{{{ source_system_name | upper }}}}'\n"
            "{%- endmacro %}\n")))
    hkey = f"m_{target}_hkey"
    if not _has_macro(skeleton, hkey):
        cols = ",\n".join(f"    '{c}'" for c in hkey_cols) or "    'source_unique_id'"
        blocks.append((hkey, (
            f"-- HKEY for {target}: pure business columns in DDL order (no *_SKEY, SOURCE_UNIQUE_ID or audit).\n"
            f"{{% macro {hkey}() -%}}\n{{{{ generate_sha2_hash_key([\n{cols}\n]) }}}}\n{{%- endmacro %}}\n")))
    header = "" if existing else (
        "-- ====================================================================\n"
        f"-- {domain} macros, created/extended by dbt-onboard-source.\n"
        "-- ====================================================================\n")
    body = "\n".join(b for _, b in blocks)
    content = (existing.rstrip() + "\n\n" + body) if existing else header + "\n" + body
    return path, content, [n for n, _ in blocks]


def _find_cte_body(sql: str, name: str) -> Optional[Tuple[int, int]]:
    """(start, end) of the parenthesised body of CTE `name as (...)`, or None."""
    m = re.search(r"\b" + re.escape(name) + r"\s+as\s*\(", sql, re.I)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(sql) and depth:
        depth += {"(": 1, ")": -1}.get(sql[i], 0)
        i += 1
    return (m.end(), i - 1) if depth == 0 else None


def patch_hub(sql: str, source_key: str, staging: str, columns: List[str], system: str,
              updated: str, inserted: str) -> Tuple[str, str]:
    """Idempotently add this source's CTE + UNION branch to an existing hub model (assets/hub_model_patch.sql)."""
    if f"ref('{staging}')" in sql or f'ref("{staging}")' in sql:
        return sql, "already includes this source"
    body = _find_cte_body(sql, "unioned")
    if not body:
        return sql, "no `unioned` CTE found; see release/hub_patch.sql to apply by hand"
    cte = (f"{source_key}_source as (\n    select * from {{{{ ref('{staging}') }}}}\n"
           "    {% if is_incremental() %}\n"
           f"    where greatest(coalesce({inserted}, '1900-01-01'::timestamp_ntz),\n"
           f"                   coalesce({updated}, '1900-01-01'::timestamp_ntz)) > $wm_ts\n"
           "    {% endif %}\n),\n\n")
    branch = (f"\n    union all\n    -- {system}\n    select\n        " + ",\n        ".join(columns)
              + f"\n    from {source_key}_source where {{{{ m_is_source_active('{source_key}') }}}}\n")
    start, end = body
    cte_at = re.search(r"\bunioned\s+as\s*\(", sql, re.I).start()
    patched = sql[:cte_at] + cte + sql[cte_at:start] + sql[start:end].rstrip() + "\n" + branch + sql[end:]
    return patched, "patched: added source CTE and UNION branch"


def _merge_project_yml(existing: str, project: str, source_key: str, schema: str, database: str,
                       profile: str = "gdp_platform", snapshots: bool = False) -> str:
    vars_lines = [f"  bronze_db: '{database}'", f"  bronze_schema_{source_key}: '{schema}'"]
    if not existing:
        return "\n".join([
            "# Generated by dbt-onboard-source (Agentic pipeline). Compile-only until the PR is reviewed.",
            f"name: {project}", "version: '1.0.0'", "config-version: 2", f"profile: {profile}",
            "model-paths: [\"models\"]", "macro-paths: [\"macros\"]",
            *(["snapshot-paths: [\"snapshots\"]"] if snapshots else []), "target-path: \"target\"",
            "clean-targets: [\"target\"]", "", "vars:", *vars_lines, "",
        ]) + "\n"
    out = existing.rstrip("\n").split("\n")
    missing = [v for v in vars_lines if not re.search(r"^\s*" + re.escape(v.split(":")[0].strip()) + r"\s*:", existing, re.M)]
    if not missing:
        return existing
    idx = next((i for i, l in enumerate(out) if re.match(r"^vars\s*:\s*$", l)), None)
    if idx is None:
        out += ["", "vars:", *missing]
    else:
        out[idx + 1:idx + 1] = missing
    return "\n".join(out) + "\n"


def _scd2_models(target: str, staging: str, domain: str, columns: List[str], updated: Optional[str],
                 conv: Dict[str, Any]) -> Tuple[str, str, Dict[str, str]]:
    """SCD2 as dbt does it natively: a snapshot keeps every version, a view exposes the current one."""
    strategy = (f"    strategy = 'timestamp',\n    updated_at = '{updated}',\n" if updated
                else "    strategy = 'check',\n    check_cols = 'all',\n")
    snapshot = (
        f"{{% snapshot {target}_snapshot %}}\n"
        "{{ config(\n    target_schema = target.schema,\n    unique_key = 'source_unique_id',\n" + strategy
        + "    invalidate_hard_deletes = true\n) }}\n\n"
        f"-- Full history of {target} (SCD2): one row per version, dbt_valid_from/dbt_valid_to bound each.\n"
        f"select * from {{{{ ref('{staging}') }}}}\n\n{{% endsnapshot %}}\n")
    current = (
        "{{ config(\n    materialized = 'view',\n"
        f"    tags = ['{conv['layer_tag']}', '{domain}', '{target}']\n) }}}}\n\n"
        f"-- {target}: the current version of each record; history lives in snapshot {target}_snapshot.\n\n"
        "select\n    " + ",\n    ".join(columns) + ",\n    dbt_valid_from as valid_from,\n    dbt_valid_to as valid_to\n"
        f"from {{{{ ref('{target}_snapshot') }}}}\nwhere dbt_valid_to is null\n")
    note = ("created SCD2 snapshot " + f"{target}_snapshot ("
            + (f"timestamp strategy on {updated}" if updated else "check strategy on all columns") + ") and a current-rows view")
    return current, note, {f"snapshots/{target}_snapshot.sql": snapshot}


# ----------------------------------------------------------------------------------------------- generate

def generate(inputs: Dict[str, Any], skeleton: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Return {"files": {path: content}, "report": {...}} following dbt-onboard-source."""
    skeleton = skeleton or {}
    conv = inputs.get("conventions") or conventions(inputs.get("standard") or GDP)
    prefix = re.sub(r"[^A-Za-z0-9]", "", str(inputs.get("prefix") or "")).upper()
    pl = prefix.lower()
    domain = snake(inputs.get("domain") or conv["default_domain"])
    model_dir = fill(conv["model_dir"], domain=domain)
    scd2 = int(conv.get("scd_type") or 1) == 2
    target = snake(inputs.get("target") or "target")
    source_key = snake(inputs.get("source_key") or "src")
    system = str(inputs.get("source_system") or source_key).upper()
    staging = f"{source_key}_{target}"
    sources: Dict[str, Dict[str, Any]] = {
        str(s["name"]).upper(): {**s, "columns": {str(k).upper(): v for k, v in (s.get("columns") or {}).items()}}
        for s in inputs.get("sources") or []
    }
    lines = [dict(l) for l in inputs.get("lines") or []]
    source_system_col = f"REF_{prefix}_SOURCE_SYSTEM_SKEY" if prefix else "REF_SOURCE_SYSTEM_SKEY"
    cols = _ordered_targets(lines, inputs.get("target_columns") or [])
    for c in cols:
        c["_class"] = classify(c, target, prefix, source_system_col)
    spec = inputs.get("model_spec") or {}
    mapped = [str(c["target_column"]) for c in cols
              if c.get("source_column") or str(c.get("transformation") or "").strip() or c["_class"] == "COMPOUND_PK"]
    missing_keys = missing_minimum_mapping(spec.get("minimum_mapping") or [], mapped)
    if missing_keys:
        raise ValueError("MINIMUM_MAPPING: the domain contract requires " + ", ".join(missing_keys)
                         + f" for {target.upper()}; map them in the STTM before generating dbt")
    casts = {str(c["target_column"]).upper(): c["cast"] for c in spec.get("casts") or []}
    contract_ctes: List[str] = []
    contract_joins: List[Dict[str, str]] = []
    lookups_used: List[str] = []
    hub_join = ""

    counts: Dict[str, int] = {}
    for c in cols:
        if c.get("source_table") and c["_class"] in {"PASSTHROUGH", "DERIVED"}:
            counts[str(c["source_table"]).upper()] = counts.get(str(c["source_table"]).upper(), 0) + 1
    planned = str(inputs.get("primary") or "").upper()
    primary = planned if planned and (planned in counts or planned in sources) else (
        max(counts, key=counts.get) if counts else (next(iter(sources), "SOURCE")))
    used = [primary] + sorted(t for t in counts if t != primary)
    joins, unjoined = _join_plan(primary, sources, used, inputs.get("joins") or [])
    alias_of = {primary: "o", **{j["table"]: j["alias"] for j in joins}}
    anomalies: List[str] = []
    if primary not in sources:
        anomalies.append(f"{primary} has no landed table; generated SQL references it by name")
        sources.setdefault(primary, {"name": primary, "identifier": primary, "columns": {}, "database": "", "schema": ""})
    for table in unjoined:
        anomalies.append(f"no join path from {primary} to {table}; its columns load as null with a TODO")

    explicit = next((str(c.get("transformation")).strip() for c in cols
                     if str(c["target_column"]).upper() == "SOURCE_UNIQUE_ID" and not c.get("source_column")
                     and str(c.get("transformation") or "").strip()
                     and not str(c.get("transformation")).strip().upper().startswith("NULL")), None)
    if explicit:  # the STTM defines the record key itself
        uid, key_reason, keys = f"({explicit})", "STTM SOURCE_UNIQUE_ID expression", []
    else:
        keys, key_reason = _unique_key(cols, inputs.get("business_keys") or [], sources, primary,
                                       inputs.get("key_candidates"))
        uid = _source_unique_id(keys, "o", sources)
    pcols = sources[primary]["columns"]
    updated_src = _updated_column(pcols, prefix)
    inserted_src = f"{prefix}_INSERTED_TS" if prefix and f"{prefix}_INSERTED_TS" in pcols else None
    active_src = f"{prefix}_IS_ACTIVE" if prefix and f"{prefix}_IS_ACTIVE" in pcols else None
    if not updated_src:
        anomalies.append(f"{primary} has no update timestamp; dedup orders by source_unique_id and audit "
                         "timestamps use current_timestamp()")

    report_cols: List[Dict[str, Any]] = []
    sp_select: List[str] = [f"{uid} as source_unique_id"]
    base_select: List[str] = ["o.source_unique_id"]
    final: List[str] = []
    hkey_cols: List[str] = []
    todos = 0
    need_ref_system = False
    has_uid_col = any(str(c["target_column"]).upper() == "SOURCE_UNIQUE_ID" for c in cols)
    def pref(col: str) -> str:  # driving-table column by its exact spelling
        return f"o.{quote(real_name(sources, primary, col))}"

    audit_exprs = {
        "IS_ACTIVE": pref(active_src) if active_src else "true",
        "INSERTED_TS": pref(inserted_src) if inserted_src else "current_timestamp()::timestamp_ntz",
        "UPDATED_TS": pref(updated_src) if updated_src else "current_timestamp()::timestamp_ntz",
        "INSERTED_BY": "current_user()",
        "UPDATED_BY": "current_user()",
    }
    for c in cols:
        name = str(c["target_column"]).upper()
        alias = name.lower()
        cls = c["_class"]
        ttype = str(c.get("target_datatype") or "")
        table = str(c.get("source_table") or primary).upper()
        scol = str(c.get("source_column") or "").upper()
        scol_sql = real_name(sources, table, c.get("source_column") or "")
        stype = sources.get(table, {}).get("columns", {}).get(scol, "")
        note, cast = "", None
        if cls == "AUDIT":
            role = audit_role(name, prefix) or "INSERTED_TS"
            expr = audit_exprs.get(role)
            if role == "ROW_HASH":
                expr = f"{{{{ m_{target}_hkey() }}}}"
                if family(ttype) == "binary":
                    expr = f"to_binary({expr}, 'HEX')"
                final.append(f"    {expr} as {alias}")
            else:
                base_select.append(f"{expr} as {alias}")
                final.append(f"    {alias}")
        elif cls == "COMPOUND_PK":
            final.append("    source_unique_id")
            note = key_reason
        elif cls == "SOURCE_SYSTEM_REF":
            need_ref_system = True
            base_select.append(f"r.{pl}_source_system_skey as {alias}")
            final.append(f"    {alias}")
        elif cls == "HKEY":
            final.append(f"    {{{{ m_{target}_hkey() }}}} as {alias}")
        elif cls == "SEQUENCE" and conv["key_strategy"] == "sequence":
            final.append(f"    {{{{ this.schema }}}}.seq_{target}_skey.nextval as {alias}")
            note = f"assigned from sequence SEQ_{target.upper()}_SKEY (silver-model convention)"
        elif cls == "SEQUENCE" and conv["key_strategy"] == "hash":
            final.append(f"    abs(hash(source_unique_id)) as {alias}")
            note = "surrogate key hashed from SOURCE_UNIQUE_ID (stable across loads, no sequence object)"
        elif cls == "SEQUENCE":
            final.append(f"    null as {alias}")
            note = "surrogate key assigned by the target platform"
        elif cls == "FK_LOOKUP" and scol and table not in unjoined and ref_lookup(name, spec):
            lookup = ref_lookup(name, spec)
            a = alias_of.get(table, "o")
            present = " ".join(sp_select)
            for attr in lookup["attributes"]:
                if f" as {attr}" not in present:
                    sp_select.append(f"nullif(trim({a}.{quote(scol_sql)}), '') as {attr}")
            for cte in lookup["ctes"]:
                if cte not in contract_ctes:
                    contract_ctes.append(cte)
            for j in lookup["joins"]:
                if j not in contract_joins:
                    contract_joins.append(j)
            base_select.append(f"{lookup['expr']} as {alias}")
            final.append(f"    {alias}")
            lookups_used.append(name)
            note = f"contract lookup {' -> '.join(lookup['ctes'])} on {', '.join(lookup['attributes'])}"
        elif cls == "HUB_FK" and spec.get("role") == "spoke" and name == str(spec.get("hub_fk") or "").upper():
            hub = str(spec.get("hub") or "").lower()
            active = fill(conv["hub_active_filter"], pl=pl or "gdp", P=prefix)
            latest = fill(conv["hub_latest_order"], pl=pl or "gdp", P=prefix)
            hub_join = (f"\n    left join (\n        select source_unique_id, {name.lower()}\n"
                        f"        from {{{{ ref('{hub}') }}}}\n        where {active}\n"
                        f"        qualify row_number() over (partition by source_unique_id order by {latest}) = 1\n"
                        f"    ) as hub\n        on hub.source_unique_id = o.source_unique_id")
            base_select.append(f"hub.{name.lower()} as {alias}")
            final.append(f"    {alias}")
            note = f"resolved from ref('{hub}') by SOURCE_UNIQUE_ID (latest active row)"
        elif cls in {"FK_LOOKUP", "HUB_FK", "STANDARDIZATION", "LOV", "UNMAPPED"} or table in unjoined:
            reason = {
                "FK_LOOKUP": f"FK lookup pending: add ref CTE + LEFT JOIN on upper(trim(...)) for {name}",
                "HUB_FK": f"hub FK pending: join ref('{name.lower().replace('_skey', '')}') on source_unique_id",
                "STANDARDIZATION": "populated by downstream standardization step",
                "LOV": "LOV mapping pending",
                "UNMAPPED": "",
            }.get(cls, f"no join path to {table}")
            base_select.append(f"null as {alias}" + (f" /* TODO: {reason} */" if reason else ""))
            final.append(f"    {alias}")
            todos += 1 if reason else 0
            note = reason
        else:
            a = alias_of.get(table, "o")
            if cls == "DERIVED":
                raw = str(c.get("transformation")).strip()
                sp_select.append(f"{raw} as {alias}")
                expr = f"o.{alias}"
                note = "STTM transformation"
            else:
                ref = f"{a}.{quote(scol_sql)}"
                if family(stype or ttype) == "text":
                    sp_select.append(f"nullif(trim({ref}), '') as {alias}")
                else:
                    sp_select.append(f"{ref} as {alias}")
                expr, cast = conform(f"o.{alias}", stype, ttype)
                if name in casts:
                    expr, cast = contract_cast(casts[name], alias), f"contract cast {casts[name]}"
                elif not stype:
                    note = "source type unknown; passthrough"
            base_select.append(f"{expr} as {alias}")
            final.append(f"    {alias}")
            if not name.endswith("_SKEY") and name != "SOURCE_UNIQUE_ID":
                kind = family(ttype or stype)
                hkey_cols.append(f"st_aswkt({alias})" if kind == "geo"
                                 else f"to_json({alias})" if kind == "semi"
                                 else f"hex_encode({alias})" if kind == "binary" else alias)
        report_cols.append({"target_column": name, "class": cls, "source": f"{table}.{scol}" if scol else None,
                            "source_type": stype or None, "target_type": ttype or None, "cast": cast, "note": note})

    # sp_<target> extraction CTE (Rule 1) ---------------------------------------------------------
    src_name = f"{target}_{source_key}_source"
    sp_from = f"    from {{{{ source('{src_name}', '{snake(primary)}') }}}} as o"
    for j in joins:
        sp_from += (f"\n    {j.get('join_type', 'left')} join {{{{ source('{src_name}', '{snake(j['table'])}') }}}} as {j['alias']}"
                    f" /* {j['cardinality']} */\n        on {j['on']}")
        if j["cardinality"] in {"1:N", "N:N"}:
            anomalies.append(f"{j['cardinality']} join to {j['table']}: the dedup keeps one {j['table']} row per key")
    for col in (inserted_src, updated_src, active_src):
        if col and pref(col) not in "".join(sp_select):
            sp_select.append(pref(col))
    order = f"{pref(updated_src)} desc" if updated_src else "source_unique_id"
    sp = (f"with sp_{target} as (\n    select\n        " + ",\n        ".join(sp_select) + "\n" + sp_from
          + f"\n    where nullif({uid}, '') is not null"
          + f"\n    qualify row_number() over (partition by {uid} order by {order}) = 1\n)")
    ref_cte = ""
    ref_join = ""
    if need_ref_system:
        ref_cte = f",\n\nref_source_system as (\n    {{{{ m_get_source_system_skey_{domain}('{system}') }}}}\n)"
        ref_join = "\n    cross join ref_source_system as r"
    ctes = spec.get("reference_ctes") or {}
    for name in contract_ctes:
        ref_cte += ",\n\n" + ctes[name]
    lookup_joins = "".join(f"\n    left join {j['cte']} as {j['alias']}\n        on {j['condition']}" for j in contract_joins)
    base = (",\n\nbase as (\n    select\n        " + ",\n        ".join(base_select) + f"\n    from sp_{target} as o"
            + ref_join + lookup_joins + hub_join + "\n)")
    final_cols = final if has_uid_col else ["    source_unique_id", *final]
    ephemeral = (
        f"{{{{ config(materialized = '{conv['staging_materialization']}') }}}}\n\n"
        f"-- {system} -> {target}: generated by dbt-onboard-source from the approved STTM.\n"
        f"-- SOURCE_UNIQUE_ID: {key_reason}.\n\n"
        + sp + ref_cte + base + "\n\nselect\n" + ",\n".join(final_cols) + "\nfrom base\n"
    )

    # hub model (Step 5) -----------------------------------------------------------------------------
    hub_path = f"{model_dir}/{target}.sql"
    hub_cols = [l.strip().split(" as ")[-1] if " as " in l else l.strip() for l in final_cols]
    upd_alias = next((str(c["target_column"]).lower() for c in cols if audit_role(str(c["target_column"]), prefix) == "UPDATED_TS"), None)
    ins_alias = next((str(c["target_column"]).lower() for c in cols if audit_role(str(c["target_column"]), prefix) == "INSERTED_TS"), None)
    unique_key = "source_unique_id"
    hub_note = "created minimal SCD1 hub (no hub model on the cut-from branch)"
    snapshot_files: Dict[str, str] = {}
    if scd2 and hub_path not in skeleton:
        # version by the source's own change time when it is mapped (audit-named or the driving table's
        # update column found from hints), otherwise compare every column
        changed = upd_alias or next((str(c["target_column"]).lower() for c in cols if updated_src and
                                     str(c.get("source_column") or "").upper() == updated_src.upper()), None)
        hub_sql, hub_note, snapshot_files = _scd2_models(target, staging, domain, hub_cols, changed, conv)
    elif hub_path in skeleton:
        hub_sql, hub_note = patch_hub(skeleton[hub_path], source_key, staging, hub_cols, system,
                                      upd_alias or "updated_ts", ins_alias or "inserted_ts")
    else:
        watermark = ""
        if upd_alias or ins_alias:
            g = ", ".join(f"coalesce({a}, '1900-01-01'::timestamp_ntz)" for a in (ins_alias, upd_alias) if a)
            watermark = (
                "    {% if is_incremental() %}\n"
                f"    where greatest({g}, '1900-01-01'::timestamp_ntz)\n"
                f"        > (select coalesce(max(greatest({g}, '1900-01-01'::timestamp_ntz)), '1900-01-01'::timestamp_ntz) from {{{{ this }}}})\n"
                "    {% endif %}\n")
        hub_sql = (
            "{{ config(\n    materialized = 'incremental',\n"
            f"    unique_key = '{unique_key}',\n    incremental_strategy = 'merge',\n"
            "    on_schema_change = 'append_new_columns',\n"
            f"    tags = ['{conv['layer_tag']}', '{domain}', '{target}']\n) }}}}\n\n"
            + f"-- {target} hub: SCD1 merge over every onboarded source (UNION ALL branch per source).\n\n"
            f"with {source_key}_source as (\n    select * from {{{{ ref('{staging}') }}}}\n" + watermark + "),\n\n"
            "unioned as (\n    -- " + system + "\n    select\n        " + ",\n        ".join(hub_cols)
            + f"\n    from {source_key}_source where {{{{ m_is_source_active('{source_key}') }}}}\n)\n\n"
            "select * from unioned\n"
            "qualify row_number() over (partition by source_unique_id order by "
            + (f"{upd_alias} desc" if upd_alias else "source_unique_id") + ") = 1\n"
        )

    # schema yml, macros, sources, project, watermark --------------------------------------------------
    pii = {str(c["target_column"]).upper() for c in cols if (c.get("_registry") or {}).get("is_pii")}
    yml = ["version: 2", "", "models:", f"  - name: {target}",
           f"    description: \"{(inputs.get('grain') or target).replace(chr(34), chr(39))}\"", "    columns:",
           "      - name: source_unique_id", f"        description: \"{key_reason}\"",
           "        tests:", "          - not_null", "          - unique"]
    for c in cols:
        name = str(c["target_column"]).upper()
        if name == "SOURCE_UNIQUE_ID":
            continue
        reg = c.get("_registry") or {}
        entry = [f"      - name: {name.lower()}"]
        definition = c.get("business_definition") or reg.get("business_definition") or reg.get("definition")
        if definition:
            entry.append(f"        description: \"{str(definition).replace(chr(34), chr(39))[:300]}\"")
        if name in pii:
            entry += ["        meta:", "          pii: true"]
        tests = []
        nullable = c.get("nullable_rule")
        if nullable is False or reg.get("nullable") is False:
            tests.append("          - not_null")
        values = c.get("accepted_values") or reg.get("accepted_values") or []
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except ValueError:
                values = []
        if values:
            tests.append("          - accepted_values:\n              values: [" + ", ".join(json.dumps(v) for v in values) + "]")
        if tests:
            entry += ["        tests:", *tests]
        yml += entry
    if spec.get("hkey_columns"):
        present = {str(c["target_column"]).upper() for c in cols}
        hkey_cols = [h.lower() for h in spec["hkey_columns"] if h.upper() in present] or hkey_cols
    macro_path, macro_sql, macros_added = _macros(domain, target, hkey_cols, skeleton, need_ref_system, prefix)
    first = sources[primary]
    project_path = "dbt_project.yml"
    project_yml = _merge_project_yml(skeleton.get(project_path, ""), fill(conv["project_name"], domain=domain),
                                     source_key, str(first.get("schema") or ""), str(first.get("database") or ""),
                                     conv["dbt_profile"], bool(snapshot_files))
    files: Dict[str, str] = {
        f"{conv['source_dir']}/{src_name}.yml": _sources_yml(src_name, source_key, {t: sources[t] for t in used if t in sources}, system),
        f"{model_dir}/{source_key}/{staging}.sql": ephemeral,
        hub_path: hub_sql,
        f"{model_dir}/_{target}.yml": "\n".join(yml) + "\n",
        macro_path: macro_sql,
        project_path: project_yml,
        **snapshot_files,
    }
    watermark_table = fill(conv["watermark_table"], P=prefix, pl=pl)
    if prefix and watermark_table:
        files[f"release/watermark_{target}.sql"] = (
            f"-- Watermark registration (dbt-onboard-source Step 6). Review, then run once per environment.\n"
            f"INSERT INTO {watermark_table}\n"
            f"  (SOURCE_SYSTEM, SOURCE_MODEL_NAME, TARGET_MODEL_NAME, WATERMARK_TIMESTAMP, {prefix}_INSERTED_TS, {prefix}_UPDATED_TS)\n"
            f"SELECT '{system}', '{source_key.upper()}_{target.upper()}', '{target.upper()}', '1900-01-01'::TIMESTAMP_NTZ,\n"
            "       CURRENT_TIMESTAMP::TIMESTAMP_NTZ(6), CURRENT_TIMESTAMP::TIMESTAMP_NTZ(6)\n"
            f"WHERE NOT EXISTS (SELECT 1 FROM {watermark_table}\n"
            f"  WHERE SOURCE_SYSTEM = '{system}' AND SOURCE_MODEL_NAME = '{source_key.upper()}_{target.upper()}'\n"
            f"    AND TARGET_MODEL_NAME = '{target.upper()}');\n")
    if "manual" in hub_note or "no `unioned`" in hub_note:
        files["release/hub_patch.sql"] = (
            f"-- Add to {hub_path}: source CTE + UNION branch (assets/hub_model_patch.sql)\n"
            f"{source_key}_source as (select * from {{{{ ref('{staging}') }}}}),\n"
            f"-- inside `unioned`:\n    union all\n    select {', '.join(hub_cols)}\n"
            f"    from {source_key}_source where {{{{ m_is_source_active('{source_key}') }}}}\n")
    status = {p: ("patched" if p in skeleton and files[p] != skeleton[p] else "unchanged" if p in skeleton else "new")
              for p in files}
    report = {
        "engine": ENGINE,
        "skill": conv["skill"] or None, "standard": conv["standard"],
        "contract": {"applied": bool(spec), "role": spec.get("role"), "hkey_from_contract": bool(spec.get("hkey_columns")),
                     "lookups": lookups_used, "hub_fk": bool(hub_join),
                     "casts": [c["target_column"] for c in report_cols if str(c.get("cast") or "").startswith("contract")]},
        "convention": conv["convention"],
        "domain": domain, "target": target, "source_key": source_key, "prefix": prefix, "source_system": system,
        "primary_source": primary, "joins": joins, "source_unique_id": {"expression": uid, "reason": key_reason,
                                                                       "columns": [k["target_column"] for k in keys]},
        "dedup_order": order, "hub": hub_note, "macros_added": macros_added,
        "columns": report_cols,
        "counts": {cls: sum(1 for c in report_cols if c["class"] == cls) for cls in sorted({c["class"] for c in report_cols})},
        "casts": sum(1 for c in report_cols if c["cast"]), "todos": todos,
        "anomalies": anomalies, "files": status,
        "rules": [
            "Rule 1: NULLIF(TRIM()) on text, passthrough for number/date, dedup qualify, not-null/blank key filter",
            "Rule 2: cast only where the bronze type differs from the target type; unmapped -> plain null",
            "Rule 3: HKEY over business columns only (no *_SKEY, SOURCE_UNIQUE_ID, audit)",
            "Rule 4: LEFT JOINs only; compound keys with '||' and coalesce(..., '')",
        ],
    }
    return {"files": files, "report": report}
