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
