"""Offline API contract tests (in-process TestClient, no Snowflake)."""
import re

import pytest

from endpoints import INVALID_BODY_POSTS, ROUTER_PROBES

pytestmark = pytest.mark.offline


def _routes(main_module):
    out = set()
    for r in main_module.app.routes:
        for m in getattr(r, "methods", None) or []:
            out.add((m, r.path))
    return out


@pytest.mark.parametrize("method,path", ROUTER_PROBES, ids=[f"{m} {p}" for m, p in ROUTER_PROBES])
def test_every_router_is_mounted(main_module, method, path):
    assert (method, path) in _routes(main_module), f"{method} {path} not registered"


def test_no_duplicate_routes(main_module):
    seen, dups = set(), []
    for r in main_module.app.routes:
        for m in getattr(r, "methods", None) or []:
            key = (m, r.path)
            if key in seen:
                dups.append(key)
            seen.add(key)
    assert not dups, f"duplicate routes: {dups}"


def test_all_api_routes_under_api_prefix(main_module):
    bad = [p for m, p in _routes(main_module)
           if not p.startswith("/api/") and p not in ("/openapi.json", "/docs", "/redoc",
                                                      "/docs/oauth2-redirect")]
    assert not bad, f"routes outside /api: {bad}"


def test_route_count_sanity(main_module):
    assert len(_routes(main_module)) > 500


def test_hub_models_runs_not_shadowed(main_module):
    paths = [r.path for r in main_module.app.routes]
    assert paths.index("/api/hub/models/runs") < paths.index("/api/hub/models/{design_id}")


def test_openapi_schema_generates(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["paths"] and "/api/health" in spec["paths"]


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    for k in ("database", "auth_mode", "build"):
        assert k in body


def test_unknown_route_404(client):
    assert client.get("/api/definitely-not-a-route").status_code == 404


@pytest.mark.parametrize("method,path", [("DELETE", "/api/health"), ("PUT", "/api/health"),
                                         ("PATCH", "/api/runs"), ("DELETE", "/api/knowledge")])
def test_wrong_method_405(client, method, path):
    assert client.request(method, path).status_code == 405


@pytest.mark.parametrize("path", INVALID_BODY_POSTS)
def test_malformed_json_rejected(client, path):
    r = client.post(path, content=b"{not-json", headers={"Content-Type": "application/json"})
    assert 400 <= r.status_code < 500, f"{path} -> {r.status_code}"


@pytest.mark.parametrize("path", INVALID_BODY_POSTS)
def test_empty_body_never_500(client, path):
    r = client.post(path, json={})
    assert r.status_code < 500, f"{path} -> {r.status_code}: {r.text[:300]}"


@pytest.mark.parametrize("path", INVALID_BODY_POSTS)
def test_wrong_types_never_500(client, path):
    r = client.post(path, json={"name": 123, "title": ["x"], "id": {"a": 1}, "user": None})
    assert r.status_code < 500, f"{path} -> {r.status_code}"


def test_validation_error_shape(client):
    r = client.post("/api/auth/login", json={})
    assert r.status_code in (400, 422)
    assert "detail" in r.json()


def test_login_rejected_in_dev_mode(client):
    r = client.post("/api/auth/login", json={"user": "u", "token": "t"})
    if client.get("/api/health").json().get("auth_mode") == "dev":
        assert r.status_code == 400


def test_ops_ingest_requires_signature(raw_client):
    r = raw_client.post("/api/ops/ingest", json={"event": "x"})
    assert r.status_code in (400, 401, 403, 422)


@pytest.mark.parametrize("origin,allowed", [("http://localhost:3000", True),
                                             ("http://127.0.0.1:3001", True),
                                             ("http://evil.example.com", False)])
def test_cors(client, origin, allowed):
    r = client.options("/api/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    got = r.headers.get("access-control-allow-origin")
    assert (got == origin) is allowed


def test_server_timing_header(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    # perf middleware is optional per config; header must be well-formed if present
    st = r.headers.get("server-timing")
    if st:
        assert re.match(r"^[\w-]+(;|,|$)", st)


@pytest.mark.parametrize("role", ["", "NOT_A_REAL_ROLE_XYZ"])
def test_bad_role_header_never_500(client, role):
    r = client.get("/api/runs", headers={"X-AIP-Role": role})
    assert r.status_code < 500 or r.status_code == 503


def test_long_persona_header_tolerated(client):
    r = client.get("/api/health", headers={"X-AIP-Persona": "x" * 500})
    assert r.status_code == 200
