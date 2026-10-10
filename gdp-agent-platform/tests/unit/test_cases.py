"""Cases and domain governance (PR Q1): domain visibility (member, GENERAL, no-member domain, admin), 404 for hidden
cases, intake dedupe by source reference and fingerprint with claims, status changes and the CASE.RESOLVE privilege,
the resolve gate, merge, domain-scoped four-eyes knowledge approval with the DATA_STEWARD fallback, the governance
mapping of every new route, and the V034 migration."""

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import app.main as api_main  # noqa: E402  (load the app first: the API modules import from it)
from services.cases import rules, service as svc  # noqa: E402
from services.governance import domains  # noqa: E402
from services.governance.policy import (  # noqa: E402
    ADDED_PRIVILEGES, DEFAULT_FOUR_EYES, DEFAULT_POLICIES, PRIVILEGES, SYSTEM_ROLES, SYSTEM_VERSION, effective_privileges,
    privilege_for,
)

ROLE_PRIVS = {name: spec["privileges"] for name, spec in SYSTEM_ROLES.items()}
GRANTS = {name: spec["inherits"] for name, spec in SYSTEM_ROLES.items()}
SALES, HR, GENERAL, OPEN = "dom-sales", "dom-hr", "dom-general", "dom-open"


def privs(*roles):
    return effective_privileges(roles, ROLE_PRIVS, GRANTS)[1]


# ---------------------------------------------------------------- fakes

class FakeDb:
    """query() answers by the first handler whose marker is in the SQL; writes are recorded."""

    def __init__(self, user="ANA"):
        self.user = user
        self.handlers, self.writes = [], []
        self.registry = [{"domain_id": SALES, "domain_name": "SALES"}, {"domain_id": HR, "domain_name": "HR"},
                         {"domain_id": GENERAL, "domain_name": "General"}, {"domain_id": OPEN, "domain_name": "OPEN"}]
        self.members = [{"domain_id": SALES, "user_name": "ana", "role": "STEWARD"},
                        {"domain_id": SALES, "user_name": "BOB", "role": "EXPERT"},
                        {"domain_id": HR, "user_name": "HANNA", "role": "OWNER"},
                        {"domain_id": GENERAL, "user_name": "GIL", "role": "EXPERT"}]
        self.user_roles = [{"user_name": "STEVE", "role_name": "DATA_STEWARD"}, {"user_name": "ROOT", "role_name": "SUPER_ADMIN"}]

    def on(self, marker, value):
        self.handlers.insert(0, (marker, value))
        return self

    def query(self, sql, params=()):
        for marker, value in self.handlers:
            if marker in sql:
                found = value(sql, params) if callable(value) else value
                return [dict(r) for r in found]
        if "FROM KNOWLEDGE.DOMAIN_REGISTRY" in sql and "DOMAIN_KNOWLEDGE" not in sql:
            return [dict(r) for r in self.registry]
        if "FROM KNOWLEDGE.DOMAIN_MEMBER" in sql:
            return [dict(r) for r in self.members]
        if "FROM GOVERNANCE.USER_ROLE WHERE ROLE_NAME" in sql:
            return [{"user_name": r["user_name"]} for r in self.user_roles if r["role_name"] == params[0]]
        if "FROM GOVERNANCE.USER_ROLE WHERE USER_NAME" in sql:
            return [{"role_name": r["role_name"]} for r in self.user_roles if r["user_name"] == params[0]]
        return []

    def execute(self, sql, params=()):
        self.writes.append((sql, params))

    def execute_count(self, sql, params=()):
        self.writes.append((sql, params))
        return 1

    def wrote(self, marker):
        return [w for w in self.writes if marker in w[0]]


@pytest.fixture(autouse=True)
def fresh_cache():
    domains.invalidate()
    yield
    domains.invalidate()


class Store:
    """The SqlStore interface in memory."""

    def __init__(self):
        self.cases, self.events, self._links, self.claims = {}, [], [], {}
        self.number = 0
        self.fail_insert = False

    def _out(self, c):
        return {**c, "links_count": len(self.links(c["case_id"]))}

    def get(self, case_id):
        c = self.cases.get(case_id)
        return self._out(c) if c else None

    def find_open_by_source(self, source, ref):
        for c in self.cases.values():
            if c["source"] == source and c["source_ref"] == ref and c["status"] in rules.OPEN_STATUSES:
                return self._out(c)
        return None

    def count_by_source(self, source, ref):
        return sum(1 for c in self.cases.values() if c["source"] == source and c["source_ref"] == ref)

    def find_open_by_fingerprint(self, domain_id, fp):
        for c in self.cases.values():
            if c["fingerprint"] == fp and c["domain_id"] == domain_id and c["status"] in rules.OPEN_STATUSES:
                return self._out(c)
        return None

    def insert(self, row, sla_hours):
        if self.fail_insert:
            raise RuntimeError("insert failed")
        self.number += 1
        self.cases[row["case_id"]] = {**row, "status": "NEW", "case_number": self.number, "sla_hours": sla_hours,
                                      "resolution": None, "resolved_by": None, "duplicate_of": None, "reopened": 0}

    def update(self, case_id, fields, expect_status=None, sla_hours=None, reopen=False):
        c = self.cases.get(case_id)
        if not c or (expect_status and c["status"] != expect_status):
            return 0
        for k, v in fields.items():
            c[k.lower()] = v
        if sla_hours is not None:
            c["sla_hours"] = sla_hours
        if reopen:
            c["reopened"] += 1
        return 1

    def claim(self, key):
        if key in self.claims:
            return False
        self.claims[key] = None
        return True

    def claimed_case(self, key):
        return self.claims.get(key)

    def bind(self, key, case_id, kind, actor, detail=None):
        self.claims[key] = case_id
        self.events.append({"case_id": case_id, "kind": kind, "actor": actor, "detail": detail or {}})

    def release_claim(self, key):
        self.claims.pop(key, None)

    def event(self, case_id, kind, actor="system", detail=None):
        self.events.append({"case_id": case_id, "kind": kind, "actor": actor, "detail": detail or {}})

    def kinds(self, case_id):
        return [e["kind"] for e in self.events if e["case_id"] == case_id]

    def links(self, case_id):
        return [link for link in self._links if link["case_id"] == case_id]

    def add_link(self, case_id, kind, ref, label, url, actor):
        if any(link["case_id"] == case_id and link["kind"] == kind and link["ref"] == ref for link in self._links):
            return False
        self._links.append({"link_id": f"l{len(self._links) + 1}", "case_id": case_id, "kind": kind, "ref": ref,
                            "label": label, "url": url})
        return True

    def remove_link(self, case_id, link_id):
        before = len(self._links)
        self._links = [link for link in self._links if not (link["case_id"] == case_id and link["link_id"] == link_id)]
        return before - len(self._links)


def spec(**kw):
    out = {"title": "Customer count wrong in DIM_CUSTOMER", "domain_id": SALES, "target_table_id": "t1",
           "source": "APP_REPORT", "kind": "DATA_BUG", "severity": "P2"}
    out.update(kw)
    return out


def see_all(_domain):
    return True


# ---------------------------------------------------------------- domain access

def test_member_general_no_member_and_hidden_domains():
    db = FakeDb()
    assert domains.member_domains(db, "Ana") == {SALES: "STEWARD"}
    assert domains.can_see(db, "ANA", SALES)            # member (stored lower case, compared upper case)
    assert domains.can_see(db, "ZOE", GENERAL)          # GENERAL is open to all, even with members
    assert domains.can_see(db, "ZOE", OPEN)             # no members: open
    assert domains.can_see(db, "ZOE", None)             # unscoped
    assert not domains.can_see(db, "ZOE", SALES)
    assert not domains.can_see(db, "ANA", HR)
    assert domains.visible_domain_ids(db, "ZOE") == {GENERAL, OPEN}
    assert domains.visible_domain_ids(db, "ANA") == {GENERAL, OPEN, SALES}
    assert domains.general_domain_id(db) == GENERAL


def test_admins_see_every_domain():
    db = FakeDb()
    assert domains.visible_domain_ids(db, "ROOT", {"*"}) is None
    assert domains.visible_domain_ids(db, "PAT", set(), {"PLATFORM_ADMIN"}) is None
    assert domains.can_see(db, "PAT", HR, (), ["PLATFORM_ADMIN"])
    assert domains.can_see(db, "ROOT", HR, privs("SUPER_ADMIN"))
    assert not domains.can_see(db, "QA", HR, privs("QA_LEAD"), ["QA_LEAD", "VIEWER"])


def test_app_admin_without_the_snowflake_role_is_filtered_like_snowflake():
    domains.invalidate()
    db = FakeDb(user="SYSUSER").on("IS_DATABASE_ROLE_IN_SESSION", [{"p": False}])
    # the row policy would hide HR from this session, so the app must not treat it as visible
    assert domains.visible_domain_ids(db, "SYSUSER", {"*"}) == {GENERAL, OPEN}
    assert not domains.can_see(db, "SYSUSER", HR, {"*"})
    assert domains.can_see(db, "SYSUSER", OPEN, {"*"})
    domains.invalidate()


def test_visibility_sql():
    assert domains.visibility_sql("C.DOMAIN_ID", None) == ("TRUE", ())
    assert domains.visibility_sql("C.DOMAIN_ID", set()) == ("C.DOMAIN_ID IS NULL", ())
    sql, params = domains.visibility_sql("C.DOMAIN_ID", {"b", "a"})
    assert sql == "(C.DOMAIN_ID IS NULL OR C.DOMAIN_ID IN (%s, %s))" and params == ("a", "b")


def test_stewards_and_fallback():
    db = FakeDb()
    assert domains.stewards_for(db, SALES) == ["ANA"]                # EXPERT is not a steward
    assert domains.stewards_for(db, HR) == ["HANNA"]
    assert domains.stewards_for(db, OPEN) == ["STEVE"]               # no members: DATA_STEWARD holders
    assert domains.stewards_for(db, GENERAL) == ["STEVE"]            # GENERAL has only an EXPERT
    assert domains.is_steward(db, "ana", SALES) and not domains.is_steward(db, "BOB", SALES)
    assert not domains.is_steward(db, "STEVE", SALES)                # named stewards win over the fallback
    assert domains.is_steward(db, "STEVE", OPEN)
    assert domains.is_steward(db, "INHERITS", OPEN, ["DATA_STEWARD", "VIEWER"])   # effective role (inherited)
    assert not domains.is_steward(db, "ZOE", OPEN, ["VIEWER"])


def test_membership_is_cached_for_a_minute():
    db = FakeDb()
    assert not domains.can_see(db, "ZOE", SALES)
    db.members.append({"domain_id": SALES, "user_name": "ZOE", "role": "EXPERT"})
    assert not domains.can_see(db, "ZOE", SALES)
    domains.invalidate()
    assert domains.can_see(db, "ZOE", SALES)


# ---------------------------------------------------------------- 404 for hidden cases

def test_hidden_case_is_404_and_visible_case_is_returned(monkeypatch):
    from app import cases_api

    db = FakeDb(user="ZOE")
    db.on("WHERE C.CASE_ID = %s", lambda sql, p: [{"case_id": p[0], "domain_id": SALES if p[0] == "c-sales" else OPEN}])
    who = {"user": "ZOE", "roles": {"QA_ENGINEER", "VIEWER"}, "privileges": privs("QA_ENGINEER")}
    with pytest.raises(HTTPException) as err:
        cases_api._visible_case(db, who, "c-sales")
    assert err.value.status_code == 404 and err.value.detail == "Case not found"
    assert cases_api._visible_case(db, who, "c-open")["domain_id"] == OPEN
    admin = {"user": "ROOT", "roles": {"SUPER_ADMIN"}, "privileges": {"*"}}
    assert cases_api._visible_case(db, admin, "c-sales")["domain_id"] == SALES


def test_scope_infers_domain_and_refuses_hidden_domain():
    from app import cases_api

    db = FakeDb(user="ZOE")
    db.on("FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE TARGET_TABLE_ID", lambda sql, p: [
        {"target_table_id": p[0], "domain_id": {"t-sales": SALES, "t-open": OPEN}[p[0]], "fqn": "A.B.C"}])
    db.on("FROM CORE.WORKFLOW_RUN", [{"domain_id": OPEN}])
    zoe = {"user": "ZOE", "roles": set(), "privileges": privs("QA_ENGINEER")}
    assert cases_api._scope(db, zoe, None, "t-open", None)["domain_id"] == OPEN
    assert cases_api._scope(db, zoe, None, None, "run-1")["domain_id"] == OPEN
    assert cases_api._scope(db, zoe, None, None, None)["domain_id"] == GENERAL
    with pytest.raises(HTTPException) as err:
        cases_api._scope(db, zoe, None, "t-sales", None)
    assert err.value.status_code == 404
    with pytest.raises(HTTPException) as err:
        cases_api._scope(db, zoe, "no-such-domain", None, None)
    assert err.value.status_code == 404


def test_list_filters_by_visible_domains_and_exact_case_number(monkeypatch):
    from app import cases_api

    db = FakeDb(user="ZOE")
    seen = []
    db.on("FROM CASES.CASE_RECORD C", lambda sql, p: seen.append((sql, p)) or ([] if "C.CASE_NUMBER," in sql else [{"n": 0}]))
    monkeypatch.setattr(cases_api, "_who", lambda _db: {"user": "ZOE", "roles": set(), "privileges": privs("QA_ENGINEER")})
    out = cases_api.list_cases(status=None, severity="P1,P2", kind=None, domain_id=None, mine=True, team_id=None,
                               q="CASE-12", sla=None, limit=50, offset=0, db=db)
    assert out["total"] == 0
    sql, params = seen[0]
    assert "C.DOMAIN_ID IN (%s, %s)" in sql and GENERAL in params and OPEN in params and SALES not in params
    assert "C.CASE_NUMBER = %s" in sql and 12 in params and "ILIKE" not in sql
    assert "UPPER(C.ASSIGNEE) = %s" in sql and "ZOE" in params
    assert all(s in params for s in rules.OPEN_STATUSES) and "RESOLVED" not in params
    with pytest.raises(HTTPException):
        cases_api.list_cases(status="BOGUS", severity=None, kind=None, domain_id=None, mine=False, team_id=None, q=None,
                             sla=None, limit=50, offset=0, db=db)


def test_case_out_shape():
    from app import cases_api

    out = cases_api.case_out({"case_id": "c1", "case_number": 7, "status": "NEW", "sla_breached": 1, "links_count": 2})
    assert out["number"] == "CASE-7" and out["number_value"] == 7 and out["sla_breached"] is True and out["links_count"] == 2
    keys = {"case_id", "number", "domain_id", "domain_name", "title", "kind", "source", "status", "severity", "assignee",
            "team_id", "sla_due_at", "sla_breached", "target_table_id", "target_fqn", "run_id", "ai_summary", "opened_by",
            "opened_at", "updated_at", "links_count"}
    assert keys <= set(out)
    detail = cases_api.case_detail_out({"case_id": "c1", "case_number": 7, "models": '["m1"]', "ai": '{"summary": "x"}'})
    assert detail["models"] == ["m1"] and detail["ai"] == {"summary": "x"}
    assert {"description", "models", "repo_id", "fingerprint", "duplicate_of", "resolution", "ai"} <= set(detail)


# ---------------------------------------------------------------- intake and dedupe

def test_open_then_same_source_ref_returns_the_open_case():
    store = Store()
    first, created, dup = svc.open_case(store, spec(source="JIRA", source_ref="GDPQA-1",
                                                    links=[{"kind": "JIRA", "ref": "GDPQA-1"}]), "ANA", see_all)
    assert created and dup is None and first["status"] == "NEW" and first["case_number"] == 1
    assert store.kinds(first["case_id"]) == ["opened"] and first["sla_hours"] == rules.SLA_HOURS["P2"]
    again, created, dup = svc.open_case(store, spec(source="JIRA", source_ref="GDPQA-1", title="Something else"), "BOB", see_all)
    assert not created and dup is None and again["case_id"] == first["case_id"] and len(store.cases) == 1


def test_fingerprint_match_returns_the_case_with_duplicate_of_and_links_the_new_source():
    store = Store()
    first, _, _ = svc.open_case(store, spec(title="Customer count wrong for 2024-01-01"), "ANA", see_all)
    again, created, dup = svc.open_case(store, spec(title="customer COUNT wrong for 2025-03-09!", source="QA_FAILURE",
                                                    source_ref="r9", links=[{"kind": "QA_RESULT", "ref": "r9"}]),
                                        "BOB", see_all)
    assert not created and dup == first["case_id"] == again["case_id"]
    assert "reported_again" in store.kinds(first["case_id"])
    assert [link["ref"] for link in store.links(first["case_id"])] == ["r9"]
    # another domain or another table is a different problem
    _, created, _ = svc.open_case(store, spec(title="Customer count wrong for 2024-01-01", domain_id=OPEN), "ANA", see_all)
    assert created
    _, created, _ = svc.open_case(store, spec(title="Customer count wrong for 2024-01-01", target_table_id="t2"), "ANA", see_all)
    assert created


def test_resolved_case_does_not_block_a_new_one_from_the_same_reference():
    store = Store()
    first, _, _ = svc.open_case(store, spec(source="JIRA", source_ref="GDPQA-2"), "ANA", see_all)
    store.cases[first["case_id"]]["status"] = "RESOLVED"
    second, created, _ = svc.open_case(store, spec(source="JIRA", source_ref="GDPQA-2", title="Reopened in Jira"), "ANA", see_all)
    assert created and second["case_id"] != first["case_id"]
    assert set(store.claims) == {svc.claim_key("JIRA", "GDPQA-2", 0), svc.claim_key("JIRA", "GDPQA-2", 1)}


def test_losing_the_claim_returns_the_winners_case_and_failed_insert_frees_the_claim():
    store = Store()
    winner, _, _ = svc.open_case(store, spec(source="INCIDENT", source_ref="i1"), "ANA", see_all)
    store.cases[winner["case_id"]]["status"] = "NEW"
    # a concurrent caller that did not see the open case yet: the claim of generation 0 is taken
    store.find_open_by_source = lambda source, ref: None
    store.count_by_source = lambda source, ref: 0
    got, created, _ = svc.open_case(store, spec(source="INCIDENT", source_ref="i1", title="Other words entirely"), "BOB", see_all)
    assert not created and got["case_id"] == winner["case_id"]
    fresh = Store()
    fresh.fail_insert = True
    with pytest.raises(RuntimeError):
        svc.open_case(fresh, spec(source="JIRA", source_ref="K-1"), "ANA", see_all)
    assert fresh.claims == {}


def test_existing_case_in_hidden_domain_is_refused_not_shown():
    store = Store()
    svc.open_case(store, spec(source="JIRA", source_ref="HR-1", domain_id=HR, title="Payroll totals differ"), "HANNA", see_all)
    with pytest.raises(svc.CaseError) as err:
        svc.open_case(store, spec(source="JIRA", source_ref="HR-1", domain_id=HR), "ZOE", lambda d: d != HR)
    assert err.value.status == 409 and "cannot see" in err.value.message
    # a hidden fingerprint twin is not returned: a new case is opened instead
    _, created, _ = svc.open_case(store, spec(domain_id=HR), "HANNA", see_all)
    assert created
    _, created, dup = svc.open_case(store, spec(domain_id=HR, source="APP_REPORT"), "ZOE", lambda d: d != HR)
    assert created and dup is None


def test_fingerprint_normalization():
    assert rules.fingerprint("Load failed 2024-01-01 run 123", "T") == rules.fingerprint("load FAILED 2025-12-31, run 9", "t")
    assert rules.fingerprint("Load failed", "T1") != rules.fingerprint("Load failed", "T2")
    assert rules.normalize_title("Row 5f0c7e2a-1b2c-4d5e-8f90-123456789abc missing") == "row # missing"


def test_severity_and_sla():
    from datetime import datetime, timezone

    assert rules.severity_from_result("CRITICAL") == "P1" and rules.severity_from_result("high") == "P2"
    assert rules.severity_from_result(None) == "P3" and rules.severity_from_jira("Highest") == "P1"
    assert rules.severity_from_jira("Lowest") == "P4" and rules.severity_from_jira("P2") == "P2"
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert (rules.sla_due("P1", t0) - t0).total_seconds() == 4 * 3600
    assert (rules.sla_due("P4", t0) - t0).days == 7
    assert rules.sla_hours({"sla_hours": {"P1": 2, "P9": 1, "P2": "bad"}})["P1"] == 2


def test_edit_moves_the_sla_with_the_severity():
    store = Store()
    case, _, _ = svc.open_case(store, spec(severity="P3"), "ANA", see_all)
    updated = svc.edit(store, case, {"SEVERITY": "P1", "TITLE": case["title"]}, "ANA")
    assert updated["severity"] == "P1" and updated["sla_hours"] == 4
    assert store.events[-1]["detail"] == {"changes": {"severity": "P1"}}
    assert svc.edit(store, updated, {"SEVERITY": "P1"}, "ANA") is updated   # nothing changed, nothing recorded


# ---------------------------------------------------------------- status changes and CASE.RESOLVE

def test_transitions_table_and_privileges():
    work, lead = privs("QA_ENGINEER"), privs("QA_LEAD")
    assert rules.check_transition("NEW", "TRIAGED", work)[0]
    assert rules.check_transition("NEW", "IN_PROGRESS", work)[0]
    ok, code, msg = rules.check_transition("FIX_APPLIED", "VERIFIED", work)
    assert not ok and code == 403 and "CASE.RESOLVE" in msg
    for target in ("CLOSED", "DUPLICATE"):
        assert rules.check_transition("NEW", target, work)[1] == 403
        assert rules.check_transition("NEW", target, lead)[0]
    assert rules.check_transition("FIX_APPLIED", "VERIFIED", lead)[0]
    assert rules.check_transition("NEW", "FIX_APPLIED", lead)[1] == 409          # not in the table
    assert rules.check_transition("NEW", "NEW", lead)[1] == 409
    assert rules.check_transition("NEW", "BOGUS", lead)[1] == 422
    assert rules.check_transition("NEW", "TRIAGED", privs("VIEWER"))[1] == 403
    # reopen needs CASE.WORK only
    for current in ("RESOLVED", "CLOSED", "DUPLICATE"):
        assert rules.check_transition(current, "IN_PROGRESS", work)[0]
    assert set(rules.TRANSITIONS) == set(rules.STATUSES)


def test_resolve_gate():
    lead = privs("QA_LEAD")
    assert rules.check_transition("VERIFIED", "RESOLVED", lead)[0]
    ok, code, msg = rules.check_transition("IN_PROGRESS", "RESOLVED", lead)
    assert not ok and code == 409 and "VERIFIED" in msg
    assert not rules.check_transition("IN_PROGRESS", "RESOLVED", lead, "too short")[0]
    assert rules.check_transition("IN_PROGRESS", "RESOLVED", lead, "fixed upstream by the source team")[0]
    assert rules.check_transition("CLOSED", "RESOLVED", lead, "fixed upstream by the source team")[1] == 409
    assert rules.check_transition("VERIFIED", "RESOLVED", privs("QA_ENGINEER"))[1] == 403


def test_change_status_records_resolver_and_reopen_clears_it():
    store = Store()
    case, _, _ = svc.open_case(store, spec(), "ANA", see_all)
    lead = privs("QA_LEAD")
    case = svc.change_status(store, case, "IN_PROGRESS", "ANA", lead)
    with pytest.raises(svc.CaseError) as err:
        svc.change_status(store, case, "RESOLVED", "LEAD", lead)
    assert err.value.status == 409
    case = svc.change_status(store, case, "RESOLVED", "LEAD", lead, note="Fixed the join", override_reason="fixed upstream by the source")
    assert case["status"] == "RESOLVED" and case["resolved_by"] == "LEAD" and case["resolution"] == "Fixed the join"
    assert store.events[-1]["detail"]["override_reason"] == "fixed upstream by the source"
    case = svc.change_status(store, case, "IN_PROGRESS", "ANA", privs("QA_ENGINEER"))
    assert case["resolved_by"] is None and case["reopened"] == 1 and store.events[-1]["kind"] == "reopened"
    with pytest.raises(svc.CaseError) as err:
        svc.change_status(store, case, "CLOSED", "ANA", privs("QA_ENGINEER"))
    assert err.value.status == 403


def test_stale_status_change_is_a_conflict():
    store = Store()
    case, _, _ = svc.open_case(store, spec(), "ANA", see_all)
    store.cases[case["case_id"]]["status"] = "TRIAGED"     # someone else moved it after we read it
    with pytest.raises(svc.CaseError) as err:
        svc.change_status(store, case, "IN_PROGRESS", "ANA", privs("QA_LEAD"))
    assert err.value.status == 409


# ---------------------------------------------------------------- merge and links

def test_merge_marks_duplicate_links_both_ways_and_moves_links():
    store = Store()
    a, _, _ = svc.open_case(store, spec(title="Orders missing", source="JIRA", source_ref="K-1",
                                        links=[{"kind": "JIRA", "ref": "K-1"}, {"kind": "RUN", "ref": "run-1"}]), "ANA", see_all)
    b, _, _ = svc.open_case(store, spec(title="Order totals off", links=[{"kind": "RUN", "ref": "run-1"}]), "ANA", see_all)
    with pytest.raises(svc.CaseError) as err:
        svc.merge(store, a, b, "ANA", privs("QA_ENGINEER"))
    assert err.value.status == 403
    merged = svc.merge(store, a, b, "LEAD", privs("QA_LEAD"))
    assert merged["status"] == "DUPLICATE" and merged["duplicate_of"] == b["case_id"]
    assert [(link["kind"], link["ref"]) for link in store.links(a["case_id"])] == [("CASE", b["case_id"])]
    assert sorted((link["kind"], link["ref"]) for link in store.links(b["case_id"])) == sorted(
        [("RUN", "run-1"), ("JIRA", "K-1"), ("CASE", a["case_id"])])
    assert "merged" in store.kinds(b["case_id"]) and "merged_into" in store.kinds(a["case_id"])
    with pytest.raises(svc.CaseError):
        svc.merge(store, b, b, "LEAD", privs("QA_LEAD"))
    with pytest.raises(svc.CaseError) as err:
        svc.merge(store, b, store.get(a["case_id"]), "LEAD", privs("QA_LEAD"))   # into a duplicate
    assert err.value.status == 409


def test_links_are_stored_once_and_removal_is_recorded():
    store = Store()
    case, _, _ = svc.open_case(store, spec(), "ANA", see_all)
    assert svc.add_link(store, case, "PR", "https://github.com/x/y/pull/1", None, None, "ANA")
    assert not svc.add_link(store, case, "PR", "https://github.com/x/y/pull/1", None, None, "ANA")
    link_id = store.links(case["case_id"])[0]["link_id"]
    svc.remove_link(store, case, link_id, "ANA")
    assert store.links(case["case_id"]) == [] and store.kinds(case["case_id"])[-1] == "link_removed"
    with pytest.raises(svc.CaseError) as err:
        svc.remove_link(store, case, link_id, "ANA")
    assert err.value.status == 404


def test_link_urls():
    assert rules.link_url("JIRA", "K-1", "https://acme.atlassian.net/") == "https://acme.atlassian.net/browse/K-1"
    assert rules.link_url("JIRA", "K-1") is None
    assert rules.link_url("INCIDENT", "i1") == "/incidents/i1" and rules.link_url("CASE", "c1") == "/qa/cases/c1"
    assert rules.link_url("PR", "javascript:alert(1)") is None
    assert rules.clean_page_context({"path": "/runs/1", "evil": "x", "run_id": None}) == {"path": "/runs/1"}


def test_jira_table_inference_needs_a_strong_unambiguous_match():
    from app import cases_api

    tables = [{"target_table_id": "t1", "domain_id": SALES, "database": "SILVER", "schema": "CRM", "table": "DIM_CUSTOMER",
               "description": "", "active": True, "has_sttm": False},
              {"target_table_id": "t2", "domain_id": SALES, "database": "SILVER", "schema": "CRM", "table": "FCT_ORDERS",
               "description": "", "active": True, "has_sttm": False}]
    top = cases_api.infer_table(tables, {"summary": "Wrong counts in CRM.DIM_CUSTOMER"}, [])
    assert top and top["target_table_id"] == "t1"
    assert cases_api.infer_table(tables, {"summary": "customer numbers look off"}, []) is None    # only word overlap
    assert cases_api.infer_table(tables, {"summary": "CRM.DIM_CUSTOMER and CRM.FCT_ORDERS"}, []) is None   # a tie


def test_from_result_refuses_passing_results(monkeypatch):
    from app import cases_api

    db = FakeDb()
    db.on("FROM QUALITY.QA_RESULT", [{"result_id": "r1", "outcome": "PASS"}])
    monkeypatch.setattr(cases_api, "_who", lambda _db: {"user": "ANA", "roles": set(), "privileges": privs("QA_ENGINEER")})
    with pytest.raises(HTTPException) as err:
        cases_api.case_from_result(cases_api.FromResultIn(qa_result_id="r1"), db)
    assert err.value.status_code == 400
    with pytest.raises(HTTPException) as err:
        cases_api.case_from_result(cases_api.FromResultIn(), db)
    assert err.value.status_code == 422


def test_from_jira_and_incident_need_their_read_privileges(monkeypatch):
    from app import cases_api

    monkeypatch.setattr(cases_api, "_who", lambda _db: {"user": "V", "roles": {"VIEWER"}, "privileges": privs("VIEWER")})
    with pytest.raises(HTTPException) as err:
        cases_api.case_from_jira(cases_api.FromJiraIn(key="GDPQA-1"), FakeDb())
    assert err.value.status_code == 403 and "JIRA.READ" in err.value.detail
    monkeypatch.setattr(cases_api, "_who", lambda _db: {"user": "Q", "roles": set(), "privileges": {"CASE.WORK"}})
    with pytest.raises(HTTPException) as err:
        cases_api.case_from_incident(cases_api.FromIncidentIn(incident_id="i1"), FakeDb())
    assert err.value.status_code == 403 and "OPS.VIEW" in err.value.detail


def test_assignee_must_see_the_domain(monkeypatch):
    from app import cases_api

    db = FakeDb(user="ANA")
    db.on("WHERE C.CASE_ID = %s", [{"case_id": "c1", "domain_id": SALES, "assignee": None}])
    monkeypatch.setattr(cases_api, "_who", lambda _db: {"user": "ANA", "roles": set(), "privileges": privs("QA_ENGINEER")})
    with pytest.raises(HTTPException) as err:
        cases_api.assign_case("c1", cases_api.AssignIn(assignee="zoe"), db)
    assert err.value.status_code == 400
    out = cases_api.assign_case("c1", cases_api.AssignIn(assignee="bob"), db)
    assert out["case"]["case_id"] == "c1"
    assert db.wrote("UPDATE CASES.CASE_RECORD SET")[0][1][0] == "BOB"
    assert db.wrote("INSERT INTO CASES.CASE_EVENT")


# ---------------------------------------------------------------- knowledge approval by domain stewards

def knowledge_db(user, items):
    db = FakeDb(user=user)
    db.on("FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE KNOWLEDGE_ID", lambda sql, p: [i for i in items if i["knowledge_id"] == p[0]])
    db.on("FROM CASES.CASE_RECORD WHERE CASE_ID", [{"resolved_by": "RITA"}])
    return db


def item(kid, domain, created_by="WRITER", origin="RUN", content_json=None):
    return {"knowledge_id": kid, "lineage_id": f"lin-{kid}", "status": "PROPOSED", "domain_id": domain,
            "created_by": created_by, "origin": origin, "content_json": content_json}


def decide_as(monkeypatch, user, roles, items, ids, decision="approve"):
    from app import knowledge_api

    monkeypatch.setattr(knowledge_api, "_approver", lambda _db: {"user": user, "roles": set(roles)})
    monkeypatch.setattr(knowledge_api, "_changed", lambda: None)
    db = knowledge_db(user, items)
    out = knowledge_api.decide(knowledge_api.Decide(ids=ids, decision=decision), db)
    return out, db


def test_steward_approves_own_domain_only(monkeypatch):
    items = [item("k1", SALES), item("k2", HR)]
    out, db = decide_as(monkeypatch, "ANA", ["QA_ENGINEER"], items, ["k1", "k2"])
    assert out["decided"] == 1 and out["decision"] == "approve"
    assert out["refused"] == [{"knowledge_id": "k2", "reason": "only a steward of this domain can decide it"}]
    assert len(db.wrote("STATUS = 'ACTIVE'")) == 1


def test_non_steward_member_is_refused(monkeypatch):
    out, _ = decide_as(monkeypatch, "BOB", ["DATA_STEWARD"], [item("k1", SALES)], ["k1"])   # EXPERT, and named stewards exist
    assert out["decided"] == 0 and out["refused"][0]["reason"].startswith("only a steward")


def test_fallback_to_data_steward_for_domains_without_stewards_and_general(monkeypatch):
    items = [item("k1", OPEN), item("k2", GENERAL)]
    out, _ = decide_as(monkeypatch, "DORA", ["DATA_STEWARD", "VIEWER"], items, ["k1", "k2"])
    assert out["decided"] == 2 and out["refused"] == []
    out, _ = decide_as(monkeypatch, "ZOE", ["VIEWER"], items, ["k1", "k2"])
    assert out["decided"] == 0 and len(out["refused"]) == 2


def test_four_eyes_proposer_and_case_resolver_cannot_approve(monkeypatch):
    items = [item("k1", SALES, created_by="ana"), item("k2", SALES, origin="CASE", content_json='{"resolved_by": "ana"}'),
             item("k3", SALES, origin="CASE", content_json={"case_id": "c1"}), item("k4", SALES, origin="CASE")]
    out, _ = decide_as(monkeypatch, "ANA", [], items, ["k1", "k2", "k3", "k4"])
    reasons = {r["knowledge_id"]: r["reason"] for r in out["refused"]}
    assert set(reasons) == {"k1", "k2"} and "proposed" in reasons["k1"] and "resolved the case" in reasons["k2"]
    assert out["decided"] == 2   # k3's resolver is RITA (case record), k4 has no resolver recorded
    out, _ = decide_as(monkeypatch, "RITA", ["SUPER_ADMIN"], items[2:3], ["k3"])
    assert out["decided"] == 0 and "resolved the case" in out["refused"][0]["reason"]   # even a super admin
    out, _ = decide_as(monkeypatch, "ANA", [], items[:1], ["k1"], decision="reject")
    assert out["decided"] == 1                       # withdrawing your own proposal is fine


def test_super_admin_decides_any_domain_and_not_pending_is_reported(monkeypatch):
    items = [item("k1", HR), {**item("k2", HR), "status": "ACTIVE"}]
    out, _ = decide_as(monkeypatch, "ROOT", ["SUPER_ADMIN"], items, ["k1", "k2", "missing"])
    assert out["decided"] == 1
    assert {r["knowledge_id"]: r["reason"] for r in out["refused"]} == {"k2": "not waiting for review",
                                                                         "missing": "not waiting for review"}


def test_inbox_lists_only_stewarded_domains(monkeypatch):
    from app import knowledge_api

    rows = [{"knowledge_id": k, "domain_id": d, "lineage_id": None, "created_by": "ANA" if k == "k1" else "W",
             "title": k, "content": "", "status": "PROPOSED", "knowledge_type": "QA_TEST"}
            for k, d in (("k1", SALES), ("k2", HR), ("k3", OPEN))]
    monkeypatch.setattr(knowledge_api, "_shape_knowledge", lambda r: {"knowledge_id": r["knowledge_id"], "domain_id": r["domain_id"]})
    db = FakeDb(user="ANA").on("K.STATUS = 'PROPOSED'", rows)
    monkeypatch.setattr(knowledge_api, "_approver", lambda _db: {"user": "ANA", "roles": set()})
    got = knowledge_api.inbox(domain_id=None, db=db)["items"]
    assert [i["knowledge_id"] for i in got] == ["k1"] and got[0]["own"] is True
    monkeypatch.setattr(knowledge_api, "_approver", lambda _db: {"user": "ROOT", "roles": {"SUPER_ADMIN"}})
    assert len(knowledge_api.inbox(domain_id=None, db=db)["items"]) == 3
    monkeypatch.setattr(knowledge_api, "_approver", lambda _db: {"user": "STEVE", "roles": {"DATA_STEWARD"}})
    assert [i["knowledge_id"] for i in knowledge_api.inbox(domain_id=None, db=db)["items"]] == ["k3"]


def test_case_resolution_knowledge_type_waits_for_review():
    from services.knowledge.validate import KNOWLEDGE_TYPES
    from services.knowledge.writer import DEFAULT_POLICY, mode_for

    assert "CASE_RESOLUTION" in KNOWLEDGE_TYPES and DEFAULT_POLICY["CASE_RESOLUTION"] == "review"
    assert mode_for({}, "CASE_RESOLUTION", "CASE") == "review"


# ---------------------------------------------------------------- governance

CASE_ROUTES = [
    ("POST", "/api/cases", "CASE.WORK"),
    ("PUT", "/api/cases/c1", "CASE.WORK"),
    ("POST", "/api/cases/c1/assign", "CASE.WORK"),
    ("POST", "/api/cases/c1/status", "CASE.WORK"),
    ("POST", "/api/cases/c1/comment", "CASE.WORK"),
    ("POST", "/api/cases/c1/links", "CASE.WORK"),
    ("DELETE", "/api/cases/c1/links/l1", "CASE.WORK"),
    ("POST", "/api/cases/c1/merge", "CASE.RESOLVE"),
    ("POST", "/api/cases/from-jira", "CASE.WORK"),
    ("POST", "/api/cases/from-incident", "CASE.WORK"),
    ("POST", "/api/cases/from-result", "CASE.WORK"),
    ("GET", "/api/cases", None),
    ("GET", "/api/cases/summary", None),
    ("GET", "/api/cases/c1", None),
    ("GET", "/api/governance/my-domains", None),
    ("GET", "/api/cases/settings", None),
    ("PUT", "/api/cases/settings", "INTEGRATION.MANAGE"),
    ("POST", "/api/cases/c1/triage", "AI.USE"),
    ("POST", "/api/cases/c1/ask", "AI.USE"),
    ("POST", "/api/cases/c1/artifacts/a1/decide", "CASE.WORK"),
    ("POST", "/api/cases/c1/artifacts/a1/run", "CASE.WORK"),
    ("POST", "/api/cases/c1/artifacts/a1/applied", "CASE.WORK"),
    ("POST", "/api/cases/c1/artifacts/a1/publish", "DBT.EDIT"),
    ("POST", "/api/cases/c1/verify", "CASE.WORK"),
    ("POST", "/api/cases/c1/jira-comment", "JIRA.WRITE"),
    ("POST", "/api/knowledge/inbox/decide", "KNOWLEDGE.EDIT"),
    ("GET", "/api/knowledge/inbox", None),
]


def test_case_routes_governance_mapping():
    for method, path, priv in CASE_ROUTES:
        found, _, matched = privilege_for(method, path)
        assert matched and found == priv, (method, path, found)


def test_every_registered_case_route_is_mapped():
    import re

    expected = {(m, p) for m, p, _ in CASE_ROUTES}
    names = {"case_id": "c1", "link_id": "l1", "artifact_id": "a1"}
    seen = 0
    for route in api_main.app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/cases") or path == "/api/governance/my-domains":
            concrete = re.sub(r"\{([^}]+)\}", lambda m: names[m.group(1)], path)
            for method in route.methods:
                seen += 1
                assert (method, concrete) in expected, (method, path)
    assert seen == len([r for r in CASE_ROUTES if "knowledge" not in r[1]])


def test_privileges_roles_and_version():
    assert SYSTEM_VERSION == 7 and ADDED_PRIVILEGES[7] == ["CASE.WORK", "CASE.RESOLVE", "PACKAGE.EDIT", "PACKAGE.APPROVE"]
    for p in ADDED_PRIVILEGES[7]:
        assert p in PRIVILEGES
    assert {"CASE.WORK", "PACKAGE.EDIT"} <= privs("QA_ENGINEER") and "CASE.RESOLVE" not in privs("QA_ENGINEER")
    assert {"CASE.WORK", "CASE.RESOLVE", "PACKAGE.EDIT", "PACKAGE.APPROVE"} <= privs("QA_LEAD")
    assert {"CASE.WORK", "CASE.RESOLVE", "PACKAGE.EDIT"} <= privs("DATA_ENGINEER")
    assert "PACKAGE.APPROVE" not in privs("DATA_ENGINEER")
    assert "CASE.WORK" in privs("SUPPORT_ENGINEER") and "CASE.RESOLVE" not in privs("SUPPORT_ENGINEER")
    assert not {"CASE.WORK", "CASE.RESOLVE"} & privs("VIEWER")
    assert DEFAULT_POLICIES["PACKAGE.APPROVE"] == "QA_LEAD" and "PACKAGE.APPROVE" in DEFAULT_FOUR_EYES


def test_bootstrap_creates_the_four_eyes_package_policy():
    from app import governance

    class Boot(FakeDb):
        def query(self, sql, params=()):
            if "SELECT ROLE_NAME FROM GOVERNANCE.APP_ROLE" in sql:
                return [{"role_name": r} for r in SYSTEM_ROLES]
            if "SETTING_KEY = 'SYSTEM_VERSION'" in sql:
                return [{"setting_value": "7"}]
            if "FROM GOVERNANCE.USER_ROLE LIMIT 1" in sql:
                return [{"x": 1}]
            return []

    db = Boot()
    saved = dict(governance._seeded)
    governance._seeded["done"] = False
    try:
        governance.bootstrap(db)
    finally:
        governance._seeded.update(saved)
        governance.invalidate()
    package = [w for w in db.wrote("MERGE INTO GOVERNANCE.APPROVAL_POLICY") if "PACKAGE.APPROVE" in w[1]]
    assert package and package[0][1] == ("PACKAGE.APPROVE", "QA_LEAD", True)


# ---------------------------------------------------------------- migration

def test_v034_migration():
    sql = (ROOT / "snowflake" / "database" / "migrations" / "V034__cases.sql").read_text(encoding="utf-8")
    for table in ("CASE_RECORD", "CASE_EVENT", "CASE_LINK", "CASE_ARTIFACT"):
        assert f"CREATE TABLE IF NOT EXISTS {{{{database}}}}.CASES.{table}" in sql
    assert "CASES.CASE_SEQ.NEXTVAL" in sql and "IDEMPOTENCY_KEY" in sql
    assert "ADD ROW ACCESS POLICY {{database}}.CASES.DOMAIN_SCOPE ON (DOMAIN_ID)" in sql
    assert "IS_DATABASE_ROLE_IN_SESSION('PLATFORM_ADMIN')" in sql and "UPPER(DOMAIN_NAME) = 'GENERAL'" in sql
    for role in ("VIEWER", "QA_ENGINEER", "DATA_ENGINEER", "OPS_SERVICE"):
        assert f"DATABASE ROLE {{{{database}}}}.{role}" in sql
    assert '"CASE"' not in sql and chr(0x2014) not in sql and chr(0x2013) not in sql
