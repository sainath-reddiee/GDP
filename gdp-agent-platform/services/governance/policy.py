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
    "INTEGRATION.MANAGE": ("Integrations", "Connect code repositories and other external systems, and schedule their refresh"),
    "CODE.VIEW": ("Integrations", "Search and read indexed client code"),
    "JIRA.READ": ("Jira", "Connect your own Jira account, read issues and triage them against a run"),
    "JIRA.WRITE": ("Jira", "Link issues to runs and tests, comment on and change the status of Jira issues, as yourself"),
    "OPS.VIEW": ("Operations", "See Airflow pipelines, runs, task logs and incidents"),
    "OPS.OPERATE": ("Operations", "Poll Airflow now, change DAG settings, and acknowledge, assign, resolve, mute or retry incidents"),
    "OPS.FIX_APPROVE": ("Operations", "Approve or reject running a known fix on an Airflow failure"),
    "OPS.FIX_PUBLISH": ("Operations", "Review and publish known fixes (never your own), and retire them"),
    "OPS.RESOLVE_ADMIN": ("Operations", "Turn automatic resolution on or off and set pipeline scopes and fix approvers"),
    "CASE.WORK": ("Cases", "Open, assign, triage, comment on and link cases, and propose fixes"),
    "CASE.RESOLVE": ("Cases", "Verify, resolve, close and mark cases as duplicates"),
    "PACKAGE.EDIT": ("Cases", "Edit domain test packages"),
    "PACKAGE.APPROVE": ("Cases", "Approve a domain test package version"),
}
# Privileges a viewer-style role never needs: holding none of the others means the user can only look.
READ_ONLY = {"AUDIT.VIEW", "ADMIN.VIEW", "APPROVAL.VIEW", "CODE.VIEW", "OPS.VIEW"}
ALL = "*"

SYSTEM_ROLES: Dict[str, Dict[str, Any]] = {
    "SUPER_ADMIN": {"description": "Everything, including governance", "privileges": [ALL], "inherits": []},
    "PLATFORM_ADMIN": {"description": "Platform settings and deployment",
                       "privileges": ["CONFIG.EDIT", "ADMIN.VIEW", "ADMIN.DEPLOY", "AUDIT.VIEW", "APPROVAL.VIEW", "INTEGRATION.MANAGE",
                                      "OPS.FIX_APPROVE", "OPS.FIX_PUBLISH", "OPS.RESOLVE_ADMIN"],
                       "inherits": ["DATA_ENGINEER"]},
    "GOVERNANCE_ADMIN": {"description": "Users, roles and approval policies",
                         "privileges": ["ROLE.MANAGE", "ADMIN.VIEW", "APPROVAL.VIEW", "AUDIT.VIEW"], "inherits": ["VIEWER"]},
    "MODEL_APPROVER": {"description": "Approves data models and mapping",
                       "privileges": ["MODEL.APPROVE", "MAPPING.APPROVE", "MODEL.EDIT", "MAPPING.DECIDE", "AI.USE"], "inherits": ["VIEWER"]},
    "STTM_APPROVER": {"description": "Approves STTM changes and the STTM gate",
                      "privileges": ["STTM.EDIT", "STTM.APPROVE", "AI.USE"], "inherits": ["VIEWER"]},
    "DQ_APPROVER": {"description": "Approves data quality checks and the pack",
                    "privileges": ["SODA.EDIT", "SODA.APPROVE", "AI.USE"], "inherits": ["VIEWER"]},
    "QA_LEAD": {"description": "Owns QA tests and sign-off", "privileges": ["QA.EDIT", "QA.SIGNOFF", "AI.USE", "JIRA.READ", "JIRA.WRITE",
                                                                           "CASE.WORK", "CASE.RESOLVE", "PACKAGE.EDIT",
                                                                           "PACKAGE.APPROVE"],
                "inherits": ["VIEWER"]},
    "QA_ENGINEER": {"description": "Writes and runs QA tests and works Jira issues; no sign-off",
                    "privileges": ["QA.EDIT", "AI.USE", "JIRA.READ", "JIRA.WRITE", "CASE.WORK", "PACKAGE.EDIT"],
                    "inherits": ["VIEWER"]},
    "SUPPORT_ENGINEER": {"description": "Watches Airflow pipelines and works incidents and their Jira tickets",
                         "privileges": ["OPS.VIEW", "OPS.OPERATE", "OPS.FIX_APPROVE", "AI.USE", "JIRA.READ", "JIRA.WRITE",
                                        "CASE.WORK"],
                         "inherits": ["VIEWER"]},
    "CODE_REVIEWER": {"description": "Approves dbt and code review",
                      "privileges": ["REVIEW.APPROVE", "REVIEW.DECIDE", "DBT.EDIT", "AI.USE"], "inherits": ["VIEWER"]},
    "DATA_STEWARD": {"description": "Owns domains and knowledge",
                     "privileges": ["KNOWLEDGE.EDIT", "DOMAIN.EDIT", "TAG.MANAGE", "SKILL.EDIT", "AI.USE"], "inherits": ["VIEWER"]},
    "SKILL_OWNER": {"description": "Owns agent skills: versions, releases and stage bindings",
                    "privileges": ["SKILL.EDIT", "SKILL.RELEASE", "AI.USE"], "inherits": ["VIEWER"]},
    "DATA_ENGINEER": {"description": "Builds runs end to end; approvals go to the owning roles",
                      "privileges": ["SOURCE.CONNECT", "PROFILE.RUN", "RUN.CREATE", "RUN.OPERATE", "RUN.ARCHIVE",
                                     "MODEL.EDIT", "MAPPING.DECIDE", "QA.EDIT", "DBT.EDIT", "TAG.MANAGE", "SKILL.EDIT",
                                     "REQUEST.CHANGES", "REVIEW.DECIDE", "AI.USE", "JIRA.READ", "JIRA.WRITE", "OPS.VIEW",
                                     "OPS.OPERATE", "OPS.FIX_PUBLISH", "CASE.WORK", "CASE.RESOLVE", "PACKAGE.EDIT"],
                      "inherits": ["VIEWER"]},
    "VIEWER": {"description": "Read everything, change nothing (no AI calls, no requests)", "privileges": ["AUDIT.VIEW", "CODE.VIEW", "OPS.VIEW"],
               "inherits": []},
}
# Privileges added to system roles after their first release: {version: [privilege]}. Bootstrap grants them to the
# system roles whose spec lists them, once, so existing deployments pick them up without overriding admin edits.
SYSTEM_VERSION = 8
ADDED_PRIVILEGES = {2: ["AI.USE"], 3: ["SKILL.EDIT", "SKILL.RELEASE"], 4: ["INTEGRATION.MANAGE", "CODE.VIEW"],
                    5: ["JIRA.READ", "JIRA.WRITE"], 6: ["OPS.VIEW", "OPS.OPERATE"],
                    7: ["CASE.WORK", "CASE.RESOLVE", "PACKAGE.EDIT", "PACKAGE.APPROVE"],
                    8: ["OPS.FIX_APPROVE", "OPS.FIX_PUBLISH", "OPS.RESOLVE_ADMIN"]}

# Actions routed for approval by default: privilege -> approver role. Everything else is privilege-only.
DEFAULT_POLICIES: Dict[str, str] = {
    "MODEL.APPROVE": "MODEL_APPROVER", "MAPPING.APPROVE": "MODEL_APPROVER",
    "STTM.EDIT": "STTM_APPROVER", "STTM.APPROVE": "STTM_APPROVER",
    "SODA.EDIT": "DQ_APPROVER", "SODA.APPROVE": "DQ_APPROVER",
    "QA.SIGNOFF": "QA_LEAD", "REVIEW.APPROVE": "CODE_REVIEWER",
    "KNOWLEDGE.EDIT": "DATA_STEWARD", "DOMAIN.EDIT": "DATA_STEWARD",
    "CONFIG.EDIT": "PLATFORM_ADMIN", "ADMIN.DEPLOY": "PLATFORM_ADMIN", "ROLE.MANAGE": "GOVERNANCE_ADMIN",
    "SKILL.RELEASE": "SKILL_OWNER", "INTEGRATION.MANAGE": "PLATFORM_ADMIN",
    "PACKAGE.APPROVE": "QA_LEAD",
}
# Default policies created with four eyes on: even holders of the privilege go through another person's approval.
DEFAULT_FOUR_EYES = {"PACKAGE.APPROVE"}

REVIEW_TARGETS = {"MAPPING_APPROVED": "MAPPING.APPROVE", "STTM_APPROVED": "STTM.APPROVE",
                  "SODA_APPROVED": "SODA.APPROVE", "DBT_APPROVED": "REVIEW.APPROVE"}

R = "/api/runs/[^/]+"
# (method, path regex, privilege or callable(body) -> privilege, title)
RULES: List[Tuple[str, str, Any, str]] = [
    ("POST", r"/api/auth/(login|logout)", None, ""),
    ("PUT", r"/api/auth/role", None, ""),
    ("POST", r"/api/(knowledge/search|catalog/analyze|catalog/preview-graph|oracle/parse)", None, ""),
    ("POST", r"/api/(agent/stream|copilot/ask|knowledge/answer)", "AI.USE", ""),
    # Copilot workspace (V048): the conversation itself needs AI.USE; every call its tools make is checked again,
    # under the person's own session, by this same middleware.
    ("POST", r"/api/copilot/sessions(/.*)?", "AI.USE", ""),
    # an AI proposal for one STTM Store line: read only (applying it is a store edit, which goes for approval)
    ("POST", r"/api/hub/sttm/[^/]+/refine", "AI.USE", ""),
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
    # Environments (apps/api/app/environments_api.py): bindings are a versioned setting; the preview only resolves,
    # guards and EXPLAINs a pack's queries with the caller's role
    ("PUT", r"/api/config/environments", "CONFIG.EDIT", "Change environment databases"),
    ("POST", r"/api/config/environments/preview", "ADMIN.VIEW", ""),
    ("POST", r"/api/costs/reconcile", "CONFIG.EDIT", "Reconcile AI cost"),
    ("POST", r"/api/(domains|domains/draft|domains/import)", "DOMAIN.EDIT", "Create or import a domain"),
    ("PUT", r"/api/domains/[^/]+/skills", "SKILL.RELEASE", "Change the skills a domain's runs load"),
    ("PUT", r"/api/domains/[^/]+/mdm", "DOMAIN.EDIT", "Change which mastered domains a domain consumes"),
    ("PUT", r"/api/domains/[^/]+/conventions", "DOMAIN.EDIT", "Change how a domain's code is built in a layer"),
    ("POST", r"/api/domains/[^/]+/references(/schema)?", "KNOWLEDGE.EDIT", "Give the platform a reference input for a domain"),
    ("POST", r"/api/domains/[^/]+/references/[^/]+/(kind|withdraw)", "KNOWLEDGE.EDIT", "Correct or withdraw a reference input"),
    ("POST", r"/api/domains/[^/]+/references/rescore", "KNOWLEDGE.EDIT", "Measure the platform's output against the references"),
    ("POST", r"/api/domains/[^/]+/references/[^/]+/use-as-mdm", "DOMAIN.EDIT", "Make a derived contract the domain's MDM (Reltio) contract"),
    ("POST", r"/api/domains/[^/]+/references/[^/]+/use-as-model", "MODEL.EDIT", "Start a domain model draft from a reference model"),
    ("POST", r"/api/gold/runs", "RUN.CREATE", "Start a Gold run on silver tables"),
    ("POST", rf"{R}/gold/propose", "MODEL.EDIT", "Propose a Gold run's gold model to the Model Workbench"),
    ("POST", rf"{R}/gold/target", "MODEL.EDIT", "Use a table of the approved gold model as a Gold run's target"),
    ("POST", rf"{R}/gold/star-schema", "RUN.CREATE", "Build a Gold run's star schema: a table run per fact and bridge"),
    ("POST", rf"{R}/gold/publish", "DBT.EDIT", "Publish a Gold run's star schema as one pull request"),
    ("POST", rf"{R}/autopilot", "RUN.OPERATE", "Run a run's automatic steps to its next review"),
    ("POST", r"/api/domains/[^/]+/documents/(confluence|upload|[^/]+/refresh|[^/]+/remove)", "KNOWLEDGE.EDIT",
     "Link, upload or refresh a domain document"),
    ("POST", r"/api/domains/[^/]+/examples/[^/]+", "KNOWLEDGE.EDIT", "Upload correct-output examples of a table"),
    ("DELETE", r"/api/domains/[^/]+", "DOMAIN.EDIT", "Delete a domain"),
    ("POST", r"/api/domains/[^/]+/restore", "DOMAIN.EDIT", "Restore a domain"),
    ("PUT", r"/api/domains/[^/]+/rules", "DOMAIN.EDIT", "Change domain rules"),
    ("PUT", r"/api/domains/[^/]+/members", "DOMAIN.EDIT", "Change domain owners and stewards"),
    ("PUT", r"/api/domains/[^/]+", "DOMAIN.EDIT", "Edit a domain"),
    ("POST", r"/api/domains/[^/]+/rollback/\d+", "DOMAIN.EDIT", "Roll a domain back to an earlier version"),
    ("POST", r"/api/domains/[^/]+/(ask|suggestions)", "AI.USE", ""),
    ("POST", r"/api/domains/[^/]+/suggestions/decision", "DOMAIN.EDIT", "Accept a domain suggestion"),
    ("POST", r"/api/knowledge", "KNOWLEDGE.EDIT", "Add knowledge"),
    ("PUT", r"/api/knowledge/[^/]+", "KNOWLEDGE.EDIT", "Edit knowledge"),
    ("POST", r"/api/knowledge/[^/]+/(retire|restore)", "KNOWLEDGE.EDIT", "Retire or restore knowledge"),
    ("POST", r"/api/knowledge/inbox/decide", "KNOWLEDGE.EDIT", "Approve or reject learned knowledge"),
    ("POST", r"/api/domains/[^/]+/learning/learn", "KNOWLEDGE.EDIT", "Learn rules from a domain's reviewer decisions"),
    ("POST", r"/api/knowledge/[^/]+/replay", "KNOWLEDGE.EDIT", "Replay a knowledge change on past runs"),
    ("POST", r"/api/questions/[^/]+/(answer|dismiss)", "KNOWLEDGE.EDIT", "Answer a question the engine asked"),
    ("POST", r"/api/knowledge/outcomes/refresh", "KNOWLEDGE.EDIT", ""),
    ("POST", r"/api/knowledge/[^/]+/rollback", "KNOWLEDGE.EDIT", "Roll knowledge back to an earlier version"),
    ("POST", r"/api/knowledge/[^/]+/verify", "KNOWLEDGE.EDIT", ""),
    ("PUT", r"/api/config/knowledge-policy", "KNOWLEDGE.EDIT", "Change how knowledge is learned"),
    ("PUT", r"/api/tags/.*", "TAG.MANAGE", ""),
    ("POST", r"/api/code/repos", "INTEGRATION.MANAGE", "Connect a code repository"),
    ("PUT", r"/api/code/repos/[^/]+", "INTEGRATION.MANAGE", "Change a code repository"),
    ("DELETE", r"/api/code/repos/[^/]+", "INTEGRATION.MANAGE", "Disconnect a code repository"),
    ("PUT", r"/api/code/repos/[^/]+/schedule", "INTEGRATION.MANAGE", "Schedule a code repository refresh"),
    ("PUT", r"/api/code/repos/[^/]+/credentials", "INTEGRATION.MANAGE", "Change a code repository's credentials"),
    ("DELETE", r"/api/code/repos/[^/]+/schedule", "INTEGRATION.MANAGE", ""),
    ("POST", r"/api/code/repos/[^/]+/refresh", "RUN.OPERATE", ""),
    ("PUT", r"/api/jira/config", "INTEGRATION.MANAGE", "Change the Jira connection"),
    ("POST", r"/api/jira/(connect|callback)", "JIRA.READ", ""),
    ("DELETE", r"/api/jira/connection", "JIRA.READ", ""),
    ("POST", r"/api/jira/issues/[^/]+/comment", "JIRA.WRITE", "Comment on a Jira issue"),
    ("POST", r"/api/jira/issues/[^/]+/transition", "JIRA.WRITE", "Change a Jira issue's status"),
    ("POST", r"/api/jira/jql/validate", "JIRA.READ", ""),
    ("POST", r"/api/jira/filters", "JIRA.READ", ""),
    ("DELETE", r"/api/jira/filters/[^/]+", "JIRA.READ", ""),
    ("POST", r"/api/jira/bugs", "JIRA.WRITE", "Create a Jira bug from a QA test"),
    ("POST", r"/api/jira/issues", "JIRA.WRITE", "Create a Jira issue"),
    ("POST", r"/api/jira/sprint-moves", "JIRA.WRITE", "Move Jira issues to a sprint"),
    ("POST", r"/api/tickets/views", None, ""),
    ("DELETE", r"/api/tickets/views/[^/]+", None, ""),
    ("POST", r"/api/jira/bulk", "JIRA.WRITE", "Bulk action on Jira issues"),
    ("POST", r"/api/qa/triage/resolve", "AI.USE", ""),
    ("POST", r"/api/qa/tables/[^/]+/triage/[^/]+", "AI.USE", ""),
    ("POST", r"/api/qa/tables/[^/]+/(ask|plan)", "AI.USE", ""),
    ("POST", r"/api/qa/(tables|suites)/[^/]+/run", "QA.EDIT", ""),
    ("POST", r"/api/qa/tables/[^/]+/tests", "QA.EDIT", ""),
    ("PUT", r"/api/qa/tests/[^/]+", "QA.EDIT", ""),
    ("DELETE", r"/api/qa/tests/[^/]+", "QA.EDIT", ""),
    ("POST", r"/api/qa/suites", "QA.EDIT", ""),
    ("PUT", r"/api/qa/suites/[^/]+", "QA.EDIT", ""),
    ("DELETE", r"/api/qa/suites/[^/]+", "QA.EDIT", ""),
    # QA Store: domain test packages (services/qa/packs.py); approving a version is four eyes by default policy
    ("POST", r"/api/domains/[^/]+/qa-packs", "PACKAGE.EDIT", "Create a domain QA pack"),
    ("POST", r"/api/domains/[^/]+/qa-packs/import", "PACKAGE.EDIT", "Import a domain QA pack from a .sql file"),
    ("PUT", r"/api/qa-packs/[^/]+", "PACKAGE.EDIT", "Edit a domain QA pack"),
    ("POST", r"/api/qa-packs/[^/]+/(submit|reject)", "PACKAGE.EDIT", ""),
    ("POST", r"/api/qa-packs/[^/]+/approve", "PACKAGE.APPROVE", "Approve a domain QA pack version"),
    ("POST", r"/api/qa-packs/[^/]+/retire", "PACKAGE.APPROVE", "Retire a domain QA pack"),
    # QA packs and Git (V042): resolving a repository conflict makes a draft or keeps the app version; publishing
    # opens a pull request with an approved version
    ("POST", r"/api/qa-packs/[^/]+/sync/[^/]+/(take-repo|keep-app)", "PACKAGE.EDIT", "Resolve a QA pack repository conflict"),
    ("POST", r"/api/qa-packs/[^/]+/publish", "PACKAGE.EDIT", "Publish a QA pack to Git"),
    ("POST", r"/api/qa-packs/[^/]+/pr-sync", None, ""),
    ("POST", r"/api/qa/tests/[^/]+/restore", "QA.EDIT", "Restore an older version of a QA test"),
    ("POST", r"/api/qa/execute", "QA.EDIT", ""),
    # QA Store: run tests copied into a pack draft (not live until the pack is submitted and approved)
    ("POST", r"/api/qa/run-tests/promote", "PACKAGE.EDIT", "Add run tests to a domain QA pack draft"),
    # Data Quality Store (apps/api/app/quality_api.py): check edits land in the table's suite draft (SODA.EDIT); the
    # suite version is then submitted and approved by a DQ_APPROVER (SODA.APPROVE, four eyes), which is the approval,
    # so these routes are never queued (NO_QUEUE_ROUTES); a scan is read-only
    ("POST", r"/api/dq/parse", None, ""),
    ("POST", r"/api/dq/catalog(/seed|/generate)?", "SODA.EDIT", "Change a data quality suite draft"),
    ("POST", r"/api/dq/catalog/[^/]+/status", "SODA.EDIT", "Pause, resume or retire a check in a suite draft"),
    ("POST", r"/api/dq/suites/(draft|submit|withdraw|import)", "SODA.EDIT", ""),
    ("PUT", r"/api/dq/suites/draft", "SODA.EDIT", ""),
    ("POST", r"/api/dq/suites/(approve|reject|apply)", "SODA.APPROVE", ""),
    ("POST", r"/api/dq/suites/publish", "SODA.EDIT", ""),
    ("POST", r"/api/dq/suites/pr-sync", None, ""),
    ("POST", r"/api/dq/run", "PROFILE.RUN", ""),
    ("POST", r"/api/dq/run-checks/decisions", "SODA.EDIT", "Decide run data quality checks from the DQ Store"),
    ("PUT", r"/api/dq/schedules", "SODA.EDIT", "Schedule data quality checks"),
    ("DELETE", r"/api/dq/schedules", "SODA.EDIT", "Remove a data quality schedule"),
    ("POST", r"/api/qa/links", "JIRA.WRITE", "Link a Jira issue to a table or test"),
    ("DELETE", r"/api/qa/links/[^/]+", "JIRA.WRITE", ""),
    ("POST", r"/api/ops/ingest", None, ""),  # Airflow push: no user session, the HMAC signature is checked by the endpoint
    ("POST", r"/api/ops/envs", "INTEGRATION.MANAGE", "Add an Airflow environment"),
    ("PUT", r"/api/ops/envs/[^/]+", "INTEGRATION.MANAGE", "Change an Airflow environment"),
    ("DELETE", r"/api/ops/envs/[^/]+", "INTEGRATION.MANAGE", "Remove an Airflow environment"),
    ("POST", r"/api/ops/envs/[^/]+/test", "INTEGRATION.MANAGE", ""),
    ("POST", r"/api/ops/envs/[^/]+/push-secret", "INTEGRATION.MANAGE", "Rotate an Airflow push secret"),
    ("POST", r"/api/ops/envs/[^/]+/poll", "OPS.OPERATE", ""),
    ("PUT", r"/api/ops/dag", "OPS.OPERATE", "Change a DAG's ops settings"),
    ("POST", r"/api/ops/incidents/bulk", "OPS.OPERATE", "Bulk action on incidents"),
    ("POST", r"/api/ops/incidents/[^/]+/(ack|reopen|assign|resolve|mute|comment|ticket)", "OPS.OPERATE", ""),
    ("POST", r"/api/ops/incidents/[^/]+/(diagnose|ask|postmortem)", "AI.USE", ""),
    # retrying in Airflow is approvable: a policy on OPS.OPERATE (none by default) queues it for the approver role
    ("POST", r"/api/ops/incidents/[^/]+/retry", "OPS.OPERATE", "Retry failed tasks in Airflow"),
    ("POST", r"/api/ops/incidents/[^/]+/retry-safety", "OPS.OPERATE", ""),
    ("POST", r"/api/ops/teams", "INTEGRATION.MANAGE", "Add a support team"),
    ("PUT", r"/api/ops/teams/[^/]+", "INTEGRATION.MANAGE", "Change a support team"),
    ("DELETE", r"/api/ops/teams/[^/]+", "INTEGRATION.MANAGE", "Delete a support team"),
    ("POST", r"/api/ops/routing", "INTEGRATION.MANAGE", "Add an incident routing rule"),
    ("PUT", r"/api/ops/routing/[^/]+", "INTEGRATION.MANAGE", "Change an incident routing rule"),
    ("DELETE", r"/api/ops/routing/[^/]+", "INTEGRATION.MANAGE", "Delete an incident routing rule"),
    ("PUT", r"/api/ops/settings", "INTEGRATION.MANAGE", "Change ops incident settings"),
    # data quality alerting (apps/api/app/dq_alerts_api.py): which support team owns a domain's DQ incidents and digest
    ("PUT", r"/api/ops/dq-alerting/routes/[^/]+", "INTEGRATION.MANAGE", "Route a domain's data quality alerts"),
    ("POST", r"/api/ops/dq-alerting/run", "OPS.OPERATE", ""),
    # Airflow failure resolution (apps/api/app/resolve_api.py): drafts by operators; publishing, approving and the
    # guardrails by their own privileges. Publishing and approving are approvals themselves (NO_QUEUE_ROUTES).
    ("POST", r"/api/ops/resolve/fixes", "OPS.OPERATE", "Write a known fix (draft)"),
    ("PUT", r"/api/ops/resolve/fixes/[^/]+", "OPS.OPERATE", "Change a draft known fix"),
    ("POST", r"/api/ops/resolve/fixes/[^/]+/publish", "OPS.FIX_PUBLISH", "Publish a known fix"),
    ("POST", r"/api/ops/resolve/fixes/[^/]+/retire", "OPS.FIX_PUBLISH", "Retire a known fix"),
    ("POST", r"/api/ops/resolve/skills", "OPS.OPERATE", "Write a multi-step fix"),
    ("PUT", r"/api/ops/resolve/skills/[^/]+", "OPS.OPERATE", "Change a multi-step fix"),
    ("POST", r"/api/ops/resolve/approvals/[^/]+/(approve|reject)", "OPS.FIX_APPROVE", "Approve or reject running a fix"),
    ("PUT", r"/api/ops/resolve/(preference|scopes|routes)", "OPS.RESOLVE_ADMIN", "Change automatic resolution"),
    ("DELETE", r"/api/ops/resolve/(scopes|routes)/[^/]+", "OPS.RESOLVE_ADMIN", "Change automatic resolution"),
    # permanent fixes: drafting proposes a code change (AI.USE checked by the endpoint); accepting is the second
    # person's review (four eyes in services.resolve.permanent); the pull request needs DBT.EDIT and a dry run
    ("POST", r"/api/ops/permanent-fixes/draft/[^/]+", "OPS.OPERATE", ""),
    ("POST", r"/api/ops/permanent-fixes/[^/]+/(accept|reject)", "OPS.FIX_PUBLISH", ""),
    ("POST", r"/api/ops/permanent-fixes/[^/]+/publish", "DBT.EDIT", "Open a permanent fix pull request"),
    ("POST", r"/api/ops/permanent-fixes/[^/]+/known-fix", "OPS.FIX_PUBLISH", ""),
    # cases (apps/api/app/cases_api.py): reads are open to everyone signed in and filtered by domain visibility; the
    # status route needs CASE.WORK here and CASE.RESOLVE in the handler for VERIFIED, RESOLVED, CLOSED and DUPLICATE
    ("POST", r"/api/cases/[^/]+/merge", "CASE.RESOLVE", "Merge a case into another as its duplicate"),
    # PR Q2: settings before the PUT /api/cases/<id> rule; triage and ask spend credits (triage also needs CASE.WORK
    # in the handler); publishing a dbt patch opens a pull request (DBT.EDIT, previewed with a token first)
    ("PUT", r"/api/cases/settings", "INTEGRATION.MANAGE", "Change case settings (auto triage, SLA hours)"),
    ("POST", r"/api/cases/[^/]+/(triage|ask)", "AI.USE", ""),
    ("POST", r"/api/cases/[^/]+/artifacts/[^/]+/publish", "DBT.EDIT", "Publish a case's dbt patch as a pull request"),
    ("POST", r"/api/cases/[^/]+/artifacts/[^/]+/(decide|run|applied)", "CASE.WORK", ""),
    ("POST", r"/api/cases/[^/]+/verify", "CASE.WORK", ""),
    ("POST", r"/api/cases/[^/]+/jira-comment", "JIRA.WRITE", ""),
    ("POST", r"/api/cases/(from-jira|from-incident|from-result)", "CASE.WORK", ""),
    ("POST", r"/api/cases", "CASE.WORK", ""),
    ("PUT", r"/api/cases/[^/]+", "CASE.WORK", ""),
    ("POST", r"/api/cases/[^/]+/(assign|status|comment|links)", "CASE.WORK", ""),
    ("DELETE", r"/api/cases/[^/]+/links/[^/]+", "CASE.WORK", ""),
    # lifecycle hubs (apps/api/app/hub_*.py, /dashboard/*): reads are open to everyone signed in
    ("POST", r"/api/hub/sources/share/preview", "SOURCE.CONNECT", ""),
    ("POST", r"/api/hub/sources/share/mount", "SOURCE.CONNECT", "Mount a Snowflake share"),
    ("POST", r"/api/hub/sources/share/onboard", "RUN.CREATE", "Onboard tables from a Snowflake share"),
    ("POST", r"/api/hub/pipelines/validate", None, ""),
    ("POST", r"/api/hub/pipelines", "SOURCE.CONNECT", "Register an external ingestion pipeline"),
    ("PATCH", r"/api/hub/pipelines/[^/]+", "SOURCE.CONNECT", "Change an external ingestion pipeline"),
    # SnapLogic ingestion control (V046): the wizard's rows carry a secret reference, never a secret, so they can be
    # queued for approval like any SOURCE.CONNECT change
    ("POST", r"/api/hub/ingestion-control/validate", None, ""),
    ("POST", r"/api/hub/ingestion-control", "SOURCE.CONNECT", "Onboard an external source for SnapLogic ingestion"),
    ("PATCH", r"/api/hub/ingestion-control/\d+/\d+", "SOURCE.CONNECT", "Change a SnapLogic ingestion configuration"),
    ("POST", r"/api/hub/snaplogic/preview(-share)?", None, ""),   # builds the rows, writes nothing
    ("POST", r"/api/hub/snaplogic/sync/[^/]+/retry", "SOURCE.CONNECT", "Write a share's SnapLogic config rows again"),
    ("PUT", r"/api/hub/intake-form", "CONFIG.EDIT", "Change the external source intake questions"),
    ("POST", r"/api/hub/profiles/bookmarks", None, ""),
    ("DELETE", r"/api/hub/profiles/bookmarks", None, ""),
    ("POST", r"/api/hub/profiles/reprofile", "PROFILE.RUN", ""),
    # Profiling Store governance (V041): schedules are operational (PROFILE.RUN); a column annotation is proposed with
    # PROFILE.RUN and decided by a data steward (KNOWLEDGE.EDIT, four eyes), which is the approval (never queued)
    ("PUT", r"/api/hub/profiles/schedules", "PROFILE.RUN", "Schedule profiling"),
    ("DELETE", r"/api/hub/profiles/schedules", "PROFILE.RUN", "Remove a profiling schedule"),
    ("POST", r"/api/hub/profiles/schedules/[^/]+/run", "PROFILE.RUN", ""),
    ("POST", r"/api/hub/profiles/annotations", "PROFILE.RUN", ""),
    ("POST", r"/api/hub/profiles/annotations/[^/]+/withdraw", "PROFILE.RUN", ""),
    ("POST", r"/api/hub/profiles/annotations/[^/]+/(approve|reject)", "KNOWLEDGE.EDIT", ""),
    ("POST", r"/api/hub/profiles/expectations", None, ""),
    ("POST", r"/api/hub/sttm/diff", None, ""),
    ("POST", r"/api/hub/sttm/[^/]+/edit", "STTM.EDIT", "Edit a mapping in the STTM catalog"),
    # Source control (V043): an approved mapping version opened as a pull request (never queued: only approved
    # versions are published and nothing changes in the app); reading pull request state back is open to anyone
    ("POST", r"/api/hub/sttm/[^/]+/publish", "STTM.EDIT", "Publish an approved mapping to Git"),
    ("POST", r"/api/source-control/sync", None, ""),
    # Model Workbench (apps/api/app/model_workbench_api.py): the draft, review and approval are the governance, so
    # these are never queued (NO_QUEUE_ROUTES); four eyes is checked by the service
    ("POST", r"/api/hub/domain-models/[^/]+/draft/validate", None, ""),
    ("POST", r"/api/hub/domain-models(/baseline)?", "MODEL.EDIT", "Create, fork or baseline a domain model"),
    ("POST", r"/api/hub/domain-models/[^/]+/draft", "MODEL.EDIT", "Open a draft of a domain model"),
    ("PUT", r"/api/hub/domain-models/[^/]+/draft", "MODEL.EDIT", "Edit a domain model draft"),
    ("POST", r"/api/hub/domain-models/[^/]+/(submit|withdraw)", "MODEL.EDIT", "Send a domain model version for review"),
    ("POST", r"/api/hub/domain-models/[^/]+/versions/\d+/(approve|reject|apply)", "MODEL.APPROVE",
     "Decide a domain model version"),
    ("POST", r"/api/hub/domain-models/[^/]+/versions/\d+/comments", "MODEL.EDIT", ""),
    ("POST", r"/api/hub/domain-models/[^/]+/comments/[^/]+/resolve", "MODEL.EDIT", ""),
    ("PUT", r"/api/hub/domain-models/[^/]+/(layout|standard)", "MODEL.EDIT", ""),
    ("POST", r"/api/hub/domain-models/[^/]+/import(-file)?", "MODEL.EDIT", ""),
    ("POST", r"/api/hub/domain-models/[^/]+/versions/\d+/publish", "MODEL.EDIT", "Open a pull request for an approved domain model"),
    ("POST", r"/api/hub/domain-models/[^/]+/pr-sync", None, ""),
    ("POST", r"/api/hub/domain-models/[^/]+/changes/[^/]+/(merge|reject)", "MODEL.EDIT", "Decide a run's model proposal"),
    ("POST", r"/api/hub/table-models/[^/]+/render", None, ""),
    ("POST", r"/api/hub/table-models/[^/]+/suggest", "AI.USE", ""),
    ("POST", r"/api/hub/table-models", "MODEL.EDIT", "Create a table model"),
    ("PUT", r"/api/hub/table-models/[^/]+(/layout|/domain)?", "MODEL.EDIT", "Edit a table model"),
    ("POST", r"/api/hub/table-models/[^/]+/versions/\d+/propose", "MODEL.EDIT", "Propose a table model to its domain model"),
    ("POST", r"/api/hub/table-models/[^/]+/versions/\d+/publish", "MODEL.EDIT", "Open a pull request for a table model"),
    ("POST", r"/api/hub/models/propose", "AI.USE", ""),
    ("POST", r"/api/hub/models/(validate|render)", None, ""),
    ("POST", r"/api/hub/models/[^/]+/publish", "DBT.EDIT", "Publish a model design as a Git branch and pull request"),
    ("POST", r"/api/hub/models(/[^/]+/finalize)?", "MODEL.EDIT", ""),
    ("POST", r"/api/hub/onboarding/validate", None, ""),
    ("POST", r"/api/skills/builder/check", None, ""),
    ("POST", r"/api/skills/builder/(questions|draft|test)", "AI.USE", ""),
    ("POST", r"/api/skills", "SKILL.EDIT", "Create a skill"),
    ("POST", r"/api/skills/[^/]+/labels/production", "SKILL.RELEASE", "Promote a skill version to production"),
    ("POST", r"/api/skills/[^/]+/labels/candidate", "SKILL.EDIT", ""),
    ("DELETE", r"/api/skills/[^/]+/labels/candidate", "SKILL.EDIT", ""),
    ("PUT", r"/api/skills/bindings", "SKILL.RELEASE", "Change which skills each stage loads"),
    ("POST", r"/api/skills/[^/]+/versions", "SKILL.EDIT", ""),
    ("POST", r"/api/skills/[^/]+/versions/[^/]+/(retire|restore)", "SKILL.EDIT", ""),
    ("PUT", r"/api/skills/[^/]+/category", "SKILL.EDIT", ""),
    # the skill scout: a dry run reads, a real scan drafts (AI.USE checked by the endpoint); proposals are reviewed
    # through the skill labels above, so neither is queued
    ("POST", r"/api/skill-proposals/scan", "SKILL.EDIT", ""),
    ("POST", r"/api/skill-proposals/[^/]+/dismiss", "SKILL.EDIT", ""),
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
    ("POST", r"/api/targets/[^/]+/(retire|restore)", "DOMAIN.EDIT", "Remove or restore a domain table"),
    ("POST", r"/api/sources/[^/]+/(retire|restore)", "SOURCE.CONNECT", "Remove or restore a source"),
    ("POST", r"/api/runs/(batch-archive|batch-cleanup)", "RUN.ARCHIVE", ""),
    ("POST", rf"{R}/(archive|restore)", "RUN.ARCHIVE", ""),
    ("POST", rf"{R}/review", lambda body: REVIEW_TARGETS.get(str((body or {}).get("to_state") or "").upper(), "REVIEW.DECIDE"),
     "Stage decision"),
    ("POST", rf"{R}/transition", "ADMIN.DEPLOY", "Force a state transition"),
    ("POST", rf"{R}/model/design", "MODEL.EDIT", ""),
    ("PUT", rf"{R}/model", "MODEL.EDIT", ""),
    ("POST", rf"{R}/model/\d+/approve", "MODEL.APPROVE", "Approve a data model"),
    ("POST", rf"{R}/model/\d+/propose", "MODEL.EDIT", "Propose a run's model to the domain model"),
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
    ("POST", rf"{R}/jira/links", "JIRA.WRITE", "Link a Jira issue to a run"),
    ("DELETE", rf"{R}/jira/links/[^/]+", "JIRA.WRITE", ""),
    ("POST", rf"{R}/jira/[^/]+/triage", "AI.USE", ""),
    ("POST", rf"{R}/suggestions/[^/]+/decision", "RUN.OPERATE", ""),
    ("POST", rf"{R}/suggestions/[^/]+", "AI.USE", ""),
    ("POST", rf"{R}/.*", "RUN.OPERATE", ""),
]
_COMPILED = [(m, re.compile(f"^{p}$"), priv, title) for m, p, priv, title in RULES]
# Calls whose body carries a secret: never stored on a change request, so never queued for approval.
SECRET_BODY = [("POST", re.compile(r"^/api/hub/pipelines$")), ("PATCH", re.compile(r"^/api/hub/pipelines/[^/]+$"))]


def carries_secret(method: str, path: str) -> bool:
    return any(m == method.upper() and pattern.match(path) for m, pattern in SECRET_BODY)

READ_RULES = [(re.compile(r"^/api/(admin/.*|config/(rules|platform|models|environments))$"), "ADMIN.VIEW"),
              (re.compile(r"^/api/jira/(issues|search|filters|boards|sprints|projects|summary|sprint-choices)(/.*)?$"), "JIRA.READ"),
              (re.compile(r"^/api/qa/links$"), "JIRA.READ"),
              (re.compile(r"^/api/runs/[^/]+/jira/.*$"), "JIRA.READ"),
              (re.compile(r"^/api/(audit|costs|value)(/.*)?$"), "AUDIT.VIEW"),
              (re.compile(r"^/api/code/setup$"), "INTEGRATION.MANAGE"),
              (re.compile(r"^/api/ops(/.*)?$"), "OPS.VIEW"),
              (re.compile(r"^/api/code/(search|file|lineage|impact|path|neighborhood|usage|summary|repos|repos/[^/]+/(runs|branches|catalog|files))$"), "CODE.VIEW"),
              (re.compile(r"^/api/governance/(roles|users|policies|settings|events|privileges)$"), "ADMIN.VIEW")]


# Store edits (the tenant-wide asset stores under /dashboard) always go through approval, even for holders of the
# privilege, and are never self-approved: the store is shared by every run. Run pages keep the normal rules.
# Setting STORE_EDITS_REQUIRE_APPROVAL (Admin, Governance) turns this off.
STORE_ROUTES = [(m, re.compile(p)) for m, p in [
    ("POST", r"^/api/hub/sttm/[^/]+/edit$"),
    ("PUT", r"^/api/dq/schedules$"), ("DELETE", r"^/api/dq/schedules$"),
    ("POST", r"^/api/dq/run-checks/decisions$"),
    # removing (and restoring) a domain table, a source or a knowledge item always needs another person's approval
    ("POST", r"^/api/targets/[^/]+/(retire|restore)$"), ("POST", r"^/api/sources/[^/]+/(retire|restore)$"),
    ("POST", r"^/api/knowledge/[^/]+/(retire|restore)$"),
]]
# approver when a privilege has no approval policy of its own
STORE_APPROVERS = {"STTM.EDIT": "STTM_APPROVER", "SODA.EDIT": "DQ_APPROVER", "DBT.EDIT": "CODE_REVIEWER",
                   "MAPPING.DECIDE": "MODEL_APPROVER", "MODEL.EDIT": "MODEL_APPROVER",
                   "DOMAIN.EDIT": "DATA_STEWARD", "KNOWLEDGE.EDIT": "DATA_STEWARD", "SOURCE.CONNECT": "PLATFORM_ADMIN"}


# Routes whose own workflow is the approval (Model Workbench and Data Quality suite drafts, reviews and decisions,
# Profiling Store annotations, publishing an approved STTM version):
# never turned into a change request. A caller who would otherwise be queued is refused, so an approval never waits
# for its own approval.
NO_QUEUE_ROUTES = [(m, re.compile(p)) for m, p in [
    ("POST", r"^/api/hub/domain-models(/.*)?$"), ("PUT", r"^/api/hub/domain-models/.+$"),
    ("POST", r"^/api/hub/table-models(/.*)?$"), ("PUT", r"^/api/hub/table-models/.+$"),
    ("POST", r"^/api/dq/catalog$"), ("POST", r"^/api/dq/catalog/(seed|generate)$"), ("POST", r"^/api/dq/catalog/[^/]+/status$"),
    ("POST", r"^/api/dq/suites/.+$"), ("PUT", r"^/api/dq/suites/.+$"),
    ("POST", r"^/api/hub/profiles/annotations(/.*)?$"),
    ("POST", r"^/api/hub/sttm/[^/]+/publish$"),
    ("POST", r"^/api/copilot/sessions(/.*)?$"),
    # what a document proposes is reviewed in the knowledge inbox; uploads cannot be queued anyway
    ("POST", r"^/api/domains/[^/]+/documents/.+$"),
    ("POST", r"^/api/domains/[^/]+/examples/[^/]+$"),
    # a reference input is read, not applied: what it proposes goes through the inbox, the Workbench or a run's gates
    ("POST", r"^/api/domains/[^/]+/references(?!/[^/]+/use-as-mdm$)(/.*)?$"),   # the MDM contract is governed
    # learning proposes to the inbox and a replay only measures; an answer is the owner's own knowledge
    ("POST", r"^/api/domains/[^/]+/learning/learn$"), ("POST", r"^/api/knowledge/[^/]+/replay$"),
    ("POST", r"^/api/questions/[^/]+/(answer|dismiss)$"),
    # a Gold run's gold model proposal is a draft, reviewed and approved in the Model Workbench (four eyes)
    ("POST", r"^/api/runs/[^/]+/gold/propose$"),
    # a fix's publish is its review (four eyes in services.resolve.rules); an approval of a fix run is the approval
    ("POST", r"^/api/ops/resolve/fixes/[^/]+/(publish|retire)$"),
    ("POST", r"^/api/ops/resolve/approvals/[^/]+/(approve|reject)$"),
    # accepting a permanent fix is the review itself; retiring or keeping the known fix after the merge too
    ("POST", r"^/api/ops/permanent-fixes/[^/]+/(accept|reject|known-fix)$"),
    # a skill scout scan only proposes (DRAFT versions nothing loads); a dismissal is the review
    ("POST", r"^/api/skill-proposals/scan$"), ("POST", r"^/api/skill-proposals/[^/]+/dismiss$"),
]]


def no_queue(method: str, path: str) -> bool:
    method = method.upper()
    return any(m == method and pattern.match(path) for m, pattern in NO_QUEUE_ROUTES)


def store_queue(method: str, path: str) -> bool:
    """Is this a store edit that always waits for another person's approval?"""
    method = method.upper()
    return any(m == method and pattern.match(path) for m, pattern in STORE_ROUTES)


def store_policy(privilege: str, policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The policy a store edit is queued under: the privilege's own approver (even when an admin switched the
    policy off for run pages) or the store default, never self-approved."""
    approver = (policy or {}).get("approver_role") or STORE_APPROVERS.get(privilege) or "PLATFORM_ADMIN"
    return {"approver_role": approver, "allow_self": False, "active": True, "requires_approval": True, "four_eyes": True}


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
