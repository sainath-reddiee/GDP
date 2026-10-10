"""OPS tables: MERGE upserts of DAGs, runs and task runs, the event log, and one incremental poll of an environment.

Keys: DAG (ENV_ID, DAG_ID), DAG_RUN (ENV_ID, DAG_ID, RUN_ID), TASK_RUN (ENV_ID, DAG_ID, RUN_ID, TASK_ID, MAP_INDEX,
TRY_NUMBER). Push and poll write the same keys; a write replaces the stored state only when its UPDATED_AT is newer
(a finished state wins a tie), so duplicate and out-of-order events are harmless.

`db` is the API's Db (or anything with query/execute/execute_count taking %s parameters).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.ops.mwaa import Mwaa, MwaaError, supports_updated_at
from services.ops.normalize import ACTIVE, dag_row, dag_run_row, excerpt, newer, task_row, ts
from services.ops.redact import redact

OVERLAP_MINUTES = 15
FIRST_LOOKBACK_HOURS = 24
MAX_RUN_PAGES = 20
MAX_TASK_FETCH = 300
MAX_ERROR_LOGS = 10
ERROR_LOG_BYTES = 64 * 1024
BATCH = 500

RUN_KEYS = ("env_id", "dag_id", "run_id")
TASK_KEYS = ("env_id", "dag_id", "run_id", "task_id", "map_index", "try_number")
_TERMINAL_SQL = "('success', 'failed', 'upstream_failed', 'skipped', 'removed')"
_ACTIVE_SQL = "('running', 'queued', 'scheduled', 'up_for_retry', 'up_for_reschedule', 'deferred', 'restarting')"


def _rank(alias: str) -> str:
    return f"IFF({alias}.STATE IN {_TERMINAL_SQL}, 2, IFF({alias}.STATE IN {_ACTIVE_SQL}, 1, 0))"


# the MERGE twin of normalize.newer
NEWER_SQL = (f"(T.UPDATED_AT IS NULL OR (S.UPDATED_AT IS NOT NULL AND (S.UPDATED_AT > T.UPDATED_AT "
             f"OR (S.UPDATED_AT = T.UPDATED_AT AND {_rank('S')} >= {_rank('T')}))))")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def poll_since(cursor: Optional[str], now: str, overlap_minutes: int = OVERLAP_MINUTES,
               first_hours: int = FIRST_LOOKBACK_HOURS) -> str:
    """Start of the next poll window: the stored cursor minus the overlap (re-reading the boundary covers clock skew
    and runs that changed while the last poll ran), or `first_hours` back on the first poll. Never in the future."""
    current = datetime.fromisoformat(ts(now))
    start = ts(cursor)
    if start:
        since = datetime.fromisoformat(start) - timedelta(minutes=overlap_minutes)
    else:
        since = current - timedelta(hours=first_hours)
    return min(since, current).isoformat(timespec="microseconds")


def dedupe(rows: Iterable[Dict[str, Any]], keys: Tuple[str, ...]) -> List[Dict[str, Any]]:
    """One row per key, the newest one (a MERGE source must not match a target row twice)."""
    best: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(k) for k in keys)
        if newer(best.get(key), row):
            best[key] = row
    return list(best.values())


def _chunks(rows: List[Dict[str, Any]], size: int = BATCH):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


_SRC = "TABLE(FLATTEN(INPUT => PARSE_JSON(%s))) F"


def upsert_dags(db: Any, rows: List[Dict[str, Any]]) -> int:
    """Airflow's own DAG fields only; the platform's settings (team, criticality, mapping) are never touched."""
    rows = list({(r["env_id"], r["dag_id"]): r for r in rows}.values())
    for chunk in _chunks(rows):
        db.execute(f"""
            MERGE INTO OPS.DAG T USING (
                SELECT F.VALUE:env_id::VARCHAR AS ENV_ID, F.VALUE:dag_id::VARCHAR AS DAG_ID,
                       F.VALUE:fileloc::VARCHAR AS FILELOC, F.VALUE:owners::ARRAY AS OWNERS, F.VALUE:tags::ARRAY AS TAGS,
                       F.VALUE:schedule::VARCHAR AS SCHEDULE, F.VALUE:is_paused::BOOLEAN AS IS_PAUSED,
                       F.VALUE:is_active::BOOLEAN AS IS_ACTIVE, F.VALUE:description::VARCHAR AS DESCRIPTION
                  FROM {_SRC}) S
            ON T.ENV_ID = S.ENV_ID AND T.DAG_ID = S.DAG_ID
            WHEN MATCHED THEN UPDATE SET FILELOC = S.FILELOC, OWNERS = S.OWNERS, TAGS = S.TAGS, SCHEDULE = S.SCHEDULE,
                 IS_PAUSED = S.IS_PAUSED, IS_ACTIVE = S.IS_ACTIVE, DESCRIPTION = S.DESCRIPTION,
                 LAST_SEEN_AT = CURRENT_TIMESTAMP(), UPDATED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (ENV_ID, DAG_ID, FILELOC, OWNERS, TAGS, SCHEDULE, IS_PAUSED, IS_ACTIVE, DESCRIPTION,
                 LAST_SEEN_AT) VALUES (S.ENV_ID, S.DAG_ID, S.FILELOC, S.OWNERS, S.TAGS, S.SCHEDULE, S.IS_PAUSED, S.IS_ACTIVE,
                 S.DESCRIPTION, CURRENT_TIMESTAMP())""", (json.dumps(chunk, default=str),))
    return len(rows)


def ensure_dags(db: Any, env_id: str, dag_ids: Iterable[str]) -> None:
    """A placeholder DAG row for runs pushed before the poller has listed the DAG."""
    ids = sorted({d for d in dag_ids if d})
    if not ids:
        return
    db.execute("""
        MERGE INTO OPS.DAG T USING (SELECT %s AS ENV_ID, F.VALUE::VARCHAR AS DAG_ID FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))) F) S
        ON T.ENV_ID = S.ENV_ID AND T.DAG_ID = S.DAG_ID
        WHEN NOT MATCHED THEN INSERT (ENV_ID, DAG_ID, IS_ACTIVE, LAST_SEEN_AT) VALUES (S.ENV_ID, S.DAG_ID, TRUE, CURRENT_TIMESTAMP())""",
               (env_id, json.dumps(ids)))


def mark_missing_inactive(db: Any, env_id: str, seen: List[str]) -> None:
    """DAGs no longer listed by Airflow (deleted files) are kept with their history but marked inactive."""
    db.execute("UPDATE OPS.DAG SET IS_ACTIVE = FALSE, UPDATED_AT = CURRENT_TIMESTAMP() "
               "WHERE ENV_ID = %s AND COALESCE(IS_ACTIVE, TRUE) AND NOT ARRAY_CONTAINS(DAG_ID::VARIANT, PARSE_JSON(%s))",
               (env_id, json.dumps(sorted(seen))))


def upsert_runs(db: Any, rows: List[Dict[str, Any]]) -> int:
    rows = dedupe(rows, RUN_KEYS)
    for chunk in _chunks(rows):
        db.execute(f"""
            MERGE INTO OPS.DAG_RUN T USING (
                SELECT F.VALUE:env_id::VARCHAR AS ENV_ID, F.VALUE:dag_id::VARCHAR AS DAG_ID, F.VALUE:run_id::VARCHAR AS RUN_ID,
                       F.VALUE:run_type::VARCHAR AS RUN_TYPE, F.VALUE:state::VARCHAR AS STATE,
                       TRY_TO_TIMESTAMP_LTZ(F.VALUE:logical_date::VARCHAR) AS LOGICAL_DATE,
                       TRY_TO_TIMESTAMP_LTZ(F.VALUE:started_at::VARCHAR) AS STARTED_AT,
                       TRY_TO_TIMESTAMP_LTZ(F.VALUE:ended_at::VARCHAR) AS ENDED_AT,
                       F.VALUE:duration_s::NUMBER(12,3) AS DURATION_S, F.VALUE:external_trigger::BOOLEAN AS EXTERNAL_TRIGGER,
                       F.VALUE:note::VARCHAR AS NOTE, F.VALUE:source::VARCHAR AS SOURCE,
                       TRY_TO_TIMESTAMP_LTZ(F.VALUE:updated_at::VARCHAR) AS UPDATED_AT
                  FROM {_SRC}) S
            ON T.ENV_ID = S.ENV_ID AND T.DAG_ID = S.DAG_ID AND T.RUN_ID = S.RUN_ID
            WHEN MATCHED AND {NEWER_SQL} THEN UPDATE SET RUN_TYPE = COALESCE(S.RUN_TYPE, T.RUN_TYPE), STATE = S.STATE,
                 LOGICAL_DATE = COALESCE(S.LOGICAL_DATE, T.LOGICAL_DATE), STARTED_AT = COALESCE(S.STARTED_AT, T.STARTED_AT),
                 ENDED_AT = S.ENDED_AT, DURATION_S = S.DURATION_S,
                 EXTERNAL_TRIGGER = COALESCE(S.EXTERNAL_TRIGGER, T.EXTERNAL_TRIGGER), NOTE = COALESCE(S.NOTE, T.NOTE),
                 SOURCE = S.SOURCE, UPDATED_AT = S.UPDATED_AT, LOADED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (ENV_ID, DAG_ID, RUN_ID, RUN_TYPE, STATE, LOGICAL_DATE, STARTED_AT, ENDED_AT, DURATION_S,
                 EXTERNAL_TRIGGER, NOTE, SOURCE, UPDATED_AT)
                 VALUES (S.ENV_ID, S.DAG_ID, S.RUN_ID, S.RUN_TYPE, S.STATE, S.LOGICAL_DATE, S.STARTED_AT, S.ENDED_AT, S.DURATION_S,
                 S.EXTERNAL_TRIGGER, S.NOTE, S.SOURCE, S.UPDATED_AT)""", (json.dumps(chunk, default=str),))
    return len(rows)


def upsert_tasks(db: Any, rows: List[Dict[str, Any]]) -> int:
    rows = dedupe(rows, TASK_KEYS)
    for chunk in _chunks(rows):
        db.execute(f"""
            MERGE INTO OPS.TASK_RUN T USING (
                SELECT F.VALUE:env_id::VARCHAR AS ENV_ID, F.VALUE:dag_id::VARCHAR AS DAG_ID, F.VALUE:run_id::VARCHAR AS RUN_ID,
                       F.VALUE:task_id::VARCHAR AS TASK_ID, F.VALUE:map_index::NUMBER AS MAP_INDEX,
                       F.VALUE:try_number::NUMBER AS TRY_NUMBER, F.VALUE:state::VARCHAR AS STATE,
                       F.VALUE:operator::VARCHAR AS OPERATOR,
                       TRY_TO_TIMESTAMP_LTZ(F.VALUE:started_at::VARCHAR) AS STARTED_AT,
                       TRY_TO_TIMESTAMP_LTZ(F.VALUE:ended_at::VARCHAR) AS ENDED_AT,
                       F.VALUE:duration_s::NUMBER(12,3) AS DURATION_S, F.VALUE:hostname::VARCHAR AS HOSTNAME,
                       F.VALUE:log_ref::VARCHAR AS LOG_REF, F.VALUE:error_excerpt::VARCHAR AS ERROR_EXCERPT,
                       F.VALUE:source::VARCHAR AS SOURCE, TRY_TO_TIMESTAMP_LTZ(F.VALUE:updated_at::VARCHAR) AS UPDATED_AT
                  FROM {_SRC}) S
            ON T.ENV_ID = S.ENV_ID AND T.DAG_ID = S.DAG_ID AND T.RUN_ID = S.RUN_ID AND T.TASK_ID = S.TASK_ID
               AND T.MAP_INDEX = S.MAP_INDEX AND T.TRY_NUMBER = S.TRY_NUMBER
            WHEN MATCHED AND {NEWER_SQL} THEN UPDATE SET STATE = S.STATE, OPERATOR = COALESCE(S.OPERATOR, T.OPERATOR),
                 STARTED_AT = COALESCE(S.STARTED_AT, T.STARTED_AT), ENDED_AT = S.ENDED_AT, DURATION_S = S.DURATION_S,
                 HOSTNAME = COALESCE(S.HOSTNAME, T.HOSTNAME), LOG_REF = COALESCE(S.LOG_REF, T.LOG_REF),
                 ERROR_EXCERPT = COALESCE(S.ERROR_EXCERPT, T.ERROR_EXCERPT), SOURCE = S.SOURCE, UPDATED_AT = S.UPDATED_AT,
                 LOADED_AT = CURRENT_TIMESTAMP()
            WHEN MATCHED AND T.ERROR_EXCERPT IS NULL AND S.ERROR_EXCERPT IS NOT NULL THEN UPDATE SET ERROR_EXCERPT = S.ERROR_EXCERPT,
                 LOADED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (ENV_ID, DAG_ID, RUN_ID, TASK_ID, MAP_INDEX, TRY_NUMBER, STATE, OPERATOR, STARTED_AT,
                 ENDED_AT, DURATION_S, HOSTNAME, LOG_REF, ERROR_EXCERPT, SOURCE, UPDATED_AT)
                 VALUES (S.ENV_ID, S.DAG_ID, S.RUN_ID, S.TASK_ID, S.MAP_INDEX, S.TRY_NUMBER, S.STATE, S.OPERATOR, S.STARTED_AT,
                 S.ENDED_AT, S.DURATION_S, S.HOSTNAME, S.LOG_REF, S.ERROR_EXCERPT, S.SOURCE, S.UPDATED_AT)""",
                   (json.dumps(chunk, default=str),))
    return len(rows)


def record_event(db: Any, event_id: str, env_id: Optional[str], source: str, kind: Optional[str], payload: Any) -> bool:
    """Insert the event once. False when the id is already there (a replay): the MERGE is the compare-and-set."""
    count = db.execute_count("""
        MERGE INTO OPS.EVENT T USING (SELECT %s AS EVENT_ID) S ON T.EVENT_ID = S.EVENT_ID
        WHEN NOT MATCHED THEN INSERT (EVENT_ID, ENV_ID, SOURCE, KIND, PAYLOAD) VALUES (S.EVENT_ID, %s, %s, %s, PARSE_JSON(%s))""",
                             (event_id, env_id, source, kind, json.dumps(payload, default=str)))
    return count == 1


def mark_event(db: Any, event_id: str, error: Optional[str] = None) -> None:
    db.execute("UPDATE OPS.EVENT SET PROCESSED_AT = CURRENT_TIMESTAMP(), ERROR = %s WHERE EVENT_ID = %s",
               ((redact(error)[:2000] if error else None), event_id))


def purge_events(db: Any, days: int = 30) -> int:
    return db.execute_count("DELETE FROM OPS.EVENT WHERE RECEIVED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP())", (-abs(int(days)),))


# ---------------------------------------------------------------- polling

def _stored_runs(db: Any, env_id: str, keys: List[Tuple[str, str]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    if not keys:
        return {}
    found = db.query("""
        SELECT R.DAG_ID, R.RUN_ID, R.STATE, TO_VARCHAR(R.UPDATED_AT, 'YYYY-MM-DD"T"HH24:MI:SS.FF6TZH:TZM') AS UPDATED_AT
          FROM OPS.DAG_RUN R
          JOIN TABLE(FLATTEN(INPUT => PARSE_JSON(%s))) F ON R.DAG_ID = F.VALUE[0]::VARCHAR AND R.RUN_ID = F.VALUE[1]::VARCHAR
         WHERE R.ENV_ID = %s""", (json.dumps([list(k) for k in keys]), env_id))
    return {(r["dag_id"], r["run_id"]): r for r in found}


def _fetch_tasks(mw: Mwaa, env_id: str, runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for run in runs:
        for item in mw.task_instances(run["dag_id"], run["run_id"]):
            out.append(task_row(env_id, item))
    return out


def fill_error_excerpts(db: Any, mw: Mwaa, env_id: str, limit: int = MAX_ERROR_LOGS) -> int:
    """Failed tasks of the last day without an error excerpt: read the end of their log and store the redacted error."""
    found = db.query("""
        SELECT DAG_ID, RUN_ID, TASK_ID, MAP_INDEX, TRY_NUMBER FROM OPS.TASK_RUN
         WHERE ENV_ID = %s AND STATE IN ('failed', 'up_for_retry') AND ERROR_EXCERPT IS NULL
           AND COALESCE(ENDED_AT, UPDATED_AT) >= DATEADD(hour, -24, CURRENT_TIMESTAMP())
         ORDER BY COALESCE(ENDED_AT, UPDATED_AT) DESC LIMIT """ + str(int(limit)), (env_id,))
    filled = 0
    for t in found:
        try:
            log = mw.task_log(t["dag_id"], t["run_id"], t["task_id"], int(t["try_number"] or 0), int(t["map_index"]),
                              cap=ERROR_LOG_BYTES)
        except MwaaError:
            continue
        text = excerpt(log.get("text")) or "(the log has no error lines)"
        db.execute("""UPDATE OPS.TASK_RUN SET ERROR_EXCERPT = %s, LOADED_AT = CURRENT_TIMESTAMP() WHERE ENV_ID = %s AND DAG_ID = %s AND RUN_ID = %s
                         AND TASK_ID = %s AND MAP_INDEX = %s AND TRY_NUMBER = %s AND ERROR_EXCERPT IS NULL""",
                   (text, env_id, t["dag_id"], t["run_id"], t["task_id"], t["map_index"], t["try_number"]))
        filled += 1
    return filled


def poll_env(db: Any, env: Dict[str, Any], client: Optional[Mwaa] = None, now: Optional[str] = None,
             max_task_fetch: int = MAX_TASK_FETCH) -> Dict[str, Any]:
    """One incremental poll: DAGs, runs changed since the cursor (minus the overlap), task instances of the runs that
    changed, error excerpts of new failures. Stores the cursor and clears LAST_ERROR. MwaaError propagates."""
    env_id = env["env_id"]
    started = ts(now) or utcnow()
    mw = client or Mwaa(env["mwaa_env"], env["region"])
    info = mw.version()

    dags, complete = mw.all_dags()
    upsert_dags(db, [dag_row(env_id, d) for d in dags])
    if complete and dags:
        mark_missing_inactive(db, env_id, [str(d["dag_id"]) for d in dags])

    since = poll_since(env.get("cursor_value"), started)
    fetched: List[Dict[str, Any]] = []
    total = 0
    for page in range(MAX_RUN_PAGES):
        runs, total = mw.dag_runs("~", since, offset=page * 100)
        fetched.extend(runs)
        if not runs or len(fetched) >= total:
            break
    paged_all = len(fetched) >= total
    rows = dedupe([dag_run_row(env_id, r) for r in fetched], RUN_KEYS)

    # start_date filtering cannot see a run that started before the window and finished inside it: re-read the runs
    # still stored as active
    if not supports_updated_at(info["version"]):
        have = {(r["dag_id"], r["run_id"]) for r in rows}
        stale = db.query(f"""SELECT DAG_ID, RUN_ID FROM OPS.DAG_RUN WHERE ENV_ID = %s AND STATE IN {_ACTIVE_SQL}
                             ORDER BY STARTED_AT LIMIT 100""", (env_id,))
        for r in stale:
            if (r["dag_id"], r["run_id"]) not in have:
                try:
                    rows.append(dag_run_row(env_id, mw.dag_run(r["dag_id"], r["run_id"])))
                except MwaaError as exc:
                    if exc.kind != "not_found":
                        raise

    rows.sort(key=lambda r: r.get("updated_at") or "")
    stored = _stored_runs(db, env_id, [(r["dag_id"], r["run_id"]) for r in rows])
    # task instances only for runs that are new, changed or still active
    changed = [r for r in rows if newer(stored.get((r["dag_id"], r["run_id"])), r) and
               (stored.get((r["dag_id"], r["run_id"])) is None
                or stored[(r["dag_id"], r["run_id"])].get("state") != r.get("state")
                or (r.get("state") or "") in ACTIVE)]
    todo, later = changed[:max_task_fetch], changed[max_task_fetch:]
    tasks = _fetch_tasks(mw, env_id, todo)
    if later:  # store only the runs whose tasks were read; the cursor stops before the first one left over
        done = {(r["dag_id"], r["run_id"]) for r in later}
        rows = [r for r in rows if (r["dag_id"], r["run_id"]) not in done]
    if rows:
        ensure_dags(db, env_id, [r["dag_id"] for r in rows])
    upsert_runs(db, rows)
    upsert_tasks(db, tasks)
    excerpts = fill_error_excerpts(db, mw, env_id)

    if later:
        cursor = later[0].get("updated_at") or since
    elif paged_all:
        cursor = started
    else:
        cursor = env.get("cursor_value") or since
    db.execute("""UPDATE OPS.AIRFLOW_ENV SET API_VERSION = %s, AIRFLOW_VERSION = %s, CURSOR_VALUE = %s,
                         LAST_POLL_AT = CURRENT_TIMESTAMP(), LAST_ERROR = NULL WHERE ENV_ID = %s""",
               (info["api_version"], info["version"][:32], cursor, env_id))
    summary = {"env_id": env_id, "version": info["version"], "api_version": info["api_version"], "dags": len(dags),
               "runs": len(rows), "tasks": len(tasks), "error_excerpts": excerpts, "since": since, "cursor": cursor,
               "complete": paged_all and not later}
    try:
        record_event(db, f"poll:{env_id}:{started}", env_id, "POLL", "poll", summary)
    except Exception:
        pass
    return summary
