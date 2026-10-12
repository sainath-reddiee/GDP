"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type Attribute = {
  name: string; datatype: string; nullable: boolean; is_pk: boolean; source_columns: string[]; derived: boolean;
  transformation_hint?: string; rationale?: string;
};
export type Entity = {
  entity_name: string; kind: string; purpose: string; grain: string; business_keys: string[]; attributes: Attribute[];
};
export type Design = {
  decision: "REUSE_EXISTING" | "EXTEND_EXISTING" | "NEW"; decision_target: string; reasons: string[]; evidence: string[];
  target_database: string; target_schema: string; primary_entity: string; entities: Entity[];
};
export type Conventions = {
  preset: "GDP" | "COMPANY" | "CUSTOM" | "NONE"; audit_columns: string[]; surrogate_key: string; key_pattern?: string;
  naming_case: string; table_prefix: string; scd_type: number | null; soft_delete: boolean;
};
export type Issue = { path: string; severity: "ERROR" | "WARN"; message: string };
export type DesignVersion = {
  design_id: string; version: number; status: string; origin: string; design: Design; conventions: Conventions;
  issues: Issue[]; instructions: string | null; model: string | null; note: string | null; created_by: string;
  created_at: string | null; approved_by: string | null;
};
export type RunProposal = {
  change_id: string; model_id: string; status: "PROPOSED" | "MERGED" | "STALE"; merged_into_version: number | null;
  created_at: string | null;
};
export type WorkbenchState = {
  enforced: boolean; model_id: string | null; model_version: number | null; model_entity: string | null;
  proposal: RunProposal | null;
};
export type ModelPayload = {
  run_id: string; target_model: string | null; standard: string | null; versions: DesignVersion[];
  current: DesignVersion | null; approved: DesignVersion | null; workbench?: WorkbenchState;
};
export type ProposeResult = {
  model_id: string; pinned: boolean; status?: "PROPOSED" | "MERGED"; change_id?: string; merged_into?: number;
  version?: number; entity?: string; target_model?: string | null;
  counts?: Record<string, number>; breaking?: boolean;
  pin?: { pinned: boolean; mapping_reset?: boolean; detail?: string; target_model?: string };
};
export type DiffChange = { entity: string; change: string; column?: string; columns?: string[]; fields?: string[]; from?: unknown; to?: unknown };

/** A table of the design as kept in the Model Store (table models, V044). */
export type StoredTable = { table_model_id: string | null; table_name: string | null; status: "CREATED" | "VERSIONED" | "UNCHANGED" | "FAILED"; version: number | null; error?: string };

export async function generateDesign(runId: string, body: {
  preset: Conventions["preset"]; custom?: Partial<Conventions>; instructions?: string; base_version?: number;
  target_database?: string; target_schema?: string;
}) {
  const r = await attemptValue(() => api<{ version: number; issues: Issue[]; grounding: Record<string, unknown>; table_models?: StoredTable[] }>(
    `/api/runs/${runId}/model/design`, { method: "POST", body: JSON.stringify(body) }));
  if (r.ok) revalidatePath(`/runs/${runId}/mapping`);
  return r;
}

export async function saveDesign(runId: string, design: Design, conventions: Conventions, note?: string) {
  const r = await attemptValue(() => api<{ version: number; issues: Issue[]; table_models?: StoredTable[] }>(`/api/runs/${runId}/model`, {
    method: "PUT", body: JSON.stringify({ design, conventions, note }),
  }));
  if (r.ok) revalidatePath(`/runs/${runId}/mapping`);
  return r;
}

export async function checkDesign(runId: string, design: Design, conventions: Conventions) {
  return attemptValue(() => api<{ issues: Issue[]; design: Design; conventions: Conventions }>(
    `/api/runs/${runId}/model/validate`, { method: "POST", body: JSON.stringify({ design, conventions }) }));
}

export async function approveDesign(runId: string, version: number) {
  const r = await attemptValue(() => api<{ target_model: string; registered: { entity: string; fqn: string }[]; mapping_reset: boolean }>(
    `/api/runs/${runId}/model/${version}/approve`, { method: "POST" }));
  if (r.ok) revalidatePath(`/runs/${runId}`, "layout");
  return r;
}

/** Propose a run design to the domain's governed model (the Model Workbench). */
export async function proposeDesign(runId: string, version: number) {
  const r = await attemptValue(() => api<ProposeResult>(`/api/runs/${runId}/model/${version}/propose`, { method: "POST" }));
  if (r.ok) revalidatePath(`/runs/${runId}`, "layout");
  return r;
}

export async function diffDesigns(runId: string, base: number, compare: number) {
  return attemptValue(() => api<{ changes: DiffChange[] }>(`/api/runs/${runId}/model/diff?base=${base}&compare=${compare}`));
}
