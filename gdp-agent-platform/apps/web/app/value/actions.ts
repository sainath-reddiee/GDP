"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

/** Save the effort baselines (a new version of PLATFORM_CONFIG VALUE_BASELINES; CONFIG.EDIT), or reset to defaults. */
export async function saveBaselines(value: Record<string, unknown>, reset = false) {
  const result = await attemptValue(() =>
    api<unknown>("/api/config/platform", { method: "PUT", body: JSON.stringify({ key: "VALUE_BASELINES", value, reset }) }));
  if (result.ok) revalidatePath("/value");
  return result;
}
