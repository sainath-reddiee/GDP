"use server";

import { redirect } from "next/navigation";
import { api, attempt, type ActionResult } from "@/lib/api";
import type { RunState } from "@/lib/types";
import type { OnboardingIntent } from "./intent-types";

export async function createRun(intent: OnboardingIntent): Promise<ActionResult> {
  let runId = "";
  const primary = intent.targets[0]?.fqn || null;
  const result = await attempt(async () => {
    const state = await api<RunState>("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        run_name: intent.run_name,
        target_model: primary,
        domain_id: intent.domain_id || null,
        intent,
      }),
    });
    runId = state.run_id;
  });
  if (!result.ok) return result;
  redirect(`/runs/${runId}`);
}
