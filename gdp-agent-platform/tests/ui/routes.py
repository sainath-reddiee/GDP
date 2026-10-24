"""Single source of truth for the application's route map, used by the suite."""

# (path, expected h1 text fragment or None when the heading is dynamic)
OS_PAGES = [
    ("/os", "Platform actions"),
    ("/os/intake", "Requests"),
    ("/os/intake/new", "Create an onboarding request"),
    ("/os/requests", "Onboarding requests"),
    ("/os/requests/history", "Request history"),
    ("/os/enable", "Source readiness"),
    ("/os/enable/new-domain", "Onboard domain"),
    ("/os/enable/onboarding", "Onboard source"),
    ("/os/design", "Profile, model and map"),
    ("/os/design/store", "Store"),
    ("/os/design/sources", "Source Store"),
    ("/os/design/profiling", "Profiling Store"),
    ("/os/design/modeling", "Modeling Store"),
    ("/os/design/sttm", "Data Mapping Store"),
    ("/os/design/qa-store", "Domain test packages"),
    ("/os/design/dq-store", "Attribute checks catalog"),
    ("/os/build", None),
    ("/os/build/ready", "Ready to build"),
    ("/os/build/runs", "Silver Pipeline"),
    ("/os/build/gold", "Gold Pipeline"),
    ("/os/build/lineage", "Lineage"),
    ("/os/build/code", "Code"),
    ("/os/history", "Run history"),
    ("/os/review", "Validation gate"),
    ("/os/review/approvals", "Approvals"),
    ("/os/skills", None),
    ("/os/skills/registry", "Agent Skills"),
    ("/os/release", "Code, test and release"),
    ("/os/release/source-control", "Source control"),
    ("/os/release/qa", "QA testing"),
    ("/os/knowledge", "Knowledge hub"),
    ("/os/run", "Production operations"),
    ("/os/run/quality", "QA and Data Quality Center"),
    ("/os/run/ops", "Pipelines"),
    ("/os/run/incidents", "Incidents"),
    ("/os/run/ops/resolutions", "Resolutions"),
    ("/os/run/ops/fixes", "Known fixes"),
    ("/os/run/ops/permanent-fixes", "Permanent fixes"),
    ("/os/run/tickets", "Tickets"),
    ("/os/people", "People and personas"),
    ("/os/admin/settings", "Admin"),
    ("/os/admin/audit", "Audit"),
    ("/os/help", "One workflow, many personas"),
    ("/os/work/inbox", None),
    ("/os/work/overview", None),
    ("/os/work/copilot", None),
]

# Legacy URL -> path it must redirect to (prefix match on the final URL path)
LEGACY_REDIRECTS = [
    ("/work", "/os/work/inbox"),
    ("/dashboard", "/os/work/overview"),
    ("/dashboard/copilot", "/os/work/copilot"),
    ("/dashboard/onboarding", "/os/enable/onboarding"),
    ("/dashboard/sources", "/os/design/sources"),
    ("/dashboard/store", "/os/design/store"),
    ("/dashboard/profiling", "/os/design/profiling"),
    ("/dashboard/modeling", "/os/design/modeling"),
    ("/dashboard/sttm", "/os/design/sttm"),
    ("/dashboard/qa-store", "/os/design/qa-store"),
    ("/dashboard/dq-store", "/os/design/dq-store"),
    ("/dashboard/source-control", "/os/release/source-control"),
    ("/dashboard/quality", "/os/run/quality"),
    ("/runs", "/os/build/runs"),
    ("/gold", "/os/build/gold"),
    ("/lineage", "/os/build/lineage"),
    ("/code", "/os/build/code"),
    ("/approvals", "/os/review/approvals"),
    ("/skills", "/os/skills/registry"),
    ("/qa", "/os/release/qa"),
    ("/ops", "/os/run/ops"),
    ("/incidents", "/os/run/incidents"),
    ("/tickets", "/os/run/tickets"),
    ("/jira", "/os/run/tickets"),
    ("/admin", "/os/admin/settings"),
    ("/audit", "/os/admin/audit"),
    ("/knowledge", "/os/knowledge"),
    ("/domains", "/os/knowledge"),
    ("/os/design/new-domain", "/os/enable/new-domain"),
]

# Left rail: label -> href
RAIL = [
    ("Home", "/os"),
    ("Intake", "/os/intake"),
    ("Enable", "/os/enable"),
    ("Design", "/os/design"),
    ("Build", "/os/build"),
    ("Review", "/os/review"),
    ("Skills", "/os/skills"),
    ("Release", "/os/release"),
    ("Knowledge", "/os/knowledge"),
    ("Run & DQ", "/os/run"),
    ("Help", "/os/help"),
]

# Section landing page -> list of (tab label, href)
SECTION_TABS = {
    "/os/enable": [
        ("Source readiness", "/os/enable"),
        ("Onboard source", "/os/enable/onboarding"),
        ("Onboard domain", "/os/enable/new-domain"),
    ],
    "/os/design": [
        ("Design board", "/os/design"),
        ("Stores", "/os/design/store"),
    ],
    "/os/build": [
        ("Build board", "/os/build"),
        ("Ready to build", "/os/build/ready"),
        ("Lineage", "/os/build/lineage"),
        ("Code", "/os/build/code"),
        ("Run history", "/os/history"),
    ],
    "/os/review": [
        ("Gates", "/os/review"),
        ("Approvals", "/os/review/approvals"),
    ],
    "/os/skills": [
        ("Skill studio", "/os/skills"),
        ("Registry", "/os/skills/registry"),
    ],
    "/os/release": [
        ("Release", "/os/release"),
        ("Source control", "/os/release/source-control"),
        ("QA testing", "/os/release/qa"),
    ],
    "/os/run": [
        ("Run & DQ", "/os/run"),
        ("Quality center", "/os/run/quality"),
        ("Monitoring", "/os/run/ops"),
        ("Incidents", "/os/run/incidents"),
        ("Resolutions", "/os/run/ops/resolutions"),
        ("Known fixes", "/os/run/ops/fixes"),
        ("Permanent fixes", "/os/run/ops/permanent-fixes"),
        ("Tickets", "/os/run/tickets"),
    ],
}

KNOWLEDGE_VIEWS = [
    ("Catalog", ""),
    ("Domains", "domains"),
    ("Ask and insights", "ask"),
    ("All items", "items"),
    ("Review inbox", "inbox"),
    ("Learning feed", "feed"),
    ("Duplicates and stale", "health"),
]

KNOWLEDGE_FAMILIES = [
    ("All knowledge", None),
    ("Domain and business", "domain"),
    ("Mapping and transformation", "mapping"),
    ("Quality and testing", "quality"),
    ("Engineering patterns", "engineering"),
    ("Operational learning", "operational"),
]

RUN_STAGES = ["source", "profile", "domain", "modeling", "soda", "qa", "dbt",
              "review", "validation", "audit"]

LANDING_SECTIONS = ["Why", "How it works", "See it run", "Knowledge loop",
                    "Features", "Trust", "Integrations"]
