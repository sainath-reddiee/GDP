import pytest

from services.source import external
from services.source.adapters import LandedExternalAdapter, adapter_for


def test_catalog_marks_what_the_platform_can_land():
    catalog = {c["id"]: c for c in external.connector_catalog()}
    assert catalog["s3"]["landable"] and catalog["upload"]["landable"]
    assert not catalog["postgres"]["landable"] and "connector" in catalog["postgres"]["guidance"]
    assert external.source_type_for("salesforce") == "EXTERNAL_SAAS"
    assert external.is_external("EXTERNAL_FILE") and not external.is_external("SNOWFLAKE_DATABASE")


def test_config_validation_keeps_credentials_out():
    cfg = external.validate_config("s3", {"url": "s3://bucket/crm/", "storage_integration": "S3_INT",
                                          "file_format": "parquet"})
    assert cfg == {"url": "s3://bucket/crm/", "storage_integration": "S3_INT", "file_format": "PARQUET"}
    with pytest.raises(AssertionError, match="object name"):
        external.validate_config("postgres", {"host": "db.local", "secret": "password=hunter2"})
    with pytest.raises(AssertionError, match="unknown fields"):
        external.validate_config("postgres", {"password": "x"})
    with pytest.raises(AssertionError, match="s3://"):
        external.validate_config("s3", {"url": "https://bucket", "storage_integration": "S3_INT"})
    with pytest.raises(AssertionError, match="storage_integration"):
        external.validate_config("s3", {"url": "s3://bucket"})
    assert external.validate_config("upload", {})["file_format"] == "CSV"


def test_table_names_and_grouping():
    assert external.table_name_for("crm/2024/Orders-Jan.csv.gz") == "ORDERS_JAN"
    assert external.table_name_for("2024_sales.parquet") == "T_2024_SALES"
    assert external.group_files(["a.csv", "b.csv"]) == {"A": ["a.csv"], "B": ["b.csv"]}
    assert external.group_files(["a.csv", "b.csv"], "ORDERS") == {"ORDERS": ["a.csv", "b.csv"]}
    assert external.staged_file("files/orders.csv", "") == "orders.csv"
    assert external.staged_file("s3://bucket/crm/orders.csv", "s3://bucket/crm/") == "orders.csv"


def test_landing_sql_infers_schema_and_copies_by_name():
    setup = external.setup_sql("AI", "EXT_CRM", "upload", {"file_format": "CSV"})
    assert setup[0].startswith('CREATE SCHEMA IF NOT EXISTS "AI"."EXT_CRM"')
    assert "PARSE_HEADER = TRUE" in setup[1] and "SNOWFLAKE_SSE" in setup[2]
    cloud = external.setup_sql("AI", "EXT_CRM", "s3", {"file_format": "CSV", "url": "s3://b/", "storage_integration": "S3_INT"})
    assert "URL = 's3://b/' STORAGE_INTEGRATION = S3_INT" in cloud[2]
    landing = external.land_sql("AI", "EXT_CRM", "CSV", "ORDERS", ["o'1.csv", "o2.csv"])
    copy, create = landing[1], landing[2]
    assert "USING TEMPLATE" in create and "INFER_SCHEMA" in create and "o''1.csv" in create
    assert "UPPER(REGEXP_REPLACE(COLUMN_NAME" in create
    assert "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE" in copy and "FILES = ('o''1.csv', 'o2.csv')" in copy


def test_landed_external_sources_read_like_snowflake_schemas():
    adapter = adapter_for("EXTERNAL_FILE", "AI", "EXT_CRM")
    assert isinstance(adapter, LandedExternalAdapter) and adapter.type_check("STANDARD").status == "PASSED"
