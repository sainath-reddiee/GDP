"use server";

import { api, attemptValue } from "@/lib/api";

export type ResultRow = Record<string, string | number | null>;

export async function executionResults(kind: "SODA" | "QA", id: string) {
  return attemptValue(() => api<{ results: ResultRow[] }>(`/api/hub/quality/executions/${kind}/${encodeURIComponent(id)}`));
}

export async function gxSuite(runId: string) {
  return attemptValue(() => api<{ run_id: string; target_table: string; gx_suite: unknown }>(`/api/hub/quality/gx/${encodeURIComponent(runId)}`));
}
