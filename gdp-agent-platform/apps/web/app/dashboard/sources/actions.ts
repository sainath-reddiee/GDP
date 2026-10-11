"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { IngestionControl, IngestionHistory, OnboardResult, Pipeline, ShareTable } from "@/lib/hub-types";

const post = (body: unknown, method = "POST") => ({ method, body: JSON.stringify(body) });

export async function validatePipeline(answers: Record<string, unknown>) {
  return attemptValue(() => api<{ valid: boolean; errors: Record<string, string> }>("/api/hub/pipelines/validate", post({ answers })));
}

export async function registerPipeline(answers: Record<string, unknown>) {
  const r = await attemptValue(() => api<{ source_id: string; pipeline: Pipeline }>("/api/hub/pipelines", post({ answers })));
  if (r.ok) revalidatePath("/dashboard/sources");
  return r;
}

export async function setPipelineStatus(sourceId: string, status: Pipeline["status"]) {
  const r = await attemptValue(() => api<{ source_id: string; status: string }>(
    `/api/hub/pipelines/${encodeURIComponent(sourceId)}`, post({ status }, "PATCH")));
  if (r.ok) revalidatePath("/dashboard/sources");
  return r;
}

export async function previewShare(database: string, schema: string) {
  return attemptValue(() => api<{ database: string; schema: string; tables: ShareTable[] }>(
    "/api/hub/sources/share/preview", post({ database, schema })));
}

export async function onboardShare(body: {
  database: string; schema: string; tables: string[]; source_system_name: string; run_name?: string; domain_id?: string;
  provider_account?: string; share_name?: string; mount?: boolean; landing_database?: string; landing_schema?: string;
}) {
  // no revalidatePath: refreshing the onboarding page would reset the wizard before its result shows; the Source
  // Store and the run list read fresh data when opened
  return attemptValue(() => api<OnboardResult>("/api/hub/sources/share/onboard", post(body)));
}

export type IngestionPayload = {
  source: Record<string, unknown>; target: Record<string, unknown>; load: Record<string, unknown>;
  snaplogic: Record<string, unknown>; tables: Record<string, unknown>[]; comments?: string;
};

/** Check the wizard's answers without saving (each error is keyed by step and field, e.g. "tables.0.source_table_sql"). */
export async function validateIngestion(payload: IngestionPayload) {
  return attemptValue(() => api<{ valid: boolean; errors: Record<string, string>; rows: Record<string, unknown>[] }>(
    "/api/hub/ingestion-control/validate", post(payload)));
}

/** Save the configuration: one catalog row for the source and one control row per table under one master ID. */
export async function saveIngestion(payload: IngestionPayload) {
  const r = await attemptValue(() => api<{ master_id: number; source_id: string; source_name: string; parent_pipeline_name: string;
                                           children: { child_id: number; landing_table: string }[] } | { pending_approval: true; detail: string }>(
    "/api/hub/ingestion-control", post(payload)));
  return r;  // no revalidatePath: refreshing the onboarding page would reset the wizard; the Source Store reads fresh data
}

export async function loadIngestion(masterId: number) {
  return attemptValue(() => api<{ master_id: number; controls: IngestionControl[]; history: IngestionHistory[] }>(
    `/api/hub/ingestion-control/${masterId}`));
}

export async function editIngestion(masterId: number, childId: number, changes: Record<string, unknown>) {
  const r = await attemptValue(() => api<{ action: string; is_active: boolean; changed: string[] } | { pending_approval: true; detail: string }>(
    `/api/hub/ingestion-control/${masterId}/${childId}`, post({ changes }, "PATCH")));
  return r;  // the table updates its own row; a page refresh would close the drawer
}

export type ShareSchema = { schema_name: string; tables: number; row_count: number | null };

/** The schemas of a mounted share, with their table counts. */
export async function listShareSchemas(database: string) {
  return attemptValue(() => api<{ database: string; schemas: ShareSchema[] }>(
    `/api/hub/sources/share/schemas?database=${encodeURIComponent(database)}`));
}

/** Mount an inbound share as a database now (needs IMPORT SHARE), returning its schemas. */
export async function mountShare(body: { provider_account: string; share_name: string; database: string }) {
  const r = await attemptValue(() => api<{ database: string; schemas: ShareSchema[] }>("/api/hub/sources/share/mount", post(body)));
  if (r.ok) revalidatePath("/dashboard/onboarding");
  return r;
}

/** Schemas of a database, for choosing where to land. */
export async function listSchemas(database: string) {
  return attemptValue(() => api<{ schemas: { schema_name: string }[] }>(`/api/catalog/schemas?database=${encodeURIComponent(database)}`));
}
