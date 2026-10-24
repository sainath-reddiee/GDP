import { apiAsSelf } from "@/lib/api";
import type { RunSummary } from "@/lib/types";
import { GATES, stateLabel } from "@/lib/run-labels";
import { gateHref } from "@/lib/os/lifecycle";
import { has } from "@/lib/os/access";
import type { OsAccess } from "@/lib/os/session";
import { RUNNING_STATES, runNeedsMe, stageOfRun } from "@/lib/os/work";
import { PERSONA_FOR_PRIVILEGE, PERSONA_FOR_ROLE, personaById, personaLabel, type PersonaId } from "@/lib/os/personas";
import { loadBoardRuns } from "@/lib/os/data";

// Every open action on the platform besides the build workflows (onboarding requests) Home lists: runs at a person's
// step that no workflow covers, change requests, Airflow fixes, domain models and data quality suites in review, model
// drafts, QA packs, knowledge proposed for review, skill proposals and incidents. Home is master data: the same under
// every persona (read with the person's own access), each action naming the persona that acts on it; only `mine`
// follows the persona they work as. A source that fails or is not deployed is left out, never the page.

export type ActionGroup = "Gates" | "Approvals" | "Models and quality" | "Knowledge and skills" | "Operations";
/** What an action is about, for Home's type filter: a domain (models, checks, knowledge), a source (Silver runs), a
 *  data product (Gold runs), or the platform itself (approvals, fixes, skills, incidents). */
export type ActionType = "DOMAIN" | "SOURCE" | "PRODUCT" | "PLATFORM";
export type ActionItem = {
  key: string; group: ActionGroup; type: ActionType; title: string; detail: string; href: string; cta: string;
  since: string | null; tone: "red" | "amber" | "blue" | "purple";
  /** who acts on it: the privilege and the persona that holds it ("Data Designer") */
  privilege: string; persona: string;
  /** the viewer's current persona may act on it */
  mine: boolean;
};

const safe = <T>(p: Promise<T>, fallback: T) => p.catch(() => fallback);
const same = (a?: string | null, b?: string | null) => !!a && !!b && a.trim().toUpperCase() === b.trim().toUpperCase();
const mineAmong = (user: string | null, list?: (string | null)[] | null) => !!user && (list ?? []).some((x) => same(x, user));

type ModelRow = { model_id: string; domain_id: string; domain_name: string; open_version: number | null; open_status: string | null;
  submitted_by: string | null; open_authors?: string[] | null; updated_at?: string | null };
type SuiteRow = { suite_id: string; table_name: string; open_version: number | null; open_status: string | null; submitted_by: string | null;
  open_authors?: string[] | null; open_summary?: string | null; updated_at?: string | null };
type PackRow = { pack_id: string; name?: string | null; version: number; status: string; domain?: string | null; created_by?: string | null;
  created_at?: string | null };
type Change = { request_id: string; title: string | null; summary: string | null; privilege: string; requested_by: string; status: string;
  created_at: string; approver_role?: string | null; allow_self?: boolean };
type FixApproval = { approval_id: string; failure_id: string; fix_name: string | null; issue_title?: string | null; status: string;
  requested_at: string; dag_id?: string | null };
type Inbox = { knowledge_id: string; title: string; knowledge_type: string; domain_name: string | null; created_at: string; created_by?: string | null };
type Proposal = { proposal_id: string; skill_name: string; title?: string | null; created_at: string };
type Incident = { incident_id: string; title: string; severity: string; status: string; last_seen: string; assignee?: string | null };

// the persona whose step a run state is, when it is a person's step and no agent is running it
const STEP_PERSONAS: PersonaId[] = ["admin", "designer", "forward_lead", "developer", "product_owner"];
const stepPersona = (state: string): PersonaId | null => STEP_PERSONAS.find((id) => personaById(id).states.includes(state)) ?? null;

/** Every open action on the platform (Home's master data, the same under every persona), each naming the persona that
 *  acts on it and whether the viewer's current persona may (`mine`, from their persona-narrowed grants). `covered`
 *  are the runs Home shows through their workflow. The lists are read with the person's own access (apiAsSelf). */
export async function loadActions(access: OsAccess, covered: Set<string>): Promise<{ items: ActionItem[]; errors: number }> {
  const user = access.user;
  const own = access.own;
  const as = access.persona.id;
  let errors = 0;
  const seesAll = has(own, "APPROVAL.VIEW");
  const [runs, changes, fixes, models, suites, packs, inbox, proposals, incidents] = await Promise.all([
    loadBoardRuns(true).then((r) => { if (r.error) errors += 1; return r.data; }),
    safe(apiAsSelf<{ requests: Change[] }>(`/api/governance/requests?scope=${seesAll ? "all&status=PENDING" : "inbox"}`), { requests: [] }),
    has(own, "OPS.VIEW") ? safe(apiAsSelf<FixApproval[]>("/api/ops/resolve/approvals?scope=open"), []) : Promise.resolve([] as FixApproval[]),
    safe(apiAsSelf<{ models: ModelRow[] }>("/api/hub/domain-models?layer=SILVER"), { models: [] }),
    safe(apiAsSelf<{ suites: SuiteRow[] }>("/api/dq/suites"), { suites: [] }),
    safe(apiAsSelf<{ packs: PackRow[] }>("/api/qa-packs"), { packs: [] }),
    safe(apiAsSelf<{ items: Inbox[] }>("/api/knowledge/inbox"), { items: [] }),
    safe(apiAsSelf<{ items: Proposal[] }>("/api/skill-proposals?status=PROPOSED&limit=50"), { items: [] }),
    has(own, "OPS.VIEW") ? safe(apiAsSelf<{ incidents: Incident[] }>("/api/ops/incidents?limit=50"), { incidents: [] }) : Promise.resolve({ incidents: [] as Incident[] }),
  ]);
  const out: ActionItem[] = [];
  const push = (a: Omit<ActionItem, "persona"> & { personaId: PersonaId | null }) => {
    const { personaId, ...rest } = a;
    out.push({ ...rest, persona: personaLabel(personaId) });
  };

  // every run at a person's step: a gate, a failure, a working step; unless its workflow on Home already shows it
  for (const run of runs.runs) {
    if (run.is_archived || run.current_state === "CANCELLED" || covered.has(run.run_id) || RUNNING_STATES.has(run.current_state)) continue;
    const s = run.current_state;
    const gate = GATES[s];
    const working = !gate && s !== "FAILED" && s !== "VALIDATION_FAILED" ? stepPersona(s) : null;
    if (!gate && s !== "FAILED" && s !== "VALIDATION_FAILED" && !(working && (run.lifecycle === "RUNNING" || run.lifecycle === "DRAFT"))) continue;
    const type: ActionType = run.pipeline === "GOLD" ? "PRODUCT" : "SOURCE";
    const board = stageOfRun(run) === "design" ? "design" : "build";
    const where = `${run.domain_name ?? "No domain"}${run.source_system_name ? ` · ${run.source_system_name}` : ""}`;
    const since = run.updated_at ?? run.created_at;
    const mine = runNeedsMe(run, access);
    if (gate) {
      push({ key: `gate-${run.run_id}`, group: "Gates", type, title: `${gate.name}: ${run.run_name}`, detail: where, privilege: gate.privilege, mine,
             personaId: s === "SODA_REVIEW" ? "forward_lead" : PERSONA_FOR_PRIVILEGE[gate.privilege] ?? null,
             href: gateHref(run.run_id, gate.slug), cta: gate.privilege === "REVIEW.APPROVE" ? "Review code" : "Review and approve", since, tone: "amber" });
    } else if (s === "VALIDATION_FAILED") {
      push({ key: `val-${run.run_id}`, group: "Gates", type, title: `Validation failed: ${run.run_name}`, detail: `${where} · fix the findings and validate again`,
             privilege: "RUN.OPERATE", mine, personaId: "forward_lead",
             href: `/os/review?run=${encodeURIComponent(run.run_id)}`, cta: "Fix validation", since, tone: "red" });
    } else if (s === "FAILED") {
      push({ key: `fail-${run.run_id}`, group: "Gates", type, title: `Run failed: ${run.run_name}`, detail: `${where} · ${run.current_stage ?? "see the run"}`,
             privilege: "RUN.OPERATE", mine, personaId: "forward_lead",
             href: `/os/${board}?view=selected&run=${encodeURIComponent(run.run_id)}`, cta: "Open the failed run", since, tone: "red" });
    } else {
      push({ key: `step-${run.run_id}`, group: "Gates", type, title: `${stateLabel(s)}: ${run.run_name}`, detail: `${where} · next step on the ${board === "design" ? "Design" : "Build"} board`,
             privilege: working === "designer" ? "MAPPING.DECIDE" : working === "admin" ? "SOURCE.CONNECT" : working === "developer" ? "REVIEW.APPROVE" : "RUN.OPERATE",
             mine, personaId: working, href: `/os/${board}?view=selected&run=${encodeURIComponent(run.run_id)}`,
             cta: board === "design" ? "Continue the design" : "Continue the build", since, tone: "blue" });
    }
  }

  for (const c of changes.requests ?? []) {
    if (c.status !== "PENDING") continue;
    const role = String(c.approver_role ?? "").toUpperCase();
    const personaId = PERSONA_FOR_ROLE[role] ?? null;
    // decided by the approver role's persona (a super admin decides any), never one's own unless allowed
    const mine = (as === "super" || (!!personaId && personaId === as)) && (!same(c.requested_by, user) || !!c.allow_self || as === "super");
    push({ key: `cr-${c.request_id}`, group: "Approvals", type: "PLATFORM", title: c.title ?? `Change needing ${c.privilege}`,
           detail: `Asked by ${c.requested_by} · ${c.privilege}`, privilege: c.privilege, mine, personaId,
           href: "/os/review/approvals?scope=inbox", cta: "Approve or reject", since: c.created_at, tone: "amber" });
  }
  for (const f of fixes) {
    if (f.status !== "OPEN") continue;
    push({ key: `fix-${f.approval_id}`, group: "Approvals", type: "PLATFORM", title: f.fix_name ?? f.issue_title ?? "Airflow fix",
           detail: `Known fix to run${f.dag_id ? ` · ${f.dag_id}` : ""}`, privilege: "OPS.FIX_APPROVE", mine: has(access, "OPS.FIX_APPROVE"), personaId: "reliability",
           href: `/os/run/ops/resolutions/${encodeURIComponent(f.failure_id)}`, cta: "Approve the fix", since: f.requested_at, tone: "amber" });
  }

  for (const m of models.models ?? []) {
    if (!m.open_version) continue;
    const authored = mineAmong(user, m.open_authors) || same(m.submitted_by, user);
    if (m.open_status === "IN_REVIEW") {
      push({ key: `model-${m.model_id}`, group: "Models and quality", type: "DOMAIN", title: `${m.domain_name} model v${m.open_version}`,
             detail: `In review · submitted by ${m.submitted_by ?? "someone"}`, privilege: "MODEL.APPROVE", personaId: "designer",
             mine: has(access, "MODEL.APPROVE") && !authored,
             href: `/os/design/modeling/${encodeURIComponent(m.model_id)}`, cta: "Review the model", since: m.updated_at ?? null, tone: "purple" });
    } else if (m.open_status === "DRAFT") {
      const system = (m.open_authors ?? []).some((a) => /^SYSTEM_/i.test(a ?? ""));
      push({ key: `draft-${m.model_id}`, group: "Models and quality", type: "DOMAIN", title: `${m.domain_name} model v${m.open_version} draft`,
             detail: system ? "Domain pack update from the deploy · review and submit it" : `Draft by ${(m.open_authors ?? []).join(", ") || "someone"} · finish and submit it`,
             privilege: "MODEL.EDIT", personaId: "designer", mine: has(access, "MODEL.EDIT") && (authored || system),
             href: `/os/design/modeling/${encodeURIComponent(m.model_id)}`, cta: "Open the draft", since: m.updated_at ?? null, tone: "blue" });
    }
  }
  for (const s of suites.suites ?? []) {
    if (s.open_status !== "IN_REVIEW") continue;
    push({ key: `dq-${s.suite_id}`, group: "Models and quality", type: "DOMAIN", title: `Data quality suite: ${s.table_name} v${s.open_version}`,
           detail: s.open_summary ?? `Submitted by ${s.submitted_by ?? "someone"}`, privilege: "SODA.APPROVE", personaId: "designer",
           mine: has(access, "SODA.APPROVE") && !mineAmong(user, s.open_authors) && !same(s.submitted_by, user),
           href: `/os/design/dq-store/suite?table=${encodeURIComponent(s.table_name)}`, cta: "Review the checks", since: s.updated_at ?? null, tone: "purple" });
  }
  for (const p of packs.packs ?? []) {
    if (p.status !== "PENDING_APPROVAL") continue;
    push({ key: `qa-${p.pack_id}-${p.version}`, group: "Models and quality", type: "DOMAIN", title: `QA pack: ${p.name ?? p.pack_id} v${p.version}`,
           detail: `${p.domain ?? "All domains"} · by ${p.created_by ?? "someone"}`, privilege: "PACKAGE.APPROVE", personaId: "qa",
           mine: has(access, "PACKAGE.APPROVE") && !same(p.created_by, user),
           href: "/os/design/qa-store", cta: "Review the pack", since: p.created_at ?? null, tone: "purple" });
  }

  const proposed = inbox.items ?? [];
  if (proposed.length) {
    const domains = [...new Set(proposed.map((i) => i.domain_name).filter(Boolean))];
    push({ key: "knowledge-inbox", group: "Knowledge and skills", type: "DOMAIN", title: `${proposed.length} knowledge item${proposed.length === 1 ? "" : "s"} proposed for review`,
           detail: domains.length ? domains.slice(0, 3).join(", ") + (domains.length > 3 ? ` and ${domains.length - 3} more` : "") : "From runs, cases and documents",
           privilege: "KNOWLEDGE.EDIT", personaId: "curator", mine: has(access, "KNOWLEDGE.EDIT"),
           href: "/os/knowledge?view=inbox", cta: "Review knowledge", since: proposed[0]?.created_at ?? null, tone: "blue" });
  }
  for (const p of (proposals.items ?? []).slice(0, 10)) {
    push({ key: `skill-${p.proposal_id}`, group: "Knowledge and skills", type: "PLATFORM", title: `Skill proposed: ${p.title ?? p.skill_name}`,
           detail: "From the skill scout", privilege: "SKILL.EDIT", personaId: "curator", mine: has(access, "SKILL.EDIT"),
           href: "/os/skills/registry/proposed", cta: "Review the proposal", since: p.created_at, tone: "blue" });
  }
  for (const i of incidents.incidents ?? []) {
    push({ key: `inc-${i.incident_id}`, group: "Operations", type: "PLATFORM", title: i.title,
           detail: `${i.severity} incident · ${i.status.toLowerCase()}${i.assignee ? ` · ${i.assignee}` : ""}`, privilege: "OPS.OPERATE", personaId: "reliability",
           mine: has(access, "OPS.OPERATE") && (!i.assignee || same(i.assignee, user)),
           href: `/os/run/incidents/${encodeURIComponent(i.incident_id)}`, cta: "Work the incident", since: i.last_seen,
           tone: i.severity === "HIGH" || i.severity === "CRITICAL" ? "red" : "amber" });
  }

  const ORDER: ActionGroup[] = ["Gates", "Approvals", "Models and quality", "Knowledge and skills", "Operations"];
  const toneRank = { red: 0, amber: 1, purple: 2, blue: 3 };
  out.sort((a, b) => ORDER.indexOf(a.group) - ORDER.indexOf(b.group) || toneRank[a.tone] - toneRank[b.tone]
    || (a.since ?? "").localeCompare(b.since ?? ""));
  return { items: out, errors };
}
