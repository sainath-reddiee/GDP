"use server";

import { revalidatePath } from "next/cache";
import { api, attempt, attemptValue, type ActionResult } from "@/lib/api";
import type { MappingSuggestion } from "@/lib/types";

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

/** Profiles every landed table, ignoring the persistent cache. */
export async function runProfilingFresh(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/profile`, { method: "POST", body: JSON.stringify({ force_refresh: true }) }),
  ));
}

/** Busts the cached profile of one table and re-profiles it; the run's stage does not change. */
export async function refreshTableProfile(runId: string, table: string): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/profile/refresh`, { method: "POST", body: JSON.stringify({ table }) }),
  ));
}

export async function identifyDomain(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/domain`, { method: "POST" })));
}

export async function confirmDomain(runId: string, domainId: string): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/domain`, { method: "PUT", body: JSON.stringify({ domain_id: domainId }) })));
}

export async function generateMapping(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/mapping`, { method: "POST" })));
}

export async function saveMappingDecisions(runId: string, decisions: Record<string, unknown>[]): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/mapping/decisions`, { method: "POST", body: JSON.stringify({ decisions }) }),
  ));
}

/** AI copilot review. Read-only: returns proposals; nothing is saved until the reviewer accepts them. */
export async function assistMapping(runId: string, sourceColumnIds: string[], instructions: string) {
  return attemptValue(() => api<{ suggestions: MappingSuggestion[]; model: string | null; skipped: number }>(
    `/api/runs/${runId}/mapping/assist`,
    { method: "POST", body: JSON.stringify({ source_column_ids: sourceColumnIds, instructions }) },
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

export type BacktestResult = {
  results: { expectation_id: string; status: "PASS" | "FAIL" | "NOT_EVALUATED"; detail: string }[];
  summary: { PASS: number; FAIL: number; NOT_EVALUATED: number };
  queries: string[];
};

export async function backtestSoda(runId: string) {
  const result = await attemptValue(() =>
    api<BacktestResult>(`/api/runs/${runId}/soda/backtest`, { method: "POST" }),
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
  prefix?: string;
  source_key?: string;
  domain_folder?: string;
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
  grant_sql?: string | null;
  branches: GitBranch[];
  latest: string;
};

export type PublishResult = {
  status: string;
  detail?: string;
  repository?: string;
  head_branch?: string;
  base_branch?: string;
  commit_sha?: string;
  files_pushed?: number;
  branch_created?: boolean;
  pull_request?: { number?: number; url?: string; created?: boolean };
  dbt_project?: { status?: string; dbt_project?: string; detail?: string };
};

export async function publishDbt(runId: string, body: Record<string, unknown>) {
  const result = await attemptValue(() => api<PublishResult>(`/api/runs/${runId}/dbt/publish`, {
    method: "POST", body: JSON.stringify(body),
  }));
  revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export async function setupGithubPublishing(runId: string, body: { token?: string; secret?: string; external_access_integration?: string }) {
  const result = await attemptValue(() => api<{ ready: boolean; detail?: string; log: { sql: string; ok: boolean; error?: string }[] }>(
    "/api/dbt/github/setup", { method: "POST", body: JSON.stringify(body) },
  ));
  revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export async function createGitRepository(runId: string, body: { name: string; origin: string; api_integration: string; git_credentials?: string }) {
  const result = await attemptValue(() => api<{ git_repository: string; origin: string; api_integration: string }>(
    "/api/dbt/git-repository", { method: "POST", body: JSON.stringify(body) },
  ));
  revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export async function checkGithub(origin: string) {
  return attemptValue(() => api<{
    status: string; detail?: string; repository?: string; default_branch?: string; push?: boolean | null;
    private?: boolean; html_url?: string;
  }>("/api/dbt/github/check", { method: "POST", body: JSON.stringify({ origin }) }));
}

export async function rotateGithubToken(token: string) {
  return attemptValue(() => api<{ rotated: boolean; secret: string }>(
    "/api/dbt/github/token", { method: "POST", body: JSON.stringify({ token }) },
  ));
}

export async function reviewDbtFile(runId: string, filePath: string, model?: string) {
  return attemptValue(() => api<{
    file_path: string; summary: string; revised_content: string; rejected_revision: string[]; model?: string;
    findings: { severity: "error" | "warning" | "info"; rule: string; message: string; line_hint: string }[];
  }>(`/api/runs/${runId}/dbt/review`, { method: "POST", body: JSON.stringify({ file_path: filePath, model }) }));
}

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
