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
  return approveStage(
    runId,
    String(form.get("to_state") || ""),
    String(form.get("justification") || ""),
    String(form.get("decision") || "APPROVE"),
    String(form.get("comments") || "") || null,
  );
}

export async function approveStage(
  runId: string,
  toState: string,
  justification: string,
  decision = "APPROVE",
  comments: string | null = null,
): Promise<ActionResult> {
  const result = await attempt(() =>
    api(`/api/runs/${runId}/review`, {
      method: "POST",
      body: JSON.stringify({
        to_state: toState,
        decision,
        business_justification: justification || null,
        comments,
      }),
    }),
  );
  revalidatePath(`/runs/${runId}`, "layout");
  return result;
}
