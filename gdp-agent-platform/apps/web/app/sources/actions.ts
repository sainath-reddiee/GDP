"use server";

import { revalidatePath } from "next/cache";
import { api, apiForm, attemptValue } from "@/lib/api";
import type {
  AnalyzeResult, CatalogInventory, Connector, ExternalFile, IngestJob, LandResult, OracleCatalog, OracleColumn, OracleProfileDoc, OracleTest,
  ProfileStoreRow, SourcesOverview, TableInsights,
} from "@/lib/types";

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
  modeling_standard: "GDP" | "GENERIC";
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

export async function loadConnectors() {
  return attemptValue(() => api<{ connectors: Connector[] }>("/api/connectors"));
}

export async function registerExternalSource(body: {
  source_system_name: string; connector: string; config: Record<string, string>;
}) {
  const result = await attemptValue(() =>
    api<{ source_system_id: string; source_system_name: string; landing: { database: string; schema: string };
      landable: boolean; guidance: string | null }>("/api/sources/external", { method: "POST", body: JSON.stringify(body) }),
  );
  revalidatePath("/sources");
  return result;
}

export async function listExternalFiles(sourceId: string) {
  return attemptValue(() => api<{ files: ExternalFile[]; landable: boolean }>(`/api/sources/${sourceId}/files`));
}

export async function uploadExternalFiles(sourceId: string, form: FormData) {
  return attemptValue(() => apiForm<{ uploaded: { file: string; bytes: number }[] }>(`/api/sources/${sourceId}/upload`, form));
}

export async function landExternalFiles(sourceId: string, files: string[], table?: string) {
  const result = await attemptValue(() =>
    api<LandResult>(`/api/sources/${sourceId}/land`, {
      method: "POST",
      body: JSON.stringify({ files, table: table || null }),
    }),
  );
  revalidatePath("/sources");
  return result;
}

// ---------------------------------------------------------------- Oracle

export async function oracleSetup(sourceId: string, password: string, externalAccessIntegration?: string) {
  return attemptValue(() => api<{ ready: boolean; log: { sql: string; ok: boolean; error?: string }[]; detail?: string }>(
    `/api/sources/${sourceId}/oracle/setup`,
    { method: "POST", body: JSON.stringify({ password, external_access_integration: externalAccessIntegration || null }) },
  ));
}

export async function oracleTest(sourceId: string) {
  return attemptValue(() => api<OracleTest>(`/api/sources/${sourceId}/oracle/test`, { method: "POST" }));
}

export async function oracleCatalog(sourceId: string) {
  return attemptValue(() => api<OracleCatalog>(`/api/sources/${sourceId}/oracle/catalog`, { method: "POST" }));
}

export async function oracleColumns(sourceId: string, tables: string[]) {
  return attemptValue(() => api<{ columns: Record<string, OracleColumn[]> }>(`/api/sources/${sourceId}/oracle/columns`, {
    method: "POST", body: JSON.stringify({ tables }),
  }));
}

export async function oracleProfileDoc(sourceId: string, table: string) {
  return attemptValue(() => api<OracleProfileDoc>(`/api/sources/${sourceId}/oracle/profile?table=${encodeURIComponent(table)}`));
}

export async function oracleProfile(sourceId: string, tables: string[]) {
  return attemptValue(() => api<{ job_id: string }>(`/api/sources/${sourceId}/oracle/profile`, {
    method: "POST", body: JSON.stringify({ tables }),
  }));
}

export async function oracleIngest(sourceId: string, body: {
  tables: string[]; mode: "replace" | "append"; storage: "MANAGED" | "ICEBERG"; watermark_columns: Record<string, string>;
  profile_after_landing: boolean;
}) {
  const result = await attemptValue(() => api<{ job_id: string }>(`/api/sources/${sourceId}/oracle/ingest`, {
    method: "POST", body: JSON.stringify(body),
  }));
  revalidatePath("/sources");
  return result;
}

export async function ingestJob(jobId: string) {
  return attemptValue(() => api<IngestJob>(`/api/ingest-jobs/${jobId}`));
}
