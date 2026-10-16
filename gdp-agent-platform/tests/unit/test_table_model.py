"""Table Model Store (V044): the single-entity specification, rendering, versioned saves with etags, run registration
and the TABLE_MODEL pull request kind. Snowflake is replaced by a fake that answers by SQL substring."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))

from services.git import roundtrip  # noqa: E402
from services.modeling import spec_v2, table_model as tm, table_model_sync as sync  # noqa: E402
from services.modeling.domain_model import Conflict  # noqa: E402


class Row(dict):
    def as_dict(self):
        return dict(self)

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else dict.__getitem__(self, key)


class Session:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def sql(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, list(params or [])))
        session = self

        class Result:
            def collect(self_inner):
                for needle, value in session.answers.items():
                    if needle in flat:
                        value = value(list(params or [])) if callable(value) else value
                        return [Row(r) for r in value]
                return []
        return Result()

    def find(self, needle):
        return [(s, p) for s, p in self.calls if needle in s]


ENTITY = {"entity_name": "CUSTOMER", "kind": "DIMENSION", "purpose": "Customers",
          "attributes": [{"name": "CUSTOMER_ID", "datatype": "NUMBER(38,0)", "nullable": False, "is_pk": True,
                          "source_columns": ["CRM_ACCOUNT.ID"]},
                         {"name": "NAME", "datatype": "VARCHAR(200)", "source_columns": ["CRM_ACCOUNT.NAME"]}]}


def header(**over):
    h = {"TABLE_MODEL_ID": tm.table_model_id("CRM", "CUSTOMER"), "TABLE_NAME": "CUSTOMER", "SOURCE_ID": "s1",
         "SOURCE_NAME": "CRM", "SOURCE_TABLES": "[]", "DOMAIN_ID": "d1", "ORIGIN": "RUN", "ORIGIN_RUN_ID": "r1",
         "LAST_RUN_ID": "r1", "MODEL_LAYER": "SILVER", "TABLE_TYPE": "STANDARD", "CURRENT_VERSION": 1, "ETAG": "e1",
         "DIAGRAM_LAYOUT": None, "LINKED_MODEL_ID": None, "LINKED_ENTITY": None, "LAST_CHANGE_ID": None,
         "CREATED_BY": "A", "CREATED_AT": None, "UPDATED_BY": "A", "UPDATED_AT": None, "DOMAIN_NAME": "SALES",
         "ORIGIN_RUN_NAME": "crm onboarding"}
    h.update(over)
    return h


# ---------------------------------------------------------------- pure


def test_id_is_deterministic_and_case_insensitive():
    assert tm.table_model_id("crm", "customer") == tm.table_model_id("CRM", "CUSTOMER")
    assert tm.table_model_id("CRM", "CUSTOMER") != tm.table_model_id("ERP", "CUSTOMER")
    assert len(tm.table_model_id("CRM", "CUSTOMER")) == 36


def test_single_keeps_one_entity_and_follows_table_type():
    raw = {"design": {"entities": [ENTITY, {**ENTITY, "entity_name": "OTHER"}]},
           "relationships": [{"from_entity": "OTHER", "from_column": "CUSTOMER_ID", "to_entity": "CUSTOMER",
                              "to_column": "CUSTOMER_ID"}]}
    spec = tm.single(raw, "m1", domain="Sales", table_type="ICEBERG", table_name="other")
    assert [e["entity_name"] for e in spec["design"]["entities"]] == ["OTHER"]
    assert spec["relationships"] == []
    assert spec["physical_defaults"]["platform"] == "ICEBERG" and spec["domain"] == "SALES"
    assert tm.single(ENTITY, "m1")["design"]["entities"][0]["entity_name"] == "CUSTOMER"


def test_validate_requires_exactly_one_table_and_flags_unknown_classification():
    empty = tm.single({"design": {"entities": []}}, "m1")
    assert any(i["message"] == "a table model has exactly one table" for i in tm.validate(empty))
    spec = tm.single({"design": {"entities": [{**ENTITY, "attributes": [
        {**ENTITY["attributes"][0], "semantic_type": "SURROGATE_KEY"},
        {**ENTITY["attributes"][1], "semantic_type": "WHATEVER"}]}]}}, "m1")
    issues = tm.validate(spec)
    assert not [i for i in issues if i["path"] == "CUSTOMER.CUSTOMER_ID"]
    assert any(i["path"] == "CUSTOMER.NAME" and i["severity"] == "WARN" for i in issues)


def test_render_standard_and_iceberg():
    std = tm.render(tm.single(ENTITY, "m1", domain="SALES", table_type="STANDARD"))
    assert "CREATE TABLE IF NOT EXISTS" in std["ddl"] and "PRIMARY KEY (CUSTOMER_ID)" in std["ddl"]
    assert any(p.endswith("customer.sql") for p in std["dbt"])
    ice = tm.single(ENTITY, "m1", domain="SALES", table_type="ICEBERG")
    assert any("external volume" in i["message"] for i in tm.validate(ice))
    ice["physical_defaults"]["iceberg"]["external_volume"] = "VOL"
    assert "CREATE ICEBERG TABLE" in tm.render(ice)["ddl"]
    files = tm.files(ice, "GOLD")
    assert "models/ddl/gold/customer.sql" in files


def test_carry_over_keeps_store_descriptions_and_ids():
    old = tm.single({"design": {"entities": [{**ENTITY, "attributes": [
        {**ENTITY["attributes"][1], "id": "keep", "description": "Legal name", "semantic_type": "ATTRIBUTE"}]}]}}, "m1")
    new = tm.single(ENTITY, "m1")
    merged = sync.carry_over(new, old)
    name = next(a for a in merged["design"]["entities"][0]["attributes"] if a["name"] == "NAME")
    assert name["description"] == "Legal name" and name["semantic_type"] == "ATTRIBUTE" and name["id"] == "keep"


def test_source_tables_are_qualified_with_the_run_source():
    run = {"SOURCE_DATABASE": "RAW", "SOURCE_SCHEMA": "CRM"}
    assert sync.source_tables(ENTITY, run) == ["RAW.CRM.CRM_ACCOUNT"]


def test_roundtrip_knows_table_models():
    assert roundtrip.KINDS["TABLE_MODEL"]["table"] == "CORE.TABLE_MODEL_VERSION"
    assert roundtrip.link("TABLE_MODEL", "abc", 3) == "/dashboard/modeling/tables/abc?version=3"
    s = Session()
    roundtrip.apply_state(s, "TABLE_MODEL", "abc", 3, "MERGED", "sha")
    sql, params = s.calls[0]
    assert "STATUS = 'PUBLISHED'" in sql and "STATUS IN ('DRAFT', 'PUBLISHED')" in sql and params[-2:] == ["abc", 3]
    s = Session()
    roundtrip.apply_state(s, "MODEL", "m", 1, "MERGED", "sha")
    assert "STATUS IN ('APPROVED', 'PUBLISHED')" in s.calls[0][0]


# ---------------------------------------------------------------- service


def test_create_inserts_header_and_version_one():
    s = Session({"SELECT COUNT(*)": [{"C": 0}]})
    out = tm.create(s, {"table_name": "customer", "source_name": "crm", "spec": {"design": {"entities": [ENTITY]}}}, "ann")
    assert out["version"] == 1 and out["table_model_id"] == tm.table_model_id("CRM", "CUSTOMER")
    assert s.find("INSERT INTO CORE.TABLE_MODEL (")
    sql, params = s.find("INSERT INTO CORE.TABLE_MODEL_VERSION")[0]
    assert params[0] == out["table_model_id"] and json.loads(params[2])["design"]["entities"][0]["entity_name"] == "CUSTOMER"


def test_create_refuses_a_duplicate_and_bad_layer():
    with pytest.raises(AssertionError, match="already has a model"):
        tm.create(Session({"SELECT COUNT(*)": [{"C": 1}]}), {"table_name": "CUSTOMER", "source_name": "CRM"}, "ann")
    with pytest.raises(AssertionError, match="layer"):
        tm.create(Session({"SELECT COUNT(*)": [{"C": 0}]}),
                  {"table_name": "CUSTOMER", "source_name": "CRM", "model_layer": "PLATINUM"}, "ann")


def _stored_spec():
    return json.dumps(tm.single(ENTITY, tm.table_model_id("CRM", "CUSTOMER"), domain="SALES"))


def test_save_with_a_stale_etag_is_a_conflict():
    s = Session({"FROM CORE.TABLE_MODEL M": [header(ETAG="fresh")]})
    with pytest.raises(Conflict):
        tm.save(s, header()["TABLE_MODEL_ID"], {"etag": "old", "spec": ENTITY}, "ann")


def test_save_adds_the_next_version_and_records_the_change():
    etags = {}

    def etag_now(params):
        return [{"ETAG": etags.get("new")}]

    def update(params):
        etags["new"] = params[1]
        return []

    s = Session({"SELECT ETAG FROM CORE.TABLE_MODEL": etag_now,
                 "UPDATE CORE.TABLE_MODEL SET CURRENT_VERSION": update,
                 "FROM CORE.TABLE_MODEL M": [header()],
                 "SELECT SPEC FROM CORE.TABLE_MODEL_VERSION": [{"SPEC": _stored_spec()}],
                 "COALESCE(MAX(VERSION), 0)": [{"V": 1}]})
    changed = {**ENTITY, "attributes": [*ENTITY["attributes"], {"name": "SEGMENT", "datatype": "VARCHAR(20)"}]}
    out = tm.save(s, header()["TABLE_MODEL_ID"], {"etag": "e1", "spec": changed, "change_note": "segment"}, "ann")
    assert out["version"] == 2 and out["counts"]["ADDITIVE"] >= 1
    sql, params = s.find("INSERT INTO CORE.TABLE_MODEL_VERSION")[0]
    assert params[1] == "2" and json.loads(params[6])["counts"]["ADDITIVE"] >= 1


def test_save_without_changes_adds_no_version():
    s = Session({"FROM CORE.TABLE_MODEL M": [header()],
                 "SELECT SPEC FROM CORE.TABLE_MODEL_VERSION": [{"SPEC": _stored_spec()}]})
    out = tm.save(s, header()["TABLE_MODEL_ID"], {"etag": "e1", "spec": json.loads(_stored_spec())}, "ann")
    assert out.get("unchanged") and not s.find("INSERT INTO")


def test_propose_sends_the_entity_to_the_domain_model(monkeypatch):
    from services.modeling import run_link

    seen = {}

    def fake(session, domain_id, incoming, primary, user, **kw):
        seen.update(domain_id=domain_id, primary=primary, kw=kw, incoming=incoming)
        return {"model_id": "dm1", "change_id": "c1", "status": "PROPOSED"}

    monkeypatch.setattr(run_link, "propose_entities", fake)
    s = Session({"FROM CORE.TABLE_MODEL M": [header()],
                 "SELECT SPEC FROM CORE.TABLE_MODEL_VERSION": [{"SPEC": _stored_spec()}]})
    out = tm.propose(s, header()["TABLE_MODEL_ID"], 1, "ann")
    assert out["change_id"] == "c1" and seen["domain_id"] == "d1" and seen["primary"] == "CUSTOMER"
    assert seen["kw"]["table_model_version"] == 1
    assert s.find("SET LINKED_MODEL_ID")[0][1][:2] == ["dm1", "CUSTOMER"]


def test_propose_needs_a_domain():
    s = Session({"FROM CORE.TABLE_MODEL M": [header(DOMAIN_ID=None)]})
    with pytest.raises(AssertionError, match="domain"):
        tm.propose(s, header()["TABLE_MODEL_ID"], 1, "ann")


def test_register_from_run_creates_one_model_per_entity():
    design = {"target_database": "SILVER", "target_schema": "SALES",
              "entities": [ENTITY, {**ENTITY, "entity_name": "ACCOUNT"}]}
    s = Session({"FROM CORE.WORKFLOW_RUN": [{"RUN_ID": "r1", "RUN_NAME": "crm", "SOURCE_SYSTEM_ID": "s1",
                                            "SOURCE_DATABASE": "RAW", "SOURCE_SCHEMA": "CRM", "DOMAIN_ID": "d1"}],
                 "FROM CORE.SOURCE_REGISTRY": [{"SOURCE_SYSTEM_ID": "s1", "SOURCE_SYSTEM_NAME": "crm"}],
                 "FROM CORE.MODEL_DESIGN": [{"DESIGN_JSON": json.dumps(design)}],
                 "SELECT COUNT(*)": [{"C": 0}]})
    out = sync.register_from_run(s, "r1", 2, "ann")
    assert [o["status"] for o in out] == ["CREATED", "CREATED"]
    assert {o["table_name"] for o in out} == {"CUSTOMER", "ACCOUNT"}
    header_insert = s.find("INSERT INTO CORE.TABLE_MODEL (")[0][1]
    assert header_insert[3] == "CRM" and "RAW.CRM.CRM_ACCOUNT" in header_insert[4] and header_insert[7] == "r1"


def test_register_from_run_skips_an_unchanged_table():
    design = {"target_database": "", "target_schema": "", "entities": [ENTITY]}
    stored = sync.carry_over(tm.single({"target_database": "", "target_schema": "", "design": {"entities": [
        {**ENTITY, "table_type": "DIMENSION", "description": "Customers"}]}}, tm.table_model_id("CRM", "CUSTOMER"),
        domain="SALES"), None)
    s = Session({"FROM CORE.WORKFLOW_RUN": [{"RUN_ID": "r1", "SOURCE_SYSTEM_ID": "s1"}],
                 "FROM CORE.SOURCE_REGISTRY": [{"SOURCE_SYSTEM_ID": "s1", "SOURCE_SYSTEM_NAME": "CRM"}],
                 "FROM CORE.MODEL_DESIGN": [{"DESIGN_JSON": json.dumps(design)}],
                 "SELECT TABLE_MODEL_ID FROM CORE.TABLE_MODEL WHERE": [{"TABLE_MODEL_ID": "x"}],
                 "FROM CORE.TABLE_MODEL M": [header()],
                 "SELECT SPEC FROM CORE.TABLE_MODEL_VERSION": [{"SPEC": json.dumps(stored)}]})
    out = sync.register_from_run(s, "r1", 3, "ann")
    assert out[0]["status"] == "UNCHANGED" and not s.find("INSERT INTO")


def test_spec_checksum_is_stable_for_the_same_input():
    a = tm.single(ENTITY, "m1", domain="SALES")
    b = tm.single(json.loads(json.dumps(a)), "m1", domain="SALES")
    assert spec_v2.checksum(a) == spec_v2.checksum(b)


def test_an_expression_wins_over_the_source_column_in_table_models_only():
    spec = tm.single({"design": {"entities": [{**ENTITY, "attributes": [
        ENTITY["attributes"][0], {**ENTITY["attributes"][1], "transformation_hint": "trim(name)", "derived": True}]}]}}, "m1",
        domain="SALES")
    mart = next(v for k, v in tm.render(spec)["dbt"].items() if k.endswith("/customer.sql"))
    assert "cast(trim(name) as" in mart and "cast(id as" in mart
    from services.modeling import render_v2
    plain = next(v for k, v in render_v2.render_dbt(spec).items() if k.endswith("/customer.sql"))
    assert "cast(name as" in plain
