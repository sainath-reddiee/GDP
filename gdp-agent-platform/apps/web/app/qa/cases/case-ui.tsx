"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { LifeBuoy, Link2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { connectJira } from "../../jira/actions";
import type { CaseOpened, CaseResult, CaseSource } from "./actions";
import { KINDS } from "./case-filters";

const KIND_LABEL: Record<string, string> = Object.fromEntries(KINDS.map((k) => [k.id, k.label]));

export const SOURCES: Record<CaseSource, string> = {
  JIRA: "Jira", INCIDENT: "Incident", QA_FAILURE: "QA failure", DQ_FAILURE: "DQ failure", APP_REPORT: "Reported in app",
};

const STATUS_LABEL: Record<string, string> = {
  NEW: "new", TRIAGED: "triaged", IN_PROGRESS: "in progress", FIX_PROPOSED: "fix proposed", FIX_APPLIED: "fix applied",
  VERIFIED: "verified", RESOLVED: "resolved", CLOSED: "closed", DUPLICATE: "duplicate",
};
const STATUS_TONE: Record<string, string> = {
  NEW: "bg-rose-50 text-rose-700 ring-rose-100",
  TRIAGED: "bg-amber-50 text-amber-700 ring-amber-100",
  IN_PROGRESS: "bg-sky-50 text-sky-700 ring-sky-100",
  FIX_PROPOSED: "bg-violet-50 text-violet-700 ring-violet-100",
  FIX_APPLIED: "bg-indigo-50 text-indigo-700 ring-indigo-100",
  VERIFIED: "bg-teal-50 text-teal-700 ring-teal-100",
  RESOLVED: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  CLOSED: "bg-slate-100 text-slate-600 ring-slate-200",
  DUPLICATE: "bg-slate-100 text-slate-600 ring-slate-200",
};

export const up = (s: string | null | undefined) => (s ?? "").toUpperCase();
export const caseStatusLabel = (s: string) => STATUS_LABEL[up(s)] ?? s.toLowerCase().replace(/_/g, " ");
export const caseKindLabel = (k: string) => KIND_LABEL[up(k)] ?? k.toLowerCase().replace(/_/g, " ");
export const caseSourceLabel = (s: string) => SOURCES[up(s) as CaseSource] ?? s.toLowerCase().replace(/_/g, " ");
export const caseHref = (id: string) => `/qa/cases/${encodeURIComponent(id)}`;

export const pill = "inline-flex shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset";

export function CaseStatusPill({ status, className }: { status: string; className?: string }) {
  return <span className={cn(pill, STATUS_TONE[up(status)] ?? STATUS_TONE.CLOSED, className)}>{caseStatusLabel(status)}</span>;
}

export function CaseKindPill({ kind }: { kind: string }) {
  return <span className={cn(pill, "bg-violet-50 text-violet-700 ring-violet-100")}>{caseKindLabel(kind)}</span>;
}

export function SourceChip({ source }: { source: string }) {
  return <span className={cn(pill, "bg-muted text-muted-foreground ring-border")}>{caseSourceLabel(source)}</span>;
}

/** "Opened CASE-12" or "Already tracked as CASE-12". */
export const openedText = (o: CaseOpened) => (o.created ? `Opened ${o.case.number}` : `Already tracked as ${o.case.number}`);

/** Opens (or finds) the case for something, then goes to it. The case page repeats what happened, so the message
 *  survives the navigation. A 428 from Jira offers to connect it and come back here. */
export function OpenCaseButton({ open, label = "Open as case", size = "sm", variant = "outline", className }: {
  open: () => Promise<CaseResult<CaseOpened>>; label?: string; size?: "sm" | "default"; variant?: "outline" | "ghost" | "default";
  className?: string;
}) {
  const router = useRouter();
  const [busy, start] = useTransition();
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const [connect, setConnect] = useState(false);
  const click = () => start(async () => {
    setError(""); setDone(""); setConnect(false);
    try {
      const r = await open();
      if (!r.ok) {
        if (r.status === 428) { setConnect(true); setError(r.error || "Connect your Jira account to open a case from an issue."); }
        else setError(r.error);
        return;
      }
      setDone(openedText(r.data));
      router.push(`${caseHref(r.data.case.case_id)}?opened=${r.data.created ? "new" : "existing"}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not open the case.");
    }
  });
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-2", className)}>
      <Button type="button" size={size} variant={variant} disabled={busy} onClick={click}>
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <LifeBuoy className="h-3.5 w-3.5" />}{label}</Button>
      {done && <span role="status" className="text-xs text-success">{done}</span>}
      {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
      {connect && <ConnectJiraLink />}
    </span>
  );
}

function ConnectJiraLink() {
  const [busy, start] = useTransition();
  const [error, setError] = useState("");
  const go = () => start(async () => {
    setError("");
    const r = await connectJira(`${window.location.pathname}${window.location.search}`);
    if (r.ok) window.location.href = r.data.url; else setError(r.error);
  });
  return (
    <span className="inline-flex items-center gap-2">
      <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={go}>
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}Connect Jira</Button>
      {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
    </span>
  );
}
