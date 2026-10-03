"""Source onboarding procedures against a deployed environment with the demo source.

    python infrastructure/deploy_snowflake.py ... --demo-source DEV_AIP_DEMO_SOURCE
    $env:AIP_SNOWFLAKE_CONNECTION = "<connection name>"; $env:AIP_DATABASE = "DEV_AI_PLATFORM"
    python -m pytest tests/integration/test_source_onboarding.py -m integration

The share test uses SNOWFLAKE_SAMPLE_DATA, which Snowflake mounts from a share in most accounts.
"""

import json
import os
import uuid

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
    payload = {"RUN_NAME": f"itest-src-{uuid.uuid4().hex[:8]}", "ENVIRONMENT": "TEST"}
    return call(cur, "CALL CORE.CREATE_RUN(%s)", (json.dumps(payload),))["run_id"]


def register(cur, run_id, name, source_type, database, schema):
    payload = {"SOURCE_SYSTEM_NAME": name, "SOURCE_TYPE": source_type, "DATABASE": database, "SCHEMA": schema}
    return call(cur, "CALL SOURCE.REGISTER_SOURCE(%s, %s)", (run_id, json.dumps(payload)))


def validate(cur, run_id, selected):
    return call(cur, "CALL SOURCE.VALIDATE_SOURCE_ACCESS(%s, %s)", (run_id, json.dumps(selected)))


def land(cur, run_id):
    return call(cur, "CALL SOURCE.EXECUTE_LANDING(%s)", (run_id,))


def test_register_unknown_database_keeps_run_created(cur):
    run_id = new_run(cur)
    with pytest.raises(Exception, match="SOURCE_NOT_ACCESSIBLE"):
        register(cur, run_id, "ITEST_MISSING", "SNOWFLAKE_DATABASE", "NO_SUCH_DATABASE_AIP", "CRM")
    state = call(cur, "CALL CORE.GET_WORKFLOW_STATE(%s)", (run_id,))
    assert state["current_state"] == "CREATED"


def test_landing_cannot_skip_access(cur):
    run_id = new_run(cur)
    register(cur, run_id, "ITEST_CRM", "SNOWFLAKE_DATABASE", DEMO, "CRM")
    with pytest.raises(Exception, match="TRANSITION_REJECTED"):
        land(cur, run_id)


def test_database_source_end_to_end(cur):
    run_id = new_run(cur)
    reg = register(cur, run_id, "ITEST_CRM", "SNOWFLAKE_DATABASE", DEMO.lower(), "crm")
    assert reg["state"]["current_state"] == "SOURCE_REGISTERED"
    assert reg["objects_discovered"] == 2

    access = validate(cur, run_id, ["CRM_CUSTOMER", "CRM_ORDER"])
    assert access["passed"], access["checks"]
    assert access["state"]["current_state"] == "ACCESS_APPROVED"
    stored = rows(cur, "SELECT CHECK_NAME, STATUS FROM SOURCE.SOURCE_ACCESS_CHECK WHERE RUN_ID = %s", (run_id,))
    assert stored and all(r["STATUS"] != "FAILED" for r in stored)

    landed = land(cur, run_id)
    assert landed["state"]["current_state"] == "LANDING_COMPLETE", landed
    by_object = {t["object"]: t for t in landed["tables"]}
    assert by_object["CRM_CUSTOMER"]["landed_rows"] == by_object["CRM_CUSTOMER"]["source_rows"] == 500
    assert by_object["CRM_ORDER"]["landed_rows"] == 2000
    assert by_object["CRM_CUSTOMER"]["landing_table"] == f"{DATABASE}.LANDING.ITEST_CRM__CRM_CUSTOMER"

    columns = rows(cur, """
        SELECT C.COLUMN_NAME, C.DATA_TYPE, C.NULLABLE, C.SOURCE_COMMENT
          FROM SOURCE.LANDING_COLUMN_REGISTRY C
          JOIN SOURCE.LANDING_TABLE_REGISTRY T ON T.LANDING_ID = C.LANDING_ID
         WHERE T.RUN_ID = %s AND T.SOURCE_TABLE = 'CRM_CUSTOMER'
         ORDER BY C.ORDINAL_POSITION""", (run_id,))
    assert [c["COLUMN_NAME"] for c in columns][:2] == ["CUST_ID", "FIRST_NM"] and len(columns) == 7
    assert columns[0]["DATA_TYPE"] == "VARCHAR(10)" and columns[0]["NULLABLE"] is False
    assert columns[0]["SOURCE_COMMENT"] == "CRM customer identifier"

    events = [r["TO_STATE"] for r in rows(cur, "SELECT TO_STATE FROM CORE.WORKFLOW_EVENT WHERE RUN_ID = %s "
                                               "ORDER BY CREATED_AT", (run_id,))]
    assert events == ["CREATED", "SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED",
                      "LANDING_PENDING", "LANDING_RUNNING", "LANDING_COMPLETE"]


def test_failed_access_returns_to_source_and_can_be_corrected(cur):
    run_id = new_run(cur)
    register(cur, run_id, "ITEST_CRM", "SNOWFLAKE_DATABASE", DEMO, "CRM")
    bad = validate(cur, run_id, ["NOT_A_TABLE"])
    assert not bad["passed"] and bad["state"]["current_state"] == "SOURCE_REGISTERED"
    assert any(c["status"] == "FAILED" and "NOT_A_TABLE" in c["detail"] for c in bad["checks"])

    good = validate(cur, run_id, ["CRM_CUSTOMER"])
    assert good["passed"] and good["state"]["current_state"] == "ACCESS_APPROVED"
    selected = rows(cur, "SELECT OBJECT_NAME FROM SOURCE.SOURCE_OBJECT WHERE RUN_ID = %s AND SELECTED_FLAG", (run_id,))
    assert [r["OBJECT_NAME"] for r in selected] == ["CRM_CUSTOMER"]


def test_share_adapter_rejects_a_standard_database(cur):
    run_id = new_run(cur)
    register(cur, run_id, "ITEST_CRM_AS_SHARE", "SNOWFLAKE_SHARE", DEMO, "CRM")
    result = validate(cur, run_id, ["CRM_CUSTOMER"])
    assert not result["passed"]
    assert any(c["name"] == "SHARE" and c["status"] == "FAILED" for c in result["checks"])


def test_share_source_lands_from_mounted_share(cur):
    run_id = new_run(cur)
    register(cur, run_id, "ITEST_TPCH", "SNOWFLAKE_SHARE", "SNOWFLAKE_SAMPLE_DATA", "TPCH_SF1")
    access = validate(cur, run_id, ["NATION", "REGION"])
    assert access["passed"], access["checks"]
    landed = land(cur, run_id)
    assert landed["state"]["current_state"] == "LANDING_COMPLETE", landed
    assert {t["object"]: t["landed_rows"] for t in landed["tables"]} == {"NATION": 25, "REGION": 5}
