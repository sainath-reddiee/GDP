"""Deterministic profiling: SQL builders and classification rules (pure, unit-tested).

Ported from the modeler's profile_real_data.py (robust null placeholders, top values, numeric ranges) and
iterative_mapper.py (semantic hints), extended with patterns, lengths, duplicates, date formats, PII and keys.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.source.identifiers import quote

NULL_PLACEHOLDERS = ("", "N/A", "NA", "NULL", "NONE", "<NULL>", ".")
TOP_VALUES = 10
TOP_PATTERNS = 5
ENUM_MAX_DISTINCT = 20

PROFILER_VERSION = "2"
LARGE_TABLE_ROWS = 10_000_000
SAMPLE_ROWS = 100_000
HISTOGRAM_BUCKETS = 10
APPROX_KEY_TOLERANCE = 0.02
RUN_SCOPED_KEYS = ("landing_column_id", "potential_foreign_key")
STAGE_PATH = re.compile(r"^[A-Z0-9_]+(/[A-Z0-9_]+){3}\.json$")

FAMILIES = {
    "TEXT": ("VARCHAR", "CHAR", "STRING", "TEXT"),
    "NUMBER": ("NUMBER", "DECIMAL", "NUMERIC", "INT", "BIGINT", "SMALLINT", "TINYINT", "FLOAT", "DOUBLE", "REAL"),
    "TIMESTAMP": ("TIMESTAMP", "DATETIME"),
    "DATE": ("DATE",),
    "TIME": ("TIME",),
    "BOOLEAN": ("BOOLEAN",),
}

DATE_FORMATS = {
    "99/99/9999": "MM/DD/YYYY",
    "9999-99-99": "YYYY-MM-DD",
    "99-99-9999": "DD-MM-YYYY",
    "9999/99/99": "YYYY/MM/DD",
}

PII_TYPES = {"EMAIL": "EMAIL", "PHONE": "PHONE", "PERSON_NAME": "NAME", "DATE_OF_BIRTH": "DATE_OF_BIRTH"}


def type_family(data_type: str) -> str:
    t = (data_type or "").upper()
    for family, prefixes in FAMILIES.items():
        if any(t.startswith(p) for p in prefixes):
            return family
    return "OTHER"


def is_large(row_count: Optional[int]) -> bool:
    return row_count is not None and int(row_count) > LARGE_TABLE_ROWS


def sampled(table_fqn: str, approximate: bool) -> str:
    return f"{table_fqn} SAMPLE ({SAMPLE_ROWS} ROWS)" if approximate else table_fqn


def stats_sql(table_fqn: str, columns: Sequence[Tuple[str, str]], approximate: bool = False) -> str:
    """One pass over the table: counts, robust nulls, distinct, min/max, length and numeric stats per column.

    Above LARGE_TABLE_ROWS the distinct count is APPROX_COUNT_DISTINCT (HyperLogLog) so the scan stays linear.
    """
    parts = ["COUNT(*) AS ROW_COUNT"]
    placeholders = ", ".join(f"'{p}'" for p in NULL_PLACEHOLDERS)
    for i, (name, data_type) in enumerate(columns):
        q = quote(name)
        family = type_family(data_type)
        parts.append(f"COUNT({q}) AS N{i}")
        if family == "OTHER":
            parts += [f"COUNT({q}) AS V{i}", f"NULL AS D{i}", f"NULL AS MIN{i}", f"NULL AS MAX{i}"]
            continue
        if family == "TEXT":
            parts.append(f"COUNT(CASE WHEN UPPER(TRIM({q})) IN ({placeholders}) THEN NULL ELSE {q} END) AS V{i}")
            parts += [f"MIN(LENGTH({q})) AS LMIN{i}", f"MAX(LENGTH({q})) AS LMAX{i}",
                      f"AVG(LENGTH({q}))::FLOAT AS LAVG{i}"]
        else:
            parts.append(f"COUNT({q}) AS V{i}")
        if family == "NUMBER":
            parts.append(f"AVG({q})::FLOAT AS AVG{i}")
        count_distinct = f"APPROX_COUNT_DISTINCT({q})" if approximate else f"COUNT(DISTINCT {q})"
        parts += [f"{count_distinct} AS D{i}", f"MIN({q})::VARCHAR AS MIN{i}", f"MAX({q})::VARCHAR AS MAX{i}"]
    return f"SELECT {', '.join(parts)} FROM {table_fqn}"


def frequencies_sql(table_fqn: str, columns: Sequence[Tuple[str, str]], limit: int = TOP_VALUES,
                    approximate: bool = False) -> Optional[str]:
    """Top values of every profilable column in one statement; C is the column's position in `columns`."""
    source = sampled(table_fqn, approximate)
    parts = []
    for i, (name, data_type) in enumerate(columns):
        if type_family(data_type) == "OTHER":
            continue
        q = quote(name)
        parts.append(f"SELECT {i} AS C, V, N FROM (SELECT {q}::VARCHAR AS V, COUNT(*) AS N FROM {source} "
                     f"WHERE {q} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {int(limit)})")
    return " UNION ALL ".join(parts) or None


def patterns_sql(table_fqn: str, columns: Sequence[Tuple[str, str]], limit: int = TOP_PATTERNS,
                 approximate: bool = False) -> Optional[str]:
    source = sampled(table_fqn, approximate)
    parts = []
    for i, (name, data_type) in enumerate(columns):
        if type_family(data_type) != "TEXT":
            continue
        q = quote(name)
        shape = f"REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE({q}::VARCHAR, '[A-Z]', 'A'), '[a-z]', 'a'), '[0-9]', '9')"
        parts.append(f"SELECT {i} AS C, P, N FROM (SELECT {shape} AS P, COUNT(*) AS N FROM {source} "
                     f"WHERE {q} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {int(limit)})")
    return " UNION ALL ".join(parts) or None


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def histogram_specs(columns: Sequence[Tuple[str, str]], stats_row: Dict[str, Any]) -> List[Tuple[int, str, float, float]]:
    """Numeric columns with a usable range: (position, name, low, high)."""
    specs = []
    for i, (name, data_type) in enumerate(columns):
        if type_family(data_type) != "NUMBER":
            continue
        low, high = _finite(stats_row.get(f"MIN{i}")), _finite(stats_row.get(f"MAX{i}"))
        if low is not None and high is not None and high > low:
            specs.append((i, name, low, high))
    return specs


def histogram_sql(table_fqn: str, specs: Sequence[Tuple[int, str, float, float]],
                  buckets: int = HISTOGRAM_BUCKETS, approximate: bool = False) -> Optional[str]:
    """Equal-width buckets 1..buckets per numeric column; the maximum value falls in the last bucket."""
    source = sampled(table_fqn, approximate)
    parts = []
    for i, name, low, high in specs:
        q = quote(name)
        parts.append(f"SELECT {i} AS C, LEAST(GREATEST(WIDTH_BUCKET({q}, {low!r}, {high!r}, {int(buckets)}), 1), "
                     f"{int(buckets)}) AS B, COUNT(*) AS N FROM {source} WHERE {q} IS NOT NULL GROUP BY 2")
    return " UNION ALL ".join(parts) or None


def build_histogram(low: float, high: float, counts: Dict[int, int],
                    buckets: int = HISTOGRAM_BUCKETS) -> List[Dict[str, Any]]:
    width = (high - low) / buckets
    return [{"lower": low + width * (b - 1), "upper": low + width * b, "count": int(counts.get(b, 0))}
            for b in range(1, buckets + 1)]


def frequency_sql(table_fqn: str, column: str, limit: int = TOP_VALUES) -> str:
    q = quote(column)
    return (f"SELECT {q}::VARCHAR AS V, COUNT(*) AS N FROM {table_fqn} WHERE {q} IS NOT NULL "
            f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {int(limit)}")


def pattern_sql(table_fqn: str, column: str, limit: int = TOP_PATTERNS) -> str:
    """Character-class shape of each value: letters -> A/a, digits -> 9."""
    q = quote(column)
    shape = f"REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE({q}::VARCHAR, '[A-Z]', 'A'), '[a-z]', 'a'), '[0-9]', '9')"
    return (f"SELECT {shape} AS P, COUNT(*) AS N FROM {table_fqn} WHERE {q} IS NOT NULL "
            f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {int(limit)}")


def column_stats(row: Dict[str, Any], i: int, row_count: int, approximate: bool = False) -> Dict[str, Any]:
    non_null = int(row.get(f"N{i}") or 0)
    valid = int(row.get(f"V{i}") or 0)
    distinct = row.get(f"D{i}")
    if approximate and distinct is not None:
        distinct = min(int(distinct), non_null)
    stats: Dict[str, Any] = {
        "row_count": row_count,
        "null_count": row_count - valid,
        "physical_null_count": row_count - non_null,
        "placeholder_null_count": non_null - valid,
        "distinct_count": None if distinct is None else int(distinct),
        "min": row.get(f"MIN{i}"),
        "max": row.get(f"MAX{i}"),
    }
    stats["null_percentage"] = round(100.0 * stats["null_count"] / row_count, 4) if row_count else 0.0
    if stats["distinct_count"] is not None:
        stats["distinct_percentage"] = round(100.0 * stats["distinct_count"] / non_null, 4) if non_null else 0.0
        stats["duplicate_count"] = max(non_null - stats["distinct_count"], 0)
    for key, col in (("min_length", "LMIN"), ("max_length", "LMAX"), ("avg_length", "LAVG"), ("average", "AVG")):
        if f"{col}{i}" in row and row[f"{col}{i}"] is not None:
            stats[key] = float(row[f"{col}{i}"]) if key in ("avg_length", "average") else int(row[f"{col}{i}"])
    return stats


def cardinality(distinct: Optional[int], non_null: int) -> Optional[str]:
    if distinct is None:
        return None
    if distinct <= 1:
        return "CONSTANT"
    if non_null and distinct == non_null:
        return "UNIQUE"
    ratio = distinct / non_null if non_null else 0
    if ratio >= 0.9:
        return "HIGH"
    if distinct <= ENUM_MAX_DISTINCT or ratio < 0.05:
        return "LOW"
    return "MEDIUM"


def date_format(patterns: Sequence[Dict[str, Any]]) -> Optional[str]:
    if not patterns:
        return None
    top = patterns[0]["pattern"]
    return DATE_FORMATS.get(top)


def _tokens(name: str) -> List[str]:
    return [t for t in re.split(r"[^A-Z0-9]+", name.upper()) if t]


def infer_semantic_type(name: str, family: str, patterns: Sequence[Dict[str, Any]],
                        card: Optional[str]) -> str:
    """Rule-based semantic type; returns a generic family type when no rule applies."""
    toks = set(_tokens(name))
    n = name.upper()
    shapes = [p["pattern"] for p in patterns]
    if "EMAIL" in n or "E_MAIL" in n or any("@" in s for s in shapes):
        return "EMAIL"
    if toks & {"PHONE", "TEL", "MOBILE", "PH"}:
        return "PHONE"
    if toks & {"DOB"} or "BIRTH" in n:
        return "DATE_OF_BIRTH"
    if (toks & {"NM", "NAME"}) and family == "TEXT" and not toks & {"ID", "NO", "CD", "CODE", "KEY"}:
        return "PERSON_NAME" if toks & {"CUST", "CUSTOMER", "FULL", "FIRST", "LAST", "PERSON", "CONTACT"} or n.endswith("_NM") else "NAME"
    if toks & {"REGION", "RGN", "COUNTRY", "STATE", "TERRITORY"}:
        return "REGION"
    if toks & {"STATUS", "STAT", "STS"}:
        return "STATUS_CODE"
    if toks & {"CURRENCY", "CCY"} or n.endswith("CURRENCY_CD"):
        return "CURRENCY_CODE"
    if toks & {"AMT", "AMOUNT", "PRICE", "VALUE", "TOTAL"} and family == "NUMBER":
        return "AMOUNT"
    if toks & {"ID", "NO", "NUM", "NBR", "KEY"} and card in ("UNIQUE", "HIGH", "MEDIUM"):
        return "IDENTIFIER"
    if family == "DATE" or (family == "TEXT" and date_format(patterns)):
        return "DATE"
    if family == "TIMESTAMP":
        return "TIMESTAMP"
    if toks & {"CD", "CODE", "TYPE"} or card == "LOW":
        return "CODE"
    return {"TEXT": "TEXT", "NUMBER": "NUMBER", "BOOLEAN": "BOOLEAN"}.get(family, "OTHER")


def pii_classification(semantic_type: str) -> str:
    return PII_TYPES.get(semantic_type, "NONE")


def mask(value: Optional[str], pii: str) -> Optional[str]:
    if value is None or pii == "NONE":
        return value
    v = str(value)
    if pii == "EMAIL" and "@" in v:
        local, _, domain = v.partition("@")
        return f"{local[:2]}***@{domain}"
    if pii == "PHONE":
        return "***" + v[-2:] if len(v) > 2 else "***"
    if pii == "NAME":
        return (v.strip()[:1] + "***") if v.strip() else "***"
    return "***"


def potential_key(row_count: int, stats: Dict[str, Any], approximate: bool = False) -> bool:
    if not row_count or stats.get("null_count") != 0 or stats.get("distinct_count") is None:
        return False
    if approximate:
        return stats["distinct_count"] >= row_count * (1 - APPROX_KEY_TOLERANCE)
    return stats["distinct_count"] == row_count


def build_profile(name: str, data_type: str, stats: Dict[str, Any], frequencies: List[Dict[str, Any]],
                  patterns: List[Dict[str, Any]], histogram: Optional[List[Dict[str, Any]]] = None,
                  approximate: bool = False) -> Dict[str, Any]:
    """Combine raw statistics into the profile record; masks values of PII columns.

    With `approximate`, distinct counts are HyperLogLog estimates and frequencies come from a sample.
    """
    family = type_family(data_type)
    non_null = stats["row_count"] - stats["physical_null_count"]
    card = cardinality(stats.get("distinct_count"), non_null)
    semantic = infer_semantic_type(name, family, patterns, card)
    pii = pii_classification(semantic)
    if pii != "NONE":
        stats = {**stats, "min": None, "max": None}
    masked_freq = [{"value": mask(f["value"], pii), "count": f["count"]} for f in frequencies]
    profile = {
        "column_name": name,
        "data_type": data_type,
        "family": family,
        "cardinality": card,
        "semantic_type": semantic,
        "pii_classification": pii,
        "potential_key": potential_key(stats["row_count"], stats, approximate),
        "patterns": patterns,
        "sample_values": masked_freq[:5],
        "statistics": {
            **stats,
            "frequency_distribution": masked_freq,
            "date_format": date_format(patterns) if family == "TEXT" else None,
            "enum_values": ([f["value"] for f in masked_freq]
                            if card in ("LOW", "CONSTANT") and stats.get("distinct_count", 99) <= ENUM_MAX_DISTINCT
                            else None),
        },
    }
    if family in ("DATE", "TIMESTAMP") or profile["statistics"]["date_format"]:
        profile["statistics"]["date_range"] = {"min": stats.get("min"), "max": stats.get("max")}
    if family == "NUMBER":
        profile["statistics"]["numeric_range"] = {"min": stats.get("min"), "max": stats.get("max")}
        if histogram and pii == "NONE":
            profile["statistics"]["histogram"] = histogram
    if approximate:
        profile["statistics"]["approximate"] = {"distinct": "APPROX_COUNT_DISTINCT",
                                                "frequencies_sample_rows": SAMPLE_ROWS}
    return profile


def enrichment_prompt(table: str, profiles: Sequence[Dict[str, Any]], skill_excerpt: str = "") -> str:
    """Statistics only; sample values of PII columns are never included."""
    lines = []
    for p in profiles:
        s = p["statistics"]
        samples = "" if p["pii_classification"] != "NONE" else ", ".join(
            str(v["value"]) for v in p["sample_values"][:3])
        lines.append(
            f"- {p['column_name']} ({p['data_type']}): null {s['null_percentage']}%, "
            f"distinct {s.get('distinct_percentage', 'n/a')}%, cardinality {p['cardinality']}, "
            f"patterns {[x['pattern'] for x in p['patterns'][:2]]}, rule-based type {p['semantic_type']}"
            + (f", samples: {samples}" if samples else ", samples withheld (personal data)")
        )
    guidance = f"\nFollow these loaded skills:\n{skill_excerpt}\n" if skill_excerpt else ""
    return (
        "You document columns of a landed source table for a data engineering catalog.\n"
        f"Table: {table}\nColumns:\n" + "\n".join(lines) + "\n"
        + guidance +
        "\nFor each column return a one-sentence business description (no sample values) and a semantic type from: "
        "IDENTIFIER, PERSON_NAME, NAME, EMAIL, PHONE, DATE_OF_BIRTH, DATE, TIMESTAMP, STATUS_CODE, CODE, REGION, "
        "CURRENCY_CODE, AMOUNT, NUMBER, TEXT, BOOLEAN."
    )


ENRICHMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "column_name": {"type": "string"},
                    "description": {"type": "string"},
                    "semantic_type": {"type": "string"},
                },
                "required": ["column_name", "description", "semantic_type"],
            },
        }
    },
    "required": ["columns"],
}

GENERIC_TYPES = {"TEXT", "NUMBER", "OTHER", "CODE", "NAME"}


# ---------------------------------------------------------------- persistent profile documents


def stage_segment(name: str) -> str:
    """Upper-case [A-Z0-9_] path segment. A short hash of the raw name is appended whenever cleaning
    changed it, so "crm-customer", "crm customer" and "CRM_CUSTOMER" never share a file."""
    raw = (name or "").strip()
    assert raw, "empty name in profile path"
    cleaned = re.sub(r"[^A-Z0-9_]", "_", raw.upper())
    if cleaned != raw:
        cleaned = f"{cleaned}__{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8].upper()}"
    return cleaned


def profile_stage_path(source_name: str, database: str, schema: str, table: str) -> str:
    path = "/".join(stage_segment(p) for p in (source_name, database, schema, table)) + ".json"
    assert STAGE_PATH.match(path), f"unsafe profile path: {path}"
    return path


def source_fingerprint(columns: Iterable[Tuple[str, str]], row_count: Optional[int],
                       last_altered: Optional[str] = None) -> str:
    """Changes when the table's columns, row count or source LAST_ALTERED change; a mismatch means stale."""
    # LAST_ALTERED is compared to the second: it reaches here from INFORMATION_SCHEMA and from stored copies
    # whose fractional-second and time-zone rendering can differ.
    payload = {"columns": [[str(n), str(t)] for n, t in columns], "rows": None if row_count is None else int(row_count),
               "altered": str(last_altered or "")[:19], "profiler": PROFILER_VERSION}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def profile_document(source: Dict[str, str], row_count: int, columns: Sequence[Dict[str, Any]],
                     fingerprint: str, approximate: bool, model: Optional[str],
                     profiled_at: str) -> Dict[str, Any]:
    """Run-independent profile of one table. Landing ids and foreign keys depend on the run and are dropped."""
    return {
        "profiler_version": PROFILER_VERSION,
        "source": {k: source[k] for k in ("source_name", "database", "schema", "table")},
        "row_count": int(row_count),
        "column_count": len(columns),
        "approximate": bool(approximate),
        "fingerprint": fingerprint,
        "model_version": model,
        "profiled_at": profiled_at,
        "columns": [{k: v for k, v in c.items() if k not in RUN_SCOPED_KEYS} for c in columns],
    }


def document_checksum(document: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def cache_is_fresh(index_row: Optional[Dict[str, Any]], fingerprint: str) -> bool:
    return bool(index_row) and index_row.get("SOURCE_FINGERPRINT") == fingerprint \
        and str(index_row.get("PROFILER_VERSION") or "") == PROFILER_VERSION


def group_rows(raw: Iterable[Dict[str, Any]], value_key: str) -> Dict[int, List[Dict[str, Any]]]:
    """Rows of the batched per-table queries (C = column position) grouped by column, most frequent first.
    UNION ALL does not keep the branches' ORDER BY, so the order is restored here."""
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for row in raw:
        grouped.setdefault(int(row["C"]), []).append({"value": row[value_key], "count": int(row["N"])})
    for items in grouped.values():
        items.sort(key=lambda x: (-x["count"], str(x["value"])))
    return grouped
