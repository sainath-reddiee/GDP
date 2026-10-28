import Link from "next/link";
import type { ReactNode } from "react";
import { AlertTriangle, ArrowRight, Check, Hand, Loader2, X } from "lucide-react";
import type { RunSummary } from "@/lib/types";
import { GATES, stateLabel } from "@/lib/run-labels";
import { lifecycleOf, type StepStatus } from "@/lib/os/lifecycle";
import { RUNNING_STATES, type BoardView } from "@/lib/os/work";
import { Empty, Tag, ago } from "./ui";
import { DeleteRunButton } from "./request-ui";
import { cn } from "@/lib/utils";

// The stage boards (Intake, Design, Build): one tab strip (Selected, Action required, Active, Completed), workflow cards
// with their steps and where each waits, and the selected workflow below. Server-safe, like ./ui.

/** A segmented control of links: Home's views and filters live in the URL. */
export function Segmented({ label, options, testId }: {
  label: string; testId?: string;
  options: { key: string; label: string; href: string; active: boolean; count?: number; dot?: boolean; disabled?: boolean; title?: string }[];
}) {
  return (
    <nav aria-label={label} data-testid={testId} className="inline-flex max-w-full overflow-x-auto rounded-lg border border-[var(--os-line)] bg-[var(--os-subtle)] p-0.5">
      {options.map((o) => o.disabled ? (
        <span key={o.key} aria-disabled="true" title={o.title} data-testid={`${testId ?? "seg"}-${o.key}`}
              className="os-faint inline-flex cursor-not-allowed items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-[12px] font-bold opacity-60">{o.label}</span>
      ) : (
        <Link key={o.key} href={o.href} scroll={false} aria-current={o.active ? "page" : undefined} title={o.title} data-testid={testId ? `${testId}-${o.key}` : undefined}
              className={cn("relative inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-[12px] font-bold transition",
                            o.active ? "bg-[var(--os-surface)] text-[var(--os-ink)] shadow-[var(--os-shadow)]" : "os-muted hover:text-[var(--os-ink)]")}>
          {o.dot && <span className="h-1.5 w-1.5 rounded-full bg-[#d85b52]" aria-hidden />}
          {o.label}
          {o.count !== undefined && (
            <span className={cn("rounded-full px-1.5 text-[10.5px] tabular-nums", o.active ? "bg-[var(--os-green-bg)] text-[var(--os-green-ink)]" : "bg-[var(--os-neutral-bg)]")}>{o.count}</span>
          )}
        </Link>
      ))}
    </nav>
  );
}

export type TrackStep = { key: string; label: string; owner?: string; state: "done" | "current" | "attention" | "pending" | "closed"; href?: string };

/** A workflow's steps as one connected track: done, the step it is at (and on whom it waits), what comes next. */
export function Track({ steps, waiting, note, since, className }: {
  steps: TrackStep[]; waiting?: string | null; since?: string | null; className?: string;
  /** what the current step is doing, in full ("Agent working · Forward Engineering Lead"), instead of "Waiting on <waiting>" */
  note?: string | null;
}) {
  return (
    <ol className={cn("grid gap-1.5", className)} style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }} aria-label="Workflow steps">
      {steps.map((s) => {
        const now = s.state === "current" || s.state === "attention";
        const body = (
          <>
            <span className={cn("block h-1.5 rounded-full transition-transform group-hover/step:scale-y-125",
              s.state === "done" ? "bg-[var(--os-step-done)]" : s.state === "current" ? "os-step-now bg-[var(--os-accent)]"
                : s.state === "attention" ? "bg-[#d85b52]" : "bg-[var(--os-line)]")} />
            <span className={cn("mt-1.5 flex items-center gap-1 truncate text-[11px] group-hover/step:text-[var(--os-accent-ink)]", now ? "font-extrabold" : s.state === "done" ? "os-muted font-semibold" : "os-faint font-semibold")}>
              {s.state === "done" && <Check className="h-3 w-3 shrink-0 text-[var(--os-step-done-ink)]" />}
              {s.state === "attention" && <AlertTriangle className="h-3 w-3 shrink-0 text-[var(--os-red-ink)]" />}
              {s.state === "current" && <Hand className="h-3 w-3 shrink-0 text-[var(--os-accent-ink)]" />}
              <span className="truncate group-hover/step:underline">{s.label}</span>
            </span>
            {now && (note || waiting || s.owner) && (
              <span className="os-faint mt-0.5 block truncate text-[10.5px]" title={note ?? undefined}>{note ?? `Waiting on ${waiting || s.owner}`}{since ? ` · ${since}` : ""}</span>
            )}
          </>
        );
        return (
          <li key={s.key} className="min-w-0" aria-current={now ? "step" : undefined} title={`${s.label}${s.owner ? ` · ${s.owner}` : ""}`}>
            {s.href ? (
              <Link href={s.href} className="group/step -mx-1 -my-0.5 block rounded-md px-1 py-0.5 transition hover:bg-[var(--os-subtle)]">
                {body}
              </Link>
            ) : body}
          </li>
        );
      })}
    </ol>
  );
}

const TRACK_STATE: Record<StepStatus, TrackStep["state"]> = {
  done: "done", active: "current", waiting: "current", failed: "attention", pending: "pending", cancelled: "closed",
};

const FIXER = "Forward Engineering Lead";

const runStepUrl = (runId: string, key: string): string => {
  const run = encodeURIComponent(runId);
  if (key === "intake") return "/os/intake";
  if (key === "enable" || key === "ingest") return "/os/enable";
  if (key === "design") return `/os/design?view=selected&run=${run}`;
  if (key === "generate") return `/os/build?view=selected&run=${run}`;
  if (key === "validate") return `/os/review?view=selected&run=${run}`;
  if (key === "pr" || key === "release") return `/os/release?view=selected&run=${run}`;
  return `/os/build?view=selected&run=${run}`;
};

/** A run's eight steps as a track, what its current step is doing (a gate's approver, the agent at work, or who fixes a
 *  failure) and how many steps are still to go. */
export function runTrack(run: Pick<RunSummary, "current_state" | "updated_at" | "created_at" | "lifecycle"> & { run_id?: string }) {
  const steps = lifecycleOf(run);
  const at = steps.find((s) => s.status !== "done");
  const gate = GATES[run.current_state];
  const failed = run.current_state === "FAILED" || run.current_state === "VALIDATION_FAILED";
  const agentWorking = RUNNING_STATES.has(run.current_state);
  // the data quality checks are approved by Build end to end (AUTOPILOT_APPROVES_DQ), not by a person
  const note = run.current_state === "SODA_REVIEW" ? `Waiting on ${FIXER}: Build end to end approves the checks`
    : gate ? `Waiting on ${gate.role}` : failed ? `Needs ${FIXER}: fix and retry`
    : at && agentWorking ? `Agent working · ${at.owner}`
    : at ? `Waiting on ${at.owner}` : null;
  return {
    steps: steps.map((s) => ({
      key: s.key,
      label: s.label,
      owner: s.owner,
      state: TRACK_STATE[s.status],
      href: run.run_id ? runStepUrl(run.run_id, s.key) : undefined,
    })),
    note,
    pending: steps.filter((s) => s.status !== "done").length,
    since: ago(run.updated_at ?? run.created_at).replace(/ ago$/, ""),
  };
}

/** Selected, Action required, Active, Completed: links that keep the selection, so Selected stays one click away. */
export function BoardTabs({ base, view, counts, selection, fallback, extra }: {
  base: string; view: BoardView; counts: { action: number; active: number; completed: number };
  /** the selected workflow's URL parameter, e.g. ["run", id] */
  selection: [string, string] | null;
  /** what Selected opens when nothing is selected yet (the first item waiting on the viewer, else the first active);
   *  Selected is always shown, and disabled only when the board has nothing at all */
  fallback?: [string, string] | null; extra?: Record<string, string>;
}) {
  const pick = selection ?? fallback ?? null;
  const href = (v: BoardView) => `${base}?${new URLSearchParams({ view: v, ...(selection ? { [selection[0]]: selection[1] } : {}), ...(extra ?? {}) })}`;
  const selectedHref = `${base}?${new URLSearchParams({ view: "selected", ...(pick ? { [pick[0]]: pick[1] } : {}), ...(extra ?? {}) })}`;
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="board-tabs">
      <Segmented label="Workflows to show" testId="board-view" options={[
        { key: "selected", label: "Selected", href: selectedHref, active: view === "selected", disabled: !pick,
          title: pick ? (selection ? "The selected item" : "Opens the first item waiting on you") : "Nothing to select yet" },
        { key: "action", label: "Action required", href: href("action"), active: view === "action", count: counts.action, dot: counts.action > 0 },
        { key: "active", label: "Active", href: href("active"), active: view === "active", count: counts.active },
        { key: "completed", label: "Completed", href: href("completed"), active: view === "completed", count: counts.completed },
      ]} />
    </div>
  );
}

/** One run on a board: what it is, its eight steps, where it waits, and how far it has to go. */
export function RunCard({ run, href, mine, current }: { run: RunSummary; href: string; mine: boolean; current?: boolean }) {
  const t = runTrack(run);
  const failed = run.current_state === "FAILED" || run.current_state === "VALIDATION_FAILED";
  return (
    <article data-testid="run-card" className={cn("os-card os-rise p-4 transition hover:shadow-[var(--os-shadow-pop)]",
                                                   mine && "border-l-[3px] border-l-[var(--os-accent)]", current && "ring-2 ring-[var(--os-accent)]")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-[min(100%,240px)] flex-1">
          <Link href={href} scroll={false} className="block truncate text-[14px] font-extrabold tracking-[-0.01em] hover:underline" title={run.run_name}>{run.run_name}</Link>
          <div className="os-faint mt-1 flex flex-wrap items-center gap-x-1.5 text-[11px]">
            <span className="os-muted font-semibold">{run.domain_name ?? "No domain"}</span>
            {run.source_system_name && <span>· {run.source_system_name}</span>}
            {run.pipeline === "GOLD" && <Tag tone="amber">Gold</Tag>}
            <span>· {t.pending ? `${t.pending} of 8 steps to go` : "all 8 steps done"}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Tag tone={failed ? "red" : run.current_state in GATES ? "amber" : "blue"}>
            {run.lifecycle === "RUNNING" && !failed && !(run.current_state in GATES) && <Loader2 className="os-spin h-3 w-3" />}
            {failed && <X className="h-3 w-3" />}{stateLabel(run.current_state)}
          </Tag>
          <Link href={href} scroll={false} className="os-btn" aria-label={`Open ${run.run_name}`}>Open <ArrowRight className="h-3.5 w-3.5" /></Link>
          <DeleteRunButton runIds={[run.run_id]} iconOnly />
        </div>
      </div>
      <Track steps={t.steps} note={t.note} since={t.since} className="mt-3.5" />
    </article>
  );
}

/** The list under a board tab, or what to say when it is empty. */
export function BoardList({ empty, children, count }: { empty: { title: string; body?: ReactNode }; children: ReactNode; count: number }) {
  return count ? <div className="flex flex-col gap-3">{children}</div> : <Empty icon={Check} title={empty.title}>{empty.body}</Empty>;
}

const shortTable = (targetModel?: string | null, fallback?: string) => {
  if (targetModel) {
    const parts = targetModel.split(".");
    return parts[parts.length - 1] || targetModel;
  }
  if (fallback?.includes(":")) return fallback.split(":").pop()?.trim() || fallback;
  return fallback ?? "Table";
};

const cleanWorkflowName = (run: RunSummary, parentName?: string | null) => {
  const raw = (parentName || run.run_name || "").replace(/\s*\(E2E test\)\s*/i, "").replace(/:\s*[A-Za-z0-9_]+$/, "").trim();
  if (run.domain_name && run.source_system_name) {
    return `${run.domain_name} · ${run.source_system_name}`;
  }
  return raw || run.run_name;
};

export const fiveStageTrack = (runs: RunSummary[], primaryRunId?: string): TrackStep[] => {
  const states = runs.map((r) => r.current_state);
  const anyFailed = states.some((s) => s === "VALIDATION_FAILED" || s === "FAILED");
  const allPublished = states.every((s) => ["COMPLETED", "DBT_APPROVED", "DEPLOYED", "PIPELINE_ENABLED"].includes(s));
  const anyInReview = states.some((s) => ["DBT_REVIEW", "VALIDATION_PASSED", "VALIDATION_FAILED", "VALIDATION_RUNNING"].includes(s));
  const anyInBuild = states.some((s) => ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING"].includes(s));
  const anyInDesign = states.some((s) => /^(PROFILING|DOMAIN|MAPPING|STTM)_/.test(s) && s !== "STTM_APPROVED");

  const activeStage = allPublished ? "publish"
    : anyFailed ? "validate"
    : anyInReview ? "validate"
    : anyInBuild ? "build"
    : anyInDesign ? "design"
    : "initiate";

  const order = ["initiate", "design", "build", "validate", "publish"] as const;
  const runParam = primaryRunId ? encodeURIComponent(primaryRunId) : null;
  const labels: Record<(typeof order)[number], { label: string; owner: string; href?: string }> = {
    initiate: { label: "Initiate", owner: "Data OS Administrator", href: "/os/intake" },
    design: { label: "Design", owner: "Data Designer", href: runParam ? `/os/design?view=selected&run=${runParam}` : "/os/design" },
    build: { label: "Build", owner: "Forward Engineering Lead", href: runParam ? `/os/build?view=selected&run=${runParam}` : "/os/build" },
    validate: { label: "Validate", owner: "Independent Review Agent", href: runParam ? `/os/review?view=selected&run=${runParam}` : "/os/review" },
    publish: { label: "Publish", owner: "Data Developer", href: runParam ? `/os/release?view=selected&run=${runParam}` : "/os/release" },
  };
  const idx = order.indexOf(activeStage);
  return order.map((k, i) => ({
    key: k,
    label: labels[k].label,
    owner: labels[k].owner,
    state: allPublished ? "done" : i < idx ? "done" : i === idx ? (anyFailed ? "attention" : "current") : "pending",
    href: labels[k].href,
  }));
};

export type WorkflowGroup = {
  key: string;
  title: string;
  subtitle: string;
  domain: string;
  source: string | null;
  primaryRun: RunSummary;
  runs: RunSummary[];
  mine: boolean;
};

/** Groups leaf runs into clean parent workflows so multi-table runs appear as one cohesive workflow card. */
export function groupRunsByWorkflow(
  runs: RunSummary[],
  allRuns: RunSummary[],
  isMine: (r: RunSummary) => boolean,
): WorkflowGroup[] {
  const parentMap = new Map<string, RunSummary>();
  for (const r of allRuns) {
    if (!r.parent_run_id) parentMap.set(r.run_id, r);
  }
  const groups = new Map<string, RunSummary[]>();
  for (const r of runs) {
    const key = r.parent_run_id ? `parent:${r.parent_run_id}` : `run:${r.run_id}`;
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  const out: WorkflowGroup[] = [];
  for (const [key, list] of groups.entries()) {
    const parent = key.startsWith("parent:") ? parentMap.get(key.slice(7)) : undefined;
    const primary = list.find((r) => isMine(r))
      ?? list.find((r) => r.current_state === "VALIDATION_FAILED" || r.current_state === "FAILED")
      ?? list.find((r) => r.current_state === "DBT_REVIEW")
      ?? list[0];
    const domain = primary.domain_name ?? parent?.domain_name ?? "No domain";
    const source = primary.source_system_name ?? parent?.source_system_name ?? null;
    const title = cleanWorkflowName(primary, parent?.run_name);
    const subtitle = (parent?.run_name || primary.run_name).replace(/:\s*[A-Za-z0-9_]+$/, "").replace(/\s*\(E2E test\)\s*/i, "").trim();
    out.push({
      key,
      title,
      subtitle,
      domain,
      source,
      primaryRun: primary,
      runs: list,
      mine: list.some((r) => isMine(r)),
    });
  }
  return out;
}

/** A grouped workflow card on Build, Validate, or Publish: shows the workflow title, 5-stage track, and target table pills. */
export function WorkflowGroupCard({
  group,
  selectHref,
  currentRunId,
}: {
  group: WorkflowGroup;
  selectHref: (runId: string) => string;
  currentRunId?: string | null;
}) {
  const { primaryRun, runs, title, subtitle, domain, source, mine } = group;
  const steps = fiveStageTrack(runs, primaryRun.run_id);
  const failedCount = runs.filter((r) => r.current_state === "VALIDATION_FAILED" || r.current_state === "FAILED").length;
  const reviewCount = runs.filter((r) => r.current_state === "DBT_REVIEW" || r.current_state === "VALIDATION_PASSED").length;
  const doneCount = runs.filter((r) => ["COMPLETED", "DBT_APPROVED", "DEPLOYED", "PIPELINE_ENABLED"].includes(r.current_state)).length;
  const readyCount = runs.length - failedCount - reviewCount - doneCount;
  const href = selectHref(primaryRun.run_id);

  return (
    <article
      data-testid="run-card"
      className={cn(
        "os-card os-rise p-4 transition hover:shadow-[var(--os-shadow-pop)]",
        mine && "border-l-[3px] border-l-[var(--os-forest)]",
        currentRunId && runs.some((r) => r.run_id === currentRunId) && "ring-2 ring-[var(--os-accent)]",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-[min(100%,240px)] flex-1">
          <Link href={href} scroll={false} className="block truncate text-[14.5px] font-extrabold tracking-[-0.01em] hover:underline" title={title}>
            {title}
          </Link>
          <div className="os-faint mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11px]">
            <Tag tone="blue">{domain}</Tag>
            {source && <span className="os-muted font-semibold">· {source}</span>}
            {subtitle && subtitle !== title && <span>· {subtitle}</span>}
            <span>· {runs.length} {runs.length === 1 ? "table" : "tables"}</span>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {failedCount > 0 && <Tag tone="red"><X className="h-3 w-3" />{failedCount} flagged</Tag>}
          {reviewCount > 0 && <Tag tone="amber">{reviewCount} in review</Tag>}
          {readyCount > 0 && <Tag tone="blue">{readyCount} building</Tag>}
          {doneCount > 0 && <Tag tone="green">{doneCount} published</Tag>}
          <Link href={href} scroll={false} className="os-btn" aria-label={`Open ${primaryRun.run_name}`}>
            Open <ArrowRight className="h-3.5 w-3.5" />
          </Link>
          <DeleteRunButton runIds={runs.map((r) => r.run_id)} iconOnly />
        </div>
      </div>

      <Track steps={steps} className="mt-3.5" />

      {runs.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-[var(--os-line-soft)] pt-2.5">
          <span className="os-faint mr-1 text-[10.5px] font-bold uppercase tracking-wide">Tables:</span>
          {runs.map((r) => {
            const name = shortTable(r.target_model, r.run_name);
            const isFailed = r.current_state === "VALIDATION_FAILED" || r.current_state === "FAILED";
            const isDone = ["COMPLETED", "DBT_APPROVED", "DEPLOYED", "PIPELINE_ENABLED"].includes(r.current_state);
            const isReview = r.current_state === "DBT_REVIEW" || r.current_state === "VALIDATION_PASSED";
            return (
              <Link
                key={r.run_id}
                href={selectHref(r.run_id)}
                scroll={false}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-bold transition hover:border-[var(--os-forest)]",
                  r.run_id === currentRunId
                    ? "border-[var(--os-forest)] bg-[var(--os-green-bg)] text-[var(--os-ink)]"
                    : "border-[var(--os-line)] bg-[var(--os-surface)]",
                )}
              >
                <span>{name}</span>
                <Tag tone={isDone ? "green" : isFailed ? "red" : isReview ? "amber" : "blue"}>{stateLabel(r.current_state)}</Tag>
              </Link>
            );
          })}
        </div>
      )}
    </article>
  );
}

/** Filter bar for Build, Validate, and Publish boards: Domain pills + optional Status pills. */
export function StageFilterBar({
  domains,
  activeDomain,
  domainHref,
  statuses,
  activeStatus,
  statusHref,
}: {
  domains: { key: string; label: string; count: number }[];
  activeDomain: string;
  domainHref: (domain: string) => string;
  statuses?: { key: string; label: string; count: number; dot?: boolean }[];
  activeStatus?: string;
  statusHref?: (status: string) => string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="stage-filter-bar">
      {domains.length > 1 && (
        <Segmented
          label="Domain filter"
          testId="domain-filter"
          options={domains.map((d) => ({
            key: d.key,
            label: d.label,
            href: domainHref(d.key),
            active: d.key === activeDomain,
            count: d.count,
          }))}
        />
      )}
      {statuses && statuses.length > 1 && statusHref && (
        <>
          {domains.length > 1 && <span className="mx-0.5 hidden h-5 w-px bg-[var(--os-line)] sm:block" aria-hidden />}
          <Segmented
            label="Status filter"
            testId="status-filter"
            options={statuses.map((s) => ({
              key: s.key,
              label: s.label,
              href: statusHref(s.key),
              active: s.key === (activeStatus ?? "all"),
              count: s.count,
              dot: s.dot,
            }))}
          />
        </>
      )}
    </div>
  );
}

/** Unified 5-stage workflow progression bar (Initiate → Design → Build → Validate → Publish) for selected workspaces. */
export function WorkflowJourneyBar({
  runId,
  currentStage,
  generated,
  validated,
  validationPassed,
  score,
  prPublished,
  released,
}: {
  runId: string;
  currentStage: "design" | "build" | "validate" | "publish";
  generated?: boolean;
  validated?: boolean;
  validationPassed?: boolean;
  score?: number | null;
  prPublished?: boolean;
  released?: boolean;
}) {
  const runParam = encodeURIComponent(runId);
  const stages: {
    key: "initiate" | "design" | "build" | "validate" | "publish";
    num: string;
    label: string;
    sub: string;
    href: string;
    state: "done" | "current" | "failed" | "pending";
  }[] = [
    {
      key: "initiate",
      num: "01",
      label: "Initiate",
      sub: "Connect & land",
      href: "/os/intake",
      state: "done",
    },
    {
      key: "design",
      num: "02",
      label: "Design",
      sub: "Profile, model & map",
      href: `/os/design?view=selected&run=${runParam}`,
      state: currentStage === "design" ? "current" : "done",
    },
    {
      key: "build",
      num: "03",
      label: "Build",
      sub: generated ? "dbt, DQ & QA ready" : "dbt, DQ & QA",
      href: `/os/build?view=selected&run=${runParam}`,
      state: currentStage === "build" ? "current" : generated ? "done" : "pending",
    },
    {
      key: "validate",
      num: "04",
      label: "Validate",
      sub: score !== undefined && score !== null
        ? `${score}% · ${validationPassed ? "Passed" : "Flagged"}`
        : "Validation & review",
      href: `/os/review?view=selected&run=${runParam}`,
      state: validated && !validationPassed ? "failed" : currentStage === "validate" ? "current" : validationPassed ? "done" : "pending",
    },
    {
      key: "publish",
      num: "05",
      label: "Publish",
      sub: released ? "Published" : prPublished ? "PR open · QA sign-off" : "PR & QA sign-off",
      href: `/os/release?view=selected&run=${runParam}`,
      state: released ? "done" : currentStage === "publish" ? "current" : prPublished ? "done" : "pending",
    },
  ];

  return (
    <nav aria-label="Workflow stages" data-testid="workflow-journey-bar" className="os-card mb-4 grid grid-cols-2 gap-2 p-2.5 sm:grid-cols-5">
      {stages.map((s) => {
        const active = s.key === currentStage;
        return (
          <Link
            key={s.key}
            href={s.href}
            aria-current={active ? "step" : undefined}
            className={cn(
              "os-hover flex flex-col justify-between rounded-lg border px-3 py-2 transition",
              active
                ? "border-[var(--os-forest)] bg-[var(--os-green-bg)] shadow-sm"
                : s.state === "failed"
                  ? "border-[var(--os-red-line)] bg-[var(--os-red-bg)]/50"
                  : "border-[var(--os-line)] bg-[var(--os-surface)]",
            )}
          >
            <div className="flex items-center justify-between gap-1.5">
              <span className="os-faint text-[10px] font-bold">{s.num}</span>
              <Tag tone={s.state === "done" ? "green" : s.state === "failed" ? "red" : active ? "blue" : "neutral"}>
                {s.state === "done" ? "Done" : s.state === "failed" ? "Flagged" : active ? "Here" : "Next"}
              </Tag>
            </div>
            <div className="mt-1 text-[13px] font-extrabold tracking-[-0.01em]">{s.label}</div>
            <div className="os-faint mt-0.5 truncate text-[10.5px]">{s.sub}</div>
          </Link>
        );
      })}
    </nav>
  );
}

