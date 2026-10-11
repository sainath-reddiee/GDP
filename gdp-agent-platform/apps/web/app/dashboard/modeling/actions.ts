"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { ModelIssue, ModelRender, ModelSpec } from "@/lib/hub-types";

const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });
type Grounding = { profiled_tables: string[]; missing_profiles: string[]; models: number; knowledge: number; skills: boolean };

export async function startFromDomain(domainId: string, mode: "MAP" | "FORK", newName?: string) {
  const qs = new URLSearchParams({ mode, ...(newName ? { new_name: newName } : {}) });
  return attemptValue(() => api<{ spec: ModelSpec; base_pack: Record<string, unknown> }>(
    `/api/hub/models/domains/${encodeURIComponent(domainId)}?${qs}`));
}

export async function propose(body: {
  mode: ModelSpec["mode"]; domain: string; parent_domain?: string | null; source_tables: string[]; use_ai: boolean;
  instructions?: string; target_platform: ModelSpec["target_platform"]; target_database?: string; target_schema?: string;
}) {
  return attemptValue(() => api<{ spec: ModelSpec; issues: ModelIssue[]; grounding: Grounding; model: string | null }>(
    "/api/hub/models/propose", post(body)));
}

export async function validateSpec(spec: ModelSpec) {
  return attemptValue(() => api<{ spec: ModelSpec; issues: ModelIssue[]; blocking: number; summary: { entities: number; columns: number; relationships: number } }>(
    "/api/hub/models/validate", post({ spec })));
}

export async function renderSpec(spec: ModelSpec, basePack: Record<string, unknown> | null, projectDir = "") {
  const qs = projectDir ? `?project_dir=${encodeURIComponent(projectDir)}` : "";
  return attemptValue(() => api<ModelRender>(`/api/hub/models/render${qs}`, post({ spec, base_pack: basePack })));
}

export async function saveDraft(spec: ModelSpec, designId: string | null) {
  const r = await attemptValue(() => api<{ design_id: string; version: number; issues: ModelIssue[] }>(
    "/api/hub/models", post({ spec, design_id: designId })));
  if (r.ok) revalidatePath("/dashboard/modeling");
  return r;
}

export async function finalize(designId: string, spec: ModelSpec, basePack: Record<string, unknown> | null, repoId: string | null) {
  const r = await attemptValue(() => api<{ design_id: string; version: number; files: string[]; branch: string }>(
    `/api/hub/models/${encodeURIComponent(designId)}/finalize`, post({ spec, base_pack: basePack, repo_id: repoId })));
  if (r.ok) revalidatePath("/dashboard/modeling");
  return r;
}

export async function publish(designId: string, version: number, repoId: string) {
  const r = await attemptValue(() => api<{ status: string; detail?: string; head_branch: string; pull_request?: { url?: string } }>(
    `/api/hub/models/${encodeURIComponent(designId)}/publish`, post({ version, repo_id: repoId })));
  if (r.ok) revalidatePath("/dashboard/modeling");
  return r;
}

// ---------------------------------------------------------------- dbt generations from runs (store edits queue)

export async function saveDbtFile(generationId: string, body: { file_path: string; content: string; base_sha: string; note: string }) {
  const { storeAction } = await import("@/lib/store-action");
  const r = await storeAction(() => api<{ generation_id: string; generation_version: number; new_version: boolean }>(
    `/api/hub/dbt/${encodeURIComponent(generationId)}/files`, post(body)));
  revalidatePath(`/dashboard/modeling/dbt/${generationId}`);
  return r;
}

export async function reviewDbtFile(runId: string, filePath: string) {
  return attemptValue(() => api<{ file_path: string; summary: string; revised_content: string;
    findings: { severity: "error" | "warning" | "info"; rule: string; message: string; line_hint: string }[] }>(
    `/api/runs/${encodeURIComponent(runId)}/dbt/review`, post({ file_path: filePath })));
}
