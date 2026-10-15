"""Source control (V043): the shared pull request table and its sync, publishing approved STTM versions, and the
Approvals before/after preview. Snowflake and GitHub are replaced by fakes."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.dbt import github, publish  # noqa: E402
from services.git import roundtrip  # noqa: E402
from services.governance import policy  # noqa: E402
from services.governance import preview as pv  # noqa: E402
from test_dq_suites import Session  # noqa: E402

MIGRATION = ROOT / "snowflake" / "database" / "migrations" / "V043__source_control.sql"


def entry(version=3, status="APPROVED", **extra):
    return {"catalog_id": "c1", "version": version, "status": status, "target_entity": "SILVER.SALES.CUSTOMER",
            "domain_name": "Sales", "domain_id": "d1", "source_system": "CRM",
            "table_design": {"grain": "one row per customer", "business_keys": ["CUSTOMER_ID"]},
            "lines": [{"target_column": "CUSTOMER_ID", "source_table": "STG_CUSTOMER", "source_column": "ID",
                       "mapping_type": "DIRECT", "target_datatype": "NUMBER"},
                      {"target_column": "EMAIL", "source_table": "STG_CUSTOMER", "source_column": "EMAIL",
                       "mapping_type": "DIRECT", "target_datatype": "VARCHAR"},
                      {"target_column": "FAX", "source_table": "STG_CUSTOMER", "source_column": "FAX",
                       "mapping_type": "DIRECT", "target_datatype": "VARCHAR"}], **extra}


# ---------------------------------------------------------------- migration


def test_migration_creates_the_table_and_backfills_each_kind_once():
    sql = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS {{database}}.CORE.GIT_PR" in sql
    assert "PRIMARY KEY (KIND, ASSET_ID, VERSION)" in sql
    for kind, source in (("MODEL", "MODEL_DESIGNS"), ("DQ_SUITE", "DQ_SUITE_VERSION"), ("QA_PACK", "DOMAIN_QA_PACKS")):
        assert f"SELECT '{kind}'" in sql and f"GOVERNANCE.{source}" in sql
        assert f"G.KIND = '{kind}'" in sql  # re-running inserts nothing twice
    assert "D.MODEL_ID IS NOT NULL" in sql  # legacy hub designs have no pull request to track


# ---------------------------------------------------------------- round trip


def test_outcome():
    assert roundtrip.outcome({"status": "PUBLISHED", "pull_request": {"url": "u", "number": 4}}) == ("OPEN", {"url": "u", "number": 4})
    assert roundtrip.outcome({"status": "NO_CHANGES"})[0] == "NO_CHANGES"
    assert roundtrip.outcome({"status": "FAILED", "detail": "403"})[0] == "FAILED"


def test_record_merges_one_row_per_version_and_never_raises():
    s = Session()
    ok = roundtrip.record(s, "STTM", "c1", 3, {"status": "PUBLISHED", "base_branch": "main",
                                                "pull_request": {"url": "https://github.com/a/b/pull/8", "number": 8}},
                          asset_name="SILVER.SALES.CUSTOMER", domain_id="d1", repo_id="r1", branch="feature/sttm-customer-v3",
                          title="STTM v3", files=["b.csv", "a.json"])
    assert ok
    sql, params = s.find("MERGE INTO CORE.GIT_PR")[0]
    assert params[:3] == ["STTM", "c1", 3] and params[8:11] == ["8", "https://github.com/a/b/pull/8", "OPEN"]
    assert json.loads(params[12]) == ["a.json", "b.csv"] and params[13] == ""
    failed = Session()
    roundtrip.record(failed, "MODEL", "m1", 2, {"status": "FAILED", "detail": "no write access"})
    assert failed.find("MERGE INTO")[0][1][10:] == ["FAILED", "", "[]", "no write access"]

    class Broken(Session):
        def sql(self, sql, params=None):
            raise RuntimeError("GIT_PR does not exist")
    assert roundtrip.record(Broken(), "QA_PACK", "p1", 1, {"status": "PUBLISHED"}) is False
    with pytest.raises(AssertionError):
        roundtrip.record(Session(), "DBT", "x", 1, {})


def test_sync_applies_each_state_to_the_table_and_the_asset():
    open_prs = [{"KIND": k, "ASSET_ID": a, "VERSION": v, "ASSET_NAME": a, "PR_NUMBER": n, "GIT_URL": "https://github.com/a/b"}
                for k, a, v, n in [("MODEL", "m1", 3, 7), ("DQ_SUITE", "s1", 2, 8), ("QA_PACK", "p1", 4, 9), ("STTM", "c1", 1, 10),
                                   ("MODEL", "m2", 1, 11)]]
    s = Session({"FROM CORE.GIT_PR G JOIN CORE.REPO": open_prs})
    states = {7: "MERGED", 8: "CLOSED", 9: "MERGED", 10: "OPEN"}

    def status(url, number):
        if number not in states:
            raise github.PublishError("read pull request", 404, {"message": "gone"})
        return {"state": states[number], "merge_commit_sha": f"sha{number}" if states[number] == "MERGED" else None}

    out = roundtrip.sync(s, {}, status)
    assert out["checked"] == 4 and out["open"] == 1 and len(out["errors"]) == 1 and out["errors"][0]["asset_id"] == "m2"
    assert [m["asset_id"] for m in out["merged"]] == ["m1", "p1"] and out["closed"][0]["kind"] == "DQ_SUITE"
    assert "DATEADD('second', -60" in s.find("FROM CORE.GIT_PR G JOIN CORE.REPO")[0][0]
    model = s.find("UPDATE CORE.MODEL_DESIGNS SET PR_STATE")[0]
    assert "STATUS = 'PUBLISHED'" in model[0] and model[1] == ["MERGED", "sha7", "m1", 3]
    suite = s.find("UPDATE CORE.DQ_SUITE_VERSION SET PR_STATE")[0]
    assert "STATUS = 'PUBLISHED'" not in suite[0] and suite[1][0] == "CLOSED"
    pack = s.find("UPDATE CORE.DOMAIN_QA_PACKS SET PR_STATE")[0]
    assert "STATUS" not in pack[0].split("WHERE")[0].replace("PR_STATE", "")  # a pack version stays APPROVED
    assert not s.find("STTM_CATALOG")  # STTM lives in GIT_PR only
    assert len(s.find("UPDATE CORE.GIT_PR SET STATE")) == 4
    one = Session()
    roundtrip.sync(one, {"kind": "STTM", "asset_id": "c1", "force": True}, status)
    sql, params = one.calls[0]
    assert "DATEADD" not in sql and params == ["STTM", "c1"]
    with pytest.raises(AssertionError):
        roundtrip.sync(Session(), {"kind": "NOPE"}, status)


def test_asset_syncs_mirror_into_the_table(monkeypatch):
    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(github, "urllib_request", lambda token: "req")
    monkeypatch.setattr(github, "pull_status", lambda req, origin, number: {"state": "MERGED", "merge_commit_sha": "abc"})
    s = Session({"FROM CORE.MODEL_DESIGNS D JOIN CORE.REPO": [{"MODEL_ID": "m1", "VERSION": 3, "PR_NUMBER": 7,
                                                                      "GIT_URL": "https://github.com/a/b"}]})
    publish.sync_model_prs(s, {"model_id": "m1"})
    assert s.find("UPDATE CORE.GIT_PR SET STATE")[0][1] == ["MERGED", "abc", "MODEL", "m1", 3]


def test_list_filters_shape_and_counts():
    sql, params = roundtrip.list_sql("STTM", "open", "d1", " cust ")
    assert "G.KIND = %s" in sql and "G.STATE IN ('OPEN', 'DRAFT')" in sql and "G.DOMAIN_ID = %s" in sql
    assert params == ("STTM", "d1", "%cust%", "%cust%", "%cust%")
    assert "G.STATE IN ('FAILED', 'CLOSED')" in roundtrip.list_sql(state="attention")[0]
    for bad in ({"kind": "X"}, {"state": "WEIRD"}):
        with pytest.raises(AssertionError):
            roundtrip.list_sql(**bad)
    row = roundtrip.shape({"KIND": "DQ_SUITE", "ASSET_ID": "s1", "VERSION": "2", "PR_NUMBER": 5, "STATE": "OPEN",
                           "FILES": '["dq/a.yml"]'})
    assert row["label"] == "Data quality suite" and row["files"] == ["dq/a.yml"] and row["href"].endswith("suite=s1")
    assert roundtrip.shape({"kind": "STTM", "asset_id": "c1", "version": 3, "pr_number": None})["href"] == "/dashboard/sttm/c1?version=3"
    got = roundtrip.counts([{"state": s} for s in ("OPEN", "DRAFT", "MERGED", "FAILED", "CLOSED", "NO_CHANGES")])
    assert got == {"total": 6, "open": 2, "merged": 1, "attention": 2, "no_changes": 1}


# ---------------------------------------------------------------- STTM publish


def test_sttm_files():
    files = publish.sttm_files(entry())
    assert sorted(files) == ["sttm/sales/customer.csv", "sttm/sales/customer.design.json"]
    csv_text = files["sttm/sales/customer.csv"]
    assert csv_text.splitlines()[0].startswith("target_column") and "CUSTOMER_ID" in csv_text
    design = json.loads(files["sttm/sales/customer.design.json"])
    assert design["table_design"]["grain"] == "one row per customer" and design["version"] == 3


def _sttm_session(status="APPROVED"):
    e = entry(status=status)
    return Session({"FROM CORE.STTM_CATALOG WHERE CATALOG_ID = ? AND VERSION = ?": [
                        {"CATALOG_ID": "c1", "VERSION": 3, "DOMAIN_ID": "d1", "DOMAIN_NAME": "Sales", "SOURCE_SYSTEM": "CRM",
                         "TARGET_ENTITY": e["target_entity"], "STATUS": status, "TABLE_DESIGN": json.dumps(e["table_design"]),
                         "LINES": json.dumps(e["lines"])}],
                    "FROM CORE.REPO WHERE REPO_ID": [{"GIT_URL": "https://github.com/a/b", "BRANCH": "main", "PROVIDER": "GITHUB"}]})


def test_publish_sttm_pushes_the_approved_version_and_records_it(monkeypatch):
    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(github, "urllib_request", lambda token: "req")
    seen = {}

    def fake_publish(request, origin, base, head, files, **kw):
        seen.update(files=files, head=head, base=base, title=kw["title"])
        return {"status": "PUBLISHED", "pull_request": {"url": "https://github.com/a/b/pull/12", "number": 12}}
    monkeypatch.setattr(github, "publish", fake_publish)
    s = _sttm_session()
    req = {"catalog_id": "c1", "version": 3, "repo_id": "r1", "head": "feature/sttm-customer-v3"}
    out = publish.publish_dbt_pr(s, "", json.dumps({"sttm": req}))
    assert out["status"] == "PUBLISHED" and out["recorded"] and seen["base"] == "main"
    assert sorted(seen["files"]) == out["files"] == ["sttm/sales/customer.csv", "sttm/sales/customer.design.json"]
    params = s.find("MERGE INTO CORE.GIT_PR")[0][1]
    assert params[:4] == ["STTM", "c1", 3, "SILVER.SALES.CUSTOMER"] and params[10] == "OPEN"
    with pytest.raises(AssertionError, match="feature/sttm-"):
        publish.publish_sttm(_sttm_session(), {**req, "head": "feature/dq-x"})
    with pytest.raises(AssertionError, match="only an approved mapping version"):
        publish.publish_sttm(_sttm_session("REVIEW"), req)


def test_sync_git_prs_uses_the_table_once_deployed(monkeypatch):
    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(github, "urllib_request", lambda token: "req")
    monkeypatch.setattr(github, "pull_status", lambda req, origin, number: {"state": "MERGED", "merge_commit_sha": "x"})
    s = Session({"FROM CORE.GIT_PR G JOIN CORE.REPO": [{"KIND": "STTM", "ASSET_ID": "c1", "VERSION": 1, "PR_NUMBER": 3,
                                                               "GIT_URL": "https://github.com/a/b"}]})
    out = publish.sync_git_prs(s, {})
    assert out["source_control"] and out["merged"][0]["asset_id"] == "c1"


# ---------------------------------------------------------------- governance


def test_publish_and_sync_routes():
    assert policy.privilege_for("POST", "/api/hub/sttm/c1/publish")[0] == "STTM.EDIT"
    assert policy.no_queue("POST", "/api/hub/sttm/c1/publish") and not policy.store_queue("POST", "/api/hub/sttm/c1/publish")
    assert policy.store_queue("POST", "/api/hub/sttm/c1/edit")  # editing a mapping still waits for its approver
    assert policy.privilege_for("POST", "/api/source-control/sync")[0] is None
    assert policy.privilege_for("GET", "/api/source-control/prs")[0] is None


# ---------------------------------------------------------------- Approvals preview


def test_kind_for():
    assert pv.kind_for("POST", "/api/hub/sttm/c1/edit") == "STTM_EDIT"
    assert pv.kind_for("PUT", "/api/dq/schedules") == "DQ_SCHEDULE"
    assert pv.kind_for("DELETE", "/api/dq/schedules?table=A.B.C") == "DQ_SCHEDULE_DELETE"
    assert pv.kind_for("POST", "/api/dq/run-checks/decisions") == "RUN_CHECK_DECISIONS"
    assert pv.kind_for("POST", "/api/runs/r1/state") == "GENERIC"


def test_generic_preview_labels_fields_and_parameters():
    out = pv.generic("POST", "/api/runs/r1/state?force=true", {"to_state": "MAPPING_REVIEW", "reason": "redo",
                                                                "options": {"notify": True, "note": "x" * 500}})
    assert out["headline"] == "POST /api/runs/r1/state"
    params, values = out["sections"]
    assert params["rows"][0] == {"label": "force", "before": None, "after": "true", "change": "set", "note": None}
    labels = {r["label"]: r["after"] for r in values["rows"]}
    assert labels["to state"] == "MAPPING_REVIEW" and labels["options.notify"] == "yes" and labels["options.note"].endswith("…")
    assert pv.generic("POST", "/api/x", None)["warnings"]


def test_sttm_preview_shows_lines_design_and_joins():
    base = entry()
    payload = {"base_version": 3, "edits": [{"target_column": "EMAIL", "transformation": "LOWER(EMAIL)"}],
               "add_lines": [{"target_column": "PHONE", "source_table": "STG_CUSTOMER", "source_column": "PHONE"}],
               "remove_lines": ["FAX"], "design": {"grain": "one row per customer and day"}, "note": "align with CRM"}
    out = pv.sttm_preview(base, base, payload, pending=True)
    assert out["kind"] == "STTM_EDIT" and not out["warnings"] and out["link"]["href"] == "/dashboard/sttm/c1"
    sections = {s["title"]: s["rows"] for s in out["sections"]}
    assert sections["Lines changed"][0] == {"label": "EMAIL · transformation", "before": None, "after": "LOWER(EMAIL)",
                                             "change": "changed", "note": None}
    assert sections["Lines added"][0]["label"] == "PHONE" and "STG_CUSTOMER.PHONE" in sections["Lines added"][0]["after"]
    assert sections["Lines removed"][0]["before"].startswith("STG_CUSTOMER.FAX")
    assert sections["Table design"][0]["before"] == "one row per customer"
    assert sections["Requester's note"][0]["after"] == "align with CRM"
    assert "1 added, 1 removed, 1 changed" in out["headline"]


def test_sttm_preview_warns_when_stale_or_no_longer_valid():
    stale = pv.sttm_preview(entry(), entry(version=4), {"base_version": 3, "remove_lines": ["FAX"]}, pending=True)
    assert "Version 4 was saved" in stale["warnings"][0]
    assert not pv.sttm_preview(entry(), entry(version=4), {"base_version": 3, "remove_lines": ["FAX"]}, pending=False)["warnings"]
    broken = pv.sttm_preview(entry(), entry(), {"base_version": 3, "edits": [{"target_column": "NOPE", "transformation": "X"}]},
                             pending=True)
    assert "no longer applies" in broken["warnings"][-1] and broken["kind"] == "STTM_EDIT"


def test_sttm_preview_join_plan(monkeypatch):
    graph = {"driving_table": "STG_CUSTOMER", "joins": [{"left_table": "STG_CUSTOMER", "right_table": "STG_ADDRESS",
                                                          "join_type": "LEFT", "condition": "STG_CUSTOMER.ID = STG_ADDRESS.CUSTOMER_ID"}]}
    monkeypatch.setattr("services.catalog.sttm.apply_joins",
                        lambda design, joins, cols, lines: ({**design, "join_graph": graph, "driving_table": "STG_CUSTOMER"}, lines))
    out = pv.sttm_preview(entry(), entry(), {"base_version": 3, "joins": {"driving_table": "STG_CUSTOMER", "joins": []}}, True)
    rows = {r["label"]: r for r in out["sections"][-1]["rows"]}
    assert rows["driving table"]["after"] == "STG_CUSTOMER" and rows["joins"]["after"].startswith("LEFT STG_ADDRESS ON")


def test_schedule_previews():
    out = pv.schedule_preview("PUT", "/api/dq/schedules", {"table": "A.B.C", "cron": "0 6 * * *"}, {"cron": "0 5 * * *"}, 3)
    assert out["sections"][0]["rows"][0] == {"label": "cron", "before": "0 5 * * *", "after": "0 6 * * *", "change": "changed",
                                             "note": "3 active check(s)"}
    assert "no active check" in pv.schedule_preview("PUT", "/x", {"table": "A.B.C", "cron": "0 6 * * *"}, None, 0)["warnings"][0]
    gone = pv.schedule_preview("DELETE", "/api/dq/schedules?table=A.B.C", None, None, None)
    assert gone["headline"].endswith("A.B.C") and "no schedule" in gone["warnings"][0]
    assert gone["sections"][0]["rows"][0]["change"] == "removed"


def test_decision_preview_beside_the_current_checks():
    payload = {"run_id": "run-12345678", "decisions": [
        {"expectation_id": "e1", "decision": "MODIFIED", "severity": "WARN", "definition": {"min": 0}, "justification": "seasonal"},
        {"expectation_id": "e2", "decision": "REJECTED"}]}
    current = {"e1": {"target_column": "AMOUNT", "check_type": "RANGE", "status": "PROPOSED", "severity": "FAIL",
                      "check_definition": {"min": 10}}}
    out = pv.decisions_preview(payload, current)
    assert out["headline"].startswith("2 check decision(s) on run run-1234") and "1 modified" in out["headline"]
    first, second = out["sections"][0]["rows"]
    assert first["label"] == "AMOUNT · RANGE (e1)" and first["before"].startswith("proposed, FAIL")
    assert first["after"].startswith("modified, WARN") and first["note"] == "seasonal"
    assert second["before"] is None and "not found" in out["warnings"][0]


class Loader:
    def __init__(self, fail=False):
        self.fail = fail

    def sttm_version(self, catalog_id, version=None):
        if self.fail:
            raise RuntimeError("warehouse suspended")
        return entry(version=version or 3)

    def dq_schedule(self, table):
        return {"cron": "0 5 * * *"}

    def dq_active_checks(self, table):
        return 2

    def run_checks(self, run_id, ids):
        return {}


def test_build_dispatches_and_falls_back():
    req = {"method": "POST", "path": "/api/hub/sttm/c1/edit", "status": "PENDING",
           "payload": {"base_version": 3, "remove_lines": ["FAX"]}}
    assert pv.build(req, Loader())["kind"] == "STTM_EDIT"
    failed = pv.build(req, Loader(fail=True))
    assert failed["kind"] == "GENERIC" and "could not be read" in failed["warnings"][0]
    sched = pv.build({"method": "PUT", "path": "/api/dq/schedules", "status": "APPLIED",
                      "payload": {"table": "A.B.C", "cron": "0 6 * * *"}}, Loader())
    assert sched["kind"] == "DQ_SCHEDULE" and sched["sections"][0]["rows"][0]["note"] is None  # not counted once decided
    assert pv.build({"method": "POST", "path": "/api/runs/r1/state", "payload": {"to_state": "X"}}, Loader())["kind"] == "GENERIC"


def test_result_summary():
    assert pv.result_summary(None) == []
    rows = pv.result_summary({"status_code": 200, "body": {"version": 4, "summary": "edited EMAIL",
                                                           "lines": [{"a": "x" * 100}] * 3}})
    labels = {r["label"]: r["after"] for r in rows}
    assert labels == {"version": "4", "summary": "edited EMAIL", "lines": "3 item(s)"}


# ---------------------------------------------------------------- preview endpoint


def test_preview_endpoint_visibility(monkeypatch):
    from fastapi.testclient import TestClient

    import app.governance as gov
    from app.main import app

    req = {"request_id": "q1", "method": "PUT", "path": "/api/dq/schedules", "payload": {"table": "A.B.C", "cron": "0 6 * * *"},
           "status": "PENDING", "requested_by": "BOB", "approver_role": "DQ_APPROVER", "allow_self": False, "result": None}

    class Db:
        def query(self, sql, params=()):
            if "DQ_SCHEDULE" in sql:
                return [{"cron": "0 5 * * *", "enabled": True}]
            if "DQ_CHECKS_CATALOG" in sql:
                return [{"n": 4}]
            return []

    monkeypatch.setattr(gov, "_one", lambda db, rid: dict(req))
    monkeypatch.setattr(gov, "settings", lambda d: dict(gov.DEFAULT_SETTINGS))
    monkeypatch.setattr(gov, "_db_for", lambda headers: None)  # the middleware lets the read through
    app.dependency_overrides[gov.gov_db] = lambda: Db()
    try:
        monkeypatch.setattr(gov, "identity", lambda d: {"user": "CARL", "roles": ["VIEWER"], "privileges": ["RUN.VIEW"]})
        assert TestClient(app).get("/api/governance/requests/q1/preview").status_code == 403
        monkeypatch.setattr(gov, "identity", lambda d: {"user": "BOB", "roles": ["DATA_ENGINEER"], "privileges": ["SODA.EDIT"]})
        res = TestClient(app).get("/api/governance/requests/q1/preview")
        assert res.status_code == 200
        body = res.json()
        assert body["kind"] == "DQ_SCHEDULE" and body["sections"][0]["rows"][0]["before"] == "0 5 * * *"
        assert body["sections"][0]["rows"][0]["note"] == "4 active check(s)" and body["result"] == []
    finally:
        app.dependency_overrides.pop(gov.gov_db, None)
