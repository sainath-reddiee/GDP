"use server";

import { revalidatePath } from "next/cache";
import { api, attempt, type ActionResult } from "@/lib/api";

export async function transitionRun(runId: string, toState: string): Promise<ActionResult> {
  const result = await attempt(() =>
    api(`/api/runs/${runId}/transition`, {
      method: "POST",
      body: JSON.stringify({ to_state: toState, reason: "console" }),
    }),
  );
  revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export async function reviewRun(runId: string, form: FormData): Promise<ActionResult> {
  const result = await attempt(() =>
    api(`/api/runs/${runId}/review`, {
      method: "POST",
      body: JSON.stringify({
        to_state: form.get("to_state"),
        decision: form.get("decision"),
        business_justification: form.get("justification") || null,
        comments: form.get("comments") || null,
      }),
    }),
  );
  revalidatePath(`/runs/${runId}`, "layout");
  return result;
}
