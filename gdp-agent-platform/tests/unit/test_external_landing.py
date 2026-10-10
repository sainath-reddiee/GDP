import json

import pytest

from services.source import external


def test_file_landing_copies_by_header_name_then_swaps_in_sanitized_table():
    sql = external.land_sql("AI", "EXT_CRM", "CSV", "ORDERS", ["o'1.csv", "o2.csv"])
    raw, copy, fresh, fill, ensure, swap = sql[:6]
    # the COPY target keeps the raw header names, so "Order Date" matches by name
    assert raw.startswith('CREATE OR REPLACE TEMPORARY TABLE "AI"."EXT_CRM"."ORDERS__RAW"') and "UPPER(" not in raw
    assert copy.startswith('COPY INTO "AI"."EXT_CRM"."ORDERS__RAW"') and "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE" in copy
    assert "FILES = ('o''1.csv', 'o2.csv')" in copy
    # the published table gets plain identifiers and is filled by position
    assert "UPPER(REGEXP_REPLACE(COLUMN_NAME" in fresh and '"ORDERS__NEW"' in fresh and "o''1.csv" in fresh
    assert fill == 'INSERT INTO "AI"."EXT_CRM"."ORDERS__NEW" SELECT * FROM "AI"."EXT_CRM"."ORDERS__RAW"'
    assert ensure.startswith('CREATE TABLE IF NOT EXISTS "AI"."EXT_CRM"."ORDERS" LIKE')
    assert swap == 'ALTER TABLE "AI"."EXT_CRM"."ORDERS" SWAP WITH "AI"."EXT_CRM"."ORDERS__NEW"'
    assert all(s.startswith("DROP TABLE IF EXISTS") for s in sql[6:])
    # the live table is never replaced or emptied before the rows are loaded
    assert not any(s.startswith("CREATE OR REPLACE TABLE \"AI\".\"EXT_CRM\".\"ORDERS\" ") for s in sql)


def test_oracle_replace_keeps_rows_when_copy_fails():
    sql = external.land_parquet_sql("DB", "EXT_HR", "EMP", "EMP/b2", "replace", exists=True)
    assert not any("TRUNCATE" in s for s in sql)
    assert sql[0] == 'CREATE OR REPLACE TEMPORARY TABLE "DB"."EXT_HR"."EMP__REPLACE" AS SELECT * FROM "DB"."EXT_HR"."EMP" LIMIT 0'
    assert sql[1].startswith('COPY INTO "DB"."EXT_HR"."EMP__REPLACE"')
    assert sql[2] == 'INSERT OVERWRITE INTO "DB"."EXT_HR"."EMP" SELECT * FROM "DB"."EXT_HR"."EMP__REPLACE"'


def test_list_pattern_escapes_backslashes_and_quotes():
    assert external.pattern_clause(None) == ""
    assert external.pattern_clause(r".*\.csv") == r" PATTERN = '.*\\.csv'"
    assert external.pattern_clause("a'b") == " PATTERN = 'a''b'"


class _Session:
    def __init__(self, fail_on=""):
        self.sql_log, self.fail_on = [], fail_on

    def sql(self, text, params=None):
        self.sql_log.append(text)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("boom")
        return self

    def collect(self):
        return []


def test_register_runs_setup_before_the_registry_row(monkeypatch):
    monkeypatch.setattr(external, "rows", lambda s, q, p=None: [{"D": "AI"}] if "CURRENT_DATABASE" in q else [])
    inserted = []
    monkeypatch.setattr(external, "insert_rows", lambda *a, **k: inserted.append(a))
    session = _Session(fail_on="CREATE STAGE")
    with pytest.raises(RuntimeError):
        external.register_external_source(session, json.dumps({"connector": "upload", "source_system_name": "crm"}))
    assert not inserted  # nothing blocks registering the name again
    session = _Session()
    external.register_external_source(session, json.dumps({"connector": "upload", "source_system_name": "crm"}))
    assert len(inserted) == 1 and any("CREATE STAGE" in s for s in session.sql_log)
