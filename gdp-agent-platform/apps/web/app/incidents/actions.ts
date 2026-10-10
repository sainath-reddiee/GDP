"use server";

import { revalidatePath } from "next/cache";
import { api, ApiError, attemptValue } from "@/lib/api";
import type { DagRun } from "../ops/actions";

// ---------------------------------------------------------------- types (the /api/ops incidents contract)

export type IncidentStatus = "OPEN" | "ACK" | "MITIGATED" | "RESOLVED" | "MUTED";
export type Severity = "P1" | "P2" | "P3" | "P4";

export type Incident = {
  incident_id: string; fingerprint: string; env_id: string; dag_id: string; task_id: string | null; run_id: string | null;
  kind: string; status: string; severity: string; team_id: string | null; team_name: string | null; assignee: string | null;
  first_seen: string | null; last_seen: string | null; occurrences: number; parent_incident_id: string | null; children: number;
  jira_key: string | null; jira_url: string | null; jira_state: string | null; title: string; error_excerpt: string | null;
  ai_summary: string | null;
};
export type SafeToRetry = "yes" | "no" | "after_fix";
export type AiEvidence = { kind: string; ref: string; text: string };
export type AiCitation = { kind: string; ref: string; path?: string | null; line?: number | null; repo_id?: string | null };
export type AiSimilar = { incident_id: string; title: string; resolution: string | null; resolved_at: string | null; score: number | null };
export type BlastRadius = { models?: string[]; tables?: string[]; domains?: string[]; sttm?: { run_id: string; target: string }[] };
export type IncidentAi = {
  category: string | null; probable_cause: string | null; evidence: AiEvidence[] | null; confidence: number | null;
  safe_to_retry: SafeToRetry | null; retry_reason: string | null; fix_steps: string[] | null; owner_hint: string | null;
  blast_radius: BlastRadius | null; citations: AiCitation[] | null; similar: AiSimilar[] | null; model: string | null;
  generated_at: string | null; context_parts: string[] | null;
};
export type AskAnswer = { answer: string; citations: { kind: string; ref: string; url?: string | null }[] };
export type Impact = {
  models: string[]; tables: string[]; domains: string[]; sttm: { run_id: string; target: string }[];
  qa: { target_table_id: string; fqn: string; last_outcome: string | null }[]; source: "code_graph" | "none" | string; detail: string | null;
};
export type RetryTask = { dag_id: string; run_id: string; task_id: string; map_index: number; state: string | null };
export type RetryPreview = { dry_run: true; tasks: RetryTask[]; preview_token: string; detail: string | null };
export type RetryDone = { dry_run: false; cleared: number; tasks: RetryTask[]; detail: string | null };
/** A call that may be held for approval (202): `pending` carries the API's message instead of an error. */
export type Gated<T> = { ok: true; data: T } | { ok: false; error: string; pending?: boolean };

export type IncidentFull = Incident & {
  ai: IncidentAi | null; resolution: string | null; resolved_by: string | null; resolved_at: string | null;
  airflow_url: string | null; muted_until: string | null;
};
export type IncidentEvent = { event_id: string; kind: string; actor: string | null; detail: unknown; created_at: string | null };
export type IncidentNotification = {
  notification_id: string; channel: string; kind: string; status: string; attempts: number; error: string | null;
  created_at: string | null; sent_at: string | null;
};
export type IncidentChild = { incident_id: string; dag_id: string; task_id: string | null; status: string };
export type IncidentDetail = {
  incident: IncidentFull; events: IncidentEvent[]; notifications: IncidentNotification[]; runs: DagRun[]; children: IncidentChild[];
};
export type IncidentSummary = { open: number; ack: number; p1_open: number; mine_open: number; unrouted: number };
export type IncidentFilters = {
  status?: string; severity?: string; team_id?: string; env_id?: string; mine?: boolean; q?: string; dag_id?: string; limit?: number; offset?: number;
};
export type BulkAction = { ids: string[]; action: "ack" | "assign" | "resolve"; assignee?: string; resolution?: string };
export type BulkResult = { incident_id: string; ok: boolean; error?: string | null };

export type OpsTeam = {
  team_id: string; name: string; jira_project: string | null; jira_component: string | null; jira_assignee_account_id: string | null;
  has_teams_webhook: boolean; escalation_minutes: number | null; has_escalation_webhook: boolean; webhook_detail?: string | null; members: string[];
};
export type TeamInput = {
  name: string; jira_project: string | null; jira_component: string | null; jira_assignee_account_id: string | null;
  escalation_minutes: number | null; members: string[];
};
export type WebhookKind = "alerts" | "escalation";

export type RoutingRule = {
  rule_id: string; priority: number; dag_pattern: string | null; tag: string | null; owner: string | null; env_id: string | null;
  team_id: string | null; severity_override: string | null; mute_until: string | null; enabled: boolean;
};
export type RuleInput = Omit<RoutingRule, "rule_id">;

export type OpsSettings = {
  jira_bot: boolean; jira_site: string | null; reopen_hours: number; alert_on_retry_for_critical: boolean; transition_on_resolve: boolean;
  done_status: string | null; rate_limit_per_10min: number; public_base_url: string | null; worker_seen_at: string | null;
  /** O3; absent on an older API */ ai_auto?: boolean; ai_severities?: string[]; weekly_digest?: boolean;
};
export type SettingsInput = Partial<Omit<OpsSettings, "jira_bot" | "jira_site" | "worker_seen_at">>;

const json = (body: unknown) => JSON.stringify(body);
const enc = encodeURIComponent;

function query(params: Record<string, string | number | boolean | undefined | null>) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    p.set(k, String(v));
  }
  return p.toString();
}

function changed(id?: string) {
  revalidatePath("/incidents");
  if (id) revalidatePath(`/incidents/${id}`);
}

// ---------------------------------------------------------------- read (OPS.VIEW)

export async function listIncidents(f: IncidentFilters) {
  return attemptValue(() => api<{ incidents: Incident[]; total: number }>(`/api/ops/incidents?${query({ ...f })}`));
}

export async function incidentSummary() {
  return attemptValue(() => api<IncidentSummary>("/api/ops/incidents/summary"));
}

export async function incidentDetail(id: string) {
  return attemptValue(() => api<IncidentDetail>(`/api/ops/incidents/${enc(id)}`));
}

export async function listTeams() {
  return attemptValue(() => api<{ teams: OpsTeam[] }>("/api/ops/teams"));
}

// ---------------------------------------------------------------- incident actions (OPS.OPERATE)

async function act(id: string, action: string, body: unknown = {}) {
  const r = await attemptValue(() => api<IncidentDetail>(`/api/ops/incidents/${enc(id)}/${action}`, { method: "POST", body: json(body) }));
  changed(id);
  return r;
}

export async function ackIncident(id: string) { return act(id, "ack"); }
export async function reopenIncident(id: string) { return act(id, "reopen"); }
export async function assignIncident(id: string, assignee: string) { return act(id, "assign", { assignee }); }
export async function resolveIncident(id: string, resolution: string) { return act(id, "resolve", { resolution }); }
export async function muteIncident(id: string, until: string, reason: string) { return act(id, "mute", { until, reason }); }
export async function commentIncident(id: string, text: string) { return act(id, "comment", { text }); }
export async function ticketIncident(id: string) { return act(id, "ticket"); }

export async function bulkIncidents(body: BulkAction) {
  const r = await attemptValue(() => api<{ results: BulkResult[] }>("/api/ops/incidents/bulk", { method: "POST", body: json(body) }));
  changed();
  return r;
}

// ---------------------------------------------------------------- AI (AI.USE), impact (OPS.VIEW), retry (OPS.OPERATE)

export async function diagnoseIncident(id: string, force: boolean) {
  const r = await attemptValue(() => api<{ ai: IncidentAi }>(`/api/ops/incidents/${enc(id)}/diagnose`, { method: "POST", body: json({ force }) }));
  changed(id);
  return r;
}

export async function askIncident(id: string, question: string) {
  return attemptValue(() => api<AskAnswer>(`/api/ops/incidents/${enc(id)}/ask`, { method: "POST", body: json({ question }) }));
}

export async function postmortemIncident(id: string) {
  return attemptValue(() => api<{ markdown: string }>(`/api/ops/incidents/${enc(id)}/postmortem`, { method: "POST", body: json({}) }));
}

export async function incidentImpact(id: string) {
  return attemptValue(() => api<Impact>(`/api/ops/incidents/${enc(id)}/impact`));
}

async function gated<T>(fn: () => Promise<T>): Promise<Gated<T>> {
  try {
    return { ok: true, data: await fn() };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, error: e.message, pending: e.status === 202 };
    throw e;
  }
}

export async function previewRetry(id: string, downstream: boolean) {
  return gated(() => api<RetryPreview>(`/api/ops/incidents/${enc(id)}/retry`, { method: "POST", body: json({ dry_run: true, downstream }) }));
}

export async function retryIncident(id: string, previewToken: string, overrideReason?: string) {
  const r = await gated(() => api<RetryDone>(`/api/ops/incidents/${enc(id)}/retry`, {
    method: "POST", body: json({ dry_run: false, preview_token: previewToken, ...(overrideReason ? { override_reason: overrideReason } : {}) }),
  }));
  changed(id);
  return r;
}

// ---------------------------------------------------------------- teams, routing, settings (INTEGRATION.MANAGE)

function adminChanged() {
  revalidatePath("/admin");
  revalidatePath("/incidents");
}

export async function createTeam(body: TeamInput) {
  const r = await attemptValue(() => api<OpsTeam>("/api/ops/teams", { method: "POST", body: json(body) }));
  adminChanged();
  return r;
}

export async function updateTeam(id: string, body: TeamInput) {
  const r = await attemptValue(() => api<OpsTeam>(`/api/ops/teams/${enc(id)}`, { method: "PUT", body: json(body) }));
  adminChanged();
  return r;
}

export async function deleteTeam(id: string) {
  const r = await attemptValue(() => api<unknown>(`/api/ops/teams/${enc(id)}`, { method: "DELETE" }));
  adminChanged();
  return r;
}

/** The URL goes to the API once and is stored as a Snowflake secret; it is never read back. */
export async function setTeamWebhook(id: string, kind: WebhookKind, url: string) {
  const r = await attemptValue(() => api<{ ok: boolean }>(`/api/ops/teams/${enc(id)}/webhook`, { method: "POST", body: json({ kind, url }) }));
  adminChanged();
  return r;
}

export async function removeTeamWebhook(id: string, kind: WebhookKind) {
  const r = await attemptValue(() => api<unknown>(`/api/ops/teams/${enc(id)}/webhook?${query({ kind })}`, { method: "DELETE" }));
  adminChanged();
  return r;
}

export async function testTeamWebhook(id: string, kind: WebhookKind) {
  return attemptValue(() => api<{ ok: boolean; detail: string | null }>(`/api/ops/teams/${enc(id)}/test`, { method: "POST", body: json({ kind }) }));
}

export async function listRouting() {
  return attemptValue(() => api<{ rules: RoutingRule[] }>("/api/ops/routing"));
}

export async function createRule(body: RuleInput) {
  const r = await attemptValue(() => api<RoutingRule>("/api/ops/routing", { method: "POST", body: json(body) }));
  adminChanged();
  return r;
}

export async function updateRule(id: string, body: RuleInput) {
  const r = await attemptValue(() => api<RoutingRule>(`/api/ops/routing/${enc(id)}`, { method: "PUT", body: json(body) }));
  adminChanged();
  return r;
}

export async function deleteRule(id: string) {
  const r = await attemptValue(() => api<unknown>(`/api/ops/routing/${enc(id)}`, { method: "DELETE" }));
  adminChanged();
  return r;
}

export async function getOpsSettings() {
  return attemptValue(() => api<OpsSettings>("/api/ops/settings"));
}

export async function saveOpsSettings(body: SettingsInput) {
  const r = await attemptValue(() => api<OpsSettings>("/api/ops/settings", { method: "PUT", body: json(body) }));
  adminChanged();
  return r;
}
