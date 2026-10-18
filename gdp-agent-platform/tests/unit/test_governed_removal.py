"""Governed removal (V068): removing or restoring a domain table, a source or a knowledge item always waits for
another person's approval, deactivates instead of deleting, records who, when and why, and survives the next seed."""

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

import app.main  # noqa: E402,F401  (load the app first: the API modules import from it)
from app import removal_api  # noqa: E402
from services.governance import policy  # noqa: E402


class Db:
    def __init__(self, row=None, fail_columns=False):
        self.row, self.fail_columns, self.writes = row, fail_columns, []

    def query(self, sql, params=()):
        return [dict(self.row)] if self.row else []

    def execute(self, sql, params=()):
        if self.fail_columns and "RETIRED_AT" in sql:
            raise RuntimeError("SQL compilation error: invalid identifier 'RETIRED_AT'")
        self.writes.append((" ".join(sql.split()), params))


TABLE = {"target_table_id": "t1", "target_table": "DIM_CUSTOMERS", "target_database": "DB", "target_schema": "S",
         "active_flag": True, "domain_id": "d1", "domain_name": "COMPANY"}
SOURCE = {"source_system_id": "s1", "source_system_name": "CRM_TEST", "source_type": "SNOWFLAKE_DATABASE",
          "connection_type": None, "active_flag": True}


@pytest.mark.parametrize("path, privilege, approver", [
    ("/api/targets/t1/retire", "DOMAIN.EDIT", "DATA_STEWARD"),
    ("/api/targets/t1/restore", "DOMAIN.EDIT", "DATA_STEWARD"),
    ("/api/sources/s1/retire", "SOURCE.CONNECT", "PLATFORM_ADMIN"),
    ("/api/knowledge/k1/retire", "KNOWLEDGE.EDIT", "DATA_STEWARD"),
])
def test_every_removal_waits_for_another_persons_approval(path, privilege, approver):
    assert policy.privilege_for("POST", path)[0] == privilege
    assert policy.store_queue("POST", path) and not policy.no_queue("POST", path)
    store = policy.store_policy(privilege, None)
    assert store["approver_role"] == approver and store["four_eyes"] and not store["allow_self"]
    # even someone who holds the privilege is queued, never allowed straight through
    assert policy.decide(privilege, {privilege}, store)[0] == "REQUEST"


def test_removing_a_table_deactivates_it_with_who_when_and_why():
    db = Db(TABLE)
    out = removal_api.retire_target("t1", removal_api.RemoveIn(reason="test table, not the company model"), db)
    assert out == {"target_table_id": "t1", "table": "DIM_CUSTOMERS", "domain": "COMPANY", "removed": True}
    sql, params = db.writes[0]
    assert "SET ACTIVE_FLAG = FALSE, RETIRED_AT = CURRENT_TIMESTAMP(), RETIRED_BY = CURRENT_USER()" in sql
    assert params == ("test table, not the company model", "t1")
    assert not any("DELETE" in s for s, _ in db.writes)


def test_removal_refuses_missing_or_already_removed_and_restore_needs_a_removed_row():
    with pytest.raises(HTTPException) as missing:
        removal_api.retire_target("nope", removal_api.RemoveIn(reason="a reason"), Db(None))
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as twice:
        removal_api.retire_target("t1", removal_api.RemoveIn(reason="a reason"), Db({**TABLE, "active_flag": False}))
    assert twice.value.status_code == 409
    with pytest.raises(HTTPException):
        removal_api.restore_target("t1", None, Db(TABLE))
    restored = Db({**TABLE, "active_flag": False})
    assert removal_api.restore_target("t1", None, restored)["removed"] is False
    assert "SET ACTIVE_FLAG = TRUE, RETIRED_AT = NULL" in restored.writes[0][0]


def test_a_reason_is_required():
    with pytest.raises(Exception):
        removal_api.RemoveIn(reason="no")


def test_sources_are_removed_the_same_way_and_work_before_v068():
    db = Db(SOURCE)
    assert removal_api.retire_source("s1", removal_api.RemoveIn(reason="duplicate test source"), db)["removed"]
    assert "UPDATE CORE.SOURCE_REGISTRY SET ACTIVE_FLAG = FALSE" in db.writes[0][0]
    old = Db(SOURCE, fail_columns=True)
    removal_api.retire_source("s1", removal_api.RemoveIn(reason="duplicate test source"), old)
    assert old.writes == [("UPDATE CORE.SOURCE_REGISTRY SET ACTIVE_FLAG = %s WHERE SOURCE_SYSTEM_ID = %s", (False, "s1"))]


def test_the_seed_never_brings_a_removed_table_back():
    text = (Path(__file__).resolve().parents[2] / "services" / "knowledge" / "packs.py").read_text(encoding="utf-8")
    assert "ACTIVE_FLAG = IFF(t.RETIRED_AT IS NULL, s.ACTIVE_FLAG, FALSE)" in text


def test_the_approvers_can_write_the_registries():
    sql = (Path(__file__).resolve().parents[2] / "snowflake" / "database" / "migrations" / "V068__governed_removal.sql").read_text()
    for table in ("TARGET_TABLE_REGISTRY", "SOURCE_REGISTRY", "DOMAIN_REGISTRY"):
        assert f"GRANT SELECT, UPDATE ON TABLE {{{{database}}}}.CORE.{table} TO DATABASE ROLE {{{{database}}}}.DATA_STEWARD" in sql


class CatalogDb:
    """INFORMATION_SCHEMA sees the table; the registry has it in another domain unless told otherwise."""

    def __init__(self, elsewhere=None):
        self.elsewhere, self.calls = elsewhere, []

    def query(self, sql, params=()):
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return [{"column_name": "ID", "data_type": "NUMBER", "ordinal_position": 1, "is_nullable": "NO", "comment": None,
                     "character_maximum_length": None, "numeric_precision": 38, "numeric_scale": 0}]
        if "TARGET_TABLE_REGISTRY" in sql:
            return [{"domain_name": self.elsewhere}] if self.elsewhere else []
        return []

    def call(self, sql, params=()):
        self.calls.append(params)
        return {"target_table_id": "t9", "domain_id": "d1"}


def test_adding_a_table_that_lives_in_another_domain_says_where_it_is():
    bind = app.main.TargetBind(database="DB", schema="S", table="DIM_CUSTOMERS", domain_id="d1")
    with pytest.raises(HTTPException) as taken:
        app.main.register_target(bind, CatalogDb(elsewhere="SALES"))
    assert taken.value.status_code == 409 and "SALES" in taken.value.detail and "Remove it there first" in taken.value.detail
    free = CatalogDb()
    assert app.main.register_target(bind, free)["target_table_id"] == "t9"
    assert '"domain_id": "d1"' in free.calls[0][0]


def test_applying_a_model_version_never_brings_a_removed_table_back():
    from services.modeling import registry_sync
    from tests.unit.test_model_workbench import Session, spec_fixture

    spec = spec_fixture()
    cust = spec["design"]["entities"][0]["entity_name"]
    s = Session({"WHERE DOMAIN_ID = ? AND UPPER(TARGET_DATABASE)": lambda p: (
        [{"TARGET_TABLE_ID": "reg-cust", "TARGET_TABLE": cust, "RETIRED_AT": "2026-10-17"}] if p[3] == cust else [])})
    out = registry_sync.apply_spec(s, spec, "d1", "m1", 4)
    assert out["kept_removed"] == [cust]
    update = s.find("UPDATE CORE.TARGET_TABLE_REGISTRY SET TARGET_DATABASE")[0][1]
    assert update[-1] == "reg-cust" and update[-3] is False   # definition kept current, still inactive
    # a table that was never removed is switched on as before
    live = Session({"WHERE DOMAIN_ID = ? AND UPPER(TARGET_DATABASE)": lambda p: (
        [{"TARGET_TABLE_ID": "reg-cust", "TARGET_TABLE": cust, "RETIRED_AT": None}] if p[3] == cust else [])})
    assert registry_sync.apply_spec(live, spec, "d1", "m1", 4)["kept_removed"] == []
    assert live.find("UPDATE CORE.TARGET_TABLE_REGISTRY SET TARGET_DATABASE")[0][1][-3] is True
