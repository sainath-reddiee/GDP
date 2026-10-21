"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import { storeAction } from "@/lib/store-action";
import type { DryRun, EnvironmentConfig } from "@/lib/store-types";

/** Deploy what changed since the last deploy; `force` re-applies every step. */
export async function deployPlatform(force = false) {
  const result = await attemptValue(() => api<{ ok: boolean; log: string[] }>(`/api/admin/apply${force ? "?force=true" : ""}`, { method: "POST" }));
  revalidatePath("/", "layout");
  return result;
}

export type RulesState = {
  rules: Record<string, unknown>; defaults: Record<string, unknown>; overridden: string[];
};

export async function saveRules(overrides: Record<string, unknown>, pipeline: "SILVER" | "GOLD" = "SILVER") {
  const result = await attemptValue(() =>
    api<RulesState>(`/api/config/rules?pipeline=${pipeline}`, { method: "PUT", body: JSON.stringify({ overrides }) }));
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

export type AccountModel = { name: string; family: string; source: string; kind?: string; available?: boolean };
export type ModelsState = {
  default: string; models: AccountModel[]; warnings: string[]; allowlist: string[] | null;
  by_stage: Record<string, string>; stages: string[];
};
export type ModelTestResult = { model: string; ok: boolean; reply?: string; error?: string; latency_ms: number };
export type ReconcileResult = {
  reconciled: number; awaiting_billing: number; access: boolean; detail: string | null;
  estimates_filled?: number; at?: string;
};

export async function loadModels(refresh = false) {
  return attemptValue(() => api<ModelsState>(`/api/config/models${refresh ? "?refresh=true" : ""}`));
}

export async function testModel(model: string) {
  return attemptValue(() => api<ModelTestResult>("/api/config/models/test", { method: "POST", body: JSON.stringify({ model }) }));
}

export async function reconcileCosts() {
  const result = await attemptValue(() => api<ReconcileResult>("/api/costs/reconcile", { method: "POST" }));
  if (result.ok) {
    revalidatePath("/audit");
    revalidatePath("/admin");
    revalidatePath("/dashboard");
  }
  return result;
}

// ---------------------------------------------------------------- environments (apps/api/app/environments_api.py)

export type EnvironmentsState = {
  config: EnvironmentConfig; saved: boolean; problems: string[]; platform_database: string; reserved: string[];
  history: { version: number; is_current: boolean; created_by: string | null; created_at: string | null; description: string | null }[];
};

/** A new version of the bindings (CONFIG.EDIT; under the default policy a platform admin approves it). */
export async function saveEnvironments(config: EnvironmentConfig, note?: string) {
  const result = await storeAction(() =>
    api<EnvironmentsState>("/api/config/environments", { method: "PUT", body: JSON.stringify({ config, note: note || null }) }));
  if (result.ok) revalidatePath("/admin");
  return result;
}

/** A pack's queries resolved with these (unsaved) bindings, guarded and EXPLAINed against the environment. */
export async function previewEnvironment(body: { config: EnvironmentConfig; environment: string | null; pack_id: string; domain_id: string }) {
  return storeAction(() => api<DryRun>("/api/config/environments/preview", { method: "POST", body: JSON.stringify(body) }));
}
