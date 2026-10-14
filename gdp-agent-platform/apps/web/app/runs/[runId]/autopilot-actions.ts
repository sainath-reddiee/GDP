"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type AutopilotStep = { action: string; label: string; from_state: string; seconds?: number; error?: string | null };
export type AutopilotStop = { kind: "GATE" | "BLOCKED" | "DONE" | "ACTION" | "WAIT"; label: string; where?: string; action?: string };
export type AutopilotJob = {
  job_id?: string; run_id: string; status: "IDLE" | "RUNNING" | "STOPPED" | "FAILED"; steps: AutopilotStep[];
  stop?: AutopilotStop | null; next?: AutopilotStop;
  children: { run_id: string; entity: string | null; steps: AutopilotStep[]; stop: AutopilotStop }[];
};
export type Accelerator = {
  run_id: string;
  mapping: { decided: number; accepted: number; changed: number; rejected: number; generated_share: number | null };
  checks: Record<string, number>;
  stages: { stage: string; minutes: number | null; human_gate: boolean }[];
  gates: { gate: string; passed: boolean; sent_back: number; first_time_right: boolean }[];
  automatic_minutes: number; automatic_steps: number;
};

export async function startAutopilot(runId: string) {
  return attemptValue(() => api<AutopilotJob>(`/api/runs/${encodeURIComponent(runId)}/autopilot`, { method: "POST" }));
}

export async function autopilotStatus(runId: string) {
  const r = await attemptValue(() => api<AutopilotJob>(`/api/runs/${encodeURIComponent(runId)}/autopilot`));
  if (r.ok && r.data.status !== "RUNNING") revalidatePath(`/runs/${runId}`, "layout");
  return r;
}

export async function runAccelerator(runId: string) {
  return attemptValue(() => api<Accelerator>(`/api/runs/${encodeURIComponent(runId)}/accelerator`));
}
