"use server";

import { revalidatePath } from "next/cache";
import { api, ApiError, attemptValue } from "@/lib/api";

export type KnowledgeHit = { TITLE?: string; KNOWLEDGE_TYPE?: string; DOMAIN_NAME?: string; CONTENT?: string; SOURCE_REFERENCE?: string };

export type KnowledgeItem = {
  knowledge_id: string; domain_id: string; domain_name: string; knowledge_type: string; title: string; content: string;
  content_json: Record<string, unknown> | null; source_reference: string | null; status: string; version: number;
  created_by: string; created_at: string; updated_at: string | null; editable: boolean; read_only_reason: string | null;
  is_current?: boolean; lineage_id?: string | null; origin?: string | null; source_run_id?: string | null;
  confidence?: number | null; change_note?: string | null; verified_by?: string | null; verified_at?: string | null;
  review_due?: string | null; reviewed_by?: string | null; review_note?: string | null; tags?: string[]; run_name?: string | null;
};

export type FeedItem = {
  knowledge_id: string; lineage_id: string | null; title: string; knowledge_type: string; origin: string | null;
  source_run_id: string | null; run_name: string | null; version: number; status: string; is_current: boolean;
  created_by: string; created_at: string; domain_name: string; change_note: string | null; source_reference: string | null;
};
export type Overview = {
  totals: { active: number; inbox: number; learned_7d: number; stale: number; verified: number; used_30d: number; uses_30d: number };
  by: { type: Record<string, number>; origin: Record<string, number>; domain: Record<string, number>; status: Record<string, number> };
  top_used: { knowledge_id: string; title: string; knowledge_type: string; domain_name: string; uses: number; runs: number }[];
  feed: FeedItem[];
};
export type InboxItem = KnowledgeItem & { current: KnowledgeItem | null };
export type KDiff = {
  base: { knowledge_id: string; version: number }; head: { knowledge_id: string; version: number };
  files: { path: string; status: "changed" | "same"; added: number; removed: number; ops: [string, number | null, number | null, string | number][] }[];
  added: number; removed: number;
};
export type KUsage = {
  runs: { run_id: string | null; run_name: string | null; current_state: string | null; stage: string; version: number; uses: number; last_used: string }[];
  by_stage: Record<string, number>; total: number;
};
export type Policy = { types: string[]; policy: Record<string, "auto" | "review">; copilot: "auto" | "review"; defaults: Record<string, string> };

export type ItemInput = {
  domain_id?: string | null; knowledge_type: string; title: string; content: string; content_json?: unknown;
  change_note?: string | null;
};

export async function searchKnowledge(query: string, domain?: string | null, knowledgeType?: string | null) {
  try {
    return await api<{ results: KnowledgeHit[] }>("/api/knowledge/search", {
      method: "POST", body: JSON.stringify({ query, limit: 8, domain: domain || null, knowledge_type: knowledgeType || null }),
    });
  } catch (e) {
    return { error: e instanceof ApiError ? e.message : "Search failed" };
  }
}

export async function answerKnowledge(question: string, domain?: string | null, knowledgeType?: string | null) {
  return attemptValue(() => api<{ answer: string; citations: KnowledgeHit[]; hits: KnowledgeHit[]; model: string | null }>(
    "/api/knowledge/answer",
    { method: "POST", body: JSON.stringify({ question, domain: domain || null, knowledge_type: knowledgeType || null }) },
  ));
}

export async function addKnowledge(item: ItemInput) {
  const result = await attemptValue(() => api<KnowledgeItem>("/api/knowledge", { method: "POST", body: JSON.stringify(item) }));
  if (result.ok) revalidatePath("/knowledge");
  return result;
}

export async function editKnowledge(id: string, item: ItemInput) {
  const result = await attemptValue(() =>
    api<KnowledgeItem>(`/api/knowledge/${id}`, { method: "PUT", body: JSON.stringify(item) }));
  if (result.ok) revalidatePath("/knowledge");
  return result;
}

export async function setKnowledgeStatus(id: string, action: "retire" | "restore") {
  const result = await attemptValue(() => api<KnowledgeItem>(`/api/knowledge/${id}/${action}`, { method: "POST" }));
  if (result.ok) revalidatePath("/knowledge");
  return result;
}

export async function knowledgeHistory(id: string) {
  return attemptValue(() => api<{ versions: KnowledgeItem[] }>(`/api/knowledge/${id}/history`));
}

function changed() {
  revalidatePath("/knowledge");
}

export async function knowledgeVersions(id: string) {
  return attemptValue(() => api<{ lineage_id: string | null; versions: KnowledgeItem[] }>(`/api/knowledge/${id}/versions`));
}

export async function knowledgeDiff(id: string, base: string, head: string) {
  return attemptValue(() => api<KDiff>(`/api/knowledge/${id}/diff?base=${base}&head=${head}`));
}

export async function knowledgeUsage(id: string) {
  return attemptValue(() => api<KUsage>(`/api/knowledge/${id}/usage`));
}

export async function rollbackKnowledge(id: string, note?: string) {
  const r = await attemptValue(() => api<KnowledgeItem>(`/api/knowledge/${id}/rollback`, { method: "POST", body: JSON.stringify({ note: note || null }) }));
  changed();
  return r;
}

export async function verifyKnowledge(id: string, days = 180) {
  const r = await attemptValue(() => api<KnowledgeItem>(`/api/knowledge/${id}/verify`, { method: "POST", body: JSON.stringify({ review_in_days: days }) }));
  changed();
  return r;
}

export async function decideInbox(ids: string[], decision: "approve" | "reject", note?: string) {
  const r = await attemptValue(() => api<{ decided: number }>("/api/knowledge/inbox/decide", {
    method: "POST", body: JSON.stringify({ ids, decision, note: note || null }),
  }));
  changed();
  return r;
}

export async function loadPolicy() {
  return attemptValue(() => api<Policy>("/api/config/knowledge-policy"));
}

export async function savePolicy(policy: Record<string, "auto" | "review">, copilot: "auto" | "review") {
  const r = await attemptValue(() => api<Policy>("/api/config/knowledge-policy", { method: "PUT", body: JSON.stringify({ policy, copilot }) }));
  changed();
  return r;
}

export async function loadKnowledgeItem(id: string) {
  const r = await attemptValue(() => api<{ versions: KnowledgeItem[] }>(`/api/knowledge/${id}/versions`));
  if (!r.ok) return r;
  const item = r.data.versions.find((v) => v.knowledge_id === id);
  return item ? { ok: true as const, data: item } : { ok: false as const, error: "Item not found" };
}
