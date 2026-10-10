"use client";

import { cn } from "@/lib/utils";
import type { IncidentStatus, Severity } from "./actions";

export const STATUSES: IncidentStatus[] = ["OPEN", "ACK", "MITIGATED", "RESOLVED", "MUTED"];
export const SEVERITIES: Severity[] = ["P1", "P2", "P3", "P4"];

const STATUS_LABEL: Record<string, string> = { OPEN: "open", ACK: "acknowledged", MITIGATED: "mitigated", RESOLVED: "resolved", MUTED: "muted" };
const STATUS_TONE: Record<string, string> = {
  OPEN: "bg-rose-50 text-rose-700 ring-rose-100",
  ACK: "bg-amber-50 text-amber-700 ring-amber-100",
  MITIGATED: "bg-sky-50 text-sky-700 ring-sky-100",
  RESOLVED: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  MUTED: "bg-slate-100 text-slate-600 ring-slate-200",
};
const SEVERITY_TONE: Record<string, string> = {
  P1: "bg-rose-600 text-white ring-rose-700",
  P2: "bg-orange-500 text-white ring-orange-600",
  P3: "bg-amber-100 text-amber-800 ring-amber-200",
  P4: "bg-slate-100 text-slate-600 ring-slate-200",
};
const KIND_LABEL: Record<string, string> = {
  FAILED: "failed", RETRIES_EXHAUSTED: "retries exhausted", LATE: "late", LONG_RUNNING: "long running", UPSTREAM: "upstream",
};

export const up = (s: string | null | undefined) => (s ?? "").toUpperCase();
export const statusLabel = (s: string) => STATUS_LABEL[up(s)] ?? s.toLowerCase();
export const kindLabel = (k: string) => KIND_LABEL[up(k)] ?? k.toLowerCase().replace(/_/g, " ");

const pill = "inline-flex shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset";

export function SeverityPill({ severity, className }: { severity: string; className?: string }) {
  return <span className={cn(pill, "font-semibold", SEVERITY_TONE[up(severity)] ?? SEVERITY_TONE.P4, className)}>{up(severity) || "P?"}</span>;
}

export function IncidentStatusPill({ status, className }: { status: string; className?: string }) {
  return <span className={cn(pill, STATUS_TONE[up(status)] ?? STATUS_TONE.MUTED, className)}>{statusLabel(status)}</span>;
}

export function KindPill({ kind }: { kind: string }) {
  return <span className={cn(pill, "bg-violet-50 text-violet-700 ring-violet-100")}>{kindLabel(kind)}</span>;
}

export const dagRunHref = (envId: string, dagId: string, runId?: string | null) =>
  `/ops/dag?${new URLSearchParams({ env: envId, dag: dagId, ...(runId ? { run: runId } : {}) })}`;

export const incidentHref = (id: string) => `/incidents/${encodeURIComponent(id)}`;

/** The datetime-local value of a date, in the viewer's time zone. */
export function localInput(d: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
