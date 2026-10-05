"use server";

import { revalidatePath } from "next/cache";
import { api, attempt, attemptValue, type ActionResult } from "@/lib/api";

export type TransformProposal = {
  target_column: string;
  sttm_line_id?: string | null;
  transformation: string;
  rationale?: string | null;
  dbt_notes?: string | null;
  soda_checks?: { check_type: string; severity: string; requirement: string; valid_values?: string[] }[];
};

export type SttmExport = { stage_path: string; csv: string; rows: number; sttm_version: number };

function after(runId: string, result: ActionResult): ActionResult {
  revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export async function runProfiling(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/profile`, { method: "POST" })));
}

export async function identifyDomain(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/domain`, { method: "POST" })));
}

export async function generateMapping(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/mapping`, { method: "POST" })));
}

export async function saveMappingDecisions(runId: string, decisions: Record<string, unknown>[]): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/mapping/decisions`, { method: "POST", body: JSON.stringify({ decisions }) }),
  ));
}

export async function generateSttm(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/sttm`, { method: "POST" })));
}

export async function refineTransformation(runId: string, payload: Record<string, unknown>) {
  return attemptValue(() => api<TransformProposal>(`/api/runs/${runId}/sttm/refine`, {
    method: "POST", body: JSON.stringify(payload),
  }));
}

export async function applyTransformation(runId: string, payload: Record<string, unknown>): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/sttm/apply`, { method: "POST", body: JSON.stringify(payload) }),
  ));
}

export async function exportSttmCsv(runId: string) {
  const result = await attemptValue(() => api<SttmExport>(`/api/runs/${runId}/sttm/export`, { method: "POST" }));
  if (result.ok) revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export async function generateSoda(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/soda`, { method: "POST" })));
}

export async function importSoda(runId: string, payload: { brief?: string; text?: string; filename?: string; rows?: unknown[] }): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/soda/import`, { method: "POST", body: JSON.stringify(payload) }),
  ));
}

export type SodaSaveResult = {
  decided: number;
  skipped: { expectation_id: string; reason: string }[];
  remaining: string[];
  complete: boolean;
};

export async function saveSodaDecisions(runId: string, decisions: Record<string, unknown>[]) {
  const result = await attemptValue(() =>
    api<SodaSaveResult>(`/api/runs/${runId}/soda/decisions`, {
      method: "POST",
      body: JSON.stringify({ decisions }),
    }),
  );
  if (result.ok) revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export type DbtPlanInput = {
  base_branch?: string;
  cut_branch?: string;
  repo?: string;
  origin?: string;
  git_repository?: string;
  api_integration?: string;
  dbt_project?: string;
  allowed_prefixes?: string[];
  push?: boolean;
  fetch_skeleton?: boolean;
};

export type GitBranch = {
  name: string;
  commit?: string;
  last_modified?: string;
  author?: string;
  message?: string;
};

export type GitBranchList = {
  repo: string;
  fetched: boolean;
  fetch_warning?: string;
  branches: GitBranch[];
  latest: string;
};

export async function listDbtBranches(runId: string, repo: string, fetchRemote = true) {
  const qs = new URLSearchParams({ repo, fetch: fetchRemote ? "true" : "false" });
  return attemptValue(() => api<GitBranchList>(`/api/runs/${runId}/dbt/branches?${qs}`));
}

export async function generateDbt(runId: string, plan?: DbtPlanInput): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/dbt`, { method: "POST", body: JSON.stringify(plan ?? {}) }),
  ));
}

export type DbtEnhanceResult = {
  file_path?: string;
  content: string;
  rationale?: string;
  summary?: string;
  model?: string;
  applied?: boolean;
};

export async function previewDbtEnhance(
  runId: string,
  payload: { file_path: string; prompt: string; model?: string },
) {
  return attemptValue(() =>
    api<DbtEnhanceResult>(`/api/runs/${runId}/dbt/enhance`, {
      method: "POST",
      body: JSON.stringify({ ...payload, apply: false }),
    }),
  );
}

export async function applyDbtEnhance(
  runId: string,
  payload: { file_path: string; content: string },
): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/dbt/enhance`, {
      method: "POST",
      body: JSON.stringify({ ...payload, apply: true }),
    }),
  ));
}

export async function validateDbt(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/validation`, { method: "POST" })));
}
