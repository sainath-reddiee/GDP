"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export async function deployPlatform() {
  const result = await attemptValue(() => api<{ ok: boolean; log: string[] }>("/api/admin/apply", { method: "POST" }));
  revalidatePath("/", "layout");
  return result;
}

export type RulesState = {
  rules: Record<string, unknown>; defaults: Record<string, unknown>; overridden: string[];
};

export async function saveRules(overrides: Record<string, unknown>) {
  const result = await attemptValue(() =>
    api<RulesState>("/api/config/rules", { method: "PUT", body: JSON.stringify({ overrides }) }));
  if (result.ok) revalidatePath("/admin");
  return result;
}

export type PlatformState = {
  settings: Record<string, { value: unknown; default: unknown; customised: boolean; changed_by: string | null; changed_at: string | null }>;
  known_models: string[];
  presets: Record<string, Record<string, unknown>>;
};

export async function savePlatformSetting(key: string, value: unknown, reset = false) {
  const result = await attemptValue(() =>
    api<PlatformState>("/api/config/platform", { method: "PUT", body: JSON.stringify({ key, value, reset }) }));
  if (result.ok) revalidatePath("/", "layout");
  return result;
}
