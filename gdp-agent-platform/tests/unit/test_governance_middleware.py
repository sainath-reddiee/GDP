import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402

import app.governance as gov  # noqa: E402
from app.main import app  # noqa: E402


class FakeDb:
    user = "ANA"

    def __init__(self):
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def query(self, sql, params=()):
        return []


def install(monkeypatch, privileges):
    db = FakeDb()
    monkeypatch.setattr(gov, "_db_for", lambda headers: db)
    monkeypatch.setattr(gov, "ready", lambda d: True)
    monkeypatch.setattr(gov, "identity", lambda d: {"user": "ANA", "roles": ["DATA_ENGINEER"], "granted": ["DATA_ENGINEER"],
                                                    "privileges": privileges})
    monkeypatch.setattr(gov, "settings", lambda d: dict(gov.DEFAULT_SETTINGS))
    monkeypatch.setattr(gov, "policies", lambda d: {"STTM.EDIT": {"requires_approval": True, "approver_role": "STTM_APPROVER",
                                                                  "four_eyes": False, "allow_self": False, "active": True}})
    return db


def test_change_without_the_privilege_becomes_a_request(monkeypatch):
    db = install(monkeypatch, ["RUN.OPERATE", "REQUEST.CHANGES"])
    res = TestClient(app).post("/api/runs/run-1/sttm/apply", json={"sttm_line_id": "L1", "transformation": "UPPER(X)"})
    assert res.status_code == 202
    body = res.json()
    assert body["pending_approval"] and body["approver_role"] == "STTM_APPROVER"
    insert = next(p for sql, p in db.executed if "CHANGE_REQUEST" in sql)
    assert insert[1] == "STTM.EDIT" and insert[4] == "run-1" and insert[6] == "/api/runs/run-1/sttm/apply"
    assert '"transformation": "UPPER(X)"' in insert[7] and insert[8] == "ANA"


def test_no_privilege_and_no_policy_is_forbidden(monkeypatch):
    install(monkeypatch, ["AUDIT.VIEW"])
    res = TestClient(app).put("/api/config/platform", json={"key": "LLM_MODEL", "value": "x"})
    assert res.status_code == 403 and "CONFIG.EDIT" in res.json()["detail"]


def test_replay_tokens_are_signed():
    token = gov._replay_token("abc")
    assert gov._valid_replay(token) == "abc"
    assert gov._valid_replay("abc.deadbeef") is None and gov._valid_replay(None) is None


def test_store_edits_queue_even_for_holders_and_are_never_self_approved(monkeypatch):
    db = install(monkeypatch, ["STTM.EDIT", "RUN.OPERATE"])
    res = TestClient(app).post("/api/hub/sttm/c1/edit", json={"base_version": 1, "edits": [{"target_column": "A", "transformation": "X"}]})
    assert res.status_code == 202 and res.json()["approver_role"] == "STTM_APPROVER"
    insert = next(p for sql, p in db.executed if "CHANGE_REQUEST" in sql)
    assert insert[1] == "STTM.EDIT" and insert[10] is False  # ALLOW_SELF


def test_store_edit_without_a_policy_uses_the_store_approver(monkeypatch):
    db = install(monkeypatch, ["SODA.EDIT", "RUN.OPERATE"])
    monkeypatch.setattr(gov, "policies", lambda d: {})
    res = TestClient(app).post("/api/dq/run-checks/decisions", json={"run_id": "r1", "decisions": []})
    assert res.status_code == 202 and res.json()["approver_role"] == "DQ_APPROVER"
    assert any("CHANGE_REQUEST" in sql for sql, _ in db.executed)


def test_workbench_routes_are_never_queued(monkeypatch):
    """MODEL.APPROVE has an approval policy, but deciding a model version is itself the review: a holder passes,
    anyone else is refused instead of being queued."""
    policy = {"MODEL.APPROVE": {"approver_role": "MODEL_APPROVER", "active": True, "requires_approval": True,
                                "four_eyes": True}}
    install(monkeypatch, ["MODEL.APPROVE", "MODEL.EDIT", "RUN.OPERATE"])
    monkeypatch.setattr(gov, "policies", lambda d: policy)
    res = TestClient(app, raise_server_exceptions=False).post("/api/hub/domain-models/m1/versions/2/approve", json={})
    assert res.status_code != 202
    db = install(monkeypatch, ["REQUEST.CHANGES", "RUN.OPERATE"])
    monkeypatch.setattr(gov, "policies", lambda d: policy)
    res = TestClient(app).post("/api/hub/domain-models/m1/versions/2/approve", json={})
    assert res.status_code == 403 and "never queued" in res.json()["detail"]
    assert not any("CHANGE_REQUEST" in sql for sql, _ in db.executed)


def test_store_queue_can_be_switched_off(monkeypatch):
    install(monkeypatch, ["STTM.EDIT", "RUN.OPERATE"])
    monkeypatch.setattr(gov, "settings", lambda d: {**gov.DEFAULT_SETTINGS, "STORE_EDITS_REQUIRE_APPROVAL": False})
    # passes the middleware (the handler itself then answers, here with a 4xx/5xx for the fake database)
    res = TestClient(app, raise_server_exceptions=False).post("/api/hub/sttm/c1/edit", json={"base_version": 1, "edits": []})
    assert res.status_code != 202


def test_run_pages_keep_the_normal_rules(monkeypatch):
    install(monkeypatch, ["STTM.EDIT", "RUN.OPERATE"])
    res = TestClient(app, raise_server_exceptions=False).post(
        "/api/runs/run-1/sttm/apply", json={"sttm_line_id": "L1", "transformation": "UPPER(X)"})
    assert res.status_code != 202
