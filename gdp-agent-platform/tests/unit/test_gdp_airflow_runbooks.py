"""The GDP Airflow runbook pack: valid knowledge, and real GDP log lines match the right runbook."""

import json

import pytest

from infrastructure.seed_knowledge import GDP_RUNBOOK_PACK, load_runbooks


def _gdp_items():
    return [dict(k, source_reference=k["key"]) for k in load_runbooks() if k["key"].startswith("ops.runbook.gdp.")]


def test_gdp_pack_is_loaded_with_the_generic_pack():
    pack = json.loads(GDP_RUNBOOK_PACK.read_text(encoding="utf-8"))["knowledge"]
    keys = {k["key"] for k in load_runbooks()}
    assert {k["key"] for k in pack} <= keys
    assert "ops.runbook.object-does-not-exist" in keys   # the generic pack is still loaded
    assert len(pack) >= 20


@pytest.mark.parametrize("log, key", [
    ("dbt_silver_company failed: Operations rate limit exceeded (093563)", "ops.runbook.gdp.dbt-rate-limit"),
    ("EXECUTE DBT PROJECT ... SYSTEM$GET_DBT_LOG: Database Error in model silver_company", "ops.runbook.gdp.dbt-model-failed"),
    ("mutex_gate: lock property_es_sync held in GDP_DAG_MUTEX, AirflowSkipException", "ops.runbook.gdp.engine-mutex"),
    ("Back-off pulling image: ImagePullBackOff GDP_ENGINE_VERSION", "ops.runbook.gdp.engine-pod-not-started"),
    ("wait_es_sync: es_sync_done status=failed post_mdm_run_ids=[...]", "ops.runbook.gdp.wait-es-sync"),
    ("publish_kafka POST /kafka/v3/clusters/abc/topics/t/records -> 401; KAFKA_OUTBOX_PROPERTY", "ops.runbook.gdp.kafka-publish"),
    ("Broken DAG: gdp_dags.py ... gdp_PARSE_FAILURE_qa", "ops.runbook.gdp.parse-failure-canary"),
    ("SnapLogic failure report: child pipeline aborted, error_count=12 > GDP_SNAPLOGIC_MAX_ERROR_COUNT", "ops.runbook.gdp.snaplogic-pipeline-terminal-failure"),
])
def test_gdp_log_lines_match_their_runbook(log, key):
    from services.ops import pipeline

    found = pipeline.match_runbooks(_gdp_items(), log)
    assert found and found[0]["source_reference"] == key
