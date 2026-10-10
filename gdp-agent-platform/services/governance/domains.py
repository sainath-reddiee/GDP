"""Domain access: who may see a domain's records (cases, packages, knowledge) and who stewards it.

A domain is visible to a user when one of these holds (the same rule as the CASES.DOMAIN_SCOPE row access policy,
migration V034):
- the domain is GENERAL (KNOWLEDGE.DOMAIN_REGISTRY.DOMAIN_NAME, upper case, as services.knowledge.procedures);
- the user is a member of it (KNOWLEDGE.DOMAIN_MEMBER, any role: OWNER, STEWARD or EXPERT);
- the domain has no members at all (nobody claimed it yet, so it stays open);
- the user is an admin: the SUPER_ADMIN or PLATFORM_ADMIN app role, or the '*' privilege.
A record without a domain (DOMAIN_ID NULL) is visible to everyone.

Stewards of a domain are its OWNER and STEWARD members. A domain with none falls back to the DATA_STEWARD app role:
stewards_for lists the users granted that role directly (GOVERNANCE.USER_ROLE, no inheritance: enough to name who to
ask), while is_steward also accepts a caller whose effective roles (inheritance included) contain DATA_STEWARD.

Membership and the registry are read with the API's Db (%s placeholders) and cached for 60 seconds, like governance.
Records a user cannot see are answered with 404 by the endpoints, so their existence does not leak.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

GENERAL = "GENERAL"
STEWARD_ROLES = ("OWNER", "STEWARD")
ROLE_RANK = {"OWNER": 0, "STEWARD": 1, "EXPERT": 2}
ADMIN_ROLES = {"SUPER_ADMIN", "PLATFORM_ADMIN"}
FALLBACK_ROLE = "DATA_STEWARD"
ALL = "*"
TTL = 60.0

_lock = threading.Lock()
_cache: Dict[str, tuple] = {}


def _cached(key: str, loader: Callable[[], Any], ttl: float = TTL) -> Any:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = loader()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


def invalidate() -> None:
    """Forget cached membership (after members change, and between tests)."""
    with _lock:
        _cache.clear()


def _up(value: Any) -> str:
    return str(value or "").strip().upper()


def _model(db) -> Dict[str, Any]:
    """{"names": {domain_id: name}, "members": {domain_id: {USER: best role}}, "general": {domain_id}}."""
    def load():
        names: Dict[str, str] = {}
        members: Dict[str, Dict[str, str]] = {}
        try:
            for r in db.query("SELECT DOMAIN_ID, DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY"):
                names[str(r["domain_id"])] = str(r.get("domain_name") or "")
        except Exception:
            pass
        try:
            for r in db.query("SELECT DOMAIN_ID, USER_NAME, ROLE FROM KNOWLEDGE.DOMAIN_MEMBER"):
                users = members.setdefault(str(r["domain_id"]), {})
                user, role = _up(r.get("user_name")), _up(r.get("role"))
                if user and (user not in users or ROLE_RANK.get(role, 9) < ROLE_RANK.get(users[user], 9)):
                    users[user] = role
        except Exception:
            pass
        general = {d for d, n in names.items() if _up(n) == GENERAL}
        return {"names": names, "members": members, "general": general}
    return _cached("model", load)


def domain_names(db) -> Dict[str, str]:
    return dict(_model(db)["names"])


def general_domain_id(db) -> Optional[str]:
    found = sorted(_model(db)["general"])
    return found[0] if found else None


def is_admin(privileges: Iterable[str] = (), roles: Iterable[str] = ()) -> bool:
    return ALL in set(privileges or ()) or bool(ADMIN_ROLES & {_up(r) for r in roles or ()})


def session_platform_admin(db) -> bool:
    """Whether the Snowflake session holds the PLATFORM_ADMIN database role, which the CASES.DOMAIN_SCOPE row policy
    uses for its "see everything" branch. An app admin without it is still filtered by Snowflake, so the app applies
    the membership rules too instead of creating records the caller cannot read back."""
    key = f"pa:{getattr(db, 'user', '')}:{getattr(db, 'role', '')}"

    def load() -> bool:
        try:
            row = db.query("SELECT IS_DATABASE_ROLE_IN_SESSION('PLATFORM_ADMIN') AS P")
            return bool(row[0].get("p")) if row else True
        except Exception:  # a session that cannot evaluate it (tests, older accounts): trust the app roles
            return True
    return bool(_cached(key, load))


def admin_bypass(db, privileges: Iterable[str] = (), roles: Iterable[str] = ()) -> bool:
    """Every domain is visible only to an app admin whose session Snowflake also lets through."""
    return is_admin(privileges, roles) and session_platform_admin(db)


def member_domains(db, user: str) -> Dict[str, str]:
    """{domain_id: role} for the domains the user is a member of (the strongest role when there are several)."""
    who = _up(user)
    return {d: users[who] for d, users in _model(db)["members"].items() if who in users}


def visible_domain_ids(db, user: str, privileges: Iterable[str] = (), roles: Iterable[str] = ()) -> Optional[Set[str]]:
    """The domain ids the user may see, or None for every domain (admins)."""
    if admin_bypass(db, privileges, roles):
        return None
    model = _model(db)
    who = _up(user)
    out = set(model["general"])
    for domain_id in model["names"]:
        users = model["members"].get(domain_id) or {}
        if not users or who in users:
            out.add(domain_id)
    # members of a domain missing from the registry still see it
    out |= {d for d, users in model["members"].items() if who in users}
    return out


def can_see(db, user: str, domain_id: Optional[str], privileges: Iterable[str] = (), roles: Iterable[str] = ()) -> bool:
    if not domain_id or admin_bypass(db, privileges, roles):
        return True
    model = _model(db)
    if domain_id in model["general"]:
        return True
    users = model["members"].get(domain_id) or {}
    return not users or _up(user) in users


def named_stewards(db, domain_id: Optional[str]) -> List[str]:
    """OWNER and STEWARD members of the domain."""
    users = _model(db)["members"].get(domain_id or "") or {}
    return sorted(u for u, role in users.items() if role in STEWARD_ROLES)


def _role_holders(db, role: str) -> List[str]:
    def load():
        try:
            return sorted({_up(r["user_name"]) for r in db.query(
                "SELECT USER_NAME FROM GOVERNANCE.USER_ROLE WHERE ROLE_NAME = %s", (role,)) if r.get("user_name")})
        except Exception:
            return []
    return _cached(f"holders:{role}", load)


def stewards_for(db, domain_id: Optional[str]) -> List[str]:
    """Who approves the domain's knowledge: its OWNER and STEWARD members, else the direct holders of DATA_STEWARD."""
    return named_stewards(db, domain_id) or _role_holders(db, FALLBACK_ROLE)


def is_steward(db, user: str, domain_id: Optional[str], roles: Iterable[str] = ()) -> bool:
    """A named steward of the domain, or (when it has none) a DATA_STEWARD holder. Admin rights are checked by the
    caller (SUPER_ADMIN may approve anything)."""
    named = named_stewards(db, domain_id)
    who = _up(user)
    if named:
        return who in named
    return FALLBACK_ROLE in {_up(r) for r in roles or ()} or who in _role_holders(db, FALLBACK_ROLE)


def direct_roles(db, user: str) -> List[str]:
    """App roles granted to the user directly (for checks about someone other than the caller)."""
    try:
        return sorted({_up(r["role_name"]) for r in db.query(
            "SELECT ROLE_NAME FROM GOVERNANCE.USER_ROLE WHERE USER_NAME = %s", (_up(user),)) if r.get("role_name")})
    except Exception:
        return []


def visibility_sql(column: str, visible: Optional[Set[str]]) -> tuple:
    """(SQL condition, params) limiting `column` to the visible domains; rows without a domain always pass."""
    if visible is None:
        return "TRUE", ()
    ids = sorted(visible)
    if not ids:
        return f"{column} IS NULL", ()
    return f"({column} IS NULL OR {column} IN ({', '.join(['%s'] * len(ids))}))", tuple(ids)
