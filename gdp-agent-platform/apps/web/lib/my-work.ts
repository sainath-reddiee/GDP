import { api } from "@/lib/api";
import { can, type RunSummary, type WhoAmI } from "@/lib/types";
import { GATES, type Gate } from "@/lib/run-labels";
import type { ChangeRequest } from "@/app/governance-actions";

// The signed-in person's work, read from the endpoints the other pages already use (no new backend). Lists the API
// does not filter per user are filtered here: runs by created_by, gates by the approval privilege, pull requests by
// opened_by. Every source is optional: one that fails or is not deployed leaves its section empty, never the page.

export type GateItem = { run: RunSummary; gate: Gate };
export type FixItem = { run: RunSummary; reason: string | null };
export type QuestionItem = { question_id: string; domain_id: string; domain_name: string; question: string; stage: string | null;
  run_id: string | null; created_at: string };
export type InboxItem = { knowledge_id: string; title: string; knowledge_type: string; domain_name: string | null; origin: string | null;
  created_at: string; own?: boolean };
export type TicketItem = { id: string; key: string | null; title: string; status: string; priority_name: string | null; url: string | null;
  updated: string | null; sla_breached?: boolean };
export type IncidentItem = { incident_id: string; title: string; severity: string; status: string; dag_id: string; last_seen: string };
export type PrItem = { title: string | null; asset_name: string | null; label: string | null; state: string; pr_url: string | null;
  href: string | null; opened_by: string | null; opened_at: string | null; kind: string };

export type MyWork = {
  gates: GateItem[];
  requests: ChangeRequest[];
  inbox: InboxItem[];
  questions: QuestionItem[];
  running: RunSummary[];
  fix: FixItem[];
  tickets: TicketItem[] | null;
  incidents: IncidentItem[] | null;
  prs: PrItem[];
  /** the gate roles this person approves (all four when governance is off) */
  approves: Gate[];
  /** runs anyone has in flight, for the "the agent is working" line */
  inFlightTotal: number;
};

const same = (a: string | null | undefined, b: string | null | undefined) =>
  !!a && !!b && a.trim().toUpperCase() === b.trim().toUpperCase();
const safe = <T>(p: Promise<T>, fallback: T) => p.catch(() => fallback);

export async function loadMyWork(me: WhoAmI | null): Promise<MyWork> {
  const user = me?.user ?? "";
  const approves = Object.values(GATES).filter((g) => can(me, g.privilege));
  const [review, running, failed, requests, inbox, myDomains, tickets, incidents, prs] = await Promise.all([
    safe(api<{ runs: RunSummary[] }>("/api/runs?needs_review=true&sort=updated&limit=200"), { runs: [] }),
    safe(api<{ runs: RunSummary[]; total?: number }>("/api/runs?status=running&sort=updated&limit=200"), { runs: [], total: 0 }),
    safe(api<{ runs: RunSummary[] }>("/api/runs?status=failed&sort=updated&limit=100"), { runs: [] }),
    safe(api<{ requests: ChangeRequest[] }>("/api/governance/requests?scope=inbox"), { requests: [] }),
    safe(api<{ items: InboxItem[] }>("/api/knowledge/inbox"), { items: [] }),
    safe(api<{ domains: { domain_id: string; name: string; steward: boolean }[] }>("/api/governance/my-domains"), { domains: [] }),
    can(me, "JIRA.READ") || can(me, "CASE.WORK")
      ? safe(api<{ tickets: TicketItem[] }>("/api/tickets?assignee=me").then((r) => r.tickets ?? []), null)
      : Promise.resolve(null),
    can(me, "OPS.VIEW")
      ? safe(api<{ incidents: IncidentItem[] }>("/api/ops/incidents?mine=true&limit=20").then((r) => r.incidents ?? []), null)
      : Promise.resolve(null),
    safe(api<{ prs: PrItem[] }>("/api/source-control/prs?state=OPEN"), { prs: [] }),
  ]);

  // gates this person can decide, oldest wait first
  const gates = review.runs
    .filter((r) => !r.is_archived && GATES[r.current_state] && can(me, GATES[r.current_state].privilege))
    .map((run) => ({ run, gate: GATES[run.current_state] }))
    .sort((a, b) => (a.run.updated_at ?? a.run.created_at).localeCompare(b.run.updated_at ?? b.run.created_at));

  const mine = (r: RunSummary) => same(r.created_by, user) && !r.is_archived;
  // needs a fix: my failed runs from the last 14 days and my runs whose validation failed
  const recent = Date.now() - 14 * 24 * 3600 * 1000;
  const broken = [
    ...review.runs.filter((r) => mine(r) && r.current_state === "VALIDATION_FAILED"),
    ...failed.runs.filter((r) => mine(r) && r.current_state !== "CANCELLED"
      && new Date(r.updated_at ?? r.created_at).getTime() >= recent),
  ].slice(0, 6);
  const fix = await Promise.all(broken.map(async (run) => {
    if (run.current_state === "VALIDATION_FAILED") return { run, reason: "Validation failed: open the checks, fix and validate again" };
    const detail = await safe(api<{ failure_reason?: string | null }>(`/api/runs/${run.run_id}`), {} as { failure_reason?: string | null });
    return { run, reason: detail.failure_reason ?? null };
  }));

  // open questions in the domains this person stewards (at most six domains, newest first)
  const stewarded = myDomains.domains.filter((d) => d.steward).slice(0, 6);
  const questions = (await Promise.all(stewarded.map((d) =>
    safe(api<{ questions: Omit<QuestionItem, "domain_name">[] }>(`/api/domains/${encodeURIComponent(d.domain_id)}/questions?status=OPEN`), { questions: [] })
      .then((r) => (r.questions ?? []).map((q) => ({ ...q, domain_name: d.name })))))).flat()
    .sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 8);

  return {
    gates,
    requests: requests.requests ?? [],
    inbox: (inbox.items ?? []).filter((i) => !i.own).slice(0, 6),
    questions,
    running: running.runs.filter(mine).slice(0, 8),
    fix,
    tickets: tickets ? tickets.slice(0, 6) : null,
    incidents: incidents ? incidents.slice(0, 5) : null,
    prs: (prs.prs ?? []).filter((p) => same(p.opened_by, user)).slice(0, 6),
    approves,
    inFlightTotal: running.total ?? running.runs.length,
  };
}
