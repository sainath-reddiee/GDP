"use server";

import { revalidatePath } from "next/cache";
import { api, attempt, type ActionResult } from "@/lib/api";

export async function saveJoinPlan(runId: string, body: {
  driving_table: string | null;
  joins: { left_table: string; right_table: string; keys: string[]; join_type: "LEFT" | "INNER";
    cardinality: string | null; remove: boolean }[];
}): Promise<ActionResult> {
  const result = await attempt(() =>
    api(`/api/runs/${runId}/sttm/joins`, { method: "PUT", body: JSON.stringify(body) }),
  );
  revalidatePath(`/runs/${runId}/sttm`);
  return result;
}
