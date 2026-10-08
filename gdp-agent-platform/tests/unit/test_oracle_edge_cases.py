import datetime as dt
import decimal

import pytest

from services.source.external import drift_sql, land_parquet_sql, schema_drift, validate_config
from services.source.oracle.catalog import _watermark_candidate, diagnose
from services.source.oracle.connect import OracleConfig, parse_connect_string
from services.source.oracle.errors import explain
from services.source.oracle.procedures import cleanup_sql, procedure_sql, schedule_sql, setup_sql
from services.source.oracle.profile import profileable, stats_sql, value_sql
from services.source.oracle.types import is_aggregatable, source_expr


# ---- connect strings and TLS

@pytest.mark.parametrize("text,expected", [
    ("db.acme.com:1521/ORCLPDB1", {"host": "db.acme.com", "port": 1521, "service_name": "ORCLPDB1", "protocol": "tcp"}),
    ("//db.acme.com/ORCLPDB1", {"host": "db.acme.com", "port": 1521, "service_name": "ORCLPDB1", "protocol": "tcp"}),
    ("tcps://adb.us-ashburn-1.oraclecloud.com:1522/g1_demo_high.adb.oraclecloud.com?ssl_server_dn_match=yes",
     {"host": "adb.us-ashburn-1.oraclecloud.com", "port": 1522, "service_name": "g1_demo_high.adb.oraclecloud.com",
      "protocol": "tcps", "ssl_server_dn_match": True}),
    ("jdbc:oracle:thin:@10.0.4.12:1521:ORCL", {"host": "10.0.4.12", "port": 1521, "sid": "ORCL", "protocol": "tcp"}),
    ("jdbc:oracle:thin:@//db.acme.com:1525/SALES", {"host": "db.acme.com", "port": 1525, "service_name": "SALES",
                                                    "protocol": "tcp"}),
    ("hr/secret@db.acme.com:1521/ORCLPDB1", {"host": "db.acme.com", "port": 1521, "service_name": "ORCLPDB1",
                                             "protocol": "tcp"}),
    ("(DESCRIPTION=(ADDRESS=(PROTOCOL=TCPS)(HOST=adb.example.com)(PORT=1522))(CONNECT_DATA=(SERVICE_NAME=svc_high))"
     "(SECURITY=(SSL_SERVER_DN_MATCH=NO)))",
     {"host": "adb.example.com", "port": 1522, "service_name": "svc_high", "protocol": "tcps",
      "ssl_server_dn_match": False}),
    ("(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=10.1.1.1)(PORT=1521))(CONNECT_DATA=(SID=XE)))",
     {"host": "10.1.1.1", "port": 1521, "sid": "XE", "protocol": "tcp"}),
])
def test_connect_strings_people_paste(text, expected):
    assert parse_connect_string(text) == expected


def test_connect_string_never_keeps_a_pasted_password():
    parsed = parse_connect_string("hr/Sup3rSecret@db.acme.com:1521/ORCLPDB1")
    assert "Sup3rSecret" not in str(parsed)


@pytest.mark.parametrize("bad", ["", "just-a-host", "(DESCRIPTION=(CONNECT_DATA=(SID=X)))"])
def test_unreadable_connect_strings_say_so(bad):
    with pytest.raises(AssertionError):
        parse_connect_string(bad)


def test_tls_descriptor_and_defaults():
    cfg = OracleConfig.from_dict({"host": "adb.example.com", "port": 1522, "service_name": "svc_high", "user": "demo",
                                  "protocol": "tcps"})
    assert "(PROTOCOL=TCPS)" in cfg.dsn() and "(SSL_SERVER_DN_MATCH=YES)" in cfg.dsn()
    off = OracleConfig.from_dict({"host": "h", "service_name": "s", "user": "u", "protocol": "tcps",
                                  "ssl_server_dn_match": "false"})
    assert "(SSL_SERVER_DN_MATCH=NO)" in off.dsn()
    assert validate_config("oracle", {"host": "h", "service_name": "s", "user": "u", "protocol": "TCPS"})["port"] == "1522"
    with pytest.raises(AssertionError, match="wallet"):
        validate_config("oracle", {"host": "h", "service_name": "s", "user": "u", "wallet_dir": "/w"})


# ---- errors people actually hit

@pytest.mark.parametrize("message,title,retry", [
    ("ORA-01017: invalid credential or not authorized; logon denied", "Wrong user name or password", False),
    ("ORA-28001: the password has expired", "The Oracle password has expired", False),
    ("ORA-28000: The account is locked.", "The Oracle account is locked", False),
    ("DPY-6005: cannot connect to database. ORA-12514: Cannot connect to database. Service X is not registered",
     "The listener does not know that service name", False),
    ("DPY-6005: cannot connect to database (CONNECTION_ID=..). [WinError 10061] No connection could be made",
     "Nothing is listening on that host and port", True),
    ("DPY-6005: cannot connect to database. timed out", "The connection timed out", True),
    ("ORA-01555: snapshot too old: rollback segment number 3", "Snapshot too old during a long read", True),
    ("DPY-3015: password verifier type 0x939 is not supported by python-oracledb in thin mode",
     "The password uses an old verifier the driver cannot read", False),
    ("Failed to connect to host: host is not allowed by the network rule", "Snowflake blocked the outbound connection",
     False),
])
def test_errors_are_explained_with_a_fix(message, title, retry):
    why = explain(message)
    assert why["title"] == title and why["fix"] and why["retryable"] is retry


def test_unknown_errors_keep_their_text():
    why = explain("ORA-99999: something new")
    assert why["code"] == "ORA-99999" and why["fix"] is None


# ---- column types

@pytest.mark.parametrize("data_type,owner,expr_part,skipped", [
    ("XMLTYPE", "SYS", "XMLSERIALIZE(CONTENT", False),
    ("SDO_GEOMETRY", "MDSYS", "SDO_UTIL.TO_WKTGEOMETRY", False),
    ("JSON", None, "JSON_SERIALIZE", False),
    ("INTERVAL DAY(2) TO SECOND(6)", None, "TO_CHAR", False),
    ("UROWID", None, "CAST(", False),
    ("TIMESTAMP(6) WITH TIME ZONE", None, "SYS_EXTRACT_UTC", False),
    ("TIMESTAMP(6) WITH LOCAL TIME ZONE", None, "SYS_EXTRACT_UTC", False),
    ("BFILE", None, None, True),
    ("ADDRESS_T", "HR", None, True),
    ("VARCHAR2", None, None, False),
])
def test_every_column_type_is_read_safely_or_skipped_with_a_reason(data_type, owner, expr_part, skipped):
    expr, why = source_expr("COL", data_type, owner)
    assert (why is not None) is skipped
    if skipped:
        assert expr is None
    elif expr_part:
        assert expr_part in expr and expr.endswith('AS "COL"')
    else:
        assert expr == '"COL"'


def test_profiling_skips_long_and_unreadable_columns_and_uses_expressions():
    cols = [{"column_name": "ID", "data_type": "NUMBER", "family": "NUMBER", "expr": '"ID"'},
            {"column_name": "NOTES", "data_type": "LONG", "family": "TEXT", "lob": True, "expr": '"NOTES"'},
            {"column_name": "DOC", "data_type": "BFILE", "family": "OTHER", "skip_reason": "file"},
            {"column_name": "AT", "data_type": "TIMESTAMP(6) WITH TIME ZONE", "family": "TIMESTAMP",
             "expr": 'SYS_EXTRACT_UTC(CAST("AT" AS TIMESTAMP WITH TIME ZONE)) AS "AT"'}]
    usable = profileable(cols)
    assert [c["column_name"] for c in usable] == ["ID", "AT"]
    assert value_sql(usable[1]) == 'SYS_EXTRACT_UTC(CAST("AT" AS TIMESTAMP WITH TIME ZONE))'
    assert 'MIN(SYS_EXTRACT_UTC(CAST("AT" AS TIMESTAMP WITH TIME ZONE)))' in stats_sql("HR", "T", usable, "")
    assert not is_aggregatable("CLOB") and is_aggregatable("VARCHAR2")


# ---- incremental loads

def test_watermark_suggestions_prefer_last_updated():
    cols = [{"COLUMN_NAME": "ID", "DATA_TYPE": "NUMBER"}, {"COLUMN_NAME": "CREATED_AT", "DATA_TYPE": "DATE"},
            {"COLUMN_NAME": "LAST_UPDATED", "DATA_TYPE": "TIMESTAMP(6)"}]
    assert _watermark_candidate(cols, ["ID"])["column"] == "LAST_UPDATED"
    assert _watermark_candidate(cols[:2], ["ID"])["column"] == "CREATED_AT"
    assert _watermark_candidate(cols[:1], ["ID"]) == {"column": "ID", "reason": "increasing numeric key (new rows only)"}
    assert _watermark_candidate([{"COLUMN_NAME": "NAME", "DATA_TYPE": "VARCHAR2"}], []) is None


class _Cur:
    def __init__(self, batches):
        self.batches, self.arraysize, self.prefetchrows, self.sql = list(batches), 0, 0, []

    def execute(self, sql, binds=None):
        self.sql.append((sql, binds))

    def fetchone(self):
        return (None,)

    def fetchmany(self):
        return self.batches.pop(0) if self.batches else []


class _Conn:
    def __init__(self, batches):
        self.cur = _Cur(batches)

    def cursor(self):
        return self.cur


def _extract(conn, cols, **kw):
    pytest.importorskip("pyarrow")
    pytest.importorskip("oracledb")
    from services.source.oracle.extract import extract_table

    return extract_table(conn, "HR", "ORDERS", cols, source_system="ORACLE:X", batch_id="b", put=lambda p, n: None, **kw)


def test_extract_reads_expressions_skips_unreadable_and_uses_index_friendly_watermarks():
    cols = [{"column_name": "ID", "data_type": "NUMBER", "precision": 10, "scale": 0, "expr": '"ID"'},
            {"column_name": "UPDATED", "data_type": "DATE", "expr": '"UPDATED"'},
            {"column_name": "SHAPE", "data_type": "SDO_GEOMETRY", "expr": 'SDO_UTIL.TO_WKTGEOMETRY("SHAPE") AS "SHAPE"'},
            {"column_name": "SCAN", "data_type": "BFILE", "skip_reason": "file on the server"}]
    conn = _Conn([[(decimal.Decimal(1), dt.datetime(2026, 1, 1), "POINT (1 2)")]])
    out = _extract(conn, cols, watermark_column="UPDATED", watermark_value="2025-12-31 00:00:00.000000",
                   lookback_minutes=30)
    select, binds = conn.cur.sql[-1]
    assert 'SDO_UTIL.TO_WKTGEOMETRY("SHAPE") AS "SHAPE"' in select and '"SCAN"' not in select
    assert "CAST(TO_TIMESTAMP(:wm, 'YYYY-MM-DD HH24:MI:SS.FF6') - NUMTODSINTERVAL(30, 'MINUTE') AS DATE)" in select
    assert binds == {"wm": "2025-12-31 00:00:00.000000"}
    assert out["skipped"] == [{"column": "SCAN", "reason": "file on the server"}] and "SCAN" not in out["columns"]
    assert out["watermark"] == "2026-01-01 00:00:00.000000"


def test_extract_can_be_cancelled_between_batches():
    from services.source.oracle.extract import Cancelled

    cols = [{"column_name": "ID", "data_type": "NUMBER", "precision": 10, "scale": 0}]
    with pytest.raises(Cancelled):
        _extract(_Conn([[(decimal.Decimal(1),)]]), cols, cancelled=lambda: True)


# ---- landing

def test_merge_lands_through_a_staging_table_and_dedupes_by_key():
    sql = land_parquet_sql("DB", "EXT_HR", "EMP", "EMP/b1", "merge", exists=True, keys=["EMP_ID"],
                           columns=["EMP_ID", "NAME"])
    assert sql[0].startswith('CREATE OR REPLACE TEMPORARY TABLE "DB"."EXT_HR"."EMP__MERGE"')
    assert sql[2].startswith('COPY INTO "DB"."EXT_HR"."EMP__MERGE"')
    merge = sql[3]
    assert 'PARTITION BY "EMP_ID"' in merge and 'T."EMP_ID" IS NOT DISTINCT FROM S."EMP_ID"' in merge
    assert 'T."NAME" = S."NAME"' in merge and 'T."EMP_ID" = S."EMP_ID"' not in merge
    assert sql[-1].startswith("DROP TABLE IF EXISTS")
    first = land_parquet_sql("DB", "EXT_HR", "EMP", "EMP/b1", "merge", exists=False, keys=["EMP_ID"], columns=["EMP_ID"])
    assert first[0].startswith("CREATE TABLE IF NOT EXISTS") and first[-1].startswith("COPY INTO")
    with pytest.raises(AssertionError, match="key"):
        land_parquet_sql("DB", "EXT_HR", "EMP", "EMP/b1", "merge", exists=True, columns=["A"])
    with pytest.raises(AssertionError):
        land_parquet_sql("DB", "EXT_HR", "EMP", "EMP/b1", "merge", exists=True, keys=['X" OR 1=1'], columns=["A"])


def test_schema_drift_adds_new_columns_and_reports_the_rest():
    drift = schema_drift({"ID": "NUMBER(10,0)", "EMAIL": "VARCHAR", "SALARY": "VARCHAR"},
                         {"ID": "NUMBER", "SALARY": "NUMBER", "PHONE": "TEXT", "_BATCH_ID": "TEXT"})
    assert drift["added"] == {"EMAIL": "VARCHAR"} and drift["missing"] == ["PHONE"]
    assert drift["retyped"] == {"SALARY": {"was": "NUMBER", "now": "VARCHAR"}} and drift["changed"]
    assert drift_sql("DB", "EXT_HR", "EMP", {"EMAIL": "VARCHAR"}) == [
        'ALTER TABLE "DB"."EXT_HR"."EMP" ADD COLUMN IF NOT EXISTS "EMAIL" VARCHAR']
    assert not schema_drift({"ID": "NUMBER(38,0)"}, {"ID": "NUMBER", "_SOURCE_FILE": "TEXT"})["changed"]


# ---- Snowflake objects

def test_existing_secret_and_integration_are_used_not_recreated():
    nothing = setup_sql("DB", "EXT_HR", "SHARED_ORACLE_EAI", "db", 1521, "HR", secret="SEC.OPS.ORACLE_HR",
                        create_secret=False, create_integration=False)
    assert nothing == []
    only_eai = setup_sql("DB", "EXT_HR", "HR_EAI", "db", 1521, "HR", secret="SEC.OPS.ORACLE_HR", create_secret=False)
    assert len(only_eai) == 2 and "ALLOWED_AUTHENTICATION_SECRETS = (SEC.OPS.ORACLE_HR)" in only_eai[1]
    proc = procedure_sql("DB", "EXT_HR", "@DB.CORE.CODE/s.zip", "SHARED_ORACLE_EAI", "SEC.OPS.ORACLE_HR")
    assert "SECRETS = ('oracle_login' = SEC.OPS.ORACLE_HR)" in proc
    with pytest.raises(AssertionError):
        setup_sql("DB", "EXT_HR", "E", "db", 1521, "HR", secret="X; DROP DATABASE DB")


def test_cleanup_drops_only_what_the_platform_created():
    chosen = cleanup_sql({"database": "DB", "schema": "EXT_HR", "secret": "SEC.OPS.S",
                          "external_access_integration": "SHARED", "created_objects": []})
    assert not any("SECRET" in s or "INTEGRATION" in s for s in chosen) and any("PROCEDURE" in s for s in chosen)
    made = cleanup_sql({"database": "DB", "schema": "EXT_HR", "secret": "DB.EXT_HR.ORACLE_LOGIN",
                        "external_access_integration": "HR_EAI", "created_objects": ["secret", "integration"]})
    assert "DROP SECRET IF EXISTS DB.EXT_HR.ORACLE_LOGIN" in made
    assert "DROP EXTERNAL ACCESS INTEGRATION IF EXISTS HR_EAI" in made


def test_schedules_are_validated_tasks():
    sid = "0d2c9a8e-1111-2222-3333-444455556666"
    sql = schedule_sql("DB", "EXT_HR", sid, "DBT_WH", "0 * * * * UTC", {"tables": ["O'BRIEN"], "mode": "merge"})
    assert "SCHEDULE = 'USING CRON 0 * * * * UTC'" in sql[0] and "O''BRIEN" in sql[0] and sql[1].endswith("RESUME")
    with pytest.raises(AssertionError):
        schedule_sql("DB", "EXT_HR", sid, "DBT_WH", "every hour", {})
    with pytest.raises(AssertionError):
        schedule_sql("DB", "EXT_HR", "x'; drop", "DBT_WH", "0 * * * * UTC", {})


# ---- diagnostics

class _DiagCur:
    def __init__(self, answers):
        self.answers, self.last = answers, None
        self.arraysize = self.prefetchrows = 0

    def execute(self, sql, **binds):
        self.last = next((v for k, v in self.answers.items() if k in sql), [(1,)])

    def fetchone(self):
        return self.last[0] if self.last else None

    def fetchall(self):
        return self.last


class _DiagConn:
    version = "19.21.0.0.0"

    def __init__(self, answers):
        self.c = _DiagCur(answers)

    def cursor(self):
        return self.c

    def close(self):
        pass


CFG = OracleConfig(host="db.acme.com", service_name="ORCLPDB1", user="ETL", schema_owner="HR")


def test_diagnose_stops_cleanly_when_the_network_is_blocked():
    def blocked(host, port, timeout):
        raise OSError("timed out")

    out = diagnose(CFG, "pw", lambda: None, probe=blocked)
    by_id = {c["id"]: c for c in out["checks"]}
    assert out["status"] == "fail" and by_id["network"]["status"] == "fail"
    assert by_id["network"]["detail"] == "The connection timed out" and by_id["login"]["status"] == "skip"


def test_diagnose_reports_a_wrong_password_with_its_fix():
    def bad():
        raise RuntimeError("ORA-01017: invalid username/password; logon denied")

    out = diagnose(CFG, "pw", bad, probe=lambda *a: None)
    login = next(c for c in out["checks"] if c["id"] == "login")
    assert login["status"] == "fail" and login["code"] == "ORA-01017" and "secret" in login["fix"]


def test_diagnose_warns_on_expiring_password_stale_stats_and_unreadable_columns():
    now = dt.datetime(2026, 10, 8)
    answers = {
        "FROM USER_USERS": [("OPEN", now + dt.timedelta(days=5), now)],
        "SYS_CONTEXT('USERENV','DB_NAME')": [("ORCL", "ORCLPDB1", "ORCLPDB1", "+00:00")],
        "NLS_DATABASE_PARAMETERS": [("NLS_CHARACTERSET", "AL32UTF8"), ("NLS_NCHAR_CHARACTERSET", "AL16UTF16")],
        "FROM ALL_USERS": [(1,)],
        "(SELECT COUNT(*) FROM ALL_TABLES": [(12, 3)],
        "AND ROWNUM = 1": [("EMPLOYEES",)],
        "SUM(CASE WHEN NUM_ROWS IS NULL": [(12, 4)],
        "DATA_TYPE_OWNER FROM ALL_TAB_COLUMNS": [("EMPLOYEES", "PHOTO_FILE", "BFILE", None),
                                                 ("EMPLOYEES", "NAME", "VARCHAR2", None)],
    }
    out = diagnose(CFG, "pw", lambda: _DiagConn(answers), probe=lambda *a: None)
    by_id = {c["id"]: c for c in out["checks"]}
    assert out["status"] == "warn" and out["ok"]
    assert by_id["account"]["status"] == "warn" and "5 day" in by_id["account"]["detail"]
    assert by_id["access"]["status"] == "ok" and "12 tables" in by_id["access"]["detail"]
    assert by_id["stats"]["status"] == "warn" and by_id["types"]["status"] == "warn"
    assert out["server"]["charset"] == "AL32UTF8" and out["server"]["skipped_columns"] == 1


def test_diagnose_flags_an_owner_without_grants():
    answers = {"FROM USER_USERS": [("OPEN", None, dt.datetime(2026, 1, 1))], "FROM ALL_USERS": [(1,)],
               "(SELECT COUNT(*) FROM ALL_TABLES": [(0, 0)], "NLS_DATABASE_PARAMETERS": [],
               "SYS_CONTEXT('USERENV','DB_NAME')": [("D", "S", "C", "UTC")], "AND ROWNUM = 1": [],
               "SUM(CASE WHEN NUM_ROWS IS NULL": [(0, 0)], "DATA_TYPE_OWNER FROM ALL_TAB_COLUMNS": []}
    out = diagnose(CFG, "pw", lambda: _DiagConn(answers), probe=lambda *a: None)
    access = next(c for c in out["checks"] if c["id"] == "access")
    assert out["status"] == "fail" and access["status"] == "fail" and "grant SELECT" in access["fix"]


def test_diagnose_without_a_password_explains_where_it_comes_from():
    out = diagnose(CFG, "", lambda: None, probe=lambda *a: None)
    login = next(c for c in out["checks"] if c["id"] == "login")
    assert login["status"] == "fail" and "environment variable" in login["fix"]
