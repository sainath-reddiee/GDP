"""Correct-output examples: a few rows of a target table exactly as they should come out of the pipeline, given by the
people who know the data. They are the strongest context a run can get, more than any description: STTM refinement
and the Copilot read them, and they show what "right" means when a result is checked.

An upload is read (CSV or Excel), checked against the table in the approved data model (unknown columns, missing
business keys) and written as one EXAMPLE knowledge item per table, always as a proposal: a steward accepts it in the
review inbox before any stage uses it. A new upload for the same table replaces the waiting proposal (same
SOURCE_REFERENCE, example.<table>).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

MAX_ROWS = 50
MIN_ROWS = 1
MAX_CELL = 500
PROMPT_ROWS = 5


class ExampleError(ValueError):
    """The upload cannot be used as examples; the message says why."""


def _ident(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_$]", "_", (value or "").strip())).strip("_").upper()


def read_rows(name: str, data: bytes) -> Tuple[List[str], List[List[str]]]:
    """(header, rows) of the first sheet or CSV: the first non-empty row is the header; empty rows are skipped."""
    lower = (name or "").lower()
    if lower.endswith((".xlsx", ".xlsm")):
        from services.agentic.attachments import xlsx_rows

        try:
            sheets = xlsx_rows(data, max_rows=MAX_ROWS * 4)
        except Exception as exc:
            raise ExampleError(f"The workbook could not be read ({exc}). Save it as .xlsx or CSV.") from None
        table = next((rows for _, rows in sheets if rows), [])
    elif lower.endswith((".csv", ".tsv", ".txt")):
        from services.modeling.model_files import _csv_rows, _text

        table = _csv_rows(_text(data))
    else:
        raise ExampleError("Upload the examples as .csv or .xlsx, one row per record, with the column names in the first row.")
    table = [r for r in table if any((c or "").strip() for c in r)]
    if not table:
        raise ExampleError("The file has no rows.")
    header = [_ident(h) for h in table[0]]
    while header and not header[-1]:
        header.pop()
    if not any(header):
        raise ExampleError("The first row must name the columns.")
    rows = [[(r[i] if i < len(r) else "").strip()[:MAX_CELL] for i in range(len(header))] for r in table[1:]]
    return header, rows


def check(table: str, header: List[str], rows: List[List[str]], entity: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The examples checked against the table's model entity (attributes, business_keys): the columns kept (known
    ones, in file order), unknown and absent columns, business keys missing in the file or empty in a row, and the
    rows (at most MAX_ROWS). Raises ExampleError when nothing usable is left."""
    if entity is None:
        raise ExampleError(f"{table} is not a table of the approved data model.")
    known = [a["name"] for a in entity.get("attributes") or [] if a.get("status") != "RETIRED"]
    known_set = set(known)
    dupes = sorted({h for h in header if h and header.count(h) > 1})
    if dupes:
        raise ExampleError(f"Columns named twice: {', '.join(dupes)}.")
    keep = [i for i, h in enumerate(header) if h in known_set]
    unknown = [h for h in header if h and h not in known_set]
    if not keep:
        raise ExampleError(f"None of the file's columns are columns of {table}. Use the table's column names in the first row.")
    columns = [header[i] for i in keep]
    data = [[r[i] for i in keep] for r in rows]
    data = [r for r in data if any(r)]
    if len(data) < MIN_ROWS:
        raise ExampleError("The file has a header but no example rows.")
    truncated = max(0, len(data) - MAX_ROWS)
    data = data[:MAX_ROWS]
    keys = [k for k in entity.get("business_keys") or [] if k in known_set]
    missing_keys = [k for k in keys if k not in columns]
    empty_keys = sorted({columns[j] for r in data for j, v in enumerate(r) if columns[j] in keys and not v})
    seen, duplicate_keys = set(), 0
    if keys and not missing_keys:
        at = [columns.index(k) for k in keys]
        for r in data:
            key = tuple(r[i] for i in at)
            duplicate_keys += key in seen
            seen.add(key)
    warnings = []
    if unknown:
        warnings.append(f"{len(unknown)} column(s) not in {table} were left out: {', '.join(unknown[:10])}")
    if missing_keys:
        warnings.append(f"business key column(s) missing: {', '.join(missing_keys)}")
    if empty_keys:
        warnings.append(f"business key column(s) empty in some rows: {', '.join(empty_keys)}")
    if duplicate_keys:
        warnings.append(f"{duplicate_keys} row(s) repeat a business key")
    if truncated:
        warnings.append(f"only the first {MAX_ROWS} rows were kept ({truncated} more in the file)")
    return {"table": table, "columns": columns, "rows": data, "row_count": len(data), "unknown_columns": unknown,
            "absent_columns": [c for c in known if c not in columns], "missing_keys": missing_keys,
            "empty_keys": empty_keys, "duplicate_keys": duplicate_keys, "warnings": warnings}


def markdown(table: str, columns: List[str], rows: List[List[str]], limit: int = PROMPT_ROWS) -> str:
    """The examples as a Markdown table (for search, review and prompts), at most `limit` rows."""
    def cell(v: str) -> str:
        v = (v or "").replace("|", "\\|").replace("\n", " ")
        return v if len(v) <= 60 else v[:57] + "..."
    lines = [f"Correct output rows of {table} ({len(rows)} example{'s' if len(rows) != 1 else ''}).", "",
             "| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"({len(rows) - limit} more rows)")
    return "\n".join(lines)


def reference(table: str) -> str:
    return f"example.{table.lower()}"


def write(session, *, domain_id: str, checked: Dict[str, Any], file_name: str) -> Optional[str]:
    """Write the examples as a proposal (EXAMPLE, origin DOCUMENT). Returns the knowledge id, None when unchanged."""
    from services.knowledge.writer import remember

    table = checked["table"]
    return remember(session, domain_id=domain_id, kind="EXAMPLE", key=reference(table),
                    title=f"{table}: correct output examples", content=markdown(table, checked["columns"], checked["rows"], 10),
                    content_json={"target_table": table, "columns": checked["columns"], "rows": checked["rows"],
                                  "row_count": checked["row_count"], "file": file_name[:300],
                                  "unknown_columns": checked["unknown_columns"], "warnings": checked["warnings"]},
                    origin="DOCUMENT", mode="review",
                    change_note=f"{checked['row_count']} example rows from {file_name[:200]}")


def as_context(items: List[Dict[str, Any]], limit: int = PROMPT_ROWS) -> List[str]:
    """Accepted EXAMPLE items (CONTENT_JSON with columns and rows) as short prompt blocks."""
    from services.common.sql import variant

    out = []
    for it in items:
        cj = variant(it.get("CONTENT_JSON") if "CONTENT_JSON" in it else it.get("content_json")) or {}
        if cj.get("columns") and cj.get("rows"):
            out.append(markdown(str(cj.get("target_table") or ""), cj["columns"], cj["rows"], limit))
    return out
