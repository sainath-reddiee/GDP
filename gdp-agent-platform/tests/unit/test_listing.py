import pytest

from services.workflow.listing import audit_where, cost_query, page, runs_where, summarise
from services.workflow.state_machine import lifecycle_status


def test_run_filters_are_binds():
    where, params = runs_where("TRUE", False, " acme ", "d1", "mapping", True)
    assert "R.RUN_NAME ILIKE %s" in where and "R.DOMAIN_ID = %s" in where and "R.CURRENT_STAGE = %s" in where
    assert "'MAPPING_REVIEW'" in where and "acme" not in where  # user text only in params
    assert params == [False, "%acme%", "%acme%", "%acme%", "%acme%", "%acme%", "d1", "MAPPING"]
    assert page(-5, 10_000, 200) == (0, 200)


def test_audit_filters():
    where, params = audit_where("r1", "human", "dbt_review", "2026-10-01", "2026-10-07", None)
    assert params == ["r1", "HUMAN", "DBT_REVIEW", "2026-10-01", "2026-10-07"]
    assert "DATEADD('day', 1, %s::DATE)" in where  # 'until' includes the whole day
    with pytest.raises(AssertionError):
        audit_where(None, "robot", None, None, None, None)


def test_cost_grouping_is_from_a_fixed_map():
    sql, params = cost_query("run", "2026-10-01", None)
    assert "GROUP BY C.RUN_ID" in sql and "MAX(R.RUN_NAME)" in sql and params == ["2026-10-01"]
    assert "ORDER BY KEY ASC" in cost_query("day", None, None)[0]
    with pytest.raises(AssertionError):
        cost_query("C.STAGE; DROP TABLE X", None, None)


def test_dashboard_counts_from_groups():
    rows = [{"current_state": "MAPPING_REVIEW", "current_stage": "MAPPING", "status": "AWAITING_REVIEW", "is_archived": False, "n": 3},
            {"current_state": "PROFILING_RUNNING", "current_stage": "DOMAIN", "status": "IN_PROGRESS", "is_archived": False, "n": 2},
            {"current_state": "CREATED", "current_stage": "SOURCE", "status": "DRAFT", "is_archived": False, "n": 1},
            {"current_state": "FAILED", "current_stage": "DBT", "status": "FAILED", "is_archived": False, "n": 4},
            {"current_state": "CANCELLED", "current_stage": "DBT", "status": "CANCELLED", "is_archived": False, "n": 1},
            {"current_state": "COMPLETED", "current_stage": "REVIEW", "status": "COMPLETED", "is_archived": True, "n": 7}]
    out = summarise(rows, lifecycle_status)
    assert out["total"] == 11 and out["archived"] == 7 and out["needs_review"] == 3
    assert out["failed"] == 4 and out["cancelled"] == 1
    assert out["by_stage"] == {"MAPPING": 3, "PROFILING": 2}
    assert out["lifecycle"]["DRAFT"] == 1 and out["lifecycle"]["RUNNING"] == 5
