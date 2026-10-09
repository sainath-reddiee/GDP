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
