"""Personas (V076): named bundles of governance roles that a super admin assigns to people.

CORE.USER_ROLE stays where privileges come from. A persona only decides which roles a person is given:
- assigning personas grants every role their bundles hold, and removing one drops a role only when no persona the
  person keeps still grants it (a role given directly, outside any persona, is kept);
- changing a bundle moves its holders with it in the same way;
- the matching personas of a person with roles but no persona (people from before personas) are suggested, not given.
Pure: the API (apps/api/app/personas_api.py) reads and writes the tables.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Set

SUPER_PERSONA = "super"
SUPER_ROLE = "SUPER_ADMIN"
VIEWER_ROLE = "VIEWER"
# roles no persona may hold: governance is a super admin's (policy.SUPER_ONLY), so the governance admin role grants
# nothing a persona could use
RETIRED_ROLES = {"GOVERNANCE_ADMIN"}

Bundles = Dict[str, Set[str]]


def roles_of(personas: Iterable[str], bundles: Bundles) -> Set[str]:
    """Every role the given personas grant."""
    out: Set[str] = set()
    for p in personas:
        out |= bundles.get(p, set())
    return out


def roles_after_assignment(current: Iterable[str], before: Iterable[str], after: Iterable[str], bundles: Bundles) -> List[str]:
    """A person's roles once their personas change from `before` to `after`: what the new personas grant, plus the
    roles they had outside any old persona. A person left with no role at all gets nothing (the default role, VIEWER,
    applies on its own)."""
    current, before, after = set(current), set(before), set(after)
    from_before, from_after = roles_of(before, bundles), roles_of(after, bundles)
    direct = current - from_before
    return sorted(direct | from_after)


def holders_after_bundle_change(holders: Dict[str, Dict[str, Set[str]]], persona: str, new_roles: Set[str],
                                bundles: Bundles) -> Dict[str, List[str]]:
    """{user: roles} for everyone holding `persona` once its bundle becomes `new_roles`. `holders` maps a user to
    {"roles": their roles now, "personas": the personas they hold}."""
    after = {**bundles, persona: set(new_roles)}
    out = {}
    for user, h in holders.items():
        if persona not in h["personas"]:
            continue
        direct = set(h["roles"]) - roles_of(h["personas"], bundles)
        out[user] = sorted(direct | roles_of(h["personas"], after))
    return out


def matching(roles: Iterable[str], bundles: Bundles) -> List[str]:
    """The personas whose whole bundle a person already holds (suggestions for people without personas), the
    largest bundles first so a broad persona is not hidden behind a narrow one it contains."""
    have = set(roles)
    fit = [p for p, b in bundles.items() if b and b <= have]
    return sorted(fit, key=lambda p: (-len(bundles[p]), p))


def validate_bundle(persona: str, roles: Iterable[str], known_roles: Iterable[str]) -> List[str]:
    """What is wrong with a bundle, in words."""
    roles, known = set(roles), set(known_roles)
    problems = []
    if not roles:
        problems.append("A persona needs at least one role.")
    unknown = sorted(roles - known)
    if unknown:
        problems.append(f"Unknown role {unknown[0]}.")
    if SUPER_ROLE in roles and persona != SUPER_PERSONA:
        problems.append("Only the Super admin persona can grant SUPER_ADMIN.")
    if persona == SUPER_PERSONA and SUPER_ROLE not in roles:
        problems.append("The Super admin persona must grant SUPER_ADMIN.")
    retired = sorted(roles & RETIRED_ROLES)
    if retired:
        problems.append(f"{retired[0]} is retired: governance belongs to super admins only.")
    return problems


def removes_last_super(user: str, new_roles: Iterable[str], super_holders: Iterable[str]) -> bool:
    """The change would leave the platform with no super admin: it takes SUPER_ADMIN from the last one."""
    holders = {u.upper() for u in super_holders}
    return user.upper() in holders and SUPER_ROLE not in set(new_roles) and not (holders - {user.upper()})
