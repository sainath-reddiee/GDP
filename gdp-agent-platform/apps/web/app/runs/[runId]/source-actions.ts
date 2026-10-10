"use server";

import { revalidatePath } from "next/cache";
import { api, attempt, attemptValue, type ActionResult } from "@/lib/api";
import type { StorageType } from "@/lib/types";

function after(runId: string, result: ActionResult): ActionResult {
  revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export async function setLandingTarget(runId: string, target: {
  landing_database?: string | null; landing_schema: string; storage_type: StorageType;
}): Promise<ActionResult> {
  return after(runId, await attempt(() =>
    api(`/api/runs/${runId}/target`, { method: "PUT", body: JSON.stringify(target) }),
  ));
}

/** Validate access to the selection, then land it as-is, resuming wherever the last attempt stopped. */
export async function validateAndLand(runId: string, selected: string[]): Promise<ActionResult> {
  let failedStep: "access" | "landing" | null = null;
  const result = await attempt(async () => {
    const out = await api<{ passed: boolean; failed_step?: "access" | "landing" }>(
      `/api/runs/${runId}/prepare`, { method: "POST", body: JSON.stringify({ selected }) },
    );
    if (!out.passed) failedStep = out.failed_step === "access" ? "access" : "landing";
  });
  if (result.ok && failedStep) {
    return after(runId, {
      ok: false,
      error: failedStep === "access"
        ? "Access check failed. See the checks below, fix the selection or grants, then try again."
        : "Landing failed for some tables. See the results below, then try again.",
    });
  }
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
  landing_schema?: string | null; storage_type?: StorageType | null;
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
