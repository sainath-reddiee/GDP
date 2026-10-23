"""Other sources, split by kind (the onboarding configuration workbook): a relational database (PostgreSQL, Oracle,
SQL Server) and files (Amazon S3, SharePoint) each have their own questions and required fields, and the SnapLogic
AODS rows they produce follow the workbook (a database has no file type; neither has a previous load time or an API).
A payload without a kind (share onboarding, edits, older callers) keeps the old rules. The AODS tables' columns are
unchanged."""

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.source import ingestion_control as ic  # noqa: E402
from services.source import snaplogic_config as sc  # noqa: E402

BASE = {
    "target": {"landing_database": "dev_landing", "landing_schema": "landing", "bronze_schema": "bronze"},
    "load": {"load_type": "APPEND", "bronze_load_type": "TRUNCATE", "load_frequency": "DAILY", "job_trigger_time": "0 2 * * * UTC"},
    "snaplogic": {"snaplogic_project_name": "GDP/Ingestion", "parent_pipeline_name": "P_INGEST", "child_pipeline_name": "C_LOAD",
                  "api_url": "https://elastic.snaplogic.com/api/1/rest/slsched/feed/x", "api_key": "vault:snaplogic/ingest"},
}
DATABASE = {**copy.deepcopy(BASE),
            "source": {"source_kind": "DATABASE", "source_name": "crm_pg", "source_type": "POSTGRESQL", "domain_id": "d1",
                       "source_account": "crm_pg_prod", "source_database_path": "CRMPROD", "source_schema_file": "SALES"},
            "tables": [{"source_table_sql": "SALES.CUSTOMERS", "source_primary_keys": "customer_id",
                        "target_previous_load_time": "2026-10-01 00:00"}]}
FILE = {**copy.deepcopy(BASE),
        "source": {"source_kind": "FILE", "source_name": "crm_files", "source_type": "S3", "domain_id": "d1",
                   "source_account": "crm_s3", "source_database_path": "s3://crm/daily/", "source_schema_file": "customers_*.csv",
                   "source_file_type": "CSV"},
        "tables": [{"source_table_sql": "CUSTOMERS"}]}


def with_(payload, **patch):
    out = copy.deepcopy(payload)
    for path, value in patch.items():
        block, key = path.split("__", 1)
        if block == "table":
            out["tables"][0][key] = value
        else:
            out[block][key] = value
    return out


def errors(payload):
    return ic.validate(payload)[2]


# ---------------------------------------------------------------- relational database

def test_a_relational_database_passes_with_the_workbook_answers():
    header, rows, errs = ic.validate(DATABASE)
    assert errs == {} and header["source_kind"] == "DATABASE"
    (row,) = rows
    assert row["source_file_type"] == "TABLE" and row["source_sheet_name"] is None
    # never asked for a database: cleared even when a caller sends them
    assert row["target_previous_load_time"] is None and row["api_url"] is None and row["api_key"] is None


@pytest.mark.parametrize("patch, path, text", [
    ({"source__source_account": ""}, "source.source_account", "Source account is required"),
    ({"source__source_database_path": ""}, "source.source_database_path", "Source database is required"),
    ({"source__source_schema_file": ""}, "source.source_schema_file", "Source schema is required"),
    ({"source__domain_id": ""}, "source.domain_id", "Domain is required"),
    ({"table__source_primary_keys": ""}, "tables.0.source_primary_keys", "Primary keys are required"),
    ({"source__source_type": "S3"}, "source.source_type", "relational database source is one of"),
    ({"source__source_type": "SALESFORCE"}, "source.source_type", "relational database source is one of"),
])
def test_a_relational_database_requires_its_answers(patch, path, text):
    assert text in errors(with_(DATABASE, **patch)).get(path, "")


def test_a_relational_database_ignores_a_file_type_sent_with_it():
    header, rows, errs = ic.validate(with_(DATABASE, source__source_file_type="CSV", source__source_sheet_name="x"))
    assert errs == {} and rows[0]["source_file_type"] == "TABLE" and rows[0]["source_sheet_name"] is None
    assert ic.validate(with_(DATABASE, source__source_type="POSTGRES"))[0]["source_type"] == "POSTGRESQL"


# ---------------------------------------------------------------- files

def test_a_file_source_passes_and_keeps_its_file_type():
    header, rows, errs = ic.validate(FILE)
    assert errs == {} and header["source_kind"] == "FILE"
    assert rows[0]["source_file_type"] == "CSV" and rows[0]["source_table_sql"] == "CUSTOMERS"
    assert rows[0]["source_primary_keys"] is None          # keys are optional for an APPEND / TRUNCATE load
    assert rows[0]["api_url"] is None and rows[0]["target_previous_load_time"] is None


@pytest.mark.parametrize("patch, path, text", [
    ({"source__source_database_path": ""}, "source.source_database_path", "Source path is required"),
    ({"source__source_schema_file": ""}, "source.source_schema_file", "Source file is required"),
    ({"source__source_account": ""}, "source.source_account", "Source account is required"),
    ({"source__source_file_type": "TABLE"}, "source.source_file_type", "Choose the file type"),
    ({"source__source_file_type": "XLSX"}, "source.source_sheet_name", "Sheet name is required"),
    ({"source__source_type": "POSTGRESQL"}, "source.source_type", "file based source is one of"),
    ({"table__source_table_sql": "SELECT * FROM X"}, "tables.0.source_table_sql", "A table name for the file's rows"),
])
def test_a_file_source_requires_its_answers(patch, path, text):
    assert text in errors(with_(FILE, **patch)).get(path, "")


def test_a_file_source_needs_keys_only_for_scd2_or_merge():
    assert "tables.0.source_primary_keys" not in errors(FILE)
    assert "needs the primary key" in errors(with_(FILE, load__bronze_load_type="MERGE"))["tables.0.source_primary_keys"]
    header, rows, errs = ic.validate(with_(FILE, source__source_file_type="XLSX", source__source_sheet_name="Customers"))
    assert errs == {} and rows[0]["source_sheet_name"] == "Customers"


# ---------------------------------------------------------------- without a kind: as before

def test_without_a_kind_the_old_rules_apply():
    legacy = {**copy.deepcopy(BASE), "source": {"source_name": "crm_any", "source_type": "SALESFORCE"},
              "tables": [{"source_table_sql": "ACCOUNTS", "target_previous_load_time": "2026-10-01 00:00"}]}
    header, rows, errs = ic.validate(legacy)
    assert errs == {} and header["source_kind"] is None
    assert rows[0]["target_previous_load_time"] == "2026-10-01 00:00:00" and rows[0]["api_url"]
    assert "source.source_kind" in errors(with_(legacy, source__source_kind="API"))


# ---------------------------------------------------------------- the AODS rows

def _detail(payload):
    header, rows, errs = ic.validate(payload)
    assert errs == {}
    return sc.to_detail(rows[0], header, 1, {"defaults": sc.DEFAULT_SETTING["defaults"]}, "ANA", "2026-10-23 10:00:00")


def test_aods_rows_follow_the_workbook_and_keep_their_columns():
    db = _detail(DATABASE)
    assert db["SOURCE_FILE_TYPE"] is None and db["SOURCE_DATABASE_PATH"] == "CRMPROD" and db["SOURCE_SCHEMA_FILE"] == "SALES"
    assert db["SOURCE_TABLE_SQL"] == "SALES.CUSTOMERS" and db["SOURCE_PRIMARY_KEYS"] == "CUSTOMER_ID"
    files = _detail(FILE)
    assert files["SOURCE_FILE_TYPE"] == "CSV" and files["SOURCE_DATABASE_PATH"] == "s3://crm/daily/"
    assert files["SOURCE_SCHEMA_FILE"] == "customers_*.csv" and files["SOURCE_SHEET_NAME"] is None
    for row in (db, files):
        assert row["TARGET_PREVIOUS_LOAD_TIME"] is None and row["API_URL"] is None and row["API_KEY"] is None
        assert set(row) == set(sc.DETAIL_COLUMNS) | {"MASTER_CONFIG_ID"}      # the same columns as before
    # a share (no kind) keeps TABLE
    share_header = {**ic.validate(DATABASE)[0], "source_kind": None}
    assert sc.to_detail(ic.validate(DATABASE)[1][0], share_header, 1, {}, "ANA", "t")["SOURCE_FILE_TYPE"] == "TABLE"
