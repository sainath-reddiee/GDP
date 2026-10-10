"""Teams notifications for incidents: Adaptive Card payloads for a Teams Workflows webhook ("post to a channel when a
webhook request is received"), webhook URL validation, and the outbox that delivers cards and Jira bot actions with
retries.

Cards carry redacted text only and Action.OpenUrl buttons (webhook cards cannot submit, so Acknowledge opens the
platform). Webhook URLs are stored ENCRYPTed on OPS.TEAM and decrypted only here, at send time; they never appear in a
payload, an error, a log line or an API response.

Outbox (OPS.NOTIFICATION): rows are enqueued with a DEDUPE_KEY (the same key is never queued twice). The worker's
'outbox' job sends due rows (PENDING, or FAILED and due again); a failure is FAILED and retries after 1 min, 5 min, 15 min, then every hour, and the 6th failed attempt
marks the row DEAD. Per team, at most rate_limit_per_10min cards (opened, re-occurred, resolved) are sent in any 10
minutes; the rest are SUPPRESSED and folded into one storm summary card per team per 10 minute window.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from services.ops.redact import redact

TEAMS_HOST_SUFFIXES = (".webhook.office.com", ".logic.azure.com", ".powerautomate.com", ".environment.api.powerplatform.com")
BACKOFF_SECONDS = (60, 300, 900, 3600)
MAX_ATTEMPTS = 6
WINDOW_SECONDS = 600
DEFAULT_RATE_LIMIT = 10
RATE_LIMITED_KINDS = {"opened", "reoccurred", "resolved"}
CARD_KINDS = ("opened", "escalated", "reoccurred", "resolved", "storm_summary", "test")
EXCERPT_CHARS = 600
SEND_TIMEOUT = 10.0

Http = Callable[[str, Dict[str, Any]], Tuple[int, str]]


# ---------------------------------------------------------------- webhook URLs and keys

def validate_webhook_url(url: str) -> str:
    """The URL when it is an https Teams Workflows, Power Automate or Logic Apps webhook; ValueError otherwise."""
    value = (url or "").strip()
    if len(value) > 2048:
        raise ValueError("The webhook URL is too long.")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("The webhook URL must start with https:// and have no user name or password.")
    if parsed.port not in (None, 443):
        raise ValueError("The webhook URL must use the default https port.")
    if not any(host.endswith(suffix) for suffix in TEAMS_HOST_SUFFIXES):
        raise ValueError("Use a Teams Workflows (Power Automate or Logic Apps) webhook URL: its host ends with "
                         + ", ".join(s.lstrip(".") for s in TEAMS_HOST_SUFFIXES) + ".")
    return value


def secret_key() -> str:
    """The host key that encrypts webhook URLs (AIP_SECRET_KEY, else JIRA_TOKEN_KEY); empty when it is not set."""
    key = (os.environ.get("AIP_SECRET_KEY") or os.environ.get("JIRA_TOKEN_KEY") or "").strip()
    return key if len(key) >= 16 else ""


def webhook_column(kind: str) -> str:
    if kind == "alerts":
        return "TEAMS_WEBHOOK_SECRET"
    if kind == "escalation":
        return "ESCALATION_WEBHOOK_SECRET"
    raise ValueError("kind must be alerts or escalation")


def webhook_url(db: Any, team_id: str, kind: str, key: Optional[str] = None) -> Optional[str]:
    """The decrypted webhook of a team (escalation falls back to the alerts webhook); None when there is none."""
    key = key or secret_key()
    if not key:
        raise RuntimeError("No encryption key on this host: set AIP_SECRET_KEY (or JIRA_TOKEN_KEY).")
    found = db.query("""SELECT IFF(TEAMS_WEBHOOK_SECRET IS NULL, NULL, TO_VARCHAR(DECRYPT(TEAMS_WEBHOOK_SECRET, %s), 'UTF-8')) AS A,
                               IFF(ESCALATION_WEBHOOK_SECRET IS NULL, NULL,
                                   TO_VARCHAR(DECRYPT(ESCALATION_WEBHOOK_SECRET, %s), 'UTF-8')) AS E
                          FROM OPS.TEAM WHERE TEAM_ID = %s""", (key, key, team_id))
    if not found:
        return None
    if kind == "escalation":
        return found[0].get("e") or found[0].get("a")
    return found[0].get("a")


# ---------------------------------------------------------------- cards

def links(incident: Dict[str, Any], base: Optional[str], jira_url: Optional[str] = None,
          airflow_url: Optional[str] = None) -> Dict[str, Optional[str]]:
    root = (base or "").rstrip("/")
    iid = quote(str(incident.get("incident_id") or ""), safe="")
    return {"ack": f"{root}/incidents/{iid}?ack=1" if root else None, "open": f"{root}/incidents/{iid}" if root else None,
            "jira": jira_url or None, "airflow": airflow_url or None}


def _text(value: Any, limit: int = 400) -> str:
    return redact(str(value or ""))[:limit]


def _actions(link: Dict[str, Optional[str]], with_ack: bool = True) -> List[Dict[str, Any]]:
    out = []
    for key, title in (("ack", "Acknowledge"), ("open", "Open incident"), ("jira", "Open Jira"), ("airflow", "Open Airflow log")):
        if key == "ack" and not with_ack:
            continue
        url = link.get(key)
        if url and url.startswith(("https://", "http://")):
            out.append({"type": "Action.OpenUrl", "title": title, "url": url})
    return out


def _message(body: List[Dict[str, Any]], actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    content: Dict[str, Any] = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                               "version": "1.4", "body": body, "msteams": {"width": "Full"}}
    if actions:
        content["actions"] = actions
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
                                                "content": content}]}


HEADLINES = {"opened": "New incident", "escalated": "Escalated: not acknowledged", "reoccurred": "Incident re-occurred",
             "resolved": "Incident resolved"}
COLORS = {"opened": "Attention", "escalated": "Attention", "reoccurred": "Warning", "resolved": "Good"}


def card(kind: str, incident: Dict[str, Any], link: Dict[str, Optional[str]], team_name: Optional[str] = None,
         note: Optional[str] = None) -> Dict[str, Any]:
    """The Teams message for one incident event (opened, escalated, reoccurred, resolved)."""
    if kind not in HEADLINES:
        raise ValueError(f"unknown card kind {kind}")
    sev = str(incident.get("severity") or "")
    facts = [{"title": "Severity", "value": sev}, {"title": "Status", "value": str(incident.get("status") or "")},
             {"title": "Environment", "value": _text(incident.get("env_id"), 64)},
             {"title": "DAG", "value": _text(incident.get("dag_id"), 250)}]
    if incident.get("task_id"):
        facts.append({"title": "Task", "value": _text(incident.get("task_id"), 250)})
    if incident.get("run_id"):
        facts.append({"title": "Run", "value": _text(incident.get("run_id"), 250)})
    facts.append({"title": "Occurrences", "value": str(int(incident.get("occurrences") or 1))})
    if team_name:
        facts.append({"title": "Team", "value": _text(team_name, 120)})
    if incident.get("jira_key"):
        facts.append({"title": "Jira", "value": _text(incident.get("jira_key"), 40)})
    body: List[Dict[str, Any]] = [
        {"type": "TextBlock", "text": f"{HEADLINES[kind]} {sev}".strip(), "weight": "Bolder", "size": "Medium",
         "color": COLORS[kind], "wrap": True},
        {"type": "TextBlock", "text": _text(incident.get("title"), 300), "wrap": True},
        {"type": "FactSet", "facts": facts},
    ]
    if note:
        body.append({"type": "TextBlock", "text": _text(note, 600), "wrap": True})
    excerpt = _text(incident.get("error_excerpt"), 4000)
    if excerpt and kind != "resolved":
        body.append({"type": "TextBlock", "text": excerpt[-EXCERPT_CHARS:], "wrap": True, "fontType": "Monospace",
                     "isSubtle": True, "size": "Small"})
    if incident.get("ai_summary") and kind != "resolved":
        body.append({"type": "TextBlock", "text": "AI: " + _text(incident.get("ai_summary"), 500), "wrap": True})
    return _message(body, _actions(link, with_ack=kind in ("opened", "escalated", "reoccurred")))


def storm_card(team_name: Optional[str], count: int, titles: List[str], base: Optional[str]) -> Dict[str, Any]:
    root = (base or "").rstrip("/")
    body: List[Dict[str, Any]] = [
        {"type": "TextBlock", "text": f"Incident storm: {int(count)} more alerts in the last 10 minutes", "weight": "Bolder",
         "size": "Medium", "color": "Attention", "wrap": True},
        {"type": "TextBlock", "text": f"Cards for {_text(team_name or 'this team', 120)} are paused for the rest of the "
                                      "window. Open the inbox to see them all.", "wrap": True},
    ]
    if titles:
        body.append({"type": "TextBlock", "text": "\n".join("- " + _text(t, 200) for t in titles[:8]), "wrap": True})
    actions = [{"type": "Action.OpenUrl", "title": "Open incidents", "url": f"{root}/incidents"}] if root else []
    return _message(body, actions)


def test_card(team_name: Optional[str], kind: str, base: Optional[str]) -> Dict[str, Any]:
    root = (base or "").rstrip("/")
    body = [{"type": "TextBlock", "text": "Test message from the data platform", "weight": "Bolder", "size": "Medium", "wrap": True},
            {"type": "TextBlock", "text": f"The {kind} webhook of {_text(team_name or 'this team', 120)} works. "
                                          "Incident alerts will arrive here.", "wrap": True}]
    return _message(body, [{"type": "Action.OpenUrl", "title": "Open incidents", "url": f"{root}/incidents"}] if root else [])


# ---------------------------------------------------------------- delivery

def backoff_seconds(attempts: int) -> int:
    """Wait before the next try after `attempts` failed attempts: 1 min, 5 min, 15 min, then 1 hour."""
    return BACKOFF_SECONDS[max(0, min(int(attempts), len(BACKOFF_SECONDS)) - 1)]


def after_failure(attempts: int) -> Tuple[str, Optional[int]]:
    """(status, seconds until the next try) after `attempts` failed attempts in all: FAILED (retrying) or DEAD."""
    if attempts >= MAX_ATTEMPTS:
        return "DEAD", None
    return "FAILED", backoff_seconds(attempts)


def _httpx_post(url: str, payload: Dict[str, Any]) -> Tuple[int, str]:
    import httpx

    with httpx.Client(timeout=SEND_TIMEOUT, follow_redirects=False) as client:
        r = client.post(url, json=payload, headers={"Content-Type": "application/json"})
    return r.status_code, r.text[:500]


def send(url: str, payload: Dict[str, Any], http: Optional[Http] = None) -> Tuple[bool, str]:
    """POST the card. (ok, detail); the detail never contains the URL."""
    post = http or _httpx_post
    try:
        status, text = post(url, payload)
    except Exception as exc:
        return False, f"could not reach the webhook ({type(exc).__name__})"
    if 200 <= int(status) < 300:
        return True, f"HTTP {status}"
    detail = redact((text or "").replace(url, "[webhook]"))[:200]
    return False, f"HTTP {status}" + (f": {detail}" if detail else "")


def plan_rate_limit(rows: List[Dict[str, Any]], sent_in_window: Dict[str, int], limit: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split due Teams rows into (send, suppress): per team, rate-limited kinds beyond `limit` cards in the window are
    suppressed (folded into a storm summary). Escalations, storm summaries and tests are never limited."""
    used = dict(sent_in_window)
    send_rows, suppress = [], []
    for row in rows:
        team = row.get("team_id") or ""
        if str(row.get("kind")) in RATE_LIMITED_KINDS:
            if used.get(team, 0) >= max(1, int(limit)):
                suppress.append(row)
                continue
            used[team] = used.get(team, 0) + 1
        send_rows.append(row)
    return send_rows, suppress


def window_start(now: datetime) -> datetime:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % WINDOW_SECONDS, tz=timezone.utc)


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


def _storm_payload(db: Any, team_id: str, base: Optional[str]) -> Dict[str, Any]:
    found = db.query("""SELECT N.INCIDENT_ID, I.TITLE FROM OPS.NOTIFICATION N
                          LEFT JOIN OPS.INCIDENT I ON I.INCIDENT_ID = N.INCIDENT_ID
                         WHERE N.TEAM_ID = %s AND N.CHANNEL = 'TEAMS' AND N.STATUS = 'SUPPRESSED'
                           AND N.CREATED_AT >= DATEADD(minute, -20, CURRENT_TIMESTAMP())
                         ORDER BY N.CREATED_AT DESC LIMIT 200""", (team_id,))
    team = db.query("SELECT NAME FROM OPS.TEAM WHERE TEAM_ID = %s", (team_id,))
    titles = list(dict.fromkeys(str(r.get("title") or "") for r in found if r.get("title")))
    return storm_card(team[0]["name"] if team else team_id, max(len(found), 1), titles, base)


def run_outbox(db: Any, settings: Dict[str, Any], http: Optional[Http] = None,
               jira: Optional[Callable[[Any, Dict[str, Any]], Tuple[str, str]]] = None, now: Optional[datetime] = None,
               limit: int = 50) -> Dict[str, int]:
    """Deliver due rows once. `jira(db, row)` handles CHANNEL JIRA rows and returns (status, detail): SENT or SKIPPED;
    it raises to retry. Returns counts by outcome."""
    now = now or datetime.now(timezone.utc)
    rows = db.query(f"""SELECT NOTIFICATION_ID, INCIDENT_ID, TEAM_ID, CHANNEL, KIND, TARGET_SECRET, PAYLOAD, ATTEMPTS, DEDUPE_KEY
                          FROM OPS.NOTIFICATION WHERE STATUS IN ('PENDING', 'FAILED') AND NEXT_AT <= CURRENT_TIMESTAMP()
                         ORDER BY CREATED_AT LIMIT {int(limit)}""")
    counts = {"sent": 0, "failed": 0, "dead": 0, "suppressed": 0, "skipped": 0}
    teams_rows = [r for r in rows if str(r.get("channel")).upper() == "TEAMS"]
    team_ids = sorted({r["team_id"] for r in teams_rows if r.get("team_id")})
    sent_counts: Dict[str, int] = {}
    if team_ids:
        found = db.query(f"""SELECT TEAM_ID, COUNT(*) AS N FROM OPS.NOTIFICATION
                              WHERE CHANNEL = 'TEAMS' AND STATUS = 'SENT' AND KIND IN ('opened', 'reoccurred', 'resolved')
                                AND SENT_AT >= DATEADD(second, -{WINDOW_SECONDS}, CURRENT_TIMESTAMP())
                                AND TEAM_ID IN ({', '.join(['%s'] * len(team_ids))}) GROUP BY TEAM_ID""", tuple(team_ids))
        sent_counts = {r["team_id"]: int(r["n"] or 0) for r in found}
    limit_cards = int(settings.get("rate_limit_per_10min") or DEFAULT_RATE_LIMIT)
    to_send, suppressed = plan_rate_limit(teams_rows, sent_counts, limit_cards)
    start = window_start(now)
    for row in suppressed:
        _mark(db, row["notification_id"], "SUPPRESSED", "rate limit: folded into a storm summary", attempted=False)
        delay = max(0, int((start.timestamp() + WINDOW_SECONDS) - now.timestamp()))
        enqueue(db, "TEAMS", "storm_summary", f"storm:{row['team_id']}:{int(start.timestamp())}", {},
                team_id=row["team_id"], target="alerts", delay_seconds=delay)
        counts["suppressed"] += 1
    ordered = to_send + [r for r in rows if str(r.get("channel")).upper() != "TEAMS"]
    for row in ordered:
        attempts = int(row.get("attempts") or 0) + 1
        try:
            if str(row.get("channel")).upper() == "TEAMS":
                status, detail = _send_teams(db, row, settings, http)
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


def _send_teams(db: Any, row: Dict[str, Any], settings: Dict[str, Any], http: Optional[Http]) -> Tuple[str, str]:
    team_id = row.get("team_id")
    if not team_id:
        return "SKIPPED", "the incident has no team"
    url = webhook_url(db, team_id, row.get("target_secret") or "alerts")
    if not url:
        return "SKIPPED", "the team has no Teams webhook"
    payload = _payload(row.get("payload"))
    if row.get("kind") == "storm_summary":
        payload = _storm_payload(db, team_id, settings.get("public_base_url"))
    elif row.get("kind") in HEADLINES:
        payload = incident_card(db, row["incident_id"], row["kind"], payload.get("note"), settings)
        if payload is None:
            return "SKIPPED", "the incident no longer exists"
    ok, detail = send(url, payload, http)
    return ("SENT" if ok else "ERROR"), detail


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


def incident_card(db: Any, incident_id: str, kind: str, note: Optional[str], settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The card for an incident as it is now (status, Jira key and links read at send time)."""
    found = db.query("""SELECT I.INCIDENT_ID, I.ENV_ID, I.DAG_ID, I.TASK_ID, I.RUN_ID, I.STATUS, I.SEVERITY, I.TITLE,
                               I.ERROR_EXCERPT, I.OCCURRENCES, I.JIRA_KEY, I.AI_SUMMARY, T.NAME AS TEAM_NAME,
                               E.AIRFLOW_URL, E.API_VERSION
                          FROM OPS.INCIDENT I LEFT JOIN OPS.TEAM T ON T.TEAM_ID = I.TEAM_ID
                          LEFT JOIN OPS.AIRFLOW_ENV E ON E.ENV_ID = I.ENV_ID
                         WHERE I.INCIDENT_ID = %s""", (incident_id,))
    if not found:
        return None
    inc = found[0]
    site = jira_site(db) if inc.get("jira_key") else None
    link = links(inc, settings.get("public_base_url"),
                 f"{site}/browse/{inc['jira_key']}" if site and inc.get("jira_key") else None,
                 airflow_link(inc.get("airflow_url"), inc.get("api_version"), inc["dag_id"], inc.get("run_id")))
    return card(kind, inc, link, inc.get("team_name"), note)
