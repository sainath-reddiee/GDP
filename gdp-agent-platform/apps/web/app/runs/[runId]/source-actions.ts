"use server";

import { revalidatePath } from "next/cache";
import { api, attempt, attemptValue, type ActionResult } from "@/lib/api";

function after(runId: string, result: ActionResult): ActionResult {
  revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export async function registerSource(runId: string, _: ActionResult | null, form: FormData): Promise<ActionResult> {
  const result = await attempt(() =>
    api(`/api/runs/${runId}/source`, {
      method: "POST",
      body: JSON.stringify({
        source_system_name: form.get("source_system_name"),
        source_type: form.get("source_type"),
        database: form.get("database"),
        schema: form.get("schema"),
        owner: form.get("owner") || null,
        security_classification: form.get("security_classification") || null,
      }),
    }),
  );
  return after(runId, result);
}

export async function validateAccess(runId: string, selected: string[]): Promise<ActionResult> {
  const result = await attempt(() =>
    api(`/api/runs/${runId}/access`, { method: "POST", body: JSON.stringify({ selected }) }),
  );
  return after(runId, result);
}

export async function executeLanding(runId: string): Promise<ActionResult> {
  const result = await attempt(() => api(`/api/runs/${runId}/landing`, { method: "POST" }));
  return after(runId, result);
}

export async function saveSourceIntent(runId: string, body: Record<string, unknown>): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/intent`, { method: "PUT", body: JSON.stringify(body) }),
  ));
}

export async function registerSourceStudio(runId: string, body: {
  source_system_name: string; source_type: string; database: string; schema: string;
  owner?: string | null; security_classification?: string | null;
}): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/source`, { method: "POST", body: JSON.stringify(body) }),
  ));
}

export async function loadTargetSuggestions(runId: string, tables: string[]) {
  const qs = new URLSearchParams({ tables: tables.join(",") });
  return attemptValue(() => api<{
    related: boolean;
    suggestions: {
      kind: "existing" | "proposed"; target_table: string; fqn: string;
      domain_name?: string | null; score: number; overlap_columns: string[]; reason: string;
    }[];
    targets: {
      target_table_id: string; domain_name: string; target_database: string; target_schema: string;
      target_table: string; fqn: string; grain?: string | null; columns?: string[];
    }[];
  }>(`/api/runs/${runId}/target-suggestions?${qs}`));
}
