import Link from "next/link";
import type { ReactNode } from "react";
import { AlertTriangle, ArrowRight, Check, Hand, Loader2, X } from "lucide-react";
import type { RunSummary } from "@/lib/types";
import { GATES, stateLabel } from "@/lib/run-labels";
import { lifecycleOf, type StepStatus } from "@/lib/os/lifecycle";
import type { BoardView } from "@/lib/os/work";
import { Empty, Tag, ago } from "./ui";
import { cn } from "@/lib/utils";

// The stage boards (Intake, Design, Build): one tab strip (Selected, Action required, Active, Completed), workflow cards
// with their steps and where each waits, and the selected workflow below. Server-safe, like ./ui.

/** A segmented control of links: Home's views and filters live in the URL. */
export function Segmented({ label, options, testId }: {
  label: string; testId?: string;
  options: { key: string; label: string; href: string; active: boolean; count?: number; dot?: boolean }[];
}) {
  return (
    <nav aria-label={label} data-testid={testId} className="inline-flex max-w-full overflow-x-auto rounded-lg border border-[var(--os-line)] bg-[var(--os-subtle)] p-0.5">
      {options.map((o) => (
        <Link key={o.key} href={o.href} scroll={false} aria-current={o.active ? "page" : undefined}
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

export type TrackStep = { key: string; label: string; owner?: string; state: "done" | "current" | "attention" | "pending" | "closed" };

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
        return (
          <li key={s.key} className="min-w-0" aria-current={now ? "step" : undefined} title={`${s.label}${s.owner ? ` · ${s.owner}` : ""}`}>
            <span className={cn("block h-1.5 rounded-full",
              s.state === "done" ? "bg-[var(--os-step-done)]" : s.state === "current" ? "os-step-now bg-[var(--os-accent)]"
                : s.state === "attention" ? "bg-[#d85b52]" : "bg-[var(--os-line)]")} />
            <span className={cn("mt-1.5 flex items-center gap-1 truncate text-[11px]", now ? "font-extrabold" : s.state === "done" ? "os-muted font-semibold" : "os-faint font-semibold")}>
              {s.state === "done" && <Check className="h-3 w-3 shrink-0 text-[var(--os-step-done-ink)]" />}
              {s.state === "attention" && <AlertTriangle className="h-3 w-3 shrink-0 text-[var(--os-red-ink)]" />}
              {s.state === "current" && <Hand className="h-3 w-3 shrink-0 text-[var(--os-accent-ink)]" />}
              <span className="truncate">{s.label}</span>
            </span>
            {now && (note || waiting || s.owner) && (
              <span className="os-faint mt-0.5 block truncate text-[10.5px]" title={note ?? undefined}>{note ?? `Waiting on ${waiting || s.owner}`}{since ? ` · ${since}` : ""}</span>
            )}
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

/** A run's eight steps as a track, what its current step is doing (a gate's approver, the agent at work, or who fixes a
 *  failure) and how many steps are still to go. */
export function runTrack(run: Pick<RunSummary, "current_state" | "updated_at" | "created_at" | "lifecycle">) {
  const steps = lifecycleOf(run);
  const at = steps.find((s) => s.status !== "done");
  const gate = GATES[run.current_state];
  const failed = run.current_state === "FAILED" || run.current_state === "VALIDATION_FAILED";
  // the data quality checks are approved by Build end to end (AUTOPILOT_APPROVES_DQ), not by a person
  const note = run.current_state === "SODA_REVIEW" ? `Waiting on ${FIXER}: Build end to end approves the checks`
    : gate ? `Waiting on ${gate.role}` : failed ? `Needs ${FIXER}: fix and retry`
    : at && run.lifecycle === "RUNNING" ? `${at.actor === "Agent" ? "Agent working" : "In progress"} · ${at.owner}`
    : at ? `Waiting on ${at.owner}` : null;
  return {
    steps: steps.map((s) => ({ key: s.key, label: s.label, owner: s.owner, state: TRACK_STATE[s.status] })),
    note,
    pending: steps.filter((s) => s.status !== "done").length,
    since: ago(run.updated_at ?? run.created_at).replace(/ ago$/, ""),
  };
}

/** Selected, Action required, Active, Completed: links that keep the selection, so Selected stays one click away. */
export function BoardTabs({ base, view, counts, selection, extra }: {
  base: string; view: BoardView; counts: { action: number; active: number; completed: number };
  /** the selected workflow's URL parameter, e.g. ["run", id]; Selected is off without one */
  selection: [string, string] | null; extra?: Record<string, string>;
}) {
  const href = (v: BoardView) => `${base}?${new URLSearchParams({ view: v, ...(selection ? { [selection[0]]: selection[1] } : {}), ...(extra ?? {}) })}`;
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="board-tabs">
      <Segmented label="Workflows to show" options={[
        ...(selection ? [{ key: "selected", label: "Selected", href: href("selected"), active: view === "selected" }] : []),
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
          {mine && <Tag tone="green">Your turn</Tag>}
          <Tag tone={failed ? "red" : run.current_state in GATES ? "amber" : "blue"}>
            {run.lifecycle === "RUNNING" && !failed && !(run.current_state in GATES) && <Loader2 className="os-spin h-3 w-3" />}
            {failed && <X className="h-3 w-3" />}{stateLabel(run.current_state)}
          </Tag>
          <Link href={href} scroll={false} className="os-btn" aria-label={`Open ${run.run_name}`}>Open <ArrowRight className="h-3.5 w-3.5" /></Link>
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
