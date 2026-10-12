"use server";

import { api, attemptValue } from "@/lib/api";
import type { CopilotSession } from "@/lib/copilot-types";

export async function createCopilotSession(body: { title: string; jira_key?: string | null }) {
  return attemptValue(() => api<{ session_id: string }>("/api/copilot/sessions", { method: "POST", body: JSON.stringify(body) }));
}

export async function loadCopilotSession(sessionId: string) {
  return attemptValue(() => api<CopilotSession>(`/api/copilot/sessions/${encodeURIComponent(sessionId)}`));
}

export async function discardCopilotChange(sessionId: string, changeId: string) {
  return attemptValue(() => api<{ change_id: string; status: string }>(
    `/api/copilot/sessions/${encodeURIComponent(sessionId)}/changes/${encodeURIComponent(changeId)}/discard`, { method: "POST" }));
}
