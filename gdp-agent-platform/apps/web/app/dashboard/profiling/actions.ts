"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { ColumnDetail, Expectations } from "@/lib/hub-types";
import type { TableInsights } from "@/lib/types";

export async function loadColumns(fqn: string, profileId?: string) {
  const qs = new URLSearchParams({ fqn, ...(profileId ? { profile_id: profileId } : {}) });
  return attemptValue(() => api<ColumnDetail>(`/api/hub/profiles/columns?${qs}`));
}

export async function loadInsights(database: string, schema: string, table: string) {
  const qs = new URLSearchParams({ database: `"${database}"`, schema: `"${schema}"`, table });
  return attemptValue(() => api<TableInsights>(`/api/catalog/profile?${qs}`));
}

export async function toggleBookmark(fqn: string, on: boolean) {
  const r = await attemptValue(() => on
    ? api<{ bookmarked: boolean }>("/api/hub/profiles/bookmarks", { method: "POST", body: JSON.stringify({ table_fqn: fqn }) })
    : api<{ bookmarked: boolean }>(`/api/hub/profiles/bookmarks?fqn=${encodeURIComponent(fqn)}`, { method: "DELETE" }));
  if (r.ok) revalidatePath("/dashboard/profiling");
  return r;
}

export async function reprofile(database: string, schema: string, tables: string[]) {
  const r = await attemptValue(() => api<{ status: string; job_id?: string }>("/api/hub/profiles/reprofile", {
    method: "POST", body: JSON.stringify({ database: `"${database}"`, schema: `"${schema}"`, tables }),
  }));
  if (r.ok) revalidatePath("/dashboard/profiling");
  return r;
}

export async function expectations(database: string, schema: string, table: string) {
  return attemptValue(() => api<Expectations>("/api/hub/profiles/expectations", {
    method: "POST", body: JSON.stringify({ database: `"${database}"`, schema: `"${schema}"`, table }),
  }));
}
