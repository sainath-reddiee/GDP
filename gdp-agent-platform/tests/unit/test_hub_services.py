"""Lifecycle hubs, service side: catalogs (profiles, STTM, models), external source intake, staged share landing,
readiness gates, contract validation, the model branch publisher and the governance rules of the hub routes."""

import json
import uuid
from datetime import datetime, timezone

import pytest

from services.catalog import models as cm
from services.catalog import profiles as cp
from services.catalog import quality as cq
from services.catalog import sttm as cs
from services.governance.policy import carries_secret, privilege_for
from services.source import pipelines, staged


class Row(dict):
    def as_dict(self):
        return dict(self)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class Session:
    """Records SQL; answers by the first matching substring in `answers`."""

    def __init__(self, answers=None, fail=()):
        self.answers, self.fail, self.calls = answers or {}, fail, []

    def sql(self, sql, params=None):
        self.calls.append((sql, params or []))
        session = self

        class Result:
            def collect(self_inner):
                for needle in session.fail:
                    if needle in sql:
                        raise RuntimeError(f"Object does not exist: {needle}")
                for needle, value in session.answers.items():
                    if needle in sql:
                        return [Row(r) for r in (value(params) if callable(value) else value)]
                return []
        return Result()


DOC = {"profiler_version": "3", "approximate": False, "row_count": 100, "columns": [
    {"column_name": "ID", "data_type": "NUMBER(38,0)", "family": "NUMBER", "semantic_type": "IDENTIFIER",
     "pii_classification": "NONE", "cardinality": "UNIQUE", "potential_key": True,
     "statistics": {"row_count": 100, "null_count": 0, "null_percentage": 0, "distinct_count": 100, "min": "1",
                    "max": "100", "histogram": [{"low": 1, "high": 50, "count": 50}]}},
    {"column_name": "EMAIL", "data_type": "VARCHAR(200)", "family": "TEXT", "semantic_type": "EMAIL",
     "pii_classification": "PII", "cardinality": "HIGH", "potential_key": False,
     "statistics": {"row_count": 100, "null_count": 10, "null_percentage": 10, "distinct_count": 90, "min": "a@x",
                    "max": "z@x", "frequency_distribution": [{"value": "***", "count": 2}]}},
]}


# ---------------------------------------------------------------- profiles


def test_profile_rows_match_insert_binds_and_never_store_pii_extremes():
    rows = cp.column_rows("p1", ("SRC", "DB", "S", "T"), DOC, "r1")
    binds = sum(e.count("?") for e in cp.EXPRS)
    assert len(cp.COLUMNS) == len(cp.EXPRS) and all(len(r) == binds for r in rows)
    email = cp.column_metrics(DOC["columns"][1], 100)
    assert email["min_value"] is None and email["max_value"] is None and email["histogram"] is None
    assert email["null_pct"] == 10 and email["cardinality_ratio"] == 1.0
    ident = cp.column_metrics(DOC["columns"][0], 100)
    assert ident["min_value"] == "1" and ident["histogram"]


def test_profile_record_never_raises_when_catalog_missing():
    session = Session(fail=("CORE.DATA_PROFILES",))
    assert cp.record(session, ("SRC", "DB", "S", "T"), DOC, None) is None
    ok = Session()
    assert cp.record(ok, ("SRC", "DB", "S", "T"), DOC, "r1")
    assert any("INSERT INTO CORE.DATA_PROFILES" in sql for sql, _ in ok.calls)


def test_profile_search_filters_are_bound_not_inlined():
    sql, params = cp.search_sql(domain="COMPANY", table="cust", run_id="r1", tag_keys=["db.s.t"], q="x",
                                bookmarked_by="ANA", sort="nulls", limit=999, offset=-5)
    assert "LIMIT 200 OFFSET 0" in sql and "AVG_NULL_PCT DESC" in sql
    assert "cust" not in sql and params.count("%cust%") == 1 and '["DB.S.T"]' in params and "ANA" in params


def test_profile_search_sorts_by_null_drift_since_the_previous_pass():
    sql, _ = cp.search_sql(sort="drift")
    assert "LEAD(AVG_NULL_PCT) OVER (PARTITION BY TABLE_FQN ORDER BY PROFILED_AT DESC)" in sql
    assert "ORDER BY NULL_DRIFT DESC NULLS LAST" in sql and "L.NULL_DRIFT" in sql
    assert "NULL_DRIFT DESC" not in cp.search_sql(sort="bogus")[0]


def test_drift_between_passes():
    before = [{"column_name": "A", "data_type": "NUMBER", "null_pct": 0, "distinct_count": 100},
              {"column_name": "B", "data_type": "VARCHAR", "null_pct": 1, "distinct_count": 10}]
    after = [{"column_name": "A", "data_type": "VARCHAR", "null_pct": 30, "distinct_count": 20},
             {"column_name": "C", "data_type": "DATE", "null_pct": 0, "distinct_count": 5}]
    d = cp.drift_between(before, after)
    assert d["changed"] and d["columns"]["C"] == ["added"] and d["columns"]["B"] == ["removed"]
    assert any(r.startswith("type") for r in d["columns"]["A"]) and any(r.startswith("nulls") for r in d["columns"]["A"])


def test_gx_checks_from_soda_suggestions():
    from services.quality.gx import render_suite

    checks = [{"check": "row_count > 0"}, {"check": "duplicate_count(ID) = 0"}, {"check": "missing_count(ID) = 0"},
              {"check": "invalid_count(STATUS) = 0", "valid_values": ["A", "B"]},
              {"check": "invalid_percent(CODE) < 5%", "valid_regex": "^[A-Z]{3}$"}, {"check": "freshness(TS) < 1d"}]
    types = [e["type"] for e in render_suite("T", cp.gx_checks(checks))["expectations"]]
    assert types == ["expect_table_row_count_to_be_between", "expect_column_values_to_be_unique",
                     "expect_column_values_to_not_be_null", "expect_column_values_to_be_in_set",
                     "expect_column_values_to_match_regex", "unexpected_rows_expectation"]


# ---------------------------------------------------------------- STTM catalog


LINES = [{"TARGET_COLUMN": "ID", "SOURCE_TABLE": "T", "SOURCE_COLUMN": "ID", "MAPPING_TYPE": "DIRECT",
          "TARGET_DATATYPE": "NUMBER"},
         {"TARGET_COLUMN": "NAME", "SOURCE_TABLE": "T", "SOURCE_COLUMN": "NM", "MAPPING_TYPE": "TRANSFORM",
          "TRANSFORMATION": "TRIM(NM)", "TARGET_DATATYPE": "VARCHAR"}]


def test_catalog_id_matches_the_v036_backfill_rule():
    import hashlib

    digest = hashlib.md5("CRM|t-1".encode()).hexdigest()
    expected = f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:]}"
    assert cs.catalog_id("CRM", "t-1") == expected


def test_sttm_edits_and_diff():
    lines, touched = cs.apply_edits(LINES, [{"target_column": "name", "transformation": "UPPER(TRIM(NM))",
                                             "business_rule": "upper case"}])
    assert touched == ["NAME"] and lines[1]["transformation"] == "UPPER(TRIM(NM))"
    d = cs.diff(LINES, lines)
    assert d["changed"][0]["fields"] == ["transformation", "business_rule"] and not d["added"] and not d["removed"]
    d2 = cs.diff(LINES, lines[:1] + [{**lines[1], "target_column": "FULL_NAME"}])
    assert [l["target_column"] for l in d2["added"]] == ["FULL_NAME"] and d2["removed"][0]["target_column"] == "NAME"


@pytest.mark.parametrize("edit, message", [
    ({"target_column": "NOPE", "transformation": "X"}, "not a target column"),
    ({"target_column": "ID", "transformation": "1; DROP TABLE X"}, "one expression"),
    ({"target_column": "ID", "transformation": "(DELETE FROM X)"}, "MAPPING_VALIDATION"),
    ({"target_column": "ID", "join_logic": "X"}, "cannot edit"),
    ({"target_column": "ID", "target_datatype": "VARCHAR; DROP"}, "not a data type"),
    ({"target_column": "ID", "source_table": "A B"}, "plain Snowflake name"),
    ({"target_column": "ID", "filter_rule": "1; DELETE"}, "one expression"),
    ({"target_column": "ID", "mapping_type": "MAGIC"}, "mapping_type"),
])
def test_sttm_edits_are_checked(edit, message):
    with pytest.raises((AssertionError, ValueError), match=message):
        cs.apply_edits(LINES, [edit])


def test_every_line_field_is_editable_and_names_are_upper_cased():
    lines, touched = cs.apply_edits(LINES, [{"target_column": "id", "source_system": "crm", "source_table": "accounts",
                                             "source_column": "acct_id", "source_datatype": "number(38,0)",
                                             "target_datatype": "number(38, 0)", "mapping_type": "transform",
                                             "transformation": "TRY_TO_NUMBER(ACCT_ID)", "business_rule": "r",
                                             "business_definition": "d", "filter_rule": "ACCT_ID IS NOT NULL"}])
    line = lines[0]
    assert touched == ["ID"] and line["source_table"] == "ACCOUNTS" and line["source_column"] == "ACCT_ID"
    assert line["target_datatype"] == "NUMBER(38, 0)" and line["mapping_type"] == "TRANSFORM"
    assert line["filter_rule"] == "ACCT_ID IS NOT NULL"


def test_plan_edit_adds_removes_and_sets_design_filters_and_joins():
    entry = {"lines": [{"target_column": "ID", "source_table": "A", "source_column": "ID"},
                       {"target_column": "NAME", "source_table": "B", "source_column": "NAME"},
                       {"target_column": "OLD", "source_table": "A", "source_column": "X"}],
             "table_design": {"grain": "one row per id"}}
    plan = cs.plan_edit(entry, {
        "add_lines": [{"target_column": "region", "target_datatype": "varchar(20)", "source_table": "B", "source_column": "REGION",
                       "mapping_type": "DIRECT"}],
        "remove_lines": ["OLD"],
        "design": {"business_keys": ["id"], "scd_type": "2", "incremental_strategy": "MERGE",
                   "filters": [{"table": "a", "condition": "A.STATUS <> 'DELETED'", "reason": "soft deletes"}]},
        "joins": {"driving_table": "A", "joins": [{"left_table": "A", "right_table": "B", "keys": ["ID=A_ID"], "join_type": "LEFT"}]},
    }, {"A": ["ID", "X", "STATUS"], "B": ["A_ID", "NAME", "REGION"]})
    cols = [l["target_column"] for l in plan["lines"]]
    assert cols == ["ID", "NAME", "REGION"] and plan["added"] == ["REGION"] and plan["removed"] == ["OLD"]
    d = plan["design"]
    assert d["business_keys"] == ["ID"] and d["scd_type"] == "2" and d["incremental_strategy"] == "merge"
    assert d["filters"] == [{"table": "A", "condition": "A.STATUS <> 'DELETED'", "reason": "soft deletes"}]
    assert d["join_graph"]["driving_table"] == "A" and d["join_graph"]["joins"][0]["condition"] == "A.ID = B.A_ID"
    by = {l["target_column"]: l for l in plan["lines"]}
    assert by["NAME"]["join_logic"] == "FROM A LEFT JOIN B ON A.ID = B.A_ID" and by["ID"]["join_logic"] == "FROM A"
    assert "added REGION" in plan["summary"] and "join plan" in plan["summary"]


@pytest.mark.parametrize("payload, message", [
    ({}, "nothing to change"),
    ({"add_lines": [{"target_column": "ID"}]}, "already a target column"),
    ({"remove_lines": ["ID"]}, "at least one target column"),
    ({"design": {"business_keys": ["NOPE"]}}, "not target columns"),
    ({"design": {"filters": [{"condition": "1; DROP TABLE X"}]}}, "one expression"),
    ({"design": {"owner": "x"}}, "cannot edit"),
    ({"joins": {"driving_table": "A", "joins": [{"left_table": "A", "right_table": "Z", "keys": ["ID=ID"]}]}}, "unknown table"),
])
def test_plan_edit_rejects(payload, message):
    entry = {"lines": [{"target_column": "ID", "source_table": "A", "source_column": "ID"}], "table_design": {}}
    with pytest.raises(AssertionError, match=message):
        cs.plan_edit(entry, payload, {"A": ["ID"]})


def test_semicolon_inside_a_literal_is_data():
    lines, _ = cs.apply_edits(LINES, [{"target_column": "ID", "transformation": "IFF(ID = 1, 'a;b', 'c')"}])
    assert lines[0]["transformation"] == "IFF(ID = 1, 'a;b', 'c')"


def test_sttm_export_is_a_dbt_project():
    files = cs.export_files({"catalog_id": "c1", "version": 2, "source_system": "CRM", "target_entity": "DB.S.DIM_X",
                             "table_design": {"business_keys": ["ID"], "grain": "one row per ID"}, "lines": LINES})
    assert "dbt_project.yml" in files and "models/marts/gdp/dim_x.sql" in files
    assert "trim(nm)" in files["models/marts/gdp/dim_x.sql"].lower()


def test_snapshot_writes_next_version_and_swallows_errors():
    session = Session({
        "FROM CORE.STTM_REGISTRY S": [{"STTM_ID": "s1", "RUN_ID": "r1", "DOMAIN_ID": "d1", "TARGET_TABLE_ID": "t1",
                                           "STATUS": "REVIEW", "TABLE_DESIGN": "{}", "DOMAIN_NAME": "COMPANY",
                                           "TARGET_DATABASE": "DB", "TARGET_SCHEMA": "S", "TARGET_TABLE": "X"}],
        "FROM CORE.STTM_LINE": [{"SOURCE_SYSTEM": "CRM", "TARGET_COLUMN": "ID", "SOURCE_TABLE": "T"}],
        "COALESCE(MAX(VERSION), 0)": [{"V": 3}],
    })
    out = cs.snapshot(session, "s1", "GENERATED", "note")
    assert out == {"catalog_id": cs.catalog_id("CRM", "t1"), "version": 4, "lines": 1}
    insert = [p for sql, p in session.calls if sql.lstrip().startswith("INSERT INTO CORE.STTM_CATALOG")][0]
    assert insert[9] == "DB.S.X" and insert[11] == "GENERATED"
    assert cs.snapshot(Session(fail=("CORE.STTM_REGISTRY",)), "s1") is None
    assert cs.snapshot_run(Session(fail=("CORE.STTM_REGISTRY",)), "r1", "TRANSFORM_EDIT") is None


# ---------------------------------------------------------------- models


def _spec(**over):
    raw = {"domain": "sales_x", "mode": "NEW", "target_schema": "SALES", "target_database": "SILVER",
           "source_tables": ["RAW.CRM.ORDERS", "RAW.CRM.CUSTOMERS"],
           "design": {"entities": [
               {"entity_name": "dim_customer", "kind": "DIMENSION", "business_keys": ["CUSTOMER_ID"],
                "attributes": [{"name": "customer_id", "datatype": "number(38,0)", "is_pk": True, "nullable": False,
                                "source_columns": ["CUSTOMERS.ID"]},
                               {"name": "attrs", "datatype": "VARIANT", "source_columns": ["CUSTOMERS.ATTRS"]}]},
               {"entity_name": "fct_order", "kind": "FACT", "business_keys": ["ORDER_ID"],
                "attributes": [{"name": "order_id", "datatype": "NUMBER(38,0)", "is_pk": True,
                                "source_columns": ["ORDERS.ID"]},
                               {"name": "customer_id", "datatype": "NUMBER(38,0)", "source_columns": ["ORDERS.CUST"]}]}]},
           "relationships": [{"from_entity": "fct_order", "from_column": "customer_id", "to_entity": "dim_customer",
                              "to_column": "customer_id"}]}
    raw.update(over)
    return cm.normalize_spec(raw)


def test_spec_normalized_and_valid():
    spec = _spec()
    assert spec["domain"] == "SALES_X" and spec["relationships"][0]["to_entity"] == "DIM_CUSTOMER"
    assert not cm.blocking(cm.validate_spec(spec, ["COMPANY"]))


@pytest.mark.parametrize("over, existing, message", [
    ({"mode": "MAP"}, ["COMPANY"], "not a registered domain"),
    ({"mode": "NEW", "domain": "COMPANY"}, ["COMPANY"], "already exists"),
    ({"mode": "FORK", "parent_domain": "NOPE"}, ["COMPANY"], "existing domain"),
    ({"relationships": [{"from_entity": "FCT_ORDER", "from_column": "NOPE", "to_entity": "DIM_CUSTOMER",
                         "to_column": "CUSTOMER_ID"}]}, [], "NOPE is not a column"),
])
def test_spec_rules(over, existing, message):
    issues = cm.blocking(cm.validate_spec(_spec(**over), existing))
    assert any(message in i["message"] for i in issues), issues


def test_iceberg_types_and_ddl():
    assert cm.iceberg_type("NUMBER(38,0)") == "DECIMAL(38,0)" and cm.iceberg_type("NUMBER(9,0)") == "INT"
    assert cm.iceberg_type("VARIANT") == "STRING" and cm.iceberg_type("TIMESTAMP_TZ") == "TIMESTAMP_LTZ(6)"
    assert cm.iceberg_type("VARCHAR(20)") == "STRING" and cm.iceberg_type("NUMBER(12,2)") == "DECIMAL(12,2)"
    ddl = cm.render_ddl(_spec(target_platform="ICEBERG", external_volume="VOL"))
    assert "CREATE ICEBERG TABLE IF NOT EXISTS SILVER.SALES.FCT_ORDER" in ddl and "EXTERNAL_VOLUME = 'VOL'" in ddl
    assert "FOREIGN KEY (CUSTOMER_ID) REFERENCES SILVER.SALES.DIM_CUSTOMER (CUSTOMER_ID)" in ddl
    assert "ATTRS STRING" in ddl
    assert "CREATE TABLE IF NOT EXISTS SILVER.SALES.DIM_CUSTOMER" in cm.render_ddl(_spec())


def test_pack_new_fork_and_map():
    new = cm.render_pack(_spec())
    assert new["domain"]["name"] == "SALES_X" and {t["table"] for t in new["targets"]} == {"DIM_CUSTOMER", "FCT_ORDER"}
    assert not cm.pack_problems(new)
    base = {"domain": {"name": "COMPANY", "description": "c"}, "targets": [{"schema": "COMPANY", "table": "COMPANY_CORE",
                                                                             "columns": [{"name": "X"}]}],
            "knowledge": [{"key": "company.glossary.duns", "type": "GLOSSARY", "title": "DUNS", "content": "d"}]}
    fork = cm.render_pack(_spec(mode="FORK", parent_domain="COMPANY", target_schema=""), base)
    assert fork["domain"]["forked_from"] == "COMPANY" and fork["domain"]["name"] == "SALES_X"
    assert {t["table"] for t in fork["targets"]} == {"COMPANY_CORE", "DIM_CUSTOMER", "FCT_ORDER"}
    assert any(k["key"] == "sales_x.glossary.duns" for k in fork["knowledge"])
    assert base["domain"]["name"] == "COMPANY"  # the base pack is not modified
    mapped = cm.render_pack(_spec(mode="MAP", domain="COMPANY"), base)
    assert mapped["domain"]["name"] == "COMPANY" and len(mapped["targets"]) == 3 and not cm.pack_problems(mapped)


def test_dbt_files_and_branch():
    files = cm.render_dbt(_spec(), "dbt")
    assert "dbt/models/staging/sales_x/_sources.yml" in files and "dbt/models/marts/sales_x/fct_order.sql" in files
    assert "ref('stg_sales_x__orders')" in files["dbt/models/marts/sales_x/fct_order.sql"]
    schema = files["dbt/models/marts/sales_x/_schema.yml"]
    assert "relationships:" in schema and "to: ref('dim_customer')" in schema
    assert cm.branch_name("Sales_X", datetime(2026, 10, 11, 9, 5, 3, tzinfo=timezone.utc)) == \
        "feature/model-sales-x-20261011090503"
    arts = cm.artifacts(_spec(), cm.render_pack(_spec()))
    assert "domain/sales_x/domain_pack.json" in arts and "domain/sales_x/model.sql" in arts


def test_spec_from_pack_links_spokes_to_hub():
    pack = {"domain": {"name": "COMPANY", "silver_schema": "COMPANY"}, "targets": [
        {"table": "COMPANY_CORE", "table_type": "HUB", "business_keys": ["SOURCE_UNIQUE_ID"],
         "columns": [{"name": "COMPANY_CORE_SKEY", "type": "NUMBER(19,0)"}, {"name": "SOURCE_UNIQUE_ID"}],
         "model_spec": {"hub": "COMPANY_CORE"}},
        {"table": "COMPANY_ADDRESS", "columns": [{"name": "COMPANY_CORE_SKEY", "type": "NUMBER(19,0)"}],
         "model_spec": {"hub": "COMPANY_CORE", "hub_fk": "COMPANY_CORE_SKEY"}}]}
    spec = cm.spec_from_pack(pack, "MAP", "COMPANY", "COMPANY")
    assert len(spec["design"]["entities"]) == 2
    assert spec["relationships"] == [{"from_entity": "COMPANY_ADDRESS", "from_column": "COMPANY_CORE_SKEY",
                                      "to_entity": "COMPANY_CORE", "to_column": "COMPANY_CORE_SKEY", "cardinality": "N:1"}]


def test_publish_model_branch_reads_files_from_the_table(monkeypatch):
    from services.dbt import publish

    pushed = {}

    def fake_publish(request, origin, base, head, files, **kw):
        pushed.update(origin=origin, base=base, head=head, files=files, draft=kw.get("draft"))
        return {"status": "PUBLISHED", "pull_request": {"url": "https://github.com/o/r/pull/7"}}

    monkeypatch.setattr(publish.github, "publish", fake_publish)
    monkeypatch.setattr(publish.github, "urllib_request", lambda token: None)
    monkeypatch.setattr(publish, "_token", lambda: "t")
    session = Session({
        "FROM CORE.MODEL_DESIGNS": [{"DOMAIN_NAME": "SALES_X", "MODE": "NEW", "STATUS": "FINALIZED",
                                           "ARTIFACTS": json.dumps({"domain/sales_x/domain_pack.json": "{}"})}],
        "FROM CORE.REPO": [{"GIT_URL": "https://github.com/o/r", "BRANCH": "main", "PROVIDER": "GITHUB"}],
    })
    payload = {"model_branch": {"design_id": "d1", "version": 2, "repo_id": "r1",
                                "head": "feature/model-sales-x-20261011090503"}}
    result = publish.publish_dbt_pr(session, "", json.dumps(payload))
    assert result["status"] == "PUBLISHED" and pushed["files"] == {"domain/sales_x/domain_pack.json": "{}"}
    assert pushed["draft"] is True and pushed["base"] == "main"
    update = [p for sql, p in session.calls if "UPDATE CORE.MODEL_DESIGNS" in sql][0]
    assert update[0] == "PUBLISHED" and update[3] == "https://github.com/o/r/pull/7"
    with pytest.raises(AssertionError, match="feature/model-"):
        publish.publish_model_branch(session, {**payload["model_branch"], "head": "main-hack"})
    draft = Session({"FROM CORE.MODEL_DESIGNS": [{"DOMAIN_NAME": "X", "MODE": "NEW", "STATUS": "DRAFT",
                                                        "ARTIFACTS": "{}"}]})
    with pytest.raises(AssertionError, match="finalized"):
        publish.publish_model_branch(draft, payload["model_branch"])


# ---------------------------------------------------------------- external pipelines


ANSWERS = {"source_name": "crm_oracle", "source_type": "ORACLE", "connection_type": "JDBC", "host": "db.example.com",
           "port": "1521", "auth_user": "svc", "auth_secret": "s3cret", "objects": "ACCOUNTS\nCONTACTS",
           "frequency": "DAILY", "target_landing_table": "landing.crm_oracle__accounts", "base_url": "ignored"}


def test_default_form_is_valid_and_answers_split():
    assert pipelines.check_form(pipelines.DEFAULT_FORM) == []
    public, secrets, errors = pipelines.validate(pipelines.DEFAULT_FORM, ANSWERS)
    assert not errors and secrets == {"auth_secret": "s3cret"} and "auth_secret" not in public
    assert public["port"] == 1521 and "base_url" not in public  # hidden by show_if
    row = pipelines.catalog_row("id1", public)
    assert row["source_name"] == "CRM_ORACLE" and row["target_landing_table"] == "LANDING.CRM_ORACLE__ACCOUNTS"
    assert row["schedule"] == {"frequency": "DAILY"} and row["connection_metadata"]["host"] == "db.example.com"
    assert "source_name" not in row["connection_metadata"]
    assert pipelines.mask({"secret_sealed": b"x", "a": 1}) == {"a": 1, "has_secret": True}


def test_intake_errors():
    _, _, errors = pipelines.validate(pipelines.DEFAULT_FORM, {**ANSWERS, "source_type": "MAINFRAME", "port": "abc",
                                                               "frequency": "CRON", "cron": "every day",
                                                               "target_landing_table": "x;drop", "host": ""})
    assert set(errors) == {"source_type", "port", "cron", "target_landing_table", "host"}


def test_bad_form_falls_back_to_default():
    assert pipelines.form_or_default({"sections": [{"fields": [{"key": "Bad Key", "type": "x"}]}]}) is pipelines.DEFAULT_FORM
    assert pipelines.check_form({"sections": []}) == ["the form must ask for source_name", "the form must ask for source_type",
                                                      "the form must ask for target_landing_table"]


def test_ingestion_secret_seals_per_row():
    from services.common import secretbox

    key = "k" * 32
    sealed = secretbox.seal('{"auth_secret": "x"}', key, secretbox.INGESTION_SECRET, "id1")
    assert secretbox.open_secret(sealed, key, secretbox.INGESTION_SECRET, "id1") == '{"auth_secret": "x"}'
    with pytest.raises(secretbox.SecretError):
        secretbox.open_secret(sealed, key, secretbox.INGESTION_SECRET, "id2")


# ---------------------------------------------------------------- staged share landing


def test_staged_landing_statements():
    run = str(uuid.uuid4())
    steps = staged.landing_steps("AI_PLATFORM", run, '"SHARE_DB"."S"."T"', '"AI_PLATFORM"."LANDING"."X__T"',
                                 "X__T", "As-is landing of 'T'")
    location = f"@AI_PLATFORM.CORE.LANDING_STAGE/runs/{run}/X__T/"
    names = [n for n, _ in steps]
    assert names == ["clear the stage", "unload", "create", "load", "swap", "swap", "swap", "clean up the stage"]
    sql = dict(steps)
    assert steps[0][1] == f"REMOVE {location}" and steps[-1][1] == f"REMOVE {location}"
    assert sql["unload"].startswith(f"COPY INTO {location}data_ FROM (SELECT * FROM \"SHARE_DB\".\"S\".\"T\")")
    assert "COMPRESSION = 'SNAPPY'" in sql["unload"] and "HEADER = TRUE" in sql["unload"]
    # rows go into the loading table, never straight into the landing table a reader may be using
    assert sql["create"].startswith('CREATE OR REPLACE TABLE "AI_PLATFORM"."LANDING"."X__T__LOADING"')
    assert "LIMIT 0" in sql["create"] and "'T''" in sql["create"]
    assert sql["load"].startswith('COPY INTO "AI_PLATFORM"."LANDING"."X__T__LOADING"')
    assert "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE" in sql["load"] and "AI_PLATFORM.CORE.PARQUET_SNAPPY" in sql["load"]
    assert [q for n, q in steps if n == "swap"] == [
        'CREATE TABLE IF NOT EXISTS "AI_PLATFORM"."LANDING"."X__T" LIKE "AI_PLATFORM"."LANDING"."X__T__LOADING"',
        'ALTER TABLE "AI_PLATFORM"."LANDING"."X__T__LOADING" SWAP WITH "AI_PLATFORM"."LANDING"."X__T"',
        'DROP TABLE IF EXISTS "AI_PLATFORM"."LANDING"."X__T__LOADING"']
    assert staged.landing_statements("AI_PLATFORM", run, '"S"."T"', '"A"."L"."X"', "X", "c") == \
        [q for _, q in staged.landing_steps("AI_PLATFORM", run, '"S"."T"', '"A"."L"."X"', "X", "c")]
    with pytest.raises(AssertionError):
        staged.stage_prefix("AI_PLATFORM", "../x", "T")
    with pytest.raises(AssertionError):
        staged.stage_prefix("AI_PLATFORM", run, "t'; drop")


@pytest.mark.parametrize("types, safe, why", [
    (["NUMBER", "TEXT", "DATE", "TIMESTAMP_NTZ", "BOOLEAN", "FLOAT", "TIME"], True, ""),
    (["NUMBER", "TIMESTAMP_TZ"], False, "TIMESTAMP_TZ"),            # Snowflake 100171 on the unload
    (["TIMESTAMP_LTZ"], False, "TIMESTAMP_LTZ"),
    (["VARIANT", "OBJECT", "ARRAY"], False, "ARRAY, OBJECT, VARIANT"),  # would come back as JSON text
    (["BINARY", "GEOGRAPHY", "GEOMETRY", "VECTOR"], False, "BINARY, GEOGRAPHY, GEOMETRY, VECTOR"),
])
def test_only_tables_whose_types_round_trip_go_through_parquet(types, safe, why):
    cols = [{"COLUMN_NAME": f"C{i}", "DATA_TYPE": t} for i, t in enumerate(types)]
    ok, reason = staged.parquet_safe(cols)
    assert ok is safe and why in reason
    assert staged.parquet_safe([{"COLUMN_NAME": "id", "DATA_TYPE": "NUMBER"},
                                {"COLUMN_NAME": "ID", "DATA_TYPE": "NUMBER"}]) == (False, "it has column names that differ only by case")
    assert staged.parquet_safe([])[0] is False


def test_copy_results_give_the_counts():
    assert staged.count_from([Row({"rows_unloaded": 7, "input_bytes": 1})], "rows_unloaded") == 7
    assert staged.count_from([Row({"file": "a", "ROWS_LOADED": 3}), Row({"file": "b", "ROWS_LOADED": 4})], "rows_loaded") == 7
    assert staged.count_from([Row({"status": "ok"})], "rows_unloaded") is None and staged.count_from([], "x") is None


class _Spec:
    copies, landing_database, landing_schema = True, "AI_PLATFORM", "LANDING"

    def create_sql(self, name, select, comment):
        return f"CREATE OR REPLACE TABLE AI_PLATFORM.LANDING.{name} AS {select}"


class _Adapter:
    database, schema = "SHARE_DB", "S"

    def count_sql(self, name):
        return f"SELECT COUNT(*) AS N FROM SOURCE_{name}"

    def select_sql(self, name):
        return f"SELECT * FROM SOURCE_{name}"


def _land(session, columns, method="STAGED_PARQUET"):
    from services.source.procedures import _land_object

    return _land_object(session, "11111111-1111-1111-1111-111111111111", {"SOURCE_SYSTEM_NAME": "NVIDIA", "SOURCE_SYSTEM_ID": "s1"},
                        _Adapter(), _Spec(), "CONTACTS", columns, method)


COLS = [{"COLUMN_NAME": "ID", "DATA_TYPE": "NUMBER", "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 38,
         "NUMERIC_SCALE": 0, "ORDINAL_POSITION": 1, "IS_NULLABLE": "NO", "COMMENT": "x" * 5000}]


def test_a_table_with_tz_columns_lands_by_ctas_with_the_reason():
    tz = COLS + [{"COLUMN_NAME": "UPDATED_AT", "DATA_TYPE": "TIMESTAMP_TZ", "CHARACTER_MAXIMUM_LENGTH": None,
                  "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None, "ORDINAL_POSITION": 2, "IS_NULLABLE": "YES", "COMMENT": None}]
    session = Session({"CURRENT_DATABASE": [{"D": "AI_PLATFORM"}], "COUNT(*)": [{"N": 5}], "LAST_QUERY_ID": [{"Q": "q1"}]})
    out = _land(session, tz)
    assert out["status"] == "COMPLETE" and out["method"] == "CTAS" and "TIMESTAMP_TZ" in out["note"]
    sqls = [q for q, _ in session.calls]
    assert not any("PARQUET" in q for q in sqls) and any(q.startswith("CREATE OR REPLACE TABLE AI_PLATFORM.LANDING.") for q in sqls)


def test_a_staged_table_counts_the_rows_the_unload_read_and_clips_long_comments():
    session = Session({"CURRENT_DATABASE": [{"D": "AI_PLATFORM"}], "COPY INTO @": [{"rows_unloaded": 5}],
                       'SELECT COUNT(*) AS N FROM "AI_PLATFORM"': [{"N": 5}], "LAST_QUERY_ID": [{"Q": "q1"}]})
    out = _land(session, COLS)
    sqls = [q for q, _ in session.calls]
    assert out["status"] == "COMPLETE" and out["method"] == "STAGED_PARQUET" and out["source_rows"] == 5
    assert not any("FROM SOURCE_CONTACTS" in q and "COUNT" in q for q in sqls)   # no second read of the share
    assert any("SWAP WITH" in q for q in sqls)
    registered = [p for q, p in session.calls if "LANDING_COLUMN_REGISTRY" in q]
    assert registered and all(len(v) <= 4000 for v in registered[0] if isinstance(v, str))


def test_a_failed_step_is_named_and_the_stage_cleanup_never_fails_a_landed_table():
    session = Session({"CURRENT_DATABASE": [{"D": "AI_PLATFORM"}], "LAST_QUERY_ID": [{"Q": "q"}]}, fail=("SWAP WITH",))
    out = _land(session, COLS)
    assert out["status"] == "FAILED" and out["error"].startswith("swap failed:")
    session = Session({"CURRENT_DATABASE": [{"D": "AI_PLATFORM"}], "COPY INTO @": [{"rows_unloaded": 2}],
                       'SELECT COUNT(*) AS N FROM "AI_PLATFORM"': [{"N": 2}], "LAST_QUERY_ID": [{"Q": "q"}]})
    original = session.sql
    removes = []

    def sql(q, params=None):
        if q.startswith("REMOVE"):
            removes.append(q)
            if len(removes) == 2:
                raise RuntimeError("stage busy")
        return original(q, params)
    session.sql = sql
    assert _land(session, COLS)["status"] == "COMPLETE"


def test_execute_landing_rejects_unknown_method():
    from services.source.procedures import _execute_landing

    with pytest.raises(AssertionError, match="landing method"):
        _execute_landing(Session(), "r1", "MAGIC")


# ---------------------------------------------------------------- readiness and contracts


def test_readiness_gates():
    ready = cq.readiness({"health": 95, "failed": 0}, {"tests": 4, "failed": 0, "errors": 0},
                         {"decision": "APPROVED", "decided_by": "ANA"})
    assert ready["status"] == "READY" and ready["score"] == 100
    blocked = cq.readiness({"health": 70, "failed": 2}, None, None, open_incidents=1)
    assert blocked["status"] == "BLOCKED"
    assert [g["status"] for g in blocked["gates"]] == ["FAIL", "PENDING", "PENDING", "FAIL"]
    assert cq.readiness(None, None, None)["status"] == "PENDING"


def test_contract_yaml():
    good = "domain: sales\ntables:\n  - name: ORDERS\n    business_keys: [ORDER_ID]\n    columns:\n      - name: ORDER_ID\n        required: true\n"
    out = cq.validate_contract("orders.yaml", good)
    assert out["kind"] == "yaml" and out["ok"] and out["summary"] == {"domain": "SALES", "tables": 1, "columns": 1}
    bad = cq.validate_contract("x.yml", "domain: sales\ntables:\n  - name: T\n    business_keys: [K]\n    columns:\n      - name: A\n      - name: a\n")
    assert not bad["ok"] and len(bad["problems"]) == 2
    assert not cq.validate_contract("x.yml", "tables: [")["ok"]
    assert not cq.validate_contract("x.yml", "- just a list")["ok"]


def test_contract_markdown_uses_the_platform_parser():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "snowflake/skills/gdp/dbt-onboard-source/references/company-contract.md").read_text(encoding="utf-8")
    out = cq.validate_contract("company-contract.md", text)
    assert out["kind"] == "markdown" and out["ok"] and out["summary"]["columns"] > 0
    assert not cq.validate_contract("empty.md", "# nothing here")["ok"]


# ---------------------------------------------------------------- governance


@pytest.mark.parametrize("method, path, privilege", [
    ("POST", "/api/hub/sources/share/onboard", "RUN.CREATE"),
    ("POST", "/api/hub/pipelines", "SOURCE.CONNECT"),
    ("PATCH", "/api/hub/pipelines/abc", "SOURCE.CONNECT"),
    ("POST", "/api/hub/pipelines/validate", None),
    ("PUT", "/api/hub/intake-form", "CONFIG.EDIT"),
    ("POST", "/api/hub/profiles/reprofile", "PROFILE.RUN"),
    ("POST", "/api/hub/profiles/bookmarks", None),
    ("POST", "/api/hub/sttm/abc/edit", "STTM.EDIT"),
    ("GET", "/api/hub/sttm/abc/export", None),
    ("POST", "/api/hub/models/propose", "AI.USE"),
    ("POST", "/api/hub/models/render", None),
    ("POST", "/api/hub/models", "MODEL.EDIT"),
    ("POST", "/api/hub/models/abc/finalize", "MODEL.EDIT"),
    ("POST", "/api/hub/models/abc/publish", "DBT.EDIT"),
    ("POST", "/api/hub/onboarding/validate", None),
    ("GET", "/api/hub/profiles", None),
])
def test_hub_routes_are_governed(method, path, privilege):
    found, _, matched = privilege_for(method, path)
    assert found == privilege and matched


def test_pipeline_secrets_are_never_queued():
    assert carries_secret("POST", "/api/hub/pipelines") and carries_secret("PATCH", "/api/hub/pipelines/x")
    assert not carries_secret("POST", "/api/hub/pipelines/validate")


def test_edit_version_writes_an_approved_version_and_syncs_the_run(monkeypatch):
    import services.workflow.procedures as wp

    events = []
    monkeypatch.setattr(wp, "_get_run", lambda s, r: {"RUN_ID": r})
    monkeypatch.setattr(wp, "_record_run_event", lambda s, run, actor, text, detail: events.append((run["RUN_ID"], text, detail)))
    latest = {"CATALOG_ID": "c1", "VERSION": 3, "STTM_ID": "s1", "RUN_ID": "r1", "DOMAIN_ID": "d1", "DOMAIN_NAME": "COMPANY",
              "SOURCE_SYSTEM": "CRM", "TARGET_TABLE_ID": "t1", "TARGET_ENTITY": "DB.S.DIM", "TABLE_DESIGN": "{}",
              "LINES": json.dumps([{"target_column": "ID", "source_table": "A", "source_column": "ID", "mapping_type": "DIRECT"},
                                   {"target_column": "OLD", "source_table": "A", "source_column": "X", "mapping_type": "DIRECT"}])}
    session = Session({
        "FROM CORE.STTM_CATALOG WHERE CATALOG_ID = ? ORDER BY VERSION DESC LIMIT 1": [latest],
        "COALESCE(MAX(VERSION), 0)": [{"V": 3}],
        "FROM CORE.STTM_REGISTRY S JOIN CORE.WORKFLOW_RUN": [{"STTM_ID": "s1", "RUN_ID": "r1", "ARCHIVED": False, "DELETED_AT": None}],
        "FROM CORE.STTM_LINE WHERE STTM_ID": [
            {"STTM_LINE_ID": "L1", "TARGET_COLUMN": "ID", "TARGET_DOMAIN": "COMPANY", "TARGET_DATABASE": "DB", "TARGET_SCHEMA": "S", "TARGET_TABLE": "DIM"},
            {"STTM_LINE_ID": "L2", "TARGET_COLUMN": "OLD", "TARGET_DOMAIN": "COMPANY", "TARGET_DATABASE": "DB", "TARGET_SCHEMA": "S", "TARGET_TABLE": "DIM"}],
    })
    out = cs.edit_version(session, "c1", json.dumps({
        "base_version": 3, "note": "fix id", "edits": [{"target_column": "ID", "transformation": "TRIM(ID)", "mapping_type": "TRANSFORM"}],
        "add_lines": [{"target_column": "NEW_COL", "source_table": "A", "source_column": "Y"}], "remove_lines": ["OLD"],
        "design": {"filters": [{"table": "A", "condition": "A.ACTIVE"}]}}), approved=True)
    assert out["version"] == 4 and out["status"] == "APPROVED" and out["run_updated"] is True
    sql = [c[0] for c in session.calls]
    insert = next(c for c in session.calls if "INSERT INTO CORE.STTM_CATALOG" in c[0])
    assert "APPROVED" in insert[1] and "CATALOG_EDIT" in insert[1] and "fix id" in insert[1]
    assert any(s.startswith("DELETE FROM CORE.STTM_LINE") for s in sql)
    update = next(c for c in session.calls if c[0].startswith("UPDATE CORE.STTM_LINE SET"))
    assert "TRIM(ID)" in update[1] and update[1][-1] == "L1" and "FILTER_RULE = ?" in update[0]
    added = next(c for c in session.calls if "INSERT INTO CORE.STTM_LINE" in c[0])
    assert "NEW_COL" in added[1] and "DIM" in added[1] and "VARCHAR" in added[1]
    design = next(c for c in session.calls if "UPDATE CORE.STTM_REGISTRY SET TABLE_DESIGN" in c[0])
    assert '"condition": "A.ACTIVE"' in design[1][0]
    assert events and events[0][0] == "r1" and "regenerate dbt" in events[0][1]


def test_edit_version_refuses_a_stale_base_and_skips_archived_runs():
    latest = {"CATALOG_ID": "c1", "VERSION": 5, "STTM_ID": "s1", "RUN_ID": "r1", "TABLE_DESIGN": "{}",
              "LINES": json.dumps([{"target_column": "ID", "source_table": "A", "source_column": "ID"}])}
    session = Session({"ORDER BY VERSION DESC LIMIT 1": [latest]})
    with pytest.raises(AssertionError, match="reload"):
        cs.edit_version(session, "c1", json.dumps({"base_version": 4, "edits": [{"target_column": "ID", "business_rule": "x"}]}))
    archived = Session({"FROM CORE.STTM_REGISTRY S JOIN": [{"STTM_ID": "s1", "RUN_ID": "r1", "ARCHIVED": True, "DELETED_AT": None}]})
    assert cs.sync_to_run(archived, "s1", "r1", {"lines": [], "design": {}}, 2, "n") is False
    assert cs.sync_to_run(archived, None, None, {"lines": [], "design": {}}, 2, "n") is False
