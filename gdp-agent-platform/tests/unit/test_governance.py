from services.governance.policy import (
    ALL, DEFAULT_POLICIES, PRIVILEGES, SYSTEM_ROLES, can_approve, decide, effective_privileges, privilege_for, summarize,
)

ROLE_PRIVS = {name: spec["privileges"] for name, spec in SYSTEM_ROLES.items()}
GRANTS = {name: spec["inherits"] for name, spec in SYSTEM_ROLES.items()}


def test_every_route_rule_names_a_known_privilege():
    for method, path, body in [("POST", "/api/runs/abc/sttm/apply", None), ("PUT", "/api/config/platform", None),
                               ("POST", "/api/runs/abc/review", {"to_state": "STTM_APPROVED"}),
                               ("POST", "/api/runs/abc/something-new", None)]:
        priv, _, _ = privilege_for(method, path, body)
        assert priv in PRIVILEGES
    assert set(DEFAULT_POLICIES) <= set(PRIVILEGES)
    assert all(p in PRIVILEGES or p == ALL for spec in SYSTEM_ROLES.values() for p in spec["privileges"])


def test_route_mapping():
    assert privilege_for("GET", "/api/runs/abc")[0] is None
    assert privilege_for("GET", "/api/config/platform")[0] == "ADMIN.VIEW"
    assert privilege_for("GET", "/api/config/catalog-display")[0] is None
    assert privilege_for("POST", "/api/runs/abc/review", {"to_state": "mapping_approved"})[0] == "MAPPING.APPROVE"
    assert privilege_for("POST", "/api/runs/abc/review", {"to_state": "MAPPING_PENDING"})[0] == "REVIEW.DECIDE"
    assert privilege_for("POST", "/api/runs/abc/model/3/approve")[0] == "MODEL.APPROVE"
    assert privilege_for("POST", "/api/runs/abc/qa/ask")[0] is None
    assert privilege_for("POST", "/api/governance/requests/x/approve")[0] is None
    assert privilege_for("PUT", "/api/governance/users/BOB")[0] == "ROLE.MANAGE"
    unknown = privilege_for("POST", "/api/brand/new")
    assert unknown[0] == "RUN.OPERATE" and unknown[2] is False


def test_inheritance_like_snowflake():
    roles, privs = effective_privileges(["DATA_ENGINEER"], ROLE_PRIVS, GRANTS)
    assert "VIEWER" in roles and "AUDIT.VIEW" in privs and "RUN.OPERATE" in privs and "STTM.APPROVE" not in privs
    roles, privs = effective_privileges(["PLATFORM_ADMIN"], ROLE_PRIVS, GRANTS)
    assert {"PLATFORM_ADMIN", "DATA_ENGINEER", "VIEWER"} <= roles and "CONFIG.EDIT" in privs
    _, privs = effective_privileges(["SUPER_ADMIN"], ROLE_PRIVS, GRANTS)
    assert set(PRIVILEGES) <= privs
    # cycles do not loop forever
    assert effective_privileges(["A"], {"A": ["X"]}, {"A": ["B"], "B": ["A"]})[0] == {"A", "B"}


def test_decisions():
    policy = {"requires_approval": True, "approver_role": "STTM_APPROVER", "four_eyes": False, "active": True}
    _, engineer = effective_privileges(["DATA_ENGINEER"], ROLE_PRIVS, GRANTS)
    _, approver = effective_privileges(["STTM_APPROVER"], ROLE_PRIVS, GRANTS)
    _, viewer = effective_privileges(["VIEWER"], ROLE_PRIVS, GRANTS)
    assert decide("STTM.EDIT", approver, policy)[0] == "ALLOW"
    assert decide("STTM.EDIT", engineer, policy) == ("REQUEST", "needs STTM_APPROVER approval")
    assert decide("STTM.EDIT", viewer, policy)[0] == "FORBID"
    assert decide("STTM.EDIT", approver, {**policy, "four_eyes": True})[0] == "REQUEST"
    assert decide("RUN.OPERATE", engineer, None)[0] == "ALLOW"
    assert decide("RUN.OPERATE", viewer, None)[0] == "FORBID"
    assert decide(None, set(), None)[0] == "ALLOW"


def test_who_may_approve():
    req = {"status": "PENDING", "approver_role": "STTM_APPROVER", "requested_by": "ANA", "allow_self": False}
    assert can_approve(req, "BOB", {"STTM_APPROVER"}, set(), True)[0]
    assert not can_approve(req, "BOB", {"DATA_ENGINEER"}, set(), True)[0]
    assert can_approve(req, "ANA", {"STTM_APPROVER"}, set(), True) == (False, "you cannot approve your own request")
    assert can_approve(req, "ANA", {"SUPER_ADMIN"}, set(), True)[0]          # super admin may self approve (setting)
    assert not can_approve(req, "ANA", {"SUPER_ADMIN"}, set(), False)[0]
    assert not can_approve({**req, "status": "APPLIED"}, "BOB", {"SUPER_ADMIN"}, set(), True)[0]


def test_summary_line():
    line = summarize("POST", "/api/runs/1234567890/sttm/apply", {"title": "Cast END_DATE"}, "Change an STTM transformation")
    assert line == "Change an STTM transformation · run 12345678 · title Cast END_DATE"
