"""Model Workbench: spec v2 (normalize, validate, lossless registry and pack round trips), the classified diff, the
renderers, the draft and review rules of services/modeling/domain_model.py, the registry sync and the API. Snowflake is
replaced by fakes that answer by SQL substring."""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))

from services.modeling import domain_model as dm  # noqa: E402
from services.modeling import model_diff, registry_sync, render_v2, spec_v2  # noqa: E402


# ---------------------------------------------------------------- fakes


class Row(dict):
    def as_dict(self):
        return dict(self)

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else dict.__getitem__(self, key)


class Session:
    """Answers session.sql(...).collect() by the first matching SQL substring; records every call."""

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


def spec_fixture():
    return spec_v2.normalize_v2({
        "domain": "SALES", "target_database": "SILVER", "target_schema": "SALES",
        "naming_standard": {"preset": "NONE"},
        "subject_areas": [{"name": "Customers"}],
        "design": {"entities": [
            {"entity_name": "DIM_CUSTOMER", "table_type": "DIMENSION", "scd_type": "2", "grain": "one row per customer",
             "business_keys": ["CUSTOMER_ID"], "description": "Customers", "subject_area": "Customers",
             "physical": {"cluster_by": ["COUNTRY"], "transient": True},
             "attributes": [
                 {"name": "CUSTOMER_ID", "datatype": "NUMBER(38,0)", "nullable": False, "is_pk": True, "description": "Key"},
                 {"name": "EMAIL", "datatype": "VARCHAR(200)", "pii": {"flag": True, "classification": "EMAIL"},
                  "semantic_type": "EMAIL"},
                 {"name": "COUNTRY", "datatype": "VARCHAR(2)", "accepted_values": ["US", "GB"], "default": "'US'"},
                 {"name": "OLD_FLAG", "datatype": "BOOLEAN", "status": "RETIRED"}]},
            {"entity_name": "FCT_ORDER", "table_type": "FACT", "business_keys": ["ORDER_ID"],
             "checks": [{"name": "CK_AMOUNT", "expression": "AMOUNT >= 0"}],
             "attributes": [
                 {"name": "ORDER_ID", "datatype": "NUMBER(38,0)", "nullable": False, "is_pk": True},
                 {"name": "CUSTOMER_ID", "datatype": "NUMBER(38,0)", "nullable": False},
                 {"name": "AMOUNT", "datatype": "NUMBER(12,2)"}]},
            {"entity_name": "LEGACY_TABLE", "status": "RETIRED", "attributes": [{"name": "X", "datatype": "INT"}]},
        ]},
        "relationships": [{"from_entity": "FCT_ORDER", "from_column": "CUSTOMER_ID", "to_entity": "DIM_CUSTOMER",
                           "to_column": "CUSTOMER_ID", "enforce": "RELY", "from_optional": False}],
    }, "model-1")


# ---------------------------------------------------------------- spec v2


def test_v1_specs_upgrade_with_stable_ids():
    v1 = {"domain": "sales", "mode": "NEW", "target_platform": "SNOWFLAKE",
          "design": {"entities": [{"entity_name": "dim_x", "kind": "DIMENSION", "purpose": "X",
                                   "attributes": [{"name": "id", "datatype": "number", "is_pk": True}]}]},
          "relationships": []}
    a, b = spec_v2.normalize_v2(v1, "m"), spec_v2.normalize_v2(copy.deepcopy(v1), "m")
    ent = a["design"]["entities"][0]
    assert a["spec_version"] == 2 and ent["entity_name"] == "DIM_X" and ent["table_type"] == "DIMENSION"
    assert ent["kind"] == "DIMENSION" and ent["description"] == "X" and ent["attributes"][0]["datatype"] == "NUMBER"
    assert ent["id"] == b["design"]["entities"][0]["id"] and ent["attributes"][0]["id"] == b["design"]["entities"][0]["attributes"][0]["id"]
    # the v1 renderers' aliases are filled
    assert a["target_platform"] == "SNOWFLAKE" and a["design"]["primary_entity"] == "DIM_X"


def test_unknown_table_types_scd_and_model_spec_are_kept():
    spec = spec_v2.normalize_v2({"domain": "C", "design": {"entities": [
        {"entity_name": "COMPANY_CORE", "table_type": "HUB", "scd_type": "1+HIST",
         "model_spec": {"role": "hub", "hkey_columns": ["A", "B"]}, "attributes": [{"name": "A", "datatype": "INT"}]}]}})
    e = spec["design"]["entities"][0]
    assert e["table_type"] == "HUB" and e["kind"] == "TABLE" and e["scd_type"] == "1+HIST"
    assert e["model_spec"] == {"role": "hub", "hkey_columns": ["A", "B"]}


def test_validation_catches_what_a_modeler_gets_wrong():
    spec = spec_fixture()
    spec["design"]["entities"][0]["attributes"].append(spec_v2._attribute({"name": "SELECT", "datatype": "VARCHAR"}, "x", []))
    spec["design"]["entities"][0]["attributes"].append(spec_v2._attribute({"name": "email", "datatype": "TEXT"}, "x", []))
    spec["design"]["entities"][1]["attributes"].append(spec_v2._attribute({"name": "BAD", "datatype": "VARCHAR(10"}, "y", []))
    spec["design"]["entities"][1]["attributes"][0]["nullable"] = True
    spec["relationships"].append(spec_v2._relationship({"from_entity": "FCT_ORDER", "from_column": "AMOUNT",
                                                         "to_entity": "DIM_CUSTOMER", "to_column": "COUNTRY"}))
    spec["relationships"].append(spec_v2._relationship({"from_entity": "FCT_ORDER", "from_column": "CUSTOMER_ID",
                                                         "to_entity": "DIM_CUSTOMER", "to_column": "OLD_FLAG"}))
    spec["physical_defaults"]["platform"] = "ICEBERG"
    msgs = [(i["path"], i["severity"], i["message"]) for i in spec_v2.validate(spec)]
    text = " | ".join(m for _, _, m in msgs)
    assert "SELECT is a Snowflake reserved word" in text
    assert "differ only by case" in text
    assert "VARCHAR(10 is not a Snowflake type" in text
    assert "part of the primary key, so it cannot be nullable" in text
    assert "Iceberg tables need an external volume" in text
    assert any(sev == "WARN" and "not a primary or unique key" in m for _, sev, m in msgs)
    assert "have different types" in text
    assert "uses a retired entity or column" in text
    long = spec_fixture()
    long["design"]["entities"][0]["entity_name"] = "T" * 300
    assert any("longer than 255" in i["message"] for i in spec_v2.validate(long))


def test_naming_standard_breaches_are_warnings():
    spec = spec_fixture()
    spec["naming_standard"] = {"table_prefix": "SLS_", "audit_columns": ["LOAD_TS"]}
    issues = spec_v2.validate(spec)
    assert any(i["severity"] == "WARN" and "should start with SLS_" in i["message"] for i in issues)
    assert any(i["severity"] == "WARN" and "missing the audit column LOAD_TS" in i["message"] for i in issues)
    assert not spec_v2.blocking([i for i in issues if "SLS_" in i["message"] or "LOAD_TS" in i["message"]])


def registry_rows(spec, model_id="model-1", version=1):
    """What registry_sync writes for a specification, as registry rows (lower-case keys)."""
    tables, columns = [], []
    for e in spec["design"]["entities"]:
        db, schema = spec_v2.placement(spec, e)
        tables.append({"target_table_id": e["id"], "target_database": db, "target_schema": schema,
                       "target_table": e["entity_name"], "table_type": e["table_type"], "grain": e["grain"],
                       "business_keys": json.dumps(e["business_keys"]), "scd_type": e["scd_type"],
                       "description": e["description"], "active_flag": e["status"] != "RETIRED",
                       "model_spec": json.dumps(registry_sync.model_spec_for(spec, e, model_id, version))})
        if e["status"] == "RETIRED":
            continue
        for v in registry_sync.column_rows(e, e["id"], []):
            columns.append({"target_column_id": v[0], "target_table_id": v[1], "column_name": v[2], "data_type": v[3],
                            "ordinal_position": v[4], "nullable": v[5], "business_definition": v[6] or None,
                            "semantic_type": v[7] or None, "is_business_key": v[8], "is_pii": v[9],
                            "accepted_values": json.dumps(v[10])})
    return tables, columns


def _live(spec):
    """Retired columns are not registered, so the round trip compares live columns."""
    out = spec_v2.active(spec)
    return out


def test_registry_round_trip_is_lossless():
    spec = spec_fixture()
    tables, columns = registry_rows(spec)
    back = spec_v2.spec_from_registry({"domain_id": "model-1", "domain_name": "SALES"}, tables, columns)
    for ent in spec["design"]["entities"]:  # retired columns leave the registry; retired tables stay, inactive
        ent["attributes"] = [a for a in ent["attributes"] if a["status"] != "RETIRED" or ent["status"] == "RETIRED"]
    retired = next(e for e in spec["design"]["entities"] if e["status"] == "RETIRED")
    retired["attributes"] = []
    for key in ("design", "relationships", "physical_defaults"):
        a = json.loads(json.dumps(spec[key], sort_keys=True))
        b = json.loads(json.dumps(back[key], sort_keys=True))
        if key == "design":
            for ent_a, ent_b in zip(a["entities"], b["entities"]):
                for f in ("purpose",):  # derived from description on both sides
                    ent_a.pop(f, None), ent_b.pop(f, None)
                for att_a, att_b in zip(ent_a["attributes"], ent_b["attributes"]):
                    for f in ("rationale", "derived", "source_columns", "transformation_hint"):  # run lineage only
                        att_a.pop(f, None), att_b.pop(f, None)
        assert a == b, key


def test_pack_round_trip_keeps_hub_spoke_models():
    pack = json.loads((ROOT / "domain" / "company" / "domain_pack.json").read_text())
    spec = spec_v2.spec_from_pack(pack, "company")
    targets = spec_v2.pack_targets(spec)
    original = {t["table"]: t for t in pack["targets"]}
    assert {t["table"] for t in targets} == set(original)
    for t in targets:
        o = original[t["table"]]
        assert t["table_type"] == o["table_type"] and t["scd_type"] == o["scd_type"]
        assert t["model_spec"] == o.get("model_spec")
        assert [c["name"] for c in t["columns"]] == [c["name"] for c in o.get("columns") or []]
        assert [c["type"] for c in t["columns"]] == [c["type"] for c in o.get("columns") or []]


def test_pack_round_trip_keeps_pii_accepted_values_and_semantics():
    spec = spec_fixture()
    back = spec_v2.spec_from_pack(render_v2.render_pack(spec), "model-1")
    email = next(a for a in back["design"]["entities"][0]["attributes"] if a["name"] == "EMAIL")
    country = next(a for a in back["design"]["entities"][0]["attributes"] if a["name"] == "COUNTRY")
    assert email["pii"]["flag"] and email["semantic_type"] == "EMAIL" and country["accepted_values"] == ["US", "GB"]
    assert back["design"]["entities"][0]["scd_type"] == "2"
    assert [e["entity_name"] for e in back["design"]["entities"]] == ["DIM_CUSTOMER", "FCT_ORDER"]  # retired left out


# ---------------------------------------------------------------- diff


@pytest.mark.parametrize("old,new,want", [
    ("VARCHAR(10)", "VARCHAR(20)", "WIDEN"), ("VARCHAR(20)", "VARCHAR(10)", "NARROW"), ("VARCHAR", "VARCHAR(100)", "NARROW"),
    ("NUMBER(10,2)", "NUMBER(12,2)", "WIDEN"), ("NUMBER(10,2)", "NUMBER(10,0)", "NARROW"), ("INT", "BIGINT", "WIDEN"),
    ("NUMBER(38,0)", "INTEGER", "SAME"), ("VARCHAR(10)", "NUMBER", "CHANGE"), ("TIMESTAMP_NTZ", "TIMESTAMP_TZ", "CHANGE"),
])
def test_type_changes(old, new, want):
    assert model_diff.type_change(old, new) == want


def _kinds(result):
    return {(c["kind"], c["class"]) for c in result["changes"]}


def test_diff_classifies_changes():
    old = spec_fixture()
    new = copy.deepcopy(old)
    cust, fct = new["design"]["entities"][0], new["design"]["entities"][1]
    cust["entity_name"] = "DIM_CLIENT"                                  # rename: same id
    cust["attributes"][1]["name"] = "EMAIL_ADDRESS"                     # column rename
    cust["attributes"][2]["datatype"] = "VARCHAR(3)"                    # widen
    cust["attributes"][2]["description"] = "ISO code"                   # documentation
    fct["attributes"][2]["datatype"] = "NUMBER(10,2)"                   # narrow
    fct["attributes"][2]["nullable"] = False                            # tighter
    fct["attributes"].append(spec_v2._attribute({"name": "NOTE", "datatype": "VARCHAR"}, fct["id"], []))
    fct["attributes"].append(spec_v2._attribute({"name": "MUST", "datatype": "INT", "nullable": False}, fct["id"], []))
    fct["grain"] = "one row per order line"
    new["relationships"] = []
    result = model_diff.diff(old, new)
    kinds = _kinds(result)
    assert ("ENTITY_RENAMED", "BREAKING") in kinds and ("COLUMN_RENAMED", "BREAKING") in kinds
    assert ("TYPE_CHANGED", "ADDITIVE") in kinds and ("TYPE_CHANGED", "BREAKING") in kinds
    assert ("NULLABILITY_CHANGED", "BREAKING") in kinds and ("DESCRIPTION_CHANGED", "NON_FUNCTIONAL") in kinds
    assert ("GRAIN_CHANGED", "BREAKING") in kinds and ("RELATIONSHIP_REMOVED", "BREAKING") in kinds
    added = {c["column"]: c["class"] for c in result["changes"] if c["kind"] == "COLUMN_ADDED"}
    assert added == {"NOTE": "ADDITIVE", "MUST": "BREAKING"}
    assert result["breaking"] and result["counts"]["NON_FUNCTIONAL"] >= 1
    paths = model_diff.breaking_paths(result)
    assert {"entity": "DIM_CUSTOMER", "column": "", "kind": "ENTITY_RENAMED"} in paths
    assert any(p["column"] == "EMAIL" and p["kind"] == "COLUMN_RENAMED" for p in paths)


def test_additive_only_and_retire():
    old = spec_fixture()
    new = copy.deepcopy(old)
    new["design"]["entities"].append(spec_v2._entity({"entity_name": "DIM_DATE", "attributes": [{"name": "D", "datatype": "DATE"}]}, "model-1"))
    assert not model_diff.diff(old, new)["breaking"]
    new["design"]["entities"][1]["status"] = "RETIRED"
    assert ("ENTITY_RETIRED", "BREAKING") in _kinds(model_diff.diff(old, new))
    assert model_diff.diff(None, old)["counts"]["ADDITIVE"] == 4  # three entities and a relationship


# ---------------------------------------------------------------- renderers


def test_ddl_renders_constraints_and_options():
    ddl = render_v2.render_ddl(spec_fixture())
    assert "CREATE TRANSIENT TABLE IF NOT EXISTS SILVER.SALES.DIM_CUSTOMER" in ddl
    assert "CONSTRAINT PK_DIM_CUSTOMER PRIMARY KEY (CUSTOMER_ID)" in ddl
    assert "COUNTRY VARCHAR(2) DEFAULT 'US'" in ddl and "CLUSTER BY (COUNTRY)" in ddl
    assert "REFERENCES SILVER.SALES.DIM_CUSTOMER (CUSTOMER_ID) RELY" in ddl
    assert "-- CHECK CK_AMOUNT: AMOUNT >= 0" in ddl and "COMMENT 'Key'" in ddl
    assert "OLD_FLAG" not in ddl and "LEGACY_TABLE" not in ddl


def test_dbt_tests_and_dictionary():
    files = render_v2.render_dbt_tests(spec_fixture())
    schema = files["models/marts/sales/_schema.yml"]
    assert 'values: ["US", "GB"]' in schema and "pii: true" in schema and "pii_classification: email" in schema
    assert "to: ref('dim_customer')" in schema and "- unique" in schema
    assert "where not (AMOUNT >= 0)" in files["tests/sales/fct_order__ck_amount.sql"]
    csv_text = render_v2.data_dictionary(spec_fixture())
    assert csv_text.splitlines()[0].startswith("subject_area,entity,table_type") and len(csv_text.splitlines()) == 9


# ---------------------------------------------------------------- draft and review rules


def model_session(status="DRAFT", authors=("ANA",), submitted_by=None, etag="e1", blockers=0, approved=1,
                  draft_spec=None, approved_spec=None, open_version=2, extra=None):
    base = approved_spec or spec_fixture()
    draft = draft_spec or base
    versions = [{"VERSION": open_version, "STATUS": status, "ORIGIN": "WORKBENCH", "BASE_VERSION": approved, "ETAG": etag,
                 "AUTHORS": json.dumps(list(authors)), "SUBMITTED_BY": submitted_by, "ISSUES": "[]"}] if status else []
    versions.append({"VERSION": 1, "STATUS": "APPROVED", "ORIGIN": "BACKFILL", "ETAG": "x", "AUTHORS": "[]", "ISSUES": "[]"})
    answers = {
        "FROM GOVERNANCE.DOMAIN_MODEL WHERE MODEL_ID = ?": [{"MODEL_ID": "m1", "DOMAIN_ID": "d1", "DOMAIN_NAME": "SALES",
                                                             "APPROVED_VERSION": approved, "OPEN_VERSION": open_version,
                                                             "NAMING_STANDARD": "{}", "DIAGRAM_LAYOUT": "{}"}],
        "ORDER BY VERSION DESC": versions,
        "SELECT SPEC FROM GOVERNANCE.MODEL_DESIGNS": lambda p: [{"SPEC": json.dumps(base if p[1] == 1 else draft)}],
        "KIND = 'BLOCKER'": [{"N": blockers}],
        "SELECT ETAG FROM": [{"ETAG": "will-not-match"}],
    }
    answers.update(extra or {})
    return Session(answers)


def test_save_draft_refuses_a_stale_etag():
    s = model_session(etag="current")
    with pytest.raises(dm.Conflict, match="saved by ANA since you opened it"):
        dm.save_draft(s, "m1", json.dumps({"etag": "old", "spec": spec_fixture()}), "BEN")
    assert not s.find("UPDATE GOVERNANCE.MODEL_DESIGNS SET SPEC")


def test_save_draft_writes_with_the_etag_guard():
    s = model_session(etag="e1")
    s.answers["SELECT ETAG FROM"] = lambda p: [{"ETAG": s.find("SET SPEC")[0][1][2]}]  # the etag this save wrote
    out = dm.save_draft(s, "m1", json.dumps({"etag": "e1", "spec": spec_fixture(), "change_note": "x"}), "ben")
    sql, params = s.find("UPDATE GOVERNANCE.MODEL_DESIGNS SET SPEC")[0]
    assert "AND ETAG = ? AND STATUS = 'DRAFT'" in sql and params[-1] == "e1" and params[3] == "BEN"
    assert out["etag"] == params[2] and out["counts"] == {"ADDITIVE": 0, "NON_FUNCTIONAL": 0, "BREAKING": 0}


def test_save_draft_detects_a_save_at_the_same_moment():
    s = model_session(etag="e1")
    with pytest.raises(dm.Conflict, match="same moment"):
        dm.save_draft(s, "m1", json.dumps({"etag": "e1", "spec": spec_fixture()}), "BEN")


def test_save_draft_needs_a_draft():
    with pytest.raises(AssertionError, match="withdraw it"):
        dm.save_draft(model_session(status="IN_REVIEW"), "m1", json.dumps({"etag": "e1", "spec": {}}), "ANA")


def test_open_draft_refuses_a_second_open_version():
    with pytest.raises(dm.Conflict, match="already open"):
        dm.open_draft(model_session(status="DRAFT"), "m1", "ANA")


def test_open_draft_undoes_itself_when_another_opened_at_the_same_time():
    s = model_session(status=None, open_version=None)
    s.answers["AND STATUS IN ('DRAFT', 'IN_REVIEW') ORDER BY VERSION"] = [{"VERSION": 2}]  # someone else's
    s.answers["ORDER BY VERSION DESC"] = [{"VERSION": 1, "STATUS": "APPROVED", "AUTHORS": "[]", "ISSUES": "[]"},
                                          {"VERSION": 2, "STATUS": "SUPERSEDED", "AUTHORS": "[]", "ISSUES": "[]"}]
    with pytest.raises(dm.Conflict, match="opened as a draft just now"):
        dm.open_draft(s, "m1", "ANA")
    assert s.find("DELETE FROM GOVERNANCE.MODEL_DESIGNS WHERE MODEL_ID = ? AND VERSION = ?")[0][1] == ["m1", 3]


def test_rollback_opens_a_draft_from_an_older_version():
    s = model_session(status=None, open_version=None, approved=3)
    s.answers["ORDER BY VERSION DESC"] = [{"VERSION": v, "STATUS": st, "AUTHORS": "[]", "ISSUES": "[]"}
                                          for v, st in [(3, "APPROVED"), (2, "SUPERSEDED"), (1, "SUPERSEDED")]]
    s.answers["AND STATUS IN ('DRAFT', 'IN_REVIEW') ORDER BY VERSION"] = [{"VERSION": 4}]
    out = dm.open_draft(s, "m1", "ANA", from_version=1)
    insert = s.find("INSERT INTO GOVERNANCE.MODEL_DESIGNS")[0][1]
    assert out == {"model_id": "m1", "version": 4, "status": "DRAFT", "etag": out["etag"], "from_version": 1}
    assert "ROLLBACK" in insert and "Roll back to version 1" in insert


def test_submit_needs_a_valid_changed_draft():
    with pytest.raises(AssertionError, match="nothing to review"):
        dm.submit(model_session(), "m1", "ANA")
    bad = spec_fixture()
    bad["design"]["entities"][0]["attributes"][0]["datatype"] = "NOPE"
    with pytest.raises(AssertionError, match="problem"):
        dm.submit(model_session(draft_spec=bad), "m1", "ANA")
    changed = spec_fixture()
    changed["design"]["entities"][0]["description"] = "Customers we sell to"
    s = model_session(draft_spec=changed)
    out = dm.submit(s, "m1", "ANA", "docs")
    assert out["status"] == "IN_REVIEW" and out["counts"]["NON_FUNCTIONAL"] == 1 and not out["breaking"]
    assert s.find("SET STATUS = 'IN_REVIEW', SUBMITTED_BY = ?")[0][1][0] == "ANA"


def _changed(breaking=False):
    spec = spec_fixture()
    if breaking:
        spec["design"]["entities"][0]["attributes"][2]["datatype"] = "VARCHAR(1)"
    else:
        spec["design"]["entities"][0]["description"] = "Customers we sell to"
    return spec


def test_approve_is_four_eyes():
    s = model_session(status="IN_REVIEW", authors=("ANA",), submitted_by="ANA", draft_spec=_changed())
    with pytest.raises(AssertionError, match="cannot approve a version you submitted or edited"):
        dm.approve(s, "m1", 2, "ana")
    s = model_session(status="IN_REVIEW", authors=("ANA", "BEN"), submitted_by="ANA", draft_spec=_changed())
    with pytest.raises(AssertionError, match="cannot approve"):
        dm.approve(s, "m1", 2, "BEN")


def test_approve_is_blocked_by_blockers_and_unacknowledged_breaking_changes():
    s = model_session(status="IN_REVIEW", submitted_by="ANA", blockers=2, draft_spec=_changed())
    with pytest.raises(AssertionError, match="2 blocking comment"):
        dm.approve(s, "m1", 2, "LEE")
    s = model_session(status="IN_REVIEW", submitted_by="ANA", draft_spec=_changed(breaking=True))
    with pytest.raises(AssertionError, match="BREAKING"):
        dm.approve(s, "m1", 2, "LEE")


def test_approve_supersedes_and_moves_the_header():
    s = model_session(status="IN_REVIEW", submitted_by="ANA", draft_spec=_changed(breaking=True))
    out = dm.approve(s, "m1", 2, "lee", acknowledge_breaking=True)
    order = [c[0] for c in s.calls]
    begin = order.index("BEGIN TRANSACTION")
    assert "SET STATUS = 'SUPERSEDED'" in order[begin + 1] and "SET STATUS = 'APPROVED'" in order[begin + 2]
    assert "APPROVED_VERSION = ?, OPEN_VERSION = NULL" in order[begin + 3] and order[begin + 4] == "COMMIT"
    assert out["acknowledged_breaking"] and out["approved_by"] == "LEE" and out["status"] == "APPROVED"


def test_reject_and_withdraw():
    s = model_session(status="IN_REVIEW", submitted_by="ANA", draft_spec=_changed())
    with pytest.raises(AssertionError, match="withdraw it instead"):
        dm.reject(s, "m1", 2, "ANA", "nope")
    out = dm.reject(s, "m1", 2, "LEE", "Grain is wrong")
    assert out["status"] == "REJECTED" and s.find("SET OPEN_VERSION = NULL")
    with pytest.raises(AssertionError, match="only the submitter or an author"):
        dm.withdraw(model_session(status="IN_REVIEW", authors=("ANA",), submitted_by="ANA"), "m1", "LEE")
    assert dm.withdraw(model_session(status="IN_REVIEW", authors=("ANA",), submitted_by="ANA"), "m1", "ANA")["status"] == "DRAFT"


def test_comments_and_layout():
    s = model_session()
    out = dm.add_comment(s, "m1", 2, json.dumps({"body": "Grain?", "path": "DIM_CUSTOMER.EMAIL", "kind": "blocker"}), "lee")
    insert = s.find("INSERT INTO GOVERNANCE.MODEL_REVIEW_COMMENT")[0][1]
    assert out["kind"] == "BLOCKER" and "DIM_CUSTOMER.EMAIL" in insert and "LEE" in insert
    with pytest.raises(AssertionError, match="kind must be"):
        dm.add_comment(s, "m1", 2, json.dumps({"body": "x", "kind": "SHOUT"}), "lee")
    dm.set_layout(s, "m1", json.dumps({"e1": {"x": 10.123, "y": 4}, "bad": 3}), "ana")
    layout = json.loads(s.find("SET DIAGRAM_LAYOUT")[0][1][0])
    assert layout == {"e1": {"x": 10.1, "y": 4.0}}


def test_backfill_writes_an_approved_baseline_once():
    tables = [{"TARGET_TABLE_ID": "t1", "DOMAIN_ID": "d1", "TARGET_DATABASE": "SILVER", "TARGET_SCHEMA": "SALES",
               "TARGET_TABLE": "DIM_CUSTOMER", "TABLE_TYPE": "DIMENSION", "GRAIN": "", "BUSINESS_KEYS": "[\"ID\"]",
               "SCD_TYPE": "1", "DESCRIPTION": "Customers", "ACTIVE_FLAG": True, "MODEL_SPEC": None}]
    cols = [{"TARGET_COLUMN_ID": "c1", "TARGET_TABLE_ID": "t1", "COLUMN_NAME": "ID", "DATA_TYPE": "NUMBER", "ORDINAL_POSITION": 1,
             "NULLABLE": False, "IS_BUSINESS_KEY": True, "IS_PII": False, "ACCEPTED_VALUES": "[]"}]
    s = Session({"SELECT DISTINCT T.DOMAIN_ID": [{"DOMAIN_ID": "d1"}],
                 "SELECT COUNT(*) FROM GOVERNANCE.DOMAIN_MODEL": [{"N": 0}],
                 "SELECT DOMAIN_ID, DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY": [{"DOMAIN_ID": "d1", "DOMAIN_NAME": "SALES"}],
                 "SELECT C.* FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY": cols,
                 "SELECT * FROM KNOWLEDGE.TARGET_TABLE_REGISTRY": tables})
    log = dm.backfill(s)
    assert log == ["domain model SALES: baseline v1 (1 entities, 1 columns)"]
    version = s.find("INSERT INTO GOVERNANCE.MODEL_DESIGNS")[0][1]
    assert dm.model_id_for("d1") in version and "APPROVED" in version and "BACKFILL" in version
    assert json.loads(version[6])["design"]["entities"][0]["id"] == "t1"  # registry ids are the entity ids
    assert s.find("UPDATE KNOWLEDGE.TARGET_TABLE_REGISTRY SET MODEL_ID = ?, MODEL_VERSION = 1")[0][1] == [dm.model_id_for("d1"), "d1"]
    s.answers["SELECT COUNT(*) FROM GOVERNANCE.DOMAIN_MODEL"] = [{"N": 1}]
    assert dm.baseline(s, "d1") is None


def test_model_id_matches_the_md5_rule():
    import hashlib
    h = hashlib.md5(b"model:d1").hexdigest()
    assert dm.model_id_for("d1").replace("-", "") == h


def test_cursor_session_adapts_qmark_binds():
    class Cursor:
        description = [("A",), ("B",)]

        def __init__(self):
            self.seen = []

        def execute(self, sql, params):
            self.seen.append((sql, params))

        def fetchall(self):
            return [(1, 2)]

    cur = Cursor()
    out = dm.CursorSession(cur).sql("SELECT ? AS A, ? AS B", ["x", 2]).collect()
    assert cur.seen == [("SELECT %s AS A, %s AS B", ("x", 2))] and out[0].as_dict() == {"A": 1, "B": 2} and out[0][1] == 2


# ---------------------------------------------------------------- registry sync


def test_apply_spec_keeps_registry_ids_and_deactivates():
    spec = spec_fixture()
    cust = spec["design"]["entities"][0]
    s = Session({
        # DIM_CUSTOMER is registered under another id (by a run): matched by name
        "WHERE DOMAIN_ID = ? AND UPPER(TARGET_DATABASE)": lambda p: [{"TARGET_TABLE_ID": "reg-cust", "TARGET_TABLE": "DIM_CUSTOMER"}]
        if p[3] == "DIM_CUSTOMER" else ([{"TARGET_TABLE_ID": "reg-legacy", "TARGET_TABLE": "LEGACY_TABLE"}] if p[3] == "LEGACY_TABLE" else []),
        "SELECT TARGET_COLUMN_ID, COLUMN_NAME FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY": lambda p: [
            {"TARGET_COLUMN_ID": "reg-email", "COLUMN_NAME": "EMAIL"}] if p[0] == "reg-cust" else [],
        "AND MODEL_ID = ? AND ACTIVE_FLAG": [{"TARGET_TABLE_ID": "old-1", "TARGET_TABLE": "DROPPED"}],
    })
    out = registry_sync.apply_spec(s, spec, "d1", "m1", 3)
    assert [r["target_table_id"] for r in out["registered"]] == ["reg-cust", cust and spec["design"]["entities"][1]["id"]]
    assert out["deactivated"] == ["LEGACY_TABLE", "DROPPED"]
    update = s.find("UPDATE KNOWLEDGE.TARGET_TABLE_REGISTRY SET TARGET_DATABASE")[0][1]
    assert update[:6] == ["SILVER", "SALES", "DIM_CUSTOMER", "DIMENSION", "one row per customer", '["CUSTOMER_ID"]']
    assert update[9:11] == ["m1", 3] and update[-1] == "reg-cust"
    model_spec = json.loads(update[8])
    assert model_spec["workbench"]["primary_key"] == ["CUSTOMER_ID"] and model_spec["primary_key"] == ["CUSTOMER_ID"]
    merge = next(p for sq, p in s.find("MERGE INTO KNOWLEDGE.TARGET_COLUMN_REGISTRY"))
    assert "reg-email" in merge and "TRUE" in merge  # the registered EMAIL keeps its id; PII is written
    delete = s.find("DELETE FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY WHERE TARGET_TABLE_ID = ? AND TARGET_COLUMN_ID NOT IN")[0][1]
    assert delete[0] == "reg-cust" and "reg-email" in delete and len(delete) == 4  # retired OLD_FLAG is not kept
    assert s.find("SET ACTIVE_FLAG = FALSE, MODEL_ID = ?, MODEL_VERSION = ?")[0][1] == ["m1", 3, "reg-legacy"]


def test_apply_model_version_only_applies_approved_versions():
    s = Session({"JOIN GOVERNANCE.DOMAIN_MODEL M": [{"SPEC": json.dumps(spec_fixture()), "STATUS": "IN_REVIEW", "VERSION": 2,
                                                     "DOMAIN_ID": "d1", "DOMAIN_NAME": "SALES"}]})
    with pytest.raises(AssertionError, match="only an approved version"):
        registry_sync.apply_model_version(s, "m1", 2)


# ---------------------------------------------------------------- API and governance


def test_workbench_routes_privileges_and_never_queued():
    from services.governance.policy import no_queue, privilege_for, store_queue

    assert privilege_for("PUT", "/api/hub/domain-models/m1/draft")[0] == "MODEL.EDIT"
    assert privilege_for("POST", "/api/hub/domain-models/m1/versions/3/approve")[0] == "MODEL.APPROVE"
    assert privilege_for("POST", "/api/hub/domain-models/m1/draft/validate")[0] is None
    assert privilege_for("POST", "/api/hub/domain-models")[0] == "MODEL.EDIT"
    assert privilege_for("POST", "/api/hub/domain-models/baseline")[0] == "MODEL.EDIT"
    assert no_queue("POST", "/api/hub/domain-models/m1/versions/3/approve") and no_queue("PUT", "/api/hub/domain-models/m1/draft")
    assert not no_queue("POST", "/api/hub/sttm/c1/edit") and not store_queue("POST", "/api/hub/domain-models/m1/submit")


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    import app.governance as gov
    import app.main as main
    import app.model_workbench_api as wb

    async def open_door(request, call_next):
        return await call_next(request)

    class Db:
        user = "lee"

    monkeypatch.setattr(gov, "middleware", open_door)
    monkeypatch.setattr(wb, "invoke_source", lambda db, fn, *a: fn(Session(), *a))
    main.app.dependency_overrides[main.current_db] = lambda: Db()
    yield TestClient(main.app), wb, monkeypatch
    main.app.dependency_overrides.clear()


def test_api_maps_conflicts_and_missing_tables(api):
    c, wb, mp = api

    def stale(session, model_id, payload, user):
        raise dm.Conflict("CONFLICT: the draft was saved by ANA")

    mp.setattr(dm, "save_draft", stale)
    r = c.put("/api/hub/domain-models/m1/draft", json={"spec": {}, "etag": "x"})
    assert r.status_code == 409 and "saved by ANA" in r.json()["detail"]

    def gone(session):
        raise RuntimeError("Object 'GOVERNANCE.DOMAIN_MODEL' does not exist or not authorized")

    mp.setattr(dm, "list_models", gone)
    assert c.get("/api/hub/domain-models").json() == {"models": [], "deployed": False}


def test_api_approve_applies_to_the_registry(api):
    c, wb, mp = api
    seen = {}
    mp.setattr(dm, "approve", lambda s, m, v, user, ack: seen.update(user=user, ack=ack) or {"status": "APPROVED"})
    mp.setattr(wb, "_source_call", lambda db, proc, fn, *a: seen.update(proc=proc, args=a) or {"registered": [1]})
    body = c.post("/api/hub/domain-models/m1/versions/3/approve", json={"acknowledge_breaking": True}).json()
    assert seen == {"user": "LEE", "ack": True, "proc": "CALL MODELING.APPLY_DOMAIN_MODEL(%s, %s)", "args": ("m1", 3)}
    assert body == {"status": "APPROVED", "registry": {"applied": True, "registered": [1]}, "runs": []}

    def boom(*a):
        raise RuntimeError("warehouse suspended")

    mp.setattr(wb, "_source_call", boom)
    body = c.post("/api/hub/domain-models/m1/versions/3/approve", json={}).json()
    assert body["status"] == "APPROVED" and body["registry"] == {"applied": False, "error": "warehouse suspended"}
    assert c.post("/api/hub/domain-models/m1/versions/3/apply").status_code == 409


def test_api_validate_and_export(api):
    c, wb, mp = api
    body = c.post("/api/hub/domain-models/m1/draft/validate", json={"spec": spec_fixture()}).json()
    assert body["summary"] == {"entities": 2, "columns": 6, "relationships": 1, "retired": 1}
    mp.setattr(dm, "get_model", lambda s, m, v: {"model": {}, "current": {"version": 4, "spec": spec_fixture()}})
    r = c.get("/api/hub/domain-models/m1/export?format=ddl")
    assert r.status_code == 200 and "PRIMARY KEY" in r.text and "sales_model_v4.sql" in r.headers["content-disposition"]
    r = c.get("/api/hub/domain-models/m1/export?format=dbt")
    assert r.headers["content-type"] == "application/zip"
    r = c.get("/api/hub/domain-models/m1/export?format=pack")
    assert json.loads(r.text)["targets"][0]["table"] == "DIM_CUSTOMER"


def test_api_diff_defaults_to_the_approved_version(api):
    c, wb, mp = api
    mp.setattr(dm, "get_model", lambda s, m, v: {"model": {"approved_version": 3}, "current": None})
    mp.setattr(dm, "diff_versions", lambda s, m, a, b: {"from": a, "to": b})
    assert c.get("/api/hub/domain-models/m1/diff?to=4").json() == {"from": 3, "to": 4}
    assert c.get("/api/hub/domain-models/m1/diff?to=4&from=1").json() == {"from": 1, "to": 4}


def test_legacy_hub_never_writes_a_canonical_model(monkeypatch):
    from fastapi.testclient import TestClient

    import app.governance as gov
    import app.hub_models_api as hub
    import app.main as main

    async def open_door(request, call_next):
        return await call_next(request)

    class Db:
        user = "ana"

        def query(self, sql, params=()):
            if "FROM GOVERNANCE.DOMAIN_MODEL WHERE MODEL_ID" in sql:
                return [{"x": 1}]
            return []

        def execute(self, sql, params=()):
            raise AssertionError("must not write")

    monkeypatch.setattr(gov, "middleware", open_door)
    monkeypatch.setattr(hub, "_checked", lambda db, spec: ({"domain": "SALES", "mode": "MAP"}, []))
    main.app.dependency_overrides[main.current_db] = lambda: Db()
    try:
        r = TestClient(main.app).post("/api/hub/models", json={"design_id": "m1", "spec": {"domain": "SALES"}})
        assert r.status_code == 409 and "Model Workbench" in r.json()["detail"]
    finally:
        main.app.dependency_overrides.clear()


# ---------------------------------------------------------------- importers and impact

DDL = """
-- dimension
CREATE OR REPLACE TRANSIENT TABLE SILVER.SALES.DIM_CUSTOMER (
    CUSTOMER_ID NUMBER(38,0) NOT NULL COMMENT 'Key',
    EMAIL VARCHAR(200) UNIQUE,
    COUNTRY VARCHAR(2) DEFAULT 'US',
    "Created At" TIMESTAMP_NTZ(9),
    CONSTRAINT PK_DIM_CUSTOMER PRIMARY KEY (CUSTOMER_ID)
) CLUSTER BY (COUNTRY) COMMENT = 'Customers we sell to';
create table if not exists sales.fct_order (
    order_id number not null primary key,
    customer_id number references dim_customer (customer_id),
    amount number(12, 2),
    constraint uq_order unique (order_id, customer_id)
);
"""


def test_parse_ddl_reads_columns_keys_and_options():
    raw = spec_v2.normalize_v2({**__import__("services.modeling.importers", fromlist=["x"]).parse_ddl(DDL), "domain": "SALES"}, "m")
    cust, fct = raw["design"]["entities"]
    assert cust["entity_name"] == "DIM_CUSTOMER" and cust["database"] == "SILVER" and cust["schema"] == "SALES"
    assert cust["physical"] == {"cluster_by": ["COUNTRY"], "transient": True, "retention_days": None}
    assert cust["description"] == "Customers we sell to" and cust["business_keys"] == ["CUSTOMER_ID"]
    cols = {a["name"]: a for a in cust["attributes"]}
    assert cols["CUSTOMER_ID"]["is_pk"] and not cols["CUSTOMER_ID"]["nullable"] and cols["CUSTOMER_ID"]["description"] == "Key"
    assert cols["EMAIL"]["is_unique"] and cols["COUNTRY"]["default"] == "'US'" and cols["CREATED AT"]["datatype"] == "TIMESTAMP_NTZ(9)"
    assert fct["entity_name"] == "FCT_ORDER" and fct["schema"] == "SALES" and fct["unique_constraints"] == [["ORDER_ID", "CUSTOMER_ID"]]
    assert {a["name"]: a["datatype"] for a in fct["attributes"]}["AMOUNT"] == "NUMBER(12,2)"
    rel = raw["relationships"][0]
    assert (rel["from_entity"], rel["from_column"], rel["to_entity"], rel["to_column"]) == ("FCT_ORDER", "CUSTOMER_ID", "DIM_CUSTOMER", "CUSTOMER_ID")
    with pytest.raises(AssertionError, match="no CREATE TABLE"):
        __import__("services.modeling.importers", fromlist=["x"]).parse_ddl("select 1")


def test_merge_keeps_ids_and_documentation():
    from services.modeling import importers

    base = spec_fixture()
    cust = base["design"]["entities"][0]
    merged = importers.merge(base, importers.parse_ddl(DDL), "model-1")
    new_cust = next(e for e in merged["design"]["entities"] if e["entity_name"] == "DIM_CUSTOMER")
    assert new_cust["id"] == cust["id"] and new_cust["table_type"] == "DIMENSION" and new_cust["scd_type"] == "2"
    email = next(a for a in new_cust["attributes"] if a["name"] == "EMAIL")
    assert email["id"] == cust["attributes"][1]["id"] and email["pii"]["flag"]  # documentation the DDL lacks is kept
    diff = model_diff.diff(base, merged)
    assert ("ENTITY_REMOVED", "BREAKING") not in _kinds(diff)  # merge never drops
    replaced = importers.merge(base, importers.parse_ddl(DDL), "model-1", replace=True)
    assert next(e for e in replaced["design"]["entities"] if e["entity_name"] == "LEGACY_TABLE")["status"] == "RETIRED"


def test_impact_finds_downstream_references_and_fails_soft():
    from services.modeling import impact

    seen = []

    def query(sql, params):
        seen.append((sql, params))
        if "CONTRACT.STTM_LINE" in sql and params[0] == "DIM_CUSTOMER":
            return [{"ENTITY": "DIM_CUSTOMER", "COLUMN_NAME": "EMAIL", "RUN_ID": "r1"}]
        if "DQ_CHECKS_CATALOG" in sql:
            raise RuntimeError("Object does not exist")
        if "QA_TEST_CASE" in sql and params[0] == "DIM_CUSTOMER":
            return [{"ENTITY": "SILVER.SALES.DIM_CUSTOMER", "RUN_ID": None}]
        return []

    out = impact.analyze(query, [{"entity": "DIM_CUSTOMER", "column": "EMAIL", "kind": "COLUMN_RENAMED"},
                                 {"entity": "FCT_ORDER", "column": "", "kind": "ENTITY_RETIRED"}])
    first = out["items"][0]
    assert first["total"] == 2 and set(first["refs"]) == {"sttm", "qa_tests"} and out["unavailable"] == ["store_checks"]
    qa_sql, qa_params = next(x for x in seen if "QA_TEST_CASE" in x[0])
    assert "SQL_TEXT ILIKE" in qa_sql and qa_params == ["DIM_CUSTOMER", "EMAIL", "EMAIL"]
    assert any("CORE.WORKFLOW_RUN" in s and p == ["FCT_ORDER"] for s, p in seen)  # open runs for an entity-level change
    assert not any("CORE.WORKFLOW_RUN" in s and p == ["DIM_CUSTOMER"] for s, p in seen)
    assert out["totals"]["sttm"] == 1 and out["labels"]["dbt"] == "Generated dbt files"
