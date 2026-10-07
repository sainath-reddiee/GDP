"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AlertTriangle, BadgeCheck, Check, Code2, Database, Eye, FileSpreadsheet, FlaskConical, GitCompareArrows,
  GitPullRequest, LayoutGrid, Lock, ScanSearch, ShieldCheck, X,
} from "lucide-react";
import type { StageStatus, StageStatusValue } from "@/lib/types";
import { byStage } from "@/lib/stages";
import { cn } from "@/lib/utils";

/** Lineage of a run: a linear spine to the STTM, three parallel lanes built from it (QA tests, Data Quality,
 *  dbt then Validation), merging into Code review. Connectors turn green as the stage on their left completes. */

const ICON: Record<string, typeof Database> = {
  SOURCE: Database, PROFILING: ScanSearch, MAPPING: GitCompareArrows, STTM: FileSpreadsheet, QA: FlaskConical,
  SODA: ShieldCheck, DBT: Code2, VALIDATION: BadgeCheck, REVIEW: GitPullRequest,
};

type NodeStatus = StageStatusValue | "AVAILABLE";

const TONE: Record<NodeStatus, { tile: string; text: string; label: string }> = {
  COMPLETE: { tile: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600", text: "text-emerald-600", label: "complete" },
  ACTIVE: { tile: "border-blue-500/40 bg-blue-500/10 text-blue-600 ring-4 ring-blue-500/10", text: "text-blue-600", label: "in progress" },
  REVIEW_REQUIRED: { tile: "border-amber-500/40 bg-amber-500/10 text-amber-600 ring-4 ring-amber-500/10", text: "text-amber-600", label: "needs review" },
  LOCKED: { tile: "border-border bg-muted/60 text-muted-foreground/60", text: "text-muted-foreground", label: "locked" },
  FAILED: { tile: "border-rose-500/40 bg-rose-500/10 text-rose-600", text: "text-rose-600", label: "failed" },
  BLOCKED: { tile: "border-rose-500/40 bg-rose-500/10 text-rose-600", text: "text-rose-600", label: "blocked" },
  CANCELLED: { tile: "border-border bg-muted/60 text-muted-foreground", text: "text-muted-foreground", label: "cancelled" },
  AVAILABLE: { tile: "border-violet-500/40 bg-violet-500/10 text-violet-600", text: "text-violet-600", label: "ready to use" },
};

function Marker({ status }: { status: NodeStatus }) {
  const base = "absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full border-2 border-card text-white";
  if (status === "COMPLETE") return <span className={cn(base, "bg-emerald-500")}><Check className="h-2.5 w-2.5" strokeWidth={3} /></span>;
  if (status === "ACTIVE") return <span className={cn(base, "bg-blue-500")}><span className="h-1.5 w-1.5 animate-ping rounded-full bg-white" /></span>;
  if (status === "REVIEW_REQUIRED") return <span className={cn(base, "bg-amber-500")}><Eye className="h-2.5 w-2.5" /></span>;
  if (status === "FAILED" || status === "BLOCKED") return <span className={cn(base, "bg-rose-500")}><X className="h-2.5 w-2.5" strokeWidth={3} /></span>;
  if (status === "LOCKED") return <span className={cn(base, "bg-muted-foreground/40")}><Lock className="h-2 w-2" /></span>;
  return null;
}

function Node({ id, label, status, href, current, note }: {
  id: string; label: string; status: NodeStatus; href?: string; current: boolean; note?: string;
}) {
  const Icon = ICON[id] ?? LayoutGrid;
  const tone = TONE[status];
  const body = (
    <>
      <span className={cn("relative grid h-9 w-9 shrink-0 place-items-center rounded-xl border transition", tone.tile)}>
        <Icon className="h-4 w-4" />
        <Marker status={status} />
      </span>
      <span className="leading-tight">
        <span className="block whitespace-nowrap text-sm font-semibold">{label}</span>
        <span className={cn("block whitespace-nowrap text-[10px] font-medium uppercase tracking-wide", tone.text)}>{note ?? tone.label}</span>
      </span>
    </>
  );
  const cls = cn("flex items-center gap-2 rounded-xl px-1.5 py-1.5 transition",
    current ? "bg-accent shadow-sm ring-1 ring-primary/20" : href ? "hover:bg-muted" : "cursor-not-allowed opacity-70");
  return href && status !== "LOCKED"
    ? <Link href={href} prefetch className={cls} aria-current={current ? "page" : undefined}>{body}</Link>
    : <div className={cls} title="Locked until earlier stages are approved">{body}</div>;
}

function Edge({ done, active, grow }: { done: boolean; active?: boolean; grow?: boolean }) {
  return (
    <span aria-hidden className={cn("relative mx-0.5 h-0.5 min-w-[14px] shrink-0 overflow-hidden rounded-full", grow ? "flex-1" : "w-4",
      done ? "bg-emerald-400" : "bg-border")}>
      {active && <span className="absolute inset-0 animate-pulse bg-gradient-to-r from-emerald-400 to-blue-400" />}
    </span>
  );
}

const LANE_H = 52;
const GAP = 8;

/** Curved fork/merge connectors between the spine and the three lanes. */
function Branches({ lanes, direction, done }: { lanes: number; direction: "fork" | "merge"; done: boolean[] }) {
  const h = lanes * LANE_H + (lanes - 1) * GAP;
  const mid = h / 2;
  const ys = Array.from({ length: lanes }, (_, i) => i * (LANE_H + GAP) + LANE_H / 2);
  return (
    <svg aria-hidden width={32} height={h} viewBox={`0 0 32 ${h}`} className="shrink-0 overflow-visible">
      {ys.map((y, i) => {
        const d = direction === "fork" ? `M0 ${mid} C 18 ${mid}, 14 ${y}, 32 ${y}` : `M0 ${y} C 18 ${y}, 14 ${mid}, 32 ${mid}`;
        return (
          <path key={i} d={d} fill="none" strokeWidth={2} strokeLinecap="round"
                className={cn(done[i] ? "stroke-emerald-400" : "stroke-border")}
                strokeDasharray={done[i] ? undefined : "4 4"} />
        );
      })}
    </svg>
  );
}

export function StageRail({ runId, stages }: { runId: string; stages: StageStatus[] }) {
  const path = usePathname();
  const status = (stage: string): StageStatusValue => stages.find((s) => s.stage === stage)?.status ?? "LOCKED";
  const noteOf = (stage: string) => stages.find((s) => s.stage === stage)?.note;
  const href = (stage: string) => {
    const meta = byStage(stage);
    return meta ? `/runs/${runId}/${meta.slug}` : undefined;
  };
  const node = (stage: string, extra?: { status?: NodeStatus; href?: string; label?: string; note?: string }) => {
    const h = extra?.href ?? href(stage);
    return (
      <Node id={stage} label={extra?.label ?? byStage(stage)?.label ?? stage} status={extra?.status ?? status(stage)}
            href={h} current={!!h && path === h} note={extra?.note ?? noteOf(stage)} />
    );
  };
  const done = (stage: string) => status(stage) === "COMPLETE";
  const live = (stage: string) => ["ACTIVE", "REVIEW_REQUIRED"].includes(status(stage));
  const sttm = status("STTM");
  // QA is a lane once the STTM is approved (status from its sign-off); before that it only previews tests.
  const qaLane = stages.find((s) => s.stage === "QA");
  const qaStatus: NodeStatus = qaLane ? qaLane.status : sttm === "LOCKED" ? "LOCKED" : "AVAILABLE";
  const spine = ["SOURCE", "PROFILING", "MAPPING", "STTM"];
  const overview = path === `/runs/${runId}`;

  return (
    <nav aria-label="Run lineage" className="surface overflow-x-auto px-4 py-3">
      <div className="flex min-w-max items-center">
        <Link href={`/runs/${runId}`} prefetch
              className={cn("flex items-center gap-2 rounded-xl px-2.5 py-2 text-sm font-semibold transition",
                overview ? "bg-accent ring-1 ring-primary/20" : "hover:bg-muted")}>
          <LayoutGrid className="h-4 w-4 text-muted-foreground" /> Overview
        </Link>
        <span aria-hidden className="mx-1.5 h-8 w-px bg-border" />

        {spine.map((stage, i) => (
          <div key={stage} className="flex items-center">
            {i > 0 && <Edge done={done(spine[i - 1])} active={done(spine[i - 1]) && live(stage)} />}
            {node(stage)}
          </div>
        ))}

        <Branches lanes={3} direction="fork" done={[sttm !== "LOCKED", done("STTM"), done("STTM")]} />

        <div className="flex flex-col" style={{ gap: GAP }} role="group"
             aria-label="Built in parallel from the approved STTM: QA tests, Data Quality, and dbt then Validation">
          <div className="flex items-center" style={{ height: LANE_H }}>
            {node("QA", { status: qaStatus, href: qaStatus === "LOCKED" ? undefined : `/runs/${runId}/qa`, label: "QA tests",
                          note: qaStatus === "AVAILABLE" ? "from the STTM" : qaLane?.note })}
            <Edge done={qaStatus === "COMPLETE"} grow />
          </div>
          <div className="flex items-center" style={{ height: LANE_H }}>
            {node("SODA")}
            <Edge done={done("SODA")} grow />
          </div>
          <div className="flex items-center" style={{ height: LANE_H }}>
            {node("DBT")}
            <Edge done={done("DBT")} active={done("DBT") && live("VALIDATION")} />
            {node("VALIDATION")}
            <Edge done={done("VALIDATION")} grow />
          </div>
        </div>

        <Branches lanes={3} direction="merge" done={[qaStatus === "COMPLETE", done("SODA"), done("VALIDATION")]} />
        {node("REVIEW")}
        {status("VALIDATION") === "FAILED" || status("VALIDATION") === "BLOCKED" ? (
          <span className="ml-3 inline-flex items-center gap-1 rounded-full bg-rose-500/10 px-2 py-1 text-[11px] font-medium text-rose-600">
            <AlertTriangle className="h-3 w-3" /> Validation must pass before review
          </span>
        ) : null}
      </div>
    </nav>
  );
}
