"""The resolution engine: ingest a failure, claim it, check the guardrails, search for a published fix, then run the
fix, wait for a person's approval, or find the cause and open a ticket (slides 2 and 3 of the design).

Everything here works on a Db (services.ops.sqlio: the API's connection, or a Snowpark session inside a procedure).
The worker calls CORE.SP_CLAIM_AND_RESOLVE and CORE.SP_RESUME_APPROVED (services.resolve.procedures), so the decisions
run in Snowflake. Two steps need credentials Snowflake does not hold, and are left to the worker: clearing the Airflow
task (CLEAR_STATE = REQUESTED, done by services.resolve.bridge) and the Jira ticket (a JIRA row on CORE.NOTIFICATION,
delivered by the outbox).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.ops.redact import redact
from services.resolve import rules

log = logging.getLogger("gdp.resolve")
SYSTEM = "system"
SEARCH_SERVICE = "KNOWN_ISSUE_SEARCH"
SEARCH_LIMIT = 8
CLAIM_STALE_MINUTES = 30   # a claim whose procedure died goes back to PENDING after this long
CLAIM_GRACE_SECONDS = 120  # a failure without an exception class waits this long for its log
MAX_ATTEMPTS = 3           # claims that end in an error before the failure is handed to a person (V065)
MAX_FIXES_PER_RUN = 2      # automatic runs of one fix for one task in one DAG run before it counts as not repairing it

FAILURE_COLUMNS = ("failure_id", "env_id", "incident_id", "dag_id", "task_id", "run_id", "map_index", "try_number",
                   "exception_class", "exception_text", "error_signature", "log_url", "query_id", "dbt_node_id", "status",
                   "flow_mode", "issue_id", "search_score", "ticket_id", "clear_state", "clear_detail", "rca", "detail",
                   "claimed_by", "claimed_at", "created_at", "updated_at")
ISSUE_COLUMNS = ("issue_id", "title", "error_signature", "dag_id", "task_id", "exception_class", "match_text", "fix_type",
                 "procedure_fqn", "skill_id", "approval_required", "status", "version_no", "owner", "source_incident_id",
                 "source_failure_id", "notes", "created_by", "created_at", "published_by", "published_at", "updated_at",
                 "proposed_by_agent", "rationale")


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def database_of(db: Any) -> str:
    found = db.query("SELECT CURRENT_DATABASE() AS D")
    return str((found[0].get("d") if found else "") or "")


# ---------------------------------------------------------------- 1. ingest (called where incidents are opened)

def ingest(db: Any, candidate: Dict[str, Any], incident_id: Optional[str]) -> Optional[str]:
    """Write a PENDING failure for a failed task try, once per try (MERGE on the dedup key). No search here: the
    claim does that. Returns the failure id when a row was added, else None."""
    if candidate.get("kind") != "RETRIES_EXHAUSTED" or not candidate.get("task_id") or candidate.get("retrying"):
        return None
    excerpt = candidate.get("error_excerpt")
    parsed = rules.parse_failure(excerpt)
    try_number = candidate.get("try_number")
    if try_number is None:   # the occurrence key ends with the try: task:<run>:<task>:<map>:<try>
        tail = str(candidate.get("occurrence_key") or "").rsplit(":", 1)[-1]
        try_number = int(tail) if tail.isdigit() else None
    key = rules.dedup_key(candidate["env_id"], candidate["dag_id"], candidate.get("task_id"), candidate.get("run_id"),
                          candidate.get("map_index"), try_number)
    failure_id = str(uuid.uuid4())
    sig = rules.error_signature(candidate["dag_id"], candidate.get("task_id"), parsed["exception_class"], excerpt)
    added = db.execute_count("""
        MERGE INTO CORE.FAILURE_EVENT T USING (SELECT %s AS DEDUP_KEY) S ON T.DEDUP_KEY = S.DEDUP_KEY
        WHEN NOT MATCHED THEN INSERT (FAILURE_ID, DEDUP_KEY, ENV_ID, INCIDENT_ID, DAG_ID, TASK_ID, RUN_ID, MAP_INDEX,
             TRY_NUMBER, EXCEPTION_CLASS, EXCEPTION_TEXT, ERROR_SIGNATURE, LOG_URL, QUERY_ID, DBT_NODE_ID, STATUS)
        VALUES (%s, S.DEDUP_KEY, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING')""",
                             (key, failure_id, candidate["env_id"], incident_id, candidate["dag_id"],
                              candidate.get("task_id"), candidate.get("run_id"),
                              int(candidate["map_index"]) if candidate.get("map_index") is not None else -1, try_number,
                              parsed["exception_class"], redact(parsed["exception_text"] or "")[:4000] or None, sig,
                              candidate.get("log_url"), parsed["query_id"], parsed["dbt_node_id"]))
    return failure_id if added >= 1 else None


def reparse(db: Any, env_id: str, task: Dict[str, Any], excerpt: Optional[str]) -> bool:
    """A failed try's error got better (the log replaced a short pushed message): a failure of that try still PENDING
    takes the class, text, signature and ids parsed from it, and its incident the excerpt when it had no traceback.
    True when a failure changed."""
    parsed = rules.parse_failure(excerpt)
    if not parsed["exception_class"]:
        return False
    key = rules.dedup_key(env_id, task["dag_id"], task.get("task_id"), task.get("run_id"), task.get("map_index"),
                          task.get("try_number"))
    sig = rules.error_signature(task["dag_id"], task.get("task_id"), parsed["exception_class"], excerpt)
    changed = db.execute_count("""
        UPDATE CORE.FAILURE_EVENT SET EXCEPTION_CLASS = %s, EXCEPTION_TEXT = %s, ERROR_SIGNATURE = %s,
               QUERY_ID = COALESCE(%s, QUERY_ID), DBT_NODE_ID = COALESCE(%s, DBT_NODE_ID), UPDATED_AT = CURRENT_TIMESTAMP(),
               DETAIL = 'error read again from the task log'
         WHERE DEDUP_KEY = %s AND STATUS = 'PENDING'""",
                               (parsed["exception_class"], redact(parsed["exception_text"] or "")[:4000] or None, sig,
                                parsed["query_id"], parsed["dbt_node_id"], key)) >= 1
    if changed:
        db.execute("""UPDATE CORE.INCIDENT SET ERROR_EXCERPT = %s
                       WHERE INCIDENT_ID = (SELECT MAX(INCIDENT_ID) FROM CORE.FAILURE_EVENT WHERE DEDUP_KEY = %s)
                         AND (ERROR_EXCERPT IS NULL OR POSITION('Traceback (most recent call last)' IN ERROR_EXCERPT) = 0)""",
                   (redact(excerpt or "")[:4000], key))
    return changed


# ---------------------------------------------------------------- reads

def get_failure(db: Any, failure_id: str) -> Optional[Dict[str, Any]]:
    found = db.query(f"SELECT {', '.join(FAILURE_COLUMNS)} FROM CORE.FAILURE_EVENT WHERE FAILURE_ID = %s", (failure_id,))
    if not found:
        return None
    row = dict(found[0])
    row["rca"] = _json(row.get("rca"))
    return row


def get_issue(db: Any, issue_id: str) -> Optional[Dict[str, Any]]:
    found = db.query(f"SELECT {', '.join(ISSUE_COLUMNS)} FROM CORE.KNOWN_ISSUE WHERE ISSUE_ID = %s", (issue_id,))
    return dict(found[0]) if found else None


def get_skill(db: Any, skill_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not skill_id:
        return None
    found = db.query("SELECT SKILL_ID, SKILL_NAME, DESCRIPTION, STEPS, VERSION_NO, OWNER FROM CORE.FIX_SKILL WHERE SKILL_ID = %s",
                     (skill_id,))
    if not found:
        return None
    row = dict(found[0])
    row["steps"] = _json(row.get("steps"))
    return row


def load_preference(db: Any) -> Dict[str, Any]:
    try:
        found = db.query("""SELECT AUTO_RESOLVE_ENABLED, SEARCH_SCORE_MIN, MAX_PARALLEL_TASKS, AIRFLOW_CLEAR_ENABLED,
                                   DEFAULT_APPROVAL_REQUIRED, GRAPH_HOPS, NOTIFY_TARGET, RCA_MODE, AGENT_PROPOSALS_ENABLED,
                                   AGENT_FIX_SCOPE
                              FROM CORE.CONTROL_PREFERENCE WHERE IS_ACTIVE ORDER BY UPDATED_AT DESC LIMIT 1""")
    except Exception:   # before V063: no agent proposal columns yet
        found = db.query("""SELECT AUTO_RESOLVE_ENABLED, SEARCH_SCORE_MIN, MAX_PARALLEL_TASKS, AIRFLOW_CLEAR_ENABLED,
                                   DEFAULT_APPROVAL_REQUIRED, GRAPH_HOPS, NOTIFY_TARGET, RCA_MODE
                              FROM CORE.CONTROL_PREFERENCE WHERE IS_ACTIVE ORDER BY UPDATED_AT DESC LIMIT 1""")
    return rules.preference(found[0] if found else None)


def load_scopes(db: Any) -> List[Dict[str, Any]]:
    return db.query("SELECT SCOPE_ID, DAG_ID, TASK_ID, FLOW_MODE, ENABLED, OWNER FROM CORE.PIPELINE_SCOPE")


def load_routes(db: Any) -> List[Dict[str, Any]]:
    return db.query("SELECT ROUTE_ID, DAG_ID, TASK_ID, APPROVER, NOTIFY_TARGET FROM CORE.APPROVAL_ROUTE")


# ---------------------------------------------------------------- status changes

def move(db: Any, failure_id: str, current: str, target: str, fields: Optional[Dict[str, Any]] = None) -> bool:
    """Compare-and-set the status (slide 4 moves only), with other columns. True when this caller moved it."""
    if not rules.can_move(current, target):
        raise ValueError(f"a failure cannot go from {current} to {target}")
    sets, params = ["STATUS = %s", "UPDATED_AT = CURRENT_TIMESTAMP()"], [target]
    for key, value in (fields or {}).items():
        if key == "rca":
            sets.append("RCA = PARSE_JSON(%s)")
            params.append(json.dumps(value, default=str))
        else:
            sets.append(f"{key.upper()} = %s")
            params.append(value)
    params += [failure_id, current]
    return db.execute_count(f"UPDATE CORE.FAILURE_EVENT SET {', '.join(sets)} WHERE FAILURE_ID = %s AND STATUS = %s",
                            tuple(params)) == 1


def release_stale_claims(db: Any) -> int:
    """IN_PROGRESS rows whose procedure died mid-way go back to PENDING (a claim never sticks forever); one that keeps
    dying is given up after MAX_ATTEMPTS."""
    stale = db.query(f"""SELECT FAILURE_ID FROM CORE.FAILURE_EVENT WHERE STATUS = 'IN_PROGRESS'
                           AND CLAIMED_AT < DATEADD(minute, -{CLAIM_STALE_MINUTES}, CURRENT_TIMESTAMP())""")
    for r in stale:
        retry_or_give_up(db, r["failure_id"], "the claim expired (the procedure stopped mid-way)")
    return len(stale)


def retry_or_give_up(db: Any, failure_id: str, reason: str) -> str:
    """After an error while resolving: back to PENDING for another attempt, or TICKETED once MAX_ATTEMPTS were used,
    so a failure the engine cannot process reaches a person instead of being claimed (and analysed) forever.
    Returns the new status, or '' when the failure was no longer IN_PROGRESS."""
    text = redact(reason)[:3000]
    found = db.query("SELECT COALESCE(ATTEMPTS, 0) AS N, INCIDENT_ID FROM CORE.FAILURE_EVENT WHERE FAILURE_ID = %s",
                     (failure_id,))
    attempts = int((found[0].get("n") if found else 0) or 0) + 1
    status = "TICKETED" if attempts >= MAX_ATTEMPTS else "PENDING"
    detail = (f"gave up after {attempts} attempts: {text}" if status == "TICKETED" else f"retrying after: {text}")[:4000]
    changed = db.execute_count("""UPDATE CORE.FAILURE_EVENT SET STATUS = %s, ATTEMPTS = %s, CLAIMED_BY = NULL,
                                         CLAIMED_AT = NULL, DETAIL = %s, UPDATED_AT = CURRENT_TIMESTAMP()
                                   WHERE FAILURE_ID = %s AND STATUS = 'IN_PROGRESS'""",
                               (status, attempts, detail, failure_id))
    if changed < 1:
        return ""
    if status == "TICKETED":
        failure = {"failure_id": failure_id, "incident_id": (found[0].get("incident_id") if found else None)}
        _jira(db, failure, "gave_up", failure_id,
              redact(f"Automatic resolution gave up on this failure after {attempts} attempts. Last error: {text}. "
                     "A person needs to look at it.")[:4000], raise_ticket=True)
        _incident_event(db, failure.get("incident_id"), "resolution_gave_up", {"attempts": attempts, "error": text[:500]})
    return status


def claim(db: Any, holder: str, limit: int) -> List[Dict[str, Any]]:
    """Take up to `limit` PENDING failures (oldest first). Each row is claimed with a compare-and-set, so two callers
    never take the same failure; IN_PROGRESS is set before any search. A failure whose error has no exception class
    yet waits up to CLAIM_GRACE_SECONDS for the log to bring it (fill_error_excerpts and reparse), so it is not
    searched and analysed on an empty error."""
    ids = db.query(f"""SELECT FAILURE_ID FROM CORE.FAILURE_EVENT
                        WHERE STATUS = 'PENDING' AND (EXCEPTION_CLASS IS NOT NULL
                              OR CREATED_AT < DATEADD(second, -{int(CLAIM_GRACE_SECONDS)}, CURRENT_TIMESTAMP()))
                        ORDER BY CREATED_AT LIMIT {int(limit)}""")
    out = []
    for r in ids:
        got = db.execute_count("""UPDATE CORE.FAILURE_EVENT SET STATUS = 'IN_PROGRESS', CLAIMED_BY = %s,
                                         CLAIMED_AT = CURRENT_TIMESTAMP(), UPDATED_AT = CURRENT_TIMESTAMP()
                                   WHERE FAILURE_ID = %s AND STATUS = 'PENDING'""", (holder[:256], r["failure_id"]))
        if got == 1:
            row = get_failure(db, r["failure_id"])
            if row:
                out.append(row)
    return out


def _incident_event(db: Any, incident_id: Optional[str], kind: str, detail: Dict[str, Any], actor: str = SYSTEM) -> None:
    if not incident_id:
        return
    try:
        from services.ops.incidents import SqlStore

        SqlStore(db).event(incident_id, kind, actor, detail)
    except Exception as exc:   # the timeline is a courtesy; never fail the resolution for it
        log.warning("incident event %s on %s not written: %s", kind, incident_id, exc)


def _audit(db: Any, failure: Dict[str, Any], issue: Optional[Dict[str, Any]], outcome: str, detail: Dict[str, Any],
           actor: str, resolution_id: Optional[str] = None) -> str:
    rid = resolution_id or str(uuid.uuid4())
    db.execute("""INSERT INTO CORE.RESOLUTION_RUN (RESOLUTION_ID, FAILURE_ID, ISSUE_ID, FIX_TYPE, FIX_NAME, OUTCOME, DETAIL,
                                                   ACTOR, FINISHED_AT)
                  SELECT %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), %s, CURRENT_TIMESTAMP()""",
               (rid, failure["failure_id"], (issue or {}).get("issue_id"), (issue or {}).get("fix_type"),
                fix_name(issue), outcome, json.dumps(detail, default=str), actor[:256]))
    return rid


def fix_name(issue: Optional[Dict[str, Any]]) -> Optional[str]:
    if not issue:
        return None
    if str(issue.get("fix_type") or "").upper() == "PROCEDURE":
        return issue.get("procedure_fqn")
    return issue.get("skill_name") or issue.get("skill_id")


# ---------------------------------------------------------------- 3. search for a published fix

def search_fixes(db: Any, failure: Dict[str, Any], database: Optional[str] = None,
                 search: Optional[Callable[[Dict[str, Any]], List[Tuple[str, Optional[float]]]]] = None
                 ) -> List[Tuple[Dict[str, Any], Optional[float]]]:
    """Candidates [(issue row, service score or None)]: PUBLISHED fixes with the same error signature first (read from
    the table, so a fix published a moment ago is found before the search service refreshes), then the Cortex Search
    hits on the match text, filtered on DAG, task and class. Every hit is re-read from the table, so a fix retired
    since the last refresh is never returned as PUBLISHED."""
    out: List[Tuple[Dict[str, Any], Optional[float]]] = []
    seen = set()
    exact = db.query(f"""SELECT {', '.join(ISSUE_COLUMNS)} FROM CORE.KNOWN_ISSUE
                          WHERE STATUS = 'PUBLISHED' AND ERROR_SIGNATURE = %s ORDER BY VERSION_NO DESC LIMIT 5""",
                     (failure.get("error_signature"),))
    for r in exact:
        seen.add(r["issue_id"])
        out.append((dict(r), None))
    try:
        hits = (search or cortex_search(db, database))(failure)
    except Exception as exc:   # the service may not exist yet or be refreshing: the exact match above still works
        log.info("known issue search unavailable: %s", exc)
        hits = []
    for issue_id, score in hits:
        if issue_id in seen:
            continue
        row = get_issue(db, issue_id)
        if row:
            seen.add(issue_id)
            out.append((row, score))
    return out


def search_request(failure: Dict[str, Any]) -> Dict[str, Any]:
    def either(column: str, value: Optional[str]) -> Dict[str, Any]:
        options = [{"@eq": {column: ""}}]
        if value:
            options.append({"@eq": {column: str(value)}})
        return {"@or": options}
    return {"query": rules.match_text(failure.get("dag_id"), failure.get("task_id"), failure.get("exception_class"),
                                      failure.get("exception_text")),
            "columns": ["ISSUE_ID", "TITLE", "STATUS"],
            "filter": {"@and": [{"@eq": {"STATUS": "PUBLISHED"}}, either("DAG_ID", failure.get("dag_id")),
                                either("TASK_ID", failure.get("task_id")),
                                either("EXCEPTION_CLASS", failure.get("exception_class"))]},
            "limit": SEARCH_LIMIT}


def _score(result: Dict[str, Any]) -> Optional[float]:
    scores = result.get("@scores") or {}
    for key in ("cosine_similarity", "reranker_score"):
        if scores.get(key) is not None:
            try:
                return float(scores[key])
            except (TypeError, ValueError):
                pass
    return None


def cortex_search(db: Any, database: Optional[str]) -> Callable[[Dict[str, Any]], List[Tuple[str, Optional[float]]]]:
    def run(failure: Dict[str, Any]) -> List[Tuple[str, Optional[float]]]:
        name = f"{database or database_of(db)}.CORE.{SEARCH_SERVICE}"
        found = db.query("SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(%s, %s) AS R", (name, json.dumps(search_request(failure))))
        result = _json(found[0]["r"]) if found else {}
        return [(str(r.get("ISSUE_ID")), _score(r)) for r in (result or {}).get("results", []) if r.get("ISSUE_ID")]
    return run


# ---------------------------------------------------------------- 4a. approval

def request_approval(db: Any, failure: Dict[str, Any], issue: Dict[str, Any], routes: List[Dict[str, Any]],
                     prefs: Dict[str, Any], kind: str = "RUN", rca: Optional[Dict[str, Any]] = None,
                     text: Optional[str] = None) -> Dict[str, Any]:
    """APPROVAL_REQUEST OPEN for the route's approver; the failure waits (AWAITING_APPROVAL) and this run ends. The
    approver sees it in Approvals and My Work, and as a comment on the incident's Jira ticket. KIND RUN: run a published
    fix; KIND PROPOSAL: publish and run the fix the agent proposed (its root cause is kept on the failure)."""
    route = rules.approver_for(routes, failure["dag_id"], failure.get("task_id")) or {}
    approval_id = str(uuid.uuid4())
    db.execute("""INSERT INTO CORE.APPROVAL_REQUEST (APPROVAL_ID, FAILURE_ID, ISSUE_ID, FIX_TYPE, FIX_NAME, STATUS, APPROVER, KIND)
                  VALUES (%s, %s, %s, %s, %s, 'OPEN', %s, %s)""",
               (approval_id, failure["failure_id"], issue["issue_id"], issue.get("fix_type"), fix_name(issue),
                route.get("approver"), kind))
    fields: Dict[str, Any] = {"detail": f"waiting for {route.get('approver') or 'an approver'} to approve "
                                        + ("the agent's proposed fix " if kind == "PROPOSAL" else "") + str(issue.get("title"))}
    if rca is not None:
        fields["rca"] = rca
    move(db, failure["failure_id"], "IN_PROGRESS", "AWAITING_APPROVAL", fields)
    text = text or (f"Known fix found for this failure: {issue.get('title')} ({issue.get('fix_type')} {fix_name(issue)}). "
                    f"It needs approval before it runs" + (f"; approver: {route['approver']}" if route.get("approver") else "") +
                    ". Approve or reject it on the incident page or in Approvals.")
    _jira(db, failure, "approval", approval_id, text, raise_ticket=False)
    _incident_event(db, failure.get("incident_id"), "fix_approval_requested",
                    {"approval_id": approval_id, "issue_id": issue["issue_id"], "approver": route.get("approver")})
    return {"action": "approval", "approval_id": approval_id, "approver": route.get("approver")}


def decide_approval(db: Any, approval_id: str, actor: str, approve: bool, note: Optional[str], may_approve_any: bool,
                    ) -> Dict[str, Any]:
    """A person's decision (API). The routed approver, or anyone holding OPS.FIX_APPROVE when no approver is routed,
    may decide; the author of the fix may not approve its own run. The worker's resume step does the rest."""
    found = db.query("""SELECT A.APPROVAL_ID, A.STATUS, A.APPROVER, A.FAILURE_ID, A.ISSUE_ID, A.KIND, K.CREATED_BY,
                               K.PUBLISHED_BY, K.STATUS AS ISSUE_STATUS
                          FROM CORE.APPROVAL_REQUEST A LEFT JOIN CORE.KNOWN_ISSUE K ON K.ISSUE_ID = A.ISSUE_ID
                         WHERE A.APPROVAL_ID = %s""", (approval_id,))
    if not found:
        raise LookupError(f"approval {approval_id} not found")
    req = found[0]
    if str(req.get("status")) != "OPEN":
        raise ValueError(f"this approval is already {str(req.get('status')).lower()}")
    approver = str(req.get("approver") or "")
    if approver and approver.lower() != actor.lower() and not may_approve_any:
        raise PermissionError(f"this fix is routed to {approver}")
    if approve and str(req.get("created_by") or "").lower() == actor.lower():
        raise PermissionError("the author of a fix cannot approve running it")
    status = "APPROVED" if approve else "REJECTED"
    if approve and str(req.get("kind") or "RUN") == "PROPOSAL" and str(req.get("issue_status")) == "DRAFT":
        # one approval publishes the agent's fix (the approver is the second person) and lets it run on its own
        # from now on; the worker then runs it for this failure
        from services.resolve import publish

        db.execute("UPDATE CORE.KNOWN_ISSUE SET APPROVAL_REQUIRED = FALSE WHERE ISSUE_ID = %s AND STATUS = 'DRAFT'",
                   (req["issue_id"],))
        publish.publish(db, req["issue_id"], actor)
    changed = db.execute_count("""UPDATE CORE.APPROVAL_REQUEST SET STATUS = %s, DECIDED_BY = %s, DECISION_NOTE = %s,
                                         DECIDED_AT = CURRENT_TIMESTAMP() WHERE APPROVAL_ID = %s AND STATUS = 'OPEN'""",
                                  (status, actor[:256], (note or "")[:2000] or None, approval_id))
    if changed != 1:
        raise ValueError("someone decided this approval a moment ago")
    failure = get_failure(db, req["failure_id"]) or {}
    _incident_event(db, failure.get("incident_id"), "fix_approved" if approve else "fix_rejected",
                    {"approval_id": approval_id, "note": (note or "")[:500]}, actor)
    return {"approval_id": approval_id, "status": status}


# ---------------------------------------------------------------- 4a. run the fix

def run_fix(db: Any, failure: Dict[str, Any], issue: Dict[str, Any], prefs: Dict[str, Any], actor: str,
            executor: Any, current: str) -> Dict[str, Any]:
    """Run the procedure or the skill. SUCCESS: RESOLVED, the Airflow task cleared (unless airflow_clear_enabled is off),
    the incident MITIGATED (it resolves itself when the rerun succeeds). FAILED: the cause is looked for and a ticket
    opened, so a fix that stopped working reaches a person."""
    database = executor.database
    resolution_id = str(uuid.uuid4())
    if failure.get("issue_id") != issue.get("issue_id"):   # an approved agent proposal: the failure now names its fix
        db.execute("UPDATE CORE.FAILURE_EVENT SET ISSUE_ID = %s WHERE FAILURE_ID = %s", (issue["issue_id"], failure["failure_id"]))
        failure = {**failure, "issue_id": issue["issue_id"]}
    try:
        if str(issue.get("fix_type")).upper() == "PROCEDURE":
            result = {"result": executor.call_procedure(rules.fix_procedure(issue.get("procedure_fqn"), database),
                                                        failure["failure_id"])}
        else:
            skill = get_skill(db, issue.get("skill_id"))
            if not skill:
                raise ValueError(f"skill {issue.get('skill_id')} not found")
            issue = {**issue, "skill_name": skill.get("skill_name")}
            result = executor.run_skill(resolution_id, failure, skill)
        ok, error = True, None
    except Exception as exc:
        ok, error, result = False, redact(f"{type(exc).__name__}: {exc}")[:2000], {}
    detail = {**result, "error": error} if error else result
    _audit(db, failure, issue, "SUCCESS" if ok else "FAILED", detail, actor, resolution_id)
    if not ok:
        _incident_event(db, failure.get("incident_id"), "fix_failed", {"issue_id": issue["issue_id"], "error": error})
        return {"action": "fix_failed", "resolution_id": resolution_id, "error": error}
    clear = "REQUESTED" if prefs.get("airflow_clear_enabled") else "SKIPPED"
    move(db, failure["failure_id"], current, "RESOLVED",
         {"clear_state": clear, "detail": f"fixed by {issue.get('title')}"[:4000]})
    _incident_event(db, failure.get("incident_id"), "auto_fixed",
                    {"issue_id": issue["issue_id"], "fix": fix_name(issue), "resolution_id": resolution_id,
                     "airflow_clear": clear})
    _mitigate(db, failure.get("incident_id"))
    if _ops_setting(db, "ticket_auto_fixes", True):   # on record in Jira: raised now, closed when the rerun succeeds
        _jira(db, failure, "fixed", failure["failure_id"], fixed_text(failure, issue, clear), raise_ticket=True,
              record=True)
    return {"action": "resolved", "resolution_id": resolution_id, "clear": clear}


def _ops_setting(db: Any, key: str, default: Any) -> Any:
    try:
        from services.ops.incidents import SqlStore

        return SqlStore(db).settings().get(key, default)
    except Exception:
        return default


def fixed_text(failure: Dict[str, Any], issue: Dict[str, Any], clear: str) -> str:
    """The Jira note of an automatic fix: what failed, the cause, the fix that ran and the rerun."""
    rca = failure.get("rca") if isinstance(failure.get("rca"), dict) else {}
    cause = issue.get("rationale") or rca.get("cause") or issue.get("notes")
    if str(issue.get("fix_type") or "").upper() == "PROCEDURE":
        how = issue.get("procedure_fqn")
    else:
        how = f"skill {issue.get('skill_name') or issue.get('skill_id')}"
    lines = [f"Fixed automatically by the platform with the known fix \"{issue.get('title')}\" ({how}).",
             f"Failure: {failure.get('dag_id')}.{failure.get('task_id')}, {failure.get('exception_class') or 'error'}: "
             f"{str(failure.get('exception_text') or '')[:400]}"]
    if cause:
        lines.append(f"Cause: {str(cause)[:800]}")
    if issue.get("proposed_by_agent"):
        lines.append(f"The fix was proposed by the RCA agent and approved by {issue.get('published_by') or 'a person'}; "
                     "it now runs without approval for this failure.")
    lines.append("Rerun: the Airflow task was cleared and runs again; this ticket closes when the rerun succeeds."
                 if clear == "REQUESTED" else "Rerun: clearing Airflow tasks is off; rerun the task to close this ticket.")
    return redact("\n".join(lines))[:4000]


def _mitigate(db: Any, incident_id: Optional[str]) -> None:
    if not incident_id:
        return
    db.execute("""UPDATE CORE.INCIDENT SET STATUS = 'MITIGATED', UPDATED_AT = CURRENT_TIMESTAMP()
                   WHERE INCIDENT_ID = %s AND STATUS IN ('OPEN', 'ACK')""", (incident_id,))


# ---------------------------------------------------------------- 4b. RCA and ticket

def analyse(analyst: Any, failure: Dict[str, Any], prefs: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return analyst.analyse(failure, prefs)
    except Exception as exc:
        return {"cause": "The cause could not be determined automatically.", "evidence": [], "path": [],
                "mode": "none", "error": redact(f"{type(exc).__name__}: {exc}")[:500]}


def open_ticket(db: Any, failure: Dict[str, Any], current: str, analyst: Any, prefs: Dict[str, Any], actor: str,
                reason: str, prior: Optional[Dict[str, Any]] = None, rca: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Find the cause (RCA agent, or the grounded diagnosis) and open the Jira ticket with it: the incident's ticket
    gets the cause as a comment, raised first if the incident has none. Status TICKETED."""
    rca = rca or analyse(analyst, failure, prefs)
    detail = {"reason": reason, "cause": rca.get("cause"), "mode": rca.get("mode"), **({"fix": prior} if prior else {})}
    _audit(db, failure, None, "TICKETED", {**detail, "evidence": rca.get("evidence"), "path": rca.get("path")}, actor)
    move(db, failure["failure_id"], current, "TICKETED", {"rca": rca, "detail": reason[:4000]})
    _jira(db, failure, "rca", failure["failure_id"], ticket_text(failure, rca, reason), raise_ticket=True)
    _incident_event(db, failure.get("incident_id"), "rca_ticketed", {"reason": reason, "cause": str(rca.get("cause"))[:500],
                                                                     "mode": rca.get("mode")})
    return {"action": "ticketed", "cause": rca.get("cause")}


def ticket_text(failure: Dict[str, Any], rca: Dict[str, Any], reason: str) -> str:
    lines = [f"Root-cause analysis ({rca.get('mode') or 'automatic'}): {reason}",
             f"Cause: {rca.get('cause') or 'not determined'}"]
    for e in (rca.get("evidence") or [])[:6]:
        lines.append(f"- {e}")
    if rca.get("path"):
        lines.append("Upstream path: " + " <- ".join(str(p) for p in rca["path"][:8]))
    if rca.get("fix_steps"):
        lines.append("Suggested steps: " + "; ".join(str(s) for s in rca["fix_steps"][:6]))
    if rca.get("proposal_note"):
        lines.append(f"No fix was proposed automatically: {rca['proposal_note']}")
    lines.append("When this is solved, publish the fix as a known fix so the same failure is fixed automatically next time.")
    return redact("\n".join(lines))[:4000]


def _jira(db: Any, failure: Dict[str, Any], what: str, ref: str, text: str, raise_ticket: bool,
          record: bool = False) -> None:
    """Queue a note on the incident's ticket. `record` (an automatic fix) raises the ticket even when the incident
    resolved before the note was sent."""
    if not failure.get("incident_id"):
        return
    from services.ops.notify import enqueue

    enqueue(db, "JIRA", "resolution", f"jira:resolution:{what}:{ref}",
            {"failure_id": failure["failure_id"], "text": text, "raise": raise_ticket, **({"record": True} if record else {})},
            incident_id=failure["incident_id"])


# ---------------------------------------------------------------- 4c. the agent proposes a fix (V063)

AGENT_AUTHOR = "RCA_AGENT"


def propose_fix(db: Any, failure: Dict[str, Any], rca: Dict[str, Any], prefs: Dict[str, Any], database: str,
                routes: List[Dict[str, Any]], proposer: Any = None) -> Optional[Dict[str, Any]]:
    """On a miss: the agent's fix from the safe-action catalogue, checked against Snowflake, as a DRAFT known fix written
    by the agent and an approval of KIND PROPOSAL. One approval publishes and runs it. None when nothing safe and valid
    was proposed (or proposals are off), and the failure is ticketed as before."""
    if not prefs.get("agent_proposals_enabled"):
        return None
    excerpt = ""
    if failure.get("incident_id"):
        found = db.query("SELECT ERROR_EXCERPT FROM CORE.INCIDENT WHERE INCIDENT_ID = %s", (failure["incident_id"],))
        excerpt = str((found[0].get("error_excerpt") if found else "") or "")
    if proposer is None:
        from services.resolve.propose import propose as proposer
    try:
        plan = proposer(db, failure, rca, prefs, database, excerpt)
    except Exception as exc:
        rca["proposal_note"] = redact(f"{type(exc).__name__}: {exc}")[:300]
        return None
    if not plan:
        return None
    from services.resolve import publish

    name = f"agent_{failure['dag_id']}_{str(failure.get('error_signature') or uuid.uuid4().hex)[:8]}"[:240]
    existing = _skill_named(db, name)
    if existing and publish.skill_locked(db, existing):   # never rewrite steps a fix runs or an approver is reviewing
        name, existing = f"{name}_{uuid.uuid4().hex[:6]}", None
    skill = publish.save_skill(db, {"skill_name": name, "steps": plan["steps"],
                                    "description": f"Proposed by the RCA agent for {failure['dag_id']}.{failure.get('task_id')}"},
                               AGENT_AUTHOR, skill_id=existing)
    draft = publish.draft_from_incident(db, failure["incident_id"]) if failure.get("incident_id") else {}
    issue = publish.create(db, {**draft, "title": f"Agent fix: {failure['dag_id']}.{failure.get('task_id')}"[:500],
                                "fix_type": "SKILL", "skill_id": skill["skill_id"], "approval_required": True,
                                "notes": plan["rationale"], "source_failure_id": failure["failure_id"]}, AGENT_AUTHOR)
    db.execute("UPDATE CORE.KNOWN_ISSUE SET PROPOSED_BY_AGENT = TRUE, RATIONALE = %s WHERE ISSUE_ID = %s",
               (plan["rationale"], issue["issue_id"]))
    rca = {**rca, "proposal": {"issue_id": issue["issue_id"], "steps": plan["steps"], "rationale": plan["rationale"],
                               "source": plan["source"], "risk": plan["risk"]}}
    steps = "; ".join(s["note"] for s in plan["steps"])
    text = (f"Root cause: {rca.get('cause')}\nThe agent proposes a fix ({plan['risk']} change): {steps}. "
            f"Why: {plan['rationale']}\nApprove it once on the incident page or in Approvals: it runs now, and the same "
            "failure is then fixed automatically.")
    out = request_approval(db, failure, {**issue, "skill_name": skill["skill_name"]}, routes, prefs, kind="PROPOSAL",
                           rca=rca, text=redact(text)[:4000])
    _incident_event(db, failure.get("incident_id"), "fix_proposed",
                    {"issue_id": issue["issue_id"], "steps": [s["note"] for s in plan["steps"]], "source": plan["source"]})
    return {**out, "action": "proposed"}


def _skill_named(db: Any, name: str) -> Optional[str]:
    found = db.query("SELECT SKILL_ID FROM CORE.FIX_SKILL WHERE SKILL_NAME = %s", (name,))
    return found[0]["skill_id"] if found else None


# ---------------------------------------------------------------- 2-4. one claimed failure

def resolve_one(db: Any, failure: Dict[str, Any], prefs: Dict[str, Any], scopes: List[Dict[str, Any]],
                routes: List[Dict[str, Any]], executor: Any, analyst: Any,
                search: Optional[Callable[[Dict[str, Any]], List[Tuple[str, Optional[float]]]]] = None,
                actor: str = SYSTEM) -> Dict[str, Any]:
    """Guardrails, search, then fix, approval or ticket for one IN_PROGRESS failure."""
    mode = rules.flow_mode(scopes, failure["dag_id"], failure.get("task_id"))
    if mode in (None, "DISABLED"):
        why = "no pipeline scope covers this task" if mode is None else "the pipeline scope is disabled"
        move(db, failure["failure_id"], "IN_PROGRESS", "OUT_OF_SCOPE", {"flow_mode": mode, "detail": why})
        return {"action": "out_of_scope", "detail": why}
    hit, score = (None, 0.0)
    if mode != "TICKET_ONLY":   # TICKET_ONLY skips the search
        hit, score = rules.best_hit(search_fixes(db, failure, executor.database, search), failure, prefs["search_score_min"])
    db.execute("""UPDATE CORE.FAILURE_EVENT SET FLOW_MODE = %s, ISSUE_ID = %s, SEARCH_SCORE = %s,
                         UPDATED_AT = CURRENT_TIMESTAMP() WHERE FAILURE_ID = %s""",
               (mode, (hit or {}).get("issue_id"), score, failure["failure_id"]))
    action = rules.decide(prefs, mode, hit)
    if action == "TICKET":
        if mode == "TICKET_ONLY":
            return open_ticket(db, failure, "IN_PROGRESS", analyst, prefs, actor, "the pipeline is set to ticket only")
        reason = f"no published fix scored {prefs['search_score_min']} or more (best {score})"
        rca = analyse(analyst, failure, prefs)
        proposal = propose_fix(db, failure, rca, prefs, executor.database, routes)
        if proposal:
            return proposal
        return open_ticket(db, failure, "IN_PROGRESS", analyst, prefs, actor, reason, rca=rca)
    if action == "APPROVAL":
        return request_approval(db, failure, hit, routes, prefs)
    earlier = fixes_in_run(db, failure, hit["issue_id"])
    if earlier >= MAX_FIXES_PER_RUN:   # the fix "works" but the task keeps failing: stop the fix-and-rerun loop
        return open_ticket(db, failure, "IN_PROGRESS", analyst, prefs, actor,
                           f"the known fix {hit.get('title')} already ran {earlier} times in this run and the task still "
                           "fails, so it does not repair this cause", prior={"issue_id": hit["issue_id"]})
    done = run_fix(db, failure, hit, prefs, actor, executor, "IN_PROGRESS")
    if done["action"] == "fix_failed":
        return open_ticket(db, failure, "IN_PROGRESS", analyst, prefs, actor,
                           f"the known fix {hit.get('title')} failed: {done['error']}", prior={"issue_id": hit["issue_id"]})
    return done


def fixes_in_run(db: Any, failure: Dict[str, Any], issue_id: str) -> int:
    """How many earlier tries of this task in this DAG run the same fix already 'repaired'."""
    found = db.query("""SELECT COUNT(*) AS N FROM CORE.FAILURE_EVENT
                         WHERE ENV_ID = %s AND DAG_ID = %s AND COALESCE(TASK_ID, '') = %s AND RUN_ID = %s
                           AND ISSUE_ID = %s AND STATUS = 'RESOLVED' AND FAILURE_ID <> %s""",
                     (failure.get("env_id"), failure["dag_id"], failure.get("task_id") or "", failure.get("run_id"),
                      issue_id, failure["failure_id"]))
    return int((found[0].get("n") if found else 0) or 0)


def claim_and_resolve(db: Any, holder: str, executor: Any, analyst: Any,
                      search: Optional[Callable[[Dict[str, Any]], List[Tuple[str, Optional[float]]]]] = None,
                      limit: Optional[int] = None) -> Dict[str, Any]:
    """CORE.SP_CLAIM_AND_RESOLVE: the guardrails are read first; with auto-resolution off nothing is claimed and
    failures stay PENDING. One failure that errors is put back to PENDING and the others go on."""
    prefs = load_preference(db)
    if not prefs["auto_resolve_enabled"]:
        return {"enabled": False, "claimed": 0, "actions": {}}
    release_stale_claims(db)
    scopes, routes = load_scopes(db), load_routes(db)
    claimed = claim(db, holder, min(int(limit or prefs["max_parallel_tasks"]), prefs["max_parallel_tasks"]))
    actions: Dict[str, int] = {}
    errors: List[str] = []
    for failure in claimed:
        try:
            result = resolve_one(db, failure, prefs, scopes, routes, executor, analyst, search)
            actions[result["action"]] = actions.get(result["action"], 0) + 1
        except Exception as exc:
            text = redact(f"{type(exc).__name__}: {exc}")[:300]
            errors.append(text)
            if retry_or_give_up(db, failure["failure_id"], text) == "TICKETED":
                actions["gave_up"] = actions.get("gave_up", 0) + 1
    out: Dict[str, Any] = {"enabled": True, "claimed": len(claimed), "actions": actions}
    if errors:
        out["errors"] = errors[:5]
    return out


def resume_approved(db: Any, executor: Any, analyst: Any, actor: str = SYSTEM, limit: int = 10) -> Dict[str, Any]:
    """CORE.SP_RESUME_APPROVED: run the fix of every APPROVED request whose failure still waits; close the failure of
    every REJECTED one. A request is taken with a compare-and-set (APPROVED -> DONE), so it runs once."""
    prefs = load_preference(db)
    counts = {"ran": 0, "rejected": 0, "failed": 0}
    for r in db.query(f"""SELECT A.APPROVAL_ID, A.FAILURE_ID, A.ISSUE_ID, A.DECIDED_BY FROM CORE.APPROVAL_REQUEST A
                            JOIN CORE.FAILURE_EVENT F ON F.FAILURE_ID = A.FAILURE_ID
                           WHERE A.STATUS = 'APPROVED' AND F.STATUS = 'AWAITING_APPROVAL'
                           ORDER BY A.DECIDED_AT LIMIT {int(limit)}"""):
        if db.execute_count("UPDATE CORE.APPROVAL_REQUEST SET STATUS = 'DONE' WHERE APPROVAL_ID = %s AND STATUS = 'APPROVED'",
                            (r["approval_id"],)) != 1:
            continue
        failure, issue = get_failure(db, r["failure_id"]), get_issue(db, r["issue_id"])
        if not failure:
            continue
        if not issue:   # the approval is used up: hand the failure to a person rather than leave it waiting forever
            move(db, failure["failure_id"], "AWAITING_APPROVAL", "TICKETED",
                 {"detail": f"the approved fix {r['issue_id']} no longer exists"})
            _jira(db, failure, "missing_fix", failure["failure_id"],
                  f"The approved fix {r['issue_id']} no longer exists, so it did not run. A person needs to look at this "
                  "failure.", raise_ticket=True)
            counts["failed"] += 1
            continue
        if str(issue.get("status")).upper() != "PUBLISHED":   # retired while it waited: never run a fix that is not live
            open_ticket(db, failure, "AWAITING_APPROVAL", analyst, prefs, actor,
                        f"the approved fix {issue.get('title')} was retired before it ran")
            counts["failed"] += 1
            continue
        done = run_fix(db, failure, issue, prefs, f"approved by {r.get('decided_by')}", executor, "AWAITING_APPROVAL")
        if done["action"] == "fix_failed":
            open_ticket(db, failure, "AWAITING_APPROVAL", analyst, prefs, actor,
                        f"the approved fix {issue.get('title')} failed: {done['error']}", prior={"issue_id": issue["issue_id"]})
            counts["failed"] += 1
        else:
            counts["ran"] += 1
    for r in db.query(f"""SELECT A.APPROVAL_ID, A.FAILURE_ID, A.ISSUE_ID, A.DECIDED_BY, A.DECISION_NOTE, A.KIND
                            FROM CORE.APPROVAL_REQUEST A JOIN CORE.FAILURE_EVENT F ON F.FAILURE_ID = A.FAILURE_ID
                           WHERE A.STATUS = 'REJECTED' AND F.STATUS = 'AWAITING_APPROVAL' LIMIT {int(limit)}"""):
        failure = get_failure(db, r["failure_id"])
        if failure and str(r.get("kind") or "RUN") == "PROPOSAL":
            # the person did not want the agent's fix: retire the draft, and a person takes the cause from the ticket
            db.execute("UPDATE CORE.KNOWN_ISSUE SET STATUS = 'RETIRED', UPDATED_AT = CURRENT_TIMESTAMP() "
                       "WHERE ISSUE_ID = %s AND STATUS = 'DRAFT'", (r["issue_id"],))
            open_ticket(db, failure, "AWAITING_APPROVAL", analyst, prefs, actor,
                        f"the agent's proposed fix was rejected by {r.get('decided_by')}: {r.get('decision_note') or ''}"[:500],
                        rca=failure.get("rca") if isinstance(failure.get("rca"), dict) else None)
            counts["rejected"] += 1
            continue
        if failure and move(db, r["failure_id"], "AWAITING_APPROVAL", "REJECTED",
                            {"detail": f"rejected by {r.get('decided_by')}: {r.get('decision_note') or ''}"[:4000]}):
            _audit(db, failure, None, "FAILED", {"reason": "rejected", "by": r.get("decided_by"),
                                                 "note": r.get("decision_note")}, actor)
            counts["rejected"] += 1
    return counts
