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
export const mayUseClassic = (g: Grants) => !g.governance || g.privileges.includes("*") || g.privileges.includes("ADMIN.VIEW");

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
  | "work" | "inbox" | "overview" | "copilot" | "value"
  | "intake" | "requests" | "onboarding"
  | "enable"
  | "design" | "domains" | "stores"
  | "build" | "pipelines" | "gold" | "lineage" | "code" | "runs"
  | "review" | "governance" | "audit"
  | "skills" | "registry"
  | "release" | "sourceControl" | "qa"
  | "knowledge" | "memory"
  | "run" | "quality" | "operations" | "incidents" | "resolutions" | "fixes" | "permanentFixes" | "tickets"
  | "admin"
  | "help";
export type Screen = {
  key: ScreenKey; href: string; label: string; section?: SectionKey; exact?: boolean; paths?: readonly string[];
  rail?: false; under?: ScreenKey;
  view?: readonly string[]; why: string;
};
export const SECTIONS: { key: SectionKey; label: string }[] = [
  { key: "work", label: "My Work" }, { key: "intake", label: "Intake" }, { key: "enable", label: "Enable" }, { key: "design", label: "Design" },
  { key: "build", label: "Build" }, { key: "review", label: "Review" }, { key: "skills", label: "Skills" }, { key: "release", label: "Release" },
  { key: "knowledge", label: "Knowledge" }, { key: "run", label: "Run & DQ" }, { key: "admin", label: "Admin" },
];

const GATE_DECIDERS = ["MAPPING.APPROVE", "STTM.APPROVE", "SODA.APPROVE", "QA.SIGNOFF", "REVIEW.APPROVE", "REVIEW.DECIDE"] as const;
/** Everyone who takes part in building: creates or operates runs, decides any gate, or oversees approvals. */
const BUILDERS = ["RUN.CREATE", "RUN.OPERATE", "SOURCE.CONNECT", "PROFILE.RUN", "MODEL.EDIT", "MODEL.APPROVE", "MAPPING.DECIDE", "STTM.EDIT",
  "SODA.EDIT", "QA.EDIT", "DBT.EDIT", ...GATE_DECIDERS, "APPROVAL.VIEW"] as const;
const OPERATORS = ["OPS.VIEW", "JIRA.READ", "CASE.WORK"] as const;

export const SCREENS: Screen[] = [
  // My Work: the person's own work, the estate overview, the copilot and the value ledger
  { key: "work", section: "work", href: "/os", label: "My Work", exact: true, why: "everyone signed in" },
  { key: "inbox", section: "work", href: "/work", label: "Inbox", why: "everyone signed in (requests, questions, tickets)" },
  { key: "overview", section: "work", href: "/dashboard", label: "Overview", exact: true, why: "everyone signed in" },
  { key: "copilot", section: "work", href: "/dashboard/copilot", label: "Copilot", view: ["AI.USE"], why: "people who may use AI" },
  { key: "value", section: "work", href: "/value", label: "Value", view: ["AUDIT.VIEW"], why: "people who read the audit trail" },

  // Intake: asking for something, and the onboarding wizards that start it
  { key: "intake", section: "intake", href: "/os/intake", label: "New request",
    view: ["RUN.CREATE", "SOURCE.CONNECT", "DOMAIN.EDIT", "REQUEST.CHANGES"], why: "people who start onboarding or file requests" },
  { key: "requests", section: "intake", href: "/os/requests", label: "Requests", why: "everyone signed in (each sees the requests of the domains they may see)" },
  { key: "onboarding", section: "intake", href: "/dashboard/onboarding", label: "Onboarding wizard", paths: ["/dashboard/sources/new", "/onboarding"],
    view: ["RUN.CREATE", "SOURCE.CONNECT", "DOMAIN.EDIT"], why: "people who onboard sources and domains" },

  { key: "enable", section: "enable", href: "/os/enable", label: "Source readiness", view: ["SOURCE.CONNECT", "PROFILE.RUN", "CONFIG.EDIT", "INTEGRATION.MANAGE"], why: "people who connect sources and configure ingestion" },

  // Design: the domain model and the stores the design is kept in
  { key: "design", section: "design", href: "/os/design", label: "Designer studio",
    view: ["MODEL.EDIT", "MODEL.APPROVE", "MAPPING.DECIDE", "MAPPING.APPROVE", "STTM.EDIT", "STTM.APPROVE", "DOMAIN.EDIT"], why: "people who model and map" },
  { key: "domains", section: "design", href: "/domains", label: "Domains", why: "everyone signed in (stewards edit)" },
  { key: "stores", section: "design", href: "/dashboard/store", label: "Stores",
    paths: ["/dashboard/sources", "/dashboard/profiling", "/dashboard/modeling", "/dashboard/sttm", "/dashboard/qa-store", "/dashboard/dq-store"], view: BUILDERS, why: "people who build runs or decide their gates" },

  // Build: one run's workbench, every pipeline, lineage and the code
  { key: "build", section: "build", href: "/os/build", label: "Workbench", view: ["RUN.OPERATE", "DBT.EDIT", "REVIEW.APPROVE"], why: "people who build or review code" },
  { key: "pipelines", section: "build", href: "/runs", label: "Silver pipeline", exact: true, view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "gold", section: "build", href: "/gold", label: "Gold pipeline", view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "lineage", section: "build", href: "/lineage", label: "Lineage", view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "code", section: "build", href: "/code", label: "Code", view: BUILDERS, why: "people who build runs or decide their gates" },
  // a run's own workspace is where every gate is decided, so it is open to every builder and approver
  { key: "runs", section: "build", href: "/runs/", label: "Run workspace", paths: ["/runs/"], rail: false, under: "build", view: BUILDERS, why: "people who build runs or decide their gates" },

  // Review: the gates, the change requests, and the audit trail behind them
  { key: "review", section: "review", href: "/os/review", label: "Gates", view: [...GATE_DECIDERS, "RUN.OPERATE", "APPROVAL.VIEW", "REQUEST.CHANGES"], why: "people who decide a gate or raise change requests" },
  { key: "governance", section: "review", href: "/approvals", label: "Approvals",
    view: ["APPROVAL.VIEW", "ROLE.MANAGE", "REQUEST.CHANGES", ...GATE_DECIDERS, "MODEL.APPROVE"], why: "people who raise or decide change requests" },
  { key: "audit", section: "review", href: "/audit", label: "Audit", view: ["AUDIT.VIEW"], why: "people who read the audit trail" },

  { key: "skills", section: "skills", href: "/os/skills", label: "Skill studio", view: ["SKILL.EDIT", "SKILL.RELEASE"], why: "skill authors and owners" },
  { key: "registry", section: "skills", href: "/skills", label: "Registry", view: ["SKILL.EDIT", "SKILL.RELEASE"], why: "skill authors and owners" },

  // Release: the PR, source control and QA
  { key: "release", section: "release", href: "/os/release", label: "Release", view: ["REVIEW.APPROVE", "DBT.EDIT", "QA.EDIT", "QA.SIGNOFF"], why: "developers and QA" },
  { key: "sourceControl", section: "release", href: "/dashboard/source-control", label: "Source control", view: ["REVIEW.APPROVE", "DBT.EDIT", "APPROVAL.VIEW"], why: "developers and reviewers" },
  { key: "qa", section: "release", href: "/qa", label: "QA testing", view: ["QA.EDIT", "QA.SIGNOFF"], why: "QA engineers and leads" },

  { key: "knowledge", section: "knowledge", href: "/os/knowledge", label: "Knowledge hub", why: "everyone signed in (shared memory; stewards curate)" },
  { key: "memory", section: "knowledge", href: "/knowledge", label: "Enterprise Memory", why: "everyone signed in (stewards curate)" },

  // Run & DQ: production quality and operations
  { key: "run", section: "run", href: "/os/run", label: "Run & DQ", view: ["OPS.VIEW", "OPS.OPERATE", "SODA.EDIT", "SODA.APPROVE", "QA.EDIT"], why: "operations and quality" },
  { key: "quality", section: "run", href: "/dashboard/quality", label: "Quality center", view: ["OPS.VIEW", "SODA.EDIT", "SODA.APPROVE", "QA.EDIT", "QA.SIGNOFF", "RUN.OPERATE"], why: "quality and operations" },
  { key: "operations", section: "run", href: "/ops", label: "Monitoring", view: OPERATORS, why: "pipeline support and case work" },
  { key: "incidents", section: "run", href: "/incidents", label: "Incidents", view: ["OPS.VIEW"], why: "pipeline support" },
  { key: "resolutions", section: "run", href: "/ops/resolutions", label: "Resolutions", view: ["OPS.VIEW"], why: "pipeline support" },
  { key: "fixes", section: "run", href: "/ops/fixes", label: "Known fixes", view: ["OPS.VIEW"], why: "pipeline support" },
  { key: "permanentFixes", section: "run", href: "/ops/permanent-fixes", label: "Permanent fixes", view: ["OPS.VIEW"], why: "pipeline support" },
  { key: "tickets", section: "run", href: "/tickets", label: "Tickets", paths: ["/jira"], view: ["JIRA.READ", "CASE.WORK"], why: "people who work Jira issues and cases" },

  { key: "admin", section: "admin", href: "/admin", label: "Admin", view: ["ADMIN.VIEW"], why: "platform and governance admins" },

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
  manageIngestion: { privilege: "SOURCE.CONNECT", label: "Manage pipelines" },
  workStep: { privilege: "RUN.OPERATE", label: "Work this step" },
  revalidate: { privilege: "RUN.OPERATE", label: "Open validation" },
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
  curateKnowledge: { privilege: "KNOWLEDGE.EDIT", label: "Curate in Enterprise Memory" },
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
  governance: ["ROLE.MANAGE", "APPROVAL.VIEW"],
};

/** May see the UI as another role (super admins and governance admins). */
export const canPreview = (g: Grants) => g.governance && (g.privileges.includes("*") || g.privileges.includes("ROLE.MANAGE"));
