"use server";

import { api, attemptValue } from "@/lib/api";

export type CopilotPage = { path: string; database?: string; schema?: string; table?: string };
export type CopilotAnswer = {
  conversation_id: string; message_id: string; answer: string; citations: string[];
  sources: { key: string; title: string | null; type: string | null }[];
  actions: { kind: string; label: string; href: string }[]; follow_ups: string[];
  domain: { id: string | null; name: string } | null; model: string; duration_ms: number;
};

export async function copilotSuggestions(page: CopilotPage) {
  const q = new URLSearchParams({ path: page.path, database: page.database ?? "", schema: page.schema ?? "", table: page.table ?? "" });
  return attemptValue(() => api<{ suggestions: string[] }>(`/api/copilot/suggestions?${q.toString()}`));
}

export async function copilotAsk(body: {
  question: string; page: CopilotPage; history: { role: string; content: string }[]; conversation_id?: string | null;
}) {
  return attemptValue(() => api<CopilotAnswer>("/api/copilot/ask", { method: "POST", body: JSON.stringify(body) }));
}

export async function saveCopilotAnswer(body: { domain_id: string; knowledge_type: string; title: string; content: string }) {
  return attemptValue(() => api<{ knowledge_id: string }>("/api/knowledge", { method: "POST", body: JSON.stringify(body) }));
}
