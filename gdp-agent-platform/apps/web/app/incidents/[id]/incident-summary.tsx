"use client";

import { useEffect, useState } from "react";
import { AlertOctagon, ArrowRight, Bot, Brain, Loader2, UserCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { FAILURE_STATUS } from "@/lib/resolve-labels";
import { listFailures, type FailureRow } from "../../ops/resolve-actions";
import type { IncidentDetail } from "../actions";

/** The last exception line of an error ("ProgrammingError: 002003 ... does not exist"), the line a developer reads first. */
export function headline(excerpt: string | null | undefined): string | null {
  const lines = String(excerpt ?? "").split("\n").map((l) => l.trim()).filter(Boolean);
  const hit = [...lines].reverse().find((l) => /(Error|Exception|Timeout|Failed|failed)\b/.test(l) && !l.startsWith("File "));
  return (hit ?? lines[lines.length - 1] ?? null)?.slice(0, 300) ?? null;
}

type Step = { n: number; title: string; icon: typeof Bot; tone: string; body: React.ReactNode; foot?: React.ReactNode };

/** The incident in four steps at the top of the page: what failed, why, what the platform did, and what to do next.
 *  Everything here is also on the page below in full; this is what a developer reads first. */
export function IncidentSummary({ data, canOperate, canFixApprove, onJump }: {
  data: IncidentDetail; canOperate: boolean; canFixApprove: boolean; onJump: (section: string) => void;
}) {
  const inc = data.incident;
  const [failure, setFailure] = useState<FailureRow | null | undefined>(inc.dq_table ? null : undefined);
  useEffect(() => {
    if (inc.dq_table) return;
    let live = true;
    listFailures({ incident_id: inc.incident_id, limit: 1 }).then((r) => { if (live) setFailure(r.ok ? r.data[0] ?? null : null); });
    return () => { live = false; };
  }, [inc.incident_id, inc.dq_table]);

  const status = String(inc.status || "").toUpperCase();
  const ai = inc.ai;
  const line = headline(inc.error_excerpt);
  const fs = failure?.status;
  const confidence = typeof ai?.confidence === "number" ? Math.round(ai.confidence * (ai.confidence <= 1 ? 100 : 1)) : null;

  const next = ((): { text: string; jump?: string } => {
    if (status === "RESOLVED") return { text: "Nothing: the incident is resolved. If it was fixed by hand, write a known fix so it is automatic next time.", jump: "resolution" };
    if (fs === "AWAITING_APPROVAL") return canFixApprove
      ? { text: "Approve or reject the known fix. Approving runs it and reruns the task.", jump: "resolution" }
      : { text: "A known fix waits for a fix approver (OPS.FIX_APPROVE). Nothing runs until they decide." };
    if (status === "OPEN" && canOperate) return { text: "Acknowledge it, so the team knows someone is on it, then read the diagnosis." };
    if (!ai) return { text: "Run the AI diagnosis to get the probable cause, the evidence and whether a retry is safe.", jump: "ai" };
    if (ai.safe_to_retry === "yes" && inc.run_id) return { text: "The diagnosis says a retry is safe: retry the task, then resolve the incident when it succeeds." };
    if (ai.safe_to_retry === "after_fix") return { text: "Fix the cause first (see the fix steps in the diagnosis), then retry the task.", jump: "ai" };
    if (fs === "TICKETED" || inc.jira_key) return { text: `Work the ticket${inc.jira_key ? ` ${inc.jira_key}` : ""}; when it is fixed, resolve the incident and write a known fix.` };
    return { text: "Read the diagnosis and its evidence, then fix, retry or resolve.", jump: "ai" };
  })();

  const platform: React.ReactNode = inc.dq_table
    ? <p>Data quality incidents are not resolved automatically. They resolve themselves when a later scan passes.</p>
    : failure === undefined ? <p className="flex items-center gap-1.5 text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Checking</p>
      : !failure ? <p>Not queued for automatic resolution (no failed task was captured for it).</p>
        : (
          <div className="space-y-1">
            <p className="font-medium">{FAILURE_STATUS[failure.status]?.label ?? failure.status}</p>
            <p className="text-muted-foreground">{failure.status === "OUT_OF_SCOPE" && failure.detail ? failure.detail
              : failure.issue_title ? `Known fix: ${failure.issue_title}` : FAILURE_STATUS[failure.status]?.hint}</p>
          </div>
        );

  const steps: Step[] = [
    { n: 1, title: "What failed", icon: AlertOctagon, tone: "text-rose-600 bg-rose-50 ring-rose-100",
      body: (
        <div className="space-y-1">
          <p className="break-words font-mono text-[12px] leading-snug text-foreground">{line ?? "No error text was captured"}</p>
          <p className="text-muted-foreground">{inc.dq_table ? inc.dq_table : <>{inc.dag_id}{inc.task_id ? ` › ${inc.task_id}` : ""}</>} · seen {inc.occurrences}×</p>
        </div>
      ), foot: inc.error_excerpt ? <Jump onClick={() => onJump("error")}>Full error</Jump> : undefined },
    { n: 2, title: "Why", icon: Brain, tone: "text-violet-600 bg-violet-50 ring-violet-100",
      body: ai?.probable_cause || inc.ai_summary
        ? (
          <div className="space-y-1">
            <p className="leading-snug text-foreground">{ai?.probable_cause ?? inc.ai_summary}</p>
            {(ai?.category || confidence !== null) && <p className="text-muted-foreground">{[ai?.category, confidence !== null ? `${confidence}% confident` : null].filter(Boolean).join(" · ")}</p>}
          </div>
        )
        : <p className="text-muted-foreground">Not diagnosed yet.</p>,
      foot: <Jump onClick={() => onJump("ai")}>{ai ? "Evidence and fix steps" : "Diagnose"}</Jump> },
    { n: 3, title: "What the platform did", icon: Bot, tone: "text-sky-600 bg-sky-50 ring-sky-100", body: platform,
      foot: inc.dq_table ? undefined : <Jump onClick={() => onJump("resolution")}>Resolution details</Jump> },
    { n: 4, title: "Your next step", icon: UserCheck, tone: "text-amber-700 bg-amber-50 ring-amber-100",
      body: <p className="leading-snug font-medium text-foreground">{next.text}</p>,
      foot: next.jump ? <Jump onClick={() => onJump(next.jump!)}>Go there</Jump> : undefined },
  ];

  return (
    <ol className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="Incident at a glance" data-testid="incident-summary">
      {steps.map((s) => (
        <li key={s.n} className={cn("surface flex flex-col gap-2 p-4", s.n === 4 && "ring-1 ring-amber-200 dark:ring-amber-900")}>
          <div className="flex items-center gap-2">
            <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg ring-1 ring-inset", s.tone)}><s.icon className="h-3.5 w-3.5" /></span>
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{s.n}. {s.title}</span>
          </div>
          <div className="flex-1 text-xs">{s.body}</div>
          {s.foot}
        </li>
      ))}
    </ol>
  );
}

function Jump({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="inline-flex items-center gap-1 self-start text-xs font-medium text-primary hover:underline">
      {children}<ArrowRight className="h-3 w-3" />
    </button>
  );
}
