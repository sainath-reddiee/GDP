"""Deterministic profiling: SQL builders and classification rules (pure, unit-tested).

Ported from the modeler's profile_real_data.py (robust null placeholders, top values, numeric ranges) and
iterative_mapper.py (semantic hints), extended with patterns, lengths, duplicates, date formats, PII and keys.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.source.identifiers import quote

NULL_PLACEHOLDERS = ("", "N/A", "NA", "NULL", "NONE", "<NULL>", ".")
TOP_VALUES = 10
TOP_PATTERNS = 5
ENUM_MAX_DISTINCT = 20

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


def stats_sql(table_fqn: str, columns: Sequence[Tuple[str, str]]) -> str:
    """One pass over the table: counts, robust nulls, distinct, min/max, length and numeric stats per column."""
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
        parts += [f"COUNT(DISTINCT {q}) AS D{i}", f"MIN({q})::VARCHAR AS MIN{i}", f"MAX({q})::VARCHAR AS MAX{i}"]
    return f"SELECT {', '.join(parts)} FROM {table_fqn}"


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


def column_stats(row: Dict[str, Any], i: int, row_count: int) -> Dict[str, Any]:
    non_null = int(row.get(f"N{i}") or 0)
    valid = int(row.get(f"V{i}") or 0)
    distinct = row.get(f"D{i}")
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


def potential_key(row_count: int, stats: Dict[str, Any]) -> bool:
    return bool(row_count) and stats.get("null_count") == 0 and stats.get("distinct_count") == row_count


def build_profile(name: str, data_type: str, stats: Dict[str, Any], frequencies: List[Dict[str, Any]],
                  patterns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Combine raw statistics into the profile record; masks values of PII columns."""
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
        "potential_key": potential_key(stats["row_count"], stats),
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
