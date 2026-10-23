import type { RunState, RunSummary } from "@/lib/types";
import { GATES } from "@/lib/run-labels";

// The Agentic Data OS lifecycle: the run's real workflow states (snowflake/database/seed/workflow_graph.json) grouped
// into the eight steps the /os screens draw. Nothing here is invented: a step is done, active, waiting on a person or
// failed purely from the run's current_state (or failed_from_state when the run failed).

export type StepKey = "intake" | "enable" | "ingest" | "design" | "generate" | "validate" | "pr" | "release";
export type StepStatus = "done" | "active" | "waiting" | "failed" | "pending" | "cancelled";
export type Actor = "Person" | "Tool" | "Agent";

export type LifecycleStep = {
  key: StepKey; label: string; owner: string; actor: Actor; what: string;
  /** the workflow states inside this step, in order */
  states: string[];
  /** where the step is worked in the classic run workspace (/runs/[id]/...) */
  slug: string;
};

export const LIFECYCLE: LifecycleStep[] = [
  { key: "intake", label: "Intake", owner: "GDP Product Owner", actor: "Person", what: "Request and scope", states: ["CREATED"], slug: "" },
  { key: "enable", label: "Enable", owner: "Data OS Administrator", actor: "Tool", what: "Source access",
    states: ["SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED"], slug: "source" },
  { key: "ingest", label: "Ingest", owner: "Data OS Administrator", actor: "Tool", what: "Land to bronze",
    states: ["LANDING_PENDING", "LANDING_RUNNING", "LANDING_COMPLETE"], slug: "source" },
  { key: "design", label: "Design", owner: "Data Designer", actor: "Agent", what: "Profile, map, DQ contract",
    states: ["PROFILING_PENDING", "PROFILING_RUNNING", "PROFILING_COMPLETE", "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "MAPPING_REVIEW",
             "MAPPING_APPROVED", "STTM_PENDING", "STTM_REVIEW", "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED"],
    slug: "modeling?tab=mapping" },
  { key: "generate", label: "Generate", owner: "Forward Engineering Lead", actor: "Agent", what: "dbt, tests, config",
    states: ["DBT_PENDING", "DBT_GENERATING"], slug: "dbt" },
  { key: "validate", label: "Validate", owner: "Independent Review Agent", actor: "Agent", what: "Policy checks",
    states: ["VALIDATION_PENDING", "VALIDATION_RUNNING", "VALIDATION_PASSED", "VALIDATION_FAILED"], slug: "validation" },
  { key: "pr", label: "PR", owner: "Data Developer", actor: "Person", what: "Code review", states: ["DBT_REVIEW", "DBT_APPROVED"], slug: "review" },
  { key: "release", label: "Release", owner: "Data Developer", actor: "Person", what: "Deploy and observe",
    states: ["COMPLETED", "DEPLOYMENT_PENDING", "DEPLOYED", "PIPELINE_ENABLED"], slug: "audit" },
];

const ORDER = LIFECYCLE.flatMap((s) => s.states);
const stepIndexOf = (state: string | null | undefined) => LIFECYCLE.findIndex((s) => !!state && s.states.includes(state));

export type StepView = LifecycleStep & { status: StepStatus };

/** The eight steps with their status for one run. */
export function lifecycleOf(run: Pick<RunSummary, "current_state"> & { failed_from_state?: string | null }): StepView[] {
  const state = run.current_state;
  const failed = state === "FAILED";
  const cancelled = state === "CANCELLED";
  const at = stepIndexOf(failed || cancelled ? run.failed_from_state ?? null : state);
  return LIFECYCLE.map((step, i) => {
    let status: StepStatus = "pending";
    if (at < 0) status = cancelled ? "cancelled" : "pending";
    else if (i < at) status = "done";
    else if (i === at) {
      if (failed || state === "VALIDATION_FAILED") status = "failed";
      else if (cancelled) status = "cancelled";
      else if (step.key === "release" && state !== "DEPLOYMENT_PENDING") status = "done";
      else if (state in GATES) status = "waiting";
      else status = "active";
    }
    return { ...step, status };
  });
}

/** The step a run is on now (the first one not done), or the last when everything is done. */
export function currentStep(steps: StepView[]): StepView {
  return steps.find((s) => s.status !== "done") ?? steps[steps.length - 1];
}

/** 0..1 through the workflow, by state order. */
export function progressOf(run: Pick<RunSummary, "current_state">): number {
  const i = ORDER.indexOf(run.current_state);
  if (run.current_state === "COMPLETED") return 1;
  return i < 0 ? 0 : (i + 1) / ORDER.length;
}

/** The classic run workspace page for a step of a run. */
export const classicHref = (runId: string, step: LifecycleStep) => `/runs/${runId}${step.slug ? `/${step.slug}` : ""}`;

/** Lifecycle for a full RunState (the build workbench). */
export const lifecycleOfRun = (r: RunState) => lifecycleOf({ current_state: r.current_state, failed_from_state: r.failed_from_state });
