"use server";

import { revalidatePath } from "next/cache";
import { ApiError, api } from "@/lib/api";
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
/** An Enhance source request: the Data Designer starts the enhancement run of an onboarded source (the delta flow). */
export const startEnhancementRun = async (id: string, itemId: string) => call(`/api/onboarding-requests/${id}/sources/${itemId}/enhance-run`, {});
export const linkRequestDomain = async (id: string, domainId: string) => call(`/api/onboarding-requests/${id}/domain`, { domain_id: domainId });
export const commentRequest = async (id: string, note: string) => call(`/api/onboarding-requests/${id}/comment`, { note });
export const cancelRequest = async (id: string, note: string) => call(`/api/onboarding-requests/${id}/cancel`, { note });
export const rejectRequest = async (id: string, note: string) => call(`/api/onboarding-requests/${id}/reject`, { note });
export const reopenRequest = async (id: string, note: string) => call(`/api/onboarding-requests/${id}/reopen`, { note });
