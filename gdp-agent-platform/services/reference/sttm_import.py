"""A person's STTM sheet as mapping lines (pure): what feeds each target column, and how.

Reads the platform's own export (services/sttm/refine.CSV_COLUMNS) and the common layouts of hand-made mapping sheets:
one row per target column with the source table and column and the transformation. Header names are matched loosely
(Source Column, src_field, Target Attribute, Transformation Logic, ...). The lines are suggestions: mapping ranks
them first with their origin, and the STTM pre-fills their transformations; the gates still decide.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

FIELDS: Dict[str, Tuple[str, ...]] = {
    "source_table": ("source table", "source entity", "src table", "source object", "from table", "source_table",
                     "source table name"),
    "source_column": ("source column", "source field", "source attribute", "src column", "src field", "source col",
                      "from column", "source_column", "source column name"),
    "target_table": ("target table", "target entity", "tgt table", "target object", "to table", "target_table",
                     "target table name"),
    "target_column": ("target column", "target field", "target attribute", "tgt column", "tgt field", "target col",
                      "to column", "target_column", "target column name"),
    "transformation": ("transformation", "transformation logic", "transformation rule", "logic", "rule", "expression",
                       "derivation", "mapping logic", "transform"),
    "target_datatype": ("target datatype", "target data type", "target type", "target_datatype"),
    "mapping_type": ("mapping type", "mapping_type", "type of mapping"),
    "business_definition": ("business definition", "definition", "description", "business_definition", "comments"),
}


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-./]+", " ", (h or "").strip().lower())).strip()


def _columns(header: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i, h in enumerate(header):
        n = _norm(h)
        for field, names in FIELDS.items():
            if field not in out and n in {_norm(x) for x in names}:
                out[field] = i
                break
    return out


def parse(sheets: List[Tuple[str, List[List[str]]]], default_target: Optional[str] = None) -> Dict[str, Any]:
    """{"lines": [{target_table, target_column, source_table, source_column, transformation, mapping_type, ...}],
    "targets": [table], "notes": [str]} from the sheets of a workbook or one CSV."""
    lines: List[Dict[str, Any]] = []
    notes: List[str] = []
    for title, rows in sheets:
        start, cols = -1, {}
        for i, row in enumerate(rows[:15]):
            c = _columns(row)
            if "target_column" in c and ("source_column" in c or "transformation" in c):
                start, cols = i, c
                break
        if start < 0:
            notes.append(f"{title}: no header with a target column and a source column or transformation")
            continue
        sheet_target = (default_target or re.sub(r"\W+", "_", title).strip("_")).upper()

        def cell(row: List[str], field: str) -> str:
            i = cols.get(field)
            return (row[i] if i is not None and i < len(row) else "").strip()

        for row in rows[start + 1:]:
            target = cell(row, "target_column").upper()
            if not target:
                continue
            transformation = cell(row, "transformation") or None
            source_column = cell(row, "source_column").upper() or None
            lines.append({
                "target_table": (cell(row, "target_table") or sheet_target).upper().split(".")[-1],
                "target_column": target,
                "source_table": (cell(row, "source_table").upper().split(".")[-1] or None),
                "source_column": source_column,
                "transformation": transformation,
                "target_datatype": cell(row, "target_datatype") or None,
                "mapping_type": (cell(row, "mapping_type").upper() or
                                 ("TRANSFORM" if transformation and transformation.upper() != (source_column or "") else
                                  "DIRECT" if source_column else "UNMAPPED")),
                "business_definition": cell(row, "business_definition") or None,
            })
    targets = sorted({l["target_table"] for l in lines})
    return {"lines": lines, "targets": targets, "notes": notes}


def for_target(lines: List[Dict[str, Any]], target_table: str) -> Dict[str, Dict[str, Any]]:
    """The reference lines of one target table, by target column."""
    t = str(target_table or "").upper().split(".")[-1]
    return {l["target_column"]: l for l in lines if l["target_table"] == t}


SQL_CALL = re.compile(r"\b(CASE|CAST|TRY_CAST|COALESCE|NVL|NVL2|IFF|DECODE|TRIM|LTRIM|RTRIM|UPPER|LOWER|INITCAP|SUBSTR|SUBSTRING|"
                      r"LEFT|RIGHT|CONCAT|REPLACE|REGEXP_\w+|TO_\w+|TRY_TO_\w+|DATE_TRUNC|DATEADD|DATEDIFF|ROUND|ABS|NULLIF|"
                      r"SPLIT_PART|LPAD|RPAD|CURRENT_\w+|SHA2|MD5|HASH)\b\s*\(?", re.I)
SQL_CHARS = re.compile(r"^[\w\s.,()'\"|+\-*/<>=:%!]+$")


def as_expression(text: Optional[str], source_column: Optional[str]) -> Optional[str]:
    """A transformation cell as a column expression the STTM can carry, or None when it is the column itself or prose
    ("take the latest value"), which stays evidence for the reviewer instead of becoming SQL."""
    t = (text or "").strip().rstrip(";")
    if not t or t.upper() == (source_column or "").upper() or t.upper() in ("DIRECT", "DIRECT MOVE", "AS IS", "1:1", "STRAIGHT"):
        return None
    if not SQL_CHARS.match(t) or len(t) > 2000:
        return None
    if SQL_CALL.search(t) or "||" in t or re.fullmatch(r"'[^']*'|-?\d+(\.\d+)?|NULL", t, re.I):
        return t
    return None


def rules_for(lines: List[Dict[str, Any]], target_table: str, source_columns: List[Dict[str, Any]],
              title: str = "your STTM") -> Dict[str, Dict[str, Any]]:
    """Mapping rules from a reference STTM for one target table, by source column (pure): each line whose source
    column is among the run's (and whose source table, when it names one, is the run's) puts its target first, with
    the reference's transformation pre-filled when it is an expression. UNMAPPED lines and lines without a source
    column are not rules."""
    names = {str(s["column_name"]).upper(): str(s.get("table_name") or "").upper().split(".")[-1] for s in source_columns}
    out: Dict[str, Dict[str, Any]] = {}
    for target_column, line in for_target(lines, target_table).items():
        src = (line.get("source_column") or "").upper().split(".")[-1]
        if not src or src not in names or line.get("mapping_type") == "UNMAPPED":
            continue
        if line.get("source_table") and names[src] and line["source_table"] != names[src]:
            continue
        if src in out:   # one source column feeding two targets: the first line wins, the reviewer sees the rest
            continue
        expression = as_expression(line.get("transformation"), src)
        out[src] = {"source_column": src, "target_column": target_column, "kind": "REFERENCE_STTM", "origin": "REFERENCE",
                    "reference": title, "transformation": expression,
                    "expression": line.get("transformation") or f"{src} as is"}
    return out
