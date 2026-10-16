"""A self-hosted Apache Airflow (KIND 'AIRFLOW'): the same REST API as MWAA, called directly over HTTP.

Every Airflow method of services.ops.mwaa.Mwaa goes through `invoke`, so this client only replaces the transport:
  - Airflow 3 (REST v2, under /api/v2): a bearer token from POST /auth/token, fetched again when it expires (401);
  - Airflow 2 (REST v1, under /api/v1): basic auth (the basic_auth API backend must be enabled).
The base URL is the environment's AIRFLOW_URL. Credentials come only from the host, never from the database:
AIP_AIRFLOW_<ENV_ID>_USER and AIP_AIRFLOW_<ENV_ID>_PASSWORD (ENV_ID upper case, other characters as '_').
Errors map to the same MwaaError kinds, so callers handle both kinds of environment alike.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from services.ops.mwaa import Mwaa, MwaaError

TIMEOUT = 30
Http = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, Any]]


def _urllib(method: str, url: str, headers: Dict[str, str], body: Optional[bytes]) -> Tuple[int, Any]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:   # noqa: S310 (operator-configured URL)
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except ValueError:
            return exc.code, raw.decode("utf-8", errors="replace")[:500]


def env_key(env_id: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", str(env_id or "").upper())


def credentials(env_id: str) -> Tuple[Optional[str], Optional[str]]:
    key = env_key(env_id)
    return os.environ.get(f"AIP_AIRFLOW_{key}_USER"), os.environ.get(f"AIP_AIRFLOW_{key}_PASSWORD")


class AirflowRest(Mwaa):
    def __init__(self, base_url: str, username: Optional[str], password: Optional[str], http: Optional[Http] = None,
                 sleep: Callable[[float], None] = time.sleep, max_tries: int = 3, env_id: str = ""):
        assert base_url, "the Airflow URL is required"
        self.base = base_url.rstrip("/")
        self.username, self.password = username, password
        self.http = http or _urllib
        self._token: Optional[str] = None
        self._prefix: Optional[str] = None
        self.env_id = env_id
        self.env_name, self.region = env_id or self.base, "local"
        self._client_obj = None
        self._sleep = sleep
        self.max_tries = max(1, int(max_tries))
        self._version = None

    # ------------------------------------------------------------ transport

    def _need_credentials(self) -> None:
        if not self.username or not self.password:
            key = env_key(self.env_id) or "<ENV_ID>"
            raise MwaaError("credentials", f"Airflow credentials are not set on this host (AIP_AIRFLOW_{key}_USER and "
                                           f"AIP_AIRFLOW_{key}_PASSWORD)")

    def _call(self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes]) -> Tuple[int, Any]:
        try:
            return self.http(method, url, headers, body)
        except (urllib.error.URLError, OSError) as exc:
            raise MwaaError("unavailable", f"Could not reach Airflow at {self.base}: {exc}") from exc

    def _token_for_v2(self, force: bool = False) -> str:
        if self._token and not force:
            return self._token
        self._need_credentials()
        status, found = self._call("POST", f"{self.base}/auth/token", {"Content-Type": "application/json"},
                                   json.dumps({"username": self.username, "password": self.password}).encode())
        if status in (401, 403):
            raise MwaaError("credentials", "Airflow refused the user name or password", status)
        if status >= 400 or not isinstance(found, dict) or not found.get("access_token"):
            raise MwaaError("airflow_error", f"Airflow did not issue a token ({status})", status)
        self._token = str(found["access_token"])
        return self._token

    def _auth(self, prefix: str, force: bool = False) -> Dict[str, str]:
        if prefix == "/api/v2":
            return {"Authorization": f"Bearer {self._token_for_v2(force)}"}
        self._need_credentials()
        pair = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return {"Authorization": f"Basic {pair}"}

    def prefix(self) -> str:
        """/api/v2 (Airflow 3) or /api/v1 (Airflow 2): the first that answers /version."""
        if self._prefix is None:
            for candidate in ("/api/v2", "/api/v1"):
                status, _ = self._call("GET", f"{self.base}{candidate}/version", {"Accept": "application/json"}, None)
                if status != 404:
                    self._prefix = candidate
                    break
            else:
                raise MwaaError("not_found", f"No Airflow REST API at {self.base}", 404)
        return self._prefix

    @staticmethod
    def _detail(found: Any) -> str:
        if isinstance(found, dict):
            return str(found.get("detail") or found.get("title") or found)[:500]
        return str(found or "")[:500]

    def invoke(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Any:
        prefix = self.prefix()
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{self.base}{prefix}{path}" + (f"?{query}" if query else "")
        data = json.dumps(body).encode() if body is not None else None
        delay, refreshed = 1.0, False
        for attempt in range(1, self.max_tries + 1):
            headers = {"Accept": "application/json", **self._auth(prefix)}
            if data is not None:
                headers["Content-Type"] = "application/json"
            status, found = self._call(method.upper(), url, headers, data)
            if status == 401 and prefix == "/api/v2" and not refreshed:   # the token expired: one fresh token
                self._token_for_v2(force=True)
                refreshed = True
                continue
            if status < 400:
                return found
            if status == 401:
                raise MwaaError("credentials", "Airflow refused the credentials", status)
            if status == 403:
                raise MwaaError("access_denied", f"Airflow refused the call: {self._detail(found)}", status)
            if status == 404:
                raise MwaaError("not_found", f"Airflow answered 404: {self._detail(found)}", status)
            if status == 429 or status >= 500:
                if attempt < self.max_tries:
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise MwaaError("throttled" if status == 429 else "unavailable", f"Airflow is busy or failing ({status})", status)
            raise MwaaError("airflow_error", f"Airflow answered {status}: {self._detail(found)}", status)
        raise MwaaError("unavailable", "Airflow did not answer")

    def version(self) -> Dict[str, Any]:
        if self._version is None:
            found = self.invoke("GET", "/version") or {}
            version = str(found.get("version") or "") if isinstance(found, dict) else ""
            self._version = {"version": version, "api_version": "v2" if self.prefix() == "/api/v2" else "v1"}
        return self._version
