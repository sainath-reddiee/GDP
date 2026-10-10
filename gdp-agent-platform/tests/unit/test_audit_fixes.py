"""Focused checks for the codebase audit fixes (pure functions, no Snowflake)."""

from pathlib import Path

import pytest

from services.common.sql import atomic
from services.dbt.procedures import _artifact_type
from services.mapping.procedures import last_per_source
from services.qa.run import hidden_columns, latest
from services.soda.expectations import _infer_format, not_yet_current, without_rejected
from services.soda.extract import requirement_from_row
from services.sttm.refine import from_transform, rules_by_column
from services.workflow.graph import load_graph
from services.workflow.procedures import interrupted_state, review_decision_error
from services.workflow.state_machine import RunContext, evaluate, stage_rail

GRAPH_FILE = Path(__file__).resolve().parents[2] / "snowflake" / "database" / "seed" / "workflow_graph.json"


@pytest.fixture(scope="module")
def graph():
    return load_graph(GRAPH_FILE)


def test_cancelled_after_failed_uses_the_state_it_failed_in(graph):
    run = {"CURRENT_STATE": "CANCELLED", "PREVIOUS_STATE": "FAILED", "FAILED_FROM_STATE": "LANDING_RUNNING"}
    assert interrupted_state(run) == "LANDING_RUNNING"
    rail = {r["stage"]: r["status"] for r in stage_rail(graph, "CANCELLED", interrupted_state(run))}
    assert rail["SOURCE"] == "CANCELLED"
    assert interrupted_state({"CURRENT_STATE": "CANCELLED", "PREVIOUS_STATE": "STTM_REVIEW",
                              "FAILED_FROM_STATE": None}) == "STTM_REVIEW"


def test_review_decision_must_match_the_direction():
    assert review_decision_error("STTM_APPROVED", "APPROVE") is None
    assert review_decision_error("STTM_PENDING", "REQUEST_CHANGES") is None
    assert review_decision_error("STTM_APPROVED", "REJECT")
    assert review_decision_error("MAPPING_PENDING", "APPROVE")


def test_csv_blank_cells_are_missing_and_lists_split():
    row = {"attribute": "STATUS", "check_type": "accepted_values", "valid_values": "ACTIVE|INACTIVE",
           "valid_min": "", "valid_max": " ", "missing_values": "NA;n/a", "severity": "FAIL"}
    req = requirement_from_row("DIM_CUSTOMER", row)
    assert req["check_type"] == "ACCEPTED_VALUES"
    assert req["definition"]["values"] == ["ACTIVE", "INACTIVE"]
    assert req["definition"]["missing_values"] == ["NA", "n/a"]
    ranged = requirement_from_row("DIM_CUSTOMER", {"attribute": "AGE", "check_type": "range",
                                                   "valid_min": "0", "valid_max": "120.5"})
    assert ranged["definition"]["min"] == 0 and ranged["definition"]["max"] == 120.5


def test_rejection_only_applies_to_its_table():
    checks = [{"check_type": "NOT_NULL", "target_column": "PHONE", "target_table": "DIM_A", "origin": "STTM"},
              {"check_type": "NOT_NULL", "target_column": "PHONE", "target_table": "DIM_B", "origin": "STTM"}]
    kept = without_rejected(checks, [{"check_type": "NOT_NULL", "target_column": "PHONE", "target_table": "dim_a"}])
    assert [c["target_table"] for c in kept] == ["DIM_B"]


def test_export_rules_stay_on_their_table():
    rules = rules_by_column([
        {"target_column": "STATUS", "target_table": "DIM_OTHER", "prompt": "other"},
        {"target_column": "STATUS", "prompt": "generic"},
        {"target_column": "NAME", "target_table": "DIM_CUSTOMER", "prompt": "mine"},
        {"target_column": "NAME", "prompt": "older generic"},
    ], "DB.SCHEMA.DIM_CUSTOMER")
    assert rules["STATUS"]["prompt"] == "generic"
    assert rules["NAME"]["prompt"] == "mine"


def test_pii_test_masks_aliased_value_columns():
    test = {"target_column": "EMAIL_ADDRESS", "source": "CRM.EMAIL"}
    hidden = hidden_columns(test, ["CUSTOMER_ID", "SOURCE_VALUE", "TARGET_VALUE"], set(), {"CUSTOMER_ID"})
    assert hidden == {"SOURCE_VALUE", "TARGET_VALUE"}
    plain_test = {"target_column": "STATUS", "source": "CRM.STATUS_CD"}
    assert hidden_columns(plain_test, ["CUSTOMER_ID", "SOURCE_VALUE"], set()) == set()
    assert hidden_columns({"target_column": "CUST_REF"}, ["VALUE"], {"CUST_REF"}) == {"VALUE"}


class _Session:
    def __init__(self):
        self.statements = []

    def sql(self, text, params=None):
        self.statements.append(text)
        return self

    def collect(self):
        return []


def test_atomic_commits_or_rolls_back():
    session = _Session()
    assert atomic(session, lambda: 7) == 7
    assert session.statements == ["BEGIN TRANSACTION", "COMMIT"]
    session = _Session()
    with pytest.raises(RuntimeError):
        atomic(session, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert session.statements == ["BEGIN TRANSACTION", "ROLLBACK"]


def test_reimport_skips_checks_already_current():
    current = [{"check_type": "NOT_NULL", "target_column": "ID",
                "definition": {"kind": "not_null", "backtest": {"status": "PASS"}}}]
    new = [{"check_type": "NOT_NULL", "target_column": "id", "definition": {"kind": "not_null"}},
           {"check_type": "UNIQUE", "target_column": "ID", "definition": {"kind": "unique"}},
           {"check_type": "UNIQUE", "target_column": "ID", "definition": {"kind": "unique"}}]
    assert [c["check_type"] for c in not_yet_current(new, current)] == ["UNIQUE"]


def test_duplicate_source_decisions_keep_the_last():
    prepared = [("S1", "APPROVED", None, "T1"), ("S2", "REJECTED", None, None), ("S1", "REJECTED", None, None)]
    assert last_per_source(prepared) == [("S1", "REJECTED", None, None), ("S2", "REJECTED", None, None)]


def test_format_inference_uses_whole_tokens():
    assert _infer_format("HOTEL_ID", "") is None
    assert _infer_format("CONTACT_TEL", "") == "phone number"
    assert _infer_format("EMAIL_ADDRESS", "") == "email"
    assert _infer_format("IS_VERIFIED", "Whether the email was verified") is None
    flag = {"target_column": "HAS_CONTACT_FLAG", "transformation": "CASE WHEN EMAIL IS NULL THEN 'N' ELSE 'Y' END",
            "business_definition": "Whether an email is present"}
    assert not any(c["definition"].get("format") == "email" for c in from_transform("DIM_C", flag))


def test_artifact_type_checks_folders_first():
    assert _artifact_type("soda/checks.yml") == "SODA_CHECKS"
    assert _artifact_type("mappings/sttm.yml") == "STTM_EXPORT"
    assert _artifact_type("release/plan.yml") == "RELEASE_PLAN"
    assert _artifact_type("models/marts/schema.yml") == "DBT_SCHEMA_YML"


def test_revalidation_path_from_validation_failed_exists(graph):
    assert evaluate(graph, RunContext("VALIDATION_FAILED"), "VALIDATION_PENDING", "SYSTEM").allowed
    assert evaluate(graph, RunContext("VALIDATION_FAILED"), "DBT_PENDING", "SYSTEM").allowed
    assert evaluate(graph, RunContext("DBT_GENERATING"), "VALIDATION_PENDING", "SYSTEM").allowed


def test_qa_results_take_the_latest_result_per_test():
    calls = []

    def query(sql, params):
        calls.append((sql, params))
        if "FROM QUALITY.QA_RUN WHERE" in sql:
            return [{"qa_run_id": "q2", "sttm_id": "s1"}]
        return [{"test_id": "QA-1", "outcome": "FAIL", "severity": "HIGH"}]

    last, results = latest(query, "r1")
    assert last["qa_run_id"] == "q2" and results[0]["outcome"] == "FAIL"
    sql, params = calls[1]
    assert "PARTITION BY R.TEST_ID" in sql and "QA_RUN_ID = %s" not in sql
    assert params == ("r1", "s1", "r1")
