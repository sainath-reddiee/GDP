import uuid
from pathlib import Path

import pytest

from services.dbt.workspace import run_project_prefix, run_project_prefixes, run_workspace_cleanup
from services.workflow.cleanup import plan_landing_drops
from services.workflow.graph import load_graph
from services.workflow.procedures import parse_run_ids
from services.workflow.state_machine import (
    LIFECYCLE_FILTERS,
    RunContext,
    evaluate,
    lifecycle_filter_sql,
    lifecycle_status,
)


def test_lifecycle_is_derived_from_state_and_archive_flag():
    assert lifecycle_status("CREATED") == "DRAFT"
    assert lifecycle_status("MAPPING_REVIEW") == "RUNNING"
    assert lifecycle_status("COMPLETED") == "COMPLETED"
    assert lifecycle_status("FAILED") == "FAILED"
    assert lifecycle_status("CANCELLED") == "FAILED"
    assert lifecycle_status("COMPLETED", is_archived=True) == "ARCHIVED"


def test_lifecycle_filters_are_constant_sql():
    assert lifecycle_filter_sql(None) == LIFECYCLE_FILTERS["all"]
    assert "IS_ARCHIVED" in lifecycle_filter_sql("Archived")
    with pytest.raises(AssertionError):
        lifecycle_filter_sql("1=1; DROP TABLE X")


GRAPH_FILE = Path(__file__).resolve().parents[2] / "snowflake" / "database" / "seed" / "workflow_graph.json"


def test_archived_runs_cannot_transition():
    graph = load_graph(GRAPH_FILE)
    assert evaluate(graph, RunContext("CREATED"), "SOURCE_REGISTERED", "SYSTEM").allowed
    blocked = evaluate(graph, RunContext("CREATED", archived=True), "SOURCE_REGISTERED", "SYSTEM")
    assert not blocked.allowed and "archived" in blocked.reason


def test_parse_run_ids_accepts_arrays_and_json_and_rejects_junk():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert parse_run_ids([b, a, a]) == sorted([a, b])
    assert parse_run_ids(f'["{a}"]') == [a]
    with pytest.raises(AssertionError):
        parse_run_ids([])
    with pytest.raises(AssertionError):
        parse_run_ids(["x'; DROP TABLE CORE.WORKFLOW_RUN; --"])


def _landing(run, table, schema="LANDING"):
    return {"RUN_ID": run, "LANDING_DATABASE": "AI", "LANDING_SCHEMA": schema, "LANDING_TABLE": table}


def test_shared_landing_tables_and_platform_schemas_are_kept():
    owned = [_landing("r1", "CRM__CUST"), _landing("r2", "CRM__CUST"), _landing("r1", "CRM__ORDER"),
             _landing("r1", "TABLE_PROFILES", schema="METADATA")]
    others = [_landing("r9", "CRM__ORDER")]
    drops, kept = plan_landing_drops(owned, others)
    assert drops == [("AI", "LANDING", "CRM__CUST")]
    reasons = {k["table"]: k for k in kept}
    assert reasons["AI.LANDING.CRM__ORDER"]["runs"] == ["r9"]
    assert "platform schema" in reasons["AI.METADATA.TABLE_PROFILES"]["reason"]


def test_workspace_cleanup_only_touches_the_runs_own_objects():
    run_id = str(uuid.uuid4())
    prefix = run_project_prefix(run_id)
    issued = []

    def execute(sql):
        issued.append(sql)
        if sql.startswith("REMOVE"):
            return [{"name": "a"}, {"name": "b"}]
        if sql.startswith("SHOW DBT PROJECTS"):
            return [{"name": f"{prefix}1"}, {"name": "CUSTOMER_PROJECT"}, {"name": f"{prefix}2"}]
        return []

    out = run_workspace_cleanup(execute, run_id)
    assert issued[0] == f"REMOVE @CODEGEN.DBT_STAGE/{run_id}/"
    assert out["files_removed"] == 2 and out["projects_dropped"] == [f"{prefix}1", f"{prefix}2"]
    assert not any("CUSTOMER_PROJECT" in s for s in issued if s.startswith("DROP"))
    assert not any("METADATA" in s for s in issued)
    with pytest.raises(AssertionError):
        run_workspace_cleanup(execute, "../../METADATA.PROFILES_STAGE")


def test_workspace_cleanup_never_raises():
    def failing(sql):
        raise RuntimeError("Insufficient privileges")

    run_id = str(uuid.uuid4())
    out = run_workspace_cleanup(failing, run_id)
    assert out["files_removed"] == 0 and len(out["errors"]) == 1 + len(run_project_prefixes(run_id))


def test_in_place_landing_rows_are_never_dropped():
    owned = [{**_landing("r1", "CUSTOMER", schema="CRM"), "INGESTION_METHOD": "IN_PLACE"},
             {**_landing("r1", "CRM__ORDER"), "INGESTION_METHOD": "CTAS"}]
    drops, kept = plan_landing_drops(owned, [])
    assert drops == [("AI", "LANDING", "CRM__ORDER")]
    assert kept == [{"table": "AI.CRM.CUSTOMER", "reason": "read in place: this is the source table"}]
