"""Retry an incident's failed tasks in Airflow (MWAA clearTaskInstances), always previewed first.

A dry run asks Airflow which task instances would be cleared and returns them with a preview token: an HMAC-signed,
10 minute token binding the incident, environment, DAG, run, the task list, the downstream flag and the person who
previewed. The real retry must present that token; its task list and flags come from the token, so what runs is what
was previewed. When the call is replayed by an approval (governance policy on OPS.OPERATE, off by default) the token
may be up to 24 hours old and the approver may differ from the requester.

A retry is refused when the AI diagnosis or a person marked the failure safe_to_retry 'no', unless an override reason
of at least 15 characters is given (it is kept on the timeline). Timeline events: retry_requested, retried, retry_failed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.ops.redact import redact

TOKEN_SECONDS = 600
REPLAY_SECONDS = 24 * 3600
OVERRIDE_MIN = 15
MAX_TASKS = 50
_FALLBACK_KEY = secrets.token_hex(32)   # without a host key, tokens are valid only in this API process


class RetryError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _key(key: Optional[str] = None) -> bytes:
    from services.ops.notify import secret_key

    return ("ops-retry:" + (key or secret_key() or _FALLBACK_KEY)).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def binding(incident: Dict[str, Any], task_ids: List[str], downstream: bool) -> Dict[str, Any]:
    return {"i": incident["incident_id"], "e": incident["env_id"], "d": incident["dag_id"], "r": incident.get("run_id"),
            "t": sorted(dict.fromkeys(str(t) for t in task_ids)), "ds": bool(downstream)}


def make_token(bound: Dict[str, Any], user: str, now: Optional[float] = None, ttl: int = TOKEN_SECONDS,
               key: Optional[str] = None) -> str:
    payload = {**bound, "u": str(user or "").upper(), "x": int((now or time.time()) + ttl)}
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_key(key), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def check_token(token: Optional[str], incident_id: str, user: str, now: Optional[float] = None, replay: bool = False,
                key: Optional[str] = None) -> Dict[str, Any]:
    """The binding inside a valid token for this incident (and this user, unless replayed by an approval)."""
    if not token or "." not in token:
        raise RetryError("Run a dry run first: the retry needs the preview_token it returns.", 422)
    body, _, sig = token.rpartition(".")
    expected = hmac.new(_key(key), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise RetryError("The preview token is not valid. Run the dry run again.", 422)
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        raise RetryError("The preview token is not valid. Run the dry run again.", 422) from None
    now = now or time.time()
    limit = int(payload.get("x") or 0) + (REPLAY_SECONDS - TOKEN_SECONDS if replay else 0)
    if now > limit:
        raise RetryError("The preview expired (it is valid for 10 minutes). Run the dry run again.", 409)
    if payload.get("i") != incident_id:
        raise RetryError("The preview token belongs to another incident.", 409)
    if not replay and str(payload.get("u") or "") != str(user or "").upper():
        raise RetryError("The preview was made by someone else. Run the dry run yourself.", 409)
    return payload


def normalize_cleared(response: Any, dag_id: str, run_id: Optional[str]) -> List[Dict[str, Any]]:
    """Airflow's clearTaskInstances answer (v1 TaskInstanceReferenceCollection or v2 TaskInstanceCollection) as
    [{dag_id, run_id, task_id, map_index, state}]."""
    items = response.get("task_instances") if isinstance(response, dict) else response
    out = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("task_id"):
            continue
        map_index = item.get("map_index")
        try:
            map_index = int(map_index) if map_index is not None else -1
        except (TypeError, ValueError):
            map_index = -1
        out.append({"dag_id": str(item.get("dag_id") or dag_id), "run_id": str(item.get("dag_run_id") or item.get("run_id") or run_id or ""),
                    "task_id": str(item["task_id"]), "map_index": map_index,
                    "state": (str(item["state"]).lower() if item.get("state") else None)})
    return out


def safety(incident: Dict[str, Any], human: Optional[Dict[str, Any]]) -> Tuple[Optional[str], str]:
    """(safe_to_retry, who said it): a person's mark wins over the AI's."""
    if human and human.get("safe_to_retry"):
        return str(human["safe_to_retry"]), f"marked by {human.get('actor') or 'a person'}"
    ai = incident.get("ai") or {}
    if isinstance(ai, dict) and ai.get("safe_to_retry"):
        return str(ai["safe_to_retry"]), "the AI diagnosis"
    return None, ""


def check_allowed(value: Optional[str], source: str, override_reason: Optional[str]) -> Optional[str]:
    """None when the retry may go ahead; raises when it was marked unsafe and no override reason is given. Returns the
    override reason (redacted) when one was needed and given."""
    if value != "no":
        return None
    reason = (override_reason or "").strip()
    if len(reason) < OVERRIDE_MIN:
        raise RetryError(f"Retrying was marked unsafe ({source}). Give an override_reason of at least {OVERRIDE_MIN} "
                         "characters to retry anyway.", 409)
    return redact(reason)[:500]


def human_mark(db: Any, incident_id: str) -> Optional[Dict[str, Any]]:
    found = db.query("""SELECT ACTOR, DETAIL FROM OPS.INCIDENT_EVENT WHERE INCIDENT_ID = %s AND KIND = 'retry_marked'
                         ORDER BY CREATED_AT DESC LIMIT 1""", (incident_id,))
    if not found:
        return None
    detail = found[0].get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            detail = {}
    return {**(detail or {}), "actor": found[0].get("actor")}


def failed_tasks(db: Any, incident: Dict[str, Any]) -> List[str]:
    """The task to retry: the incident's task, else the run's failed tasks (latest try)."""
    if incident.get("task_id"):
        return [incident["task_id"]]
    found = db.query("""SELECT DISTINCT TASK_ID FROM OPS.TASK_RUN WHERE ENV_ID = %s AND DAG_ID = %s AND RUN_ID = %s
                         AND STATE IN ('failed', 'upstream_failed')
                        QUALIFY TRY_NUMBER = MAX(TRY_NUMBER) OVER (PARTITION BY TASK_ID, MAP_INDEX)
                        ORDER BY TASK_ID LIMIT 50""", (incident["env_id"], incident["dag_id"], incident.get("run_id")))
    return [r["task_id"] for r in found]


def retry(db: Any, incident_id: str, *, dry_run: bool, user: str, downstream: bool = False,
          task_ids: Optional[List[str]] = None, preview_token: Optional[str] = None, override_reason: Optional[str] = None,
          replay: bool = False, mwaa_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
          now: Optional[float] = None) -> Dict[str, Any]:
    """{dry_run, tasks, cleared, detail, preview_token?, safe_to_retry}. MwaaError propagates (the API maps it)."""
    from services.ops.context import load_incident
    from services.ops.incidents import SqlStore

    incident = load_incident(db, incident_id)
    if not incident:
        raise RetryError(f"Incident {incident_id} not found", 404)
    if not incident.get("run_id"):
        raise RetryError("This incident has no Airflow run to retry (a late DAG has nothing to clear).", 409)
    env = db.query("SELECT ENV_ID, MWAA_ENV, REGION FROM OPS.AIRFLOW_ENV WHERE ENV_ID = %s", (incident["env_id"],))
    if not env:
        raise RetryError(f"Airflow environment {incident['env_id']} not found", 404)
    env = env[0]
    value, source = safety(incident, human_mark(db, incident_id))
    store = SqlStore(db)

    def client():
        if mwaa_factory:
            return mwaa_factory(env)
        from services.ops.mwaa import Mwaa

        return Mwaa(env["mwaa_env"], env["region"])

    if dry_run:
        tasks = [str(t) for t in (task_ids or failed_tasks(db, incident)) if str(t).strip()][:MAX_TASKS]
        if not tasks:
            raise RetryError("No failed task to retry in this run.", 409)
        found = client().clear_task_instances(incident["dag_id"], tasks, incident["run_id"], dry_run=True,
                                              include_downstream=downstream)
        cleared = normalize_cleared(found, incident["dag_id"], incident["run_id"])
        detail = (f"Airflow would clear {len(cleared)} task instance(s). Confirm within 10 minutes."
                  if cleared else "Airflow would clear nothing: the tasks are not in a failed state any more.")
        if value == "no":
            detail += f" Retrying was marked unsafe ({source}): an override reason is required."
        return {"dry_run": True, "tasks": cleared, "cleared": None, "detail": detail,
                "preview_token": make_token(binding(incident, tasks, downstream), user, now), "safe_to_retry": value}

    bound = check_token(preview_token, incident_id, user, now, replay)
    if task_ids is not None and sorted(dict.fromkeys(str(t) for t in task_ids)) != bound["t"]:
        raise RetryError("The tasks differ from the preview. Run the dry run again.", 409)
    if (bound.get("e"), bound.get("d"), bound.get("r")) != (incident["env_id"], incident["dag_id"], incident["run_id"]):
        raise RetryError("The incident moved to another run since the preview. Run the dry run again.", 409)
    override = check_allowed(value, source, override_reason)
    store.event(incident_id, "retry_requested", user, {"tasks": bound["t"], "downstream": bound["ds"], "run_id": bound["r"],
                                                       "override_reason": override, "safe_to_retry": value})
    try:
        found = client().clear_task_instances(incident["dag_id"], bound["t"], incident["run_id"], dry_run=False,
                                              include_downstream=bool(bound["ds"]))
    except Exception as exc:
        store.event(incident_id, "retry_failed", user, {"error": redact(str(exc))[:300]})
        raise
    cleared = normalize_cleared(found, incident["dag_id"], incident["run_id"])
    store.event(incident_id, "retried", user, {"cleared": len(cleared), "tasks": bound["t"], "run_id": bound["r"]})
    return {"dry_run": False, "tasks": cleared, "cleared": len(cleared), "safe_to_retry": value,
            "detail": f"Cleared {len(cleared)} task instance(s); Airflow schedules them again. The incident resolves "
                      "itself when the retry succeeds."}


def mark(db: Any, incident_id: str, value: str, reason: Optional[str], actor: str) -> Dict[str, Any]:
    """A person's own safe-to-retry call (wins over the AI's)."""
    from services.ops.incidents import SqlStore

    if value not in ("yes", "no", "after_fix"):
        raise RetryError("safe_to_retry must be yes, no or after_fix", 422)
    store = SqlStore(db)
    if not store.get(incident_id):
        raise RetryError(f"Incident {incident_id} not found", 404)
    store.event(incident_id, "retry_marked", actor, {"safe_to_retry": value, "reason": redact((reason or "").strip())[:500] or None})
    return {"safe_to_retry": value}
