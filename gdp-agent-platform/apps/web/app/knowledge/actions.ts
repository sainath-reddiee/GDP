"use server";

import { api, ApiError } from "@/lib/api";

export async function searchKnowledge(query: string) {
  try {
    return await api<{ results: { TITLE?: string; KNOWLEDGE_TYPE?: string; DOMAIN_NAME?: string; CONTENT?: string }[] }>(
      "/api/knowledge/search",
      { method: "POST", body: JSON.stringify({ query, limit: 8 }) },
    );
  } catch (e) {
    return { error: e instanceof ApiError ? e.message : "Search failed" };
  }
}
