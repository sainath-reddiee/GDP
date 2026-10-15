"""SnapLogic ingestion control (V046): the wizard's validation, one master ID with a child row per table, history,
editing and activation, and the governance rules. Snowflake is replaced by a fake that answers by SQL substring."""

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
import app.governance as gov  # noqa: E402
from services.governance import policy  # noqa: E402
from services.source import ingestion_control as ic  # noqa: E402
from test_hub_api import FakeDb  # noqa: E402

MIGRATION = ROOT / "snowflake" / "database" / "migrations" / "V046__ingestion_control_config.sql"

GOOD = {
    "source": {"source_name": "crm_oracle", "source_type": "ORACLE", "source_account": "crm_oracle_prod",
               "source_database_path": "CRMPROD", "source_schema_file": "SALES", "source_file_type": "TABLE"},
    "target": {"landing_database": "dev_landing", "landing_schema": "landing", "bronze_schema": "bronze"},
    "load": {"load_type": "APPEND", "bronze_load_type": "MERGE", "load_frequency": "DAILY", "job_trigger_time": "0 2 * * * UTC"},
    "snaplogic": {"snaplogic_project_name": "GDP/Ingestion", "parent_pipeline_name": "P_INGEST", "child_pipeline_name": "C_ORACLE",
                  "api_url": "https://elastic.snaplogic.com/api/1/rest/slsched/feed/x", "api_key": "vault:snaplogic/ingest"},
    "tables": [{"source_table_sql": "SALES.CUSTOMERS", "source_primary_keys": "customer_id", "source_delta_key": "updated_at"},
               {"source_table_sql": "SELECT ID, NAME FROM SALES.ORDERS WHERE NOTE <> 'a;b'", "source_primary_keys": "id",
                "deletes": True, "source_delete_key": "is_deleted", "source_delete_key_value": "Y"}],
    "comments": "first load",
}


def good(**patch):
    """GOOD with block__key=value changes, e.g. good(source__source_file_type="XLSX")."""
    out = copy.deepcopy(GOOD)
    for path, value in patch.items():
        block, key = path.split("__", 1)
        out[block][key] = value
    return out


# ---------------------------------------------------------------- migration


def test_migration_has_every_column_and_the_history():
    sql = MIGRATION.read_text()
    for col in ("MASTER_ID", "CHILD_ID", "PARENT_PIPELINE_NAME", "CHILD_PIPELINE_NAME", "SNAPLOGIC_PROJECT_NAME", "SOURCE_ACCOUNT",
                "SOURCE_DATABASE_PATH", "SOURCE_SCHEMA_FILE", "SOURCE_TABLE_SQL", "SOURCE_FILE_TYPE", "SOURCE_SHEET_NAME",
                "SOURCE_DELTA_KEY", "SOURCE_PRIMARY_KEYS", "SOURCE_WHERE_CLAUSE", "SOURCE_AUDIT_COLUMNS", "TARGET_ACCOUNT",
                "LANDING_DATABASE", "LANDING_SCHEMA", "LANDING_TABLE", "BRONZE_SCHEMA", "BRONZE_TABLE", "EXTERNAL_STAGE",
                "EXTERNAL_VOLUME", "BASE_LOCATION", "CONFIG_TABLE", "LOAD_TYPE", "BRONZE_LOAD_TYPE", "LOAD_FREQUENCY",
                "JOB_TRIGGER_TIME", "TARGET_PREVIOUS_LOAD_TIME", "LAST_RUN_TIMESTAMP", "LANDING_TO_BRONZE_FLAG", "IS_ACTIVE",
                "COMPLETION_FLAG", "SOURCE_DELETE_ACTION", "SOURCE_DELETE_KEY", "SOURCE_DELETE_KEY_VALUE", "SOURCE_DELETE_QUERY",
                "API_URL", "API_KEY", "CREATED_BY", "CREATED_AT", "UPDATED_AT", "COMMENTS"):
        assert f"\n    {col} " in sql, col
    assert "PRIMARY KEY (MASTER_ID, CHILD_ID)" in sql and "SEQ_INGESTION_MASTER_ID.NEXTVAL" in sql
    assert "INGESTION_CONTROL_HISTORY" in sql and "CREATE TABLE IF NOT EXISTS" in sql
    # every column the wizard writes is in the table; the rest are ids, audit columns or set by SnapLogic
    assert {c.upper() for c in ic.COLUMNS} <= {line.split()[0] for line in sql.splitlines() if line.startswith("    ") and line.split()}


# ---------------------------------------------------------------- validation


def test_a_good_onboarding_yields_one_row_per_table():
    header, rows, errors = ic.validate(GOOD)
    assert not errors, errors
    assert header["source_name"] == "CRM_ORACLE" and header["landing_database"] == "DEV_LANDING"
    assert [r["landing_table"] for r in rows] == ["CRM_ORACLE__CUSTOMERS", "CRM_ORACLE__ORDERS"]
    assert rows[0]["bronze_table"] == "CRM_ORACLE__CUSTOMERS" and rows[0]["source_primary_keys"] == "CUSTOMER_ID"
    assert rows[0]["source_delete_action"] == "NOT_REQUIRED" and rows[0]["source_delete_key"] is None
    assert rows[1]["source_delete_action"] == "REQUIRED" and rows[1]["source_delete_key"] == "IS_DELETED"
    assert rows[0]["source_sheet_name"] is None and rows[0]["is_active"] and rows[0]["landing_to_bronze_flag"]
    assert set(rows[0]) == set(ic.COLUMNS)


@pytest.mark.parametrize("patch,path", [
    ({"source__source_file_type": "XLSX"}, "source.source_sheet_name"),
    ({"source__source_name": "1bad"}, "source.source_name"),
    ({"target__landing_database": ""}, "target.landing_database"),
    ({"target__iceberg": True}, "target.external_volume"),
    ({"load__job_trigger_time": "every day"}, "load.job_trigger_time"),
    ({"load__load_type": "UPSERT"}, "load.load_type"),
    ({"snaplogic__api_url": "http://plain"}, "snaplogic.api_url"),
    ({"snaplogic__api_key": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"}, "snaplogic.api_key"),
    ({"snaplogic__api_key": "a8F3kL9qZ2xW7vB4nM6pR1tY5uI0oE3s"}, "snaplogic.api_key"),
    ({"snaplogic__parent_pipeline_name": ""}, "snaplogic.parent_pipeline_name"),
])
def test_validation_errors_are_keyed_by_step_and_field(patch, path):
    _, _, errors = ic.validate(good(**patch))
    assert path in errors, errors


def test_sheet_name_only_for_excel_and_on_demand_needs_no_cron():
    _, rows, errors = ic.validate(good(source__source_file_type="XLSX", source__source_sheet_name="Customers"))
    assert not errors and rows[0]["source_sheet_name"] == "Customers"
    _, rows, _ = ic.validate(good(source__source_sheet_name="Ignored"))
    assert rows[0]["source_sheet_name"] is None
    _, rows, errors = ic.validate(good(load__load_frequency="ON_DEMAND", load__job_trigger_time=""))
    assert not errors and rows[0]["job_trigger_time"] is None


def test_table_rules():
    bad = copy.deepcopy(GOOD)
    bad["tables"] = [{"source_table_sql": "SALES.CUSTOMERS; DROP TABLE X", "source_primary_keys": "id"},
                     {"source_table_sql": "DELETE FROM SALES.ORDERS", "source_primary_keys": "id"},
                     {"source_table_sql": "SALES.ITEMS", "source_primary_keys": "a b"},
                     {"source_table_sql": "SALES.ITEMS", "source_primary_keys": "id", "landing_table": "CRM_ORACLE__ITEMS"},
                     {"source_table_sql": "SALES.X", "deletes": True},
                     {"source_table_sql": "SALES.Y", "source_where_clause": "1=1; DELETE FROM X", "source_primary_keys": "id"}]
    _, _, e = ic.validate(bad)
    assert "One table name or one query" in e["tables.0.source_table_sql"]
    assert "read-only" in e["tables.1.source_table_sql"]
    assert "tables.2.source_primary_keys" in e
    assert "already loads CRM_ORACLE__ITEMS" in e["tables.3.landing_table"]
    assert "MERGE needs the primary key" in e["tables.4.source_primary_keys"] and "tables.4.source_delete_key" in e
    assert "tables.5.source_where_clause" in e
    empty = copy.deepcopy(GOOD)
    empty["tables"] = []
    assert ic.validate(empty)[2]["tables"] == "Add at least one table"


def test_starting_values_for_snaplogic_columns():
    payload = good(load__last_run_timestamp="2026-10-01T02:00", load__completion_flag=True)
    payload["tables"][0]["target_previous_load_time"] = "2026-09-30"
    _, rows, errors = ic.validate(payload)
    assert not errors
    assert rows[0]["target_previous_load_time"] == "2026-09-30 00:00:00" and rows[1]["target_previous_load_time"] is None
    assert rows[0]["last_run_timestamp"] == "2026-10-01 02:00:00" and rows[0]["completion_flag"] is True
    bad = good(load__last_run_timestamp="yesterday")
    bad["tables"][0]["target_previous_load_time"] = "soon"
    _, _, errors = ic.validate(bad)
    assert "load.last_run_timestamp" in errors and "tables.0.target_previous_load_time" in errors
    assert ic.validate(GOOD)[1][0]["completion_flag"] is False


def test_secret_references_pass_and_tokens_do_not():
    for ref in ("vault:snaplogic/ingest", "SNAPLOGIC_INGEST_TOKEN", "kv/prod/snaplogic.key"):
        assert not ic.looks_like_token(ref), ref
    for token in ("ghp_x1y2z3", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0", "sk-abc", "a8F3kL9qZ2xW7vB4nM6pR1tY5uI0oE3s"):
        assert ic.looks_like_token(token), token


def test_insert_binds_one_master_id_for_every_row():
    header, rows, _ = ic.validate(GOOD)
    sql = ic.insert_sql(len(rows))
    params = ic.insert_params(42, "src-1", header, rows, "ANA")
    width = 3 + len(ic.COLUMNS) + 3
    assert sql.count("(%s") == 2 and len(params) == 2 * width
    assert params[0] == 42 and params[width] == 42 and params[1] == "src-1"
    assert "CHILD_ID" not in sql.split("VALUES")[0]  # from the sequence default


def test_edit_row_and_actions():
    _, rows, _ = ic.validate(GOOD)
    current = {**rows[0], "comments": None}
    row, errors = ic.edit_row(current, {"load_type": "TRUNCATE", "comments": "nightly"})
    assert not errors and row["load_type"] == "TRUNCATE" and row["comments"] == "nightly"
    _, errors = ic.edit_row(current, {"landing_table": "OTHER"})
    assert "landing_table" in errors
    _, errors = ic.edit_row(current, {"job_trigger_time": "soon"})
    assert "job_trigger_time" in errors
    off, _ = ic.edit_row(current, {"is_active": False})
    assert ic.action_for(current, off) == "DEACTIVATED" and ic.action_for(off, current) == "ACTIVATED"
    assert ic.action_for(current, row) == "UPDATED"


# ---------------------------------------------------------------- API


@pytest.fixture
def client(monkeypatch):
    async def open_door(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(gov, "middleware", open_door)
    holder = {"db": FakeDb()}
    main.app.dependency_overrides[main.current_db] = lambda: holder["db"]
    yield TestClient(main.app), holder
    main.app.dependency_overrides.clear()


def test_create_writes_catalog_control_rows_and_history_in_order(client):
    c, holder = client
    holder["db"] = FakeDb({"CURRENT_USER() AS U": [{"u": "ANA"}], "SEQ_INGESTION_MASTER_ID.NEXTVAL": [{"n": 7}],
                           "SELECT CHILD_ID, LANDING_TABLE": [{"child_id": 11, "landing_table": "CRM_ORACLE__CUSTOMERS"},
                                                              {"child_id": 12, "landing_table": "CRM_ORACLE__ORDERS"}]})
    r = c.post("/api/hub/ingestion-control", json=GOOD)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["master_id"] == 7 and [x["child_id"] for x in out["children"]] == [11, 12]
    sqls = [" ".join(s.split()) for s, _ in holder["db"].executed]
    order = [next(i for i, s in enumerate(sqls) if needle in s) for needle in
             ("BEGIN", "INSERT INTO CORE.INGESTION_PIPELINE_CATALOG", "SEQ_INGESTION_MASTER_ID.NEXTVAL",
              "INSERT INTO CORE.INGESTION_CONTROL_CONFIG", "INGESTION_CONTROL_HISTORY", "COMMIT")]
    assert order == sorted(order)
    assert sum("INSERT INTO CORE.INGESTION_CONTROL_HISTORY" in s for s in sqls) == 2
    insert = next(p for s, p in holder["db"].executed if "INSERT INTO CORE.INGESTION_CONTROL_CONFIG" in s)
    assert insert[0] == 7 and "vault:snaplogic/ingest" in insert


def test_create_rejects_bad_answers_clashes_and_missing_table(client):
    c, holder = client
    r = c.post("/api/hub/ingestion-control", json=good(source__source_file_type="XLSX"))
    assert r.status_code == 422 and "source.source_sheet_name" in r.json()["detail"]["errors"]
    holder["db"] = FakeDb({"SELECT LANDING_TABLE FROM": [{"landing_table": "CRM_ORACLE__ORDERS"}]})
    r = c.post("/api/hub/ingestion-control", json=GOOD)
    assert r.status_code == 409 and "CRM_ORACLE__ORDERS" in r.json()["detail"]
    holder["db"] = FakeDb(fail=("INGESTION_CONTROL_CONFIG",))
    assert c.post("/api/hub/ingestion-control", json=GOOD).status_code == 409


def test_list_detail_and_edit(client):
    c, holder = client
    _, rows, _ = ic.validate(GOOD)
    stored = {**rows[0], "master_id": 7, "child_id": 11, "source_id": "s", "domain_id": None, "target_previous_load_time": None,
              "last_run_timestamp": None, "completion_flag": False, "created_by": "ANA", "updated_by": None, "comments": None,
              "created_at_text": "2026-10-11 10:00:00.000", "updated_at_text": None, "last_run_text": None, "source_name": "CRM_ORACLE"}
    holder["db"] = FakeDb({"FROM CORE.INGESTION_CONTROL_HISTORY": [{"child_id": 11, "action": "CREATED", "is_active": True,
                                                                              "changed_by": "ANA", "changed_at": "2026-10-11 10:00"}],
                           "LEFT JOIN CORE.INGESTION_PIPELINE_CATALOG P": [stored],
                           "SELECT * FROM CORE.INGESTION_CONTROL_CONFIG": [stored]})
    listed = c.get("/api/hub/ingestion-control?q=crm&active=true").json()
    assert listed["deployed"] and listed["controls"][0]["master_id"] == 7 and listed["controls"][0]["created_at"].startswith("2026-10-11")
    detail = c.get("/api/hub/ingestion-control/7").json()
    assert detail["history"][0]["action"] == "CREATED"
    r = c.patch("/api/hub/ingestion-control/7/11", json={"changes": {"is_active": False}})
    assert r.status_code == 200 and r.json()["action"] == "DEACTIVATED"
    update = next(s for s, _ in holder["db"].executed if s.lstrip().startswith("UPDATE CORE.INGESTION_CONTROL_CONFIG"))
    assert "IS_ACTIVE = %s" in update and "UPDATED_BY = CURRENT_USER()" in update
    assert any("INGESTION_CONTROL_HISTORY" in s for s, _ in holder["db"].executed)
    assert c.patch("/api/hub/ingestion-control/7/11", json={"changes": {"landing_table": "X"}}).status_code == 422
    holder["db"] = FakeDb(fail=("INGESTION_CONTROL_CONFIG",))
    assert c.get("/api/hub/ingestion-control").json() == {"controls": [], "deployed": False}


def test_governance_rules():
    assert policy.privilege_for("POST", "/api/hub/ingestion-control")[0] == "SOURCE.CONNECT"
    assert policy.privilege_for("PATCH", "/api/hub/ingestion-control/3/9")[0] == "SOURCE.CONNECT"
    assert policy.privilege_for("POST", "/api/hub/ingestion-control/validate")[0] is None
    assert not policy.carries_secret("POST", "/api/hub/ingestion-control")  # a secret reference only
