"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

// ---------------------------------------------------------------- types (the /api/skill-proposals contract)

export type ProposalState = "PROPOSED" | "CANDIDATE" | "PROMOTED" | "DISMISSED" | "DISCARDED";
export type SkillProposal = {
  proposal_id: string; cluster_key: string; status: "PROPOSED" | "DISCARDED" | "DISMISSED"; state: ProposalState;
  skill_id: string | null; skill_name: string | null; title: string | null; stage: string | null;
  evidence_count: number; domain_count: number;
  evidence: { knowledge_id: string; lineage_id: string | null; title: string; domain: string | null; runs: number | null; helpfulness: number | null }[];
  replacements: { from_kind: string; placeholder: string; count: number }[];
  transfer_score: number | null; transfer_label: string | null;
  eval: { total: number; candidate_passed: number; baseline_passed: number; candidate_rate: number; baseline_rate: number;
    baseline_label: string; verdict: "better" | "same" | "worse" } | null;
  issues: { level: "error" | "warning"; message: string }[] | null;
  discard_reason: string | null; model: string | null; dismissed_by: string | null; dismiss_note: string | null;
  created_at: string;
};
export type ScoutCluster = { key: string; type: string; stage: string; theme: string; items: number; domains: number; runs: number;
  helpfulness: number; rank: number; skip: string | null; titles: string[] };
export type ScoutPlan = { settings: ScoutSettings; clusters: ScoutCluster[]; due: ScoutCluster[] };
export type ScoutSettings = { enabled: boolean; cadence_days: number; max_per_run: number; min_items: number; min_domains: number;
  min_runs: number; min_helpfulness: number; min_observed: number; title_similarity: number };
export type ScanResult = { clusters: number; proposed: { proposal_id: string; skill_name: string | null }[];
  discarded: { proposal_id: string; reason: string | null }[]; errors: string[] };

const json = (body: unknown) => JSON.stringify(body);

function changed() {
  revalidatePath("/skills/proposed");
  revalidatePath("/skills");
  revalidatePath("/work");
}

export async function listProposals() {
  return attemptValue(() => api<{ items: SkillProposal[]; available: boolean; counts: Partial<Record<ProposalState, number>> }>(
    "/api/skill-proposals?limit=200"));
}

export async function scoutPlan() {
  return attemptValue(() => api<ScoutPlan>("/api/skill-proposals/scan", { method: "POST", body: json({ dry_run: true }) }));
}

export async function scoutDraft(key: string) {
  const r = await attemptValue(() => api<ScanResult>("/api/skill-proposals/scan", { method: "POST", body: json({ dry_run: false, key }) }));
  if (r.ok) changed();
  return r;
}

export async function dismissProposal(proposalId: string, note: string) {
  const r = await attemptValue(() => api<SkillProposal>(`/api/skill-proposals/${encodeURIComponent(proposalId)}/dismiss`, {
    method: "POST", body: json({ note }),
  }));
  if (r.ok) changed();
  return r;
}

export async function saveScoutSettings(value: Partial<ScoutSettings>, reset = false) {
  const r = await attemptValue(() => api<unknown>("/api/config/platform", { method: "PUT", body: json({ key: "SKILL_SCOUT", value, reset }) }));
  if (r.ok) changed();
  return r;
}
