import pytest

from services.source.adapters import (
    FAILED,
    PASSED,
    WARNING,
    SnowflakeDatabaseAdapter,
    SnowflakeShareAdapter,
    adapter_for,
    checks_passed,
)
from services.source.identifiers import fqn, format_data_type, landing_table_name, normalize, quote


class FakeSnowflake:
    """Answers the metadata queries an adapter issues, keyed by a substring of the SQL."""

    def __init__(self, db_type="STANDARD", schemas=("CRM",), tables=None, unreadable=(), db_error=None):
        self.db_type = db_type
        self.schemas = schemas
        self.tables = tables if tables is not None else [
            {"TABLE_NAME": "CRM_CUSTOMER", "TABLE_TYPE": "BASE TABLE", "ROW_COUNT": 500, "BYTES": 1024,
             "LAST_ALTERED": "2026-10-01"},
            {"TABLE_NAME": "V_ACTIVE", "TABLE_TYPE": "VIEW", "ROW_COUNT": None, "BYTES": None, "LAST_ALTERED": None},
        ]
        self.unreadable = set(unreadable)
        self.db_error = db_error
        self.sql = []

    def __call__(self, sql, params):
        self.sql.append((sql, params))
        if "INFORMATION_SCHEMA.DATABASES" in sql:
            if self.db_error:
                raise RuntimeError(self.db_error)
            return [{"TYPE": self.db_type}] if self.db_type else []
        if "INFORMATION_SCHEMA.SCHEMATA" in sql:
            return [{"SCHEMA_NAME": params[0]}] if params[0] in self.schemas else []
        if "INFORMATION_SCHEMA.TABLES" in sql:
            return self.tables
        if sql.endswith("LIMIT 0"):
            if any(f'"{t}"' in sql for t in self.unreadable):
                raise RuntimeError("Insufficient privileges")
            return []
        raise AssertionError(f"unexpected SQL: {sql}")


def test_identifiers():
    assert normalize("snowflake_sample_data") == "SNOWFLAKE_SAMPLE_DATA"
    assert normalize('"MixedCase"') == "MixedCase"
    assert normalize("has space") == "has space"
    assert quote('a"b') == '"a""b"'
    assert fqn("DB", "S", "T") == '"DB"."S"."T"'
    assert landing_table_name("CRM", "crm-customer v2") == "CRM__CRM_CUSTOMER_V2"
    assert landing_table_name("CRM", "1X") == "CRM__1X"
    assert format_data_type("TEXT", 50) == "VARCHAR(50)"
    assert format_data_type("NUMBER", None, 12, 2) == "NUMBER(12,2)"
    assert format_data_type("DATE") == "DATE"
    with pytest.raises(AssertionError):
        normalize("")


def test_injection_attempt_stays_inside_quotes():
    adapter = SnowflakeDatabaseAdapter("DB", "S")
    sql = adapter.select_sql('X"; DROP TABLE Y; --')
    assert sql == 'SELECT * FROM "DB"."S"."X""; DROP TABLE Y; --"'


def test_discover_maps_object_types():
    objects = SnowflakeDatabaseAdapter("demo", "crm").discover_objects(FakeSnowflake())
    assert [(o.name, o.object_type, o.database, o.schema) for o in objects] == [
        ("CRM_CUSTOMER", "TABLE", "DEMO", "CRM"), ("V_ACTIVE", "VIEW", "DEMO", "CRM")]


def test_database_adapter_all_checks_pass():
    checks = SnowflakeDatabaseAdapter("DEMO", "CRM").validate_access(FakeSnowflake(), ["CRM_CUSTOMER", "V_ACTIVE"])
    assert [c.name for c in checks] == ["DATABASE", "SOURCE_TYPE", "SCHEMA", "TABLES",
                                        "SELECTED_OBJECTS", "SELECTED_OBJECTS"]
    assert checks_passed(checks)


def test_database_adapter_warns_on_share():
    checks = SnowflakeDatabaseAdapter("DEMO", "CRM").validate_access(
        FakeSnowflake(db_type="IMPORTED DATABASE"), ["CRM_CUSTOMER"])
    assert checks[1].status == WARNING and checks_passed(checks)


def test_share_adapter_requires_imported_database():
    checks = SnowflakeShareAdapter("DEMO", "CRM").validate_access(FakeSnowflake(), ["CRM_CUSTOMER"])
    assert checks[-1].name == "SHARE" and checks[-1].status == FAILED and not checks_passed(checks)
    ok = SnowflakeShareAdapter("DEMO", "CRM").validate_access(FakeSnowflake(db_type="IMPORTED DATABASE"),
                                                              ["CRM_CUSTOMER"])
    assert checks_passed(ok)


@pytest.mark.parametrize("fake, selected, failed_check", [
    (FakeSnowflake(db_error="Database 'X' does not exist or not authorized"), ["CRM_CUSTOMER"], "DATABASE"),
    (FakeSnowflake(db_type=None), ["CRM_CUSTOMER"], "DATABASE"),
    (FakeSnowflake(schemas=()), ["CRM_CUSTOMER"], "SCHEMA"),
    (FakeSnowflake(tables=[]), ["CRM_CUSTOMER"], "TABLES"),
    (FakeSnowflake(), [], "SELECTED_OBJECTS"),
    (FakeSnowflake(), ["NOT_THERE"], "SELECTED_OBJECTS"),
    (FakeSnowflake(unreadable=["CRM_CUSTOMER"]), ["CRM_CUSTOMER"], "SELECTED_OBJECTS"),
])
def test_failures_are_reported_with_the_failing_check(fake, selected, failed_check):
    checks = SnowflakeDatabaseAdapter("DEMO", "CRM").validate_access(fake, selected)
    assert not checks_passed(checks)
    assert checks[-1].name == failed_check and checks[-1].status == FAILED
    assert all(c.status == PASSED for c in checks if c.name not in (failed_check, "SOURCE_TYPE"))


def test_selected_limit():
    checks = SnowflakeDatabaseAdapter("DEMO", "CRM").validate_access(FakeSnowflake(), [f"T{i}" for i in range(51)])
    assert checks[-1].status == FAILED and "at most" in checks[-1].remediation


def test_adapter_for_rejects_unknown_type():
    assert isinstance(adapter_for("SNOWFLAKE_SHARE", "D", "S"), SnowflakeShareAdapter)
    with pytest.raises(AssertionError):
        adapter_for("FILE", "D", "S")
