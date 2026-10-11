"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { ColumnAnnotation, ColumnDetail, Expectations } from "@/lib/hub-types";
import type { TableInsights } from "@/lib/types";

export async function loadColumns(fqn: string, profileId?: string, compareTo?: string) {
  const qs = new URLSearchParams({ fqn, ...(profileId ? { profile_id: profileId } : {}), ...(compareTo ? { compare_to: compareTo } : {}) });
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

// ---------------------------------------------------------------- schedules and annotations (V041)

const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });

export async function saveSchedule(body: { schedule_id?: string; database: string; schema: string; tables: string[]; cron: string; force_refresh: boolean }) {
  const r = await attemptValue(() => api<{ schedule_id: string; task: string }>("/api/hub/profiles/schedules", { method: "PUT", body: JSON.stringify(body) }));
  if (r.ok) revalidatePath("/dashboard/profiling");
  return r;
}

export async function deleteSchedule(id: string) {
  const r = await attemptValue(() => api<{ deleted: boolean }>(`/api/hub/profiles/schedules?id=${encodeURIComponent(id)}`, { method: "DELETE" }));
  if (r.ok) revalidatePath("/dashboard/profiling");
  return r;
}

export async function runSchedule(id: string) {
  const r = await attemptValue(() => api<{ status: string; profiled?: number; cached?: number; failed?: number; drifted?: number; error?: string }>(
    `/api/hub/profiles/schedules/${encodeURIComponent(id)}/run`, { method: "POST" }));
  revalidatePath("/dashboard/profiling");
  return r;
}

export type AnnotationDraft = {
  table: string; column: string; description?: string; business_definition?: string; pii_classification?: string;
  semantic_type?: string; accepted_values?: string[]; note?: string;
};

export async function proposeAnnotation(body: AnnotationDraft) {
  const r = await attemptValue(() => api<{ annotation_id: string; status: string }>("/api/hub/profiles/annotations", post(body)));
  if (r.ok) revalidatePath("/dashboard/profiling");
  return r;
}

export async function decideAnnotation(id: string, decision: "approve" | "reject" | "withdraw", note?: string) {
  const r = await attemptValue(() => api<{ status: string; model?: { model_id?: string; change_id?: string; entity?: string; skipped?: string; error?: string } | null }>(
    `/api/hub/profiles/annotations/${encodeURIComponent(id)}/${decision}`, post(decision === "withdraw" ? {} : { note: note ?? null })));
  if (r.ok) revalidatePath("/dashboard/profiling");
  return r;
}

export async function loadAnnotations(table: string) {
  return attemptValue(() => api<{ annotations: ColumnAnnotation[] }>(`/api/hub/profiles/annotations?table=${encodeURIComponent(table)}`));
}

