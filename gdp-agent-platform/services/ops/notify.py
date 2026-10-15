"""The incident outbox: Jira bot actions queued on OPS.NOTIFICATION and delivered by the worker with retries, plus the
links and keys the incident features share.

Outbox (OPS.NOTIFICATION): rows are enqueued with a DEDUPE_KEY (the same key is never queued twice). The worker's
'outbox' job sends due rows (PENDING, or FAILED and due again); each row is first claimed (SENDING, for 5 minutes; a
claim whose sender died is due again), so two workers never send it twice. A failure is FAILED and retries after 1 min,
5 min, 15 min, then every hour, and the 6th failed attempt marks the row DEAD.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote

from services.common import secretbox
from services.ops.redact import redact

BACKOFF_SECONDS = (60, 300, 900, 3600)
MAX_ATTEMPTS = 6


# ---------------------------------------------------------------- keys and links

def secret_key() -> str:
    """The host key that seals ops secrets (AIP_SECRET_KEY, else JIRA_TOKEN_KEY); empty when it is not set."""
    return secretbox.ops_key()


def links(incident: Dict[str, Any], base: Optional[str], jira_url: Optional[str] = None,
          airflow_url: Optional[str] = None) -> Dict[str, Optional[str]]:
    root = (base or "").rstrip("/")
    iid = quote(str(incident.get("incident_id") or ""), safe="")
    return {"ack": f"{root}/incidents/{iid}?ack=1" if root else None, "open": f"{root}/incidents/{iid}" if root else None,
            "jira": jira_url or None, "airflow": airflow_url or None}


# ---------------------------------------------------------------- delivery

def backoff_seconds(attempts: int) -> int:
    """Wait before the next try after `attempts` failed attempts: 1 min, 5 min, 15 min, then 1 hour."""
    return BACKOFF_SECONDS[max(0, min(int(attempts), len(BACKOFF_SECONDS)) - 1)]


def after_failure(attempts: int) -> Tuple[str, Optional[int]]:
    """(status, seconds until the next try) after `attempts` failed attempts in all: FAILED (retrying) or DEAD."""
    if attempts >= MAX_ATTEMPTS:
        return "DEAD", None
    return "FAILED", backoff_seconds(attempts)


def enqueue(db: Any, channel: str, kind: str, dedupe_key: str, payload: Optional[Dict[str, Any]] = None,
            incident_id: Optional[str] = None, team_id: Optional[str] = None, target: Optional[str] = None,
            status: str = "PENDING", delay_seconds: int = 0, error: Optional[str] = None) -> bool:
    """Queue one notification unless its dedupe key is already queued. True when a row was added."""
    count = db.execute_count("""
        MERGE INTO OPS.NOTIFICATION T USING (SELECT %s AS DEDUPE_KEY) S ON T.DEDUPE_KEY = S.DEDUPE_KEY
        WHEN NOT MATCHED THEN INSERT (NOTIFICATION_ID, INCIDENT_ID, TEAM_ID, CHANNEL, KIND, TARGET_SECRET, PAYLOAD, STATUS,
             ATTEMPTS, NEXT_AT, ERROR, DEDUPE_KEY)
        VALUES (%s, %s, %s, %s, %s, %s, PARSE_JSON(%s), %s, 0, DATEADD(second, %s, CURRENT_TIMESTAMP()), %s, S.DEDUPE_KEY)""",
                             (dedupe_key, str(uuid.uuid4()), incident_id, team_id, channel, kind, target,
                              json.dumps(payload or {}, default=str), status, int(delay_seconds),
                              (error or None) and redact(error)[:2000]))
    return count >= 1


def _payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _mark(db: Any, notification_id: str, status: str, error: Optional[str] = None, delay: Optional[int] = None,
          attempted: bool = True) -> None:
    db.execute(f"""UPDATE OPS.NOTIFICATION SET STATUS = %s, ERROR = %s,
                          ATTEMPTS = ATTEMPTS + {1 if attempted else 0},
                          SENT_AT = IFF(%s = 'SENT', CURRENT_TIMESTAMP(), SENT_AT),
                          NEXT_AT = IFF(%s IS NULL, NEXT_AT, DATEADD(second, %s, CURRENT_TIMESTAMP()))
                    WHERE NOTIFICATION_ID = %s""",
               (status, (redact(error)[:2000] if error else None), status, delay, int(delay or 0), notification_id))


CLAIM_MINUTES = 5


def claim(db: Any, notification_id: str) -> bool:
    """Take one due row for sending (compare-and-set): it becomes SENDING with NEXT_AT CLAIM_MINUTES ahead, so another
    worker reading the same due row cannot claim it too. A SENDING row whose claim ran out (its sender died) is due
    again. True when this caller got it."""
    return db.execute_count(f"""UPDATE OPS.NOTIFICATION SET STATUS = 'SENDING',
                                       NEXT_AT = DATEADD(minute, {CLAIM_MINUTES}, CURRENT_TIMESTAMP())
                                 WHERE NOTIFICATION_ID = %s AND STATUS IN ('PENDING', 'FAILED', 'SENDING')
                                   AND NEXT_AT <= CURRENT_TIMESTAMP()""", (notification_id,)) == 1


def run_outbox(db: Any, settings: Dict[str, Any],
               jira: Optional[Callable[[Any, Dict[str, Any]], Tuple[str, str]]] = None, now: Optional[datetime] = None,
               limit: int = 50, keep_going: Optional[Callable[[], bool]] = None) -> Dict[str, int]:
    """Deliver due rows once. `jira(db, row)` handles CHANNEL JIRA rows and returns (status, detail): SENT or SKIPPED;
    it raises to retry. Rows on any other channel (left from a retired one) are SKIPPED. Each row is claimed before it
    is sent, so two workers never send it twice. `keep_going` (the job lease by default) is asked before every row:
    when the lease is lost the run stops. Returns counts by outcome."""
    from services.ops import lease

    keep_going = keep_going or lease.still_held
    rows = db.query(f"""SELECT NOTIFICATION_ID, INCIDENT_ID, TEAM_ID, CHANNEL, KIND, TARGET_SECRET, PAYLOAD, ATTEMPTS, DEDUPE_KEY
                          FROM OPS.NOTIFICATION WHERE STATUS IN ('PENDING', 'FAILED', 'SENDING') AND NEXT_AT <= CURRENT_TIMESTAMP()
                         ORDER BY CREATED_AT LIMIT {int(limit)}""")
    counts = {"sent": 0, "failed": 0, "dead": 0, "skipped": 0, "busy": 0}
    for row in rows:
        if not keep_going():
            break   # the job lease was lost: another worker owns the outbox now
        if not claim(db, row["notification_id"]):
            counts["busy"] += 1   # another worker is sending it
            continue
        attempts = int(row.get("attempts") or 0) + 1
        try:
            if str(row.get("channel")).upper() != "JIRA":
                status, detail = "SKIPPED", "channel no longer delivered"
            elif jira is not None:
                status, detail = jira(db, row)
            else:
                status, detail = "SKIPPED", "no Jira handler"
        except Exception as exc:
            status, detail = "ERROR", redact(f"{type(exc).__name__}: {exc}")[:500]
        if status in ("SENT", "SKIPPED"):
            _mark(db, row["notification_id"], status, None if status == "SENT" else detail)
            counts["sent" if status == "SENT" else "skipped"] += 1
            continue
        next_status, delay = after_failure(attempts)
        _mark(db, row["notification_id"], next_status, detail, delay)
        counts["dead" if next_status == "DEAD" else "failed"] += 1
    return counts


def airflow_link(base: Optional[str], api_version: Optional[str], dag_id: str, run_id: Optional[str] = None) -> Optional[str]:
    """Deep link into the Airflow UI (Airflow 3: /dags/<id>/runs/<run>; Airflow 2: the grid view)."""
    if not base or not dag_id:
        return None
    root = base.rstrip("/")
    dag = quote(dag_id, safe="")
    if api_version == "v2":
        return f"{root}/dags/{dag}" + (f"/runs/{quote(run_id, safe='')}" if run_id else "")
    return f"{root}/dags/{dag}/grid" + (f"?dag_run_id={quote(run_id, safe='')}" if run_id else "")


def jira_site(db: Any) -> Optional[str]:
    try:
        found = db.query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'JIRA' AND IS_CURRENT "
                         "ORDER BY VERSION DESC LIMIT 1")
    except Exception:
        return None
    value = _payload(found[0].get("config_value")) if found else {}
    return (str(value.get("site_url") or "").rstrip("/")) or None
