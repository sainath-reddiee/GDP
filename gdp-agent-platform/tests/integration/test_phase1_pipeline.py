"""Phase 1 vertical slice against a deployed environment with the demo CRM source.

    python infrastructure/deploy_snowflake.py --connection $env:AIP_SNOWFLAKE_CONNECTION \
        --database DEV_AI_PLATFORM --warehouse DBT_WH --demo-source DEV_AIP_DEMO_SOURCE
    python -m pytest tests/integration/test_phase1_pipeline.py -m integration -s
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.integration

CONNECTION = os.getenv("AIP_SNOWFLAKE_CONNECTION")
DATABASE = os.getenv("AIP_DATABASE", "DEV_AI_PLATFORM")
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


def test_phase1_source_to_dbt_review(cur):
    payload = {"RUN_NAME": f"itest-p1-{uuid.uuid4().hex[:8]}", "ENVIRONMENT": "TEST",
               "TARGET_MODEL": "GDP.DIM_CUSTOMER"}
    run_id = call(cur, "CALL CORE.CREATE_RUN(%s)", (json.dumps(payload),))["run_id"]
    call(cur, "CALL SOURCE.REGISTER_SOURCE(%s, %s)", (run_id, json.dumps({
        "SOURCE_SYSTEM_NAME": "ITEST_CRM_P1", "SOURCE_TYPE": "SNOWFLAKE_DATABASE",
        "DATABASE": DEMO, "SCHEMA": "CRM",
    })))
    call(cur, "CALL SOURCE.VALIDATE_SOURCE_ACCESS(%s, %s)", (run_id, json.dumps(["CRM_CUSTOMER"])))
    landing = call(cur, "CALL SOURCE.EXECUTE_LANDING(%s)", (run_id,))
    assert landing["state"]["current_state"] == "LANDING_COMPLETE"

    profile = call(cur, "CALL PROFILE.RUN_PROFILING(%s)", (run_id,))
    assert profile["state"]["current_state"] in ("PROFILING_COMPLETE", "DOMAIN_IDENTIFIED")

    domain = call(cur, "CALL KNOWLEDGE.IDENTIFY_DOMAIN(%s)", (run_id,))
    assert domain["state"]["current_state"] == "DOMAIN_IDENTIFIED"
    assert domain["domain"]["domain_name"] == "GDP"

    mapping = call(cur, "CALL MAPPING.GENERATE_MAPPING_CANDIDATES(%s)", (run_id,))
    assert mapping["state"]["current_state"] == "MAPPING_REVIEW"

    cur.execute(
        """
        SELECT C.CANDIDATE_ID, C.SOURCE_COLUMN_ID, C.TARGET_COLUMN_ID, C.RANK, C.RECOMMENDATION,
               C.FINAL_SCORE, C.TRANSFORMATION, L.COLUMN_NAME AS SRC, T.COLUMN_NAME AS TGT
          FROM MAPPING.MAPPING_CANDIDATE C
          JOIN SOURCE.LANDING_COLUMN_REGISTRY L ON L.LANDING_COLUMN_ID = C.SOURCE_COLUMN_ID
          JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY T ON T.TARGET_COLUMN_ID = C.TARGET_COLUMN_ID
         WHERE C.RUN_ID = %s AND C.IS_CURRENT
         ORDER BY C.FINAL_SCORE DESC, C.RANK
        """,
        (run_id,),
    )
    cols = [d[0] for d in cur.description]
    tops = [dict(zip(cols, r)) for r in cur.fetchall()]
    required = ["CUSTOMER_ID", "CUSTOMER_NAME", "CUSTOMER_STATUS"]
    by_target = {}
    used_sources = set()
    for target in required:
        for row in tops:
            if row["TGT"] == target and row["SOURCE_COLUMN_ID"] not in used_sources:
                by_target[target] = row
                used_sources.add(row["SOURCE_COLUMN_ID"])
                break
    decisions = []
    for target in required:
        row = by_target.get(target)
        if not row:
            continue
        transform = row["TRANSFORMATION"]
        if target == "CUSTOMER_NAME":
            transform = "TRIM(INITCAP(TRIM(first_nm)) || ' ' || INITCAP(TRIM(last_nm)))"
        decisions.append({
            "candidate_id": row["CANDIDATE_ID"], "source_column_id": row["SOURCE_COLUMN_ID"],
            "decision": "MODIFIED" if target == "CUSTOMER_NAME" else "APPROVED",
            "transformation": transform,
            "business_justification": "itest: cover required GDP DIM_CUSTOMER columns",
        })
        used_sources.add(row["SOURCE_COLUMN_ID"])
    for row in tops:
        if row["SOURCE_COLUMN_ID"] not in used_sources:
            decisions.append({"source_column_id": row["SOURCE_COLUMN_ID"], "decision": "REJECTED"})
            used_sources.add(row["SOURCE_COLUMN_ID"])

    saved = call(cur, "CALL MAPPING.SAVE_MAPPING_DECISIONS(%s, %s)", (run_id, json.dumps(decisions)))
    assert saved["status"]["complete"], saved["status"]

    reviewed = call(cur, "CALL CORE.REVIEW_TRANSITION(%s, %s, %s, %s, %s)",
                    (run_id, "MAPPING_APPROVED", "APPROVE", "itest: mappings complete", None))
    assert reviewed["state"]["current_state"] == "MAPPING_APPROVED"

    sttm = call(cur, "CALL CONTRACT.GENERATE_STTM(%s)", (run_id,))
    assert sttm["state"]["current_state"] == "STTM_REVIEW"
    call(cur, "CALL CORE.REVIEW_TRANSITION(%s, %s, %s, %s, %s)",
         (run_id, "STTM_APPROVED", "APPROVE", "itest: STTM matches GDP DIM_CUSTOMER", None))

    soda = call(cur, "CALL CONTRACT.GENERATE_SODA(%s)", (run_id,))
    assert soda["state"]["current_state"] == "SODA_REVIEW"
    call(cur, "CALL CORE.REVIEW_TRANSITION(%s, %s, %s, %s, %s)",
         (run_id, "SODA_APPROVED", "APPROVE", "itest: Soda checks cover grain and required columns", None))

    dbt = call(cur, "CALL CODEGEN.GENERATE_DBT(%s)", (run_id,))
    assert dbt["state"]["current_state"] == "VALIDATION_PENDING"
    assert dbt["files"]

    validation = call(cur, "CALL CODEGEN.VALIDATE_DBT(%s)", (run_id,))
    assert validation["status"] == "PASSED", validation
    assert validation["state"]["current_state"] == "DBT_REVIEW"

    done = call(cur, "CALL CORE.REVIEW_TRANSITION(%s, %s, %s, %s, %s)",
                (run_id, "DBT_APPROVED", "APPROVE", "itest: generated project reviewed", None))
    assert done["state"]["current_state"] == "COMPLETED"
