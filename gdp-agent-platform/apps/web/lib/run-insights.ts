import type { RunSummary } from "@/lib/types";
import { byStage } from "@/lib/stages";

/** Workflow states that wait on a person (CORE.WORKFLOW_STATE kind REVIEW, plus a failed validation to fix). */
export const REVIEW_STATES = new Set(["MAPPING_REVIEW", "STTM_REVIEW", "SODA_REVIEW", "DBT_REVIEW", "VALIDATION_FAILED"]);

/** Pipeline order used for the stage funnel; DOMAIN shows under Profiling (it is identified right after). */
export const FUNNEL = [
  { stage: "SOURCE", label: "Source" },
  { stage: "PROFILING", label: "Profiling" },
  { stage: "MAPPING", label: "Mapping" },
  { stage: "STTM", label: "STTM" },
  { stage: "SODA", label: "Data Quality" },
  { stage: "DBT", label: "dbt" },
  { stage: "VALIDATION", label: "Validation" },
  { stage: "REVIEW", label: "Code review" },
] as const;

export function needsReview(run: RunSummary) {
  return !run.is_archived && REVIEW_STATES.has(run.current_state);
}

export function isCancelled(run: RunSummary) {
  return run.current_state === "CANCELLED";
}

export function summarize(runs: RunSummary[]) {
  const live = runs.filter((r) => !r.is_archived);
  const stages: Record<string, number> = {};
  for (const r of live) {
    if (r.lifecycle !== "RUNNING") continue;
    const stage = r.current_stage === "DOMAIN" ? "PROFILING" : r.current_stage ?? "SOURCE";
    stages[stage] = (stages[stage] ?? 0) + 1;
  }
  return {
    total: live.length,
    running: live.filter((r) => r.lifecycle === "RUNNING").length,
    drafts: live.filter((r) => r.lifecycle === "DRAFT").length,
    review: live.filter(needsReview).length,
    completed: live.filter((r) => r.lifecycle === "COMPLETED").length,
    failed: live.filter((r) => r.lifecycle === "FAILED" && !isCancelled(r)).length,
    cancelled: live.filter(isCancelled).length,
    stages,
  };
}

/** Where a run should open: its current stage page, or the overview. */
export function runHref(run: Pick<RunSummary, "run_id" | "current_stage">) {
  const stage = run.current_stage === "DOMAIN" ? "PROFILING" : run.current_stage;
  const meta = stage ? byStage(stage) : undefined;
  return meta ? `/runs/${run.run_id}/${meta.slug}` : `/runs/${run.run_id}`;
}

export function ago(value: string | null | undefined) {
  if (!value) return "";
  const iso = value.trim().replace(/^(\d{4}-\d{2}-\d{2})[ T]/, "$1T").replace(/\s*([+-])(\d{2}):?(\d{2})$/, "$1$2:$3");
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (Number.isNaN(mins)) return value.slice(0, 16);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.round(mins / 60)}h ago`;
  if (mins < 43200) return `${Math.round(mins / 1440)}d ago`;
  return value.slice(0, 10);
}
