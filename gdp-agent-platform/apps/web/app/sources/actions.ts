"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { SourceInventory, SourcesOverview, TableProfileDoc } from "@/lib/types";

export async function loadOverview() {
  return attemptValue(() => api<SourcesOverview>("/api/sources/overview"));
}

export async function loadInventory(sourceId: string) {
  return attemptValue(() => api<SourceInventory>(`/api/sources/${sourceId}/inventory`));
}

export async function loadTableProfile(sourceId: string, table: string) {
  return attemptValue(() =>
    api<TableProfileDoc>(`/api/sources/${sourceId}/profiles/${encodeURIComponent(table)}`),
  );
}

export async function profileTables(sourceId: string, tables: string[], forceRefresh: boolean) {
  return attemptValue(() =>
    api<{ status: string; job_id: string; tables: string[] }>(`/api/sources/${sourceId}/profile-tables`, {
      method: "POST",
      body: JSON.stringify({ tables, force_refresh: forceRefresh }),
    }),
  );
}

export async function sendToModeling(sourceId: string, tables: string[], runName: string) {
  const result = await attemptValue(() =>
    api<{ run_id: string; stage: string; error?: string; cache_hits?: number; computed?: number }>(
      `/api/sources/${sourceId}/modeling-run`,
      { method: "POST", body: JSON.stringify({ tables, run_name: runName || null }) },
    ),
  );
  revalidatePath("/runs");
  revalidatePath("/dashboard");
  return result;
}

export async function registerConnection(body: {
  database: string; schema: string; source_type: string; source_system_name?: string;
}) {
  const result = await attemptValue(() =>
    api<{ source_system_id: string; created: boolean; objects_discovered: number }>("/api/sources", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  );
  revalidatePath("/sources");
  return result;
}
