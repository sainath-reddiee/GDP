"""Bringing client material in: data quality checks from a requirement (any table in the Data Quality Store), QA
queries from a .sql file, and the CI files that publish merged suites to Soda Cloud."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

import app.main  # noqa: E402,F401  (load the app first: the API modules import from it)

from services.catalog import dq_requirements as dr  # noqa: E402
from services.catalog.dq import ORIGINS  # noqa: E402
from services.catalog.dq_suite import check_record  # noqa: E402
from services.governance import policy  # noqa: E402
from services.qa.pack_files import parse_any, split_sql  # noqa: E402
from services.soda import cloud_ci  # noqa: E402
from services.soda.extract import requirement_from_row  # noqa: E402

COLUMNS = ["CUSTOMER_ID", "EMAIL", "STATUS", "UPDATED_AT", "AMOUNT"]


def req(**row):
    return requirement_from_row("DB.S.CUSTOMERS", row)


# ---------------------------------------------------------------- data quality from a requirement


@pytest.mark.parametrize("row, kind", [
    ({"target_column": "customer_id", "check_type": "NOT_NULL", "severity": "FAIL"}, "not_null"),
    ({"target_column": "CUSTOMER_ID", "check_type": "UNIQUE", "severity": "error"}, "unique"),
    ({"target_column": "STATUS", "check_type": "ACCEPTED_VALUES", "valid_values": "ACTIVE|INACTIVE"}, "accepted_values"),
    ({"target_column": "EMAIL", "check_type": "CUSTOM", "valid_format": "email"}, "format"),
    ({"target_column": "AMOUNT", "check_type": "RANGE", "valid_min": "0", "valid_max": 1000000}, "range"),
    ({"target_column": "UPDATED_AT", "check_type": "FRESHNESS", "freshness": "24 hours"}, "freshness"),
    ({"check_type": "ROW_COUNT", "threshold": "100"}, "row_count"),
])
def test_requirements_become_valid_checks(row, kind):
    raw, why = dr.to_check(req(**row), COLUMNS)
    assert why is None and raw["definition"]["kind"] == kind
    record, problems = check_record({**raw, "origin": "REQUIREMENT"})
    assert problems == [] and record["origin"] == "REQUIREMENT" and record["soda_dsl"]
    assert raw["severity"] in ("FAIL", "WARN")


def test_the_column_is_matched_to_the_table_and_freshness_is_normalised():
    raw, _ = dr.to_check(req(target_column="updated_at", check_type="FRESHNESS", freshness="< 6 h"), COLUMNS)
    assert raw["target_column"] == "UPDATED_AT" and raw["definition"]["threshold"] == "6h"


@pytest.mark.parametrize("row, reason", [
    ({"target_column": "NOT_A_COLUMN", "check_type": "NOT_NULL"}, "not a column of this table"),
    ({"target_column": "UPDATED_AT", "check_type": "FRESHNESS", "freshness": "recent"}, "needs a duration"),
    ({"target_column": "EMAIL", "check_type": "SOMETHING_ELSE"}, "not a standard check"),
])
def test_what_cannot_be_a_check_is_left_out_with_a_reason(row, reason):
    raw, why = dr.to_check(req(**row), COLUMNS)
    assert raw is None and reason in why


def test_csv_rows_are_staged_without_ai(monkeypatch):
    from services.catalog import dq, dq_suite

    staged = {}
    monkeypatch.setattr(dr, "table_columns", lambda s, fqn: COLUMNS)
    monkeypatch.setattr(dq, "_domain_for", lambda s, fqn, given: "d1")
    monkeypatch.setattr(dq_suite, "stage_many", lambda s, fqn, raws, origin, user, domain: staged.update(
        fqn=fqn, raws=raws, origin=origin, domain=domain) or {"added": len(raws), "skipped": 0, "problems": []})
    text = "target_column,check_type,severity,requirement\nCUSTOMER_ID,UNIQUE,FAIL,one row per customer\nFOO,NOT_NULL,WARN,x\n"
    out = dr.generate(None, "db.s.customers", text, "brief.csv", "ME",
                      complete=lambda *a, **k: pytest.fail("rows never call AI"))
    assert out["source"] == "rows" and out["added"] == 1 and out["proposed"] == 2
    assert out["left_out"] == ["FOO: not a column of this table"]
    assert staged["origin"] == "REQUIREMENT" and staged["domain"] == "d1"
    assert staged["raws"][0]["requirement"] == "one row per customer"


def test_a_free_text_brief_is_read_by_ai_grounded_on_the_columns(monkeypatch):
    from contextlib import contextmanager
    from services.catalog import dq, dq_suite
    from services.common import audit

    monkeypatch.setattr(dr, "table_columns", lambda s, fqn: COLUMNS)
    monkeypatch.setattr(dq, "_domain_for", lambda s, fqn, given: None)
    monkeypatch.setattr(dq_suite, "stage_many", lambda *a, **k: {"added": len(a[2]), "skipped": 0, "problems": []})
    monkeypatch.setattr(audit, "record_cost", lambda *a, **k: None)

    @contextmanager
    def tool_call(*a, **k):
        class Call:
            summary = ""
        yield Call()
    monkeypatch.setattr(audit, "tool_call", tool_call)
    prompts = []

    def complete(session, prompt, schema, stage=None):
        prompts.append(prompt)
        return {"requirements": [{"target_column": "EMAIL", "check_type": "NOT_NULL", "severity": "FAIL",
                                  "requirement": "every customer has an email"}]}, {}, "claude-x"
    out = dr.generate(None, "DB.S.CUSTOMERS", "Every customer must have an email address on file.", "", "ME",
                      complete=complete)
    assert out["source"] == "brief" and out["added"] == 1 and out["model"] == "claude-x"
    assert "CUSTOMER_ID, EMAIL, STATUS" in prompts[0]


def test_generate_is_a_governed_draft_edit_and_the_origin_is_allowed():
    assert "REQUIREMENT" in ORIGINS and len("REQUIREMENT") <= 16   # DQ_CHECKS_CATALOG.ORIGIN is VARCHAR(16)
    assert policy.privilege_for("POST", "/api/dq/catalog/generate")[0] == "SODA.EDIT"
    assert policy.no_queue("POST", "/api/dq/catalog/generate")   # the suite draft is itself reviewed and approved


# ---------------------------------------------------------------- QA queries from a .sql file


def test_plain_sql_becomes_named_queries_and_writes_are_left_out():
    text = """-- Orphan addresses: every address has a company
SELECT a.ID FROM DB.S.ADDR a LEFT JOIN DB.S.CO c ON c.ID = a.CO_ID WHERE c.ID IS NULL;
/* Duplicate companies */ SELECT ID FROM DB.S.CO GROUP BY ID HAVING COUNT(*) > 1;
DELETE FROM DB.S.CO;
-- Orphan addresses: every address has a company
SELECT ';' AS s FROM DB.S.CO WHERE NAME = 'it''s'
"""
    pack = parse_any(text, "Client checks")
    assert [q["query_name"] for q in pack["queries"]] == [
        "orphan_addresses_every_address_has_a_company", "duplicate_companies", "orphan_addresses_every_address_has_a_company_2"]
    assert pack["queries"][2]["sql_expression"].endswith("'it''s'")
    assert len(pack["skipped"]) == 1 and "DELETE" in pack["skipped"][0]


def test_the_annotated_pack_format_still_parses():
    text = "-- @pack Keys\n-- @query unique_id\n-- @severity WARN\nSELECT ID FROM DB.S.CO GROUP BY ID HAVING COUNT(*) > 1\n"
    pack = parse_any(text)
    assert pack["name"] == "Keys" and pack["queries"][0]["severity"] == "WARN" and pack["skipped"] == []


def test_semicolons_in_quotes_and_comments_do_not_split():
    assert split_sql("SELECT 'a;b' FROM T; -- c;d\nSELECT 2 FROM T /* e;f */;") == [
        "SELECT 'a;b' FROM T", "-- c;d\nSELECT 2 FROM T /* e;f */"]


def test_a_file_without_queries_is_refused():
    with pytest.raises(ValueError, match="no read-only queries"):
        parse_any("UPDATE DB.S.CO SET X = 1;", "x")


def test_the_import_endpoint_creates_a_draft_pack(monkeypatch):
    from app import qa_api

    created = {}

    class Packs:
        @staticmethod
        def create_pack(session, domain_id, payload_json):
            created.update(domain=domain_id, payload=json.loads(payload_json))
            return {"pack_id": "p1", "version": 1, "status": "DRAFT", "queries": len(created["payload"]["queries"])}

    monkeypatch.setattr(qa_api, "_svc", lambda name: Packs)
    monkeypatch.setattr(qa_api, "_domain_id", lambda db, value: "d1")
    monkeypatch.setattr(qa_api, "_call", lambda db, fn, *a: fn(None, *a))
    body = qa_api.PackImportIn(filename="client_checks.sql", text="SELECT ID FROM DB.S.CO WHERE ID IS NULL;\nDROP TABLE X;")
    out = qa_api.import_qa_pack("COMPANY", body, db=None)
    assert out["status"] == "DRAFT" and out["name"] == "client checks" and len(out["skipped"]) == 1
    assert created["payload"]["change_note"] == "Imported from client_checks.sql"
    assert policy.privilege_for("POST", "/api/domains/d1/qa-packs/import")[0] == "PACKAGE.EDIT"


# ---------------------------------------------------------------- Soda Cloud from CI


def _script():
    ns = {"__name__": "soda_cloud_scan"}
    exec(compile(cloud_ci.SCRIPT, cloud_ci.SCRIPT_PATH, "exec"), ns)
    return ns


def test_the_scan_script_points_each_suite_at_its_own_database_and_schema():
    ns = _script()
    database, schema, checks = ns["split"]("checks for DEV_DB.SILVER.CUSTOMERS:\n  - row_count > 0\n")
    assert (database, schema) == ("DEV_DB", "SILVER") and checks.startswith("checks for CUSTOMERS:\n  - row_count > 0")
    config = ns["configuration"](database, schema)
    assert "database: DEV_DB" in config and "${SODA_CLOUD_API_KEY_SECRET}" in config and "${SNOWFLAKE_PASSWORD}" in config
    with pytest.raises(ValueError):
        ns["split"]("not a suite\n")


def test_the_scan_script_runs_soda_and_returns_the_worst_exit_code(tmp_path, monkeypatch):
    ns = _script()
    suite = tmp_path / "customers.soda.yml"
    suite.write_text("checks for DB.S.CUSTOMERS:\n  - row_count > 0\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(ns["subprocess"], "call", lambda args: calls.append(args) or 2)
    assert ns["main"]([str(suite)]) == 2
    assert calls[0][:4] == ["soda", "scan", "-d", "snowflake_dq"]


def test_the_workflow_runs_on_merged_suites_and_names_every_secret():
    files = cloud_ci.files()
    workflow = files[cloud_ci.WORKFLOW_PATH]
    assert 'paths: ["dq/**/*.soda.yml"]' in workflow and "workflow_dispatch" in workflow
    for secret in cloud_ci.SECRETS:
        assert f"secrets.{secret}" in workflow
    assert "python scripts/soda_cloud_scan.py" in workflow and cloud_ci.SCRIPT_PATH in files


def test_a_pack_query_on_a_table_outside_the_domain_is_accepted():
    """QA reads any table the caller's role can read; the read-only guard still refuses writes and short names."""
    from services.qa.packs import clean_queries

    out = clean_queries([{"query_name": "q1", "sql_expression": "SELECT 1 FROM OTHER_DB.S.T"}])
    assert out[0]["sql_expression"] == "SELECT 1 FROM OTHER_DB.S.T"
    with pytest.raises(AssertionError, match="fully qualified"):
        clean_queries([{"query_name": "q1", "sql_expression": "SELECT 1 FROM S.T"}])
    with pytest.raises(AssertionError, match="not allowed in a read-only test"):
        clean_queries([{"query_name": "q1", "sql_expression": "SELECT 1 FROM A.B.C; DELETE FROM A.B.C"}])
