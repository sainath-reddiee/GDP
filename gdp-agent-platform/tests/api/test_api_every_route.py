"""Source-driven API coverage: one test per registered FastAPI route (method + path).

Offline (TestClient, fake DB, RBAC bypassed): every route resolves - never 404/405 for its own
method - and returns a response (no transport crash).
Live: every GET route answers < 500 with ids discovered from list endpoints (or a dummy id, where
a 4xx is the expected answer); every non-GET route rejects an invalid body with < 500 only when
AIP_API_ALLOW_WRITES=1 (an empty body may be a valid command on action endpoints).
"""
import re

import pytest

MISSING = "00000000-0000-0000-0000-000000000000"
SKIP_LIVE = {"/api/agent/stream", "/api/ops/ingest", "/api/auth/logout"}


def _routes_from_source():
    """Fallback when app.main cannot be imported: parse decorators in apps/api/app/**/*.py."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "apps" / "api" / "app"
    rx = re.compile(r'@\w+\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')
    out = set()
    for f in root.rglob("*.py"):
        for m in rx.finditer(f.read_text(encoding="utf-8", errors="ignore")):
            if m.group(2).startswith("/api/"):
                out.add((m.group(1).upper(), m.group(2)))
    return sorted(out)


def _all_routes():
    try:
        import app.main as main  # noqa: WPS433
    except Exception:
        return _routes_from_source()
    out = []
    for r in main.app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/"):
            continue
        for m in sorted(getattr(r, "methods", None) or []):
            if m in ("HEAD", "OPTIONS"):
                continue
            out.append((m, path))
    return sorted(set(out))


ROUTES = _all_routes()
GET_ROUTES = [p for m, p in ROUTES if m == "GET"]
WRITE_ROUTES = [(m, p) for m, p in ROUTES if m != "GET"]


def _fill(path: str, ids: dict | None = None) -> str:
    ids = ids or {}

    def sub(m):
        name = m.group(1)
        return ids.get(name, MISSING if "id" in name else "x")
    return re.sub(r"\{([^}:]+)(:[^}]+)?\}", sub, path)


def test_route_inventory_loaded():
    assert len(ROUTES) > 500, "could not import apps/api app.main - check the API dependencies"


# ---------------------------------------------------------------- offline
@pytest.mark.offline
@pytest.mark.parametrize("method,path", ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
def test_route_resolves_offline(client, method, path):
    url = _fill(path)
    r = client.request(method, url, json={} if method != "GET" else None)
    assert r.status_code != 405, f"{method} {path}: method not allowed on its own route"
    if r.status_code == 404:
        detail = r.json().get("detail", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        assert detail and detail != "Not Found", f"{method} {path}: route not reached (404 Not Found)"


# ---------------------------------------------------------------- live
@pytest.fixture(scope="session")
def live_ids(api):
    """Map path-param name -> a real id taken from list endpoints."""
    from endpoints import DETAIL_FROM_LIST
    from test_api_live_read import _first_id

    found = {}
    names = {"/api/runs": "run_id", "/api/domains": "domain_id", "/api/knowledge": "knowledge_id",
             "/api/skills": "name", "/api/onboarding-requests": "request_id", "/api/ops/incidents": "incident_id",
             "/api/cases": "case_id", "/api/ops/permanent-fixes": "fix_id", "/api/ops/resolve/failures": "failure_id",
             "/api/ops/resolve/fixes": "issue_id", "/api/sources": "source_id", "/api/hub/domain-models": "model_id",
             "/api/hub/sttm": "catalog_id", "/api/qa/suites": "suite_id", "/api/qa-packs": "pack_id",
             "/api/copilot/sessions": "session_id", "/api/code/repos": "repo_id",
             "/api/skill-proposals": "proposal_id", "/api/qa/tables": "target_table_id"}
    for lst, fields, _ in DETAIL_FROM_LIST:
        try:
            r = api.get(lst)
            rid = _first_id(r.json(), fields) if r.status_code < 400 else None
        except Exception:
            rid = None
        if rid and lst in names:
            found[names[lst]] = rid
    found.setdefault("pipeline", "silver")
    found.setdefault("stage", "profile")
    found.setdefault("kind", "qa")
    return found


@pytest.mark.live
@pytest.mark.parametrize("path", GET_ROUTES)
def test_every_get_route_live(api, live_ids, path):
    if path in SKIP_LIVE or path.endswith((".zip", ".csv")):
        pytest.skip("streaming / binary endpoint")
    r = api.get(_fill(path, live_ids))
    assert r.status_code < 500, f"GET {path} -> {r.status_code}: {r.text[:300]}"


@pytest.mark.live
@pytest.mark.writes
@pytest.mark.parametrize("method,path", WRITE_ROUTES, ids=[f"{m} {p}" for m, p in WRITE_ROUTES])
def test_every_write_route_rejects_garbage_live(api, method, path):
    if path in SKIP_LIVE:
        pytest.skip("special endpoint")
    r = api.request(method, _fill(path), content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code < 500, f"{method} {path} -> {r.status_code}: {r.text[:300]}"
