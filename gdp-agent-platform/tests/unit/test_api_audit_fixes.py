"""Backend audit fixes: atomic writes, status checks before replay, 400s instead of 500s, and safe caches."""

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402  (loads the routers first, so code_api is not imported half-way)
import app.code_api as code_api  # noqa: E402
import app.domains_api as domains_api  # noqa: E402
import app.governance as gov  # noqa: E402
import app.jira_api as jira_api  # noqa: E402
from app.db import transaction  # noqa: E402


class FakeDb:
    """Answers queries by the first matching SQL fragment; records every statement."""

    user, role = "ANA", "DATA_ENGINEER"

    def __init__(self, answers=None, rowcount=1, fail_on=None):
        self.answers, self.rowcount, self.fail_on = answers or {}, rowcount, fail_on
        self.executed = []

    def query(self, sql, params=()):
        self.executed.append(sql)
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return [dict(r) for r in rows]
        return []

    def execute(self, sql, params=()):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom")
        self.executed.append(sql)

    def execute_count(self, sql, params=()):
        self.executed.append(sql)
        return self.rowcount


# --------------------------------------------------------------------------- db helpers


def test_transaction_commits_or_rolls_back():
    db = FakeDb()
    with transaction(db):
        db.execute("UPDATE A")
    assert db.executed == ["BEGIN", "UPDATE A", "COMMIT"]
    db = FakeDb(fail_on="INSERT")
    with pytest.raises(RuntimeError):
        with transaction(db):
            db.execute("UPDATE A")
            db.execute("INSERT B")
    assert db.executed == ["BEGIN", "UPDATE A", "ROLLBACK"]


def test_put_config_is_one_transaction():
    db = FakeDb()
    main._put_config(db, "RULES", {"a": 1}, "d")
    assert db.executed[0] == "BEGIN" and db.executed[-1] == "COMMIT" and len(db.executed) == 4


# --------------------------------------------------------------------------- governance


def _who(monkeypatch, user="ANA"):
    monkeypatch.setattr(gov, "identity", lambda d: {"user": user, "roles": ["X"], "granted": ["X"], "privileges": ["*"]})
    monkeypatch.setattr(gov, "_event", lambda *a, **k: None)


def test_cancel_and_reject_need_a_pending_row(monkeypatch):
    _who(monkeypatch)
    row = {"request_id": "q1", "requested_by": "ANA", "status": "PENDING", "payload": None, "result": None,
           "approver_role": "X", "allow_self": True}
    db = FakeDb({"FROM GOVERNANCE.CHANGE_REQUEST": [row]}, rowcount=0)
    with pytest.raises(HTTPException) as err:
        gov.cancel_request("q1", db)
    assert err.value.status_code == 409
    assert any("AND STATUS = 'PENDING'" in s for s in db.executed if s.startswith("UPDATE"))
    monkeypatch.setattr(gov, "can_approve", lambda *a: (True, ""))
    monkeypatch.setattr(gov, "settings", lambda d: dict(gov.DEFAULT_SETTINGS))
    with pytest.raises(HTTPException) as err:
        gov.reject_request("q1", gov.Decision(note="no"), db)
    assert err.value.status_code == 409
    ok = FakeDb({"FROM GOVERNANCE.CHANGE_REQUEST": [row]}, rowcount=1)
    assert gov.cancel_request("q1", ok)["status"] == "CANCELLED"


def test_multipart_upload_needing_approval_is_refused_not_queued(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(gov, "_db_for", lambda headers: db)
    monkeypatch.setattr(gov, "ready", lambda d: True)
    monkeypatch.setattr(gov, "identity", lambda d: {"user": "ANA", "roles": [], "granted": [], "privileges": ["REQUEST.CHANGES"]})
    monkeypatch.setattr(gov, "settings", lambda d: dict(gov.DEFAULT_SETTINGS))
    monkeypatch.setattr(gov, "privilege_for", lambda m, p, b: ("SOURCE.EDIT", "Upload", None))
    monkeypatch.setattr(gov, "decide", lambda *a: ("REQUEST", ""))
    monkeypatch.setattr(gov, "policies", lambda d: {"SOURCE.EDIT": {"requires_approval": True, "approver_role": "ADMIN",
                                                                    "four_eyes": False, "allow_self": False, "active": True}})
    res = TestClient(main.app).post("/api/sources/s1/upload", files={"files": ("a.csv", b"x,y\n1,2\n", "text/csv")})
    assert res.status_code == 403 and "ADMIN" in res.json()["detail"]
    assert not any("CHANGE_REQUEST" in s for s in db.executed)


# --------------------------------------------------------------------------- endpoints and helpers


def test_unauthenticated_endpoints_now_need_a_session():
    for fn in (main.ingest_job, main.cancel_ingest_job, main.oracle_env_check):
        assert "db" in inspect.signature(fn).parameters
    assert not inspect.iscoroutinefunction(main.upload_external_files)


def test_legacy_dbt_plan_keeps_its_stored_location():
    prior = {"git_repository": "DB.CODEGEN.HAND", "origin": "https://github.com/acme/x", "project_dir": "analytics"}
    db = FakeDb({"FROM CORE.WORKFLOW_RUN": [{"domain_id": "d1"}],
                 "FROM KNOWLEDGE.DOMAIN_KNOWLEDGE": [{"content_json": json.dumps(prior)}]})
    out = main._resolve_dbt_payload(db, "r1", {"origin": "https://evil/x", "git_repository": "X.Y.Z", "push": True})
    assert out["git_repository"] == "DB.CODEGEN.HAND" and out["origin"] == "https://github.com/acme/x"
    assert out["project_dir"] == "analytics" and out["push"] is True


def test_run_cache_is_per_caller_and_pruned():
    main._run_cache.clear()
    a, b = FakeDb(), FakeDb()
    b.user = "BOB"
    assert main._cached_run(a, "r1", lambda: {"who": "a"}) == {"who": "a"}
    assert main._cached_run(b, "r1", lambda: {"who": "b"}) == {"who": "b"}
    assert main._cached_run(a, "r1", lambda: {"who": "x"}) == {"who": "a"}
    main._run_cache[("OLD", "", "r9")] = (0.0, {})
    main._cached_run(a, "r2", lambda: {})
    assert ("OLD", "", "r9") not in main._run_cache
    main._drop_run("r1")
    assert not any(k[2] == "r1" for k in main._run_cache)


def test_update_repo_rejects_a_bad_folder_before_writing():
    repo = {"repo_id": "r1", "name": "DEMO", "branch": "main", "git_repository": "DB.CODE.DEMO",
            "git_url": "https://github.com/a/b", "domain_ids": "[]", "include_globs": "[]", "exclude_globs": "[]",
            "kind": "DBT", "enabled": True, "created_objects": None,
            "stats": json.dumps({"dbt_project_roots": [{"root": "analytics"}]})}
    db = FakeDb({"FROM CODE.REPO WHERE REPO_ID": [repo]})
    with pytest.raises(HTTPException) as err:
        code_api.update_repo("r1", code_api.RepoUpdate(domain_ids=["d1"], dbt_project_dir="nope"), db)
    assert err.value.status_code == 400 and not any(s.startswith("UPDATE") for s in db.executed)


def test_blank_owner_is_a_400_before_any_write():
    db = FakeDb({"FROM KNOWLEDGE.DOMAIN_REGISTRY": [{"domain_id": "d1", "config": "{}"}]})
    body = domains_api.Members(members=[domains_api.Member(user="  ", role="OWNER")])
    with pytest.raises(HTTPException) as err:
        domains_api.set_members("d1", body, db)
    assert err.value.status_code == 400 and not any(s.startswith(("DELETE", "INSERT")) for s in db.executed)


def test_bad_jira_key_is_a_400():
    with pytest.raises(HTTPException) as err:
        jira_api._key("not a key")
    assert err.value.status_code == 400
    assert jira_api._key("ab-12") == "AB-12"


def test_qa_delete_unknown_test_is_404():
    with pytest.raises(HTTPException) as err:
        main.qa_delete("r1", "t1", FakeDb())
    assert err.value.status_code == 404


def test_create_domain_does_not_return_a_deleted_one():
    db = FakeDb({"FROM KNOWLEDGE.DOMAIN_REGISTRY": [{"domain_id": "d1", "domain_name": "Sales", "active_flag": False}]})
    with pytest.raises(HTTPException) as err:
        main.create_domain(main.CreateDomain(domain_name="sales"), db)
    assert err.value.status_code == 409
    live = FakeDb({"FROM KNOWLEDGE.DOMAIN_REGISTRY": [{"domain_id": "d1", "domain_name": "Sales", "active_flag": True}]})
    assert main.create_domain(main.CreateDomain(domain_name="sales"), live) == {
        "domain": {"domain_id": "d1", "domain_name": "Sales"}, "created": False}


def test_restore_domain_only_brings_back_what_the_delete_retired(monkeypatch):
    config = {"deleted_at": "2026-01-01", "retired_knowledge": ["k1", "k2"]}
    db = FakeDb({"FROM KNOWLEDGE.DOMAIN_REGISTRY": [{"domain_id": "d1", "domain_name": "S", "active_flag": False,
                                                     "config": json.dumps(config)}]})
    monkeypatch.setattr(main, "_domain_snapshot", lambda *a, **k: None)
    main.restore_domain("d1", db)
    knowledge = [s for s in db.executed if "UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE" in s]
    assert len(knowledge) == 1 and "FLATTEN" in knowledge[0]
    assert db.executed[0] != "BEGIN" and "BEGIN" in db.executed and db.executed[-1] == "COMMIT"


def test_soda_modified_decision_for_unknown_check_is_400():
    body = main.SodaDecisions(decisions=[{"expectation_id": "e9", "decision": "MODIFIED", "definition": {"kind": "x"}}])
    with pytest.raises(HTTPException) as err:
        main.save_soda("r1", body, FakeDb())
    assert err.value.status_code == 400


def test_put_intent_for_a_missing_run_is_404():
    with pytest.raises(HTTPException) as err:
        main.put_run_intent("r1", main.IntentPatch(), FakeDb())
    assert err.value.status_code == 404


def test_list_runs_only_falls_back_before_v006(monkeypatch):
    class Broken(FakeDb):
        def query(self, sql, params=()):
            if "TOTAL_MATCHES" in sql:
                raise RuntimeError("SQL compilation error: Object 'SOURCE.SOURCE_OBJECT' does not exist")
            return []

    with pytest.raises(HTTPException):
        main.list_runs(db=Broken())
