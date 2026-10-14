"""What a reference input is, from its name and content (pure).

DATA_MODEL   a data model: Erwin XML, a data dictionary (a row per column: table, column, data type), DDL, a model or
             domain pack JSON
MDM_SCHEMA   a post-MDM model (Reltio's output): a data model whose tables include a *_CROSS_REFERENCE with an
             enterprise id; read as a data model and as the MDM contract it implies
STTM         a source-to-target mapping sheet: source and target columns per row
DBT_CODE     dbt project files: dbt_project.yml, models with ref()/source()/config(), a zip of a dbt project
SAMPLE_ROWS  rows of one table (a CSV or sheet whose header is column names, not a dictionary or a mapping)
DOCUMENT     anything else: PDF, Word, PowerPoint, images, Markdown, Confluence; read for knowledge
The person confirms or changes the kind before it is used.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Dict, List, Tuple

KINDS = ("DATA_MODEL", "MDM_SCHEMA", "STTM", "DBT_CODE", "SAMPLE_ROWS", "DOCUMENT")
SOURCE_HINTS = ("source column", "source field", "source attribute", "src column", "source col", "source_column",
                "from column")
TARGET_HINTS = ("target column", "target field", "target attribute", "tgt column", "target col", "target_column",
                "to column")
DBT_MARKERS = re.compile(r"\{\{\s*(ref|source|config)\s*\(", re.I)
XREF = re.compile(r"_CROSS_REFERENCE\b", re.I)


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-./]+", " ", (h or "").strip().lower())).strip()


def _header_kind(rows: List[List[str]]) -> Tuple[str, str]:
    """STTM, DATA_MODEL or SAMPLE_ROWS from a sheet's first rows."""
    from services.modeling.model_files import _header_row

    for row in rows[:15]:
        names = {_norm(c) for c in row}
        if any(_norm(h) in names for h in SOURCE_HINTS) and any(_norm(h) in names for h in TARGET_HINTS):
            return "STTM", "source and target columns per row"
    i, _cols = _header_row(rows)
    if i >= 0:
        tables = {(r[_cols["table"]] if "table" in _cols and len(r) > _cols["table"] else "") for r in rows[i + 1:]}
        flat = " ".join(t for t in tables if t)
        if XREF.search(flat):
            return "MDM_SCHEMA", "a data dictionary that includes a cross-reference table"
        return "DATA_MODEL", "a data dictionary (a row per column)"
    return "SAMPLE_ROWS", "rows of a table (the header is column names)"


def _csv_rows(text: str) -> List[List[str]]:
    import csv

    sample = text[:200_000]
    try:
        dialect = csv.Sniffer().sniff(sample.split("\n", 1)[0] if "\n" in sample else sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.reader(io.StringIO(sample), dialect) if any(c.strip() for c in r)][:40]


def detect(name: str, data: bytes) -> Dict[str, str]:
    """{"kind", "why"} for a file; DOCUMENT when nothing more specific fits."""
    lower = (name or "").lower()
    if lower.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = [n.lower() for n in z.namelist()]
        except zipfile.BadZipFile:
            return {"kind": "DOCUMENT", "why": "not a readable zip; kept as a document"}
        if any(n.endswith("dbt_project.yml") for n in names) or any("/models/" in f"/{n}" and n.endswith(".sql") for n in names):
            return {"kind": "DBT_CODE", "why": "a dbt project (dbt_project.yml or models/*.sql)"}
        return {"kind": "DOCUMENT", "why": "a zip without a dbt project; its files are read as documents"}
    if lower.endswith((".xlsx", ".xlsm")):
        try:
            from services.agentic.attachments import xlsx_rows

            sheets = xlsx_rows(data, max_rows=60)
        except Exception:
            return {"kind": "DOCUMENT", "why": "a workbook that could not be read as a table; read as a document"}
        for _title, rows in sheets:
            if rows:
                kind, why = _header_kind(rows)
                return {"kind": kind, "why": why}
        return {"kind": "DOCUMENT", "why": "an empty workbook"}
    if lower.endswith((".pdf", ".docx", ".pptx", ".doc", ".ppt", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
                       ".tif", ".tiff", ".html", ".htm", ".md")):
        return {"kind": "DOCUMENT", "why": "a document or image, read for knowledge"}
    text = data[:400_000].decode("utf-8", errors="ignore")
    head = text.lstrip()[:4000]
    if lower.endswith("dbt_project.yml") or DBT_MARKERS.search(text) or (lower.endswith((".yml", ".yaml")) and re.search(r"^models:\s*$", text, re.M)):
        return {"kind": "DBT_CODE", "why": "dbt code (ref, source or config, or a models: file)"}
    if lower.endswith(".xml") or head.startswith("<"):
        if re.search(r"erwin|<Entity\b|<Attribute\b", head, re.I):
            return {"kind": "MDM_SCHEMA" if XREF.search(text) else "DATA_MODEL", "why": "an Erwin model (XML)"}
        return {"kind": "DOCUMENT", "why": "an XML document"}
    if re.search(r"\bCREATE\s+(OR\s+REPLACE\s+)?(ICEBERG\s+|TRANSIENT\s+)?TABLE\b", text, re.I):
        return {"kind": "MDM_SCHEMA" if XREF.search(text) else "DATA_MODEL", "why": "DDL (CREATE TABLE statements)"}
    if lower.endswith(".json") or head.startswith("{"):
        if re.search(r'"(targets|entities|design)"\s*:', head):
            return {"kind": "DATA_MODEL", "why": "a model specification or domain pack (JSON)"}
        return {"kind": "DOCUMENT", "why": "a JSON document"}
    if lower.endswith((".csv", ".tsv", ".txt")):
        rows = _csv_rows(text)
        if rows and len(rows[0]) > 1:
            kind, why = _header_kind(rows)
            return {"kind": kind, "why": why}
    return {"kind": "DOCUMENT", "why": "read for knowledge"}
