"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { ArchiveResult, CleanupResult } from "@/lib/types";

function refreshLists(runIds: string[] = []) {
  revalidatePath("/runs");
  revalidatePath("/dashboard");
  for (const runId of runIds) revalidatePath(`/runs/${runId}`, "layout");
}

export async function setRunsArchived(runIds: string[], archived: boolean) {
  const result = await attemptValue(() =>
    api<ArchiveResult>("/api/runs/batch-archive", {
      method: "POST",
      body: JSON.stringify({ run_ids: runIds, archived }),
    }),
  );
  refreshLists(runIds);
  return result;
}

export async function cleanupRuns(
  runIds: string[],
  options: { drop_landing_tables: boolean; delete_workspaces: boolean },
) {
  const result = await attemptValue(() =>
    api<CleanupResult>("/api/runs/batch-cleanup", {
      method: "POST",
      body: JSON.stringify({ run_ids: runIds, ...options, delete_runs: true }),
    }),
  );
  refreshLists(runIds);
  return result;
}
