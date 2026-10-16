"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

// ---------------------------------------------------------------- types (the /api/ops/resolve contract)

export type FailureStatus = "PENDING" | "IN_PROGRESS" | "OUT_OF_SCOPE" | "AWAITING_APPROVAL" | "REJECTED" | "RESOLVED" | "TICKETED";
export type FlowMode = "AUTO" | "APPROVAL_ALWAYS" | "TICKET_ONLY" | "DISABLED";
export type FixType = "PROCEDURE" | "SKILL";

export type Preference = {
  auto_resolve_enabled: boolean; search_score_min: number; max_parallel_tasks: number; airflow_clear_enabled: boolean;
  default_approval_required: boolean; graph_hops: number; notify_target: string | null; rca_mode: "AGENT" | "GROUNDED";
  /** agent-proposed fixes from the safe-action catalogue (V063) */
  agent_proposals_enabled?: boolean; agent_fix_scope?: string | null;
};
export type ResolveSummary = {
  enabled: boolean; preference: Preference; statuses: Partial<Record<FailureStatus, number>>;
  fixes: Partial<Record<"DRAFT" | "PUBLISHED" | "RETIRED", number>>; approvals_open: number; approvals_mine: number;
  runs: Partial<Record<"SUCCESS" | "FAILED" | "TICKETED", number>>;
};
export type FailureRow = {
  failure_id: string; env_id: string; incident_id: string | null; dag_id: string; task_id: string | null; run_id: string | null;
  try_number: number | null; exception_class: string | null; exception_text: string | null; status: FailureStatus;
  flow_mode: FlowMode | null; issue_id: string | null; search_score: number | null; ticket_id: string | null;
  clear_state: string | null; detail: string | null; created_at: string; updated_at: string; issue_title?: string | null;
  severity?: string | null; jira_key?: string | null;
};
export type Rca = { mode?: string; cause?: string; evidence?: string[]; path?: string[]; fix_steps?: string[]; agent_note?: string;
  proposal?: { issue_id: string; steps: SkillStep[]; rationale: string; source: string; risk: string }; proposal_note?: string;
  category?: string | null; confidence?: number | null; graph_nodes?: number };
export type FailureDetail = {
  failure: FailureRow & { rca: Rca | null; query_id: string | null; dbt_node_id: string | null; error_signature: string;
    log_url: string | null; clear_detail: string | null; claimed_by: string | null };
  issue: KnownFix | null;
  runs: { resolution_id: string; issue_id: string | null; fix_type: FixType | null; fix_name: string | null;
    outcome: "SUCCESS" | "FAILED" | "TICKETED"; detail: Record<string, unknown> | null; actor: string | null; started_at: string;
    finished_at: string | null }[];
  steps: { resolution_id: string; step_no: number; action: string; status: string; output: unknown; started_at: string }[];
  approvals: FixApproval[];
};
export type FixApproval = {
  approval_id: string; failure_id: string; issue_id: string; fix_type: FixType | null; fix_name: string | null;
  status: "OPEN" | "APPROVED" | "REJECTED" | "DONE"; approver: string | null; decided_by: string | null;
  decision_note: string | null; requested_at: string; decided_at: string | null; issue_title?: string | null;
  /** RUN: run a published fix; PROPOSAL: publish and run the fix the agent proposed */
  kind?: "RUN" | "PROPOSAL"; proposed_by_agent?: boolean | null; rationale?: string | null; cause?: string | null;
  steps?: SkillStep[] | null;
  dag_id?: string; task_id?: string | null; run_id?: string | null; incident_id?: string | null; search_score?: number | null;
  exception_class?: string | null;
};
export type KnownFix = {
  issue_id: string; title: string; error_signature: string | null; dag_id: string | null; task_id: string | null;
  exception_class: string | null; match_text: string; fix_type: FixType; procedure_fqn: string | null; skill_id: string | null;
  approval_required: boolean | null; status: "DRAFT" | "PUBLISHED" | "RETIRED"; version_no: number; owner: string | null;
  source_incident_id: string | null; source_failure_id: string | null; notes: string | null; created_by: string | null;
  created_at: string; published_by: string | null; published_at: string | null; updated_at: string;
  skill_name?: string | null; runs_ok?: number; runs_failed?: number; search_refresh?: string;
  proposed_by_agent?: boolean | null; rationale?: string | null;
};
export type FixInput = Partial<Pick<KnownFix, "title" | "error_signature" | "dag_id" | "task_id" | "exception_class" | "match_text"
  | "fix_type" | "procedure_fqn" | "skill_id" | "approval_required" | "owner" | "notes" | "source_incident_id" | "source_failure_id">>;
export type SkillStep = { step_no: number; action: string; input?: string | Record<string, unknown> | null; note?: string };
export type FixSkill = { skill_id: string; skill_name: string; description: string | null; steps: SkillStep[]; version_no: number;
  owner: string | null; updated_at?: string };
export type Scope = { scope_id: string; dag_id: string; task_id: string | null; flow_mode: FlowMode; enabled: boolean;
  owner: string | null; updated_by: string | null; updated_at: string };
export type Route = { route_id: string; dag_id: string; task_id: string | null; approver: string; notify_target: string | null;
  updated_by: string | null; updated_at: string };

const json = (body: unknown) => JSON.stringify(body);
const enc = encodeURIComponent;

function changed() {
  revalidatePath("/ops/resolutions");
  revalidatePath("/ops/fixes");
  revalidatePath("/incidents");
  revalidatePath("/work");
  revalidatePath("/approvals");
}

// ---------------------------------------------------------------- read (OPS.VIEW)

export async function resolveSummary() {
  return attemptValue(() => api<ResolveSummary>("/api/ops/resolve/summary"));
}

export async function listFailures(params: { status?: string; dag?: string; incident_id?: string; limit?: number }) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
  return attemptValue(() => api<FailureRow[]>(`/api/ops/resolve/failures?${q}`));
}

export async function failureDetail(id: string) {
  return attemptValue(() => api<FailureDetail>(`/api/ops/resolve/failures/${enc(id)}`));
}

// ---------------------------------------------------------------- approvals (OPS.FIX_APPROVE)

export async function decideFix(approvalId: string, approve: boolean, note: string) {
  const r = await attemptValue(() => api<{ approval_id: string; status: string }>(
    `/api/ops/resolve/approvals/${enc(approvalId)}/${approve ? "approve" : "reject"}`, { method: "POST", body: json({ note }) }));
  changed();
  return r;
}

// ---------------------------------------------------------------- known fixes

export async function saveFix(issueId: string | null, body: FixInput) {
  const r = await attemptValue(() => issueId
    ? api<KnownFix>(`/api/ops/resolve/fixes/${enc(issueId)}`, { method: "PUT", body: json(body) })
    : api<KnownFix>("/api/ops/resolve/fixes", { method: "POST", body: json(body) }));
  changed();
  return r;
}

export async function publishFix(issueId: string) {
  const r = await attemptValue(() => api<KnownFix>(`/api/ops/resolve/fixes/${enc(issueId)}/publish`, { method: "POST" }));
  changed();
  return r;
}

export async function retireFix(issueId: string) {
  const r = await attemptValue(() => api<KnownFix>(`/api/ops/resolve/fixes/${enc(issueId)}/retire`, { method: "POST" }));
  changed();
  return r;
}

export async function saveSkill(skillId: string | null, body: { skill_name: string; description?: string; steps: SkillStep[]; owner?: string }) {
  const r = await attemptValue(() => skillId
    ? api<FixSkill>(`/api/ops/resolve/skills/${enc(skillId)}`, { method: "PUT", body: json(body) })
    : api<FixSkill>("/api/ops/resolve/skills", { method: "POST", body: json(body) }));
  changed();
  return r;
}

// ---------------------------------------------------------------- guardrails (OPS.RESOLVE_ADMIN)

export async function savePreference(body: Partial<Preference>) {
  const r = await attemptValue(() => api<Preference>("/api/ops/resolve/preference", { method: "PUT", body: json(body) }));
  changed();
  return r;
}

export async function saveScope(body: { scope_id?: string; dag_id: string; task_id?: string | null; flow_mode: FlowMode; enabled: boolean; owner?: string | null }) {
  const r = await attemptValue(() => api<Scope>("/api/ops/resolve/scopes", { method: "PUT", body: json(body) }));
  changed();
  return r;
}

export async function deleteScope(scopeId: string) {
  const r = await attemptValue(() => api<unknown>(`/api/ops/resolve/scopes/${enc(scopeId)}`, { method: "DELETE" }));
  changed();
  return r;
}

export async function saveRoute(body: { route_id?: string; dag_id: string; task_id?: string | null; approver: string; notify_target?: string | null }) {
  const r = await attemptValue(() => api<Route>("/api/ops/resolve/routes", { method: "PUT", body: json(body) }));
  changed();
  return r;
}

export async function deleteRoute(routeId: string) {
  const r = await attemptValue(() => api<unknown>(`/api/ops/resolve/routes/${enc(routeId)}`, { method: "DELETE" }));
  changed();
  return r;
}
