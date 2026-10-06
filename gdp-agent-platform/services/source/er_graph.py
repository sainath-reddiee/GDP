"""Pure helpers that infer source joins and source->target column mappings for the onboarding ER preview."""

from __future__ import annotations

import re

_KEY_SUFFIX = re.compile(r"(_ID|_KEY|_CODE|_NO|_NUM|ID)$")
_GENERIC = {"ID", "KEY", "NAME", "CODE", "STATUS", "TYPE", "CREATED_AT", "UPDATED_AT", "LOAD_TS", "DESCRIPTION"}
_PLURAL = re.compile(r"(IES|ES|S)$")


def _stem(table: str) -> str:
    """CRM_CUSTOMERS -> CUSTOMER; TBL_ORDER -> ORDER."""
    name = table.upper().split("_")[-1]
    if name.endswith("IES"):
        return name[:-3] + "Y"
    return _PLURAL.sub("", name) if len(name) > 3 else name


def _family(data_type: str) -> str:
    kind = (data_type or "").upper()
    if kind in {"NUMBER", "FIXED", "INTEGER", "INT", "BIGINT", "DECIMAL", "FLOAT", "REAL"}:
        return "num"
    if kind in {"TEXT", "VARCHAR", "STRING", "CHAR"}:
        return "text"
    if kind.startswith("TIMESTAMP") or kind == "DATE":
        return "time"
    return kind.lower()


def key_columns(table: str, columns: list[dict]) -> set[str]:
    """Columns that look like this table's own primary key."""
    stem = _stem(table)
    names = [str(c.get("column_name") or "").upper() for c in columns]
    keys = {n for n in names if n in {"ID", f"{stem}_ID", f"{stem}ID", f"{stem}_KEY"}}
    if not keys and names and _KEY_SUFFIX.search(names[0]):
        keys.add(names[0])
    return keys


def infer_joins(tables: dict[str, list[dict]]) -> list[dict]:
    """Infer joins between source tables from shared key-like column names with compatible types."""
    pks = {t: key_columns(t, cols) for t, cols in tables.items()}
    joins: list[dict] = []
    names = sorted(tables)
    for i, left in enumerate(names):
        lcols = {str(c.get("column_name") or "").upper(): _family(c.get("data_type") or "") for c in tables[left]}
        for right in names[i + 1:]:
            rcols = {str(c.get("column_name") or "").upper(): _family(c.get("data_type") or "") for c in tables[right]}
            keys = []
            for col, fam in lcols.items():
                if col in _GENERIC and col != "ID":
                    continue
                if col == "ID" or col not in rcols or rcols[col] != fam:
                    continue
                if _KEY_SUFFIX.search(col):
                    keys.append(col)
            left_pk = any(k in pks[left] for k in keys)
            right_pk = any(k in pks[right] for k in keys)
            # ORDERS.CUSTOMER_ID -> CUSTOMER.ID
            fk = f"{_stem(right)}_ID"
            if fk in lcols and "ID" in rcols and fk not in keys:
                keys.append(f"{fk}=ID")
                right_pk = True
            fk = f"{_stem(left)}_ID"
            if fk in rcols and "ID" in lcols and fk not in keys:
                keys.append(f"ID={fk}")
                left_pk = True
            if not keys:
                continue
            if left_pk and right_pk:
                cardinality = "1:1"
            elif left_pk:
                cardinality = "1:N"
            elif right_pk:
                cardinality = "N:1"
            else:
                cardinality = "N:N"
            confidence = min(1.0, 0.5 + 0.2 * len(keys) + (0.2 if left_pk or right_pk else 0))
            joins.append({
                "left": left, "right": right, "keys": keys,
                "cardinality": cardinality, "confidence": round(confidence, 2),
            })
    return joins


def mapping_edges(tables: dict[str, list[dict]], targets: list[dict]) -> list[dict]:
    """Source table -> target model edges, weighted by overlapping column names."""
    edges: list[dict] = []
    for table, cols in tables.items():
        src = {str(c.get("column_name") or "").upper() for c in cols}
        for target in targets:
            tgt = {str(c).upper() for c in (target.get("columns") or [])}
            overlap = sorted(src & tgt)
            if not overlap and not target.get("selected"):
                continue
            edges.append({
                "from": table,
                "to": target.get("target_table"),
                "kind": "planned" if target.get("selected") else "candidate",
                "weight": len(overlap),
                "columns": overlap[:25],
            })
    return edges


def isolated_tables(tables: dict[str, list[dict]], joins: list[dict]) -> list[str]:
    linked = {j["left"] for j in joins} | {j["right"] for j in joins}
    return sorted(t for t in tables if t not in linked) if len(tables) > 1 else []
