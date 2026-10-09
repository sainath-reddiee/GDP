"""Governance model (pure): privileges, system roles, which API call needs which privilege, and the decision
for one call: ALLOW, REQUEST (store a change request routed to an approver role) or FORBID.

Like Snowflake: privileges are granted to roles, roles are granted to roles (inheritance) and to users, and a
user's effective privileges are the union over every role they hold (secondary roles ALL). A user who holds the
privilege of an action performs it; a user who does not, but may raise requests, has it routed to the approver role
of the action's policy. "Four eyes" on a policy makes even privilege holders go through approval.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# code -> (group, description)
PRIVILEGES: Dict[str, Tuple[str, str]] = {
    "SOURCE.CONNECT": ("Sources", "Connect and configure sources (Snowflake, Oracle, files)"),
    "PROFILE.RUN": ("Sources", "Profile tables and land data"),
    "RUN.CREATE": ("Runs", "Create runs and modeling runs"),
    "RUN.OPERATE": ("Runs", "Run pipeline steps (generate mapping, STTM, checks, dbt, validation)"),
    "RUN.ARCHIVE": ("Runs", "Archive, restore and clean up runs"),
    "MODEL.EDIT": ("Data model", "Design and edit target models"),
    "MODEL.APPROVE": ("Data model", "Approve a model design as the run's target"),
    "MAPPING.DECIDE": ("Mapping", "Accept or reject mapping candidates"),
    "MAPPING.APPROVE": ("Mapping", "Approve the mapping gate"),
    "STTM.EDIT": ("STTM", "Change STTM transformations and joins"),
    "STTM.APPROVE": ("STTM", "Approve the STTM gate"),
    "SODA.EDIT": ("Data quality", "Add, edit and decide data quality checks"),
    "SODA.APPROVE": ("Data quality", "Approve the data quality pack"),
    "QA.EDIT": ("QA", "Write, edit and run QA tests"),
    "QA.SIGNOFF": ("QA", "Sign off QA"),
    "DBT.EDIT": ("dbt", "Generate, enhance and publish dbt"),
    "REVIEW.APPROVE": ("Code review", "Approve code review"),
    "REVIEW.DECIDE": ("Code review", "Send a stage back or request changes"),
    "KNOWLEDGE.EDIT": ("Knowledge", "Add, edit, retire and restore knowledge"),
    "DOMAIN.EDIT": ("Knowledge", "Create, import, edit and delete domains"),
    "TAG.MANAGE": ("Knowledge", "Tag profiles, runs and models"),
    "SKILL.EDIT": ("Skills", "Create skill versions and categories, set a candidate, try a version on a run"),
    "SKILL.RELEASE": ("Skills", "Promote a skill version to production and change which skills each stage loads"),
    "CONFIG.EDIT": ("Admin", "Change platform settings, rules and models"),
    "ADMIN.VIEW": ("Admin", "Open the Admin panel"),
    "ADMIN.DEPLOY": ("Admin", "Redeploy procedures and integrations (GitHub)"),
    "ROLE.MANAGE": ("Governance", "Manage users, roles, privileges and approval policies"),
    "APPROVAL.VIEW": ("Governance", "See every change request"),
    "REQUEST.CHANGES": ("Governance", "Raise change requests for actions you cannot do yourself"),
    "AUDIT.VIEW": ("Governance", "Open the audit trail"),
    "AI.USE": ("AI", "Use AI: copilot, AI review, ask for tests or checks, AI designs (each call costs credits)"),
}
# Privileges a viewer-style role never needs: holding none of the others means the user can only look.
READ_ONLY = {"AUDIT.VIEW", "ADMIN.VIEW", "APPROVAL.VIEW"}
ALL = "*"

SYSTEM_ROLES: Dict[str, Dict[str, Any]] = {
    "SUPER_ADMIN": {"description": "Everything, including governance", "privileges": [ALL], "inherits": []},
    "PLATFORM_ADMIN": {"description": "Platform settings and deployment",
                       "privileges": ["CONFIG.EDIT", "ADMIN.VIEW", "ADMIN.DEPLOY", "AUDIT.VIEW", "APPROVAL.VIEW"],
                       "inherits": ["DATA_ENGINEER"]},
    "GOVERNANCE_ADMIN": {"description": "Users, roles and approval policies",
                         "privileges": ["ROLE.MANAGE", "ADMIN.VIEW", "APPROVAL.VIEW", "AUDIT.VIEW"], "inherits": ["VIEWER"]},
    "MODEL_APPROVER": {"description": "Approves data models and mapping",
                       "privileges": ["MODEL.APPROVE", "MAPPING.APPROVE", "MODEL.EDIT", "MAPPING.DECIDE", "AI.USE"], "inherits": ["VIEWER"]},
    "STTM_APPROVER": {"description": "Approves STTM changes and the STTM gate",
                      "privileges": ["STTM.EDIT", "STTM.APPROVE", "AI.USE"], "inherits": ["VIEWER"]},
    "DQ_APPROVER": {"description": "Approves data quality checks and the pack",
                    "privileges": ["SODA.EDIT", "SODA.APPROVE", "AI.USE"], "inherits": ["VIEWER"]},
    "QA_LEAD": {"description": "Owns QA tests and sign-off", "privileges": ["QA.EDIT", "QA.SIGNOFF", "AI.USE"], "inherits": ["VIEWER"]},
    "CODE_REVIEWER": {"description": "Approves dbt and code review",
                      "privileges": ["REVIEW.APPROVE", "REVIEW.DECIDE", "DBT.EDIT", "AI.USE"], "inherits": ["VIEWER"]},
    "DATA_STEWARD": {"description": "Owns domains and knowledge",
                     "privileges": ["KNOWLEDGE.EDIT", "DOMAIN.EDIT", "TAG.MANAGE", "SKILL.EDIT", "AI.USE"], "inherits": ["VIEWER"]},
    "SKILL_OWNER": {"description": "Owns agent skills: versions, releases and stage bindings",
                    "privileges": ["SKILL.EDIT", "SKILL.RELEASE", "AI.USE"], "inherits": ["VIEWER"]},
    "DATA_ENGINEER": {"description": "Builds runs end to end; approvals go to the owning roles",
                      "privileges": ["SOURCE.CONNECT", "PROFILE.RUN", "RUN.CREATE", "RUN.OPERATE", "RUN.ARCHIVE",
                                     "MODEL.EDIT", "MAPPING.DECIDE", "QA.EDIT", "DBT.EDIT", "TAG.MANAGE", "SKILL.EDIT",
                                     "REQUEST.CHANGES", "REVIEW.DECIDE", "AI.USE"],
                      "inherits": ["VIEWER"]},
    "VIEWER": {"description": "Read everything, change nothing (no AI calls, no requests)", "privileges": ["AUDIT.VIEW"],
               "inherits": []},
}
# Privileges added to system roles after their first release: {version: [privilege]}. Bootstrap grants them to the
# system roles whose spec lists them, once, so existing deployments pick them up without overriding admin edits.
SYSTEM_VERSION = 3
ADDED_PRIVILEGES = {2: ["AI.USE"], 3: ["SKILL.EDIT", "SKILL.RELEASE"]}

# Actions routed for approval by default: privilege -> approver role. Everything else is privilege-only.
DEFAULT_POLICIES: Dict[str, str] = {
    "MODEL.APPROVE": "MODEL_APPROVER", "MAPPING.APPROVE": "MODEL_APPROVER",
    "STTM.EDIT": "STTM_APPROVER", "STTM.APPROVE": "STTM_APPROVER",
    "SODA.EDIT": "DQ_APPROVER", "SODA.APPROVE": "DQ_APPROVER",
    "QA.SIGNOFF": "QA_LEAD", "REVIEW.APPROVE": "CODE_REVIEWER",
    "KNOWLEDGE.EDIT": "DATA_STEWARD", "DOMAIN.EDIT": "DATA_STEWARD",
    "CONFIG.EDIT": "PLATFORM_ADMIN", "ADMIN.DEPLOY": "PLATFORM_ADMIN", "ROLE.MANAGE": "GOVERNANCE_ADMIN",
    "SKILL.RELEASE": "SKILL_OWNER",
}

REVIEW_TARGETS = {"MAPPING_APPROVED": "MAPPING.APPROVE", "STTM_APPROVED": "STTM.APPROVE",
                  "SODA_APPROVED": "SODA.APPROVE", "DBT_APPROVED": "REVIEW.APPROVE"}

R = "/api/runs/[^/]+"
# (method, path regex, privilege or callable(body) -> privilege, title)
RULES: List[Tuple[str, str, Any, str]] = [
    ("POST", r"/api/auth/(login|logout)", None, ""),
    ("PUT", r"/api/auth/role", None, ""),
    ("POST", r"/api/(knowledge/search|catalog/analyze|catalog/preview-graph|oracle/parse)", None, ""),
    ("POST", r"/api/(agent/stream|copilot/ask|knowledge/answer)", "AI.USE", ""),
    ("POST", r"/api/oracle/integrations/check", "SOURCE.CONNECT", ""),
    ("POST", rf"{R}/model/validate", None, ""),
    ("POST", rf"{R}/(mapping/assist|qa/ask|qa/plan|sttm/refine)", "AI.USE", ""),
    ("POST", rf"{R}/soda/backtest", "RUN.OPERATE", ""),
    ("POST", r"/api/governance/requests/[^/]+/(approve|reject|cancel)", None, ""),  # checked by the endpoint
    ("POST", r"/api/governance/roles", "ROLE.MANAGE", "Create a role"),
    ("POST", r"/api/governance/roles/[^/]+/members", "ROLE.MANAGE", "Change role members"),
    ("PUT", r"/api/governance/(users|roles|policies)/[^/]+", "ROLE.MANAGE", "Change access or an approval policy"),
    ("DELETE", r"/api/governance/roles/[^/]+", "ROLE.MANAGE", "Delete a role"),
    ("PUT", r"/api/governance/settings", "ROLE.MANAGE", "Change governance settings"),
    ("POST", r"/api/admin/apply", "ADMIN.DEPLOY", "Redeploy procedures and the agent"),
    ("POST", r"/api/dbt/(github/setup|github/token|git-repository)", "ADMIN.DEPLOY", "Change the GitHub integration"),
    ("POST", r"/api/dbt/github/check", "ADMIN.VIEW", ""),
    ("PUT", r"/api/config/(platform|rules)", "CONFIG.EDIT", "Change platform settings"),
    ("POST", r"/api/config/models/test", "ADMIN.VIEW", ""),
    ("POST", r"/api/costs/reconcile", "CONFIG.EDIT", "Reconcile AI cost"),
    ("POST", r"/api/(domains|domains/draft|domains/import)", "DOMAIN.EDIT", "Create or import a domain"),
    ("DELETE", r"/api/domains/[^/]+", "DOMAIN.EDIT", "Delete a domain"),
    ("POST", r"/api/domains/[^/]+/restore", "DOMAIN.EDIT", "Restore a domain"),
    ("PUT", r"/api/domains/[^/]+/rules", "DOMAIN.EDIT", "Change domain rules"),
    ("POST", r"/api/domains/[^/]+/(ask|suggestions)", "AI.USE", ""),
    ("POST", r"/api/domains/[^/]+/suggestions/decision", "DOMAIN.EDIT", "Accept a domain suggestion"),
    ("POST", r"/api/knowledge", "KNOWLEDGE.EDIT", "Add knowledge"),
    ("PUT", r"/api/knowledge/[^/]+", "KNOWLEDGE.EDIT", "Edit knowledge"),
    ("POST", r"/api/knowledge/[^/]+/(retire|restore)", "KNOWLEDGE.EDIT", "Retire or restore knowledge"),
    ("PUT", r"/api/tags/.*", "TAG.MANAGE", ""),
    ("POST", r"/api/skills/[^/]+/labels/production", "SKILL.RELEASE", "Promote a skill version to production"),
    ("POST", r"/api/skills/[^/]+/labels/candidate", "SKILL.EDIT", ""),
    ("DELETE", r"/api/skills/[^/]+/labels/candidate", "SKILL.EDIT", ""),
    ("PUT", r"/api/skills/bindings", "SKILL.RELEASE", "Change which skills each stage loads"),
    ("POST", r"/api/skills/[^/]+/versions", "SKILL.EDIT", ""),
    ("POST", r"/api/skills/[^/]+/versions/[^/]+/(retire|restore)", "SKILL.EDIT", ""),
    ("PUT", r"/api/skills/[^/]+/category", "SKILL.EDIT", ""),
    ("POST", r"/api/skills/categories", "SKILL.EDIT", ""),
    ("PUT", r"/api/skills/categories/[^/]+", "SKILL.EDIT", ""),
    ("DELETE", r"/api/skills/categories/[^/]+", "SKILL.EDIT", ""),
    ("PUT", rf"{R}/skills/[^/]+", "SKILL.EDIT", "Try a skill version on a run"),
    ("DELETE", rf"{R}/skills/[^/]+", "SKILL.EDIT", ""),
    ("POST", r"/api/(sources|sources/external)", "SOURCE.CONNECT", "Connect a source"),
    ("DELETE", r"/api/sources/[^/]+/oracle(/schedule)?", "SOURCE.CONNECT", "Remove an Oracle source or schedule"),
    ("PUT", r"/api/sources/[^/]+/oracle/(connection|schedule)", "SOURCE.CONNECT", "Change an Oracle connection"),
    ("POST", r"/api/sources/[^/]+/oracle/(setup|password)", "SOURCE.CONNECT", "Set up an Oracle source"),
    ("POST", r"/api/sources/[^/]+/oracle/(test|catalog|columns|preview)", "PROFILE.RUN", ""),
    ("POST", r"/api/sources/[^/]+/(oracle/ingest|oracle/profile|land|upload|profile-tables)", "PROFILE.RUN", ""),
    ("POST", r"/api/catalog/profile-tables", "PROFILE.RUN", ""),
    ("POST", r"/api/ingest-jobs/[^/]+/cancel", "PROFILE.RUN", ""),
    ("POST", r"/api/(sources/[^/]+/modeling-run|catalog/modeling-run|runs)", "RUN.CREATE", ""),
    ("POST", r"/api/targets/register", "MODEL.EDIT", ""),
    ("POST", r"/api/runs/(batch-archive|batch-cleanup)", "RUN.ARCHIVE", ""),
    ("POST", rf"{R}/(archive|restore)", "RUN.ARCHIVE", ""),
    ("POST", rf"{R}/review", lambda body: REVIEW_TARGETS.get(str((body or {}).get("to_state") or "").upper(), "REVIEW.DECIDE"),
     "Stage decision"),
    ("POST", rf"{R}/transition", "ADMIN.DEPLOY", "Force a state transition"),
    ("POST", rf"{R}/model/design", "MODEL.EDIT", ""),
    ("PUT", rf"{R}/model", "MODEL.EDIT", ""),
    ("POST", rf"{R}/model/\d+/approve", "MODEL.APPROVE", "Approve a data model"),
    ("PUT", rf"{R}/(target|intent|domain)", "MODEL.EDIT", "Change the run's target"),
    ("POST", rf"{R}/mapping/decisions", "MAPPING.DECIDE", ""),
    ("POST", rf"{R}/sttm/apply", "STTM.EDIT", "Change an STTM transformation"),
    ("PUT", rf"{R}/sttm/joins", "STTM.EDIT", "Change the STTM join plan"),
    ("POST", rf"{R}/soda/(checks|decisions|import)", "SODA.EDIT", "Change data quality checks"),
    ("POST", rf"{R}/qa/signoff", "QA.SIGNOFF", "QA sign-off"),
    ("POST", rf"{R}/qa/(tests|run)", "QA.EDIT", ""),
    ("PUT", rf"{R}/qa/tests/[^/]+", "QA.EDIT", ""),
    ("DELETE", rf"{R}/qa/tests/[^/]+", "QA.EDIT", ""),
    ("POST", rf"{R}/dbt(/enhance|/publish|/review)?", "DBT.EDIT", ""),
    ("POST", rf"{R}/suggestions/[^/]+/decision", "RUN.OPERATE", ""),
    ("POST", rf"{R}/suggestions/[^/]+", "AI.USE", ""),
    ("POST", rf"{R}/.*", "RUN.OPERATE", ""),
]
_COMPILED = [(m, re.compile(f"^{p}$"), priv, title) for m, p, priv, title in RULES]
READ_RULES = [(re.compile(r"^/api/(admin/.*|config/(rules|platform|models))$"), "ADMIN.VIEW"),
              (re.compile(r"^/api/(audit|costs)(/.*)?$"), "AUDIT.VIEW"),
              (re.compile(r"^/api/governance/(roles|users|policies|settings|events|privileges)$"), "ADMIN.VIEW")]


def privilege_for(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], str, bool]:
    """(privilege or None when anyone signed in may call it, title, matched). Unknown mutating calls need
    RUN.OPERATE so nothing new slips through ungoverned."""
    method = method.upper()
    if method in ("GET", "HEAD", "OPTIONS"):
        for pattern, priv in READ_RULES:
            if pattern.match(path):
                return priv, "", True
        return None, "", True
    for m, pattern, priv, title in _COMPILED:
        if m == method and pattern.match(path):
            return (priv(body) if callable(priv) else priv), title, True
    return "RUN.OPERATE", "", False


def effective_privileges(user_roles: Iterable[str], role_privileges: Dict[str, Iterable[str]],
                         role_grants: Dict[str, Iterable[str]]) -> Tuple[Set[str], Set[str]]:
    """(all roles including inherited ones, privileges). A role with '*' holds every privilege."""
    roles: Set[str] = set()
    stack = [r.upper() for r in user_roles]
    while stack:
        role = stack.pop()
        if role in roles:
            continue
        roles.add(role)
        stack.extend(r.upper() for r in role_grants.get(role, []))
    privs: Set[str] = set()
    for role in roles:
        privs.update(role_privileges.get(role, []))
    if ALL in privs:
        privs = set(PRIVILEGES) | {ALL}
    return roles, privs


def decide(privilege: Optional[str], privileges: Set[str], policy: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """('ALLOW' | 'REQUEST' | 'FORBID', reason)."""
    if not privilege:
        return "ALLOW", ""
    active = bool(policy and policy.get("active", True) and policy.get("requires_approval", True))
    holds = privilege in privileges or ALL in privileges
    if holds and not (active and policy.get("four_eyes")):
        return "ALLOW", ""
    if active and ("REQUEST.CHANGES" in privileges or holds):
        return "REQUEST", f"needs {policy.get('approver_role')} approval"
    return "FORBID", f"needs the {privilege} privilege ({PRIVILEGES.get(privilege, ('', privilege))[1]})"


def can_approve(request: Dict[str, Any], user: str, roles: Set[str], privileges: Set[str],
                super_self_approve: bool) -> Tuple[bool, str]:
    """May this user approve or reject the request?"""
    if request.get("status") != "PENDING":
        return False, "the request is no longer pending"
    approver = str(request.get("approver_role") or "").upper()
    own = str(request.get("requested_by") or "").upper() == str(user or "").upper()
    is_super = "SUPER_ADMIN" in roles
    if own and not (request.get("allow_self") or (is_super and super_self_approve)):
        return False, "you cannot approve your own request"
    if is_super or approver in roles:
        return True, ""
    return False, f"only {approver} can decide this request"


def summarize(method: str, path: str, body: Optional[Dict[str, Any]], title: str) -> str:
    """One line for the approvals inbox."""
    parts = path.strip("/").split("/")
    run = parts[2][:8] if len(parts) > 2 and parts[1] == "runs" else ""
    detail = ""
    if isinstance(body, dict):
        for key in ("to_state", "decision", "title", "target_column", "key", "table", "name"):
            if body.get(key):
                detail = f"{key.replace('_', ' ')} {str(body[key])[:60]}"
                break
    return " · ".join(x for x in (title or f"{method} {path}", f"run {run}" if run else "", detail) if x)


def read_only(privileges: Iterable[str]) -> bool:
    """True when the user can only look: no privilege beyond the viewing ones."""
    privs = set(privileges)
    return ALL not in privs and not (privs - READ_ONLY)


def creates_cycle(role: str, inherits: Iterable[str], grants: Dict[str, Iterable[str]]) -> bool:
    """Would granting `inherits` to `role` make a loop (role inheriting itself)?"""
    role = role.upper()
    stack, seen = [r.upper() for r in inherits], set()
    while stack:
        r = stack.pop()
        if r == role:
            return True
        if r in seen:
            continue
        seen.add(r)
        stack.extend(x.upper() for x in grants.get(r, []))
    return False
