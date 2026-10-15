"""Profiling Store governance (V041): schedule SQL, comparing two passes, column annotations (propose, four eyes,
model proposal with a patch), the merge of an annotation patch into a model draft, governance and the API."""

import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))

from services.catalog import profile_ops as po  # noqa: E402
from services.governance import policy  # noqa: E402
from services.modeling import spec_v2  # noqa: E402

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


def spec():
    return spec_v2.normalize_v2({"domain": "SALES", "target_database": "SILVER", "target_schema": "SALES", "design": {"entities": [
        {"entity_name": "CUSTOMER", "attributes": [
            {"name": "ID", "datatype": "NUMBER", "is_pk": True, "nullable": False},
            {"name": "EMAIL", "datatype": "VARCHAR"},
            {"name": "FAX", "datatype": "VARCHAR", "status": "RETIRED"}]}]}}, "m1")


# ---------------------------------------------------------------- pure


def test_schedule_sql_validates_and_names_the_task():
    sid = str(uuid.uuid4())
    task, stmts = po.schedule_sql("AIP", sid, "0 5 * * * UTC", "WH")
    assert task == f"AIP.PROFILE.{po.task_name(sid)}" and "USING CRON 0 5 * * * UTC" in stmts[0]
    assert f"RUN_SCHEDULED_PROFILE('{sid}')" in stmts[0] and stmts[1].endswith("RESUME")
    for bad in [("AIP;DROP", sid, "0 5 * * *", "WH"), ("AIP", sid, "whenever", "WH"), ("AIP", "x'); DROP", "0 5 * * *", "WH")]:
        with pytest.raises((AssertionError, ValueError)):
            po.schedule_sql(*bad)


def test_compare_two_passes_column_by_column():
    a = [{"column_name": "ID", "data_type": "NUMBER", "null_pct": 0, "distinct_count": 100, "row_count": 100},
         {"column_name": "EMAIL", "data_type": "VARCHAR", "null_pct": 2, "distinct_count": 90, "semantic_type": "EMAIL"},
         {"column_name": "OLD", "data_type": "VARCHAR", "null_pct": 0, "distinct_count": 5}]
    b = [{"column_name": "ID", "data_type": "NUMBER", "null_pct": 0, "distinct_count": 120, "row_count": 120},
         {"column_name": "EMAIL", "data_type": "VARCHAR(500)", "null_pct": 20, "distinct_count": 95, "semantic_type": "TEXT"},
         {"column_name": "NEW", "data_type": "DATE", "null_pct": 0, "distinct_count": 10}]
    out = po.compare(a, b)
    by = {c["column_name"]: c for c in out["columns"]}
    assert by["ID"]["status"] == "same" and by["NEW"]["status"] == "added" and by["OLD"]["status"] == "removed"
    email = by["EMAIL"]
    assert email["status"] == "changed" and email["null_delta"] == 18
    assert any("type" in f for f in email["flags"]) and any("nulls" in f for f in email["flags"]) and any("semantic" in f for f in email["flags"])
    assert out["row_count"] == {"a": 100.0, "b": 120.0, "delta": 20.0}
    assert out["counts"] == {"added": 1, "removed": 1, "changed": 1, "same": 1}
    assert [c["column_name"] for c in out["columns"]] == ["ID", "EMAIL", "NEW", "OLD"]


def test_clean_annotation_and_patch():
    fields, problems = po.clean_annotation({"description": " Customer email ", "pii_classification": "email",
                                            "semantic_type": "email address", "accepted_values": ["a", " "]})
    assert not problems and fields == {"description": "Customer email", "pii_classification": "EMAIL",
                                       "semantic_type": "EMAIL_ADDRESS", "accepted_values": ["a"]}
    assert po.clean_annotation({})[1] and po.clean_annotation({"pii_classification": "x;drop"})[1]
    assert po.patch_for({"pii_classification": "NONE"}) == {"pii": {"flag": False, "classification": None}}
    patch = po.patch_for(fields)
    assert patch["pii"] == {"flag": True, "classification": "EMAIL"} and patch["description"] == "Customer email"
    out = po.apply_patch(spec(), "customer", "email", patch)
    email = next(a for a in out["design"]["entities"][0]["attributes"] if a["name"] == "EMAIL")
    assert email["description"] == "Customer email" and email["pii"]["classification"] == "EMAIL"
    with pytest.raises(AssertionError):
        po.apply_patch(spec(), "CUSTOMER", "NOPE", patch)


# ---------------------------------------------------------------- annotations (fake session)


def pending(proposed_by="BEN", column="EMAIL", **fields):
    return {"ANNOTATION_ID": "a1", "TABLE_FQN": TABLE, "COLUMN_NAME": column, "DESCRIPTION": fields.get("description"),
            "BUSINESS_DEFINITION": None, "PII_CLASSIFICATION": fields.get("pii"), "SEMANTIC_TYPE": None, "ACCEPTED_VALUES": None,
            "DETECTED": "{}", "NOTE": None, "STATUS": "PROPOSED", "PROPOSED_BY": proposed_by, "PROPOSED_AT": None,
            "DECIDED_BY": None, "DECIDED_AT": None, "DECISION_NOTE": None, "MODEL_ID": None, "MODEL_CHANGE_ID": None}


def test_propose_replaces_an_older_pending_one():
    s = Session({"FROM CORE.DATA_PROFILES": [{"DESCRIPTION": None, "PII_CLASSIFICATION": "NONE", "SEMANTIC_TYPE": "EMAIL"}]})
    out = po.propose(s, json.dumps({"table": "silver.sales.customer", "column": "email", "pii_classification": "PII",
                                    "description": "Where we send invoices"}), "ana")
    assert out["table"] == TABLE and out["column"] == "EMAIL" and out["status"] == "PROPOSED"
    assert s.find("SET STATUS = 'WITHDRAWN'")[0][1] == ["ANA", TABLE, "EMAIL"]
    row = s.find("INSERT INTO CORE.PROFILE_ANNOTATION")[0][1]
    assert row[3] == "Where we send invoices" and row[5] == "PII" and json.loads(row[8])["semantic_type"] == "EMAIL"
    with pytest.raises(AssertionError):
        po.propose(Session(), json.dumps({"table": TABLE, "column": "EMAIL"}), "ana")


def test_approve_is_four_eyes_and_proposes_to_the_model(monkeypatch):
    s = Session({"FROM CORE.PROFILE_ANNOTATION WHERE ANNOTATION_ID": [pending(description="Invoice email", pii="PII")]})
    with pytest.raises(AssertionError, match="your own"):
        po.approve(s, "a1", "ben")
    monkeypatch.setattr(po, "_model_entity", lambda session, table: {
        "model_id": "m1", "version": 3, "spec": spec(), **__import__("services.catalog.dq_suite", fromlist=["x"]).model_for_table(spec(), TABLE)})
    out = po.approve(s, "a1", "lee", "agreed")
    assert out["status"] == "APPROVED" and out["model"]["change_id"] and out["model"]["counts"]["NON_FUNCTIONAL"] >= 1
    assert s.find("SET STATUS = 'SUPERSEDED'")[0][1] == [TABLE, "EMAIL"]
    change = s.find("INSERT INTO CORE.MODEL_CHANGE")[0][1]
    assert change[1] == "m1" and change[3] == "CUSTOMER" and change[8] == "a1"
    patch = json.loads(change[9])
    assert patch == {"entity": "CUSTOMER", "column": "EMAIL",
                     "set": {"description": "Invoice email", "pii": {"flag": True, "classification": "PII"}}}
    assert s.find("SET MODEL_ID = ?, MODEL_CHANGE_ID = ?")[0][1][0] == "m1"


def test_approve_without_a_model_or_on_a_retired_column(monkeypatch):
    s = Session({"FROM CORE.PROFILE_ANNOTATION WHERE ANNOTATION_ID": [pending(description="x")]})
    monkeypatch.setattr(po, "_model_entity", lambda session, table: None)
    assert po.approve(s, "a1", "lee")["model"] is None and not s.find("INSERT INTO CORE.MODEL_CHANGE")
    from services.catalog import dq_suite
    s = Session({"FROM CORE.PROFILE_ANNOTATION WHERE ANNOTATION_ID": [pending(column="FAX", description="x")]})
    monkeypatch.setattr(po, "_model_entity", lambda session, table: {"model_id": "m1", "version": 3, "spec": spec(),
                                                                      **dq_suite.model_for_table(spec(), TABLE)})
    out = po.approve(s, "a1", "lee")
    assert "not an active column" in out["model"]["skipped"] and not s.find("INSERT INTO CORE.MODEL_CHANGE")


def test_reject_and_withdraw_rules():
    s = Session({"FROM CORE.PROFILE_ANNOTATION WHERE ANNOTATION_ID": [pending()]})
    with pytest.raises(AssertionError, match="withdraw it instead"):
        po.reject(s, "a1", "ben", "no")
    with pytest.raises(AssertionError, match="say why"):
        po.reject(s, "a1", "lee", "")
    assert po.reject(s, "a1", "lee", "not PII")["status"] == "REJECTED"
    with pytest.raises(AssertionError, match="only the person"):
        po.withdraw(s, "a1", "lee")
    assert po.withdraw(s, "a1", "ben")["status"] == "WITHDRAWN"


def test_effective_splits_approved_and_pending():
    approved = {**pending(description="ok"), "STATUS": "APPROVED"}
    s = Session({"WHERE TABLE_FQN = ? AND STATUS IN ('APPROVED', 'PROPOSED')": [approved, {**pending(column="ID"), "ANNOTATION_ID": "a2"}]})
    out = po.effective(s, TABLE)
    assert set(out["approved"]) == {"EMAIL"} and set(out["pending"]) == {"ID"}


def test_merge_change_applies_an_annotation_patch_to_the_draft(monkeypatch):
    from services.modeling import domain_model, run_link

    draft = spec()
    draft["design"]["entities"][0]["attributes"][1]["description"] = "edited in the draft"
    draft["design"]["entities"][0]["description"] = "draft-only edit"
    saved = {}
    monkeypatch.setattr(domain_model, "get_model", lambda session, mid: {"open": {"version": 4, "status": "DRAFT", "etag": "e"}})
    monkeypatch.setattr(domain_model, "_spec", lambda session, mid, v: draft)
    monkeypatch.setattr(domain_model, "save_draft", lambda session, mid, payload, user: saved.update(json.loads(payload)) or {"etag": "e2", "counts": {}})
    s = Session({"SELECT STATUS, PROPOSED_ENTITIES, PATCH FROM CORE.MODEL_CHANGE": [
        {"STATUS": "PROPOSED", "PROPOSED_ENTITIES": "[]",
         "PATCH": json.dumps({"entity": "CUSTOMER", "column": "EMAIL", "set": {"pii": {"flag": True, "classification": "PII"}}})}]})
    out = run_link.merge_change(s, "m1", "c1", "ana")
    entity = saved["spec"]["design"]["entities"][0]
    email = next(a for a in entity["attributes"] if a["name"] == "EMAIL")
    assert out["status"] == "MERGED" and email["pii"]["classification"] == "PII"
    assert email["description"] == "edited in the draft" and entity["description"] == "draft-only edit"


def test_run_scheduled_records_outcome_and_drift(monkeypatch):
    import services.profiling.procedures as procs

    monkeypatch.setattr(procs, "profile_source_tables", lambda session, sid, payload: {
        "profiled": ["CUSTOMERS"], "cached": ["ORDERS"], "failed": []})
    monkeypatch.setattr(po, "_drifted", lambda session, db, schema, tables: [{"table": "CUSTOMERS", "columns": {"EMAIL": ["nulls 1.0% -> 20.0%"]}}])
    s = Session({"FROM CORE.PROFILE_SCHEDULE WHERE SCHEDULE_ID": [
        {"SOURCE_SYSTEM_ID": "src", "DATABASE_NAME": "RAW", "SCHEMA_NAME": "CRM", "TABLES": '["CUSTOMERS", "ORDERS"]',
         "FORCE_REFRESH": False, "ENABLED": True}]})
    out = po.run_scheduled(s, "s1")
    assert out == {"schedule_id": "s1", "status": "OK", "profiled": 1, "cached": 1, "failed": 0, "drifted": 1}
    upd = s.find("UPDATE CORE.PROFILE_SCHEDULE SET LAST_RUN_AT")[0][1]
    assert upd[0] == "OK" and json.loads(upd[1])["drifted"][0]["table"] == "CUSTOMERS"

    def boom(*a, **k):
        raise RuntimeError("no access")
    monkeypatch.setattr(procs, "profile_source_tables", boom)
    assert po.run_scheduled(s, "s1")["status"] == "FAILED"


# ---------------------------------------------------------------- governance and API


def test_policy_rules():
    assert policy.privilege_for("POST", "/api/hub/profiles/annotations")[0] == "PROFILE.RUN"
    assert policy.privilege_for("POST", "/api/hub/profiles/annotations/a1/approve")[0] == "KNOWLEDGE.EDIT"
    assert policy.privilege_for("POST", "/api/hub/profiles/annotations/a1/reject")[0] == "KNOWLEDGE.EDIT"
    assert policy.privilege_for("PUT", "/api/hub/profiles/schedules")[0] == "PROFILE.RUN"
    assert policy.privilege_for("POST", "/api/hub/profiles/schedules/s1/run")[0] == "PROFILE.RUN"
    assert policy.no_queue("POST", "/api/hub/profiles/annotations/a1/approve")
    assert not policy.store_queue("POST", "/api/hub/profiles/annotations")


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main
    import app.governance as gov
    import app.hub_catalog_api as hc

    class Db:
        user = "lee"

        def __init__(self):
            self.executed, self.calls = [], []

        def query(self, sql, params=()):
            if "SELECT PROFILE_ID, MAX(RUN_ID)" in sql:
                return [{"profile_id": "p2", "run_id": None, "row_count": 120, "columns": 2, "profiled_at": "2026-10-11", "profiled_by": "A"},
                        {"profile_id": "p1", "run_id": None, "row_count": 100, "columns": 2, "profiled_at": "2026-10-01", "profiled_by": "A"}]
            if "WHERE PROFILE_ID = %s" in sql:
                pid = params[0]
                return [{"column_name": "ID", "data_type": "NUMBER", "null_pct": 0, "distinct_count": 100 if pid == "p1" else 120,
                         "row_count": 100 if pid == "p1" else 120, "top_values": None, "histogram": None}]
            if "SELECT TASK_NAME FROM CORE.PROFILE_SCHEDULE" in sql:
                return [{"task_name": f"{main.DATABASE}.PROFILE.PROF_X"}]
            return []

        def execute(self, sql, params=()):
            self.executed.append(sql)

        def call(self, sql, params=()):
            self.calls.append(sql)
            return {"status": "OK"}

    db = Db()

    async def open_door(request, call_next):
        return await call_next(request)
    monkeypatch.setattr(gov, "middleware", open_door)
    monkeypatch.setattr(main, "_ensure_source", lambda db_, d, s: {"source_system_id": "src"})
    main.app.dependency_overrides[main.current_db] = lambda: db
    yield TestClient(main.app), hc, db
    main.app.dependency_overrides.clear()


def test_api_compare_schedule_and_annotations(client, monkeypatch):
    c, hc, db = client
    calls = []
    monkeypatch.setattr(hc, "invoke_source", lambda db_, fn, *a: calls.append((fn.__name__, a)) or (
        {"approved": {}, "pending": {}} if fn.__name__ == "effective" else {"ok": True}))
    body = c.get("/api/hub/profiles/columns", params={"fqn": "RAW.CRM.CUSTOMERS", "compare_to": "p1"}).json()
    assert body["comparison"]["a"] == "p1" and body["comparison"]["b"] == "p2"
    assert body["comparison"]["row_count"]["delta"] == 20 and body["annotations"] == {"approved": {}, "pending": {}}
    res = c.put("/api/hub/profiles/schedules", json={"database": "RAW", "schema": "CRM", "cron": "0 5 * * * UTC", "tables": ["B", "A", "A"]})
    assert res.status_code == 200 and res.json()["tables"] == ["A", "B"]
    assert any(s.startswith("CREATE OR REPLACE TASK") for s in db.executed)
    assert c.put("/api/hub/profiles/schedules", json={"database": "RAW", "schema": "CRM", "cron": "nope nope"}).status_code == 400
    assert c.post("/api/hub/profiles/schedules/s1/run").json() == {"status": "OK"}
    assert c.delete("/api/hub/profiles/schedules", params={"id": "s1"}).json()["deleted"]
    c.post("/api/hub/profiles/annotations", json={"table": "RAW.CRM.CUSTOMERS", "column": "EMAIL", "pii_classification": "PII"})
    c.post("/api/hub/profiles/annotations/a1/approve", json={"note": "ok"})
    c.post("/api/hub/profiles/annotations/a1/reject", json={"note": "no"})
    names = [n for n, _ in calls if n != "effective"]
    assert names == ["propose", "approve", "reject"] and calls[-1][1][1] == "LEE"
