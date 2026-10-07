"""What an Oracle schema holds: tables and views with estimated sizes and comments, and their columns with
constraints. Read from the ALL_* dictionary views, so the user needs no DBA privileges."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from services.source.oracle.connect import cursor
from services.source.oracle.types import map_type, oracle_type_name

MAX_TABLES = 2000


def _dicts(cur) -> List[Dict[str, Any]]:
    names = [d[0].upper() for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def test_connection(conn, schema_owner: str) -> Dict[str, Any]:
    cur = cursor(conn, 100)
    version = f"Oracle {conn.version}"  # from the connection handshake; V$VERSION needs a grant many users lack
    cur.execute("SELECT USER, SYS_CONTEXT('USERENV', 'DB_NAME'), SYS_CONTEXT('USERENV', 'SERVICE_NAME') FROM DUAL")
    user, db_name, service = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM ALL_TABLES WHERE OWNER = :o", o=schema_owner.upper())
    tables = int(cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM ALL_VIEWS WHERE OWNER = :o", o=schema_owner.upper())
    views = int(cur.fetchone()[0])
    return {"version": version, "user": user, "database": db_name, "service": service,
            "schema_owner": schema_owner.upper(), "visible_tables": tables, "visible_views": views,
            "warning": None if tables or views else
            f"{user} sees no tables or views owned by {schema_owner.upper()}; grant SELECT on them first"}


def fetch_catalog(conn, schema_owner: str) -> List[Dict[str, Any]]:
    """Tables and views of the owner with optimizer row estimates, last analysis and comments."""
    cur = cursor(conn, 1000)
    cur.execute(
        """
        SELECT T.TABLE_NAME, 'TABLE' AS OBJECT_TYPE, T.NUM_ROWS, T.LAST_ANALYZED, T.BLOCKS, C.COMMENTS,
               T.PARTITIONED, T.TEMPORARY
          FROM ALL_TABLES T
          LEFT JOIN ALL_TAB_COMMENTS C ON C.OWNER = T.OWNER AND C.TABLE_NAME = T.TABLE_NAME
         WHERE T.OWNER = :o AND T.NESTED = 'NO' AND T.SECONDARY = 'N'
           AND T.TABLE_NAME NOT LIKE 'BIN$%' AND T.TABLE_NAME NOT LIKE 'DR$%'
        UNION ALL
        SELECT V.VIEW_NAME, 'VIEW', NULL, NULL, NULL, C.COMMENTS, 'NO', 'N'
          FROM ALL_VIEWS V
          LEFT JOIN ALL_TAB_COMMENTS C ON C.OWNER = V.OWNER AND C.TABLE_NAME = V.VIEW_NAME
         WHERE V.OWNER = :o
         ORDER BY 1
        """, o=schema_owner.upper())
    out = []
    for r in _dicts(cur)[:MAX_TABLES]:
        out.append({"table": r["TABLE_NAME"], "type": r["OBJECT_TYPE"],
                    "estimated_rows": None if r["NUM_ROWS"] is None else int(r["NUM_ROWS"]),
                    "last_analyzed": str(r["LAST_ANALYZED"])[:19] if r["LAST_ANALYZED"] else None,
                    "comment": r["COMMENTS"], "partitioned": r["PARTITIONED"] == "YES",
                    "temporary": r["TEMPORARY"] == "Y"})
    return out


def inspect_columns(conn, schema_owner: str, tables: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Columns per table (exact spelling, order, type, nullability, comment) with PK/UK/FK membership."""
    owner = schema_owner.upper()
    out: Dict[str, List[Dict[str, Any]]] = {t: [] for t in tables}
    if not tables:
        return out
    cur = cursor(conn, 5000)
    keys: Dict[tuple, List[str]] = {}
    for chunk_start in range(0, len(tables), 500):
        chunk = list(tables[chunk_start:chunk_start + 500])
        binds = {f"t{i}": t for i, t in enumerate(chunk)}
        in_list = ", ".join(f":t{i}" for i in range(len(chunk)))
        cur.execute(
            f"""
            SELECT AC.TABLE_NAME, ACC.COLUMN_NAME, AC.CONSTRAINT_TYPE, AC.CONSTRAINT_NAME,
                   RC.TABLE_NAME AS REF_TABLE
              FROM ALL_CONSTRAINTS AC
              JOIN ALL_CONS_COLUMNS ACC ON ACC.OWNER = AC.OWNER AND ACC.CONSTRAINT_NAME = AC.CONSTRAINT_NAME
              LEFT JOIN ALL_CONSTRAINTS RC ON RC.OWNER = AC.R_OWNER AND RC.CONSTRAINT_NAME = AC.R_CONSTRAINT_NAME
             WHERE AC.OWNER = :o AND AC.CONSTRAINT_TYPE IN ('P', 'U', 'R') AND AC.TABLE_NAME IN ({in_list})
            """, o=owner, **binds)
        for r in _dicts(cur):
            keys.setdefault((r["TABLE_NAME"], r["COLUMN_NAME"]), []).append(
                {"P": "PK", "U": "UNIQUE", "R": f"FK->{r['REF_TABLE']}"}[r["CONSTRAINT_TYPE"]])
        cur.execute(
            f"""
            SELECT C.TABLE_NAME, C.COLUMN_NAME, C.COLUMN_ID, C.DATA_TYPE, C.DATA_PRECISION, C.DATA_SCALE,
                   C.CHAR_LENGTH, C.DATA_LENGTH, C.NULLABLE, C.NUM_DISTINCT, C.NUM_NULLS, CC.COMMENTS
              FROM ALL_TAB_COLUMNS C
              LEFT JOIN ALL_COL_COMMENTS CC ON CC.OWNER = C.OWNER AND CC.TABLE_NAME = C.TABLE_NAME
                                          AND CC.COLUMN_NAME = C.COLUMN_NAME
             WHERE C.OWNER = :o AND C.TABLE_NAME IN ({in_list})
             ORDER BY C.TABLE_NAME, C.COLUMN_ID
            """, o=owner, **binds)
        for r in _dicts(cur):
            precision = None if r["DATA_PRECISION"] is None else int(r["DATA_PRECISION"])
            scale = None if r["DATA_SCALE"] is None else int(r["DATA_SCALE"])
            length = r["CHAR_LENGTH"] or r["DATA_LENGTH"]
            spec = map_type(r["DATA_TYPE"], precision, scale)
            out.setdefault(r["TABLE_NAME"], []).append({
                "column_name": r["COLUMN_NAME"], "ordinal": int(r["COLUMN_ID"]),
                "oracle_type": oracle_type_name(r["DATA_TYPE"], precision, scale, length),
                "data_type": r["DATA_TYPE"], "precision": precision, "scale": scale,
                "snowflake_type": spec.snowflake, "family": spec.family, "lob": spec.lob,
                "nullable": r["NULLABLE"] == "Y", "comment": r["COMMENTS"],
                "constraints": keys.get((r["TABLE_NAME"], r["COLUMN_NAME"]), []),
                "stats_distinct": r["NUM_DISTINCT"], "stats_nulls": r["NUM_NULLS"]})
    return out
