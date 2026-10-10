"""Amazon MWAA connector: the Airflow REST API through boto3 `mwaa.invoke_rest_api` (IAM, no Airflow web token).

Credentials come only from the default AWS chain on the host (instance or task role, AWS_PROFILE, environment); this
module never accepts keys. The host needs airflow:InvokeRestApi and airflow:GetEnvironment on the environment (and
logs:FilterLogEvents, logs:GetLogEvents for CloudWatch fallbacks later).

Airflow 2 serves REST v1 and Airflow 3 serves v2; invoke_rest_api takes paths relative to the API root ('/dags'), so
the paths are the same and the adapter only changes parameters and payload handling. The version comes from /version.
Throttling and 5xx answers are retried with backoff, at most 3 tries.
"""

from __future__ import annotations

import ast
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

LOG_CAP = 256 * 1024
LOG_PAGES = 50
PAGE = 100
REQUIRED_ACTIONS = ["airflow:InvokeRestApi", "airflow:GetEnvironment", "logs:FilterLogEvents", "logs:GetLogEvents"]
_RETRY_CODES = {"ThrottlingException", "TooManyRequestsException", "RequestLimitExceeded", "ServiceUnavailableException",
                "InternalServerException", "RestApiServerException"}
_CREDENTIAL_ERRORS = {"NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError", "ProfileNotFound",
                      "NoRegionError", "UnauthorizedSSOTokenError", "SSOTokenLoadError", "TokenRetrievalError"}


class MwaaError(Exception):
    """kind: credentials | access_denied | not_found | airflow_error | throttled | unavailable."""

    def __init__(self, kind: str, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.kind, self.status = kind, status


def _seg(value: Any) -> str:
    return quote(str(value), safe="")


def _version_tuple(text: str) -> Tuple[int, ...]:
    out = []
    for part in str(text or "").split("+")[0].split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def api_version_for(airflow_version: str) -> str:
    """'v2' for Airflow 3 and later, 'v1' for Airflow 2."""
    major = (_version_tuple(airflow_version) or (2,))[0]
    return "v2" if major >= 3 else "v1"


def supports_updated_at(airflow_version: str) -> bool:
    """dagRuns accepts updated_at_gte from Airflow 2.6 (v1) and in every v2."""
    return _version_tuple(airflow_version) >= (2, 6, 0)


def log_text(content: Any) -> str:
    """The text of a log page: a string, a v1 '[(host, text)]' string, or v2 structured messages."""
    if content is None:
        return ""
    if isinstance(content, list):
        lines = []
        for item in content:
            if isinstance(item, dict):
                stamp = item.get("timestamp") or ""
                level = item.get("level") or ""
                event = item.get("event") if item.get("event") is not None else item.get("message", "")
                lines.append(" ".join(str(x) for x in (stamp, level.upper() if level else "", event) if x != ""))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                lines.append(str(item[1]))
            else:
                lines.append(str(item))
        return "\n".join(lines)
    text = str(content)
    if text.startswith("[(") and len(text) < 8 * 1024 * 1024:
        try:
            return log_text(ast.literal_eval(text))
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            return text
    return text


def keep_tail(text: str, cap: int = LOG_CAP) -> Tuple[str, bool]:
    """At most `cap` bytes of UTF-8, keeping the end (where the error is)."""
    data = text.encode("utf-8")
    if len(data) <= cap:
        return text, False
    tail = data[-cap:].decode("utf-8", errors="ignore")
    cut = tail.find("\n")
    return (tail[cut + 1:] if 0 <= cut < 2000 else tail), True


class Mwaa:
    def __init__(self, env_name: str, region: str, client: Any = None, sleep: Callable[[float], None] = time.sleep,
                 max_tries: int = 3):
        assert env_name and region, "MWAA environment name and region are required"
        self.env_name, self.region = env_name, region
        self._client_obj = client
        self._sleep = sleep
        self.max_tries = max(1, int(max_tries))
        self._version: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------ transport

    def _client(self) -> Any:
        if self._client_obj is None:
            try:
                import boto3  # lazy: only hosts that talk to MWAA need it
            except ImportError as exc:
                raise MwaaError("unavailable", "boto3 is not installed on this host (pip install boto3)") from exc
            try:
                self._client_obj = boto3.session.Session().client("mwaa", region_name=self.region)
            except Exception as exc:
                raise self._map(exc) from exc
        return self._client_obj

    def _map(self, exc: BaseException) -> MwaaError:
        name = type(exc).__name__
        if isinstance(exc, MwaaError):
            return exc
        if name in _CREDENTIAL_ERRORS:
            return MwaaError("credentials", "AWS credentials are not available on the API host")
        response = getattr(exc, "response", None) or {}
        code = str((response.get("Error") or {}).get("Code") or name)
        message = str((response.get("Error") or {}).get("Message") or exc)[:500]
        rest_status = response.get("RestApiStatusCode")
        http = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if code in ("AccessDeniedException", "AccessDenied", "UnrecognizedClientException", "InvalidClientTokenId",
                    "ExpiredTokenException", "ExpiredToken"):
            if code in ("UnrecognizedClientException", "InvalidClientTokenId", "ExpiredTokenException", "ExpiredToken"):
                return MwaaError("credentials", "AWS credentials are not available on the API host (they are invalid or expired)")
            return MwaaError("access_denied", "AWS denied the call. The host's IAM role needs " + ", ".join(REQUIRED_ACTIONS)
                             + f" on the MWAA environment {self.env_name}.", 403)
        if code == "ResourceNotFoundException":
            return MwaaError("not_found", f"MWAA environment {self.env_name} was not found in {self.region}", 404)
        if rest_status == 403:
            return MwaaError("access_denied", "Airflow refused the call: the IAM principal's Airflow role lacks this permission "
                             "(MWAA maps airflow:InvokeRestApi to an Airflow role).", 403)
        if rest_status == 404:
            return MwaaError("not_found", f"Airflow answered 404: {self._rest_detail(response) or message}", 404)
        if code in _RETRY_CODES or rest_status == 429 or (rest_status or 0) >= 500 or (http or 0) >= 500 or http == 429:
            return MwaaError("throttled" if (rest_status == 429 or http == 429 or "Throttl" in code) else "unavailable",
                             f"MWAA is busy or failing ({code}): {message}", rest_status or http)
        if name in ("EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ConnectionClosedError"):
            return MwaaError("unavailable", f"Could not reach MWAA in {self.region}: {message}")
        return MwaaError("airflow_error", f"{code}: {self._rest_detail(response) or message}", rest_status or http)

    @staticmethod
    def _rest_detail(response: Dict[str, Any]) -> str:
        body = response.get("RestApiResponse")
        if isinstance(body, dict):
            return str(body.get("detail") or body.get("title") or "")[:500]
        return str(body or "")[:500]

    def invoke(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Any:
        """One REST call; returns the decoded RestApiResponse. Throttling and 5xx are retried with backoff."""
        request: Dict[str, Any] = {"Name": self.env_name, "Path": path, "Method": method.upper()}
        if params:
            request["QueryParameters"] = {k: v for k, v in params.items() if v is not None}
        if body is not None:
            request["Body"] = body
        delay = 1.0
        for attempt in range(1, self.max_tries + 1):
            try:
                answer = self._client().invoke_rest_api(**request)
                return answer.get("RestApiResponse")
            except Exception as exc:
                error = self._map(exc)
                if error.kind in ("throttled", "unavailable") and attempt < self.max_tries:
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise error from exc
        raise MwaaError("unavailable", "MWAA did not answer")  # not reached

    # ------------------------------------------------------------ Airflow API

    def version(self) -> Dict[str, Any]:
        """{version, api_version}; cached per instance."""
        if self._version is None:
            found = self.invoke("GET", "/version") or {}
            version = str(found.get("version") or "") if isinstance(found, dict) else ""
            self._version = {"version": version, "api_version": api_version_for(version)}
        return self._version

    @property
    def api_version(self) -> str:
        return self.version()["api_version"]

    def dags(self, limit: int = PAGE, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
        found = self.invoke("GET", "/dags", {"limit": int(limit), "offset": int(offset)}) or {}
        dags = list(found.get("dags") or [])
        return dags, int(found.get("total_entries") or len(dags))

    def all_dags(self, page: int = PAGE, max_pages: int = 20) -> Tuple[List[Dict[str, Any]], bool]:
        """(dags, complete) over every page up to max_pages."""
        out: List[Dict[str, Any]] = []
        for i in range(max_pages):
            dags, total = self.dags(page, i * page)
            out.extend(dags)
            if not dags or len(out) >= total:
                return out, True
        return out, False

    def dag_runs(self, dag_id: str = "~", updated_since: Optional[str] = None, limit: int = PAGE,
                 offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
        """Runs changed since `updated_since` (ISO time): updated_at_gte where Airflow supports it, else
        start_date_gte (the caller widens the window and re-reads runs it still has as running)."""
        params: Dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
        if updated_since:
            key = "updated_at_gte" if supports_updated_at(self.version()["version"]) else "start_date_gte"
            params[key] = updated_since
        found = self.invoke("GET", f"/dags/{_seg(dag_id)}/dagRuns", params) or {}
        runs = list(found.get("dag_runs") or [])
        return runs, int(found.get("total_entries") or len(runs))

    def dag_run(self, dag_id: str, run_id: str) -> Dict[str, Any]:
        return self.invoke("GET", f"/dags/{_seg(dag_id)}/dagRuns/{_seg(run_id)}") or {}

    def task_instances(self, dag_id: str, run_id: str, page: int = PAGE, max_pages: int = 20) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for i in range(max_pages):
            found = self.invoke("GET", f"/dags/{_seg(dag_id)}/dagRuns/{_seg(run_id)}/taskInstances",
                                {"limit": page, "offset": i * page}) or {}
            items = list(found.get("task_instances") or [])
            for item in items:
                item.setdefault("dag_id", dag_id)
                item.setdefault("dag_run_id", run_id)
            out.extend(items)
            if not items or len(out) >= int(found.get("total_entries") or 0):
                break
        return out

    def task_log(self, dag_id: str, run_id: str, task_id: str, try_number: int, map_index: int = -1,
                 cap: int = LOG_CAP) -> Dict[str, Any]:
        """{text, truncated}: every page through the continuation token, capped at `cap` bytes keeping the tail."""
        path = f"/dags/{_seg(dag_id)}/dagRuns/{_seg(run_id)}/taskInstances/{_seg(task_id)}/logs/{int(try_number)}"
        token: Optional[str] = None
        text, truncated = "", False
        for _ in range(LOG_PAGES):
            params: Dict[str, Any] = {"full_content": "false"}
            if map_index is not None and int(map_index) >= 0:
                params["map_index"] = int(map_index)
            if token:
                params["token"] = token
            found = self.invoke("GET", path, params)
            if isinstance(found, dict):
                page, next_token = log_text(found.get("content")), found.get("continuation_token")
            else:
                page, next_token = log_text(found), None
            if page:
                text = f"{text}\n{page}" if text else page
                text, cut = keep_tail(text, cap)
                truncated = truncated or cut
            if not next_token or next_token == token or not page:
                break
            token = next_token
        return {"text": text, "truncated": truncated}

    def clear_task_instances(self, dag_id: str, task_ids: List[str], run_id: str, dry_run: bool = True,
                             only_failed: bool = True, include_downstream: bool = False) -> Any:
        """Clear (retry) tasks of one run. Dry run by default: it returns what would be cleared."""
        assert task_ids, "task_ids are required"
        body = {"dry_run": bool(dry_run), "task_ids": list(task_ids), "dag_run_id": run_id, "only_failed": bool(only_failed),
                "reset_dag_runs": True, "include_upstream": False, "include_downstream": bool(include_downstream)}
        return self.invoke("POST", f"/dags/{_seg(dag_id)}/clearTaskInstances", body=body)

    # ------------------------------------------------------------ DAG dependencies (PR O3)

    def datasets(self, page: int = PAGE, max_pages: int = 20) -> Tuple[List[Dict[str, Any]], bool]:
        """(datasets, complete): Airflow 2 /datasets, Airflow 3 /assets, every page up to max_pages."""
        path, key = ("/assets", "assets") if self.api_version == "v2" else ("/datasets", "datasets")
        out: List[Dict[str, Any]] = []
        for i in range(max_pages):
            found = self.invoke("GET", path, {"limit": page, "offset": i * page}) or {}
            items = list(found.get(key) or []) if isinstance(found, dict) else []
            out.extend(items)
            if not items or len(out) >= int((found or {}).get("total_entries") or 0):
                return out, True
        return out, False

    def dag_tasks(self, dag_id: str) -> List[Dict[str, Any]]:
        """The DAG's task definitions (operator class, downstream ids; params and extra links where Airflow exposes them)."""
        found = self.invoke("GET", f"/dags/{_seg(dag_id)}/tasks") or {}
        return list(found.get("tasks") or []) if isinstance(found, dict) else []

    def task_links(self, dag_id: str, run_id: str, task_id: str, map_index: int = -1) -> Dict[str, Any]:
        """Resolved operator extra links of one task instance ({name: url}); ExternalTaskSensor and
        TriggerDagRunOperator link to the other DAG this way."""
        params = {"map_index": int(map_index)} if map_index is not None and int(map_index) >= 0 else None
        found = self.invoke("GET", f"/dags/{_seg(dag_id)}/dagRuns/{_seg(run_id)}/taskInstances/{_seg(task_id)}/links", params)
        return found if isinstance(found, dict) else {}
