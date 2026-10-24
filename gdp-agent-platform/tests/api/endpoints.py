"""Endpoint catalogue used by the API tests (method, path)."""

# Collection / summary GETs with no path params - must return 2xx JSON on a healthy API.
STATIC_GETS = [
    "/api/health", "/api/auth/me", "/api/auth/roles",
    "/api/runs", "/api/audit", "/api/costs", "/api/metrics/summary", "/api/run-history",
    "/api/build/readiness", "/api/dbt/github",
    "/api/sources", "/api/sources/databases", "/api/sources/overview",
    "/api/oracle/secrets", "/api/oracle/integrations", "/api/oracle/env-check",
    "/api/landing/targets", "/api/profiles", "/api/profiles/store",
    "/api/catalog/schemas", "/api/targets", "/api/connectors",
    "/api/domains", "/api/domain-cards", "/api/admin/workflow",
    "/api/config/rules", "/api/config/platform", "/api/config/catalog-display", "/api/config/models",
    "/api/config/knowledge-policy", "/api/config/environments",
    "/api/knowledge", "/api/knowledge/overview", "/api/knowledge/feed", "/api/knowledge/inbox",
    "/api/knowledge/outcomes", "/api/knowledge/duplicates", "/api/knowledge/stale",
    "/api/copilot/suggestions", "/api/copilot/sessions", "/api/tags",
    "/api/governance/requests/pending", "/api/governance/requests", "/api/governance/privileges",
    "/api/governance/roles", "/api/governance/users", "/api/governance/policies",
    "/api/governance/settings", "/api/governance/events", "/api/governance/personas",
    "/api/governance/people", "/api/governance/my-domains", "/api/me/personas",
    "/api/skills", "/api/skills/bindings", "/api/skills/builder/models",
    "/api/skill-proposals", "/api/skill-proposals/settings",
    "/api/gold/inputs", "/api/lineage/objects",
    "/api/code/setup", "/api/code/repos",
    "/api/jira/status",
    "/api/qa/tables", "/api/qa/results", "/api/qa/suites", "/api/qa/links", "/api/qa/environments",
    "/api/qa-packs", "/api/qa-packs/summary", "/api/qa/run-tests",
    "/api/dq/catalog", "/api/dq/history", "/api/dq/summary", "/api/dq/suites",
    "/api/dq/suites/publishing", "/api/dq/failing", "/api/dq/digests",
    "/api/dq/run-checks", "/api/dq/coverage",
    "/api/ops/summary", "/api/ops/envs", "/api/ops/dags", "/api/ops/dq-alerting/routes",
    "/api/ops/datadog", "/api/ops/incidents", "/api/ops/incidents/summary",
    "/api/ops/incidents/reliability", "/api/ops/teams", "/api/ops/routing", "/api/ops/settings",
    "/api/ops/resolve/summary", "/api/ops/resolve/failures", "/api/ops/resolve/approvals",
    "/api/ops/resolve/fixes", "/api/ops/resolve/skills", "/api/ops/resolve/preference",
    "/api/ops/resolve/scopes", "/api/ops/resolve/routes", "/api/ops/permanent-fixes",
    "/api/cases", "/api/cases/summary", "/api/cases/settings",
    "/api/hub/intake-form", "/api/hub/pipelines", "/api/hub/ingestion-control",
    "/api/hub/snaplogic/status", "/api/hub/sources/shares",
    "/api/hub/profiles", "/api/hub/profiles/schedules", "/api/hub/profiles/annotations",
    "/api/hub/sttm", "/api/hub/models/runs", "/api/hub/dbt", "/api/hub/models/context",
    "/api/hub/domain-models", "/api/hub/domain-models/publishing",
    "/api/hub/table-models", "/api/hub/table-models/sources",
    "/api/hub/quality/summary", "/api/hub/quality/executions", "/api/hub/quality/suites",
    "/api/hub/quality/gates", "/api/hub/quality/incidents", "/api/hub/overview",
    "/api/source-control/prs", "/api/tickets", "/api/tickets/views", "/api/removals",
    "/api/onboarding-requests", "/api/search?q=customer",
]

# (list endpoint, id field candidates, detail templates using {id})
DETAIL_FROM_LIST = [
    ("/api/runs", ["run_id", "id", "RUN_ID"], [
        "/api/runs/{id}", "/api/runs/{id}/intent", "/api/runs/{id}/model-graph", "/api/runs/{id}/model",
        "/api/runs/{id}/source", "/api/runs/{id}/audit", "/api/runs/{id}/profile", "/api/runs/{id}/domain",
        "/api/runs/{id}/mapping", "/api/runs/{id}/sttm", "/api/runs/{id}/soda", "/api/runs/{id}/qa",
        "/api/runs/{id}/qa/signoff", "/api/runs/{id}/qa/results", "/api/runs/{id}/dbt",
        "/api/runs/{id}/validation", "/api/runs/{id}/target-suggestions", "/api/runs/{id}/skills",
        "/api/runs/{id}/questions", "/api/runs/{id}/autopilot", "/api/runs/{id}/accelerator",
        "/api/runs/{id}/gold", "/api/runs/{id}/jira/links", "/api/runs/{id}/soda/scans",
    ]),
    ("/api/domains", ["domain_id", "id", "DOMAIN_ID"], [
        "/api/domains/{id}", "/api/domains/{id}/versions", "/api/domains/{id}/rules",
        "/api/domains/{id}/columns", "/api/domains/{id}/members", "/api/domains/{id}/conventions",
        "/api/domains/{id}/provenance", "/api/domains/{id}/contract", "/api/domains/{id}/data-model",
        "/api/domains/{id}/playbook", "/api/domains/{id}/mdm-layer", "/api/domains/{id}/skills",
        "/api/domains/{id}/documents", "/api/domains/{id}/examples", "/api/domains/{id}/mdm",
        "/api/domains/{id}/mdm/coverage", "/api/domains/{id}/references", "/api/domains/{id}/learning",
        "/api/domains/{id}/questions", "/api/domains/{id}/qa-packs", "/api/domains/{id}/suggestions",
        "/api/domains/{id}/export",
    ]),
    ("/api/knowledge", ["knowledge_id", "id", "KNOWLEDGE_ID"], [
        "/api/knowledge/{id}/history", "/api/knowledge/{id}/versions", "/api/knowledge/{id}/usage",
    ]),
    ("/api/skills", ["name", "NAME"], ["/api/skills/{id}", "/api/skills/{id}/diff"]),
    ("/api/onboarding-requests", ["request_id", "id"], [
        "/api/onboarding-requests/{id}", "/api/onboarding-requests/{id}/files"]),
    ("/api/ops/incidents", ["incident_id", "id"], [
        "/api/ops/incidents/{id}", "/api/ops/incidents/{id}/impact"]),
    ("/api/cases", ["case_id", "id"], ["/api/cases/{id}"]),
    ("/api/ops/permanent-fixes", ["fix_id", "id"], ["/api/ops/permanent-fixes/{id}"]),
    ("/api/ops/resolve/failures", ["failure_id", "id"], ["/api/ops/resolve/failures/{id}"]),
    ("/api/ops/resolve/fixes", ["issue_id", "id"], ["/api/ops/resolve/fixes/{id}"]),
    ("/api/sources", ["source_id", "id"], [
        "/api/sources/{id}/inventory", "/api/sources/{id}/files"]),
    ("/api/hub/domain-models", ["model_id", "id"], [
        "/api/hub/domain-models/{id}", "/api/hub/domain-models/{id}/changes"]),
    ("/api/hub/table-models", ["model_id", "id"], [
        "/api/hub/table-models/{id}", "/api/hub/table-models/{id}/related"]),
    ("/api/hub/sttm", ["catalog_id", "id"], [
        "/api/hub/sttm/{id}", "/api/hub/sttm/{id}/preview", "/api/hub/sttm/{id}/context"]),
    ("/api/qa/suites", ["suite_id", "id"], []),
    ("/api/qa-packs", ["pack_id", "id"], ["/api/qa-packs/{id}", "/api/qa-packs/{id}/runs"]),
    ("/api/copilot/sessions", ["session_id", "id"], ["/api/copilot/sessions/{id}"]),
    ("/api/code/repos", ["repo_id", "id"], [
        "/api/code/repos/{id}/branches", "/api/code/repos/{id}/runs", "/api/code/repos/{id}/files"]),
    ("/api/skill-proposals", ["proposal_id", "id"], ["/api/skill-proposals/{id}"]),
]

# Detail endpoints that must answer 404 (not 500) for a non-existent id.
NOT_FOUND = [
    "/api/runs/{x}", "/api/domains/{x}", "/api/onboarding-requests/{x}", "/api/ops/incidents/{x}",
    "/api/cases/{x}", "/api/ops/permanent-fixes/{x}", "/api/skills/{x}", "/api/hub/domain-models/{x}",
    "/api/hub/table-models/{x}", "/api/hub/sttm/{x}", "/api/qa-packs/{x}", "/api/copilot/sessions/{x}",
    "/api/skill-proposals/{x}", "/api/ops/resolve/failures/{x}", "/api/ingest-jobs/{x}",
    "/api/profile-jobs/{x}",
]

# Create/action endpoints: an empty or malformed body must be rejected with 4xx, never 5xx.
INVALID_BODY_POSTS = [
    "/api/auth/login", "/api/runs", "/api/sources", "/api/sources/external", "/api/domains",
    "/api/domains/import", "/api/knowledge", "/api/knowledge/search", "/api/knowledge/answer",
    "/api/knowledge/inbox/decide", "/api/copilot/ask", "/api/targets/register",
    "/api/governance/roles", "/api/skills", "/api/skills/categories", "/api/skills/builder/draft",
    "/api/gold/runs", "/api/code/repos", "/api/jira/issues", "/api/jira/jql/validate",
    "/api/qa/suites", "/api/qa/execute", "/api/qa/links", "/api/dq/catalog", "/api/dq/parse",
    "/api/dq/run", "/api/ops/envs", "/api/ops/teams", "/api/ops/routing",
    "/api/ops/resolve/fixes", "/api/ops/resolve/skills", "/api/cases", "/api/cases/from-jira",
    "/api/hub/pipelines", "/api/hub/pipelines/validate", "/api/hub/ingestion-control",
    "/api/hub/models", "/api/hub/models/propose", "/api/hub/domain-models", "/api/hub/table-models",
    "/api/hub/sttm/diff", "/api/hub/onboarding/validate", "/api/tickets/views",
    "/api/onboarding-requests", "/api/catalog/preview-graph", "/api/oracle/parse",
    "/api/ops/ingest",
]

# A representative route per router file - proves every router is mounted.
ROUTER_PROBES = [
    ("GET", "/api/health"), ("GET", "/api/governance/roles"), ("GET", "/api/me/personas"),
    ("GET", "/api/skills"), ("GET", "/api/skills/builder/models"), ("GET", "/api/skill-proposals"),
    ("GET", "/api/knowledge/overview"), ("POST", "/api/questions/{question_id}/answer"),
    ("GET", "/api/domain-cards"), ("GET", "/api/domains/{domain_id}/provenance"),
    ("GET", "/api/domains/{domain_id}/playbook"), ("GET", "/api/domains/{domain_id}/documents"),
    ("GET", "/api/domains/{domain_id}/mdm"), ("GET", "/api/domains/{domain_id}/references"),
    ("GET", "/api/gold/inputs"), ("GET", "/api/lineage/trace"), ("GET", "/api/code/repos"),
    ("GET", "/api/jira/status"), ("GET", "/api/qa/tables"), ("GET", "/api/dq/catalog"),
    ("GET", "/api/dq/failing"), ("GET", "/api/config/environments"), ("GET", "/api/ops/summary"),
    ("GET", "/api/ops/datadog"), ("GET", "/api/ops/incidents"), ("GET", "/api/ops/resolve/summary"),
    ("GET", "/api/ops/permanent-fixes"), ("GET", "/api/cases"), ("GET", "/api/runs/{run_id}/autopilot"),
    ("GET", "/api/pipelines/{pipeline}/summary"), ("GET", "/api/copilot/sessions"),
    ("GET", "/api/hub/pipelines"), ("GET", "/api/hub/profiles"), ("GET", "/api/hub/models/runs"),
    ("GET", "/api/hub/models/context"), ("GET", "/api/hub/domain-models"),
    ("GET", "/api/hub/table-models"), ("GET", "/api/hub/quality/summary"), ("GET", "/api/hub/overview"),
    ("GET", "/api/source-control/prs"), ("GET", "/api/tickets"), ("GET", "/api/removals"),
    ("GET", "/api/onboarding-requests"), ("GET", "/api/search"), ("POST", "/api/agent/stream"),
]
