"""Datadog connector: pipeline logs for incidents and AI diagnosis, and monitors that open incidents.

The keys come only from the host's environment (docker/api.env or the OS), never from the database, a request or a
file the platform writes:
  DATADOG_SITE      the Datadog site, e.g. datadoghq.com, us3.datadoghq.com, datadoghq.eu (default datadoghq.com)
  DATADOG_API_KEY   an API key (identifies the organisation)
  DATADOG_APP_KEY   an application key with logs_read_data and monitors_read (a scoped key is best)
  DATADOG_APP_URL   optional, the web address people open (a custom subdomain); links only, it never gets a key
Only Datadog's own hosts (api.<site>) receive the keys: an unknown site is refused, so a changed setting can never
send them elsewhere. Answers are redacted before they leave this module; error messages never hold a key.

What the platform does with it (the DATADOG platform setting, Admin, Integrations, Datadog):
  logs      an incident's Datadog logs for the failing DAG and task, on the incident page and, when the Airflow log
            cannot be read, in the AI diagnosis (services.ops.context.log_part);
  monitors  monitors in Alert (and No Data, Warn when chosen) open incidents in the ops inbox and resolve them when the
            monitor recovers (services.ops.datadog_monitors).
Throttling (429) and 5xx answers are retried with backoff, at most 3 tries.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote, urlencode

from services.ops.redact import redact

ENV_KEYS = ("DATADOG_SITE", "DATADOG_API_KEY", "DATADOG_APP_KEY", "DATADOG_APP_URL")
DEFAULT_SITE = "datadoghq.com"
# datadoghq.com and its regional subdomains (us3, us5, ap1, ...), datadoghq.eu, ddog-gov.com
SITE = re.compile(r"^(?:[a-z0-9]{2,8}\.)?(?:datadoghq\.com|datadoghq\.eu|ddog-gov\.com)$")
APP_URL = re.compile(r"^https://[a-z0-9.-]+(?::\d+)?/?$", re.I)
TIMEOUT = 20.0
TRIES = 3
MAX_WAIT = 10.0
LOG_LIMIT_MAX = 1000
MESSAGE_MAX = 4000
MONITOR_PAGE = 100
MONITOR_PAGES = 20
PLACEHOLDERS = ("dag_id", "task_id", "run_id", "env_id", "try_number")
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
STATES = ("Alert", "Warn", "No Data")
SEVERITIES = ("P1", "P2", "P3", "P4")
# a Datadog query value: characters with a meaning in the query syntax are escaped with a backslash
_SPECIAL = re.compile(r'([+\-=&|><!(){}\[\]^"~*?:\\/\s])')

DEFAULT_SETTING: Dict[str, Any] = {
    "enabled": False,
    "log_query": "dag_id:{dag_id} task_id:{task_id}",
    "log_lookback_minutes": 30,
    "log_limit": 200,
    "diagnosis_logs": True,
    "monitors": True,
    "monitor_tags": [],
    "monitor_states": ["Alert", "No Data"],
    "default_severity": "P3",
}


class DatadogError(Exception):
    """kind: not_configured | credentials | access_denied | not_found | bad_request | throttled | unavailable."""

    def __init__(self, kind: str, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.kind, self.status = kind, status


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- configuration (keys from the environment only)

def normalize_site(value: Any) -> str:
    """'https://app.datadoghq.eu/' -> 'datadoghq.eu'; '' -> the default site."""
    site = str(value or "").strip().lower()
    site = re.sub(r"^https?://", "", site).split("/")[0]
    for prefix in ("api.", "app."):
        if site.startswith(prefix):
            site = site[len(prefix):]
    return site or DEFAULT_SITE


def config_from_env(environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """{site, api_key, app_key, app_url, problems}. Never logged or returned to a client as is (see public())."""
    env = os.environ if environ is None else environ
    site = normalize_site(env.get("DATADOG_SITE"))
    problems: List[str] = []
    if not SITE.match(site):
        problems.append(f"DATADOG_SITE '{site}' is not a Datadog site (for example datadoghq.com, us3.datadoghq.com, "
                        "datadoghq.eu)")
    app_url = str(env.get("DATADOG_APP_URL") or "").strip().rstrip("/")
    if app_url and not APP_URL.match(app_url):
        problems.append("DATADOG_APP_URL must be an https address such as https://app.datadoghq.com")
        app_url = ""
    api_key, app_key = str(env.get("DATADOG_API_KEY") or "").strip(), str(env.get("DATADOG_APP_KEY") or "").strip()
    if not api_key:
        problems.append("DATADOG_API_KEY is not set on the API host")
    if not app_key:
        problems.append("DATADOG_APP_KEY is not set on the API host")
    return {"site": site, "api_key": api_key, "app_key": app_key, "app_url": app_url or f"https://app.{site}",
            "problems": problems}


def public(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """What a page may see: the site and links, and whether each key is present. Never a key or part of one."""
    return {"site": cfg["site"], "app_url": cfg["app_url"], "api_key_set": bool(cfg["api_key"]),
            "app_key_set": bool(cfg["app_key"]), "configured": not cfg["problems"], "problems": list(cfg["problems"])}


# ---------------------------------------------------------------- the setting

def validate_setting(value: Any) -> Tuple[Dict[str, Any], List[str]]:
    if not isinstance(value, dict):
        return dict(DEFAULT_SETTING), ["the Datadog setting must be an object"]
    out = dict(DEFAULT_SETTING)
    problems = [f"unknown Datadog setting {k}" for k in value if k not in DEFAULT_SETTING]
    for k in ("enabled", "diagnosis_logs", "monitors"):
        if k in value:
            if not isinstance(value[k], bool):
                problems.append(f"{k} must be true or false")
            else:
                out[k] = value[k]
    query = str(value.get("log_query", out["log_query"]) or "").strip()
    unknown = sorted(set(PLACEHOLDER.findall(query)) - set(PLACEHOLDERS))
    if not query:
        problems.append("the log query is empty")
    elif unknown:
        problems.append(f"the log query names {', '.join('{' + u + '}' for u in unknown)}; it can use "
                        + ", ".join("{" + p + "}" for p in PLACEHOLDERS))
    elif len(query) > 1000:
        problems.append("the log query is longer than 1000 characters")
    else:
        out["log_query"] = query
    for k, low, high in (("log_lookback_minutes", 1, 1440), ("log_limit", 10, LOG_LIMIT_MAX)):
        if k in value:
            v = value[k]
            if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
                problems.append(f"{k} must be a whole number from {low} to {high}")
            else:
                out[k] = v
    tags = value.get("monitor_tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        problems.append("monitor_tags must be a list of tags such as team:data")
    else:
        cleaned = [t.strip() for t in tags if t.strip()]
        bad = [t for t in cleaned if not re.match(r"^[A-Za-z][\w./:-]{0,199}$", t)]
        problems += [f"'{t}' is not a Datadog tag" for t in bad]
        out["monitor_tags"] = [t for t in cleaned if t not in bad][:20]
    states = value.get("monitor_states", out["monitor_states"])
    if not isinstance(states, list) or not states or any(s not in STATES for s in states):
        problems.append(f"monitor_states must list one or more of {', '.join(STATES)}")
    else:
        out["monitor_states"] = [s for s in STATES if s in states]
    severity = value.get("default_severity", out["default_severity"])
    if severity not in SEVERITIES:
        problems.append(f"default_severity must be one of {', '.join(SEVERITIES)}")
    else:
        out["default_severity"] = severity
    return out, problems


def setting(db: Any) -> Dict[str, Any]:
    """The saved DATADOG setting, with defaults (off before an admin saves one)."""
    import json

    try:
        found = db.query("""SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'DATADOG' AND IS_CURRENT
                            ORDER BY VERSION DESC LIMIT 1""")
    except Exception:
        return dict(DEFAULT_SETTING)
    raw = found[0].get("config_value") if found else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    value, _ = validate_setting(raw) if isinstance(raw, dict) else (dict(DEFAULT_SETTING), [])
    return value


# ---------------------------------------------------------------- log queries (pure)

def escape(value: Any) -> str:
    return _SPECIAL.sub(r"\\\1", str(value))


def log_query(template: str, values: Mapping[str, Any]) -> str:
    """The template with each {name} replaced by its escaped value. A term whose value is missing is left out
    ('task_id:{task_id}' for a run-level incident), so the query still finds the DAG's logs."""
    terms = []
    for term in str(template or "").split():
        names = PLACEHOLDER.findall(term)
        if any(values.get(n) in (None, "") for n in names):
            continue
        terms.append(PLACEHOLDER.sub(lambda m: escape(values[m.group(1)]), term))
    return " ".join(terms)


def incident_values(incident: Mapping[str, Any]) -> Dict[str, Any]:
    return {"dag_id": incident.get("dag_id"), "task_id": incident.get("task_id"), "run_id": incident.get("run_id"),
            "env_id": incident.get("env_id"), "try_number": incident.get("try_number")}


def _dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def incident_window(incident: Mapping[str, Any], lookback_minutes: int, now: Optional[datetime] = None
                    ) -> Tuple[datetime, datetime]:
    """From `lookback_minutes` before the incident was first seen to 15 minutes after it was last seen (not past now)."""
    now = now or utcnow()
    first = _dt(incident.get("first_seen")) or _dt(incident.get("opened_at")) or now
    last = _dt(incident.get("last_seen")) or first
    start = first - timedelta(minutes=lookback_minutes)
    end = min(now, max(last, first) + timedelta(minutes=15))
    return start, max(end, start + timedelta(minutes=1))


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def logs_link(app_url: str, query: str, start: datetime, end: datetime) -> str:
    return (f"{app_url}/logs?" + urlencode({"query": query, "from_ts": int(start.timestamp() * 1000),
                                           "to_ts": int(end.timestamp() * 1000), "live": "false"}))


def monitor_link(app_url: str, monitor_id: Any) -> str:
    return f"{app_url}/monitors/{quote(str(monitor_id), safe='')}"


def log_entry(item: Mapping[str, Any]) -> Dict[str, Any]:
    """One /api/v2/logs/events/search result, reduced and redacted."""
    a = item.get("attributes") or {}
    tags = [str(t) for t in (a.get("tags") or [])]
    keep = [t for t in tags if t.split(":")[0] in ("dag_id", "task_id", "run_id", "env", "service", "airflow_env")]
    return {"id": str(item.get("id") or ""), "at": a.get("timestamp"), "status": a.get("status"),
            "service": a.get("service"), "host": a.get("host"),
            "message": redact(str(a.get("message") or ""))[:MESSAGE_MAX], "tags": keep[:10]}


def logs_text(entries: List[Dict[str, Any]]) -> str:
    """Oldest first, one line per entry, as a log reads."""
    lines = []
    for e in sorted(entries, key=lambda e: str(e.get("at") or "")):
        head = " ".join(str(v) for v in (e.get("at"), (e.get("status") or "").upper(), e.get("service")) if v)
        lines.append(f"{head} {e.get('message') or ''}".strip())
    return "\n".join(lines)


# ---------------------------------------------------------------- the client

Http = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]], Dict[str, Any]], Tuple[int, Any, Dict[str, str]]]


def _httpx(method: str, url: str, headers: Dict[str, str], body: Optional[Dict[str, Any]], params: Dict[str, Any]
           ) -> Tuple[int, Any, Dict[str, str]]:
    import httpx

    try:
        r = httpx.request(method, url, headers=headers, json=body, params=params or None, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise DatadogError("unavailable", f"Datadog could not be reached ({type(exc).__name__})") from None
    try:
        data = r.json()
    except ValueError:
        data = None
    return r.status_code, data, {k.lower(): v for k, v in r.headers.items()}


def _errors(data: Any) -> str:
    items = data.get("errors") if isinstance(data, dict) else None
    text = "; ".join(str(e.get("detail") or e.get("title") or e) if isinstance(e, dict) else str(e)
                     for e in (items or [])[:3])
    return redact(text)[:300]


class Client:
    def __init__(self, cfg: Dict[str, Any], http: Optional[Http] = None, sleep: Callable[[float], None] = time.sleep):
        if cfg.get("problems"):
            raise DatadogError("not_configured", "; ".join(cfg["problems"]))
        if not SITE.match(cfg["site"]):
            raise DatadogError("not_configured", "the Datadog site is not a Datadog site")
        self.cfg, self.http, self.sleep = cfg, http or _httpx, sleep
        self.base = f"https://api.{cfg['site']}"

    def _call(self, method: str, path: str, *, body: Optional[Dict[str, Any]] = None,
              params: Optional[Dict[str, Any]] = None, app_key: bool = True) -> Any:
        headers = {"DD-API-KEY": self.cfg["api_key"], "Accept": "application/json"}
        if app_key:
            headers["DD-APPLICATION-KEY"] = self.cfg["app_key"]
        if body is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(TRIES):
            status, data, head = self.http(method, self.base + path, headers, body, params or {})
            if status < 400:
                return data
            if (status == 429 or status >= 500) and attempt < TRIES - 1:
                try:
                    wait = float(head.get("x-ratelimit-reset") or 0) if status == 429 else 0.0
                except ValueError:
                    wait = 0.0
                self.sleep(min(MAX_WAIT, wait or 2.0 ** attempt))
                continue
            detail = _errors(data)
            if status == 429:
                raise DatadogError("throttled", "Datadog is rate limiting these calls; try again in a minute", status)
            if status >= 500:
                raise DatadogError("unavailable", f"Datadog answered {status}" + (f": {detail}" if detail else ""), status)
            if status in (401, 403):
                which = "API key" if not app_key else "API key or application key (or the application key's scopes)"
                raise DatadogError("credentials" if status == 401 else "access_denied",
                                   f"Datadog refused the {which}" + (f": {detail}" if detail else ""), status)
            if status == 404:
                raise DatadogError("not_found", f"Datadog has no {path}", status)
            raise DatadogError("bad_request", f"Datadog rejected the request" + (f": {detail}" if detail else ""), status)
        raise DatadogError("unavailable", "Datadog did not answer")   # not reached

    def validate(self) -> bool:
        """True when the API key is valid (GET /api/v1/validate, which needs no application key)."""
        data = self._call("GET", "/api/v1/validate", app_key=False)
        return bool(isinstance(data, dict) and data.get("valid"))

    def search_logs(self, query: str, start: datetime, end: datetime, limit: int = 200) -> List[Dict[str, Any]]:
        """Newest first, at most `limit` (one page up to 1000). Needs logs_read_data on the application key."""
        body = {"filter": {"query": query or "*", "from": _iso(start), "to": _iso(end)}, "sort": "-timestamp",
                "page": {"limit": max(1, min(int(limit), LOG_LIMIT_MAX))}}
        data = self._call("POST", "/api/v2/logs/events/search", body=body)
        return [log_entry(d) for d in (data or {}).get("data") or [] if isinstance(d, dict)]

    def monitors(self, tags: Optional[List[str]] = None, states: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Monitors (with the given monitor tags), with each group's state. Needs monitors_read."""
        params: Dict[str, Any] = {"group_states": ",".join(s.lower() for s in (states or STATES)),
                                  "page_size": MONITOR_PAGE}
        if tags:
            params["monitor_tags"] = ",".join(tags)
        out: List[Dict[str, Any]] = []
        for page in range(MONITOR_PAGES):
            data = self._call("GET", "/api/v1/monitor", params={**params, "page": page})
            items = [m for m in (data or []) if isinstance(m, dict)]
            out += items
            if len(items) < MONITOR_PAGE:
                break
        return out

    def check(self, query: str = "*") -> Dict[str, Any]:
        """The connection test: the API key, then each read the platform uses. Never raises for one failing read."""
        result: Dict[str, Any] = {"site": self.cfg["site"], "api_key": False, "logs": None, "monitors": None,
                                  "errors": {}}
        try:
            result["api_key"] = self.validate()
        except DatadogError as exc:
            result["errors"]["api_key"] = str(exc)
            return result
        now = utcnow()
        try:
            result["logs"] = len(self.search_logs(query, now - timedelta(minutes=15), now, 5))
        except DatadogError as exc:
            result["errors"]["logs"] = str(exc)
        try:
            result["monitors"] = len(self.monitors())
        except DatadogError as exc:
            result["errors"]["monitors"] = str(exc)
        return result


def client(environ: Optional[Mapping[str, str]] = None, http: Optional[Http] = None) -> Client:
    return Client(config_from_env(environ), http)


# ---------------------------------------------------------------- an incident's logs

def incident_logs(incident: Mapping[str, Any], setting_value: Dict[str, Any], cli: Optional[Client] = None,
                  now: Optional[datetime] = None, minutes: Optional[int] = None) -> Dict[str, Any]:
    """{query, from, to, link, logs} for an incident, newest first. Raises DatadogError."""
    cli = cli or client()
    lookback = minutes or int(setting_value.get("log_lookback_minutes") or DEFAULT_SETTING["log_lookback_minutes"])
    start, end = incident_window(incident, lookback, now)
    query = log_query(setting_value.get("log_query") or DEFAULT_SETTING["log_query"], incident_values(incident))
    entries = cli.search_logs(query, start, end, int(setting_value.get("log_limit") or DEFAULT_SETTING["log_limit"]))
    return {"query": query, "from": _iso(start), "to": _iso(end), "link": logs_link(cli.cfg["app_url"], query, start, end),
            "logs": entries}
