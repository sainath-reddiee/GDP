"""Data Quality suites (V040): checks versioned per table, the classified diff, validation against the approved domain
model, export, the draft and review rules of services/catalog/dq_suite.py, the catalog apply, Git publishing and the
API. Snowflake is replaced by fakes that answer by SQL substring."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))

from services.catalog import dq_suite as ds  # noqa: E402
from services.dbt import github, publish  # noqa: E402
from services.governance import policy  # noqa: E402

TABLE = "SILVER.SALES.CUSTOMER"


class Row(dict):
    def as_dict(self):
        return dict(self)

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else dict.__getitem__(self, key)


class Session:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def sql(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, list(params or [])))
        session = self

        class Result:
            def collect(self_inner):
                for needle, value in session.answers.items():
                    if needle in flat:
                        value = value(list(params or [])) if callable(value) else value
                        return [Row(r) for r in value]
                return []
        return Result()

    def find(self, needle):
        return [(s, p) for s, p in self.calls if needle in s]


def check(col="EMAIL", kind="not_null", severity="FAIL", cid=None, **definition):
    record, problems = ds.check_record({"check_id": cid, "target_column": col, "severity": severity,
                                        "definition": {"kind": kind, **definition}})
    assert not problems, problems
    return record


# ---------------------------------------------------------------- pure


def test_check_record_assigns_ids_and_keeps_status():
    c = check()
    assert len(c["check_id"]) == 36 and c["status"] == "ACTIVE" and c["soda_dsl"] and c["headline"] == "missing_count(EMAIL) = 0"
    paused, _ = ds.check_record({**c, "status": "PAUSED"})
    assert paused["check_id"] == c["check_id"] and paused["status"] == "PAUSED"
    _, problems = ds.check_record({"target_column": "X", "definition": {"kind": "nonsense"}})
    assert problems
    checks, problems = ds.normalize_checks([c, {**c}])  # a repeated id gets a new one
    assert not problems and checks[0]["check_id"] != checks[1]["check_id"]


def test_diff_classifies_tightened_loosened_changed_and_documentation():
    nn = check(cid="a")
    pct = check("PHONE", "missing_percent", "WARN", cid="b", max_percent=2)
    old_only = check("NAME", cid="c")
    sev = check("ID", "unique", "FAIL", cid="d")
    doc = check("CITY", cid="e")
    base = [nn, pct, old_only, sev, doc]
    new = [
        {**nn, "status": "PAUSED"},                                        # paused: loosened
        check("PHONE", "missing_percent", "WARN", cid="b", max_percent=5),  # relaxed threshold
        {**sev, "severity": "WARN"},                                       # softer severity
        {**doc, "requirement": "City is shown on invoices"},               # reason only
        check("COUNTRY", cid="f"),                                         # added
    ]
    out = ds.diff(base, new)
    by = {c["check_id"]: c for c in out["changes"]}
    assert by["a"]["class"] == "LOOSENED" and "paused" in by["a"]["message"]
    assert by["b"]["class"] == "LOOSENED"
    assert by["c"]["kind"] == "REMOVED" and by["c"]["class"] == "LOOSENED"
    assert by["d"]["class"] == "LOOSENED" and "severity fail to warn" in by["d"]["message"]
    assert by["e"]["class"] == "DOCUMENTATION"
    assert by["f"]["kind"] == "ADDED" and by["f"]["class"] == "TIGHTENED"
    assert out["loosened"] and out["counts"]["LOOSENED"] == 4
    stricter = ds.diff([pct], [check("PHONE", "missing_percent", "WARN", cid="b", max_percent=1)])
    assert stricter["changes"][0]["class"] == "TIGHTENED" and not stricter["loosened"]
    moved = ds.diff([nn], [{**check("EMAIL2", cid="a")}])
    assert moved["changes"][0]["class"] == "CHANGED"
    assert ds.diff(None, [nn])["counts"]["TIGHTENED"] == 1
    assert ds.diff([nn], [nn])["changes"] == []


def test_validate_against_the_model():
    a, b = check(cid="a"), check("EMAIL", cid="b")
    assert ds.validate([a, b])[0]["severity"] == "ERROR"  # same check twice
    model = {"entity": "CUSTOMER", "model_version": 3, "entity_status": "ACTIVE",
             "columns": {"EMAIL": "ACTIVE", "FAX": "RETIRED"}}
    issues = ds.validate([check(cid="a"), check("FAX", cid="f"), check("NOPE", cid="n"), check(None, "row_count", cid="r", gt=0)], model)
    sev = {i["check_id"]: i["severity"] for i in issues}
    assert sev == {"f": "ERROR", "n": "WARN"} and "v3" in issues[0]["message"]
    retired = ds.validate([], {**model, "entity_status": "RETIRED"})
    assert retired[0]["severity"] == "ERROR" and ds.blocking(retired)


def test_model_for_table_matches_placement_then_name():
    from services.modeling import spec_v2

    spec = spec_v2.normalize_v2({"domain": "SALES", "target_database": "SILVER", "target_schema": "SALES", "design": {"entities": [
        {"entity_name": "CUSTOMER", "attributes": [{"name": "ID", "datatype": "NUMBER", "is_pk": True, "nullable": False},
                                                   {"name": "FAX", "datatype": "VARCHAR", "status": "RETIRED"}]}]}}, "m1")
    got = ds.model_for_table(spec, TABLE)
    assert got["entity"] == "CUSTOMER" and got["columns"] == {"ID": "ACTIVE", "FAX": "RETIRED"}
    assert ds.model_for_table(spec, "OTHER.DB.CUSTOMER")["entity"] == "CUSTOMER"  # name only
    assert ds.model_for_table(spec, "SILVER.SALES.ORDERS") is None


def test_export_soda_and_gx_only_active_checks():
    checks = [check(cid="a"), {**check("PHONE", cid="p"), "status": "PAUSED"}]
    name, media, soda = ds.export(TABLE, checks, "soda")
    assert name == "silver_sales_customer.soda.yml" and "EMAIL" in soda and "PHONE" not in soda
    name, media, gx = ds.export(TABLE, checks, "gx")
    suite = json.loads(gx)
    assert media == "application/json" and suite["expectations"] and "PHONE" not in gx
    files = ds.artifacts(TABLE, "SALES", checks)
    assert sorted(files) == ["dq/sales/silver_sales_customer.gx.json", "dq/sales/silver_sales_customer.soda.yml"]


# ---------------------------------------------------------------- lifecycle (fake session)


SUITE = {"SUITE_ID": ds.suite_id_for(TABLE), "TABLE_NAME": TABLE, "DOMAIN_ID": None, "MODEL_ID": None,
         "APPROVED_VERSION": 1, "OPEN_VERSION": None, "DEFAULT_REPO_ID": None}


def version(v, status, authors=("ANA",), etag="e1", submitted_by=None):
    return {"VERSION": v, "STATUS": status, "BASE_VERSION": 1, "ETAG": etag, "AUTHORS": json.dumps(list(authors)),
            "ORIGIN": "STORE", "CHANGE_NOTE": None, "MODEL_ID": None, "MODEL_VERSION": None, "SUBMITTED_BY": submitted_by,
            "SUBMITTED_AT": None, "APPROVED_BY": None, "APPROVED_AT": None, "REJECTED_BY": None, "REJECTED_AT": None,
            "REJECT_REASON": None, "REVIEW_SUMMARY": None, "CATALOG_SYNCED_AT": None, "REPO_ID": None, "BRANCH": None,
            "PR_URL": None, "PR_NUMBER": None, "PR_STATE": None, "MERGED_SHA": None, "PR_SYNCED_AT": None,
            "CREATED_BY": "ANA", "CREATED_AT": None, "UPDATED_AT": None}


def lifecycle(versions, checks_by_version, suite=None):
    """A session over one suite: versions (newest first) and their checks; ETAG reads echo the last write."""
    s = Session()

    def etag(_params):
        writes = s.find("UPDATE GOVERNANCE.DQ_SUITE_VERSION SET CHECKS = PARSE_JSON(?), ETAG = ?")
        return [{"ETAG": writes[-1][1][1]}] if writes else []

    s.answers = {
        "FROM GOVERNANCE.DQ_SUITE WHERE TABLE_NAME = ?": [suite or SUITE],
        "SELECT ETAG FROM GOVERNANCE.DQ_SUITE_VERSION": etag,
        "SELECT CHECKS FROM GOVERNANCE.DQ_SUITE_VERSION": lambda p: [{"CHECKS": json.dumps(checks_by_version[int(p[1])])}],
        "FROM GOVERNANCE.DQ_SUITE_VERSION WHERE SUITE_ID = ? AND STATUS IN ('DRAFT', 'IN_REVIEW')": lambda p: [
            {"VERSION": v["VERSION"]} for v in versions if v["STATUS"] in ("DRAFT", "IN_REVIEW")],
        "FROM GOVERNANCE.DQ_SUITE_VERSION WHERE SUITE_ID = ? ORDER BY VERSION DESC": lambda p: versions,
    }
    return s


def test_stage_check_opens_a_draft_and_adds_the_check():
    approved = [check(cid="a")]
    versions = [version(1, "APPROVED")]
    s = lifecycle(versions, {1: approved, 2: approved})

    def opened(*_a, **_k):  # open_draft inserts version 2; the fake then lists it
        versions.insert(0, version(2, "DRAFT"))
        return {"version": 2}
    import services.catalog.dq_suite as mod
    real = mod.open_draft
    mod.open_draft = opened
    try:
        out = ds.stage_check(s, json.dumps({"table": TABLE, "dsl": "duplicate_count(ID) = 0", "severity": "FAIL"}), "ben")
    finally:
        mod.open_draft = real
    assert out["draft_opened"] and out["version"] == 2 and out["action"] == "added" and out["counts"]["TIGHTENED"] == 1
    written = json.loads(s.find("SET CHECKS = PARSE_JSON(?)")[-1][1][0])
    assert [c["target_column"] for c in written] == ["EMAIL", "ID"]
    assert s.find("SET CHECKS = PARSE_JSON(?)")[-1][1][2] == "BEN"


def test_stage_refuses_while_in_review_and_rejects_duplicates():
    s = lifecycle([version(2, "IN_REVIEW"), version(1, "APPROVED")], {1: [check(cid="a")], 2: [check(cid="a")]})
    with pytest.raises(AssertionError, match="in review"):
        ds.stage_status(s, TABLE, "a", "PAUSED", "ben")
    s = lifecycle([version(2, "DRAFT"), version(1, "APPROVED")], {1: [check(cid="a")], 2: [check(cid="a")]})
    with pytest.raises(AssertionError, match="same check"):
        ds.stage_check(s, json.dumps({"table": TABLE, "dsl": "missing_count(EMAIL) = 0"}), "ben")
    out = ds.stage_status(s, TABLE, "a", "RETIRED", "ben")
    assert out["action"] == "removed" and out["loosened"]
    assert json.loads(s.find("SET CHECKS = PARSE_JSON(?)")[-1][1][0]) == []


def test_save_draft_with_a_stale_etag_is_a_conflict():
    s = lifecycle([version(2, "DRAFT", etag="current"), version(1, "APPROVED")], {1: [], 2: []})
    with pytest.raises(ds.Conflict):
        ds.save_draft(s, TABLE, json.dumps({"etag": "old", "checks": []}), "ben")
    out = ds.save_draft(s, TABLE, json.dumps({"etag": "current", "checks": [check(cid="z")]}), "ben")
    assert out["version"] == 2 and out["counts"]["TIGHTENED"] == 1


def test_submit_needs_a_change_and_no_errors():
    same = [check(cid="a")]
    s = lifecycle([version(2, "DRAFT"), version(1, "APPROVED")], {1: same, 2: same})
    with pytest.raises(AssertionError, match="nothing to review"):
        ds.submit(s, TABLE, "ben")
    dup = [check(cid="a"), check(cid="b")]
    s = lifecycle([version(2, "DRAFT"), version(1, "APPROVED")], {1: same, 2: dup})
    with pytest.raises(AssertionError, match="problem"):
        ds.submit(s, TABLE, "ben")
    s = lifecycle([version(2, "DRAFT"), version(1, "APPROVED")], {1: same, 2: same + [check("ID", cid="b")]})
    out = ds.submit(s, TABLE, "ben", "add id")
    assert out["status"] == "IN_REVIEW" and s.find("SET STATUS = 'IN_REVIEW'")[0][1][0] == "BEN"


def test_approve_is_four_eyes_and_loosening_needs_acknowledgement():
    base = [check(cid="a"), check("ID", cid="b")]
    loosen = [check(cid="a")]
    s = lifecycle([version(2, "IN_REVIEW", authors=("BEN",), submitted_by="BEN"), version(1, "APPROVED")], {1: base, 2: loosen})
    with pytest.raises(AssertionError, match="cannot approve"):
        ds.approve(s, TABLE, 2, "ben")
    with pytest.raises(AssertionError, match="LOOSENED"):
        ds.approve(s, TABLE, 2, "lee")
    out = ds.approve(s, TABLE, 2, "lee", acknowledge_loosened=True)
    assert out["status"] == "APPROVED" and out["acknowledged_loosened"]
    assert s.find("SET STATUS = 'SUPERSEDED'") and s.find("SET APPROVED_VERSION = ?")[0][1][0] == 2
    assert s.calls[-1][0] == "COMMIT"


def test_approve_records_the_model_version(monkeypatch):
    monkeypatch.setattr(ds, "model_context", lambda session, table, domain: {
        "model_id": "m1", "model_version": 4, "entity": "CUSTOMER", "entity_status": "ACTIVE", "columns": {"EMAIL": "ACTIVE", "ID": "ACTIVE"}})
    s = lifecycle([version(2, "IN_REVIEW", authors=("BEN",)), version(1, "APPROVED")], {1: [], 2: [check(cid="a")]})
    out = ds.approve(s, TABLE, 2, "lee")
    approved = s.find("SET STATUS = 'APPROVED', APPROVED_BY = ?")[0][1]
    assert approved[2:4] == ["m1", "4"] and out["model_version"] == 4


def test_apply_suite_version_merges_and_retires():
    checks = [check(cid="a"), {**check("ID", cid="b"), "status": "PAUSED"}]
    s = Session({"JOIN GOVERNANCE.DQ_SUITE_VERSION V": [{"TABLE_NAME": TABLE, "DOMAIN_ID": "d1", "STATUS": "APPROVED",
                                                        "CHECKS": json.dumps(checks)}]})
    out = ds.apply_suite_version(s, "s1", 2)
    assert out == {"table": TABLE, "version": 2, "checks": 2, "active": 1}
    merges = s.find("MERGE INTO GOVERNANCE.DQ_CHECKS_CATALOG")
    assert [m[1][0] for m in merges] == ["a", "b"] and merges[1][1][7] == "PAUSED"
    retire = s.find("SET STATUS = 'RETIRED'")[0]
    assert "NOT IN (?, ?)" in retire[0] and retire[1] == [TABLE, "a", "b"]
    draft = Session({"JOIN GOVERNANCE.DQ_SUITE_VERSION V": [{"TABLE_NAME": TABLE, "DOMAIN_ID": None, "STATUS": "DRAFT", "CHECKS": "[]"}]})
    with pytest.raises(AssertionError, match="approved"):
        ds.apply_suite_version(draft, "s1", 2)


def test_ensure_baselines_a_catalog_table():
    catalog = [{"CHECK_ID": "c1", "COLUMN_NAME": "EMAIL", "CHECK_TYPE": "NOT_NULL", "CHECK_DEFINITION": '{"kind": "not_null"}',
                "SODA_DSL": "x", "SEVERITY": "FAIL", "STATUS": "ACTIVE", "ORIGIN": "PROFILE", "REQUIREMENT": None, "SOURCE_RUN_ID": None}]
    created = {"n": 0}

    def header(_p):
        created["n"] += 1
        return [] if created["n"] == 1 else [SUITE]
    s = Session({"FROM GOVERNANCE.DQ_SUITE WHERE TABLE_NAME = ?": header,
                 "FROM GOVERNANCE.DQ_CHECKS_CATALOG WHERE TABLE_NAME = ?": catalog})
    out = ds._ensure(s, TABLE, "d1", "ana")
    assert out["suite_id"] == SUITE["SUITE_ID"]
    inserted = s.find("INSERT INTO GOVERNANCE.DQ_SUITE_VERSION")[0][1]
    assert inserted[2] == "APPROVED" and json.loads(inserted[4])[0]["check_id"] == "c1"
    assert s.find("SET APPROVED_VERSION = 1")


# ---------------------------------------------------------------- Git


def test_publish_dq_suite_and_sync(monkeypatch):
    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(github, "urllib_request", lambda token: "req")
    seen = {}

    def fake_publish(request, origin, base, head, files, **kw):
        seen.update(files=files, head=head, draft=kw.get("draft"))
        return {"status": "PUBLISHED", "pull_request": {"url": "https://github.com/a/b/pull/5", "number": 5}}
    monkeypatch.setattr(github, "publish", fake_publish)
    s = Session({"FROM GOVERNANCE.DQ_SUITE_VERSION V JOIN GOVERNANCE.DQ_SUITE S": [
                    {"STATUS": "APPROVED", "CHECKS": json.dumps([check(cid="a")]), "TABLE_NAME": TABLE, "DOMAIN_ID": "d1",
                     "DOMAIN_NAME": "SALES"}],
                 "FROM CODE.REPO WHERE REPO_ID": [{"GIT_URL": "https://github.com/a/b", "BRANCH": "main", "PROVIDER": "GITHUB"}]})
    out = publish.publish_dbt_pr(s, "", json.dumps({"dq_suite": {"suite_id": "s1", "version": 2, "repo_id": "r1",
                                                                 "head": "feature/dq-customer-v2"}}))
    assert out["status"] == "PUBLISHED" and seen["draft"] is False
    assert sorted(seen["files"]) == ["dq/sales/silver_sales_customer.gx.json", "dq/sales/silver_sales_customer.soda.yml"]
    upd = s.find("UPDATE GOVERNANCE.DQ_SUITE_VERSION SET STATUS = ?")[0][1]
    assert upd[0] == "APPROVED" and upd[5] == "OPEN"
    with pytest.raises(AssertionError):
        publish.publish_dbt_pr(s, "", json.dumps({"dq_suite": {"suite_id": "s1", "version": 2, "repo_id": "r1", "head": "main"}}))

    monkeypatch.setattr(github, "pull_status", lambda req, origin, number: {"state": "MERGED", "merge_commit_sha": "abc"})
    sync = Session({"FROM GOVERNANCE.DQ_SUITE_VERSION V JOIN CODE.REPO": [{"SUITE_ID": "s1", "VERSION": 2, "PR_NUMBER": 5,
                                                                          "GIT_URL": "https://github.com/a/b"}]})
    got = publish.publish_dbt_pr(sync, "", json.dumps({"sync_dq_prs": {"suite_id": "s1"}}))
    assert got["merged"] == [{"suite_id": "s1", "version": 2}]
    assert "STATUS = 'PUBLISHED'" in sync.find("UPDATE GOVERNANCE.DQ_SUITE_VERSION SET PR_STATE")[0][0]
    both = publish.publish_dbt_pr(Session(), "", json.dumps({"sync_git_prs": {}}))
    assert set(both) == {"models", "dq_suites", "qa_packs"}


# ---------------------------------------------------------------- governance and API


def test_dq_edits_are_drafts_never_queued():
    for method, path in [("POST", "/api/dq/catalog"), ("POST", "/api/dq/catalog/seed"), ("POST", "/api/dq/catalog/c1/status"),
                         ("POST", "/api/dq/suites/submit"), ("PUT", "/api/dq/suites/draft"), ("POST", "/api/dq/suites/approve")]:
        assert policy.no_queue(method, path) and not policy.store_queue(method, path), path
    assert policy.store_queue("PUT", "/api/dq/schedules")
    assert policy.privilege_for("POST", "/api/dq/suites/approve")[0] == "SODA.APPROVE"
    assert policy.privilege_for("POST", "/api/dq/suites/reject")[0] == "SODA.APPROVE"
    assert policy.privilege_for("POST", "/api/dq/suites/submit")[0] == "SODA.EDIT"
    assert policy.privilege_for("POST", "/api/dq/suites/pr-sync")[0] is None


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main
    import app.governance as gov
    import app.quality_api as qa

    class Db:
        user = "lee"

        def query(self, sql, params=()):
            return [{"table_name": TABLE}] if "WHERE CHECK_ID" in sql else []

        def call(self, sql, params=()):
            return {"status": "PUBLISHED"}

    async def open_door(request, call_next):
        return await call_next(request)
    monkeypatch.setattr(gov, "middleware", open_door)
    main.app.dependency_overrides[main.current_db] = lambda: Db()
    yield TestClient(main.app), qa
    main.app.dependency_overrides.clear()


def test_api_routes_stage_approve_and_export(client, monkeypatch):
    c, qa = client
    calls = []

    def invoke(db, fn, *a):
        name = getattr(fn, "__name__", getattr(getattr(fn, "func", None), "__name__", "fn"))
        calls.append((name, a))
        if name == "approve":
            return {"suite_id": "s1", "version": 2, "status": "APPROVED"}
        if name == "export_version":
            return ("x.soda.yml", "application/x-yaml", "checks for X:\n")
        return {"ok": True}
    monkeypatch.setattr(qa, "invoke_source", invoke)
    monkeypatch.setattr(qa, "_source_call", lambda db, sql, fn, *a: {"checks": 2, "sql": sql})
    c.post("/api/dq/catalog", json={"table": TABLE, "dsl": "row_count > 0"})
    c.post("/api/dq/catalog/c1/status", json={"status": "PAUSED"})
    c.post("/api/dq/catalog/seed", json={"run_id": "r1"})
    out = c.post("/api/dq/suites/approve", json={"table": TABLE, "version": 2, "acknowledge_loosened": True}).json()
    assert out["catalog"]["applied"] and "APPLY_DQ_SUITE" in out["catalog"]["sql"]
    res = c.get("/api/dq/suites/export", params={"table": TABLE, "format": "soda"})
    assert res.status_code == 200 and 'filename="x.soda.yml"' in res.headers["content-disposition"]
    names = [n for n, _ in calls]
    assert names == ["stage_check", "stage_status", "seed_run", "approve", "export_version"]
    assert calls[1][1][:3] == (TABLE, "c1", "PAUSED") and calls[3][1][2:] == ("LEE", True)


def test_api_maps_conflict_to_409(client, monkeypatch):
    c, qa = client

    def boom(db, fn, *a):
        raise ds.Conflict("CONFLICT: stale")
    monkeypatch.setattr(qa, "invoke_source", boom)
    assert c.put("/api/dq/suites/draft", json={"table": TABLE, "etag": "x", "checks": []}).status_code == 409
