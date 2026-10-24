"""Working as a persona narrows a person's privileges to the persona's bundle, never above what they hold."""

import contextvars

from app.governance import ALL, _persona, current_persona, narrow


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
