"""Schedule-aware polling: with push on, the worker polls an environment at a slow safety interval and polls at its
normal interval only around the times its DAGs run.

Push (the listener plugin, airflow_plugins/gdp_listener) brings each failure in seconds, so polling is only a safety
net for events the plugin could not send. Polling every minute all day long would load the Airflow API and the
warehouse for nothing when DAGs run once a day at a set time. An environment counts as pushing only when push is on
AND a pushed event arrived in the last PUSH_LIVE_HOURS (a plugin that was never installed, or stopped sending, falls
back to normal polling by itself).

A run window opens LEAD_MINUTES before a DAG's next cron fire time and stays open for its maximum duration (else its
slow successful runs, else DEFAULT_RUN_MINUTES) after it fires. A DAG with a completion SLA (EXPECTED_BY_CRON) also has
a window ending LEAD_MINUTES after it. Dataset-triggered and manual DAGs have no window: push and the safety poll
cover them.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

from services.ops.detect import next_fire, parse_cron, prev_fire

LEAD_MINUTES = 15
DEFAULT_RUN_MINUTES = 60
SAFETY_POLL_SECONDS = 1800
PUSH_LIVE_HOURS = 36
MIN_POLL_SECONDS = 60


def cron_of(value: Any) -> Optional[str]:
    """The value when it is a cron expression (or @daily and the like), else None ('None', 'Dataset', timetables)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parse_cron(text)
    except (ValueError, KeyError):
        return None
    return text


def run_minutes(dag: Dict[str, Any]) -> int:
    if dag.get("max_duration_min"):
        return max(1, int(dag["max_duration_min"]))
    try:
        p95 = float(dag.get("p95_s"))
        return max(1, math.ceil(p95 / 60))
    except (TypeError, ValueError):
        return DEFAULT_RUN_MINUTES


def in_run_window(dags: Iterable[Dict[str, Any]], now: datetime, lead: int = LEAD_MINUTES) -> bool:
    """True when any active, unpaused DAG is about to run, is running by its schedule, or is near its SLA."""
    lead_t = timedelta(minutes=lead)
    for dag in dags:
        if dag.get("is_paused") or dag.get("is_active") is False:
            continue
        tz = dag.get("timezone")
        length = timedelta(minutes=run_minutes(dag))
        for expr, before, after in ((cron_of(dag.get("schedule")), lead_t, length + lead_t),
                                    (cron_of(dag.get("expected_by_cron")), length + lead_t, lead_t)):
            if not expr:
                continue
            try:
                upcoming, last = next_fire(expr, now, tz), prev_fire(expr, now, tz)
            except (ValueError, KeyError):
                continue
            if upcoming and upcoming - now <= before:
                return True
            if last and now - last <= after:
                return True
    return False


def effective_interval(poll_seconds: Any, push_live: bool, in_window: bool, safety: int = SAFETY_POLL_SECONDS) -> int:
    """Seconds between polls: the environment's own interval without live push or inside a run window, else the
    safety interval (never shorter than the environment's own)."""
    poll = max(MIN_POLL_SECONDS, int(poll_seconds or 300))
    if not push_live or in_window:
        return poll
    return max(poll, int(safety))


def env_state(db: Any, env_id: str, now: datetime) -> Dict[str, bool]:
    """{push_live, in_window} of one environment, from its settings, its recent pushed events and its DAGs."""
    found = db.query("""SELECT E.PUSH_ENABLED,
                               (SELECT MAX(V.RECEIVED_AT) FROM CORE.EVENT V WHERE V.ENV_ID = E.ENV_ID AND V.SOURCE = 'PUSH'
                                   AND V.RECEIVED_AT >= DATEADD(hour, %s, CURRENT_TIMESTAMP())) AS LAST_PUSH_AT
                          FROM CORE.AIRFLOW_ENV E WHERE E.ENV_ID = %s""", (-PUSH_LIVE_HOURS, env_id))
    row = found[0] if found else {}
    push_live = bool(row.get("push_enabled")) and row.get("last_push_at") is not None
    if not push_live:
        return {"push_live": False, "in_window": True}
    dags = db.query("""
        SELECT D.DAG_ID, D.SCHEDULE, D.EXPECTED_BY_CRON, D.MAX_DURATION_MIN, D.TIMEZONE, D.IS_PAUSED, D.IS_ACTIVE, S.P95_S
          FROM CORE.DAG D
          LEFT JOIN (SELECT DAG_ID, PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY DURATION_S) AS P95_S FROM CORE.DAG_RUN
                      WHERE ENV_ID = %s AND STATE = 'success'
                        AND COALESCE(STARTED_AT, LOGICAL_DATE) >= DATEADD(day, -30, CURRENT_TIMESTAMP())
                      GROUP BY DAG_ID) S ON S.DAG_ID = D.DAG_ID
         WHERE D.ENV_ID = %s AND COALESCE(D.IS_ACTIVE, TRUE) AND NOT COALESCE(D.IS_PAUSED, FALSE)""", (env_id, env_id))
    return {"push_live": True, "in_window": in_run_window(dags, now)}
