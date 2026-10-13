"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

/** The Tickets page: Jira issues and platform cases in one list (apps/api/app/tickets_api.py). */
export type TicketFilters = {
  source?: "all" | "jira" | "case"; status?: "open" | "done" | "any"; assignee?: "any" | "me" | "unassigned";
  priority?: string; type?: string; kind?: string; project?: string; sprint?: string; updated_from?: string; updated_to?: string;
  domain_id?: string; sla_breached?: boolean; linked?: boolean; copilot?: boolean; q?: string;
};
export type Ticket = {
  id: string; sources: ("JIRA" | "CASE")[]; key: string; title: string; status: string | null; done: boolean;
  priority: string | null; priority_name: string | null; type: string | null; assignee: string | null; assignee_is_me: boolean;
  project: string | null; labels: string[]; updated: string | null; created: string | null; url: string | null;
  sprint: { id: number; name: string; state: string } | null; domain_id: string | null; domain_name: string | null;
  sla_due_at: string | null; sla_breached: boolean; linked: number;
  case: { case_id: string; number: string; status: string; kind: string; jira_key: string | null; target_fqn: string | null; run_id: string | null; links: number } | null;
  copilot: { session_id: string; drafts: number } | null;
};
export type TicketList = {
  tickets: Ticket[]; counts: { jira: number; case: number; total: number };
  jira: { connected?: boolean; reason?: string; connect?: boolean; skipped?: boolean; truncated?: boolean; sprint_field?: string | null; jql?: string };
  cases: { deployed?: boolean; skipped?: boolean };
};
export type TicketView = { view_id: string; name: string; shared: boolean; owner: string | null; mine: boolean; filters: TicketFilters };
export type SprintChoice = { id: number; name: string; state: string; board: string };

/** The query string for a set of filters (empty values left out). */
function query(f: TicketFilters) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === "" || v === false || v === null) continue;
    p.set(k, v === true ? "true" : String(v));
  }
  return p.toString();
}

export async function listTickets(f: TicketFilters) {
  return attemptValue(() => api<TicketList>(`/api/tickets?${query(f)}`));
}

export async function listTicketViews() {
  return attemptValue(() => api<{ views: TicketView[]; deployed: boolean }>("/api/tickets/views"));
}

export async function saveTicketView(name: string, filters: TicketFilters, shared: boolean) {
  return attemptValue(() => api<TicketView>("/api/tickets/views", { method: "POST", body: JSON.stringify({ name, filters, shared }) }));
}

export async function deleteTicketView(viewId: string) {
  return attemptValue(() => api<{ deleted: boolean }>(`/api/tickets/views/${encodeURIComponent(viewId)}`, { method: "DELETE" }));
}

/** The active and future sprints of a project's boards (the default project when none is given). */
export async function sprintChoices(project = "") {
  return attemptValue(() => api<{ project: string; sprints: SprintChoice[]; sprint_field: string | null }>(
    `/api/jira/sprint-choices?project=${encodeURIComponent(project)}`));
}

/** Put Jira issues in a sprint (or to the backlog with null), as the signed-in person. */
export async function moveToSprint(keys: string[], sprintId: number | null) {
  const out = await attemptValue(() => api<{ moved: string[]; failed: { key: string; error: string }[] }>(
    "/api/jira/sprint-moves", { method: "POST", body: JSON.stringify({ keys, sprint_id: sprintId }) }));
  revalidatePath("/tickets");
  return out;
}
