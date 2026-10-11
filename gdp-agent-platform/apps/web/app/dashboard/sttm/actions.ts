"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import { storeAction } from "@/lib/store-action";
import type { SttmDiff, SttmFilter, SttmLine } from "@/lib/hub-types";

export type SttmEdit = { target_column: string } & Partial<Omit<SttmLine, "target_column" | "join_logic">>;
export type SttmChange = {
  base_version: number; note?: string;
  edits?: SttmEdit[]; add_lines?: SttmEdit[]; remove_lines?: string[];
  design?: { grain?: string | null; business_keys?: string[]; dedup?: string | null; scd_type?: string; incremental_strategy?: string; filters?: SttmFilter[] };
  joins?: { driving_table: string | null; joins: { left_table: string; right_table: string; keys: string[]; join_type: "LEFT" | "INNER"; cardinality: string | null; remove: boolean }[] };
};

/** One store edit; governance queues it for the STTM approver (202, "sent for approval"). */
export async function submitChange(catalogId: string, change: SttmChange) {
  const r = await storeAction(() => api<{ version: number; status: string; summary: string; run_updated: boolean }>(
    `/api/hub/sttm/${encodeURIComponent(catalogId)}/edit`, { method: "POST", body: JSON.stringify(change) }));
  revalidatePath(`/dashboard/sttm/${catalogId}`);
  return r;
}

export async function refineLine(catalogId: string, body: { target_column: string; prompt: string; source_table?: string | null;
  source_column?: string | null; source_datatype?: string | null; target_datatype?: string | null; current_transformation?: string | null;
  business_definition?: string | null }) {
  return storeAction(() => api<{ transformation: string; rationale: string | null; dbt_notes: string | null }>(
    `/api/hub/sttm/${encodeURIComponent(catalogId)}/refine`, { method: "POST", body: JSON.stringify(body) }));
}

export async function compare(catalogId: string, fromVersion: number, toVersion: number) {
  return attemptValue(() => api<SttmDiff>("/api/hub/sttm/diff", {
    method: "POST", body: JSON.stringify({ catalog_id: catalogId, from_version: fromVersion, to_version: toVersion }),
  }));
}

export async function publishSttm(catalogId: string, version: number, repoId: string) {
  return attemptValue(() => api<{ status: string; detail?: string; head_branch?: string; files?: string[] }>(
    `/api/hub/sttm/${encodeURIComponent(catalogId)}/publish`, { method: "POST", body: JSON.stringify({ version, repo_id: repoId }) }));
}
