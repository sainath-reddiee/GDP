"use server";

import { revalidatePath } from "next/cache";
import { api } from "@/lib/api";
import { storeAction } from "@/lib/store-action";
import type { PackDetail, PackQuery, PackRun, QaExecution } from "@/lib/store-types";

const PATH = "/dashboard/qa-store";
const json = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });

export type PackPayload = {
  name: string; description: string | null; tags: string[]; change_note?: string | null; base_version?: number | null;
  queries: Pick<PackQuery, "query_name" | "description" | "sql_expression" | "severity" | "expectation" | "tags">[];
};

export async function loadPack(packId: string, version?: number | null) {
  return storeAction(() => api<PackDetail>(`/api/qa-packs/${packId}${version ? `?version=${version}` : ""}`));
}

export async function loadPackRuns(packId: string) {
  return storeAction(() => api<{ runs: PackRun[] }>(`/api/qa-packs/${packId}/runs?limit=20`).then((r) => r.runs));
}

export async function createPack(domainId: string, payload: PackPayload) {
  const r = await storeAction(() => api<{ pack_id: string; version: number }>(`/api/domains/${encodeURIComponent(domainId)}/qa-packs`, json(payload)));
  revalidatePath(PATH);
  return r;
}

/** A new draft pack from a .sql file: annotated pack format, or plain read-only queries separated by ';'. */
export type Imported = { pack_id: string; version: number; status: string; queries: number; name: string; skipped: string[] };

export async function importPack(domainId: string, body: { filename: string; text: string; name?: string | null }) {
  // no revalidation here: the dialog shows the outcome and refreshes the page when it closes
  return storeAction(() => api<Imported>(`/api/domains/${encodeURIComponent(domainId)}/qa-packs/import`, json(body)));
}

export async function savePack(packId: string, payload: PackPayload) {
  const r = await storeAction(() => api<{ version: number; diff: { added: string[]; removed: string[]; changed: { query_name: string; fields: string[] }[] } | null }>(
    `/api/qa-packs/${packId}`, { method: "PUT", body: JSON.stringify(payload) }));
  revalidatePath(PATH);
  return r;
}

export async function transitionPack(packId: string, step: "submit" | "reject" | "approve" | "retire", note?: string) {
  const body = step === "submit" || step === "reject" ? json({ note: note ?? null }) : { method: "POST" };
  const r = await storeAction(() => api<{ version?: number; status: string; knowledge?: number }>(`/api/qa-packs/${packId}/${step}`, body));
  revalidatePath(PATH);
  return r;
}

/** Runs a pack version (approved by default), some of its queries, or one ad-hoc query (not stored). */
export async function executeQa(body: { domain_id: string; pack_id?: string; version?: number | null; query_ids?: string[]; sql?: string }) {
  const r = await storeAction(() => api<QaExecution>("/api/qa/execute", json(body)));
  if (body.pack_id && !body.sql) revalidatePath(PATH);
  return r;
}

// ---------------------------------------------------------------- run tests

export async function promoteTests(body: { test_ids: string[]; domain: string; pack_id?: string | null; name?: string | null; note?: string | null }) {
  const r = await storeAction(() => api<{ pack_id: string | null; added: number; skipped: number }>("/api/qa/run-tests/promote", json(body)));
  revalidatePath(PATH);
  return r;
}

export async function openTestCase(qaResultId: string) {
  return storeAction(() => api<{ case: { case_id: string; number: number } }>("/api/cases/from-result", json({ qa_result_id: qaResultId })));
}

// ---------------------------------------------------------------- Git: repository conflicts and publishing (V042)

export async function loadPackSync(packId: string) {
  return storeAction(() => api<{ conflict: import("@/lib/store-types").PackConflict | null }>(`/api/qa-packs/${encodeURIComponent(packId)}/sync`));
}

export async function resolvePackSync(packId: string, syncId: string, how: "take-repo" | "keep-app") {
  const r = await storeAction(() => api<{ resolution: string; draft_version?: number }>(
    `/api/qa-packs/${encodeURIComponent(packId)}/sync/${encodeURIComponent(syncId)}/${how}`, { method: "POST" }));
  revalidatePath("/dashboard/qa-store");
  return r;
}

export async function loadGitRepos() {
  return storeAction(() => api<{ repos: { repo_id: string; name: string; branch: string }[]; publisher_ready: boolean }>("/api/hub/domain-models/publishing"));
}

export async function publishPack(packId: string, version: number, repoId: string) {
  const r = await storeAction(() => api<{ status: string; detail?: string; head_branch?: string; path?: string }>(
    `/api/qa-packs/${encodeURIComponent(packId)}/publish`, { method: "POST", body: JSON.stringify({ version, repo_id: repoId }) }));
  return r;  // the drawer updates its own version list; the pack list does not show pull requests
}

export async function syncPackPrs(packId: string, force = false) {
  return storeAction(() => api<{ checked: number; merged: unknown[]; closed: unknown[]; detail?: string }>(
    `/api/qa-packs/${encodeURIComponent(packId)}/pr-sync`, { method: "POST", body: JSON.stringify({ force }) }));
}
