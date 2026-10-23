"""Multi-target mapping, phase 4: a run that mapped one source to a hub and its spokes continues as one table run per
target table. When its mapping is approved (services/workflow/procedures.py review_transition), each included table of
its target set (CORE.RUN_TARGET, hub first) gets a child run (PARENT_RUN_ID) that starts at MAPPING_APPROVED, pinned to
its entity, with the parent's landing tables, profiles, candidates and decisions for that table copied under its own
run id. Every later stage (STTM, dbt, QA, Data Quality, validation) then reads a child exactly as it reads any run.

Copied ids are derived, not random: UUID_STRING(<child run id>, <parent id>), so a copy refers to the copies of what
the parent row referred to (a decision's candidate and source column, a profile's landing table) and copying twice
writes the same ids. The physical landing tables are shared; only registry rows are copied. The rows are written in
the approval's transaction, so the parent is never approved with half its children.

Approving the mapping again (after it was reopened) refreshes the children still at MAPPING_APPROVED or STTM_PENDING
with the new decisions; a child that has moved on is left as it is and reported.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from services.common.sql import rows

REFRESHABLE = ("MAPPING_APPROVED", "STTM_PENDING")


def table_runs_needed(session, run_id: str) -> List[Dict[str, Any]]:
    """The included tables of a parent's target set, hub first; empty unless the run maps to more than one table."""
    try:
        found = rows(session, """SELECT ENTITY_NAME, TARGET_FQN, TARGET_TABLE_ID, ROLE, ORDINAL FROM CORE.RUN_TARGET
                                   WHERE RUN_ID = ? AND IS_CURRENT AND INCLUDED ORDER BY ORDINAL""", [run_id])
    except Exception as exc:
        if "RUN_TARGET" in str(exc).upper() and "DOES NOT EXIST" in str(exc).upper():
            return []  # before V074
        raise
    return found if len(found) > 1 else []


def children(session, run_id: str) -> List[Dict[str, Any]]:
    """The run's table runs (not deleted), oldest first."""
    return rows(session, """SELECT RUN_ID, MODEL_ENTITY, TARGET_MODEL, CURRENT_STATE FROM CORE.WORKFLOW_RUN
                             WHERE PARENT_RUN_ID = ? AND DELETED_AT IS NULL ORDER BY CREATED_AT""", [run_id])


def fan_out(session, parent: Dict[str, Any], state_stage: str, state_status: str, graph_version: str) -> Dict[str, Any]:
    """One table run per included target table of an approved multi-target parent (see the module notes). Returns
    {"created": [...], "refreshed": [...], "kept": [...]} with each child's run id and entity."""
    tables = table_runs_needed(session, parent["RUN_ID"])
    out: Dict[str, List[Dict[str, Any]]] = {"created": [], "refreshed": [], "kept": []}
    if not tables:
        return out
    existing = {str(c["MODEL_ENTITY"] or "").upper(): c for c in children(session, parent["RUN_ID"])}
    for t in tables:
        entity = str(t["ENTITY_NAME"]).upper()
        child = existing.get(entity)
        if child and child["CURRENT_STATE"] not in REFRESHABLE:
            out["kept"].append({"run_id": child["RUN_ID"], "entity": entity, "state": child["CURRENT_STATE"]})
            continue
        if child:
            child_id = child["RUN_ID"]
            for table in ("MAPPING_CANDIDATE", "MAPPING_DECISION"):
                session.sql(f"UPDATE CORE.{table} SET IS_CURRENT = FALSE WHERE RUN_ID = ? AND IS_CURRENT",
                            params=[child_id]).collect()
        else:
            child_id = str(uuid.uuid4())
            _create_child(session, parent, child_id, t, state_stage, state_status, graph_version)
        _copy_sources(session, parent["RUN_ID"], child_id)
        _copy_mapping(session, parent["RUN_ID"], child_id, t["TARGET_TABLE_ID"])
        out["refreshed" if child else "created"].append({"run_id": child_id, "entity": entity, "role": t["ROLE"]})
    return out


def _create_child(session, parent: Dict[str, Any], child_id: str, t: Dict[str, Any], stage: str, status: str,
                  graph_version: str) -> None:
    """The child's run row: the parent's (source, domain, model, standard, pipeline), pinned to the table's entity,
    starting at MAPPING_APPROVED."""
    name = f"{parent['RUN_NAME']}: {t['ENTITY_NAME']}"[:256]
    session.sql("""
        INSERT INTO CORE.WORKFLOW_RUN
        SELECT * REPLACE (? AS RUN_ID, ? AS RUN_NAME, ? AS TARGET_MODEL, ? AS MODEL_ENTITY,
                          'MAPPING_APPROVED' AS CURRENT_STATE, ? AS CURRENT_STAGE, NULL AS PREVIOUS_STATE,
                          NULL AS FAILED_FROM_STATE, ? AS STATUS, 0 AS STATE_VERSION, CURRENT_USER() AS CREATED_BY,
                          CURRENT_TIMESTAMP() AS CREATED_AT, CURRENT_TIMESTAMP() AS UPDATED_AT, NULL AS COMPLETED_AT,
                          NULL AS FAILURE_REASON, 0 AS RETRY_COUNT, FALSE AS IS_ARCHIVED, NULL AS ARCHIVED_AT,
                          NULL AS ARCHIVED_BY, NULL AS DELETED_AT, NULL AS DELETED_BY, ? AS PARENT_RUN_ID)
          FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?""",
                params=[child_id, name, t["TARGET_FQN"], t["ENTITY_NAME"], stage, status, parent["RUN_ID"],
                        parent["RUN_ID"]]).collect()
    session.sql("""
        INSERT INTO CORE.WORKFLOW_EVENT (EVENT_ID, RUN_ID, FROM_STATE, TO_STATE, ACTOR_TYPE, ACTOR, REASON, DETAILS,
                                         GRAPH_VERSION)
        SELECT ?, ?, NULL, 'MAPPING_APPROVED', 'SYSTEM', CURRENT_USER(), ?, PARSE_JSON(?), ?""",
                params=[str(uuid.uuid4()), child_id, f"table run of {parent['RUN_NAME']} for {t['ENTITY_NAME']}"[:4000],
                        json.dumps({"parent_run_id": parent["RUN_ID"], "entity": t["ENTITY_NAME"], "role": t["ROLE"],
                                    "target": t["TARGET_FQN"]}), graph_version]).collect()


def _copy_sources(session, parent_id: str, child_id: str) -> None:
    """The parent's landing tables, their columns and the current profiles, under the child's run id (rows the child
    already has are not written again)."""
    session.sql("""
        INSERT INTO CORE.LANDING_TABLE_REGISTRY
        SELECT * REPLACE (UUID_STRING(?, LANDING_ID) AS LANDING_ID, ? AS RUN_ID)
          FROM CORE.LANDING_TABLE_REGISTRY P
         WHERE P.RUN_ID = ? AND NOT EXISTS (SELECT 1 FROM CORE.LANDING_TABLE_REGISTRY C
                                             WHERE C.LANDING_ID = UUID_STRING(?, P.LANDING_ID))""",
                params=[child_id, child_id, parent_id, child_id]).collect()
    session.sql("""
        INSERT INTO CORE.LANDING_COLUMN_REGISTRY
        SELECT * REPLACE (UUID_STRING(?, LANDING_COLUMN_ID) AS LANDING_COLUMN_ID, UUID_STRING(?, LANDING_ID) AS LANDING_ID)
          FROM CORE.LANDING_COLUMN_REGISTRY P
         WHERE P.LANDING_ID IN (SELECT LANDING_ID FROM CORE.LANDING_TABLE_REGISTRY WHERE RUN_ID = ?)
           AND NOT EXISTS (SELECT 1 FROM CORE.LANDING_COLUMN_REGISTRY C
                            WHERE C.LANDING_COLUMN_ID = UUID_STRING(?, P.LANDING_COLUMN_ID))""",
                params=[child_id, child_id, parent_id, child_id]).collect()
    session.sql("""
        INSERT INTO CORE.PROFILE_REGISTRY
        SELECT * REPLACE (UUID_STRING(?, PROFILE_ID) AS PROFILE_ID, ? AS RUN_ID,
                          UUID_STRING(?, SOURCE_TABLE_ID) AS SOURCE_TABLE_ID, UUID_STRING(?, SOURCE_COLUMN_ID) AS SOURCE_COLUMN_ID)
          FROM CORE.PROFILE_REGISTRY P
         WHERE P.RUN_ID = ? AND P.IS_CURRENT AND NOT EXISTS (SELECT 1 FROM CORE.PROFILE_REGISTRY C
                                                              WHERE C.PROFILE_ID = UUID_STRING(?, P.PROFILE_ID))""",
                params=[child_id, child_id, child_id, child_id, parent_id, child_id]).collect()


def _copy_mapping(session, parent_id: str, child_id: str, table_id: str) -> None:
    """The parent's current candidates and decisions for one target table, under the child's run id."""
    of_table = "SELECT TARGET_COLUMN_ID FROM CORE.TARGET_COLUMN_REGISTRY WHERE TARGET_TABLE_ID = ?"
    session.sql(f"""
        INSERT INTO CORE.MAPPING_CANDIDATE
        SELECT * REPLACE (UUID_STRING(?, CANDIDATE_ID) AS CANDIDATE_ID, ? AS RUN_ID,
                          UUID_STRING(?, SOURCE_COLUMN_ID) AS SOURCE_COLUMN_ID, ? AS TARGET_TABLE_ID)
          FROM CORE.MAPPING_CANDIDATE
         WHERE RUN_ID = ? AND IS_CURRENT AND (TARGET_TABLE_ID = ? OR TARGET_COLUMN_ID IN ({of_table}))""",
                params=[child_id, child_id, child_id, table_id, parent_id, table_id, table_id]).collect()
    session.sql(f"""
        INSERT INTO CORE.MAPPING_DECISION
        SELECT * REPLACE (UUID_STRING(?, DECISION_ID) AS DECISION_ID, ? AS RUN_ID,
                          UUID_STRING(?, SOURCE_COLUMN_ID) AS SOURCE_COLUMN_ID,
                          IFF(CANDIDATE_ID IS NULL, NULL, UUID_STRING(?, CANDIDATE_ID)) AS CANDIDATE_ID, ? AS TARGET_TABLE_ID)
          FROM CORE.MAPPING_DECISION
         WHERE RUN_ID = ? AND IS_CURRENT AND (TARGET_TABLE_ID = ? OR TARGET_COLUMN_ID IN ({of_table}))""",
                params=[child_id, child_id, child_id, child_id, table_id, parent_id, table_id, table_id]).collect()


def progress(child_states: List[str]) -> Dict[str, int]:
    """How far a parent's table runs are (pure): total, done (code approved or completed), failed."""
    done = sum(1 for s in child_states if s in ("DBT_APPROVED", "COMPLETED"))
    failed = sum(1 for s in child_states if s and (s.endswith("_FAILED") or s == "FAILED"))
    return {"total": len(child_states), "done": done, "failed": failed}
