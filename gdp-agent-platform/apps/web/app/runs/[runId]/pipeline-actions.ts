"use server";

import { revalidatePath } from "next/cache";
import { api, attempt, type ActionResult } from "@/lib/api";

function after(runId: string, result: ActionResult): ActionResult {
  revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export async function runProfiling(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/profile`, { method: "POST" })));
}

export async function identifyDomain(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/domain`, { method: "POST" })));
}

export async function generateMapping(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/mapping`, { method: "POST" })));
}

export async function saveMappingDecisions(runId: string, decisions: Record<string, unknown>[]): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/mapping/decisions`, { method: "POST", body: JSON.stringify({ decisions }) }),
  ));
}

export async function generateSttm(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/sttm`, { method: "POST" })));
}

export async function generateSoda(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/soda`, { method: "POST" })));
}

export async function importSoda(runId: string, payload: { brief?: string; text?: string; filename?: string; rows?: unknown[] }): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/soda/import`, { method: "POST", body: JSON.stringify(payload) }),
  ));
}

export async function saveSodaDecisions(runId: string, decisions: Record<string, unknown>[]): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/soda/decisions`, { method: "POST", body: JSON.stringify({ decisions }) }),
  ));
}

export async function generateDbt(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/dbt`, { method: "POST" })));
}

export async function validateDbt(runId: string): Promise<ActionResult> {
  return after(runId, await attempt(() => api(`/api/runs/${runId}/validation`, { method: "POST" })));
}
