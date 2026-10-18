"use server";

import { revalidatePath } from "next/cache";
import { api, ApiError, attemptValue } from "@/lib/api";

// Governed removal (apps/api/app/removal_api.py): removing or restoring a domain table or a source always waits for
// another person's approval, so a request normally comes back "pending" with who must approve it.

export type RemovalKind = "target" | "source" | "knowledge";
export type RemovalResult = { ok: true; pending: boolean; message: string } | { ok: false; error: string };
export type RegisteredTable = { target_table_id: string; domain_id: string; domain_name: string; target_database: string;
  target_schema: string; target_table: string; table_type: string | null; columns: number };
export type Removed = {
  targets: { target_table_id: string; target_table: string; target_database: string; target_schema: string; domain_name: string | null;
    retired_by: string | null; retired_at: string | null; retire_reason: string | null }[];
  sources: { source_system_id: string; source_system_name: string; source_type: string; retired_by: string | null;
    retired_at: string | null; retire_reason: string | null }[];
};

const PATH: Record<RemovalKind, string> = { target: "targets", source: "sources", knowledge: "knowledge" };

async function call(kind: RemovalKind, id: string, action: "retire" | "restore", reason: string): Promise<RemovalResult> {
  try {
    await api(`/api/${PATH[kind]}/${encodeURIComponent(id)}/${action}`, {
      method: "POST", body: JSON.stringify(reason.trim() ? { reason: reason.trim() } : {}),
    });
    revalidatePath("/domains", "layout");
    revalidatePath("/dashboard/sources");
    return { ok: true, pending: false, message: action === "retire" ? "Removed" : "Restored" };
  } catch (e) {
    if (e instanceof ApiError && e.status === 202) return { ok: true, pending: true, message: e.message };
    if (e instanceof ApiError) return { ok: false, error: e.message };
    throw e;
  }
}

export async function requestRemoval(kind: RemovalKind, id: string, reason: string) {
  return call(kind, id, "retire", reason);
}

export async function requestRestore(kind: RemovalKind, id: string) {
  return call(kind, id, "restore", "");
}

export async function domainTables(domainId: string) {
  return attemptValue(() => api<{ targets: RegisteredTable[] }>(`/api/targets?domain_id=${encodeURIComponent(domainId)}`));
}

export async function removedItems() {
  return attemptValue(() => api<Removed>("/api/removals"));
}

// Adding a table to a domain: the same registration the modeling flow uses, pinned to this domain.
export type CatalogTable = { table_name: string; table_type: string; row_count: number | null };

export async function catalogDatabases() {
  return attemptValue(() => api<{ databases: { database_name: string }[] }>("/api/sources/databases"));
}

export async function catalogSchemas(database: string) {
  return attemptValue(() => api<{ schemas: { schema_name: string }[] }>(`/api/catalog/schemas?database=${encodeURIComponent(database)}`));
}

export async function catalogTables(database: string, schema: string) {
  return attemptValue(() => api<{ tables: CatalogTable[] }>(
    `/api/catalog/tables?database=${encodeURIComponent(database)}&schema=${encodeURIComponent(schema)}`));
}

export async function addTableToDomain(domainId: string, database: string, schema: string, table: string): Promise<RemovalResult> {
  try {
    await api("/api/targets/register", { method: "POST", body: JSON.stringify({ database, schema, table, domain_id: domainId }) });
    revalidatePath("/domains", "layout");
    return { ok: true, pending: false, message: "Added" };
  } catch (e) {
    if (e instanceof ApiError && e.status === 202) return { ok: true, pending: true, message: e.message };
    if (e instanceof ApiError) return { ok: false, error: e.message };
    throw e;
  }
}
