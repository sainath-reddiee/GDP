import type { RunSummary } from "@/lib/types";
import { GATES } from "@/lib/run-labels";
import { LIFECYCLE, type StepKey } from "@/lib/os/lifecycle";
import { has, type Grants } from "@/lib/os/access";
import { waitRole, type OnboardingRequest, type RequestItem, type Wait } from "@/lib/os/requests";
import { PERSONAS } from "@/lib/os/personas";

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

/** The runs every board and Home read: the same list, so their counts agree. The most recently updated runs, plus
 *  every run at a gate and every failed run however long they have waited (a gate untouched for weeks would otherwise
 *  sort past the limit and drop off). */
export const RUNS_QUERY = "sort=updated&limit=500";
export const RUNS_WAITING = ["needs_review=true&sort=updated&limit=500", "status=failed&sort=updated&limit=500"];
export function mergeRuns<T extends { run_id: string }>(...lists: T[][]): T[] {
  const seen = new Set<string>();
  return lists.flat().filter((r) => (seen.has(r.run_id) ? false : (seen.add(r.run_id), true)));
}

/** A run mapped to several tables fans out into its table runs; the leaf runs are the ones boards list and work on. */
export function leafRuns<T extends { run_id: string; parent_run_id?: string | null; is_archived?: boolean; current_state?: string }>(runs: T[]): T[] {
  const parents = new Set(
    runs.filter((r) => !r.is_archived && r.current_state !== "CANCELLED").map((r) => r.parent_run_id).filter(Boolean) as string[],
  );
  return runs.filter((r) => !parents.has(r.run_id));
}

/** Resolves a run id from the URL: when it names a parent run whose work continues in its table runs, picks the
 *  table run waiting on the viewer (or at the board's stage). */
export function resolveBoardRun(runs: RunSummary[], runId: string | undefined,
                                access: Grants & { persona?: { states: string[] } }, stage: Stage): RunSummary | null {
  if (!runId) return null;
  const kids = runs.filter((r) => !r.is_archived && r.current_state !== "CANCELLED" && r.parent_run_id === runId);
  if (kids.length) {
    return kids.find((r) => runNeedsMe(r, access))
      ?? kids.find((r) => stageOfRun(r) === stage)
      ?? kids.find((r) => r.current_state !== "COMPLETED" && r.current_state !== "DBT_APPROVED")
      ?? kids[0];
  }
  return runs.find((r) => r.run_id === runId) ?? null;
}

/** A run waits on the viewer: a gate they decide, a failed validation or run they operate, or a step their persona
 *  works. One rule for Home and the Design and Build boards (a gate the viewer may only raise a change request on is
 *  not theirs to act on, so it is not "Action required"). */
export function runNeedsMe(run: Pick<RunSummary, "current_state" | "lifecycle">, access: Grants & { persona?: { states: string[] } }): boolean {
  const s = run.current_state;
  // the data quality checks are approved by Build end to end too (AUTOPILOT_APPROVES_DQ), as the request routes them
  if (s === "SODA_REVIEW") return has(access, GATES[s].privilege) || has(access, "RUN.OPERATE");
  if (s in GATES) return has(access, GATES[s].privilege);
  if (s === "VALIDATION_FAILED" || s === "FAILED") return has(access, "RUN.OPERATE");
  // a step the viewer's persona works (the Data Designer profiles and maps without RUN.OPERATE), while no agent runs
  // it; a run not started yet (CREATED) is a draft, still somebody's step
  return !!access.persona?.states.includes(s) && (run.lifecycle === "RUNNING" || run.lifecycle === "DRAFT") && !RUNNING_STATES.has(s);
}

/** States where an agent is working: nobody has anything to do until it finishes. */
export const RUNNING_STATES = new Set(["ACCESS_VALIDATION", "LANDING_RUNNING", "PROFILING_RUNNING", "DBT_GENERATING", "VALIDATION_RUNNING"]);

/** Whether a wait on a request belongs to the viewer's current persona and privilege, while no agent is running it. */
export function waitIsMine(w: Wait, items: RequestItem[], access: Grants & { persona?: { id: string; label: string } }): boolean {
  if (!has(access, w.privilege)) return false;
  if (access.persona && access.persona.id !== "super") {
    const role = waitRole(w);
    if (role && PERSONAS.some((p) => p.label.toLowerCase() === role.toLowerCase())
      && access.persona.label.toLowerCase() !== role.toLowerCase()) {
      return false;
    }
  }
  const item = w.item_id ? items.find((i) => i.item_id === w.item_id) : undefined;
  return !(item?.run_state && RUNNING_STATES.has(item.run_state));
}

/** A workflow whose next step is the viewer's: open, a wait on a privilege they hold, and not a step an agent is still
 *  running (a landing or validation in progress is shown as active, not as something to do). */
export function requestNeedsMe(r: Pick<OnboardingRequest, "status" | "waits" | "items">,
                               access: Grants & { persona?: { id: string; label: string } }): boolean {
  if (r.status !== "OPEN") return false;
  return r.waits.some((w) => waitIsMine(w, r.items, access));
}

/** The run holding a request at its stage: the first live source whose run is at that stage, else any linked run. */
export function requestRun(r: OnboardingRequest): string | null {
  const live = r.items.filter((i) => !i.removed_at && (i.active_run_id || i.run_id));
  const pick = live.find((i) => i.stage === r.stage) ?? live[0];
  return pick?.active_run_id ?? pick?.run_id ?? null;
}

/** Where Home sends a workflow: Intake, with it selected (the request's steps, who acts now, its sources). */
export const workflowHref = (r: Pick<OnboardingRequest, "request_id">) =>
  `/os/intake?${new URLSearchParams({ view: "selected", request: r.request_id })}`;

/** The stage board an open workflow is worked on, with its run selected (Design, Build), or the screen of its stage
 *  (Enable, Review); null when it is at Intake or closed. */
export function stageHref(r: OnboardingRequest): string | null {
  if (r.status !== "OPEN") return null;
  const run = requestRun(r);
  if (r.stage === "ENABLE") return "/os/enable";
  if (r.stage === "DESIGN" && run) return `/os/design?${new URLSearchParams({ view: "selected", run })}`;
  if (r.stage === "BUILD" && run) return `/os/build?${new URLSearchParams({ view: "selected", run })}`;
  if (r.stage === "REVIEW" && run) return `/os/review?run=${encodeURIComponent(run)}`;
  return null;
}

/** Open requests by intent, for the in-flight counts beside New and Enhance. */
export function inflight(requests: Pick<OnboardingRequest, "status" | "intent">[], active: "new" | "enhance" | null = null) {
  const open = requests.filter((r) => r.status === "OPEN");
  return { new: open.filter((r) => r.intent !== "ENHANCE").length, enhance: open.filter((r) => r.intent === "ENHANCE").length, active };
}

/** The view a board opens on: the selected item when one is named, else the viewer's queue when it has any, else Active. */
export function boardView(param: string | undefined, selected: boolean, actionCount: number): BoardView {
  if (param === "selected" || param === "action" || param === "active" || param === "completed") return param === "selected" && !selected ? "active" : param;
  return selected ? "selected" : actionCount ? "action" : "active";
}
