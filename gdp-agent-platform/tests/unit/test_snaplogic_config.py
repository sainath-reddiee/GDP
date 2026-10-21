"""SnapLogic config sync (V070, V071): onboarding writes SnapLogic's config tables, CORE.AODS_SNAPLOGIC_CONFIG_MASTER (one
row per source) and CORE.AODS_SNAPLOGIC_CONFIG_DETAIL (one row per table), in the platform's own schema, following the
live GDP_* conventions, in the same transaction as the other CORE rows, only when the SNAPLOGIC_SYNC setting is on.
Nothing is written to another database. Snowflake is a fake that answers by SQL substring."""

import copy
import json
import re
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
from app import hub_sources_api as hub  # noqa: E402
from services.governance import policy  # noqa: E402
from services.common import platform_config  # noqa: E402
from services.source import ingestion_control as ic  # noqa: E402
from services.source import snaplogic_config as sc  # noqa: E402
from services.source import snaplogic_sync as ss  # noqa: E402
from test_hub_api import FakeDb  # noqa: E402
from test_ingestion_control import GOOD, good  # noqa: E402

# SnapLogic's config DDL, in order (AODS_SNAPLOGIC_CONFIG_MASTER / _DETAIL)
LIVE_MASTER = ["MASTER_CONFIG_ID", "SOURCE_SYSTEM", "SOURCE_TYPE", "ACTIVE_FLAG", "CREATED_AT", "SCHEDULE_CRON", "SOURCE_SYSTEM_DESC",
               "DOMAIN", "AIRFLOW_SOURCE_SYSTEM", "SCHEDULE_FREQUENCY", "SILVER_COMMAND_NAME"]
LIVE_DETAIL = [
    "CHILD_CONFIG_ID", "MASTER_CONFIG_ID", "PARENT_PIPELINE_NAME", "CHILD_PIPELINE_NAME", "SNAPLOGIC_PROJECT_NAME", "SOURCE_ACCOUNT",
    "SOURCE_DATABASE_PATH", "SOURCE_SCHEMA_FILE", "SOURCE_TABLE_SQL", "SOURCE_FILE_TYPE", "SOURCE_DELTA_KEY", "SOURCE_PRIMARY_KEYS",
    "SOURCE_WHERE_CLAUSE", "TARGET_ACCOUNT", "LANDING_DATABASE", "LANDING_SCHEMA", "LANDING_TABLE", "BRONZE_DATABASE",
    "BRONZE_SCHEMA", "BRONZE_TABLE", "TARGET_PREVIOUS_LOAD_TIME", "LOAD_TYPE", "LOAD_FREQUENCY", "JOB_TRIGGER_TIME", "API_URL",
    "API_KEY", "EXTERNAL_STAGE", "LAST_RUN_TIMESTAMP", "IS_ACTIVE", "COMPLETION_FLAG", "LANDING_TO_BRONZE_FLAG", "CREATED_BY",
    "CREATED_AT", "UPDATED_AT", "COMMENTS", "SOURCE_AUDIT_COLUMNS", "BRONZE_LOAD_TYPE", "SOURCE_DELETE_ACTION", "SOURCE_DELETE_KEY",
    "SOURCE_DELETE_KEY_VALUE", "SOURCE_DELETE_QUERY", "SOURCE_SHEET_NAME", "CONFIG_TABLE", "EXTERNAL_VOLUME", "BASE_LOCATION"]
MASTER_T = "CORE.AODS_SNAPLOGIC_CONFIG_MASTER"
DETAIL_T = "CORE.AODS_SNAPLOGIC_CONFIG_DETAIL"
DB = main.DATABASE if hasattr(main, "DATABASE") else "DEV_AI_PLATFORM"
TABLES = {"master": f"{DB}.{MASTER_T}", "detail": f"{DB}.{DETAIL_T}"}
ON = {**sc.DEFAULT_SETTING, "enabled": True, "database": DB}


def _setting(value):
    return {"FROM CORE.PLATFORM_CONFIG": [{"config_value": json.dumps(value)}]}


# ---------------------------------------------------------------- the rows


def test_the_columns_are_the_live_tables_without_their_identity_columns():
    assert len(LIVE_MASTER) == 11 and len(LIVE_DETAIL) == 45
    assert list(sc.MASTER_COLUMNS) == LIVE_MASTER[1:] and list(sc.DETAIL_COLUMNS) == LIVE_DETAIL[1:]
    assert sc.insert_master_sql(MASTER_T).count("%s") == 10 and sc.insert_detail_sql(DETAIL_T, 2).count("%s") == 88
    assert "UNION ALL" in sc.insert_detail_sql(DETAIL_T, 2) and "CREATED_AT" not in sc.MASTER_COLUMNS[:3]


def test_a_share_row_follows_the_live_conventions():
    """The PDL_TAG share (UAT_C360_DB.EXT) as SnapLogic's live rows record a share table."""
    body = hub.ShareOnboard(database="UAT_C360_DB", schema="EXT", tables=["PDL_TAG"], source_system_name="PDL_TEST",
                            snaplogic={"bronze_schema": "PDL", "bronze_database": "DEV_GDP_BRONZE_DB", "primary_keys": {"PDL_TAG": "ID"},
                                       "schedule_cron": "0 6 * * *"})
    payload = hub.share_control_payload(body, "UAT_C360_DB", "EXT", [{"object": "PDL_TAG", "landing_table": "DEV_LANDING.LANDING.PDL_TEST__PDL_TAG"}])
    header, rows, errors = ic.validate(payload)
    assert errors == {}
    stamp = sc.created_at()
    master = sc.to_master(header, ON, stamp)
    detail = sc.to_detail(rows[0], header, 31, ON, "ANA", stamp)
    assert master | {"CREATED_AT": None} == {"SOURCE_SYSTEM": "PDL_TEST", "SOURCE_TYPE": "SNOWFLAKE_SHARE", "ACTIVE_FLAG": False,
                                             "CREATED_AT": None, "SCHEDULE_CRON": "0 6 * * *", "SOURCE_SYSTEM_DESC": None, "DOMAIN": None,
                                             "AIRFLOW_SOURCE_SYSTEM": None, "SCHEDULE_FREQUENCY": "DAILY", "SILVER_COMMAND_NAME": None}
    expected = {
        "MASTER_CONFIG_ID": 31, "PARENT_PIPELINE_NAME": "GDP_config_driven_framework_parent",
        "CHILD_PIPELINE_NAME": "z_reusable_SnowflakeShare_Snowflake_load", "SNAPLOGIC_PROJECT_NAME": "GLOBAL_DATA_PLATFORM/Global_Data_Ingestion",
        "SOURCE_DATABASE_PATH": "UAT_C360_DB", "SOURCE_SCHEMA_FILE": "EXT", "SOURCE_TABLE_SQL": "PDL_TAG", "SOURCE_FILE_TYPE": "TABLE",
        "SOURCE_PRIMARY_KEYS": "ID", "TARGET_ACCOUNT": "GDP_Integration/Snowflake_GDP_Dev", "LANDING_DATABASE": "DEV_LANDING",
        "LANDING_SCHEMA": "LANDING", "LANDING_TABLE": "PDL_TEST__PDL_TAG", "BRONZE_DATABASE": "DEV_GDP_BRONZE_DB", "BRONZE_SCHEMA": "PDL",
        "BRONZE_TABLE": "PDL_TEST__PDL_TAG", "LOAD_TYPE": "APPEND", "LOAD_FREQUENCY": "DAILY", "JOB_TRIGGER_TIME": None,
        "IS_ACTIVE": False, "COMPLETION_FLAG": False, "LANDING_TO_BRONZE_FLAG": True, "BRONZE_LOAD_TYPE": "SCD2",
        "SOURCE_DELETE_ACTION": "NOT_REQUIRED", "CONFIG_TABLE": TABLES["detail"], "EXTERNAL_VOLUME": "gdp_sf_dev_bronze",
        "BASE_LOCATION": "gdp_sf_dev_bronze/", "CREATED_BY": "ANA",
    }
    assert {k: detail[k] for k in expected} == expected


@pytest.mark.parametrize("source_type, child", [
    ("ORACLE", "z_reusable_SnowflakeShare_Snowflake_load"), ("SHAREPOINT", "z_reusable_Sharepoint_Snowflake_load"),
    ("S3", "z_reusable_s3_snowflake_load"), ("POSTGRES", "z_reusable_postgres_snowflake_load"), ("DNB", "z_dnb_load"),
])
def test_the_child_pipeline_follows_the_source_type_unless_one_is_named(source_type, child):
    assert sc.child_pipeline(source_type, ON) == child
    assert sc.child_pipeline(source_type, ON, "my_child") == "my_child"


def test_the_cron_is_on_the_master_row_and_postgres_is_written_as_postgresql():
    header, rows, _ = ic.validate(good(source__source_type="POSTGRES"))
    assert sc.to_master(header, ON, "s")["SCHEDULE_CRON"] == "0 2 * * * UTC"
    assert sc.to_master(header, ON, "s")["SOURCE_TYPE"] == "POSTGRESQL"
    assert sc.to_detail(rows[0], header, 1, ON, "ANA", "s")["JOB_TRIGGER_TIME"] is None
    assert sc.domain_label("COMPANY") == "Company" and sc.domain_label("real_estate") == "Real Estate"


def test_the_setting_is_off_by_default_and_the_tables_are_always_cores():
    assert platform_config.DEFAULTS["SNAPLOGIC_SYNC"]["enabled"] is False and "SNAPLOGIC_SYNC" in platform_config.ADMIN_SETTINGS
    assert sc.tables() == {"master": MASTER_T, "detail": DETAIL_T, "master_fqn": MASTER_T, "detail_fqn": DETAIL_T}
    # a table_prefix left in an older value (V070's seed) is ignored: the tables are not a setting
    value, problems = sc.validate_setting({"enabled": True, "table_prefix": "DEV_GDP_UTIL_DB.CONFIG.AODS_"})
    assert problems == [] and "table_prefix" not in value and value["enabled"] is True
    assert ss.setting(FakeDb())["enabled"] is False and ss.setting(FakeDb(_setting(ON)), DB)["enabled"] is True
    assert sc.tables(ss.setting(FakeDb(_setting(ON)), DB))["detail_fqn"] == TABLES["detail"]


def test_validation_additions_are_backward_compatible():
    header, rows, errors = ic.validate(GOOD)
    assert errors == {} and header["schedule_cron"] == "0 2 * * * UTC" and header["bronze_database"] is None
    assert set(rows[0]) == set(ic.COLUMNS)              # the CORE row is unchanged
    for kind in ("SNOWFLAKE_SHARE", "SNOWFLAKE", "POSTGRESQL", "SHAREPOINT", "S3_DEALIQ", "DNB", "ZOOMINFO_API", "POSTGRES"):
        assert ic.validate(good(source__source_type=kind))[2] == {}, kind
    assert ic.validate(good(target__target_account="GDP_Integration/Snowflake_GDP_Dev"))[2] == {}
    _, _, errors = ic.validate(good(source__source_account="Smartsheet/abc123XYZ987def456GHI789jkl012MNOpqr"))
    assert "token" in errors["source.source_account"]
    _, _, errors = ic.validate(good(load__schedule_cron="soon"))
    assert "load.schedule_cron" in errors


# ---------------------------------------------------------------- the writer


def _sync_db(existing_master=None, clash=None, read_back=None, extra=None):
    answers = {**_setting(ON), "CURRENT_USER() AS U": [{"u": "ANA"}], "SEQ_INGESTION_MASTER_ID.NEXTVAL": [{"n": 7}],
               "SELECT CHILD_ID, LANDING_TABLE": [{"child_id": 11, "landing_table": "CRM_ORACLE__CUSTOMERS"},
                                                  {"child_id": 12, "landing_table": "CRM_ORACLE__ORDERS"}],
               "CONFIG_DETAIL WHERE IS_ACTIVE": clash or [],
               "WHERE UPPER(SOURCE_SYSTEM)": existing_master or [],
               "WHERE SOURCE_SYSTEM = %s AND CREATED_AT": [{"master_config_id": 31}],
               "WHERE MASTER_CONFIG_ID = %s AND CREATED_AT": read_back if read_back is not None else
               [{"child_config_id": 501, "landing_table": "CRM_ORACLE__CUSTOMERS"}, {"child_config_id": 502, "landing_table": "CRM_ORACLE__ORDERS"}],
               **(extra or {})}
    return FakeDb(answers)


@pytest.fixture
def client(monkeypatch):
    async def open_door(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(gov, "middleware", open_door)
    holder = {"db": FakeDb()}
    main.app.dependency_overrides[main.current_db] = lambda: holder["db"]
    yield TestClient(main.app), holder
    main.app.dependency_overrides.clear()


def _sqls(db):
    return [" ".join(s.split()) for s, _ in db.executed]


def test_with_the_sync_on_core_and_snaplogic_rows_are_written_in_one_transaction(client):
    c, holder = client
    holder["db"] = _sync_db()
    r = c.post("/api/hub/ingestion-control", json=GOOD)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["snaplogic"] == {"master_config_id": 31, "master_created": True, "child_config_ids": [501, 502], "tables": TABLES}
    sqls = _sqls(holder["db"])
    order = [next(i for i, s in enumerate(sqls) if needle in s) for needle in (
        "BEGIN", "INSERT INTO CORE.INGESTION_PIPELINE_CATALOG", "INSERT INTO CORE.INGESTION_CONTROL_CONFIG",
        f"INSERT INTO {MASTER_T}", f"INSERT INTO {DETAIL_T}", "UPDATE CORE.INGESTION_PIPELINE_CATALOG SET SCHEDULE_CRON",
        "UPDATE CORE.INGESTION_CONTROL_CONFIG SET BRONZE_DATABASE", "INSERT INTO CORE.INGESTION_CONTROL_HISTORY", "COMMIT",
        "INSERT INTO CORE.SNAPLOGIC_SYNC_LOG")]
    assert order == sorted(order)
    detail = next(p for s, p in holder["db"].executed if s.startswith(f"INSERT INTO {DETAIL_T}"))
    assert len(detail) == 88 and detail[0] == 31
    assert not any("GDP_SNAPLOGIC" in s or "DEV_GDP_UTIL_DB" in s for s in sqls)   # only the platform's own CORE
    links = [p for s, p in holder["db"].executed if "SET BRONZE_DATABASE" in s]
    assert [p[1] for p in links] == [501, 502] and all(p[2] == 7 for p in links)


def test_with_the_sync_off_nothing_but_the_core_rows_is_written(client):
    c, holder = client
    holder["db"] = _sync_db(extra=_setting(sc.DEFAULT_SETTING))
    holder["db"].answers.update(_setting(sc.DEFAULT_SETTING))
    r = c.post("/api/hub/ingestion-control", json=GOOD)
    assert r.status_code == 200 and "snaplogic" not in r.json()
    assert not any("AODS_" in s or "SNAPLOGIC_SYNC_LOG" in s or "SET BRONZE_DATABASE" in s for s in _sqls(holder["db"]))


def test_a_source_keeps_one_master_row_and_a_clash_or_a_short_read_back_rolls_back(client):
    c, holder = client
    holder["db"] = _sync_db(existing_master=[{"master_config_id": 9}])
    r = c.post("/api/hub/ingestion-control", json=GOOD)
    assert r.json()["snaplogic"]["master_config_id"] == 9 and r.json()["snaplogic"]["master_created"] is False
    assert not any(s.startswith(f"INSERT INTO {MASTER_T}") for s in _sqls(holder["db"]))

    holder["db"] = _sync_db(clash=[{"landing_database": "DEV_LANDING", "landing_schema": "LANDING", "landing_table": "CRM_ORACLE__ORDERS"}])
    r = c.post("/api/hub/ingestion-control", json=GOOD)
    assert r.status_code == 409 and "CRM_ORACLE__ORDERS" in r.json()["detail"]
    assert "ROLLBACK" in _sqls(holder["db"]) and "COMMIT" not in _sqls(holder["db"])

    holder["db"] = _sync_db(read_back=[{"child_config_id": 501, "landing_table": "CRM_ORACLE__CUSTOMERS"}])
    r = c.post("/api/hub/ingestion-control", json=GOOD)
    assert r.status_code >= 400 and "ROLLBACK" in _sqls(holder["db"])
    failed = next(p for s, p in holder["db"].executed if "INSERT INTO CORE.SNAPLOGIC_SYNC_LOG" in s)
    assert failed[1:3] == ("EXTERNAL", "FAILED")


def test_an_edit_follows_its_snaplogic_row_or_is_skipped_when_there_is_none(client):
    c, holder = client
    _, rows, _ = ic.validate(GOOD)
    linked = {**rows[0], "master_id": 7, "child_id": 11, "snaplogic_child_config_id": 501, "comments": None}
    holder["db"] = FakeDb({**_setting(ON), "SELECT * FROM CORE.INGESTION_CONTROL_CONFIG": [linked]})
    r = c.patch("/api/hub/ingestion-control/7/11", json={"changes": {"is_active": False}})
    assert r.status_code == 200 and r.json()["snaplogic"] == {"child_config_id": 501, "table": TABLES["detail"]}
    update = next((s, p) for s, p in holder["db"].executed if s.startswith(f"UPDATE {DETAIL_T}"))
    assert "IS_ACTIVE = %s" in update[0] and update[1][-1] == 501 and False in update[1]
    holder["db"] = FakeDb({**_setting(ON), "SELECT * FROM CORE.INGESTION_CONTROL_CONFIG": [{**linked, "snaplogic_child_config_id": None}]})
    r = c.patch("/api/hub/ingestion-control/7/11", json={"changes": {"is_active": False}})
    assert r.status_code == 200 and "before the SnapLogic sync was on" in r.json()["snaplogic"]["skipped"]
    assert not any(s.startswith(f"UPDATE {DETAIL_T}") for s, _ in holder["db"].executed)


def test_a_share_writes_its_snaplogic_rows_after_landing_and_keeps_a_retry_when_that_fails(client, monkeypatch):
    c, holder = client
    landed = {"tables": [{"object": "PDL_TAG", "status": "COMPLETE", "landing_table": "DEV_LANDING.LANDING.PDL_TEST__PDL_TAG"}]}
    monkeypatch.setattr(hub, "_share_tables", lambda db, d, s: [{"table_name": "PDL_TAG"}])
    monkeypatch.setattr(hub, "_source_call", lambda db, proc, handler, *args: landed if "LANDING" in proc else {"passed": True})
    body = {"database": "UAT_C360_DB", "schema": "EXT", "tables": ["PDL_TAG"], "source_system_name": "PDL_TEST"}
    # off: the share onboarding is exactly as before
    holder["db"] = FakeDb({"CORE.CREATE_RUN": {"run_id": "r1"}})
    r = c.post("/api/hub/sources/share/onboard", json=body)
    assert r.json()["ok"] and "snaplogic" not in r.json()
    # on, with answers: CORE and SnapLogic rows after the landing
    holder["db"] = _sync_db(extra={"CORE.CREATE_RUN": {"run_id": "r1"},
                                   "SELECT CHILD_ID, LANDING_TABLE": [{"child_id": 11, "landing_table": "PDL_TEST__PDL_TAG"}],
                                   "WHERE MASTER_CONFIG_ID = %s AND CREATED_AT": [{"child_config_id": 601, "landing_table": "PDL_TEST__PDL_TAG"}]})
    r = c.post("/api/hub/sources/share/onboard", json={**body, "snaplogic": {"bronze_schema": "PDL", "primary_keys": {"PDL_TAG": "ID"}, "schedule_cron": "0 6 * * *"}})
    assert r.json()["snaplogic"]["ok"] and r.json()["snaplogic"]["child_config_ids"] == [601]
    # on, but the SCD2 Bronze load has no keys: the landing stands, the failure is kept with a retry
    holder["db"] = _sync_db(extra={"CORE.CREATE_RUN": {"run_id": "r1"}})
    r = c.post("/api/hub/sources/share/onboard", json={**body, "snaplogic": {"bronze_schema": "PDL", "schedule_cron": "0 6 * * *"}})
    out = r.json()
    assert out["ok"] and out["snaplogic"]["ok"] is False and "retry" in out["snaplogic"]
    logged = next(p for s, p in holder["db"].executed if "INSERT INTO CORE.SNAPLOGIC_SYNC_LOG" in s)
    assert logged[1:3] == ("SHARE", "FAILED") and json.loads(logged[9])["source"]["source_type"] == "SNOWFLAKE_SHARE"


def test_a_failed_share_sync_is_retried_with_corrected_keys(client):
    c, holder = client
    request = hub.share_control_payload(hub.ShareOnboard(database="UAT_C360_DB", schema="EXT", tables=["PDL_TAG"], source_system_name="PDL_TEST",
                                                         snaplogic={"bronze_schema": "PDL", "schedule_cron": "0 6 * * *"}), "UAT_C360_DB", "EXT",
                                        [{"object": "PDL_TAG", "landing_table": "DEV_LANDING.LANDING.PDL_TEST__PDL_TAG"}])
    holder["db"] = _sync_db(extra={"FROM CORE.SNAPLOGIC_SYNC_LOG": [{"sync_id": "s1", "kind": "SHARE", "status": "FAILED", "run_id": "r1",
                                                                     "request": request}],
                                   "SELECT CHILD_ID, LANDING_TABLE": [{"child_id": 11, "landing_table": "PDL_TEST__PDL_TAG"}],
                                   "WHERE MASTER_CONFIG_ID = %s AND CREATED_AT": [{"child_config_id": 601, "landing_table": "PDL_TEST__PDL_TAG"}]})
    assert c.post("/api/hub/snaplogic/sync/s1/retry", json={"changes": {}}).status_code == 422   # still no keys
    r = c.post("/api/hub/snaplogic/sync/s1/retry", json={"changes": {"primary_keys": {"PDL_TAG": "ID"}}})
    assert r.status_code == 200, r.text and r.json()["snaplogic"]["child_config_ids"] == [601]
    assert any("SET STATUS = 'RETRIED'" in s for s in _sqls(holder["db"]))


def test_previews_write_nothing(client):
    c, holder = client
    holder["db"] = _sync_db()
    r = c.post("/api/hub/snaplogic/preview", json=GOOD).json()
    assert r["valid"] and r["master"]["SOURCE_SYSTEM"] == "CRM_ORACLE" and len(r["details"]) == 2 and r["details"][0]["MASTER_CONFIG_ID"] is None
    r = c.post("/api/hub/snaplogic/preview-share", json={"database": "UAT_C360_DB", "schema": "EXT", "tables": ["PDL_TAG"],
                                                         "source_system_name": "PDL_TEST", "landing_database": "DEV_LANDING",
                                                         "landing_schema": "LANDING", "snaplogic": {"bronze_schema": "PDL", "bronze_load_type": "TRUNCATE",
                                                                                                     "schedule_cron": "0 6 * * *"}}).json()
    assert r["valid"], r["errors"]
    assert r["details"][0]["LANDING_TABLE"] == "PDL_TEST__PDL_TAG" and r["details"][0]["SOURCE_DATABASE_PATH"] == "UAT_C360_DB"
    assert not any(s.startswith(("INSERT", "UPDATE", "BEGIN")) for s in _sqls(holder["db"]))
    status = c.get("/api/hub/snaplogic/status").json()
    assert status["enabled"] and status["tables"] == TABLES


def test_governance_and_the_migrations():
    assert policy.privilege_for("POST", "/api/hub/snaplogic/sync/s1/retry")[0] == "SOURCE.CONNECT"
    assert policy.privilege_for("POST", "/api/hub/snaplogic/preview")[0] is None
    assert policy.privilege_for("POST", "/api/hub/snaplogic/preview-share")[0] is None
    v070 = (ROOT / "snowflake" / "database" / "migrations" / "V070__snaplogic_config_sync.sql").read_text(encoding="utf-8")
    for col in ("SCHEDULE_CRON", "SCHEDULE_FREQUENCY", "SOURCE_SYSTEM_DESC", "DOMAIN", "AIRFLOW_SOURCE_SYSTEM", "SILVER_COMMAND_NAME",
                "SNAPLOGIC_MASTER_CONFIG_ID", "BRONZE_DATABASE", "SNAPLOGIC_CHILD_CONFIG_ID"):
        assert f"ADD COLUMN IF NOT EXISTS {col} " in v070, col


def test_the_aods_tables_are_created_in_core_exactly_as_snaplogic_defines_them():
    sql = (ROOT / "snowflake" / "database" / "migrations" / "V071__aods_snaplogic_config_in_core.sql").read_text(encoding="utf-8")
    for table, live in (("AODS_SNAPLOGIC_CONFIG_MASTER", LIVE_MASTER), ("AODS_SNAPLOGIC_CONFIG_DETAIL", LIVE_DETAIL)):
        body = sql.split(f"CREATE TABLE IF NOT EXISTS {{{{database}}}}.CORE.{table} (")[1].split(");")[0]
        columns = [line.split()[0] for line in body.splitlines() if line.strip()]
        assert columns == live, table
        assert f"GRANT SELECT, INSERT, UPDATE ON TABLE {{{{database}}}}.CORE.{table} TO DATABASE ROLE {{{{database}}}}.DATA_ENGINEER" in sql
    assert "MASTER_CONFIG_ID NUMBER(38,0) AUTOINCREMENT START 1 INCREMENT 1 NOORDER" in sql
    assert "CHILD_CONFIG_ID NUMBER(38,0) AUTOINCREMENT START 1 INCREMENT 1 ORDER" in sql
    assert "OBJECT_DELETE(CONFIG_VALUE, 'table_prefix')" in sql
    # one database, one schema: nothing outside the platform database's CORE
    assert not re.search(r"\b(DEV_GDP_UTIL_DB|CREATE\s+SCHEMA|CREATE\s+DATABASE)\b", sql)


# ---------------------------------------------------------------- the deploy tool


def test_the_environment_file_names_snaplogics_role_and_nothing_else():
    from infrastructure import deploy

    base = {"snowflake": {"connection": "c", "role": "AODS_PLATFORM_ADMIN", "warehouse": "WH", "database": "AI_PLATFORM"},
            "access": {"model": "account_roles", "role_prefix": "AODS"}}
    env = deploy.validate_env({**base, "snaplogic": {"enabled": False, "snaplogic_roles": ["snaplogic_rw"]}}, "client")
    assert env["snaplogic"] == {"enabled": False, "snaplogic_roles": ["SNAPLOGIC_RW"]}
    with pytest.raises(deploy.ConfigError, match="always CORE.AODS_SNAPLOGIC_CONFIG_MASTER"):
        deploy.validate_env({**base, "snaplogic": {"table_prefix": "DEV_GDP_UTIL_DB.CONFIG.AODS_"}}, "client")
    assert deploy.validate_env(base, "client")["snaplogic"] is None
    grants = deploy.access_statements(env, owns_warehouse=False)
    assert "GRANT SELECT, UPDATE ON TABLE AI_PLATFORM.CORE.AODS_SNAPLOGIC_CONFIG_DETAIL TO ROLE SNAPLOGIC_RW" in grants
    assert "GRANT USAGE ON SCHEMA AI_PLATFORM.CORE TO ROLE SNAPLOGIC_RW" in grants
    assert not any("DEV_GDP_UTIL_DB" in g for g in grants)


def test_the_deploy_seeds_the_setting_once():
    from infrastructure.seed_knowledge import seed_snaplogic

    class Cur:
        def __init__(self, count):
            self.count, self.executed = count, []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchall(self):
            return [(self.count,)]

    fresh = Cur(0)
    assert seed_snaplogic(fresh, "AI_PLATFORM", {"enabled": False}) == [
        "snaplogic sync: off, writing AI_PLATFORM.CORE.AODS_SNAPLOGIC_CONFIG_MASTER and AI_PLATFORM.CORE.AODS_SNAPLOGIC_CONFIG_DETAIL"]
    value = json.loads(fresh.executed[-1][1][1])
    assert value["enabled"] is False and "table_prefix" not in value
    kept = Cur(1)
    assert seed_snaplogic(kept, "AI_PLATFORM", {"enabled": True}) == ["snaplogic sync: kept the saved setting"]
    assert seed_snaplogic(Cur(0), "AI_PLATFORM", None) == []
