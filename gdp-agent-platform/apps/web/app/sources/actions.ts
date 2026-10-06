"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { CatalogInventory, ProfileStoreRow, SourcesOverview, TableProfileDoc } from "@/lib/types";

export async function loadOverview() {
  return attemptValue(() => api<SourcesOverview>("/api/sources/overview"));
}

export async function loadCatalogInventory(database: string, schema: string) {
  const qs = new URLSearchParams({ database, schema });
  return attemptValue(() => api<CatalogInventory>(`/api/catalog/inventory?${qs}`));
}

export async function loadCatalogProfile(database: string, schema: string, table: string) {
  const qs = new URLSearchParams({ database, schema, table });
  return attemptValue(() => api<TableProfileDoc>(`/api/catalog/profile?${qs}`));
}

export async function loadProfileStore() {
  return attemptValue(() => api<{ profiles: ProfileStoreRow[] }>("/api/profiles/store"));
}

export async function profileCatalogTables(database: string, schema: string, tables: string[], forceRefresh: boolean) {
  return attemptValue(() =>
    api<{ status: string; job_id: string; tables: string[] }>("/api/catalog/profile-tables", {
      method: "POST",
      body: JSON.stringify({ database, schema, tables, force_refresh: forceRefresh }),
    }),
  );
}

export async function catalogModelingRun(database: string, schema: string, tables: string[], runName: string) {
  const result = await attemptValue(() =>
    api<{ run_id: string; stage: string; error?: string }>("/api/catalog/modeling-run", {
      method: "POST",
      body: JSON.stringify({ database, schema, tables, run_name: runName || null }),
    }),
  );
  revalidatePath("/runs");
  revalidatePath("/dashboard");
  return result;
}
