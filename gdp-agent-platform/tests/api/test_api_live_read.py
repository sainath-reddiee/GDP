"""Live read-only API tests against a running API (AIP_API_URL)."""
import time

import pytest

from endpoints import DETAIL_FROM_LIST, NOT_FOUND, STATIC_GETS

pytestmark = pytest.mark.live
MISSING = "00000000-0000-0000-0000-000000000000"


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("items", "rows", "data", "results", "runs", "domains", "requests", "incidents",
                  "cases", "skills", "sessions", "repos", "proposals", "fixes", "failures", "suites",
                  "packs", "models", "catalogs"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _first_id(payload, fields):
    for row in _rows(payload):
        if isinstance(row, dict):
            for f in fields:
                if row.get(f) not in (None, ""):
                    return str(row[f])
    return None


@pytest.mark.parametrize("path", STATIC_GETS)
def test_get_endpoint_ok(api, path):
    r = api.get(path)
    assert r.status_code < 400, f"GET {path} -> {r.status_code}: {r.text[:300]}"
    assert r.headers.get("content-type", "").startswith("application/json")
    r.json()


@pytest.mark.parametrize("path", STATIC_GETS)
def test_get_endpoint_latency(api, path):
    t = time.monotonic()
    api.get(path)
    assert time.monotonic() - t < 60, f"GET {path} took more than 60s"


@pytest.mark.parametrize(
    "lst,fields,tpl",
    [(l, f, t) for l, f, tpls in DETAIL_FROM_LIST for t in tpls],
    ids=[t for _, _, tpls in DETAIL_FROM_LIST for t in tpls],
)
def test_detail_endpoint_ok(api, lst, fields, tpl):
    listing = api.get(lst)
    assert listing.status_code < 400
    rid = _first_id(listing.json(), fields)
    if not rid:
        pytest.skip(f"no records in {lst}")
    r = api.get(tpl.format(id=rid))
    assert r.status_code < 400, f"GET {tpl} -> {r.status_code}: {r.text[:300]}"


@pytest.mark.parametrize("tpl", NOT_FOUND)
def test_missing_resource_is_4xx(api, tpl):
    r = api.get(tpl.format(x=MISSING))
    assert 400 <= r.status_code < 500, f"GET {tpl} -> {r.status_code}"


def test_health_contract(api):
    body = api.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["auth_mode"] in ("dev", "pat")


def test_me_identifies_user(api):
    me = api.get("/api/auth/me").json()
    assert me, "whoami returned empty"


def test_roles_is_list(api):
    r = api.get("/api/auth/roles").json()
    assert isinstance(r, (list, dict))


def test_pat_mode_requires_session(anon_api, api):
    if api.get("/api/health").json()["auth_mode"] != "pat":
        pytest.skip("API runs in dev mode")
    assert anon_api.get("/api/runs").status_code == 401
    assert anon_api.get("/api/runs", headers={"X-AIP-Session": "bogus"}).status_code == 401


def test_invalid_role_rejected(api):
    r = api.get("/api/runs", headers={"X-AIP-Role": "NO_SUCH_ROLE_ZZZ"})
    assert r.status_code in (200, 403)


@pytest.mark.parametrize("params", [{"limit": 5}, {"offset": 0, "limit": 1}, {"q": "customer"},
                                    {"status": "active"}])
def test_knowledge_filters(api, params):
    r = api.get("/api/knowledge", params=params)
    assert r.status_code < 400
    if "limit" in params:
        assert len(_rows(r.json())) <= params["limit"]


def test_knowledge_search(api):
    r = api.post("/api/knowledge/search", json={"query": "customer"})
    assert r.status_code < 400


def test_search_empty_query(api):
    assert api.get("/api/search", params={"q": ""}).status_code < 500


def test_lineage_trace_unknown_object(api):
    assert api.get("/api/lineage/trace", params={"object": "NO.SUCH.TABLE"}).status_code < 500


def test_pipeline_summaries(api):
    for p in ("silver", "gold"):
        assert api.get(f"/api/pipelines/{p}/summary").status_code < 500


@pytest.mark.parametrize("path", ["/api/runs/{x}/suggestions/profile", "/api/runs/{x}/soda/kit",
                                  "/api/runs/{x}/sttm/preview", "/api/runs/{x}/model/diff"])
def test_run_subresources_missing_run(api, path):
    assert api.get(path.format(x=MISSING)).status_code < 500
