"""Bring an existing model into a domain model draft: Snowflake DDL text, a domain_pack.json, or a live schema read from
INFORMATION_SCHEMA (reverse engineering). Importers only build a specification; the workbench loads it into the
editor as unsaved changes, so it is saved, reviewed and approved like any edit and never touches an approved version.

Merging keeps the ids of entities and columns that already exist by name, so the review shows what changed instead
of a drop and an add.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from services.modeling import spec_v2

_CREATE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:LOCAL|GLOBAL)\s+)?(?P<kind>TRANSIENT\s+|TEMPORARY\s+|TEMP\s+|VOLATILE\s+|ICEBERG\s+|DYNAMIC\s+)?"
    r"TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[\w$.\"]+)\s*\(", re.IGNORECASE)


def _ident(raw: str) -> str:
    return raw.strip().strip('"').upper()


def _split(body: str) -> List[str]:
    """Split on commas at depth 0, outside quotes."""
    parts, depth, cur, quote = [], 0, [], None
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def _body(text: str, start: int) -> Tuple[str, int]:
    depth, quote, i = 1, None, start
    while i < len(text) and depth:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    return text[start:i - 1], i


def _cols(raw: str) -> List[str]:
    return [_ident(c) for c in raw.split(",") if c.strip()]


_TYPE = re.compile(r"^([A-Za-z_]+(?:\s+PRECISION)?(?:\s*\([\d\s,]+\))?)", re.IGNORECASE)


def parse_ddl(text: str) -> Dict[str, Any]:
    """CREATE TABLE statements as a raw v2 specification ({design: {entities}, relationships}). Columns, NOT NULL,
    DEFAULT, COMMENT, inline and table-level PRIMARY KEY, UNIQUE and FOREIGN KEY, CLUSTER BY, TRANSIENT and the
    table COMMENT are read; anything else is ignored."""
    text = re.sub(r"--[^\n]*", "", text or "")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    entities, rels = [], []
    pos = 0
    while True:
        m = _CREATE.search(text, pos)
        if not m:
            break
        body, end = _body(text, m.end())
        tail = text[end:end + 2000].split(";", 1)[0]
        name = m.group("name").split(".")
        parts = [_ident(p) for p in name]
        table = parts[-1]
        entity: Dict[str, Any] = {"entity_name": table, "attributes": [], "business_keys": [], "unique_constraints": [],
                                  "physical": {"transient": (m.group("kind") or "").strip().upper() == "TRANSIENT"}}
        if len(parts) == 3:
            entity["database"], entity["schema"] = parts[0], parts[1]
        elif len(parts) == 2:
            entity["schema"] = parts[0]
        pk: List[str] = []
        for item in _split(body):
            up = item.upper()
            cons = re.match(r"^(?:CONSTRAINT\s+[\w$\"]+\s+)?(PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY)\s*\(([^)]*)\)(.*)$", item, re.I | re.S)
            if cons:
                kind, cols, rest = cons.group(1).upper(), _cols(cons.group(2)), cons.group(3)
                if kind.startswith("PRIMARY"):
                    pk = cols
                elif kind == "UNIQUE":
                    entity["unique_constraints"].append(cols)
                else:
                    ref = re.search(r"REFERENCES\s+([\w$.\"]+)\s*\(([^)]*)\)", rest, re.I)
                    if ref:
                        to_cols = _cols(ref.group(2))
                        for fc, tc in zip(cols, to_cols):
                            rels.append({"from_entity": table, "from_column": fc, "to_entity": _ident(ref.group(1).split(".")[-1]),
                                         "to_column": tc, "enforce": "RELY" if re.search(r"\bRELY\b", rest, re.I) else "NONE"})
                continue
            col = re.match(r'^("[^"]+"|[\w$]+)\s+(.*)$', item, re.S)
            if not col or up.startswith(("CONSTRAINT", "CHECK ", "INDEX ", "KEY ")):
                continue
            cname, rest = _ident(col.group(1)), col.group(2).strip()
            t = _TYPE.match(rest)
            datatype = re.sub(r"\s+", "", t.group(1)).upper() if t else "VARCHAR"
            attr: Dict[str, Any] = {"name": cname, "datatype": datatype, "nullable": not re.search(r"\bNOT\s+NULL\b", rest, re.I)}
            d = re.search(r"\bDEFAULT\s+('(?:[^']|'')*'|[^\s,]+(?:\([^)]*\))?)", rest, re.I)
            if d:
                attr["default"] = d.group(1)
            c = re.search(r"\bCOMMENT\s+'((?:[^']|'')*)'", rest, re.I)
            if c:
                attr["description"] = c.group(1).replace("''", "'")
            if re.search(r"\bPRIMARY\s+KEY\b", rest, re.I):
                pk.append(cname)
            if re.search(r"\bUNIQUE\b", rest, re.I):
                attr["is_unique"] = True
            ref = re.search(r"\bREFERENCES\s+([\w$.\"]+)\s*\(([^)]*)\)", rest, re.I)
            if ref:
                rels.append({"from_entity": table, "from_column": cname, "to_entity": _ident(ref.group(1).split(".")[-1]),
                             "to_column": _cols(ref.group(2))[0] if _cols(ref.group(2)) else cname})
            entity["attributes"].append(attr)
        for a in entity["attributes"]:
            if a["name"] in pk:
                a["is_pk"], a["nullable"] = True, False
        entity["business_keys"] = list(pk)
        cluster = re.search(r"\bCLUSTER\s+BY\s*\(([^)]*)\)", tail, re.I)
        if cluster:
            entity["physical"]["cluster_by"] = _cols(cluster.group(1))
        comment = re.search(r"\bCOMMENT\s*=\s*'((?:[^']|'')*)'", tail, re.I)
        if comment:
            entity["description"] = comment.group(1).replace("''", "'")
        entities.append(entity)
        pos = end
    assert entities, "no CREATE TABLE statement found"
    return {"design": {"entities": entities}, "relationships": rels}


def from_schema(session, database: str, schema: str, tables: Optional[List[str]] = None) -> Dict[str, Any]:
    """A live Snowflake schema as a raw v2 specification: columns, types, nullability, defaults and comments from
    INFORMATION_SCHEMA, primary and foreign keys from SHOW PRIMARY KEYS / SHOW IMPORTED KEYS (when readable)."""
    from services.common.sql import rows

    db, sch = _ident(database), _ident(schema)
    assert re.match(r"^[A-Z_][A-Z0-9_$]*$", db) and re.match(r"^[A-Z_][A-Z0-9_$]*$", sch), "database and schema are plain names"
    wanted = [_ident(t) for t in tables or [] if str(t).strip()]
    filt = f" AND TABLE_NAME IN ({', '.join('?' for _ in wanted)})" if wanted else ""
    cols = rows(session, f"""SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION,
                                    NUMERIC_SCALE, IS_NULLABLE, COLUMN_DEFAULT, COMMENT
                               FROM "{db}".INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ?{filt}
                              ORDER BY TABLE_NAME, ORDINAL_POSITION""", [sch, *wanted])
    assert cols, f"no tables found in {db}.{sch}"
    comments = {}
    try:
        for t in rows(session, f'SELECT TABLE_NAME, COMMENT FROM "{db}".INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?', [sch]):
            comments[t["TABLE_NAME"]] = t["COMMENT"]
    except Exception:
        pass
    by_table: Dict[str, Dict[str, Any]] = {}
    for c in cols:
        t = by_table.setdefault(c["TABLE_NAME"], {"entity_name": c["TABLE_NAME"], "description": comments.get(c["TABLE_NAME"]) or "",
                                                 "attributes": []})
        dtype = str(c["DATA_TYPE"] or "VARCHAR").upper()
        if dtype in ("TEXT", "VARCHAR") and c.get("CHARACTER_MAXIMUM_LENGTH") and int(c["CHARACTER_MAXIMUM_LENGTH"]) < 16777216:
            dtype = f"VARCHAR({int(c['CHARACTER_MAXIMUM_LENGTH'])})"
        elif dtype == "TEXT":
            dtype = "VARCHAR"
        elif dtype == "NUMBER" and c.get("NUMERIC_PRECISION") is not None:
            dtype = f"NUMBER({int(c['NUMERIC_PRECISION'])},{int(c.get('NUMERIC_SCALE') or 0)})"
        t["attributes"].append({"name": c["COLUMN_NAME"], "datatype": dtype, "nullable": str(c["IS_NULLABLE"]).upper() == "YES",
                                "default": c.get("COLUMN_DEFAULT"), "description": c.get("COMMENT") or ""})
    rels: List[Dict[str, Any]] = []
    try:
        for k in rows(session, f'SHOW PRIMARY KEYS IN SCHEMA "{db}"."{sch}"'):
            low = {str(a).lower(): v for a, v in k.items()}
            t = by_table.get(low.get("table_name"))
            if t:
                for a in t["attributes"]:
                    if a["name"] == low.get("column_name"):
                        a["is_pk"], a["nullable"] = True, False
                t.setdefault("business_keys", []).append(low.get("column_name"))
    except Exception:
        pass
    try:
        for k in rows(session, f'SHOW IMPORTED KEYS IN SCHEMA "{db}"."{sch}"'):
            low = {str(a).lower(): v for a, v in k.items()}
            if low.get("fk_table_name") in by_table:
                rels.append({"from_entity": low["fk_table_name"], "from_column": low.get("fk_column_name"),
                             "to_entity": low.get("pk_table_name"), "to_column": low.get("pk_column_name")})
    except Exception:
        pass
    return {"target_database": db, "target_schema": sch, "design": {"entities": list(by_table.values())}, "relationships": rels}


def merge(base: Dict[str, Any], incoming_raw: Dict[str, Any], model_id: str, replace: bool = False) -> Dict[str, Any]:
    """The draft with the imported entities: a same-named entity is replaced but keeps its id and the ids of its
    same-named columns; new entities are added; with `replace`, entities the import does not have are retired
    (never deleted, so the review shows them)."""
    out = copy.deepcopy(base)
    incoming = spec_v2.normalize_v2({**incoming_raw, "domain": base.get("domain")}, model_id)
    by_name = {e["entity_name"]: e for e in out["design"]["entities"]}
    seen = set()
    for e in incoming["design"]["entities"]:
        seen.add(e["entity_name"])
        old = by_name.get(e["entity_name"])
        if old:
            old_cols = {a["name"]: a for a in old["attributes"]}
            for a in e["attributes"]:
                if a["name"] in old_cols:
                    a["id"] = old_cols[a["name"]]["id"]
                    for keep in ("business_definition", "semantic_type", "pii", "accepted_values", "tags", "description"):
                        empty = not (a.get(keep) or {}).get("flag") if keep == "pii" else not a.get(keep)
                        if empty and old_cols[a["name"]].get(keep):
                            a[keep] = old_cols[a["name"]][keep]
            e["id"] = old["id"]
            for keep in ("table_type", "scd_type", "grain", "subject_area", "model_spec", "description"):
                if (not e.get(keep) or (keep in ("table_type", "scd_type") and e[keep] in ("TABLE", "1"))) and old.get(keep):
                    e[keep] = old[keep]
            e["kind"] = e["table_type"] if e["table_type"] in spec_v2.V1_KINDS else "TABLE"
            if old.get("database") and not e.get("database"):
                e["database"] = old["database"]
            if old.get("schema") and not e.get("schema"):
                e["schema"] = old["schema"]
            out["design"]["entities"][out["design"]["entities"].index(old)] = e
        else:
            out["design"]["entities"].append(e)
    if replace:
        for e in out["design"]["entities"]:
            if e["entity_name"] not in seen:
                e["status"] = "RETIRED"
    rel_keys = {(r["from_entity"], r["from_column"], r["to_entity"], r["to_column"]) for r in out.get("relationships") or []}
    for r in incoming.get("relationships") or []:
        if (r["from_entity"], r["from_column"], r["to_entity"], r["to_column"]) not in rel_keys:
            out.setdefault("relationships", []).append(r)
    phys = out["physical_defaults"]
    for k in ("database", "schema"):
        if not phys.get(k) and incoming["physical_defaults"].get(k):
            phys[k] = incoming["physical_defaults"][k]
    return spec_v2.normalize_v2(out, model_id)
