"use server";

import { revalidatePath } from "next/cache";
import { api } from "@/lib/api";
import { storeAction } from "@/lib/store-action";
import type { DqHistory, DqScan } from "@/lib/store-types";

const PATH = "/dashboard/dq-store";
const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });

export type CheckPayload = {
  check_id?: string | null; table: string; column?: string | null; severity: "WARN" | "FAIL"; requirement?: string | null;
  definition?: Record<string, unknown> | null; dsl?: string | null; domain_id?: string | null;
};
export type Parsed = { ok: true; check: { target_column: string | null; definition: Record<string, unknown>; soda_dsl: string }; headline: string }
  | { ok: false; problems: string[] };

export async function previewCheck(body: CheckPayload) {
  return storeAction(() => api<Parsed>("/api/dq/parse", post(body)));
}

export async function saveCheck(body: CheckPayload) {
  const r = await storeAction(() => api<{ check_id: string; table: string; created?: boolean }>("/api/dq/catalog", post(body)));
  revalidatePath(PATH);
  return r;
}

export async function setCheckStatus(checkId: string, status: "ACTIVE" | "PAUSED" | "RETIRED") {
  const r = await storeAction(() => api<{ status: string }>(`/api/dq/catalog/${encodeURIComponent(checkId)}/status`, post({ status })));
  revalidatePath(PATH);
  return r;
}

export async function seedChecks(body: { table?: string; run_id?: string }) {
  const r = await storeAction(() => api<{ table: string; added: number; skipped: number; problems: string[] }>("/api/dq/catalog/seed", post(body)));
  revalidatePath(PATH);
  return r;
}

export async function runTable(table: string, checkIds?: string[]) {
  const r = await storeAction(() => api<DqScan>("/api/dq/run", post({ table, check_ids: checkIds ?? null })));
  revalidatePath(PATH);
  return r;
}

export async function loadHistory(table: string) {
  return storeAction(() => api<DqHistory>(`/api/dq/history?table=${encodeURIComponent(table)}&limit=20`));
}

export async function setSchedule(table: string, cron: string | null) {
  const r = cron
    ? await storeAction(() => api<{ cron: string; task: string }>("/api/dq/schedules", { method: "PUT", body: JSON.stringify({ table, cron }) }))
    : await storeAction(() => api<{ schedule: null }>(`/api/dq/schedules?table=${encodeURIComponent(table)}`, { method: "DELETE" }));
  revalidatePath(PATH);
  return r;
}
