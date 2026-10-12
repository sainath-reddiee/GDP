"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { AnalyzeResult, CatalogInventory, ProfileStoreRow, SourcesOverview, TableInsights } from "@/lib/types";
import { sfIdent } from "./identifiers";

export async function loadOverview() {
  return attemptValue(() => api<SourcesOverview>("/api/sources/overview"));
}

export async function loadCatalogInventory(database: string, schema: string) {
  const qs = new URLSearchParams({ database: sfIdent(database), schema: sfIdent(schema) });
  return attemptValue(() => api<CatalogInventory>(`/api/catalog/inventory?${qs}`));
}

export async function loadCatalogProfile(database: string, schema: string, table: string) {
  const qs = new URLSearchParams({ database: sfIdent(database), schema: sfIdent(schema), table });
  return attemptValue(() => api<TableInsights>(`/api/catalog/profile?${qs}`));
}

export async function loadProfileStore() {
  return attemptValue(() => api<{ profiles: ProfileStoreRow[] }>("/api/profiles/store"));
}

export async function profileCatalogTables(database: string, schema: string, tables: string[], forceRefresh: boolean) {
  return attemptValue(() =>
    api<{ status: string; job_id: string; tables: string[] }>("/api/catalog/profile-tables", {
      method: "POST",
      body: JSON.stringify({ database: sfIdent(database), schema: sfIdent(schema), tables, force_refresh: forceRefresh }),
    }),
  );
}

export async function analyzeTables(database: string, schema: string, tables: string[]) {
  return attemptValue(() =>
    api<AnalyzeResult>("/api/catalog/analyze", {
      method: "POST",
      body: JSON.stringify({ database: sfIdent(database), schema: sfIdent(schema), tables }),
    }),
  );
}

export async function catalogModelingRun(body: {
  database: string; schema: string; tables: string[]; run_name: string | null; domain_id: string | null;
  modeling_standard: "GDP" | "GENERIC";
  targets: { fqn: string; target_table: string; domain_name?: string | null; target_table_id?: string | null }[];
  proposed_name?: string | null; proposed_schema?: string | null;
}) {
  const result = await attemptValue(() =>
    api<{ run_id: string; stage: string; error?: string }>("/api/catalog/modeling-run", {
      method: "POST",
      body: JSON.stringify({ ...body, database: sfIdent(body.database), schema: sfIdent(body.schema) }),
    }),
  );
  revalidatePath("/runs");
  revalidatePath("/dashboard");
  return result;
}
