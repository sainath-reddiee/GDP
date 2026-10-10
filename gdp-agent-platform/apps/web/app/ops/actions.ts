"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

// ---------------------------------------------------------------- types (the /api/ops contract)

export type OpsSummary = { envs: number; dags: number; failing_24h: number; running: number; last_poll_at: string | null };

export type AirflowEnv = {
  env_id: string; name: string; kind: string; mwaa_env: string; region: string; airflow_url: string | null; api_version: string | null;
  enabled: boolean; poll_seconds: number; push_enabled: boolean; has_push_secret: boolean; last_poll_at: string | null;
  last_error: string | null; dags: number;
};
export type EnvInput = {
  name: string; mwaa_env: string; region: string; airflow_url?: string | null; poll_seconds: number; enabled: boolean; push_enabled: boolean;
};
export type EnvTest = { ok: boolean; version: string | null; api_version: string | null; dags_sample: string[]; error: string | null };
export type PushSecret = { secret: string; header_names: string[]; url_path: string };

export type Criticality = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type DagRow = {
  env_id: string; dag_id: string; owners: string[]; tags: string[]; schedule: string | null; is_paused: boolean; is_active: boolean;
  criticality: Criticality | null; team_id: string | null; domain_id: string | null; last_state: string | null; last_run_at: string | null;
  last_duration_s: number | null; /** 0..1 or null */ success_7d: number | null; /** 0..1 or null */ success_30d: number | null;
  runs_7d: number; p50_s: number | null; p95_s: number | null; open_incidents: number;
};
export type DagDetail = DagRow & {
  fileloc: string | null; expected_by_cron: string | null; max_duration_min: number | null; repo_id: string | null; repo_path: string | null;
  airflow_url: string | null; /** O3; absent on an older API */ timezone?: string | null; mute_until?: string | null; mute_reason?: string | null;
};
export type DagRun = {
  run_id: string; run_type: string | null; state: string | null; logical_date: string | null; start: string | null; end: string | null;
  duration_s: number | null; external_trigger: boolean | null; failed_tasks: number | null;
};
export type TaskRun = {
  task_id: string; map_index: number; try_number: number; state: string | null; operator: string | null; start: string | null;
  end: string | null; duration_s: number | null; error_excerpt: string | null; hostname: string | null;
};
export type TaskLog = { text: string; truncated: boolean; redacted: boolean; source: string | null; detail: string | null };
export type DagSettings = {
  team_id?: string | null; criticality?: Criticality | null; expected_by_cron?: string | null; max_duration_min?: number | null;
  domain_id?: string | null; repo_id?: string | null; repo_path?: string | null;
  timezone?: string | null; mute_until?: string | null; mute_reason?: string | null;
};
export type Reliability = {
  period: { from: string; to: string; days: number };
  teams: { team_id: string | null; name: string | null; incidents: number; p1: number; mttr_min: number | null; mtta_min: number | null; repeats: number }[];
  top_dags: { env_id: string; dag_id: string; failures: number; success_rate: number | null }[];
  repeats: { fingerprint: string; title: string; count: number }[];
  ai: { diagnoses: number; cost_usd: number | null };
};
export type DagFilters = { env_id: string; q?: string; state?: string; team_id?: string; owner?: string };

const json = (body: unknown) => JSON.stringify(body);
const enc = encodeURIComponent;

function changed() {
  revalidatePath("/ops");
  revalidatePath("/admin");
}

function query(params: Record<string, string | number | undefined | null>) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  return p.toString();
}

// ---------------------------------------------------------------- read (OPS.VIEW)

export async function opsSummary() {
  return attemptValue(() => api<OpsSummary>("/api/ops/summary"));
}

export async function listEnvs() {
  return attemptValue(() => api<{ envs: AirflowEnv[] }>("/api/ops/envs"));
}

export async function listDags(f: DagFilters) {
  return attemptValue(() => api<{ dags: DagRow[] }>(`/api/ops/dags?${query({ ...f })}`));
}

export async function dagDetail(envId: string, dagId: string) {
  return attemptValue(() => api<{ dag: DagDetail; runs: DagRun[] }>(`/api/ops/dag?${query({ env_id: envId, dag_id: dagId })}`));
}

export async function runDetail(envId: string, dagId: string, runId: string) {
  return attemptValue(() => api<{ run: DagRun; tasks: TaskRun[] }>(`/api/ops/run?${query({ env_id: envId, dag_id: dagId, run_id: runId })}`));
}

export async function taskLog(envId: string, dagId: string, runId: string, taskId: string, tryNumber: number, mapIndex: number) {
  return attemptValue(() => api<TaskLog>(`/api/ops/task-log?${query({
    env_id: envId, dag_id: dagId, run_id: runId, task_id: taskId, try: tryNumber, map_index: mapIndex,
  })}`));
}

export async function reliability(days: number, teamId: string) {
  return attemptValue(() => api<Reliability>(`/api/ops/reliability?${query({ days, team_id: teamId })}`));
}

// ---------------------------------------------------------------- operate (OPS.OPERATE)

export async function pollEnv(envId: string) {
  const r = await attemptValue(() => api<{ started: boolean; detail: string | null }>(`/api/ops/envs/${enc(envId)}/poll`, { method: "POST" }));
  changed();
  return r;
}

export async function saveDagSettings(envId: string, dagId: string, body: DagSettings) {
  const r = await attemptValue(() => api<unknown>(`/api/ops/dag?${query({ env_id: envId, dag_id: dagId })}`, { method: "PUT", body: json(body) }));
  changed();
  return r;
}

// ---------------------------------------------------------------- environments (INTEGRATION.MANAGE)

export async function createEnv(body: EnvInput) {
  const r = await attemptValue(() => api<AirflowEnv>("/api/ops/envs", { method: "POST", body: json(body) }));
  changed();
  return r;
}

export async function updateEnv(envId: string, body: EnvInput) {
  const r = await attemptValue(() => api<AirflowEnv>(`/api/ops/envs/${enc(envId)}`, { method: "PUT", body: json(body) }));
  changed();
  return r;
}

export async function deleteEnv(envId: string) {
  const r = await attemptValue(() => api<unknown>(`/api/ops/envs/${enc(envId)}`, { method: "DELETE" }));
  changed();
  return r;
}

export async function testEnv(envId: string) {
  return attemptValue(() => api<EnvTest>(`/api/ops/envs/${enc(envId)}/test`, { method: "POST" }));
}

export async function rotatePushSecret(envId: string) {
  const r = await attemptValue(() => api<PushSecret>(`/api/ops/envs/${enc(envId)}/push-secret`, { method: "POST" }));
  changed();
  return r;
}
