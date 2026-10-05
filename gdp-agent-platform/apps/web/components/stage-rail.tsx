"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { AlertTriangle, CheckCircle2, CircleDot, Eye, Lock, XCircle } from "lucide-react";
import type { StageStatus, StageStatusValue } from "@/lib/types";
import { byStage } from "@/lib/stages";
import { cn } from "@/lib/utils";

const ICONS: Record<StageStatusValue, typeof Lock> = {
  COMPLETE: CheckCircle2,
  ACTIVE: CircleDot,
  REVIEW_REQUIRED: Eye,
  LOCKED: Lock,
  FAILED: XCircle,
  BLOCKED: AlertTriangle,
  CANCELLED: XCircle,
};

const TONE: Record<StageStatusValue, string> = {
  COMPLETE: "text-success",
  ACTIVE: "text-primary",
  REVIEW_REQUIRED: "text-warning",
  LOCKED: "text-muted-foreground",
  FAILED: "text-destructive",
  BLOCKED: "text-destructive",
  CANCELLED: "text-muted-foreground",
};

const RING: Record<StageStatusValue, string> = {
  COMPLETE: "border-success/40 bg-success/10",
  ACTIVE: "border-primary/40 bg-primary/10",
  REVIEW_REQUIRED: "border-warning/40 bg-warning/10",
  LOCKED: "border-border bg-muted/40",
  FAILED: "border-destructive/40 bg-destructive/10",
  BLOCKED: "border-destructive/40 bg-destructive/10",
  CANCELLED: "border-border bg-muted/40",
};

export function StageRail({ runId, stages }: { runId: string; stages: StageStatus[] }) {
  const path = usePathname();
  const overview = path === `/runs/${runId}`;
  return (
    <nav aria-label="Run stages" className="overflow-x-auto rounded-xl border bg-card px-3 py-2.5 shadow-sm">
      <ol className="flex min-w-max items-center gap-0">
        <li>
          <Link
            href={`/runs/${runId}`}
            prefetch
            className={cn(
              "flex items-center gap-2 rounded-md px-2 py-1 text-sm font-medium hover:bg-muted",
              overview && "bg-accent text-accent-foreground",
            )}
          >
            Overview
          </Link>
        </li>
        {stages.map((s, i) => {
          const meta = byStage(s.stage);
          if (!meta) return null;
          const Icon = ICONS[s.status];
          const href = `/runs/${runId}/${meta.slug}`;
          const current = path === href;
          const body = (
            <>
              <span className={cn("flex h-7 w-7 items-center justify-center rounded-full border", RING[s.status])}>
                <Icon className={cn("h-3.5 w-3.5", TONE[s.status])} />
              </span>
              <span className="leading-tight">
                <span className="block text-sm font-medium">{meta.label}</span>
                <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
                  {s.status === "REVIEW_REQUIRED" ? "review" : s.status.replace(/_/g, " ").toLowerCase()}
                </span>
              </span>
            </>
          );
          return (
            <li key={s.stage} className="flex items-center">
              <span className="mx-1.5 h-px w-4 bg-border" aria-hidden />
              {s.status === "LOCKED" ? (
                <div className="flex cursor-not-allowed items-center gap-2 px-1.5 py-1 text-muted-foreground" title="Locked until earlier stages are approved">
                  {body}
                </div>
              ) : (
                <Link
                  href={href}
                  prefetch
                  className={cn("flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-muted", current && "bg-accent text-accent-foreground")}
                >
                  {body}
                </Link>
              )}
              {i === 6 && <span className="sr-only">Data Quality and dbt generate from the approved STTM in parallel</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
