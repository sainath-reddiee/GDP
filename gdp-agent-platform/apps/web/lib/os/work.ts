import type { RunSummary } from "@/lib/types";
import { GATES } from "@/lib/run-labels";
import { LIFECYCLE, type StepKey } from "@/lib/os/lifecycle";
import { actMode, has, type Grants } from "@/lib/os/access";
import type { OnboardingRequest } from "@/lib/os/requests";

// Which stage board a workflow belongs on, whether it waits on the viewer, and where Home sends a click. Shared by Home
// and the Intake, Design and Build boards (components/os/stage-board.tsx). Server-safe.

export type Stage = "intake" | "enable" | "design" | "build" | "review" | "done";
export type BoardView = "selected" | "action" | "active" | "completed";

const STEP_STAGE: Record<StepKey, Stage> = {
  intake: "intake", enable: "enable", ingest: "enable", design: "design", generate: "build", validate: "build", pr: "review", release: "done",
};
// a failed run is worked where it failed; the summary carries the stage name, not the state it failed from
const DESIGN_STAGES = /profil|domain|model|mapping|sttm|soda|dq/i;

/** The stage a run is at now: by the lifecycle step of its state. Failed runs by the stage they failed in. */
export function stageOfRun(run: Pick<RunSummary, "current_state" | "current_stage">): Stage {
  const s = run.current_state;
  if (s === "FAILED") return DESIGN_STAGES.test(run.current_stage ?? "") ? "design" : "build";
  const step = LIFECYCLE.find((x) => x.states.includes(s));
  return step ? STEP_STAGE[step.key] : "intake";
}

const ORDER: Stage[] = ["intake", "enable", "design", "build", "review", "done"];
/** The run has moved past this stage (completed here). */
export const pastStage = (run: Pick<RunSummary, "current_state" | "current_stage">, stage: Stage) =>
  run.current_state !== "FAILED" && run.current_state !== "CANCELLED" && ORDER.indexOf(stageOfRun(run)) > ORDER.indexOf(stage);

/** A run waits on the viewer: a gate they decide (or may ask a decision on), a failed validation or run they operate,
 *  or a step their persona works. Governance leads (APPROVAL.VIEW) see every gate. */
export function runNeedsMe(run: Pick<RunSummary, "current_state" | "lifecycle">, access: Grants & { persona?: { states: string[] } }): boolean {
  const s = run.current_state;
  if (s in GATES) return actMode(access, GATES[s].privilege) !== "none" || has(access, "APPROVAL.VIEW");
  if (!has(access, "RUN.OPERATE")) return false;
  if (s === "VALIDATION_FAILED" || s === "FAILED") return true;
  return !!access.persona?.states.includes(s) && run.lifecycle === "RUNNING";
}

/** The run holding a request at its stage: the first live source whose run is at that stage, else any linked run. */
export function requestRun(r: OnboardingRequest): string | null {
  const live = r.items.filter((i) => !i.removed_at && i.run_id);
  return (live.find((i) => i.stage === r.stage) ?? live[0])?.run_id ?? null;
}

/** Where Home sends a workflow: the stage board it is at, with it selected. */
export function workflowHref(r: OnboardingRequest): string {
  const run = requestRun(r);
  const pick = (board: string, key: "run" | "request", id: string) => `/os/${board}?${new URLSearchParams({ view: "selected", [key]: id })}`;
  if (r.status === "OPEN") {
    if (r.stage === "ENABLE") return "/os/enable";
    if (r.stage === "DESIGN" && run) return pick("design", "run", run);
    if (r.stage === "BUILD" && run) return pick("build", "run", run);
    if (r.stage === "REVIEW" && run) return `/os/review?run=${encodeURIComponent(run)}`;
  }
  return pick("intake", "request", r.request_id);
}

/** The view a board opens on: the selected item when one is named, else the viewer's queue when it has any, else Active. */
export function boardView(param: string | undefined, selected: boolean, actionCount: number): BoardView {
  if (param === "selected" || param === "action" || param === "active" || param === "completed") return param === "selected" && !selected ? "active" : param;
  return selected ? "selected" : actionCount ? "action" : "active";
}
