"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type JiraStatus = {
  installed: boolean; ready: boolean; missing?: string[]; client_id?: boolean; client_secret?: boolean; token_key?: boolean;
  site_url?: string | null; default_project?: string | null; redirect_uri?: string; users_connected?: number;
  connected?: { cloud_id: string; site_url: string | null; account_id: string | null; display_name: string | null; connected_at: string; updated_at: string } | null;
};
export type JiraIssue = {
  key: string; id: string; summary: string; status: string | null; status_category: string | null; priority: string | null;
  type: string | null; assignee: string | null; reporter: string | null; project: string | null; labels: string[];
  updated: string | null; created: string | null; url: string | null; linked?: number;
};
export type JiraIssueDetail = JiraIssue & {
  description: string; environment: string;
  comments: { id: string; author: string | null; created: string | null; text: string }[];
  attachments: { id: string; name: string; mime: string; size: number | null; author: string | null; created: string | null; previewable: boolean }[];
  links: { link_id: string; run_id: string; qa_test_id: string | null; target_table: string | null; linked_by: string; linked_at: string }[];
  history: { action: string; status: string; error: string | null; acted_by: string; acted_at: string; run_id: string | null }[];
};
export type TriageTest = {
  title: string; category: string; objective: string; sql: string; expected: string; valid: boolean; problems: string[];
  compile_error: string | null; note: string | null;
};
export type Triage = {
  diagnosis: string; likely_cause: string; affected_columns: string[]; severity: string; reproducible: boolean; questions: string[];
  tests: TriageTest[]; model: string; issue: string;
};
export type RunLink = { link_id: string; issue_key: string; qa_test_id: string | null; test_title: string | null; summary: string | null;
                        status: string | null; linked_by: string; linked_at: string };

export async function jiraStatus() {
  return attemptValue(() => api<JiraStatus>("/api/jira/status"));
}

export async function saveJiraConfig(body: { site_url?: string; client_id?: string; redirect_uri?: string; default_project?: string }) {
  const r = await attemptValue(() => api<JiraStatus>("/api/jira/config", { method: "PUT", body: JSON.stringify(body) }));
  revalidatePath("/admin");
  return r;
}

export async function connectJira(returnTo: string) {
  return attemptValue(() => api<{ url: string }>("/api/jira/connect", { method: "POST", body: JSON.stringify({ return_to: returnTo }) }));
}

export async function disconnectJira() {
  const r = await attemptValue(() => api("/api/jira/connection", { method: "DELETE" }));
  revalidatePath("/admin");
  return r;
}

export async function jiraIssues(scope: "mine" | "run" | "search", opts: { runId?: string; q?: string; project?: string } = {}) {
  const p = new URLSearchParams({ scope });
  if (opts.runId) p.set("run_id", opts.runId);
  if (opts.q) p.set("q", opts.q);
  if (opts.project) p.set("project", opts.project);
  return attemptValue(() => api<{ issues: JiraIssue[]; jql: string | null }>(`/api/jira/issues?${p}`));
}

export async function jiraIssue(key: string) {
  return attemptValue(() => api<JiraIssueDetail>(`/api/jira/issues/${encodeURIComponent(key)}`));
}

export async function jiraAttachment(key: string, id: string) {
  return attemptValue(() => api<{ name: string; text: string; truncated: boolean }>(`/api/jira/issues/${encodeURIComponent(key)}/attachments/${encodeURIComponent(id)}`));
}

export async function runJiraLinks(runId: string) {
  return attemptValue(() => api<{ links: RunLink[] }>(`/api/runs/${runId}/jira/links`));
}

export async function linkJira(runId: string, body: { issue_key: string; qa_test_id?: string | null; remote_link?: boolean }) {
  const r = await attemptValue(() => api<{ linked: string; already: boolean; remote_link: string | null }>(
    `/api/runs/${runId}/jira/links`, { method: "POST", body: JSON.stringify(body) }));
  revalidatePath(`/runs/${runId}/qa`);
  return r;
}

export async function unlinkJira(runId: string, linkId: string) {
  const r = await attemptValue(() => api(`/api/runs/${runId}/jira/links/${linkId}`, { method: "DELETE" }));
  revalidatePath(`/runs/${runId}/qa`);
  return r;
}

export async function triageJira(runId: string, key: string) {
  return attemptValue(() => api<Triage>(`/api/runs/${runId}/jira/${encodeURIComponent(key)}/triage`, { method: "POST" }));
}

export async function jiraReport(runId: string, key: string) {
  return attemptValue(() => api<{ markdown: string; results: { title: string; outcome: string | null }[]; run_url: string }>(
    `/api/runs/${runId}/jira/${encodeURIComponent(key)}/report`));
}

export async function commentJira(key: string, markdown: string, runId?: string) {
  return attemptValue(() => api<{ comment_id: string; url: string }>(`/api/jira/issues/${encodeURIComponent(key)}/comment`,
    { method: "POST", body: JSON.stringify({ markdown, run_id: runId }) }));
}

export async function jiraTransitions(key: string) {
  return attemptValue(() => api<{ transitions: { id: string; name: string; to: string | null; category: string | null }[] }>(
    `/api/jira/issues/${encodeURIComponent(key)}/transitions`));
}

export async function transitionJira(key: string, transitionId: string, runId?: string) {
  return attemptValue(() => api<{ status: string }>(`/api/jira/issues/${encodeURIComponent(key)}/transition`,
    { method: "POST", body: JSON.stringify({ transition_id: transitionId, run_id: runId }) }));
}
