"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { AnalyzeResult, CatalogInventory, ProfileStoreRow, SourcesOverview, TableInsights } from "@/lib/types";

export async function loadOverview() {
  return attemptValue(() => api<SourcesOverview>("/api/sources/overview"));
}

export async function loadCatalogInventory(database: string, schema: string) {
  const qs = new URLSearchParams({ database, schema });
  return attemptValue(() => api<CatalogInventory>(`/api/catalog/inventory?${qs}`));
}

export async function loadCatalogProfile(database: string, schema: string, table: string) {
  const qs = new URLSearchParams({ database, schema, table });
  return attemptValue(() => api<TableInsights>(`/api/catalog/profile?${qs}`));
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

export async function analyzeTables(database: string, schema: string, tables: string[]) {
  return attemptValue(() =>
    api<AnalyzeResult>("/api/catalog/analyze", {
      method: "POST",
      body: JSON.stringify({ database, schema, tables }),
    }),
  );
}

export async function catalogModelingRun(body: {
  database: string; schema: string; tables: string[]; run_name: string | null; domain_id: string | null;
  targets: { fqn: string; target_table: string; domain_name?: string | null; target_table_id?: string | null }[];
}) {
  const result = await attemptValue(() =>
    api<{ run_id: string; stage: string; error?: string }>("/api/catalog/modeling-run", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  );
  revalidatePath("/runs");
  revalidatePath("/dashboard");
  return result;
}
