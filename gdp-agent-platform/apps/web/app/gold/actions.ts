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
};

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
    const r = await api<{ model_id: string; version: number; notes: string[]; tables: { entity: string; table_type: string; conformed: boolean }[] }>(
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
