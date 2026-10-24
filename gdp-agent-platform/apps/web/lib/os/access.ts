// The one access list of the Agentic Data OS experience: which privileges open each screen, which privilege each
// action needs, and which privileges make a person each persona. Privileges and roles are the governance ones
// (services/governance/policy.py); nothing here grants anything. The API still enforces every action; this decides
// what a person is shown.

export type Grants = { governance: boolean; privileges: string[] };

/** The request header the middleware sets to the page's path, for the root layout's gate. */
export const PATH_HEADER = "x-ados-path";
/** The admin-only switch back to the classic layout (sidebar and top bar). */
export const CLASSIC_COOKIE = "ados_classic";
/** May use the classic layout: platform and governance admins (everyone while governance is off). */
export const mayUseClassic = (g: Grants) => !g.governance || g.privileges.includes("*");

/** Holds the privilege (everything is allowed until governance is deployed, as in lib/types `can`). */
export const has = (g: Grants, privilege: string) => !g.governance || g.privileges.includes("*") || g.privileges.includes(privilege);
/** Holds any of them; no list means anyone signed in. */
export const hasAny = (g: Grants, privileges?: readonly string[]) => !privileges?.length || privileges.some((p) => has(g, p));

/* screens */

// The rail has the UX package's ten entries (plus Admin for admins); each entry's pages are its tabs. Every signed-in
// page, /os and classic, belongs to one screen (a tab): its own href, or one of the pages it covers (`paths`: "=/x" that
// page only, "/x/" what is under it, "/x" both). The most specific match wins. A screen with `rail: false` is covered
// pages only (a run's workspace), shown as the tab `under` it. An entry is on the rail when any of its tabs is open.
export type SectionKey = "work" | "intake" | "enable" | "design" | "build" | "review" | "skills" | "release" | "knowledge" | "run" | "admin";
export type ScreenKey =
  | "work" | "inbox" | "overview" | "copilot"
  | "intake" | "requests" | "requestHistory" | "onboarding"
  | "enable"
  | "design" | "newDomain" | "domains" | "stores"
  | "ready" | "build" | "pipelines" | "gold" | "lineage" | "code" | "history" | "runs"
  | "review" | "governance" | "audit"
  | "skills" | "registry"
  | "release" | "sourceControl" | "qa"
  | "knowledge" | "memory"
  | "run" | "quality" | "operations" | "incidents" | "resolutions" | "fixes" | "permanentFixes" | "tickets"
  | "people" | "admin"
  | "help";
export type Screen = {
  key: ScreenKey; href: string; label: string; section?: SectionKey; exact?: boolean; paths?: readonly string[];
  rail?: false; under?: ScreenKey;
  view?: readonly string[]; why: string;
};
export const SECTIONS: { key: SectionKey; label: string }[] = [
  { key: "work", label: "Home" }, { key: "intake", label: "Intake" }, { key: "enable", label: "Enable" }, { key: "design", label: "Design" },
  { key: "build", label: "Build" }, { key: "review", label: "Review" }, { key: "skills", label: "Skills" }, { key: "release", label: "Release" },
  { key: "knowledge", label: "Knowledge" }, { key: "run", label: "Run & DQ" }, { key: "admin", label: "Admin" },
];

const GATE_DECIDERS = ["MAPPING.APPROVE", "STTM.APPROVE", "SODA.APPROVE", "QA.SIGNOFF", "REVIEW.APPROVE", "REVIEW.DECIDE"] as const;
/** Everyone who takes part in building: creates or operates runs, decides any gate, or oversees approvals. */
const BUILDERS = ["RUN.CREATE", "RUN.OPERATE", "SOURCE.CONNECT", "PROFILE.RUN", "MODEL.EDIT", "MODEL.APPROVE", "MAPPING.DECIDE", "STTM.EDIT",
  "SODA.EDIT", "QA.EDIT", "DBT.EDIT", ...GATE_DECIDERS, "APPROVAL.VIEW"] as const;
const OPERATORS = ["OPS.VIEW", "JIRA.READ", "CASE.WORK"] as const;
/** Super admins only: Admin and governance (the API enforces the same, policy.SUPER_ONLY). */
const SUPER = ["*"] as const;

export const SCREENS: Screen[] = [
  // Home: the workflows in flight, the inbox and the estate overview
  { key: "work", section: "work", href: "/os", label: "Home", exact: true, why: "everyone signed in" },
  { key: "inbox", section: "work", href: "/os/work/inbox", label: "Inbox", why: "everyone signed in (requests, questions, tickets)" },
  { key: "overview", section: "work", href: "/os/work/overview", label: "Overview", exact: true, why: "everyone signed in" },
  // Copilot opens from the header; its full page is reached from the drawer, not a tab
  { key: "copilot", section: "work", href: "/os/work/copilot", label: "Copilot", rail: false, view: ["AI.USE"], why: "people who may use AI" },

  // Intake: asking for something, and the onboarding wizards that start it
  { key: "intake", section: "intake", href: "/os/intake", label: "New request",
    view: ["RUN.CREATE", "SOURCE.CONNECT", "DOMAIN.EDIT", "REQUEST.CHANGES"], why: "people who start onboarding or file requests" },
  { key: "requests", section: "intake", href: "/os/requests", label: "Requests", why: "everyone signed in (each sees the requests of the domains they may see)" },
  { key: "requestHistory", section: "intake", href: "/os/requests/history", label: "Request history", why: "everyone signed in (finished, cancelled and rejected requests, read only)" },

  // Enable: onboarding sources is the Data OS Administrator's alone (registering, landing and starting the run)
  { key: "enable", section: "enable", href: "/os/enable", label: "Source readiness", view: ["SOURCE.CONNECT", "DOMAIN.ONBOARD", "CONFIG.EDIT", "INTEGRATION.MANAGE"], why: "the Data OS Administrator (onboarding sources and domains, configuring ingestion)" },
  { key: "onboarding", section: "enable", href: "/os/enable/onboarding?type=source", label: "Onboard a source", paths: ["/os/enable/onboarding", "/os/design/sources/new"],
    view: ["SOURCE.CONNECT"], why: "the Data OS Administrator (onboarding a source registers it, lands it and starts its run)" },
  { key: "newDomain", section: "enable", href: "/os/enable/new-domain", label: "Onboard a domain", paths: ["/os/design/new-domain"], view: ["DOMAIN.ONBOARD"],
    why: "the Data OS Administrator (a domain's model, contract and memory); data owners request one on Intake" },

  // Design: the domain model and the stores the design is kept in
  { key: "design", section: "design", href: "/os/design", label: "Designer studio",
    view: ["MODEL.EDIT", "MODEL.APPROVE", "MAPPING.DECIDE", "MAPPING.APPROVE", "STTM.EDIT", "STTM.APPROVE", "DOMAIN.EDIT"], why: "people who model and map" },
  { key: "domains", section: "knowledge", href: "/os/knowledge?view=domains", label: "Domains", paths: ["/os/knowledge/domains"], rail: false, under: "knowledge", why: "everyone signed in (stewards edit)" },
  { key: "stores", section: "design", href: "/os/design/store", label: "Stores",
    paths: ["/os/design/sources", "/os/design/profiling", "/os/design/modeling", "/os/design/sttm", "/os/design/qa-store", "/os/design/dq-store"], view: BUILDERS, why: "people who build runs or decide their gates" },

  // Build: what is ready to build (design done), one run's workbench, lineage, the code and every run's history. The
  // Silver and Gold pipeline lists live on under Run history (sourcing, modelling and mapping moved to Intake, Enable
  // and Design), still guarded and reachable from there.
  { key: "ready", section: "build", href: "/os/build/ready", label: "Ready to build", view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "build", section: "build", href: "/os/build", label: "Workbench", view: ["RUN.OPERATE", "DBT.EDIT", "REVIEW.APPROVE"], why: "people who build or review code" },
  { key: "pipelines", section: "build", href: "/os/build/runs", label: "Silver pipeline", paths: ["=/os/build/runs"], rail: false, under: "history", view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "gold", section: "build", href: "/os/build/gold", label: "Gold pipeline", paths: ["/os/build/gold"], rail: false, under: "history", view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "lineage", section: "build", href: "/os/build/lineage", label: "Lineage", view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "code", section: "build", href: "/os/build/code", label: "Code", view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "history", section: "build", href: "/os/history", label: "Run history", why: "everyone signed in (every run of both pipelines, read only)" },
  // a run's own workspace is where every gate is decided, so it is open to every builder and approver
  { key: "runs", section: "build", href: "/os/build/runs/", label: "Run workspace", paths: ["/os/build/runs/"], rail: false, under: "build", view: BUILDERS, why: "people who build runs or decide their gates" },

  // Review: the gates, the change requests, and the audit trail behind them
  { key: "review", section: "review", href: "/os/review", label: "Gates", view: [...GATE_DECIDERS, "RUN.OPERATE", "APPROVAL.VIEW", "REQUEST.CHANGES"], why: "people who decide a gate or raise change requests" },
  { key: "governance", section: "review", href: "/os/review/approvals", label: "Approvals",
    view: ["APPROVAL.VIEW", "ROLE.MANAGE", "REQUEST.CHANGES", ...GATE_DECIDERS, "MODEL.APPROVE"], why: "people who raise or decide change requests" },


  { key: "skills", section: "skills", href: "/os/skills", label: "Skill studio", view: ["SKILL.EDIT", "SKILL.RELEASE"], why: "skill authors and owners" },
  { key: "registry", section: "skills", href: "/os/skills/registry", label: "Registry", view: ["SKILL.EDIT", "SKILL.RELEASE"], why: "skill authors and owners" },

  // Release: the PR, source control and QA
  { key: "release", section: "release", href: "/os/release", label: "Release", view: ["REVIEW.APPROVE", "DBT.EDIT", "QA.EDIT", "QA.SIGNOFF"], why: "developers and QA" },
  { key: "sourceControl", section: "release", href: "/os/release/source-control", label: "Source control", view: ["REVIEW.APPROVE", "DBT.EDIT", "APPROVAL.VIEW"], why: "developers and reviewers" },
  { key: "qa", section: "release", href: "/os/release/qa", label: "QA testing", view: ["QA.EDIT", "QA.SIGNOFF"], why: "QA engineers and leads" },

  { key: "knowledge", section: "knowledge", href: "/os/knowledge", label: "Knowledge hub", why: "everyone signed in (shared memory; stewards curate)" },

  // Run & DQ: production quality and operations
  { key: "run", section: "run", href: "/os/run", label: "Run & DQ", view: ["OPS.VIEW", "OPS.OPERATE", "SODA.EDIT", "SODA.APPROVE", "QA.EDIT"], why: "operations and quality" },
  { key: "quality", section: "run", href: "/os/run/quality", label: "Quality center", view: ["OPS.VIEW", "SODA.EDIT", "SODA.APPROVE", "QA.EDIT", "QA.SIGNOFF", "RUN.OPERATE"], why: "quality and operations" },
  { key: "operations", section: "run", href: "/os/run/ops", label: "Monitoring", view: OPERATORS, why: "pipeline support and case work" },
  { key: "incidents", section: "run", href: "/os/run/incidents", label: "Incidents", view: ["OPS.VIEW"], why: "pipeline support" },
  { key: "resolutions", section: "run", href: "/os/run/ops/resolutions", label: "Resolutions", view: ["OPS.VIEW"], why: "pipeline support" },
  { key: "fixes", section: "run", href: "/os/run/ops/fixes", label: "Known fixes", view: ["OPS.VIEW"], why: "pipeline support" },
  { key: "permanentFixes", section: "run", href: "/os/run/ops/permanent-fixes", label: "Permanent fixes", view: ["OPS.VIEW"], why: "pipeline support" },
  { key: "tickets", section: "run", href: "/os/run/tickets", label: "Tickets", paths: ["/os/run/jira"], view: ["JIRA.READ", "CASE.WORK"], why: "people who work Jira issues and cases" },

  // Admin: a super admin's alone, people and their personas first
  { key: "people", section: "admin", href: "/os/people", label: "People & personas", view: SUPER, why: "super admins only" },
  { key: "admin", section: "admin", href: "/os/admin/settings", label: "Platform settings", view: SUPER, why: "super admins only" },
  { key: "audit", section: "admin", href: "/os/admin/audit", label: "Audit", view: SUPER, why: "super admins only" },

  { key: "help", href: "/os/help", label: "Help", why: "everyone signed in" },
];

export const screenByKey = (key: ScreenKey) => SCREENS.find((s) => s.key === key)!;
export const canSee = (g: Grants, key: ScreenKey) => hasAny(g, screenByKey(key).view);
/** The rail entry a screen belongs to. */
export const sectionOf = (key: ScreenKey | null | undefined) => (key ? screenByKey(key)?.section ?? null : null);

/** How well a pattern matches a path: its length when it does, -1 when not ("=/x" exact, "/x/" under, "/x" both). */
function matchLength(pattern: string, path: string): number {
  if (pattern.startsWith("=")) return path === pattern.slice(1) ? pattern.length : -1;
  if (pattern.endsWith("/")) return path.startsWith(pattern) && path.length > pattern.length ? pattern.length : -1;
  return path === pattern || path.startsWith(`${pattern}/`) ? pattern.length : -1;
}

/** The screen a path belongs to (its own href or one of its covered pages), most specific first; null for pages no
 *  screen owns (sign-in, the BFF), which governance does not gate. */
export function screenOfPath(path: string): Screen | null {
  let best: Screen | null = null;
  let len = -1;
  for (const s of SCREENS) {
    const own = s.rail === false ? [] : [s.exact ? `=${s.href}` : s.href];
    for (const p of [...own, ...(s.paths ?? [])]) {
      const n = matchLength(p, path);
      if (n > len) { best = s; len = n; }
    }
  }
  return best;
}

/* actions: what each button on the /os screens needs (the page that performs it enforces the same privilege) */

export const ACTIONS = {
  startOnboarding: { privilege: "RUN.CREATE", label: "Start onboarding" },
  connectSource: { privilege: "SOURCE.CONNECT", label: "Connect a source" },
  onboardDomain: { privilege: "DOMAIN.ONBOARD", label: "Onboard a domain" },
  manageIngestion: { privilege: "SOURCE.CONNECT", label: "Manage pipelines" },
  workStep: { privilege: "RUN.OPERATE", label: "Work this step" },
  revalidate: { privilege: "RUN.OPERATE", label: "Open validation" },
  validateAgain: { privilege: "RUN.OPERATE", label: "Validate again" },
  reviewStandards: { privilege: "RUN.OPERATE", label: "Review standards" },
  openDbtStudio: { privilege: "DBT.EDIT", label: "Open dbt studio" },
  publishPr: { privilege: "DBT.EDIT", label: "Publish from dbt studio" },
  reviewCode: { privilege: "REVIEW.APPROVE", label: "Review code" },
  decideGate: { privilege: "MAPPING.APPROVE", label: "Approve" },
  answerQuestion: { privilege: "MAPPING.DECIDE", label: "Answer" },
  editModel: { privilege: "MODEL.EDIT", label: "Edit model" },
  runQa: { privilege: "QA.EDIT", label: "Open QA" },
  signoffQa: { privilege: "QA.SIGNOFF", label: "Sign off QA" },
  editSkill: { privilege: "SKILL.EDIT", label: "Edit and version the skill" },
  releaseSkill: { privilege: "SKILL.RELEASE", label: "Review candidate" },
  curateKnowledge: { privilege: "KNOWLEDGE.EDIT", label: "Curate knowledge" },
  verifyKnowledge: { privilege: "KNOWLEDGE.EDIT", label: "Verify" },
  decideInbox: { privilege: "KNOWLEDGE.EDIT", label: "Approve" },
  operateIncident: { privilege: "OPS.OPERATE", label: "Investigate" },
  viewOps: { privilege: "OPS.VIEW", label: "Operations" },
  viewApprovals: { privilege: "APPROVAL.VIEW", label: "All approvals" },
} as const satisfies Record<string, { privilege: string; label: string }>;
export type ActionId = keyof typeof ACTIONS;

/** How a person may take an action: directly, as a change request routed to the approver role, or not at all. */
export type ActMode = "do" | "request" | "none";
export function actMode(g: Grants, privilege: string): ActMode {
  if (has(g, privilege)) return "do";
  if (g.governance && g.privileges.includes("REQUEST.CHANGES")) return "request";
  return "none";
}

/* personas, from privileges: a person is offered only the personas their roles qualify them for */

export const PERSONA_REQUIRES: Record<string, readonly string[]> = {
  forward_lead: ["RUN.OPERATE"],
  product_owner: ["RUN.CREATE", "DOMAIN.EDIT"],
  admin: ["SOURCE.CONNECT", "CONFIG.EDIT", "INTEGRATION.MANAGE"],
  designer: ["MODEL.EDIT", "MODEL.APPROVE", "MAPPING.APPROVE", "STTM.EDIT", "STTM.APPROVE"],
  developer: ["REVIEW.APPROVE", "DBT.EDIT"],
  qa: ["QA.EDIT", "QA.SIGNOFF", "SODA.EDIT", "SODA.APPROVE"],
  reliability: ["OPS.OPERATE", "OPS.FIX_APPROVE"],
  curator: ["KNOWLEDGE.EDIT", "SKILL.RELEASE"],
  super: ["*"],
};

/** May see the UI as another role (super admins and governance admins). */
export const canPreview = (g: Grants) => g.governance && g.privileges.includes("*");
