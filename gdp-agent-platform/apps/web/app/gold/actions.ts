"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type GoldInputs = {
  domain_id: string; domain_name: string; role: "MASTERED" | "CONSUMER" | null;
  consumes: { domain: string; silver_column: string; gold_column: string }[];
  schemas: { database: string; schema: string; error?: string; missing?: string[];
    tables: { table: string; type: string; row_count: number | null; last_altered: string | null; suggested: boolean }[] }[];
};
export type GoldTable = { entity: string; table_type: "FACT" | "DIMENSION" | "BRIDGE" | string; grain: string | null;
  description: string | null; columns: number; fqn: string };
export type RunGold = {
  run_id: string; domain_id: string; domain_name: string; role: "MASTERED" | "CONSUMER" | null;
  consumes: { domain: string; silver_column: string; gold_column: string }[];
  model: { model_id: string; approved_version: number | null; open_version: number | null } | null;
  tables: GoldTable[];
  target: { model_id: string; version: number; entity: string; fqn: string | null } | null;
  inputs: { database: string; schema: string; table: string; row_count: number | null; columns: number }[];
  can_choose_target: boolean;
  /** set on a table run: the Gold run it belongs to */
  parent_run_id?: string | null;
  /** a Gold run's table runs (its star schema) */
  children?: GoldChild[];
};
export type GoldChild = { run_id: string; run_name: string; entity: string | null; fqn: string | null; table_type: string | null;
  current_state: string; current_stage: string | null; status: string | null };

export async function goldInputs(domainId: string) {
  return attemptValue(() => api<GoldInputs>(`/api/gold/inputs?domain_id=${encodeURIComponent(domainId)}`));
}

export async function startGoldRun(body: { domain_id: string; database: string; schema: string; tables: string[]; run_name?: string }) {
  return attemptValue(async () => {
    const r = await api<{ run_id: string; stage: string; error?: string }>("/api/gold/runs", { method: "POST", body: JSON.stringify(body) });
    revalidatePath("/gold");
    return r;
  });
}

export async function runGold(runId: string) {
  return attemptValue(() => api<RunGold>(`/api/runs/${encodeURIComponent(runId)}/gold`));
}

export async function proposeGold(runId: string) {
  return attemptValue(async () => {
    const r = await api<{ model_id: string; version: number; notes: string[]; tables: { entity: string; table_type: string; conformed: boolean }[];
      reference: { reference_id: string; title: string | null } | null;
      score: { pct: number; matched: number; total: number; details?: { types_agree_pct?: number } } | null;
      alignment: { proposed: string | null; reference: string | null; how: string; added_columns?: string[] }[] }>(
      `/api/runs/${encodeURIComponent(runId)}/gold/propose`, { method: "POST" });
    revalidatePath(`/runs/${runId}/modeling`);
    return r;
  });
}

export async function chooseGoldTarget(runId: string, entity: string) {
  return attemptValue(async () => {
    const r = await api<{ target_model: string; mapping_reset?: boolean }>(`/api/runs/${encodeURIComponent(runId)}/gold/target`,
      { method: "POST", body: JSON.stringify({ entity }) });
    revalidatePath(`/runs/${runId}`, "layout");
    return r;
  });
}

export async function buildStarSchema(runId: string) {
  return attemptValue(async () => {
    const r = await api<{ created: unknown[]; errors: { entity: string; error: string }[]; unsourced: string[]; dimensions: string[]; children: GoldChild[] }>(
      `/api/runs/${encodeURIComponent(runId)}/gold/star-schema`, { method: "POST" });
    revalidatePath(`/runs/${runId}`, "layout");
    revalidatePath("/gold");
    return r;
  });
}

export async function publishStarSchema(runId: string) {
  return attemptValue(() => api<{ status: string; detail?: string; pull_request?: { url?: string; number?: number } | null; tables?: string[]; conflicts?: string[] }>(
    `/api/runs/${encodeURIComponent(runId)}/gold/publish`, { method: "POST", body: JSON.stringify({}) }));
}
