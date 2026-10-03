"use server";

import { revalidatePath } from "next/cache";
import { api, attempt, type ActionResult } from "@/lib/api";

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
