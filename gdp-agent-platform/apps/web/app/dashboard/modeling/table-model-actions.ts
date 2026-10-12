"use server";

import { revalidatePath } from "next/cache";
import { api } from "@/lib/api";
import { storeAction } from "@/lib/store-action";
import type { DmDiff, DmIssue, DmSpec, TmLayer, TmRelated, TmRendered, TmSourceTable, TmSuggestion, TmTableType } from "@/lib/model-types";

const BASE = "/api/hub/table-models";
const enc = encodeURIComponent;
const send = (method: string, body?: unknown) => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });
const touch = (id?: string) => {
  revalidatePath("/dashboard/modeling");
  if (id) revalidatePath(`/dashboard/modeling/tables/${id}`);
};

export async function loadSourceTables(search = "") {
  return storeAction(() => api<{ tables: TmSourceTable[] }>(`${BASE}/sources${search ? `?search=${enc(search)}` : ""}`));
}

export async function createTableModel(body: { landing_id?: string; table_name?: string; source_name?: string; domain_id?: string | null;
  model_layer: TmLayer; table_type: TmTableType }) {
  const r = await storeAction(() => api<{ table_model_id: string; version: number }>(BASE, send("POST", body)));
  touch();
  return r;
}

export async function saveTableModel(id: string, body: { spec: DmSpec; etag: string; change_note?: string; model_layer: TmLayer; table_type: TmTableType }) {
  const r = await storeAction(() => api<{ version: number; etag: string; unchanged?: boolean; issues: DmIssue[]; counts?: Record<string, number>; breaking?: boolean }>(
    `${BASE}/${enc(id)}`, send("PUT", { ...body, change_note: body.change_note || null })));
  if (r.ok) touch(id);
  return r;
}

export async function renderTableModel(id: string, spec: DmSpec, tableType: TmTableType, domain?: string | null) {
  return storeAction(() => api<TmRendered & { issues: DmIssue[] }>(`${BASE}/${enc(id)}/render`,
    send("POST", { spec, table_type: tableType, domain: domain || null })));
}

export async function suggestColumns(id: string, spec: DmSpec, refresh = false) {
  return storeAction(() => api<{ items: TmSuggestion[]; error: string | null; cached: boolean }>(`${BASE}/${enc(id)}/suggest`,
    send("POST", { spec, refresh })));
}

export async function loadTableDiff(id: string, to: number, from?: number | null) {
  return storeAction(() => api<DmDiff>(`${BASE}/${enc(id)}/diff?to=${to}${from ? `&from=${from}` : ""}`));
}

export async function loadRelated(id: string) {
  return storeAction(() => api<TmRelated>(`${BASE}/${enc(id)}/related`));
}

export async function saveTableLayout(id: string, positions: Record<string, { x: number; y: number }>) {
  return storeAction(() => api<{ positions: number }>(`${BASE}/${enc(id)}/layout`, send("PUT", { positions })));
}

export async function setTableDomain(id: string, domainId: string) {
  const r = await storeAction(() => api<{ domain_name: string }>(`${BASE}/${enc(id)}/domain`, send("PUT", { domain_id: domainId })));
  touch(id);
  return r;
}

export async function proposeTableModel(id: string, version: number) {
  const r = await storeAction(() => api<{ model_id: string; change_id?: string; status?: string; pinned?: boolean }>(
    `${BASE}/${enc(id)}/versions/${version}/propose`, send("POST", {})));
  touch(id);
  return r;
}

export async function publishTableModel(id: string, version: number, body: { repo_id: string; base?: string | null }) {
  const r = await storeAction(() => api<{ status: string; detail?: string; head_branch?: string; pull_request?: { url: string | null; number: number | null } }>(
    `${BASE}/${enc(id)}/versions/${version}/publish`, send("POST", body)));
  touch(id);
  return r;
}

export async function syncTablePrs() {
  const r = await storeAction(() => api<{ checked: number; merged: unknown[]; closed: unknown[]; detail?: string }>(
    "/api/source-control/sync", send("POST", { kind: "TABLE_MODEL", force: false })));
  touch();
  return r;
}
