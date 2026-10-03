"use server";

import { api } from "@/lib/api";
import type { SchemaRow, TableRow } from "./catalog-types";

export async function loadSchemas(database: string) {
  return api<{ schemas: SchemaRow[] }>(`/api/catalog/schemas?database=${encodeURIComponent(database)}`);
}

export async function loadTables(database: string, schema: string) {
  return api<{ tables: TableRow[] }>(
    `/api/catalog/tables?database=${encodeURIComponent(database)}&schema=${encodeURIComponent(schema)}`,
  );
}

export async function registerTarget(database: string, schema: string, table: string) {
  return api<{ target_model: string }>("/api/targets/register", {
    method: "POST",
    body: JSON.stringify({ database, schema, table }),
  });
}
