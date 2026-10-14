"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type GoldInput = { database: string; schema: string; table: string; layer: "SILVER_PRE_MDM" | "SILVER_POST_MDM" };
export type GoldLine = { gold_table: string; gold_column: string; kind: "KEY" | "COLUMN" | "RESOLVE" | "DERIVED" | "AUDIT";
  source_layer: string | null; source_table: string | null; source_column: string | null; expression: string | null; note: string | null };
export type GoldAttr = { name: string; datatype: string; nullable: boolean; is_pk: boolean; description?: string };
export type GoldEntity = { entity_name: string; table_type: string; conformed_from?: string | null; database?: string; schema?: string;
  grain?: string; business_keys: string[]; description?: string; attributes: GoldAttr[] };
export type GoldSpec = { domain: string; layer: "GOLD"; physical_defaults: { database: string; schema: string; platform: string };
  design: { entities: GoldEntity[] };
  relationships: { from_entity: string; from_column: string; to_entity: string; to_column: string; cardinality: string }[] };
export type GoldBuild = {
  build_id: string; domain_id: string; domain_name: string; name: string; step: "INPUTS" | "MODELING" | "REVIEW" | "CODE" | "DONE";
  status: "DRAFT" | "PROPOSED" | "IN_REVIEW" | "APPROVED" | "GENERATED" | "CANCELLED"; inputs: GoldInput[] | null;
  proposal: { spec: GoldSpec; notes: string[] } | null; model_id: string | null; model_version: number | null;
  model: { model_id: string; approved_version: number | null; open_version: number | null } | null;
  lines: GoldLine[]; role: "MASTERED" | "CONSUMER" | null; consumes: { domain: string; silver_column: string; gold_column: string }[];
  created_by: string; created_at: string; updated_at: string | null;
  generation: { version: number; model_version: number; paths: string[];
    report: { engine: string; tables: string[]; conformed: string[]; conventions: unknown; assumptions: string[]; notes?: string[] } } | null;
};
export type GoldBuildRow = { build_id: string; domain_id: string; domain_name: string; name: string; step: string; status: string;
  inputs: number; tables: number; model_id: string | null; created_by: string; created_at: string; updated_at: string | null };
export type GoldCandidate = GoldInput & { role: string; exists?: boolean; row_count?: number | null; last_altered?: string | null; error?: string };

const B = (id: string) => `/api/gold/builds/${encodeURIComponent(id)}`;

export async function createGoldBuild(domainId: string, name?: string) {
  const r = await attemptValue(() => api<GoldBuild>("/api/gold/builds", { method: "POST", body: JSON.stringify({ domain_id: domainId, name }) }));
  revalidatePath("/gold");
  return r;
}
export async function goldCandidates(buildId: string) {
  return attemptValue(() => api<{ role: string | null; tables: GoldCandidate[] }>(`${B(buildId)}/candidates`));
}
export async function setGoldInputs(buildId: string, tables: GoldInput[]) {
  const r = await attemptValue(() => api<GoldBuild>(`${B(buildId)}/inputs`, { method: "PUT", body: JSON.stringify({ tables }) }));
  revalidatePath(`/gold/${buildId}`);
  return r;
}
export async function proposeGold(buildId: string) {
  const r = await attemptValue(() => api<GoldBuild>(`${B(buildId)}/propose`, { method: "POST" }));
  revalidatePath(`/gold/${buildId}`);
  return r;
}
export async function goldToWorkbench(buildId: string) {
  const r = await attemptValue(() => api<GoldBuild>(`${B(buildId)}/workbench`, { method: "POST" }));
  revalidatePath(`/gold/${buildId}`);
  return r;
}
export async function cancelGold(buildId: string) {
  const r = await attemptValue(() => api<GoldBuild>(`${B(buildId)}/cancel`, { method: "POST" }));
  revalidatePath("/gold");
  return r;
}
export async function generateGold(buildId: string) {
  const r = await attemptValue(() => api<GoldBuild>(`${B(buildId)}/generate`, { method: "POST" }));
  revalidatePath(`/gold/${buildId}`);
  return r;
}
export async function goldFiles(buildId: string) {
  return attemptValue(() => api<{ version: number; files: Record<string, string> }>(`${B(buildId)}/generation`));
}
