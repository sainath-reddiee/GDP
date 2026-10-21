"use server";

import { api, attemptValue } from "@/lib/api";
import type { RegisteredTable } from "@/app/removal-actions";

// Finding a table to work on: the tables the platform already knows, and any table the signed-in role can see.

export async function registeredTables() {
  return attemptValue(() => api<{ targets: RegisteredTable[] }>("/api/targets"));
}

export async function tableColumns(database: string, schema: string, table: string) {
  const qs = new URLSearchParams({ database, schema, table });
  return attemptValue(() => api<{ columns: { column_name: string; data_type: string }[] }>(`/api/catalog/columns?${qs}`));
}
