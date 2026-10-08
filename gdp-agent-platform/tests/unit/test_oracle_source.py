import datetime as dt
import decimal

import pytest

from services.source.external import land_parquet_sql, validate_config
from services.source.oracle.connect import OracleConfig, is_transient, with_retry
from services.source.oracle.extract import column_specs, snowflake_names, watermark_text
from services.source.oracle.procedures import procedure_sql, setup_sql
from services.source.oracle.profile import frequencies_sql, patterns_sql, sample_clause, stats_sql
from services.source.oracle.types import map_type, oracle_type_name
from services.source.procedures import _location


@pytest.mark.parametrize("data_type,p,s,arrow,snow", [
    ("NUMBER", 10, 0, "int64", "NUMBER(10,0)"),
    ("NUMBER", 38, 0, "decimal:38,0", "NUMBER(38,0)"),
    ("NUMBER", 12, 2, "decimal:12,2", "NUMBER(12,2)"),
    ("NUMBER", None, None, "decimal:38,10", "NUMBER(38,10)"),
    ("NUMBER", None, 0, "decimal:38,0", "NUMBER(38,0)"),  # INTEGER columns
    ("FLOAT", 126, None, "decimal:38,10", "NUMBER(38,10)"),
    ("BINARY_DOUBLE", None, None, "float64", "FLOAT"),
    ("DATE", None, None, "timestamp", "TIMESTAMP_NTZ"),  # Oracle DATE keeps its time of day
    ("TIMESTAMP(6)", None, 6, "timestamp", "TIMESTAMP_NTZ"),
    ("TIMESTAMP(6) WITH TIME ZONE", None, 6, "timestamptz", "TIMESTAMP_TZ"),
    ("VARCHAR2", None, None, "string", "VARCHAR"),
    ("CLOB", None, None, "string", "VARCHAR"),
    ("BLOB", None, None, "binary", "BINARY"),
    ("INTERVAL DAY(2) TO SECOND(6)", None, None, "string", "VARCHAR"),
])
def test_type_mapping_keeps_precision(data_type, p, s, arrow, snow):
    spec = map_type(data_type, p, s)
    assert (spec.arrow, spec.snowflake) == (arrow, snow)
    assert map_type("CLOB").lob and not map_type("VARCHAR2").lob


def test_type_names_for_display():
    assert oracle_type_name("NUMBER", 12, 2, None) == "NUMBER(12,2)"
    assert oracle_type_name("VARCHAR2", None, None, 40) == "VARCHAR2(40)"
    assert oracle_type_name("NUMBER", None, None, None) == "NUMBER"


def test_dsn_and_validation():
    dsn = OracleConfig.from_dict({"host": "db.acme.com", "service_name": "ORCLPDB", "user": "hr"}).dsn()
    assert "(PROTOCOL=TCP)(HOST=db.acme.com)(PORT=1521)" in dsn and "(SERVICE_NAME=ORCLPDB)" in dsn
    assert "(SID=ORCL)" in OracleConfig.from_dict({"host": "10.0.0.5", "port": 1522, "sid": "ORCL", "user": "hr"}).dsn()
    with pytest.raises(AssertionError):
        OracleConfig.from_dict({"host": "x", "service_name": "A", "sid": "B", "user": "hr"})
    with pytest.raises(AssertionError):
        OracleConfig.from_dict({"host": "bad host!", "service_name": "A", "user": "hr"})


def test_only_network_errors_are_retried():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("ORA-03113: end-of-file on communication channel")
        return "ok"
    assert with_retry(flaky, sleep=sleeps.append) == "ok" and sleeps == [1.5, 3.0]
    assert not is_transient(RuntimeError("ORA-01017: invalid username/password"))
    with pytest.raises(RuntimeError, match="ORA-01017"):
        with_retry(lambda: (_ for _ in ()).throw(RuntimeError("ORA-01017: invalid")), sleep=sleeps.append)


COLS = [
    {"column_name": "EMP_ID", "family": "NUMBER", "lob": False, "data_type": "NUMBER", "precision": 6, "scale": 0},
    {"column_name": "Full Name", "family": "TEXT", "lob": False, "data_type": "VARCHAR2"},
    {"column_name": "HIRED", "family": "TIMESTAMP", "lob": False, "data_type": "DATE"},
    {"column_name": "NOTES", "family": "TEXT", "lob": True, "data_type": "CLOB"},
]


def test_profile_sql_matches_the_profiler_aliases_and_samples_big_tables():
    assert sample_clause(50_000) == ("", False)
    clause, approx = sample_clause(10_000_000)
    assert approx and clause == " SAMPLE (1.000000)"
    sql = stats_sql("hr", "EMPLOYEES", COLS, clause)
    for alias in ("N0", "V0", "D0", "MIN0", "MAX0", "AVG0", "LMIN1", "LAVG1", "V1", "N3", "LMAX3"):
        assert f" AS {alias}" in sql
    assert 'FROM "HR"."EMPLOYEES" SAMPLE (1.000000)' in sql and "APPROX_COUNT_DISTINCT" in sql
    assert '"Full Name"' in sql and "D3" not in sql  # LOBs: no distinct/min/max
    assert "COUNT(DISTINCT" in stats_sql("hr", "EMPLOYEES", COLS, "")
    freq = frequencies_sql("hr", "EMPLOYEES", COLS, "")
    assert freq.count("UNION ALL") == 2 and "FETCH FIRST 10 ROWS ONLY" in freq and "NOTES" not in freq
    assert "TRANSLATE(SUBSTR(\"Full Name\"" in patterns_sql("hr", "EMPLOYEES", COLS, "")


def test_column_names_become_snowflake_safe_and_unique():
    assert snowflake_names(["Full Name", "FULL_NAME", "1st", "_SOURCE_SYSTEM"]) == \
        {"Full Name": "FULL_NAME", "FULL_NAME": "FULL_NAME_2", "1st": "C_1ST", "_SOURCE_SYSTEM": "SOURCE_SYSTEM"}
    specs = column_specs([{"column_name": "AMOUNT", "data_type": "NUMBER"}], oversized=["AMOUNT"])
    assert specs["AMOUNT"].snowflake == "VARCHAR"  # too large for NUMBER(38,10): carried as text
    assert watermark_text(dt.datetime(2026, 10, 7, 13, 54, 1, 250)) == "2026-10-07 13:54:01.000250"


def test_landing_creates_once_then_replaces_or_appends():
    first = land_parquet_sql("DB", "EXT_HR", "EMPLOYEES", "EMPLOYEES/20261007_abc", "replace", exists=False)
    assert first[0].startswith('CREATE TABLE IF NOT EXISTS "DB"."EXT_HR"."EMPLOYEES" USING TEMPLATE')
    assert """LOCATION => '@"DB"."EXT_HR"."FILES"/EMPLOYEES/20261007_abc/'""" in first[0]
    assert "ADD COLUMN IF NOT EXISTS _SOURCE_FILE VARCHAR, _INGESTED_AT TIMESTAMP_LTZ" in first[1]
    assert "INCLUDE_METADATA = (_SOURCE_FILE = METADATA$FILENAME" in first[-1] and "TRUNCATE" not in " ".join(first)
    again = land_parquet_sql("DB", "EXT_HR", "EMPLOYEES", "EMPLOYEES/b2", "replace", exists=True)
    assert again[0] == 'TRUNCATE TABLE "DB"."EXT_HR"."EMPLOYEES"' and again[1].startswith("COPY INTO")
    assert len(land_parquet_sql("DB", "EXT_HR", "EMPLOYEES", "EMPLOYEES/b3", "append", exists=True)) == 1
    ice = land_parquet_sql("DB", "EXT_HR", "EMPLOYEES", "EMPLOYEES/b4", "replace", exists=False, external_volume="VOL")
    assert ice[0].startswith("CREATE ICEBERG TABLE IF NOT EXISTS") and "EXTERNAL_VOLUME = 'VOL'" in ice[0]
    with pytest.raises(AssertionError):
        land_parquet_sql("DB", "EXT_HR", "T", "x'; DROP", "replace")


def test_oracle_connector_config():
    cfg = validate_config("oracle", {"host": "db.acme.com", "service_name": "ORCLPDB", "user": "hr"})
    assert cfg["schema_owner"] == "HR" and cfg["runtime"] == "snowflake" and cfg["port"] == "1521"
    with pytest.raises(AssertionError, match="password_env"):
        validate_config("oracle", {"host": "db", "sid": "ORCL", "user": "hr", "runtime": "api_host"})
    with pytest.raises(AssertionError, match="service name or a SID"):
        validate_config("oracle", {"host": "db", "user": "hr"})


def test_snowflake_runtime_ddl():
    sql = setup_sql("DB", "EXT_HR", "HR_ORACLE_ACCESS", "db.acme.com", 1521, "HR")
    assert "TYPE = PASSWORD" in sql[0] and "VALUE_LIST = ('db.acme.com:1521')" in sql[1]
    assert "'<oracle password>'" in sql[0]  # bound only when the setup runs
    proc = procedure_sql("DB", "EXT_HR", "@DB.CORE.CODE_STAGE/services_x.zip", "HR_ORACLE_ACCESS")
    assert "EXECUTE AS OWNER" in proc and "'oracledb'" in proc and "SECRETS = ('oracle_login' = DB.EXT_HR.ORACLE_LOGIN)" in proc


def test_registration_compares_location_only():
    assert _location('{"database": "DB", "schema": "EXT_HR", "connector": "oracle", "loads": {}}') == \
        {"database": "DB", "schema": "EXT_HR"}


class FakeCursor:
    """Enough of an oracledb cursor for extract_table: description, execute, fetchmany."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.arraysize = 0
        self.prefetchrows = 0
        self.max_query = None

    def execute(self, sql, binds=None):
        self.sql, self.binds = sql, binds
        if sql.startswith("SELECT MAX(ABS"):
            self.batches_max = [(decimal.Decimal("12.5"),)]

    def fetchone(self):
        return (decimal.Decimal("12.5"),)

    def fetchmany(self):
        return self.batches.pop(0) if self.batches else []


class FakeConn:
    def __init__(self, batches):
        self.cur = FakeCursor(batches)

    def cursor(self):
        return self.cur


def test_extract_streams_typed_parquet_with_lineage(tmp_path):
    pyarrow = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    pytest.importorskip("oracledb")
    from services.source.oracle.extract import extract_table

    cols = [{"column_name": "EMP_ID", "data_type": "NUMBER", "precision": 6, "scale": 0},
            {"column_name": "Full Name", "data_type": "VARCHAR2"},
            {"column_name": "SALARY", "data_type": "NUMBER"},
            {"column_name": "HIRED", "data_type": "DATE"}]
    rows = [(decimal.Decimal(1), "Ana", decimal.Decimal("1234.123456789012"), dt.datetime(2020, 1, 2, 9, 30)),
            (decimal.Decimal(2), None, None, dt.datetime(2024, 5, 6, 17, 0))]
    saved = []

    def put(local, name):
        dest = tmp_path / name
        with open(local, "rb") as src, open(dest, "wb") as out:
            out.write(src.read())
        saved.append(dest)

    result = extract_table(FakeConn([rows[:1], rows[1:]]), "hr", "EMPLOYEES", cols, source_system="ORACLE:HR",
                           batch_id="b1", put=put, watermark_column="HIRED")
    assert result["rows"] == 2 and result["files"] == ["part-00001.parquet"] and len(saved) == 1
    assert result["columns"]["Full Name"] == "FULL_NAME" and result["notes"] == {"rounded": 1}
    assert result["watermark"] == "2024-05-06 17:00:00.000000"
    table = pq.read_table(saved[0])
    assert table.schema.field("EMP_ID").type == pyarrow.int64()
    assert str(table.schema.field("SALARY").type) == "decimal128(38, 10)"
    assert str(table.schema.field("HIRED").type) == "timestamp[us]"
    assert table.column("_BATCH_ID").to_pylist() == ["b1", "b1"]
    assert table.column("FULL_NAME").to_pylist() == ["Ana", None]
