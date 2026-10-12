"use server";

import { revalidatePath } from "next/cache";
import { api } from "@/lib/api";
import { storeAction } from "@/lib/store-action";
import type { DmComment, DmDiff, DmImpact, DmIssue, DmSpec } from "@/lib/model-types";

const BASE = "/api/hub/domain-models";
const enc = encodeURIComponent;
const send = (method: string, body?: unknown) => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });
const touch = (modelId?: string) => {
  revalidatePath("/dashboard/modeling");
  if (modelId) revalidatePath(`/dashboard/modeling/${modelId}`);
};

export async function createModel(domainId: string) {
  const r = await storeAction(() => api<{ model_id: string; version: number }>(BASE, send("POST", { domain_id: domainId })));
  touch();
  return r;
}

export async function forkModel(modelId: string, domain: string, version?: number | null) {
  const r = await storeAction(() => api<{ model_id: string; domain: string }>(BASE, send("POST", { fork_of: modelId, domain, version: version ?? null })));
  touch();
  return r;
}

export async function baselineAll() {
  const r = await storeAction(() => api<{ log: string[] }>(`${BASE}/baseline`, send("POST", {})));
  touch();
  return r;
}

export async function openDraft(modelId: string, fromVersion?: number | null, note?: string) {
  const r = await storeAction(() => api<{ version: number; etag: string }>(`${BASE}/${enc(modelId)}/draft`,
    send("POST", { from_version: fromVersion ?? null, note: note ?? null })));
  touch(modelId);
  return r;
}

export async function saveDraft(modelId: string, spec: DmSpec, etag: string, changeNote?: string) {
  const r = await storeAction(() => api<{ version: number; etag: string; issues: DmIssue[]; counts: Record<string, number>; breaking: boolean }>(
    `${BASE}/${enc(modelId)}/draft`, send("PUT", { spec, etag, change_note: changeNote || null })));
  if (r.ok) touch(modelId);
  return r;
}

export async function validateDraft(modelId: string, spec: DmSpec) {
  return storeAction(() => api<{ spec: DmSpec; issues: DmIssue[] }>(`${BASE}/${enc(modelId)}/draft/validate`, send("POST", { spec })));
}

export async function submitDraft(modelId: string, note: string) {
  const r = await storeAction(() => api<{ version: number; counts: Record<string, number>; breaking: boolean }>(
    `${BASE}/${enc(modelId)}/submit`, send("POST", { note })));
  touch(modelId);
  return r;
}

export async function withdrawVersion(modelId: string) {
  const r = await storeAction(() => api<{ version: number }>(`${BASE}/${enc(modelId)}/withdraw`, send("POST", {})));
  touch(modelId);
  return r;
}

export async function approveVersion(modelId: string, version: number, acknowledgeBreaking: boolean) {
  const r = await storeAction(() => api<{ version: number; registry: { applied: boolean; error?: string; registered?: unknown[]; deactivated?: string[] } }>(
    `${BASE}/${enc(modelId)}/versions/${version}/approve`, send("POST", { acknowledge_breaking: acknowledgeBreaking })));
  touch(modelId);
  return r;
}

export async function rejectVersion(modelId: string, version: number, reason: string) {
  const r = await storeAction(() => api<{ version: number }>(`${BASE}/${enc(modelId)}/versions/${version}/reject`, send("POST", { reason })));
  touch(modelId);
  return r;
}

export async function applyVersion(modelId: string, version: number) {
  const r = await storeAction(() => api<{ applied: boolean }>(`${BASE}/${enc(modelId)}/versions/${version}/apply`, send("POST", {})));
  touch(modelId);
  return r;
}

export async function loadDiff(modelId: string, to: number, from?: number | null) {
  return storeAction(() => api<DmDiff>(`${BASE}/${enc(modelId)}/diff?to=${to}${from ? `&from=${from}` : ""}`));
}

export async function loadImpact(modelId: string, to: number, from?: number | null) {
  return storeAction(() => api<DmImpact>(`${BASE}/${enc(modelId)}/impact?to=${to}${from ? `&from=${from}` : ""}`));
}

export async function loadComments(modelId: string, version: number) {
  return storeAction(() => api<{ comments: DmComment[] }>(`${BASE}/${enc(modelId)}/versions/${version}/comments`));
}

export async function addComment(modelId: string, version: number, body: { body: string; path: string; kind: DmComment["kind"]; parent_id?: string | null }) {
  return storeAction(() => api<{ comment_id: string }>(`${BASE}/${enc(modelId)}/versions/${version}/comments`, send("POST", body)));
}

export async function resolveComment(modelId: string, commentId: string, resolved: boolean) {
  return storeAction(() => api<{ resolved: boolean }>(`${BASE}/${enc(modelId)}/comments/${enc(commentId)}/resolve`, send("POST", { resolved })));
}

export async function saveLayout(modelId: string, positions: Record<string, { x: number; y: number }>) {
  return storeAction(() => api<{ positions: number }>(`${BASE}/${enc(modelId)}/layout`, send("PUT", { positions })));
}

export async function saveStandard(modelId: string, standard: Record<string, unknown>) {
  const r = await storeAction(() => api<{ naming_standard: Record<string, unknown> }>(`${BASE}/${enc(modelId)}/standard`, send("PUT", standard)));
  touch(modelId);
  return r;
}

export async function importInto(modelId: string, body: { kind: "ddl" | "pack" | "schema"; text?: string; pack?: unknown; database?: string;
  schema?: string; tables?: string[]; replace?: boolean; spec: DmSpec }) {
  return storeAction(() => api<{ spec: DmSpec; issues: DmIssue[]; counts: Record<string, number>; entities: number }>(
    `${BASE}/${enc(modelId)}/import`, send("POST", body)));
}

// ---------------------------------------------------------------- Git and run proposals

export type Publishing = { repos: { repo_id: string; name: string; git_url: string; branch: string }[]; publisher_ready: boolean };
export type RunChange = {
  change_id: string; base_version: number | null; run_id: string | null; run_design_version: number | null; target_entity: string | null;
  diff: { counts?: Record<string, number>; breaking?: boolean; changes?: { kind: string; class: string; message: string }[] };
  status: string; merged_into_version: number | null; note: string | null; created_by: string | null; created_at: string | null;
  decided_by: string | null; run_name: string | null; current_state: string | null;
  source?: "RUN" | "ANNOTATION" | "TABLE_MODEL"; annotation_id?: string | null;
  table_model_id?: string | null; table_model_version?: number | null;
  patch?: { entity: string; column: string; set: Record<string, unknown> } | null;
};

export async function loadPublishing() {
  return storeAction(() => api<Publishing>(`${BASE}/publishing`));
}

export async function publishVersion(modelId: string, version: number, body: { repo_id: string; base?: string | null }) {
  const r = await storeAction(() => api<{ status: string; detail?: string; head_branch?: string; pull_request?: { url: string | null; number: number | null } }>(
    `${BASE}/${enc(modelId)}/versions/${version}/publish`, send("POST", body)));
  touch(modelId);
  return r;
}

export async function syncPrs(modelId: string, force = false) {
  const r = await storeAction(() => api<{ checked: number; merged: unknown[]; closed: unknown[]; errors: unknown[]; detail?: string }>(
    `${BASE}/${enc(modelId)}/pr-sync`, send("POST", { force })));
  touch(modelId);
  return r;
}

export async function loadChanges(modelId: string) {
  return storeAction(() => api<{ changes: RunChange[] }>(`${BASE}/${enc(modelId)}/changes`));
}

export async function mergeChange(modelId: string, changeId: string) {
  const r = await storeAction(() => api<{ version: number; counts: Record<string, number> }>(`${BASE}/${enc(modelId)}/changes/${enc(changeId)}/merge`, send("POST", {})));
  touch(modelId);
  return r;
}

export async function rejectChange(modelId: string, changeId: string, note: string) {
  const r = await storeAction(() => api<{ status: string }>(`${BASE}/${enc(modelId)}/changes/${enc(changeId)}/reject`, send("POST", { note })));
  touch(modelId);
  return r;
}
