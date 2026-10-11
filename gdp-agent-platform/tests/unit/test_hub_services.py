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
    session = Session(fail=("GOVERNANCE.DATA_PROFILES",))
    assert cp.record(session, ("SRC", "DB", "S", "T"), DOC, None) is None
    ok = Session()
    assert cp.record(ok, ("SRC", "DB", "S", "T"), DOC, "r1")
    assert any("INSERT INTO GOVERNANCE.DATA_PROFILES" in sql for sql, _ in ok.calls)


def test_profile_search_filters_are_bound_not_inlined():
    sql, params = cp.search_sql(domain="COMPANY", table="cust", run_id="r1", tag_keys=["db.s.t"], q="x",
                                bookmarked_by="ANA", sort="nulls", limit=999, offset=-5)
    assert "LIMIT 200 OFFSET 0" in sql and "AVG_NULL_PCT DESC" in sql
    assert "cust" not in sql and params.count("%cust%") == 1 and '["DB.S.T"]' in params and "ANA" in params


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
    ({"target_column": "ID", "target_datatype": "X"}, "cannot edit"),
    ({"target_column": "ID", "mapping_type": "MAGIC"}, "mapping_type"),
])
def test_sttm_edits_are_checked(edit, message):
    with pytest.raises((AssertionError, ValueError), match=message):
        cs.apply_edits(LINES, [edit])


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
        "FROM CONTRACT.STTM_REGISTRY S": [{"STTM_ID": "s1", "RUN_ID": "r1", "DOMAIN_ID": "d1", "TARGET_TABLE_ID": "t1",
                                           "STATUS": "REVIEW", "TABLE_DESIGN": "{}", "DOMAIN_NAME": "COMPANY",
                                           "TARGET_DATABASE": "DB", "TARGET_SCHEMA": "S", "TARGET_TABLE": "X"}],
        "FROM CONTRACT.STTM_LINE": [{"SOURCE_SYSTEM": "CRM", "TARGET_COLUMN": "ID", "SOURCE_TABLE": "T"}],
        "COALESCE(MAX(VERSION), 0)": [{"V": 3}],
    })
    out = cs.snapshot(session, "s1", "GENERATED", "note")
    assert out == {"catalog_id": cs.catalog_id("CRM", "t1"), "version": 4, "lines": 1}
    insert = [p for sql, p in session.calls if sql.lstrip().startswith("INSERT INTO GOVERNANCE.STTM_CATALOG")][0]
    assert insert[9] == "DB.S.X" and insert[11] == "GENERATED"
    assert cs.snapshot(Session(fail=("CONTRACT.STTM_REGISTRY",)), "s1") is None
    assert cs.snapshot_run(Session(fail=("CONTRACT.STTM_REGISTRY",)), "r1", "TRANSFORM_EDIT") is None


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
        "FROM GOVERNANCE.MODEL_DESIGNS": [{"DOMAIN_NAME": "SALES_X", "MODE": "NEW", "STATUS": "FINALIZED",
                                           "ARTIFACTS": json.dumps({"domain/sales_x/domain_pack.json": "{}"})}],
        "FROM CODE.REPO": [{"GIT_URL": "https://github.com/o/r", "BRANCH": "main", "PROVIDER": "GITHUB"}],
    })
    payload = {"model_branch": {"design_id": "d1", "version": 2, "repo_id": "r1",
                                "head": "feature/model-sales-x-20261011090503"}}
    result = publish.publish_dbt_pr(session, "", json.dumps(payload))
    assert result["status"] == "PUBLISHED" and pushed["files"] == {"domain/sales_x/domain_pack.json": "{}"}
    assert pushed["draft"] is True and pushed["base"] == "main"
    update = [p for sql, p in session.calls if "UPDATE GOVERNANCE.MODEL_DESIGNS" in sql][0]
    assert update[0] == "PUBLISHED" and update[3] == "https://github.com/o/r/pull/7"
    with pytest.raises(AssertionError, match="feature/model-"):
        publish.publish_model_branch(session, {**payload["model_branch"], "head": "main-hack"})
    draft = Session({"FROM GOVERNANCE.MODEL_DESIGNS": [{"DOMAIN_NAME": "X", "MODE": "NEW", "STATUS": "DRAFT",
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
    stmts = staged.landing_statements("AI_PLATFORM", run, '"SHARE_DB"."S"."T"', '"AI_PLATFORM"."LANDING"."X__T"',
                                      "X__T", "As-is landing of 'T'")
    location = f"@AI_PLATFORM.SOURCE.LANDING_STAGE/runs/{run}/X__T/"
    assert stmts[0] == f"REMOVE {location}"
    assert stmts[1].startswith(f"COPY INTO {location}data_ FROM (SELECT * FROM \"SHARE_DB\".\"S\".\"T\")")
    assert "COMPRESSION = 'SNAPPY'" in stmts[1] and "HEADER = TRUE" in stmts[1]
    assert "LIMIT 0" in stmts[2] and "'T''" in stmts[2]
    assert "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE" in stmts[3] and "AI_PLATFORM.SOURCE.PARQUET_SNAPPY" in stmts[3]
    with pytest.raises(AssertionError):
        staged.stage_prefix("AI_PLATFORM", "../x", "T")
    with pytest.raises(AssertionError):
        staged.stage_prefix("AI_PLATFORM", run, "t'; drop")


def test_execute_landing_rejects_unknown_method():
    from services.source.procedures import execute_landing

    with pytest.raises(AssertionError, match="landing method"):
        execute_landing(Session(), "r1", "MAGIC")


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

    text = (Path(__file__).resolve().parents[2] / "snowflake/skills/gdp/dbt-onboard-source/references/company-contract.md").read_text()
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
    ("POST", "/api/hub/sttm/abc/export", None),
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
