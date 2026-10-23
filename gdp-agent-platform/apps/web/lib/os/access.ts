// The one access list of the Agentic Data OS experience: which privileges open each screen, which privilege each
// action needs, and which privileges make a person each persona. Privileges and roles are the governance ones
// (services/governance/policy.py); nothing here grants anything. The API still enforces every action; this decides
// what a person is shown.

export type Grants = { governance: boolean; privileges: string[] };

/** Holds the privilege (everything is allowed until governance is deployed, as in lib/types `can`). */
export const has = (g: Grants, privilege: string) => !g.governance || g.privileges.includes("*") || g.privileges.includes(privilege);
/** Holds any of them; no list means anyone signed in. */
export const hasAny = (g: Grants, privileges?: readonly string[]) => !privileges?.length || privileges.some((p) => has(g, p));

/* screens */

export type ScreenKey = "work" | "intake" | "enable" | "design" | "build" | "review" | "skills" | "release" | "knowledge" | "run" | "help";
export type Screen = { key: ScreenKey; href: string; label: string; exact?: boolean; view?: readonly string[]; why: string };

const GATE_DECIDERS = ["MAPPING.APPROVE", "STTM.APPROVE", "SODA.APPROVE", "QA.SIGNOFF", "REVIEW.APPROVE", "REVIEW.DECIDE"] as const;

export const SCREENS: Screen[] = [
  { key: "work", href: "/os", label: "My Work", exact: true, why: "everyone signed in" },
  { key: "intake", href: "/os/intake", label: "Intake", view: ["RUN.CREATE", "SOURCE.CONNECT", "DOMAIN.EDIT"], why: "people who start onboarding" },
  { key: "enable", href: "/os/enable", label: "Enable", view: ["SOURCE.CONNECT", "PROFILE.RUN", "CONFIG.EDIT", "INTEGRATION.MANAGE"], why: "people who connect sources and configure ingestion" },
  { key: "design", href: "/os/design", label: "Design",
    view: ["MODEL.EDIT", "MODEL.APPROVE", "MAPPING.DECIDE", "MAPPING.APPROVE", "STTM.EDIT", "STTM.APPROVE", "DOMAIN.EDIT"], why: "people who model and map" },
  { key: "build", href: "/os/build", label: "Build", view: ["RUN.OPERATE", "DBT.EDIT", "REVIEW.APPROVE"], why: "people who build or review code" },
  { key: "review", href: "/os/review", label: "Review", view: [...GATE_DECIDERS, "RUN.OPERATE", "APPROVAL.VIEW"], why: "people who decide a gate" },
  { key: "skills", href: "/os/skills", label: "Skills", view: ["SKILL.EDIT", "SKILL.RELEASE"], why: "skill authors and owners" },
  { key: "release", href: "/os/release", label: "Release", view: ["REVIEW.APPROVE", "DBT.EDIT", "QA.EDIT", "QA.SIGNOFF"], why: "developers and QA" },
  { key: "knowledge", href: "/os/knowledge", label: "Knowledge", why: "everyone signed in (shared memory; stewards curate)" },
  { key: "run", href: "/os/run", label: "Run & DQ", view: ["OPS.VIEW", "OPS.OPERATE", "SODA.EDIT", "SODA.APPROVE", "QA.EDIT"], why: "operations and quality" },
  { key: "help", href: "/os/help", label: "Help", why: "everyone signed in" },
];

export const screenByKey = (key: ScreenKey) => SCREENS.find((s) => s.key === key)!;
export const canSee = (g: Grants, key: ScreenKey) => hasAny(g, screenByKey(key).view);
/** The /os screen a path belongs to. */
export const screenOfPath = (path: string) =>
  [...SCREENS].sort((a, b) => b.href.length - a.href.length).find((s) => (s.exact ? path === s.href : path === s.href || path.startsWith(`${s.href}/`)));

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
