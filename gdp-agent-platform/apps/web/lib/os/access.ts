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

// Every signed-in page belongs to one screen: the /os screens and the classic workspaces they absorbed. A screen is a
// rail entry (in a section) unless `rail` is false; `paths` are the other pages it covers ("=/x" that page only, "/x/"
// what is under it, "/x" both). The most specific match wins, so /admin (ADMIN.VIEW) is checked before Governance.
export type ScreenKey =
  | "work" | "inbox" | "overview" | "copilot"
  | "intake" | "enable" | "design" | "build" | "runs" | "review" | "skills" | "release"
  | "pipelines" | "stores" | "knowledge"
  | "run" | "quality" | "operations"
  | "governance" | "audit" | "admin" | "value"
  | "help";
export type Section = "Work" | "Workflow" | "Assets" | "Operate" | "Govern";
export type Screen = {
  key: ScreenKey; href: string; label: string; section?: Section; exact?: boolean; paths?: readonly string[];
  /** false: covered pages only, highlighted as `under` in the rail */
  rail?: false; under?: ScreenKey;
  view?: readonly string[]; why: string;
};
export const SECTIONS: Section[] = ["Work", "Workflow", "Assets", "Operate", "Govern"];

const GATE_DECIDERS = ["MAPPING.APPROVE", "STTM.APPROVE", "SODA.APPROVE", "QA.SIGNOFF", "REVIEW.APPROVE", "REVIEW.DECIDE"] as const;
/** Everyone who takes part in building: creates or operates runs, decides any gate, or oversees approvals. */
const BUILDERS = ["RUN.CREATE", "RUN.OPERATE", "SOURCE.CONNECT", "PROFILE.RUN", "MODEL.EDIT", "MODEL.APPROVE", "MAPPING.DECIDE", "STTM.EDIT",
  "SODA.EDIT", "QA.EDIT", "DBT.EDIT", ...GATE_DECIDERS, "APPROVAL.VIEW"] as const;

export const SCREENS: Screen[] = [
  { key: "work", section: "Work", href: "/os", label: "My Work", exact: true, why: "everyone signed in" },
  { key: "inbox", section: "Work", href: "/work", label: "Inbox", why: "everyone signed in (requests, questions, tickets)" },
  { key: "overview", section: "Work", href: "/dashboard", label: "Overview", exact: true, why: "everyone signed in" },
  { key: "copilot", section: "Work", href: "/dashboard/copilot", label: "Copilot", view: ["AI.USE"], why: "people who may use AI" },

  { key: "intake", section: "Workflow", href: "/os/intake", label: "Intake", paths: ["/dashboard/onboarding", "/dashboard/sources/new", "/onboarding"],
    view: ["RUN.CREATE", "SOURCE.CONNECT", "DOMAIN.EDIT"], why: "people who start onboarding" },
  { key: "enable", section: "Workflow", href: "/os/enable", label: "Enable", view: ["SOURCE.CONNECT", "PROFILE.RUN", "CONFIG.EDIT", "INTEGRATION.MANAGE"], why: "people who connect sources and configure ingestion" },
  { key: "design", section: "Workflow", href: "/os/design", label: "Design",
    view: ["MODEL.EDIT", "MODEL.APPROVE", "MAPPING.DECIDE", "MAPPING.APPROVE", "STTM.EDIT", "STTM.APPROVE", "DOMAIN.EDIT"], why: "people who model and map" },
  { key: "build", section: "Workflow", href: "/os/build", label: "Build", view: ["RUN.OPERATE", "DBT.EDIT", "REVIEW.APPROVE"], why: "people who build or review code" },
  // a run's own workspace is where every gate is decided, so it is open to every builder and approver
  { key: "runs", href: "/runs/", label: "Run workspace", paths: ["/runs/"], rail: false, under: "build", view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "review", section: "Workflow", href: "/os/review", label: "Review", view: [...GATE_DECIDERS, "RUN.OPERATE", "APPROVAL.VIEW", "REQUEST.CHANGES"], why: "people who decide a gate or raise change requests" },
  { key: "skills", section: "Workflow", href: "/os/skills", label: "Skills", paths: ["/skills"], view: ["SKILL.EDIT", "SKILL.RELEASE"], why: "skill authors and owners" },
  { key: "release", section: "Workflow", href: "/os/release", label: "Release", paths: ["/dashboard/source-control"], view: ["REVIEW.APPROVE", "DBT.EDIT", "QA.EDIT", "QA.SIGNOFF"], why: "developers and QA" },

  { key: "pipelines", section: "Assets", href: "/runs", label: "Pipelines", exact: true, paths: ["/gold", "/lineage", "/code"], view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "stores", section: "Assets", href: "/dashboard/store", label: "Stores",
    paths: ["/dashboard/sources", "/dashboard/profiling", "/dashboard/modeling", "/dashboard/sttm", "/dashboard/qa-store", "/dashboard/dq-store"], view: BUILDERS, why: "people who build runs or decide their gates" },
  { key: "knowledge", section: "Assets", href: "/os/knowledge", label: "Knowledge", paths: ["/knowledge", "/domains"], why: "everyone signed in (shared memory; stewards curate)" },

  { key: "run", section: "Operate", href: "/os/run", label: "Run & DQ", view: ["OPS.VIEW", "OPS.OPERATE", "SODA.EDIT", "SODA.APPROVE", "QA.EDIT"], why: "operations and quality" },
  { key: "quality", section: "Operate", href: "/dashboard/quality", label: "Quality", paths: ["/qa"], view: ["OPS.VIEW", "SODA.EDIT", "SODA.APPROVE", "QA.EDIT", "QA.SIGNOFF", "RUN.OPERATE"], why: "quality and operations" },
  { key: "operations", section: "Operate", href: "/ops", label: "Operations", paths: ["/incidents", "/tickets", "/jira"], view: ["OPS.VIEW", "JIRA.READ", "CASE.WORK"], why: "pipeline support and case work" },

  { key: "governance", section: "Govern", href: "/approvals", label: "Approvals",
    view: ["APPROVAL.VIEW", "ROLE.MANAGE", "REQUEST.CHANGES", ...GATE_DECIDERS, "MODEL.APPROVE"], why: "people who raise or decide change requests" },
  { key: "audit", section: "Govern", href: "/audit", label: "Audit", view: ["AUDIT.VIEW"], why: "people who read the audit trail" },
  { key: "value", section: "Govern", href: "/value", label: "Value", view: ["AUDIT.VIEW"], why: "people who read the audit trail" },
  { key: "admin", section: "Govern", href: "/admin", label: "Admin", view: ["ADMIN.VIEW"], why: "platform and governance admins" },

  { key: "help", href: "/os/help", label: "Help", why: "everyone signed in" },
];

export const screenByKey = (key: ScreenKey) => SCREENS.find((s) => s.key === key)!;
export const canSee = (g: Grants, key: ScreenKey) => hasAny(g, screenByKey(key).view);

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
