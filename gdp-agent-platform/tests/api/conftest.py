"""Fixtures for API tests.

Two layers:
  * offline  - FastAPI TestClient against app.main with RBAC bypassed and a fake DB
               (no Snowflake needed): routing, validation, auth, CORS, OpenAPI.
  * live     - httpx against a running API (AIP_API_URL, default http://127.0.0.1:8001)
               marked `live`; skipped automatically when the API is unreachable.
Write tests (create/update/delete) are marked `writes` and need AIP_API_ALLOW_WRITES=1.
"""
import os
import sys
from pathlib import Path

import httpx
import pytest

API_DIR = Path(__file__).resolve().parents[2] / "apps" / "api"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(Path(__file__).parent))

API_URL = os.environ.get("AIP_API_URL", "http://127.0.0.1:8001").rstrip("/")
ALLOW_WRITES = os.environ.get("AIP_API_ALLOW_WRITES", "0") == "1"
SESSION = os.environ.get("AIP_API_SESSION", "")  # needed only when API runs with AIP_AUTH=pat
ROLE = os.environ.get("AIP_API_ROLE", "")


def pytest_configure(config):
    for name, desc in [("api", "API test"), ("live", "needs a running API"),
                       ("writes", "changes data; needs AIP_API_ALLOW_WRITES=1"),
                       ("offline", "in-process TestClient, no Snowflake")]:
        config.addinivalue_line("markers", f"{name}: {desc}")


_live_ok = None


def _api_up() -> bool:
    global _live_ok
    if _live_ok is None:
        try:
            _live_ok = httpx.get(API_URL + "/api/health", timeout=10).status_code == 200
        except Exception:
            _live_ok = False
    return _live_ok


def pytest_collection_modifyitems(config, items):
    here = Path(__file__).parent
    for item in items:
        if here not in Path(str(item.fspath)).parents:
            continue
        item.add_marker(pytest.mark.api)
        if "writes" in item.keywords and not ALLOW_WRITES:
            item.add_marker(pytest.mark.skip(reason="set AIP_API_ALLOW_WRITES=1"))
        if "live" in item.keywords and not _api_up():
            item.add_marker(pytest.mark.skip(reason=f"API not reachable at {API_URL}"))


# ---------------------------------------------------------------- live
@pytest.fixture(scope="session")
def api() -> httpx.Client:
    headers = {"Accept": "application/json"}
    if SESSION:
        headers["X-AIP-Session"] = SESSION
    if ROLE:
        headers["X-AIP-Role"] = ROLE
    with httpx.Client(base_url=API_URL, headers=headers, timeout=120) as c:
        yield c


@pytest.fixture(scope="session")
def anon_api() -> httpx.Client:
    with httpx.Client(base_url=API_URL, timeout=60) as c:
        yield c


# ---------------------------------------------------------------- offline
class FakeDb:
    """Answers every query with no rows so handlers run without Snowflake."""

    def __getattr__(self, name):
        def _any(*a, **k):
            return []
        return _any


@pytest.fixture(scope="session")
def main_module():
    try:
        import app.main as main  # noqa: WPS433
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"cannot import apps/api app.main: {exc}")
    return main


@pytest.fixture
def client(main_module, monkeypatch):
    from fastapi.testclient import TestClient
    import app.governance as gov

    async def open_door(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(gov, "middleware", open_door)
    main_module.app.dependency_overrides[main_module.current_db] = lambda: FakeDb()
    with TestClient(main_module.app, raise_server_exceptions=False) as c:
        yield c
    main_module.app.dependency_overrides.clear()


@pytest.fixture
def raw_client(main_module):
    """TestClient without dependency overrides (real auth path)."""
    from fastapi.testclient import TestClient
    with TestClient(main_module.app, raise_server_exceptions=False) as c:
        yield c
