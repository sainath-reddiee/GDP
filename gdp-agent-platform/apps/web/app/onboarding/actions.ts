"use server";

import { redirect } from "next/navigation";
import { api, attempt, type ActionResult } from "@/lib/api";
import type { RunState } from "@/lib/types";

export async function createRun(_: ActionResult | null, form: FormData): Promise<ActionResult> {
  let runId = "";
  const result = await attempt(async () => {
    const state = await api<RunState>("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        run_name: form.get("run_name"),
        target_model: form.get("target_model") || null,
      }),
    });
    runId = state.run_id;
  });
  if (!result.ok) return result;
  redirect(`/runs/${runId}`);
}
