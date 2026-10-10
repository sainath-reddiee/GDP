"""Snowpark handlers for the workflow stored procedures (deployed in CORE, EXECUTE AS OWNER).

Authorization model: callers get USAGE on specific procedures, never write privileges on tables.
TRANSITION_RUN performs SYSTEM transitions only; REVIEW_TRANSITION (granted to reviewer roles)
performs HUMAN transitions and records the review decision.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional

from services.workflow.graph import CANCELLED_STATE, FAILED_STATE, INITIAL_STATE, graph_from_rows
from services.workflow.state_machine import (
    RunContext,
    evaluate,
    failed_from_after,
    lifecycle_status,
    run_status_for,
    stage_rail,
)

MAX_TEXT = 4000
MAX_DETAILS_BYTES = 16_000
REVIEW_DECISIONS = {"APPROVE", "REJECT", "REQUEST_CHANGES", "REOPEN"}
MAX_BULK_RUNS = 200
RUN_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

CREATE_RUN_FIELDS = {
    "RUN_NAME", "DOMAIN_ID", "SOURCE_SYSTEM_ID", "TARGET_MODEL",
    "CORRELATION_ID", "ENVIRONMENT", "CONFIG_VERSION", "MODELING_STANDARD",
}


def _n(value: Optional[Any]) -> Any:
    """Snowpark binds Python None as the string 'None'; pair with NULLIF(?, '') in SQL."""
    return "" if value is None else value


def _rows(session, sql: str, params: Optional[list] = None) -> list:
    return [r.as_dict() for r in session.sql(sql, params=params or []).collect()]


def _parse_details(details_json: Optional[str]) -> Dict[str, Any]:
    if not details_json:
        return {}
    assert len(details_json.encode("utf-8")) <= MAX_DETAILS_BYTES, "details_json too large"
    details = json.loads(details_json)
    assert isinstance(details, dict), "details_json must be a JSON object"
    return details


def _text(value: Optional[str], field: str, required: bool = False) -> Optional[str]:
    if value is None or str(value).strip() == "":
        assert not required, f"{field} is required"
        return None
    value = str(value).strip()
    assert len(value) <= MAX_TEXT, f"{field} exceeds {MAX_TEXT} characters"
    return value


def _load_graph(session):
    states = _rows(session, "SELECT STATE, STAGE, KIND, ORDINAL, PHASE, ENABLED, RETRY_TO, GRAPH_VERSION "
                            "FROM CORE.WORKFLOW_STATE")
    assert states, "CORE.WORKFLOW_STATE is empty; run the deploy seed step"
    versions = {r["GRAPH_VERSION"] for r in states}
    assert len(versions) == 1, f"WORKFLOW_STATE holds multiple graph versions: {sorted(versions)}"
    transitions = _rows(session, "SELECT FROM_STATE, TO_STATE, ACTOR, GUARD, ENABLED FROM CORE.WORKFLOW_TRANSITION")
    return graph_from_rows(versions.pop(), states, transitions)


def _current_user(session) -> str:
    return session.sql("SELECT CURRENT_USER() AS U").collect()[0]["U"]


def _get_run(session, run_id: str) -> Dict[str, Any]:
    rows = _rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    assert rows, f"run {run_id} not found"
    return rows[0]


def _is_closed(run: Dict[str, Any]) -> bool:
    """Archived or soft-deleted; columns are absent before V006 is applied."""
    return bool(run.get("IS_ARCHIVED")) or run.get("DELETED_AT") is not None


def parse_run_ids(value: Any) -> List[str]:
    """RUN_IDS arrive as a Python list (ARRAY argument) or JSON text; every id must be a UUID."""
    if isinstance(value, str):
        value = json.loads(value or "[]")
    assert isinstance(value, list), "RUN_IDS must be an array of run ids"
    ids = sorted({str(v).strip() for v in value if str(v).strip()})
    assert ids, "RUN_IDS is empty"
    assert len(ids) <= MAX_BULK_RUNS, f"at most {MAX_BULK_RUNS} runs per call"
    bad = [i for i in ids if not RUN_ID.match(i)]
    assert not bad, f"not run ids: {bad[:5]}"
    return ids


def interrupted_state(run: Dict[str, Any]) -> Optional[str]:
    """State a FAILED or CANCELLED run stopped in; a run cancelled after failing keeps FAILED_FROM_STATE."""
    state = run["CURRENT_STATE"]
    if state == FAILED_STATE or (state == CANCELLED_STATE and run.get("PREVIOUS_STATE") == FAILED_STATE):
        return run["FAILED_FROM_STATE"]
    return run["PREVIOUS_STATE"]


def review_decision_error(to_state: str, decision: str) -> Optional[str]:
    """APPROVE only moves a run into an *_APPROVED state; any other decision only moves it back."""
    if to_state.endswith("_APPROVED") and decision != "APPROVE":
        return f"{to_state} requires DECISION = APPROVE, got {decision}"
    if not to_state.endswith("_APPROVED") and decision == "APPROVE":
        return f"APPROVE cannot move the run to {to_state}; use REJECT, REQUEST_CHANGES or REOPEN"
    return None


def _state_payload(graph, run: Dict[str, Any]) -> Dict[str, Any]:
    state = run["CURRENT_STATE"]
    interrupted = interrupted_state(run)
    allowed = [
        {"to_state": t.to_state, "actor": t.actor}
        for t in graph.outgoing(state)
        if t.enabled and graph.states[t.to_state].enabled
    ]
    return {
        "run_id": run["RUN_ID"],
        "current_state": state,
        "current_stage": run["CURRENT_STAGE"],
        "status": run["STATUS"],
        "lifecycle": lifecycle_status(state, _is_closed(run)),
        "is_archived": _is_closed(run),
        "state_version": run["STATE_VERSION"],
        "failed_from_state": run["FAILED_FROM_STATE"],
        "failure_reason": run["FAILURE_REASON"],
        "graph_version": graph.version,
        "stages": stage_rail(graph, state, interrupted),
        "allowed_transitions": allowed,
    }


def _apply_transition(session, graph, run: Dict[str, Any], to_state: str, actor_type: str,
                      reason: Optional[str], details: Dict[str, Any],
                      in_transaction: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    ctx = RunContext(run["CURRENT_STATE"], run["FAILED_FROM_STATE"], _is_closed(run))
    decision = evaluate(graph, ctx, to_state, actor_type)
    if not decision.allowed:
        raise ValueError(f"TRANSITION_REJECTED: {decision.reason}")

    is_retry = ctx.current_state == FAILED_STATE and to_state != CANCELLED_STATE
    failure_reason = reason if to_state == FAILED_STATE else (None if is_retry else run["FAILURE_REASON"])
    completed = to_state in ("COMPLETED", CANCELLED_STATE)
    actor = _current_user(session)
    event_id = str(uuid.uuid4())

    session.sql("BEGIN TRANSACTION").collect()
    try:
        updated = session.sql(
            """
            UPDATE CORE.WORKFLOW_RUN
               SET PREVIOUS_STATE    = CURRENT_STATE,
                   CURRENT_STATE     = ?,
                   CURRENT_STAGE     = NULLIF(?, ''),
                   STATUS            = ?,
                   FAILED_FROM_STATE = NULLIF(?, ''),
                   FAILURE_REASON    = NULLIF(?, ''),
                   RETRY_COUNT       = RETRY_COUNT + ?,
                   STATE_VERSION     = STATE_VERSION + 1,
                   UPDATED_AT        = CURRENT_TIMESTAMP(),
                   COMPLETED_AT      = IFF(?, CURRENT_TIMESTAMP(), COMPLETED_AT)
             WHERE RUN_ID = ? AND STATE_VERSION = ?
            """,
            params=[
                to_state,
                _n(decision.new_stage),
                decision.new_status,
                _n(failed_from_after(ctx, to_state)),
                _n(failure_reason),
                1 if is_retry else 0,
                completed,
                run["RUN_ID"],
                run["STATE_VERSION"],
            ],
        ).collect()
        if updated[0][0] != 1:
            raise ValueError("CONCURRENT_UPDATE: run state changed since it was read; reload and retry")

        session.sql(
            """
            INSERT INTO CORE.WORKFLOW_EVENT
                (EVENT_ID, RUN_ID, FROM_STATE, TO_STATE, ACTOR_TYPE, ACTOR, REASON, DETAILS, GRAPH_VERSION)
            SELECT ?, ?, ?, ?, ?, ?, NULLIF(?, ''), PARSE_JSON(?), ?
            """,
            params=[event_id, run["RUN_ID"], ctx.current_state, to_state, actor_type, actor,
                    _n(reason), json.dumps(details), graph.version],
        ).collect()
        if in_transaction:
            in_transaction(event_id)
        session.sql("COMMIT").collect()
    except Exception:
        session.sql("ROLLBACK").collect()
        raise

    return {"event_id": event_id, "from_state": ctx.current_state, "to_state": to_state,
            "status": decision.new_status, "actor": actor}


def create_run(session, payload_json: str) -> Dict[str, Any]:
    payload = _parse_details(payload_json)
    unknown = set(payload) - CREATE_RUN_FIELDS
    assert not unknown, f"unknown fields: {sorted(unknown)}"
    values = {k: _text(payload.get(k), k) for k in CREATE_RUN_FIELDS}
    _text(values["RUN_NAME"], "RUN_NAME", required=True)
    standard = values["MODELING_STANDARD"]
    assert standard is None or str(standard).upper() in ("GDP", "GENERIC"), "MODELING_STANDARD must be GDP or GENERIC"
    domain_id = _n(values["DOMAIN_ID"])
    target_model = _n(values["TARGET_MODEL"])
    if not domain_id and target_model:
        found = _rows(
            session,
            """
            SELECT DOMAIN_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
             WHERE ACTIVE_FLAG AND UPPER(TARGET_TABLE) = UPPER(?)
             ORDER BY TARGET_TABLE LIMIT 1
            """,
            [str(target_model).split(".")[-1]],
        )
        if found:
            domain_id = found[0]["DOMAIN_ID"]

    graph = _load_graph(session)
    run_id = str(uuid.uuid4())
    session.sql("BEGIN TRANSACTION").collect()
    try:
        session.sql(
            """
            INSERT INTO CORE.WORKFLOW_RUN
                (RUN_ID, RUN_NAME, DOMAIN_ID, SOURCE_SYSTEM_ID, TARGET_MODEL, CURRENT_STATE, CURRENT_STAGE,
                 STATUS, CREATED_BY, CORRELATION_ID, ENVIRONMENT, CONFIG_VERSION, GRAPH_VERSION, MODELING_STANDARD)
            SELECT ?, ?, NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), ?, ?, ?, CURRENT_USER(),
                   NULLIF(?, ''), COALESCE(NULLIF(?, ''), 'DEV'), NULLIF(?, ''), ?, NULLIF(?, '')
            """,
            params=[run_id, values["RUN_NAME"], domain_id, _n(values["SOURCE_SYSTEM_ID"]),
                    target_model, INITIAL_STATE, graph.states[INITIAL_STATE].stage,
                    run_status_for(graph, INITIAL_STATE), _n(values["CORRELATION_ID"]),
                    _n(values["ENVIRONMENT"]), _n(values["CONFIG_VERSION"]), graph.version,
                    str(standard).upper() if standard else ""],
        ).collect()
        session.sql(
            """
            INSERT INTO CORE.WORKFLOW_EVENT
                (EVENT_ID, RUN_ID, FROM_STATE, TO_STATE, ACTOR_TYPE, ACTOR, REASON, DETAILS, GRAPH_VERSION)
            SELECT ?, ?, NULL, ?, 'SYSTEM', CURRENT_USER(), 'run created', PARSE_JSON(?), ?
            """,
            params=[str(uuid.uuid4()), run_id, INITIAL_STATE, json.dumps(payload), graph.version],
        ).collect()
        session.sql("COMMIT").collect()
    except Exception:
        session.sql("ROLLBACK").collect()
        raise
    return _state_payload(graph, _get_run(session, run_id))


def get_workflow_state(session, run_id: str) -> Dict[str, Any]:
    run_id = _text(run_id, "RUN_ID", required=True)
    graph = _load_graph(session)
    return _state_payload(graph, _get_run(session, run_id))


def transition_run(session, run_id: str, to_state: str, reason: str, details_json: str) -> Dict[str, Any]:
    """SYSTEM transition (agent tools, backend jobs). HUMAN gates are rejected here."""
    run_id = _text(run_id, "RUN_ID", required=True)
    to_state = _text(to_state, "TO_STATE", required=True).upper()
    reason = _text(reason, "REASON")
    details = _parse_details(details_json)

    graph = _load_graph(session)
    result = _apply_transition(session, graph, _get_run(session, run_id), to_state, "SYSTEM", reason, details)
    result["state"] = _state_payload(graph, _get_run(session, run_id))
    return result


def review_transition(session, run_id: str, to_state: str, decision: str,
                      business_justification: str, comments: str) -> Dict[str, Any]:
    """HUMAN gate transition. The reviewer is the Snowflake user executing the call."""
    run_id = _text(run_id, "RUN_ID", required=True)
    to_state = _text(to_state, "TO_STATE", required=True).upper()
    decision = _text(decision, "DECISION", required=True).upper()
    assert decision in REVIEW_DECISIONS, f"DECISION must be one of {sorted(REVIEW_DECISIONS)}"
    justification = _text(business_justification, "BUSINESS_JUSTIFICATION")
    comments = _text(comments, "COMMENTS")
    if decision == "APPROVE":
        assert justification, "BUSINESS_JUSTIFICATION is required to approve"
    mismatch = review_decision_error(to_state, decision)
    assert mismatch is None, mismatch

    graph = _load_graph(session)
    run = _get_run(session, run_id)
    from_state = run["CURRENT_STATE"]
    details = {"decision": decision, "business_justification": justification, "comments": comments}

    if from_state == "MAPPING_REVIEW" and to_state == "MAPPING_APPROVED" and decision == "APPROVE":
        from services.mapping.procedures import mapping_status
        status = mapping_status(session, run_id)
        if status["source_columns"]:
            assert status["complete"], (
                "MAPPING_INCOMPLETE: decide every source column and cover required targets. "
                f"undecided={status['undecided']} missing_required={status['missing_required_targets']}"
            )

    def record_review(event_id: str) -> None:
        session.sql(
            """
            INSERT INTO CORE.REVIEW_DECISION
                (REVIEW_ID, RUN_ID, EVENT_ID, STAGE, FROM_STATE, TO_STATE, DECISION,
                 BUSINESS_JUSTIFICATION, COMMENTS, REVIEWER)
            SELECT ?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''), NULLIF(?, ''), CURRENT_USER()
            """,
            params=[str(uuid.uuid4()), run_id, event_id, graph.states[from_state].stage,
                    from_state, to_state, decision, _n(justification), _n(comments)],
        ).collect()
        if decision != "APPROVE":
            return
        if from_state == "STTM_REVIEW" and to_state == "STTM_APPROVED":
            session.sql(
                "UPDATE CONTRACT.STTM_REGISTRY SET STATUS = 'APPROVED', REVIEWED_BY = CURRENT_USER(), "
                "REVIEWED_AT = CURRENT_TIMESTAMP() WHERE RUN_ID = ? AND STATUS = 'REVIEW'",
                params=[run_id],
            ).collect()
        if from_state == "SODA_REVIEW" and to_state == "SODA_APPROVED":
            session.sql(
                "UPDATE CONTRACT.SODA_EXPECTATION_REGISTRY SET STATUS = 'APPROVED', REVIEWED_BY = CURRENT_USER(), "
                "REVIEWED_AT = CURRENT_TIMESTAMP() WHERE RUN_ID = ? AND IS_CURRENT AND STATUS <> 'REJECTED'",
                params=[run_id],
            ).collect()
            from services.common.audit import tool_call

            try:  # tool_call records a FAILED row with the error before re-raising
                with tool_call(session, run_id, "store_approved_soda_set", {"run_id": run_id}) as call:
                    from services.soda.procedures import store_approved_set

                    store_approved_set(session, run_id)
                    call.summary = "approved Soda checks kept as knowledge"
            except Exception:
                pass  # knowledge is a by-product; the approval itself must not fail on it
        if from_state == "DBT_REVIEW" and to_state == "DBT_APPROVED":
            session.sql(
                "UPDATE CODEGEN.DBT_GENERATION_REGISTRY SET GENERATION_STATUS = 'APPROVED' "
                "WHERE RUN_ID = ? AND GENERATION_STATUS = 'GENERATED'",
                params=[run_id],
            ).collect()

    result = _apply_transition(session, graph, run, to_state, "HUMAN", f"review: {decision}",
                               details, in_transaction=record_review)
    if from_state == "DBT_REVIEW" and to_state == "DBT_APPROVED" and decision == "APPROVE":
        _apply_transition(session, graph, _get_run(session, run_id), "COMPLETED", "SYSTEM",
                          "phase 1 complete after dbt approval", {})
    result["state"] = _state_payload(graph, _get_run(session, run_id))
    return result


def _record_run_event(session, run: Dict[str, Any], actor_type: str, reason: str, details: Dict[str, Any]) -> None:
    """Lifecycle event that does not change workflow state (FROM_STATE = TO_STATE)."""
    session.sql(
        """
        INSERT INTO CORE.WORKFLOW_EVENT
            (EVENT_ID, RUN_ID, FROM_STATE, TO_STATE, ACTOR_TYPE, ACTOR, REASON, DETAILS, GRAPH_VERSION)
        SELECT ?, ?, ?, ?, ?, CURRENT_USER(), ?, PARSE_JSON(?), ?
        """,
        params=[str(uuid.uuid4()), run["RUN_ID"], run["CURRENT_STATE"], run["CURRENT_STATE"], actor_type,
                reason, json.dumps(details), run["GRAPH_VERSION"]],
    ).collect()


def set_runs_archived(session, run_ids_json: str, archived: bool) -> Dict[str, Any]:
    """Archive hides runs from the default list and freezes their state; restore reverses it.

    Bumping STATE_VERSION makes any stage procedure that read the run before archiving fail
    with CONCURRENT_UPDATE instead of moving an archived run.
    """
    ids = parse_run_ids(run_ids_json)
    archived = bool(archived)
    graph = _load_graph(session)
    changed: List[str] = []
    skipped: List[Dict[str, str]] = []
    for run_id in ids:
        found = _rows(session, "SELECT * FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
        if not found:
            skipped.append({"run_id": run_id, "reason": "not found"})
            continue
        run = found[0]
        if run.get("DELETED_AT") is not None:
            skipped.append({"run_id": run_id, "reason": "deleted"})
            continue
        if bool(run.get("IS_ARCHIVED")) == archived:
            skipped.append({"run_id": run_id, "reason": "already archived" if archived else "not archived"})
            continue
        state = graph.states.get(run["CURRENT_STATE"])
        if archived and state is not None and state.kind == "RUNNING":
            skipped.append({"run_id": run_id, "reason": f"{run['CURRENT_STATE']} is still running"})
            continue
        session.sql("BEGIN TRANSACTION").collect()
        try:
            updated = session.sql(
                """
                UPDATE CORE.WORKFLOW_RUN
                   SET IS_ARCHIVED   = ?,
                       ARCHIVED_AT   = IFF(?, CURRENT_TIMESTAMP(), NULL),
                       ARCHIVED_BY   = IFF(?, CURRENT_USER(), NULL),
                       STATE_VERSION = STATE_VERSION + 1,
                       UPDATED_AT    = CURRENT_TIMESTAMP()
                 WHERE RUN_ID = ? AND STATE_VERSION = ? AND DELETED_AT IS NULL
                """,
                params=[archived, archived, archived, run_id, run["STATE_VERSION"]],
            ).collect()
            if updated[0][0] != 1:
                raise ValueError("CONCURRENT_UPDATE: run state changed since it was read; reload and retry")
            _record_run_event(session, run, "HUMAN", "run archived" if archived else "run restored",
                              {"archived": archived})
            session.sql("COMMIT").collect()
        except Exception:
            session.sql("ROLLBACK").collect()
            raise
        changed.append(run_id)
    return {"archived": archived, "changed": changed, "skipped": skipped}
