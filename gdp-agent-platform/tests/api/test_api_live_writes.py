"""Live write lifecycles (create -> read -> update -> retire/delete).

Run only with AIP_API_ALLOW_WRITES=1 against a DEV database. Every record is tagged UI-AUTOTEST
and cleaned up in a finally block.
"""
import time
import uuid

import pytest

pytestmark = [pytest.mark.live, pytest.mark.writes]
TAG = f"API-AUTOTEST-{int(time.time())}"


def _id(resp, *keys):
    body = resp.json()
    for k in keys:
        if isinstance(body, dict) and body.get(k):
            return str(body[k])
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, dict):
                for k in keys:
                    if v.get(k):
                        return str(v[k])
    raise AssertionError(f"no id in {body}")


def _any_domain(api):
    rows = api.get("/api/domains").json()
    rows = rows if isinstance(rows, list) else rows.get("items") or rows.get("domains") or []
    if not rows:
        pytest.skip("no domains to attach knowledge to")
    return rows[0].get("domain_id") or rows[0].get("id")


def test_knowledge_lifecycle(api):
    domain = _any_domain(api)
    r = api.post("/api/knowledge", json={"domain_id": domain, "knowledge_type": "glossary",
                                         "title": TAG, "content": "created by API test"})
    assert r.status_code < 400, r.text
    kid = _id(r, "knowledge_id", "id")
    try:
        assert any(TAG in str(x) for x in [api.get("/api/knowledge", params={"q": TAG}).json()])
        u = api.put(f"/api/knowledge/{kid}", json={"title": TAG + " v2", "content": "updated",
                                                     "change_note": "api test"})
        assert u.status_code < 400, u.text
        assert api.get(f"/api/knowledge/{kid}/versions").status_code < 400
        assert api.get(f"/api/knowledge/{kid}/history").status_code < 400
        assert api.post(f"/api/knowledge/{kid}/verify", json={}).status_code < 400
    finally:
        assert api.post(f"/api/knowledge/{kid}/retire", json={}).status_code < 400
    assert api.post(f"/api/knowledge/{kid}/restore", json={}).status_code < 400
    api.post(f"/api/knowledge/{kid}/retire", json={})


def test_ticket_view_lifecycle(api):
    r = api.post("/api/tickets/views", json={"name": TAG, "filters": {}})
    assert r.status_code < 400, r.text
    vid = _id(r, "view_id", "id")
    assert any(TAG in str(v) for v in [api.get("/api/tickets/views").json()])
    assert api.delete(f"/api/tickets/views/{vid}").status_code < 400


def test_copilot_session_lifecycle(api):
    r = api.post("/api/copilot/sessions", json={"title": TAG})
    assert r.status_code < 400, r.text
    sid = _id(r, "session_id", "id")
    assert api.get(f"/api/copilot/sessions/{sid}").status_code < 400
    assert api.post(f"/api/copilot/sessions/{sid}/delete", json={}).status_code < 400


def test_ops_team_lifecycle(api):
    r = api.post("/api/ops/teams", json={"name": TAG})
    assert r.status_code < 400, r.text
    tid = _id(r, "team_id", "id")
    try:
        assert api.put(f"/api/ops/teams/{tid}", json={"name": TAG + "-2"}).status_code < 400
    finally:
        assert api.delete(f"/api/ops/teams/{tid}").status_code < 400


def test_skill_category_lifecycle(api):
    r = api.post("/api/skills/categories", json={"name": TAG})
    assert r.status_code < 400, r.text
    cid = _id(r, "category_id", "id")
    try:
        assert api.put(f"/api/skills/categories/{cid}", json={"name": TAG + "-2"}).status_code < 400
    finally:
        assert api.delete(f"/api/skills/categories/{cid}").status_code < 400


def test_run_create_archive_restore(api):
    sources = api.get("/api/sources").json()
    sources = sources if isinstance(sources, list) else sources.get("items") or []
    if not sources:
        pytest.skip("no sources")
    r = api.post("/api/runs", json={"source_id": sources[0].get("source_id") or sources[0].get("id"),
                                    "name": TAG})
    assert r.status_code < 400, r.text
    rid = _id(r, "run_id", "id")
    assert api.get(f"/api/runs/{rid}").status_code < 400
    assert api.post(f"/api/runs/{rid}/archive", json={}).status_code < 400
    assert api.post(f"/api/runs/{rid}/restore", json={}).status_code < 400
    api.post(f"/api/runs/{rid}/archive", json={})


def test_knowledge_policy_roundtrip(api):
    current = api.get("/api/config/knowledge-policy").json()
    assert api.put("/api/config/knowledge-policy", json=current).status_code < 400
    assert api.get("/api/config/knowledge-policy").json() == current


def test_onboarding_request_lifecycle(api):
    r = api.post("/api/onboarding-requests", json={"title": TAG, "intent": "new",
                                                   "description": "api test"})
    assert r.status_code < 400, r.text
    rid = _id(r, "request_id", "id")
    assert api.post(f"/api/onboarding-requests/{rid}/comment", json={"body": "hi"}).status_code < 400
    assert api.post(f"/api/onboarding-requests/{rid}/cancel", json={"reason": "test"}).status_code < 400


def test_governance_request_unknown_id(api):
    r = api.post(f"/api/governance/requests/{uuid.uuid4()}/approve", json={})
    assert 400 <= r.status_code < 500
