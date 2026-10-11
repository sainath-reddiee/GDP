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
