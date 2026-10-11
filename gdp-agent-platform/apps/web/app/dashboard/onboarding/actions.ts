"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { OnboardingCheck } from "@/lib/hub-types";

export async function checkOnboarding(pack: Record<string, unknown> | null, contractName: string | null, contractText: string | null) {
  return attemptValue(() => api<OnboardingCheck>("/api/hub/onboarding/validate", {
    method: "POST",
    body: JSON.stringify({ pack: pack ?? undefined, contract_name: contractName ?? undefined, contract_text: contractText ?? undefined }),
  }));
}

/** Registers the pack exactly as the deploy seeds repository packs (the existing /api/domains/import). */
export async function importPack(pack: Record<string, unknown>) {
  const r = await attemptValue(() => api<{ domain_id: string; domain_name: string; targets: number; columns: number; knowledge: number }>(
    "/api/domains/import", { method: "POST", body: JSON.stringify({ pack }) }));
  if (r.ok) { revalidatePath("/domains"); revalidatePath("/dashboard/onboarding"); }
  return r;
}
