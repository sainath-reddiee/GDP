"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { OnboardResult, Pipeline, ShareTable } from "@/lib/hub-types";

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
  const r = await attemptValue(() => api<OnboardResult>("/api/hub/sources/share/onboard", post(body)));
  if (r.ok) { revalidatePath("/dashboard/sources"); revalidatePath("/runs"); }
  return r;
}
