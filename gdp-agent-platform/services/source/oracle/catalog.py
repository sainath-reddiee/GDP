"""What an Oracle schema holds and whether the platform can read it.

  diagnose()       a step-by-step connection check (network, login, account, server, access, statistics, types,
                   latency) that reports every step instead of stopping at the first exception
  fetch_catalog()  tables, views and materialized views with row and size estimates, keys, comments and a
                   suggested incremental (watermark) column
  inspect_columns() columns with types, keys, comments and how each is read (or why it is skipped)

Everything comes from the ALL_* / USER_* dictionary views and NLS_DATABASE_PARAMETERS, so no DBA privileges are
needed."""

from __future__ import annotations

import re
import socket
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from services.source.oracle.connect import OracleConfig, cursor
from services.source.oracle.errors import explain
from services.source.oracle.types import map_type, oracle_type_name, source_expr

MAX_TABLES = 2000
STALE_DAYS = 30
UPDATED_NAME = re.compile(r"(UPDATE|UPD_|MODIF|CHANGE|LAST_?MOD|LAST_?UPD)", re.IGNORECASE)
CREATED_NAME = re.compile(r"(CREAT|INSERT|LOAD)", re.IGNORECASE)
TIMESTAMP_NAME = re.compile(r"(_TS$|_AT$|TIMESTAMP|_DT$|_DATE$)", re.IGNORECASE)


def _dicts(cur) -> List[Dict[str, Any]]:
    names = [d[0].upper() for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _check(checks: List[Dict[str, Any]], cid: str, label: str, status: str, detail: str = "",
           fix: Optional[str] = None, ms: Optional[int] = None, **extra) -> Dict[str, Any]:
    item = {"id": cid, "label": label, "status": status, "detail": detail, "fix": fix, "ms": ms, **extra}
    checks.append(item)
    return item


def _failed(checks: List[Dict[str, Any]], cid: str, label: str, exc: BaseException, started: float) -> None:
    why = explain(exc)
    _check(checks, cid, label, "fail", why["title"], why["fix"], int((time.time() - started) * 1000),
           code=why["code"], error=why["detail"])


def diagnose(cfg: OracleConfig, password: str, connect: Callable[[], Any],
             probe: Callable[[str, int, float], None] = None) -> Dict[str, Any]:
    """Run every check it can; later checks are skipped (not failed) when an earlier one blocks them."""
    checks: List[Dict[str, Any]] = []
    server: Dict[str, Any] = {}
    probe = probe or (lambda host, port, timeout: socket.create_connection((host, port), timeout=timeout).close())

    _check(checks, "config", "Connection details", "ok",
           f"{cfg.protocol.upper()} {cfg.host}:{cfg.port} / {cfg.service_name or 'SID ' + str(cfg.sid)} as {cfg.user}")

    started = time.time()
    try:
        probe(cfg.host, cfg.port, 10.0)
        _check(checks, "network", "Network reaches the listener", "ok", f"{cfg.host}:{cfg.port} accepted a connection",
               ms=int((time.time() - started) * 1000))
    except Exception as exc:
        _failed(checks, "network", "Network reaches the listener", exc, started)
        return _summary(checks, server, ("login", "account", "server", "access", "read", "stats", "types", "latency"))

    started = time.time()
    if not password:
        _check(checks, "login", "Login", "fail", "No password is available to this runtime",
               "Choose or create the Snowflake secret, or set the environment variable on the API host.")
        return _summary(checks, server, ("account", "server", "access", "read", "stats", "types", "latency"))
    try:
        conn = connect()
    except Exception as exc:
        _failed(checks, "login", "Login", exc, started)
        return _summary(checks, server, ("account", "server", "access", "read", "stats", "types", "latency"))
    try:
        _check(checks, "login", "Login", "ok", f"Signed in as {cfg.user}", ms=int((time.time() - started) * 1000))
        cur = cursor(conn, 200)
        owner = cfg.schema_owner.upper()

        # account: status and password expiry (USER_USERS is always readable by the user itself)
        try:
            cur.execute("SELECT ACCOUNT_STATUS, EXPIRY_DATE, SYSDATE FROM USER_USERS")
            status, expiry, now = cur.fetchone()
            days = (expiry - now).days if expiry else None
            server["account_status"], server["password_expires_in_days"] = status, days
            if "GRACE" in str(status) or (days is not None and days <= 14):
                _check(checks, "account", "Account and password", "warn",
                       f"Password expires in {max(days or 0, 0)} day(s)" if days is not None else str(status),
                       "Change the Oracle password before it expires, then use Update password here.")
            else:
                _check(checks, "account", "Account and password", "ok",
                       f"{status}" + (f", password valid for {days} more days" if days is not None else
                                      ", password does not expire"))
        except Exception as exc:
            _check(checks, "account", "Account and password", "skip", explain(exc)["title"])

        # server: version, names, character sets, time zone
        try:
            server["version"] = conn.version
            cur.execute("SELECT SYS_CONTEXT('USERENV','DB_NAME'), SYS_CONTEXT('USERENV','SERVICE_NAME'), "
                        "SYS_CONTEXT('USERENV','CON_NAME'), DBTIMEZONE FROM DUAL")
            server["database"], server["service"], server["container"], server["db_timezone"] = cur.fetchone()
            cur.execute("SELECT PARAMETER, VALUE FROM NLS_DATABASE_PARAMETERS "
                        "WHERE PARAMETER IN ('NLS_CHARACTERSET', 'NLS_NCHAR_CHARACTERSET')")
            nls = dict(cur.fetchall())
            server["charset"], server["nchar_charset"] = nls.get("NLS_CHARACTERSET"), nls.get("NLS_NCHAR_CHARACTERSET")
            major = int(str(conn.version).split(".")[0])
            detail = (f"Oracle {conn.version} · {server['database']} ({server['service']}) · "
                      f"{server['charset']} · DB time zone {server['db_timezone']}")
            if server["charset"] not in ("AL32UTF8", "UTF8"):
                _check(checks, "server", "Database", "warn", detail,
                       f"{server['charset']} is not Unicode. Text is converted to UTF-8 on read; characters the "
                       "database could not store were already replaced in Oracle.")
            elif major < 12:
                _check(checks, "server", "Database", "warn", detail, "Oracle 12.1 or later is recommended.")
            else:
                _check(checks, "server", "Database", "ok", detail)
        except Exception as exc:
            _check(checks, "server", "Database", "warn", explain(exc)["title"])

        # access: does the owner exist and what can the user see in it
        started = time.time()
        try:
            cur.execute("SELECT COUNT(*) FROM ALL_USERS WHERE USERNAME = :o", o=owner)
            owner_exists = int(cur.fetchone()[0]) > 0
            cur.execute("SELECT (SELECT COUNT(*) FROM ALL_TABLES WHERE OWNER = :o), "
                        "(SELECT COUNT(*) FROM ALL_VIEWS WHERE OWNER = :o) FROM DUAL", o=owner)
            tables, views = (int(x) for x in cur.fetchone())
            server["visible_tables"], server["visible_views"] = tables, views
            if not owner_exists:
                _check(checks, "access", f"Objects in {owner}", "fail", f"There is no schema named {owner}",
                       "Check the schema owner (Oracle stores unquoted names in upper case).")
            elif not tables and not views:
                _check(checks, "access", f"Objects in {owner}", "fail",
                       f"{cfg.user} sees no tables or views in {owner}",
                       f"Ask the DBA to grant SELECT on {owner}'s tables to {cfg.user} (or READ ANY TABLE).")
            else:
                _check(checks, "access", f"Objects in {owner}", "ok", f"{tables} tables and {views} views visible",
                       ms=int((time.time() - started) * 1000))
        except Exception as exc:
            _failed(checks, "access", f"Objects in {owner}", exc, started)
            tables = views = 0

        # read: can a row actually be selected (dictionary visibility is not always SELECT)
        try:
            cur.execute("SELECT TABLE_NAME FROM ALL_TABLES WHERE OWNER = :o AND TEMPORARY = 'N' "
                        "AND ROWNUM = 1", o=owner)
            first = cur.fetchone()
            if first:
                from services.source.oracle.profile import table_ref

                cur.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM {table_ref(owner, first[0])} WHERE ROWNUM <= 1)")
                cur.fetchone()
                _check(checks, "read", "Read data", "ok", f"Read a row from {owner}.{first[0]}")
            else:
                _check(checks, "read", "Read data", "skip", "No tables to read")
        except Exception as exc:
            _failed(checks, "read", "Read data", exc, time.time())

        # statistics: row estimates come from the optimizer
        try:
            cur.execute(f"""SELECT COUNT(*), SUM(CASE WHEN NUM_ROWS IS NULL OR LAST_ANALYZED < SYSDATE - {STALE_DAYS}
                                                  THEN 1 ELSE 0 END)
                              FROM ALL_TABLES WHERE OWNER = :o AND TEMPORARY = 'N'""", o=owner)
            total, stale = (int(x or 0) for x in cur.fetchone())
            if stale:
                _check(checks, "stats", "Optimizer statistics", "warn",
                       f"{stale} of {total} tables were never analyzed or not in the last {STALE_DAYS} days",
                       "Row estimates in the table list may be off. Profiling and loads count real rows, so results "
                       "are unaffected; a DBA can refresh with DBMS_STATS.GATHER_SCHEMA_STATS.")
            else:
                _check(checks, "stats", "Optimizer statistics", "ok", f"All {total} tables have recent statistics")
        except Exception as exc:
            _check(checks, "stats", "Optimizer statistics", "skip", explain(exc)["title"])

        # types: columns that cannot be extracted
        try:
            cur.execute("SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, DATA_TYPE_OWNER FROM ALL_TAB_COLUMNS WHERE OWNER = :o",
                        o=owner)
            skipped = [(t, c, why) for t, c, d, o_ in cur.fetchall() for _, why in [source_expr(c, d, o_)] if why]
            server["skipped_columns"] = len(skipped)
            if skipped:
                sample = ", ".join(f"{t}.{c}" for t, c, _ in skipped[:4])
                _check(checks, "types", "Column types", "warn",
                       f"{len(skipped)} column(s) cannot be extracted ({sample}{'…' if len(skipped) > 4 else ''})",
                       "They are left out of loads with the reason shown per table; everything else lands.")
            else:
                _check(checks, "types", "Column types", "ok", "Every column type can be extracted")
        except Exception as exc:
            _check(checks, "types", "Column types", "skip", explain(exc)["title"])

        # latency: round trips decide extract speed far more than bandwidth
        try:
            timings = []
            for _ in range(3):
                t0 = time.perf_counter()
                cur.execute("SELECT 1 FROM DUAL")
                cur.fetchone()
                timings.append((time.perf_counter() - t0) * 1000)
            avg = round(sum(timings) / len(timings), 1)
            server["latency_ms"] = avg
            _check(checks, "latency", "Round-trip latency", "warn" if avg > 150 else "ok", f"{avg} ms per round trip",
                   "High latency slows extracts; run nearer to the database (the other runtime) if possible."
                   if avg > 150 else None, ms=int(avg))
        except Exception as exc:
            _check(checks, "latency", "Round-trip latency", "skip", explain(exc)["title"])
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return _summary(checks, server, ())


def _summary(checks: List[Dict[str, Any]], server: Dict[str, Any], skipped: Sequence[str]) -> Dict[str, Any]:
    labels = {"login": "Login", "account": "Account and password", "server": "Database", "access": "Schema objects",
              "read": "Read data", "stats": "Optimizer statistics", "types": "Column types",
              "latency": "Round-trip latency"}
    for cid in skipped:
        _check(checks, cid, labels.get(cid, cid), "skip", "Not checked: an earlier step failed")
    status = "fail" if any(c["status"] == "fail" for c in checks) else \
        "warn" if any(c["status"] == "warn" for c in checks) else "ok"
    first_fail = next((c for c in checks if c["status"] == "fail"), None)
    return {"ok": status != "fail", "status": status, "checks": checks, "server": server,
            "headline": first_fail["detail"] if first_fail else
            f"Connected to Oracle {server.get('version', '')}".strip()}


def _watermark_candidate(columns: List[Dict[str, Any]], pk: List[str]) -> Optional[Dict[str, str]]:
    """The column an incremental load should follow: a last-updated date/timestamp, else a created one, else a
    single numeric primary key."""
    dated = [c for c in columns if map_type(c["DATA_TYPE"]).family == "TIMESTAMP"]
    tiers = ((lambda n: UPDATED_NAME.search(n), "last-updated timestamp"),
             (lambda n: TIMESTAMP_NAME.search(n) and not CREATED_NAME.search(n), "change timestamp"),
             (lambda n: CREATED_NAME.search(n), "created timestamp (new rows only)"))
    for matches, why in tiers:
        hit = next((c for c in dated if matches(c["COLUMN_NAME"])), None)
        if hit:
            return {"column": hit["COLUMN_NAME"], "reason": why}
    if len(pk) == 1:
        col = next((c for c in columns if c["COLUMN_NAME"] == pk[0]), None)
        if col and map_type(col["DATA_TYPE"]).family == "NUMBER":
            return {"column": pk[0], "reason": "increasing numeric key (new rows only)"}
    return None


def _stale(analyzed: Any) -> bool:
    if analyzed is None:
        return True
    if hasattr(analyzed, "timestamp"):
        return time.time() - analyzed.timestamp() > STALE_DAYS * 86400
    return False


def fetch_catalog(conn, schema_owner: str) -> List[Dict[str, Any]]:
    """Tables, views and materialized views of the owner with row and size estimates, last analysis, primary key,
    column counts, columns that cannot be read, comments and a suggested watermark column."""
    owner = schema_owner.upper()
    cur = cursor(conn, 2000)
    cur.execute(
        """
        SELECT T.TABLE_NAME, CASE WHEN M.MVIEW_NAME IS NOT NULL THEN 'MVIEW'
                                  WHEN X.TABLE_NAME IS NOT NULL THEN 'EXTERNAL' ELSE 'TABLE' END AS OBJECT_TYPE,
               T.NUM_ROWS, T.AVG_ROW_LEN, T.LAST_ANALYZED, C.COMMENTS, T.PARTITIONED, T.TEMPORARY, T.IOT_TYPE
          FROM ALL_TABLES T
          LEFT JOIN ALL_TAB_COMMENTS C ON C.OWNER = T.OWNER AND C.TABLE_NAME = T.TABLE_NAME
          LEFT JOIN ALL_MVIEWS M ON M.OWNER = T.OWNER AND M.MVIEW_NAME = T.TABLE_NAME
          LEFT JOIN ALL_EXTERNAL_TABLES X ON X.OWNER = T.OWNER AND X.TABLE_NAME = T.TABLE_NAME
         WHERE T.OWNER = :o AND T.NESTED = 'NO' AND T.SECONDARY = 'N'
           AND T.TABLE_NAME NOT LIKE 'BIN$%' AND T.TABLE_NAME NOT LIKE 'DR$%' AND T.TABLE_NAME NOT LIKE 'MLOG$%'
           AND T.TABLE_NAME NOT LIKE 'RUPD$%' AND T.TABLE_NAME NOT LIKE 'SYS_IOT_OVER%'
        UNION ALL
        SELECT V.VIEW_NAME, 'VIEW', NULL, NULL, NULL, C.COMMENTS, 'NO', 'N', NULL
          FROM ALL_VIEWS V
          LEFT JOIN ALL_TAB_COMMENTS C ON C.OWNER = V.OWNER AND C.TABLE_NAME = V.VIEW_NAME
         WHERE V.OWNER = :o
         ORDER BY 1
        """, o=owner)
    objects = _dicts(cur)[:MAX_TABLES]

    cur.execute("""SELECT AC.TABLE_NAME, ACC.COLUMN_NAME FROM ALL_CONSTRAINTS AC
                     JOIN ALL_CONS_COLUMNS ACC ON ACC.OWNER = AC.OWNER AND ACC.CONSTRAINT_NAME = AC.CONSTRAINT_NAME
                    WHERE AC.OWNER = :o AND AC.CONSTRAINT_TYPE = 'P' ORDER BY AC.TABLE_NAME, ACC.POSITION""", o=owner)
    pks: Dict[str, List[str]] = {}
    for table, column in cur.fetchall():
        pks.setdefault(table, []).append(column)

    cur.execute("""SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, DATA_TYPE_OWNER FROM ALL_TAB_COLUMNS
                    WHERE OWNER = :o ORDER BY TABLE_NAME, COLUMN_ID""", o=owner)
    columns: Dict[str, List[Dict[str, Any]]] = {}
    for r in _dicts(cur):
        columns.setdefault(r["TABLE_NAME"], []).append(r)

    out = []
    for r in objects:
        name = r["TABLE_NAME"]
        cols = columns.get(name, [])
        skipped = [{"column": c["COLUMN_NAME"], "reason": why} for c in cols
                   for _, why in [source_expr(c["COLUMN_NAME"], c["DATA_TYPE"], c["DATA_TYPE_OWNER"])] if why]
        rows_ = None if r["NUM_ROWS"] is None else int(r["NUM_ROWS"])
        analyzed = r["LAST_ANALYZED"]
        out.append({
            "table": name, "type": r["OBJECT_TYPE"], "estimated_rows": rows_,
            "estimated_bytes": int(rows_ * int(r["AVG_ROW_LEN"])) if rows_ is not None and r["AVG_ROW_LEN"] else None,
            "last_analyzed": str(analyzed)[:19] if analyzed else None,
            "stale_stats": r["OBJECT_TYPE"] in ("TABLE", "MVIEW") and _stale(analyzed),
            "comment": r["COMMENTS"], "partitioned": r["PARTITIONED"] == "YES", "temporary": r["TEMPORARY"] == "Y",
            "iot": bool(r["IOT_TYPE"]), "primary_key": pks.get(name, []), "column_count": len(cols),
            "skipped_columns": skipped, "watermark": _watermark_candidate(cols, pks.get(name, [])),
            "extractable": r["TEMPORARY"] != "Y" and len(cols) > len(skipped),
            "not_extractable_reason": "global temporary table: its rows exist only inside other sessions"
            if r["TEMPORARY"] == "Y" else ("no readable columns" if cols and len(cols) == len(skipped) else None),
        })
    return out


def inspect_columns(conn, schema_owner: str, tables: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Columns per table (exact spelling, order, type, nullability, comment) with PK/UK/FK membership and how each is
    read: `expr` is the SELECT expression, `skip_reason` says why a column is left out."""
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
            SELECT C.TABLE_NAME, C.COLUMN_NAME, C.COLUMN_ID, C.DATA_TYPE, C.DATA_TYPE_OWNER, C.DATA_PRECISION,
                   C.DATA_SCALE, C.CHAR_LENGTH, C.DATA_LENGTH, C.NULLABLE, C.NUM_DISTINCT, C.NUM_NULLS, CC.COMMENTS
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
            expr, skip = source_expr(r["COLUMN_NAME"], r["DATA_TYPE"], r["DATA_TYPE_OWNER"])
            out.setdefault(r["TABLE_NAME"], []).append({
                "column_name": r["COLUMN_NAME"], "ordinal": int(r["COLUMN_ID"]),
                "oracle_type": oracle_type_name(r["DATA_TYPE"], precision, scale, length),
                "data_type": r["DATA_TYPE"], "data_type_owner": r["DATA_TYPE_OWNER"], "precision": precision,
                "scale": scale, "snowflake_type": None if skip else spec.snowflake, "family": spec.family,
                "lob": spec.lob, "free_number": spec.free_number, "expr": expr, "skip_reason": skip,
                "nullable": r["NULLABLE"] == "Y", "comment": r["COMMENTS"],
                "constraints": keys.get((r["TABLE_NAME"], r["COLUMN_NAME"]), []),
                "stats_distinct": r["NUM_DISTINCT"], "stats_nulls": r["NUM_NULLS"]})
    return out


def preview(conn, schema_owner: str, table: str, columns: Sequence[Dict[str, Any]], limit: int = 20) -> Dict[str, Any]:
    """The first rows of a table as display text (long values clipped), read with the same expressions as loads."""
    from services.source.oracle.profile import table_ref

    readable = [c for c in columns if not c.get("skip_reason")]
    assert readable, f"{table} has no readable columns"
    cur = cursor(conn, max(1, min(int(limit), 100)))
    cur.execute(f"SELECT {', '.join(c['expr'] for c in readable)} FROM {table_ref(schema_owner, table)} "
                f"WHERE ROWNUM <= {max(1, min(int(limit), 100))}")

    def show(v: Any) -> Any:
        if v is None:
            return None
        if hasattr(v, "read"):
            v = v.read()
        if isinstance(v, (bytes, bytearray)):
            return f"<{len(v)} bytes>"
        text = v.isoformat(sep=" ") if hasattr(v, "isoformat") else str(v)
        return text if len(text) <= 200 else text[:200] + "…"

    rows_ = [[show(v) for v in row] for row in cur.fetchall()]
    return {"table": table, "columns": [c["column_name"] for c in readable], "rows": rows_,
            "skipped": [{"column": c["column_name"], "reason": c["skip_reason"]} for c in columns if c.get("skip_reason")]}
