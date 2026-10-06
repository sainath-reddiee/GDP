"""Persistent multi-table profiling against a deployed environment (V006 applied).

    $env:AIP_SNOWFLAKE_CONNECTION = "<connection name>"; $env:AIP_DATABASE = "DEV_AI_PLATFORM"
    python -m pytest tests/integration/test_multi_table_profiling.py -m integration

Creates five scratch tables in <AIP_DEMO_SOURCE>.ITEST_PROFILE_<hex>, onboards them in one run, then onboards
the same tables in a second run and checks every profile is read back from @METADATA.PROFILES_STAGE.
"""

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.integration

CONNECTION = os.getenv("AIP_SNOWFLAKE_CONNECTION")
DATABASE = os.getenv("AIP_DATABASE", "AI_PLATFORM")
DEMO = os.getenv("AIP_DEMO_SOURCE", "DEV_AIP_DEMO_SOURCE")
TABLES = [f"T{i}" for i in range(5)]

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


@pytest.fixture(scope="module")
def scratch(cur):
    schema = f"ITEST_PROFILE_{uuid.uuid4().hex[:8].upper()}"
    source = f"ITEST_{schema}"
    cur.execute(f"CREATE SCHEMA {DEMO}.{schema}")
    for i, table in enumerate(TABLES):
        cur.execute(f"CREATE TABLE {DEMO}.{schema}.{table} AS "
                    f"SELECT * FROM {DEMO}.CRM.CRM_CUSTOMER ORDER BY CUST_ID LIMIT {100 + 10 * i}")
    yield schema, source
    cur.execute(f"DROP SCHEMA IF EXISTS {DEMO}.{schema}")
    for r in rows(cur, "SELECT PROFILE_STAGE_PATH FROM METADATA.TABLE_PROFILES WHERE SCHEMA_NAME = %s", (schema,)):
        cur.execute(f"REMOVE @METADATA.PROFILES_STAGE/{r['PROFILE_STAGE_PATH']}")
    cur.execute("DELETE FROM METADATA.TABLE_PROFILES WHERE SCHEMA_NAME = %s", (schema,))


def onboard_and_profile(cur, schema, source, options=None):
    run_id = call(cur, "CALL CORE.CREATE_RUN(%s)", (json.dumps(
        {"RUN_NAME": f"itest-profile-{uuid.uuid4().hex[:8]}", "ENVIRONMENT": "TEST"}),))["run_id"]
    call(cur, "CALL SOURCE.REGISTER_SOURCE(%s, %s)", (run_id, json.dumps(
        {"SOURCE_SYSTEM_NAME": source, "SOURCE_TYPE": "SNOWFLAKE_DATABASE", "DATABASE": DEMO, "SCHEMA": schema})))
    access = call(cur, "CALL SOURCE.VALIDATE_SOURCE_ACCESS(%s, %s)", (run_id, json.dumps(TABLES)))
    assert access["passed"], access["checks"]
    landed = call(cur, "CALL SOURCE.EXECUTE_LANDING(%s)", (run_id,))
    assert landed["state"]["current_state"] == "LANDING_COMPLETE", landed
    result = call(cur, "CALL PROFILE.RUN_PROFILING(%s, %s)", (run_id, json.dumps(options or {"concurrency_limit": 3})))
    return run_id, result


def test_profiles_are_staged_then_reused_by_the_next_run(cur, scratch):
    schema, source = scratch

    first_run, first = onboard_and_profile(cur, schema, source)
    summary = first["profiled"]
    assert summary["computed"] == 5 and summary["cache_hits"] == 0, summary
    assert all(t["persisted"] for t in summary["tables"]), summary

    index = rows(cur, "SELECT TABLE_NAME, PROFILE_STAGE_PATH, PROFILED_IN_RUN, ROW_COUNT FROM METADATA.TABLE_PROFILES "
                      "WHERE SOURCE_NAME = %s AND SCHEMA_NAME = %s ORDER BY TABLE_NAME", (source, schema))
    assert [r["TABLE_NAME"] for r in index] == TABLES
    assert [r["ROW_COUNT"] for r in index] == [100, 110, 120, 130, 140]
    assert all(r["PROFILED_IN_RUN"] == first_run for r in index)
    cur.execute(f"LIST @METADATA.PROFILES_STAGE/{index[0]['PROFILE_STAGE_PATH'].rsplit('/', 1)[0]}/")
    assert len(cur.fetchall()) == 5

    second_run, second = onboard_and_profile(cur, schema, source)
    summary = second["profiled"]
    assert summary["cache_hits"] == 5 and summary["computed"] == 0, summary
    cached = rows(cur, "SELECT DISTINCT STATISTICS_JSON:cache::VARCHAR AS CACHE FROM PROFILE.PROFILE_REGISTRY "
                       "WHERE RUN_ID = %s AND IS_CURRENT", (second_run,))
    assert [r["CACHE"] for r in cached] == ["HIT"]
    unchanged = rows(cur, "SELECT PROFILED_IN_RUN FROM METADATA.TABLE_PROFILES WHERE SOURCE_NAME = %s", (source,))
    assert {r["PROFILED_IN_RUN"] for r in unchanged} == {first_run}

    refreshed = call(cur, "CALL PROFILE.REFRESH_TABLE_PROFILE(%s, %s)", (second_run, "T3"))
    assert refreshed["cache"] == "REFRESHED" and refreshed["registry_updated"] is True
    owner = rows(cur, "SELECT PROFILED_IN_RUN FROM METADATA.TABLE_PROFILES WHERE SOURCE_NAME = %s AND TABLE_NAME = 'T3'",
                 (source,))
    assert owner[0]["PROFILED_IN_RUN"] == second_run


def test_source_change_invalidates_only_that_table(cur, scratch):
    schema, source = scratch
    onboard_and_profile(cur, schema, source)
    cur.execute(f"INSERT INTO {DEMO}.{schema}.T1 SELECT * FROM {DEMO}.{schema}.T0 LIMIT 5")
    _, result = onboard_and_profile(cur, schema, source)
    by_table = {t["table"]: t["cache"] for t in result["profiled"]["tables"]}
    assert by_table["T1"] == "MISS"
    assert all(by_table[t] == "HIT" for t in TABLES if t != "T1"), by_table
