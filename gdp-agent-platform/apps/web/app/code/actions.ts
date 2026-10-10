"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type CodeRepo = {
  repo_id: string; name: string; git_url: string; provider: string; branch: string; git_repository: string;
  api_integration: string | null; secret_name: string | null; domain_ids: string[]; include_globs: string[]; exclude_globs: string[];
  kind: string; enabled: boolean; schedule_cron: string | null; last_commit: string | null; last_indexed: string | null;
  status: string; error: string | null; refreshing?: boolean; owns_git_repository?: boolean;
  use_for_dbt?: boolean | null; dbt_project_dir?: string | null; open_pr?: boolean | null; draft_pr?: boolean | null;
  stats: { files?: number; chunks?: number; edges?: number; languages?: number; by_kind?: Record<string, number>; dbt_projects?: string[];
           pending_files?: number; skipped_files?: number; dbt_project_roots?: { name: string; root: string }[] };
  last_run: { status?: string; started_at?: string; duration_ms?: number; files_changed?: number; error?: string | null };
};
export type CodeSetup = {
  integrations: { name: string; usable?: boolean; allowed_prefixes?: string[]; allowed_secrets?: string[]; detail?: string }[];
  repositories: { name: string; fqn: string; origin?: string; api_integration: string; usable?: boolean }[];
  secrets: { name: string; type: string }[]; warnings: string[];
};
export type CodeHit = {
  chunk_id: string; repo_id: string; repo_name: string | null; path: string; start_line: number; end_line: number; kind: string;
  name: string | null; text: string; match: "name" | "search";
};
export type CodeFile = {
  repo: { repo_id: string; name: string; git_url: string; branch: string; commit: string | null }; path: string; text: string;
  chunks: { chunk_id: string; start_line: number; end_line: number; kind: string; name: string | null; refs: string[]; sources: string[];
            columns: string[]; tests: string[] }[];
};
export type RepoBranch = { name: string; commit: string; current?: boolean };
export type Edge = { from_name: string; to_name: string; kind: string; path: string; repo_id: string; origin: string };
export type IndexRun = {
  index_run_id: string; status: string; started_at: string; finished_at: string | null; commit_sha: string | null; files_seen: number;
  files_changed: number; files_removed: number; chunks: number; edges: number; duration_ms: number | null; triggered_by: string; error: string | null;
};

function changed() {
  revalidatePath("/admin");
  revalidatePath("/code");
}

export async function loadSetup() {
  return attemptValue(() => api<CodeSetup>("/api/code/setup"));
}

export async function connectRepo(body: {
  name: string; git_url: string; branch: string; api_integration: string; existing_git_repository?: string | null;
  secret_name?: string | null; username?: string | null; token?: string | null; domain_ids: string[];
  include_globs: string[]; exclude_globs: string[]; kind: string; index_now: boolean;
}) {
  const r = await attemptValue(() => api<CodeRepo>("/api/code/repos", { method: "POST", body: JSON.stringify(body) }));
  changed();
  return r;
}

export async function updateRepo(id: string, body: Partial<Pick<CodeRepo, "branch" | "domain_ids" | "include_globs" | "exclude_globs" | "kind" | "enabled">>
  & { use_for_dbt?: boolean; dbt_project_dir?: string; open_pr?: boolean; draft_pr?: boolean }) {
  const r = await attemptValue(() => api<CodeRepo & { reindexing?: boolean }>(`/api/code/repos/${id}`, { method: "PUT", body: JSON.stringify(body) }));
  changed();
  return r;
}

export async function removeRepo(id: string, dropObjects = false) {
  const r = await attemptValue(() => api<{ removed: string; dropped: string[]; kept: string[] }>(
    `/api/code/repos/${id}${dropObjects ? "?drop_objects=true" : ""}`, { method: "DELETE" }));
  changed();
  return r;
}

export async function refreshRepo(id: string) {
  const r = await attemptValue(() => api<{ started: boolean }>(`/api/code/repos/${id}/refresh`, { method: "POST" }));
  changed();
  return r;
}

export async function scheduleRepo(id: string, cron: string | null) {
  const r = await attemptValue(() => cron
    ? api(`/api/code/repos/${id}/schedule`, { method: "PUT", body: JSON.stringify({ cron }) })
    : api(`/api/code/repos/${id}/schedule`, { method: "DELETE" }));
  changed();
  return r;
}

export async function listRepos() {
  return attemptValue(() => api<{ repos: CodeRepo[]; ready: boolean }>("/api/code/repos"));
}

export async function indexRuns(id: string) {
  return attemptValue(() => api<{ runs: IndexRun[] }>(`/api/code/repos/${id}/runs`));
}

export async function searchCode(q: string, repoId?: string, kind?: string) {
  const p = new URLSearchParams({ q });
  if (repoId) p.set("repo_id", repoId);
  if (kind) p.set("kind", kind);
  return attemptValue(() => api<{ hits: CodeHit[] }>(`/api/code/search?${p}`));
}

export async function readFile(repoId: string, path: string) {
  return attemptValue(() => api<CodeFile>(`/api/code/file?repo_id=${encodeURIComponent(repoId)}&path=${encodeURIComponent(path)}`));
}

export async function lineage(name: string, repoId?: string) {
  const p = new URLSearchParams({ name });
  if (repoId) p.set("repo_id", repoId);
  return attemptValue(() => api<{ name: string; upstream: Edge[]; downstream: Edge[] }>(`/api/code/lineage?${p}`));
}

export async function repoBranches(id: string, fetch = true) {
  return attemptValue(() => api<{ branches: RepoBranch[]; current: string; fetched: boolean; error: string | null; current_exists: boolean }>(
    `/api/code/repos/${id}/branches?fetch=${fetch}`));
}

export async function setCredentials(id: string, body: { mode: "token" | "secret" | "public"; username?: string | null; token?: string | null; secret_name?: string | null }) {
  const r = await attemptValue(() => api<CodeRepo>(`/api/code/repos/${id}/credentials`, { method: "PUT", body: JSON.stringify(body) }));
  changed();
  return r;
}

// dbt publishing to GitHub: one token for the workspace, configured once in Admin, Integrations
export type PublishingStatus = { ready: boolean; config: { secret?: string; external_access_integration?: string } | null };

export async function setupPublishing(token?: string) {
  const r = await attemptValue(() => api<{ ready: boolean; detail?: string; log: { sql: string; ok: boolean; error?: string }[] }>(
    "/api/dbt/github/setup", { method: "POST", body: JSON.stringify(token ? { token } : {}) }));
  changed();
  return r;
}

export async function rotatePublishingToken(token: string) {
  return attemptValue(() => api<{ rotated: boolean; secret: string }>("/api/dbt/github/token", { method: "POST", body: JSON.stringify({ token }) }));
}

export async function checkPublishing(origin: string) {
  return attemptValue(() => api<{ status: string; detail?: string; repository?: string; default_branch?: string; push?: boolean | null; private?: boolean }>(
    "/api/dbt/github/check", { method: "POST", body: JSON.stringify({ origin }) }));
}
