"""CORE.SP_CLEANUP_PIPELINE_RUNS: soft-delete runs and purge their sandbox artifacts.

Purged:  landing tables the runs created, the runs' staged dbt workspace and auto-named DBT PROJECTs.
Kept:    a landing table another live run still points at (landing names are <SOURCE>__<OBJECT>, so
         runs of the same source share them), METADATA.TABLE_PROFILES, @METADATA.PROFILES_STAGE,
         and the audit trail (CORE.WORKFLOW_EVENT, CORE.REVIEW_DECISION, registries).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.common.sql import clip, rows
from services.dbt.workspace import run_workspace_cleanup
from services.source.identifiers import fqn
from services.workflow.procedures import _load_graph, _record_run_event, parse_run_ids

PROTECTED_SCHEMAS = frozenset({
    "METADATA", "INFORMATION_SCHEMA", "CORE", "SOURCE", "PROFILE", "KNOWLEDGE",
    "MAPPING", "CONTRACT", "CODEGEN", "AUDIT",
})
PURGEABLE_STATES = frozenset({"COMPLETED", "CANCELLED", "FAILED"})

TableKey = Tuple[str, str, str]


def _key(row: Dict[str, Any]) -> TableKey:
    return (str(row["LANDING_DATABASE"]), str(row["LANDING_SCHEMA"]), str(row["LANDING_TABLE"]))


def plan_landing_drops(owned: Iterable[Dict[str, Any]],
                       others: Iterable[Dict[str, Any]]) -> Tuple[List[TableKey], List[Dict[str, Any]]]:
    """Which of the cleaned-up runs' landing tables can be dropped.

    `others` are landing registry rows of live runs outside the cleanup set. A table they reference
    is kept; so is anything in a platform schema, whatever the registry says.
    """
    shared: Dict[TableKey, List[str]] = {}
    for row in others:
        shared.setdefault(_key(row), []).append(str(row["RUN_ID"]))
    drops: List[TableKey] = []
    kept: List[Dict[str, Any]] = []
    for key in sorted({_key(r) for r in owned}):
        name = ".".join(key)
        if key[1].upper() in PROTECTED_SCHEMAS:
            kept.append({"table": name, "reason": f"{key[1]} is a platform schema"})
        elif key in shared:
            kept.append({"table": name, "reason": "still used by another run",
                         "runs": sorted(set(shared[key]))[:5]})
        else:
            drops.append(key)
    return drops, kept


def _purge_landing(session, run_ids: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"dropped": [], "kept": [], "errors": []}
    ids_json = json.dumps(run_ids)
    owned = rows(session, """
        SELECT RUN_ID, LANDING_DATABASE, LANDING_SCHEMA, LANDING_TABLE
          FROM SOURCE.LANDING_TABLE_REGISTRY
         WHERE ARRAY_CONTAINS(RUN_ID::VARIANT, PARSE_JSON(?)::ARRAY) AND INGESTION_STATUS <> 'PURGED'""",
                 [ids_json])
    if not owned:
        return out
    names = sorted({r["LANDING_TABLE"] for r in owned})
    others = rows(session, """
        SELECT L.RUN_ID, L.LANDING_DATABASE, L.LANDING_SCHEMA, L.LANDING_TABLE
          FROM SOURCE.LANDING_TABLE_REGISTRY L
          JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = L.RUN_ID
         WHERE NOT ARRAY_CONTAINS(L.RUN_ID::VARIANT, PARSE_JSON(?)::ARRAY)
           AND R.DELETED_AT IS NULL
           AND L.INGESTION_STATUS <> 'PURGED'
           AND ARRAY_CONTAINS(L.LANDING_TABLE::VARIANT, PARSE_JSON(?)::ARRAY)""",
                  [ids_json, json.dumps(names)])
    drops, out["kept"] = plan_landing_drops(owned, others)
    for database, schema, table in drops:
        name = f"{database}.{schema}.{table}"
        try:
            session.sql(f"DROP TABLE IF EXISTS {fqn(database, schema, table)}").collect()
        except Exception as exc:
            out["errors"].append({"table": name, "error": clip(exc, 400)})
            continue
        session.sql("""
            UPDATE SOURCE.LANDING_TABLE_REGISTRY SET INGESTION_STATUS = 'PURGED'
             WHERE ARRAY_CONTAINS(RUN_ID::VARIANT, PARSE_JSON(?)::ARRAY)
               AND LANDING_DATABASE = ? AND LANDING_SCHEMA = ? AND LANDING_TABLE = ?""",
                    params=[ids_json, database, schema, table]).collect()
        out["dropped"].append(name)
    return out


def _mark_deleted(session, run: Dict[str, Any], details: Dict[str, Any]) -> bool:
    session.sql("BEGIN TRANSACTION").collect()
    try:
        updated = session.sql("""
            UPDATE CORE.WORKFLOW_RUN
               SET DELETED_AT = CURRENT_TIMESTAMP(), DELETED_BY = CURRENT_USER(), IS_ARCHIVED = TRUE,
                   ARCHIVED_AT = COALESCE(ARCHIVED_AT, CURRENT_TIMESTAMP()),
                   ARCHIVED_BY = COALESCE(ARCHIVED_BY, CURRENT_USER()),
                   STATE_VERSION = STATE_VERSION + 1, UPDATED_AT = CURRENT_TIMESTAMP()
             WHERE RUN_ID = ? AND DELETED_AT IS NULL""", params=[run["RUN_ID"]]).collect()
        if updated[0][0] != 1:
            session.sql("ROLLBACK").collect()
            return False
        _record_run_event(session, run, "HUMAN", "run deleted", details)
        session.sql("COMMIT").collect()
        return True
    except Exception:
        session.sql("ROLLBACK").collect()
        raise


def cleanup_pipeline_runs(session, run_ids: Any, drop_sandbox_tables: bool,
                          delete_workspaces: Optional[bool] = None,
                          mark_deleted: Optional[bool] = True) -> Dict[str, Any]:
    ids = parse_run_ids(run_ids)
    drop_sandbox = bool(drop_sandbox_tables)
    delete_ws = drop_sandbox if delete_workspaces is None else bool(delete_workspaces)
    mark = True if mark_deleted is None else bool(mark_deleted)

    graph = _load_graph(session)
    found = {r["RUN_ID"]: r for r in rows(session, """
        SELECT RUN_ID, CURRENT_STATE, GRAPH_VERSION, IS_ARCHIVED, DELETED_AT
          FROM CORE.WORKFLOW_RUN WHERE ARRAY_CONTAINS(RUN_ID::VARIANT, PARSE_JSON(?)::ARRAY)""",
        [json.dumps(ids)])}
    result: Dict[str, Any] = {
        "requested": len(ids), "not_found": [i for i in ids if i not in found],
        "deleted": [], "skipped": [], "landing": {"dropped": [], "kept": [], "errors": []},
        "workspaces": [], "profiles_preserved": True,
    }

    targets: List[str] = []
    for run_id in ids:
        run = found.get(run_id)
        if run is None:
            continue
        state = graph.states.get(run["CURRENT_STATE"])
        if state is not None and state.kind == "RUNNING":
            result["skipped"].append({"run_id": run_id, "reason": f"{run['CURRENT_STATE']} is still running"})
            continue
        closed = bool(run.get("IS_ARCHIVED")) or run.get("DELETED_AT") is not None
        if not mark and not closed and run["CURRENT_STATE"] not in PURGEABLE_STATES:
            result["skipped"].append({"run_id": run_id,
                                      "reason": "archive or finish the run before purging its sandbox"})
            continue
        targets.append(run_id)

    if drop_sandbox and targets:
        result["landing"] = _purge_landing(session, targets)
    if delete_ws:
        execute = lambda sql: rows(session, sql)  # noqa: E731
        result["workspaces"] = [run_workspace_cleanup(execute, run_id) for run_id in targets]

    for run_id in targets:
        details = {"drop_sandbox_tables": drop_sandbox, "delete_workspaces": delete_ws,
                   "landing_dropped": len(result["landing"]["dropped"])}
        if mark and found[run_id].get("DELETED_AT") is None:
            if _mark_deleted(session, found[run_id], details):
                result["deleted"].append(run_id)
        elif drop_sandbox or delete_ws:
            _record_run_event(session, found[run_id], "HUMAN", "sandbox purged", details)
    return result


def cleanup_pipeline_runs_basic(session, run_ids: Any, drop_sandbox_tables: bool) -> Dict[str, Any]:
    return cleanup_pipeline_runs(session, run_ids, drop_sandbox_tables)
