"""Workflow procedures against a deployed environment.

    $env:AIP_SNOWFLAKE_CONNECTION = "<connection name>"; $env:AIP_DATABASE = "AI_PLATFORM"
    python -m pytest tests/integration -m integration

Runs are created with ENVIRONMENT='TEST' and are never deleted (tables are append-only).
"""

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.integration

CONNECTION = os.getenv("AIP_SNOWFLAKE_CONNECTION")
DATABASE = os.getenv("AIP_DATABASE", "AI_PLATFORM")

if not CONNECTION:
    pytest.skip("AIP_SNOWFLAKE_CONNECTION not set", allow_module_level=True)


@pytest.fixture(scope="module")
def cur():
    import snowflake.connector

    con = snowflake.connector.connect(connection_name=CONNECTION)
    c = con.cursor()
    c.execute(f"USE DATABASE {DATABASE}")
    yield c
    con.close()


def call(cur, sql, params=()):
    cur.execute(sql, params)
    value = cur.fetchone()[0]
    return json.loads(value) if isinstance(value, str) else value


def new_run(cur):
    payload = {"RUN_NAME": f"itest-{uuid.uuid4().hex[:8]}", "ENVIRONMENT": "TEST", "TARGET_MODEL": "GDP.DIM_CUSTOMER"}
    return call(cur, "CALL CORE.CREATE_RUN(%s)", (json.dumps(payload),))


def system(cur, run_id, to_state, reason="itest"):
    return call(cur, "CALL CORE.TRANSITION_RUN(%s, %s, %s, %s)", (run_id, to_state, reason, "{}"))


def review(cur, run_id, to_state, decision, justification=None):
    return call(cur, "CALL CORE.REVIEW_TRANSITION(%s, %s, %s, %s, %s)",
                (run_id, to_state, decision, justification, None))


FORWARD_TO_MAPPING_REVIEW = [
    "SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING", "LANDING_RUNNING",
    "LANDING_COMPLETE", "PROFILING_PENDING", "PROFILING_RUNNING", "PROFILING_COMPLETE",
    "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "MAPPING_REVIEW",
]


def test_create_run_starts_in_created(cur):
    state = new_run(cur)
    assert state["current_state"] == "CREATED"
    assert state["stages"][0] == {"stage": "SOURCE", "status": "ACTIVE"}
    assert all(s["status"] == "LOCKED" for s in state["stages"][1:])


def test_skip_is_rejected(cur):
    run_id = new_run(cur)["run_id"]
    with pytest.raises(Exception, match="TRANSITION_REJECTED"):
        system(cur, run_id, "LANDING_PENDING")


def test_review_gate_and_reviewer_identity(cur):
    cur.execute("SELECT CURRENT_USER()")
    me = cur.fetchone()[0]

    run_id = new_run(cur)["run_id"]
    for state in FORWARD_TO_MAPPING_REVIEW:
        result = system(cur, run_id, state)
    assert result["state"]["status"] == "AWAITING_REVIEW"

    with pytest.raises(Exception, match="requires a HUMAN actor"):
        system(cur, run_id, "MAPPING_APPROVED")
    with pytest.raises(Exception, match="BUSINESS_JUSTIFICATION is required"):
        review(cur, run_id, "MAPPING_APPROVED", "APPROVE")

    result = review(cur, run_id, "MAPPING_APPROVED", "APPROVE", "itest: mappings verified")
    assert result["to_state"] == "MAPPING_APPROVED"

    cur.execute("SELECT REVIEWER, DECISION, BUSINESS_JUSTIFICATION, STAGE FROM CORE.REVIEW_DECISION WHERE RUN_ID = %s",
                (run_id,))
    assert cur.fetchall() == [(me, "APPROVE", "itest: mappings verified", "MAPPING")]

    cur.execute("SELECT DISTINCT ACTOR FROM CORE.WORKFLOW_EVENT WHERE RUN_ID = %s", (run_id,))
    assert {r[0] for r in cur.fetchall()} == {me}

    cur.execute("SELECT COUNT(*) FROM CORE.WORKFLOW_EVENT WHERE RUN_ID = %s", (run_id,))
    assert cur.fetchone()[0] == 1 + len(FORWARD_TO_MAPPING_REVIEW) + 1
    assert_no_none_strings(cur, run_id)


def test_failure_and_retry(cur):
    run_id = new_run(cur)["run_id"]
    for state in ["SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING", "LANDING_RUNNING"]:
        system(cur, run_id, state)
    failed = system(cur, run_id, "FAILED", "itest: simulated copy failure")
    rail = {s["stage"]: s["status"] for s in failed["state"]["stages"]}
    assert rail["LANDING"] == "FAILED"

    with pytest.raises(Exception, match="retry must go to LANDING_PENDING"):
        system(cur, run_id, "PROFILING_PENDING")
    retried = system(cur, run_id, "LANDING_PENDING")
    assert retried["state"]["failed_from_state"] is None

    cur.execute("SELECT RETRY_COUNT, FAILURE_REASON FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    assert cur.fetchone() == (1, None)
    assert_no_none_strings(cur, run_id)


def assert_no_none_strings(cur, run_id):
    """Optional values must be stored as SQL NULL, never as the text 'None'."""
    for table in ("CORE.WORKFLOW_RUN", "CORE.WORKFLOW_EVENT", "CORE.REVIEW_DECISION"):
        cur.execute(f"SELECT * FROM {table} WHERE RUN_ID = %s", (run_id,))
        columns = [d[0] for d in cur.description]
        for row in cur.fetchall():
            bad = [c for c, v in zip(columns, row) if v == "None"]
            assert not bad, f"{table}: literal 'None' stored in {bad}"
