"""Multi-target mapping, phase 1: a silver run pinned to an approved domain model gets every active entity of it as its
target set (CORE.RUN_TARGET, V074), hub first; an entity without columns or registry row is listed, not included. A
child run, a gold model, and a database before V074 keep the single pinned entity. Snowflake is a fake."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.mapping import procedures as mp  # noqa: E402
from services.modeling import run_link, spec_v2  # noqa: E402
from test_model_workbench import Session  # noqa: E402

MIGRATION = ROOT / "snowflake" / "database" / "migrations" / "V074__run_target_set.sql"


def col(name):
    return {"name": name, "datatype": "VARCHAR(100)"}


def property_spec(layer="SILVER"):
    return spec_v2.normalize_v2({
        "domain": "PROPERTY", "layer": layer, "target_database": "DEV_GDP_SILVER_DB", "target_schema": "PROPERTY",
        "design": {"entities": [
            {"entity_name": "PROPERTY_USAGE", "table_type": "SPOKE", "attributes": [col("SOURCE_UNIQUE_ID"), col("USAGE_DESC")]},
            {"entity_name": "PROPERTY_ADDRESS", "table_type": "SPOKE", "attributes": [col("SOURCE_UNIQUE_ID"), col("ADDRESS")]},
            {"entity_name": "PROPERTY_CORE", "table_type": "HUB", "attributes": [col("SOURCE_UNIQUE_ID"), col("PROPERTY_NAME")]},
            {"entity_name": "PROPERTY_OWNER", "table_type": "SPOKE", "attributes": []},
            {"entity_name": "PROPERTY_LEGACY", "table_type": "SPOKE", "status": "RETIRED", "attributes": [col("X")]},
        ]},
    }, "m1")


REGISTRY = {"PROPERTY_CORE": ("t-core", True), "PROPERTY_ADDRESS": ("t-addr", True), "PROPERTY_USAGE": ("t-use", False),
            "PROPERTY_OWNER": ("t-own", True)}


def registry(params):
    found = REGISTRY.get(params[2])
    return [{"TARGET_TABLE_ID": found[0], "ACTIVE_FLAG": found[1]}] if found else []


def pin_session(parent=None, layer="SILVER", extra=None):
    answers = {
        "SELECT D.SPEC, D.STATUS FROM CORE.MODEL_DESIGNS": [{"SPEC": json.dumps(property_spec(layer)), "STATUS": "APPROVED"}],
        "SELECT PARENT_RUN_ID FROM CORE.WORKFLOW_RUN": [{"PARENT_RUN_ID": parent}],
        "FROM CORE.TARGET_TABLE_REGISTRY WHERE UPPER(TARGET_DATABASE)": registry,
    }
    answers.update(extra or {})
    return Session(answers)


def test_the_target_set_is_every_active_entity_hub_first():
    targets = run_link.target_set(pin_session(), property_spec(), "PROPERTY_ADDRESS")
    assert [t["entity"] for t in targets] == ["PROPERTY_CORE", "PROPERTY_ADDRESS", "PROPERTY_OWNER", "PROPERTY_USAGE"]
    assert [t["ordinal"] for t in targets] == [1, 2, 3, 4] and targets[0]["role"] == "HUB"
    by = {t["entity"]: t for t in targets}
    assert by["PROPERTY_CORE"]["target_fqn"] == "DEV_GDP_SILVER_DB.PROPERTY.PROPERTY_CORE"
    assert by["PROPERTY_CORE"]["included"] and by["PROPERTY_ADDRESS"]["target_table_id"] == "t-addr"
    # listed with the reason, never silently dropped
    assert by["PROPERTY_OWNER"]["excluded_reason"] == "no columns in the approved model"
    assert by["PROPERTY_USAGE"]["excluded_reason"] == "the target table is not active in the registry"
    assert "PROPERTY_LEGACY" not in by


def test_an_unregistered_entity_is_listed_without_a_table():
    s = pin_session(extra={"FROM CORE.TARGET_TABLE_REGISTRY WHERE UPPER(TARGET_DATABASE)":
                           lambda p: [] if p[2] == "PROPERTY_ADDRESS" else registry(p)})
    addr = next(t for t in run_link.target_set(s, property_spec(), "PROPERTY_CORE") if t["entity"] == "PROPERTY_ADDRESS")
    assert addr["target_table_id"] is None and not addr["included"] and addr["excluded_reason"] == "not registered as a target table"


def test_pinning_a_silver_run_records_its_target_set():
    s = pin_session()
    out = run_link.pin_run(s, "r1", "m1", 1, "PROPERTY_CORE")
    assert out["target_model"] == "DEV_GDP_SILVER_DB.PROPERTY.PROPERTY_CORE" and len(out["targets"]) == 4
    # the run keeps the hub as its single target for the stages that read one
    assert s.find("UPDATE CORE.WORKFLOW_RUN SET MODEL_ID = ?")[0][1][2:4] == ["PROPERTY_CORE", "DEV_GDP_SILVER_DB.PROPERTY.PROPERTY_CORE"]
    retire = s.find("UPDATE CORE.RUN_TARGET SET IS_CURRENT = FALSE")
    insert = s.find("INSERT INTO CORE.RUN_TARGET")
    assert retire[0][1] == ["r1"] and len(insert) == 1
    assert s.calls.index(retire[0]) < s.calls.index(insert[0])     # the previous set stops being current first
    params = insert[0][1]                                           # 11 binds a row, the new id first
    assert len(params) == 4 * 11
    assert params[1:11] == ["r1", "m1", "1", "PROPERTY_CORE", "DEV_GDP_SILVER_DB.PROPERTY.PROPERTY_CORE", "t-core",
                            "HUB", "1", "TRUE", ""]
    owner = params[2 * 11:3 * 11]                                   # CORE, ADDRESS, OWNER, USAGE
    assert owner[4] == "PROPERTY_OWNER" and owner[9:] == ["FALSE", "no columns in the approved model"]


@pytest.mark.parametrize("kind", ["child", "gold"])
def test_child_and_gold_runs_keep_the_single_pinned_entity(kind):
    s = pin_session(parent="p1") if kind == "child" else pin_session(layer="GOLD")
    out = run_link.pin_run(s, "r1", "m1", 1, "PROPERTY_ADDRESS")
    assert "targets" not in out and out["entity"] == "PROPERTY_ADDRESS"
    assert not s.find("RUN_TARGET")


def test_before_v074_pinning_still_works():
    class Missing(Session):
        def sql(self, sql, params=None):
            if "UPDATE CORE.RUN_TARGET" in sql:
                raise RuntimeError("SQL compilation error: Table 'CORE.RUN_TARGET' does not exist or not authorized.")
            return super().sql(sql, params)

    s = Missing(pin_session().answers)
    out = run_link.pin_run(s, "r1", "m1", 1, "PROPERTY_CORE")
    assert "targets" not in out and out["target_model"].endswith("PROPERTY_CORE") and not s.find("INSERT INTO CORE.RUN_TARGET")


def test_other_errors_while_recording_are_not_hidden():
    class Broken(Session):
        def sql(self, sql, params=None):
            if "UPDATE CORE.RUN_TARGET" in sql:
                raise RuntimeError("Insufficient privileges to operate on table 'RUN_TARGET'")
            return super().sql(sql, params)

    with pytest.raises(RuntimeError, match="Insufficient privileges"):
        run_link.pin_run(Broken(pin_session().answers), "r1", "m1", 1, "PROPERTY_CORE")


# ---------------------------------------------------------------- mapping reads the set

RUN = {"RUN_ID": "r1", "TARGET_MODEL": "DEV_GDP_SILVER_DB.PROPERTY.PROPERTY_CORE", "DOMAIN_ID": "d1"}
SINGLE = {"FROM CORE.TARGET_TABLE_REGISTRY WHERE ACTIVE_FLAG AND TARGET_DATABASE = ?": [
    {"TARGET_TABLE_ID": "t-core", "TARGET_TABLE": "PROPERTY_CORE"}]}


def test_target_tables_reads_the_run_target_set_in_order():
    s = Session({"FROM CORE.RUN_TARGET R": [{"TARGET_TABLE_ID": "t-core", "TARGET_TABLE": "PROPERTY_CORE", "ROLE": "HUB", "ORDINAL": 1},
                                            {"TARGET_TABLE_ID": "t-addr", "TARGET_TABLE": "PROPERTY_ADDRESS", "ROLE": "SPOKE", "ORDINAL": 2}]})
    out = mp.target_tables(s, RUN)
    assert [t["TARGET_TABLE_ID"] for t in out] == ["t-core", "t-addr"]
    sql = s.find("FROM CORE.RUN_TARGET R")[0][0]
    assert "R.IS_CURRENT AND R.INCLUDED AND T.ACTIVE_FLAG" in sql and "ORDER BY R.ORDINAL" in sql


def test_target_tables_without_a_set_is_the_single_target():
    out = mp.target_tables(Session(SINGLE), RUN)
    assert out == [{"TARGET_TABLE_ID": "t-core", "TARGET_TABLE": "PROPERTY_CORE", "ROLE": "TABLE", "ORDINAL": 1,
                    "ENTITY_NAME": "PROPERTY_CORE"}]


def test_target_tables_before_v074_is_the_single_target():
    class Missing(Session):
        def sql(self, sql, params=None):
            if "CORE.RUN_TARGET" in sql:
                raise RuntimeError("Object 'CORE.RUN_TARGET' does not exist or not authorized.")
            return super().sql(sql, params)

    assert [t["TARGET_TABLE_ID"] for t in mp.target_tables(Missing(SINGLE), RUN)] == ["t-core"]
    with pytest.raises(AssertionError, match="NO_TARGET_MODEL"):
        mp.target_tables(Missing(SINGLE), {**RUN, "TARGET_MODEL": None})


def test_the_migration_adds_the_set_and_the_table_columns_without_changing_rows():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS {{database}}.CORE.RUN_TARGET" in sql
    for table in ("MAPPING_CANDIDATE", "MAPPING_DECISION"):
        assert f"ALTER TABLE {{{{database}}}}.CORE.{table} ADD COLUMN IF NOT EXISTS TARGET_TABLE_ID VARCHAR(36);" in sql
    assert "DEFAULT" not in sql.split("ADD COLUMN IF NOT EXISTS TARGET_TABLE_ID")[1].split(";")[0]
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.RUN_TARGET TO DATABASE ROLE {{database}}.DATA_ENGINEER" in sql
    assert "DELETE" not in sql.upper().replace("NOTHING IS DELETED", "")
