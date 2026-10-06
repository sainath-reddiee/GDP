"""Run archive, restore and batch cleanup against a deployed environment (V006 applied).

    $env:AIP_SNOWFLAKE_CONNECTION = "<connection name>"; $env:AIP_DATABASE = "DEV_AI_PLATFORM"
    python -m pytest tests/integration/test_run_cleanup.py -m integration
"""

import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CONNECTION = os.getenv("AIP_SNOWFLAKE_CONNECTION")
DATABASE = os.getenv("AIP_DATABASE", "AI_PLATFORM")
DEMO = os.getenv("AIP_DEMO_SOURCE", "DEV_AIP_DEMO_SOURCE")

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


def rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def new_run(cur):
    payload = {"RUN_NAME": f"itest-clean-{uuid.uuid4().hex[:8]}", "ENVIRONMENT": "TEST"}
    return call(cur, "CALL CORE.CREATE_RUN(%s)", (json.dumps(payload),))["run_id"]


def landed_run(cur, source):
    run_id = new_run(cur)
    call(cur, "CALL SOURCE.REGISTER_SOURCE(%s, %s)", (run_id, json.dumps(
        {"SOURCE_SYSTEM_NAME": source, "SOURCE_TYPE": "SNOWFLAKE_DATABASE", "DATABASE": DEMO, "SCHEMA": "CRM"})))
    assert call(cur, "CALL SOURCE.VALIDATE_SOURCE_ACCESS(%s, %s)", (run_id, '["CRM_CUSTOMER"]'))["passed"]
    assert call(cur, "CALL SOURCE.EXECUTE_LANDING(%s)", (run_id,))["state"]["current_state"] == "LANDING_COMPLETE"
    return run_id


def put_workspace(cur, run_id):
    tmp = Path(tempfile.mkdtemp()) / "stg_dummy.sql"
    tmp.write_text("select 1 as x\n", encoding="utf-8")
    cur.execute(f"PUT 'file://{tmp.as_posix()}' @CODEGEN.DBT_STAGE/{run_id}/v1/models/ AUTO_COMPRESS = FALSE")


def stage_files(cur, path):
    cur.execute(f"LIST @{path}")
    return cur.fetchall()


def profile_snapshot(cur):
    count = rows(cur, "SELECT COUNT(*) AS N, MAX(PROFILED_AT)::VARCHAR AS LATEST FROM METADATA.TABLE_PROFILES")[0]
    return count["N"], count["LATEST"], len(stage_files(cur, "METADATA.PROFILES_STAGE"))


def cleanup(cur, run_ids, drop=True, workspaces=True, delete=True):
    return call(cur, "CALL CORE.SP_CLEANUP_PIPELINE_RUNS(PARSE_JSON(%s)::ARRAY, %s, %s, %s)",
                (json.dumps(run_ids), drop, workspaces, delete))


def test_batch_cleanup_purges_sandbox_but_keeps_profiles_and_audit(cur):
    source = f"ITEST_CLEAN_{uuid.uuid4().hex[:8].upper()}"
    landing = f"{DATABASE}.LANDING.{source}__CRM_CUSTOMER"
    profiles_before = profile_snapshot(cur)

    runs = [landed_run(cur, source) for _ in range(3)]
    for run_id in runs:
        put_workspace(cur, run_id)
        assert stage_files(cur, f"CODEGEN.DBT_STAGE/{run_id}/")
    keeper = landed_run(cur, source)

    result = cleanup(cur, runs)
    assert sorted(result["deleted"]) == sorted(runs)
    assert result["profiles_preserved"] is True
    kept = {k["table"]: k for k in result["landing"]["kept"]}
    assert landing in kept and keeper in kept[landing]["runs"], result["landing"]
    for run_id in runs:
        assert not stage_files(cur, f"CODEGEN.DBT_STAGE/{run_id}/")

    marked = rows(cur, "SELECT RUN_ID, DELETED_AT, IS_ARCHIVED FROM CORE.WORKFLOW_RUN "
                       "WHERE ARRAY_CONTAINS(RUN_ID::VARIANT, PARSE_JSON(%s)::ARRAY)", (json.dumps(runs),))
    assert len(marked) == 3 and all(r["DELETED_AT"] is not None and r["IS_ARCHIVED"] for r in marked)
    audit = rows(cur, "SELECT COUNT(*) AS N FROM CORE.WORKFLOW_EVENT WHERE REASON = 'run deleted' "
                      "AND ARRAY_CONTAINS(RUN_ID::VARIANT, PARSE_JSON(%s)::ARRAY)", (json.dumps(runs),))
    assert audit[0]["N"] == 3
    assert profile_snapshot(cur) == profiles_before

    with pytest.raises(Exception, match="archived or deleted"):
        call(cur, "CALL CORE.TRANSITION_RUN(%s, %s, %s, %s)", (runs[0], "CANCELLED", "itest", "{}"))

    last = cleanup(cur, [keeper])
    assert landing in last["landing"]["dropped"], last
    cur.execute(f"SHOW TABLES LIKE '{source}__CRM_CUSTOMER' IN SCHEMA {DATABASE}.LANDING")
    assert cur.fetchall() == []
    purged = rows(cur, "SELECT DISTINCT INGESTION_STATUS AS S FROM SOURCE.LANDING_TABLE_REGISTRY WHERE RUN_ID = %s",
                  (keeper,))
    assert [r["S"] for r in purged] == ["PURGED"]
    assert profile_snapshot(cur) == profiles_before


def test_purge_only_requires_a_closed_run(cur):
    run_id = new_run(cur)
    result = cleanup(cur, [run_id], drop=True, workspaces=True, delete=False)
    assert result["deleted"] == [] and result["skipped"][0]["run_id"] == run_id
    call(cur, "CALL CORE.SET_RUN_ARCHIVED(%s, TRUE)", (json.dumps([run_id]),))
    result = cleanup(cur, [run_id], drop=True, workspaces=True, delete=False)
    assert result["skipped"] == [] and result["deleted"] == []


def test_archive_freezes_and_restore_unfreezes(cur):
    run_id = new_run(cur)
    archived = call(cur, "CALL CORE.SET_RUN_ARCHIVED(%s, TRUE)", (json.dumps([run_id]),))
    assert archived["changed"] == [run_id]
    state = call(cur, "CALL CORE.GET_WORKFLOW_STATE(%s)", (run_id,))
    assert state["lifecycle"] == "ARCHIVED" and state["is_archived"] is True
    with pytest.raises(Exception, match="archived or deleted"):
        call(cur, "CALL SOURCE.REGISTER_SOURCE(%s, %s)", (run_id, json.dumps(
            {"SOURCE_SYSTEM_NAME": "ITEST_CRM", "SOURCE_TYPE": "SNOWFLAKE_DATABASE", "DATABASE": DEMO,
             "SCHEMA": "CRM"})))

    restored = call(cur, "CALL CORE.SET_RUN_ARCHIVED(%s, FALSE)", (json.dumps([run_id]),))
    assert restored["changed"] == [run_id]
    assert call(cur, "CALL CORE.GET_WORKFLOW_STATE(%s)", (run_id,))["lifecycle"] == "DRAFT"
    again = call(cur, "CALL CORE.SET_RUN_ARCHIVED(%s, FALSE)", (json.dumps([run_id]),))
    assert again["changed"] == [] and again["skipped"][0]["reason"] == "not archived"
