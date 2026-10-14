import type { RunSummary } from "@/lib/types";
import { stageLabel } from "@/lib/stages";
import { REVIEW_STATES } from "@/lib/run-insights";

// Run status helpers shared by the run table (client) and the Command Center (server).

/** Pipeline 1 steps as the run table's progress dots; Modeling covers the MAPPING and STTM stages. */
export const STAGE_ORDER = ["SOURCE", "PROFILING", "MODELING", "SODA", "DBT", "VALIDATION", "REVIEW"];

export function stageInfo(r: RunSummary) {
  const raw = r.current_stage === "DOMAIN" ? "PROFILING" : r.current_stage;
  const stage = raw === "MAPPING" || raw === "STTM" ? "MODELING" : raw;
  const index = stage ? STAGE_ORDER.indexOf(stage) : -1;
  const label = raw ? stageLabel(raw) : "Not started";
  const state = (r.current_state || "").replace(/_/g, " ").toLowerCase();
  return { index, label, state };
}

export function statusPill(r: RunSummary) {
  if (r.current_state === "CANCELLED") return { label: "Cancelled", cls: "bg-muted text-muted-foreground", dot: "bg-muted-foreground/50" };
  if (!r.is_archived && REVIEW_STATES.has(r.current_state)) return { label: "Needs review", cls: "bg-rose-500/10 text-rose-600", dot: "bg-rose-500" };
  switch (r.lifecycle) {
    case "RUNNING": return { label: "Running", cls: "bg-blue-500/10 text-blue-600", dot: "bg-blue-500 animate-pulse" };
    case "DRAFT": return { label: "Draft", cls: "bg-slate-500/10 text-slate-600", dot: "bg-slate-400" };
    case "COMPLETED": return { label: "Completed", cls: "bg-emerald-500/10 text-emerald-600", dot: "bg-emerald-500" };
    case "FAILED": return { label: "Failed", cls: "bg-amber-500/10 text-amber-700", dot: "bg-amber-500" };
    default: return { label: "Archived", cls: "bg-muted text-muted-foreground", dot: "bg-muted-foreground/50" };
  }
}

export function formatAge(minutes: number | null | undefined, createdAt: string): string {
  if (minutes == null) return createdAt.slice(0, 16).replace("T", " ");
  if (minutes < 60) return `${Math.max(minutes, 0)}m`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}h`;
  return `${Math.floor(minutes / (60 * 24))}d`;
}

