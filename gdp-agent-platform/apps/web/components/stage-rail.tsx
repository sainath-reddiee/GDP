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

export function StageRail({ runId, stages }: { runId: string; stages: StageStatus[] }) {
  const path = usePathname();
  return (
    <aside className="w-56 shrink-0 rounded-xl border bg-card p-2 shadow-sm">
      <Link
        href={`/runs/${runId}`}
        prefetch
        className={cn("mb-1 block rounded-md px-2 py-1.5 text-sm font-medium hover:bg-muted",
          path === `/runs/${runId}` && "bg-accent text-accent-foreground")}
      >
        Overview
      </Link>
      {stages.map((s) => {
        const meta = byStage(s.stage);
        if (!meta) return null;
        const Icon = ICONS[s.status];
        const href = `/runs/${runId}/${meta.slug}`;
        const body = (
          <>
            <Icon className={cn("h-4 w-4", TONE[s.status])} />
            <span>{meta.label}</span>
            <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground">
              {s.status.replace("_", " ").toLowerCase()}
            </span>
          </>
        );
        const cls = "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm";
        return s.status === "LOCKED" ? (
          <div key={s.stage} className={cn(cls, "cursor-not-allowed text-muted-foreground")} aria-disabled title="Locked until earlier stages are approved">
            {body}
          </div>
        ) : (
          <Link key={s.stage} href={href} prefetch className={cn(cls, "hover:bg-muted", path === href && "bg-accent text-accent-foreground")}>
            {body}
          </Link>
        );
      })}
    </aside>
  );
}
