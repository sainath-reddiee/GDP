"""Multi-target mapping, phase 4: approving the mapping of a run mapped to a hub and its spokes creates one table run
per table (hub first), starting at MAPPING_APPROVED with that table's candidates and decisions and the parent's
landing tables and profiles copied under its own run id (derived ids). The parent then waits on its table runs.
Snowflake is a fake."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.mapping import children as ch  # noqa: E402
from services.workflow import autopilot  # noqa: E402
from test_model_workbench import Session  # noqa: E402

PARENT = {"RUN_ID": "p1", "RUN_NAME": "LIGHTBOX", "DOMAIN_ID": "d1"}
SET = [{"ENTITY_NAME": "PROPERTY_CORE", "TARGET_FQN": "DB.PROPERTY.PROPERTY_CORE", "TARGET_TABLE_ID": "t-core", "ROLE": "HUB", "ORDINAL": 1},
       {"ENTITY_NAME": "PROPERTY_ADDRESS", "TARGET_FQN": "DB.PROPERTY.PROPERTY_ADDRESS", "TARGET_TABLE_ID": "t-addr", "ROLE": "SPOKE", "ORDINAL": 2}]


def session(targets=SET, existing=()):
    return Session({"FROM CORE.RUN_TARGET": list(targets), "WHERE PARENT_RUN_ID = ? AND DELETED_AT IS NULL": list(existing)})


def test_one_table_is_not_fanned_out():
    s = session(targets=SET[:1])
    assert ch.fan_out(s, PARENT, "MAPPING", "IN_PROGRESS", "g1") == {"created": [], "refreshed": [], "kept": []}
    assert not s.find("INSERT INTO")


def test_each_table_gets_a_table_run_hub_first():
    s = session()
    out = ch.fan_out(s, PARENT, "MAPPING", "IN_PROGRESS", "g1")
    assert [(c["entity"], c["role"]) for c in out["created"]] == [("PROPERTY_CORE", "HUB"), ("PROPERTY_ADDRESS", "SPOKE")]
    runs = s.find("INSERT INTO CORE.WORKFLOW_RUN SELECT * REPLACE")
    assert len(runs) == 2
    sql, params = runs[0]
    assert "'MAPPING_APPROVED' AS CURRENT_STATE" in sql and "AS PARENT_RUN_ID" in sql and "FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?" in sql
    child = params[0]
    assert params[1:] == ["LIGHTBOX: PROPERTY_CORE", "DB.PROPERTY.PROPERTY_CORE", "PROPERTY_CORE", "MAPPING", "IN_PROGRESS", "p1", "p1"]
    assert child == out["created"][0]["run_id"]
    # its own event, then the sources and that table's mapping copied under its run id
    assert s.find("INSERT INTO CORE.WORKFLOW_EVENT")[0][1][1] == child
    landing = s.find("INSERT INTO CORE.LANDING_TABLE_REGISTRY")[0]
    assert "UUID_STRING(?, LANDING_ID) AS LANDING_ID" in landing[0] and landing[1] == [child, child, "p1", child]
    profile = s.find("INSERT INTO CORE.PROFILE_REGISTRY")[0]
    assert "UUID_STRING(?, SOURCE_TABLE_ID) AS SOURCE_TABLE_ID" in profile[0] and "P.IS_CURRENT" in profile[0]
    decisions = [p for q, p in s.find("INSERT INTO CORE.MAPPING_DECISION")]
    assert decisions[0][-4:] == ["t-core", "p1", "t-core", "t-core"] and decisions[1][-4:] == ["t-addr", "p1", "t-addr", "t-addr"]
    candidates = s.find("INSERT INTO CORE.MAPPING_CANDIDATE")[1]
    assert "IS_CURRENT" in candidates[0] and candidates[1][-4:] == ["t-addr", "p1", "t-addr", "t-addr"]


def test_approving_again_refreshes_waiting_table_runs_and_keeps_the_rest():
    s = session(existing=[{"RUN_ID": "c-core", "MODEL_ENTITY": "PROPERTY_CORE", "CURRENT_STATE": "STTM_REVIEW"},
                          {"RUN_ID": "c-addr", "MODEL_ENTITY": "PROPERTY_ADDRESS", "CURRENT_STATE": "MAPPING_APPROVED"}])
    out = ch.fan_out(s, PARENT, "MAPPING", "IN_PROGRESS", "g1")
    assert out["kept"] == [{"run_id": "c-core", "entity": "PROPERTY_CORE", "state": "STTM_REVIEW"}]
    assert [c["run_id"] for c in out["refreshed"]] == ["c-addr"] and not out["created"]
    assert not s.find("INSERT INTO CORE.WORKFLOW_RUN")
    retired = [p for q, p in s.find("SET IS_CURRENT = FALSE WHERE RUN_ID = ? AND IS_CURRENT")]
    assert retired == [["c-addr"], ["c-addr"]]            # its old candidates and decisions
    assert s.find("INSERT INTO CORE.MAPPING_DECISION")[0][1][1] == "c-addr"


def test_before_v074_there_is_nothing_to_fan_out():
    class Missing(Session):
        def sql(self, sql, params=None):
            if "CORE.RUN_TARGET" in sql:
                raise RuntimeError("Object 'CORE.RUN_TARGET' does not exist or not authorized.")
            return super().sql(sql, params)

    assert ch.table_runs_needed(Missing(), "p1") == []


def test_progress():
    assert ch.progress(["DBT_APPROVED", "COMPLETED", "STTM_REVIEW", "VALIDATION_FAILED"]) == {"total": 4, "done": 2, "failed": 1}


# ---------------------------------------------------------------- the parent waits on its table runs

def test_autopilot_parks_a_parent_on_its_table_runs():
    facts = {"pipeline": "SILVER", "has_children": True, "table_runs": {"total": 4, "done": 1, "failed": 0}}
    step = autopilot.next_action("MAPPING_APPROVED", facts)
    assert step["kind"] == "GATE" and "1 of 4 done" in step["label"]
    done = autopilot.next_action("MAPPING_APPROVED", {**facts, "table_runs": {"total": 4, "done": 4, "failed": 0}})
    assert done["kind"] == "DONE"
    # a run without table runs goes on as before
    assert autopilot.next_action("MAPPING_APPROVED", {"pipeline": "SILVER"})["action"] == "generate_sttm"


def test_a_parent_with_table_runs_does_not_generate_its_own_sttm(monkeypatch):
    from services.sttm import procedures as sp

    class FakeStage:
        def __init__(self, session, run_id):
            self.run = {"RUN_ID": run_id}

        def require(self, *states):
            pass

    monkeypatch.setattr(sp, "Stage", FakeStage)
    s = Session({"WHERE PARENT_RUN_ID = ? AND DELETED_AT IS NULL": [{"RUN_ID": "c1", "MODEL_ENTITY": "X", "CURRENT_STATE": "MAPPING_APPROVED"}]})
    with pytest.raises(ValueError, match="RUN_HAS_TABLE_RUNS"):
        sp.generate_sttm(s, "p1")


def test_approving_the_mapping_fans_out_in_the_approval(monkeypatch):
    from services.workflow import procedures as wp

    class State:
        stage = "MAPPING"

    class Graph:
        version = "g1"
        states = {"MAPPING_REVIEW": State(), "MAPPING_APPROVED": State()}

    seen = {}
    monkeypatch.setattr(wp, "_load_graph", lambda s: Graph())
    monkeypatch.setattr(wp, "_get_run", lambda s, r: {**PARENT, "CURRENT_STATE": "MAPPING_REVIEW"})
    monkeypatch.setattr(wp, "run_status_for", lambda g, st: "IN_PROGRESS")
    monkeypatch.setattr(wp, "_state_payload", lambda g, r: {})
    monkeypatch.setattr("services.mapping.procedures.mapping_status", lambda s, r: {"source_columns": 3, "complete": True})

    def apply(session, graph, run, to_state, actor, reason, details, in_transaction=None):
        in_transaction("e1")    # the fan-out runs inside the approval's transaction
        return {}

    monkeypatch.setattr(wp, "_apply_transition", apply)
    monkeypatch.setattr(ch, "fan_out", lambda s, run, stage, status, version: seen.update(run=run["RUN_ID"], stage=stage) or
                        {"created": [{"run_id": "c1", "entity": "PROPERTY_CORE"}], "refreshed": [], "kept": []})
    out = wp.review_transition(Session(), "p1", "MAPPING_APPROVED", "APPROVE", "mapping reviewed", "")
    assert seen == {"run": "p1", "stage": "MAPPING"} and out["table_runs"]["created"][0]["run_id"] == "c1"
