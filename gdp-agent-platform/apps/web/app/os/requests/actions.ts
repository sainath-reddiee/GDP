"use server";

import { revalidatePath } from "next/cache";
import { ApiError, api, apiForm } from "@/lib/api";
import type { Duplicate, RequestDetail } from "@/lib/os/requests";

// Onboarding request writes, each one call to the API (apps/api/app/requests_api.py), which checks who may make it.

export type RequestResult = { ok: true; data: RequestDetail } | { ok: false; error: string; duplicates?: Duplicate[] };

async function call(path: string, body: unknown): Promise<RequestResult> {
  try {
    const data = await api<RequestDetail>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
    revalidatePath("/os", "layout");
    return { ok: true, data };
  } catch (e) {
    if (e instanceof ApiError) {
      // a 409 for an open request that already asks for the same thing carries those requests
      try {
        const parsed = JSON.parse(e.message);
        if (parsed && Array.isArray(parsed.duplicates)) return { ok: false, error: parsed.message ?? "An open request already asks for this.", duplicates: parsed.duplicates };
      } catch { /* a plain message */ }
      return { ok: false, error: e.message };
    }
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export type NewRequestInput = {
  request_type: "SOURCE" | "DOMAIN" | "PRODUCT"; intent?: "NEW" | "ENHANCE"; title: string; business_outcome?: string; domain_id?: string | null;
  proposed_domain?: string | null; priority: "LOW" | "MEDIUM" | "HIGH"; needed_by?: string | null;
  sources: { source_system_id?: string; source_name?: string; note?: string }[]; allow_duplicate?: boolean;
};

export const createRequest = async (body: NewRequestInput) => call("/api/onboarding-requests", body);
export const addRequestSource = async (id: string, source: { source_system_id?: string; source_name?: string; note?: string }) =>
  call(`/api/onboarding-requests/${id}/sources`, source);
export const linkRequestSource = async (id: string, itemId: string, sourceId: string | null) =>
  call(`/api/onboarding-requests/${id}/sources/${itemId}/source`, { source_system_id: sourceId });
export const linkRequestRun = async (id: string, itemId: string, runId: string | null) =>
  call(`/api/onboarding-requests/${id}/sources/${itemId}/run`, { run_id: runId });
export const removeRequestSource = async (id: string, itemId: string) => call(`/api/onboarding-requests/${id}/sources/${itemId}/remove`, {});
export type StartRequestRunOptions = {
  run_name?: string | null;
  tables?: string[];
  targets?: { fqn: string; target_table: string; domain_name?: string | null; target_table_id?: string | null }[];
  modeling_standard?: "GDP" | "GENERIC";
  proposed_name?: string | null;
  proposed_schema?: string | null;
  autopilot?: boolean;
};

/** An Enhance source request: the Data Designer starts the enhancement run of an onboarded source (the delta flow). */
export const startEnhancementRun = async (id: string, itemId: string, options: StartRequestRunOptions = {}) =>
  call(`/api/onboarding-requests/${id}/sources/${itemId}/enhance-run`, options);

/** Start a pipeline run for a registered source on an onboarding request (New or Enhance) with selected tables and target models. */
export const startRequestRun = async (id: string, itemId: string, options: StartRequestRunOptions = {}) =>
  call(`/api/onboarding-requests/${id}/sources/${itemId}/start-run`, options);
export const linkRequestDomain = async (id: string, domainId: string) => call(`/api/onboarding-requests/${id}/domain`, { domain_id: domainId });
export const commentRequest = async (id: string, note: string) => call(`/api/onboarding-requests/${id}/comment`, { note });
export const cancelRequest = async (id: string, note: string) => call(`/api/onboarding-requests/${id}/cancel`, { note });
export const rejectRequest = async (id: string, note: string) => call(`/api/onboarding-requests/${id}/reject`, { note });
export const reopenRequest = async (id: string, note: string) => call(`/api/onboarding-requests/${id}/reopen`, { note });

/** Files of any format on a request (a data dictionary, a model export, a sample domain JSON, a PDF). Once the
 *  request's domain exists the API hands them to the domain's documents, where they are read for review. */
export type UploadedRequestFile = { file_name: string; file_id?: string; bytes?: number; duplicate?: boolean; error?: string; note?: string };
export async function uploadRequestFiles(id: string, form: FormData): Promise<{ ok: true; files: UploadedRequestFile[] } | { ok: false; error: string }> {
  try {
    const r = await apiForm<{ files: UploadedRequestFile[] }>(`/api/onboarding-requests/${encodeURIComponent(id)}/files`, form);
    revalidatePath("/os", "layout");
    return { ok: true, files: r.files };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}
export const removeRequestFile = async (id: string, fileId: string) => call(`/api/onboarding-requests/${id}/files/${fileId}/remove`, {});

export async function deleteRunsAction(runIds: string[]): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    await api("/api/runs/batch-cleanup", {
      method: "POST",
      body: JSON.stringify({ run_ids: runIds, drop_landing_tables: false, delete_workspaces: false, delete_runs: true }),
    });
    revalidatePath("/os", "layout");
    revalidatePath("/runs", "layout");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export async function deleteRequestAction(id: string, deleteRuns = true): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    await api(`/api/onboarding-requests/${encodeURIComponent(id)}/delete`, {
      method: "POST",
      body: JSON.stringify({ delete_runs: deleteRuns }),
    });
    revalidatePath("/os", "layout");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export async function startSourceRunForRequest(input: {
  requestId: string;
  itemId: string;
  sourceName: string;
  sourceType?: string | null;
  databaseName?: string | null;
  schemaName?: string | null;
  domainId?: string | null;
  domainName?: string | null;
  note?: string | null;
  runName?: string | null;
  tables?: string[];
  targets?: { fqn: string; target_table: string; domain_name?: string | null; target_table_id?: string | null }[];
  modelingStandard?: "GDP" | "GENERIC";
  proposedName?: string | null;
  proposedSchema?: string | null;
  autopilot?: boolean;
}): Promise<{ ok: true; runId: string } | { ok: false; error: string }> {
  try {
    const runName = (input.runName?.trim() || `${input.sourceName}${input.domainName ? ` · ${input.domainName}` : ""}`).slice(0, 256);
    const viaRequest = await startRequestRun(input.requestId, input.itemId, {
      run_name: runName,
      tables: input.tables ?? [],
      targets: input.targets ?? [],
      modeling_standard: input.modelingStandard ?? "GENERIC",
      proposed_name: input.proposedName ?? null,
      proposed_schema: input.proposedSchema ?? null,
      autopilot: input.autopilot ?? false,
    });
    if (viaRequest.ok) {
      const linkedItem = viaRequest.data.items.find((i) => i.item_id === input.itemId);
      const runId = linkedItem?.active_run_id ?? linkedItem?.run_id ?? "";
      revalidatePath("/os", "layout");
      return { ok: true, runId };
    }
    const created = await api<{ run_id?: string; RUN_ID?: string }>("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        run_name: runName,
        domain_id: input.domainId ?? null,
        target_model: input.targets?.[0]?.fqn ?? undefined,
        modeling_standard: input.modelingStandard ?? undefined,
        intent: {
          source: {
            origin: "snowflake",
            source_system_name: input.sourceName,
            source_type: input.sourceType ?? "SNOWFLAKE",
            database: input.databaseName ?? null,
            schema: input.schemaName ?? null,
            ...(input.tables?.length ? { tables: input.tables } : {}),
          },
          ...(input.targets?.length ? { targets: input.targets, model_existing: true, path: "map_existing" } : {}),
          domain_id: input.domainId ?? null,
          request_id: input.requestId,
          scope: input.note ?? null,
        },
      }),
    });
    const runId = created.run_id ?? created.RUN_ID;
    if (!runId) return { ok: false, error: "Run could not be created." };
    await api(`/api/onboarding-requests/${encodeURIComponent(input.requestId)}/sources/${encodeURIComponent(input.itemId)}/run`, {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    });
    revalidatePath("/os", "layout");
    return { ok: true, runId };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

