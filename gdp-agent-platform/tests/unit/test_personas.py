"""Personas (V076): bundles of roles a super admin gives people (services/governance/personas.py), and the API that
assigns them and edits bundles (apps/api/app/personas_api.py), with a fake database."""

import sys
from pathlib import Path

import pytest

from services.governance import personas as ps
from services.governance.policy import privilege_for

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

BUNDLES = {
    "product_owner": {"DATA_STEWARD"},
    "designer": {"DATA_ENGINEER", "MODEL_APPROVER", "STTM_APPROVER", "DQ_APPROVER"},
    "forward_lead": {"DATA_ENGINEER", "SKILL_OWNER"},
    "curator": {"DATA_STEWARD", "SKILL_OWNER"},
    "developer": {"CODE_REVIEWER"},
    "super": {"SUPER_ADMIN"},
}


def test_assigning_grants_every_role_the_bundles_hold():
    assert ps.roles_after_assignment([], [], ["designer", "developer"], BUNDLES) == \
        ["CODE_REVIEWER", "DATA_ENGINEER", "DQ_APPROVER", "MODEL_APPROVER", "STTM_APPROVER"]


def test_removing_a_persona_keeps_roles_another_persona_still_grants():
    current = ps.roles_after_assignment([], [], ["designer", "forward_lead"], BUNDLES)
    after = ps.roles_after_assignment(current, ["designer", "forward_lead"], ["forward_lead"], BUNDLES)
    assert after == ["DATA_ENGINEER", "SKILL_OWNER"]            # DATA_ENGINEER stays: Forward Lead still grants it


def test_a_role_given_directly_outside_any_persona_is_kept():
    after = ps.roles_after_assignment(["QA_ENGINEER", "DATA_STEWARD"], ["product_owner"], [], BUNDLES)
    assert after == ["QA_ENGINEER"]
    assert ps.roles_after_assignment(["QA_ENGINEER"], [], ["developer"], BUNDLES) == ["CODE_REVIEWER", "QA_ENGINEER"]


def test_a_bundle_change_moves_its_holders_but_not_what_others_grant():
    holders = {
        "ANA": {"roles": {"DATA_ENGINEER", "SKILL_OWNER"}, "personas": {"forward_lead"}},
        "BO": {"roles": {"DATA_ENGINEER", "SKILL_OWNER", "MODEL_APPROVER", "STTM_APPROVER", "DQ_APPROVER"}, "personas": {"forward_lead", "designer"}},
        "CY": {"roles": {"CODE_REVIEWER"}, "personas": {"developer"}},
    }
    moved = ps.holders_after_bundle_change(holders, "forward_lead", {"SKILL_OWNER", "CODE_REVIEWER"}, BUNDLES)
    assert set(moved) == {"ANA", "BO"}                           # CY does not hold it
    assert moved["ANA"] == ["CODE_REVIEWER", "SKILL_OWNER"]       # DATA_ENGINEER left with the bundle
    assert "DATA_ENGINEER" in moved["BO"]                         # ...but BO's Designer persona still grants it


def test_people_from_before_personas_get_suggestions_broadest_first():
    assert ps.matching({"DATA_STEWARD", "SKILL_OWNER"}, BUNDLES) == ["curator", "product_owner"]
    assert ps.matching({"VIEWER"}, BUNDLES) == []


@pytest.mark.parametrize("persona,roles,needle", [
    ("designer", [], "at least one role"),
    ("designer", ["NOPE"], "Unknown role"),
    ("designer", ["SUPER_ADMIN"], "Only the Super admin persona"),
    ("super", ["DATA_ENGINEER"], "must grant SUPER_ADMIN"),
    ("designer", ["GOVERNANCE_ADMIN"], "retired"),
])
def test_bundles_are_validated(persona, roles, needle):
    known = {"DATA_ENGINEER", "SUPER_ADMIN", "GOVERNANCE_ADMIN"}
    assert any(needle in p for p in ps.validate_bundle(persona, roles, known))


def test_the_last_super_admin_cannot_be_removed():
    assert ps.removes_last_super("ANA", ["DATA_ENGINEER"], ["ANA"])
    assert not ps.removes_last_super("ANA", ["DATA_ENGINEER"], ["ANA", "BO"])
    assert not ps.removes_last_super("CY", ["DATA_ENGINEER"], ["ANA"])         # CY was never a super admin


def test_persona_routes_are_governance_and_need_role_manage():
    assert privilege_for("PUT", "/api/governance/personas/designer")[0] == "ROLE.MANAGE"
    assert privilege_for("PUT", "/api/governance/people/ANA/personas")[0] == "ROLE.MANAGE"
    assert privilege_for("GET", "/api/governance/people")[0] == "ROLE.MANAGE"
    assert privilege_for("GET", "/api/me/personas")[0] is None


# ---------------------------------------------------------------- the API, with a fake database


class FakeDb:
    user = "ANA"

    def __init__(self, user_roles, user_personas, bundles=BUNDLES):
        self.user_roles = {u: set(r) for u, r in user_roles.items()}
        self.user_personas = {u: set(p) for u, p in user_personas.items()}
        self.bundles = {k: set(v) for k, v in bundles.items()}
        self.writes = []

    def query(self, sql, params=()):
        if "FROM CORE.PERSONA P LEFT JOIN CORE.PERSONA_ROLE" in sql:
            return [{"persona_id": p, "role_name": r} for p, rs in self.bundles.items() for r in (rs or [None])]
        if "FROM CORE.USER_PERSONA" in sql:
            return [{"user_name": u, "persona_id": p} for u, ps_ in self.user_personas.items() for p in ps_]
        if "FROM CORE.USER_ROLE" in sql:
            return [{"user_name": u, "role_name": r} for u, rs in self.user_roles.items() for r in rs]
        if "FROM CORE.PERSONA WHERE PERSONA_ID" in sql:
            return [{"persona_id": params[0]}] if params[0] in self.bundles else []
        if "FROM CORE.APP_ROLE" in sql:
            roles = {r for rs in self.bundles.values() for r in rs} | {"VIEWER", "QA_ENGINEER", "GOVERNANCE_ADMIN"}
            return [{"role_name": r, "description": r} for r in sorted(roles)]
        return []

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.writes.append((sql, params))
        if sql.startswith("DELETE FROM CORE.USER_ROLE"):
            self.user_roles[params[0]] = set()
        elif sql.startswith("INSERT INTO CORE.USER_ROLE"):
            self.user_roles.setdefault(params[0], set()).add(params[1])
        elif sql.startswith("DELETE FROM CORE.USER_PERSONA"):
            self.user_personas[params[0]] = set()
        elif sql.startswith("INSERT INTO CORE.USER_PERSONA"):
            self.user_personas.setdefault(params[0], set()).add(params[1])
        elif sql.startswith("DELETE FROM CORE.PERSONA_ROLE"):
            self.bundles[params[0]] = set()
        elif sql.startswith("INSERT INTO CORE.PERSONA_ROLE"):
            self.bundles.setdefault(params[0], set()).add(params[1])


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.governance as gov
    import app.main as main
    import app.personas_api as api_mod

    async def open_door(request, call_next):
        return await call_next(request)
    monkeypatch.setattr(gov, "middleware", open_door)
    monkeypatch.setattr(api_mod, "_event", lambda *a, **k: None)
    holder = {}

    def use(db):
        holder["db"] = db
        main.app.dependency_overrides[gov.gov_db] = lambda: db
        return db
    yield TestClient(main.app), use
    main.app.dependency_overrides.pop(gov.gov_db, None)


def test_assigning_personas_through_the_api_writes_the_roles(client):
    c, use = client
    db = use(FakeDb({"ANA": {"SUPER_ADMIN"}, "BO": {"QA_ENGINEER"}}, {"ANA": {"super"}}))
    res = c.put("/api/governance/people/bo/personas", json={"personas": ["designer"]})
    assert res.status_code == 200 and res.json()["user"] == "BO"
    assert db.user_roles["BO"] == {"QA_ENGINEER", "DATA_ENGINEER", "MODEL_APPROVER", "STTM_APPROVER", "DQ_APPROVER"}
    assert db.user_personas["BO"] == {"designer"}
    assert c.put("/api/governance/people/bo/personas", json={"personas": ["nope"]}).status_code == 422


def test_the_last_super_admin_keeps_the_super_persona(client):
    c, use = client
    use(FakeDb({"ANA": {"SUPER_ADMIN"}}, {"ANA": {"super"}}))
    res = c.put("/api/governance/people/ANA/personas", json={"personas": ["developer"]})
    assert res.status_code == 409 and "last super admin" in res.json()["detail"]


def test_editing_a_bundle_moves_its_holders(client, monkeypatch):
    import app.personas_api as api_mod
    monkeypatch.setattr(api_mod, "list_personas", lambda db: {"ok": True})
    c, use = client
    db = use(FakeDb({"ANA": {"SUPER_ADMIN"}, "BO": {"DATA_ENGINEER", "SKILL_OWNER"}}, {"ANA": {"super"}, "BO": {"forward_lead"}}))
    res = c.put("/api/governance/personas/forward_lead", json={"roles": ["SKILL_OWNER", "CODE_REVIEWER"]})
    assert res.status_code == 200
    assert db.bundles["forward_lead"] == {"SKILL_OWNER", "CODE_REVIEWER"} and db.user_roles["BO"] == {"SKILL_OWNER", "CODE_REVIEWER"}
    assert c.put("/api/governance/personas/forward_lead", json={"roles": ["SUPER_ADMIN"]}).status_code == 422
    assert c.put("/api/governance/personas/ghost", json={"roles": ["SKILL_OWNER"]}).status_code == 404
