"""Copilot workspace API (apps/api/app/copilot_agent_api.py): routes against a fake database, the governance rules
that apply to them, and the publish check that tells the review drawer what each draft would meet."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.governance as gov  # noqa: E402
from app.main import app, current_db  # noqa: E402
from services.governance.policy import no_queue, privilege_for  # noqa: E402

SID = "11111111-2222-3333-4444-555555555555"
SESSION = {"session_id": SID, "title": "t", "status": "ACTIVE", "jira_key": None, "domain_id": None, "model": None,
           "created_by": "ANA", "created_at": "2026-10-13T00:00:00.000+00:00", "updated_at": "2026-10-13T00:00:00.000+00:00"}


def change_row(cid, kind, ops, status="PROPOSED"):
    return {"change_id": cid, "seq": 1, "change_key": f"{kind}:{cid}", "kind": kind, "title": cid, "status": status,
            "operations": json.dumps(ops), "preview": "{}", "result": None, "request_id": None, "updated_at": None}


class FakeDb:
    """Answers a query by the first SQL fragment it contains; records every write."""

    user, role = "ANA", "DATA_ENGINEER"

    def __init__(self, answers=None, rowcount=1):
        self.answers, self.rowcount, self.writes = answers or {}, rowcount, []

    def query(self, sql, params=()):
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return [dict(r) for r in rows]
        return []

    def execute(self, sql, params=()):
        self.writes.append((sql, params))

    def execute_count(self, sql, params=()):
        self.writes.append((sql, params))
        return self.rowcount


@pytest.fixture
def client(monkeypatch):
    """A test client whose routes see `state["db"]`, with governance on and the given privileges."""
    state = {"db": FakeDb(), "privileges": ["AI.USE"], "policies": {}}
    monkeypatch.setattr(gov, "_db_for", lambda headers: state["db"])
    monkeypatch.setattr(gov, "ready", lambda d: True)
    monkeypatch.setattr(gov, "identity", lambda d: {"user": "ANA", "roles": ["DATA_ENGINEER"], "granted": ["DATA_ENGINEER"],
                                                    "privileges": state["privileges"]})
    monkeypatch.setattr(gov, "settings", lambda d: dict(gov.DEFAULT_SETTINGS))
    monkeypatch.setattr(gov, "policies", lambda d: state["policies"])
    app.dependency_overrides[current_db] = lambda: state["db"]
    try:
        yield TestClient(app), state
    finally:
        app.dependency_overrides.pop(current_db, None)


# ---------------------------------------------------------------- governance rules for the copilot's own routes

def test_copilot_routes_need_ai_use_and_are_never_queued():
    for path in ("/api/copilot/sessions", f"/api/copilot/sessions/{SID}/turn", f"/api/copilot/sessions/{SID}/publish",
                 f"/api/copilot/sessions/{SID}/delete", f"/api/copilot/sessions/{SID}/changes/c1/discard"):
        assert privilege_for("POST", path)[0] == "AI.USE", path
        assert no_queue("POST", path), path
    assert privilege_for("GET", f"/api/copilot/sessions/{SID}/publish-check")[0] is None


def test_without_ai_use_the_chat_is_refused_not_queued(client):
    http, state = client
    state["privileges"] = ["REQUEST.CHANGES"]
    state["policies"] = {"AI.USE": {"requires_approval": True, "approver_role": "X", "four_eyes": False, "active": True}}
    res = http.post("/api/copilot/sessions", json={"title": "x"})
    assert res.status_code == 403 and "AI.USE" in res.json()["detail"]
    assert not any("CHANGE_REQUEST" in sql for sql, _ in state["db"].writes)


# ---------------------------------------------------------------- routes

def test_creating_a_conversation_writes_the_session_and_its_eight_steps(client):
    http, state = client
    res = http.post("/api/copilot/sessions", json={"title": "Add loyalty tier", "jira_key": "gdp-7"})
    assert res.status_code == 200 and res.json()["session_id"]
    writes = [sql for sql, _ in state["db"].writes]
    assert sum("COPILOT_SESSION" in s for s in writes) == 1 and sum("COPILOT_PLAN_STEP" in s for s in writes) == 8
    assert state["db"].writes[0][1][2] == "GDP-7"


def test_someone_elses_or_a_deleted_conversation_is_not_found(client):
    http, _ = client
    assert http.get(f"/api/copilot/sessions/{SID}").status_code == 404   # the own-rows filter found nothing


def test_a_turn_needs_a_message_or_answers(client):
    http, state = client
    state["db"] = FakeDb({"SELECT SESSION_ID, TITLE": [SESSION]})
    assert http.post(f"/api/copilot/sessions/{SID}/turn", json={"message": "  "}).status_code == 400


def test_bad_jira_keys_are_refused_before_the_agent_runs(client):
    http, state = client
    state["db"] = FakeDb({"SELECT SESSION_ID, TITLE": [SESSION]})
    res = http.post(f"/api/copilot/sessions/{SID}/turn", json={"message": "hi", "jira_keys": ["not a key"]})
    assert res.status_code == 400 and "Not Jira issue keys" in res.json()["detail"]
    assert not any("COPILOT_EVENT" in sql for sql, _ in state["db"].writes)


def test_only_an_open_draft_can_be_discarded(client):
    http, state = client
    state["db"] = FakeDb({"SELECT SESSION_ID, TITLE": [SESSION]}, rowcount=0)
    assert http.post(f"/api/copilot/sessions/{SID}/changes/c1/discard").status_code == 409


def test_a_conversation_cannot_be_deleted_while_it_publishes(client):
    http, state = client
    state["db"] = FakeDb({"SELECT SESSION_ID, TITLE": [SESSION], "SELECT 1 AS X": [{"x": 1}]})
    assert http.post(f"/api/copilot/sessions/{SID}/delete").status_code == 409
    state["db"] = FakeDb({"SELECT SESSION_ID, TITLE": [SESSION]})
    assert http.post(f"/api/copilot/sessions/{SID}/delete").json() == {"session_id": SID, "deleted": True}
    writes = " ".join(sql for sql, _ in state["db"].writes)
    assert "STATUS = 'DISCARDED'" in writes and "IS_DELETED = TRUE" in writes and "STATUS = 'DELETED'" in writes


def test_publishing_nothing_claimable_is_refused(client):
    http, state = client
    state["db"] = FakeDb({"SELECT SESSION_ID, TITLE": [SESSION]}, rowcount=0)   # already published or discarded
    res = http.post(f"/api/copilot/sessions/{SID}/publish", json={"change_ids": ["c1"]})
    assert res.status_code == 409


# ---------------------------------------------------------------- the publish check

def test_publish_check_reports_what_each_draft_would_meet(client):
    http, state = client
    state["privileges"] = ["AI.USE", "MODEL.EDIT", "STTM.EDIT", "REQUEST.CHANGES"]
    state["policies"] = {"STTM.EDIT": {"requires_approval": True, "approver_role": "STTM_APPROVER", "four_eyes": False,
                                       "allow_self": False, "active": True}}
    state["db"] = FakeDb({"SELECT SESSION_ID, TITLE": [SESSION], "SELECT CHANGE_ID, SEQ": [
        change_row("m1", "MODEL", [{"method": "PUT", "path": "/api/hub/table-models/tm1", "body": {}},
                                   {"method": "POST", "path": "/api/hub/table-models/tm1/versions/{op0.version}/publish",
                                    "body": {"repo_id": "r1"}}]),
        change_row("s1", "STTM", [{"method": "POST", "path": "/api/hub/sttm/c1/edit", "body": {}}]),
        change_row("i1", "INGESTION", [{"method": "POST", "path": "/api/hub/ingestion-control", "body": {}}]),
        change_row("old", "MODEL", [{"method": "PUT", "path": "/api/hub/table-models/tm1", "body": {}}], status="APPLIED"),
    ]})
    checks = http.get(f"/api/copilot/sessions/{SID}/publish-check").json()["checks"]
    assert checks["m1"]["status"] == "ok" and checks["m1"]["privileges"] == ["MODEL.EDIT"]
    assert checks["s1"]["status"] == "approval" and checks["s1"]["approver_role"] == "STTM_APPROVER"
    assert checks["i1"]["status"] == "forbidden" and "SOURCE.CONNECT" in checks["i1"]["reason"]
    assert "old" not in checks   # only open drafts


def test_the_check_and_the_middleware_decide_the_same_way(client):
    http, state = client
    state["privileges"] = ["AI.USE", "RUN.OPERATE"]
    db = FakeDb()
    seen = gov.preview_call(db, "PUT", "/api/config/platform", {"key": "LLM_MODEL", "value": "x"})
    res = http.put("/api/config/platform", json={"key": "LLM_MODEL", "value": "x"})
    assert seen["verdict"] == "FORBID" and res.status_code == 403
    assert gov.preview_call(db, "GET", "/api/hub/table-models")["verdict"] == "ALLOW"
