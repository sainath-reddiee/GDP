"use server";

import { api, attemptValue } from "@/lib/api";
import type { LearnedRunbooks } from "@/components/ops/runbook-solutions";

// ---------------------------------------------------------------- types (the /api/ops/datadog contract)

/** What the API host has; never a key. */
export type DatadogConnection = {
  site: string; app_url: string; api_key_set: boolean; app_key_set: boolean;
  /** how many DATADOG_HOST / DATADOG_SERVICE names the API host has (never the names) */
  hosts_set: number; services_set: number; configured: boolean; problems: string[];
};
/** The DATADOG platform setting (services/ops/datadog.py DEFAULT_SETTING). */
export type DatadogSetting = {
  enabled: boolean; log_query: string; log_lookback_minutes: number; log_limit: number; diagnosis_logs: boolean;
  /** where a failed task's error is read from: Airflow then Datadog, Datadog first, or Airflow only */
  failure_logs: "fallback" | "first" | "off";
  monitors: boolean; monitor_tags: string[]; monitor_states: ("Alert" | "Warn" | "No Data")[]; default_severity: "P1" | "P2" | "P3" | "P4";
  /** the pipelines' Datadog hosts and services; empty: DATADOG_HOST / DATADOG_SERVICE on the API host */
  hosts: string[]; services: string[];
};
export type DatadogStatus = { connection: DatadogConnection; setting: DatadogSetting };
export type DatadogTest = {
  ok: boolean; connection: DatadogConnection; api_key: boolean; logs: number | null; monitors: number | null;
  errors: Record<string, string>; query?: string;
};
export type DatadogLog = { id: string; at: string | null; status: string | null; service: string | null; host: string | null; message: string; tags: string[] };
export type IncidentDatadogLogs = {
  enabled: boolean; logs: DatadogLog[]; error: string | null; kind?: string;
  query?: string; from?: string; to?: string; link?: string;
  /** the runbooks these logs matched, and errors proposed for review (services/ops/runbook_learning.py) */
  runbooks?: LearnedRunbooks;
};
export type MonitorCheck = { disabled?: boolean; monitors?: number; alerting?: number; actions?: Record<string, number>; resolved?: number; errors?: string[] };

const enc = encodeURIComponent;

export async function loadDatadog() {
  return attemptValue(() => api<DatadogStatus>("/api/ops/datadog"));
}

export async function testDatadog() {
  return attemptValue(() => api<DatadogTest>("/api/ops/datadog/test", { method: "POST" }));
}

export async function checkMonitorsNow() {
  return attemptValue(() => api<MonitorCheck>("/api/ops/datadog/monitors/check", { method: "POST" }));
}

export async function incidentDatadogLogs(incidentId: string, minutes?: number) {
  return attemptValue(() => api<IncidentDatadogLogs>(`/api/ops/incidents/${enc(incidentId)}/datadog-logs${minutes ? `?minutes=${minutes}` : ""}`));
}
