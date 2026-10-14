"""Data model files brought into a domain model draft: an Erwin model saved as XML, a data dictionary or Erwin column
report (Excel or CSV), DDL (Erwin's forward-engineered script, or any CREATE TABLE script) and a domain_pack.json.

Every parser returns a raw v2 specification ({design: {entities}, relationships}) plus notes on what was skipped, so
the workbench merges it into the draft as unsaved changes for review, like any other import. Nothing here is approved
automatically.

Erwin's native .erwin file is a proprietary binary: it is refused with the two exports Erwin offers instead
(File, Save As, XML; or Tools, Forward Engineer, Schema Generation, which writes DDL).
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree

MAX_BYTES = 25 * 1024 * 1024
GUID = re.compile(r"\{[0-9A-Fa-f-]{36}\}(?:\+[0-9A-Fa-f]+)?")
NATIVE = (".erwin", ".erw", ".er1", ".dm1", ".pdm", ".ldm")


class ModelFileError(ValueError):
    """The file cannot be read as a data model; the message says what to export instead."""


def _local(tag: Any) -> str:
    """Element name without namespace or prefix: {ns}Entity, EMX:Entity and Entity are all Entity."""
    return str(tag).rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def physical(name: str) -> str:
    """A logical name as an identifier: 'Customer Id' -> CUSTOMER_ID."""
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_$]", "_", (name or "").strip())).strip("_").upper()


_TYPES = [
    (r"^N?VARCHAR2|^NVARCHAR|^CHARACTER VARYING|^VARCHAR", "VARCHAR"),
    (r"^N?CHAR\b|^NCHAR|^CHARACTER\b", "CHAR"),
    (r"^DATETIME2|^DATETIME|^SMALLDATETIME|^TIMESTAMP WITHOUT TIME ZONE", "TIMESTAMP_NTZ"),
    (r"^DATETIMEOFFSET|^TIMESTAMP WITH TIME ZONE", "TIMESTAMP_TZ"),
    (r"^BIT$", "BOOLEAN"),
    (r"^MONEY$", "NUMBER(19,4)"),
    (r"^SMALLMONEY$", "NUMBER(10,4)"),
    (r"^UNIQUEIDENTIFIER$", "VARCHAR(36)"),
    (r"^(N?TEXT|CLOB|NCLOB|LONG)$", "VARCHAR"),
    (r"^(IMAGE|BLOB|LONG RAW|VARBINARY\(MAX\))$", "BINARY"),
    (r"^SERIAL$", "INTEGER"),
    (r"^BIGSERIAL$", "BIGINT"),
]


def snowflake_type(raw: str) -> str:
    """A modeling tool's data type in Snowflake terms; anything already valid is kept as it is."""
    t = re.sub(r"\s+", " ", (raw or "").strip().upper())
    if not t:
        return "VARCHAR"
    t = t.replace("(MAX)", "") if re.match(r"^N?VARCHAR", t) else t
    t = re.sub(r"\s+(BYTE|CHAR)\)", ")", t)   # Oracle VARCHAR2(50 BYTE)
    for pattern, target in _TYPES:
        m = re.match(pattern, t)
        if m:
            size = t[m.end():].strip()
            keep = size if size.startswith("(") and target in ("VARCHAR", "CHAR") else ""
            return target + keep
    return t


# ---------------------------------------------------------------- Erwin XML

def _props(el: ElementTree.Element) -> Dict[str, str]:
    """An Erwin object's properties by name: child elements of its *Props / Properties block, its own simple
    children, and its XML attributes (the formats of Erwin 4 to 9 and later differ only in where these live)."""
    out: Dict[str, str] = {}
    for k, v in el.attrib.items():
        out[_local(k)] = v
    for child in el:
        name = _local(child.tag)
        if name.endswith("Props") or name == "Properties":
            for p in child:
                if len(p) == 0:
                    out.setdefault(_local(p.tag), (p.text or "").strip())
        elif len(child) == 0 and (child.text or "").strip():
            out.setdefault(name, child.text.strip())
    return out


def _refs(value: Optional[str]) -> List[str]:
    return GUID.findall(value or "") or ([value.strip()] if value and value.strip() else [])


def _name(props: Dict[str, str]) -> str:
    raw = props.get("Physical_Name") or ""
    if not raw or "%" in raw:   # unset, or an Erwin macro such as %EntityName
        raw = props.get("name") or props.get("Name") or ""
    return physical(raw)


def _not_null(props: Dict[str, str]) -> Optional[bool]:
    for key in ("Null_Option_Type", "Null_Option", "Nullability"):
        v = (props.get(key) or "").strip().upper()
        if not v:
            continue
        if v in ("1", "NOT NULL", "NOT_NULL", "NN", "TRUE"):
            return True
        if v in ("0", "NULL", "NULLABLE", "FALSE"):
            return False
    return None


def _owned(el: ElementTree.Element, kind: str, stop: str) -> Iterable[ElementTree.Element]:
    """Descendants named `kind` that belong to `el` and not to a nested `stop` object."""
    for child in el:
        name = _local(child.tag)
        if name == kind:
            yield child
        elif name != stop:
            yield from _owned(child, kind, stop)


def parse_erwin_xml(text: str) -> Dict[str, Any]:
    """An Erwin model saved as XML: entities (tables), attributes (columns, with data type, nullability and
    definition), primary key groups, relationships (foreign key columns through each attribute's parent attribute)
    and subject areas. Physical names are used when set, otherwise the logical names become identifiers."""
    if re.search(r"<!DOCTYPE|<!ENTITY", text[:5000], re.I):
        raise ModelFileError("The XML declares a DOCTYPE or entities, which are not accepted. Save the model as plain XML.")
    try:
        root = ElementTree.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ElementTree.ParseError as exc:
        raise ModelFileError(f"The file is not well-formed XML ({exc}).") from None
    entities_el = [e for e in root.iter() if _local(e.tag) == "Entity"]
    if not entities_el:
        raise ModelFileError("No Erwin entities were found in this XML. In Erwin use File, Save As, XML file; or export "
                             "the schema as DDL (Tools, Forward Engineer).")
    notes: List[str] = []
    by_id: Dict[str, Dict[str, Any]] = {}          # entity id -> entity
    attr_index: Dict[str, Tuple[str, str]] = {}    # attribute id -> (entity name, column name)
    fk_links: List[Tuple[str, str, str, str]] = []  # (child entity, child column, relationship id, parent attribute id)
    entities: List[Dict[str, Any]] = []
    for el in entities_el:
        p = _props(el)
        name = _name(p)
        if not name:
            continue
        entity: Dict[str, Any] = {"entity_name": name, "attributes": [], "business_keys": [],
                                  "description": p.get("Definition") or p.get("Comment") or ""}
        if p.get("Owner") or p.get("Schema_Name"):
            entity["schema"] = physical(p.get("Owner") or p.get("Schema_Name") or "")
        for a in _owned(el, "Attribute", "Entity"):
            ap = _props(a)
            col = _name(ap)
            if not col or any(x["name"] == col for x in entity["attributes"]):
                continue
            attr = {"name": col, "datatype": snowflake_type(ap.get("Physical_Data_Type") or ap.get("Logical_Data_Type")
                                                             or ap.get("Datatype") or ""),
                    "description": ap.get("Definition") or ap.get("Comment") or ""}
            nn = _not_null(ap)
            if nn is not None:
                attr["nullable"] = not nn
            if ap.get("Default_Value") and "%" not in ap["Default_Value"]:
                attr["default"] = ap["Default_Value"][:500]
            entity["attributes"].append(attr)
            if a.get("id"):
                attr_index[a.get("id")] = (name, col)
            rel, parent_attr = ap.get("Parent_Relationship_Ref"), ap.get("Parent_Attribute_Ref")
            if rel and parent_attr:
                fk_links.append((name, col, _refs(rel)[0], _refs(parent_attr)[0]))
        pk_ids: List[str] = []
        for kg in _owned(el, "Key_Group", "Entity"):
            kp = _props(kg)
            kind = (kp.get("Key_Group_Type") or "").upper()
            if kind == "PK" or (not kind and (kp.get("name") or "").upper().startswith("XPK")):
                for m in kg.iter():
                    if _local(m.tag) == "Key_Group_Member":
                        pk_ids += _refs(_props(m).get("Attribute_Ref"))
        pk_cols = [attr_index[i][1] for i in pk_ids if i in attr_index and attr_index[i][0] == name]
        for attr in entity["attributes"]:
            if attr["name"] in pk_cols:
                attr["is_pk"], attr["nullable"] = True, False
        entity["business_keys"] = pk_cols
        if not entity["attributes"]:
            notes.append(f"{name} has no attributes in the file")
        entities.append(entity)
        if el.get("id"):
            by_id[el.get("id")] = entity
    rel_ends: Dict[str, Tuple[str, str, bool]] = {}   # relationship id -> (parent entity, child entity, optional)
    for r in root.iter():
        if _local(r.tag) != "Relationship":
            continue
        rp = _props(r)
        parent, child = _refs(rp.get("Parent_Entity_Ref")), _refs(rp.get("Child_Entity_Ref"))
        if parent and child and parent[0] in by_id and child[0] in by_id and r.get("id"):
            rel_ends[r.get("id")] = (by_id[parent[0]]["entity_name"], by_id[child[0]]["entity_name"],
                                     _not_null(rp) is False)
    relationships, linked = [], set()
    for child, col, rel_id, parent_attr in fk_links:
        target = attr_index.get(parent_attr)
        if not target:
            continue
        ends = rel_ends.get(rel_id)
        relationships.append({"from_entity": child, "from_column": col, "to_entity": target[0], "to_column": target[1],
                              "cardinality": "N:1", "from_optional": ends[2] if ends else True})
        linked.add(rel_id)
    by_name = {e["entity_name"]: e for e in entities}
    for rel_id, (parent, child, optional) in rel_ends.items():
        if rel_id in linked:
            continue
        # no attribute-level links: pair the parent's key columns with same-named child columns
        keys = by_name[parent]["business_keys"]
        child_cols = {a["name"] for a in by_name[child]["attributes"]}
        pairs = [(k, k) for k in keys if k in child_cols]
        for fc, tc in pairs:
            relationships.append({"from_entity": child, "from_column": fc, "to_entity": parent, "to_column": tc,
                                  "cardinality": "N:1", "from_optional": optional})
        if not pairs:
            notes.append(f"relationship {child} to {parent}: no matching key columns, not imported")
    for sa in root.iter():
        if _local(sa.tag) != "Subject_Area":
            continue
        area = (_props(sa).get("name") or "").strip()[:80]
        if not area or area.lower() in ("<main subject area>", "main subject area"):
            continue
        for ref in GUID.findall(ElementTree.tostring(sa, encoding="unicode")):
            if ref in by_id and not by_id[ref].get("subject_area"):
                by_id[ref]["subject_area"] = area
    return {"design": {"entities": entities}, "relationships": relationships, "notes": notes}


# ---------------------------------------------------------------- data dictionaries (Excel, CSV)

HEADERS: Dict[str, Tuple[str, ...]] = {
    "table": ("table", "table name", "tablename", "entity", "entity name", "physical table name", "table physical name",
              "target table", "physical name of table", "table_name", "entity_name", "object name"),
    "column": ("column", "column name", "columnname", "attribute", "attribute name", "physical column name",
               "column physical name", "field", "field name", "column_name", "attribute_name", "target column"),
    "datatype": ("datatype", "data type", "type", "physical data type", "column datatype", "column data type",
                 "data_type", "attribute datatype", "logical data type"),
    "nullable": ("nullable", "null option", "null", "is nullable", "null option type", "allow nulls", "null_option",
                 "allows null"),
    "required": ("required", "not null", "mandatory", "is required", "is mandatory"),
    "pk": ("pk", "is pk", "primary key", "is primary key", "primary_key", "is_pk", "pk flag", "key", "key type",
           "primary key flag", "is key"),
    "business_key": ("business key", "is business key", "business_key", "bk", "natural key"),
    "description": ("description", "definition", "comment", "comments", "column definition", "attribute definition",
                    "column description", "attribute description", "notes", "column comment"),
    "business_definition": ("business definition", "business_definition", "business description"),
    "references_table": ("references", "references table", "fk table", "parent table", "parent entity",
                         "foreign key table", "referenced table", "fk to"),
    "references_column": ("references column", "fk column", "parent column", "parent attribute", "referenced column",
                          "foreign key column"),
    "table_description": ("table description", "entity definition", "table definition", "entity description",
                          "table comment"),
    "schema": ("schema", "schema name", "owner", "table schema"),
    "table_type": ("table type", "table_type", "entity type"),
    "subject_area": ("subject area", "subject_area", "area"),
    "default": ("default", "default value"),
    "pii": ("pii", "is pii", "sensitive"),
}
TRUE = {"y", "yes", "true", "1", "x", "pk", "t", "✓", "not null", "nn", "required", "mandatory"}


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-./]+", " ", (h or "").strip().lower())).strip()


def _columns_of(header: List[str]) -> Dict[str, int]:
    found: Dict[str, int] = {}
    for i, h in enumerate(header):
        n = _norm(h)
        for field, names in HEADERS.items():
            if field not in found and n in {_norm(x) for x in names}:
                found[field] = i
                break
    return found


def _header_row(rows: List[List[str]]) -> Tuple[int, Dict[str, int]]:
    """The first of the top rows that names at least a column field (and a table field or a data type)."""
    for i, row in enumerate(rows[:15]):
        cols = _columns_of(row)
        if "column" in cols and ("table" in cols or "datatype" in cols):
            return i, cols
    return -1, {}


def _truthy(v: str) -> bool:
    return (v or "").strip().lower() in TRUE


def parse_dictionary(sheets: List[Tuple[str, List[List[str]]]]) -> Dict[str, Any]:
    """Rows of one or more sheets, one row per column: table, column, data type, nullability, keys, definitions and
    foreign keys. A sheet without a table column is read as one table named after the sheet."""
    entities: Dict[str, Dict[str, Any]] = {}
    relationships: List[Dict[str, Any]] = []
    notes: List[str] = []
    used = 0
    for title, rows in sheets:
        at, cols = _header_row(rows)
        if at < 0:
            continue
        used += 1
        skipped = 0
        get = lambda r, f: (r[cols[f]].strip() if f in cols and cols[f] < len(r) and r[cols[f]] is not None else "")  # noqa: E731
        for r in rows[at + 1:]:
            table = physical(get(r, "table") or ("" if "table" in cols else title))
            col = physical(get(r, "column"))
            if not table or not col:
                skipped += bool(any((c or "").strip() for c in r))
                continue
            e = entities.setdefault(table, {"entity_name": table, "attributes": [], "business_keys": []})
            if get(r, "table_description") and not e.get("description"):
                e["description"] = get(r, "table_description")
            if get(r, "schema") and not e.get("schema"):
                e["schema"] = physical(get(r, "schema"))
            if get(r, "table_type") and not e.get("table_type"):
                e["table_type"] = get(r, "table_type").upper()
            if get(r, "subject_area") and not e.get("subject_area"):
                e["subject_area"] = get(r, "subject_area")[:80]
            if any(a["name"] == col for a in e["attributes"]):
                notes.append(f"{table}.{col} is listed twice; the first row is kept")
                continue
            attr: Dict[str, Any] = {"name": col, "datatype": snowflake_type(get(r, "datatype")),
                                    "description": get(r, "description")}
            if get(r, "business_definition"):
                attr["business_definition"] = get(r, "business_definition")
            if "nullable" in cols and get(r, "nullable"):
                raw = get(r, "nullable").strip().lower()
                attr["nullable"] = not (raw in ("n", "no", "false", "0", "not null", "nn") or raw.startswith("not"))
            elif "required" in cols and get(r, "required"):
                attr["nullable"] = not _truthy(get(r, "required"))
            pk = get(r, "pk")
            if pk and (_truthy(pk) or pk.strip().upper().startswith("PK")):
                attr["is_pk"], attr["nullable"] = True, False
            if get(r, "default"):
                attr["default"] = get(r, "default")[:500]
            if get(r, "pii") and _truthy(get(r, "pii")):
                attr["pii"] = True
            e["attributes"].append(attr)
            if attr.get("is_pk") or _truthy(get(r, "business_key")):
                e["business_keys"].append(col)
            ref_table = physical(get(r, "references_table").split("(")[0].split(".")[-1])
            if ref_table:
                inline = re.search(r"\(([^)]+)\)", get(r, "references_table"))
                ref_col = physical(get(r, "references_column") or (inline.group(1) if inline else "") or col)
                relationships.append({"from_entity": table, "from_column": col, "to_entity": ref_table, "to_column": ref_col,
                                      "cardinality": "N:1"})
        if skipped:
            notes.append(f"{title}: {skipped} row(s) without a table or column name skipped")
    if not entities:
        raise ModelFileError("No data dictionary was found: a sheet needs a header row with a column name and a table "
                             "name or data type (for example Table, Column, Data Type, Nullable, Primary Key, Definition).")
    known = {(e["entity_name"], a["name"]) for e in entities.values() for a in e["attributes"]}
    kept = [r for r in relationships if (r["to_entity"], r["to_column"]) in known]
    if len(kept) < len(relationships):
        notes.append(f"{len(relationships) - len(kept)} foreign key(s) point at tables or columns not in the file; not imported")
    if used > 1:
        notes.append(f"{used} sheets read")
    return {"design": {"entities": list(entities.values())}, "relationships": kept, "notes": notes}


def _csv_rows(text: str) -> List[List[str]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect) if any((c or "").strip() for c in row)]


# ---------------------------------------------------------------- any file

def _text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            if encoding == "utf-16" and data[:2] not in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_file(name: str, data: bytes, model_id: str = "") -> Dict[str, Any]:
    """Any supported model file as {spec, format, notes}. Raises ModelFileError with what to export instead."""
    from services.modeling import importers, spec_v2

    lower = (name or "").lower()
    if len(data) > MAX_BYTES:
        raise ModelFileError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if lower.endswith(NATIVE):
        raise ModelFileError("This is a native modeling-tool file, which cannot be read outside the tool. From Erwin, "
                             "use File, Save As, XML file; or Tools, Forward Engineer, Schema Generation to save the DDL. "
                             "A column report exported to Excel works too.")
    if lower.endswith((".xlsx", ".xlsm")):
        from services.agentic.attachments import xlsx_rows

        try:
            sheets = xlsx_rows(data, max_rows=20000)
        except Exception as exc:
            raise ModelFileError(f"The workbook could not be read ({exc}). Save it as .xlsx or CSV.") from None
        out, fmt = parse_dictionary(sheets), "Data dictionary (Excel)"
    elif lower.endswith(".xls"):
        raise ModelFileError("Old Excel (.xls) files are not read. Save the sheet as .xlsx or CSV.")
    else:
        text = _text(data)
        head = text.lstrip()[:2000]
        if lower.endswith(".json") or head.startswith("{"):
            try:
                pack = json.loads(text)
            except ValueError as exc:
                raise ModelFileError(f"The JSON could not be read ({exc}).") from None
            if not isinstance(pack, dict) or not (pack.get("targets") or pack.get("design") or pack.get("entities")):
                raise ModelFileError("The JSON is not a domain_pack.json or a model specification.")
            out = (spec_v2.spec_from_pack(pack, model_id) if pack.get("targets") else pack)
            fmt = "Domain pack" if pack.get("targets") else "Model specification (JSON)"
        elif lower.endswith(".xml") or head.startswith("<"):
            out, fmt = parse_erwin_xml(text), "Erwin model (XML)"
        elif lower.endswith((".csv", ".tsv", ".txt")) and not re.search(r"\bCREATE\s+", text[:20000], re.I):
            out, fmt = parse_dictionary([(re.sub(r"\.\w+$", "", name.rsplit("/", 1)[-1]), _csv_rows(text))]), "Data dictionary (CSV)"
        else:
            try:
                out = importers.parse_ddl(text)
            except AssertionError:
                raise ModelFileError("No CREATE TABLE statement was found. Supported: Erwin XML, DDL (.sql), a data "
                                     "dictionary (.xlsx or .csv) or a domain_pack.json.") from None
            fmt = "DDL"
    notes = list(out.pop("notes", []) or [])
    entities = (out.get("design") or {}).get("entities") or []
    return {"spec": out, "format": fmt, "notes": notes, "entities": len(entities),
            "columns": sum(len(e.get("attributes") or e.get("columns") or []) for e in entities),
            "relationships": len(out.get("relationships") or [])}
