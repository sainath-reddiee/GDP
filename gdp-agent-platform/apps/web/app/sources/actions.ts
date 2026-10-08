"use server";

import { revalidatePath } from "next/cache";
import { api, apiForm, attemptValue } from "@/lib/api";
import type {
  AnalyzeResult, CatalogInventory, Connector, ExternalFile, IngestJob, LandResult, OracleCatalog, OracleColumn, OracleOverview, OraclePreview, OracleProfileDoc, OracleSchedule, OracleTest,
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

export type SetupResult = { ready: boolean; log: { sql: string; ok: boolean; error?: string }[]; detail?: string;
                            failed_step?: string; secret?: string; integration?: string };
export type IntegrationCheck = { name: string; enabled: boolean; allows_host: boolean; allows_secret: boolean | null;
                                 network_values: string[]; secrets: string[]; usable: boolean };

export async function oracleSecrets() {
  return attemptValue(() => api<{ secrets: { name: string; comment: string | null; owner: string | null; created_on: string }[];
                                 error?: string }>("/api/oracle/secrets"));
}

export async function oracleSecretUser(name: string) {
  return attemptValue(() => api<{ name: string; username: string | null }>(
    `/api/oracle/secrets/describe?name=${encodeURIComponent(name)}`));
}

export async function oracleIntegrations() {
  return attemptValue(() => api<{ integrations: { name: string; enabled: boolean; comment: string | null }[]; error?: string }>(
    "/api/oracle/integrations"));
}

export async function oracleCheckIntegration(body: { name: string; host: string; port: number; secret?: string }) {
  return attemptValue(() => api<IntegrationCheck>("/api/oracle/integrations/check", { method: "POST", body: JSON.stringify(body) }));
}

export async function oracleParse(text: string) {
  return attemptValue(() => api<Record<string, string | number | boolean>>("/api/oracle/parse",
    { method: "POST", body: JSON.stringify({ text }) }));
}

export async function oracleEnvCheck(name: string) {
  return attemptValue(() => api<{ name: string; present: boolean }>(`/api/oracle/env-check?name=${encodeURIComponent(name)}`));
}

export async function oracleOverview(sourceId: string) {
  return attemptValue(() => api<OracleOverview>(`/api/sources/${sourceId}/oracle`));
}

export async function oracleSetup(sourceId: string, body: {
  secret_mode: "new" | "existing"; password?: string; secret?: string;
  integration_mode: "new" | "existing"; external_access_integration?: string;
}) {
  return attemptValue(() => api<SetupResult>(`/api/sources/${sourceId}/oracle/setup`,
    { method: "POST", body: JSON.stringify(body) }));
}

export async function oraclePassword(sourceId: string, password: string) {
  return attemptValue(() => api<{ updated: string }>(`/api/sources/${sourceId}/oracle/password`,
    { method: "POST", body: JSON.stringify({ password }) }));
}

export async function oracleConnection(sourceId: string, config: Record<string, string>) {
  return attemptValue(() => api<{ connection: Record<string, string>; note: string | null }>(
    `/api/sources/${sourceId}/oracle/connection`, { method: "PUT", body: JSON.stringify({ config }) }));
}

export async function oracleTest(sourceId: string) {
  return attemptValue(() => api<OracleTest>(`/api/sources/${sourceId}/oracle/test`, { method: "POST" }));
}

export async function oracleRemove(sourceId: string, dropLanded: boolean) {
  const result = await attemptValue(() => api<{ removed: string; log: { sql: string; ok: boolean; error?: string }[] }>(
    `/api/sources/${sourceId}/oracle?drop_landed=${dropLanded}`, { method: "DELETE" }));
  if (result.ok) revalidatePath("/sources");
  return result;
}

export async function oracleCatalog(sourceId: string) {
  return attemptValue(() => api<OracleCatalog>(`/api/sources/${sourceId}/oracle/catalog`, { method: "POST" }));
}

export async function oracleColumns(sourceId: string, tables: string[]) {
  return attemptValue(() => api<{ columns: Record<string, OracleColumn[]>; missing: string[] }>(
    `/api/sources/${sourceId}/oracle/columns`, { method: "POST", body: JSON.stringify({ tables }) }));
}

export async function oraclePreview(sourceId: string, table: string, limit = 20) {
  return attemptValue(() => api<OraclePreview>(`/api/sources/${sourceId}/oracle/preview`,
    { method: "POST", body: JSON.stringify({ table, limit }) }));
}

export async function oracleProfileDoc(sourceId: string, table: string) {
  return attemptValue(() => api<OracleProfileDoc>(`/api/sources/${sourceId}/oracle/profile?table=${encodeURIComponent(table)}`));
}

export async function oracleProfile(sourceId: string, tables: string[]) {
  return attemptValue(() => api<{ job_id: string }>(`/api/sources/${sourceId}/oracle/profile`, {
    method: "POST", body: JSON.stringify({ tables }),
  }));
}

export type IngestOptions = {
  tables: string[]; mode: "replace" | "append" | "merge"; storage: "MANAGED" | "ICEBERG";
  watermark_columns: Record<string, string>; merge_keys: Record<string, string[]>; lookback_minutes: number;
  profile_after_landing: boolean;
};

export async function oracleIngest(sourceId: string, body: IngestOptions) {
  const result = await attemptValue(() => api<{ job_id: string }>(`/api/sources/${sourceId}/oracle/ingest`, {
    method: "POST", body: JSON.stringify(body),
  }));
  if (result.ok) revalidatePath("/sources");
  return result;
}

export async function ingestJob(jobId: string) {
  return attemptValue(() => api<IngestJob>(`/api/ingest-jobs/${jobId}`));
}

export async function cancelIngestJob(jobId: string) {
  return attemptValue(() => api<{ job_id: string; status: string }>(`/api/ingest-jobs/${jobId}/cancel`, { method: "POST" }));
}

export async function oracleSchedule(sourceId: string, body: {
  tables: string[]; mode: "append" | "merge" | "replace"; cron: string; watermark_columns: Record<string, string>;
  merge_keys: Record<string, string[]>; lookback_minutes: number;
}) {
  return attemptValue(() => api<{ schedule: OracleSchedule }>(`/api/sources/${sourceId}/oracle/schedule`,
    { method: "PUT", body: JSON.stringify(body) }));
}

export async function oracleUnschedule(sourceId: string) {
  return attemptValue(() => api<{ schedule: null }>(`/api/sources/${sourceId}/oracle/schedule`, { method: "DELETE" }));
}
