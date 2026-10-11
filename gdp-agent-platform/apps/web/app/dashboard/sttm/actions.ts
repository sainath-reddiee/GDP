"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { SttmDiff } from "@/lib/hub-types";

export type SttmEdit = { target_column: string; transformation?: string | null; mapping_type?: string; business_rule?: string | null; business_definition?: string | null };

export async function saveEdits(catalogId: string, baseVersion: number, edits: SttmEdit[], note?: string) {
  const r = await attemptValue(() => api<{ catalog_id: string; version: number; edited: string[] }>(
    `/api/hub/sttm/${encodeURIComponent(catalogId)}/edit`,
    { method: "POST", body: JSON.stringify({ base_version: baseVersion, edits, note: note || undefined }) }));
  if (r.ok) revalidatePath(`/dashboard/sttm/${catalogId}`);
  return r;
}

export async function compare(catalogId: string, fromVersion: number, toVersion: number) {
  return attemptValue(() => api<SttmDiff>("/api/hub/sttm/diff", {
    method: "POST", body: JSON.stringify({ catalog_id: catalogId, from_version: fromVersion, to_version: toVersion }),
  }));
}
