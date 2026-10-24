import { api } from "@/lib/api";
import type { RunSummary } from "@/lib/types";
import { GATES, stateLabel } from "@/lib/run-labels";
import { gateHref } from "@/lib/os/lifecycle";
import { has } from "@/lib/os/access";
import type { OsAccess } from "@/lib/os/session";
import { runNeedsMe, stageOfRun } from "@/lib/os/work";
import { loadBoardRuns } from "@/lib/os/data";

// Everything that waits on the viewer besides the build workflows (onboarding requests) Home lists: run gates and
// failed runs no request covers, change requests to approve, Airflow fixes to approve, domain models and data quality
// suites in review, QA packs to approve, knowledge proposed for review, skill proposals and incidents. Each source is
// read from the endpoint its own screen uses and filtered here by the viewer's privileges (the persona they work as);
// a source that fails or is not deployed is left out, never the page.

export type ActionGroup = "Gates" | "Approvals" | "Models and quality" | "Knowledge and skills" | "Operations";
/** What an action is about, for Home's type filter: a domain (models, checks, knowledge), a source (Silver runs), a
 *  data product (Gold runs), or the platform itself (approvals, fixes, skills, incidents). */
export type ActionType = "DOMAIN" | "SOURCE" | "PRODUCT" | "PLATFORM";
export type ActionItem = {
  key: string; group: ActionGroup; type: ActionType; title: string; detail: string; href: string; cta: string;
  since: string | null; tone: "red" | "amber" | "blue" | "purple";
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
  created_at: string; can_decide: boolean };
type FixApproval = { approval_id: string; failure_id: string; fix_name: string | null; issue_title?: string | null; status: string;
  requested_at: string; dag_id?: string | null };
type Inbox = { knowledge_id: string; title: string; knowledge_type: string; domain_name: string | null; created_at: string; own?: boolean };
type Proposal = { proposal_id: string; skill_name: string; title?: string | null; created_at: string };
type Incident = { incident_id: string; title: string; severity: string; status: string; last_seen: string };

/** The actions waiting on the viewer besides the workflows Home lists as theirs. `covered` are the runs of those
 *  workflows, not repeated here. Every run that waits on the viewer by the boards' own rule (runNeedsMe over the same
 *  run list) is here, so Home's count is what the Design and Build boards count, plus everything else. */
export async function loadActions(access: OsAccess, covered: Set<string>): Promise<{ items: ActionItem[]; errors: number }> {
  const user = access.user;
  let errors = 0;
  const tracked = <T>(p: Promise<T>, fallback: T) => p.catch(() => { errors += 1; return fallback; });
  const [runs, changes, fixes, models, suites, packs, inbox, proposals, incidents] = await Promise.all([
    loadBoardRuns().then((r) => { if (r.error) errors += 1; return r.data; }),
    safe(api<{ requests: Change[] }>("/api/governance/requests?scope=inbox"), { requests: [] }),
    has(access, "OPS.FIX_APPROVE") ? safe(api<FixApproval[]>("/api/ops/resolve/approvals?scope=mine"), []) : Promise.resolve([] as FixApproval[]),
    has(access, "MODEL.APPROVE") || has(access, "MODEL.EDIT") ? safe(api<{ models: ModelRow[] }>("/api/hub/domain-models?layer=SILVER"), { models: [] }) : Promise.resolve({ models: [] as ModelRow[] }),
    has(access, "SODA.APPROVE") ? safe(api<{ suites: SuiteRow[] }>("/api/dq/suites"), { suites: [] }) : Promise.resolve({ suites: [] as SuiteRow[] }),
    has(access, "PACKAGE.APPROVE") ? safe(api<{ packs: PackRow[] }>("/api/qa-packs"), { packs: [] }) : Promise.resolve({ packs: [] as PackRow[] }),
    has(access, "KNOWLEDGE.EDIT") ? safe(api<{ items: Inbox[] }>("/api/knowledge/inbox"), { items: [] }) : Promise.resolve({ items: [] as Inbox[] }),
    has(access, "SKILL.EDIT") ? safe(api<{ items: Proposal[] }>("/api/skill-proposals?status=PROPOSED&limit=20"), { items: [] }) : Promise.resolve({ items: [] as Proposal[] }),
    has(access, "OPS.OPERATE") ? safe(api<{ incidents: Incident[] }>("/api/ops/incidents?mine=true&limit=20"), { incidents: [] }) : Promise.resolve({ incidents: [] as Incident[] }),
  ]);
  const out: ActionItem[] = [];

  // every run waiting on the viewer (a gate they decide, a failure they operate, a step their persona works), as the
  // Design and Build boards count them, unless a workflow of theirs on Home already shows it
  for (const run of runs.runs) {
    if (run.is_archived || run.current_state === "CANCELLED" || covered.has(run.run_id) || !runNeedsMe(run, access)) continue;
    const type: ActionType = run.pipeline === "GOLD" ? "PRODUCT" : "SOURCE";
    const board = stageOfRun(run) === "design" ? "design" : "build";
    const where = `${run.domain_name ?? "No domain"}${run.source_system_name ? ` · ${run.source_system_name}` : ""}`;
    const since = run.updated_at ?? run.created_at;
    const gate = GATES[run.current_state];
    if (gate) {
      out.push({ key: `gate-${run.run_id}`, group: "Gates", type, title: `${gate.name}: ${run.run_name}`, detail: `${where} · ${gate.role}`,
                 href: gateHref(run.run_id, gate.slug), cta: gate.privilege === "REVIEW.APPROVE" ? "Review code" : "Review and approve", since, tone: "amber" });
    } else if (run.current_state === "VALIDATION_FAILED") {
      out.push({ key: `val-${run.run_id}`, group: "Gates", type, title: `Validation failed: ${run.run_name}`, detail: `${where} · fix the findings and validate again`,
                 href: `/os/review?run=${encodeURIComponent(run.run_id)}`, cta: "Fix validation", since, tone: "red" });
    } else if (run.current_state === "FAILED") {
      out.push({ key: `fail-${run.run_id}`, group: "Gates", type, title: `Run failed: ${run.run_name}`, detail: `${where} · ${run.current_stage ?? "see the run"}`,
                 href: `/os/${board}?view=selected&run=${encodeURIComponent(run.run_id)}`, cta: "Open the failed run", since, tone: "red" });
    } else {
      out.push({ key: `step-${run.run_id}`, group: "Gates", type, title: `${stateLabel(run.current_state)}: ${run.run_name}`, detail: `${where} · your step on the ${board === "design" ? "Design" : "Build"} board`,
                 href: `/os/${board}?view=selected&run=${encodeURIComponent(run.run_id)}`, cta: board === "design" ? "Continue the design" : "Continue the build", since, tone: "blue" });
    }
  }

  for (const c of changes.requests ?? []) {
    if (!c.can_decide || c.status !== "PENDING") continue;
    out.push({ key: `cr-${c.request_id}`, group: "Approvals", type: "PLATFORM", title: c.title ?? `Change needing ${c.privilege}`, detail: `Asked by ${c.requested_by} · ${c.privilege}`,
               href: "/os/review/approvals?scope=inbox", cta: "Approve or reject", since: c.created_at, tone: "amber" });
  }
  for (const f of fixes) {
    if (f.status !== "OPEN") continue;
    out.push({ key: `fix-${f.approval_id}`, group: "Approvals", type: "PLATFORM", title: f.fix_name ?? f.issue_title ?? "Airflow fix", detail: `Known fix to run${f.dag_id ? ` · ${f.dag_id}` : ""}`,
               href: `/os/run/ops/resolutions/${encodeURIComponent(f.failure_id)}`, cta: "Approve the fix", since: f.requested_at, tone: "amber" });
  }

  for (const m of models.models ?? []) {
    if (!m.open_version) continue;
    const authored = mineAmong(user, m.open_authors) || same(m.submitted_by, user);
    if (m.open_status === "IN_REVIEW" && has(access, "MODEL.APPROVE") && !authored) {
      out.push({ key: `model-${m.model_id}`, group: "Models and quality", type: "DOMAIN", title: `${m.domain_name} model v${m.open_version}`, detail: `In review · submitted by ${m.submitted_by ?? "someone"}`,
                 href: `/os/design/modeling/${encodeURIComponent(m.model_id)}`, cta: "Review the model", since: m.updated_at ?? null, tone: "purple" });
    } else if (m.open_status === "DRAFT" && has(access, "MODEL.EDIT")
               && (authored || (m.open_authors ?? []).some((a) => /^SYSTEM_/i.test(a ?? "")))) {
      // a draft of mine, or one the platform opened (a domain pack update from the deploy) that nobody owns yet
      const system = !authored;
      out.push({ key: `draft-${m.model_id}`, group: "Models and quality", type: "DOMAIN", title: `${m.domain_name} model v${m.open_version} draft`,
                 detail: system ? "Domain pack update from the deploy · review and submit it" : "Your draft · finish and submit it",
                 href: `/os/design/modeling/${encodeURIComponent(m.model_id)}`, cta: "Open the draft", since: m.updated_at ?? null, tone: "blue" });
    }
  }
  for (const s of suites.suites ?? []) {
    if (s.open_status !== "IN_REVIEW" || mineAmong(user, s.open_authors) || same(s.submitted_by, user)) continue;
    out.push({ key: `dq-${s.suite_id}`, group: "Models and quality", type: "DOMAIN", title: `Data quality suite: ${s.table_name} v${s.open_version}`, detail: s.open_summary ?? `Submitted by ${s.submitted_by ?? "someone"}`,
               href: `/os/design/dq-store/suite?table=${encodeURIComponent(s.table_name)}`, cta: "Review the checks", since: s.updated_at ?? null, tone: "purple" });
  }
  for (const p of packs.packs ?? []) {
    if (p.status !== "PENDING_APPROVAL" || same(p.created_by, user)) continue;
    out.push({ key: `qa-${p.pack_id}-${p.version}`, group: "Models and quality", type: "DOMAIN", title: `QA pack: ${p.name ?? p.pack_id} v${p.version}`, detail: `${p.domain ?? "All domains"} · by ${p.created_by ?? "someone"}`,
               href: "/os/design/qa-store", cta: "Review the pack", since: p.created_at ?? null, tone: "purple" });
  }

  const proposed = (inbox.items ?? []).filter((i) => !i.own);
  if (proposed.length) {
    const domains = [...new Set(proposed.map((i) => i.domain_name).filter(Boolean))];
    out.push({ key: "knowledge-inbox", group: "Knowledge and skills", type: "DOMAIN", title: `${proposed.length} knowledge item${proposed.length === 1 ? "" : "s"} proposed for review`,
               detail: domains.length ? domains.slice(0, 3).join(", ") + (domains.length > 3 ? ` and ${domains.length - 3} more` : "") : "From runs, cases and documents",
               href: "/os/knowledge?view=inbox", cta: "Review knowledge", since: proposed[0]?.created_at ?? null, tone: "blue" });
  }
  for (const p of (proposals.items ?? []).slice(0, 5)) {
    out.push({ key: `skill-${p.proposal_id}`, group: "Knowledge and skills", type: "PLATFORM", title: `Skill proposed: ${p.title ?? p.skill_name}`, detail: "From the skill scout",
               href: "/os/skills/registry/proposed", cta: "Review the proposal", since: p.created_at, tone: "blue" });
  }
  for (const i of incidents.incidents ?? []) {
    out.push({ key: `inc-${i.incident_id}`, group: "Operations", type: "PLATFORM", title: i.title, detail: `${i.severity} incident · ${i.status.toLowerCase()}`,
               href: `/os/run/incidents/${encodeURIComponent(i.incident_id)}`, cta: "Work the incident", since: i.last_seen, tone: i.severity === "HIGH" || i.severity === "CRITICAL" ? "red" : "amber" });
  }

  const ORDER: ActionGroup[] = ["Gates", "Approvals", "Models and quality", "Knowledge and skills", "Operations"];
  const toneRank = { red: 0, amber: 1, purple: 2, blue: 3 };
  out.sort((a, b) => ORDER.indexOf(a.group) - ORDER.indexOf(b.group) || toneRank[a.tone] - toneRank[b.tone]
    || (a.since ?? "").localeCompare(b.since ?? ""));
  return { items: out, errors };
}
