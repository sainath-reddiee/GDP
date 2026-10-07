"use server";

import { revalidatePath } from "next/cache";
import { api, ApiError, attemptValue } from "@/lib/api";

export type KnowledgeHit = { TITLE?: string; KNOWLEDGE_TYPE?: string; DOMAIN_NAME?: string; CONTENT?: string; SOURCE_REFERENCE?: string };

export type KnowledgeItem = {
  knowledge_id: string; domain_id: string; domain_name: string; knowledge_type: string; title: string; content: string;
  content_json: Record<string, unknown> | null; source_reference: string | null; status: string; version: number;
  created_by: string; created_at: string; updated_at: string | null; editable: boolean; read_only_reason: string | null;
  is_current?: boolean;
};

export type ItemInput = {
  domain_id?: string | null; knowledge_type: string; title: string; content: string; content_json?: unknown;
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
