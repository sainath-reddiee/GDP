"use server";

import { api, apiForm, attemptValue } from "@/lib/api";
import type { Attachment, CopilotSession, SessionRow } from "@/lib/copilot-types";

export async function createCopilotSession(body: { title: string; jira_key?: string | null }) {
  return attemptValue(() => api<{ session_id: string }>("/api/copilot/sessions", { method: "POST", body: JSON.stringify(body) }));
}

export async function listCopilotSessions() {
  return attemptValue(() => api<{ sessions: SessionRow[]; deployed: boolean }>("/api/copilot/sessions"));
}

export async function loadCopilotSession(sessionId: string) {
  return attemptValue(() => api<CopilotSession>(`/api/copilot/sessions/${encodeURIComponent(sessionId)}`));
}

export async function discardCopilotChange(sessionId: string, changeId: string) {
  return attemptValue(() => api<{ change_id: string; status: string }>(
    `/api/copilot/sessions/${encodeURIComponent(sessionId)}/changes/${encodeURIComponent(changeId)}/discard`, { method: "POST" }));
}

/** Attach files: the API reads their text now; the files themselves are not kept. */
export async function uploadCopilotFiles(sessionId: string, form: FormData) {
  return attemptValue(() => apiForm<{ attachments: Attachment[] }>(
    `/api/copilot/sessions/${encodeURIComponent(sessionId)}/attachments`, form));
}

export async function removeCopilotFile(sessionId: string, attachmentId: string) {
  return attemptValue(() => api<{ removed: boolean }>(
    `/api/copilot/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(attachmentId)}/remove`, { method: "POST" }));
}

/** Remove a conversation: its unpublished drafts are discarded; anything already published stays published. */
export async function deleteCopilotSession(sessionId: string) {
  return attemptValue(() => api<{ session_id: string; deleted: boolean }>(
    `/api/copilot/sessions/${encodeURIComponent(sessionId)}/delete`, { method: "POST" }));
}
