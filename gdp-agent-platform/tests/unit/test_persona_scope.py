"""Working as a persona narrows a person's privileges to the persona's bundle, never above what they hold."""

import contextvars

from app.governance import ALL, _persona, current_persona, narrow, narrow_roles


def test_persona_never_grants_more_than_the_person_holds():
    own = {"MAPPING.DECIDE", "RUN.OPERATE", "DBT.EDIT"}
    designer = {"MAPPING.DECIDE", "MAPPING.APPROVE", "MODEL.EDIT"}
    assert narrow(own, designer) == {"MAPPING.DECIDE"}


def test_super_admin_gets_exactly_the_persona_and_super_persona_keeps_everything():
    assert narrow({ALL}, {"QA.EDIT", "QA.SIGNOFF"}) == {"QA.EDIT", "QA.SIGNOFF"}
    assert narrow({"RUN.OPERATE"}, {ALL}) == {"RUN.OPERATE"}
    assert narrow({ALL}, None) == {ALL}   # unknown persona (or before V076): unchanged


def test_persona_is_per_request():
    ctx = contextvars.copy_context()
    ctx.run(_persona.set, "designer")
    assert ctx.run(current_persona) == "designer"
    assert current_persona() is None


def test_role_based_decisions_follow_the_persona_too():
    # a super admin working as Forward Engineering Lead decides change requests as that lead, not as a super admin:
    # an STTM change for the Data Mapping approver is not theirs
    from services.governance.policy import can_approve
    lead = {"DATA_ENGINEER", "SKILL_OWNER", "VIEWER"}
    roles = narrow_roles({"SUPER_ADMIN"}, lead)
    assert roles == lead
    request = {"status": "PENDING", "approver_role": "STTM_APPROVER", "requested_by": "ANA"}
    assert not can_approve(request, "SAM", roles, set(), True)[0]
    assert can_approve(request, "SAM", narrow_roles({"SUPER_ADMIN"}, None), set(), True)[0]   # no persona: everything
    assert narrow_roles({"DATA_ENGINEER", "STTM_APPROVER"}, {"DATA_DESIGNER", "STTM_APPROVER"}) == {"STTM_APPROVER"}
    assert narrow_roles({"DATA_ENGINEER"}, {"SUPER_ADMIN"}) == {"DATA_ENGINEER"}   # the super persona keeps their own
